"""
JARVIS — Mantenimiento del estado local.

Antes no había NINGUNA herramienta para limpiar estado viejo, así que se
acumulaba: task_events sin techo, workflows 'running' de semanas atrás
reviviendo monitores en cada boot, sesiones tmux huérfanas (sin terminal en
DB). Esto junta esas limpiezas en una pasada segura, disparable a mano desde
la UI (POST /api/workspace/mantenimiento).

`sesiones_huerfanas` es pura y testeable; `ejecutar_mantenimiento` la usa.
"""
import json
import asyncio
import os
import re
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta

from plotspace.core.database import get_db
from plotspace.core.terminal_backend import backend

# Solo sesiones de card (jarvis_<id>). El mobile preview u otras sesiones con
# otro nombre NO matchean y por lo tanto nunca se las considera huérfanas.
_SESION_RE = re.compile(r'^jarvis_(\d+)$')
_STALE_HORAS = 12

# Logs por-terminal (.workspace/logs/terminal_<id>_<nombre>.log[.1]): crecían sin
# techo (790MB medidos) porque la rotación de 5MB acota cada log VIVO pero los de
# terminales muertas nunca se purgaban. Janitor: borra logs de terminales no
# activas o sin tocar hace más de _LOG_MAX_EDAD_DIAS.
_LOG_RE = re.compile(r'terminal_(\d+)_')
_LOG_MAX_EDAD_DIAS = 7


def sesiones_huerfanas(sesiones_tmux: list, ids_db) -> list:
    """Nombres de sesión `jarvis_<n>` que NO corresponden a ninguna terminal
    conocida en DB (ids_db). Pura: el caller le pasa el estado ya leído."""
    ids = set(ids_db)
    huerfanas = []
    for s in sesiones_tmux:
        m = _SESION_RE.match((s or '').strip())
        if m and int(m.group(1)) not in ids:
            huerfanas.append(s.strip())
    return huerfanas


def huerfanas_a_matar(sesiones_tmux: list, ids_db, db_ok: bool, existe_en_db=None) -> list:
    """Lista FINAL de sesiones a matar, con el guard anti kill-all.

    Si el paso DB falló o no devolvió NINGÚN id (`db_ok`/`ids_db`), la lista de
    terminales está incompleta y TODA sesión jarvis_* parecería huérfana → no se
    mata NADA (un `database is locked` mataba el enjambre entero). `existe_en_db`
    re-chequea cada candidata justo antes del kill (cierra la race con una
    terminal creada entre el snapshot de ids y el list-sessions); si el chequeo
    falla, ante la duda NO se mata. Pura salvo el callback: testeable."""
    if not db_ok or not ids_db:
        return []
    matar = []
    for s in sesiones_huerfanas(sesiones_tmux, ids_db):
        if existe_en_db is not None:
            tid = int(_SESION_RE.match(s).group(1))
            try:
                if existe_en_db(tid):
                    continue
            except Exception:
                continue
        matar.append(s)
    return matar


def _existe_terminal(terminal_id: int) -> bool:
    """Re-chequeo puntual pre-kill. Ante error de DB devuelve True: en la duda,
    la sesión NO se mata (el mantenimiento se puede repetir; un agente muerto no)."""
    try:
        conn = get_db()
        try:
            return conn.execute('SELECT 1 FROM terminals WHERE id = ?',
                                (terminal_id,)).fetchone() is not None
        finally:
            conn.close()
    except Exception:
        return True


def _tid_de_log(nombre: str):
    """id de terminal del nombre de archivo de log (terminal_<id>_...), o None
    si no matchea (archivo ajeno: solo se borra por edad, nunca por inactividad)."""
    m = _LOG_RE.match(nombre or '')
    return int(m.group(1)) if m else None


def logs_a_purgar(archivos: list, ids_activos, ahora: float, max_edad_s: float) -> list:
    """Paths a borrar. archivos: [{'path','mtime','tid'}]. Se borra un log si su
    terminal ya no está activa (tid no en ids_activos) o si no se tocó hace más
    de max_edad_s. Un log de terminal activa y reciente NO se toca (la rotación
    de 5MB ya lo acota). Pura: el caller le pasa el estado ya leído del disco."""
    activos = set(ids_activos)
    borrar = []
    for f in archivos:
        tid = f.get('tid')
        inactiva = tid is not None and tid not in activos
        vieja = (ahora - f['mtime']) > max_edad_s
        if inactiva or vieja:
            borrar.append(f['path'])
    return borrar


