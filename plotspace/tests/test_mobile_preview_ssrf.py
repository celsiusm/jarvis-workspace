"""
Test: el chequeo de backend de mobile_preview no es un SSRF.

`_backend_status_sync` lee EXPO_PUBLIC_API_URL del .env de la app —contenido que
controla el repo/agente, no Jarvis— y le hace un OPTIONS. Sin guard, un
`EXPO_PUBLIC_API_URL=http://127.0.0.1:.../` o `http://169.254.169.254/...` haría
que el server probee destinos internos (escáner de puertos / metadata). El guard
(ssrf.url_destino_segura) debe cortar ANTES de cualquier request de red.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import plotspace.routers.mobile_preview as mp


def _app_con_api_url(tmp_path, url):
    (tmp_path / '.env').write_text(f'EXPO_PUBLIC_API_URL={url}\n', encoding='utf-8')
    return str(tmp_path)


def _sin_red(monkeypatch):
    """Hace explotar urlopen: si el guard funciona, NUNCA se llama."""
    import urllib.request

    def _boom(*a, **k):
        raise AssertionError('urlopen NO debería llamarse: el guard SSRF tiene que cortar antes')

    monkeypatch.setattr(urllib.request, 'urlopen', _boom)


def test_bloquea_loopback(tmp_path, monkeypatch):
    ruta = _app_con_api_url(tmp_path, 'http://127.0.0.1:9999/api')
    monkeypatch.setattr(mp, '_ruta_app_expo', lambda pid: ruta)
    _sin_red(monkeypatch)
    res = mp._backend_status_sync(999)
    assert res['configurado'] is True
    assert res['alcanzable'] is False
    assert 'bloqueado' in res


def test_bloquea_metadata_link_local(tmp_path, monkeypatch):
    ruta = _app_con_api_url(tmp_path, 'http://169.254.169.254/latest/meta-data/')
    monkeypatch.setattr(mp, '_ruta_app_expo', lambda pid: ruta)
    _sin_red(monkeypatch)
    res = mp._backend_status_sync(999)
    assert res['alcanzable'] is False
    assert 'bloqueado' in res


def test_bloquea_scheme_no_http(tmp_path, monkeypatch):
    # file:// / gopher:// etc.: urllib los maneja, el guard exige http(s).
    ruta = _app_con_api_url(tmp_path, 'file:///etc/passwd')
    monkeypatch.setattr(mp, '_ruta_app_expo', lambda pid: ruta)
    _sin_red(monkeypatch)
    res = mp._backend_status_sync(999)
    assert res['alcanzable'] is False
    assert 'bloqueado' in res


def test_url_publica_no_se_bloquea_por_ssrf(tmp_path, monkeypatch):
    # Una API pública legítima (caso real: onrender.com) NO debe quedar marcada
    # como bloqueada por el guard. Stubeamos la red para no depender de internet.
    ruta = _app_con_api_url(tmp_path, 'https://testapp.onrender.com/api')
    monkeypatch.setattr(mp, '_ruta_app_expo', lambda pid: ruta)
    monkeypatch.setattr(mp, '_ultimo_puerto', {}, raising=False)

    import urllib.request

    class _Resp:
        status = 200
        headers = {'Access-Control-Allow-Origin': '*'}

        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: _Resp())
    # El código usa un opener propio (sin seguir redirects), no urlopen: sin
    # stubearlo, este test le pegaba a onrender.com DE VERDAD.
    monkeypatch.setattr(urllib.request, 'build_opener',
                        lambda *a, **k: type('_Op', (), {'open': lambda self, *x, **y: _Resp()})())
    # Hermético: el guard RESUELVE el dominio antes de dejar pasar. Sin esto el
    # test dependía del DNS de la máquina (en un sandbox sin DNS caía como
    # "bloqueado"). Una IP pública de documentación hace de onrender.com.
    from plotspace.core import ssrf
    monkeypatch.setattr(ssrf, '_resolver', lambda host: ['93.184.216.34'])
    res = mp._backend_status_sync(999)
    assert res['configurado'] is True
    assert 'bloqueado' not in res
    assert res['alcanzable'] is True


if __name__ == '__main__':
    # Patrón del proyecto: correr como script además de pytest. Mínimo shim de
    # monkeypatch para no depender de pytest en el modo script.
    import traceback

    class _MP:
        def __init__(self): self._undo = []
        def setattr(self, obj, name, val, raising=True):
            old = getattr(obj, name, None)
            self._undo.append((obj, name, old))
            setattr(obj, name, val)
        def deshacer(self):
            for obj, name, old in reversed(self._undo):
                setattr(obj, name, old)

    import tempfile
    import pathlib
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith('test_') and callable(fn):
            mpatch = _MP()
            with tempfile.TemporaryDirectory() as d:
                try:
                    fn(pathlib.Path(d), mpatch)
                    print(f'ok  {nombre}')
                except Exception:
                    fallos += 1
                    print(f'FAIL {nombre}')
                    traceback.print_exc()
                finally:
                    mpatch.deshacer()
    sys.exit(1 if fallos else 0)
