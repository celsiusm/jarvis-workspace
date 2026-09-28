# JARVIS — Monitor en vivo de agentes (pestaña «Tareas» del dock).
#
# Reemplaza al kanban manual. GET /api/projects/{id}/agentes devuelve TODAS las
# terminales activas del proyecto — las que abrió el usuario y las que lanzó
# Jarvis (modo enjambre) — con UN estado derivado por agente y lo que está
# haciendo. Todo sale de fuentes que YA existen, en modo solo-lectura:
#   · tabla terminals (id, nombre, tipo_ia, fecha_creacion + origen/tarea/
#     orquestacion/orquestacion_ts cuando existen — se leen defensivamente)
#   · orq_contexto.infos_terminales → fase/esperando de agent_watch, liveness
#     (caído), archivos escritos, tarea enviada, último TASK_*, dev servers
#   · títulos vivos del pane (el mismo helper que alimenta las cards)
#   · «terminó»: agent_watch no lo guarda como nivel (solo emite el WS
#     agente_termino), así que acá se lleva un mapa chico alimentado por
#     broadcaster.escuchar (agente_termino / TASK_* → terminó; agente_trabajando /
#     agente_espera → lo limpia).

import asyncio
import re
import time
from typing import Optional

from fastapi import APIRouter

from plotspace.core.database import get_db

router = APIRouter(prefix="/api", tags=["tasks"])

# Prioridad (índice menor gana): un agente caído no puede estar esperando; uno
# que espera respuesta importa más que uno que trabaja; etc.
ESTADOS = ('caido', 'esperando', 'trabajando', 'arrancando', 'termino', 'quieto')

_TIPOS_SHELL = {'shell', 'manual', 'bash', 'zsh', ''}
_TOPE_TAREA = 600
_TOPE_ARCHIVOS = 8

# ─── «Terminó» (nivel) desde los eventos del broadcaster ─────────────────────
# tid → {'ts': monotonic, 'via': 'agente_termino'|'TASK_DONE'|..., 'motivo': str}
_terminados: dict = {}
_listener_registrado = False


def marcar_evento(data: dict, ahora: float = None) -> None:
    """Aplica un evento del broadcaster al mapa de «terminó». Puro salvo el
    dict del módulo (testeable llamándolo directo)."""
    tipo = data.get('type')
    tid = data.get('terminal_id')
    if tid is None:
        return
    try:
        tid = int(tid)
    except (TypeError, ValueError):
        return
    ahora = time.monotonic() if ahora is None else ahora
    if tipo == 'agente_termino':
        _terminados[tid] = {'ts': ahora, 'via': 'agente_termino', 'motivo': ''}
    elif tipo == 'task_event' and data.get('event') in ('TASK_DONE', 'TASK_BLOCKED', 'TASK_ERROR'):
        _terminados[tid] = {'ts': ahora, 'via': data.get('event'),
                            'motivo': data.get('motivo') or ''}
    elif tipo in ('agente_trabajando', 'agente_espera'):
        _terminados.pop(tid, None)
    if len(_terminados) > 512:
        for k in sorted(_terminados, key=lambda k: _terminados[k]['ts'])[:len(_terminados) - 512]:
            _terminados.pop(k, None)


async def _on_evento(data: dict):
    marcar_evento(data)


def registrar_listener() -> None:
    global _listener_registrado
    if _listener_registrado:
        return
    try:
        from plotspace.core.events import broadcaster
        broadcaster.escuchar(_on_evento)
        _listener_registrado = True
    except Exception as e:           # nunca romper el import del router
        print(f'[tasks] no se pudo registrar el listener: {e}')


registrar_listener()


# ─── Lógica pura ──────────────────────────────────────────────────────────────

def derivar_estado(info: dict, termino: bool = False) -> str:
    """UN estado por agente, con prioridad
    caido > esperando > trabajando > arrancando > termino > quieto."""
    info = info or {}
    if info.get('estado') in ('caido', 'sin_sesion'):
        return 'caido'
    if info.get('esperando'):
        return 'esperando'
    fase = info.get('fase') or ''
    if fase == 'trabajando':
        return 'trabajando'
    if fase == 'arrancando':
        return 'arrancando'
    if termino:
        return 'termino'
    return 'quieto'


