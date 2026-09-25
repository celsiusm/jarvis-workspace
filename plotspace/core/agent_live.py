# plotspace/core/agent_live.py
"""Agents Live — tracking en vivo de qué archivo toca cada agente.

Poller background (patrón dev_detect): cada 2s captura las últimas ~120
líneas del pane tmux de cada terminal activa y parsea las operaciones de
archivo que las CLIs imprimen (`● Update(path)` etc.). Con eso mantiene:

  - qué archivos tocó cada agente (reads/writes + timestamps)
  - PROPIEDAD: el primer agente que escribe un archivo es su dueño
    (expira a los 10 min sin tocarlo o al morir su terminal)
  - PERMISOS: el watcher del mailbox nos pasa los mensajes y parseamos
    la convención `PERMISO <archivo>` / `OK <archivo>` / `NO <archivo>`
  - `.jarvis/LIVE.md` del proyecto (regenerado completo, gitignored)

Jarvis es 100% PASIVO acá (contrato 2026-06-07): NUNCA escribe en la
terminal de un agente. El agente sabe por el protocolo de su CLAUDE.md
que tiene `.jarvis/LIVE.md` para mirar quién toca qué; los conflictos
solo se registran (actividad + WS → toast/pestaña Live, para el humano).
El único que entrega mensajes a terminales es el mailbox agente-a-agente.

Eventos WS por el broadcaster del proyecto (todos llevan snapshot completo
— el estado es chico, no vale la pena delta):
  - live_update       {snapshot}
  - permiso_pedido    {permiso, snapshot}
  - permiso_resuelto  {permiso, snapshot}
  - conflicto_archivo {archivo, dueno_nombre, intruso_nombre, terminal_id, snapshot}

Formatos de op por CLI: ver _PATRONES_OPS. Para agregar una CLI nueva,
capturá su pane real (`tmux capture-pane -t jarvis_<id> -p -S -120`) y
sumá su regex + test.
"""

import asyncio
import os
import re
import tempfile
import time
from collections import deque
from datetime import datetime

from plotspace.core.database import get_db
from plotspace.core.events import broadcaster
from plotspace.core import pane_capture
from plotspace import protocolos

INTERVALO_S = 2
PROPIEDAD_TTL_S = 600      # 10 min sin tocar el archivo → propiedad expira
ALERTA_THROTTLE_S = 600    # 1 alerta por (archivo, par de agentes) cada 10 min
MAX_ACTIVIDAD = 50

# Eventos de fase que emite agent_watch: al recibirlos empujamos un snapshot al
# instante (un agente recién nacido dispara 'agente_trabajando' en cuanto imprime
# su banner → aparece en Live sin esperar el poll de 2s; y el estado se refresca).
_TIPOS_FASE = {'agente_trabajando', 'agente_espera', 'agente_termino'}

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[mGKHFJA-Z]')

# Operaciones de archivo por CLI. La línea debe ARRANCAR con el bullet de la
# CLI (filtra menciones en prosa: "usá Update(x)" citado en una tarea).
# Claude Code: `● Update(path)` (relevado de panes reales 2026-06-07).
# Codex/Gemini: agregar acá al relevar sus panes (ver docstring).
_PATRONES_OPS = [
    re.compile(r'^[●⏺]\s*(?P<verbo>Update|Write|Create|Edit|NotebookEdit|Read)\((?P<path>[^)\n]+)\)'),
]
_VERBOS_READ = {'Read'}


# ─── Lógica pura (testeable sin tmux ni asyncio) ──────────────────────────────

def _limpiar_path(p: str) -> str:
    p = p.strip().strip('"\'')
    if p.startswith('./'):
        p = p[2:]
    return p


def operaciones_nuevas(texto, vistos: dict) -> list:
    """[(op, path)] de un texto de pane (op ∈ {'read','write'}), solo lo
    NUEVO respecto de la captura anterior (ventanas solapadas). `vistos` (mutado en el lugar) guarda
    {hash_linea: ocurrencias_en_la_captura_anterior}: una línea repetida MÁS
    veces que antes son ops nuevas. Una re-edición real del mismo archivo
    imprime una línea idéntica — el dedup viejo (presencia en un FIFO sin
    expiración) la perdía para siempre: la propiedad del dueño expiraba
    aunque siguiera editando y las escrituras repetidas de un intruso
    pasaban sin alerta. Lo que scrolleó fuera del pane desaparece de
    `vistos` con esta captura: si reaparece después, cuenta. Caso aceptado:
    una ocurrencia sale y otra idéntica entra en el MISMO intervalo de 2s
    → esa no se cuenta."""
    limpio = _ANSI_RE.sub('', texto or '')
    ops = []
    ahora_counts: dict = {}
    for linea in limpio.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        match = None
        for pat in _PATRONES_OPS:
            match = pat.match(linea)
            if match:
                break
        if not match:
            continue
        h = hash(linea)
        ahora_counts[h] = ahora_counts.get(h, 0) + 1
        if ahora_counts[h] <= vistos.get(h, 0):
            continue
        op = 'read' if match.group('verbo') in _VERBOS_READ else 'write'
        ops.append((op, _limpiar_path(match.group('path'))))
    vistos.clear()
    vistos.update(ahora_counts)
    return ops


