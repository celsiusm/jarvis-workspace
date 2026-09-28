"""
Test: el MOTIVO de TASK_BLOCKED/TASK_ERROR se captura end-to-end.

Antes el sistema tiraba el "por qué" de cada fallo: el sentinel parseaba
{estado, motivo} pero el poller descartaba el motivo y el broadcast decía
"está bloqueado" a secas. Sin motivos persistidos no hay nada de qué aprender
(capa Captura del sistema de memoria).

Cubre:
  - migración: task_events tiene columna motivo
  - _procesar_keyword_evento → procesar_task_event_interno persiste el motivo
  - sentinel._ciclo propaga el motivo del archivo
  - el broadcast task_event lleva el motivo
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plotspace.tests._harness import fresh_db
from plotspace.core.database import get_db


# ─── Migración: columna motivo en task_events ────────────────────────────────

def test_task_events_tiene_columna_motivo():
    fresh_db()
    conn = get_db()
    try:
        cols = {r['name'] for r in conn.execute('PRAGMA table_info(task_events)').fetchall()}
    finally:
        conn.close()
    assert 'motivo' in cols, f"task_events sin columna motivo: {cols}"


# ─── _procesar_keyword_evento: persiste (vía orquestador) + broadcast ─────────

def _correr_keyword(keyword, motivo=None):
    fresh_db()
    import plotspace.routers.terminals as term
    from plotspace.core import events, logs

    emitidos = []

    async def _fake_broadcast(project_id, data):
        emitidos.append((project_id, data))

    orig_bc, orig_evento = events.broadcaster.broadcast, logs.evento
    events.broadcaster.broadcast = _fake_broadcast
    logs.evento = lambda *a, **k: None
    try:
        asyncio.run(term._procesar_keyword_evento(5, 1, keyword, motivo=motivo))
    finally:
        events.broadcaster.broadcast = orig_bc
        logs.evento = orig_evento

    conn = get_db()
    try:
        row = conn.execute('SELECT * FROM task_events ORDER BY id DESC LIMIT 1').fetchone()
    finally:
        conn.close()
    return row, emitidos


def test_procesar_keyword_persiste_motivo_y_broadcastea():
    row, emitidos = _correr_keyword('TASK_BLOCKED', 'falta la API key')
    assert row['event'] == 'TASK_BLOCKED'
    assert row['project_id'] == 1 and row['terminal_id'] == 5
    assert row['motivo'] == 'falta la API key'
    ev = next(d for _, d in emitidos if d['type'] == 'task_event')
    assert ev['event'] == 'TASK_BLOCKED' and ev['motivo'] == 'falta la API key'


def test_procesar_keyword_sin_motivo_sigue_andando():
    row, emitidos = _correr_keyword('TASK_DONE')
    assert row['event'] == 'TASK_DONE'
    assert row['motivo'] in (None, '')
    ev = next(d for _, d in emitidos if d['type'] == 'task_event')
    assert ev['motivo'] == ''


# ─── sentinel._ciclo propaga el motivo ────────────────────────────────────────

def test_ciclo_sentinel_propaga_motivo():
    import plotspace.core.sentinel as sen
    import plotspace.routers.terminals as term

    with tempfile.TemporaryDirectory() as d:
        sig = os.path.join(d, '.jarvis', 'signals')
        os.makedirs(sig)
        with open(os.path.join(sig, 'terminal_8.json'), 'w') as f:
            f.write('{"estado":"blocked","motivo":"npm install falla con EACCES"}')

        llamadas = []

        async def _fake_proc(tid, pid, kw, motivo=None):
            llamadas.append((tid, pid, kw, motivo))

        orig_act, orig_proc, orig_tt = sen._terminales_activas, term._procesar_keyword_evento, asyncio.to_thread
        sen._terminales_activas = lambda: [{'id': 8, 'project_id': 7, 'ruta': d}]
        term._procesar_keyword_evento = _fake_proc

        async def _direct(fn, *a, **k):
            return fn(*a, **k)

        asyncio.to_thread = _direct
        try:
            asyncio.run(sen._ciclo())
        finally:
            sen._terminales_activas = orig_act
            term._procesar_keyword_evento = orig_proc
            asyncio.to_thread = orig_tt

        assert llamadas == [(8, 7, 'TASK_BLOCKED', 'npm install falla con EACCES')]


if __name__ == '__main__':
    import traceback
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith('test_') and callable(fn):
            try:
                fn(); print(f'ok  {nombre}')
            except Exception:
                fallos += 1; print(f'FAIL {nombre}'); traceback.print_exc()
    sys.exit(1 if fallos else 0)
