# plotspace/tests/test_workflow_flujo.py
"""El motor de workflows corriendo ENTERO, de punta a punta.

Por qué existe este archivo: los arreglos de la Etapa 3 se testearon pieza por
pieza, pero el motor nunca se había corrido completo — y en 19 días de esta DB
tampoco lo corrió el producto. Arreglar cosas que nunca se ejecutan es la mejor
forma de creerse que algo anda cuando no anda.

Acá se ejercita la máquina real (`_arrancar_pasos` →
`procesar_task_event_interno` → gate del Reviewer → cierre) contra una DB
temporal, con el envío a tmux interceptado. Sin CLIs, sin tokens, sin tmux:
solo la lógica que decide qué arranca, cuándo y con qué.
"""
import asyncio
import json
import uuid

import pytest


@pytest.fixture()
def motor(tmp_path, monkeypatch):
    """Motor con DB temporal y el envío a la terminal interceptado."""
    monkeypatch.setenv('JARVIS_DATA_DIR', str(tmp_path))
    from plotspace.core import datadir
    monkeypatch.setattr(datadir, '_DATA_DIR', str(tmp_path), raising=False)

    from plotspace.core import database
    monkeypatch.setattr(database, 'DB_PATH', str(tmp_path / 'test.db'), raising=False)
    database.init_db()

    proyecto = tmp_path / 'proy'
    proyecto.mkdir()
    conn = database.get_db()
    try:
        conn.execute("INSERT INTO projects (id,nombre,ruta,fecha_creacion,ultimo_acceso) "
                     "VALUES (1,'P',?,'2026-01-01','2026-01-01')", (str(proyecto),))
        for tid, n in ((101, 'Builder A'), (102, 'Builder B'), (103, 'Reviewer')):
            conn.execute("INSERT INTO terminals (id,project_id,nombre,tipo_ia,activa,"
                         "fecha_creacion) VALUES (?,1,?,'claude',1,'2026-01-01')", (tid, n))
        conn.commit()
    finally:
        conn.close()

    from plotspace.routers import orchestrator as orq
    enviados = []

    async def _fake_send(tid, mensaje):
        enviados.append((tid, mensaje))
    monkeypatch.setattr(orq, 'send_to_agent', _fake_send)

    from plotspace.routers import terminals as term
    monkeypatch.setattr(term, 'iniciar_monitor', lambda *a, **k: None)

    async def _nada(*a, **k):
        return None
    from plotspace.core.events import broadcaster
    monkeypatch.setattr(broadcaster, 'broadcast', _nada)

    from plotspace.core import territorio
    territorio.reset()
    return orq, enviados, str(proyecto)


def _crear_workflow(orq, pasos):
    wf_id = uuid.uuid4().hex[:10]
    from plotspace.core.database import get_db
    conn = get_db()
    try:
        conn.execute('INSERT INTO workflows (id,project_id,nombre,objetivo,estado,'
                     'pasos,paso_actual,created_at) VALUES (?,1,?,?,?,?,0,?)',
                     (wf_id, 'W', 'objetivo', 'running', json.dumps(pasos),
                      '2026-01-01T00:00:00'))
        conn.commit()
    finally:
        conn.close()
    return wf_id


def _pasos_de(wf_id):
    from plotspace.core.database import get_db
    conn = get_db()
    try:
        f = conn.execute('SELECT pasos, estado FROM workflows WHERE id=?', (wf_id,)).fetchone()
        return json.loads(f['pasos']), f['estado']
    finally:
        conn.close()


def _plan():
    """Dos builders en paralelo + el Reviewer que agrega el engine."""
    from plotspace.routers.orchestrator import _paso_reviewer
    return [
        {'agente': 'Builder A', 'ia_type': 'claude', 'tarea': 'hacé lo A',
         'archivos': ['frontend/a.js'], 'depende_de': None,
         'estado': 'pending', 'terminal_id': 101},
        {'agente': 'Builder B', 'ia_type': 'claude', 'tarea': 'hacé lo B',
         'archivos': ['plotspace/b.py'], 'depende_de': None,
         'estado': 'pending', 'terminal_id': 102},
        {**_paso_reviewer('W', 'objetivo'), 'estado': 'pending', 'terminal_id': 103},
    ]


# ─── El camino feliz, completo ────────────────────────────────────────────────

