"""Señal de cierre estructurada por sentinel-file — la fuente PRIMARIA del
cierre de una tarea, agnóstica al CLI.

Scrapear el pane tmux por TASK_* es frágil POR DISEÑO: si el agente imprime el
cierre con chrome de TUI (cajas, prefijos, reflow) la regex no matchea — y cada
CLI pinta distinto. Además la ventana del monitor es de 100 líneas: un cierre
tapado por output verboso se pierde.

Acá la verdad vive en un ARCHIVO que el agente escribe con un comando de shell
(`.jarvis/signals/terminal_<id>.json` con {estado, motivo, memorias_usadas};
la instrucción la agrega orchestrator._cierre_prompt_directo a cada tarea que
entrega Jarvis). El contenido es byte-idéntico sin importar qué CLI lo corrió,
no se sale de ninguna ventana y persiste a un reinicio. Un poller dedicado (NO
toca el delicado _monitor_keywords) lo lee en TODAS las terminales activas y
resuelve el cierre por la MISMA vía que el monitor (_procesar_keyword_evento →
task_events + broadcast); el parseo de pane queda como fallback.
"""
import asyncio
import json
import os

INTERVALO_S = 2

# estado del sentinel → keyword del protocolo existente.
ESTADOS = {'done': 'TASK_DONE', 'blocked': 'TASK_BLOCKED', 'error': 'TASK_ERROR'}


# ─── Lógica pura ──────────────────────────────────────────────────────────────

def parsear(texto):
    """Valida el JSON del sentinel. Devuelve {estado, keyword, motivo,
    memorias_usadas} o None.

    Rechaza basura / escritura a medias (half-write) → el caller cae al pane. NO
    confiar a ciegas: schema mínimo (estado ∈ done/blocked/error).
    `memorias_usadas` (slugs de .jarvis/memory/ que el agente leyó) mide si la
    memoria compartida se usa de verdad — basura no-lista degrada a []."""
    try:
        d = json.loads(texto)
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict):
        return None
    estado = str(d.get('estado', '')).lower().strip()
    if estado not in ESTADOS:
        return None
    usadas = d.get('memorias_usadas')
    if not isinstance(usadas, list):
        usadas = []
    usadas = [u for u in usadas if isinstance(u, str) and u.strip()]
    return {'estado': estado, 'keyword': ESTADOS[estado],
            'motivo': str(d.get('motivo') or ''), 'memorias_usadas': usadas}


def ruta_sentinel(project_ruta, terminal_id):
    """Ruta del sentinel de una terminal dentro del proyecto."""
    return os.path.join(project_ruta, '.jarvis', 'signals', f'terminal_{terminal_id}.json')


# ─── Capa impura: lee/consume el archivo ──────────────────────────────────────

def leer_y_consumir(project_ruta, terminal_id, iniciado_ts=None):
    """Lee el sentinel de la terminal. Si es válido y fresco (mtime ≥ iniciado_ts
    de la tarea, para no re-procesar un archivo viejo de una corrida anterior), lo
    BORRA (one-shot) y devuelve el dict parseado. Si no existe / es viejo / es
    half-write inválido → None (el pane sigue como fallback)."""
    path = ruta_sentinel(project_ruta, terminal_id)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    if iniciado_ts is not None and mtime < iniciado_ts:
        return None   # sentinel viejo (terminal reusada): ignorar
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            contenido = f.read()
    except OSError:
        return None
    d = parsear(contenido)
    if d is None:
        return None   # half-write / basura: dejar el archivo, reintenta el próximo ciclo
    try:
        os.remove(path)   # one-shot: no re-procesar
    except OSError:
        pass
    return d


# ─── Poller dedicado (no toca _monitor_keywords) ──────────────────────────────

def _terminales_activas():
    """Terminales activas con su proyecto — el universo del poller."""
    from plotspace.core.database import get_db
    conn = get_db()
    try:
        filas = conn.execute(
            "SELECT t.id, t.project_id, p.ruta FROM terminals t "
            "JOIN projects p ON p.id = t.project_id WHERE t.activa = 1").fetchall()
        return [dict(f) for f in filas]
    finally:
        conn.close()


def consumir_senal(project_ruta, project_id, terminal_id):
    """Lee y consume (one-shot) la señal de una terminal y registra las
    memorias que el agente dijo usar (tabla memoria_uso: el salience del
    recall). El evento en sí (task_events + broadcast) lo registra el caller
    por _procesar_keyword_evento. Devuelve el dict parseado o None."""
    d = leer_y_consumir(project_ruta, terminal_id, None)
    if d is None:
        return None
    if d.get('memorias_usadas'):
        try:
            from plotspace.core.database import registrar_uso_memorias
            registrar_uso_memorias(project_id, terminal_id,
                                   d['memorias_usadas'], d['estado'])
        except Exception as e:
            print(f'[sentinel] uso de memorias de terminal {terminal_id}: {e}')
        try:
            from plotspace.core import logs
            logs.evento('memorias_usadas', terminal_id=terminal_id,
                        project_id=project_id, slugs=d['memorias_usadas'][:20])
        except Exception:
            pass
    return d


async def poller_sentinel():
    """Background task — nunca crashea el servidor (patrón STATE.md)."""
    if os.getenv('SENTINEL', 'on').lower() == 'off':
        print('[sentinel] desactivado (SENTINEL=off)')
        return
    print(f'[sentinel] activo — poll cada {INTERVALO_S}s')
    while True:
        await asyncio.sleep(INTERVALO_S)
        try:
            await _ciclo()
        except Exception as e:
            print(f'[sentinel] Error en ciclo: {e}')


async def _ciclo():
    terminales = await asyncio.to_thread(_terminales_activas)
    for t in terminales:
        try:
            d = await asyncio.to_thread(consumir_senal, t['ruta'],
                                        t['project_id'], t['id'])
        except Exception as e:
            print(f"[sentinel] terminal {t['id']}: {e}")
            continue
        if d is None:
            continue
        print(f"[sentinel] terminal {t['id']} cerró por sentinel: {d['keyword']}")
        try:
            from plotspace.routers.terminals import _procesar_keyword_evento
            # El motivo del sentinel-file es la ÚNICA fuente estructurada del
            # "por qué" de un bloqueo/error — no descartarlo (capa Captura).
            await _procesar_keyword_evento(t['id'], t['project_id'], d['keyword'],
                                           motivo=(d['motivo'] or None))
        except Exception as e:
            print(f"[sentinel] fallo al procesar terminal {t['id']}: {e}")