def relativo_al_proyecto(path: str, ruta_proyecto: str) -> str:
    """Una CLI puede imprimir el path absoluto y otra el relativo, y con
    shells mezclados el MISMO archivo llega como `C:\\proy\\app\\x.js` desde
    PowerShell y como `/mnt/c/proy/app/x.js` desde WSL. Sin unificarlos, el
    mismo archivo tiene DOS claves de propiedad y el conflicto entre sus
    dueños NO se detecta — se pisan en silencio.

    La forma canónica (relativa al proyecto, con `/`) vive en `core/rutas`;
    acá solo se la aplica. Fuera del proyecto queda absoluta, normalizada."""
    from plotspace.core import rutas
    return rutas.canonica(path, ruta_proyecto)


def dueno_vigente(d, ahora) -> bool:
    """¿El registro de propiedad sigue vivo? (tocó el archivo hace <TTL)."""
    return bool(d) and (ahora - d['ultima']) < PROPIEDAD_TTL_S


def registrar_escritura(duenos: dict, clave: tuple, tid: int, nombre: str,
                        ahora: float) -> tuple:
    """clave = (project_id, path). Devuelve (resultado, dueño_vigente):
      'nueva'     → no había dueño vigente: tid lo es desde ahora
      'propia'    → tid ya era el dueño: renueva ultima
      'conflicto' → otro es el dueño vigente (se devuelve ESE registro)"""
    d = duenos.get(clave)
    if not dueno_vigente(d, ahora):
        d = {'tid': tid, 'nombre': nombre, 'desde': ahora, 'ultima': ahora}
        duenos[clave] = d
        return 'nueva', d
    if d['tid'] == tid:
        d['ultima'] = ahora
        return 'propia', d
    return 'conflicto', d


def aplicar_op(archivos: dict, duenos: dict, pid: int, tid: int, nombre: str,
               ruta_proyecto: str, op: str, path: str, ahora: float = None) -> tuple:
    """Aplica UNA operación de archivo al estado (archivos + dueños). Pura: el
    caller pasa los dicts (el poller pasa los del módulo; los tests, los suyos).

    Es la vía de ingesta del hook PostToolUse, que reemplaza al parseo de panes:
    el CLI dejó de imprimir `● Update(archivo)` y pasó a resúmenes colapsados que
    ni nombran el archivo, así que la propiedad se leía de una pantalla que ya no
    dice nada. Acá el dato viene del CONTRATO de la herramienta.

    Devuelve (path_normalizado, resultado, dueño):
      resultado ∈ 'read' | 'nueva' | 'propia' | 'conflicto'; None si no hubo op."""
    ahora = time.monotonic() if ahora is None else ahora
    path = relativo_al_proyecto(_limpiar_path(path or ''), ruta_proyecto)
    if not path:
        return '', None, None
    info = archivos.setdefault(tid, {}).setdefault(
        path, {'reads': 0, 'writes': 0, 'primera': ahora, 'ultima': ahora})
    info['reads' if op == 'read' else 'writes'] += 1
    info['ultima'] = ahora
    if op != 'write':
        return path, 'read', None
    res, dueno = registrar_escritura(duenos, (pid, path), tid, nombre, ahora)
    return path, res, dueno


def debe_alertar(alertas: dict, clave, ahora: float) -> bool:
    """Throttle: True (y registra) si no se alertó esta clave hace <THROTTLE."""
    t = alertas.get(clave)
    if t is not None and ahora - t < ALERTA_THROTTLE_S:
        return False
    alertas[clave] = ahora
    return True


def cambios_de_roster(roster_prev: dict, roster_actual: dict,
                      fases_prev: dict, fases_actual: dict,
                      tid_pid: dict) -> set:
    """pids que cambiaron desde el ciclo anterior por ALTA/BAJA de terminal o
    por cambio de fase (idle↔trabajando) de alguna de sus terminales. Sirve para
    emitir un live_update AUNQUE nadie haya tocado un archivo — antes, sin
    file-op, el poller no emitía y un agente recién creado no aparecía en la
    pestaña Live hasta que escribía algo (o hasta reabrir la pestaña). Puro.
      roster_prev / roster_actual: pid → set(tids) activos
      fases_prev  / fases_actual:  tid → fase ('trabajando' / 'idle' / ...)
      tid_pid:                     tid → pid del roster ACTUAL (para la fase)"""
    cambios = set()
    for pid in set(roster_prev) | set(roster_actual):
        if set(roster_prev.get(pid) or ()) != set(roster_actual.get(pid) or ()):
            cambios.add(pid)
    for tid, pid in tid_pid.items():
        if fases_actual.get(tid, 'idle') != fases_prev.get(tid, 'idle'):
            cambios.add(pid)
    return cambios


# `PERMISO <archivo> — detalle` / `OK <archivo>: detalle` / `NO <archivo> - detalle`
# El archivo no puede EMPEZAR con coma (filtra "OK, lo miro" — prosa común);
# la puntuación final se limpia con rstrip al extraerlo.
_PERMISO_RE = re.compile(
    r'^\s*(?P<tipo>PERMISO|OK|NO)\s+(?P<archivo>[^\s,][^\s]*)\s*(?:[—–:-]+\s*)?(?P<detalle>.*)$',
    re.IGNORECASE,
)


def parsear_permiso(msg):
    """Convención liviana del mailbox. None si el mensaje no la usa.
    El archivo debe parecer un path (tener '.' o '/'): "OK dale nomás"
    no es una respuesta de permiso."""
    if not msg:
        return None
    m = _PERMISO_RE.match(msg)
    if not m:
        return None
    archivo = m.group('archivo').rstrip('.,;:')
    if '.' not in archivo and '/' not in archivo:
        return None
    return {'tipo': m.group('tipo').lower(), 'archivo': archivo,
            'detalle': m.group('detalle').strip()}


