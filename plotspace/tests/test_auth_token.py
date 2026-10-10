"""Acceso desde la red con token.

En la PC propia (Jarvis escuchando en 127.0.0.1) no hay login: abrís
http://localhost:3000 y entrás. Cuando Jarvis escucha en la RED (Docker,
`--host 0.0.0.0` para el celular, un servidor tipo Unraid) cualquiera de esa red
llegaba a una terminal. Ahora, en ese caso, el que entra desde OTRA máquina
necesita el token (link que se imprime al arrancar); lo que corre en la misma
máquina (los hooks de los agentes, el navegador local) sigue igual.
"""
import os
import stat

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from plotspace.core import auth
from plotspace.tests._harness import fresh_db

LAN = ('192.168.1.20', 51000)      # otra máquina de la red
LOCAL = ('127.0.0.1', 51000)       # la misma máquina


@pytest.fixture
def red(monkeypatch, tmp_path):
    """Jarvis escuchando en 0.0.0.0, token auto-generado en un data dir temporal."""
    monkeypatch.setattr(auth, 'host_de_escucha', lambda: '0.0.0.0')
    monkeypatch.delenv('JARVIS_TOKEN', raising=False)
    monkeypatch.setattr(auth, '_RUTA_TOKEN', str(tmp_path / 'acceso-token'))
    monkeypatch.setattr(auth, '_token_cache', None)
    fresh_db()
    import plotspace.main as main
    return main.app


def _cli(app, client=LAN, **kw):
    return TestClient(app, client=client, **kw)


# ─── Cuándo se exige ─────────────────────────────────────────────────────────

def test_local_no_pide_token(monkeypatch):
    monkeypatch.setattr(auth, 'host_de_escucha', lambda: '127.0.0.1')
    monkeypatch.delenv('JARVIS_TOKEN', raising=False)
    assert auth.token_requerido() is False


def test_red_pide_token(monkeypatch):
    monkeypatch.setattr(auth, 'host_de_escucha', lambda: '0.0.0.0')
    monkeypatch.delenv('JARVIS_TOKEN', raising=False)
    assert auth.token_requerido() is True


def test_jarvis_token_off_lo_apaga(monkeypatch):
    monkeypatch.setattr(auth, 'host_de_escucha', lambda: '0.0.0.0')
    monkeypatch.setenv('JARVIS_TOKEN', 'off')
    assert auth.token_requerido() is False


def test_jarvis_token_explicito(monkeypatch):
    monkeypatch.setattr(auth, 'host_de_escucha', lambda: '127.0.0.1')
    monkeypatch.setenv('JARVIS_TOKEN', 'mi-secreto-largo')
    monkeypatch.setattr(auth, '_token_cache', None)
    assert auth.token_requerido() is True
    assert auth.token_actual() == 'mi-secreto-largo'


def test_token_generado_se_guarda_0600(monkeypatch, tmp_path):
    monkeypatch.delenv('JARVIS_TOKEN', raising=False)
    ruta = tmp_path / 'acceso-token'
    monkeypatch.setattr(auth, '_RUTA_TOKEN', str(ruta))
    monkeypatch.setattr(auth, '_token_cache', None)
    t = auth.token_actual()
    assert len(t) >= 24 and ruta.read_text().strip() == t
    assert stat.S_IMODE(os.stat(ruta).st_mode) == 0o600
    monkeypatch.setattr(auth, '_token_cache', None)
    assert auth.token_actual() == t            # persiste entre reinicios


def test_cliente_local():
    assert auth.cliente_local('127.0.0.1', {})
    assert auth.cliente_local('::1', {})
    assert not auth.cliente_local('192.168.1.20', {})
    assert not auth.cliente_local('172.17.0.1', {})          # gateway de Docker = afuera
    # Un proxy inverso en la misma máquina llega desde loopback pero NO es local
    assert not auth.cliente_local('127.0.0.1', {'x-forwarded-for': '203.0.113.9'})
    assert not auth.cliente_local('127.0.0.1', {'forwarded': 'for=203.0.113.9'})


# ─── HTTP ────────────────────────────────────────────────────────────────────

def test_api_sin_token_401(red):
    assert _cli(red).get('/api/projects').status_code == 401


def test_pagina_sin_token_muestra_login(red):
    r = _cli(red).get('/workspace')
    assert r.status_code == 401
    assert 'token' in r.text.lower() and '<form' in r.text


def test_misma_maquina_no_necesita_token(red):
    assert _cli(red, client=LOCAL).get('/api/projects').status_code == 200


def test_health_sin_token(red):
    assert _cli(red).get('/api/health').status_code == 200


def test_link_con_token_deja_cookie_y_limpia_la_url(red):
    c = _cli(red, follow_redirects=False)
    tok = auth.token_actual()
    r = c.get(f'/workspace?id=3&token={tok}')
    assert r.status_code == 303
    assert r.headers['location'] == '/workspace?id=3'      # el token no queda en la URL
    assert auth.TOKEN_COOKIE in r.headers.get('set-cookie', '')
    assert 'httponly' in r.headers['set-cookie'].lower()
    assert c.get('/api/projects').status_code == 200       # la cookie ya autoriza


def test_token_incorrecto_401(red):
    c = _cli(red)
    assert c.get('/api/projects?token=nope').status_code == 401
    c.cookies.set(auth.TOKEN_COOKIE, 'nope')
    assert c.get('/api/projects').status_code == 401


def test_header_x_jarvis_token(red):
    r = _cli(red).get('/api/projects', headers={'X-Jarvis-Token': auth.token_actual()})
    assert r.status_code == 200


def test_con_token_se_puede_entrar_por_nombre(red):
    # Unraid: http://tower:3000. Sin token el Host por nombre se rechazaba (400).
    c = _cli(red)
    c.cookies.set(auth.TOKEN_COOKIE, auth.token_actual())
    assert c.get('/api/projects', headers={'Host': 'tower:3000'}).status_code == 200
    r = c.post('/api/projects', json={}, headers={'Host': 'tower:3000', 'Origin': 'http://tower:3000'})
    assert r.status_code not in (400, 401, 403)


def test_con_token_igual_rechaza_origin_ajeno(red):
    c = _cli(red)
    c.cookies.set(auth.TOKEN_COOKIE, auth.token_actual())
    r = c.post('/api/projects', json={}, headers={'Host': 'tower:3000', 'Origin': 'http://evil.com'})
    assert r.status_code == 403


def test_por_nombre_sin_token_no_entra(red):
    assert _cli(red).get('/api/projects', headers={'Host': 'tower:3000'}).status_code == 401


# ─── WebSockets ──────────────────────────────────────────────────────────────

def test_ws_sin_token_rechazado(red):
    with pytest.raises(WebSocketDisconnect) as e:
        with _cli(red).websocket_connect('/ws/events/1') as ws:
            ws.receive_json()
    assert e.value.code == 4401


def test_ws_con_cookie_ok(red):
    c = _cli(red)
    c.cookies.set(auth.TOKEN_COOKIE, auth.token_actual())
    with c.websocket_connect('/ws/events/1', headers={'Origin': 'http://testserver'}) as ws:
        assert ws.receive_json()['type'] == 'hola'


def test_ws_local_sin_token_ok(red):
    with _cli(red, client=LOCAL).websocket_connect('/ws/events/1') as ws:
        assert ws.receive_json()['type'] == 'hola'
