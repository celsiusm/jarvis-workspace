"""
Test: el camino del enjambre sin motor de workflows (2026-09-28).

Jarvis reparte trabajo con `spawn_terminal` + `tarea` (un agente por action):
crea la terminal, marca su ORIGEN en la fila (origen/tarea/orquestacion/
orquestacion_ts — lo lee el monitor en vivo de Tasks), espera a que el CLI esté
listo y le pega la tarea con su cierre sentinel. `enviar_prompt` también deja
el origen. Y el cierre de una tarea (procesar_task_event_interno) solo se
persiste y se broadcastea: no hay pasos que avanzar.
"""
import asyncio
import json
from types import SimpleNamespace

import plotspace.routers.orchestrator as orch
from plotspace.core.database import get_db


def _proyecto(tmp_path, terminales=()):
    conn = get_db()
    conn.execute("INSERT INTO projects (id,nombre,ruta,fecha_creacion,ultimo_acceso) "
                 "VALUES (1,'P',?,'2026-01-01','2026-01-01')", (str(tmp_path),))
    for tid in terminales:
        conn.execute("INSERT INTO terminals (id,project_id,nombre,tipo_ia,activa,"
                     "fecha_creacion) VALUES (?,1,?,'claude',1,'2026-01-01')",
                     (tid, f'T{tid}'))
    conn.commit()
    conn.close()


def _fila(tid):
    conn = get_db()
    try:
        return dict(conn.execute('SELECT * FROM terminals WHERE id = ?', (tid,)).fetchone())
    finally:
        conn.close()


def _mocks(monkeypatch, enviados, broadcasts, sesiones):
    import plotspace.routers.terminals as term
    from plotspace.core.events import broadcaster

    async def falso_send(tid, msg, crudo=False):
        enviados.append((tid, msg, crudo))
        return True

    async def listo(tid, timeout=30.0):
        return True

    async def nada(*a, **k):
        return None

    async def crear_sesion(tid, cwd, comando_cli=None, es_reanudacion=False):
        sesiones.append((tid, cwd, comando_cli))

    async def bc(pid, data):
        broadcasts.append((pid, data))

    monkeypatch.setattr(orch, 'send_to_agent', falso_send)
    monkeypatch.setattr(orch, '_esperar_agente_listo', listo)
    monkeypatch.setattr(orch, '_actualizar_state_md', nada)
    monkeypatch.setattr(term, '_crear_sesion_tmux', crear_sesion)
    monkeypatch.setattr(term, '_preparar_proyecto', nada)
    monkeypatch.setattr(broadcaster, 'broadcast', bc)
    from plotspace.core import agent_live
    monkeypatch.setattr(agent_live, 'publicar_roster', nada)


def _procesar(tmp_path, raw, terminales_activas, mensaje='construí notas'):
    req = orch.ChatRequest(project_id=1, message=mensaje)

    async def main():
        res = await orch._procesar_respuesta_orquestador(
            raw, SimpleNamespace(input_tokens=0, output_tokens=0),
            {'id': 1, 'ruta': str(tmp_path), 'nombre': 'P'}, req,
            terminales_activas, stop_reason='end_turn')
        # La entrega de la tarea corre en background: esperarla.
        pendientes = list(orch._tareas_fondo)
        if pendientes:
            await asyncio.gather(*pendientes)
        return res
    return asyncio.run(main())


# ─── spawn_terminal CON tarea ────────────────────────────────────────────────

def test_spawn_con_tarea_registra_origen_y_entrega_la_tarea(tmp_path, monkeypatch):
    _proyecto(tmp_path)
    enviados, broadcasts, sesiones = [], [], []
    _mocks(monkeypatch, enviados, broadcasts, sesiones)
    raw = json.dumps({'message': 'Lanzo Backend.', 'actions': [
        {'type': 'spawn_terminal', 'name': 'Backend', 'ia_type': 'claude',
         'tarea': 'OBJETIVO: CRUD /api/notes.', 'archivos': ['plotspace/routers/notes.py']}]})
    res = _procesar(tmp_path, raw, [], mensaje='  construí un módulo\nde notas  ')

    assert len(res['created_terminals']) == 1
    assert 'workflow_card' not in res
    tid = res['created_terminals'][0]['id']

    fila = _fila(tid)
    assert fila['origen'] == 'jarvis'
    assert fila['tarea'] == 'OBJETIVO: CRUD /api/notes.'
    assert fila['orquestacion'] == 'construí un módulo de notas'
    assert fila['orquestacion_ts']

    # la sesión nace YA corriendo el CLI autónomo, en la ruta del proyecto
    assert len(sesiones) == 1 and sesiones[0][0] == tid and sesiones[0][1] == str(tmp_path)
    assert '--dangerously-skip-permissions' in sesiones[0][2]
    assert f'--session-id {fila["session_uuid"]}' in sesiones[0][2]

    # la tarea llegó (después de "listo"), con territorio y cierre sentinel
    assert len(enviados) == 1
    t_env, texto, crudo = enviados[0]
    assert t_env == tid and crudo is False
    assert texto.startswith('OBJETIVO: CRUD /api/notes.')
    assert 'plotspace/routers/notes.py' in texto
    assert f'.jarvis/signals/terminal_{tid}.json' in texto

    assert (1, {'type': 'agentes_update'}) in broadcasts


