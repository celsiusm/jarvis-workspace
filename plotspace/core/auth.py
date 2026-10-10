# JARVIS — candado de Host / Origin (anti DNS-rebinding y CSWSH).
# No hay token de acceso: la app es local. El default escucha en 127.0.0.1;
# 0.0.0.0 es explícito. Lo que queda es no aceptar un Host/Origin de un
# dominio ajeno que apunte a esta máquina.

import os
import ipaddress
from urllib.parse import urlsplit


def _hostname_de(valor: str) -> str:
    """Extrae el hostname de un 'host:port', una URL o un Origin.
    urlsplit normaliza IPv6 (`[::1]`) y separa el puerto."""
    v = (valor or '').strip()
    if '://' not in v:
        v = 'http://' + v
    try:
        return (urlsplit(v).hostname or '').lower()
    except ValueError:
        return ''


# Hosts no-browser siempre permitidos. `testserver` es el Host por defecto del
# TestClient de Starlette/httpx; NO es un vector de rebinding (los browsers no
# pueden forjar el header Host vía fetch — solo lo manda un cliente no-browser).
_HOSTS_SEGUROS = frozenset({'localhost', 'testserver'})


def _es_host_local(hostname: str) -> bool:
    """True si es `localhost`/host de test seguro o una IP literal (loopback o
    LAN). Rechaza nombres de dominio: ese es el vector de rebinding/sitios
    externos."""
    if not hostname:
        return False
    if hostname in _HOSTS_SEGUROS:
        return True
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def host_permitido(host_header: str | None, extra=()) -> bool:
    """Valida el header Host (anti DNS-rebinding). Acepta localhost e IPs
    —incluida la IP de LAN— y cualquier host extra (env JARVIS_ALLOWED_HOSTS).
    Header ausente = nada que rechazar → permitido."""
    if not host_header:
        return True
    hostname = _hostname_de(host_header)
    if hostname and hostname in {h.lower() for h in extra}:
        return True
    return _es_host_local(hostname)


_PUERTO_DEFAULT = {'http': 80, 'https': 443, 'ws': 80, 'wss': 443}


def _host_puerto(valor: str, esquema: str = 'http'):
    """(hostname, puerto) de un Origin ('http://h:p') o de un header Host ('h:p').
    El puerto ausente toma el default del esquema. None si no se puede parsear."""
    v = (valor or '').strip()
    if '://' in v:
        esquema = v.split('://', 1)[0].lower()
    else:
        v = '//' + v
    try:
        u = urlsplit(v)
        hostname = (u.hostname or '').lower()
        puerto = u.port or _PUERTO_DEFAULT.get(esquema, 80)
    except ValueError:
        return None
    return (hostname, puerto) if hostname else None


def _es_loopback_o_lan(hostname: str) -> bool:
    """localhost o una IP literal de loopback / red privada (RFC1918, ULA,
    link-local). NUNCA una IP pública."""
    if hostname in _HOSTS_SEGUROS:
        return True
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    # Lista EXPLÍCITA: `ip.is_private` de Python también da True para rangos de
    # documentación/reservados (203.0.113.0/24, 2001:db8::/32…) que no son LAN.
    return any(ip in red for red in _REDES_LOCALES if ip.version == red.version)


_REDES_LOCALES = tuple(ipaddress.ip_network(r) for r in (
    '127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16',
    '::1/128', 'fc00::/7', 'fe80::/10',
))


def origen_permitido(origin_header: str | None, extra=(), host: str | None = None) -> bool:
    """Valida el header Origin (anti CSWSH / CSRF). Origin ausente = cliente
    no-browser o navegación same-origin → permitido. `null` (sandbox/data:) →
    rechazado. Un host de `extra` (JARVIS_ALLOWED_HOSTS) → permitido.

    Con `host` (el header Host de la request) la regla es MISMO ORIGEN: el
    Origin tiene que ser exactamente ese host:puerto, y ese Host tiene que ser
    válido (host_permitido; si no, un dominio con DNS-rebinding sería
    "same-origin" consigo mismo). La UI legítima siempre es same-origin, así
    que esto deja afuera a una web en una IP pública Y a las páginas de otros
    puertos locales (dev servers con código de terceros).

    Sin `host` (llamadores viejos): solo localhost o IP de loopback/LAN privada.
    Antes valía cualquier IP literal: una web servida desde una IP PÚBLICA podía
    abrir ws://127.0.0.1:3000/ws/terminal/N y escribir comandos."""
    if not origin_header:
        return True
    if origin_header == 'null':
        return False
    hostname = _hostname_de(origin_header)
    extras = {h.lower() for h in extra}
    if hostname and hostname in extras:
        return True
    if host is not None:
        o = _host_puerto(origin_header)
        if o is None:
            return False
        h = _host_puerto(host, origin_header.split('://', 1)[0].lower() if '://' in origin_header else 'http')
        return h is not None and o == h and host_permitido(host, extra)
    return _es_loopback_o_lan(hostname)


def hosts_extra() -> tuple:
    """Allowlist opcional de hosts/orígenes por nombre (ej. un dominio .local
    en /etc/hosts), separada por comas en env JARVIS_ALLOWED_HOSTS."""
    crudo = os.environ.get('JARVIS_ALLOWED_HOSTS', '').strip()
    return tuple(h.strip().lower() for h in crudo.split(',') if h.strip())


def imprimir_banner():
    """Arranque: una línea, sin token. El dibujo de caja viejo tumba cp1252."""
    print('[jarvis] listo — abrí http://127.0.0.1:3000')
