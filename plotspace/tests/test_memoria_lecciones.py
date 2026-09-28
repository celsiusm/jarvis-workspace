"""
Test: memoria_lecciones — el destilador de lecciones del enjambre.

Cierra el loop de aprendizaje (patrón wb_gusto): los motivos de
TASK_BLOCKED/TASK_ERROR acumulados en task_events se destilan (con umbral,
1 llamada barata a la API; sin key degrada en silencio) a ≤20 reglas en
`.jarvis/memory/lecciones-del-enjambre.md`, y ese contenido se inyecta entre
markers en CLAUDE.md/AGENTS.md — las lecciones se cargan SIEMPRE, no opt-in.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plotspace.tests._harness import fresh_db
from plotspace.core.database import get_db
from plotspace.core import memoria_lecciones as lec


def _sembrar_fallos(project_id=1, n=3, desde=0):
    conn = get_db()
    try:
        conn.execute("INSERT OR IGNORE INTO projects (id, nombre, ruta, fecha_creacion, ultimo_acceso) "
                     "VALUES (?, 'p', '/tmp/p', '2026-07-10', '2026-07-10')", (project_id,))
        for i in range(n):
            conn.execute(
                "INSERT INTO task_events (terminal_id, project_id, event, timestamp, workflow_id, motivo) "
                "VALUES (?, ?, 'TASK_BLOCKED', '2026-07-10T10:00:00', 'wf1', ?)",
                (10 + i, project_id, f'motivo de prueba {desde + i}'))
        conn.commit()
    finally:
        conn.close()


# ─── señales desde task_events ───────────────────────────────────────────────

def test_senales_nuevas_lee_motivos():
    fresh_db()
    _sembrar_fallos(n=2)
    senales, max_id = lec.senales_nuevas(1)
    assert len(senales) == 2
    assert 'motivo de prueba 0' in senales[0]
    assert 'TASK_BLOCKED' in senales[0]
    assert max_id >= 2


def test_senales_respeta_desde_id():
    fresh_db()
    _sembrar_fallos(n=3)
    _, max_id = lec.senales_nuevas(1)
    senales2, _ = lec.senales_nuevas(1, desde_id=max_id)
    assert senales2 == []


# ─── prompt ──────────────────────────────────────────────────────────────────

def test_prompt_incluye_existente_y_tope():
    p = lec.armar_prompt('# Lecciones del enjambre\n- vieja regla', ['TASK_ERROR: x'])
    assert 'vieja regla' in p
    assert 'TASK_ERROR: x' in p
    assert '20' in p                    # tope de reglas


# ─── destilación (API mockeada) ──────────────────────────────────────────────

def test_destilar_sin_key_degrada():
    fresh_db()
    _sembrar_fallos(n=2)
    with tempfile.TemporaryDirectory() as d:
        key_prev = os.environ.pop('ANTHROPIC_API_KEY', None)
        try:
            r = lec.destilar_ahora(1, d, estado_path=os.path.join(d, 'estado.json'))
        finally:
            if key_prev is not None:
                os.environ['ANTHROPIC_API_KEY'] = key_prev
        assert r['ok'] is False
        assert not os.path.exists(os.path.join(d, '.jarvis', 'memory', lec.LECCIONES_BASENAME))


def test_destilar_escribe_leccion_e_inyecta():
    fresh_db()
    _sembrar_fallos(n=2)
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, 'CLAUDE.md'), 'w') as f:
            f.write('# Proyecto\n')
        estado = os.path.join(d, 'estado.json')
        orig = lec._llamar_destilador
        lec._llamar_destilador = lambda prompt: (
            '# Lecciones del enjambre\n- Antes de levantar un server, chequeá el puerto.\n')
        key_prev = os.environ.get('ANTHROPIC_API_KEY')
        os.environ['ANTHROPIC_API_KEY'] = 'sk-test-fake'
        try:
            r = lec.destilar_ahora(1, d, estado_path=estado)
        finally:
            lec._llamar_destilador = orig
            if key_prev is None:
                os.environ.pop('ANTHROPIC_API_KEY', None)
            else:
                os.environ['ANTHROPIC_API_KEY'] = key_prev

        assert r['ok'] is True
        archivo = os.path.join(d, '.jarvis', 'memory', lec.LECCIONES_BASENAME)
        src = open(archivo).read()
        assert 'tags: [leccion' in src              # frontmatter de memoria
        assert 'chequeá el puerto' in src
        # inyectado SIEMPRE-cargado en CLAUDE.md y AGENTS.md
        claude = open(os.path.join(d, 'CLAUDE.md')).read()
        assert lec.MARKER_START in claude and 'chequeá el puerto' in claude
        agents = open(os.path.join(d, 'AGENTS.md')).read()
        assert 'chequeá el puerto' in agents
        # el estado avanzó (no re-destila las mismas señales)
        st = json.load(open(estado))
        assert st['1']['ultimo_evento_id'] >= 2


def test_umbral_evita_llamadas_prematuras():
    fresh_db()
    _sembrar_fallos(n=2)                             # menos que el umbral
    with tempfile.TemporaryDirectory() as d:
        r = lec.tal_vez_destilar(1, d, umbral=6, estado_path=os.path.join(d, 'e.json'))
        assert r is None


def test_flag_off_apaga_todo():
    fresh_db()
    _sembrar_fallos(n=10)
    with tempfile.TemporaryDirectory() as d:
        os.environ['MEMORIA_LECCIONES'] = 'off'
        try:
            r = lec.tal_vez_destilar(1, d, umbral=1, estado_path=os.path.join(d, 'e.json'))
        finally:
            os.environ.pop('MEMORIA_LECCIONES', None)
        assert r is None


# ─── inyección de markers ────────────────────────────────────────────────────

def test_inyectar_refresca_e_idempotente():
    with tempfile.TemporaryDirectory() as d:
        mdir = os.path.join(d, '.jarvis', 'memory')
        os.makedirs(mdir)
        with open(os.path.join(mdir, lec.LECCIONES_BASENAME), 'w') as f:
            f.write('---\ntitulo: L\ntags: [leccion]\n---\n\n# Lecciones del enjambre\n- Regla uno.\n')
        with open(os.path.join(d, 'CLAUDE.md'), 'w') as f:
            f.write(f'# P\n\n{lec.MARKER_START}\nviejo\n{lec.MARKER_END}\n')
        lec.inyectar_lecciones(d)
        lec.inyectar_lecciones(d)                    # idempotente
        claude = open(os.path.join(d, 'CLAUDE.md')).read()
        assert claude.count(lec.MARKER_START) == 1
        assert 'Regla uno' in claude and 'viejo' not in claude


def test_inyectar_sin_lecciones_es_noop():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, 'CLAUDE.md'), 'w') as f:
            f.write('# P\n')
        lec.inyectar_lecciones(d)
        assert lec.MARKER_START not in open(os.path.join(d, 'CLAUDE.md')).read()


# ─── Compilación determinista desde memorias [leccion] (cero API) ────────────
# task_events puede estar VACÍA (el enjambre trabaja en terminales directas), pero
# los agentes SÍ escriben lecciones como memorias. Esa mitad del bloque
# siempre-cargado no depende de la API ni de que haya cierres registrados.

def _leccion(d, slug, resumen='', cuerpo='cuerpo de la lección',
             tags='leccion, git', estado='vigente', actualizado='2026-07-10'):
    mdir = os.path.join(d, '.jarvis', 'memory')
    os.makedirs(mdir, exist_ok=True)
    extra = f'resumen: {resumen}\n' if resumen else ''
    with open(os.path.join(mdir, slug + '.md'), 'w') as f:
        f.write(f'---\ntitulo: {slug}\ntags: [{tags}]\n{extra}'
                f'actualizado: {actualizado}\nestado: {estado}\n---\n\n{cuerpo}\n')


def test_lecciones_de_memorias_compila_resumen_y_puntero():
    with tempfile.TemporaryDirectory() as d:
        _leccion(d, 'no-add-a', resumen='Nunca git add -A: el índice es compartido.')
        _leccion(d, 'sin-tag-leccion', tags='git', resumen='no debería aparecer')
        lineas = lec.lecciones_de_memorias(d)
        assert len(lineas) == 1
        assert 'Nunca git add -A' in lineas[0]
        assert 'no-add-a.md' in lineas[0]


def test_lecciones_de_memorias_excluye_no_vigentes_y_autogeneradas():
    with tempfile.TemporaryDirectory() as d:
        _leccion(d, 'viva', resumen='Regla viva.')
        _leccion(d, 'muerta', resumen='Regla vieja.', estado='obsoleta')
        _leccion(d, 'archivada', resumen='Historia.', estado='archivo')
        mdir = os.path.join(d, '.jarvis', 'memory')
        with open(os.path.join(mdir, lec.LECCIONES_BASENAME), 'w') as f:
            f.write('---\ntitulo: L\ntags: [leccion]\n---\n\n# Lecciones del enjambre\n- x\n')
        lineas = lec.lecciones_de_memorias(d)
        assert len(lineas) == 1 and 'Regla viva' in lineas[0]


def test_lecciones_de_memorias_ordena_por_frescura_y_capea():
    with tempfile.TemporaryDirectory() as d:
        for i in range(15):
            _leccion(d, f'lec-{i:02d}', resumen=f'Regla {i}.',
                     actualizado=f'2026-07-{i + 1:02d}')
        lineas = lec.lecciones_de_memorias(d, k=5)
        assert len(lineas) == 5
        assert 'Regla 14' in lineas[0], 'la más fresca primero'


def test_lecciones_sin_resumen_usa_primera_linea():
    with tempfile.TemporaryDirectory() as d:
        _leccion(d, 'sin-resumen', cuerpo='La primera línea del cuerpo manda.\n\nDetalle.')
        lineas = lec.lecciones_de_memorias(d)
        assert 'La primera línea del cuerpo manda' in lineas[0]


def test_inyectar_compila_lecciones_de_memorias_sin_destilado():
    # SIN lecciones-del-enjambre.md (la API nunca corrió): el bloque
    # siempre-cargado igual existe, compilado de las memorias [leccion]
    with tempfile.TemporaryDirectory() as d:
        _leccion(d, 'no-add-a', resumen='Nunca git add -A: el índice es compartido.')
        with open(os.path.join(d, 'CLAUDE.md'), 'w') as f:
            f.write('# P\n')
        lec.inyectar_lecciones(d)
        claude = open(os.path.join(d, 'CLAUDE.md')).read()
        assert lec.MARKER_START in claude
        assert 'Nunca git add -A' in claude


# ─── Deltas (ACE): el destilador opera por operaciones, no por rewrite ───────

def test_aplicar_deltas_agrega_reemplaza_quita():
    reglas = ['Regla uno.', 'Regla dos.', 'Regla tres.']
    salida = ('AGREGAR: Regla nueva.\n'
              'REEMPLAZAR 2: Regla dos afinada.\n'
              'QUITAR 3\n')
    nuevas, ops = lec.aplicar_deltas(reglas, salida)
    assert nuevas == ['Regla uno.', 'Regla dos afinada.', 'Regla nueva.']
    assert ops == 3


def test_aplicar_deltas_nada_y_ruido():
    reglas = ['Regla uno.']
    nuevas, ops = lec.aplicar_deltas(reglas, 'NADA\n')
    assert nuevas == ['Regla uno.'] and ops == 0
    nuevas2, ops2 = lec.aplicar_deltas(reglas, 'bla bla sin operaciones')
    assert nuevas2 == ['Regla uno.'] and ops2 == 0


def test_aplicar_deltas_cap_y_dedup():
    reglas = [f'Regla {i}.' for i in range(19)]
    salida = 'AGREGAR: Regla nueva.\nAGREGAR: Regla nueva.\nAGREGAR: Otra más.\n'
    nuevas, _ = lec.aplicar_deltas(reglas, salida)
    assert len(nuevas) == 20                      # cap
    assert nuevas.count('Regla nueva.') == 1      # dedup
    assert 'Otra más.' in nuevas
    assert 'Regla 0.' not in nuevas               # cayó la más vieja


def test_destilar_con_deltas_escribe_e_inyecta():
    fresh_db()
    _sembrar_fallos(n=2)
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, 'CLAUDE.md'), 'w') as f:
            f.write('# Proyecto\n')
        orig = lec._llamar_destilador
        lec._llamar_destilador = lambda prompt: 'AGREGAR: Antes de levantar un server, chequeá el puerto.\n'
        key_prev = os.environ.get('ANTHROPIC_API_KEY')
        os.environ['ANTHROPIC_API_KEY'] = 'sk-test-fake'
        try:
            r = lec.destilar_ahora(1, d, estado_path=os.path.join(d, 'e.json'))
        finally:
            lec._llamar_destilador = orig
            if key_prev is None:
                os.environ.pop('ANTHROPIC_API_KEY', None)
            else:
                os.environ['ANTHROPIC_API_KEY'] = key_prev
        assert r['ok'] is True
        src = open(os.path.join(d, '.jarvis', 'memory', lec.LECCIONES_BASENAME)).read()
        assert '- Antes de levantar un server, chequeá el puerto.' in src
        assert 'chequeá el puerto' in open(os.path.join(d, 'CLAUDE.md')).read()


# ─── Señales de éxito (SWE-Exp): lo que FUNCIONÓ también enseña ──────────────

def test_senales_incluyen_exitos_con_motivo():
    fresh_db()
    conn = get_db()
    try:
        conn.execute("INSERT OR IGNORE INTO projects (id, nombre, ruta, fecha_creacion, ultimo_acceso) "
                     "VALUES (1, 'p', '/tmp/p', '2026-07-10', '2026-07-10')")
        conn.execute("INSERT INTO task_events (terminal_id, project_id, event, timestamp, motivo) "
                     "VALUES (10, 1, 'TASK_DONE', '2026-07-10T10:00:00', "
                     "'el fix salió al primer intento replayando el log del pane')")
        conn.execute("INSERT INTO task_events (terminal_id, project_id, event, timestamp, motivo) "
                     "VALUES (11, 1, 'TASK_DONE', '2026-07-10T10:01:00', '')")
        conn.execute("INSERT INTO task_events (terminal_id, project_id, event, timestamp, motivo) "
                     "VALUES (12, 1, 'TASK_BLOCKED', '2026-07-10T10:02:00', 'no pude por X')")
        conn.commit()
    finally:
        conn.close()
    senales, _ = lec.senales_nuevas(1)
    assert len(senales) == 2                      # DONE sin motivo queda afuera
    assert any('TASK_DONE' in s and 'replayando' in s for s in senales)
    assert any('TASK_BLOCKED' in s for s in senales)


# ─── estado del destilador visible (nada de degradar en silencio) ────────────

def test_estado_lecciones_reporta_senales_y_api():
    fresh_db()
    _sembrar_fallos(n=2)
    with tempfile.TemporaryDirectory() as d:
        _leccion(d, 'una-leccion', resumen='Regla.')
        key_prev = os.environ.pop('ANTHROPIC_API_KEY', None)
        try:
            st = lec.estado_lecciones(1, d)
        finally:
            if key_prev is not None:
                os.environ['ANTHROPIC_API_KEY'] = key_prev
        assert st['senales_pendientes'] == 2
        assert st['umbral'] >= 1
        assert st['api_ok'] is False
        assert st['activo'] is True
        assert st['archivo_destilado'] is False
        assert st['lecciones_memoria'] == 1


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
