# plotspace/tests/test_preview_stop_guard.py
"""POST /preview/{pid}/stop con `url`: SOLO mata el puerto si pertenece a un dev
server detectado de ESE proyecto (o a su preview propio). Una url arbitraria no
mata nada; un puerto malformado es 400, no 500."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import plotspace.core.dev_detect as dd
import plotspace.core.puertos as puertos
from plotspace.routers import orchestrator
from plotspace.tests._harness import fresh_db


@pytest.fixture
def ctx(monkeypatch):
    fresh_db()
    monkeypatch.setattr(dd, '_persist_cargado', True)
    monkeypatch.setattr(dd, '_persistir_estado', lambda: None)
    monkeypatch.setattr(dd, '_detectados', {
        7: {'http://localhost:5173': {'terminal_id': 1, 'terminal_nombre': 'A', 'tipo': 'server'}},
        8: {'http://localhost:5999': {'terminal_id': 2, 'terminal_nombre': 'B', 'tipo': 'server'}},
    })
    monkeypatch.setattr(dd, '_descartadas', {})
    matados = []

    def _matar(port):
        matados.append(port)
        return {'ok': True, 'pids': [4242]}
    monkeypatch.setattr(puertos, 'matar_puerto', _matar)
    app = FastAPI()
    app.include_router(orchestrator.router)
    return TestClient(app), matados


def test_url_detectada_del_proyecto_si_se_mata(ctx):
    client, matados = ctx
    r = client.post('/api/orchestrator/preview/7/stop', json={'url': 'http://localhost:5173'})
    assert r.status_code == 200, r.text
    assert matados == [5173]
    assert r.json()['pids'] == [4242]


def test_url_arbitraria_no_mata_nada(ctx):
    client, matados = ctx
    r = client.post('/api/orchestrator/preview/7/stop', json={'url': 'http://localhost:22'})
    assert r.status_code == 200, r.text
    assert matados == []


def test_server_de_otro_proyecto_no_se_mata(ctx):
    client, matados = ctx
    r = client.post('/api/orchestrator/preview/7/stop', json={'url': 'http://localhost:5999'})
    assert r.status_code == 200, r.text
    assert matados == []
    assert 'http://localhost:5999' in dd._detectados[8]


def test_puerto_malformado_da_400(ctx):
    client, matados = ctx
    r = client.post('/api/orchestrator/preview/7/stop', json={'url': 'http://localhost:99999'})
    assert r.status_code == 400, r.text
    assert matados == []
