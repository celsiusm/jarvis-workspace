"""
JARVIS — Sistema: versión + reinicio in-app.

El banner "Actualizar" de la franja lee /api/system/version. La detección es
AUTOMÁTICA vía git: al bootear, el server se guarda el HEAD del repo. hay_update
se prende cuando hay un COMMIT NUEVO desde el arranque — es decir, cuando un
agente TERMINÓ una tarea y la commiteó. Las ediciones sin commitear (trabajo en
curso) NO prenden el banner: así "Actualizar ahora" aparece apenas una terminal
commitea lo suyo y se puede aplicar lo terminado, aunque otra terminal de Jarvis
siga trabajando (pedido del usuario 2026-06-18). Como el HEAD es el de ESTE repo,
solo cuenta el código de Jarvis. Las novedades salen de los mensajes de commit.
Sin bump manual de nada.

Si el proyecto no fuera un repo git, cae al modo viejo: compara el archivo
VERSION corriendo vs el de disco. /api/system/restart hace un re-exec del MISMO
proceso (os.execv): mismo PID, misma sesión y misma terminal — si el usuario
aloja el server en su terminal, sigue alojado ahí después de actualizar.

Para probar el banner a mano: editá cualquier archivo de plotspace/ o frontend/
(la firma git cambia y /version pasa a hay_update=true).
"""
import asyncio
import hashlib
import os
import re
import subprocess
import sys
import threading
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from plotspace.core import agent_watch
from plotspace.core.database import get_db

router = APIRouter(prefix="/api/system", tags=["system"])

_ARRANQUE = time.time()   # ~arranque del proceso (para el uptime del endpoint de métricas)

_REPO_ROOT      = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_VERSION_PATH   = os.path.join(_REPO_ROOT, 'VERSION')
_CHANGELOG_PATH = os.path.join(_REPO_ROOT, 'CHANGELOG.md')


# ─── Lógica pura (testeable) ─────────────────────────────────────────────────

def _semver_tuple(s: str) -> tuple:
    out = []
    for p in (s or '').strip().split('.')[:4]:
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    while len(out) < 4:
        out.append(0)
    return tuple(out)


def _semver_mayor(a: str, b: str) -> bool:
    """True si a > b como semver x.y.z[.h] (el 4º segmento es el hotfix)."""
    return _semver_tuple(a) > _semver_tuple(b)


def calcular_siguiente_version(actual: str, hotfix: bool = False) -> str:
    """Versionado automático por update aplicado, formato x.x.xx (el patch
    siempre con dos dígitos). Update normal: patch +1 (1.5.08 → 1.5.09;
    descarta el segmento hotfix: 1.5.01.3 → 1.5.02) y desborda al minor en 99
    (1.5.99 → 1.6.00). Hotfix (todo lo nuevo son fixes): suma un 4º segmento
    (1.5.08 → 1.5.08.1)."""
    partes = []
    for p in (actual or '').strip().split('.')[:4]:
        try:
            partes.append(int(p))
        except ValueError:
            partes.append(0)
    while len(partes) < 3:
        partes.append(0)
    if hotfix:
        if len(partes) >= 4:
            partes[3] += 1
        else:
            partes.append(1)
    else:
        partes = partes[:3]
        partes[2] += 1
        if partes[2] > 99:
            partes[2] = 0
            partes[1] += 1
    cuerpo = f'{partes[0]}.{partes[1]}.{partes[2]:02d}'
    return cuerpo if len(partes) < 4 else f'{cuerpo}.{partes[3]}'


def _novedades_entre(changelog: str, corriendo: str, disponible: str) -> list:
    """Secciones del changelog con versión en (corriendo, disponible].
    Formato esperado: '## X.Y.Z' + bullets '- …'. Más nueva primero."""
    secciones = []
    actual = None
    for linea in (changelog or '').splitlines():
        m = re.match(r'^##\s+(\d+\.\d+\.\d+)\s*$', linea.strip())
        if m:
            actual = {'version': m.group(1), 'items': []}
            secciones.append(actual)
        elif actual is not None:
            t = linea.strip()
            if t.startswith('- '):
                actual['items'].append(t[2:].strip())
    return [s for s in secciones
            if _semver_mayor(s['version'], corriendo)
            and not _semver_mayor(s['version'], disponible)]


# ─── Git (detección automática de cambios) ───────────────────────────────────

def _entorno_git():
    from plotspace.core.gitutil import entorno_no_interactivo
    return entorno_no_interactivo()


def _git(*args):
    """Corre git en el repo. Devuelve stdout (puede ser '') o None si falló
    (rc!=0, no es repo, git ausente, timeout)."""
    try:
        r = subprocess.run(
            ['git', '-C', _REPO_ROOT, *args],
            capture_output=True, text=True, timeout=5,
            # Ver core/gitutil: nada de prompts ni ventanas de login desde el motor.
            env=_entorno_git(),
        )
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


# Solo estas rutas cuentan como "el código" (lo que un reinicio aplica). El resto
# del árbol —.jarvis/LIVE.md, MAILBOX, data/, .workspace/, docs— la app lo reescribe
# en runtime y no es una versión nueva: incluirlo prendía el aviso solo (falso positivo).
_RUTAS_CODIGO = ('plotspace', 'frontend')