# RESERVA (mailbox v2): "voy a tocar X" ANTES de editar — la cola se forma
# antes del choque, no se negocia al commitear. Lease con TTL propio; cuando
# el reservante edita de verdad, la propiedad normal lo releva.
_RESERVA_RE = re.compile(
    r'^\s*RESERVA\s+(?P<archivo>[^\s,][^\s]*)\s*(?:[—–:-]+\s*)?(?P<detalle>.*)$',
    re.IGNORECASE,
)
RESERVA_TTL_S = 900          # 15 min: reservar y no aparecer no bloquea a nadie
_reservas: dict = {}         # (pid, path) → {'tid','nombre','detalle','ts'}


def parsear_reserva(msg):
    """None si el mensaje no es una RESERVA (mismo criterio de path que
    parsear_permiso: el archivo debe tener '.' o '/')."""
    if not msg:
        return None
    m = _RESERVA_RE.match(msg)
    if not m:
        return None
    archivo = m.group('archivo').rstrip('.,;:')
    if '.' not in archivo and '/' not in archivo:
        return None
    return {'archivo': archivo, 'detalle': m.group('detalle').strip()}


def aplicar_reserva(reservas: dict, duenos: dict, pid: int, path: str,
                    tid, nombre: str, ahora: float):
    """Intenta registrar la reserva. ('ok', None) o ('ocupada', quien) si el
    archivo tiene dueño vigente AJENO o reserva vigente AJENA."""
    for (dpid, dpath), d in duenos.items():
        if dpid == pid and _match_archivo(dpath, path) and dueno_vigente(d, ahora):
            if d['nombre'].strip().lower() != nombre.strip().lower():
                return 'ocupada', d['nombre']
    for (rpid, rpath), r in list(reservas.items()):
        if rpid == pid and _match_archivo(rpath, path) \
                and (ahora - r['ts']) < RESERVA_TTL_S \
                and r['nombre'].strip().lower() != nombre.strip().lower():
            return 'ocupada', r['nombre']
    reservas[(pid, path)] = {'tid': tid, 'nombre': nombre, 'ts': ahora}
    return 'ok', None


def _revisar_reservas(ahora: float) -> set:
    """Poda reservas vencidas (TTL). Devuelve pids con cambios."""
    cambios = set()
    for clave, r in list(_reservas.items()):
        if (ahora - r['ts']) >= RESERVA_TTL_S:
            _reservas.pop(clave, None)
            cambios.add(clave[0])
    return cambios


def _quitar_prefijo_rel(p: str) -> str:
    # PREFIJO './' (no lstrip de chars: '.gitignore' debe seguir siendo '.gitignore')
    return p[2:] if p.startswith('./') else p


def _match_archivo(a: str, b: str) -> bool:
    """'frontend/shared/ui.js' matchea 'ui.js' (el dueño suele responder
    con el basename)."""
    a, b = _quitar_prefijo_rel(a), _quitar_prefijo_rel(b)
    return a == b or a.endswith('/' + b) or b.endswith('/' + a)


def aplicar_permiso(permisos: list, de: str, para: str, parsed: dict, ahora: float):
    """Avanza la lista de permisos de un proyecto con un mensaje parseado.
    PERMISO → agrega pendiente. OK/NO → resuelve el último pendiente que
    matchee (archivo por sufijo, pide == destinatario de la respuesta).
    Devuelve el permiso afectado, o None."""
    if parsed['tipo'] == 'permiso':
        p = {'archivo': parsed['archivo'], 'pide': de, 'dueno': para,
             'detalle': parsed['detalle'], 'respuesta': '',
             'estado': 'pendiente', 'ts': ahora}
        permisos.append(p)
        return p
    # ok / no: el que responde (de) es el dueño; el destinatario (para) pidió
    for p in reversed(permisos):
        if (p['estado'] == 'pendiente'
                and _match_archivo(p['archivo'], parsed['archivo'])
                and p['pide'].strip().lower() == para.strip().lower()):
            p['estado'] = 'ok' if parsed['tipo'] == 'ok' else 'no'
            p['respuesta'] = parsed['detalle']
            return p
    return None


LIVE_NOMBRE = os.path.join('.jarvis', 'LIVE.md')

PROTOCOLO_MARKER_START = '<!-- JARVIS_LIVE_START -->'
PROTOCOLO_MARKER_END   = '<!-- JARVIS_LIVE_END -->'

# El TEXTO vive en `plotspace/protocolos/live.md` — ver ese paquete.
PROTOCOLO = protocolos.leer('live')


def _fmt_hace(s: int) -> str:
    if s < 60:
        return f'{s}s'
    if s < 3600:
        return f'{s // 60}m'
    return f'{s // 3600}h'


