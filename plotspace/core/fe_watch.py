# plotspace/core/fe_watch.py
"""FE Watch — recarga del browser SOLO por caminos autorizados.

Dos señales, un mismo destino (location.reload() en workspace.js):

  1. BOOT_ID: uuid de este proceso. El WS de eventos lo manda como primer
     mensaje ({type:'hola', boot_id}) al conectar. El cliente guarda el
     primero que ve; si tras una reconexión llega otro distinto, el server
     se reinició → recarga. Eso también entierra los "failed to fetch"
     durante reinicios: apenas vuelve el server, la página renace fresca.

  2. Poller (patrón dev_detect, cada 2s): mira si el HEAD del repo se movió
     (= apareció un COMMIT nuevo, una tarea TERMINADA) y emite
     `codigo_commiteado` → el cliente re-chequea el banner "Actualizar ahora".
     Ese botón es el ÚNICO disparador de reload por código nuevo.

  3. AUTO-PUSH (2026-07-08): el server es el DUEÑO del push a GitHub. Los
     agentes commitean local y por política NO pushean, y todo el update del
     dashboard es local (HEAD) — el push mantiene el backup en origin sin que
     nadie se acuerde. Ese eslabón quedaba sin dueño: se juntaban commits
     (9 el día que se detectó). Ahora, al detectar HEAD nuevo (y una vez al boot,
     para drenar backlog) se hace `git push origin main` en el executor.
     Nunca --force; el pre-push hook (scan de secretos) sigue de red; si falla
     (sin red, hook), backoff de PUSH_BACKOFF_S y reintento. AUTO_PUSH=off lo
     apaga.

Historia (2026-07-03): el poller también escaneaba mtimes de frontend/** y
emitía `frontend_actualizado` para recargar solo ante ediciones SIN commit.
Se sacó: parpadeaba el workspace (reload → re-attach de TODAS las terminales,
~5s) cada vez que el enjambre editaba el frontend y caía a idle un instante
(el gate por 'agente trabajando' es permeable: un agente pensando/esperando la
API queda idle). El usuario quiere que el reload lo autorice SOLO él, con el
banner "Actualizar ahora" (basado en commits). El escaneo de FS y su máquina
de quietud (firma_frontend/evaluar_cambio, gate por agentes) viven en git si
alguna vez se revierte la política. Ver memoria [[no-pedir-f5-fe-watch]].
"""

import asyncio
import os
import time
import uuid

from plotspace.core.events import broadcaster

INTERVALO_S = 2
# Un commit nuevo es un evento raro: chequear el HEAD (fork+exec de git) cada 2s
# todo el día es desperdicio. Lo miramos 1 de cada N ticks (~10s); la detección
# del banner tolera de sobra ese retardo y dejamos de competir por CPU/FDs.
TICKS_POR_HEAD = 5

BOOT_ID = uuid.uuid4().hex

_FRONTEND = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', 'frontend')


# ─── Lógica pura (testeable sin asyncio ni broadcaster) ───────────────────────

def cambio_de_commit(estado: dict, head: str) -> bool:
    """True si el HEAD del repo cambió desde el último scan = apareció un COMMIT
    NUEVO (una tarea TERMINADA por algún agente). estado = {'head': str|None}.
    El primer scan es baseline (el commit con el que arrancó el server no avisa);
    head vacío (no es repo git / git falló) no dispara. NO se gatea por agentes:
    un commit es discreto y terminado, y el banner "Actualizar ahora" (que se basa
    en commits) debe re-chequearse YA, aunque otra terminal siga trabajando."""
    if not head:
        return False
    if estado.get('head') is None:
        estado['head'] = head
        return False
    if head != estado['head']:
        estado['head'] = head
        return True
    return False


PUSH_BACKOFF_S = 300   # tras un push fallido (sin red, hook), esperar 5 min


def auto_push_habilitado() -> bool:
    """AUTO_PUSH=on|1|yes|true lo prende; default OFF en clones ajenos."""
    return os.getenv('AUTO_PUSH', 'off').strip().lower() in ('on', '1', 'yes', 'true')


def debe_pushear(estado: dict, head: str, ahora: float, habilitado: bool = True) -> bool:
    """¿Corresponde intentar `git push`? PURA. estado = {'pusheado': sha|None,
    'fallo_hasta': float}. pusheado=None (boot) con un HEAD válido → push
    baseline que drena el backlog acumulado con el server apagado. No re-pushea
    el mismo HEAD y respeta el backoff de un fallo previo."""
    if not habilitado or not head:
        return False
    if ahora < estado.get('fallo_hasta', 0.0):
        return False
    return head != estado.get('pusheado')


