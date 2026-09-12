import asyncio
import base64
import glob
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState
from pydantic import BaseModel

from plotspace.core.database import get_db
from plotspace.core.terminal_backend import EspecSesion, backend as motor_terminales

router = APIRouter(tags=["terminals"])

# Se prende cuando reconciliar_sesiones_tmux() terminó de trickle-ear los CLIs
# tras un reboot. La precarga de Whisper lo espera para no competir por CPU con
# el arranque de las terminales (el pico de "abrir el workspace a la mañana").
reconcile_listo = asyncio.Event()

# Tope de terminales por workspace (espejado en el frontend: MAX_TERMINALES en
# workspace.js). El mosaico auto-tile reparte el espacio hasta este número.
MAX_TERMINALES = 12

# Tope de tamaño del log persistente por terminal. Al superarlo rota a un único
# `.1` (pisa el `.1` previo) → disco acotado a ~2× por terminal. Antes crecía
# sin techo: se midieron 122 MB en un solo log y ~500 MB en total.
MAX_LOG_BYTES = 5 * 1024 * 1024  # 5 MB

# Buffer de log del PTY: cada cuánto (s) y cada cuántos bytes acumulados se
# vuelca el log a disco. El write salía SÍNCRONO por cada chunk de os.read
# dentro del event loop (cientos/seg con un agente activo) → micro-stalls en
# ráfaga que congelaban TODOS los WS a la vez (lag de tipeo + entrega de voz).
# Ahora se acumula en memoria y se flushea FUERA del loop (to_thread). Ver
# _LogWriter y [[lag-tipeo-append-log]].
_LOG_FLUSH_S = 0.5
_LOG_FLUSH_BYTES = 64 * 1024

# Registro en memoria: terminal_id → {'process': ptyprocess.PtyProcess, 'type': 'pty'}
terminal_processes: dict = {}

# Monitor de keywords: terminal_id → asyncio.Task
keyword_monitors: dict = {}

# Fast-path event-driven: terminal_id → asyncio.Event de "despertar al monitor".
# El poller agent_watch (que ya captura cada pane a 1s y ya computa
# hay_keyword_protocolo) llama solicitar_chequeo_inmediato cuando ve un TASK_*
# fresco: en vez de esperar el sleep del monitor, le adelanta el próximo tick.
# El monitor sigue siendo el ÚNICO que decide/escribe (capture+diff+keyword); el
# trigger solo ahorra latencia. El Event vive/muere con la Task del monitor.
_monitor_wakeups: dict = {}

KEYWORDS_CONTROL = {"TASK_DONE", "TASK_BLOCKED", "TASK_ERROR"}

# Regex para limpiar códigos ANSI del output de terminal
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[mGKHFJA-Z]')

# Regex por keyword: la línea solo puede tener no-letras antes/después del keyword.
# Filtra "Cuando termines escribí TASK_DONE" pero acepta "✅ TASK_DONE".
_KW_SOLO_RE = {
    kw: re.compile(r'^[^a-zA-Z]*' + re.escape(kw) + r'[^a-zA-Z]*$')
    for kw in KEYWORDS_CONTROL
}


def _linea_es_keyword(linea: str, kw: str) -> bool:
    """True si la línea es output real del agente con el keyword (no una instrucción)."""
    clean = _ANSI_RE.sub('', linea).strip()
    return bool(_KW_SOLO_RE[kw].match(clean))


def _env_terminal() -> dict:
    """Entorno para procesos PTY/tmux: sin ANTHROPIC_API_KEY, con colores."""
    env = os.environ.copy()
    env.pop('ANTHROPIC_API_KEY', None)
    # Marcas de IDENTIDAD de sesión de Claude Code: si el server lo arrancó una
    # sesión de claude (un dev, un agente), viajan uvicorn → motor → terminal y
    # los claude del workspace nacen creyéndose sesión HIJA de esa otra
    # ("Transcript saving is off — inherited CLAUDE_CODE_CHILD_SESSION marker",
    # 2026-08-02). Se sellan ANTES de los knobs de config, que van más abajo.
    for marca in ('CLAUDECODE', 'CLAUDE_CODE_CHILD_SESSION',
                  'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_EXECPATH',
                  'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_SSE_PORT',
                  'CLAUDE_PID', 'CLAUDE_EFFORT'):
        env.pop(marca, None)
    env['TERM'] = 'xterm-256color'
    env['COLORTERM'] = 'truecolor'
    # Prompt suggestions (ghost text) de Claude Code APAGADAS (pedido del usuario
    # 2026-07-02): el mensaje sugerido agranda el cuadro de input con texto que él
    # no puso. Var verificada contra el binario 2.1.198 + docs oficiales; solo
    # afecta a claude (las demás CLIs la ignoran) y a sesiones NUEVAS. Escape
    # hatch: JARVIS_PROMPT_SUGGESTIONS=on las restaura.
    if os.environ.get('JARVIS_PROMPT_SUGGESTIONS', '').lower() != 'on':
        env['CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION'] = 'false'
    # Claude Code en modo FULLSCREEN (pantalla alternativa, v2.1.89+, docs
    # code.claude.com/docs/en/fullscreen.md): la conversación y los diálogos
    # (/usage, /status) viven CONTENIDOS en el alt-screen — nada cae jamás al
    # scrollback. Mata de raíz la familia "texto multiplicado/cortado al
    # redimensionar" (una TUI inline no puede borrar lo que ya cayó al
    # historial: cada resize con transcript largo re-imprimía TODO al ancho
    # nuevo dejando la copia vieja arriba — video del usuario 2026-07-02) y los
    # residuos de /usage. La rueda scrollea el transcript real de Claude.
    # Trade-off aceptado por el usuario: el modal Historial ve menos
    # profundidad para claude (history de tmux ≈ 0 en alt-screen). Solo afecta
    # a claude — las demás CLIs ignoran la var. Reintroducido de 3661136 (la
    # "bala de plata" arrastrada por el revert masivo 4cabb53).
    # Apagable: JARVIS_CLAUDE_FULLSCREEN=off.
    if os.environ.get('JARVIS_CLAUDE_FULLSCREEN', 'on').lower() != 'off':
        env['CLAUDE_CODE_NO_FLICKER'] = '1'
        # FULL_REPAINT acompaña al fullscreen (deep work 2026-07-08, video del
        # freeze): con conversaciones GIGANTES (212.7k tokens) el scroll del
        # transcript emite frames PARCIALES (regiones negras huérfanas) y
        # claude se calla hasta 29.8s re-layouteando. Con full-repaint cada
        # frame 2026 borra y repinta el viewport completo → regiones negras
        # imposibles y el peor silencio cae a 2.3s (-92%), medido con la MISMA
        # conversación del incidente (v2.1.204). Costo ~4x bytes SOLO durante
        # scroll agresivo (~190KB/s pico) — trivial para el motor control.
        # Ver [[negro-fullscreen-frames-2026]]. Apagable: JARVIS_CLAUDE_FULL_REPAINT=off.
        if os.environ.get('JARVIS_CLAUDE_FULL_REPAINT', 'on').lower() != 'off':
            env['CLAUDE_CODE_ALT_SCREEN_FULL_REPAINT'] = '1'
    return env


def _nvm_bin_dir(base: Optional[str] = None) -> Optional[str]:
    """Dir `bin` del Node de WSL (node + codex + npm del nvm). El workspace es un
    directorio Linux (WSL): `codex` DEBE correr con el binario NATIVO de Ubuntu,
    no con el shim de Windows (/mnt/c/.../npm/codex) que tira `exec: node: not
    found`. Y el codex del nvm es un `.js` con shebang `env node`, así que `node`
    también tiene que estar en el PATH — los dos viven en este mismo bin.

    Prioriza el bin que tiene `codex`, luego el que tiene `node`, luego cualquiera.
    None si no hay nvm (entorno sin Node). PURA salvo el listado de disco."""
    base = base or os.path.expanduser('~/.nvm/versions/node')
    if not os.path.isdir(base):
        return None
    bins = sorted(glob.glob(os.path.join(base, '*', 'bin')), reverse=True)
    con_codex = [d for d in bins if os.path.exists(os.path.join(d, 'codex'))]
    con_node = [d for d in bins if os.path.exists(os.path.join(d, 'node'))]
    return (con_codex or con_node or bins or [None])[0]


def _prefijo_path_wsl(nvm_bin: Optional[str]) -> str:
    """Prefijo shell que antepone el toolchain Node de WSL al PATH del pane ANTES
    de lanzar el CLI. Vacío si no hay nvm. PURA.

    Por qué en el comando y NO vía `tmux -e PATH=...`: tmux IGNORA el override de
    PATH por -e (sí respeta CODEX_HOME y otras vars — verificado empíricamente),
    y el pane queda con el PATH del server, donde `codex` cae al shim de Windows.
    Un `export` en el propio comando del pane es shell puro → a prueba de balas."""
    return f'export PATH="{nvm_bin}:$PATH"; ' if nvm_bin else ''


# Nombre con sufijo numerado: "Claude Code #3" → base "Claude Code", n 3
_SUFIJO_NUM_RE = re.compile(r'^(?P<base>.*\S)\s+#(?P<n>\d+)$')


def resolver_nombre_unico(nombres_activos, deseado: str) -> str:
    """El nombre de la terminal es la IDENTIDAD de coordinación del agente
    (mailbox 1-a-1, dueños y permisos de Agents Live): dos activas del mismo
    proyecto no pueden compartirlo. Si `deseado` choca (case-insensitive,
    igual que el matching del mailbox), numera con el MÁXIMO '#N' ya usado
    por esa base + 1 — los huecos no se reusan: un '#2' muerto puede seguir
    citado en el MAILBOX y un homónimo nuevo heredaría sus mensajes."""
    deseado = deseado.strip()
    usados = {n.strip().lower() for n in nombres_activos}
    if deseado.lower() not in usados:
        return deseado
    m = _SUFIJO_NUM_RE.match(deseado)
    base = m.group('base') if m else deseado
    base_lower = base.lower()
    maximo = 1
    for n in nombres_activos:
        mn = _SUFIJO_NUM_RE.match(n.strip())
        if mn and mn.group('base').lower() == base_lower:
            maximo = max(maximo, int(mn.group('n')))
    return f'{base} #{maximo + 1}'


# CLIs que aceptan FIJAR el id de sesión al arrancar (`--session-id <uuid>`) → id
# determinista por terminal, el resume más robusto. El resto (codex/opencode/agy)
# reanuda "la más reciente". Ver _comando_lanzamiento.
_CLIS_SESSION_ID = ('claude', 'qwen')


def _session_uuid_para(tipo_ia: str) -> Optional[str]:
    """uuid4 fresco para una terminal claude/qwen — se arranca con `--session-id
    <uuid>` y ese uuid ES el id determinista de la conversación en disco, dando el
    mapeo terminal→transcript para poder `--resume <uuid>` tras un reboot (ver
    _comando_lanzamiento). None para el resto de CLIs (no dejan fijar el id)."""
    return str(uuid.uuid4()) if tipo_ia in _CLIS_SESSION_ID else None


# Comandos de arranque por tipo de CLI. claude va PELADO: `--permission-mode auto`
# se sacó a pedido del usuario (2026-07-06) — su ~/.claude/settings.json ya tiene
# `"permissions": {"defaultMode": "auto"}`, así que el flag era redundante y encima
# ensuciaba la línea. El CLI se lanza como PROGRAMA del pane (sin eco), así que ni
# el --session-id/--resume con su uuid se ve; igual conviene que sea corto.
_COMANDOS_CLI = {
    'claude':      'claude',
    'codex':       'codex',
    'opencode':    'opencode',
    'qwen':        'qwen',
    'antigravity': 'agy',
    'grok':        'grok',
    # Cursor CLI (curl-installer) se llama `agent`; Pi es el binario `pi`.
    'cursor':      'agent',
    'pi':          'pi',
}


def _comando_lanzamiento(tipo_ia: Optional[str], session_uuid: Optional[str],
                         jsonl_existe: bool, es_reanudacion: bool = False) -> Optional[str]:
    """Comando de arranque del CLI para una terminal (PURO — sin disco ni DB).

    Persistencia por CLI (tras un corte de luz/reboot vuelve con su conversación):
    - claude / qwen: id DETERMINISTA por terminal → `--session-id <uuid>` en frío,
      `--resume <uuid>` al reanudar. claude decide con el transcript en disco
      (jsonl_existe); qwen con es_reanudacion, + `--chat-recording` (sin el cual
      qwen NO guarda ni --resume anda). Es el modo más robusto (per-terminal).
    - codex / opencode / antigravity: NO dejan fijar el id al arrancar, así que se
      reanuda "la MÁS RECIENTE" (`codex resume --last`, `opencode --continue`,
      `agy --continue`). Perfecto con 1 instancia por cuenta/proyecto; con varias
      del mismo tipo en la misma cuenta puede traer la de otra terminal (best-effort).
    es_reanudacion=True lo pasa reconciliar_sesiones_tmux (recrear tras reboot); en
    la creación normal es False (arranque en frío). SIN `--permission-mode auto`
    (lo pone el settings.json). None para manual/shell (no se autolanza CLI).

    OJO: el borrado explícito (✕ → activa=0) no se reanuda — reconciliar sólo
    resucita activa=1; acá sólo importa qué CLI arranca al crear la sesión."""
    t = tipo_ia or ''
    if t == 'claude':
        if not session_uuid:
            return 'claude'                       # legacy (id aleatorio, no resumible)
        return (f'claude --resume {session_uuid}' if jsonl_existe
                else f'claude --session-id {session_uuid}')
    if t == 'qwen':
        rec = '--chat-recording'                  # sin esto qwen NO guarda ni --resume anda
        if session_uuid:
            return (f'qwen --resume {session_uuid} {rec}' if es_reanudacion
                    else f'qwen --session-id {session_uuid} {rec}')
        return f'qwen --continue {rec}' if es_reanudacion else f'qwen {rec}'
    if t == 'codex':
        return 'codex resume --last' if es_reanudacion else 'codex'
    if t == 'opencode':
        return 'opencode --continue' if es_reanudacion else 'opencode'
    if t == 'antigravity':
        return 'agy --continue' if es_reanudacion else 'agy'
    if t == 'grok':
        # Grok Build (beta, @xai-official/grok): no documenta resume/--continue,
        # así que la reanudación post-reboot arranca FRESCO (best-effort hasta
        # que xAI sume el flag; revisar `grok --help` al actualizar el CLI).
        return 'grok'
    if t == 'cursor':
        # Cursor CLI (binario `agent`): `--continue` reanuda la sesión más
        # reciente (igual que codex/opencode: best-effort por terminal).
        return 'agent --continue' if es_reanudacion else 'agent'
    if t == 'pi':
        # Pi (@earendil-works/pi-coding-agent): `-c` continúa la más reciente.
        return 'pi -c' if es_reanudacion else 'pi'
    return _COMANDOS_CLI.get(t)                    # None para manual/shell


# Arranque VISIBLE (camino intermedio, pedido del usuario 2026-07-10): estos
# CLIs arrancan en frío SIN flags obligatorios en la línea, así que el pane
# puede nacer como shell de login (prompt de WSL a la vista) y el CLI tipearse
# CORTO por send-keys. claude no pierde la reanudación: su SessionStart hook
# postea el uuid VIVO a la DB en cada arranque (_guardar_session_uuid). qwen
# queda AFUERA: necesita --session-id + --chat-recording en la línea y no hay
# hook que capture el id después.
_CLIS_ARRANQUE_VISIBLE = {'claude', 'codex', 'opencode', 'antigravity', 'grok',
                          'cursor', 'pi'}


def _arranque_visible(tipo_ia: Optional[str], comando_cli: Optional[str],
                      es_reanudacion: bool, modo: Optional[str]) -> bool:
    """¿El pane nace como shell a la vista + CLI tipeado corto? (PURO).

    Programa del pane (invisible, el de siempre) cuando:
    - comando_cli explícito (workflows: lleva flags que no queremos tipeados y
      el engine manda la tarea por send-keys — no puede caer en un bash),
    - es_reanudacion (reconciliar/attach post-reboot: el --resume manda),
    - el tipo no está en _CLIS_ARRANQUE_VISIBLE (qwen/manual/desconocidos),
    - TERMINALES_ARRANQUE=limpio (vía de escape al comportamiento previo)."""
    if (modo or 'shell').strip().lower() == 'limpio':
        return False
    if comando_cli or es_reanudacion:
        return False
    return (tipo_ia or '') in _CLIS_ARRANQUE_VISIBLE


def _comando_corto(tipo_ia: Optional[str]) -> Optional[str]:
    """El comando pelado que se tipea a la vista (`claude`, `agy`, ...)."""
    return _COMANDOS_CLI.get(tipo_ia or '')


def _tipear_cli_visible(terminal_id: int, corto: str, max_espera: float = 15.0):
    """Tipea el CLI corto en el shell del pane, ESPERANDO primero el prompt.

    El send-keys inmediato quedaba pre-echoado por la tty ARRIBA del prompt
    (bash tarda ~1-2s en sourcear nvm bajo carga; el kernel echo-a el input
    bufferizado en canónico y readline lo redibuja después → línea `claude`
    suelta arriba, verificado con capture-pane). Polleando hasta que el pane
    pinte algo (la PS1 es lo PRIMERO que aparece en un login shell, no hay
    motd) el tipeo cae sobre el prompt, como hecho a mano. Best-effort: al
    agotar la espera se tipea igual (readline lo redibuja bien) y cualquier
    error (sesión muerta, motor trabado) solo se loguea — corre en hilo daemon."""
    motor = motor_terminales()
    try:
        deadline = time.monotonic() + max_espera
        while time.monotonic() < deadline:
            pantalla = motor.capturar(terminal_id)
            if pantalla is None:
                return                      # la sesión ya no existe
            if pantalla.strip():
                break                       # apareció el prompt
            time.sleep(0.25)
        motor.enviar_texto(terminal_id, corto)
        motor.enviar_tecla(terminal_id, 'Enter')
    except Exception as e:
        print(f'[terminales] tipeo visible en {terminal_id} falló: {e}')


