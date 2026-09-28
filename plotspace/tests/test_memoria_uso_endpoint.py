"""GET /memory/uso — stream de recall para la pestaña Live de Memoria.

Invariantes:
1. Devuelve SOLO los eventos del proyecto pedido, lo más nuevo primero, con el
   nombre de la terminal (LEFT JOIN: una terminal borrada no rompe nada).
2. conteo separa inyectada (el recall la sugirió) de leída (el agente la citó).
3. La ruta no la captura /memory/{slug} (orden de declaración).
"""
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plotspace.tests._harness import fresh_db, make_client_and_project
from plotspace.core import database as db
from plotspace.routers import memory


def _cliente():
    fresh_db()
    d = tempfile.mkdtemp()
    _, pid = make_client_and_project(d)
    app = FastAPI()
    app.include_router(memory.router)
    return TestClient(app), pid


def _terminal(pid, nombre):
    conn = db.get_db()
    try:
        cur = conn.execute(
            "INSERT INTO terminals (project_id, nombre, tipo_ia, fecha_creacion) VALUES (?, ?, 'claude', '2026-01-01')",
            (pid, nombre))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_uso_devuelve_eventos_del_proyecto_con_terminal():
    client, pid = _cliente()
    tid = _terminal(pid, 'Backend')
    db.registrar_uso_memorias(pid, tid, ['flow-control'], 'inyectada')
    db.registrar_uso_memorias(pid, tid, ['flow-control', 'puertos'], 'done')
    db.registrar_uso_memorias(pid + 99, tid, ['de-otro-proyecto'], 'done')
    r = client.get(f"/api/projects/{pid}/memory/uso")
    assert r.status_code == 200, r.text
    data = r.json()
    slugs = [e['slug'] for e in data['eventos']]
    assert 'de-otro-proyecto' not in slugs
    assert slugs[0] == 'puertos', 'lo más nuevo primero'
    assert data['eventos'][0]['terminal'] == 'Backend'
    assert data['conteo']['flow-control'] == {'inyectada': 1, 'leida': 1}


def test_uso_terminal_borrada_y_limite():
    client, pid = _cliente()
    db.registrar_uso_memorias(pid, 12345, ['a', 'b', 'c'], 'blocked')
    r = client.get(f"/api/projects/{pid}/memory/uso?n=2")
    data = r.json()
    assert len(data['eventos']) == 2
    assert data['eventos'][0]['terminal'] is None


def test_uso_proyecto_inexistente_404():
    client, _ = _cliente()
    assert client.get("/api/projects/9999/memory/uso").status_code == 404
