"""Watchdog de pasos colgados — la red de seguridad del swarm.

El dolor #1 del producto: un workflow se cuelga en 'running' para siempre
cuando el TASK_DONE del agente se sale de la ventana de 100 líneas del monitor
(build verboso, stack trace) o se pierde en un reinicio del server. La card
queda congelada y el usuario tiene que ir a la terminal a mirar a mano.

Este poller, por cada paso 'running' que lleva demasiado tiempo SIN producir
output (agent_watch != 'trabajando') y SIN TASK_*:
  1. RESCATE primero: re-captura el scrollback COMPLETO del pane (capture-pane
     -S -, sin la ventana de 100 líneas) y busca un TASK_* perdido. Si aparece,
     lo resuelve EN SILENCIO por la misma vía que el monitor
     (_procesar_keyword_evento) — el caso "output > 100 líneas" se auto-cura.
  2. Si no hay keyword perdido (el agente murió de verdad / quedó en un prompt),
     emite WS 'paso_estancado' con la info (toast en la UI). Una sola vez por
     episodio.

El sello `iniciado_ts` (wall-clock) lo pone el orquestador al arrancar el paso
(orchestrator._arrancar_pasos), así que el watchdog sobrevive al restart del
server. Pasos legacy sin sello se auto-sellan en el primer ciclo (no se juzgan
hasta tener edad medible).

Apagable con WATCHDOG=off. Umbral/intervalo por env (WATCHDOG_UMBRAL_S /
WATCHDOG_INTERVALO_S).
"""
import asyncio
import json
import os
import re

from plotspace.core.database import get_db
from plotspace.core import agent_watch

INTERVALO_S = int(os.getenv('WATCHDOG_INTERVALO_S', '20'))
UMBRAL_S = int(os.getenv('WATCHDOG_UMBRAL_S', '180'))   # 3 min quieto = sospechoso

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[mGKHFJA-Z]')
# TASK_* solo-en-línea (mismo filtro que terminals._linea_es_keyword), con grupo
# para saber CUÁL apareció.
_KW_RE = re.compile(r'^[^a-zA-Z]*TASK_(DONE|BLOCKED|ERROR)[^a-zA-Z]*$')

# Episodios ya avisados: (workflow_id, paso_idx) → no re-emitir cada ciclo. Se
# limpia cuando el paso deja de estar 'running' (se resolvió o el wf terminó).
_avisados: set = set()


# ─── Lógica pura ──────────────────────────────────────────────────────────────

def buscar_keyword_perdido(texto):
    """Último TASK_* real en el scrollback (escaneo desde el final), o None.

    Rescata el cierre que se salió de la ventana de 100 líneas del monitor."""
    limpio = _ANSI_RE.sub('', texto or '')
    for linea in reversed(limpio.splitlines()):
        l = linea.strip()
        if not l:
            continue
        m = _KW_RE.match(l)
        if m:
            return 'TASK_' + m.group(1)
    return None


def edad_paso(paso, ahora):
    """Segundos desde que el paso arrancó (wall-clock), o None si no hay sello."""
    ts = paso.get('iniciado_ts')
    if ts is None:
        return None
    return ahora - ts


def paso_candidato_estancado(paso, ahora, umbral, trabajando) -> bool:
    """¿Este paso merece el chequeo de rescate? running + viejo + quieto.

    `trabajando`: True si agent_watch ve output cambiando (entonces NO está
    colgado, solo es lento → no se toca)."""
    if paso.get('estado') != 'running':
        return False
    edad = edad_paso(paso, ahora)
    if edad is None:
        return False
    if edad < umbral:
        return False
    return not trabajando


# ─── Capa impura: tmux + DB + broadcaster ─────────────────────────────────────

def _workflows_running():
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            # 'paused' también: un BLOCKED pausa el workflow ENTERO, pero sus
            # pasos paralelos siguen corriendo y hay que seguir vigilándolos.
            "SELECT id, project_id, nombre, pasos FROM workflows "
            "WHERE estado IN ('running', 'paused')"
        )
        out = []
        for r in cur.fetchall():
            try:
                pasos = json.loads(r['pasos'] or '[]')
            except (ValueError, TypeError):
                pasos = []
            out.append({'id': r['id'], 'project_id': r['project_id'],
                        'nombre': r['nombre'], 'pasos': pasos})
        return out
    finally:
        conn.close()