def armar_snapshot(project_id: int, rows: list, archivos: dict, duenos: dict,
                   permisos: list, fases: dict, actividad: list,
                   ahora: float, reservas: dict = None) -> dict:
    """Estado completo del proyecto para la API/WS/LIVE.md. Puro: recibe
    todo por parámetro (el caller pasa los dicts del módulo)."""
    agentes = []
    for row in rows:
        tid = row['tid']
        archs = []
        for path, info in sorted((archivos.get(tid) or {}).items(),
                                 key=lambda kv: -kv[1]['ultima']):
            d = duenos.get((project_id, path))
            archs.append({
                'path': path,
                'reads': info['reads'],
                'writes': info['writes'],
                'hace_s': int(ahora - info['ultima']),
                'dueno': bool(d and d['tid'] == tid and dueno_vigente(d, ahora)),
            })
        # El estado viaja TAL CUAL cuando es uno de los conocidos: colapsarlo a
        # trabajando|idle (como se hacía antes) disfrazaba de idle a un agente
        # cuyo CLI ya se había cerrado, y LIVE.md es de donde lee el guard de
        # propiedad — o sea que un fantasma seguía bloqueando commits ajenos.
        # Lo desconocido cae a 'idle': falla abierta, nunca a un estado inventado.
        fase = fases.get(tid, 'idle')
        agentes.append({
            'terminal_id': tid,
            'nombre': row['tnombre'],
            'tipo_ia': row['tipo_ia'] or 'manual',
            'estado': fase if fase in _ESTADOS_CONOCIDOS else 'idle',
            'archivos': archs,
        })
    return {
        'agentes': agentes,
        'permisos': [{**{k: v for k, v in p.items() if k != 'ts'},
                      'hace_s': int(ahora - p['ts'])} for p in permisos],
        'reservas': [{'path': path, 'nombre': r['nombre'], 'tid': r['tid'],
                      'hace_s': int(ahora - r['ts'])}
                     for (rpid, path), r in sorted((reservas or {}).items())
                     if rpid == project_id],
        'actividad': list(actividad),
    }


_ESTADOS_CONOCIDOS = ('trabajando', 'idle', 'caido', 'sin_sesion')

_EMOJI_ESTADO = {
    'trabajando': '🟢 trabajando',
    'idle':       '⚪ idle',
    # Los dos muertos. El texto lo lee un humano en la pestaña Live, pero el 💀
    # lo lee el guard de propiedad (scripts/guard_propiedad.py) para dejar de
    # bloquear commits en nombre de un agente que ya no está.
    'caido':      '💀 caído (su CLI se cerró)',
    'sin_sesion': '💀 caído (sin sesión tmux)',
}
_EMOJI_PERMISO = {'pendiente': '⏳', 'ok': '✅', 'no': '⛔'}


def generar_live_md(snapshot: dict, ahora_str: str) -> str:
    """Markdown de .jarvis/LIVE.md a partir de un snapshot. Solo writes
    (leer no genera conflicto — los reads van por la API, no acá)."""
    out = ['# LIVE — qué está haciendo cada agente (auto-generado, NO editar)',
           f'Actualizado: {ahora_str}', '']
    if not snapshot['agentes']:
        out.append('Sin agentes activos.')
    for a in snapshot['agentes']:
        out.append(f"## {a['nombre']} ({a['tipo_ia']}, terminal {a['terminal_id']}) "
                   f"— {_EMOJI_ESTADO.get(a['estado'], a['estado'])}")
        escritos = [f for f in a['archivos'] if f['writes'] > 0]
        if not escritos:
            out.append('- (sin escrituras detectadas)')
        for f in escritos:
            dueno = ' 🔒 dueño' if f['dueno'] else ''
            out.append(f"- `{f['path']}` — write ×{f['writes']} "
                       f"(hace {_fmt_hace(f['hace_s'])}){dueno}")
        out.append('')
    if snapshot['permisos']:
        out.append('## Permisos')
        for p in snapshot['permisos']:
            emoji = _EMOJI_PERMISO.get(p['estado'], '·')
            linea = (f"- {emoji} {p['pide']} pidió PERMISO sobre `{p['archivo']}` "
                     f"(dueño: {p['dueno']}) — hace {_fmt_hace(p['hace_s'])}")
            if p['estado'] != 'pendiente':
                linea += f" → {p['estado'].upper()}"
                if p['respuesta']:
                    linea += f": {p['respuesta']}"
            out.append(linea)
    if snapshot.get('reservas'):
        out.append('## Reservas')
        for r in snapshot['reservas']:
            out.append(f"- 🔖 `{r['path']}` — {r['nombre']} "
                       f"(hace {_fmt_hace(r['hace_s'])})")
    return '\n'.join(out).rstrip() + '\n'


def escribir_live_md(project_path: str, contenido: str):
    """Escritura atómica (tmp + replace): un agente nunca lee a medias."""
    destino = os.path.join(project_path, LIVE_NOMBRE)
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(destino), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(contenido)
        os.replace(tmp, destino)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def asegurar_live(project_path: str):
    """Inyecta el protocolo LIVE en CLAUDE.md (markers idempotentes, mismo
    patrón que asegurar_mailbox) + agrega .jarvis/LIVE.md al .gitignore
    del proyecto (es estado efímero, no se versiona)."""
    claude_md = os.path.join(project_path, 'CLAUDE.md')
    try:
        contenido = ''
        if os.path.exists(claude_md):
            with open(claude_md, encoding='utf-8') as f:
                contenido = f.read()
        if PROTOCOLO_MARKER_START in contenido:
            inicio = contenido.index(PROTOCOLO_MARKER_START)
            # Si falta el END marker (corrupción manual), index() tira ValueError → lo traga el except: se loguea y no se repara (aceptable, localhost single-user).
            fin    = contenido.index(PROTOCOLO_MARKER_END) + len(PROTOCOLO_MARKER_END)
            nuevo  = contenido[:inicio] + PROTOCOLO + contenido[fin:]
        else:
            sep   = '' if (not contenido or contenido.endswith('\n\n')) else ('\n' if contenido.endswith('\n') else '\n\n')
            nuevo = contenido + sep + PROTOCOLO + '\n'
        if nuevo != contenido:
            with open(claude_md, 'w', encoding='utf-8') as f:
                f.write(nuevo)
            print(f'[agent-live] Protocolo inyectado en {claude_md}')
    except Exception as e:
        print(f'[agent-live] No pude inyectar el protocolo: {e}')

    gitignore = os.path.join(project_path, '.gitignore')
    try:
        gi = ''
        if os.path.exists(gitignore):
            with open(gitignore, encoding='utf-8') as f:
                gi = f.read()
        faltan = [e for e in ('.jarvis/LIVE.md', '.jarvis/jv') if e not in gi]
        if faltan:
            sep = '' if (not gi or gi.endswith('\n')) else '\n'
            with open(gitignore, 'a', encoding='utf-8') as f:
                f.write(sep + '\n'.join(faltan) + '\n')
    except Exception as e:
        print(f'[agent-live] No pude tocar el .gitignore: {e}')

    # CLI del enjambre disponible en el proyecto (`.jarvis/jv`): el agente
    # pregunta lo que necesita en vez de cargar 30K tokens de protocolo.
    try:
        from plotspace.core import swarm_cli
        raiz = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        if swarm_cli.asegurar_jv(project_path, raiz):
            print(f'[agent-live] CLI jv instalado en {project_path}/.jarvis/jv')
    except Exception as e:
        print(f'[agent-live] no pude instalar el CLI jv: {e}')