def _firma_codigo():
    """Firma del estado del código: (head, hash). Cubre el HEAD (cualquier commit
    nuevo) + el diff y los archivos sin trackear DENTRO de plotspace/ y frontend/.
    Cambia ante cualquier edición de código, pero NO ante los archivos que la app
    toca sola. Devuelve None si no es un repo git."""
    head = _git('rev-parse', 'HEAD')
    if head is None:
        return None
    diff   = _git('diff', 'HEAD', '--', *_RUTAS_CODIGO) or ''
    status = _git('status', '--porcelain', '--', *_RUTAS_CODIGO) or ''
    h = hashlib.sha1()
    h.update(head.encode());   h.update(b'\0')
    h.update(diff.encode());   h.update(b'\0')
    h.update(status.encode())
    # Los untracked no entran al diff: sumar tamaño+mtime de cada uno para que
    # también disparen sus EDICIONES (no solo su aparición en el status).
    untracked = _git('ls-files', '--others', '--exclude-standard', '--', *_RUTAS_CODIGO) or ''
    for rel in untracked.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        try:
            st = os.stat(os.path.join(_REPO_ROOT, rel))
            h.update(f'\0{rel}:{st.st_size}:{st.st_mtime_ns}'.encode())
        except OSError:
            h.update(f'\0{rel}:gone'.encode())
    return (head.strip(), h.hexdigest())


# Estado del código con el que arrancó ESTE proceso. Antes se computaba al
# importar el módulo, pero _firma_codigo() corre 3-4 git y bajo carga tardó
# 3.07s de import (medido 2026-07-02) — retrasaba el arranque completo del
# server. Ahora es LAZY: un hilo lo captura apenas importa (≈ estado de boot,
# sin bloquear) y cualquier lector lo asegura vía _asegurar_firma_boot().
_FIRMA_BOOT  = None                                # (head, hash) | None
_COMMIT_BOOT = None
_FIRMA_BOOT_LISTA = False
_firma_boot_lock = threading.Lock()


def _asegurar_firma_boot():
    """Captura la firma de boot UNA sola vez, fuera del import. Respeta valores
    ya seteados (p. ej. monkeypatch de tests): solo computa si _FIRMA_BOOT
    sigue en None."""
    global _FIRMA_BOOT, _COMMIT_BOOT, _FIRMA_BOOT_LISTA
    if _FIRMA_BOOT_LISTA:
        return
    with _firma_boot_lock:
        if _FIRMA_BOOT_LISTA:
            return
        if _FIRMA_BOOT is None:
            f = _firma_codigo()
            _FIRMA_BOOT = f
            _COMMIT_BOOT = f[0] if f else None
        _FIRMA_BOOT_LISTA = True


# La captura arranca ya, en paralelo al boot: para cuando llegue el primer
# /version la firma está lista sin haber frenado el import ni el request.
threading.Thread(target=_asegurar_firma_boot, daemon=True,
                 name='jarvis-firma-boot').start()


_TITULO = "Hay una nueva versión"


# ─── Novedades auto-detectadas (como los títulos vivos de las terminales) ─────
# No se escriben a mano: se LEEN del git. Por los archivos que tocó cada commit
# nuevo se clasifica el cambio por área, y el texto sale del mensaje del commit
# limpiado y recortado (corto). Cero mantenimiento.

# Áreas en orden fijo de presentación, con su ícono. 'App' = cambios del shell
# de escritorio (desktop/): cuando el update los incluye, aparecen bajo esta
# etiqueta para que se lea "esto también actualiza la app".
_AREAS = [('Interfaz', '🎨'), ('Motor', '⚙️'), ('App', '🖥️'), ('Seguridad', '🔒'), ('General', '✨')]
_ICONO = dict(_AREAS)
_ORDEN = [a for a, _ in _AREAS]

# Prefijo de Conventional Commits: feat: / fix(scope): / refactor!: …
_PREFIJO_CC = re.compile(r'^(\w+)(?:\([^)]*\))?(?:!)?:\s+')
# Tipos meta que no son "novedad" para el usuario.
_TIPOS_RUIDO = frozenset({'chore', 'release', 'merge', 'ci', 'build', 'bump', 'wip'})
# Tipos que califican un update como hotfix (4º segmento de versión).
_TIPOS_HOTFIX = frozenset({'fix', 'hotfix'})
# Palabras que marcan un cambio como de seguridad (gana sobre el área por archivo).
_RE_SEGURIDAD = re.compile(r'segur|security|vuln|secret|cve|csrf|xss|ssrf', re.I)
_MAX_LEN = 72


def _limpiar_subject(subject: str):
    """(tipo, texto). Saca el prefijo `tipo(scope):`, capitaliza y recorta a un
    texto corto. texto='' si es un merge o queda vacío. tipo='' si no hay prefijo."""
    s = (subject or '').strip()
    if s.lower().startswith('merge '):
        return ('merge', '')
    m = _PREFIJO_CC.match(s)
    tipo = m.group(1).lower() if m else ''
    if m:
        s = s[m.end():].strip()
    if not s:
        return (tipo, '')
    s = s[0].upper() + s[1:]
    if len(s) > _MAX_LEN:
        s = s[:_MAX_LEN - 1].rstrip() + '…'
    return (tipo, s)