def _lanzar_tipeo_visible(terminal_id: int, corto: str):
    """Dispara el tipeo visible en un hilo daemon: la espera del prompt (hasta
    ~2s con nvm lento) no debe frenar la creación — un batch de 9 terminales
    la pagaría en serie."""
    import threading
    threading.Thread(target=_tipear_cli_visible, args=(terminal_id, corto),
                     daemon=True, name=f'tipeo-{terminal_id}').start()


_UUID_RE = re.compile(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')


from plotspace.core import hooks_cli as _hooks_cli


def asegurar_session_hook(settings_path: str, hook_cmd: str) -> bool:
    """Instala el SessionStart hook de claude en settings_path (idempotente,
    preservando todo lo demás). Devuelve True si lo agregó, False si ya estaba.
    Se llama al boot con ~/.claude/settings.json. Best-effort: cualquier IO/JSON
    roto se traga (nunca rompe el arranque del server)."""
    try:
        import json as _json
        try:
            with open(settings_path, encoding='utf-8') as f:
                data = _json.load(f)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        hooks = data.setdefault('hooks', {})
        grupos = hooks.setdefault('SessionStart', [])
        # ¿ya está el comando EXACTO? Nada que hacer.
        for grp in grupos:
            for h in grp.get('hooks', []):
                if h.get('command') == hook_cmd:
                    return False
        # Una versión VIEJA del mismo hook se REEMPLAZA, no se acumula. Sin
        # esto, arreglar el comando no le servía a quien ya lo tenía mal: el
        # roto seguía registrado y fallando en cada arranque de agente
        # (2026-07-27: `python3` + ruta sin comillas, en Windows). Se
        # identifica por el script, y solo se tocan las entradas nuestras —
        # settings.json es del usuario y puede tener hooks suyos al lado.
        marca = os.path.basename(_hooks_cli.SCRIPT_SESSION)
        reemplazado = False
        for grp in grupos:
            for h in grp.get('hooks', []):
                if marca in (h.get('command') or ''):
                    h['command'] = hook_cmd
                    reemplazado = True
        if not reemplazado:
            grupos.append({'hooks': [{'type': 'command', 'command': hook_cmd}]})
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        with open(settings_path, 'w', encoding='utf-8') as f:
            _json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


def _guardar_session_uuid(terminal_id: int, session_uuid: str) -> bool:
    """Actualiza terminals.session_uuid al uuid VIVO de claude (lo llama el
    SessionStart hook cada arranque). Así --resume apunta siempre al transcript
    actual, no al inicial — claude rota el <uuid>.jsonl al compactar/continuar y
    sin esto la reanudación traía contexto viejo. Valida el formato uuid (el hook
    manda lo que venga). Devuelve True si actualizó."""
    if not session_uuid or not _UUID_RE.match(session_uuid):
        return False
    conn = get_db()
    try:
        conn.execute("UPDATE terminals SET session_uuid=? WHERE id=?",
                     (session_uuid, terminal_id))
        conn.commit()
        return True
    finally:
        conn.close()


def _transcript_claude_existe(session_uuid: Optional[str]) -> bool:
    """True si el `<uuid>.jsonl` de claude ya existe en ~/.claude/projects/
    (IMPURO: toca disco). El uuid es único global, así que globear por nombre
    esquiva la codificación exacta del cwd (claude reemplaza `/` y `.` por `-`
    en la carpeta del proyecto). Determina --resume vs --session-id."""
    if not session_uuid:
        return False
    patron = os.path.join(os.path.expanduser('~'), '.claude', 'projects',
                          '*', f'{session_uuid}.jsonl')
    return bool(glob.glob(patron))


def _launch_command_de_terminal(terminal_id: int, es_reanudacion: bool = False) -> Optional[str]:
    """launch_command para una terminal (IMPURO: DB + disco; corre en thread).
    Lee tipo_ia + session_uuid de la fila y consulta si el transcript existe.
    es_reanudacion=True (lo pasa reconciliar tras un reboot) hace que los CLIs
    sin id determinista reanuden la sesión más reciente. Ver _comando_lanzamiento."""
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT t.tipo_ia, t.session_uuid, p.ruta FROM terminals t '
            'JOIN projects p ON t.project_id = p.id WHERE t.id = ?',
            (terminal_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    cmd = _comando_lanzamiento(
        row['tipo_ia'], row['session_uuid'],
        _transcript_claude_existe(row['session_uuid']),
        es_reanudacion,
    )
    # Shell (sin CLI → cmd None): al REANUDAR tras un reboot, re-imprimir el
    # scrollback guardado (snapshot) para que el historial visual vuelva donde
    # quedó, en vez de un shell en blanco. Ver core/terminal_snapshot.py.
    if cmd is None and es_reanudacion:
        try:
            from plotspace.core.terminal_snapshot import comando_restore_shell
            cmd = comando_restore_shell(row['ruta'], terminal_id)
        except Exception:
            cmd = None
    return cmd


def _tipo_ia_de(terminal_id: int) -> Optional[str]:
    """tipo_ia de la fila (IMPURO: DB; corre en thread). None si no existe."""
    conn = get_db()
    try:
        row = conn.execute('SELECT tipo_ia FROM terminals WHERE id = ?',
                           (terminal_id,)).fetchone()
        return row['tipo_ia'] if row else None
    finally:
        conn.close()


def _nombres_activos(cursor, project_id: int, salvo_id: int = None) -> list:
    """Nombres de las terminales activas del proyecto (para unicidad)."""
    if salvo_id is None:
        cursor.execute('SELECT nombre FROM terminals WHERE project_id = ? AND activa = 1',
                       (project_id,))
    else:
        cursor.execute('SELECT nombre FROM terminals WHERE project_id = ? AND activa = 1 AND id != ?',
                       (project_id, salvo_id))
    return [r['nombre'] for r in cursor.fetchall()]


# ─── Git helpers (síncronos) ───────────────────────────────────────────────────

def _git_run(cwd: str, args: list) -> tuple:
    """Ejecuta comando git síncrono. Devuelve (returncode, salida).
    timeout=15: git init/commit (con hooks) o una ruta en un mount de red lento
    no deben colgar el handler indefinidamente (auditoría perf)."""
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=15)
        return result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return -1, 'git timeout'
    except Exception as e:
        return -1, str(e)


def _asegurar_git(project_path: str):
    """Inicializa repo git con commit vacío si no existe o no tiene commits."""
    if not os.path.isdir(os.path.join(project_path, '.git')):
        _git_run(project_path, ['git', 'init'])
        _git_run(project_path, [
            'git', '-c', 'user.email=jarvis@local', '-c', 'user.name=JARVIS',
            'commit', '--allow-empty', '-m', 'init: JARVIS workspace',
        ])
        return
    rc, _ = _git_run(project_path, ['git', 'rev-parse', 'HEAD'])
    if rc != 0:
        _git_run(project_path, [
            'git', '-c', 'user.email=jarvis@local', '-c', 'user.name=JARVIS',
            'commit', '--allow-empty', '-m', 'init: JARVIS workspace',
        ])


# Lock por proyecto para serializar _preparar_proyecto: el cuerpo era atómico
# (sin awaits) y ahora corre en thread; dos conexiones simultáneas al mismo
# proyecto no deben reescribir CLAUDE.md/memoria/mailbox a la vez.
_preparar_locks: dict = {}
# TTL: el cuerpo pesado (git + reescritura de CLAUDE.md + memoria/mailbox/
# puertos/live) corría ENTERO en cada conexión WS — al abrir un workspace de 9
# terminales, la 9ª esperaba la suma de las 8 anteriores bajo el lock antes de
# siquiera attachear (auditoría 2026-07-02). Preparar una vez por minuto por
# proyecto alcanza: el contenido inyectado solo cambia con acciones del usuario.
_PREPARAR_TTL = 60.0
_preparado_ts: dict = {}


async def _preparar_proyecto(project_path: str, project_id: Optional[int] = None) -> str:
    """Asegura carpeta + repo git + skills/memoria/mailbox del proyecto. Corre en
    CADA conexión de terminal y hace git + escrituras de archivo SÍNCRONAS: en el
    event loop trababa el tipeo de todas las terminales (peor al cargar un
    workspace de 9 = 9× git+I/O de golpe). Ahora va FUERA del loop (to_thread),
    serializado por proyecto y con TTL (ver _PREPARAR_TTL). Devuelve project_path."""
    lock = _preparar_locks.setdefault(project_path, asyncio.Lock())
    async with lock:
        if time.monotonic() - _preparado_ts.get(project_path, -_PREPARAR_TTL) < _PREPARAR_TTL:
            return project_path
        await asyncio.to_thread(_preparar_proyecto_sync, project_path, project_id)
        _preparado_ts[project_path] = time.monotonic()
    return project_path


def _preparar_proyecto_sync(project_path: str, project_id: Optional[int] = None) -> str:
    """Cuerpo síncrono de _preparar_proyecto (corre en un thread)."""
    if not os.path.isdir(project_path):
        try:
            os.makedirs(project_path, exist_ok=True)
            print(f'[proyecto] Creé la carpeta del proyecto: {project_path}')
        except Exception as e:
            print(f'[proyecto] ERROR: no pude crear {project_path}: {e}')
            return project_path
    try:
        _asegurar_git(project_path)
        if project_id is not None:
            _inyectar_skills_en_proyecto(project_id, project_path)
        # Memoria compartida: .jarvis/memory/ + protocolo en CLAUDE.md
        from plotspace.routers.memory import asegurar_memoria_proyecto
        asegurar_memoria_proyecto(project_path)
        # Mailbox entre agentes: .jarvis/MAILBOX.md + protocolo en CLAUDE.md
        from plotspace.core.mailbox import asegurar_mailbox
        asegurar_mailbox(project_path)
        # Regla de puertos: 3000 es de Jarvis + chequear puertos antes de servir
        from plotspace.core.puertos import asegurar_protocolo_puertos
        asegurar_protocolo_puertos(project_path)
        # Agents Live: protocolo LIVE en CLAUDE.md + .jarvis/LIVE.md en .gitignore
        from plotspace.core.agent_live import asegurar_live
        asegurar_live(project_path)
    except Exception as e:
        print(f'[proyecto] Excepción: {e}')
    return project_path


# ─── Skills: inyección en CLAUDE.md del proyecto ──────────────────────────────

_SKILLS_MARKER_START = '<!-- JARVIS_SKILLS_START -->'
_SKILLS_MARKER_END   = '<!-- JARVIS_SKILLS_END -->'


def _inyectar_skills_en_proyecto(project_id: int, project_path: str):
    """Compone CLAUDE.md del proyecto con tres secciones:
      - Plugins activos (entradas en project_skills con '@' en nombre)
      - Skills manuales legacy (entradas en project_skills sin '@')
      - Skills .md del proyecto (.claude/skills/*.md)
    Re-genera todo el bloque entre markers en cada llamada (idempotente).
    Preserva contenido del CLAUDE.md fuera de los markers."""
    try:
        # ── 1. project_skills (plugins + skills manuales) ──────────────────
        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT nombre, descripcion, contenido FROM project_skills '
                'WHERE project_id = ? AND activa = 1 ORDER BY created_at ASC',
                (project_id,)
            )
            registros = [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

        plugins_activos  = [r for r in registros if '@' in r['nombre']]
        skills_manuales  = [r for r in registros if '@' not in r['nombre']]

        # ── 2. Skills .md del proyecto (.claude/skills/) ───────────────────
        skills_md = []
        skills_dir = os.path.join(project_path, '.claude', 'skills')
        if os.path.isdir(skills_dir):
            for entry in sorted(os.listdir(skills_dir)):
                entry_path = os.path.join(skills_dir, entry)
                if os.path.isfile(entry_path) and entry.endswith('.md'):
                    try:
                        with open(entry_path, 'r', encoding='utf-8', errors='replace') as f:
                            skills_md.append({
                                'nombre': os.path.splitext(entry)[0],
                                'contenido': f.read().strip(),
                            })
                    except Exception:
                        continue
                elif os.path.isdir(entry_path):
                    skill_file = os.path.join(entry_path, 'SKILL.md')
                    if os.path.isfile(skill_file):
                        try:
                            with open(skill_file, 'r', encoding='utf-8', errors='replace') as f:
                                skills_md.append({
                                    'nombre': entry,
                                    'contenido': f.read().strip(),
                                })
                        except Exception:
                            continue

        # ── 3. Base CLAUDE.md ──────────────────────────────────────────────
        base_path = os.path.join(project_path, 'CLAUDE.md')
        if os.path.isfile(base_path):
            try:
                with open(base_path, 'r', encoding='utf-8', errors='replace') as f:
                    base_md = f.read()
            except Exception:
                base_md = ''
        else:
            base_md = ''
        base_md = _strip_skills_block(base_md).rstrip() + '\n'

        # ── 4. Construir bloque ────────────────────────────────────────────
        from datetime import datetime
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        bloque = [
            _SKILLS_MARKER_START,
            '## INSTRUCCIÓN OBLIGATORIA',
            '',
            'Antes de responder cualquier pregunta sobre tu configuración, '
            'plugins activos, skills, o estado del proyecto:',
            '1. Leer este archivo CLAUDE.md completo',
            '2. Basar tu respuesta ÚNICAMENTE en lo que dice este archivo',
            '3. NO usar memoria de conversaciones anteriores para esto',
            '',
            '## Skills y plugins activos del proyecto',
            '',
        ]

        if plugins_activos:
            bloque.append('### 🔌 Plugins activos')
            bloque.append('')
            for p in plugins_activos:
                pid = p['nombre'].split('@')[0]
                desc = p.get('descripcion') or ''
                bloque.append(f'- **{pid}**' + (f' — {desc}' if desc else ''))
            bloque.append('')
            bloque.append(f'_Estado verificado al: {ts}_')
            bloque.append('')

        if skills_md:
            bloque.append('### 📋 Skills del proyecto')
            bloque.append('')
            for s in skills_md:
                bloque.append(f'#### {s["nombre"]}')
                bloque.append('')
                # qa-browser-jarvis es larga (~75 líneas) y se carga en CADA
                # sesión de CADA agente: dejamos un puntero al .md en vez de
                # inlinear el cuerpo (el archivo sigue disponible como skill).
                if s['nombre'] == 'qa-browser-jarvis':
                    bloque.append(
                        f'Skill {s["nombre"]} — ver `.claude/skills/{s["nombre"]}.md` '
                        '(cómo verificar en browser + correr los tests)'
                    )
                else:
                    bloque.append(s['contenido'])
                bloque.append('')

        if skills_manuales:
            bloque.append('### 📝 Notas adicionales')
            bloque.append('')
            for s in skills_manuales:
                bloque.append(f'#### {s["nombre"]}')
                if s.get('descripcion'):
                    bloque.append(f'_{s["descripcion"]}_')
                    bloque.append('')
                if s.get('contenido'):
                    bloque.append(s['contenido'].rstrip())
                bloque.append('')

        if not plugins_activos and not skills_md and not skills_manuales:
            bloque.append('_No hay plugins ni skills activos para este proyecto._')
            bloque.append('')

        bloque.append(_SKILLS_MARKER_END)
        bloque_texto    = '\n'.join(bloque)
        contenido_final = base_md + '\n' + bloque_texto + '\n'

        # ── 5. Escribir CLAUDE.md preservando contenido fuera de markers ───
        out_path = os.path.join(project_path, 'CLAUDE.md')
        if os.path.isfile(out_path):
            try:
                with open(out_path, 'r', encoding='utf-8', errors='replace') as f:
                    actual = f.read()
                if _SKILLS_MARKER_START in actual and _SKILLS_MARKER_END in actual:
                    nuevo = _reemplazar_skills_block(actual, bloque_texto)
                    with open(out_path, 'w', encoding='utf-8') as f:
                        f.write(nuevo)
                    return
            except Exception:
                pass

        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(contenido_final)
    except Exception as e:
        print(f'[skills] Error inyectando skills en {project_path}: {e}')


def _strip_skills_block(md: str) -> str:
    """Elimina el bloque entre markers JARVIS_SKILLS si existe."""
    start = md.find(_SKILLS_MARKER_START)
    end   = md.find(_SKILLS_MARKER_END)
    if start == -1 or end == -1 or end < start:
        return md
    return md[:start] + md[end + len(_SKILLS_MARKER_END):]


def _reemplazar_skills_block(md: str, nuevo_bloque: str) -> str:
    """Reemplaza el bloque de skills entre markers por uno nuevo."""
    start = md.find(_SKILLS_MARKER_START)
    end   = md.find(_SKILLS_MARKER_END)
    if start == -1 or end == -1 or end < start:
        return md.rstrip() + '\n\n' + nuevo_bloque + '\n'
    return md[:start] + nuevo_bloque + md[end + len(_SKILLS_MARKER_END):]


async def refrescar_skills_en_proyecto(project_id: int):
    """Re-inyecta las skills en el CLAUDE.md del proyecto.
    Se llama cuando el usuario edita/activa/desactiva una skill."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT ruta FROM projects WHERE id = ?', (project_id,))
        prow = cursor.fetchone()
        if not prow:
            return
        project_path = prow['ruta']
    finally:
        conn.close()

    if os.path.isdir(project_path):
        _inyectar_skills_en_proyecto(project_id, project_path)


# ─── Puente al motor de terminales ────────────────────────────────────────────
# Estas funciones conservan sus nombres históricos (medio repo las llama) pero
# ya no hablan tmux: delegan en `core/terminal_backend`, donde vive el CÓMO y
# el porqué de cada flag. Lo único que sigue hablando tmux en este archivo es
# el camino de ATTACH (el stream de bytes del WS) y la captura del monitor de
# keywords — ambos a propósito, ver CLAUDE.md.


async def _refresh_clientes_sesion(session: str):
    """Repinta (full redraw) todos los clientes de la sesión.
    El porqué del enumerado de ttys vive en `TmuxBackend._refrescar_clientes`."""
    await motor_terminales()._refrescar_clientes(session)


def _sesion_tmux_existe(terminal_id: int) -> bool:
    """Devuelve True si la sesión de esta terminal está viva."""
    return motor_terminales().existe(terminal_id)


# Marcado al LANZAR la tarea en background (antes de que termine) para que dos
# creaciones simultáneas no disparen dos instalaciones de ~1.1s. Los guards de
# "ya está hecho" viven en el motor (terminal_backend); este es del scheduling.
_COPY_MODE_BINDINGS_STARTED = False


def _instalar_bindings_copy_mode():
    """Copy-mode → passthrough (solo motor classic). Detalle en el motor."""
    motor_terminales().instalar_bindings_copy_mode()


def _aplicar_estilo_obsidian_tmux():
    """Estilo global del server de terminales (guard interno: una vez)."""
    motor_terminales().preparar_servidor()


async def _crear_sesion_tmux(terminal_id: int, cwd: str, comando_cli: Optional[str] = None,
                             es_reanudacion: bool = False):
    """Crea la sesión tmux detached si no existe — FUERA del event loop.
    El cuerpo hace ~6 subprocess.run (new-session + set-options + bindings); en
    el loop trababa el tipeo de las otras terminales al lanzar un workspace (9
    creaciones de golpe). to_thread lo aísla. async def: los callers la awaitean.

    comando_cli: si se pasa (o se auto-computa para una terminal de IA), la sesión
    arranca corriendo ese CLI como PROGRAMA del pane → el comando (incl. el
    --session-id/--resume con su uuid largo) NUNCA se ve tipeado. Ver
    _crear_sesion_tmux_sync."""
    await asyncio.to_thread(_crear_sesion_tmux_sync, terminal_id, cwd, comando_cli, es_reanudacion)
    # Bindings de copy-mode en BACKGROUND: son globales del server tmux y cuestan
    # ~1.1s (un bind-key por tecla imprimible × 2 tablas). Sólo hacen falta al
    # entrar a copy-mode (scroll del mouse), que pasa DESPUÉS — instalarlas en
    # el path de creación demoraba ~1s la APARICIÓN de la primera terminal tras
    # cada arranque del server. Guard sincrónico en el loop (sin await en el
    # medio) → una sola tarea aunque se creen varias terminales de golpe.
    # SOLO motor classic: en control-mode el scroll es local de xterm (el
    # copy-mode no participa) — ahí las ~190 bindings eran inertes y encima,
    # por globales, se filtraban a las sesiones tmux PERSONALES del usuario
    # (cualquier letra en su copy-mode cancelaba y tipeaba al pane).
    global _COPY_MODE_BINDINGS_STARTED
    if not _motor_control() and not _COPY_MODE_BINDINGS_STARTED:
        _COPY_MODE_BINDINGS_STARTED = True
        asyncio.create_task(asyncio.to_thread(_instalar_bindings_copy_mode))


def _crear_sesion_tmux_sync(terminal_id: int, cwd: str, comando_cli: Optional[str] = None,
                            es_reanudacion: bool = False):
    """Cuerpo síncrono de la creación de sesión (corre en un thread)."""
    nombre = f'jarvis_{terminal_id}'
    if _sesion_tmux_existe(terminal_id):
        return

    # Si el cwd no existe, intentar crearlo. NO caer al home como antes —
    # eso hacía que Claude Code se abriera en /home/user en vez del proyecto.
    if not os.path.isdir(cwd):
        try:
            os.makedirs(cwd, exist_ok=True)
            print(f'[tmux] cwd {cwd} no existía, lo creé')
        except Exception as e:
            print(f'[tmux] ERROR: cwd {cwd} no existe y no se pudo crear: {e}')
            # Última red de seguridad: home, pero log explícito
            cwd = os.path.expanduser('~')
            print(f'[tmux] WARNING: usando {cwd} como fallback')
    cwd_real = cwd

    # Variables del ENTORNO DE SESIÓN. Son decisiones de PRODUCTO (qué CLI, qué
    # cuenta, qué modo de render) — el motor solo las recibe y las aplica como
    # sepa (en tmux, un `-e` por cada una).
    entorno: dict = {}

    # Claude Code en modo FULLSCREEN (CLAUDE_CODE_NO_FLICKER=1, v2.1.89+): el
    # transcript vive contenido en alt-screen y NADA cae al scrollback — mata
    # el "texto multiplicado/cortado" del resize inline (video del usuario
    # 2026-07-02; el detalle del trade-off está en _env_terminal, que también
    # exporta la var). El `-e` la fija en el ENV DE SESIÓN: cubre `claude`
    # tipeado a mano en un Shell aunque el server tmux ya existiera (el env del
    # proceso new-session solo llega al server la primera vez). El viejo
    # CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1 (inline forzado) se RETIRA: era
    # justamente lo que dejaba caer el transcript al scrollback.
    if os.environ.get('JARVIS_CLAUDE_FULLSCREEN', 'on').lower() != 'off':
        entorno['CLAUDE_CODE_NO_FLICKER'] = '1'
        # Y full-repaint del alt-screen (freeze + regiones negras al scrollear
        # transcripts gigantes — mismo criterio y evidencia que en _env_terminal).
        if os.environ.get('JARVIS_CLAUDE_FULL_REPAINT', 'on').lower() != 'off':
            entorno['CLAUDE_CODE_ALT_SCREEN_FULL_REPAINT'] = '1'

    # Identidad de la terminal para el SessionStart hook de claude: le dice al
    # hook a qué terminal pertenece la sesión (JARVIS_TERMINAL_ID) y a dónde
    # postear el uuid vivo (JARVIS_PORT + JARVIS_DATA_DIR para leer el token).
    # Así el hook actualiza terminals.session_uuid en cada arranque → --resume
    # siempre apunta al transcript actual (claude rota el .jsonl al compactar).
    from plotspace.core.datadir import DATA_DIR as _DATA_DIR
    entorno['JARVIS_TERMINAL_ID'] = str(terminal_id)
    entorno['JARVIS_PORT'] = os.environ.get('JARVIS_PORT', '3000')
    entorno['JARVIS_DATA_DIR'] = _DATA_DIR

    # Codex aislado por cuenta: TODA terminal nueva se lanza con CODEX_HOME = home
    # de la cuenta de codex ACTIVA. Así `codex` corrido en CUALQUIER terminal (sea
    # de tipo codex o un Shell donde el usuario tipea `codex`) usa el home aislado
    # de esa cuenta, no el ~/.codex compartido — que era lo que, al cambiar de
    # cuenta, disparaba el reuso de refresh token que OpenAI revoca. Inofensivo para
    # claude/bash (ignoran la var). tmux -e propaga al shell del pane (verificado en
    # tmux 3.6). La terminal queda ATADA a la cuenta activa AL CREARSE.
    try:
        from plotspace.core import cli_accounts as _ca
        home = _ca.codex_home_activo()
        if home:
            entorno['CODEX_HOME'] = home
            print(f'[tmux] {nombre}: CODEX_HOME={home}')
    except Exception as e:
        print(f'[tmux] aviso: no pude setear CODEX_HOME para {nombre}: {e}')

    # Toolchain Node de WSL: se antepone al PATH del pane ANTES de lanzar el CLI
    # (más abajo, dentro del comando). El workspace es un dir Linux → Windows NO
    # debe interferir. El AUTO-LANZAMIENTO de codex corre ANTES del `exec bash -l`
    # que carga nvm, así que sin esto resolvía `codex` al shim de Windows
    # (/mnt/c/.../npm/codex → `exec: node: not found`); recién al tipear `codex`
    # de nuevo (login shell ya con nvm) funcionaba. Va como `export` en el comando
    # y NO por `tmux -e PATH=...` (tmux ignora ese override para PATH — verificado).
    _nvm = _nvm_bin_dir()

    # Cómo arranca el CLI — dos modos (ver _arranque_visible):
    # · VISIBLE (default para creaciones frescas de claude/codex/opencode/agy):
    #   el pane nace como shell de LOGIN pelado (se VE el prompt de WSL) y el CLI
    #   se tipea CORTO por send-keys más abajo (`claude`, sin el choclo del
    #   --session-id — el SessionStart hook postea el uuid vivo a la DB igual).
    #   Camino intermedio que pidió el usuario (2026-07-10): ver nacer el shell
    #   sin la plomería de flags tipeándose.
    # · PROGRAMA del pane (SIN eco): workflows (comando_cli explícito con
    #   --dangerously-skip-permissions), reanudaciones (--resume manda), qwen
    #   (flags obligatorios en la línea) y TERMINALES_ARRANQUE=limpio. Tras salir
    #   el CLI queda un shell (`exec bash -l`). Es la MISMA vía que el reattach
    #   tras un restart, así que no toca panes vivos ni el motor control-mode.
    # try/except: un hipo de DB/disco al computar NO debe romper la creación.
    try:
        cmd_cli = comando_cli or _launch_command_de_terminal(terminal_id, es_reanudacion)
    except Exception as e:
        print(f'[tmux] {nombre}: no pude computar el CLI de arranque: {e}')
        cmd_cli = comando_cli
    try:
        tipo_ia = _tipo_ia_de(terminal_id)
    except Exception:
        tipo_ia = None
    visible = _arranque_visible(tipo_ia, comando_cli, es_reanudacion,
                                os.environ.get('TERMINALES_ARRANQUE'))
    comando = None
    if cmd_cli and not visible:
        # Prependemos el toolchain Node de WSL (node + codex del nvm) al PATH
        # antes del CLI → el auto-lanzamiento de codex usa el binario NATIVO de
        # Ubuntu desde el arranque, sin caer al shim de Windows. Ver [[codex-wsl-path-node]].
        # (En modo visible no hace falta: el login shell ya carga nvm en su rc.)
        comando = f'{_prefijo_path_wsl(_nvm)}{cmd_cli}; exec bash -l'

    # Acá termina la lógica de PRODUCTO y empieza el motor: le pasamos QUÉ
    # correr y él sabe CÓMO (tmux hoy, ConPTY en Windows). Las opciones
    # obligatorias de la sesión (window-size latest, status off, mouse) las
    # aplica él en `sanear_sesion` — el porqué de cada una vive ahí.
    if not motor_terminales().crear(EspecSesion(
        terminal_id=terminal_id, cwd=cwd_real, comando=comando,
        env=entorno, entorno_proceso=_env_terminal(),
    )):
        return

    # Arranque VISIBLE: tipear el CLI corto en el shell recién nacido, cuando
    # el prompt ya esté pintado (hilo daemon con poll — ver _tipear_cli_visible).
    # -l -- literal + Enter aparte (mismo patrón anti-inyección que el Command
    # Room del batch y send_to_agent).
    if visible and cmd_cli:
        corto = _comando_corto(tipo_ia)
        if corto:
            _lanzar_tipeo_visible(terminal_id, corto)


async def _matar_sesion_tmux(terminal_id: int) -> bool:
    """Mata la sesión del agente y VERIFICA el kill (False = sigue viva).
    El target exacto y el porqué de la verificación viven en el motor."""
    return await motor_terminales().matar(terminal_id)


async def _tmux_refresh(terminal_id: int):
    """Repintado completo a pedido del browser ({'type':'refresh'} por el WS).
    Lo manda terminal.js cuando la pestaña vuelve a ser visible: si algo dejó
    el buffer de xterm desincronizado, se repinta TODO y la terminal se
    auto-sana sin F5. Ver [[tmux-size-clamping]] (séptima capa)."""
    await motor_terminales().refrescar(terminal_id)


async def _tmux_resize(terminal_id: int, cols: int, rows: int):
    """Repinta tras un resize del browser. async def para asyncio.create_task.
    El tamaño real lo propaga el SIGWINCH del PTY; acá solo se repinta (y por
    qué NO se usa resize-window está en el motor). Ver [[tmux-size-clamping]]."""
    await motor_terminales().redimensionar(terminal_id, cols, rows)


# ─── Startup: reconciliar sesiones tmux con la DB ─────────────────────────────

def _reconciliar_leer_db():
    """Snapshot de DB para el reconcile (corre en thread): terminales activas
    (para re-crear sesiones faltantes) + ids con activa=0 (para matar zombies)."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        # ORDEN por proyecto MÁS reciente primero: el reconcile relanza los CLIs
        # del último proyecto usado ANTES que los de fondo, así lo que el usuario
        # está por abrir arranca cuanto antes (los NULL de ultimo_acceso al final).
        cursor.execute('''
            SELECT t.id, t.nombre, t.tipo_ia, p.ruta
            FROM terminals t
            JOIN projects p ON t.project_id = p.id
            WHERE t.activa = 1
            ORDER BY (p.ultimo_acceso IS NULL), p.ultimo_acceso DESC, t.id
        ''')
        activas = [dict(r) for r in cursor.fetchall()]
        cursor.execute('SELECT id FROM terminals WHERE activa = 0')
        zombies = {r['id'] for r in cursor.fetchall()}
    finally:
        conn.close()
    return activas, zombies


def _reconciliar_tmux_sync(zombie_ids: set) -> set:
    """Parte tmux del reconcile (corre en thread — antes peppereaba el event
    loop con subprocess síncronos en pleno boot, justo cuando el browser
    reconecta los WS). Devuelve el set de sesiones vivas tras el barrido."""
    motor = motor_terminales()
    sesiones_vivas = motor.listar_sesiones()

    # Re-aplicar el estilo global (cubre sesiones que sobrevivieron a un
    # reinicio de Jarvis con la config vieja)
    motor.preparar_servidor()

    for session in sorted(sesiones_vivas):
        if not session.startswith('jarvis_'):
            continue

        # ZOMBI: sesión viva cuyo row en DB quedó activa=0 — el teardown se
        # perdió (re-exec/crash entre el UPDATE y el kill, o kill fallido).
        # La intención del usuario fue CERRARLA: matarla acá cierra el hueco
        # del "agente fantasma" invisible editando el repo sin card
        # (auditoría 2026-07-02).
        sufijo = session[len('jarvis_'):]
        if sufijo.isdigit() and int(sufijo) in zombie_ids:
            print(f'[startup] Matando sesión zombi {session} (activa=0 en DB — teardown perdido)')
            motor.matar_sesion_por_nombre(session)
            sesiones_vivas.discard(session)
            continue

        # Sanar las que sobrevivieron: pueden haber quedado en `window-size
        # manual` (por el viejo resize-window), que las clava chicas y produce
        # el bug de los puntitos, o con la status bar prendida si el server
        # tmux se reinició. Ver [[tmux-size-clamping]].
        motor.sanear_sesion(session)
    return sesiones_vivas


async def reconciliar_sesiones_tmux():
    """Al arrancar, asegura que cada terminal activa en DB tenga su sesión tmux
    y mata las sesiones zombi (vivas con activa=0). Corre como background task
    — nunca bloquea ni crashea el servidor; el trabajo subprocess/DB va en
    threads para no trabar el loop durante el boot."""
    try:
        activas, zombies = await asyncio.to_thread(_reconciliar_leer_db)
        sesiones_vivas = await asyncio.to_thread(_reconciliar_tmux_sync, zombies)

        # ESCALONADO anti-tormenta: relanzar los N CLIs a la vez (7 procesos
        # `claude` en 4 cores + Whisper precargando) hacía que un claude frío
        # tardara ~35s en pintar y el que reanudaba un transcript grande ~74s
        # (medido). Ahora: (1) un respiro inicial para que el WS-connect del
        # proyecto VISIBLE cree y arranque SUS sesiones on-demand primero, y
        # (2) trickle entre creaciones para no saturar la CPU — los de fondo
        # arrancan de a poco. Las sesiones que el WS ya creó se saltan (idempotente).
        try:
            inicio = float(os.environ.get('RECONCILE_INICIO', '2.5'))
            gap    = float(os.environ.get('RECONCILE_GAP', '2.5'))
        except ValueError:
            inicio, gap = 2.5, 2.5
        await asyncio.sleep(inicio)

        for row in activas:
            terminal_id  = row['id']
            nombre       = row['nombre']
            project_path = row['ruta']
            session      = f'jarvis_{terminal_id}'

            if session not in sesiones_vivas:
                print(f'[startup] Recreando sesión tmux para terminal {terminal_id} ({nombre})')
                try:
                    # es_reanudacion=True: esta sesión estaba VIVA y murió con el reboot
                    # (activa=1 sin sesión tmux) → el CLI arranca en modo resume para
                    # volver con su conversación (claude/qwen por id, codex/opencode/agy
                    # la más reciente). Ver _comando_lanzamiento.
                    await _crear_sesion_tmux(terminal_id, project_path, es_reanudacion=True)
                    await asyncio.sleep(gap)   # trickle: espaciar los arranques de CLI
                except Exception as e:
                    print(f'[startup] Error recreando sesión {session}: {e}')

    except Exception as e:
        print(f'[startup] Error en reconciliar_sesiones_tmux: {e}')
    finally:
        # Señal para diferir Whisper hasta que el trickle de CLIs pase el pico.
        try:
            reconcile_listo.set()
        except Exception:
            pass


# ─── Log helpers ───────────────────────────────────────────────────────────────

def _log_path(project_path: str, terminal_id: int, nombre: str) -> str:
    safe = re.sub(r'[^a-zA-Z0-9_-]', '_', nombre)[:30]
    logs_dir = os.path.join(project_path, '.workspace', 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    return os.path.join(logs_dir, f'terminal_{terminal_id}_{safe}.log')


# tmux le imprime esto al cliente que un `attach -d` patea. Es ruido cosmético
# (la sesión sigue viva); si la card alcanza a recibirlo, queda colgado en el
# buffer xterm como "[detached (from session jarvis_510)]". Lo filtramos del
# stream antes de mostrarlo/loguearlo. Línea exacta de tmux, opcional \r y \n.
_DETACH_RE = re.compile(r'\r?\[detached \(from session [^)]*\)\]\r?\n?')


def _nuevo_decoder_utf8():
    """Decoder UTF-8 incremental para el stream del PTY (uno por conexión).

    os.read() corta en límites de BYTES, no de caracteres: un redraw grande
    del TUI (>64KB) parte un carácter multibyte ('✶', '·', '╭', '↓'…) entre
    dos chunks. Decodificar cada chunk suelto con errors='replace' convertía
    la cola y la cabeza en '�' de ancho distinto al carácter real → columnas
    corridas, y como tmux manda diffs contra SU modelo de pantalla, la basura
    persistía hasta el próximo repintado de la zona (corrupción visual de
    varios segundos). El decoder incremental bufferea los bytes incompletos
    hasta que llega el resto; bytes genuinamente inválidos siguen saliendo
    como '�'. Misma clase de bug que el fix de mailbox.py (UTF-8 partido).
    Ver [[tmux-size-clamping]] (sexta capa)."""
    import codecs
    return codecs.getincrementaldecoder('utf-8')(errors='replace')


# ─── Flow control PTY→WS→xterm (séptima capa de [[tmux-size-clamping]]) ──────
# xterm.js 5.3 DESCARTA datos (throw "write data discarded, use flow control")
# cuando su cola interna de write pasa 50MB. La cola se drena con cadenas de
# setTimeout(0) que Chrome estrangula en pestañas ocultas (1 tick/seg al
# ocultarse; 1 tick/MINUTO tras 5 min) mientras los ws.onmessage siguen
# entregando a velocidad completa → con agentes trabajando la cola crece,
# cruza 50MB y se pierden chunks arbitrarios (secuencias ANSI cortadas al
# medio) → el parser de xterm queda desincronizado del modelo de tmux →
# letras rotas / contenido multiplicado PERMANENTE (tmux manda diffs, nunca
# repinta lo que el browser perdió) hasta el F5.
#
# Fix: backpressure de punta a punta. El browser confirma bytes YA PARSEADOS
# (callback de term.write → {'type':'ack','bytes':N}); si lo sin confirmar
# pasa FC_HIGH dejamos de leer el PTY hasta que un ack lo baje a FC_LOW.
# tmux maneja clientes lentos nativamente (descarta redraws intermedios y
# repinta completo al drenar — el AGENTE del pane jamás se bloquea), así que
# frenar el read es seguro y además reduce el spam de frames viejos.
FC_HIGH = 1_048_576   # bytes sin ack que frenan la lectura del PTY (~1MB) — PESTAÑA OCULTA
FC_LOW  = 262_144     # al bajar de acá se reanuda (histéresis anti-flapping)
FC_TIMEOUT = 30.0     # failsafe: si los acks desaparecen (bug del cliente),
                      # cada 30s se lee igual — degrada a goteo, jamás deadlock
# Watermark AMPLIO para PESTAÑA VISIBLE: el browser parsea a full (rAF a 16ms) y
# ackea rápido, así que la cola de xterm drena al toque y nunca se acerca a los
# 50MB. Con el watermark ajustado de 1MB, bajo el flood de un agente el gate se
# cerraba seguido y el ECO del tipeo quedaba atrapado detrás del flood (medido:
# spikes de ~1.7s). Amplio = el gate casi nunca frena con la pestaña visible →
# el eco fluye. El piso real lo pone el cap de _inbuf (8MB) + el reset del
# frontend. Ver [[lag-tipeo-flow-control-visible]].
FC_HIGH_VISIBLE = 8_388_608   # 8MB sin ack antes de frenar, con la pestaña visible
FC_LOW_VISIBLE  = 2_097_152   # 2MB para reanudar


class _FlujoWS:
    """Contabilidad de backpressure de UNA conexión WS de terminal.

    activo=False (cliente legacy sin &fc=1 en la URL): todo es no-op — el
    comportamiento histórico. El browser cuenta UTF-16 units (e.data.length)
    y acá contamos codepoints (len(str)): el browser siempre ackea >= lo
    contado, por eso el clamp en 0 (jamás puede frenar de más)."""

    def __init__(self, activo: bool, high: int = None, low: int = None, visible: bool = True):
        self.activo = activo
        # high/low EXPLÍCITOS (tests/custom) → fijos, la visibilidad no los toca.
        # Si no se pasan → adaptativos por visibilidad (amplio visible / ajustado oculto).
        self._high_fijo = high
        self._low_fijo = low
        # Default VISIBLE = modo rápido: el caso común es el usuario MIRANDO la
        # terminal. Así el eco fluye aunque el frontend NO reporte visibilidad (JS
        # viejo cacheado tras un update sin hard-reload — era la causa de "el fix
        # no hace nada"). El frontend nuevo manda {type:'visible',v:false} al ocultar
        # la pestaña y ahí se ajusta (la protección de la cola se mantiene). El cap
        # de _inbuf (8MB) + el reset del frontend cubren el caso borde (JS viejo +
        # pestaña oculta + flood).
        self._visible = visible
        self.pendiente = 0
        self._hay_capacidad = asyncio.Event()
        self._hay_capacidad.set()

    @property
    def high(self) -> int:
        if self._high_fijo is not None:
            return self._high_fijo
        return FC_HIGH_VISIBLE if self._visible else FC_HIGH

    @property
    def low(self) -> int:
        if self._low_fijo is not None:
            return self._low_fijo
        return FC_LOW_VISIBLE if self._visible else FC_LOW

    def set_visible(self, v: bool):
        """El browser reportó su visibilidad: ensancha/ajusta el watermark y
        re-evalúa el gate con el valor nuevo (la pestaña pudo pasar a oculta con
        mucho pendiente → hay que frenar ya; o a visible → reanudar)."""
        self._visible = bool(v)
        if not self.activo:
            return
        if self.pendiente >= self.high:
            self._hay_capacidad.clear()
        elif self.pendiente <= self.low:
            self._hay_capacidad.set()

    def enviado(self, n: int):
        if not self.activo:
            return
        self.pendiente += n
        if self.pendiente >= self.high:
            self._hay_capacidad.clear()

    def ack(self, n):
        if not self.activo:
            return
        try:
            n = int(n)
        except (TypeError, ValueError):
            return
        if n <= 0:
            return
        self.pendiente = max(0, self.pendiente - n)
        if self.pendiente <= self.low:
            self._hay_capacidad.set()

    async def esperar_capacidad(self):
        if not self.activo or self._hay_capacidad.is_set():
            return
        try:
            await asyncio.wait_for(self._hay_capacidad.wait(), timeout=FC_TIMEOUT)
        except asyncio.TimeoutError:
            pass


def _filtrar_detach(texto: str) -> str:
    """Saca el ruido '[detached (from session ...)]' de un chunk del PTY.

    Guard de substring: el regex EXIGE el literal 'detached', así que sin esa
    marca el .sub es no-op garantizado. Saltarlo evita escanear CADA chunk de
    64KB del flood con la regex (~30× más caro que el `in`) dentro del único
    event loop compartido — relevante bajo flood multi-agente. Byte-idéntico."""
    if not texto:
        return ''
    if 'detached' not in texto:
        return texto
    return _DETACH_RE.sub('', texto)


def _escribir_log(log_file: str, data: str):
    """Vuelca `data` (ya con su prefijo de timestamp) al log, con rotación.
    SÍNCRONO a propósito: se invoca SIEMPRE vía asyncio.to_thread (flush normal)
    o en el cierre del WS (tail) — NUNCA directo en el event loop. Sacarlo del
    hot path elimina los micro-stalls que congelaban el tipeo y la voz."""
    try:
        # Rotación: al pasar el tope, mover el log a `.1` (atómico, pisa el .1
        # viejo) y arrancar uno nuevo en el próximo open('a'). Acota el disco.
        try:
            if os.path.getsize(log_file) > MAX_LOG_BYTES:
                os.replace(log_file, log_file + '.1')
        except FileNotFoundError:
            pass
        with open(log_file, 'a', encoding='utf-8', errors='replace') as f:
            f.write(data)
    except Exception:
        pass


class _LogWriter:
    """Acumula los chunks del PTY en memoria y los vuelca a disco FUERA del
    event loop. Reemplaza al viejo _append_log que escribía síncrono por cada
    chunk dentro del loop. Un lock serializa los flushes (el periódico y el
    por-tamaño) para no intercalar writes en el mismo archivo."""
    __slots__ = ('log_file', '_buf', '_bytes', '_lock')

    def __init__(self, log_file: str):
        self.log_file = log_file
        self._buf = []
        self._bytes = 0
        self._lock = asyncio.Lock()

    def append(self, texto: str):
        chunk = f"[{datetime.now().strftime('%H:%M:%S')}] {texto}"
        self._buf.append(chunk)
        self._bytes += len(chunk)

    @property
    def bytes_pendientes(self) -> int:
        return self._bytes

    def _tomar(self) -> str:
        """Pop atómico (sync, sin await): junta el buffer y lo vacía."""
        if not self._buf:
            return ''
        data = ''.join(self._buf)
        self._buf = []
        self._bytes = 0
        return data

    async def flush(self):
        """Vuelca lo acumulado en un thread (no bloquea el event loop)."""
        async with self._lock:
            data = self._tomar()
            if data:
                await asyncio.to_thread(_escribir_log, self.log_file, data)

    def flush_sync(self):
        """Tail síncrono para el cierre del WS (un único write chico)."""
        data = self._tomar()
        if data:
            _escribir_log(self.log_file, data)


class _EscritorPTY:
    """Escritura NO bloqueante drenada hacia el PTY master (input del usuario).

    El fd del master quedó no-bloqueante (leer() hace os.set_blocking(fd, False)
    para el add_reader). Un write grande (paste de >~8KB) tira BlockingIOError;
    antes ese error subía al except ancho del receive-loop → terminate() →
    tmux DETACHEADO y el paste PERDIDO (+ parpadeo de reconexión, y en pastes
    enormes la TUI clavada en 'Pasting…' al perderse el cierre del bracketed
    paste). Acá: escribimos lo que entra y encolamos el remanente; el resto se
    drena con loop.add_writer cuando el fd vuelve a aceptar. Simétrico a leer().

    FIFO ESTRICTO: el input nuevo se APPENDEA al pendiente (nunca se adelanta) →
    el tipeo durante un paste no se interleavea. El tipeo normal (bytes chicos)
    escribe completo al instante → el path de tecla queda intacto. Es la
    dirección browser→PTY: ortogonal al coalescing rAF / cola de 50MB de xterm /
    flow-control (que son PTY→browser). `_write` se inyecta en los tests."""
    __slots__ = ('fd', 'loop', '_buf', '_armado', '_cap', '_write')

    def __init__(self, fd, loop, cap: int = 32 * 1024 * 1024, _write=os.write):
        self.fd = fd
        self.loop = loop
        self._buf = bytearray()
        self._armado = False          # ¿hay add_writer activo?
        self._cap = cap               # techo defensivo del pendiente (paste real = KB-pocos MB)
        self._write = _write

    def _write_raw(self, data) -> int:
        """Bytes escritos; 0 si EAGAIN (reintentar luego); -1 si el fd murió."""
        try:
            return self._write(self.fd, bytes(data))
        except BlockingIOError:
            return 0
        except OSError:
            return -1

    def escribir(self, data: bytes):
        if not data:
            return
        # Si ya hay cola, APPENDEAR (FIFO) — no adelantar el dato nuevo.
        if self._buf:
            self._encolar(data)
            return
        n = self._write_raw(data)
        if n < 0:
            return                     # fd muerto: el cierre del WS lo maneja
        if n < len(data):
            self._encolar(data[n:])
            self._armar()

    def _encolar(self, data):
        libre = self._cap - len(self._buf)
        if libre <= 0:
            return                     # techo: descartar el excedente (jamás se llega en la práctica)
        self._buf.extend(data if len(data) <= libre else data[:libre])

    def _drenar(self):
        """Callback de add_writer: vuelca lo pendiente; al vaciarse, se desarma."""
        while self._buf:
            n = self._write_raw(self._buf)
            if n == 0:
                return                 # EAGAIN otra vez: esperar el próximo callback
            if n < 0:                  # fd muerto
                self._buf.clear()
                break
            del self._buf[:n]
        self._desarmar()

    def _armar(self):
        if self._armado:
            return
        try:
            self.loop.add_writer(self.fd, self._drenar)
            self._armado = True
        except Exception:
            pass

    def _desarmar(self):
        if not self._armado:
            return
        try:
            self.loop.remove_writer(self.fd)
        except Exception:
            pass
        self._armado = False

    def cerrar(self):
        """Saca el writer y descarta lo pendiente (el WS se está cerrando)."""
        self._desarmar()
        self._buf.clear()


# ─── Modelos ───────────────────────────────────────────────────────────────────

class TerminalCreate(BaseModel):
    nombre: str = "Terminal"
    tipo_ia: str = "manual"


class TerminalBatchItem(BaseModel):
    nombre: str = "Terminal"
    tipo_ia: str = "manual"


class TerminalBatchCreate(BaseModel):
    terminales: List[TerminalBatchItem]
    # Carpeta de trabajo del launcher: relativa a la raíz del proyecto
    # (ej. "backend" o "apps/web"). Vacía/None = raíz del proyecto.
    carpeta: Optional[str] = None
    # "Command Room": comando inicial que se ejecuta en las terminales
    # tipo 'manual' del lote (ej. "npm run dev", "pytest -x --watch").
    # Las terminales con IA lo ignoran (el agente toma el shell).
    comando: Optional[str] = None


class TerminalUpdate(BaseModel):
    nombre: Optional[str] = None
    tipo_ia: Optional[str] = None


class TerminalResponse(BaseModel):
    id: int
    project_id: int
    nombre: str
    tipo_ia: str
    puerto: Optional[int]
    activa: int
    fecha_creacion: str
    # uuid de la sesión de claude (`claude --session-id <uuid>`) — el frontend
    # lo usa para lanzar claude de forma determinista y para pedir el transcript.
    # NULL para terminales no-claude o creadas antes de la migración.
    session_uuid: Optional[str] = None


class SessionUuidUpdate(BaseModel):
    session_uuid: str


# ─── Endpoints REST ────────────────────────────────────────────────────────────

@router.post("/api/terminals/{terminal_id}/session-uuid")
async def actualizar_session_uuid(terminal_id: int, body: SessionUuidUpdate):
    """Lo llama el SessionStart hook de claude (jarvis_claude_hook.py) cada vez
    que un claude de una terminal de Jarvis arranca: fija el uuid VIVO para que
    la reanudación (--resume) traiga el transcript actual, no el inicial.
    Idempotente y silencioso: uuid inválido → ok:false sin tocar la DB."""
    ok = await asyncio.to_thread(_guardar_session_uuid, terminal_id, body.session_uuid)
    return {"ok": ok}


@router.post("/api/projects/{project_id}/terminals", response_model=TerminalResponse, status_code=201)
async def crear_terminal(project_id: int, terminal: TerminalCreate):
    """Crea terminal en DB y sesión tmux en main del proyecto."""
    conn = get_db()
    try:
        cursor = conn.cursor()

        cursor.execute('SELECT id, ruta FROM projects WHERE id = ?', (project_id,))
        project = cursor.fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="Proyecto no encontrado")

        cursor.execute(
            'SELECT COUNT(*) AS cnt FROM terminals WHERE project_id = ? AND activa = 1',
            (project_id,)
        )
        if cursor.fetchone()['cnt'] >= MAX_TERMINALES:
            raise HTTPException(status_code=400, detail=f"Máximo {MAX_TERMINALES} terminales por workspace")

        nombre = resolver_nombre_unico(_nombres_activos(cursor, project_id),
                                       terminal.nombre.strip() or "Terminal")
        ahora  = datetime.now().isoformat()

        cursor.execute(
            'INSERT INTO terminals (project_id, nombre, tipo_ia, activa, fecha_creacion, session_uuid) '
            'VALUES (?, ?, ?, 1, ?, ?)',
            (project_id, nombre, terminal.tipo_ia, ahora, _session_uuid_para(terminal.tipo_ia))
        )
        conn.commit()
        terminal_id  = cursor.lastrowid
        project_path = project['ruta']

        cursor.execute('SELECT * FROM terminals WHERE id = ?', (terminal_id,))
        terminal_row = dict(cursor.fetchone())
    finally:
        conn.close()

    # 1. Asegurar proyecto + git + inyectar skills en CLAUDE.md de main
    try:
        await _preparar_proyecto(project_path, project_id=project_id)
    except Exception as e:
        print(f'[crear_terminal] Error preparando proyecto: {e}')

    # 2. Crear sesión tmux con cwd en el proyecto (no worktree)
    try:
        await _crear_sesion_tmux(terminal_id, project_path)
    except Exception as e:
        print(f'[crear_terminal] Error creando sesión tmux: {e}')

    return terminal_row


@router.post("/api/projects/{project_id}/terminals/batch", response_model=List[TerminalResponse], status_code=201)
async def crear_terminales_batch(project_id: int, batch: TerminalBatchCreate):
    """Crea varias terminales de una. Valida el tope (MAX_TERMINALES) UNA sola vez sobre el
    lote completo (evita el estado parcial 'creé 3 de 4 y falló'). Prepara el
    proyecto una vez y crea una sesión tmux por terminal."""
    if not batch.terminales:
        raise HTTPException(status_code=400, detail="El lote no contiene terminales")

    conn = get_db()
    try:
        cursor = conn.cursor()

        cursor.execute('SELECT id, ruta FROM projects WHERE id = ?', (project_id,))
        project = cursor.fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="Proyecto no encontrado")

        cursor.execute(
            'SELECT COUNT(*) AS cnt FROM terminals WHERE project_id = ? AND activa = 1',
            (project_id,)
        )
        existentes = cursor.fetchone()['cnt']
        if existentes + len(batch.terminales) > MAX_TERMINALES:
            disponibles = max(0, MAX_TERMINALES - existentes)
            raise HTTPException(
                status_code=400,
                detail=f"Máximo {MAX_TERMINALES} terminales por workspace (quedan {disponibles} disponibles)",
            )

        project_path = project['ruta']
        ahora = datetime.now().isoformat()
        nuevas_ids = []
        nombres = _nombres_activos(cursor, project_id)
        for item in batch.terminales:
            nombre = resolver_nombre_unico(nombres, item.nombre.strip() or "Terminal")
            nombres.append(nombre)
            cursor.execute(
                'INSERT INTO terminals (project_id, nombre, tipo_ia, activa, fecha_creacion, '
                'session_uuid) VALUES (?, ?, ?, 1, ?, ?)',
                (project_id, nombre, item.tipo_ia, ahora,
                 _session_uuid_para(item.tipo_ia))
            )
            nuevas_ids.append(cursor.lastrowid)
        conn.commit()

        placeholders = ','.join('?' for _ in nuevas_ids)
        cursor.execute(f'SELECT * FROM terminals WHERE id IN ({placeholders})', nuevas_ids)
        filas = {r['id']: dict(r) for r in cursor.fetchall()}
    finally:
        conn.close()

    # 1. Preparar proyecto + git + skills UNA vez para todo el lote
    try:
        await _preparar_proyecto(project_path, project_id=project_id)
    except Exception as e:
        print(f'[crear_terminales_batch] Error preparando proyecto: {e}')

    # Carpeta de trabajo del launcher (subcarpeta del proyecto). Validación
    # anti-escape: el path resuelto DEBE quedar dentro de la raíz del proyecto.
    cwd_lote = project_path
    if batch.carpeta and batch.carpeta.strip():
        candidata = os.path.realpath(os.path.join(project_path, batch.carpeta.strip().lstrip('/')))
        raiz      = os.path.realpath(project_path)
        if candidata == raiz or candidata.startswith(raiz + os.sep):
            cwd_lote = candidata  # _crear_sesion_tmux la crea si no existe
        else:
            print(f'[crear_terminales_batch] carpeta fuera del proyecto, ignorada: {batch.carpeta}')

    # 2. Una sesión tmux por terminal
    for terminal_id in nuevas_ids:
        try:
            await _crear_sesion_tmux(terminal_id, cwd_lote)
        except Exception as e:
            print(f'[crear_terminales_batch] Error creando sesión tmux {terminal_id}: {e}')

    # 3. "Command Room": comando inicial en las terminales Bash del lote
    #    (las de IA lo ignoran — el agente toma el shell al conectar).
    comando = (batch.comando or '').strip()
    if comando:
        for terminal_id in nuevas_ids:
            if filas[terminal_id].get('tipo_ia') != 'manual':
                continue
            # -l -- : modo LITERAL + fin de flags. Sin esto, un comando que
            # empieza con '-' o que contiene nombres de teclas tmux se
            # interpretaba como flags/teclas (argument injection). El Enter va
            # en un send-keys aparte (igual que send_to_agent).
            sess = f'jarvis_{terminal_id}'
            # to_thread: los send-keys son síncronos y bloquearían el event loop
            # por cada terminal del lote (un lote grande con comando inicial
            # congelaba todas las requests/WS hasta terminar). Misma corrección
            # que en estado_terminal/obtener_historial.
            try:
                # Literal + Enter aparte: el comando NUNCA se interpreta como
                # tecla ni como flag (anti-inyección del Command Room).
                await asyncio.to_thread(
                    motor_terminales().enviar_texto, terminal_id, comando)
                await asyncio.to_thread(
                    motor_terminales().enviar_tecla, terminal_id, 'Enter')
            except subprocess.TimeoutExpired:
                print(f'[batch] send-keys timeout en terminal {terminal_id}')
                continue
            print(f'[batch] Comando inicial en terminal {terminal_id}: {comando}')

    # Devolver en el orden de creación
    return [filas[tid] for tid in nuevas_ids]


