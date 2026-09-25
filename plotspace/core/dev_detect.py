"""Detección automática de dev servers levantados por agentes en sus terminales.

Poller background (patrón STATE.md): cada 2s captura el pane tmux de cada
terminal activa y busca URLs locales impresas por dev servers (Vite, Next,
uvicorn, Expo, etc.). Antes de anunciar verifica con un socket que el puerto
realmente responde — una URL mencionada en texto no dispara nada.

Rastrea VARIOS dev servers por proyecto (un agente puede levantar 3 localhost,
o varios agentes uno cada uno): cada URL viva se anuncia y el Web Preview la
abre en su propia pestaña.

Eventos WS por el broadcaster del proyecto:
  - dev_server_detectado {url, terminal_id, terminal_nombre}  (uno por URL nueva)
  - dev_server_caido     {url}                                (uno por URL muerta)

El registro lo consulta orchestrator._preview_url_activo() (pill, single);
urls_detectadas() da todas. El stop del pill descarta una URL puntual vía
descartar().
"""

import asyncio
import json
import os
import re
import subprocess
import time
from typing import Optional
from urllib.parse import urlparse

from plotspace.core.database import get_db
from plotspace.core.datadir import ruta_data
from plotspace.core.events import broadcaster
from plotspace.core import pane_capture
from plotspace.core.terminal_backend import backend, TODO_EL_SCROLLBACK

# Puerto del propio Jarvis: las URLs a este puerto en los panes suelen ser
# referencias al dashboard (curl a la API, instrucciones de tareas), no un dev
# server del proyecto — y además uvicorn ya lo ocupa, nada más puede bindearlo.
# En modo app el shell lo elige dinámico vía JARVIS_PORT (default histórico 3000).
PUERTO_JARVIS = int(os.environ.get('JARVIS_PORT', '3000'))

# Metro (Expo): en proyectos Expo lo maneja Mobile Preview, no el Web Preview.
PUERTO_METRO = 8081

INTERVALO_S = 2   # 4→2s: detecta el dev server (y abre el preview) ~2s antes

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[mGKHFJA-Z]')

# http://localhost:5173, http://127.0.0.1:8000/docs, http://0.0.0.0:4321 …
# El puerto es OBLIGATORIO (los dev servers siempre lo imprimen) — sin él
# cualquier "http://localhost" suelto en un texto sería falso positivo.
_URL_LOCAL_RE = re.compile(
    r'https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0):\d{2,5}(?:/[^\s\'"<>)\]]*)?',
    re.IGNORECASE,
)

# Los CLIs de IA (gemini/codex) levantan un server EFÍMERO de loopback para el
# callback de su login OAuth (gemini .../oauth2callback, codex :1455/auth/callback).
# NO son dev servers: anunciarlos (a) rompía el login —el TCP-check de liveness le
# pega un request y el `finally{server.close()}` de gemini cerraba el server antes
# del callback real → "connection refused"— y (b) abría un localhost inútil en el
# Web Preview. Los excluimos por path (fuente texto) y por proceso dueño (fuente puerto).
_OAUTH_CALLBACK_PATH_RE = re.compile(
    r'(oauth2?[._-]?callback|auth/callback|/authcode|/callback(?:[/?#]|$))', re.IGNORECASE)
# Marcadores en la cmdline del proceso dueño de un puerto = un CLI de IA (logueándose
# o con su server interno tipo IDE). Substrings de PATH DE PAQUETE / instalación,
# PRECISOS: el match es sobre el proceso DUEÑO del puerto (NO sobre el nombre/cwd del
# proyecto), así que un proyecto llamado 'codex-app'/'opencode-y'/'claude-x' NO cae.
# NO usar tokens pelados ('claude'/'opencode'/'qwen') — darían falso positivo cuando
# el dev server real corre dentro de un proyecto con ese nombre.
_CLI_LOGIN_MARKERS = ('gemini-cli', '@google/gemini', 'gemini.js', '@openai/codex',
                      'opencode-ai', '@anthropic-ai/claude-code')
# claude NATIVO corre como binario con cmdline PELADA 'claude' (un solo token, sin
# path ni subcomando): el único discriminador robusto es el exe, que apunta a su dir
# de instalación. Su login bindea un puerto loopback EFÍMERO aleatorio (no fijo) y su
# IDE-server otro — ambos son del CLI, nunca dev servers del proyecto.
_CLI_LOGIN_EXE_MARKERS = ('/share/claude/',)

# Contexto de login en la LÍNEA del pane que contiene la URL. Sirve para la URL
# PELADA (sin /oauth2callback en el path) que algunos CLIs imprimen — p.ej. codex:
# "Starting local login server on http://localhost:1455." Un dev server nunca
# imprime estas palabras en la misma línea que su URL, así que el riesgo de
# excluir un dev server real es nulo (el chequeo es por LÍNEA, no por todo el pane).
_LOGIN_CONTEXT_RE = re.compile(
    r'login server|sign[ -]?in|device code|authoriz|oauth|to authenticate',
    re.IGNORECASE,
)


# ─── Lógica pura (testeable sin tmux ni red) ──────────────────────────────────