def registrar_push(estado: dict, head: str, ok: bool, ahora: float,
                   backoff: float = PUSH_BACKOFF_S) -> None:
    """Anota el resultado: éxito registra el HEAD (no repetir) y limpia el
    backoff; fallo arma la ventana de espera SIN marcar el HEAD (reintenta)."""
    if ok:
        estado['pusheado'] = head
        estado['fallo_hasta'] = 0.0
    else:
        estado['fallo_hasta'] = ahora + backoff


def _push_origen() -> bool:
    """`git push origin main` síncrono — invocar vía run_in_executor (la red
    puede tardar y NO debe frenar el event loop). Jamás --force: en este flujo
    el repo local es el único escritor de origin/main, así que siempre es
    fast-forward; cualquier fallo (red, pre-push hook de secretos, divergencia
    exótica) se loguea y el backoff reintenta después."""
    import subprocess
    try:
        # env no interactivo: sin credenciales, git PREGUNTA — y en Windows
        # pregunta con una ventana. Un poller de fondo no puede plantarle un
        # diálogo de login al usuario, ni colgarse 120s esperando input.
        from plotspace.core.gitutil import entorno_no_interactivo
        r = subprocess.run(['git', '-C', _FRONTEND, 'push', 'origin', 'main'],
                           capture_output=True, text=True, timeout=120,
                           env=entorno_no_interactivo())
        if r.returncode == 0:
            return True
        print(f"[fe-watch] auto-push falló: {(r.stderr or r.stdout).strip()[-400:]}")
        return False
    except Exception as e:
        print(f'[fe-watch] auto-push error: {e}')
        return False


def invalidar_cache_version():
    """El commit nuevo tiene que VERSE ya en /version: tira el snapshot git
    cacheado (TTL 30s) de routers/system. Se llama ANTES del broadcast — si el
    cache era de antes del commit, el re-chequeo del banner veía
    hay_update=false y el banner recién aparecía en el poll de 120s (minutos
    de espera). Lazy import (core → routers), patrón orchestrator↔terminals;
    best-effort: un fallo acá jamás frena el aviso."""
    try:
        from plotspace.routers.system import invalidar_cache_git
        invalidar_cache_git()
    except Exception:
        pass


# ─── Poller ───────────────────────────────────────────────────────────────────

def _head_repo() -> str:
    """HEAD actual del repo (commit hash) o '' si no es git / git falla.
    Subprocess corto — invocar vía run_in_executor para no frenar el event loop."""
    import subprocess
    try:
        r = subprocess.run(['git', '-C', _FRONTEND, 'rev-parse', 'HEAD'],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ''
    except Exception:
        return ''


async def poller_frontend():
    """Background task: nunca crashea el servidor (patrón STATE.md).

    Único trabajo: detectar commits nuevos (HEAD se movió) para re-chequear el
    banner "Actualizar ahora". Ya NO escanea mtimes ni recarga por ediciones sin
    commit (ver docstring del módulo)."""
    estado_head = {'head': None}
    estado_push = {'pusheado': None, 'fallo_hasta': 0.0}
    loop = asyncio.get_event_loop()
    tick = 0
    while True:
        await asyncio.sleep(INTERVALO_S)
        tick += 1
        try:
            # Commit nuevo (HEAD se movió) = una tarea TERMINADA → re-chequear el
            # banner "Actualizar ahora" YA, sin esperar el poll de 120s ni que el
            # agente quede idle. SIN gate de agentes a propósito: el banner se
            # basa en commits, así que se muestra aunque otra terminal siga laburando.
            # Solo 1 de cada TICKS_POR_HEAD ticks: el primero (tick=1) fija el
            # baseline y de ahí en más ~cada 10s, en vez de un git por tick.
            if tick % TICKS_POR_HEAD == 1:
                head = await loop.run_in_executor(None, _head_repo)
                if cambio_de_commit(estado_head, head):
                    # Tirar el cache de /version ANTES de avisar: el re-chequeo
                    # del banner debe ver el commit YA, no el snapshot de 30s.
                    invalidar_cache_version()
                    print('[fe-watch] commit nuevo → broadcast codigo_commiteado')
                    await broadcaster.broadcast_global({'type': 'codigo_commiteado'})
                # Auto-push: mismo tick que el chequeo de HEAD. El primer tick
                # (pusheado=None) drena el backlog; después solo ante HEAD nuevo.
                if debe_pushear(estado_push, head, time.monotonic(), auto_push_habilitado()):
                    ok = await loop.run_in_executor(None, _push_origen)
                    registrar_push(estado_push, head, ok, time.monotonic())
                    if ok:
                        print(f'[fe-watch] auto-push OK → origin/main en {head[:9]}')
        except Exception as e:
            print(f'[fe-watch] Error en ciclo: {e}')