async def _capturar_scrollback(terminal_id: int) -> str:
    """TODO el buffer del pane (sin el límite de 100 líneas del monitor).

    Por el motor, no por tmux directo: en Windows no hay binario `tmux` y este
    rescate —releer el buffer entero para encontrar un TASK_* que quedó fuera
    de la ventana— es justamente el que no puede fallar en silencio.
    """
    from plotspace.core.terminal_backend import backend, TODO_EL_SCROLLBACK
    try:
        return await backend().capturar_async(terminal_id, TODO_EL_SCROLLBACK)
    except Exception:   # motor caído / sesión inexistente / timeout
        return ''


def _esta_trabajando(terminal_id: int) -> bool:
    st = agent_watch._estados.get(terminal_id)
    return bool(st and st.get('fase') == 'trabajando')


async def poller_watchdog():
    """Background task — nunca crashea el servidor (patrón STATE.md)."""
    if os.getenv('WATCHDOG', 'on').lower() == 'off':
        print('[watchdog] desactivado (WATCHDOG=off)')
        return
    print(f'[watchdog] activo — umbral {UMBRAL_S}s, intervalo {INTERVALO_S}s')
    while True:
        await asyncio.sleep(INTERVALO_S)
        try:
            await _ciclo()
        except Exception as e:
            print(f'[watchdog] Error en ciclo: {e}')


async def _ciclo():
    import time
    from plotspace.core.events import broadcaster

    wfs = await asyncio.to_thread(_workflows_running)
    ahora = time.time()

    # Set de pasos running ahora (para podar _avisados de episodios resueltos).
    running_ahora = set()
    for wf in wfs:
        for idx, paso in enumerate(wf['pasos']):
            if paso.get('estado') == 'running' and paso.get('terminal_id'):
                running_ahora.add((wf['id'], idx))
    for clave in list(_avisados):
        if clave not in running_ahora:
            _avisados.discard(clave)

    for wf in wfs:
        pasos = wf['pasos']
        sin_sello = False
        for idx, paso in enumerate(pasos):
            tid = paso.get('terminal_id')
            if paso.get('estado') != 'running' or not tid:
                continue

            # Auto-sello de pasos legacy sin iniciado_ts: medir desde ahora.
            if paso.get('iniciado_ts') is None:
                sin_sello = True
                continue

            trabajando = _esta_trabajando(tid)
            if not paso_candidato_estancado(paso, ahora, UMBRAL_S, trabajando):
                continue

            # Candidato: rescatar primero (TASK_* perdido fuera de la ventana).
            scrollback = await _capturar_scrollback(tid)
            kw = buscar_keyword_perdido(scrollback)
            if kw:
                print(f'[watchdog] rescatando paso {idx} de {wf["id"]}: {kw} perdido en terminal {tid}')
                try:
                    from plotspace.routers.terminals import _procesar_keyword_evento
                    await _procesar_keyword_evento(tid, wf['project_id'], kw)
                except Exception as e:
                    print(f'[watchdog] fallo el rescate de terminal {tid}: {e}')
                _avisados.discard((wf['id'], idx))
                await broadcaster.broadcast(wf['project_id'], {
                    'type': 'paso_rescatado',
                    'terminal_id': tid,
                    'terminal_nombre': paso.get('agente'),
                    'workflow_id': wf['id'],
                    'paso_idx': idx,
                    'keyword': kw,
                })
                continue

            # Sin keyword perdido: el agente murió o quedó en un prompt. Avisar
            # UNA vez por episodio.
            clave = (wf['id'], idx)
            if clave in _avisados:
                continue
            _avisados.add(clave)
            ultima = _ultima_linea_scrollback(scrollback)
            print(f'[watchdog] paso {idx} de {wf["id"]} ESTANCADO en terminal {tid} '
                  f'(hace {int(ahora - paso["iniciado_ts"])}s)')
            await broadcaster.broadcast(wf['project_id'], {
                'type': 'paso_estancado',
                'terminal_id': tid,
                'terminal_nombre': paso.get('agente'),
                'workflow_id': wf['id'],
                'workflow_nombre': wf['nombre'],
                'paso_idx': idx,
                'edad_seg': int(ahora - paso['iniciado_ts']),
                'ultima_linea': ultima,
            })

        if sin_sello:
            # Re-lee y sella DENTRO del lock del workflow: reescribir esta copia
            # (leída hace un rato) pisaba un done recién guardado y forzaba
            # 'running' sobre un workflow en pausa.
            from plotspace.routers.orchestrator import sellar_pasos_sin_inicio
            await sellar_pasos_sin_inicio(wf['id'], ahora)


def _ultima_linea_scrollback(texto) -> str:
    limpio = _ANSI_RE.sub('', texto or '')
    lineas = [l.strip() for l in limpio.splitlines() if l.strip()]
    return lineas[-1] if lineas else ''