def normalizar_url_local(url: str) -> Optional[str]:
    """Canonicaliza una URL local: los alias de loopback (0.0.0.0, 127.0.0.1,
    [::1]) → 'localhost', para que el MISMO server no aparezca dos veces (una
    por alias) en el menú de localhost ni como dos pestañas del Web Preview —
    un pane que imprime 'http://127.0.0.1:5173' y la fuente de puertos LISTEN
    (que ya arma 'http://localhost:5173') se de-dupean así por puerto. Más la
    limpieza de puntuación final pegada ('…:8000.' al cierre de una oración)."""
    if not url:
        return None
    url = url.strip().rstrip('.,;:!')
    # Solo el PRIMER '//' (la autoridad host:puerto) — count=1 no toca un path
    # que casualmente contenga la misma IP (http://localhost:3000/x/127.0.0.1).
    url = url.replace('//0.0.0.0', '//localhost', 1)
    url = url.replace('//127.0.0.1', '//localhost', 1)
    url = url.replace('//[::1]', '//localhost', 1)
    return url or None


def puerto_de(url: str) -> Optional[int]:
    try:
        return urlparse(url).port
    except ValueError:
        return None


def puerto_excluido(port: Optional[int], es_expo: bool = False) -> bool:
    """El 3000 (Jarvis) nunca es un dev server del proyecto; Metro (:8081) lo
    maneja Mobile Preview en proyectos Expo. Compartido por las dos fuentes de
    detección (texto del pane y escaneo de puertos)."""
    if port == PUERTO_JARVIS:
        return True
    if es_expo and port == PUERTO_METRO:
        return True
    return False


def es_callback_oauth(url: str) -> bool:
    """¿La URL es el callback efímero del login OAuth de un CLI (gemini/codex)?
    Esos no son dev servers del proyecto: no hay que anunciarlos ni abrirlos en
    el preview (y pegarles un TCP-check rompía el login). Match por path."""
    try:
        path = urlparse(url).path or ''
    except ValueError:
        return False
    return bool(_OAUTH_CALLBACK_PATH_RE.search(path))


def _linea_de_login(texto: str, ini: int, fin: int) -> bool:
    """¿La URL (en [ini,fin)) está en una LÍNEA de login de un CLI? Caza la URL
    PELADA sin path — p.ej. codex 'Starting local login server on
    http://localhost:1455'. Solo mira la línea del match, no todo el pane."""
    nl_ant = texto.rfind('\n', 0, ini)
    nl_post = texto.find('\n', fin)
    linea = texto[(nl_ant + 1 if nl_ant >= 0 else 0):(nl_post if nl_post >= 0 else len(texto))]
    return bool(_LOGIN_CONTEXT_RE.search(linea))


# ─── Demos servidos por el PROPIO Jarvis (:3000/static/<dir>/…) ────────────────
# Los agentes muestran mockups/galerías dejando HTML en frontend/<dir>/ y
# sirviéndolo por Jarvis (WSL no reenvía puertos nuevos a Windows — patrón
# "demos vía /static"). No son procesos: "viven" mientras sus archivos existan
# y "cerrarlos" = ocultarlos del menú (JAMÁS se toca el 3000, es Jarvis mismo).

# Carpetas de frontend/ que SON la app ('' = archivos sueltos como index.html).
_APP_STATIC_DIRS = {'', 'sections', 'shared', 'shell', 'vendor'}

_FRONTEND_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'frontend'))


def es_demo_jarvis(url: str) -> bool:
    """¿La URL es un demo servido por el propio Jarvis? = :3000/static/<dir>/…
    con <dir> fuera de la superficie de la app (sections/shared/shell/vendor)."""
    try:
        p = urlparse(url)
        puerto = p.port      # lazy: la ValueError salta ACÁ, no en urlparse
    except ValueError:
        # Un pane que imprime `http://localhost:99999` (el regex acepta hasta 5
        # dígitos) hacía explotar la detección ENTERA, no solo esa URL.
        return False
    if puerto != PUERTO_JARVIS:
        return False
    path = p.path or ''
    if not path.startswith('/static/'):
        return False
    resto = path[len('/static/'):]
    if '/' not in resto:
        return False   # archivo suelto en la raíz (index.html) = app, no demo
    return resto.split('/', 1)[0] not in _APP_STATIC_DIRS


def extraer_demos_jarvis(texto: str) -> list:
    """URLs de demos del propio Jarvis presentes en el texto de un pane, más
    reciente primero (mismo pipeline que extraer_urls_locales, SOLO :3000)."""
    limpio = _ANSI_RE.sub('', texto or '')
    urls, vistas = [], set()
    for m in reversed(list(_URL_LOCAL_RE.finditer(limpio))):
        url = normalizar_url_local(m.group(0))
        if not url or url in vistas:
            continue
        if not es_demo_jarvis(url):
            continue
        vistas.add(url)
        urls.append(url)
    return urls


def demo_vivo(url: str, base: Optional[str] = None) -> bool:
    """¿El demo sigue en disco? /static/X → frontend/X (archivo directo, o
    carpeta con index.html). Un demo 'muere' cuando le borran los archivos.
    Guard anti-traversal: el path resuelto queda SIEMPRE dentro de frontend/."""
    base = os.path.normpath(base or _FRONTEND_DIR)
    try:
        p = urlparse(url)
    except ValueError:
        return False
    rel = (p.path or '')[len('/static/'):].lstrip('/')
    destino = os.path.normpath(os.path.join(base, rel))
    if not destino.startswith(base + os.sep):
        return False
    if os.path.isfile(destino):
        return True
    return os.path.isfile(os.path.join(destino, 'index.html'))


