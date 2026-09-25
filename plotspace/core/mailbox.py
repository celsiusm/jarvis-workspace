# JARVIS — Mailbox entre agentes.
# Buzón compartido en .jarvis/MAILBOX.md del proyecto: los agentes se dejan
# mensajes append-only con el formato `- @De -> @Para: mensaje`.
#
# Un watcher (background task, mismo patrón que STATE.md) detecta líneas nuevas.
# La ENTREGA EN VIVO (inyectar el mensaje en la terminal del destinatario vía
# send_to_agent) está DESACTIVADA por defecto desde 2026-06-18 (pedido del usuario:
# interrumpía a los agentes). El watcher igual procesa permisos de Agents Live y
# refleja la línea en el chat; solo no tipea nada en las terminales. Re-activable
# con MAILBOX_ENTREGA_TERMINAL=on (ver _ENTREGAR_EN_TERMINAL). Cuando estaba activa,
# la entrega era ESTRICTAMENTE 1-a-1: '@todos' nunca se inyectaba (interrumpía a
# todos) y un destinatario ambiguo tampoco — solo match exacto o substring único.

import asyncio
import json
import os
import re

from plotspace.core.database import get_db
from plotspace.core.datadir import ruta_data
from plotspace import protocolos

MAILBOX_NOMBRE = os.path.join('.jarvis', 'MAILBOX.md')
OFFSETS_PATH   = ruta_data('mailbox-offsets.json')

# Entrega en IDLE (mailbox v2): cuando el destinatario NO está trabajando ni
# esperando respuesta del usuario, el digest de sus pendientes se le tipea —
# el momento seguro que faltaba (la entrega inmediata interrumpía, por eso se
# apagó; la no-entrega dejaba mensajes muertos). Apagable sin código.
_ENTREGA_IDLE = os.getenv('MAILBOX_ENTREGA_IDLE', 'on').strip().lower() in ('1', 'on', 'true', 'yes')

# Inyección del mensaje en la TERMINAL del destinatario: DESACTIVADA por pedido del
# usuario (2026-06-18). Los agentes se spameaban "leé/mirá/PERMISO …" entre ellos y
# eso los interrumpía en pleno trabajo (sobre todo en proyectos con varios agentes).
# El MAILBOX.md y Agents Live siguen vivos; solo NO se tipea nada en las terminales.
# Re-activable sin tocar código: MAILBOX_ENTREGA_TERMINAL=on.
_ENTREGAR_EN_TERMINAL = os.getenv('MAILBOX_ENTREGA_TERMINAL', 'off').strip().lower() in ('1', 'on', 'true', 'yes')

# `- @Backend -> @Frontend: el endpoint quedó en /api/v2` (acepta → o ->)
_LINEA_RE = re.compile(
    r'^-\s*@?(?P<de>[^@\n]+?)\s*(?:→|->)\s*@?(?P<para>[^:\n]+?)\s*:\s*(?P<msg>.+)$'
)

PROTOCOLO_MARKER_START = '<!-- JARVIS_MAILBOX_START -->'
PROTOCOLO_MARKER_END   = '<!-- JARVIS_MAILBOX_END -->'

# El TEXTO vive en `plotspace/protocolos/mailbox.md` — ver ese paquete.
PROTOCOLO = protocolos.leer('mailbox')

# offset de lectura por proyecto (bytes ya procesados) — PERSISTIDO en
# data/mailbox-offsets.json: antes vivía en memoria y un reinicio del server
# se tragaba (re-baseline) los mensajes escritos durante la ventana.
_offsets: dict[int, int] = {}
_offsets_cargados = False


def _cargar_offsets(path=None):
    global _offsets, _offsets_cargados
    if _offsets_cargados and path is None:
        return
    _offsets_cargados = True
    try:
        with open(path or OFFSETS_PATH, encoding='utf-8') as f:
            _offsets = {int(k): int(v) for k, v in json.load(f).items()}
    except Exception:
        _offsets = {}


def _guardar_offsets(path=None):
    try:
        destino = path or OFFSETS_PATH
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        with open(destino, 'w', encoding='utf-8') as f:
            json.dump({str(k): v for k, v in _offsets.items()}, f)
    except Exception:
        pass