def _logs_dir_de(ruta_proyecto: str) -> str:
    return os.path.join(ruta_proyecto or '', '.workspace', 'logs')


def purgar_logs_viejos(max_edad_dias: int = _LOG_MAX_EDAD_DIAS) -> dict:
    """Borra los logs por-terminal viejos / de terminales inactivas en el
    .workspace/logs/ de cada proyecto. Defensiva: un fallo de IO no aborta el
    resto. Devuelve {'logs_borrados', 'bytes_liberados'}."""
    resumen = {'logs_borrados': 0, 'bytes_liberados': 0}
    try:
        conn = get_db()
        try:
            cur = conn.cursor()
            ids_activos = {r['id'] for r in
                           cur.execute('SELECT id FROM terminals WHERE activa = 1').fetchall()}
            rutas = [r['ruta'] for r in cur.execute('SELECT ruta FROM projects').fetchall()]
        finally:
            conn.close()
    except Exception as e:
        print(f'[mantenimiento] purga de logs: no pude leer DB: {e}')
        return resumen

    ahora = time.time()
    max_edad_s = max_edad_dias * 86400
    for ruta in rutas:
        logs_dir = _logs_dir_de(ruta)
        if not os.path.isdir(logs_dir):
            continue
        archivos, sizes = [], {}
        try:
            nombres = os.listdir(logs_dir)
        except OSError:
            continue
        for nombre in nombres:
            path = os.path.join(logs_dir, nombre)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if not os.path.isfile(path):
                continue
            archivos.append({'path': path, 'mtime': st.st_mtime, 'tid': _tid_de_log(nombre)})
            sizes[path] = st.st_size
        for path in logs_a_purgar(archivos, ids_activos, ahora, max_edad_s):
            try:
                os.remove(path)
                resumen['logs_borrados'] += 1
                resumen['bytes_liberados'] += sizes.get(path, 0)
            except OSError as e:
                print(f'[mantenimiento] no pude borrar {path}: {e}')
    return resumen


# ── Backup de la DB (post-incidente rmtree 2026-07-03) ───────────────────────
# El borrado accidental del repo se llevó data/jarvis.db y fue lo ÚNICO
# irrecuperable: su única copia vivía DENTRO del árbol borrado. Snapshot
# consistente (sqlite backup API, seguro con WAL y escrituras en vuelo) a un
# directorio FUERA del repo + espejo best-effort en /mnt/d (sobrevive incluso
# a la pérdida del vhdx de WSL). Rotación por cantidad, corre en el poller.
_BACKUP_DIR = os.path.join(os.path.expanduser('~'), '.jarvis-backups')
# Espejo configurable: el default histórico es el disco D: montado en WSL;
# vacío = espejo desactivado a propósito (una app instalada en otra PC no
# tiene /mnt/d).
_BACKUP_ESPEJO = os.environ.get('JARVIS_BACKUP_ESPEJO', '/mnt/d/jarvis-backups')
_BACKUP_MAX = 48          # ~1 día de historia a 1 backup/30 min (la DB pesa KB)
_BACKUP_RE = re.compile(r'^jarvis-\d{8}-\d{6}\.db$')


def backups_a_purgar(nombres: list, max_copias: int) -> list:
    """Backups que SOBRAN (los más viejos) para dejar a lo sumo max_copias.
    Solo considera los nuestros (jarvis-YYYYMMDD-HHMMSS.db; el timestamp del
    nombre ordena cronológico) — cualquier otro archivo JAMÁS se toca. Pura."""
    propios = sorted(n for n in nombres if _BACKUP_RE.match(n or ''))
    if max_copias < 1 or len(propios) <= max_copias:
        return []
    return propios[:len(propios) - max_copias]


def _rotar_backups(dir_: str, max_copias: int) -> int:
    """Borra los backups sobrantes de dir_. Defensiva: IO roto no aborta."""
    borrados = 0
    try:
        for viejo in backups_a_purgar(os.listdir(dir_), max_copias):
            try:
                os.remove(os.path.join(dir_, viejo))
                borrados += 1
            except OSError:
                pass
    except OSError:
        pass
    return borrados


