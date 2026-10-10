"""
Host + CORS + CSRF sobre la app real. No hay token de acceso: la API
responde sin cookie. El candado que queda es Host (anti-rebinding) y Origin
en mutaciones.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi.testclient import TestClient

from plotspace.tests._harness import fresh_db


def _client():
    fresh_db()
    import plotspace.main as main
    return TestClient(main.app)


def test_api_sin_cookie_ok():
    client = _client()
    assert client.get('/api/projects').status_code == 200


def test_host_externo_rechazado_400():
    client = _client()
    r = client.get('/api/projects', headers={'Host': 'evil.attacker.com'})
    assert r.status_code == 400


def test_host_localhost_ok():
    client = _client()
    r = client.get('/api/projects', headers={'Host': 'localhost:3000'})
    assert r.status_code == 200


def test_cors_no_refleja_origin_externo():
    client = _client()
    r = client.get('/api/projects', headers={'Origin': 'http://evil.com'})
    assert r.headers.get('access-control-allow-origin') != 'http://evil.com'


def test_cors_refleja_localhost():
    client = _client()
    r = client.get('/api/projects', headers={'Origin': 'http://localhost:3000'})
    assert r.headers.get('access-control-allow-origin') == 'http://localhost:3000'


def test_cors_no_refleja_ip_publica():
    client = _client()
    r = client.get('/api/projects', headers={'Origin': 'http://93.184.216.34'})
    assert r.headers.get('access-control-allow-origin') != 'http://93.184.216.34'


def test_cors_refleja_lan_privada():
    client = _client()
    r = client.get('/api/projects', headers={'Origin': 'http://192.168.1.50:3000'})
    assert r.headers.get('access-control-allow-origin') == 'http://192.168.1.50:3000'


def test_mutacion_origin_externo_da_403():
    client = _client()
    r = client.post('/api/projects', json={}, headers={'Origin': 'http://evil.com'})
    assert r.status_code == 403


def test_mutacion_sin_origin_pasa_el_gate():
    client = _client()
    r = client.post('/api/projects', json={})
    assert r.status_code not in (401, 403)


def test_mutacion_origin_local_pasa_el_gate():
    client = _client()
    # Un browser en localhost:3000 manda Host y Origin del MISMO origen.
    r = client.post('/api/projects', json={}, headers={'Host': 'localhost:3000',
                                                       'Origin': 'http://localhost:3000'})
    assert r.status_code not in (401, 403)


def test_get_con_origin_externo_no_se_bloquea():
    client = _client()
    r = client.get('/api/projects', headers={'Origin': 'http://evil.com'})
    assert r.status_code == 200


def test_health_no_expone_secretos():
    client = _client()
    r = client.get('/api/health')
    assert r.status_code == 200
    body = r.json()
    assert body.get('status') == 'ok'
    assert 'anthropic_key' not in body
    assert 'whisper_cargado' not in body


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


def test_mutacion_origin_ip_publica_da_403():
    client = _client()
    r = client.post('/api/projects', json={}, headers={'Origin': 'http://93.184.216.34'})
    assert r.status_code == 403


def test_mutacion_origin_otro_puerto_local_da_403():
    # Una página de un dev server en localhost:5173 no puede mutar Jarvis.
    client = _client()
    r = client.post('/api/projects', json={}, headers={'Host': 'localhost:3000',
                                                       'Origin': 'http://localhost:5173'})
    assert r.status_code == 403


def test_ws_eventos_origin_ip_publica_rechazado():
    import pytest
    from starlette.websockets import WebSocketDisconnect
    client = _client()
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect('/ws/events/1', headers={'Origin': 'http://203.0.113.5'}) as ws:
            ws.receive_json()
    assert e.value.code == 4403


def test_ws_eventos_mismo_origen_ok():
    client = _client()
    with client.websocket_connect('/ws/events/1', headers={'Host': 'testserver',
                                                          'Origin': 'http://testserver'}) as ws:
        assert ws.receive_json().get('type') == 'hola'