# ─── Estado del poller (en memoria, muere con el server — por diseño) ─────────

_vistos:    dict = {}  # tid → {hash de línea → ocurrencias en la última captura}
_archivos:  dict = {}  # tid → {path → {'reads','writes','primera','ultima'}}
_duenos:    dict = {}  # (pid, path) → {'tid','nombre','desde','ultima'}
_permisos:  dict = {}  # pid → [permiso, ...]  # (las listas de permisos no se podan: volumen real acotado, mueren con el server)
_alertas:   dict = {}  # (pid, path, frozenset({tids})) → ts última alerta
_actividad: dict = {}  # pid → deque(maxlen=MAX_ACTIVIDAD)
_ultimo_md: dict = {}  # pid → hash del último LIVE.md escrito (no reescribir igual)
_roster:    dict = {}  # pid → set(tids) activos del ciclo anterior (alta/baja)
_fases_prev: dict = {} # tid → fase del ciclo anterior (idle↔trabajando)


def _registrar_actividad(pid: int, texto: str, clase: str = 'op'):
    cola = _actividad.setdefault(pid, deque(maxlen=MAX_ACTIVIDAD))
    cola.appendleft({'hora': datetime.now().strftime('%H:%M:%S'),
                     'texto': texto, 'clase': clase})


def _fases_agent_watch() -> dict:
    """tid → 'trabajando' | 'idle' para la pestaña Live. Deriva del MISMO nivel
    que el brillo de la card (`terminales_trabajando`, que incluye las que están
    confirmando su fin) → la Live, el brillo y el sonido de 'terminé' quedan
    conectados: una terminal se ve 'trabajando' hasta que su fin se confirma, y
    ahí se apagan/suenan todos juntos. Solo lectura del estado de agent_watch."""
    try:
        from plotspace.core import agent_watch
        trab = set(agent_watch.terminales_trabajando())
        return {tid: ('trabajando' if tid in trab else 'idle')
                for tid in agent_watch._estados}
    except Exception:
        return {}


def estados_de(rows: list) -> dict:
    """{tid: 'trabajando'|'idle'|'caido'|'sin_sesion'} para las filas de un
    proyecto: la fase de agent_watch enriquecida con el liveness REAL (un solo
    `tmux list-panes` cacheado para todo el enjambre).

    Sin esto, un agente cuyo CLI se cerró figura idle para siempre — y con él
    figuran vivos su territorio, su propiedad y los commits que nunca va a hacer."""
    try:
        from plotspace.core import liveness
        return liveness.estados_ahora(
            [{'id': r['tid'], 'tipo_ia': r.get('tipo_ia')} for r in rows or ()],
            _fases_agent_watch())
    except Exception as e:                      # nunca romper el snapshot
        print(f'[agent-live] liveness no disponible: {e}')
        return _fases_agent_watch()


def snapshot(project_id: int, rows: list, ahora: float = None) -> dict:
    """Snapshot del proyecto con el estado vivo del módulo."""
    ahora = time.monotonic() if ahora is None else ahora
    return armar_snapshot(
        project_id, rows, _archivos, _duenos,
        _permisos.get(project_id, []), estados_de(rows),
        list(_actividad.get(project_id, [])), ahora, reservas=_reservas,
    )


async def publicar_roster(project_id: int):
    """Push INMEDIATO de un snapshot del proyecto (terminal creada/eliminada),
    sin esperar el poll de 2s: un agente recién lanzado aparece en Live al
    instante. Barato (el roster sale de la DB, no captura panes) y fire-and-forget
    (nunca propaga — si algo falla, el poller lo cubre en ≤2s). Sincroniza el
    roster trackeado para que el poller no re-emita el mismo cambio."""
    try:
        rows = await asyncio.to_thread(_rows_de, project_id)
        if not rows:
            # Se fue la última terminal: el poller no puede emitir (sin ruta) y
            # el modal, al reabrirse, re-fetchea igual. Dejamos el roster limpio.
            _roster.pop(project_id, None)
            return
        _roster[project_id] = {r['tid'] for r in rows}
        await _publicar(project_id, rows[0]['ruta'], rows)
    except Exception as e:
        print(f'[agent-live] publicar_roster({project_id}) falló: {e}')