def _area_de(subject: str, files) -> str:
    """Área del cambio: Seguridad (por keyword) gana; después el shell (desktop/);
    si no, por carpeta tocada."""
    if _RE_SEGURIDAD.search(subject or ''):
        return 'Seguridad'
    if any(f.startswith('desktop/') for f in files):
        return 'App'
    if any(f.startswith('frontend/') for f in files):
        return 'Interfaz'
    if any(f.startswith('plotspace/') for f in files):
        return 'Motor'
    return 'General'


def _agrupar_novedades(commits) -> list:
    """commits: [{'subject','files'}] → [{'area','icon','items'}] por área (orden
    fijo), con texto corto/limpio, sin duplicados ni ruido (chore/merge/…)."""
    grupos = {a: [] for a in _ORDEN}
    for c in commits:
        tipo, texto = _limpiar_subject(c.get('subject', ''))
        if not texto or tipo in _TIPOS_RUIDO:
            continue
        area = _area_de(c.get('subject', ''), c.get('files', []))
        if texto not in grupos[area]:
            grupos[area].append(texto)
    return [{'area': a, 'icon': _ICONO[a], 'items': grupos[a]}
            for a in _ORDEN if grupos[a]]


def _es_hotfix(commits) -> bool:
    """True si lo nuevo son SOLO fixes (conventional commits): el update se
    versiona como hotfix (4º segmento). El ruido (chore/merge/…) no vota; sin
    commits con novedad (p. ej. ediciones sin commitear) es un update normal."""
    tipos = []
    for c in commits or []:
        tipo, texto = _limpiar_subject(c.get('subject', ''))
        if not texto or tipo in _TIPOS_RUIDO:
            continue
        tipos.append(tipo)
    return bool(tipos) and all(t in _TIPOS_HOTFIX for t in tipos)


def _commits_con_archivos() -> list:
    """[{'subject','files'}] de los commits en (_COMMIT_BOOT, HEAD]. Vacío si no
    hay commits nuevos. Usa \\x1f como separador de registro (no aparece en texto)."""
    _asegurar_firma_boot()
    if not _COMMIT_BOOT:
        return []
    out = _git('log', f'{_COMMIT_BOOT}..HEAD', '--name-only', '--format=\x1f%s')
    if not out:
        return []
    commits = []
    actual = None
    for linea in out.splitlines():
        if linea.startswith('\x1f'):
            actual = {'subject': linea[1:].strip(), 'files': []}
            commits.append(actual)
        elif linea.strip() and actual is not None:
            actual['files'].append(linea.strip())
    return commits


def _estado_git():
    """(hay_update, titulo, novedades) según git, o None si no es repo git.
    hay_update = hay un COMMIT NUEVO desde que arrancó el server (= una tarea que
    algún agente TERMINÓ y commiteó). Las ediciones sin commitear (trabajo en
    curso) NO disparan el banner: "Actualizar ahora" recién aparece cuando la
    tarea se commiteó, así se aplica lo terminado aunque otra terminal de Jarvis
    siga trabajando (pedido del usuario 2026-06-18). Como el HEAD es el de ESTE
    repo, solo cuenta el código de Jarvis (commits de otros proyectos no lo mueven).
    novedades = los commits nuevos, agrupados por área (vacío si HEAD no se movió)."""
    _asegurar_firma_boot()
    firma = _firma_codigo()
    if firma is None or _FIRMA_BOOT is None:
        return None
    head_actual, _ = firma
    if not (_COMMIT_BOOT and head_actual != _COMMIT_BOOT):
        return (False, '', [])    # sin commit nuevo (a lo sumo ediciones sin commitear)
    return (True, _TITULO, _agrupar_novedades(_commits_con_archivos()))


# ─── Lectura de disco (modo viejo / fallback sin git) ────────────────────────

def leer_version_disco() -> str:
    try:
        with open(_VERSION_PATH, encoding='utf-8') as f:
            return f.read().strip() or '0.0.0'
    except Exception:
        return '0.0.0'


def _leer_changelog() -> str:
    try:
        with open(_CHANGELOG_PATH, encoding='utf-8') as f:
            return f.read()
    except Exception:
        return ''


_VERSION_BOOT = leer_version_disco()


def _escribir_version(v: str):
    with open(_VERSION_PATH, 'w', encoding='utf-8') as f:
        f.write(v + '\n')


def _version_proxima(hay_update: bool) -> str:
    """A qué versión salta el próximo update aplicado (la que muestra el banner
    y la que /restart escribe). Sin update pendiente, es la corriente."""
    if not hay_update:
        return _VERSION_BOOT
    return calcular_siguiente_version(_VERSION_BOOT, hotfix=_es_hotfix(_commits_con_archivos()))


