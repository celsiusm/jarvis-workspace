import asyncio
import os
import sys
from contextlib import asynccontextmanager

# ANTES de cualquier print del arranque: la consola de Windows usa cp1252 y un
# solo carácter fuera de ASCII (el banner, una tilde) levanta UnicodeEncodeError
# DENTRO del lifespan de FastAPI — que aborta el arranque entero. Ver
# core/consola.py.
from plotspace.core.consola import asegurar_salida_estandar

asegurar_salida_estandar()

from datetime import datetime

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Cargar variables de entorno desde plotspace/.env antes de cualquier import que las use
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(_env_path)

# Asegurar que el directorio raíz esté en el path para imports absolutos
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import WebSocket, WebSocketDisconnect

from plotspace.core.database import init_db, get_db
from plotspace.core.events import broadcaster
from plotspace.routers import browser, cuentas, fs, live, memory, mobile_preview, radio, review, orchestrator, plugins, projects, projects_files, system, tasks, terminals, voice, workspace

# Referencias a los background tasks del startup: asyncio solo guarda weakrefs
# a las tasks, así que sin esto el GC podía recogerlas a mitad de ejecución.
_state_task = None
_dev_detect_task = None
_bg_tasks: list = []


@asynccontextmanager
async def lifespan(app):
    """Ciclo de vida del server: startup → yield → shutdown. Reemplaza los
    @app.on_event('startup'/'shutdown') deprecados de FastAPI."""
    await _startup()
    yield
    await _shutdown()


app = FastAPI(
    title="JARVIS",
    description="Dashboard local para orquestar agentes de IA",
    version=system.leer_version_disco(),   # fuente única: archivo VERSION
    lifespan=lifespan,
)