def extraer_urls_locales(texto: str, es_expo: bool = False) -> list:
    """URLs de dev servers locales presentes en el texto de un pane, limpias
    de ANSI, normalizadas y deduplicadas — ordenadas de la MÁS RECIENTE
    (última aparición) a la más vieja. No toca la red.

    Excluye el puerto de Jarvis siempre, y Metro (:8081) en proyectos Expo
    (eso ya lo maneja Mobile Preview)."""
    limpio = _ANSI_RE.sub('', texto or '')
    urls, vistas = [], set()
    for m in reversed(list(_URL_LOCAL_RE.finditer(limpio))):
        url = normalizar_url_local(m.group(0))
        if not url or url in vistas:
            continue
        if puerto_excluido(puerto_de(url), es_expo):
            continue
        if es_callback_oauth(url):
            continue
        if _linea_de_login(limpio, m.start(), m.end()):
            continue
        vistas.add(url)
        urls.append(url)
    return urls


def extraer_candidatos_pane(texto: str, es_expo: bool = False) -> list:
    """Dev servers Y demos del propio Jarvis presentes en el texto de un pane,
    MEZCLADOS y de la MÁS RECIENTE a la más vieja: [{'url', 'tipo'}]. Mismos
    filtros que extraer_urls_locales (puertos excluidos, callbacks OAuth,
    líneas de login) y extraer_demos_jarvis (solo /static/<dir> fuera de la
    app). Alimenta la búsqueda on-demand del salto del preview, donde importa
    el MÁS RECIENTE global sin distinguir de entrada server/demo."""
    limpio = _ANSI_RE.sub('', texto or '')
    out, vistas = [], set()
    for m in reversed(list(_URL_LOCAL_RE.finditer(limpio))):
        url = normalizar_url_local(m.group(0))
        if not url or url in vistas:
            continue
        if es_demo_jarvis(url):
            vistas.add(url)
            out.append({'url': url, 'tipo': 'demo'})
            continue
        if puerto_excluido(puerto_de(url), es_expo):
            continue
        if es_callback_oauth(url):
            continue
        if _linea_de_login(limpio, m.start(), m.end()):
            continue
        vistas.add(url)
        out.append({'url': url, 'tipo': 'server'})
    return out


# ─── Fuente 2: puertos TCP en LISTEN (ground-truth del SO) ───────────────────
# Muchos dev servers NO imprimen su URL en un pane rastreado: se levantan
# detached/background (huérfanos bajo /init, subprocesos del propio Jarvis, el
# Bash de un agente). El raspado de texto del pane no los ve nunca. Esta fuente
# los detecta por el puerto real en LISTEN y los atribuye a un proyecto por
# ancestría de proceso (→ terminal) o, en su defecto, por el cwd del proceso.

_PID_RE = re.compile(r'pid=(\d+)')


def parsear_ss_listen(texto: str) -> list:
    """Parsea la salida de `ss -tlnpH` → [{'port': int, 'pid': int|None}].

    Formato de cada línea (sin header, -H):
      LISTEN 0 5  0.0.0.0:5050  0.0.0.0:*  users:(("python3",pid=3412044,fd=3))
    La 4ª columna es la dirección local (host:puerto); el pid sale del bloque
    users:((...)). Las líneas sin proceso (resolvers DNS del sistema) quedan con
    pid=None y el caller las descarta (no se pueden atribuir a un proyecto)."""
    out = []
    for linea in (texto or '').splitlines():
        campos = linea.split()
        if len(campos) < 4:
            continue
        local = campos[3]
        # host:puerto → el puerto es lo que sigue al último ':'. IPv6: [::]:5050.
        if ':' not in local:
            continue
        cola = local.rsplit(':', 1)[1]
        try:
            port = int(cola)
        except ValueError:
            continue
        m = _PID_RE.search(linea)
        out.append({'port': port, 'pid': int(m.group(1)) if m else None})
    return out


def proyecto_de_cwd(cwd: Optional[str], proyectos: list) -> Optional[int]:
    """Mapea el cwd de un proceso al proyecto cuyo `ruta` lo contiene (match de
    prefijo más largo, así un proyecto anidado le gana al padre). `proyectos` =
    [{'id': int, 'ruta': str}]. Devuelve el id o None."""
    if not cwd:
        return None
    cwd = cwd.rstrip('/')
    mejor_id, mejor_len = None, -1
    for p in proyectos:
        ruta = (p.get('ruta') or '').rstrip('/')
        if not ruta:
            continue
        if cwd == ruta or cwd.startswith(ruta + '/'):
            if len(ruta) > mejor_len:
                mejor_id, mejor_len = p.get('id'), len(ruta)
    return mejor_id


# ─── Estado del poller ─────────────────────────────────────────────────────────