# ─── Cache del trabajo git de /version ───────────────────────────────────────
# _estado_git() son 3-4 subprocess git (y 2 más con update pendiente, vía
# _version_proxima). Con el disco cargado por el swarm se midió HASTA 22.9s por
# corrida (2026-07-02). Cada pestaña pollea /version cada 120s y fe_watch lo
# re-dispara tras CADA commit de agente: sin cache, N pestañas = N corridas.
# TTL 30s + lock anti-estampida: una corrida sirve a todos. El cache guarda
# también la identidad de _estado_git: si alguien lo reemplaza (tests), se
# invalida al toque. /restart NO pasa por acá (necesita frescura).
# Un commit nuevo NO espera el TTL: fe_watch llama a invalidar_cache_git()
# apenas ve moverse el HEAD, ANTES de avisar al browser — así el re-chequeo
# del banner "Actualizar ahora" ve git fresco y el banner aparece en segundos
# (con cache viejo veía hay_update=false y quedaba esperando el poll de 120s).

_GIT_TTL = 30.0
_git_cache = {'fn': None, 'ts': 0.0, 'val': None}
_git_cache_lock = threading.Lock()


def invalidar_cache_git():
    """Tira el snapshot cacheado: el próximo /version recomputa git fresco.
    La llama fe_watch al detectar un commit nuevo, antes del broadcast."""
    global _git_cache
    with _git_cache_lock:
        _git_cache = {'fn': None, 'ts': 0.0, 'val': None}


def _snapshot_git(ttl: float = None):
    """(estado, proxima) cacheados: estado = _estado_git(), proxima = la
    versión que anuncia el banner. Estado None si no es repo git."""
    global _git_cache
    ttl = _GIT_TTL if ttl is None else ttl
    fn = _estado_git
    c = _git_cache
    if c['fn'] is fn and (time.monotonic() - c['ts']) < ttl:
        return c['val']
    with _git_cache_lock:
        c = _git_cache
        if c['fn'] is fn and (time.monotonic() - c['ts']) < ttl:
            return c['val']
        estado = fn()
        proxima = _version_proxima(bool(estado[0])) if estado is not None else None
        val = (estado, proxima)
        _git_cache = {'fn': fn, 'ts': time.monotonic(), 'val': val}
        return val


# ─── Agentes trabajando (campo INFORMATIVO de /version) ──────────────────────
# OJO: ya NO gatea el banner. El banner se decide por COMMIT NUEVO (ver
# _estado_git): se actualiza lo terminado aunque haya agentes a mitad de tarea.
# Este flag queda como dato (¿hay laburo en curso en Jarvis?) por si lo quiere
# el modal/tooltip; se calcula SOLO sobre el proyecto de Jarvis (este repo):
# terminales trabajando o frenadas en un prompt (agent_watch). Agentes de otros
# proyectos no cuentan.

def _jarvis_project_id():
    """project_id del proyecto cuyo ruta es ESTE repo (donde corre el server). El
    código de Jarvis se trabaja solo en ese proyecto; los agentes de OTROS
    proyectos no lo tocan, así que no deben bloquear el update. None si no se
    encuentra (→ fallback global, conservador)."""
    try:
        real = os.path.realpath(_REPO_ROOT)
        conn = get_db()
        try:
            cur = conn.cursor()
            cur.execute('SELECT id, ruta FROM projects')
            for row in cur.fetchall():
                try:
                    if row['ruta'] and os.path.realpath(row['ruta']) == real:
                        return row['id']
                except Exception:
                    continue
        finally:
            conn.close()
    except Exception:
        pass
    return None


def _terminales_activas_de(project_id) -> set:
    try:
        conn = get_db()
        try:
            cur = conn.cursor()
            cur.execute('SELECT id FROM terminals WHERE project_id = ? AND activa = 1',
                        (project_id,))
            return {row['id'] for row in cur.fetchall()}
        finally:
            conn.close()
    except Exception:
        return set()


def _agentes_trabajando() -> bool:
    # SOLO el proyecto de Jarvis (este repo): un agente trabajando en OTRO
    # proyecto no toca este código → no bloquea el "Actualizar ahora". Si no
    # ubicamos el proyecto de Jarvis, fallback global (conservador, como antes).
    pid = _jarvis_project_id()
    tids = _terminales_activas_de(pid) if pid is not None else None
    try:
        return bool(agent_watch.hay_agentes_ocupados(terminal_ids=tids))
    except Exception:
        return False


# ─── Discord Rich Presence ("Jugando Jarvis") ────────────────────────────────
# La tarjeta la ARMA este backend (GET /api/system/presence: details/state ya
# formateados, bilingüe ES/EN + conteo de agentes) y la EMPUJA al IPC de
# Discord el lanzador Jarvis.exe (scripts/jarvis-shell.cs — el pipe
# discord-ipc-N vive en Windows y WSL no lo alcanza). La sección visible la
# reporta el frontend (frontend/shared/presence.js, POST /presence/state).

_PRESENCE_SECCIONES = {
    'home':        ('Inicio',      'Home'),
    'terminals':   ('Terminales',  'Terminals'),
    'preview':     ('Vista Web',   'Web Preview'),
    'jarvis':      ('Jarvis',      'Jarvis'),
    'editor':      ('Editor',      'Editor'),
    'tasks':       ('Tareas',      'Tasks'),
    'review':      ('Revisión',    'Review'),
    'mobile':      ('Móvil',       'Mobile'),
    'settings':    ('Ajustes',     'Settings'),
}

