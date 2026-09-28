"""Snapshot del scrollback de las terminales SHELL, para persistir su historial
visual tras un corte de luz / reboot.

Un CLI (claude/codex/qwen/…) reanuda su CONVERSACIÓN por su cuenta (ver
terminals._comando_lanzamiento). Un shell no tiene "conversación", así que acá
guardamos su pantalla+scrollback a disco cada pocos segundos; cuando reconciliar
recrea la sesión tras un reboot, se re-imprime ese snapshot → el usuario ve su
historial de comandos donde lo dejó, en vez de un shell en blanco.

Límite HONESTO: es persistencia VISUAL. Los procesos que corrían murieron con el
corte y NO reviven; lo que vuelve es el texto (comandos + salida). El cwd y el
`.bash_history` los persiste bash solo. Ver [[persistencia-resume-terminales]].
"""
import asyncio
import os
import shlex
import subprocess

from plotspace.core.database import get_db
from plotspace.core.terminal_backend import backend

SNAP_LINEAS = 800     # líneas de scrollback que guardamos por shell
INTERVALO_S = 20      # cada cuánto refrescamos el snapshot


def ruta_snapshot(project_path: str, terminal_id: int) -> str:
    d = os.path.join(project_path, '.workspace', 'snapshots')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f'terminal_{terminal_id}.snap')


def comando_restore_shell(project_path: str, terminal_id: int):
    """Comando para _crear_sesion_tmux que re-imprime el snapshot del shell (si
    existe). None si no hay snapshot → shell pelado. _crear_sesion_tmux_sync le
    agrega `; exec bash -l`, así que tras re-imprimir queda un shell usable."""
    try:
        snap = ruta_snapshot(project_path, terminal_id)
    except Exception:
        return None
    if not os.path.exists(snap):
        return None
    q = shlex.quote(snap)
    from plotspace.core.idioma_ui import L
    marca = L('— sesión anterior restaurada (los procesos no siguen vivos) —',
              '— previous session restored (its processes are no longer running) —')
    return f"cat {q} 2>/dev/null; echo {shlex.quote(marca)}"


def _shells_activos():
    """(id, ruta_proyecto) de las terminales SHELL (tipo_ia='manual') activas."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT t.id, p.ruta FROM terminals t JOIN projects p ON t.project_id = p.id "
            "WHERE t.activa = 1 AND t.tipo_ia = 'manual'"
        ).fetchall()
    finally:
        conn.close()
    return [(r['id'], r['ruta']) for r in rows]


def _snapshot_uno(terminal_id: int, project_path: str):
    try:
        cap = backend().capturar(terminal_id, lineas=SNAP_LINEAS,
                                 con_escapes=True) or ''
    except Exception:
        return
    if not cap.strip():
        return                       # pane vacío → no pisar un snapshot bueno con nada
    cap = cap.rstrip('\n') + '\n'    # sin la cola de líneas en blanco del capture
    try:
        with open(ruta_snapshot(project_path, terminal_id), 'w',
                  encoding='utf-8', errors='replace') as f:
            f.write(cap)
    except Exception:
        pass


async def poller_snapshots():
    """Cada INTERVALO_S guarda el scrollback de cada shell activo (todo en threads
    para no tocar el event loop). Nunca crashea el server."""
    while True:
        try:
            shells = await asyncio.to_thread(_shells_activos)
            for tid, ruta in shells:
                await asyncio.to_thread(_snapshot_uno, tid, ruta)
        except Exception as e:
            print(f'[snapshot] error en el poller: {e}')
        await asyncio.sleep(INTERVALO_S)