# El texto que Jarvis/el orquestador le pega a un agente trae la tarea + el
# protocolo (memoria, TASK_DONE, sentinel…). Al monitor le interesa la tarea.
_CORTES_TAREA = re.compile(
    r'(\n\s*\n\s*(antes de empezar|cuando termines|al terminar|when you finish|'
    r'before starting|protocolo|cierre de tarea|task closure)|'
    r'\bcuando termines\b|\bTASK_DONE\b)', re.I)


def limpiar_tarea(texto) -> str:
    t = (texto or '').strip()
    if not t:
        return ''
    m = _CORTES_TAREA.search(t)
    if m and m.start() > 0:
        t = t[:m.start()].rstrip(' \n.,;:-')
    if len(t) > _TOPE_TAREA:
        t = t[:_TOPE_TAREA - 1].rstrip() + '…'
    return t


def _num(v):
    try:
        return None if v is None else max(0, int(v))
    except (TypeError, ValueError):
        return None


def armar_agente(row: dict, info: dict, titulo: Optional[str], term: Optional[dict],
                 pregunta_en_pantalla: bool = False, ahora: float = None) -> dict:
    """Arma el dict público de un agente. Puro."""
    ahora = time.monotonic() if ahora is None else ahora
    info = dict(info or {})
    tipo = (row.get('tipo_ia') or 'manual').strip().lower()
    # Prompt a la vista (p.ej. «¿confiás en esta carpeta?» del boot) que la
    # máquina de agent_watch no marcó porque no venía de un ciclo de trabajo.
    if (pregunta_en_pantalla and not info.get('esperando') and tipo not in _TIPOS_SHELL
            and info.get('fase') in ('arrancando', 'idle', '') and not term):
        info['esperando'] = True
    estado = derivar_estado(info, termino=bool(term))

    if estado == 'termino' and term:
        hace = ahora - term['ts']
    elif estado == 'caido':
        hace = None
    else:
        hace = info.get('fase_hace_s')

    # Tarea: la columna de la terminal (la escribe quien la lanzó) o la última
    # que se le pegó (orq_contexto.registrar_envio).
    tarea = None
    if row.get('tarea'):
        tarea = {'texto': limpiar_tarea(row['tarea']), 'hace_s': None}
    elif isinstance(info.get('tarea'), dict) and info['tarea'].get('texto'):
        tarea = {'texto': limpiar_tarea(info['tarea']['texto']),
                 'hace_s': _num(info['tarea'].get('hace_s'))}
    if tarea and not tarea['texto']:
        tarea = None

    ev = info.get('ultimo_evento') or None
    ultimo = None
    if ev:
        ultimo = {'event': ev.get('event'), 'motivo': ev.get('motivo') or '',
                  'hace_s': _num(ev.get('hace_s'))}
    elif term and term.get('via', '').startswith('TASK_'):
        ultimo = {'event': term['via'], 'motivo': term.get('motivo') or '',
                  'hace_s': _num(ahora - term['ts'])}

    origen = (row.get('origen') or '').strip().lower()
    return {
        'id': row['id'],
        'nombre': row.get('nombre') or f"T{row['id']}",
        'tipo_ia': tipo,
        'origen': 'jarvis' if origen == 'jarvis' else 'usuario',
        'orquestacion': (row.get('orquestacion') or '').strip() or None,
        'orquestacion_ts': row.get('orquestacion_ts') or None,
        'creado': row.get('fecha_creacion'),
        'estado': estado,
        'estado_hace_s': _num(hace),
        'fase': info.get('fase') or '',
        'titulo': titulo or None,
        'tarea': tarea,
        'archivos': [{'path': f.get('path'), 'writes': f.get('writes', 0)}
                     for f in (info.get('archivos') or [])[:_TOPE_ARCHIVOS] if f.get('path')],
        'ultimo_evento': ultimo,
        'dev_servers': list(info.get('dev_servers') or []),
    }


def resumir(agentes: list) -> dict:
    out = {}
    for a in agentes:
        out[a['estado']] = out.get(a['estado'], 0) + 1
    return out