# CORS: la app es same-origin (el frontend lo sirve el mismo server), así que
# CORS solo hace falta para tolerar requests del propio origen. Con `*` +
# credentials, Starlette REFLEJA el Origin del atacante → una web maliciosa
# podía leer respuestas autenticadas. Restringido a localhost/127.0.0.1/IP
# (incluye la IP de LAN del celular); los dominios externos quedan afuera.
# El regex viejo `(?:\d{1,3}\.){3}\d{1,3}` matcheaba CUALQUIER IPv4 —incluidas
# las públicas— así que una web servida desde una IP pública cruda quedaba
# reflejada con credentials. Acotado a loopback + LAN privada (RFC1918), que
# es el único acceso legítimo (localhost o la IP de LAN del celular).
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=(
        r"^https?://("
        r"localhost|127\.0\.0\.1|\[::1\]"
        r"|10\.(?:\d{1,3}\.){2}\d{1,3}"
        r"|192\.168\.\d{1,3}\.\d{1,3}"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
        r")(?::\d+)?$"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()


# ─── Git helper (para STATE.md y limpieza de worktrees) ───────────────────────

async def _run_git(cwd: str, *args: str) -> tuple:
    """Ejecuta 'git <args>' en cwd. Devuelve (returncode, salida)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            'git', *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        return proc.returncode, (out + err).decode(errors='replace')
    except Exception as e:
        return -1, str(e)



# ─── Background task: STATE.md cada 10 segundos ───────────────────────────────

async def _tarea_state_md():
    """Genera STATE.md para cada proyecto con terminales activas. Nunca crashea el servidor."""
    while True:
        await asyncio.sleep(10)
        try:
            await _actualizar_state_md()
        except Exception as e:
            print(f'[STATE.md] Error en ciclo: {e}')


def _state_md_query():
    """Query SYNC de SQLite (conexión nueva → thread-safe). Se corre en un thread
    para NO bloquear el event loop en el ciclo de 10s (audit latencia)."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT p.id, p.nombre, p.ruta,
                   t.id AS tid, t.nombre AS tnombre, t.tipo_ia, t.fecha_creacion
            FROM projects p
            JOIN terminals t ON t.project_id = p.id
            WHERE t.activa = 1
        ''')
        return cursor.fetchall()
    finally:
        conn.close()


async def _actualizar_state_md():
    rows = await asyncio.to_thread(_state_md_query)

    # Agrupar por proyecto
    proyectos: dict = {}
    for row in rows:
        pid = row['id']
        if pid not in proyectos:
            proyectos[pid] = {
                'nombre': row['nombre'],
                'ruta':   row['ruta'],
                'terminales': [],
            }
        proyectos[pid]['terminales'].append({
            'id':             row['tid'],
            'nombre':         row['tnombre'],
            'tipo_ia':        row['tipo_ia'],
            'fecha_creacion': row['fecha_creacion'],
        })

    for proyecto in proyectos.values():
        try:
            await _escribir_state_md(proyecto)
        except Exception as e:
            print(f'[STATE.md] Error escribiendo para {proyecto["nombre"]}: {e}')


async def _escribir_state_md(proyecto: dict):
    ruta = proyecto['ruta']
    if not os.path.isdir(ruta):
        return

    workspace_dir = os.path.join(ruta, '.workspace')
    os.makedirs(workspace_dir, exist_ok=True)

    ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    lineas = [
        f'# Estado del workspace — {ahora}\n\n',
        f'**Proyecto:** {proyecto["nombre"]}  \n',
        f'**Ruta:** `{ruta}`\n\n',
        '## Agentes activos\n\n',
        '| ID | Nombre | IA | Desde |\n',
        '|----|--------|----|-------|\n',
    ]

    for t in proyecto['terminales']:
        try:
            dt    = datetime.fromisoformat(t['fecha_creacion'])
            desde = dt.strftime('%H:%M')
        except Exception:
            desde = '—'

        lineas.append(f'| {t["id"]} | {t["nombre"]} | {t["tipo_ia"]} | {desde} |\n')

    lineas.append('\n')
    lineas.append('## Archivos modificados\n\n')

    # Todos los agentes trabajan en main: un solo git status compartido.
    rc, out = await _run_git(ruta, 'status', '--porcelain')
    if rc == 0 and out.strip():
        archivos = [l.strip() for l in out.strip().splitlines() if l.strip()][:30]
        for archivo in archivos:
            lineas.append(f'- `{archivo}`\n')
        lineas.append('\n')
    else:
        lineas.append('_Sin cambios pendientes._\n\n')

    # Write atómico: a un .tmp en el MISMO dir y os.replace al destino. Si el disco
    # se llena a mitad de la escritura, el STATE.md viejo queda intacto (los agentes
    # lo leen para coordinarse: nunca debe quedar truncado a medias).
    state_path = os.path.join(workspace_dir, 'STATE.md')

    def _escribir_atomico():
        tmp_path = state_path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.writelines(lineas)
        os.replace(tmp_path, state_path)

    await asyncio.to_thread(_escribir_atomico)   # I/O de disco fuera del único event loop


# ─── Startup ──────────────────────────────────────────────────────────────────

async def _startup():
    global _state_task, _dev_detect_task

    # 0. Executor default más amplio. El default de Python es min(32, CPUs+4)
    #    — con 4 CPUs son 8 threads, que se agotan rápido entre tareas
    #    bloqueantes concurrentes (subprocess, archivos, etc.) y dejan al
    #    resto en cola (input lag). 32 da margen sin costo apreciable.
    from concurrent.futures import ThreadPoolExecutor
    asyncio.get_event_loop().set_default_executor(
        ThreadPoolExecutor(max_workers=32, thread_name_prefix='jarvis')
    )

    jarvis_auth.imprimir_banner()

    # 0.6 Avisar temprano si falta la API key: sin esto, el primer chat al
    #     orquestador fallaba con un 500 confuso desconectado de la causa.
    if not os.environ.get('ANTHROPIC_API_KEY'):
        print('[startup] ⚠️  ANTHROPIC_API_KEY no está seteada (plotspace/.env). '
              'El orquestador y el Web Builder fallarán hasta configurarla.')

    # 0.7 Salud del event loop. Si el server arrancó SIN `--loop asyncio` quedó en
    #     uvloop, que en este entorno (WSL2 + Py3.14) traba el loop ~0.4-1s de
    #     forma periódica bajo el workload subprocess/PTY → corta el eco de TODAS
    #     las terminales a la vez. Antes era SILENCIOSO; ahora se grita acá y se
    #     expone en /version (el frontend ofrece reiniciar). Ver
    #     system.nombre_event_loop / _comando_uvicorn.
    _loop = system.nombre_event_loop()
    if _loop == 'uvloop':
        print('[startup] ⚠️  EVENT LOOP = uvloop — el tipeo de las terminales se '
              'cortará ~0.4-1s cada tanto. Reiniciá con `python -m backend` (o '
              '`--loop asyncio`, o el botón "Actualizar ahora" de la UI).')
    else:
        print(f'[startup] event loop = {_loop} ✓ (sin stall)')

    # 0.8 Provenance del enjambre: repoblar el libro de ediciones desde el
    #     snapshot. El "Actualizar ahora" reemplaza el proceso (os.execv) y la
    #     memoria se va entera — sin esto, cada update apagaba de golpe TODOS los
    #     íconos de vínculo de las cards y dejaba al overlay sin nada que
    #     mostrar, con los agentes todavía escribiendo los mismos archivos.
    try:
        from plotspace.core import provenance as _prov
        _n = _prov.cargar_snapshot()
        if _n:
            print(f'[startup] provenance: {_n} ediciones recuperadas del snapshot')
        from plotspace.core import territorio as _terr
        _nt = _terr.cargar_snapshot()
        if _nt:
            print(f'[startup] territorio: {_nt} reclamos recuperados del snapshot')
    except Exception as e:
        print(f'[startup] provenance/territorio: snapshot no recuperado ({e})')

    # 1. Pre-cargar modelo Whisper en su hilo dedicado (el mismo donde se
    #    ejecutarán las transcripciones; el modelo no es thread-safe). NO se
    #    espera (antes bloqueaba el startup ~30-40s sin servir ni un request):
    #    el executor es de un solo hilo, así que la primera transcripción se
    #    encola DETRÁS del preload igual → la thread-safety se mantiene.
    #    DIFERIDA: la precarga (464MB, ~1 core, sube el RSS a ~3GB) competía por
    #    CPU/RAM con el arranque simultáneo de los CLIs justo al abrir el
    #    workspace. Ahora espera a que reconciliar termine el trickle de CLIs
    #    (o un tope de 90s), así el pico de las terminales tiene la CPU libre.
    #    Si dictás voz antes, transcribir() la carga on-demand igual.
    async def _precargar_whisper():
        try:
            await asyncio.wait_for(terminals.reconcile_listo.wait(), timeout=90)
        except (asyncio.TimeoutError, Exception):
            pass
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(voice._whisper_executor, voice._cargar_whisper)
        except Exception as e:
            print(f'[startup] Whisper preload falló: {e}')
    # Default SIN precarga: el modelo es RSS anónimo del uvicorn (small ≈ 1 GB,
    # turbo ≈ 3,2 GB) y Whisper es solo el FALLBACK del SR del browser — tenerlo
    # residente desde el boot era el 50% del "VmmemWSL gigante con cero
    # terminales". Carga on-demand (el PTT manda /prewarm al apretar) y el
    # vigilante lo descarga tras WHISPER_IDLE_UNLOAD s de ocio.
    if os.getenv('WHISPER_PRELOAD', 'off').strip().lower() in ('on', '1', 'true', 'yes'):
        _bg_tasks.append(asyncio.create_task(_precargar_whisper()))
    else:
        print('[startup] Whisper on-demand (WHISPER_PRELOAD=off): carga al primer '
              'dictado y se descarga tras ocio')
    _bg_tasks.append(asyncio.create_task(voice.vigilar_ocio_whisper()))

    # 2. Reconciliar sesiones tmux en background — no bloquea el startup
    _bg_tasks.append(asyncio.create_task(terminals.reconciliar_sesiones_tmux()))

    # 2b. Instalar el SessionStart hook de claude (idempotente): captura el
    #     session-id VIVO en cada arranque para que --resume traiga el transcript
    #     actual (claude rota el .jsonl al compactar). Ver [[persistencia-resume-terminales]].
    try:
        from plotspace.core import hooks_cli as _hc
        _raiz_hook = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        _settings = os.path.join(os.path.expanduser('~'), '.claude', 'settings.json')
        if terminals.asegurar_session_hook(_settings, _hc.comando_session_hook(_raiz_hook)):
            print('[startup] SessionStart hook de claude instalado')
    except Exception as e:
        print(f'[startup] no pude instalar el session hook: {e}')

    # 2c. Instalar los hooks de PROVENANCE (idempotente): PostToolUse registra
    #     cada edición REAL y PreToolUse frena la sobrescritura destructiva. Es
    #     el reemplazo del parseo de panes, que quedó ciego cuando el CLI pasó a
    #     resúmenes colapsados sin nombre de archivo (0 ops detectadas en 4,8 MB
    #     de log). Sin esto no hay propiedad, ni guard de commits, ni conflictos.
    try:
        from plotspace.core import hooks_cli
        _raiz = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        _settings = os.path.join(os.path.expanduser('~'), '.claude', 'settings.json')
        if hooks_cli.asegurar_hooks_provenance(_settings, hooks_cli.comando_hook(_raiz)):
            print('[startup] hooks de provenance del enjambre instalados')
    except Exception as e:
        print(f'[startup] no pude instalar los hooks de provenance: {e}')

    # 2d. Adaptador de provenance de opencode (plugin JS): opencode NO dispara el
    #     hook de Claude, así que sin esto sus ediciones son invisibles para el
    #     enjambre (fantasma: sin propiedad, sin colisiones, sin jv estado).
    #     Idempotente, best-effort. Codex va aparte (tailer del rollout).
    try:
        from plotspace.core import cli_adapters
        _raiz_oc = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        if cli_adapters.asegurar_opencode_plugin(_raiz_oc):
            print('[startup] plugin de provenance de opencode instalado')
        if cli_adapters.asegurar_qwen_hook(_raiz_oc):
            print('[startup] hook de provenance de qwen instalado')
        if cli_adapters.asegurar_antigravity_hook(_raiz_oc):
            print('[startup] hook de provenance de Antigravity instalado')
    except Exception as e:
        print(f'[startup] no pude instalar los adaptadores de CLI: {e}')

    # 4. Reanudar workflows que estaban corriendo antes del reinicio
    #    (marca los zombies viejos como 'error' en vez de revivir sus monitores)
    _bg_tasks.append(asyncio.create_task(orchestrator.reanudar_workflows()))

    # 4.1 Acotar task_events (insert-only, antes crecía sin techo)
    try:
        from plotspace.core.database import purgar_task_events
        purgar_task_events()
    except Exception as e:
        print(f'[startup] purgar_task_events falló: {e}')

    # 4. Arrancar background task STATE.md
    _state_task = asyncio.create_task(_tarea_state_md())

    # 5. Mailbox entre agentes: watcher que entrega mensajes en vivo
    from plotspace.core.mailbox import vigilar_mailboxes
    _bg_tasks.append(asyncio.create_task(vigilar_mailboxes()))

    # 6. Detección de dev servers de agentes → auto-abre el Web Preview
    from plotspace.core.dev_detect import poller_dev_servers
    _dev_detect_task = asyncio.create_task(poller_dev_servers())

    # 7. Detección de "terminó / espera respuesta" de agentes → sonidos en UI.
    #    Antes del poll, RECONCILIAR el estado desde los panes vivos: tras un
    #    reload por actualización _estados queda vacío y una terminal que ya venía
    #    trabajando quedaría atrapada en 'arrancando' (nunca 'trabajando'). El
    #    reconcile la siembra correcta; recién después arranca el poller (sin
    #    carrera). No bloquea el startup (es un bg task).
    from plotspace.core.agent_watch import poller_agentes, reconciliar_al_boot

    async def _reconciliar_y_pollear_agentes():
        await reconciliar_al_boot()
        await poller_agentes()
    _bg_tasks.append(asyncio.create_task(_reconciliar_y_pollear_agentes()))

    # 8. Agents Live: tracking de archivos por agente + propiedad/permisos
    from plotspace.core.agent_live import poller_live
    _bg_tasks.append(asyncio.create_task(poller_live()))

    # 9. FE Watch: cambió el frontend / reinició el server → el browser se
    #    recarga solo (nadie vuelve a pedir F5)
    from plotspace.core.fe_watch import poller_frontend
    _bg_tasks.append(asyncio.create_task(poller_frontend()))

    # 10. Watchdog del swarm: rescata el TASK_DONE perdido y avisa pasos colgados
    #     (red de seguridad para dejar el swarm corriendo desatendido)
    from plotspace.core.swarm_watchdog import poller_watchdog
    _bg_tasks.append(asyncio.create_task(poller_watchdog()))

    # 11. Sentinel-file: cierre estructurado de pasos (determinista, multi-CLI)
    from plotspace.core.sentinel import poller_sentinel
    _bg_tasks.append(asyncio.create_task(poller_sentinel()))

    # 11b. Codex Watch: tailea el rollout JSONL de cada Codex → provenance. Codex
    #      edita con apply_patch (fuera del gate de los hooks), así que sin esto es
    #      un fantasma para el enjambre (sin propiedad, colisiones, jv estado).
    #      Apagable con CODEX_WATCH=off.
    from plotspace.core.codex_watch import poller_codex
    _bg_tasks.append(asyncio.create_task(poller_codex()))

    # 12. Janitor de logs: purga periódica de .workspace/logs (fuga de disco
    #     medida en 790MB — logs de terminales muertas que nunca se purgaban)
    from plotspace.core.mantenimiento import poller_purga_logs
    _bg_tasks.append(asyncio.create_task(poller_purga_logs()))

    # 13. Snapshot de shells: guarda el scrollback de las terminales SHELL para
    #     restaurar su historial visual tras un corte de luz (los CLIs reanudan
    #     su conversación solos; un shell no, así que se re-imprime el snapshot).
    from plotspace.core.terminal_snapshot import poller_snapshots
    _bg_tasks.append(asyncio.create_task(poller_snapshots()))

    # 14. Diagnóstico de boot (deep work del freeze de scroll post-update,
    #     2026-07-11): los primeros 40s muestrea cada 200ms el LAG del event
    #     loop (drift de un sleep corto — si el loop se clava, la rueda de las
    #     terminales se clava con él) + loadavg + CPU ocupada (/proc/stat), y
    #     escribe UNA línea JSON a data/diag_boot.jsonl al terminar. Se alinea
    #     por timestamps con el informe del browser (data/diag_update.jsonl).
    #     Costo: 40s de sleeps cortos y una escritura; nada en régimen.
    async def _diag_boot():
        import json as _json
        import time
        from plotspace.core.datadir import ruta_data
        t0 = time.time()
        muestras = []

        def _cpu():
            try:
                with open('/proc/stat') as f:
                    p = f.readline().split()[1:8]
                v = [int(x) for x in p]
                return sum(v), v[3]   # total, idle
            except Exception:
                return None, None

        tot0, idle0 = _cpu()
        try:
            while time.time() - t0 < 40:
                ini = time.perf_counter()
                await asyncio.sleep(0.2)
                lag = (time.perf_counter() - ini - 0.2) * 1000
                tot1, idle1 = _cpu()
                busy = None
                if tot0 is not None and tot1 is not None and tot1 > tot0:
                    busy = round(100 * (1 - (idle1 - idle0) / (tot1 - tot0)))
                tot0, idle0 = tot1, idle1
                try:
                    with open('/proc/loadavg') as f:
                        load = float(f.read().split()[0])
                except Exception:
                    load = None
                muestras.append({'t': round((time.time() - t0) * 1000),
                                 'lag': round(lag), 'cpu': busy, 'load': load})
            linea = _json.dumps({'boot_ts': t0, 'muestras': muestras})

            def _append():
                path = ruta_data('diag_boot.jsonl')
                try:
                    if os.path.getsize(path) > 5 * 1024 * 1024:
                        os.replace(path, path + '.1')
                except OSError:
                    pass
                with open(path, 'a', encoding='utf-8') as f:
                    f.write(linea + '\n')
            await asyncio.to_thread(_append)
        except Exception as e:
            print(f'[diag-boot] sampler cayó: {e}')
    _bg_tasks.append(asyncio.create_task(_diag_boot()))

    # 15. Cazador de STALLS del event loop (deep work 2026-07-11, 2ª vuelta):
    #     la telemetría del update real probó un bloqueo SÍNCRONO de ~4.0s del
    #     loop (lag=3980ms, CPU 28% → algo ESPERÓ un lock/IO en el thread del
    #     loop, alineado 1:1 con la ventana muda del scroll del usuario). Este
    #     latido re-arma un timer de faulthandler cada 0.5s; si el loop no late
    #     por ≥2s, el timer vence y vuelca el STACK de TODOS los threads a
    #     data/diag_stall.log — el stack del MainThread firma al culpable con
    #     archivo:línea. Siempre activo: costo = re-armar un timer C 2×/s.
    async def _cazador_stalls():
        import faulthandler
        import time
        from plotspace.core.datadir import ruta_data
        try:
            f = open(ruta_data('diag_stall.log'), 'a')
        except OSError as e:
            print(f'[diag-stall] sin log: {e}')
            return
        ultimo_tam = os.fstat(f.fileno()).st_size
        while True:
            try:
                # ¿Hubo un dump mientras estábamos clavados? Sellarlo con hora
                # (faulthandler escribe crudo al fd, sin timestamps propios).
                tam = os.fstat(f.fileno()).st_size
                if tam > ultimo_tam:
                    f.write(f'\n^^^ stall del event loop detectado — sellado '
                            f'{datetime.now().isoformat()} (epoch {time.time():.3f})\n\n')
                    f.flush()
                    ultimo_tam = os.fstat(f.fileno()).st_size
                faulthandler.dump_traceback_later(2.0, file=f)
            except Exception:
                pass
            await asyncio.sleep(0.5)
    _bg_tasks.append(asyncio.create_task(_cazador_stalls()))


async def _shutdown():
    # El libro del enjambre, a disco: un Ctrl+C o un SIGTERM no tienen por qué
    # dejar ciegos a los agentes. (El re-exec del updater NO pasa por acá —
    # execv no corre el shutdown de lifespan — así que lo guarda `_reexec()`.)
    try:
        from plotspace.core import provenance as _prov
        _prov.guardar_snapshot()
        from plotspace.core import territorio as _terr
        _terr.guardar_snapshot()
    except Exception:
        pass


# ─── Routers ──────────────────────────────────────────────────────────────────

app.include_router(live.router)
app.include_router(projects.router)
app.include_router(projects_files.router)
app.include_router(fs.router)        # /api/fs: explorador de carpetas (picker de proyecto)
app.include_router(plugins.router)
app.include_router(terminals.router)
app.include_router(workspace.router)
app.include_router(orchestrator.router)
app.include_router(voice.router)
app.include_router(mobile_preview.router)
app.include_router(tasks.router)
app.include_router(memory.router)
app.include_router(review.router)
app.include_router(system.router)   # /api/system: versión + reinicio in-app (re-exec in place)
app.include_router(cuentas.router)  # /api/cuentas: vincular/switchear cuentas de CLIs
app.include_router(radio.router)    # /api/radio: fuentes de la Radio (música local + Spotify)
app.include_router(browser.router)  # /ws/browser: Browser server-side (Chromium + screencast)

# ─── Host / Origin (anti DNS-rebinding y CSRF). No hay token de acceso. ──────

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from plotspace.core import auth as jarvis_auth


# Headers de seguridad aplicados a TODA respuesta HTTP (defensa en profundidad):
# - nosniff: el browser no adivina el Content-Type (evita que un .md/.txt se
#   ejecute como HTML/JS).
# - X-Frame-Options SAMEORIGIN + frame-ancestors 'self': nadie EXTERNO puede
#   embeber Jarvis en un iframe (anti-clickjacking del workspace). No afecta a
#   Jarvis embebiendo OTROS sitios (eso lo gobiernan los headers del hijo).
# - Referrer-Policy no-referrer: ninguna URL de Jarvis viaja en el header
#   Referer hacia afuera cuando el preview carga un sitio externo.
# CSP completa se deja fuera a propósito: el front usa estilos/scripts inline +
# CDN (xterm) + Google Fonts; una CSP estricta a ciegas rompería la app.
SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'SAMEORIGIN',
    'Content-Security-Policy': "frame-ancestors 'self'",
    'Referrer-Policy': 'no-referrer',
}

# Documentos HTML que sirve el shell. Estos referencian los .js/.css con ?v=N;
# si el browser los cachea por heurística (tienen ETag/Last-Modified pero NINGÚN
# Cache-Control → RFC 7234 §4.2.2: el browser PUEDE cachear ~10% de la antigüedad
# del archivo = HORAS de staleness silencioso), queda apuntando a los ?v viejos
# → carga JS viejo aunque el server ya tenga el nuevo en disco. `no-cache` NO es
# "no guardar": obliga a REVALIDAR contra el server en cada navegación (con el
# ETag que ya emiten → 304 barato si no cambió, HTML fresco con el ?v nuevo si
# cambió). Así un F5 SIEMPRE trae el HTML actual y, con él, los scripts actuales.
# Solo el DOCUMENTO lleva no-cache: los .js/.css versionados con ?v= siguen
# cacheándose fuerte (la URL cambia cuando cambia el contenido).
_HTML_PAGES = {'/', '/workspace', '/editor'}


# Tope de tamaño de request (256 MB). Cubre uploads/zip; el editor y la API real
# mandan payloads chicos. Configurable por env para casos legítimos grandes.
try:
    MAX_BODY_BYTES = int(os.environ.get('JARVIS_MAX_BODY_MB', '256')) * 1024 * 1024
except ValueError:
    MAX_BODY_BYTES = 256 * 1024 * 1024


def _body_demasiado_grande(content_length) -> bool:
    """True si el header Content-Length supera el tope. Ausente/no-numérico → no
    se puede pre-chequear acá: lo corta el LimiteBodyMiddleware contando bytes."""
    if not content_length or not str(content_length).isdigit():
        return False
    return int(content_length) > MAX_BODY_BYTES


class _BodyDemasiadoGrande(Exception):
    """Se levanta cuando el body supera MAX_BODY_BYTES contando bytes reales."""


class LimiteBodyMiddleware:
    """Middleware ASGI que envuelve `receive` y cuenta los BYTES REALES del body
    a medida que llegan, cortando en MAX_BODY_BYTES — funcione o no el header
    Content-Length (un upload chunked sin Content-Length esquivaba el chequeo del
    middleware http y Starlette spool-eaba las partes-archivo a /tmp sin tope =
    disk-fill / caída del server). Al exceder levanta _BodyDemasiadoGrande, que el
    exception handler convierte en 413; el parser deja de leer → el spool queda
    acotado al límite. Lee MAX_BODY_BYTES del módulo en cada request (testeable)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        total = 0
        limite = MAX_BODY_BYTES

        async def receive_contado():
            nonlocal total
            message = await receive()
            if message.get("type") == "http.request":
                total += len(message.get("body", b"") or b"")
                if total > limite:
                    raise _BodyDemasiadoGrande()
            return message

        return await self.app(scope, receive_contado, send)


app.add_middleware(LimiteBodyMiddleware)


@app.exception_handler(_BodyDemasiadoGrande)
async def _handler_body_grande(request: Request, exc: _BodyDemasiadoGrande):
    return JSONResponse(status_code=413, content={'detail': 'Cuerpo demasiado grande'})


@app.middleware("http")
async def _middleware_http(request: Request, call_next):
    # Anti DNS-rebinding: rechazar Host que no sea localhost/IP (un dominio del
    # atacante resolviendo a 127.0.0.1 quedaría afuera). El acceso legítimo
    # —localhost o la IP de LAN— pasa.
    if not jarvis_auth.host_permitido(request.headers.get('host'), jarvis_auth.hosts_extra()):
        resp = JSONResponse(status_code=400, content={'detail': 'Host no permitido'})
    elif _body_demasiado_grande(request.headers.get('content-length')):
        # Tope de body ANTES de leerlo: un POST multipart de varios GB (upload /
        # upload-zip) llenaba /tmp o reventaba la RAM de uvicorn (Starlette spool-ea
        # las partes-archivo sin cuota). Rechazar por Content-Length cubre TODOS los
        # endpoints de una. (auditoría 2ª pasada — DoS)
        resp = JSONResponse(status_code=413, content={'detail': 'Cuerpo demasiado grande'})
    else:
        path = request.url.path
        resp = None
        # CSRF: una mutación disparada por una web maliciosa viaja con Origin
        # cross-site → afuera. Origin ausente = curl o same-origin → permitido.
        if (path.startswith('/api/')
                and request.method not in ('GET', 'HEAD', 'OPTIONS')
                and not jarvis_auth.origen_permitido(request.headers.get('origin'),
                                                     jarvis_auth.hosts_extra())):
            resp = JSONResponse(status_code=403, content={'detail': 'Origin no permitido'})
        if resp is None:
            resp = await call_next(request)
    # Endurecer headers en toda respuesta (setdefault: no pisa headers propios de la ruta).
    for _k, _v in SECURITY_HEADERS.items():
        resp.headers.setdefault(_k, _v)
    # El DOCUMENTO HTML del shell se revalida siempre (no-cache) → F5 trae el HTML
    # actual con los ?v=N actuales y nunca queda servido un index/workspace viejo
    # que apunte a JS viejo. Los estáticos (/static/...) NO se tocan: su ?v= ya
    # hace cache-busting determinista. setdefault por si una ruta fija el suyo.
    # Cualquier .html de /static es un DOCUMENTO igual que el shell:
    # referencia .js/.css con ?v=N, así que también revalida.
    if request.url.path in _HTML_PAGES or request.url.path.endswith('.html'):
        resp.headers.setdefault('Cache-Control', 'no-cache')
    return resp


@app.get("/login")
async def auth_login_url():
    """Ruta vieja del token: ahora no hay login. Manda al home."""
    return RedirectResponse('/', status_code=302)


# ─── Páginas HTML ─────────────────────────────────────────────────────────────

FRONTEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend')


@app.get("/")
async def pagina_home():
    return FileResponse(os.path.join(FRONTEND, 'index.html'))


@app.get("/workspace")
async def pagina_workspace():
    return FileResponse(os.path.join(FRONTEND, 'shell', 'workspace.html'))


@app.get("/editor")
async def pagina_editor():
    # Editor standalone (pestaña de navegador).
    return FileResponse(os.path.join(FRONTEND, 'shell', 'editor-standalone.html'))


@app.get("/api/health")
async def health():
    """Health check liviano. Solo confirma que el proceso responde."""
    return {'status': 'ok'}


@app.get("/favicon.ico")
async def favicon():
    # Fallback multiresolución (16/32/48) para navegadores sin soporte de favicon SVG
    # y para requests directos a la raíz. El SVG se sirve vía /static/shared/favicon.svg.
    return FileResponse(os.path.join(FRONTEND, 'shared', 'favicon.ico'), media_type='image/x-icon')


@app.get("/manifest.webmanifest")
async def manifest():
    # Web App Manifest servido desde la RAÍZ (scope "/") para que Jarvis sea
    # instalable como app standalone: en ese modo el navegador corre sin barra de
    # pestañas ni omnibox y —clave— SIN el botón nativo de "salir de pantalla
    # completa" de Chrome (ese ✕ que aparece al llevar el mouse arriba en F11).
    return FileResponse(os.path.join(FRONTEND, 'manifest.webmanifest'),
                        media_type='application/manifest+json')


# ─── WebSocket de eventos por proyecto ───────────────────────────────────────

@app.websocket("/ws/events/{project_id}")
async def ws_events(websocket: WebSocket, project_id: int):
    """Canal de eventos en tiempo real para el workspace.
    Broadcasts: task_event, workflow_update, orquestador_mensaje."""
    # El middleware http NO corre para websockets: Origin anti CSWSH.
    if not jarvis_auth.origen_permitido(websocket.headers.get('origin'), jarvis_auth.hosts_extra()):
        await websocket.close(code=4403)
        return
    if not await broadcaster.connect(websocket, project_id):
        return   # tope global de WS alcanzado (el broadcaster ya cerró el socket)
    try:
        # Primer mensaje: boot_id del proceso. El cliente compara al
        # reconectar — distinto = server reiniciado → se recarga solo.
        from plotspace.core.fe_watch import BOOT_ID
        await websocket.send_json({'type': 'hola', 'boot_id': BOOT_ID})
        while True:
            # Mantener la conexión viva esperando mensajes del cliente (ping/pong)
            await websocket.receive_text()
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception:
        pass
    finally:
        broadcaster.disconnect(websocket, project_id)


# ─── Archivos estáticos ────────────────────────────────────────────────────────
# Se monta todo el árbol frontend/ en /static; el HTML referencia rutas reales
# (frontend organizado por sección: shell/, shared/, sections/<x>/).
# html=True: una URL de DIRECTORIO ('/static/<demo>/') sirve su index.html —
# los agentes anuncian sus demos así (sin index.html) y el iframe del Web
# Preview recibía 404 (pedido del usuario 2026-07-11).
app.mount("/static", StaticFiles(directory=FRONTEND, html=True), name="static")
