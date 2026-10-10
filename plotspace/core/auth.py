# JARVIS — candado de acceso: Host / Origin (anti DNS-rebinding y CSWSH) y,
# cuando el server escucha en la RED, un token.
#
# En la PC propia (127.0.0.1, el default) no hay login: abrís localhost:3000 y
# entrás. Si Jarvis escucha en la red (Docker, Unraid, `--host 0.0.0.0` para el
# celular), quien entra desde OTRA máquina necesita el token: Jarvis tiene
# terminales, y sin esto cualquiera de esa red llegaba a una. Lo que corre en la
# misma máquina (hooks de los agentes, el navegador local) no lo necesita.
# JARVIS_TOKEN=off lo apaga; JARVIS_TOKEN=<valor> fija uno propio.

import hmac
import os
import ipaddress
import secrets
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


def origen_permitido(origin_header: str | None, extra=(), host: str | None = None,
                     confiar_host: bool = False) -> bool:
    """Valida el header Origin (anti CSWSH / CSRF). Origin ausente = cliente
    no-browser o navegación same-origin → permitido. `null` (sandbox/data:) →
    rechazado. Un host de `extra` (JARVIS_ALLOWED_HOSTS) → permitido.

    Con `host` (el header Host de la request) la regla es MISMO ORIGEN: el
    Origin tiene que ser exactamente ese host:puerto, y ese Host tiene que ser
    válido (host_permitido; si no, un dominio con DNS-rebinding sería
    "same-origin" consigo mismo). La UI legítima siempre es same-origin, así
    que esto deja afuera a una web en una IP pública Y a las páginas de otros
    puertos locales (dev servers con código de terceros).

    `confiar_host=True` (request con token válido): el Host no necesita ser
    una IP/localhost — basta el mismo origen.

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
        # confiar_host: la request ya vino con un token válido → el Host puede ser
        # un nombre (http://tower:3000); el rebinding no tiene el token.
        return h is not None and o == h and (confiar_host or host_permitido(host, extra))
    return _es_loopback_o_lan(hostname)


def hosts_extra() -> tuple:
    """Allowlist opcional de hosts/orígenes por nombre (ej. un dominio .local
    en /etc/hosts), separada por comas en env JARVIS_ALLOWED_HOSTS."""
    crudo = os.environ.get('JARVIS_ALLOWED_HOSTS', '').strip()
    return tuple(h.strip().lower() for h in crudo.split(',') if h.strip())


# ─── Token de acceso (solo cuando el server escucha en la red) ────────────────

TOKEN_COOKIE = 'jarvis_acceso'
_RUTA_TOKEN = None          # data/acceso-token (se resuelve perezoso; los tests lo apuntan a tmp)
_token_cache = None
_HOSTS_LOOPBACK = ('127.0.0.1', 'localhost', '::1')


def host_de_escucha() -> str:
    from plotspace.core.escucha import host_puerto_actuales
    return host_puerto_actuales()[0]


def _es_loopback(host: str) -> bool:
    h = (host or '').strip().strip('[]').lower()
    if h in _HOSTS_LOOPBACK:
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _modo_token() -> str:
    """'auto' (default) · 'off' · o el token literal de JARVIS_TOKEN."""
    v = (os.environ.get('JARVIS_TOKEN') or '').strip()
    if not v or v.lower() == 'auto':
        return 'auto'
    if v.lower() in ('off', '0', 'no', 'false'):
        return 'off'
    return v


def token_requerido() -> bool:
    """¿Se exige token a quien entra desde otra máquina? auto = solo si el
    server escucha en la red (no en loopback)."""
    modo = _modo_token()
    if modo == 'off':
        return False
    if modo != 'auto':
        return True
    return not _es_loopback(host_de_escucha())


def _ruta_token() -> str:
    if _RUTA_TOKEN:
        return _RUTA_TOKEN
    from plotspace.core.datadir import ruta_data
    return ruta_data('acceso-token')


def token_actual() -> str:
    """El token vigente: el de JARVIS_TOKEN o uno aleatorio persistido (0600)
    en data/acceso-token, así sobrevive a reinicios y updates del contenedor."""
    global _token_cache
    modo = _modo_token()
    if modo not in ('auto', 'off'):
        return modo
    if _token_cache:
        return _token_cache
    ruta = _ruta_token()
    try:
        with open(ruta, encoding='utf-8') as f:
            t = f.read().strip()
        if len(t) >= 24:
            _token_cache = t
            return t
    except OSError:
        pass
    t = secrets.token_urlsafe(32)
    os.makedirs(os.path.dirname(ruta) or '.', exist_ok=True)
    fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(t + '\n')
    os.chmod(ruta, 0o600)
    _token_cache = t
    return t


def token_valido(candidato) -> bool:
    if not candidato:
        return False
    return hmac.compare_digest(str(candidato).encode(), token_actual().encode())


_CABECERAS_PROXY = ('x-forwarded-for', 'forwarded', 'x-real-ip')


def cliente_local(client_host, headers) -> bool:
    """La request viene de ESTA máquina (loopback) y no a través de un proxy.
    Dentro de Docker el navegador del usuario llega desde el gateway del bridge
    (172.17.0.1) → NO es local; los hooks de los agentes llegan por 127.0.0.1."""
    if any(headers.get(k) for k in _CABECERAS_PROXY):
        return False
    return _es_loopback(client_host or '')


def token_de_request(headers, cookies, query) -> str:
    """Token de la request: ?token= (el link del arranque) → X-Jarvis-Token →
    Authorization: Bearer → cookie."""
    t = query.get('token') or headers.get('x-jarvis-token')
    if not t:
        a = headers.get('authorization') or ''
        if a.lower().startswith('bearer '):
            t = a[7:].strip()
    return t or cookies.get(TOKEN_COOKIE) or ''


def acceso_por_token(client_host, headers, cookies, query):
    """(permitido, con_token) para una request que NO es de una ruta exenta.
    con_token=True → vino con un token válido (el Host puede ser un nombre)."""
    if not token_requerido() or cliente_local(client_host, headers):
        return True, False
    if token_valido(token_de_request(headers, cookies, query)):
        return True, True
    return False, False


def ws_permitido(websocket) -> tuple:
    """(ok, codigo_de_cierre) para un upgrade WebSocket: token (si hace falta)
    + Origin del mismo origen. El middleware http NO corre para websockets."""
    client = websocket.client.host if websocket.client else ''
    ok, con_token = acceso_por_token(client, websocket.headers, websocket.cookies,
                                     websocket.query_params)
    if not ok:
        return False, 4401
    if not origen_permitido(websocket.headers.get('origin'), hosts_extra(),
                            host=websocket.headers.get('host', ''), confiar_host=con_token):
        return False, 4403
    return True, None


def pagina_login_html() -> str:
    """Página mínima para pegar el token. Usa los tokens de color del tema
    (/static es público: el código del front no es secreto)."""
    return """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Jarvis</title>
