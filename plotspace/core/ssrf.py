"""
JARVIS — Guard anti-SSRF para fetches/navegaciones server-side.

Dos lugares hacen requests a URLs que NO controla Jarvis: el probe de
embebibilidad (httpx) y el browser remoto (Chromium). Un atacante (una web
abierta al lado, o el LLM vía prompt-injection) podría apuntarlos a destinos
internos para exfiltrar datos o pegarle a servicios que confían en "localhost".

Política calibrada a esta app LOCAL single-user:
  - BLOQUEAR: loopback (127/8, ::1), link-local (169.254/16 — incluye el
    endpoint de metadata 169.254.169.254 — y fe80::/10), reservadas,
    multicast, unspecified. Esos son los destinos peligrosos que el browser
    del propio usuario normalmente NO debería tocar vía el server.
  - PERMITIR: IPs públicas y LAN privada (10/8, 172.16/12, 192.168/16). El
    usuario ya alcanza la LAN desde su propio browser, así que bloquearla no
    suma seguridad y rompería previews legítimos de dev servers en otra
    máquina de la red.

EXCEPCIÓN ACOTADA — tus dev servers: el Web Preview existe para ver el localhost que
levantó un agente, así que `permitir_loopback` deja pasar un nombre de loopback
(`localhost`, `127.0.0.1`, `::1`) SOLO en los puertos que el caller declara (los dev
servers detectados por `dev_detect`). Un dominio público que resuelva a 127.0.0.1
(DNS-rebinding) sigue bloqueado: la excepción mira el NOMBRE escrito, no solo la IP.

`_ip_bloqueada` es pura y testeable; `url_destino_segura` resuelve DNS (blocking
— los callers la corren en un thread).
"""
import ipaddress
import socket
from urllib.parse import urlsplit


def _ip_bloqueada(ip_str: str) -> bool:
    """True si la IP cae en un rango interno peligroso (loopback/link-local/
    reservada/multicast/unspecified). La LAN privada NO se bloquea."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # no parseable → no confiable
    return bool(
        ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolver(hostname: str) -> list:
    """hostname → lista de IPs (vacía si no resuelve). Blocking (DNS)."""
    try:
        infos = socket.getaddrinfo(hostname, None)
        return [info[4][0] for info in infos]
    except Exception:
        return []


_NOMBRES_LOOPBACK = ('localhost', '127.0.0.1', '0.0.0.0', '::1')


def es_loopback_permitido(url: str, puertos) -> bool:
    """True si `url` apunta a un nombre de loopback en uno de los `puertos` permitidos
    (iterable de ints). Pura: no resuelve DNS."""
    try:
        partes = urlsplit((url or '').strip())
        host = (partes.hostname or '').lower()
        puerto = partes.port or (443 if partes.scheme == 'https' else 80)
    except ValueError:
        return False
    if partes.scheme not in ('http', 'https') or host not in _NOMBRES_LOOPBACK:
        return False
    return puerto in set(puertos or ())


def resolver_y_validar(url: str, permitir_loopback=None) -> tuple:
    """(ok, motivo, ip). Como url_destino_segura pero devuelve TAMBIÉN una IP
    validada (la primera resuelta, ya confirmada no-interna) para que el caller
    pueda PINNEAR el connect a esa IP y cerrar el TOCTOU de DNS-rebinding (el
    cliente HTTP, si re-resolviera, podría caer en una IP interna distinta)."""
    u = (url or '').strip()
    try:
        partes = urlsplit(u)
    except ValueError:
        return (False, 'URL inválida', None)
    if partes.scheme not in ('http', 'https'):
        return (False, 'solo http/https', None)
    host = partes.hostname
    if not host:
        return (False, 'URL sin host', None)
    if permitir_loopback and es_loopback_permitido(u, permitir_loopback):
        return (True, None, '127.0.0.1')
    ips = _resolver(host)
    if not ips:
        return (False, 'el host no resuelve', None)
    for ip in ips:
        if _ip_bloqueada(ip):
            return (False, f'host interno no permitido ({ip})', None)
    return (True, None, ips[0])


def url_destino_segura(url: str, permitir_loopback=None) -> tuple:
    """(ok, motivo). ok=True solo si es http/https a un host que resuelve a
    IP(s) NO bloqueadas. Si CUALQUIER IP resuelta es interna peligrosa, se
    rechaza (defensa contra hosts que resuelven a varias IPs)."""
    ok, motivo, _ip = resolver_y_validar(url, permitir_loopback)
    return (ok, motivo)