def archivar_mailbox(archivo: str, max_lineas: int = 120, conservar: int = 40,
                     max_bytes: int = 16384) -> int:
    """Janitor: si el MAILBOX vivo superó `max_lineas` de mensajes O `max_bytes`
    de peso, mueve las más viejas a MAILBOX-archivo.md — el archivo vivo queda
    SIEMPRE corto (era append-only eterno: 259 líneas que cada agente pagaba en
    contexto). El umbral por BYTES existe porque el de líneas no protege nada
    con mensajes-ensayo: a 533 chars promedio (medido en la DB 2026-08-02),
    109 líneas eran 58 KB ≈ 14K tokens por lectura. Se conservan las últimas
    `conservar` líneas que entren en `max_bytes/2`. El caller garantiza quietud
    (mtime). Devuelve cuántas líneas archivó."""
    try:
        with open(archivo, encoding='utf-8', errors='replace') as f:
            lineas = f.read().splitlines()
    except OSError:
        return 0
    # cabecera = líneas iniciales de título/comentario (no-mensaje)
    i = 0
    while i < len(lineas) and not lineas[i].lstrip().startswith('- '):
        i += 1
    cabecera, mensajes = lineas[:i], lineas[i:]
    peso = sum(len(l.encode('utf-8')) + 1 for l in mensajes)
    if len(mensajes) <= max_lineas and peso <= max_bytes:
        return 0
    # Vivas: las más NUEVAS que entren en el presupuesto (líneas Y bytes).
    presupuesto = max_bytes // 2
    vivas, acumulado = [], 0
    for linea in reversed(mensajes[-conservar:]):
        peso_linea = len(linea.encode('utf-8')) + 1
        if vivas and acumulado + peso_linea > presupuesto:
            break
        vivas.append(linea)
        acumulado += peso_linea
    vivas.reverse()
    viejas = mensajes[:len(mensajes) - len(vivas)]
    destino = os.path.join(os.path.dirname(archivo), 'MAILBOX-archivo.md')
    try:
        existe = os.path.exists(destino)
        with open(destino, 'a', encoding='utf-8') as f:
            if not existe:
                f.write('# Mailbox — archivo histórico (lo mueve el janitor)\n')
            f.write('\n'.join(viejas) + '\n')
        with open(archivo, 'w', encoding='utf-8') as f:
            f.write('\n'.join(cabecera + vivas) + '\n')
        return len(viejas)
    except OSError:
        return 0


def leer_lineas_nuevas(archivo: str, offset: int) -> tuple:
    """Lee desde `offset` SOLO líneas completas (hasta el último '\\n') y
    devuelve (lineas, offset_nuevo). Binario + decode errors='replace':
    el modo texto con seek a un offset en bytes tiraba UnicodeDecodeError
    si caía a mitad de un char multibyte (emoji/acentos) y el ciclo del
    watcher fallaba para siempre. El offset avanza por bytes CONSUMIDOS
    (no por getsize(), que pudo medir antes de un append → re-entrega), y
    un append a medio escribir no se consume: queda para cuando llegue su
    '\\n' (antes la media línea se tragaba y el mensaje se perdía)."""
    with open(archivo, 'rb') as f:
        f.seek(offset)
        data = f.read()
    fin = data.rfind(b'\n')
    if fin < 0:
        return [], offset
    completo = data[:fin + 1]
    lineas = completo.decode('utf-8', errors='replace').splitlines()
    return lineas, offset + len(completo)


def asegurar_mailbox(project_path: str):
    """Crea el MAILBOX seed + inyecta el protocolo en CLAUDE.md (mismo
    patrón de markers que skills/memoria). Idempotente."""
    archivo = os.path.join(project_path, MAILBOX_NOMBRE)
    if not os.path.exists(archivo):
        os.makedirs(os.path.dirname(archivo), exist_ok=True)
        with open(archivo, 'w', encoding='utf-8') as f:
            f.write('# Mailbox del workspace\n'
                    '<!-- - @De -> @Para: mensaje  ·  canal por archivo: leé .jarvis/MAILBOX.md -->\n')

    # Estado efímero del mailbox v2 → .gitignore (no se versiona)
    try:
        gi = os.path.join(project_path, '.gitignore')
        actual = ''
        if os.path.exists(gi):
            with open(gi, encoding='utf-8') as f:
                actual = f.read()
        faltan = [e for e in ('.jarvis/mailbox-pendientes.json',)
                  if e not in actual]
        if faltan:
            with open(gi, 'a', encoding='utf-8') as f:
                if actual and not actual.endswith('\n'):
                    f.write('\n')
                f.write('\n'.join(faltan) + '\n')
    except Exception:
        pass

    claude_md = os.path.join(project_path, 'CLAUDE.md')
    try:
        contenido = ''
        if os.path.exists(claude_md):
            with open(claude_md, encoding='utf-8') as f:
                contenido = f.read()
        if PROTOCOLO_MARKER_START in contenido:
            inicio = contenido.index(PROTOCOLO_MARKER_START)
            fin    = contenido.index(PROTOCOLO_MARKER_END) + len(PROTOCOLO_MARKER_END)
            nuevo  = contenido[:inicio] + PROTOCOLO + contenido[fin:]
        else:
            sep   = '' if (not contenido or contenido.endswith('\n\n')) else ('\n' if contenido.endswith('\n') else '\n\n')
            nuevo = contenido + sep + PROTOCOLO + '\n'
        if nuevo != contenido:
            with open(claude_md, 'w', encoding='utf-8') as f:
                f.write(nuevo)
            print(f'[mailbox] Protocolo inyectado en {claude_md}')
    except Exception as e:
        print(f'[mailbox] No pude inyectar el protocolo: {e}')


