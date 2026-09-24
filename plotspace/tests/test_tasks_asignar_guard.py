# plotspace/tests/test_tasks_asignar_guard.py
"""POST /tasks/{id}/asignar: la terminal tiene que ser del MISMO proyecto que
la tarea (404 si no), y si el pegado al agente falla (send_to_agent → False)
responde 409 y la tarea NO queda 'running' (antes quedaba colgada en running
sin que el agente la hubiera recibido)."""
import asyncio
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plotspace.core.database import get_db
from plotspace.routers import orchestrator as orq
from plotspace.routers import tasks
from plotspace.routers import terminals as term
from plotspace.tests._harness import fresh_db, make_client_and_project


def _terminal(pid, nombre):
    conn = get_db()
    try:
        cur = conn.execute("INSERT INTO terminals (project_id, nombre, tipo_ia, activa, "
                           "fecha_creacion) VALUES (?, ?, 'claude', 1, '2026-01-01')",
                           (pid, nombre))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _estado(task_id):
    conn = get_db()
    try:
        return conn.execute('SELECT estado FROM tasks WHERE id = ?', (task_id,)).fetchone()['estado']
    finally:
        conn.close()


@pytest.fixture
def ctx(monkeypatch):
    fresh_db()
    _, pid = make_client_and_project(tempfile.mkdtemp())
    _, otro = make_client_and_project(tempfile.mkdtemp())
    app = FastAPI()
    app.include_router(tasks.router)
    client = TestClient(app)
    monitores = []
    monkeypatch.setattr(term, 'iniciar_monitor', lambda tid, p: monitores.append(tid))

    async def _nada(*a, **k):
        return None
    monkeypatch.setattr(tasks, '_broadcast_tasks', _nada)
    r = client.post(f'/api/projects/{pid}/tasks', json={'titulo': 'hacer algo'})
    assert r.status_code in (200, 201), r.text
    return client, pid, otro, r.json()['id'], monitores


def test_terminal_de_otro_proyecto_404(ctx, monkeypatch):
    client, pid, otro, task_id, monitores = ctx
    ajena = _terminal(otro, 'Ajena')
    enviados = []

    async def _send(tid, m):
        enviados.append(tid)
        return True
    monkeypatch.setattr(orq, 'send_to_agent', _send)
    r = client.post(f'/api/tasks/{task_id}/asignar', json={'terminal_id': ajena})
    assert r.status_code == 404, r.text
    assert enviados == [] and monitores == []
    assert _estado(task_id) == 'backlog'


def test_pegado_fallido_409_y_no_queda_running(ctx, monkeypatch):
    client, pid, otro, task_id, monitores = ctx
    tid = _terminal(pid, 'Propia')

    async def _send(t, m):
        return False
    monkeypatch.setattr(orq, 'send_to_agent', _send)
    r = client.post(f'/api/tasks/{task_id}/asignar', json={'terminal_id': tid})
    assert r.status_code == 409, r.text
    assert monitores == []
    assert _estado(task_id) == 'backlog'


def test_pegado_ok_queda_running(ctx, monkeypatch):
    client, pid, otro, task_id, monitores = ctx
    tid = _terminal(pid, 'Propia')

    async def _send(t, m):
        return True
    monkeypatch.setattr(orq, 'send_to_agent', _send)
    r = client.post(f'/api/tasks/{task_id}/asignar', json={'terminal_id': tid})
    assert r.status_code == 200, r.text
    assert r.json()['estado'] == 'running'
    assert monitores == [tid]


class _Backend:
    def __init__(self, existe):
        self._existe = existe
        self.teclas = []

    def existe(self, tid):
        return self._existe

    def enviar_tecla(self, tid, tecla):
        self.teclas.append(tecla)


def test_send_to_agent_devuelve_false_sin_sesion(monkeypatch):
    fresh_db()
    monkeypatch.setattr(orq, 'backend', lambda: _Backend(False))
    assert asyncio.run(orq.send_to_agent(123, 'x')) is False


def test_send_to_agent_devuelve_false_si_el_pegado_falla(monkeypatch):
    fresh_db()
    be = _Backend(True)
    monkeypatch.setattr(orq, 'backend', lambda: be)
    monkeypatch.setattr(orq.subprocess, 'run',
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, b'', b'no pane'))
    assert asyncio.run(orq.send_to_agent(123, 'x')) is False
    assert be.teclas == []
    # Y no se registra un SENT de algo que nunca llegó al agente.
    conn = get_db()
    try:
        n = conn.execute("SELECT COUNT(*) FROM task_events WHERE terminal_id = 123 "
                         "AND event = 'SENT'").fetchone()[0]
    finally:
        conn.close()
    assert n == 0


def test_send_to_agent_devuelve_true_si_pego(monkeypatch):
    fresh_db()
    be = _Backend(True)
    monkeypatch.setattr(orq, 'backend', lambda: be)
    monkeypatch.setattr(orq.subprocess, 'run',
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, b'', b''))

    async def _sin_espera(*a):
        return None
    monkeypatch.setattr(orq.asyncio, 'sleep', _sin_espera)
    assert asyncio.run(orq.send_to_agent(123, 'x')) is True
    assert be.teclas == ['Enter', 'Enter']