def respaldar_db(db_path: str = None, destino_dir: str = None,
                 max_copias: int = _BACKUP_MAX,
                 espejo_dir: str = _BACKUP_ESPEJO) -> dict:
    """Snapshot de la DB a destino_dir (default ~/.jarvis-backups) + espejo
    best-effort si el disco del espejo está montado. Nunca levanta.
    Devuelve {'backup': path|None, 'espejo': path|None, 'purgados': n}."""
    resumen = {'backup': None, 'espejo': None, 'purgados': 0}
    if db_path is None:
        from plotspace.core.database import DB_PATH
        db_path = os.path.abspath(DB_PATH)
    destino_dir = destino_dir or _BACKUP_DIR
    if not os.path.isfile(db_path):
        return resumen
    nombre = datetime.now().strftime('jarvis-%Y%m%d-%H%M%S.db')
    destino = os.path.join(destino_dir, nombre)
    try:
        os.makedirs(destino_dir, exist_ok=True)
        src = sqlite3.connect(db_path, timeout=5.0)
        try:
            dst = sqlite3.connect(destino)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        resumen['backup'] = destino
    except Exception as e:
        print(f'[mantenimiento] backup de DB falló: {e}')
        return resumen
    resumen['purgados'] = _rotar_backups(destino_dir, max_copias)
    # Espejo: solo si su carpeta madre existe (D: montado). Un fallo acá NUNCA
    # invalida el backup primario ya escrito.
    if espejo_dir and os.path.isdir(os.path.dirname(espejo_dir)):
        try:
            os.makedirs(espejo_dir, exist_ok=True)
            espejo = os.path.join(espejo_dir, nombre)
            shutil.copy2(destino, espejo)
            resumen['espejo'] = espejo
            _rotar_backups(espejo_dir, max_copias)
        except Exception as e:
            print(f'[mantenimiento] espejo de backup falló: {e}')
    return resumen


# ── Janitor de huérfanos (post-mortem del segfault de tmux, 2026-07-02) ──────
# El server de tmux murió solo (signal 11, bug de tmux 3.6) y dejó atrás:
# 55 clientes `tmux attach-session -d -t jarvis_N` del motor classic colgados
# del socket muerto (~330MB RSS) y procesos de agentes (jest) huérfanos de
# ppid=1 quemando CPU durante 21 HORAS, invisibles porque ya no vivían en
# ninguna terminal. Nada los auditaba → load 18 con 4 cores. Este janitor
# corre en el poller de 30 min:
#  · clientes attach NUESTROS (firma exacta) con sesión target inexistente →
#    kill directo: son siempre basura de Jarvis, cero riesgo.
#  · procesos huérfanos PESADOS de los proyectos → NO se matan (pueden ser
#    algo legítimo del usuario): se AVISA por WS (toast) y queda en el log.
_ATTACH_RE = re.compile(r'tmux attach-session -d -t (jarvis_\d+)$')
_PROYECTOS_HINT = '/proyectos/'


def parsear_etime(etime: str) -> int:
    """`ps -o etime` → segundos. Formatos: MM:SS · HH:MM:SS · D-HH:MM:SS."""
    etime = (etime or '').strip()
    if not etime:
        return 0
    dias = 0
    if '-' in etime:
        d, etime = etime.split('-', 1)
        dias = int(d)
    partes = [int(p) for p in etime.split(':')]
    while len(partes) < 3:
        partes.insert(0, 0)
    h, m, s = partes
    return dias * 86400 + h * 3600 + m * 60 + s


def clientes_attach_muertos(procesos: list, sesiones_vivas) -> list:
    """PIDs de clientes `tmux attach-session -d -t jarvis_N` cuya sesión ya NO
    existe. procesos = [(pid, args)]. Pura: testeable sin ps ni tmux."""
    vivas = set(sesiones_vivas)
    muertos = []
    for pid, args in procesos:
        m = _ATTACH_RE.search((args or '').strip())
        if m and m.group(1) not in vivas:
            muertos.append(pid)
    return muertos


def huerfanos_pesados(procesos: list, umbral_cpu: float = 5.0,
                      min_edad_s: int = 3600) -> list:
    """Procesos huérfanos (ppid=1) de los PROYECTOS que llevan horas quemando
    CPU — la firma del desastre jest post-segfault. procesos = [{pid, ppid,
    etime_s, cpu, args}]. Solo DETECTA (matarlos es decisión del usuario)."""
    out = []
    for p in procesos:
        if p.get('ppid') != 1:
            continue
        if _PROYECTOS_HINT not in (p.get('args') or ''):
            continue
        if p.get('cpu', 0.0) < umbral_cpu or p.get('etime_s', 0) < min_edad_s:
            continue
        out.append(p)
    return out