def _proyectos_con_terminales() -> list:
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT p.id, p.ruta
            FROM projects p JOIN terminals t ON t.project_id = p.id
            WHERE t.activa = 1
        ''')
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def _terminales_de(project_id: int) -> list:
    """id + nombre + tipo_ia de las terminales activas. El `tipo_ia` importa para
    el liveness: un pane en bash es un CLI muerto si la terminal es de IA, pero
    es lo NORMAL si es una shell del usuario."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, nombre, tipo_ia FROM terminals '
            'WHERE project_id = ? AND activa = 1',
            (project_id,),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


_BROADCAST = ('todos', 'all', 'everyone')


def resolver_destinos(terminales: list, para: str) -> list:
    """Resuelve el destinatario a terminales concretas. 1-a-1 estricto:
    los broadcasts no se entregan a NINGUNA terminal, y un nombre que
    matchea varias (ej "@Claude Code" con "#3" y "#4" vivas) tampoco se
    entrega — inyectarle texto a un agente que está trabajando lo
    confunde, así que ante la duda no se inyecta nada."""
    para_lower = para.strip().lower()
    if para_lower in _BROADCAST:
        return []
    exactos = [t for t in terminales if t['nombre'].strip().lower() == para_lower]
    if exactos:
        # >1 exacto = homónimas (numeración vieja por conteo): ambiguo igual
        # que el substring — no caer a parciales, que matchearían lo mismo.
        return exactos if len(exactos) == 1 else []
    parciales = [t for t in terminales if para_lower in t['nombre'].lower()]
    return parciales if len(parciales) == 1 else []


def es_momento_seguro(estado_watch, estado_vivo=None) -> bool:
    """¿Se le puede tipear el digest a esta terminal sin molestar? SOLO en
    'idle' puro: trabajando interrumpe, 'esperando' es peor (el texto se
    tomaría como respuesta a la pregunta que el agente le hizo al usuario),
    'arrancando' todavía no tiene prompt. Sin estado → no.

    `estado_vivo` es el liveness real (core/liveness.py). Un CLI que se cerró
    deja el pane en un bash, y para agent_watch eso es 'idle puro' —un prompt de
    shell no matchea ningún patrón de pregunta—, así que el digest se le tipeaba
    ADENTRO del bash y el mensaje moría ahí. None = sin dato: se entrega igual
    (falla abierta, no dejamos de entregar por no poder leer tmux)."""
    if not estado_watch:
        return False
    if estado_vivo in ('caido', 'sin_sesion'):
        return False
    return (estado_watch.get('fase') == 'idle'
            and not estado_watch.get('esperando'))


def _es_handoff(msg: str) -> bool:
    """HANDOFF = pasar trabajo con contexto destilado — va primero y ENTERO
    (un handoff truncado es peor que ninguno)."""
    return (msg or '').strip().upper().startswith('HANDOFF')


# ─── Qué mensaje AMERITA despertar al destinatario ───────────────────────────
# MEDIDO en este proyecto: 52 de 134 mensajes (38%) se mandaron a un agente que YA
# había cerrado su tarea, y 47 de esos eran charla de coordinación. Cada entrega
# tipeada le quema al destinatario un turno COMPLETO de inferencia (y a veces lo
# pone a "hacer cosas" con la tarea ya cerrada).
#
# La regla se decide por el MENSAJE, NO por el estado del agente: filtrar por
# "ya terminó" sería un downgrade — rompería `jv ask` (el que pregunta queda
# bloqueado 240s) y los HANDOFF, que justamente van a agentes libres. Lo que no
# despierta NO se pierde: queda en su inbox (`jv inbox`) y se le inyecta con su
# próxima tarea (bloque_pendientes_para_tarea).
CLASES_QUE_DESPIERTAN = ('ask', 'handoff')


def clase_de_mensaje(msg, espera=False) -> str:
    """'ask' | 'handoff' | 'normal'.
      ask     → quien escribió quedó BLOQUEADO esperando la respuesta (`jv ask`);
                sin despertar al destinatario el ask expira SIEMPRE.
      handoff → le pasan trabajo con contexto (va primero y entero).
      normal  → coordinación: llega al inbox, sin interrumpir a nadie."""
    if espera:
        return 'ask'
    return 'handoff' if _es_handoff(msg) else 'normal'


def debe_despertar(mensaje) -> bool:
    """¿Este mensaje justifica tipearle el digest al destinatario? Un mensaje sin
    clase (histórico, anterior a la migración) NO despierta."""
    if not isinstance(mensaje, dict):
        return False
    return (mensaje.get('clase') or 'normal') in CLASES_QUE_DESPIERTAN


def _ordenar_mensajes(msgs: list) -> list:
    return sorted(msgs, key=lambda m: 0 if _es_handoff(m.get('msg', '')) else 1)


def armar_digest(msgs: list) -> str:
    """Digest de pendientes para tipear al destinatario en su momento idle.
    Corto y accionable; la respuesta va por el archivo, como siempre.
    Los HANDOFF van primero y completos."""
    if not msgs:
        return ''
    msgs = _ordenar_mensajes(msgs)
    lineas = [f"📬 Mailbox — {len(msgs)} mensaje{'s' if len(msgs) != 1 else ''} "
              "mientras trabajabas:"]
    for m in msgs[:6]:
        tope = 1500 if _es_handoff(m['msg']) else 300
        lineas.append(f"  - de {m['de']}: {m['msg'][:tope]}")
    if len(msgs) > 6:
        lineas.append(f"  (+{len(msgs) - 6} más en .jarvis/MAILBOX.md)")
    lineas.append("Respondé por .jarvis/MAILBOX.md si hace falta y seguí con lo tuyo.")
    return '\n'.join(lineas)


_AVISO_RESUMEN_MAX = 140


def construir_avisos(destinos: list, de: str, msg: str) -> list:
    """Eventos WS 'mailbox_aviso' (uno por destinatario CONCRETO) para encender
    el aura de su card — el reemplazo NO intrusivo de la entrega in-terminal
    (que se apagó porque interrumpía a los agentes). `destinos` ya viene
    resuelto 1-a-1 por resolver_destinos: un broadcast/ambiguo es [] → no
    enciende nada. Puro y testeable (sin tmux ni WS)."""
    resumen = msg.strip()[:_AVISO_RESUMEN_MAX]
    return [{'type': 'mailbox_aviso', 'terminal_id': t['id'],
             'de': de.strip(), 'resumen': resumen} for t in destinos]


async def _entregar(project_id: int, de: str, para: str, msg: str):
    """Registra el mensaje con estado + avisa por aura; la entrega real la
    hace el ciclo idle (o el engine al despachar tarea)."""
    from plotspace.routers.orchestrator import send_to_agent
    from plotspace.core.events import broadcaster

    # CANDADO CONTRA LA RE-ENTREGA DEL HISTORIAL. El offset en bytes era el
    # único guard, y el 2026-07-25 quedó desfasado tras un reinicio: el watcher
    # re-registró ~108 líneas viejas como nuevas y las resolvió contra las
    # terminales de HOY — mensajes de sagas del 21-22 de julio cayeron en el
    # inbox de un agente que reusaba un nombre de julio. Una línea que ya entró
    # no vuelve a entrar; el offset queda como pura optimización.
    # (`jv msg` NO pasa por acá: registra él mismo, y ahí repetir un texto
    # idéntico puede ser deliberado.)
    try:
        from plotspace.core.database import mensaje_ya_registrado
        if await asyncio.to_thread(mensaje_ya_registrado, project_id,
                                   de.strip(), para.strip(), msg.strip()):
            return
    except Exception as e:
        print(f'[mailbox] no pude chequear duplicados: {e}')

    destinos = resolver_destinos(await asyncio.to_thread(_terminales_de, project_id), para)

    # Mailbox v2: persistir con estado (pendiente | sin_destino). La tabla es
    # la fuente de la entrega garantizada; el .md queda como canal de escritura.
    try:
        from plotspace.core.database import registrar_mensaje_mailbox
        await asyncio.to_thread(
            registrar_mensaje_mailbox, project_id, de.strip(), para.strip(),
            msg.strip(), destinos[0]['id'] if len(destinos) == 1 else None,
            clase_de_mensaje(msg))
    except Exception as e:
        print(f'[mailbox] no pude persistir el mensaje: {e}')

    # Inyección en terminal gateada (ver _ENTREGAR_EN_TERMINAL): por defecto NO se
    # tipea el mensaje en la terminal del destinatario — antes interrumpía al agente.
    if _ENTREGAR_EN_TERMINAL:
        for t in destinos:
            try:
                await send_to_agent(
                    t['id'],
                    f"📬 Mensaje de {de.strip()} (mailbox): {msg.strip()}",
                )
            except Exception as e:
                print(f'[mailbox] No pude entregar a terminal {t["id"]}: {e}')

    # Aviso NO intrusivo (siempre, esté o no el push): un WS por destinatario
    # concreto enciende el aura de su card — sabe que tiene mensaje sin que se
    # le tipee nada en el pane (lo consume el frontend con TerminalAura).
    for ev in construir_avisos(destinos, de, msg):
        try:
            await broadcaster.broadcast(project_id, ev)
        except Exception:
            pass

    # Convención PERMISO/OK/NO → estado de permisos de Agents Live
    try:
        from plotspace.core import agent_live
        await agent_live.procesar_mensaje_mailbox(project_id, de.strip(), para.strip(), msg.strip())
    except Exception as e:
        print(f'[mailbox] agent_live no pudo procesar: {e}')

    # NO se refleja cada mensaje en el chat del orquestador (se quitó 2026-07-19,
    # pedido del usuario: el chat es SU canal con Jarvis y el tráfico de
    # coordinación entre agentes lo inundaba — 20+ mensajes por saga). El
    # destinatario se entera por el aura de su card; al chat solo llegan
    # EXCEPCIONES que necesitan al usuario (permisos vencidos — ver mailbox v2).

    if not destinos:
        print(f'[mailbox] Sin entrega para "@{para}" en proyecto {project_id} '
              f'(broadcast deshabilitado, o destinatario ambiguo/inexistente)')


PENDIENTES_NOMBRE = os.path.join('.jarvis', 'mailbox-pendientes.json')


def _escribir_pendientes_json(project_ruta: str, pendientes: list):
    """Refleja los pendientes en .jarvis/mailbox-pendientes.json (por
    terminal_id destino) — lo lee el aviso del pre-commit sin server.
    Compare-before-write; sin pendientes escribe {} (estado limpio)."""
    data = {}
    for m in pendientes:
        data.setdefault(str(m['terminal_id']), []).append(
            {'de': m['de'], 'msg': m['msg'][:300]})
    try:
        path = os.path.join(project_ruta, PENDIENTES_NOMBRE)
        nuevo = json.dumps(data, ensure_ascii=False)
        try:
            with open(path, encoding='utf-8') as f:
                if f.read() == nuevo:
                    return
        except OSError:
            pass
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(nuevo)
    except Exception:
        pass


async def _entregar_pendientes_idle(project_id: int, project_ruta: str = None):
    """Mailbox v2: tipea el digest de pendientes a cada destinatario que esté
    en un momento SEGURO (idle puro según agent_watch). Marca entregados."""
    from plotspace.core.database import mensajes_pendientes_mailbox, marcar_mensajes_entregados
    pendientes = await asyncio.to_thread(mensajes_pendientes_mailbox, project_id)
    if project_ruta:
        _escribir_pendientes_json(project_ruta, pendientes)
    if not pendientes or not _ENTREGA_IDLE:
        return
    from plotspace.core import agent_watch
    por_terminal = {}
    for m in pendientes:
        por_terminal.setdefault(m['terminal_id'], []).append(m)

    # Liveness de los destinatarios: un pane muerto se ve idle y se tragaba el
    # digest adentro de un bash. Un solo `tmux list-panes` para todos (cacheado).
    vivos = {}
    try:
        from plotspace.core import liveness
        tipos = {t['id']: t.get('tipo_ia')
                 for t in await asyncio.to_thread(_terminales_de, project_id)}
        # A un THREAD: estados_ahora forkea tmux y esto corre en el event loop.
        vivos = await asyncio.to_thread(
            liveness.estados_ahora,
            [{'id': tid, 'tipo_ia': tipos.get(tid)} for tid in por_terminal], {})
    except Exception:
        vivos = {}                     # sin dato: se entrega igual

    entregados = False
    for tid, msgs in por_terminal.items():
        if not es_momento_seguro(agent_watch._estados.get(tid), vivos.get(tid)):
            continue
        # Solo se TIPEA lo que amerita despertarlo (ask / handoff). El resto queda
        # PENDIENTE: lo lee con `jv inbox` o le llega inyectado con su próxima
        # tarea — sin quemarle un turno de inferencia por charla de coordinación
        # (medido: 38% de los mensajes iban a agentes con la tarea ya cerrada).
        despiertan = [m for m in msgs if debe_despertar(m)]
        if not despiertan:
            continue
        try:
            from plotspace.routers.orchestrator import send_to_agent
            await send_to_agent(tid, armar_digest(despiertan))
            await asyncio.to_thread(marcar_mensajes_entregados,
                                    [m['id'] for m in despiertan])
            entregados = True
            print(f'[mailbox] digest de {len(despiertan)} mensaje(s) entregado a terminal {tid} (idle)')
        except Exception as e:
            print(f'[mailbox] no pude entregar el digest a terminal {tid}: {e}')
    if entregados and project_ruta:
        pendientes = await asyncio.to_thread(mensajes_pendientes_mailbox, project_id)
        _escribir_pendientes_json(project_ruta, pendientes)


def bloque_pendientes_para_tarea(terminal_id: int) -> str:
    """Bloque para el PROMPT de una tarea nueva (lo llama el engine): los
    mensajes que le llegaron a esa terminal y siguen pendientes. Los marca
    entregados (la tarea los lleva — no re-tipearlos después). '' si nada."""
    try:
        from plotspace.core.database import (get_db, mensajes_pendientes_mailbox,
                                           marcar_mensajes_entregados)
        conn = get_db()
        try:
            fila = conn.execute('SELECT project_id FROM terminals WHERE id = ?',
                                (terminal_id,)).fetchone()
        finally:
            conn.close()
        if not fila:
            return ''
        msgs = mensajes_pendientes_mailbox(fila['project_id'], terminal_id)
        if not msgs:
            return ''
        marcar_mensajes_entregados([m['id'] for m in msgs])
        lineas = ["\n\nMENSAJES DEL MAILBOX para vos (leelos antes de empezar):"]
        for m in _ordenar_mensajes(msgs)[:6]:
            tope = 1500 if _es_handoff(m['msg']) else 300
            lineas.append(f"  - de {m['de']}: {m['msg'][:tope]}")
        return '\n'.join(lineas)
    except Exception:
        return ''


async def vigilar_mailboxes():
    """Background task: cada 4s revisa los MAILBOX de proyectos con
    terminales activas — procesa líneas nuevas Y entrega pendientes a los
    destinatarios en idle. Nunca crashea."""
    while True:
        await asyncio.sleep(4)
        try:
            _cargar_offsets()
            hubo_lineas = False
            for proy in await asyncio.to_thread(_proyectos_con_terminales):
                archivo = os.path.join(proy['ruta'], MAILBOX_NOMBRE)
                if os.path.exists(archivo):
                    size = os.path.getsize(archivo)
                    offset = _offsets.get(proy['id'])
                    if offset is None:
                        # primer vistazo: baseline (no reenviar historial viejo)
                        _offsets[proy['id']] = size
                        hubo_lineas = True
                    elif size < offset:     # archivo truncado/archivado
                        _offsets[proy['id']] = size
                        hubo_lineas = True
                    elif size > offset:
                        lineas, _offsets[proy['id']] = leer_lineas_nuevas(archivo, offset)
                        hubo_lineas = True
                        for linea in lineas:
                            m = _LINEA_RE.match(linea.strip())
                            if m:
                                await _entregar(proy['id'], m['de'], m['para'], m['msg'])
                # entrega idle: corre SIEMPRE (haya o no líneas nuevas) — el
                # momento seguro del destinatario llega cuando llega.
                await _entregar_pendientes_idle(proy['id'], proy['ruta'])
            if hubo_lineas:
                _guardar_offsets()
        except Exception as e:
            print(f'[mailbox] Error en ciclo: {e}')