def test_spawn_con_tarea_es_un_agente_por_action(tmp_path, monkeypatch):
    _proyecto(tmp_path)
    enviados, broadcasts, sesiones = [], [], []
    _mocks(monkeypatch, enviados, broadcasts, sesiones)
    raw = json.dumps({'message': 'ok', 'actions': [
        {'type': 'spawn_terminal', 'name': 'A', 'ia_type': 'claude', 'count': 3,
         'tarea': 'hacé A'},
        {'type': 'spawn_terminal', 'name': 'B', 'ia_type': 'codex', 'tarea': 'hacé B'}]})
    res = _procesar(tmp_path, raw, [])
    assert [t['nombre'] for t in res['created_terminals']] == ['A', 'B #2']
    assert sorted(e[1].split('\n')[0] for e in enviados) == ['hacé A', 'hacé B']


def test_spawn_sin_tarea_no_marca_origen_ni_entrega(tmp_path, monkeypatch):
    _proyecto(tmp_path)
    enviados, broadcasts, sesiones = [], [], []
    _mocks(monkeypatch, enviados, broadcasts, sesiones)
    raw = json.dumps({'message': 'Dos Claude.', 'actions': [
        {'type': 'spawn_terminal', 'name': 'Claude Code', 'ia_type': 'claude', 'count': 2}]})
    res = _procesar(tmp_path, raw, [])
    assert len(res['created_terminals']) == 2
    for t in res['created_terminals']:
        assert _fila(t['id'])['origen'] is None
    assert enviados == [] and sesiones == []
    assert not any(d.get('type') == 'agentes_update' for _, d in broadcasts)


def test_tarea_truncada_por_max_tokens_no_lanza_agentes(tmp_path, monkeypatch):
    _proyecto(tmp_path)
    enviados, broadcasts, sesiones = [], [], []
    _mocks(monkeypatch, enviados, broadcasts, sesiones)
    raw = json.dumps({'message': 'ok', 'actions': [
        {'type': 'spawn_terminal', 'name': 'A', 'ia_type': 'claude', 'tarea': 'hacé A'}]})
    req = orch.ChatRequest(project_id=1, message='x')
    res = asyncio.run(orch._procesar_respuesta_orquestador(
        raw, None, {'id': 1, 'ruta': str(tmp_path), 'nombre': 'P'}, req, [],
        stop_reason='max_tokens'))
    assert res['created_terminals'] == [] and enviados == []
    assert 'longitud' in res['response'] or 'length' in res['response']


# ─── enviar_prompt deja el origen ────────────────────────────────────────────

def test_enviar_prompt_registra_origen_y_la_respuesta_no(tmp_path, monkeypatch):
    from plotspace.core import agent_watch
    _proyecto(tmp_path, terminales=(31, 32))
    monkeypatch.setitem(agent_watch._estados, 31, {'fase': 'idle'})
    monkeypatch.setitem(agent_watch._estados, 32, {'fase': 'idle', 'esperando': True})
    enviados, broadcasts, sesiones = [], [], []
    _mocks(monkeypatch, enviados, broadcasts, sesiones)
    raw = json.dumps({'message': 'ok', 'actions': [
        {'type': 'enviar_prompt', 'terminal_id': 31, 'prompt': 'pulí la landing'},
        {'type': 'enviar_prompt', 'terminal_id': 32, 'prompt': 'y', 'es_respuesta': True}]})
    ts = [{'id': 31, 'nombre': 'T31', 'tipo_ia': 'claude'},
          {'id': 32, 'nombre': 'T32', 'tipo_ia': 'claude'}]
    _procesar(tmp_path, raw, ts, mensaje='pulí la landing')
    assert [e[0] for e in enviados] == [31, 32]
    f31, f32 = _fila(31), _fila(32)
    assert f31['origen'] == 'jarvis' and f31['tarea'] == 'pulí la landing'
    assert f31['orquestacion'] == 'pulí la landing'
    assert f32['origen'] is None
    assert (1, {'type': 'agentes_update'}) in broadcasts