def _listar_procesos() -> list:
    """Snapshot de ps para el janitor: [{pid, ppid, etime_s, cpu, args}]."""
    res = subprocess.run(['ps', '-eo', 'pid,ppid,etime,pcpu,args', '--no-headers'],
                         capture_output=True, text=True, timeout=5)
    out = []
    for linea in res.stdout.splitlines():
        partes = linea.split(None, 4)
        if len(partes) < 5:
            continue
        try:
            out.append({'pid': int(partes[0]), 'ppid': int(partes[1]),
                        'etime_s': parsear_etime(partes[2]),
                        'cpu': float(partes[3]), 'args': partes[4]})
        except ValueError:
            continue
    return out


def ronda_huerfanos() -> dict:
    """Una pasada del janitor. Mata los clientes attach muertos (nuestros) y
    devuelve los huérfanos pesados DETECTADOS (avisar, no matar)."""
    resumen = {'clientes_attach_matados': 0, 'huerfanos_pesados': []}
    try:
        procesos = _listar_procesos()
    except Exception as e:
        print(f'[mantenimiento] janitor: ps falló: {e}')
        return resumen
    try:
        vivas = sorted(backend().listar_sesiones())
        for pid in clientes_attach_muertos(
                [(p['pid'], p['args']) for p in procesos], vivas):
            try:
                # SIGKILL a propósito: el cliente tmux ATRAPA el SIGTERM e
                # intenta despedirse escribiendo al socket muerto → queda
                # trabado para siempre (medido 2026-07-03: 57 sobrevivieron
                # al TERM tras 1½ días colgados). Son basura inequívoca
                # nuestra, sin nada que cerrar con elegancia.
                os.kill(pid, 9)
                resumen['clientes_attach_matados'] += 1
            except (ProcessLookupError, PermissionError):
                pass
    except Exception as e:
        print(f'[mantenimiento] janitor de clientes tmux falló: {e}')
    try:
        resumen['huerfanos_pesados'] = [
            {'pid': p['pid'], 'cpu': p['cpu'], 'horas': p['etime_s'] // 3600,
             'args': p['args'][:120]}
            for p in huerfanos_pesados(procesos)
        ]
    except Exception as e:
        print(f'[mantenimiento] detección de huérfanos falló: {e}')
    return resumen


def ejecutar_mantenimiento(keep_eventos: int = 5000) -> dict:
    """Síncrona (el caller la corre en un thread). Devuelve un resumen de lo
    limpiado. Cada paso es defensivo: un fallo no aborta los demás."""
    resumen = {
        'task_events_purgados':   0,
        'workflows_stale':        0,
        'tmux_huerfanas_matadas': 0,
        'logs_borrados':          0,
        'bytes_liberados':        0,
        'vacuum':                 False,
    }

    # ── DB: purga de task_events + workflows zombie por edad ──────────────────
    ids_db: set = set()
    db_ok = False
    try:
        conn = get_db()
        try:
            cur = conn.cursor()
            antes = cur.execute('SELECT COUNT(*) FROM task_events').fetchone()[0]
            cur.execute(
                'DELETE FROM task_events WHERE id NOT IN '
                '(SELECT id FROM task_events ORDER BY id DESC LIMIT ?)',
                (keep_eventos,)
            )
            despues = cur.execute('SELECT COUNT(*) FROM task_events').fetchone()[0]
            resumen['task_events_purgados'] = max(0, antes - despues)

            limite = (datetime.now() - timedelta(hours=_STALE_HORAS)).isoformat()
            r = cur.execute(
                "UPDATE workflows SET estado = 'error' "
                "WHERE estado IN ('running', 'paused') AND created_at < ?",
                (limite,)
            )
            resumen['workflows_stale'] = r.rowcount if r.rowcount and r.rowcount > 0 else 0

            ids_db = {row['id'] for row in cur.execute('SELECT id FROM terminals').fetchall()}
            conn.commit()
            db_ok = True
        finally:
            conn.close()
    except Exception as e:
        print(f'[mantenimiento] paso DB falló: {e}')

    # ── tmux: matar sesiones huérfanas (sin terminal en DB) ───────────────────
    # GUARD anti kill-all: con el paso DB fallido (o sin ids) la lista está
    # incompleta y TODAS las jarvis_* parecerían huérfanas → se saltea entero.
    if not db_ok or not ids_db:
        resumen['tmux_salteado'] = 'db_fallo' if not db_ok else 'db_sin_ids'
        print(f"[mantenimiento] paso tmux SALTEADO ({resumen['tmux_salteado']}): "
              "no se mata nada con la lista de terminales incompleta")
    else:
        try:
            sesiones = sorted(backend().listar_sesiones())
            for s in huerfanas_a_matar(sesiones, ids_db, db_ok, _existe_terminal):
                backend().matar_sesion_por_nombre(s)
                resumen['tmux_huerfanas_matadas'] += 1
        except Exception as e:
            print(f'[mantenimiento] paso tmux falló: {e}')

    # ── logs por-terminal: purga de viejos / de terminales inactivas ──────────
    try:
        resumen.update(purgar_logs_viejos())
    except Exception as e:
        print(f'[mantenimiento] purga de logs falló: {e}')

    # ── janitor de huérfanos: clientes attach muertos + detección de pesados ──
    try:
        resumen.update(ronda_huerfanos())
    except Exception as e:
        print(f'[mantenimiento] janitor de huérfanos falló: {e}')

    # ── VACUUM: reclamar espacio tras las purgas ──────────────────────────────
    try:
        conn = get_db()
        try:
            conn.execute('VACUUM')
            resumen['vacuum'] = True
        finally:
            conn.close()
    except Exception as e:
        print(f'[mantenimiento] VACUUM falló: {e}')

    return resumen