# project_id → dict {url: {'terminal_id', 'terminal_nombre'}}. VARIOS dev
# servers por proyecto: un agente puede levantar 3 localhost, o varios agentes
# uno cada uno → TODOS se rastrean y el Web Preview los abre como pestañas.
# El orden del dict = orden de detección (la más reciente queda al final).
_detectados: dict = {}

# project_id → set de URLs que el usuario descartó (✕ del pill): no volver a
# anunciarlas mientras el server siga vivo. Se limpia cuando el puerto muere,
# así un server NUEVO en el mismo puerto sí re-anuncia.
_descartadas: dict = {}

# ruta → (timestamp, bool) — cache del check Expo (lee package.json del disco)
_cache_expo: dict = {}
_CACHE_EXPO_TTL = 60

# ── Persistencia del estado (sobrevive reinicios del server) ───────────────────
# El estado detectados/descartadas era SOLO memoria: cada update in-app (re-exec)
# lo perdía y la atribución server→terminal quedaba vacía. El re-scan del pane
# recupera lo que sigue en el scrollback, pero un agente TUI (claude fullscreen,
# alt-screen) NO retiene la URL en el history de tmux → irrecuperable. Por eso
# el estado va a disco (best-effort) y se recarga al boot; el ciclo de liveness
# purga en segundos lo que ya murió (TCP para servers, disco para demos).
_PERSIST_PATH = ruta_data('dev_servers.json')
_persist_cargado = False
_persist_ultimo = None   # último JSON escrito — evita reescrituras por tick


def _persistir_estado() -> None:
    global _persist_ultimo
    data = json.dumps({
        'detectados': {str(pid): urls for pid, urls in _detectados.items()},
        'descartadas': {str(pid): sorted(urls) for pid, urls in _descartadas.items()},
    }, ensure_ascii=False)
    if data == _persist_ultimo:
        return
    try:
        tmp = _PERSIST_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(data)
        os.replace(tmp, _PERSIST_PATH)
        _persist_ultimo = data
    except OSError:
        pass   # disco lleno/permiso: la detección sigue, solo sin memoria post-reinicio