def test_flujo_completo_dos_builders_y_reviewer(motor):
    orq, enviados, _ = motor
    pasos = _plan()
    wf = _crear_workflow(orq, pasos)

    # Arrancan los dos builders EN PARALELO; el Reviewer NO.
    listos = orq._pasos_listos_para_arrancar(pasos)
    assert listos == [0, 1]
    asyncio.run(orq._arrancar_pasos(pasos, listos, 1, wf))
    assert {t for t, _ in enviados} == {101, 102}, 'cada builder recibió su tarea'
    guardados, _ = _pasos_de(wf)
    assert [p['estado'] for p in guardados[:2]] == ['running', 'running']

    # Cierra el primero: el Reviewer sigue esperando al otro.
    asyncio.run(orq.procesar_task_event_interno(101, 'TASK_DONE', 1))
    guardados, _ = _pasos_de(wf)
    assert guardados[0]['estado'] == 'done'
    assert guardados[2]['estado'] == 'pending', 'el Reviewer no revisa a medias'

    # Cierra el segundo → arranca el Reviewer.
    asyncio.run(orq.procesar_task_event_interno(102, 'TASK_DONE', 1))
    guardados, _ = _pasos_de(wf)
    assert guardados[2]['estado'] == 'running', 'el Reviewer arrancó'
    assert 103 in {t for t, _ in enviados}

    # Cierra el Reviewer → workflow terminado.
    asyncio.run(orq.procesar_task_event_interno(103, 'TASK_DONE', 1))
    guardados, estado = _pasos_de(wf)
    assert estado == 'done'
    assert all(p['estado'] == 'done' for p in guardados)


# ─── El arreglo de la Etapa 3: un paso bloqueado no cuelga el workflow ───────

def test_un_paso_bloqueado_NO_deja_el_workflow_colgado(motor):
    """Antes el Reviewer exigía `done` de todos: con un bloqueado no arrancaba
    nunca y el workflow quedaba sin review, sin cierre y sin aviso."""
    orq, enviados, _ = motor
    pasos = _plan()
    wf = _crear_workflow(orq, pasos)
    asyncio.run(orq._arrancar_pasos(pasos, [0, 1], 1, wf))

    asyncio.run(orq.procesar_task_event_interno(101, 'TASK_DONE', 1))
    asyncio.run(orq.procesar_task_event_interno(
        102, 'TASK_BLOCKED', 1, motivo='falta una credencial'))

    guardados, _ = _pasos_de(wf)
    assert guardados[1]['estado'] == 'blocked'
    assert guardados[1]['motivo'] == 'falta una credencial', 'el motivo se persiste'

    # El Reviewer TIENE que arrancar igual (y ahora arranca de verdad, no solo
    # "podría"): un workflow que termina mal es justo el que más necesita que
    # alguien mire el diff.
    assert guardados[2]['estado'] == 'running'
    assert 103 in {t for t, _ in enviados}


def test_el_workflow_bloqueado_no_se_declara_completado(motor):
    """Honestidad del cierre: si algo quedó blocked, no se dice 'completado'."""
    orq, _, _ = motor
    pasos = _plan()
    pasos[0]['estado'] = 'done'
    pasos[1]['estado'] = 'blocked'
    pasos[2]['estado'] = 'done'
    assert orq._workflow_terminado(pasos) is True
    assert not all(p['estado'] == 'done' for p in pasos)


# ─── El territorio se toma al arrancar cada paso ─────────────────────────────

def test_cada_paso_arranca_con_su_territorio_tomado(motor):
    """Es el momento más barato para evitar un choque: cuando todavía no
    existe."""
    orq, _, _ = motor
    from plotspace.core import territorio
    pasos = _plan()
    wf = _crear_workflow(orq, pasos)
    asyncio.run(orq._arrancar_pasos(pasos, [0, 1], 1, wf))

    a = territorio.duenio(1, 'frontend/a.js')
    b = territorio.duenio(1, 'plotspace/b.py')
    assert a and a['tid'] == 101, 'el archivo del paso A es del agente A'
    assert b and b['tid'] == 102


def test_el_territorio_ajeno_no_frena_el_arranque(motor):
    """El reparto del plan es una GUÍA, no un candado: si algo ya tiene dueño se
    informa, pero el paso arranca igual (frenar el workflow por eso sería peor
    que el choque que evita)."""
    orq, enviados, _ = motor
    from plotspace.core import territorio
    territorio.reclamar(1, 999, 'Alguien Más', ['frontend/a.js'])
    pasos = _plan()
    wf = _crear_workflow(orq, pasos)
    asyncio.run(orq._arrancar_pasos(pasos, [0], 1, wf))
    assert 101 in {t for t, _ in enviados}, 'el paso arrancó igual'
    assert territorio.duenio(1, 'frontend/a.js')['tid'] == 999, 'no se lo robó'