# ─── Cierre de tarea: persistir + broadcast, nada más ────────────────────────

def test_procesar_task_event_persiste_y_broadcastea(tmp_path, monkeypatch):
    _proyecto(tmp_path, terminales=(41,))
    broadcasts = []
    from plotspace.core.events import broadcaster

    async def bc(pid, data):
        broadcasts.append((pid, data))
    monkeypatch.setattr(broadcaster, 'broadcast', bc)

    asyncio.run(orch.procesar_task_event_interno(41, 'TASK_BLOCKED', 1,
                                                 motivo='falta la tabla users'))
    conn = get_db()
    try:
        fila = dict(conn.execute('SELECT * FROM task_events WHERE terminal_id = 41').fetchone())
    finally:
        conn.close()
    assert fila['event'] == 'TASK_BLOCKED' and fila['project_id'] == 1
    assert fila['motivo'] == 'falta la tabla users'
    assert broadcasts == [(1, {'type': 'task_event', 'event': 'TASK_BLOCKED',
                               'terminal_id': 41, 'terminal_nombre': 'T41',
                               'motivo': 'falta la tabla users'})]


def test_procesar_task_event_sin_motivo_guarda_null(tmp_path, monkeypatch):
    _proyecto(tmp_path, terminales=(42,))
    from plotspace.core.events import broadcaster

    async def bc(pid, data):
        return None
    monkeypatch.setattr(broadcaster, 'broadcast', bc)
    asyncio.run(orch.procesar_task_event_interno(42, 'TASK_DONE', 1))
    conn = get_db()
    try:
        fila = conn.execute('SELECT motivo FROM task_events WHERE terminal_id = 42').fetchone()
    finally:
        conn.close()
    assert fila['motivo'] is None


# ─── Piezas puras ────────────────────────────────────────────────────────────

def test_objetivo_orquestacion_una_linea_y_recortado():
    assert orch._objetivo_orquestacion('  hola\n  mundo ') == 'hola mundo'
    largo = orch._objetivo_orquestacion('x' * 300)
    assert len(largo) == 120 and largo.endswith('…')
    assert orch._objetivo_orquestacion(None) == ''


def test_sanitizar_normaliza_tarea_y_archivos():
    msg, acts = orch._sanitizar_respuesta({'message': 'ok', 'actions': [
        {'type': 'spawn_terminal', 'name': 'A', 'tarea': '  hacé A  ',
         'archivos': ['a.py', 3, ' ', 'b/*']},
        {'type': 'spawn_terminal', 'name': 'B', 'tarea': 42, 'archivos': 'x'},
        {'type': 'none'}]}, '')
    assert msg == 'ok'
    assert acts[0]['tarea'] == 'hacé A' and acts[0]['archivos'] == ['a.py', 'b/*']
    assert 'tarea' not in acts[1] and 'archivos' not in acts[1]
    assert acts[2] == {'type': 'none'}


def test_sanitizar_basura_da_par_seguro():
    assert orch._sanitizar_respuesta(None, 'texto') == ('texto', [{'type': 'none'}])
    msg, acts = orch._sanitizar_respuesta({'message': '', 'actions': None, 'workflow': {}}, '')
    assert msg == 'Procesado.' and acts == [{'type': 'none'}]


def test_comando_cli_agente():
    assert orch._comando_cli_agente('claude', 'u-1') == \
        'claude --session-id u-1 --dangerously-skip-permissions'
    assert orch._comando_cli_agente('codex') == 'codex'
    assert orch._comando_cli_agente('manual') is None


def test_infos_terminales_toma_la_tarea_persistida(tmp_path):
    """Tras un reinicio no hay envío en memoria: la tarea sale de la fila."""
    from plotspace.core import orq_contexto
    _proyecto(tmp_path)
    t = {'id': 777, 'nombre': 'X', 'tipo_ia': 'claude', 'origen': 'jarvis',
         'tarea': 'hacé X', 'orquestacion': 'orden', 'orquestacion_ts': '2026-01-01T00:00:00'}
    info = orq_contexto.infos_terminales(1, [t], eventos=[], con_live=False)[777]
    assert info['tarea']['texto'] == 'hacé X'
    assert info['origen']['orquestacion'] == 'orden'
    for clave in ('fase', 'esperando', 'fase_hace_s', 'estado', 'archivos',
                  'ultimo_evento', 'dev_servers'):
        assert clave in info