def _cargar_estado() -> None:
    global _persist_cargado
    _persist_cargado = True
    try:
        with open(_PERSIST_PATH, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return   # sin archivo (primer boot) o corrupto: arrancar vacío
    try:
        for pid, urls in (data.get('detectados') or {}).items():
            if isinstance(urls, dict):
                _detectados.setdefault(int(pid), {}).update(urls)
        for pid, urls in (data.get('descartadas') or {}).items():
            _descartadas.setdefault(int(pid), set()).update(urls)
    except (TypeError, ValueError):
        pass


def _asegurar_cargado() -> None:
    if not _persist_cargado:
        _cargar_estado()


def urls_detectadas(project_id: int) -> list:
    """Todas las URLs de dev servers vivos del proyecto, en orden de detección
    (la más reciente al final). El Web Preview abre una pestaña por cada una."""
    _asegurar_cargado()
    return list(_detectados.get(project_id, {}).keys())


def servers_detectados(project_id: int) -> list:
    """Detalle de cada entrada viva: [{url, terminal_id, terminal_nombre, tipo}],
    en orden de detección. tipo = 'server' (dev server con proceso) o 'demo'
    (página servida por el propio Jarvis). Lo consume el menú de localhost."""
    _asegurar_cargado()
    return [
        {'url': url, 'terminal_id': info.get('terminal_id'),
         'terminal_nombre': info.get('terminal_nombre'),
         'tipo': info.get('tipo') or 'server'}
        for url, info in _detectados.get(project_id, {}).items()
    ]


def url_detectada(project_id: int) -> Optional[str]:
    """URL más reciente del dev server detectado para el proyecto, o None.
    El pill de la barra es single → muestra el último localhost que apareció."""
    _asegurar_cargado()
    urls = _detectados.get(project_id)
    return next(reversed(urls)) if urls else None


def descartar(project_id: int, url: Optional[str] = None) -> Optional[str]:
    """El usuario cerró/paró un server: dejar de anunciar esa URL (el server
    del agente puede seguir vivo en su tmux — no es nuestro, no lo matamos).
    Sin url descarta la más reciente; con url descarta esa específica.
    Devuelve la URL descartada o None."""
    _asegurar_cargado()
    urls = _detectados.get(project_id)
    if not urls:
        return None
    if url is None:
        url = next(reversed(urls))
    if url not in urls:
        return None
    urls.pop(url, None)
    if not urls:
        _detectados.pop(project_id, None)
    _descartadas.setdefault(project_id, set()).add(url)
    _persistir_estado()
    return url


# ─── Helpers async ─────────────────────────────────────────────────────────────

async def _puerto_vivo(url: str, timeout: float = 0.6) -> bool:
    """TCP connect al host:puerto de la URL. asyncio nativo — no bloquea."""
    try:
        p = urlparse(url)
        host = p.hostname or 'localhost'
        port = p.port or (443 if p.scheme == 'https' else 80)
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def _capture_pane(terminal_id: int) -> str:
    """Pane tmux vía la captura COMPARTIDA con cache: una sola captura por terminal por tick sirve
    a los tres pollers (antes cada uno forkeaba su propio capture-pane). Es el mismo bloque que el
    viejo `-S -120`; las URLs se TCP-chequean antes de anunciar, así que ver un poco más de scrollback
    es inocuo."""
    return await pane_capture.capturar(terminal_id)


def _proyecto_es_expo(ruta: str) -> bool:
    """Lazy import (core → router, solo acá) + cache: lee disco cada 60s máx."""
    ahora = time.monotonic()
    cacheado = _cache_expo.get(ruta)
    if cacheado and ahora - cacheado[0] < _CACHE_EXPO_TTL:
        return cacheado[1]
    try:
        from plotspace.routers.mobile_preview import _es_proyecto_expo
        es = _es_proyecto_expo(ruta)
    except Exception:
        es = False
    _cache_expo[ruta] = (ahora, es)
    return es


# ─── Búsqueda ON-DEMAND: el localhost de UNA terminal (salto del preview) ──────

async def _scrollback_completo(terminal_id: int) -> str:
    """TODO el buffer del pane (el poller solo ve las últimas LINEAS_PANE
    líneas vía pane_capture) — mismo patrón que el rescate del watchdog."""
    try:
        return await backend().capturar_async(terminal_id, TODO_EL_SCROLLBACK)
    except Exception:   # motor caído / sesión inexistente / timeout
        return ''


def _row_terminal(terminal_id: int) -> Optional[dict]:
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT t.nombre AS tnombre, t.project_id AS pid, p.ruta AS ruta
            FROM terminals t JOIN projects p ON p.id = t.project_id
            WHERE t.id = ?
        ''', (terminal_id,))
        r = cursor.fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


async def buscar_url_de_terminal(project_id: int, terminal_id: int) -> Optional[dict]:
    """El localhost MÁS RECIENTE que levantó ESA terminal (server o demo) —
    fuente on-demand del salto del Web Preview al maximizar/seleccionar la
    card del agente.

    1. Snapshot vivo (_detectados) si ya tiene una entrada de esa terminal.
    2. Si no: escanea el scrollback COMPLETO del pane — el poller solo ve las
       últimas líneas, así que tras un reinicio del server (estado en memoria)
       o mucho output la URL ya scrolleó y el snapshot quedó vacío — y valida
       liveness (TCP para servers, disco para demos). Lo encontrado se
       registra en _detectados SIN broadcast (la UI la maneja quien pidió el
       salto; el menú de localhost lo levanta en su próximo fetch).
    Respeta las URLs descartadas por el usuario (✕). Devuelve {'url', 'tipo'}
    o None."""
    _asegurar_cargado()
    vivos = _detectados.get(project_id) or {}
    for url in reversed(list(vivos.keys())):
        info = vivos[url]
        if info.get('terminal_id') == terminal_id:
            return {'url': url, 'tipo': info.get('tipo') or 'server'}

    row = await asyncio.to_thread(_row_terminal, terminal_id)
    if not row or row['pid'] != project_id:
        return None
    texto = await _scrollback_completo(terminal_id)
    if not texto:
        return None
    es_expo = _proyecto_es_expo(row['ruta'])
    descartadas = _descartadas.get(project_id) or set()
    for cand in extraer_candidatos_pane(texto, es_expo):
        if cand['url'] in descartadas:
            continue
        if cand['tipo'] == 'demo':
            vivo = demo_vivo(cand['url'])
        else:
            vivo = await _puerto_vivo(cand['url'])
        if not vivo:
            continue
        _detectados.setdefault(project_id, {})[cand['url']] = {
            'terminal_id': terminal_id, 'terminal_nombre': row['tnombre'],
            'tipo': cand['tipo'],
        }
        _persistir_estado()
        return cand
    return None


# ─── Helpers de la fuente 2 (puertos LISTEN) — syscalls, no event loop ──────────

def _puertos_listen() -> set:
    """Puertos TCP en LISTEN (IPv4+IPv6). Corre en CADA tick del poller, así que
    tiene que ser barato.

    psutil primero: es el mismo código en Linux, macOS y Windows (donde
    /proc no existe). El lector de /proc/net/tcp[6] queda de respaldo — es aún
    más barato en Linux y cubre el caso de psutil ausente. La columna `st` ==
    '0A' es LISTEN; la dirección local viene como HEX `IP:PUERTO`."""
    try:
        import psutil
        return {c.laddr.port for c in psutil.net_connections(kind='inet')
                if c.status == psutil.CONN_LISTEN and c.laddr}
    except Exception:
        pass
    ports = set()
    for path in ('/proc/net/tcp', '/proc/net/tcp6'):
        try:
            with open(path) as f:
                next(f, None)  # header
                for linea in f:
                    campos = linea.split()
                    if len(campos) > 3 and campos[3] == '0A':
                        try:
                            ports.add(int(campos[1].rsplit(':', 1)[1], 16))
                        except (ValueError, IndexError):
                            pass
        except Exception:
            pass
    return ports


def _ss_listeners() -> list:
    """[{'port', 'pid'}] de los sockets en LISTEN, con el pid del proceso dueño.
    Solo se llama cuando hay puertos candidatos nuevos (raro), así que su costo
    es marginal. psutil primero (multiplataforma); `ss -tlnpH` de respaldo."""
    try:
        import psutil
        vistos = set()
        salida = []
        for c in psutil.net_connections(kind='inet'):
            if c.status != psutil.CONN_LISTEN or not c.laddr or not c.pid:
                continue
            clave = (c.laddr.port, c.pid)
            if clave in vistos:      # IPv4 e IPv6 del mismo server son una sola cosa
                continue
            vistos.add(clave)
            salida.append({'port': c.laddr.port, 'pid': c.pid})
        if salida:
            return salida
    except Exception:
        pass
    try:
        r = subprocess.run(['ss', '-tlnpH'], capture_output=True, text=True, timeout=3)
        return parsear_ss_listen(r.stdout)
    except Exception:
        return []


def _cwd_de_pid(pid: int) -> Optional[str]:
    """Directorio de trabajo del proceso — así se atribuye un puerto a un
    proyecto. psutil primero (multiplataforma); /proc de respaldo en Linux."""
    try:
        import psutil
        return psutil.Process(pid).cwd()
    except Exception:
        pass
    try:
        return os.readlink(f'/proc/{pid}/cwd')
    except OSError:
        return None


def _cmdline_o_exe_es_login_cli(cmd: str, exe: str) -> bool:
    """¿La cmdline o el exe corresponden a un CLI de IA? PURA (strings) → testeable
    sin /proc. El match es sobre el proceso DUEÑO del puerto, NUNCA sobre el nombre
    /cwd del proyecto: un dev server real en un proyecto claude-x/opencode-y/codex-app
    NO cae (su cmdline/exe no contiene los paths de paquete/instalación del CLI)."""
    cmd = (cmd or '').lower()
    exe = (exe or '').lower()
    # Los markers de paquete pueden caer en la cmdline (node CLIs: 'node /x/@openai/codex')
    # o en el exe (opencode: cmdline 'opencode auth login' pero exe '.../opencode-ai/...').
    if any(m in cmd or m in exe for m in _CLI_LOGIN_MARKERS):
        return True
    if any(m in exe for m in _CLI_LOGIN_EXE_MARKERS):
        return True
    # antigravity CLI (agy): binario nativo con cmdline PELADA 'agy'. Al arrancar
    # bindea 2 puertos loopback EFÍMEROS para su IPC interno (TUI ↔ backend del
    # agente), NO dev servers del proyecto. Discriminador robusto = el BASENAME del
    # exe es 'agy' (un dev server real nunca lo tiene, aunque el proyecto se llame
    # 'agy'; su exe sería node/python/…). Substring de exe no sirve: '/agy' cae en
    # '/home/user/agy-proj/...'. Por eso basename exacto, no `in`.
    if os.path.basename(exe.rstrip('/')) == 'agy':
        return True
    # codex (binario rust), durante `codex login`: el subcomando queda en la cmdline.
    return 'codex' in cmd and 'login' in cmd


def _proceso_es_login_cli(pid: Optional[int]) -> bool:
    """¿El proceso dueño del puerto es un CLI de IA (gemini/codex/claude/opencode)?
    Esos no levantan dev servers del proyecto: el localhost que bindean es el callback
    de su login OAuth o su server interno (p.ej. el IDE-server de claude) → no
    anunciarlo (rompía el login y amontonaba localhost inútiles en el preview). Lee
    la cmdline Y el ejecutable (claude nativo tiene cmdline pelada → exe).
    psutil primero (multiplataforma), /proc de respaldo.
    Falla-abierto: sin datos legibles → False (comportamiento previo)."""
    if not pid:
        return False
    cmd = exe = None
    try:
        import psutil
        proc = psutil.Process(pid)
        cmd = ' '.join(proc.cmdline())
        try:
            exe = proc.exe()
        except Exception:
            exe = ''
    except Exception:
        pass
    if cmd is None:
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as f:
                cmd = f.read().replace(b'\x00', b' ').decode('utf-8', 'replace')
        except OSError:
            return False
        try:
            exe = os.readlink(f'/proc/{pid}/exe')
        except OSError:
            exe = ''
    return _cmdline_o_exe_es_login_cli(cmd, exe or '')


def _ppid_de(pid: int) -> Optional[int]:
    """ppid leyendo /proc/<pid>/stat. El comm va entre paréntesis y puede tener
    espacios → tomamos lo que sigue al ÚLTIMO ')'; ppid es el 2º campo de ahí."""
    try:
        with open(f'/proc/{pid}/stat') as f:
            data = f.read()
        cola = data[data.rindex(')') + 1:].split()
        return int(cola[1])  # state, ppid, ...
    except (OSError, ValueError, IndexError):
        return None


def _ancestros(pid: int) -> list:
    """[pid, ppid, abuelo, …] hasta init. Sirve para ver si un server desciende
    del shell de algún pane tmux (pane_pid)."""
    out, vistos, cur = [], set(), pid
    for _ in range(40):
        if not cur or cur in vistos or cur <= 1:
            break
        vistos.add(cur)
        out.append(cur)
        cur = _ppid_de(cur)
    return out


def _pane_pids_por_terminal(rows: list) -> dict:
    """{pane_pid: row} de las terminales activas (vía tmux). Un server cuyo árbol
    de ancestros incluye un pane_pid fue levantado en ESA terminal → se le
    atribuye con nombre. Solo se arma si hay candidatos nuevos."""
    m = {}
    for row in rows:
        try:
            pid = backend().pid_de_terminal(row['tid'])
            if pid is not None:
                m[pid] = row
        except Exception:
            pass
    return m


def _atribuir_puerto(pid: Optional[int], pane_pids: dict, proyectos: list) -> Optional[dict]:
    """Atribuye un puerto a un proyecto: 1) ancestría → terminal (preciso, da
    nombre); 2) cwd → proyecto. Sin pid o sin proyecto → None (se descarta:
    resolvers DNS del sistema, listeners ajenos)."""
    if not pid:
        return None
    for a in _ancestros(pid):
        row = pane_pids.get(a)
        if row:
            return {'project_id': row['pid'], 'terminal_id': row['tid'],
                    'terminal_nombre': row['tnombre']}
    proj = proyecto_de_cwd(_cwd_de_pid(pid), proyectos)
    if proj is not None:
        return {'project_id': proj, 'terminal_id': None, 'terminal_nombre': None}
    return None


# ─── Poller ────────────────────────────────────────────────────────────────────

async def poller_dev_servers():
    """Background task: nunca crashea el servidor (patrón STATE.md)."""
    while True:
        await asyncio.sleep(INTERVALO_S)
        try:
            await _ciclo()
        except Exception as e:
            print(f'[dev-detect] Error en ciclo: {e}')


def _rows_activas():
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT t.id AS tid, t.nombre AS tnombre, t.project_id AS pid, p.ruta AS ruta
            FROM terminals t JOIN projects p ON p.id = t.project_id
            WHERE t.activa = 1
        ''')
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def _proyectos_rows():
    """Todos los proyectos (id, ruta) — para atribuir un puerto por cwd aunque el
    proyecto no tenga terminales activas ahora mismo."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT id, ruta FROM projects')
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def _puertos_ya_conocidos() -> set:
    """Puertos ya rastreados o descartados (en cualquier proyecto): no son
    candidatos nuevos del escaneo de puertos → así no reatribuimos cada tick."""
    ports = set()
    for urls in _detectados.values():
        ports.update(p for p in (puerto_de(u) for u in urls) if p)
    for urls in _descartadas.values():
        ports.update(p for p in (puerto_de(u) for u in urls) if p)
    return ports


async def _ciclo():
    _asegurar_cargado()
    # DB síncrona fuera del event loop (to_thread) — ver nota en agent_watch._ciclo.
    rows = await asyncio.to_thread(_rows_activas)
    # Puertos en LISTEN del SO (barato, sin pid) — fuente 2 + limpieza de descartadas.
    listen_ports = await asyncio.to_thread(_puertos_listen)

    # 1. Liveness POR URL: cada dev server rastreado se chequea. Si su puerto
    #    murió, se limpia y se avisa (la pestaña del preview queda — no la
    #    cerramos sola). Al morir se saca de descartadas: un server NUEVO en el
    #    mismo puerto vuelve a anunciarse.
    # Liveness EN PARALELO: antes era un await secuencial por URL → con K dev
    # servers caídos el ciclo tardaba 0.6s×K y retrasaba la detección de los
    # nuevos. gather corta el wall-clock al del puerto más lento (mismo patrón
    # que las capturas en agent_watch._ciclo). El procesamiento de los muertos
    # queda igual: un dev_server_caido por URL, en orden estable.
    pares = [(pid, url) for pid in list(_detectados.keys())
             for url in list(_detectados[pid].keys())]

    async def _sigue_vivo(pid, url):
        # Un demo del propio Jarvis vive mientras existan sus archivos (el
        # puerto 3000 siempre responde — chequearlo no diría nada).
        info = _detectados.get(pid, {}).get(url) or {}
        if info.get('tipo') == 'demo':
            return await asyncio.to_thread(demo_vivo, url)
        return await _puerto_vivo(url)

    vivos = await asyncio.gather(*[_sigue_vivo(pid, url) for pid, url in pares])
    for (pid, url), vivo in zip(pares, vivos):
        if vivo:
            continue
        if pid not in _detectados:
            continue   # el pid pudo quedar limpio por una URL anterior del mismo ciclo
        _detectados[pid].pop(url, None)
        desc = _descartadas.get(pid)
        if desc:
            desc.discard(url)
        await broadcaster.broadcast(pid, {'type': 'dev_server_caido', 'url': url})
        print(f"[dev-detect] Caído {url} (proyecto {pid})")
        if not _detectados.get(pid):
            _detectados.pop(pid, None)
            _descartadas.pop(pid, None)

    # 1b. Limpieza de descartadas cuyo puerto ya NO escucha: la liveness de
    #     arriba solo recorre _detectados, así que un server descartado a mano
    #     nunca se sacaba de _descartadas y bloqueaba para siempre el re-anuncio
    #     de un server nuevo en ese mismo puerto. Acá lo resolvemos con el set de
    #     puertos vivos (barato, ya lo tenemos).
    for pid in list(_descartadas.keys()):
        # Demos descartados: siguen bloqueados mientras sus archivos existan
        # (el pane los sigue mostrando); si los borran, se libera el re-anuncio.
        vivas = {u for u in _descartadas[pid]
                 if (demo_vivo(u) if es_demo_jarvis(u) else puerto_de(u) in listen_ports)}
        if vivas:
            _descartadas[pid] = vivas
        else:
            _descartadas.pop(pid, None)

    # 2. Buscar servers NUEVOS en TODAS las terminales activas (no cortamos al
    #    primero: un pane puede imprimir varios localhost y un proyecto puede
    #    tener varias terminales sirviendo). Cada URL viva y no rastreada se
    #    anuncia → el Web Preview la abre en su propia pestaña.
    for row in rows:
        pid = row['pid']
        texto = await _capture_pane(row['tid'])
        if not texto:
            continue
        # Demos del propio Jarvis (:3000/static/<dir>/…): un agente dejó un
        # mockup para que el usuario lo vea y elija. Van SIEMPRE (también en
        # Expo — no dependen del dev server del proyecto). tipo='demo' → el ✕
        # del menú solo los oculta, nunca toca el proceso del 3000.
        for url in reversed(extraer_demos_jarvis(texto)):
            if url in _detectados.get(pid, {}):
                continue
            if url in _descartadas.get(pid, set()):
                continue
            if not await asyncio.to_thread(demo_vivo, url):
                continue
            _detectados.setdefault(pid, {})[url] = {
                'terminal_id': row['tid'], 'terminal_nombre': row['tnombre'],
                'tipo': 'demo',
            }
            await broadcaster.broadcast(pid, {
                'type': 'dev_server_detectado',
                'url': url,
                'terminal_id': row['tid'],
                'terminal_nombre': row['tnombre'],
                'tipo': 'demo',
            })
            print(f"[dev-detect] demo {url} detectado en terminal {row['tid']} (proyecto {pid})")
        # Proyectos Expo: el dev server lo muestra el Mobile Preview (marco de
        # teléfono), NO el Web Preview. No anunciamos dev servers para Expo → el
        # Web Preview no se auto-abre ni aparece su pill (pedido del usuario).
        if _proyecto_es_expo(row['ruta']):
            continue
        # extraer_urls_locales viene más-reciente-primero; anunciamos de la más
        # vieja a la más nueva para que la pestaña recién abierta (la más
        # reciente) quede activa en el preview.
        for url in reversed(extraer_urls_locales(texto)):
            if url in _detectados.get(pid, {}):
                continue
            if url in _descartadas.get(pid, set()):
                continue
            if not await _puerto_vivo(url):
                continue
            _detectados.setdefault(pid, {})[url] = {
                'terminal_id': row['tid'], 'terminal_nombre': row['tnombre'],
            }
            await broadcaster.broadcast(pid, {
                'type': 'dev_server_detectado',
                'url': url,
                'terminal_id': row['tid'],
                'terminal_nombre': row['tnombre'],
            })
            print(f"[dev-detect] {url} detectado en terminal {row['tid']} (proyecto {pid})")

    # 3. Dev servers que NO imprimieron su URL en un pane rastreado (se
    #    levantaron detached/background: huérfanos bajo /init, subprocesos del
    #    propio Jarvis, el Bash de un agente). Los cazamos por el PUERTO real en
    #    LISTEN — ground truth del SO — y los atribuimos a un proyecto por
    #    ancestría de proceso (→ terminal) o por cwd. Solo trabajamos los puertos
    #    candidatos NUEVOS (no rastreados ni descartados), así el costo del
    #    escaneo de pid/ancestría es marginal (casi siempre 0 candidatos).
    candidatos = {p for p in listen_ports if p != PUERTO_JARVIS} - _puertos_ya_conocidos()
    if not candidatos:
        return
    proyectos = await asyncio.to_thread(_proyectos_rows)
    ruta_de = {p['id']: p.get('ruta') for p in proyectos}
    listeners = await asyncio.to_thread(_ss_listeners)
    pane_pids = await asyncio.to_thread(_pane_pids_por_terminal, rows)
    for lst in listeners:
        port = lst['port']
        if port not in candidatos:
            continue
        if _proceso_es_login_cli(lst['pid']):
            continue   # callback efímero de un login OAuth (gemini/codex), no un dev server
        info = _atribuir_puerto(lst['pid'], pane_pids, proyectos)
        if not info:
            continue   # sin proyecto (resolver DNS, listener del sistema) → fuera
        pid = info['project_id']
        if puerto_excluido(port, _proyecto_es_expo(ruta_de.get(pid, '') or '')):
            continue
        url = f'http://localhost:{port}'
        # dedup por puerto dentro del proyecto: el pane-scan pudo registrar la
        # misma URL con path (http://localhost:5173/app) — no duplicar.
        if any(puerto_de(u) == port for u in _detectados.get(pid, {})):
            continue
        if url in _descartadas.get(pid, set()):
            continue
        if not await _puerto_vivo(url):
            continue
        _detectados.setdefault(pid, {})[url] = {
            'terminal_id': info['terminal_id'], 'terminal_nombre': info['terminal_nombre'],
        }
        await broadcaster.broadcast(pid, {
            'type': 'dev_server_detectado',
            'url': url,
            'terminal_id': info['terminal_id'],
            'terminal_nombre': info['terminal_nombre'],
        })
        print(f"[dev-detect] {url} detectado por puerto LISTEN "
              f"(pid {lst['pid']}, proyecto {pid})")