# Último estado que reportó el frontend (in-memory; se pierde al reiniciar y el
# frontend lo re-reporta en el primer tick). Sin persistencia a propósito.
_PRESENCE = {'seccion': None, 'locale': 'es', 'project_id': None, 'proyecto': None}

# Keys de los art assets subidos al Discord Developer Portal (son
# deployment-specific → env-overridable). El usuario subió el icono con la key
# 'plotspace-icon-1024'; si lo renombra, alcanza con la env, sin tocar código.
_PRESENCE_ICONO = os.environ.get('DISCORD_PRESENCE_ICON', 'plotspace-icon-1024')
# Punto de estado (mini-icono sobre el icono): OPCIONAL, apagado por ahora
# (pedido del usuario) → small_image vacío = Discord no lo muestra; el estado
# igual va en la línea 2. Para prenderlo: env DISCORD_PRESENCE_DOT_ON=dot_verde
# y DISCORD_PRESENCE_DOT_OFF=dot_gris (o volver a poner esos defaults).
_PRESENCE_DOT_ON = os.environ.get('DISCORD_PRESENCE_DOT_ON', '')
_PRESENCE_DOT_OFF = os.environ.get('DISCORD_PRESENCE_DOT_OFF', '')


def formatear_seccion(seccion, locale) -> str:
    """Línea 1 de la tarjeta: 'In Settings' / 'En Ajustes'. Sección desconocida
    (o None, ej. recién booteado) cae a Jarvis."""
    es, en = _PRESENCE_SECCIONES.get(seccion, ('Jarvis', 'Jarvis'))
    return f'In {en}' if locale == 'en' else f'En {es}'


def formatear_estado(activos, trabajando, max_t, locale) -> str:
    """Línea 2: conteo de agentes, ej. '3 agents · 1 working (3 of 12)'. Sin
    agentes → texto de reposo. La cláusula '· N working' se omite si nadie
    trabaja (era ruido)."""
    en = (locale == 'en')
    if activos <= 0:
        return 'Idle — no agents' if en else 'Inactivo — sin agentes'
    if en:
        base = f"{activos} {'agent' if activos == 1 else 'agents'}"
        if trabajando > 0:
            base += f' · {trabajando} working'
        return f'{base} ({activos} of {max_t})'
    base = f"{activos} {'agente' if activos == 1 else 'agentes'}"
    if trabajando > 0:
        base += f' · {trabajando} trabajando'
    return f'{base} ({activos} de {max_t})'


def _max_terminales() -> int:
    # Lazy import: terminals.py ↔ orchestrator.py tienen imports circulares y
    # system.py no debe arrastrarlos al top. 12 = MAX_TERMINALES (fallback).
    try:
        from plotspace.routers.terminals import MAX_TERMINALES
        return MAX_TERMINALES
    except Exception:
        return 12


def _conteo_agentes(project_id) -> tuple:
    """(activos, trabajando) del proyecto que el usuario está mirando. 'activos'
    = terminales activas en DB; 'trabajando' = las que agent_watch ve en fase
    'trabajando' (misma señal que el brillo de la card)."""
    if project_id is None:
        return (0, 0)
    tids = _terminales_activas_de(project_id)
    trabajando = 0
    try:
        for tid in tids:
            st = agent_watch._estados.get(tid)
            if st and st.get('fase') == 'trabajando':
                trabajando += 1
    except Exception:
        pass
    return (len(tids), trabajando)


def _set_presence(payload: dict) -> None:
    """Guarda lo que reporta el frontend (POST /presence/state), normalizado."""
    seccion = payload.get('seccion')
    _PRESENCE['seccion'] = str(seccion) if seccion else None
    _PRESENCE['locale'] = 'en' if payload.get('locale') == 'en' else 'es'
    if payload.get('locale') in ('en', 'es'):
        # El idioma también lo usan los textos que nacen en el server (avisos
        # del orquestador, avisos del enjambre): core/idioma_ui.
        from plotspace.core import idioma_ui
        idioma_ui.fijar(payload['locale'])
    pid = payload.get('project_id')
    if isinstance(pid, bool):            # True==1: no dejar que un bool se cuele
        pid = None
    elif isinstance(pid, int):
        pass
    elif isinstance(pid, str) and pid.isdigit():
        pid = int(pid)
    else:
        pid = None
    _PRESENCE['project_id'] = pid
    proy = payload.get('proyecto')
    _PRESENCE['proyecto'] = str(proy)[:64] if proy else None