def agrupar_orquestaciones(agentes: list) -> list:
    """Agentes lanzados por Jarvis agrupados por objetivo de la orquestación
    (el más reciente primero). Los de origen jarvis sin objetivo van juntos."""
    grupos = {}
    for a in agentes:
        if a['origen'] != 'jarvis':
            continue
        clave = a['orquestacion'] or ''
        g = grupos.setdefault(clave, {'objetivo': a['orquestacion'], 'ts': None,
                                      'agentes': [], 'resumen': {}})
        g['agentes'].append(a['id'])
        g['resumen'][a['estado']] = g['resumen'].get(a['estado'], 0) + 1
        ts = a.get('orquestacion_ts') or a.get('creado')
        if ts and (g['ts'] is None or str(ts) < str(g['ts'])):
            g['ts'] = ts
    return sorted(grupos.values(), key=lambda g: str(g['ts'] or ''), reverse=True)


# ─── Fuentes (impuras, cada una degrada a vacío) ─────────────────────────────

def _terminales(project_id: int) -> list:
    conn = get_db()
    try:
        filas = conn.execute(
            'SELECT * FROM terminals WHERE project_id = ? AND activa = 1 ORDER BY id',
            (project_id,)).fetchall()
    finally:
        conn.close()
    campos = ('id', 'nombre', 'tipo_ia', 'fecha_creacion', 'origen', 'tarea',
              'orquestacion', 'orquestacion_ts')
    out = []
    for f in filas:
        keys = set(f.keys())
        out.append({k: (f[k] if k in keys else None) for k in campos})
    return out


def _infos(project_id: int, terminales: list) -> dict:
    try:
        from plotspace.core import orq_contexto
        return orq_contexto.infos_terminales(project_id, terminales) or {}
    except Exception as e:
        print(f'[tasks] infos_terminales falló: {e}')
        return {}


def _titulos() -> dict:
    try:
        from plotspace.routers.terminals import _titulos_vivos_tmux
        return _titulos_vivos_tmux() or {}
    except Exception:
        return {}


async def _preguntas(tids: list) -> dict:
    """{tid: bool} — ¿hay un prompt interactivo en la cola del pane? Usa la
    captura compartida (cache TTL) de pane_capture: no suma forks de tmux."""
    if not tids:
        return {}
    try:
        from plotspace.core import pane_capture
        from plotspace.core.agent_watch import hay_pregunta
    except Exception:
        return {}

    async def una(tid):
        try:
            texto = await asyncio.wait_for(pane_capture.capturar(tid, ttl=2.0), 2.5)
            return tid, hay_pregunta(texto)
        except Exception:
            return tid, False
    return dict(await asyncio.gather(*[una(t) for t in tids]))


@router.get("/projects/{project_id}/agentes")
async def agentes_del_proyecto(project_id: int):
    """Monitor en vivo: {agentes, orquestaciones, resumen}."""
    terminales = await asyncio.to_thread(_terminales, project_id)
    if not terminales:
        return {'agentes': [], 'orquestaciones': [], 'resumen': {}}
    infos, titulos = await asyncio.gather(
        asyncio.to_thread(_infos, project_id, terminales),
        asyncio.to_thread(_titulos),
    )
    # Solo vale la pena mirar la pantalla de las CLIs de IA quietas/arrancando.
    candidatos = [t['id'] for t in terminales
                  if (t.get('tipo_ia') or '').lower() not in _TIPOS_SHELL
                  and (infos.get(t['id']) or {}).get('fase') in ('arrancando', 'idle', '')
                  and not (infos.get(t['id']) or {}).get('esperando')
                  and t['id'] not in _terminados]
    preguntas = await _preguntas(candidatos)
    ahora = time.monotonic()
    agentes = [armar_agente(t, infos.get(t['id']) or {}, titulos.get(t['id']),
                            _terminados.get(t['id']), preguntas.get(t['id'], False), ahora)
               for t in terminales]
    return {'agentes': agentes,
            'orquestaciones': agrupar_orquestaciones(agentes),
            'resumen': resumir(agentes)}