def _rows_de(project_id: int = None) -> list:
    conn = get_db()
    try:
        cursor = conn.cursor()
        sql = ('SELECT t.id AS tid, t.nombre AS tnombre, t.tipo_ia AS tipo_ia, '
               't.project_id AS pid, p.ruta AS ruta '
               'FROM terminals t JOIN projects p ON p.id = t.project_id '
               'WHERE t.activa = 1')
        if project_id is None:
            cursor.execute(sql)
        else:
            cursor.execute(sql + ' AND t.project_id = ?', (project_id,))
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def _pid_de_terminal(tid: int):
    """project_id de una terminal activa (None si no existe/está inactiva)."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT project_id FROM terminals WHERE id = ? AND activa = 1', (tid,))
        row = cursor.fetchone()
        return row['project_id'] if row else None
    finally:
        conn.close()


async def _on_evento_broadcast(data: dict):
    """Listener del broadcaster: reacciona a los cambios de fase de agent_watch
    empujando un snapshot fresco del proyecto AL INSTANTE (sin esperar el poll).
    Filtra por tipo → NO reacciona a nuestros propios live_update (evita loop)."""
    if data.get('type') not in _TIPOS_FASE:
        return
    tid = data.get('terminal_id')
    if tid is None:
        return
    try:
        pid = await asyncio.to_thread(_pid_de_terminal, tid)
    except Exception:
        return
    if pid is not None:
        await publicar_roster(pid)


async def _capture_pane(terminal_id: int) -> str:
    """Pane tmux vía la captura COMPARTIDA con cache: una sola captura por terminal por tick sirve
    a los tres pollers (antes cada uno forkeaba su propio capture-pane con `-S -120`). Para agent_live
    es IDÉNTICO (mismo `-S -120`); para los otros dos ahorra dos capture-pane por tick por terminal."""
    return await pane_capture.capturar(terminal_id)


async def _publicar(pid: int, ruta: str, rows_pid: list, tipo: str = 'live_update',
                    extra: dict = None):
    """Regenera LIVE.md (si cambió) + broadcast WS con snapshot completo.

    El snapshot va a un THREAD porque desde que incluye liveness forkea `tmux
    list-panes`, y esto corre en el event loop cada vez que un agente edita algo.
    Un subprocess bloqueante acá se ve como cortes en el eco del tipeo de TODAS
    las terminales (es el mismo pecado que el stall de uvloop que documenta el
    CLAUDE.md)."""
    snap = await asyncio.to_thread(snapshot, pid, rows_pid)
    md = generar_live_md(snap, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    h = hash(md)
    if _ultimo_md.get(pid) != h:
        _ultimo_md[pid] = h
        try:
            escribir_live_md(ruta, md)
        except Exception as e:
            print(f'[agent-live] No pude escribir LIVE.md: {e}')
    await broadcaster.broadcast(pid, {'type': tipo, 'snapshot': snap, **(extra or {})})


async def _registrar_conflicto(pid: int, path: str, dueno: dict, row: dict, ahora: float):
    """No-dueño escribió archivo ajeno → actividad + WS (toast/pestaña Live).
    A los agentes NO se les avisa: el intruso ya sabe por su CLAUDE.md que
    LIVE.md existe y que la palabra del dueño manda."""
    _registrar_actividad(pid, f"⚠ conflicto: {row['tnombre']} escribió {path} "
                              f"(dueño: {dueno['nombre']})", 'alerta')
    rows_pid = await asyncio.to_thread(_rows_de, pid)
    await _publicar(pid, row['ruta'], rows_pid, tipo='conflicto_archivo', extra={
        'archivo': path, 'dueno_nombre': dueno['nombre'],
        'intruso_nombre': row['tnombre'], 'terminal_id': row['tid'],
    })


async def procesar_mensaje_mailbox(pid: int, de: str, para: str, msg: str):
    """Llamado por mailbox._entregar con cada mensaje entregado: si usa la
    convención PERMISO/OK/NO o RESERVA, avanza el estado y publica."""
    reserva = parsear_reserva(msg)
    if reserva:
        rows_pid = await asyncio.to_thread(_rows_de, pid)
        tid = next((r['tid'] for r in rows_pid
                    if r['tnombre'].strip().lower() == de.strip().lower()), None)
        res, quien = aplicar_reserva(_reservas, _duenos, pid, reserva['archivo'],
                                     tid, de, time.monotonic())
        if res == 'ok':
            _registrar_actividad(pid, f"🔖 {de} reservó {reserva['archivo']}"
                                      + (f" — {reserva['detalle']}" if reserva['detalle'] else ''),
                                 'reserva')
        else:
            _registrar_actividad(pid, f"🚫 reserva de {de} sobre {reserva['archivo']} "
                                      f"rechazada (lo tiene {quien})", 'reserva')
        if rows_pid:
            await _publicar(pid, rows_pid[0]['ruta'], rows_pid)
        return
    parsed = parsear_permiso(msg)
    if not parsed:
        return
    permiso = aplicar_permiso(_permisos.setdefault(pid, []), de, para, parsed,
                              ahora=time.monotonic())
    if not permiso:
        return
    if parsed['tipo'] == 'permiso':
        _registrar_actividad(pid, f"⏳ {de} pidió PERMISO sobre {parsed['archivo']} a {para}", 'permiso')
        tipo = 'permiso_pedido'
    else:
        emoji = '✅' if permiso['estado'] == 'ok' else '⛔'
        _registrar_actividad(pid, f"{emoji} {de} respondió {permiso['estado'].upper()} "
                                  f"{parsed['archivo']} a {para}", 'permiso')
        tipo = 'permiso_resuelto'
    rows_pid = await asyncio.to_thread(_rows_de, pid)
    if rows_pid:
        await _publicar(pid, rows_pid[0]['ruta'], rows_pid, tipo=tipo,
                        extra={'permiso': {k: v for k, v in permiso.items() if k != 'ts'}})


def _row_terminal(terminal_id: int):
    """(tid, tnombre, pid, ruta) de una terminal ACTIVA, o None."""
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT t.id AS tid, t.nombre AS tnombre, t.tipo_ia AS tipo_ia, '
            't.project_id AS pid, p.ruta AS ruta FROM terminals t '
            'JOIN projects p ON p.id = t.project_id '
            'WHERE t.id = ? AND t.activa = 1', (terminal_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


async def registrar_op_externa(terminal_id: int, op: str, path: str,
                               antes: str = '', despues: str = '',
                               sobrescritura: bool = False,
                               publicar: bool = True) -> dict:
    """Ingesta de una operación de archivo REPORTADA POR EL HOOK del CLI.

    Es la fuente PRIMARIA de propiedad desde que el pane dejó de nombrar los
    archivos. Hace exactamente lo que hacía el poller con una op parseada
    (contabilizar, asignar dueño, alertar conflicto, publicar) más el registro
    en el libro de provenance, que es lo que habilitan el commit por hunk y la
    detección de colisiones por funcionalidad.

    Devuelve un dict con el resultado (el endpoint lo refleja al hook)."""
    row = await asyncio.to_thread(_row_terminal, terminal_id)
    if not row:
        return {'estado': 'sin_terminal'}
    pid, nombre, ruta = row['pid'], row['tnombre'], row['ruta']
    ahora = time.monotonic()
    path_norm, res, dueno = aplicar_op(_archivos, _duenos, pid, terminal_id,
                                       nombre, ruta, op, path, ahora)
    if not path_norm:
        return {'estado': 'sin_path'}

    try:
        from plotspace.core import provenance
        provenance.registrar(pid, terminal_id, nombre, path_norm, op,
                             antes=antes, despues=despues,
                             sobrescritura=sobrescritura)
    except Exception as e:
        print(f'[agent-live] no pude registrar provenance: {e}')

    if res == 'write' or op == 'write':
        _registrar_actividad(pid, f"✏ {nombre} → {path_norm}", 'op')

    if res == 'conflicto' and dueno:
        clave = (pid, path_norm, frozenset({terminal_id, dueno['tid']}))
        if debe_alertar(_alertas, clave, ahora):
            await _registrar_conflicto(pid, path_norm, dueno, row, ahora)
            return {'estado': 'conflicto', 'path': path_norm,
                    'dueno': dueno['nombre'], 'dueno_tid': dueno['tid']}

    if publicar:
        rows_pid = await asyncio.to_thread(_rows_de, pid)
        if rows_pid:
            await _publicar(pid, ruta, rows_pid)
    return {'estado': res or 'ok', 'path': path_norm, 'pid': pid, 'nombre': nombre,
            **({'dueno': dueno['nombre']} if dueno else {})}


def _revisar_permisos(ahora: float) -> set:
    """Expira pendientes (estado para la UI — a los agentes no se les
    recuerda nada): cuando la propiedad del dueño venció, o por timeout
    propio (PROPIEDAD_TTL_S sin respuesta). Un pendiente cuyo dueño NO está
    trackeado (corre fuera de un pane tmux, o el @Para no es el nombre
    exacto de su terminal) no expira al instante: antes dueno_vigente(None)
    lo mataba en el primer ciclo (≤2s) y el OK posterior del dueño ya no
    encontraba pendiente que resolver. Devuelve pids con cambios."""
    cambios = set()
    for pid, lista in _permisos.items():
        for p in lista:
            if p['estado'] != 'pendiente':
                continue
            # ¿la propiedad de ese archivo sigue vigente para ese dueño?
            d = next((v for (dpid, path), v in _duenos.items()
                      if dpid == pid and _match_archivo(path, p['archivo'])
                      and v['nombre'].strip().lower() == p['dueno'].strip().lower()), None)
            dueno_vencido = d is not None and not dueno_vigente(d, ahora)
            sin_respuesta = (ahora - p['ts']) > PROPIEDAD_TTL_S
            if not (dueno_vencido or sin_respuesta):
                continue
            if dueno_vencido:
                # Mailbox v2: una propiedad VENCIDA no puede seguir bloqueando —
                # auto-OK con constancia (el guard de propiedad lee el '→ OK'
                # del LIVE.md y desbloquea el commit del que pedía).
                p['estado'] = 'ok'
                p['respuesta'] = 'auto-OK: la propiedad del dueño venció sin respuesta'
                _registrar_actividad(pid, f"✅ auto-OK a {p['pide']} sobre "
                                          f"{p['archivo']} (la propiedad de "
                                          f"{p['dueno']} venció)", 'permiso')
            else:
                p['estado'] = 'expirado'
                _registrar_actividad(pid, f"💨 permiso sobre {p['archivo']} expiró "
                                          f"({p['dueno']} no respondió en "
                                          f"{PROPIEDAD_TTL_S // 60} min)", 'permiso')
                # Escalamiento al USUARIO (lo único de coordinación que toca su
                # chat): un permiso muerto en silencio dejaba al que pedía
                # colgado sin que nadie se enterara.
                _escalaciones.append({
                    'pid': pid,
                    'texto': (f"⏳ Permiso trabado: {p['pide']} espera a {p['dueno']} "
                              f"por `{p['archivo']}` hace {PROPIEDAD_TTL_S // 60}+ min "
                              "sin respuesta — destrabalo vos o pedile que conteste "
                              "por el MAILBOX."),
                })
            cambios.add(pid)
    return cambios


# Escalaciones pendientes de publicar al chat del usuario (las drena el
# poller — _revisar_permisos es sync y no puede broadcastear).
_escalaciones: list = []


def _purgar_terminales(vivas: set):
    """Terminales eliminadas no dejan estado huérfano (patrón agent_watch).

    OJO CON LO QUE **NO** SE PURGA: la provenance del muerto se CONSERVA a
    propósito. Borrarla era el origen del "tal agente tiene commits que no hizo":
    sin sus registros, `/swarm/fragmentos` dejaba de reportar sus hunks como
    ajenos, `commit_propio` pasaba el archivo de modo 'hunk' a 'archivo entero',
    y el siguiente agente se llevaba el trabajo sin commitear del muerto adentro
    de su propio commit. Conservándola, ese trabajo queda ATRIBUIDO y se puede
    declarar como herencia (core/herencia.py). El libro ya está acotado por
    tamaño (deque) y por ventana, así que no crece sin techo.

    Lo que sí se libera es el TERRITORIO y la PROPIEDAD: un muerto no puede
    seguir bloqueándole el paso a los vivos."""
    for tid in list(_vistos.keys()):
        if tid not in vivas:
            _vistos.pop(tid, None)
            _archivos.pop(tid, None)
            try:
                from plotspace.core import briefing
                briefing.olvidar_terminal(tid)
            except Exception:
                pass
    for clave in list(_duenos.keys()):
        if _duenos[clave]['tid'] not in vivas:
            tid_muerta = _duenos.pop(clave)['tid']
            try:
                from plotspace.core import territorio
                territorio.purgar_terminal(tid_muerta)   # su territorio queda libre
            except Exception:
                pass


_listener_registrado = False


async def poller_live():
    """Background task: nunca crashea el servidor (patrón STATE.md)."""
    global _listener_registrado
    if not _listener_registrado:
        # Push instantáneo ante los cambios de fase de agent_watch (además del
        # backstop de roster/fase del ciclo). Registrado acá (arranca una sola
        # vez con el poller) para no tener que tocar main.py.
        broadcaster.escuchar(_on_evento_broadcast)
        _listener_registrado = True
    while True:
        await asyncio.sleep(INTERVALO_S)
        try:
            await _ciclo()
        except Exception as e:
            print(f'[agent-live] Error en ciclo: {e}')


async def _ciclo():
    rows = await asyncio.to_thread(_rows_de)   # DB sync fuera del loop (ver agent_watch._ciclo)
    ahora = time.monotonic()
    _purgar_terminales({r['tid'] for r in rows})
    cambios = set()

    # Roster + fase: un agente recién creado (o que pasó idle↔trabajando) tiene
    # que aparecer/actualizarse en Live SIN esperar a que toque un archivo. El
    # push directo (publicar_roster) lo hace instantáneo; esto es el backstop que
    # cubre TODA vía de alta/baja (reconcile del boot, orquestador, etc.) en ≤2s.
    roster_actual: dict = {}
    tid_pid: dict = {}
    for r in rows:
        roster_actual.setdefault(r['pid'], set()).add(r['tid'])
        tid_pid[r['tid']] = r['pid']
    fases_actual = _fases_agent_watch()
    cambios |= cambios_de_roster(_roster, roster_actual, _fases_prev, fases_actual, tid_pid)
    _roster.clear();     _roster.update(roster_actual)
    _fases_prev.clear(); _fases_prev.update(fases_actual)

    for row in rows:
        tid, pid = row['tid'], row['pid']
        texto = await _capture_pane(tid)
        if not texto:
            continue
        ops = operaciones_nuevas(texto, _vistos.setdefault(tid, {}))
        for op, path in ops:
            path = relativo_al_proyecto(path, row['ruta'])
            info = _archivos.setdefault(tid, {}).setdefault(
                path, {'reads': 0, 'writes': 0, 'primera': ahora, 'ultima': ahora})
            info['reads' if op == 'read' else 'writes'] += 1
            info['ultima'] = ahora
            cambios.add(pid)
            if op != 'write':
                continue
            _registrar_actividad(pid, f"✏ {row['tnombre']} → {path}", 'op')
            res, dueno = registrar_escritura(_duenos, (pid, path), tid,
                                             row['tnombre'], ahora)
            if res == 'conflicto':
                clave = (pid, path, frozenset({tid, dueno['tid']}))
                if debe_alertar(_alertas, clave, ahora):
                    await _registrar_conflicto(pid, path, dueno, row, ahora)

    cambios |= _revisar_permisos(ahora)
    cambios |= _revisar_reservas(ahora)

    # Drenar escalaciones al chat del usuario (permisos trabados sin respuesta)
    while _escalaciones:
        esc = _escalaciones.pop(0)
        try:
            from plotspace.core.events import broadcaster
            await broadcaster.broadcast(esc['pid'], {
                'type': 'orquestador_mensaje', 'message': esc['texto']})
        except Exception:
            pass

    rutas = {r['pid']: r['ruta'] for r in rows}
    for pid in cambios:
        if pid in rutas:
            await _publicar(pid, rutas[pid], [r for r in rows if r['pid'] == pid])