def _payload_presence() -> dict:
    """Contenido de la tarjeta que consume el lanzador (GET /presence)."""
    st = _PRESENCE
    locale = st.get('locale') or 'es'
    activos, trabajando = _conteo_agentes(st.get('project_id'))
    if trabajando > 0:
        small_text = f'{trabajando} working' if locale == 'en' else f'{trabajando} trabajando'
    else:
        small_text = 'Idle' if locale == 'en' else 'Inactivo'
    return {
        'app':          'Jarvis',
        'details':      formatear_seccion(st.get('seccion'), locale),
        'state':        formatear_estado(activos, trabajando, _max_terminales(), locale),
        'large_image':  _PRESENCE_ICONO,
        'large_text':   'Jarvis',
        # Punto de estado (asset OPCIONAL): verde = alguien trabaja, gris = reposo.
        # Vacío si no se subieron los assets → Discord no muestra el mini-icono.
        'small_image':  _PRESENCE_DOT_ON if trabajando > 0 else _PRESENCE_DOT_OFF,
        'small_text':   small_text,
        'agentes_activos':    activos,
        'agentes_trabajando': trabajando,
        'max':          _max_terminales(),
        'proyecto':     st.get('proyecto'),
        'locale':       locale,
    }


# ─── Canary de arranque (gate del restart) ───────────────────────────────────

def _canary_import(modulo: str = 'plotspace.main') -> tuple:
    """(ok, error). Importa el módulo en un subproceso limpio ANTES del re-exec:
    si el código nuevo ni siquiera importa (SyntaxError, import roto, dep que
    falta), el restart se rechaza y el server viejo sigue vivo — el botón
    Actualizar nunca puede dejar al usuario con el server muerto."""
    try:
        r = subprocess.run(
            [sys.executable, '-c', f'import {modulo}'],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return (False, 'El chequeo de arranque tardó más de 120s')
    if r.returncode == 0:
        return (True, '')
    cola = (r.stderr or '').strip().splitlines()[-8:]
    return (False, '\n'.join(cola))


# ─── Reinicio (re-exec in place, sin systemd) ────────────────────────────────
#
# NO usar un relauncher detached (setsid + sesión nueva): en WSL, cerrar la
# última consola apaga el distro entero a los pocos segundos, matando al server
# huérfano Y al tmux server (todas las conversaciones de los agentes). os.execv
# reemplaza ESTE proceso por el uvicorn nuevo conservando PID, sesión y terminal:
# el server sigue alojado en la terminal donde el usuario lo levantó.

def _comando_uvicorn(python=None, host='0.0.0.0', port='3000'):
    """argv para levantar el server. `--loop asyncio` FUERZA el event loop puro
    de Python en lugar de uvloop (el default de uvicorn cuando uvloop está
    instalado).

    Por qué: en este entorno (WSL2 + Python 3.14 + uvloop 0.22) uvloop sufre un
    bloqueo PERIÓDICO del event loop de ~400ms cada ~1.45s — medido contra el
    eco de tipeo de una terminal: con uvloop p95=405ms (el "va fluido 9 letras,
    corta, sigue" que reportaba el usuario); con `--loop asyncio` el MISMO
    workload (pollers + PTY + WS) da p95=3ms, p99=8ms, sin picos. El stall NO es
    de los pollers ni del GC (todos descartados midiendo): es de uvloop. asyncio
    puro es marginalmente más lento en throughput crudo pero acá no es el cuello
    de botella (el cuello es tmux/PTY), y elimina el stall por completo."""
    return [
        python or sys.executable, '-m', 'uvicorn', 'plotspace.main:app',
        '--host', host, '--port', port, '--loop', 'asyncio',
    ]


def nombre_event_loop(loop=None) -> str:
    """'uvloop' o 'asyncio' según el event loop ACTIVO.

    uvloop (el default de uvicorn cuando está instalado — lo trae
    uvicorn[standard]) sufre el stall periódico descrito en _comando_uvicorn.
    Si el server arrancó SIN `--loop asyncio`, queda en uvloop y el tipeo se
    corta cada tanto, EN SILENCIO. Este detector alimenta el aviso de arranque
    (main._startup) y el campo de /version → el footgun deja de ser invisible.

    Detecta por el módulo de la clase del loop (uvloop.Loop vive en 'uvloop').
    `loop=None` mira el loop corriendo; sin loop corriendo devuelve el default
    seguro 'asyncio' (jamás alarma de más por no poder medir)."""
    try:
        if loop is None:
            loop = asyncio.get_running_loop()
        return 'uvloop' if 'uvloop' in (type(loop).__module__ or '') else 'asyncio'
    except Exception:
        return 'asyncio'


def loop_degradado(nombre: str = None) -> bool:
    """¿El event loop activo es el que corta el tipeo (uvloop)?"""
    return (nombre if nombre is not None else nombre_event_loop()) == 'uvloop'


def _reexec():
    """Reemplaza el proceso actual por un uvicorn fresco (no retorna). Python
    marca todos los fds CLOEXEC (PEP 446), así que el socket :3000 se libera
    en el exec y el server nuevo puede bindearlo sin carrera.

    Antes del exec, cierre ordenado de los HIJOS (clientes `tmux -C` del motor
    control y attaches PTY del classic): el PID sobrevive al execv, así que
    todo hijo vivo quedaba zombi colgando del server nuevo, que no los conoce
    ni los reapea (auditoría 2026-07-02: 144 defunct acumulados). Todo es
    best-effort: un fallo acá JAMÁS puede frenar el restart."""
    try:
        from plotspace.core import control_mode as _cm
        _cerrar = getattr(_cm, 'cerrar_clientes_para_reexec', None)
        if _cerrar:
            _cerrar()
    except Exception:
        pass
    try:
        from plotspace.routers import terminals as _t
        for _info in list(getattr(_t, 'terminal_processes', {}).values()):
            try:
                _p = _info.get('process') if isinstance(_info, dict) else _info
                if _p is not None:
                    _p.terminate()
            except Exception:
                pass
    except Exception:
        pass
    try:
        from plotspace.routers import voice as _voice
        _voice.matar_worker_stt_para_reexec()
    except Exception:
        pass
    # El libro de provenance del enjambre vive en memoria y el execv se la lleva
    # entera: sin este volcado, actualizar apagaba TODOS los íconos de vínculo de
    # las cards y el overlay se quedaba sin grupo, aunque los agentes siguieran
    # trabajando sobre los mismos archivos. Best-effort como todo lo de acá.
    try:
        from plotspace.core import provenance as _prov
        _prov.guardar_snapshot()
        from plotspace.core import territorio as _terr
        _terr.guardar_snapshot()
    except Exception:
        pass
    os.chdir(_REPO_ROOT)
    os.execv(sys.executable, _comando_uvicorn())


def reiniciar_servidor(retraso: float = 1.0):
    """Re-exec diferido en un hilo: el retraso deja que la respuesta HTTP del
    endpoint llegue al browser antes de que el proceso se reemplace."""
    def _demorado():
        time.sleep(retraso)
        try:
            _reexec()
        except Exception as e:   # si el exec falla, el server viejo sigue vivo
            print(f'[system] re-exec falló: {e}')
    threading.Thread(target=_demorado, daemon=True, name='jarvis-reexec').start()


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/metrics")
def metrics():
    """Snapshot de runtime del swarm para observabilidad/diagnóstico (read-only).
    `def` (no async) → FastAPI lo threadpolea: la DB sync no bloquea el event loop.
    Devuelve: uptime, terminales activas, últimos task_events,
    uso acumulado del orquestador, y los eventos estructurados recientes del log."""
    from plotspace.core import logs as _logs
    snap = {'uptime_s': round(time.time() - _ARRANQUE, 1)}
    try:
        conn = get_db()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS n FROM terminals WHERE activa = 1")
            snap['terminales_activas'] = cur.fetchone()['n']
            cur.execute("SELECT terminal_id, project_id, event, timestamp "
                        "FROM task_events ORDER BY id DESC LIMIT 10")
            snap['task_events_recientes'] = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT COALESCE(SUM(input_tokens),0) AS i, COALESCE(SUM(output_tokens),0) AS o, "
                        "COALESCE(SUM(llamadas),0) AS ll FROM orquestador_uso")
            u = cur.fetchone()
            snap['orquestador_uso'] = {'input_tokens': u['i'], 'output_tokens': u['o'], 'llamadas': u['ll']}
        finally:
            conn.close()
    except Exception as e:
        snap['db_error'] = str(e)
    # Salud de los pollers heurísticos (¿cuántos agentes ve trabajando/esperando ahora?)
    try:
        snap['agentes_estado'] = {k: v.get('fase') for k, v in agent_watch._estados.items()}
    except Exception:
        snap['agentes_estado'] = {}
    snap['log_reciente'] = _logs.leer_recientes(20)
    return snap


