"""
Test: guard anti-SSRF (plotspace/core/ssrf.py).

Política para esta app local: bloquear loopback / link-local (metadata
169.254.169.254) / reservadas / multicast; PERMITIR LAN privada y públicas.
Se prueba con IPs literales para no depender del DNS de red.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plotspace.core.ssrf import _ip_bloqueada, url_destino_segura


# ─── _ip_bloqueada (pura) ────────────────────────────────────────────────────

def test_loopback_bloqueado():
    assert _ip_bloqueada('127.0.0.1')
    assert _ip_bloqueada('127.5.5.5')
    assert _ip_bloqueada('::1')


def test_link_local_y_metadata_bloqueado():
    assert _ip_bloqueada('169.254.169.254')   # endpoint de metadata cloud
    assert _ip_bloqueada('169.254.0.1')
    assert _ip_bloqueada('fe80::1')


def test_multicast_reservada_unspecified_bloqueado():
    assert _ip_bloqueada('224.0.0.1')         # multicast
    assert _ip_bloqueada('0.0.0.0')           # unspecified
    assert _ip_bloqueada('no-es-una-ip')      # no parseable → no confiable


def test_lan_privada_permitida():
    # El usuario ya alcanza la LAN desde su browser → no es un objetivo SSRF útil
    assert not _ip_bloqueada('10.0.0.5')
    assert not _ip_bloqueada('192.168.1.50')
    assert not _ip_bloqueada('172.16.0.1')


def test_publica_permitida():
    assert not _ip_bloqueada('8.8.8.8')
    assert not _ip_bloqueada('1.1.1.1')


# ─── url_destino_segura (con IPs literales) ──────────────────────────────────

def test_esquema_no_http_rechazado():
    assert url_destino_segura('ftp://example.com')[0] is False
    assert url_destino_segura('file:///etc/passwd')[0] is False
    assert url_destino_segura('')[0] is False


def test_sin_host_rechazado():
    assert url_destino_segura('http://')[0] is False


def test_url_loopback_y_metadata_rechazada():
    assert url_destino_segura('http://127.0.0.1:8080/')[0] is False
    assert url_destino_segura('http://169.254.169.254/latest/meta-data')[0] is False
    assert url_destino_segura('http://[::1]:3000/')[0] is False


def test_url_lan_y_publica_por_ip_permitida():
    assert url_destino_segura('http://10.0.0.5:3000/')[0] is True
    assert url_destino_segura('https://192.168.1.50:8080/app')[0] is True
    assert url_destino_segura('http://8.8.8.8/')[0] is True


# ── Excepción acotada: loopback SOLO en los puertos de tus dev servers ───────
def test_loopback_permitido_solo_en_puertos_declarados():
    from plotspace.core.ssrf import es_loopback_permitido
    assert es_loopback_permitido('http://localhost:5173/app', {5173})
    assert es_loopback_permitido('http://127.0.0.1:5173', {5173})
    assert es_loopback_permitido('http://[::1]:5173/', {5173})
    assert not es_loopback_permitido('http://localhost:5174/', {5173})   # otro puerto
    assert not es_loopback_permitido('http://localhost:3000/', {5173})   # Jarvis
    assert not es_loopback_permitido('http://localhost:5173/', set())
    assert not es_loopback_permitido('http://evil.com:5173/', {5173})    # nombre ≠ loopback
    assert not es_loopback_permitido('ftp://localhost:5173/', {5173})
    assert not es_loopback_permitido('http://169.254.169.254:80/', {80})  # metadata nunca


def test_url_destino_segura_con_loopback_permitido():
    assert url_destino_segura('http://localhost:5173/', {5173})[0] is True
    assert url_destino_segura('http://localhost:5174/', {5173})[0] is False
    assert url_destino_segura('http://169.254.169.254/x', {80})[0] is False
    # sin lista, la política original no cambia
    assert url_destino_segura('http://localhost:5173/')[0] is False


def test_puertos_vivos_de_dev_detect():
    from plotspace.core import dev_detect
    guardado = dict(dev_detect._detectados)
    dev_detect._detectados.clear()
    dev_detect._persist_cargado = True
    try:
        dev_detect._detectados[1] = {'http://localhost:5173': {}, 'http://localhost:3000': {}}
        dev_detect._detectados[2] = {'http://localhost:8000': {}}
        assert dev_detect.puertos_vivos() == {5173, 8000}      # el 3000 (Jarvis) nunca
    finally:
        dev_detect._detectados.clear(); dev_detect._detectados.update(guardado)


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