_huerfanos_avisados: set = set()   # pids ya avisados por WS (no re-tostear lo mismo cada 30 min)


def mantener_memorias() -> dict:
    """Regenera el INDEX.md de la memoria compartida de cada proyecto que la
    tenga. Self-healing periódico: antes el INDEX solo se regeneraba al abrir
    la pestaña Memoria de la UI, así que derivaba entre visitas (entradas
    appendeadas a mano, fuera de orden). Compare-before-write: cero churn."""
    resumen = {'proyectos': 0}
    try:
        conn = get_db()
        try:
            rutas = [r['ruta'] for r in conn.execute('SELECT ruta FROM projects').fetchall()]
        finally:
            conn.close()
    except Exception as e:
        print(f'[mantenimiento] memoria: no pude leer proyectos: {e}')
        return resumen
    # Lazy import (router → se evita el ciclo en el boot de core)
    from plotspace.routers.memory import _regenerar_index
    from plotspace.core.memoria_lecciones import inyectar_lecciones
    for ruta in rutas:
        if not os.path.isdir(os.path.join(ruta or '', '.jarvis', 'memory')):
            continue
        # Salud que EJECUTA (solo lo reversible): cuarentena vencida + gracia,
        # sin uso registrado → estado: archivo automático. Flag para apagarlo.
        if os.environ.get('MEMORIA_AUTOARCHIVO', 'on').lower() != 'off':
            try:
                from plotspace.core.memoria_lint import auto_archivar
                from plotspace.routers.memory import _slugs_usados
                archivadas = auto_archivar(ruta, usados=_slugs_usados())
                if archivadas:
                    print(f'[mantenimiento] memoria: auto-archivadas en {ruta}: '
                          f'{archivadas}')
            except Exception as e:
                print(f'[mantenimiento] memoria: auto-archivo de {ruta} falló: {e}')
        try:
            _regenerar_index(ruta)
            resumen['proyectos'] += 1
        except Exception as e:
            print(f'[mantenimiento] memoria: INDEX de {ruta} falló: {e}')
        # Bloque siempre-cargado de lecciones: se recompila acá (determinista
        # desde las memorias [leccion] + destilado si existe) para que refleje
        # lo que el enjambre escribió, haya workflows o no. Compare-before-write.
        try:
            inyectar_lecciones(ruta)
        except Exception as e:
            print(f'[mantenimiento] memoria: lecciones de {ruta} fallaron: {e}')
        # MAILBOX largo → archivo histórico (solo si está QUIETO >1h: no pisar
        # un append en vuelo; el watcher re-baselinea al ver el archivo achicarse).
        try:
            from plotspace.core.mailbox import MAILBOX_NOMBRE, archivar_mailbox
            mb = os.path.join(ruta, MAILBOX_NOMBRE)
            if os.path.exists(mb) and (time.time() - os.path.getmtime(mb)) > 3600:
                n = archivar_mailbox(mb)
                if n:
                    print(f'[mantenimiento] mailbox: {n} líneas archivadas en {ruta}')
        except Exception as e:
            print(f'[mantenimiento] mailbox de {ruta} falló: {e}')
    return resumen