@router.get("/version")
async def version():
    """¿Hay código nuevo desde que arrancó el server? + qué cambió.

    El cuerpo corre ENTERO en to_thread: el trabajo git (hasta 22.9s medidos
    bajo carga) y las consultas de DB no pueden pisar el event loop — todas las
    terminales lo comparten y un poll de /version congelaba el eco de TODAS.
    Solo nombre_event_loop() se toma acá (necesita el loop corriendo)."""
    _loop = nombre_event_loop()
    return await asyncio.to_thread(_payload_version, _loop)


def _payload_version(_loop: str):
    corriendo  = _VERSION_BOOT
    disponible = leer_version_disco()
    # Salud del event loop: si quedó en uvloop, el tipeo se corta cada tanto y el
    # frontend ofrece reiniciar para optimizar (ver nombre_event_loop).
    _loop_mal = (_loop == 'uvloop')

    git, proxima = _snapshot_git()
    if git is not None:
        hay, titulo, novedades = git
        return {
            'corriendo':  corriendo,
            'disponible': disponible,
            'proxima':    proxima,
            'hay_update': hay,
            'agentes_trabajando': _agentes_trabajando(),
            'titulo':     titulo or f"Versión {disponible} disponible",
            'novedades':  novedades,
            'event_loop': _loop,
            'loop_degradado': _loop_mal,
        }

    # Fallback sin git: comparar el archivo VERSION (modo viejo). Las novedades
    # salen del CHANGELOG (un solo grupo "✨ Novedades", misma forma que el modal).
    hay = _semver_mayor(disponible, corriendo)
    items = [it for sec in _novedades_entre(_leer_changelog(), corriendo, disponible)
             for it in sec['items']]
    novedades = [{'area': 'Novedades', 'icon': '✨', 'items': items}] if items else []
    return {
        'corriendo':  corriendo,
        'disponible': disponible,
        'proxima':    disponible,   # sin git, la versión la maneja el archivo VERSION
        'hay_update': hay,
        'agentes_trabajando': _agentes_trabajando(),
        'titulo':     _TITULO,
        'novedades':  novedades,
        'event_loop': _loop,
        'loop_degradado': _loop_mal,
    }


