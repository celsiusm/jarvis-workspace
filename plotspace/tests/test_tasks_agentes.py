# plotspace/tests/test_tasks_agentes.py
"""Monitor en vivo de agentes (pestaña Tareas): derivación del estado (puro),
mapa de «terminó» alimentado por eventos, agrupación por orquestación y el
endpoint GET /api/projects/{id}/agentes con las fuentes mockeadas."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plotspace.core.database import get_db
from plotspace.routers import tasks


# ─── derivar_estado ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('info,termino,esperado', [
    ({'estado': 'caido', 'esperando': True, 'fase': 'trabajando'}, True, 'caido'),
    ({'estado': 'sin_sesion'}, False, 'caido'),
    ({'esperando': True, 'fase': 'trabajando'}, True, 'esperando'),
    ({'fase': 'trabajando'}, True, 'trabajando'),
    ({'fase': 'arrancando'}, True, 'arrancando'),
    ({'fase': 'idle'}, True, 'termino'),
    ({'fase': 'idle'}, False, 'quieto'),
    ({}, False, 'quieto'),
    (None, False, 'quieto'),
])
def test_derivar_estado_prioridad(info, termino, esperado):
    assert tasks.derivar_estado(info, termino) == esperado


def test_limpiar_tarea_corta_el_protocolo():
    txt = ('Agregá tests al router de pagos\n\nAntes de empezar leé .jarvis/memory/INDEX.md. '
           'Cuando termines escribí TASK_DONE.')
    assert tasks.limpiar_tarea(txt) == 'Agregá tests al router de pagos'
    assert tasks.limpiar_tarea('') == ''
    largo = 'x' * 2000
    assert len(tasks.limpiar_tarea(largo)) == tasks._TOPE_TAREA


# ─── mapa de «terminó» ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _limpiar_terminados():
    tasks._terminados.clear()
    yield
    tasks._terminados.clear()


def test_marcar_evento_termino_y_reset():
    tasks.marcar_evento({'type': 'agente_termino', 'terminal_id': 7}, ahora=100.0)
    assert tasks._terminados[7]['via'] == 'agente_termino'
    tasks.marcar_evento({'type': 'agente_trabajando', 'terminal_id': 7})
    assert 7 not in tasks._terminados
    tasks.marcar_evento({'type': 'task_event', 'terminal_id': '8', 'event': 'TASK_BLOCKED',
                         'motivo': 'falta la API key'}, ahora=5.0)
    assert tasks._terminados[8] == {'ts': 5.0, 'via': 'TASK_BLOCKED', 'motivo': 'falta la API key'}
    tasks.marcar_evento({'type': 'agente_espera', 'terminal_id': 8})
    assert 8 not in tasks._terminados
    # eventos ajenos o sin terminal: no-op
    tasks.marcar_evento({'type': 'live_update'})
    tasks.marcar_evento({'type': 'task_event', 'terminal_id': 9, 'event': 'TASK_STARTED'})
    assert tasks._terminados == {}


def test_listener_registrado_una_vez():
    from plotspace.core.events import broadcaster
    tasks.registrar_listener()
    tasks.registrar_listener()
    assert broadcaster._listeners.count(tasks._on_evento) == 1


# ─── armar_agente / agrupar ───────────────────────────────────────────────────

def _row(tid, **kw):
    base = {'id': tid, 'nombre': f'Agente {tid}', 'tipo_ia': 'claude',
            'fecha_creacion': '2026-09-28T10:00:00', 'origen': None, 'tarea': None,
            'orquestacion': None, 'orquestacion_ts': None}
    base.update(kw)
    return base


def test_armar_agente_termino_tiempo_y_evento():
    term = {'ts': 40.0, 'via': 'TASK_DONE', 'motivo': 'listo'}
    a = tasks.armar_agente(_row(1), {'fase': 'idle', 'fase_hace_s': 3}, 'Refactor auth', term, ahora=100.0)
    assert a['estado'] == 'termino'
    assert a['estado_hace_s'] == 60
    assert a['titulo'] == 'Refactor auth'
    assert a['ultimo_evento'] == {'event': 'TASK_DONE', 'motivo': 'listo', 'hace_s': 60}
    assert a['origen'] == 'usuario'


def test_armar_agente_pregunta_en_pantalla_al_arrancar():
    a = tasks.armar_agente(_row(2), {'fase': 'arrancando'}, None, None, pregunta_en_pantalla=True)
    assert a['estado'] == 'esperando'
    # una shell con «[y/N]» en pantalla NO se marca esperando
    s = tasks.armar_agente(_row(3, tipo_ia='shell'), {'fase': 'idle'}, None, None, pregunta_en_pantalla=True)
    assert s['estado'] == 'quieto'
    # trabajando gana a la heurística de pantalla
    w = tasks.armar_agente(_row(4), {'fase': 'trabajando'}, None, None, pregunta_en_pantalla=True)
    assert w['estado'] == 'trabajando'


def test_armar_agente_tarea_columna_gana_a_envio():
    info = {'fase': 'trabajando', 'tarea': {'texto': 'otra', 'hace_s': 12},
            'archivos': [{'path': 'a.py', 'writes': 2, 'reads': 1}],
            'dev_servers': ['http://localhost:5173'],
            'ultimo_evento': {'event': 'TASK_ERROR', 'motivo': 'rompió', 'hace_s': 9.7}}
    a = tasks.armar_agente(_row(5, tarea='La de la columna', origen='jarvis', orquestacion='Login'), info, None, None)
    assert a['tarea'] == {'texto': 'La de la columna', 'hace_s': None}
    assert a['archivos'] == [{'path': 'a.py', 'writes': 2}]
    assert a['dev_servers'] == ['http://localhost:5173']
    assert a['ultimo_evento']['motivo'] == 'rompió'
    assert a['origen'] == 'jarvis' and a['orquestacion'] == 'Login'
    b = tasks.armar_agente(_row(6), info, None, None)
    assert b['tarea'] == {'texto': 'otra', 'hace_s': 12}


def test_agrupar_orquestaciones_y_resumen():
    ags = [
        tasks.armar_agente(_row(1, origen='jarvis', orquestacion='Login', orquestacion_ts='2026-09-28T09'),
                           {'fase': 'trabajando'}, None, None),
        tasks.armar_agente(_row(2, origen='jarvis', orquestacion='Login', orquestacion_ts='2026-09-28T09'),
                           {'fase': 'idle'}, None, {'ts': 0, 'via': 'agente_termino'}),
        tasks.armar_agente(_row(3, origen='jarvis', orquestacion='Pagos', orquestacion_ts='2026-09-28T11'),
                           {'fase': 'trabajando'}, None, None),
        tasks.armar_agente(_row(4), {'estado': 'caido'}, None, None),
    ]
    grupos = tasks.agrupar_orquestaciones(ags)
    assert [g['objetivo'] for g in grupos] == ['Pagos', 'Login']
    assert grupos[1]['agentes'] == [1, 2]
    assert grupos[1]['resumen'] == {'trabajando': 1, 'termino': 1}
    assert tasks.resumir(ags) == {'trabajando': 2, 'termino': 1, 'caido': 1}


# ─── endpoint ─────────────────────────────────────────────────────────────────

def _proyecto_con_terminales():
    conn = get_db()
    try:
        cur = conn.execute("INSERT INTO projects (nombre, ruta, fecha_creacion, ultimo_acceso) "
                           "VALUES (?, ?, ?, ?)",
                           ('demo', '/tmp', '2026-09-28', '2026-09-28'))
        pid = cur.lastrowid
        ids = []
        for nombre, tipo, activa in (('Backend', 'claude', 1), ('Front', 'codex', 1),
                                     ('Vieja', 'claude', 0)):
            c = conn.execute("INSERT INTO terminals (project_id, nombre, tipo_ia, activa, fecha_creacion) "
                             "VALUES (?, ?, ?, ?, '2026-09-28T10:00:00')", (pid, nombre, tipo, activa))
            ids.append(c.lastrowid)
        conn.commit()
        return pid, ids
    finally:
        conn.close()


def _client():
    app = FastAPI()
    app.include_router(tasks.router)
    return TestClient(app)


def test_endpoint_agentes(monkeypatch):
    pid, (t1, t2, _t3) = _proyecto_con_terminales()
    monkeypatch.setattr(tasks, '_infos', lambda p, ts: {
        t1: {'fase': 'trabajando', 'fase_hace_s': 30, 'archivos': [{'path': 'x.py', 'writes': 1}]},
        t2: {'fase': 'idle', 'esperando': True, 'fase_hace_s': 4},
    })
    monkeypatch.setattr(tasks, '_titulos', lambda: {t1: 'Escribiendo tests', t2: None})

    async def _sin_preguntas(tids):
        return {}
    monkeypatch.setattr(tasks, '_preguntas', _sin_preguntas)

    r = _client().get(f'/api/projects/{pid}/agentes')
    assert r.status_code == 200
    data = r.json()
    assert [a['id'] for a in data['agentes']] == [t1, t2]          # la inactiva no sale
    a1, a2 = data['agentes']
    assert a1['estado'] == 'trabajando' and a1['titulo'] == 'Escribiendo tests'
    assert a1['estado_hace_s'] == 30 and a1['tipo_ia'] == 'claude'
    assert a2['estado'] == 'esperando' and a2['nombre'] == 'Front'
    assert data['resumen'] == {'trabajando': 1, 'esperando': 1}
    assert data['orquestaciones'] == []


def test_endpoint_sin_terminales():
    r = _client().get('/api/projects/999/agentes')
    assert r.status_code == 200
    assert r.json() == {'agentes': [], 'orquestaciones': [], 'resumen': {}}


def test_endpoint_fuentes_caidas_degradan(monkeypatch):
    """Si orq_contexto o tmux fallan, el endpoint igual responde (todos quietos)."""
    pid, (t1, t2, _t3) = _proyecto_con_terminales()
    from plotspace.core import orq_contexto

    def _boom(*a, **k):
        raise RuntimeError('sin tabla')
    monkeypatch.setattr(orq_contexto, 'infos_terminales', _boom)
    monkeypatch.setattr(tasks, '_titulos', lambda: {})

    async def _sin_preguntas(tids):
        return {}
    monkeypatch.setattr(tasks, '_preguntas', _sin_preguntas)
    r = _client().get(f'/api/projects/{pid}/agentes')
    assert r.status_code == 200
    assert {a['estado'] for a in r.json()['agentes']} == {'quieto'}


def test_viejos_endpoints_del_kanban_no_existen():
    c = _client()
    assert c.get('/api/projects/1/tasks').status_code == 404
    assert c.post('/api/tasks/1/asignar', json={'terminal_id': 1}).status_code in (404, 405)