<link rel="stylesheet" href="/static/shared/tokens.css">
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--ob-bg-0);
color:var(--ob-fg-0);font:15px/1.5 var(--font-ui)}main{width:min(420px,90vw)}h1{font-size:20px;margin:0 0 6px}
p{color:var(--ob-fg-2);margin:0 0 18px}input{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;
border:1px solid var(--ob-line-2);background:var(--ob-bg-1);color:inherit;font:inherit}
button{margin-top:10px;width:100%;padding:10px;border:0;border-radius:8px;background:var(--ob-accent);
color:var(--ob-on-accent);font:600 15px var(--font-ui);cursor:pointer}small{display:block;margin-top:14px;color:var(--ob-fg-2)}</style>
</head><body><main><h1>Jarvis está protegido</h1>
<p>Estás entrando desde otra máquina. Pegá el token de acceso.<br>
<span lang="en">Jarvis is protected: paste the access token.</span></p>
<form method="get"><input name="token" autocomplete="off" autofocus placeholder="token" aria-label="token">
<button type="submit">Entrar</button></form>
<small>El link con el token aparece en los logs al arrancar (<code>docker logs</code>)
y está guardado en <code>data/acceso-token</code>.</small></main></body></html>"""


def imprimir_banner():
    """Arranque: una línea (el dibujo de caja viejo tumba cp1252). Si el server
    escucha en la red, también el link con el token."""
    from plotspace.core.escucha import host_puerto_actuales
    host, port = host_puerto_actuales()
    print(f'[jarvis] listo — abrí http://127.0.0.1:{port}')
    if token_requerido():
        print(f'[jarvis] escuchando en la red ({host}): desde otra máquina se entra con token.')
        print(f'[jarvis] abrí  http://<ip-de-este-equipo>:{port}/?token={token_actual()}')