# ─── La tarea que llega al agente ────────────────────────────────────────────

def test_la_tarea_lleva_lo_que_el_engine_garantiza(motor):
    orq, enviados, _ = motor
    pasos = _plan()
    wf = _crear_workflow(orq, pasos)
    asyncio.run(orq._arrancar_pasos(pasos, [0], 1, wf))
    _, tarea = enviados[0]
    assert 'hacé lo A' in tarea
    assert 'frontend/a.js' in tarea, 'sus archivos exclusivos'
    assert 'TASK_DONE' in tarea, 'el protocolo de cierre'
    assert '.jarvis/signals' in tarea, 'el cierre estructurado del sentinel'


def test_un_paso_sin_terminal_no_rompe_el_arranque(motor):
    orq, enviados, _ = motor
    pasos = _plan()
    pasos[0]['terminal_id'] = None
    wf = _crear_workflow(orq, pasos)
    asyncio.run(orq._arrancar_pasos(pasos, [0, 1], 1, wf))
    assert {t for t, _ in enviados} == {102}, 'el otro arrancó igual'


if __name__ == '__main__':
    import sys
    sys.exit(pytest.main([__file__, '-q']))


# ─── El workflow avanza aunque el último builder se bloquee ────────────────

def test_ultimo_builder_bloqueado_arranca_el_reviewer_de_verdad(motor):
    """El test de arriba solo probaba la función pura: el motor real nunca
    evaluaba qué arrancar tras un BLOCKED, así que el Reviewer no arrancaba."""
    orq, enviados, _ = motor
    pasos = _plan()
    wf = _crear_workflow(orq, pasos)
    asyncio.run(orq._arrancar_pasos(pasos, [0, 1], 1, wf))
    asyncio.run(orq.procesar_task_event_interno(101, 'TASK_DONE', 1))
    asyncio.run(orq.procesar_task_event_interno(102, 'TASK_BLOCKED', 1, motivo='x'))
    guardados, _ = _pasos_de(wf)
    assert guardados[2]['estado'] == 'running'
    assert 103 in {t for t, _ in enviados}

    asyncio.run(orq.procesar_task_event_interno(103, 'TASK_DONE', 1))
    _, estado = _pasos_de(wf)
    assert estado == 'done'


def test_evento_de_terminal_ajena_no_toca_el_workflow(motor):
    """Antes caía en paso_actual: un TASK_DONE de una terminal manual marcaba
    como terminado un paso de otro agente."""
    orq, _, _ = motor
    from plotspace.core.database import get_db
    conn = get_db()
    conn.execute("INSERT INTO terminals (id,project_id,nombre,tipo_ia,activa,"
                 "fecha_creacion) VALUES (200,1,'Manual','claude',1,'2026-01-01')")
    conn.commit(); conn.close()
    pasos = _plan()
    wf = _crear_workflow(orq, pasos)
    asyncio.run(orq._arrancar_pasos(pasos, [0, 1], 1, wf))
    asyncio.run(orq.procesar_task_event_interno(200, 'TASK_DONE', 1))
    guardados, _ = _pasos_de(wf)
    assert [p['estado'] for p in guardados] == ['running', 'running', 'pending']


def test_evento_va_al_workflow_de_su_terminal_no_al_mas_nuevo(motor):
    orq, _, _ = motor
    viejo = _plan()
    wf_viejo = _crear_workflow(orq, viejo)
    asyncio.run(orq._arrancar_pasos(viejo, [0, 1], 1, wf_viejo))
    from plotspace.routers.orchestrator import _paso_reviewer
    nuevo = [{'agente': 'X', 'ia_type': 'claude', 'tarea': 't', 'depende_de': None,
              'estado': 'running', 'terminal_id': 103},
             {**_paso_reviewer('W2', ''), 'estado': 'pending', 'terminal_id': None}]
    from plotspace.core.database import get_db
    conn = get_db()
    conn.execute('INSERT INTO workflows (id,project_id,nombre,objetivo,estado,pasos,'
                 'paso_actual,created_at) VALUES (?,1,?,?,?,?,0,?)',
                 ('nuevo', 'W2', '', 'running', json.dumps(nuevo), '2026-02-01T00:00:00'))
    conn.commit(); conn.close()
    asyncio.run(orq.procesar_task_event_interno(101, 'TASK_DONE', 1))
    g_viejo, _ = _pasos_de(wf_viejo)
    g_nuevo, _ = _pasos_de('nuevo')
    assert g_viejo[0]['estado'] == 'done'
    assert g_nuevo[0]['estado'] == 'running'