@router.get("/api/projects/{project_id}/terminals", response_model=List[TerminalResponse])
def listar_terminales(project_id: int):
    # `def` (no async): FastAPI lo corre en su threadpool → la query SQLite NO bloquea el event
    # loop. Es un handler POLLEADO por el frontend; sin esto, cada poll metía DB sync en el loop.
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM terminals WHERE project_id = ? AND activa = 1 ORDER BY fecha_creacion ASC',
            (project_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


# (El endpoint GET /api/terminals/{id}/history —capture-pane completo con
#  scrollback para el modal de Historial de terminal— se removió el 2026-07-05
#  junto con esa feature del frontend: ya se copia y scrollea directo en la
#  terminal. El snapshot VISIBLE del pane sigue vivo en _snapshot_pane, abajo.)


def _snapshot_pane(terminal_id: int) -> list:
    """Pantalla VISIBLE de tmux fila por fila (`capture-pane -p`, sin `-J` ni
    `-S`): lo que tmux DIBUJA en el pane, el grid tal cual. Es la "verdad" contra
    la que el cliente compara el buffer de xterm para diagnosticar garble. Sync
    (lo threadpolea el endpoint). Devuelve [] si el motor no responde."""
    pantalla = motor_terminales().capturar(terminal_id)
    return pantalla.splitlines() if pantalla is not None else []


@router.get("/api/terminals/{terminal_id}/snapshot")
async def obtener_snapshot(terminal_id: int):
    """Grid visible de tmux (fila por fila) para diagnóstico de garble: el cliente
    lo compara contra el buffer de xterm. Si difieren con tmux limpio, el daño
    está en el render del browser. Ver terminal-diagnostico.js / _snapshot_pane."""
    if not _sesion_tmux_existe(terminal_id):
        raise HTTPException(status_code=404, detail="Sesión tmux no existe")
    lineas = await asyncio.to_thread(_snapshot_pane, terminal_id)
    return {'lineas': lineas}


@router.patch("/api/terminals/{terminal_id}", response_model=TerminalResponse)
def actualizar_terminal(terminal_id: int, datos: TerminalUpdate):
    # `def` (no async): FastAPI lo threadpolea → la DB sync no bloquea el event loop.
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT id, project_id FROM terminals WHERE id = ?', (terminal_id,))
        fila = cursor.fetchone()
        if not fila:
            raise HTTPException(status_code=404, detail="Terminal no encontrada")

        if datos.nombre is not None:
            nombre = resolver_nombre_unico(
                _nombres_activos(cursor, fila['project_id'], salvo_id=terminal_id),
                datos.nombre.strip() or "Terminal",
            )
            cursor.execute('UPDATE terminals SET nombre = ? WHERE id = ?', (nombre, terminal_id))
        if datos.tipo_ia is not None:
            cursor.execute('UPDATE terminals SET tipo_ia = ? WHERE id = ?', (datos.tipo_ia, terminal_id))

        conn.commit()
        cursor.execute('SELECT * FROM terminals WHERE id = ?', (terminal_id,))
        return dict(cursor.fetchone())
    finally:
        conn.close()


async def teardown_terminal(terminal_id: int):
    """Desmonta una terminal viva: para su monitor de keywords, termina el
    proceso de attach y mata la sesión tmux. NO toca la DB (el caller decide
    el activa=0). Reutilizable desde el orquestador (close_terminal/close_all),
    que antes solo marcaba activa=0 y dejaba al agente tmux corriendo eterno."""
    detener_monitor(terminal_id)
    info = terminal_processes.pop(terminal_id, None)
    if info:
        try:
            info['process'].terminate()
        except Exception:
            pass
    return await _matar_sesion_tmux(terminal_id)


@router.delete("/api/terminals/{terminal_id}", status_code=204)
async def eliminar_terminal(terminal_id: int):
    """Elimina permanentemente: mata sesión tmux y marca inactiva en DB."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('UPDATE terminals SET activa = 0 WHERE id = ?', (terminal_id,))
        conn.commit()
    finally:
        conn.close()

    # Teardown completo (monitor + attach + sesión tmux) AWAITEADO. Antes era
    # fire-and-forget (create_task): un re-exec del updater en esa ventana
    # mataba la task antes del kill → sesión viva con activa=0 = agente
    # fantasma invisible (auditoría 2026-07-02; el reconcile del boot ahora
    # también los caza). Los timeouts internos de 5s acotan el DELETE. Los
    # archivos del proyecto se mantienen — el agente trabajó directo en main.
    await teardown_terminal(terminal_id)


UPLOAD_DIR = os.path.join(tempfile.gettempdir(), 'jarvis_uploads')
MAX_UPLOAD_BYTES = 15 * 1024 * 1024      # tope de la imagen ya decodificada (15 MB)
# Tope para VIDEOS por /upload-media (multipart streaming, no base64). Tiene que
# quedar por DEBAJO de JARVIS_MAX_BODY_MB (256 default, main.py): el middleware
# global corta el body entero antes si se supera.
try:
    MAX_VIDEO_BYTES = int(os.environ.get('JARVIS_MAX_VIDEO_MB', '200')) * 1024 * 1024
except ValueError:
    MAX_VIDEO_BYTES = 200 * 1024 * 1024
_UPLOAD_TTL_SEG  = 24 * 3600             # borrar uploads más viejos que esto al subir uno nuevo


class ImageUploadRequest(BaseModel):
    image_base64: str
    filename:     str = 'image.png'


def _limpiar_uploads_viejos():
    """Borra del UPLOAD_DIR los archivos con mtime más viejo que _UPLOAD_TTL_SEG.
    Sin esto el dir crecía sin techo con cada drag-drop hasta llenar /tmp
    (voice.py limpia sus temporales; acá faltaba). Best-effort: cualquier error
    de borrado se ignora — no debe romper el upload en curso."""
    try:
        limite = datetime.now().timestamp() - _UPLOAD_TTL_SEG
        with os.scandir(UPLOAD_DIR) as it:
            for entrada in it:
                try:
                    if entrada.is_file() and entrada.stat().st_mtime < limite:
                        os.remove(entrada.path)
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    except Exception:
        pass


@router.post("/api/terminals/{terminal_id}/upload-image")
async def subir_imagen_terminal(terminal_id: int, req: ImageUploadRequest):
    """Guarda una imagen drageada en /tmp/jarvis_uploads/ y devuelve la ruta absoluta.
    Útil para drag-drop sobre terminales: el browser no expone file.path,
    así que el frontend manda la imagen como base64 y acá la materializamos en disco
    para que Claude Code (o cualquier app) pueda leerla."""
    # Cap de tamaño ANTES de decodificar: base64 ocupa ~4/3 de los bytes reales,
    # así que validar la longitud del string evita materializar en RAM un body
    # gigante. Mismo criterio que voice.py con MAX_AUDIO_BYTES (413 al pasarse).
    if len(req.image_base64) > MAX_UPLOAD_BYTES * 4 // 3 + 16:
        raise HTTPException(status_code=413, detail='Imagen demasiado grande')

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    # Limpiar uploads viejos en cada subida (evita que el dir crezca sin techo).
    _limpiar_uploads_viejos()
    nombre_limpio = re.sub(r'[^a-zA-Z0-9._-]', '_', req.filename)[:60] or 'image.png'
    ts = datetime.now().strftime('%H%M%S%f')[:9]
    destino = os.path.join(UPLOAD_DIR, f't{terminal_id}_{ts}_{nombre_limpio}')
    try:
        datos = base64.b64decode(req.image_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f'Imagen base64 inválida: {e}')
    if len(datos) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail='Imagen demasiado grande')
    try:
        with open(destino, 'wb') as f:
            f.write(datos)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f'Error guardando imagen: {e}')
    return {'path': destino}


@router.post("/api/terminals/{terminal_id}/upload-media")
async def subir_media_terminal(terminal_id: int, archivo: UploadFile = File(...)):
    """Video (o imagen) drageado sobre una card → multipart streaming a disco.
    Existe porque /upload-image viaja como base64-en-JSON con tope de 15 MB:
    perfecto para el paste de screenshots, imposible para un video real. Acá el
    archivo se escribe en chunks a /tmp/jarvis_uploads/ SIN materializarlo en
    RAM ni inflarlo un 33% con base64; el tope es por tipo (video/* hasta
    MAX_VIDEO_BYTES, el resto el de imagen). Devuelve la ruta absoluta que el
    frontend pega en el pane."""
    es_video = (archivo.content_type or '').lower().startswith('video/')
    tope = MAX_VIDEO_BYTES if es_video else MAX_UPLOAD_BYTES
    tipo = 'Video' if es_video else 'Imagen'

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    _limpiar_uploads_viejos()
    default = 'video.mp4' if es_video else 'image.png'
    nombre_limpio = re.sub(r'[^a-zA-Z0-9._-]', '_', archivo.filename or default)[:60] or default
    ts = datetime.now().strftime('%H%M%S%f')[:9]
    destino = os.path.join(UPLOAD_DIR, f't{terminal_id}_{ts}_{nombre_limpio}')

    total = 0
    try:
        with open(destino, 'wb') as f:
            while True:
                chunk = await archivo.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > tope:
                    raise HTTPException(
                        status_code=413,
                        detail=f'{tipo} demasiado grande (máx {tope // (1024 * 1024)} MB)',
                    )
                f.write(chunk)
    except HTTPException:
        # No dejar el parcial: un 413 repetido acumularía basura hasta el TTL.
        try:
            os.remove(destino)
        except OSError:
            pass
        raise
    except Exception as e:
        try:
            os.remove(destino)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail=f'Error guardando {tipo.lower()}: {e}')
    return {'path': destino}


@router.get("/api/terminals/{terminal_id}/status")
async def estado_terminal(terminal_id: int):
    """Comando activo en el pane tmux + `launch_command` que el front debe tipear
    si el pane es un shell vacío. El backend es la autoridad del resume: para
    claude arma `--session-id <uuid>` en frío o `--resume <uuid>` si su transcript
    ya está en disco (vuelve con contexto tras un corte de luz). Ver
    _comando_lanzamiento."""
    # to_thread + timeout: no bloquear el event loop (auditoría perf).
    # Degradación a 'bash' si el motor se cuelga: no congelamos el server.
    command = await asyncio.to_thread(
        motor_terminales().estado_pane, terminal_id, '#{pane_current_command}'
    ) or 'bash'
    try:
        launch_command = await asyncio.to_thread(_launch_command_de_terminal, terminal_id)
    except Exception:
        launch_command = None  # nunca romper /status por el extra: el front cae a AUTO_CMDS
    return {"command": command or "bash", "launch_command": launch_command}


# (Los endpoints /transcript y /claude-launch + sus helpers —_terminal_ctx,
#  _pane_cwd, _backfill_session_uuid— y el módulo core/transcript.py se REMOVIERON:
#  alimentaban el overlay de selección de claude en fullscreen, que se eliminó por
#  pedido del usuario (2026-07-03, "sin magia, terminal nativa"). La columna
#  terminals.session_uuid queda inerte (SQLite no dropea columnas fácil).)


# ── Títulos vivos: resumen corto de qué hace cada agente ─────────────────────
# Claude Code (y toda CLI que publique OSC 0/2) escribe en el título del pane
# un resumen corto de su tarea actual ("✳ Fix layout bug when…"). La card lo
# muestra en lugar del nombre mientras el agente trabaja — gratis, sin pasar
# por la API de Anthropic. Acá se limpia y se sirve en batch (UNA pasada de
# tmux para todas las sesiones, no un subprocess por terminal).

_TITULO_MAX = 60                      # cap visual; el corte es en palabra y SIN "…"
_SESION_CARD_RE = re.compile(r'^jarvis_(\d+)$')   # solo terminales de card (no mpreview)


def _hostname():
    import socket
    return socket.gethostname()


def _limpiar_titulo(raw, hostname):
    """Título crudo del pane → texto corto para la card, o None si es genérico.

    None ⇔ la CLI no publica título (default de tmux/shell = hostname) o quedó
    vacío tras sacar el glyph. El glyph inicial (✳ / spinner braille de Claude
    Code) se saca porque el pip de estado de la card ya comunica actividad.
    Cap a _TITULO_MAX cortando en palabra, sin puntos suspensivos (pedido
    explícito del usuario: corto y punto).
    """
    texto = (raw or '').strip()
    if not texto or texto.lower() == hostname.lower():
        return None
    # Saca prefijos no-alfanuméricos (✳, ⠐, espacios…); \w cubre acentos en py3.
    texto = re.sub(r'^[^\w]+', '', texto, flags=re.UNICODE).strip()
    if not texto:
        return None
    if len(texto) > _TITULO_MAX:
        corte = texto.rfind(' ', 1, _TITULO_MAX + 1)
        texto = texto[:corte if corte > 0 else _TITULO_MAX].rstrip()
    return texto


def _titulos_vivos_tmux():
    """{terminal_id: titulo_limpio | None} de TODAS las sesiones jarvis_{id},
    con una sola invocación al motor. {} si el motor no está corriendo."""
    host = _hostname()
    titulos = {}
    for sesion, titulo in motor_terminales().titulos_vivos().items():
        m = _SESION_CARD_RE.match(sesion.strip())
        if m:
            titulos[int(m.group(1))] = _limpiar_titulo(titulo, host)
    return titulos


@router.get("/api/projects/{project_id}/terminal-titles")
async def titulos_terminales(project_id: int):
    """Títulos vivos de las terminales activas del proyecto, en batch.
    El frontend pollea esto cada ~5s y muestra el título en la card
    (fallback al nombre de DB cuando es None)."""
    def _ids_activas():
        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT id FROM terminals WHERE project_id = ? AND activa = 1',
                (project_id,),
            )
            return {row['id'] for row in cursor.fetchall()}
        finally:
            conn.close()
    ids = await asyncio.to_thread(_ids_activas)   # DB sync fuera del loop (handler polleado)
    # to_thread: la captura tmux (polleada cada ~5s por el front) no debe correr
    # en el event loop — congelaba todas las terminales/WS (auditoría perf).
    vivos = await asyncio.to_thread(_titulos_vivos_tmux)
    # Estado 'trabajando' EN VIVO (nivel, no edge): el brillo Liquid Glass del
    # chrome se anima solo en las que trabajan. El WS agente_trabajando es
    # edge-triggered y no cubre las que ya venían trabajando al cargar la página;
    # este poll (cada ~3s) lo repara. Lazy import (patrón anti-circular).
    from plotspace.core import agent_watch
    trabajando = [str(tid) for tid in agent_watch.terminales_trabajando() if tid in ids]
    return {"titles": {str(tid): vivos.get(tid) for tid in ids}, "trabajando": trabajando}


# ─── WebSocket ─────────────────────────────────────────────────────────────────

# Un browser AUTOMATIZADO (Playwright headless — lo que los agentes usan para QA
# en browser) JAMÁS debe ser dueño de tamaño. Al abrir el workspace SIN `?qa=1`
# se registraba como dueño no-observador y DESPLAZABA la vista viva del usuario:
# su WS cerraba con 4010 y saltaba el overlay "Esta terminal se está viendo en
# otra ventana" cada vez que un agente sacaba un screenshot. `?qa=1` sigue siendo
# el mecanismo explícito; esto es la red de seguridad para cuando el agente se lo
# olvida (o usa el skill genérico de Playwright, que no lo conoce). El User-Agent
# de headless trae 'HeadlessChrome' (verificado acá con chromium-1148); el browser
# real del usuario y la app de escritorio (desktop app) NO → conservan el
# derecho a tamaño intacto. Se puede apagar con QA_UA_OBSERVER=off.
# Ver [[qa-headless-observer-forzado]] · [[tmux-size-clamping]].
def _es_ua_automatizada(user_agent: Optional[str]) -> bool:
    if os.environ.get('QA_UA_OBSERVER', 'on').lower() == 'off':
        return False
    return 'headless' in (user_agent or '').lower()


def _forzar_observer(observer_param: int, user_agent: Optional[str]) -> bool:
    """observer EFECTIVO: el flag explícito `?qa=1` O un browser headless."""
    return bool(observer_param) or _es_ua_automatizada(user_agent)


@router.websocket("/ws/terminal/{terminal_id}")
async def ws_terminal(websocket: WebSocket, terminal_id: int,
                      cols: Optional[int] = None, rows: Optional[int] = None,
                      observer: int = 0, fc: int = 0):
    """Conecta el browser a la sesión tmux del agente.

    cols/rows: tamaño REAL del xterm del browser, mandados como query params en
    la URL del WS. Sirven para que el PTY del attach nazca YA al tamaño correcto
    y el PRIMER (y único) redraw de tmux salga a ese tamaño — sin el doble redraw
    (220×50 default → tamaño real) que reflowea dos veces el TUI y tritura el
    scrollback. Ver [[tmux-size-clamping]].

    observer=1: attach de solo-lectura que no desplaza al dueño ni redimensiona
    la ventana (páginas QA abiertas con ?qa=1). Ver _cmd_attach().

    fc=1: el cliente implementa flow control (ackea bytes parseados con
    {'type':'ack','bytes':N}) → el backend frena la lectura del PTY cuando hay
    demasiado sin confirmar, para que la cola de xterm nunca llegue a los 50MB
    donde tira datos en pestañas ocultas. Un cliente legacy (JS cacheado viejo)
    no manda fc y conserva el comportamiento histórico. Ver _FlujoWS."""
    # El middleware http NO corre para websockets: Origin anti CSWSH.
    from plotspace.core import auth as jarvis_auth
    if not jarvis_auth.origen_permitido(websocket.headers.get('origin'), jarvis_auth.hosts_extra()):
        await websocket.close(code=4403)
        return
    # Red de seguridad anti-desplazamiento: un browser headless (QA de un agente)
    # que se conecta sin ?qa=1 se degrada a observer para no robarle el tamaño al
    # usuario (overlay 4010). Ver _forzar_observer.
    _ua = websocket.headers.get('user-agent')
    if not observer and _es_ua_automatizada(_ua):
        from plotspace.core import logs
        logs.evento('qa_ua_observer_forzado', nivel='warn',
                    terminal_id=terminal_id, user_agent=(_ua or '')[:120])
    observer = 1 if _forzar_observer(observer, _ua) else 0
    await websocket.accept()

    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT t.*, p.ruta AS project_path
            FROM terminals t
            JOIN projects p ON t.project_id = p.id
            WHERE t.id = ? AND t.activa = 1
        ''', (terminal_id,))
        terminal = cursor.fetchone()
    finally:
        conn.close()

    if not terminal:
        await websocket.send_text("\r\n\x1b[31mTerminal no encontrada o inactiva\x1b[0m\r\n")
        await websocket.close()
        return

    project_path = terminal['project_path']
    nombre       = terminal['nombre']
    project_id   = terminal['project_id']

    # Asegurar proyecto + skills + sesión tmux (puede faltar si el servidor reinició)
    await _preparar_proyecto(project_path, project_id=project_id)

    # es_reanudacion=True: si el WS-connect gana la carrera al reconcile y crea la
    # sesión él, igual debe arrancar el CLI en modo RESUME — si no, codex/qwen/
    # opencode/agy volvían SIN su conversación (claude decide por el .jsonl en
    # disco, pero los otros dependen de este flag). Ver reconciliar_sesiones_tmux.
    await _crear_sesion_tmux(terminal_id, project_path, es_reanudacion=True)

    # Carrera contra eliminar_terminal: si la terminal fue borrada (activa=0)
    # mientras preparábamos el proyecto / creábamos la sesión, _crear_sesion_tmux
    # acaba de RESUCITAR la sesión que teardown_terminal había matado, dejando un
    # zombie eterno (borrada en DB/UI pero con el agente corriendo y sin forma de
    # cerrarlo). Re-chequear activa=1 acá y, si pasó a inactiva, re-matar la
    # sesión y cerrar el WS antes de attachear.
    conn2 = get_db()
    try:
        c2 = conn2.cursor()
        c2.execute('SELECT activa FROM terminals WHERE id = ?', (terminal_id,))
        fila2 = c2.fetchone()
    finally:
        conn2.close()
    if not fila2 or not fila2['activa']:
        await _matar_sesion_tmux(terminal_id)
        try:
            await websocket.close(code=4404, reason='terminal-borrada')
        except Exception:
            pass
        return

    log_file = _log_path(project_path, terminal_id, nombre)

    # ── MOTOR DE UN EMULADOR (feature flag TERMINALES_MOTOR=control) ──
    # tmux deja de dibujar: attach -C (control mode) entrega los bytes CRUDOS
    # de la app y xterm.js pasa a ser el ÚNICO emulador (scroll local nativo,
    # sin copy-mode, sin re-wrap doble, sin alt-screen del attach). tmux queda
    # como guardián de procesos + caño. Default 'classic' hasta validar la
    # batería E2E completa — el motor viejo queda intacto abajo.
    if _motor_control():
        await _sesion_control(websocket, terminal_id, log_file,
                              cols=cols, rows=rows, observer=bool(observer),
                              fc=bool(fc))
        return

    await _sesion_tmux(websocket, terminal_id, project_path, log_file,
                       cols=cols, rows=rows, observer=bool(observer),
                       fc=bool(fc))


# ─── Sesión tmux vía PTY ───────────────────────────────────────────────────────

# Tamaño con el que nace el PTY del attach si el browser no mandó cols/rows
# (clientes legacy sin el query param). Es solo un default de arranque: el
# window-size latest + el primer resize del browser lo corrigen igual.
_PTY_DEFAULT_COLS = 220
_PTY_DEFAULT_ROWS = 50


def _winsize_inicial(cols: Optional[int], rows: Optional[int]) -> tuple:
    """Valida cols/rows del browser y devuelve (rows, cols) para el spawn del PTY.
    Aplica el mismo piso degenerado (cols<20 / rows<5) que onResize: tamaños
    chicos hacen que tmux reformatee el output una letra por línea. Si vienen
    fuera de rango o ausentes, cae al default de arranque."""
    c = cols if (isinstance(cols, int) and cols >= 20) else _PTY_DEFAULT_COLS
    r = rows if (isinstance(rows, int) and rows >= 5) else _PTY_DEFAULT_ROWS
    # Techo defensivo: un cols/rows absurdo (URL manipulada) no debe romper tmux.
    c = min(c, 1000)
    r = min(r, 500)
    return r, c


def _cmd_attach(session: str, observer: bool = False) -> list:
    """argv del attach del PTY a la sesión tmux.

    - Dueño (default): `attach -d` — detacha cualquier OTRO cliente al conectar.
      Una terminal Jarvis es para UN cliente web; recargar la pestaña desplaza
      al anterior. Evita el clamping por cliente zombie. Ver [[tmux-size-clamping]].
    - Observador (?observer=1 en el WS, lo manda una página abierta con ?qa=1 —
      los recorridos QA de Playwright): attach SIN -d con flags
      `read-only,ignore-size` — mira la terminal sin desplazar al dueño, no
      puede tipear y NO participa del `window-size latest` (no le achica la
      ventana al usuario al tamaño del viewport headless). Sin esto, cada QA
      de frontend robaba el attach del usuario y le dejaba las terminales
      congeladas / con el scrollback triturado por el re-wrap."""
    if observer:
        return ['tmux', 'attach-session', '-t', session, '-f', 'read-only,ignore-size']
    return ['tmux', 'attach-session', '-d', '-t', session]


def _motor_control() -> bool:
    """Feature flag del motor de UN emulador. Default: CONTROL (validado con
    batería E2E completa 2026-07-02 — 10/10 escenarios: scroll local sin
    copy-mode, F5 restaura scrollback, resize convergente, TUI mid-resize sin
    residuos, alt-screen, observador, monitores intactos, 0 errores).
    Vía de escape: TERMINALES_MOTOR=classic vuelve al motor viejo (intacto)."""
    return os.getenv('TERMINALES_MOTOR', 'control').strip().lower() == 'control'


# Cache de /api/clis: la detección mira el PATH (en WSL incluye decenas de
# dirs /mnt/c y tarda ~2-3s). La UI la pide al arrancar y al abrir el picker;
# sin cache, el aviso "Falta instalar" aparecía segundos tarde. TTL corto: el
# estado cambia poco (solo al instalar un CLI, que invalida este cache).
_CLIS_CACHE = {'ts': 0.0, 'data': None}
_CLIS_TTL_S = 30.0


async def _clis_estado_cacheado():
    ahora = time.monotonic()
    if _CLIS_CACHE['data'] is not None and (ahora - _CLIS_CACHE['ts']) < _CLIS_TTL_S:
        return _CLIS_CACHE['data']
    from plotspace.core import clis as _clis
    # La detección puede tardar (mirar el PATH del entorno): fuera del loop.
    data = await asyncio.to_thread(_clis.estado)
    _CLIS_CACHE['data'] = data
    _CLIS_CACHE['ts'] = ahora
    return data


@router.get("/api/clis")
async def listar_clis():
    """Qué CLIs de agente hay instalados en esta máquina, y cuáles se pueden
    instalar desde acá."""
    return await _clis_estado_cacheado()


@router.post("/api/clis/{cli_id}/instalar")
async def instalar_cli(cli_id: str):
    """Instala un CLI de agente por npm.

    Tarda minutos, así que va a un thread y el progreso se avisa por WS
    (`cli_instalando` / `cli_instalado`) — el mismo patrón que el resto de las
    cosas largas de la app."""
    from plotspace.core import clis as _clis
    from plotspace.core.events import broadcaster

    if not _clis.comando_instalar(cli_id):
        raise HTTPException(status_code=400, detail='ese agente no se instala desde acá')

    await broadcaster.broadcast({'type': 'cli_instalando', 'cli': cli_id})
    r = await asyncio.to_thread(_clis.instalar, cli_id)
    _CLIS_CACHE['data'] = None   # el estado cambió: que la próxima consulta re-detecte
    await broadcaster.broadcast({'type': 'cli_instalado', 'cli': cli_id,
                                 'ok': r['ok'], 'salida': r['salida'][-400:]})
    return r


# Cuánto scrollback se re-reproduce al attachear (el "F5 restaura todo"):
# 2000 líneas con colores ≈ el reach útil del scroll sin inflar el attach.
_SEED_LINEAS = 2000
# Watermarks de la cola interna WS (eventos %output pendientes de mandar):
# por encima se PAUSA la lectura del cliente de control (backpressure hacia
# tmux, aislado por cliente); al drenar por debajo del bajo, se reanuda.
_COLA_PAUSA = 64
_COLA_REANUDA = 16
# Tope del coalescing de la cola (fix "parpadeo del cursor", 2026-07-17): un
# redraw de claude (full-repaint por tecla) llega en 12-43 %output de ≤~2730B;
# mandarlos 1-a-1 partía el frame en el browser (pintaba con el cursor apagado
# entre mensajes) y multiplicaba ~10× los send WS bajo flood. 256KB junta
# cualquier redraw entero sin armar sends monstruosos.
_COALESCE_MAX = 256 * 1024


def _juntar_cola(cola: asyncio.Queue, primero: bytes, tope: int = _COALESCE_MAX):
    """→ (data, eof). Une `primero` con todo lo YA encolado (get_nowait — no
    espera nada nuevo, cero latencia agregada) hasta cruzar `tope` bytes. Un
    None (EOF) corta la unión y vuelve como eof=True: el caller manda el lote
    y cierra AHÍ (igual que cuando el None salía solo de la cola — lo
    posterior a un EOF nunca se mandó tampoco antes). El caso común (eco de
    tipeo, cola vacía) devuelve `primero` intacto sin copiar."""
    if cola.qsize() == 0:
        return primero, False
    partes = [primero]
    total = len(primero)
    while total < tope:
        try:
            sig = cola.get_nowait()
        except asyncio.QueueEmpty:
            break
        if sig is None:
            return (b''.join(partes) if len(partes) > 1 else primero), True
        if not isinstance(sig, bytes):
            # Item SINTÉTICO (str: secuencia de modos de la sync viva) — no se
            # junta con bytes de salida ni pasa por el decoder. Se devuelve a la
            # cola para mandarlo aparte; el leve reordenamiento vs la salida es
            # inocuo (un set de modo afecta la INTERPRETACIÓN del input, no lo
            # que se pinta).
            cola.put_nowait(sig)
            break
        partes.append(sig)
        total += len(sig)
    return (b''.join(partes) if len(partes) > 1 else primero), False
# Ventana del watchdog de re-seed post-resize (fix S1 2026-07-08): si tras un
# resize que cambió medidas el pane no emite NADA en este tiempo (app idle que
# no redibuja en SIGWINCH — claude fullscreen, upstream #43273), se re-siembra.
# 0.4s: un redraw legítimo llega en decenas de ms; idle = no llega nunca.
_RESEED_MUDO_S = 0.4
# Cadencia del poller de SINCRONIZACIÓN VIVA de modos privados (DECCKM de
# flechas, mouse-tracking). tmux ABSORBE esas secuencias en flags de pane y no
# las reenvía por %output, así que el seed las sincroniza una vez; sin re-chequear
# los flags, un menú que entra en modo-aplicación DESPUÉS del último seed deja las
# flechas y el clic muertos. 1.0s: barato (un display-message por el stream de
# control, sin fork) y el modo ya está fijado cuando el usuario va a tocar una
# tecla. Ver [[modos-privados-sync-vivo]].
_SYNC_MODOS_S = 1.0


async def _sesion_control(websocket: WebSocket, terminal_id: int, log_file: str,
                          cols: Optional[int] = None, rows: Optional[int] = None,
                          observer: bool = False, fc: bool = False):
    """Attach por tmux CONTROL MODE: el browser recibe los bytes CRUDOS de la
    app (xterm.js = único emulador) y tmux queda como guardián + caño.

    - seed: capture-pane -e + cursor real + flag de alt-screen → el attach/F5
      restaura pantalla, colores y posición SIN redraw de la app (cero reflow).
    - input: send-keys -H (hex) — cualquier byte viaja sin quoting.
    - resize: refresh-client -C → UNA orden, tmux hace el SIGWINCH.
    - flow control: mismo protocolo ack del cliente (_FlujoWS) + pausa de
      lectura del pipe (backpressure aislado: tmux bufferea de su lado).
    - observer (?qa=1): mismo stream de lectura, input/resize ignorados. No
      desplaza al dueño (los clientes de control conviven) ni toca el tamaño.
    """
    from plotspace.core.control_mode import (ClienteControl, armar_seed,
                                           debe_resembrar, liberar_dueno,
                                           masa_capture, registrar_dueno,
                                           reseed_seguro, resolver_modos,
                                           seed_degradado, sincronizar_modos)

    session = f'jarvis_{terminal_id}'
    r_ini, c_ini = _winsize_inicial(cols, rows)
    decoder = _nuevo_decoder_utf8()
    flujo = _FlujoWS(activo=fc)
    log_writer = _LogWriter(log_file)
    cliente = ClienteControl(session)
    cola: asyncio.Queue = asyncio.Queue()
    cerrando = False
    desplazado = False   # otra ventana tomó el control (dueño único de tamaño)
    salida_n = 0         # eventos %output recibidos (marca del watchdog de re-seed)
    masa_seed = None     # líneas con texto del último capture sembrado (guard anti-destrucción)
    modos_previos = None    # último dict de modos privados sembrado/empujado a xterm (caché anti-degradado + base del diff de la sync viva)
    seed_en_curso = False   # captura de seed en vuelo: NO pausar el reader (ver _hacer_seed)
    candado_seed = asyncio.Lock()   # serializa refresh ↔ watchdog (ambos cancelan/relanzan _enviador)

    def _on_output(data: bytes):
        # Corre EN el loop (add_reader): encolar y aplicar backpressure local.
        # Durante una captura de seed NO se pausa: la respuesta %begin/%end
        # viaja por el MISMO reader — pausarlo dejaría la captura esperando su
        # timeout de 5s con el output cortado (el freeze de ~5.5s post-update).
        nonlocal salida_n
        salida_n += 1
        cola.put_nowait(data)
        if not seed_en_curso and cola.qsize() > _COLA_PAUSA:
            cliente.pausar_lectura()

    def _on_exit():
        # La sesión murió (kill-session / tmux caído): avisar al front al
        # instante — overlay de reconexión, no terminal congelada (5ª capa).
        cola.put_nowait(None)

    def _desplazar():
        # Un dueño NUEVO se conectó a esta terminal: esta vista pierde el
        # derecho a tamaño y cierra con 4010 (el front NO auto-reintenta —
        # si no, las dos pestañas quedan en ping-pong de detach eterno).
        nonlocal desplazado
        desplazado = True
        cola.put_nowait(None)

    async def _enviador():
        """Cola → WS, con flow control y log. None = EOF.

        COALESCING (fix "parpadeo del cursor", 2026-07-17): mandar 1 send_text
        por %output partía cada redraw de claude (12-43 mensajes por
        full-repaint por tecla) y el browser pintaba frames intermedios con el
        cursor APAGADO → el dot parpadeaba al tipear. _juntar_cola une lo YA
        encolado (cero latencia agregada: solo junta lo que esperaba turno) →
        el frame cruza entero en 1-3 sends y bajo flood caen ~10× los
        mensajes WS (y las pasadas de deflate) del único event loop."""
        while True:
            data = await cola.get()
            if isinstance(data, str):
                # Secuencia SINTÉTICA ya decodificada (sync viva de modos): va
                # VERBATIM por el único sender, sin decoder de bytes, coalescing
                # ni log — igual que el seed, que también son bytes de control
                # inyectados por el backend, no salida real del pane.
                try:
                    await websocket.send_text(data)
                except Exception:
                    return
            else:
                eof = data is None
                if not eof:
                    data, eof = _juntar_cola(cola, data)
                    await flujo.esperar_capacidad()
                    texto = decoder.decode(data)
                    if texto:
                        try:
                            await websocket.send_text(texto)
                        except Exception:
                            return
                        flujo.enviado(len(texto))
                        log_writer.append(texto)
                        if log_writer.bytes_pendientes > 64 * 1024:
                            await log_writer.flush()
                if eof:
                    if not cerrando:
                        try:
                            if desplazado:
                                await websocket.close(code=4010, reason='desplazado')
                            else:
                                await websocket.close(code=4000, reason='pty-eof')
                        except Exception:
                            pass
                    return
            if cola.qsize() < _COLA_REANUDA:
                cliente.reanudar_lectura()

    def _capturar_seed_sync():
        # Fallback del seed (el camino primario va por el stream de control,
        # ver _capturar_seed_control). timeout=5: eran los ÚNICOS subprocess
        # sin timeout del archivo — un tmux colgado dejaba el WS accepted y
        # MUDO para siempre (card en blanco sin overlay ni retry).
        #
        # motor-tmux: fallback del seed del attach control-mode — pide flags de
        # pane (alternate_on, mouse_*, keypad_cursor) que son del modelo de tmux.
        try:
            info = subprocess.run(
                ['tmux', 'display', '-p', '-t', session,
                 '#{alternate_on}\t#{cursor_x}\t#{cursor_y}\t#{pane_height}\t'
                 '#{mouse_any_flag}\t#{mouse_button_flag}\t#{mouse_all_flag}\t'
                 '#{keypad_cursor_flag}'],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip().split('\t')
            # motor-tmux: mismo camino que arriba, inalcanzable con ConPTY.
            cap = subprocess.run(
                ['tmux', 'capture-pane', '-p', '-e', '-t', session,
                 '-S', f'-{_SEED_LINEAS}'],
                capture_output=True, text=True, timeout=5,
            ).stdout
        except subprocess.TimeoutExpired:
            print(f'[control] seed de {session}: tmux no respondió (timeout) — seed vacío')
            return [], ''
        return info, cap

    async def _capturar_seed_control():
        """Seed por el PROPIO stream de control: la respuesta %begin/%end llega
        ORDENADA respecto de los %output, así que todo lo que la app emitió
        antes del %end ya está DENTRO de la captura — lo encolado hasta acá se
        descarta sin race (era la fuente del contenido duplicado al reconectar
        con un agente floodeando; auditoría 2026-07-02)."""
        ok1, cuerpo1 = await cliente.comando_con_respuesta(
            f"display-message -p -t {session} "
            "'#{alternate_on}|#{cursor_x}|#{cursor_y}|#{pane_height}|"
            "#{mouse_any_flag}|#{mouse_button_flag}|#{mouse_all_flag}|"
            "#{keypad_cursor_flag}'")
        ok2, cuerpo2 = await cliente.comando_con_respuesta(
            f'capture-pane -p -e -t {session} -S -{_SEED_LINEAS}')
        if not ok1 or not ok2:
            raise RuntimeError(f'capture por control falló: {(cuerpo1 or cuerpo2)[:1]}')
        info = (cuerpo1[0] if cuerpo1 else '').split('|')
        cap = '\n'.join(cuerpo2)
        return info, cap

    async def _hacer_seed(proteger: bool = False) -> bool:
        """Captura + drena la cola + siembra xterm. True si el WS sigue vivo.
        Lo usan el attach inicial y el refresh (re-seed = 'F5 sin F5').

        `proteger=True` (watchdog post-resize): NO pintar un capture que tenga
        MENOS contenido del que ya se sembró — tras achicar filas, tmux recorta
        su alt-screen y no lo restaura al volver a crecer, mientras que xterm sí.
        Sin esto, el watchdog pintaba la copia mutilada de tmux sobre la
        pantalla sana del browser y CORTABA la salida (ver reseed_seguro).

        REINTENTO anti-seed-degradado (2026-07-11): si la captura de estado vino
        incompleta (tmux ocupado → timeout, o carrera con el arranque de la app),
        el seed caía a los defaults (alt_on=False, modos=None) y esa terminal
        perdía alt-screen + mouse-tracking → SCROLL MUERTO hasta el próximo
        redraw de la app ('una terminal random no scrollea y revive al mandarle
        un mensaje'). Un respiro de 0.3s y UNA recaptura curan el caso típico;
        si sigue degradada se siembra lo que haya (jamás mudo, como siempre).

        DESTRABAR ANTES DE CAPTURAR (fix del freeze de ~5.5s post-update, deep
        work 2026-07-11): la respuesta %begin/%end de la captura la lee el
        MISMO reader que _on_output PAUSÓ si la cola cruzó _COLA_PAUSA (p.ej.
        scrolleando a un agente que floodea). Capturar con el reader pausado =
        esperar los 5.0s del timeout (con el _enviador cancelado en el branch
        'refresh' → el WS mudo) y recién ahí caer al fallback sync: el usuario
        lo vivía como "el scroll se congela ~5.5s y revive solo". El backlog
        previo se puede tirar sin miedo — la captura trae el estado COMPLETO
        del pane —, así que: drenar, revivir el reader, y capturar con
        seed_en_curso=True para que _on_output no lo re-pause a mitad de
        captura (la cola crece unos ms y se drena al final)."""
        nonlocal seed_en_curso, masa_seed, modos_previos

        def _drenar_cola():
            # Un None (EOF) encolado se re-emite para no perder el cierre.
            try:
                while True:
                    item = cola.get_nowait()
                    if item is None:
                        cola.put_nowait(None)
                        break
            except asyncio.QueueEmpty:
                pass

        seed_en_curso = True
        try:
            _drenar_cola()
            cliente.reanudar_lectura()
            info, cap = [], ''
            for intento in range(2):
                try:
                    info, cap = await _capturar_seed_control()
                except Exception:
                    # Camino degradado: captura por subprocess (con timeout). Menos
                    # exacto (ventana de ms entre captura y drenaje) pero jamás muda.
                    try:
                        info, cap = await asyncio.to_thread(_capturar_seed_sync)
                    except Exception:
                        info, cap = [], ''
                if not seed_degradado(info):
                    break
                if intento == 0 and not cerrando:
                    await asyncio.sleep(0.3)
            # Drenar lo encolado DURANTE la captura: con el camino serializado
            # TODO lo anterior al %end ya está en la captura; re-aplicarlo
            # encima del seed duplicaba el contenido.
            _drenar_cola()
        finally:
            seed_en_curso = False
        # El drenaje pudo dejar la lectura pausada con la cola vacía: sin esto,
        # nadie la reanuda (el _enviador solo reanuda al consumir) → deadlock.
        cliente.reanudar_lectura()
        try:
            alt_on = info[0] == '1'
            cx, cy, alto = int(info[1]), int(info[2]), int(info[3])
        except (IndexError, ValueError):
            alt_on, cx, cy, alto = False, 0, 0, r_ini
        # Modos: los recién capturados si son fiables; si la captura vino
        # DEGRADADA, se REUSA el último bueno (no tirar el DECCKM/mouse de un
        # menú vivo por un hipo de tmux). Se cachea para el diff de la sync viva.
        modos = resolver_modos(info, modos_previos)
        if modos is not None:
            modos_previos = modos
        # Candado anti-destrucción del watchdog: si el capture perdió contenido,
        # es el recorte de tmux — la pantalla del browser está MEJOR que esto.
        if proteger and alt_on and not reseed_seguro(cap, masa_seed):
            from plotspace.core import logs
            logs.evento('terminal_reseed_omitido', terminal_id=terminal_id,
                        masa=masa_capture(cap), masa_previa=masa_seed)
            return True
        try:
            await websocket.send_text(armar_seed(cap, alt_on, cx, cy, alto, modos))
        except Exception:
            return False
        masa_seed = masa_capture(cap)
        return True

    tarea_envio = None
    tarea_reseed = None
    tarea_sync = None    # poller de sincronización viva de modos privados
    ult_dims = (c_ini, r_ini)   # último tamaño pedido a tmux (gate del watchdog)

    async def _reseed_si_mudo(marca: int):
        """Watchdog del fix S1 "negro al salir de fullscreen" (2026-07-08): tras
        un resize que CAMBIÓ las medidas del pane, la única fuente de repintado
        es la app (refresh-client -C solo hace el SIGWINCH; tmux no re-emite
        pantalla en control mode) — y claude fullscreen IDLE no redibuja en
        SIGWINCH (state-gated, upstream #43273): queda el crop/pad del
        alt-buffer (mayormente negro) hasta el próximo output. Si en la ventana
        el pane siguió MUDO y está en alt-screen, re-sembramos con la verdad de
        tmux (mismo baile serializado que el branch 'refresh'). El seed alt
        arranca con clear+home (armar_seed) y viaja en UN send_text → pinta
        atómico. decisión pura: debe_resembrar (testeada)."""
        nonlocal tarea_envio
        try:
            await asyncio.sleep(_RESEED_MUDO_S)
            if cerrando or salida_n != marca:
                return                      # llegó output: la app repintó sola
            ok, cuerpo = await cliente.comando_con_respuesta(
                f"display-message -p -t {session} '#{{alternate_on}}'")
            alt_on = bool(ok and cuerpo and cuerpo[0].strip() == '1')
            if not debe_resembrar(salida_n - marca, alt_on):
                return
            async with candado_seed:
                if cerrando or salida_n != marca:
                    return
                if tarea_envio is not None:
                    tarea_envio.cancel()
                    try:
                        await tarea_envio
                    except (asyncio.CancelledError, Exception):
                        pass
                if await _hacer_seed(proteger=True):
                    tarea_envio = asyncio.create_task(_enviador())
        except (asyncio.CancelledError, Exception):
            pass

    async def _sincronizar_modos_vivo():
        """Sincronización VIVA de los modos privados (DECCKM de flechas,
        mouse-tracking). La app los prende/apaga con secuencias que tmux ABSORBE
        en sus flags de pane: NO viajan por %output (igual que el alt-screen). El
        seed las re-enuncia UNA sola vez; sin esto, un menú del agente que entra
        en modo-aplicación DESPUÉS del último seed deja las FLECHAS y el CLIC
        muertos (xterm manda las flechas como CSI \\x1b[B y el CLI espera SS3
        \\x1bOB), mientras que un byte literal como 'n' sigue andando — el bug
        'no me puedo mover con las flechas en el menú del agente' (2026-07-23).
        Poll barato por el stream de control (sin fork): leer los flags y, cuando
        cambian, encolar la secuencia para que el ÚNICO sender la mande (sin
        pisarse con el _enviador). Ver [[modos-privados-sync-vivo]]."""
        nonlocal modos_previos
        while not cerrando and not cliente._cerrado:
            await asyncio.sleep(_SYNC_MODOS_S)
            if cerrando or cliente._cerrado:
                break
            # Saltar mientras hay un seed/probe en vuelo: comparten el stream de
            # control (comando_con_respuesta es FIFO) — no competir por los
            # futures; el próximo tick sincroniza igual.
            if seed_en_curso or cliente._esperas:
                continue
            try:
                ok, cuerpo = await cliente.comando_con_respuesta(
                    f"display-message -p -t {session} "
                    "'#{alternate_on}|#{cursor_x}|#{cursor_y}|#{pane_height}|"
                    "#{mouse_any_flag}|#{mouse_button_flag}|#{mouse_all_flag}|"
                    "#{keypad_cursor_flag}'", timeout=3.0)
            except Exception:
                continue
            if not ok or not cuerpo:
                continue
            seq, modos_previos = sincronizar_modos(modos_previos, cuerpo[0].split('|'))
            if seq:
                cola.put_nowait(seq)

    try:
        cliente.iniciar(on_output=_on_output, on_exit=_on_exit)
        if not observer:
            # DUEÑO ÚNICO de tamaño (semántica del `attach -d` del motor
            # classic): desplazar al dueño anterior — su WS cierra con 4010.
            anterior = registrar_dueno(terminal_id, cliente, _desplazar)
            if anterior is not None:
                try:
                    anterior['desplazar']()
                except Exception:
                    pass
                asyncio.create_task(anterior['cliente'].cerrar())
            # Tamaño ANTES del seed: si el pane estaba en otro tamaño, la app
            # ya recibe su único SIGWINCH y el seed captura el estado final.
            cliente.resize(c_ini, r_ini)
            await asyncio.sleep(0.15)   # deja asentar el redraw del SIGWINCH
        if not await _hacer_seed():
            return
        tarea_envio = asyncio.create_task(_enviador())
        tarea_sync = asyncio.create_task(_sincronizar_modos_vivo())

        while True:
            msg = await websocket.receive_json()
            tipo = msg.get('type')

            if tipo == 'input':
                if observer:
                    continue          # un observador (QA) jamás tipea
                data = msg.get('data', '')
                if data:
                    await cliente.enviar_bytes(data.encode('utf-8', errors='replace'))

            elif tipo == 'resize':
                if observer:
                    continue          # ni redimensiona
                try:
                    rr = int(msg.get('rows', 24))
                    cc = int(msg.get('cols', 80))
                except (TypeError, ValueError):
                    continue
                if rr < 5 or cc < 20:
                    continue          # piso degenerado, mismo criterio clásico
                cc2, rr2 = min(cc, 1000), min(rr, 500)
                cambio = (cc2, rr2) != ult_dims
                ult_dims = (cc2, rr2)
                cliente.resize(cc2, rr2)
                # Watchdog S1: si el pane queda MUDO tras un resize que cambió
                # medidas, re-sembrar (una app idle no redibuja en SIGWINCH y
                # la card queda con el crop/pad negro del alt-buffer). Ráfagas
                # de resize re-arman el watchdog (solo decide el último).
                if cambio:
                    if tarea_reseed is not None:
                        tarea_reseed.cancel()
                    tarea_reseed = asyncio.create_task(_reseed_si_mudo(salida_n))

            elif tipo == 'ack':
                flujo.ack(msg.get('bytes', 0))

            elif tipo == 'visible':
                flujo.set_visible(bool(msg.get('v', True)))

            elif tipo == 'refresh':
                # RE-SEED real ("F5 sin F5"): era un no-op y el frontend tiene
                # 3 caminos de auto-sanación que confían en él — el peor hacía
                # term.reset() y esperaba un repintado que nunca llegaba →
                # card EN BLANCO permanente (auditoría 2026-07-02). CONTRATO:
                # el front hace term.reset() ANTES de mandar refresh; acá se
                # re-captura por el camino serializado y se re-siembra. El
                # _enviador se pausa (cancel) para que ningún byte post-captura
                # se cuele ANTES del seed y quede pisado por el repintado.
                if observer:
                    continue
                async with candado_seed:   # no pisarse con el watchdog de re-seed
                    if tarea_envio is not None:
                        tarea_envio.cancel()
                        try:
                            await tarea_envio
                        except (asyncio.CancelledError, Exception):
                            pass
                    if not await _hacer_seed():
                        return
                    tarea_envio = asyncio.create_task(_enviador())

    except (WebSocketDisconnect, asyncio.CancelledError, Exception):
        pass
    finally:
        cerrando = True
        if not observer:
            liberar_dueno(terminal_id, cliente)
        if tarea_reseed is not None:
            tarea_reseed.cancel()
        if tarea_sync is not None:
            tarea_sync.cancel()
        if tarea_envio is not None:
            tarea_envio.cancel()
        await cliente.cerrar()        # detach: la sesión y el agente siguen
        log_writer.flush_sync()


async def _sesion_tmux(websocket: WebSocket, terminal_id: int,
                       project_path: str, log_file: str,
                       cols: Optional[int] = None, rows: Optional[int] = None,
                       observer: bool = False, fc: bool = False):
    """Adjunta un PTY a la sesión tmux.
    Al desconectar el WS, terminate() detacha sin matar la sesión.
    observer=True: attach read-only,ignore-size sin -d (ver _cmd_attach).
    fc=True: el cliente ackea bytes parseados → backpressure (ver _FlujoWS)."""
    try:
        import ptyprocess
    except ImportError:
        await websocket.send_text(
            "\r\n\x1b[31mptyprocess no instalado — ejecutá: pip install ptyprocess\x1b[0m\r\n"
        )
        return

    loop    = asyncio.get_event_loop()
    session = f'jarvis_{terminal_id}'
    cwd     = project_path if os.path.isdir(project_path) else os.path.expanduser('~')

    # Auto-sanación en cada conexión: re-asegurar window-size latest antes de
    # attachear. Cubre sesiones legacy que quedaron en 'manual' (clavadas chicas)
    # de antes del fix; sin esto, una sesión 'manual' no se recupera ni con el
    # attach nuevo. Ver [[tmux-size-clamping]].
    # to_thread + timeout: corre en el event loop antes de spawnear el PTY; un
    # tmux colgado acá bloqueaba la apertura de TODA terminal nueva. Es
    # best-effort: si se cuelga, seguimos al attach igual (degradación).
    try:
        await asyncio.to_thread(
            subprocess.run,
            ['tmux', 'set-option', '-t', session, 'window-size', 'latest'],
            capture_output=True, timeout=5,
        )
    except subprocess.TimeoutExpired:
        pass

    # Tamaño con el que nace el PTY: el REAL del browser (cols/rows del query
    # param). Crítico contra el triturado: con window-size latest + attach -d,
    # el primer redraw de tmux sale a ESTE tamaño. Si naciera al default
    # (220×50) y el browser fuera 242×37, habría DOS redraws (220×50 → 242×37):
    # el TUI reflowea dos veces, xterm pinta ambos frames encimados y tmux
    # re-reflowea el scrollback wrapeado → palabras desparramadas / líneas
    # dobles que se acumulan. Naciendo ya al tamaño real, el resize del browser
    # en ws.onopen es un no-op (mismo tamaño) y no hay segundo redraw.
    # Ver [[tmux-size-clamping]].
    pty_rows, pty_cols = _winsize_inicial(cols, rows)
    try:
        # Dueño: attach -d (un attach nuevo desplaza al anterior, igual que
        # recargar la pestaña; evita el clamping por cliente zombie).
        # Observador (QA): sin -d + read-only,ignore-size — no roba ni
        # redimensiona. Ver _cmd_attach() y [[tmux-size-clamping]].
        proceso = ptyprocess.PtyProcess.spawn(
            _cmd_attach(session, observer),
            dimensions=(pty_rows, pty_cols),
            env=_env_terminal(),
            cwd=cwd,
        )
        # La entrada de terminal_processes es del DUEÑO (teardown_terminal la
        # usa para terminar el attach real): un observador no la pisa.
        if not observer:
            terminal_processes[terminal_id] = {'process': proceso, 'type': 'pty'}

        # Un solo refresh tras el attach deja el buffer consistente al tamaño con
        # que nació el cliente (sin esperar al primer resize del browser). Barato
        # y elimina cualquier resto del redraw del tamaño anterior. window-size
        # latest ya tomó pty_cols×pty_rows como "último cliente". Fuera del loop
        # (to_thread): corre en CADA conexión y no debe trabar las otras terminales.
        await _refresh_clientes_sesion(session)

        # FIX 9-terminales: el read del PTY era bloqueante en run_in_executor —
        # cada terminal conectada OCUPABA un thread del pool default para
        # siempre (parked en read()). Con 4 CPUs el pool es de 8 threads:
        # 8 terminales = pool lleno; la 9.ª no conseguía thread y además
        # dejaba sin executor a Whisper/TTS → input lag y mensajes trabados.
        # Ahora: fd no-bloqueante + loop.add_reader (asyncio nativo, 0 threads).
        # flujo: backpressure de punta a punta (séptima capa de
        # [[tmux-size-clamping]]) — si el browser no confirma lo enviado
        # (pestaña oculta = parser de xterm estrangulado por Chrome), dejamos
        # de leer el PTY antes de que la cola de xterm llegue a los 50MB donde
        # tira datos. tmux absorbe el freno sin bloquear al agente.
        flujo = _FlujoWS(activo=fc)
        # Log batcheado fuera del event loop (ver _LogWriter): el read loop solo
        # acumula en memoria; el write real va en thread.
        log_writer = _LogWriter(log_file)
        # Writer drenado no-bloqueante (input → PTY): un paste grande ya no tira
        # BlockingIOError ni detacha tmux (ver _EscritorPTY). El fd lo deja
        # no-bloqueante leer() (corre antes del primer input: receive_json cede).
        escritor = _EscritorPTY(proceso.fd, loop)

        async def leer():
            fd = proceso.fd
            os.set_blocking(fd, False)
            hay_data = asyncio.Event()
            loop.add_reader(fd, hay_data.set)
            # Stateful por conexión: junta caracteres multibyte partidos entre
            # chunks de os.read (ver _nuevo_decoder_utf8).
            decoder = _nuevo_decoder_utf8()
            try:
                while True:
                    await hay_data.wait()
                    hay_data.clear()
                    await flujo.esperar_capacidad()
                    try:
                        data = os.read(fd, 65536)
                    except BlockingIOError:
                        continue          # falso positivo de readiness
                    except OSError:
                        break             # PTY cerrado (detach/kill)
                    if not data:
                        break             # EOF
                    texto = _filtrar_detach(decoder.decode(data))
                    if not texto:
                        continue          # era solo el ruido de detach (o un
                                          # char multibyte aún incompleto)
                    if websocket.client_state == WebSocketState.CONNECTED:
                        await websocket.send_text(texto)
                        flujo.enviado(len(texto))
                    # Solo acumula en memoria (barato). El flush a disco lo hace
                    # el flusher periódico, o acá mismo si el buffer creció mucho.
                    log_writer.append(texto)
                    if log_writer.bytes_pendientes >= _LOG_FLUSH_BYTES:
                        await log_writer.flush()
                # Salir del while = PTY muerto (EOF/OSError): otro `attach -d`
                # nos desplazó (recarga de pestaña / página QA) o la sesión
                # murió. Cerrar el WS ACTIVAMENTE: antes quedaba abierto y la
                # terminal se congelaba en silencio con el canvas viejo (sin
                # overlay, mezclando anchos) hasta que un resize posterior
                # reventara. Con el close, el frontend muestra el overlay de
                # reconexión al instante. Ver [[tmux-size-clamping]].
                if websocket.client_state == WebSocketState.CONNECTED:
                    try:
                        await websocket.close(code=4000, reason='pty-eof')
                    except Exception:
                        pass
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            finally:
                try: loop.remove_reader(fd)
                except Exception: pass

        async def flushear_log():
            """Vuelca el buffer del log a disco cada _LOG_FLUSH_S (fuera del
            event loop, vía to_thread). Garantiza que el log no quede atrás
            aunque el agente deje de escupir output."""
            try:
                while True:
                    await asyncio.sleep(_LOG_FLUSH_S)
                    await log_writer.flush()
            except asyncio.CancelledError:
                pass

        tarea_lectura = asyncio.create_task(leer())
        tarea_flush = asyncio.create_task(flushear_log())

        try:
            while True:
                msg  = await websocket.receive_json()
                tipo = msg.get('type', 'input')

                if tipo == 'input':
                    if observer:
                        continue          # un observador (QA) jamás tipea
                    data_in = msg.get('data', '')
                    if data_in:
                        escritor.escribir(data_in.encode('utf-8', errors='replace'))

                elif tipo == 'ack':
                    # El browser confirma bytes YA PARSEADOS por xterm
                    # (callback de term.write). Baja `pendiente` y, si quedó
                    # bajo FC_LOW, reanuda la lectura del PTY. También lo
                    # mandan los observadores (QA): su stream tiene el mismo
                    # problema de cola en headless/background.
                    flujo.ack(msg.get('bytes', 0))

                elif tipo == 'visible':
                    # El browser reporta su visibilidad: con la pestaña visible el
                    # watermark del flow control se ensancha (el eco del tipeo no
                    # queda atrapado detrás del flood); con oculta se ajusta para
                    # proteger la cola de 50MB de xterm. Ver _FlujoWS.set_visible.
                    flujo.set_visible(bool(msg.get('v', True)))

                elif tipo == 'refresh':
                    # La pestaña volvió a ser visible: repintado completo de
                    # tmux → cualquier resto de desincronía visual se sana
                    # sin F5. Ver _tmux_refresh.
                    asyncio.create_task(_tmux_refresh(terminal_id))

                elif tipo == 'resize':
                    if observer:
                        continue          # un observador (QA) jamás redimensiona: su attach
                                          # es read-only/ignore-size y un _tmux_resize suyo
                                          # repinta al DUEÑO en momentos raros (garble).
                    try:
                        rows = int(msg.get('rows', 24))
                        cols = int(msg.get('cols', 80))
                    except (TypeError, ValueError):
                        continue          # frame de resize malformado: ignorarlo, NO matar el WS
                    # Filtro defensivo: tamaños chicos hacen que tmux y el PTY
                    # reformatten el output verticalmente y se queda así aunque
                    # después se restaure. Mismo límite que el frontend.
                    if rows < 5 or cols < 20:
                        continue
                    # Techo: un resize absurdo (cliente manipulado) no debe llegar
                    # a setwinsize. Mismo cap que _winsize_inicial (auditoría 2ª pasada).
                    cols = min(cols, 1000)
                    rows = min(rows, 500)
                    proceso.setwinsize(rows, cols)
                    asyncio.create_task(_tmux_resize(terminal_id, cols, rows))

        except (WebSocketDisconnect, asyncio.CancelledError, Exception):
            pass
        finally:
            tarea_lectura.cancel()
            tarea_flush.cancel()
            # Saca el add_writer del fd ANTES de terminate() (no dejar un callback
            # colgado sobre un fd que se va a cerrar).
            escritor.cerrar()
            # Tail: vuelca lo que quedó en buffer al cerrar (write único y chico).
            log_writer.flush_sync()
            # terminate() → detacha de tmux, NO mata la sesión
            try:
                proceso.terminate()
            except Exception:
                pass
            # Pop solo si la entrada sigue siendo NUESTRA: si un attach nuevo
            # ya la pisó (recarga de pestaña que nos desplazó), borrarla dejaría
            # a teardown_terminal sin el proceso del dueño vigente.
            if terminal_processes.get(terminal_id, {}).get('process') is proceso:
                terminal_processes.pop(terminal_id, None)

    except Exception as e:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_text(
                f"\r\n\x1b[31mError conectando a tmux ({session}): {e}\x1b[0m\r\n"
            )


# (_read_pty eliminado: el read del PTY ahora es no-bloqueante vía
#  loop.add_reader en _sesion_tmux — cero threads por terminal.)


# ─── Monitor de keywords ───────────────────────────────────────────────────────

async def _capture_tmux_output(terminal_id: int) -> str:
    """Últimas 100 líneas del pane, por el MOTOR (`backend()`).

    Sigue sin pasar por `pane_capture` a propósito (ver CLAUDE.md): el monitor
    de keywords necesita su propia lectura, no la cacheada que comparten los
    pollers — un TASK_DONE visto 0,8 s tarde es un paso que no avanza.
    """
    from plotspace.core.terminal_backend import backend
    try:
        return await backend().capturar_async(terminal_id, 100)
    except Exception:
        return ''   # motor colgado: no acumular procesos/FDs en el poller


async def _monitor_keywords(terminal_id: int, project_id: int):
    """Monitorea el output de una terminal buscando keywords de control.
    Captura baseline al arrancar para ignorar historial previo.
    Corre como asyncio.Task hasta ser cancelado o la terminal eliminada."""
    # Baseline: todo lo que ya está en el pane al arrancar se ignora.
    # Evita disparar con el texto de la tarea que JARVIS acaba de enviar.
    last_capture = await _capture_tmux_output(terminal_id)
    print(f'[monitor] Terminal {terminal_id}: baseline {len(last_capture.splitlines())} líneas')

    # Fast-path: el loop avanza ante (a) trigger de agent_watch que vio un TASK_*
    # fresco, o (b) el timeout de 2s (red de seguridad: peor caso == hoy). El
    # Event se crea lazy acá para que nazca/muera con esta Task.
    ev = _monitor_wakeups.setdefault(terminal_id, asyncio.Event())
    try:
        while True:
            try:
                await asyncio.wait_for(ev.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            ev.clear()

            # Verificar que la terminal sigue activa (DB sync fuera del loop: to_thread).
            def _sigue_activa():
                conn = get_db()
                try:
                    cur = conn.cursor()
                    cur.execute('SELECT activa FROM terminals WHERE id = ?', (terminal_id,))
                    r = cur.fetchone()
                    return bool(r and r['activa'])
                finally:
                    conn.close()
            if not await asyncio.to_thread(_sigue_activa):
                break

            capture = await _capture_tmux_output(terminal_id)
            if not capture or capture == last_capture:
                last_capture = capture
                continue

            # Solo evaluar líneas nuevas que no estaban en la captura anterior
            old_lines = set(last_capture.splitlines())
            new_lines = [l for l in capture.splitlines() if l.strip() and l not in old_lines]

            for kw in KEYWORDS_CONTROL:
                # _linea_es_keyword filtra instrucciones tipo "escribí TASK_DONE"
                if any(_linea_es_keyword(l, kw) for l in new_lines):
                    await _procesar_keyword_evento(terminal_id, project_id, kw)
                    break  # un evento por ciclo de 5s

            last_capture = capture

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f'[monitor] Error terminal {terminal_id}: {e}')
    finally:
        # Pop CONDICIONAL: si entre que este monitor murió y el finally ya se registró
        # un monitor NUEVO para la misma terminal (recreación rápida), no borrarlo.
        if keyword_monitors.get(terminal_id) is asyncio.current_task():
            keyword_monitors.pop(terminal_id, None)
            _monitor_wakeups.pop(terminal_id, None)


def _workflow_de_terminal(cursor, project_id: int, terminal_id: int):
    """ID del workflow activo (running/paused) cuyos pasos incluyen la terminal,
    o None. Pobla task_events.workflow_id (antes siempre NULL) para poder
    correlacionar cada fallo con su workflow — insumo de las lecciones."""
    import json as _json
    cursor.execute(
        "SELECT id, pasos FROM workflows WHERE project_id = ? "
        "AND estado IN ('running', 'paused') ORDER BY created_at DESC",
        (project_id,)
    )
    for row in cursor.fetchall():
        try:
            pasos = _json.loads(row['pasos'] or '[]')
        except (ValueError, TypeError):
            continue
        if any(p.get('terminal_id') == terminal_id for p in pasos):
            return row['id']
    return None


async def _procesar_keyword_evento(terminal_id: int, project_id: int, keyword: str,
                                   motivo: Optional[str] = None):
    """Registra el evento en DB (con motivo y workflow si los hay) y llama al
    orquestador. El motivo viene del sentinel-file ({estado, motivo}); el
    monitor de pane y el watchdog no lo conocen y pasan None."""
    ahora = datetime.now().isoformat()
    def _insert_evento():
        conn = get_db()
        try:
            wf_id = _workflow_de_terminal(conn.cursor(), project_id, terminal_id)
            conn.execute(
                'INSERT INTO task_events (terminal_id, project_id, event, timestamp, workflow_id, motivo) VALUES (?, ?, ?, ?, ?, ?)',
                (terminal_id, project_id, keyword, ahora, wf_id, (motivo or None))
            )
            conn.commit()
        finally:
            conn.close()
    await asyncio.to_thread(_insert_evento)   # write sync fuera del loop (corre en el ciclo del monitor)

    from plotspace.core import logs
    extra = {'motivo': motivo[:300]} if motivo else {}
    logs.evento('task_event', terminal_id=terminal_id, project_id=project_id, keyword=keyword,
                nivel=('error' if keyword == 'TASK_ERROR' else 'warn' if keyword == 'TASK_BLOCKED' else 'info'),
                **extra)

    # agent_watch: anotar el keyword para que el poller heurístico no duplique
    # el sonido que este evento ya va a disparar en el frontend.
    from plotspace.core.agent_watch import registrar_keyword
    registrar_keyword(terminal_id)

    # Lazy import para evitar circular dependency con orchestrator.py
    try:
        from plotspace.routers.orchestrator import procesar_task_event_interno
        await procesar_task_event_interno(terminal_id, keyword, project_id, motivo=(motivo or ''))
    except Exception as e:
        print(f'[monitor] Error notificando orquestador: {e}')


def iniciar_monitor(terminal_id: int, project_id: int):
    """Arranca (o reinicia) el monitor de keywords para una terminal."""
    if terminal_id in keyword_monitors:
        keyword_monitors[terminal_id].cancel()
    task = asyncio.create_task(_monitor_keywords(terminal_id, project_id))
    keyword_monitors[terminal_id] = task
    print(f'[monitor] Iniciado para terminal {terminal_id}')


def detener_monitor(terminal_id: int):
    """Cancela el monitor de keywords de una terminal."""
    task = keyword_monitors.pop(terminal_id, None)
    if task:
        task.cancel()
        print(f'[monitor] Detenido para terminal {terminal_id}')


def solicitar_chequeo_inmediato(terminal_id: int):
    """Fast-path event-driven: despierta al monitor de keywords de una terminal sin esperar
    su sleep. Lo llama agent_watch._ciclo cuando ve un TASK_* fresco en la cola del pane (que
    ya capturó a 1s). Best-effort e IDEMPOTENTE: si no hay Event (monitor no corriendo, o la
    ventana entre cancel y recreación) es no-op → el monitor avanza igual por su timeout de 2s.
    NO procesa el evento ni toca DB/orquestador: SOLO adelanta el próximo tick del MISMO loop
    del monitor, que sigue siendo el único que decide (capture + diff + _linea_es_keyword) y
    escribe. Ningún modo de fallo de este fast-path puede colgar un workflow."""
    ev = _monitor_wakeups.get(terminal_id)
    if ev is not None:
        ev.set()