# ─── Readiness: gate del reload post-update ──────────────────────────────────
# Tras aplicar un update el server hace os.execv (proceso nuevo, boot_id nuevo) y
# el browser recarga. Pero "el server volvió" (acepta conexiones / manda boot_id
# nuevo) NO es lo mismo que "las terminales ya se pueden re-enganchar": la
# reconciliación de sesiones tmux corre como bg task que NO gatea el arranque
# (main._startup). Si el browser recarga apenas el proceso levanta, la página
# fresca re-attachea las N terminales contra un tmux todavía reconciliándose (+
# resaca del canary + CPU saturada) → sus seeds (capture-pane) llegan vacíos o
# tarde → cards NEGRAS y el scroll queda muerto (el seed no restaura alt-screen +
# mouse-tracking a tiempo). Este gate hace que el frontend espere a que
# reconcile_listo esté seteado ANTES de recargar. Ver terminals.reconcile_listo.

def _server_listo() -> bool:
    """¿El server terminó de reconciliar las sesiones tmux tras arrancar?
    reconcile_listo se setea en el finally de reconciliar_sesiones_tmux()
    (terminals.py) cuando el trickle de relanzos pasó el pico. Fail-open: si no se
    puede leer, True — mejor recargar que trabar la página esperando un semáforo
    inaccesible. Lazy import (system → terminals), patrón orchestrator↔terminals."""
    try:
        from plotspace.routers.terminals import reconcile_listo
        return bool(reconcile_listo.is_set())
    except Exception:
        return True


@router.get("/ready")
def ready():
    """200 con {ready} = ¿el server ya reconcilió las sesiones tmux? El updater y
    el reload por boot_id esperan ready=true ANTES de recargar (ver _server_listo).
    Abierto (RUTAS_ABIERTAS, como /health): el waiter tiene que andar durante el
    reinicio, aunque la cookie no esté fresca. Sin secreto: solo un booleano."""
    return {'ready': _server_listo()}


@router.post("/presence/state")
async def presence_state(payload: dict):
    """El frontend reporta en qué sección está + idioma + proyecto actual
    (in-memory). Lo consume Jarvis.exe para la Rich Presence de Discord.
    Token-gated (vía /api/). Nunca falla: cualquier payload raro se normaliza."""
    _set_presence(payload or {})
    return {'ok': True}


@router.get("/presence")
async def presence():
    """Contenido de la tarjeta 'Jugando Jarvis' de Discord. Lo poletea Jarvis.exe
    cada ~15s y lo empuja al IPC de Discord (el pipe vive en Windows). El conteo
    de agentes toca DB → to_thread para no pisar el event loop de las terminales."""
    return await asyncio.to_thread(_payload_presence)


@router.post("/diag-update")
async def diag_update(payload: dict):
    """Recibe el informe del diagnóstico post-update del browser (ver
    frontend/shell/diag-update.js — deep work del freeze de scroll 2026-07-11)
    y lo appendea a data/diag_update.jsonl con el timestamp del server, para
    alinear la línea de tiempo del cliente con la del boot (diag_boot.jsonl).
    Best-effort: una línea JSON por update aplicado."""
    import json as _json
    from plotspace.core.datadir import ruta_data
    try:
        path = ruta_data('diag_update.jsonl')
        # Rotación bruta: no acumular sin techo (cada informe pesa ~50-100KB).
        try:
            if os.path.getsize(path) > 5 * 1024 * 1024:
                os.replace(path, path + '.1')
        except OSError:
            pass
        linea = _json.dumps({'server_ts': time.time(), **(payload or {})},
                            ensure_ascii=False)

        def _append():
            with open(path, 'a', encoding='utf-8') as f:
                f.write(linea + '\n')
        await asyncio.to_thread(_append)
    except Exception as e:
        print(f'[diag-update] no pude persistir el informe: {e}')
    return {'ok': True}


@router.post("/restart")
def restart():
    """Aplica la actualización: canary de arranque → bump de versión → re-exec.
    Token-gated (vía /api/). Sync a propósito: el canary es un subprocess.run
    que puede tardar varios segundos y FastAPI lo corre en el threadpool.
    Si el canary falla, NO se reinicia ni se bumpea: 409 con el error."""
    ok, err = _canary_import()
    if not ok:
        # Audit trail: antes el traceback viajaba solo en el body del 409 y se
        # perdía si nadie lo miraba — ahora queda como señal de fricción.
        from plotspace.core import logs as _logs
        _logs.evento('canary_fallo', nivel='error', detalle=(err or '')[:2000])
        return JSONResponse(status_code=409, content={
            'ok': False,
            'error': 'El código nuevo no arranca — la actualización no se aplicó.',
            'detalle': err,
        })
    git = _estado_git()
    nueva = _VERSION_BOOT
    if git is not None and git[0]:          # hay update real → la versión avanza
        nueva = _version_proxima(True)
        _escribir_version(nueva)
    reiniciar_servidor()
    return {'ok': True, 'version': nueva}