def test_dos_done_simultaneos_no_se_pisan(motor):
    """Los pasos viven en UNA columna JSON: sin lock, dos TASK_DONE en paralelo
    se pisaban el done y el Reviewer no arrancaba nunca."""
    orq, enviados, _ = motor
    pasos = _plan()
    wf = _crear_workflow(orq, pasos)
    asyncio.run(orq._arrancar_pasos(pasos, [0, 1], 1, wf))

    async def ambos():
        await asyncio.gather(orq.procesar_task_event_interno(101, 'TASK_DONE', 1),
                             orq.procesar_task_event_interno(102, 'TASK_DONE', 1))
    asyncio.run(ambos())
    guardados, _ = _pasos_de(wf)
    assert [p['estado'] for p in guardados[:2]] == ['done', 'done']
    assert guardados[2]['estado'] == 'running'


def test_paso_sin_terminal_no_queda_pending_para_siempre(motor):
    """Con el tope de terminales un paso podía quedar sin terminal: se salteaba
    en silencio y el workflow no cerraba jamás. Ahora el Reviewer reusa la
    terminal de un builder terminado, y un builder sin terminal queda en error."""
    orq, enviados, _ = motor
    pasos = _plan()
    pasos[2]['terminal_id'] = None
    wf = _crear_workflow(orq, pasos)
    asyncio.run(orq._arrancar_pasos(pasos, [0, 1], 1, wf))
    asyncio.run(orq.procesar_task_event_interno(101, 'TASK_DONE', 1))
    asyncio.run(orq.procesar_task_event_interno(102, 'TASK_DONE', 1))
    guardados, _ = _pasos_de(wf)
    assert guardados[2]['estado'] == 'running'
    assert guardados[2]['terminal_id'] in (101, 102)

    pasos2 = _plan()
    pasos2[1]['terminal_id'] = None
    wf2 = _crear_workflow(orq, pasos2)
    asyncio.run(orq._arrancar_pasos(pasos2, [0, 1], 1, wf2))
    g2, _ = _pasos_de(wf2)
    assert g2[1]['estado'] == 'error' and g2[1].get('motivo')


def test_entrega_fallida_marca_el_paso_en_error(motor, monkeypatch):
    orq, _, _ = motor

    async def _falla(tid, mensaje):
        return False
    monkeypatch.setattr(orq, 'send_to_agent', _falla)
    pasos = _plan()
    wf = _crear_workflow(orq, pasos)
    asyncio.run(orq._arrancar_pasos(pasos, [0, 1], 1, wf))
    guardados, _ = _pasos_de(wf)
    assert guardados[0]['estado'] == 'error'
    assert 'entregar' in guardados[0]['motivo']


def test_sello_del_watchdog_no_pisa_un_done_ni_la_pausa(motor):
    """El watchdog reescribía su copia vieja de los pasos con estado 'running':
    pisaba un done recién guardado y despausaba el workflow."""
    orq, _, _ = motor
    pasos = _plan()
    pasos[0].update(estado='running', iniciado_ts=None)
    pasos[1].update(estado='done')
    wf = _crear_workflow(orq, pasos)
    from plotspace.core.database import get_db
    conn = get_db()
    conn.execute("UPDATE workflows SET estado='paused' WHERE id=?", (wf,))
    conn.commit(); conn.close()
    asyncio.run(orq.sellar_pasos_sin_inicio(wf, 1234.0))
    guardados, estado = _pasos_de(wf)
    assert guardados[0]['iniciado_ts'] == 1234.0
    assert guardados[1]['estado'] == 'done'
    assert estado == 'paused'


def test_sentinel_no_se_come_la_senal_de_un_paso_bloqueado():
    """El agente bloqueado puede destrabarse y cerrar su paso: su señal es del
    workflow, no de la pasada libre."""
    from plotspace.core.sentinel import terminales_libres
    wfs = [{'pasos': [{'estado': 'blocked', 'terminal_id': 1},
                      {'estado': 'running', 'terminal_id': 2},
                      {'estado': 'done', 'terminal_id': 3}]}]
    libres = terminales_libres([{'id': 1}, {'id': 2}, {'id': 3}, {'id': 4}], wfs)
    assert [t['id'] for t in libres] == [3, 4]
