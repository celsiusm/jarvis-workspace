"""Guardas de cierre del orquestador (auditoría 2026-07-02).

El JSON de acciones viene de un modelo chico (haiku) SIN confirmación:
  - close_terminal con un id alucinado podía matar una terminal de OTRO proyecto;
  - close_all mataba agentes a mitad de tarea sin preguntar.

Reglas nuevas: validación de proyecto; cierre negado si la víctima está
'trabajando' (con INSISTENCIA: repetir el pedido dentro de la ventana ejecuta
igual — el guard es contra el accidente, no contra el usuario).
"""
import asyncio
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from plotspace.routers import orchestrator as orch
from plotspace.routers import terminals as terminals_mod
from plotspace.core import agent_watch


# ─── Fakes mínimos de DB ──────────────────────────────────────────────────────

class _Cur:
    """Cursor scripteado por contenido del SQL."""
    def __init__(self, term_rows=None, wf_rows=None):
        self.term_rows = term_rows or []
        self.wf_rows = wf_rows or []
        self._last = ''

    def execute(self, sql, *a):
        self._last = sql
        return self

    def fetchall(self):
        return self.wf_rows if 'workflows' in self._last else self.term_rows

    def fetchone(self):
        rows = self.wf_rows if 'workflows' in self._last else self.term_rows
        return rows[0] if rows else None


class _Conn:
    def __init__(self, term_rows=None, wf_rows=None):
        self._cur = _Cur(term_rows, wf_rows)
        self.updates = []

    def cursor(self):
        return self._cur

    def execute(self, sql, *a):
        if sql.strip().upper().startswith('UPDATE'):
            self.updates.append((sql, a))
            return self._cur
        return self._cur.execute(sql, *a)

    def commit(self):
        pass

    def close(self):
        pass


class _TeardownRec:
    def __init__(self):
        self.calls = []

    async def __call__(self, tid):
        self.calls.append(tid)


def _con_parches(term_rows, estados, fn):
    """Corre `fn(teardown_recorder)` con get_db/teardown/agent_watch parcheados."""
    rec = _TeardownRec()
    conn = _Conn(term_rows)
    orig_db = orch.get_db
    orig_td = terminals_mod.teardown_terminal
    orig_est = agent_watch._estados
    orch.get_db = lambda: conn
    terminals_mod.teardown_terminal = rec
    agent_watch._estados = dict(estados)
    orch._cierres_rechazados.clear()
    try:
        return fn(rec), rec, conn
    finally:
        orch.get_db = orig_db
        terminals_mod.teardown_terminal = orig_td
        agent_watch._estados = orig_est
        orch._cierres_rechazados.clear()


# ─── close_terminal: validación de proyecto ───────────────────────────────────

def test_cerrar_terminal_de_otro_proyecto_se_niega():
    fila = {'project_id': 7, 'nombre': 'Claude Code #1', 'activa': 1}
    motivo, rec, _ = _con_parches(
        [fila], {},
        lambda r: asyncio.run(orch._cerrar_terminal(5, project_id=8)))
    assert motivo and 'OTRO proyecto' in motivo
    assert rec.calls == []          # no hubo teardown


def test_cerrar_terminal_inexistente_se_niega():
    motivo, rec, _ = _con_parches(
        [], {}, lambda r: asyncio.run(orch._cerrar_terminal(99, project_id=8)))
    assert motivo and rec.calls == []


def test_cerrar_terminal_del_proyecto_correcto_cierra():
    fila = {'project_id': 8, 'nombre': 'Claude Code #1', 'activa': 1}
    motivo, rec, conn = _con_parches(
        [fila], {5: {'fase': 'idle'}},
        lambda r: asyncio.run(orch._cerrar_terminal(5, project_id=8)))
    assert motivo is None
    assert rec.calls == [5]
    assert conn.updates              # activa=0 antes del teardown


def test_cerrar_terminal_sin_project_id_no_valida_proyecto():
    # Compat con callers internos que ya scopearon ellos.
    fila = {'project_id': 7, 'nombre': 'X', 'activa': 1}
    motivo, rec, _ = _con_parches(
        [fila], {}, lambda r: asyncio.run(orch._cerrar_terminal(5)))
    assert motivo is None and rec.calls == [5]


# ─── close_terminal: guard de 'trabajando' + insistencia ──────────────────────

def test_cerrar_terminal_trabajando_se_niega_y_con_insistencia_cierra():
    fila = {'project_id': 8, 'nombre': 'Builder', 'activa': 1}

    def flujo(rec):
        m1 = asyncio.run(orch._cerrar_terminal(5, project_id=8))
        m2 = asyncio.run(orch._cerrar_terminal(5, project_id=8))
        return m1, m2

    (m1, m2), rec, _ = _con_parches([fila], {5: {'fase': 'trabajando'}}, flujo)
    assert m1 and 'TRABAJANDO' in m1     # primera vez: negado con motivo
    assert m2 is None                     # el usuario insistió: se ejecuta
    assert rec.calls == [5]


# ─── close_all: todo-o-nada + insistencia ─────────────────────────────────────

def test_cerrar_todas_con_uno_trabajando_no_cierra_ninguna():
    rows = [{'id': 1, 'nombre': 'Builder'}, {'id': 2, 'nombre': 'Scout'}]
    saltadas, rec, conn = _con_parches(
        rows, {1: {'fase': 'trabajando'}, 2: {'fase': 'idle'}},
        lambda r: asyncio.run(orch._cerrar_todas(8)))
    # Todo-o-nada: closed_all=true en el frontend borra TODAS las cards, así que
    # un cierre parcial desincronizaría la UI. Se niega entero y se avisa.
    assert saltadas == ['Builder']
    assert rec.calls == []
    assert conn.updates == []


def test_cerrar_todas_insistencia_cierra_todo():
    rows = [{'id': 1, 'nombre': 'Builder'}, {'id': 2, 'nombre': 'Scout'}]

    def flujo(rec):
        s1 = asyncio.run(orch._cerrar_todas(8))
        s2 = asyncio.run(orch._cerrar_todas(8))
        return s1, s2

    (s1, s2), rec, _ = _con_parches(
        rows, {1: {'fase': 'trabajando'}}, flujo)
    assert s1 == ['Builder'] and s2 == []
    assert sorted(rec.calls) == [1, 2]


def test_cerrar_todas_idle_cierra_directo():
    rows = [{'id': 1, 'nombre': 'A'}, {'id': 2, 'nombre': 'B'}]
    saltadas, rec, _ = _con_parches(
        rows, {1: {'fase': 'idle'}},   # 2 sin estado (recién nacida) = cerrable
        lambda r: asyncio.run(orch._cerrar_todas(8)))
    assert saltadas == [] and sorted(rec.calls) == [1, 2]


if __name__ == '__main__':
    import traceback
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith('test_') and callable(fn):
            try:
                fn()
                print('ok', nombre)
            except Exception:
                fallos += 1
                print('FAIL', nombre)
                traceback.print_exc()
    sys.exit(1 if fallos else 0)
