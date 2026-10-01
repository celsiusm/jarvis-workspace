"""Tests de la detección de CLIs de agente (`core/clis.py`).

POR QUÉ IMPORTA
===============
Hoy Jarvis da por hecho que los CLIs están: abre la terminal y tipea `claude`.
En la máquina de alguien que acaba de instalar la app, eso muestra una terminal
negra con `command not found` — y esa sería la primera impresión de todo
usuario nuevo.

La regla que estos tests protegen: **no se promete lo que no se puede
cumplir.** Antigravity no sale de npm, y sin Node no hay forma de instalar
nada: en esos casos la app informa en vez de ofrecer un botón muerto.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plotspace.core import clis


def _por_id(lista, cid):
    return next(c for c in lista if c['id'] == cid)


# ── detección ────────────────────────────────────────────────────────────

def test_detecta_lo_que_hay_en_el_sistema():
    detectados = clis.detectar(existe_local=lambda b, path=None: b == 'claude')
    assert _por_id(detectados, 'claude')['instalado'] is True
    assert _por_id(detectados, 'codex')['instalado'] is False


def test_una_sonda_rota_no_miente():
    """Si no se puede preguntar, la respuesta honesta es "no está": ofrecer
    instalarlo es recuperable; decir que está y que falle al abrir, no."""
    detectados = clis.detectar(
        existe_local=lambda b, path=None: (_ for _ in ()).throw(OSError('PATH roto')),
    )
    assert all(c['instalado'] is False for c in detectados)


def test_la_deteccion_real_no_explota():
    """Sin mocks, contra el sistema de verdad. NO se asume que haya ningún CLI
    instalado: en la máquina de desarrollo están todos y en un runner de CI no
    hay ninguno, y las dos respuestas son correctas. Lo que se verifica es que
    la detección conteste sin romperse, que es de lo que depende la pantalla de
    bienvenida."""
    detectados = clis.detectar()
    assert len(detectados) == len(clis.CATALOGO)
    for c in detectados:
        assert isinstance(c['instalado'], bool), c
        assert c['nombre'] and c['id']


# ── no prometer lo que no se puede cumplir ───────────────────────────────

def test_antigravity_se_detecta_pero_no_se_ofrece_instalar():
    """Es una app de escritorio de Google, no un paquete de npm. Un botón
    «Instalar» que no puede cumplir es peor que una línea que explique."""
    detectados = clis.detectar(existe_local=lambda b, path=None: False)
    assert _por_id(detectados, 'antigravity')['instalable'] is False
    assert _por_id(detectados, 'claude')['instalable'] is True
    assert clis.comando_instalar('antigravity') is None


def test_un_cli_desconocido_no_arma_un_comando():
    assert clis.comando_instalar('no-existe') is None
    assert clis.comando_instalar('') is None


def test_cursor_se_detecta_por_agent_o_cursor_agent():
    """El curl-installer de Cursor crea DOS symlinks al mismo binario (`agent`
    de nuevo nombre + `cursor-agent` legacy): con que éste o aquel exista
    por el PATH, se da por instalado."""
    for b in ('agent', 'cursor-agent'):
        detectados = clis.detectar(existe_local=lambda x, path=None, b=b: x == b)
        assert _por_id(detectados, 'cursor')['instalado'] is True, b
    detectados = clis.detectar(existe_local=lambda b, path=None: b == 'codex')
    assert _por_id(detectados, 'cursor')['instalado'] is False


def test_cursor_no_se_instala_desde_npm():
    """Cursor tiene su curl-installer oficial: no hay paquete npm que prometer
    (mismo trato que Antigravity)."""
    detectados = clis.detectar(existe_local=lambda b, path=None: False)
    assert _por_id(detectados, 'cursor')['instalable'] is False
    assert clis.comando_instalar('cursor') is None


def test_pi_se_instala_con_ignore_scripts():
    """El quickstart de Pi manda `npm install -g --ignore-scripts` — el
    comando debe respetarlo tal cual."""
    cmd = clis.comando_instalar('pi')
    assert cmd == ['npm', 'install', '-g', '--ignore-scripts',
                   '@earendil-works/pi-coding-agent'], cmd


# ── el comando de instalación ────────────────────────────────────────────

def test_instalar_en_el_sistema():
    cmd = clis.comando_instalar('claude')
    assert cmd == ['npm', 'install', '-g', '@anthropic-ai/claude-code'], cmd


def test_sin_node_no_se_promete_instalar_nada():
    assert clis.hay_node(existe_local=lambda b, path=None: False) is False
    assert clis.hay_node(existe_local=lambda b, path=None: b == 'npm') is True


# ── lo que consume la pantalla de bienvenida ─────────────────────────────

def test_el_estado_dice_si_es_un_primer_arranque():
    from unittest import mock
    with mock.patch.object(clis, 'detectar',
                           return_value=[{'id': 'claude', 'nombre': 'x',
                                          'instalado': False, 'instalable': True}]), \
         mock.patch.object(clis, 'hay_node', return_value=True):
        e = clis.estado()
    assert e['primer_arranque'] is True, 'sin ningún CLI, la app tiene algo que decir'

    with mock.patch.object(clis, 'detectar',
                           return_value=[{'id': 'claude', 'nombre': 'x',
                                          'instalado': True, 'instalable': True}]), \
         mock.patch.object(clis, 'hay_node', return_value=True):
        e = clis.estado()
    assert e['primer_arranque'] is False, 'con uno instalado ya se puede trabajar'


# ── instalación ──────────────────────────────────────────────────────────

class _R:
    def __init__(self, rc, out='', err=''):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_instalar_devuelve_el_error_tal_cual():
    """Quien ve esto acaba de instalar la app y no tiene nada más para
    orientarse: un "no se pudo" genérico lo deja sin salida."""
    r = clis.instalar('claude', correr=lambda *a, **kw: _R(1, '', 'EACCES: permission denied'))
    assert r['ok'] is False
    assert 'EACCES' in r['salida'], r


def test_instalar_ok():
    r = clis.instalar('codex', correr=lambda *a, **kw: _R(0, 'added 1 package'))
    assert r['ok'] is True and 'added 1 package' in r['salida']


def test_instalar_lo_que_no_se_instala():
    r = clis.instalar('antigravity')
    assert r['ok'] is False and 'no se instala' in r['salida']


def test_una_instalacion_colgada_no_queda_para_siempre():
    import subprocess as sp

    def _colgado(*a, **kw):
        raise sp.TimeoutExpired(cmd='npm', timeout=600)

    r = clis.instalar('claude', correr=_colgado)
    assert r['ok'] is False and 'tardó demasiado' in r['salida']


def test_la_salida_no_crece_sin_limite():
    # npm puede escupir megas de warnings; eso viaja por WS y va a la UI.
    r = clis.instalar('claude', correr=lambda *a, **kw: _R(0, 'x' * 50000))
    assert len(r['salida']) <= 2000


if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main([__file__, '-q']))


# ── "Instalar" desde la UI: el comando que se tipea en una terminal ───────

def _con_node(b, path=None):
    return b == 'npm'


def _sin_node(b, path=None):
    return False


def test_comando_de_terminal_con_node_es_el_npm_pelado():
    assert clis.comando_terminal('claude', _con_node) == 'npm install -g @anthropic-ai/claude-code'
    assert clis.comando_terminal('codex', _con_node) == 'npm install -g @openai/codex'
    assert clis.comando_terminal('pi', _con_node) == 'npm install -g --ignore-scripts @earendil-works/pi-coding-agent'


def test_sin_node_el_comando_instala_node_primero_y_sigue_en_un_solo_clic():
    cmd = clis.comando_terminal('claude', _sin_node)
    assert cmd.startswith('curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/')
    assert 'nvm install --lts' in cmd
    assert cmd.endswith('&& npm install -g @anthropic-ai/claude-code')
    # nvm.sh se carga en ESA shell antes de usar npm (si no, "npm: command not found")
    assert cmd.index('nvm.sh') < cmd.index('npm install')


def test_cursor_usa_su_instalador_oficial_con_o_sin_node():
    esperado = 'curl https://cursor.com/install -fsS | bash'
    assert clis.comando_terminal('cursor', _con_node) == esperado
    assert clis.comando_terminal('cursor', _sin_node) == esperado


def test_lo_que_no_tiene_comando_no_se_inventa_uno():
    assert clis.comando_terminal('antigravity', _con_node) is None
    assert clis.comando_terminal('no-existe', _con_node) is None


def test_el_estado_trae_comando_url_y_si_incluye_node():
    d = clis.detectar(existe_local=lambda b, path=None: b in ('claude', 'npm'))
    assert _por_id(d, 'claude')['comando'] is None            # instalado: nada que instalar
    assert _por_id(d, 'claude')['url'] is None
    cx = _por_id(d, 'codex')
    assert cx['comando'] == 'npm install -g @openai/codex' and cx['con_node'] is False
    assert _por_id(d, 'cursor')['comando'].startswith('curl https://cursor.com/install')
    ag = _por_id(d, 'antigravity')
    assert ag['comando'] is None and ag['url'] == 'https://antigravity.google'
    assert ag['con_node'] is False


def test_sin_node_el_estado_lo_avisa_solo_donde_hace_falta():
    d = clis.detectar(existe_local=_sin_node)
    assert _por_id(d, 'codex')['con_node'] is True
    assert _por_id(d, 'cursor')['con_node'] is False          # curl, no necesita Node
    assert _por_id(d, 'antigravity')['con_node'] is False


# ── endpoints (/api/clis?refrescar · batch con instalar_cli) ──────────────

def _cliente_con_proyecto(monkeypatch):
    import tempfile
    from fastapi.testclient import TestClient
    from plotspace.main import app
    from plotspace.core.database import get_db
    from plotspace.tests._harness import fresh_db
    import plotspace.routers.terminals as T

    fresh_db()
    ruta = tempfile.mkdtemp(prefix='jarvis_proj_')
    conn = get_db()
    try:
        cur = conn.execute("INSERT INTO projects (nombre, ruta, fecha_creacion, ultimo_acceso) VALUES ('p', ?, '2026-01-01T00:00:00', '2026-01-01T00:00:00')", (ruta,))
        conn.commit()
        pid = cur.lastrowid
    finally:
        conn.close()

    escritos = []

    class _Motor:
        def enviar_texto(self, tid, texto): escritos.append(('texto', tid, texto))
        def enviar_tecla(self, tid, tecla): escritos.append(('tecla', tid, tecla))

    async def _nada(*a, **k): return None
    monkeypatch.setattr(T, '_crear_sesion_tmux', _nada)
    monkeypatch.setattr(T, '_preparar_proyecto', _nada)
    monkeypatch.setattr(T, 'motor_terminales', lambda: _Motor())
    return TestClient(app), pid, escritos, T


def test_refrescar_se_salta_el_cache(monkeypatch):
    from fastapi.testclient import TestClient
    from plotspace.main import app
    import plotspace.routers.terminals as T
    llamadas = []
    monkeypatch.setattr(clis, 'estado', lambda: (llamadas.append(1), {'clis': [], 'hay_node': True, 'primer_arranque': True})[1])
    T._CLIS_CACHE.update(ts=0.0, data=None)
    with TestClient(app) as c:
        c.get('/api/clis'); c.get('/api/clis')
        assert len(llamadas) == 1                       # la segunda salió del cache
        c.get('/api/clis?refrescar=1')
        assert len(llamadas) == 2                       # refrescar re-detecta


def test_batch_instalar_cli_tipea_el_comando_que_arma_el_server(monkeypatch):
    monkeypatch.setattr(clis, 'hay_node', lambda existe_local=None: True)
    c, pid, escritos, T = _cliente_con_proyecto(monkeypatch)
    T._CLIS_CACHE.update(ts=1.0, data={'clis': []})
    with c:
        r = c.post(f'/api/projects/{pid}/terminals/batch', json={
            'terminales': [{'nombre': 'Instalar Codex', 'tipo_ia': 'manual'}],
            'instalar_cli': 'codex',
            'comando': 'rm -rf /',          # un comando del cliente NO se usa cuando hay instalar_cli
        })
    assert r.status_code == 201
    tid = r.json()[0]['id']
    assert ('texto', tid, 'npm install -g @openai/codex') in escritos
    assert ('tecla', tid, 'Enter') in escritos
    assert not any('rm -rf' in str(e) for e in escritos)
    assert T._CLIS_CACHE['data'] is None                # el estado va a cambiar: invalida el cache


def test_batch_instalar_cli_desconocido_se_rechaza_sin_crear_nada(monkeypatch):
    from plotspace.core.database import get_db
    c, pid, escritos, T = _cliente_con_proyecto(monkeypatch)
    with c:
        for malo in ('antigravity', 'no-existe'):
            r = c.post(f'/api/projects/{pid}/terminals/batch', json={
                'terminales': [{'nombre': 'x', 'tipo_ia': 'manual'}], 'instalar_cli': malo})
            assert r.status_code == 400
    conn = get_db()
    try:
        assert conn.execute('SELECT COUNT(*) FROM terminals').fetchone()[0] == 0
    finally:
        conn.close()
    assert escritos == []
