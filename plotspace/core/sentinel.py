"""Señal de cierre estructurada por sentinel-file — mata la fragilidad del
parseo de panes y desbloquea Codex/Gemini en el cierre.

Hoy el cierre de un paso se detecta scrapeando el pane tmux por TASK_* (regex
que exige la keyword SOLA en la línea). Eso es frágil POR DISEÑO: si el agente
imprime el cierre con chrome de TUI (cajas, prefijos, reflow) la regex no
matchea — y cada CLI pinta distinto, así que el protocolo está de-facto atado a
Claude Code. Además la ventana del monitor es de 100 líneas: un cierre tapado
por output verboso se pierde.

Acá la verdad pasa a vivir en un ARCHIVO que el agente escribe con un comando de
shell (`.jarvis/signals/terminal_<id>.json` con {estado, motivo}). El contenido
es byte-idéntico sin importar qué CLI lo corrió (no pasa por el render de la
TUI), no se sale de ninguna ventana y persiste a un reinicio. Un poller dedicado
(NO toca el delicado _monitor_keywords) lo lee como fuente primaria y resuelve
el paso por la MISMA vía que el monitor (_procesar_keyword_evento); el parseo de
pane queda como fallback. Degrada con elegancia: sin sentinel, todo sigue igual.
"""
import asyncio
import json
import os
from datetime import datetime

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


def instruccion_cierre(terminal_id):
    """Texto que el ENGINE agrega a la tarea (igual que el bloque de ownership):
    reliable, no depende de que el LLM lo incluya. Funciona en cualquier CLI con
    shell (Claude/Codex/Gemini). Pide el post-mortem mínimo: motivo obligatorio
    al fallar (materia prima de las lecciones) + memorias_usadas (medición)."""
    return (
        "\n\nCIERRE ESTRUCTURADO (ADEMÁS del TASK_DONE de arriba, no en vez de): "
        "apenas termines, ejecutá este comando para señalar tu estado de forma "
        "confiable (independiente de cómo se vea tu terminal):\n"
        f"  mkdir -p .jarvis/signals && printf '%s' '{{\"estado\":\"done\",\"memorias_usadas\":[]}}' > .jarvis/signals/terminal_{terminal_id}.json\n"
        "En \"memorias_usadas\" listá los slugs de .jarvis/memory/ que leíste y te "
        "sirvieron ([] si ninguna). Si te bloqueás usá \"blocked\"; si hubo error, "
        "\"error\" — en ambos casos el campo \"motivo\" es OBLIGATORIO y concreto "
        "(qué falló y por qué, no 'hubo un problema'). En \"done\" el motivo es "
        "opcional: si un enfoque no-obvio te funcionó, resumilo en una línea "
        "(alimenta las lecciones del enjambre). Si el tropiezo se podía "
        "prevenir con una regla corta, guardá además una memoria con tags "
        "[leccion] según el protocolo. "
        "Hacé LAS DOS cosas: TASK_DONE en el pane Y el archivo."
    )


# ─── Capa impura: lee/consume el archivo ──────────────────────────────────────