async def poller_purga_logs():
    """Background task: cada 30 min purga los logs viejos/inactivos (la fuga de
    disco de .workspace/logs) + corre el janitor de huérfanos (clientes attach
    muertos se matan solos; huérfanos pesados de proyectos se AVISAN por WS —
    matarlos es decisión del usuario) + respalda la DB fuera del repo (uno al
    boot y uno por ronda). Los pasos de DB/tmux del mantenimiento siguen siendo
    manuales a propósito. Nunca crashea."""
    try:
        r0 = await asyncio.to_thread(respaldar_db)
        if r0['backup']:
            print(f"[mantenimiento] backup de DB al boot: {r0['backup']}"
                  + (f" (+espejo {r0['espejo']})" if r0['espejo'] else ''))
    except Exception as e:
        print(f'[mantenimiento] backup de DB al boot falló: {e}')
    while True:
        await asyncio.sleep(1800)
        try:
            await asyncio.to_thread(respaldar_db)
        except Exception as e:
            print(f'[mantenimiento] backup periódico de DB falló: {e}')
        try:
            r = await asyncio.to_thread(purgar_logs_viejos)
            if r['logs_borrados']:
                print(f"[mantenimiento] purga de logs: {r['logs_borrados']} archivos, "
                      f"{r['bytes_liberados'] // (1024 * 1024)}MB liberados")
        except Exception as e:
            print(f'[mantenimiento] poller de purga de logs falló: {e}')
        try:
            await asyncio.to_thread(mantener_memorias)
        except Exception as e:
            print(f'[mantenimiento] janitor de memoria falló: {e}')
        try:
            from plotspace.core.memoria_lecciones import tal_vez_destilar_todos
            await asyncio.to_thread(tal_vez_destilar_todos)
        except Exception as e:
            print(f'[mantenimiento] destilador de lecciones falló: {e}')
        try:
            from plotspace.core.memoria_endurecimiento import pasada_janitor
            await asyncio.to_thread(pasada_janitor)
        except Exception as e:
            print(f'[mantenimiento] escalera de endurecimiento falló: {e}')
        try:
            # Paracaídas del árbol compartido: foto del WIP sucio de cada
            # proyecto a refs/jarvis/wip (índice temporal — jamás el real).
            from plotspace.core.wip_snapshots import snapshots_todos
            rw = await asyncio.to_thread(snapshots_todos)
            if rw['fotos']:
                print(f"[mantenimiento] wip: {rw['fotos']} snapshot(s) del árbol sucio")
            # Los fallos se dicen SIEMPRE: este paracaídas estuvo cinco días
            # muerto porque un 0 silencioso se leía igual que "no había nada".
            if rw.get('fallos'):
                print(f"[mantenimiento] ⚠ wip: {rw['fallos']} snapshot(s) FALLARON — "
                      + '; '.join(rw.get('motivos', [])[:3]))
        except Exception as e:
            print(f'[mantenimiento] snapshots de WIP fallaron: {e}')
        try:
            r2 = await asyncio.to_thread(ronda_huerfanos)
            if r2['clientes_attach_matados']:
                print(f"[mantenimiento] janitor: {r2['clientes_attach_matados']} "
                      f"clientes tmux muertos matados")
            pesados = r2['huerfanos_pesados']
            pids = {p['pid'] for p in pesados}
            if pesados and pids != _huerfanos_avisados:
                _huerfanos_avisados.clear()
                _huerfanos_avisados.update(pids)
                from plotspace.core.events import broadcaster
                await broadcaster.broadcast_global({'type': 'sistema_huerfanos',
                                                    'procesos': pesados})
                print(f'[mantenimiento] janitor: {len(pesados)} huérfanos pesados '
                      f'detectados (avisados por WS): {[p["pid"] for p in pesados]}')
            elif not pesados:
                _huerfanos_avisados.clear()
        except Exception as e:
            print(f'[mantenimiento] janitor de huérfanos falló: {e}')