def leer_y_consumir(project_ruta, terminal_id, iniciado_ts=None):
    """Lee el sentinel de la terminal. Si es válido y fresco (mtime ≥ iniciado_ts
    del paso, para no re-procesar un archivo viejo de una corrida anterior), lo
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

def _workflows_running():
    from plotspace.core.database import get_db
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT w.id, w.project_id, w.pasos, p.ruta "
            "FROM workflows w JOIN projects p ON p.id = w.project_id "
            "WHERE w.estado IN ('running', 'paused')"
        )
        out = []
        for r in cur.fetchall():
            try:
                pasos = json.loads(r['pasos'] or '[]')
            except (ValueError, TypeError):
                pasos = []
            out.append({'id': r['id'], 'project_id': r['project_id'],
                        'ruta': r['ruta'], 'pasos': pasos})
        return out
    finally:
        conn.close()


def _terminales_activas():
    """Terminales activas con su proyecto — el universo de la pasada libre."""
    from plotspace.core.database import get_db
    conn = get_db()
    try:
        filas = conn.execute(
            "SELECT t.id, t.project_id, p.ruta FROM terminals t "
            "JOIN projects p ON p.id = t.project_id WHERE t.activa = 1").fetchall()
        return [dict(f) for f in filas]
    finally:
        conn.close()


# Pasos cuya señal es del WORKFLOW (la pasada libre no debe comérsela): el
# running, y el blocked — su agente puede destrabarse y cerrar el paso. Antes
# un workflow en pausa por un BLOCKED quedaba fuera de la pasada y las señales
# de sus pasos paralelos se consumían como "libres" sin avanzar nada.
_ESTADOS_VIGILADOS = ('running', 'blocked')


def terminales_libres(terminales: list, wfs: list) -> list:
    """Filtra las terminales que NO son un paso running de un workflow — esas
    señales las consume la pasada de workflows (que además avanza el paso)."""
    ocupadas = {p.get('terminal_id') for wf in wfs for p in wf.get('pasos', [])
                if p.get('estado') in _ESTADOS_VIGILADOS and p.get('terminal_id')}
    return [t for t in terminales if t['id'] not in ocupadas]


def procesar_senal_libre(project_ruta, project_id, terminal_id):
    """Cierre estructurado FUERA de workflows. El enjambre trabaja mayormente
    en terminales directas — sin esta pasada, la telemetría (motivos →
    lecciones, memorias_usadas → salience del recall) solo existía en modo
    workflow: task_events VACÍA fue el síntoma. Acá no hay paso que avanzar ni
    orquestador que notificar: registrar y punto. Devuelve el dict o None."""
    d = leer_y_consumir(project_ruta, terminal_id, None)
    if d is None:
        return None
    try:
        from plotspace.core.database import get_db, registrar_uso_memorias
        conn = get_db()
        try:
            conn.execute(
                'INSERT INTO task_events (terminal_id, project_id, event, timestamp, '
                'workflow_id, motivo) VALUES (?, ?, ?, ?, NULL, ?)',
                (terminal_id, project_id, d['keyword'],
                 datetime.now().isoformat(), (d['motivo'] or None)))
            conn.commit()
        finally:
            conn.close()
        if d.get('memorias_usadas'):
            registrar_uso_memorias(project_id, terminal_id,
                                   d['memorias_usadas'], d['estado'])
    except Exception as e:
        print(f'[sentinel] señal libre de terminal {terminal_id}: {e}')
    try:
        from plotspace.core import logs
        logs.evento('senal_libre', terminal_id=terminal_id, project_id=project_id,
                    keyword=d['keyword'],
                    **({'motivo': d['motivo'][:300]} if d['motivo'] else {}))
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
    wfs = await asyncio.to_thread(_workflows_running)
    for wf in wfs:
        for paso in wf['pasos']:
            tid = paso.get('terminal_id')
            if paso.get('estado') not in _ESTADOS_VIGILADOS or not tid:
                continue
            d = leer_y_consumir(wf['ruta'], tid, paso.get('iniciado_ts'))
            if d is None:
                continue
            print(f"[sentinel] paso de terminal {tid} cerró por sentinel: {d['keyword']}")
            if d.get('memorias_usadas'):
                # Medición de lectura CRUZADA con el resultado del paso: la
                # tabla memoria_uso es la fuente del salience del recall (una
                # memoria leída en pasos done sube; leída solo en fallos, no).
                # El audit trail queda como rastro humano-legible.
                try:
                    from plotspace.core.database import registrar_uso_memorias
                    registrar_uso_memorias(wf['project_id'], tid,
                                           d['memorias_usadas'], d['estado'])
                except Exception:
                    pass
                try:
                    from plotspace.core import logs
                    logs.evento('memorias_usadas', terminal_id=tid,
                                project_id=wf['project_id'],
                                slugs=d['memorias_usadas'][:20])
                except Exception:
                    pass
            try:
                from plotspace.routers.terminals import _procesar_keyword_evento
                # El motivo del sentinel-file es la ÚNICA fuente estructurada del
                # "por qué" de un bloqueo/error — no descartarlo (capa Captura).
                await _procesar_keyword_evento(tid, wf['project_id'], d['keyword'],
                                               motivo=(d['motivo'] or None))
            except Exception as e:
                print(f'[sentinel] fallo al procesar terminal {tid}: {e}')

    # Pasada LIBRE (después de la de workflows, que tiene prioridad sobre sus
    # pasos running): consume las señales de las demás terminales activas.
    try:
        terminales = await asyncio.to_thread(_terminales_activas)
        for t in terminales_libres(terminales, wfs):
            d = await asyncio.to_thread(procesar_senal_libre,
                                        t['ruta'], t['project_id'], t['id'])
            if d:
                print(f"[sentinel] cierre libre de terminal {t['id']}: {d['keyword']}")
    except Exception as e:
        print(f'[sentinel] pasada libre: {e}')
