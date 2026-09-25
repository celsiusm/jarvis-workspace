"""Detección de "terminó" / "espera respuesta" de agentes, sin keywords.

Poller background (patrón dev_detect): cada 1s captura las últimas líneas del
pane tmux de cada terminal activa y corre una máquina de estados por terminal.
Una terminal recién vista arranca en 'arrancando': el churn del boot de la CLI
(banner/loading) no cuenta como trabajo — la máquina se arma recién cuando el
pane se observó estable una vez (sin esto, crear una terminal sonaba "terminé").
Cuando un agente que venía trabajando (output cambiando ≥4 polls seguidos) se
queda quieto 4 polls (~4s), clasifica el final del pane:

  - prompt interactivo visible → WS agente_espera  → sonido "necesito respuesta"
  - sin prompt                 → WS agente_termino → sonido "terminé"

Eventos WS por el broadcaster del proyecto:
  - agente_espera     {terminal_id, terminal_nombre}
  - agente_termino    {terminal_id, terminal_nombre}
  - agente_trabajando {terminal_id, terminal_nombre} — la máquina se armó
    (idle→trabajando): el frontend apaga el aura de notificación de esa card.

El monitor de keywords (terminals.py) registra acá cada TASK_* detectado vía
registrar_keyword(); si hubo uno hace <SUPRESION_S para esa terminal, el poller
no emite (ese sonido ya sonó por la vía del protocolo).
"""

import asyncio
import os
import re
import time

from plotspace.core.database import get_db
from plotspace.core.events import broadcaster
from plotspace.core import logs as _logs
from plotspace.core import pane_capture
from plotspace.core import cli_accounts

INTERVALO_S = 1
TTL_CAPTURA_PROPIA = 0.9    # < INTERVALO_S SIEMPRE: la máquina de estados compara
                            # pane contra pane ENTRE ticks — un cache ≥ tick le
                            # devolvería el mismo texto tick por medio y el conteo
                            # de cambios consecutivos no llegaría nunca al umbral.
                            # Este poller es el CAPTURADOR fresco del sistema; los
                            # de 2s (agent_live/dev_detect) reusan su captura
                            # vía el TTL default más largo de pane_capture.
POLLS_PARA_TRABAJANDO = 4   # ≥4 polls seguidos con cambios = trabajando (~4s);
                            # filtra comandos manuales y tipeo breve. Las CLIs
                            # reales redibujan su spinner cada ~1s: nunca fallan.
POLLS_PARA_QUIETO = 4       # 4 polls sin cambios = quieto (~4s) → el sonido de
                            # "terminé" sale 4-5s después del último output
                            # (antes era 6-9s con polls de 3s y umbral 2)
SUPRESION_S = 10            # keyword TASK_* hace <10s → no duplicar sonido
GRACIA_ARRANQUE_S = 60      # terminal joven (<60s para el poller) → no emitir:
                            # el boot de las CLIs es multifase (banner → quieto
                            # → ráfaga final) y dispara falsos "terminé"
CONFIRMACION_FIN_S = 12     # Tras la quietud (evaluar), el "terminé" NO suena al
                            # toque: se confirma que el pane sigue IGUAL estos
                            # segundos. Un turno que termina y el agente RETOMA
                            # poco después (típico: commitea una parte y sigue) NO
                            # es "terminó de verdad" — si el pane vuelve a cambiar
                            # dentro de la ventana, el fin se CANCELA (no suena).
                            # 'agente_espera' (prompt a la vista) sigue inmediato.

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[mGKHFJA-Z]')

# TASK_* solo-en-línea (espejo de _KW_SOLO_RE en terminals.py): acepta
# "✅ TASK_DONE", filtra la instrucción "Cuando termines escribí TASK_DONE".
_KW_SOLO_RE = re.compile(r'^[^a-zA-Z]*TASK_(DONE|BLOCKED|ERROR)[^a-zA-Z]*$')

# Prompts interactivos de las CLIs (Claude Code, Codex, Gemini, y/n genérico).
# Se evalúan solo sobre la COLA del pane (últimas _LINEAS_COLA líneas con
# contenido): una pregunta vieja que quedó arriba ya fue respondida.
_LINEAS_COLA = 12
_PATRONES_PREGUNTA = [
    re.compile(r'do you want', re.I),
    re.compile(r'\[y/n\]', re.I),
    re.compile(r'\(y/n\)', re.I),
    re.compile(r'^\W*1[.)]\s+\S', re.M),      # menú numerado (❯ 1. Yes / 1) Sí)
    re.compile(r'esc to cancel', re.I),
    re.compile(r'press enter', re.I),
    re.compile(r'(continue|proceed|continuar)\?', re.I),
    re.compile(r'\ballow\b.*\?', re.I),
]


# ─── Lógica pura (testeable sin tmux ni asyncio) ──────────────────────────────

def hay_pregunta(texto) -> bool:
    """¿Las últimas líneas del pane muestran un prompt interactivo esperando
    al usuario?"""
    limpio = _ANSI_RE.sub('', texto or '')
    lineas = [l.strip() for l in limpio.splitlines() if l.strip()]
    cola = '\n'.join(lineas[-_LINEAS_COLA:])
    return any(p.search(cola) for p in _PATRONES_PREGUNTA)


def hay_keyword_protocolo(texto) -> bool:
    """¿La cola del pane termina con un TASK_* real (línea sola, mismo filtro
    que _linea_es_keyword en terminals.py)? Entonces este final le corresponde
    al protocolo de keywords: con el poller a 1s el heurístico (4-5s) puede
    ganarle la carrera al monitor de keywords (5s) y duplicar el sonido."""
    limpio = _ANSI_RE.sub('', texto or '')
    lineas = [l.strip() for l in limpio.splitlines() if l.strip()]
    return any(_KW_SOLO_RE.match(l) for l in lineas[-_LINEAS_COLA:])


# ─── Auto-rotación de cuentas al pegar contra el rate-limit (lógica PURA) ──────
# "El foso": ningún competidor orquesta varias cuentas de suscripción. Cuando un
# agente pega su rate/usage limit, rotamos a otra cuenta sana del MISMO tipo_ia
# usando el switch que YA existe en cli_accounts (conservador: no se reinventa el
# flujo de cuentas, que toca OAuth real). v1 = detectar + switch + avisar.

AUTO_ROTACION_ENV = 'AUTO_ROTACION'   # 'on' (default) / 'off'
COOLDOWN_ROTACION_S = 600   # por terminal: a lo sumo una rotación cada ~10 min.
                            # El mensaje de límite queda clavado en el pane un
                            # buen rato → sin esto rotaríamos cada segundo.
LIMITE_CUENTA_TTL_S = 5 * 3600  # ventana en que una cuenta que pegó el límite se
                                # EVITA al elegir la próxima (≈ reset del
                                # "5-hour limit" de Claude); luego vuelve a entrar.
_LINEAS_LIMITE = 15         # se evalúa solo la COLA del pane (un límite que ya
                            # scrolleó arriba no cuenta — el agente siguió).

# Firmas de rate/usage limit en DOS niveles (auditoría 2026-07-02: la rotación
# por falso positivo reescribe la credencial GLOBAL de claude y veta la cuenta
# sana 5 horas — el radio de daño exige asimetría):
#   - FUERTES: jerga inequívoca que las CLIs imprimen al frenarse por SU límite.
#     Rotan en cualquier fase.
#   - GENÉRICAS: prosa HTTP común que también aparece en el CONTENIDO del
#     trabajo (un agente debuggeando los 429 de la app del usuario, fixtures de
#     tests). Solo rotan si el agente NO está 'trabajando': una CLI limitada de
#     verdad se frena (pane quieto); el 429 del trabajo viene con pane vivo.
_LIMITE_FUERTES = [re.compile(p, re.I) for p in (
    r'usage limit reached',
    r'reached your usage limit',
    r'approaching usage limit',
    r'hit your .{0,20}limit',                       # "you've hit your usage/plan limit"
    r'5[\s-]?hour limit',
    r'rate_limit_error',                            # error type verbatim de la API Anthropic
)]
_LIMITE_GENERICOS = [re.compile(p, re.I) for p in (
    r'rate.?limit.?(?:reached|exceeded|error|exhausted)',
    r'quota exceeded',
    r'has been exhausted',                          # "Resource has been exhausted" (quota)
    r'too many requests',
    r'try again (?:at|in)\b',                       # "...at 3pm" / "...in 25 min" (reset)
)]


def linea_limite(texto):
    """(linea, tier) de la primera línea de la COLA del pane que matchea una
    firma de límite — tier ∈ {'fuerte','generica'} — o (None, None). Las fuertes
    se buscan primero: si conviven ambas, gana la clasificación fuerte."""
    limpio = _ANSI_RE.sub('', texto or '')
    lineas = [l.strip() for l in limpio.splitlines() if l.strip()]
    cola = lineas[-_LINEAS_LIMITE:]
    for l in cola:
        if any(p.search(l) for p in _LIMITE_FUERTES):
            return l, 'fuerte'
    for l in cola:
        if any(p.search(l) for p in _LIMITE_GENERICOS):
            return l, 'generica'
    return None, None


def debe_rotar(texto, fase) -> tuple:
    """Decisión PURA de la auto-rotación: (rotar, linea, tier).
    Fuerte → rota siempre. Genérica → solo con el agente NO 'trabajando'
    (fase None = terminal sin estado todavía: no se bloquea)."""
    linea, tier = linea_limite(texto)
    if linea is None:
        return False, None, None
    if tier == 'generica' and fase == 'trabajando':
        return False, linea, tier
    return True, linea, tier


def proxima_cuenta_sana(cuentas, activa_id, excluidas=None):
    """Selección PURA de la próxima cuenta a la que rotar. Round-robin: empieza
    JUSTO DESPUÉS de la activa y devuelve el primer id que no sea la activa ni
    esté en `excluidas` (cuentas limitadas hace poco). None si no hay ninguna
    sana distinta de la activa.

    `cuentas` = lista de dicts con 'id' (orden estable, como cli_accounts.listar).
    Si la activa no está en la lista, recorre desde el principio."""
    excluidas = excluidas or set()
    ids = [c['id'] for c in cuentas]
    if activa_id in ids:
        start = ids.index(activa_id) + 1
        orden = ids[start:] + ids[:start]
    else:
        orden = ids
    for cid in orden:
        if cid == activa_id or cid in excluidas:
            continue
        return cid
    return None


# Estado impuro de la rotación (testeable: las funciones toman `ahora`/`ts`).
_rotacion_terminal: dict = {}   # terminal_id → time.monotonic() de la última rotación
_limite_cuenta: dict = {}       # account_id → time.monotonic() en que pegó el límite

# Dedupe del audit de descartes: la firma genérica queda EN el pane muchos ticks
# seguidos (poller de 1s) — sin esto, el log se inunda con la misma línea.
_DESCARTE_LOG_S = 300
_descarte_logueado: dict = {}   # terminal_id → monotonic del último log de descarte


def _descartar_logueable(terminal_id: int, ahora: float = None) -> bool:
    ahora = time.monotonic() if ahora is None else ahora
    t = _descarte_logueado.get(terminal_id)
    if t is not None and ahora - t < _DESCARTE_LOG_S:
        return False
    _descarte_logueado[terminal_id] = ahora
    return True


def registrar_rotacion(terminal_id: int, ts: float = None):
    """Sella el cooldown de rotación de una terminal."""
    _rotacion_terminal[terminal_id] = time.monotonic() if ts is None else ts


def cooldown_rotacion_vencido(terminal_id: int, ahora: float = None) -> bool:
    """¿Pasó COOLDOWN_ROTACION_S desde la última rotación de esta terminal?
    (True también si nunca rotó: no hay cooldown que esperar.)"""
    t = _rotacion_terminal.get(terminal_id)
    if t is None:
        return True
    ahora = time.monotonic() if ahora is None else ahora
    return ahora - t >= COOLDOWN_ROTACION_S


def registrar_limite_cuenta(account_id: int, ts: float = None):
    """Marca que una cuenta pegó su límite (para evitarla al elegir la próxima)."""
    _limite_cuenta[account_id] = time.monotonic() if ts is None else ts


def cuentas_limitadas(ahora: float = None) -> set:
    """Ids de cuentas que pegaron su límite dentro de LIMITE_CUENTA_TTL_S (las que
    NO conviene reusar todavía). Las vencidas se ignoran (vuelven a entrar)."""
    ahora = time.monotonic() if ahora is None else ahora
    return {cid for cid, t in _limite_cuenta.items()
            if ahora - t < LIMITE_CUENTA_TTL_S}


def estado_inicial(ts: float = None) -> dict:
    """Estado de la máquina para una terminal recién vista. Arranca en
    'arrancando': el boot de la CLI (banner/loading) NO es trabajo — recién
    se arma (pasa a 'idle') cuando el pane se observó estable una vez.
    `ts` (monotonic) = nacimiento para la gracia de arranque; None = sin gracia
    (tests de la máquina pura)."""
    return {'fase': 'arrancando', 'hash': None, 'cambios': 0, 'quietos': 0,
            'nacido': ts, 'esperando': False, 'fase_desde': ts}


def actualizar_esperando(st: dict, evento, texto) -> dict:
    """Mantiene st['esperando']: el agente cerró su ciclo de trabajo con un
    prompt interactivo a la vista → sigue a mitad de tarea (gate del updater).
    Se setea al 'evaluar' ANTES de la supresión de sonidos (la supresión calla
    la campana, no el estado) y se limpia al volver a trabajar o cuando la
    pregunta desaparece de la cola del pane (el usuario respondió y el agente
    cerró sin llegar a re-armar la máquina — sin esto quedaba True para
    siempre y el banner no salía nunca)."""
    if evento == 'evaluar':
        esperando = hay_pregunta(texto)
    elif evento == 'trabajando':
        esperando = False
    else:
        esperando = bool(st.get('esperando')) and hay_pregunta(texto)
    return dict(st, esperando=esperando)


def hay_agentes_ocupados(estados: dict = None, terminal_ids=None) -> bool:
    """¿Algún agente está a mitad de tarea? Trabajando, o quieto con un prompt
    esperando respuesta del usuario. Lo consume /api/system/version (gate del
    banner "Actualizar ahora"). `terminal_ids` (set) acota a esas terminales:
    el gate del update solo mira el proyecto de Jarvis, no otros proyectos."""
    if estados is None:
        estados = _estados
    items = estados.items()
    if terminal_ids is not None:
        items = [(k, v) for k, v in items if k in terminal_ids]
    return any(st.get('fase') == 'trabajando' or st.get('esperando')
               for _, st in items)


def terminales_trabajando(estados: dict = None, pendientes=None) -> list:
    """IDs de terminal que están 'trabajando' EN NIVEL (no edge) — la fuente
    única del brillo Liquid Glass de la card Y del indicador 'trabajando' de la
    pestaña Live. Incluye:
      - fase == 'trabajando' (activa), y
      - las que están CONFIRMANDO su fin (`_fin_pendiente`): la máquina ya vio
        quietud pero el 'terminé' todavía no se confirmó. Mantenerlas acá hace
        que el brillo/Live NO se apaguen a los ~4s de quietud y después el sonido
        12s más tarde (desconectados), sino que el brillo, el indicador Live y el
        sonido se apaguen/suenen TODOS JUNTOS cuando el fin se confirma (o sigan
        encendidos si el agente retoma). Así "conectado" al Live, como pidió el
        usuario. El frontend también lo pollea junto con los títulos → recupera
        el estado tras un reload (el WS agente_trabajando es solo edge)."""
    if estados is None:
        estados = _estados
    if pendientes is None:
        pendientes = _fin_pendiente
    return [tid for tid, st in estados.items()
            if st.get('fase') == 'trabajando'
            or tid in pendientes
            # Re-armando desde idle (volvió a producir output tras una pausa/fin
            # cancelado): mantener el brillo encendido AHORA — sin esperar los 4
            # polls que tarda en re-armar a 'trabajando' → no parpadea al retomar.
            or (st.get('fase') == 'idle' and st.get('cambios', 0) > 0)]


# Estado de trabajo por PROYECTO (para el anillo verde del orbe en la franja).
# Se emite GLOBAL (broadcast_global) — así TODOS los clientes ven encenderse el
# anillo del workspace que arrancó a trabajar, no solo el del proyecto activo
# (los eventos agente_* son por-proyecto). Dedupe: solo se emite en el EDGE
# busy↔idle del proyecto, no en cada tick.
_proyecto_trabajo_last: dict = {}   # project_id → bool ya emitido


async def _emitir_trabajo_proyecto(pid, rows):
    """Si el estado de trabajo del proyecto `pid` cambió (algún agente trabajando
    ⇄ ninguno), lo avisa GLOBAL. `rows` son las terminales activas de este tick
    (traen tid + pid), así se sabe qué terminales son del proyecto."""
    try:
        pid_tids = {r['tid'] for r in rows if r['pid'] == pid}
        trabajando = bool(pid_tids & set(terminales_trabajando()))
        if _proyecto_trabajo_last.get(pid) == trabajando:
            return
        _proyecto_trabajo_last[pid] = trabajando
        await broadcaster.broadcast_global({
            'type': 'proyecto_trabajo',
            'project_id': pid,
            'trabajando': trabajando,
        })
    except Exception:
        pass


def edad_desde_creacion(fecha_creacion_iso, ahora_wall: float) -> float:
    """Segundos desde que se creó la terminal (fecha_creacion ISO de la DB) hasta
    `ahora_wall` (timestamp de reloj). Robusto a None / formato inválido → 0.0.
    Sirve para reconstruir la gracia de arranque tras un restart del server."""
    if not fecha_creacion_iso:
        return 0.0
    try:
        from datetime import datetime
        creado = datetime.fromisoformat(str(fecha_creacion_iso)).timestamp()
    except (ValueError, TypeError):
        return 0.0
    return max(0.0, ahora_wall - creado)


def sembrar_estado(activo: bool, hash_actual, edad_seg: float, ahora: float) -> dict:
    """Estado con el que se SIEMBRA una terminal viva tras un restart del server
    (cuando _estados quedó vacío). Clave del fix: NUNCA 'arrancando' — su única
    salida es la estabilidad que un pane activo jamás da, así que una terminal
    que venía trabajando quedaría atrapada ahí y `terminales_trabajando()` la
    dejaría afuera. En cambio:
      activo=True  (el pane cambió entre dos capturas) → 'trabajando' directo
      activo=False (pane quieto en el arranque)        → 'idle' (arma normal si
                    retoma; sin trampa)
    `nacido` se reconstruye de la edad real (ahora - edad_seg): una terminal
    vieja queda SIN gracia (para no callar un 'terminó' real 60s post-restart);
    una recién creada conserva la gracia residual (podía estar booteando)."""
    st = estado_inicial(ts=ahora - min(edad_seg, 3600.0))
    st['hash'] = hash_actual
    if activo:
        st.update(fase='trabajando', cambios=POLLS_PARA_TRABAJANDO, quietos=0)
    else:
        st.update(fase='idle', cambios=0, quietos=0)
    return st


def en_gracia(st: dict, ahora: float = None) -> bool:
    """¿La terminal es demasiado joven para emitir? El boot de las CLIs es
    multifase (banner → quieto → ráfaga final): 'arrancando' sola no alcanza,
    porque la máquina se arma en la primera ventana estable y la ráfaga final
    del boot dispara un falso 'terminé'. Mientras dura la gracia, la máquina
    corre igual pero el poller no emite."""
    nacido = st.get('nacido')
    if nacido is None:
        return False
    ahora = time.monotonic() if ahora is None else ahora
    return ahora - nacido < GRACIA_ARRANQUE_S


def transicionar(st: dict, hash_pane) -> tuple:
    """Avanza la máquina con el hash del contenido del pane de este poll.
    Devuelve (nuevo_estado, evento) — evento ∈ {None, 'trabajando', 'evaluar'}.
    'trabajando' = se armó (idle→trabajando): el caller emite agente_trabajando
    (el frontend apaga el aura de esa card). 'evaluar' = venía trabajando y se
    quedó quieto: el caller clasifica pregunta/fin con el texto del pane y
    emite el WS que corresponda."""
    cambio = st['hash'] is not None and hash_pane != st['hash']
    nuevo = dict(st, hash=hash_pane)

    if st['fase'] == 'arrancando':
        # El boot de la CLI (banner/loading) churnea unos polls y se asienta:
        # esperamos POLLS_PARA_QUIETO polls estables antes de armar → ese churn
        # no cuenta como trabajo. PERO si el pane cambia SIN PARAR (nunca se
        # asienta) NO podemos quedarnos acá para siempre: un agente que arranca
        # trabajando ya (el orquestador le inyecta la tarea al instante, o el
        # server reinició mientras generaba) se quedaría atrapado y nunca
        # emitiría 'trabajando'. Si el churn se sostiene POLLS_PARA_TRABAJANDO
        # polls, es trabajo real → armamos igual (el falso 'terminó' del boot
        # que esto podría destapar lo tapa la gracia de arranque de 60s).
        if cambio:
            nuevo['quietos'] = 0
            nuevo['cambios'] = st['cambios'] + 1
            if nuevo['cambios'] >= POLLS_PARA_TRABAJANDO:
                nuevo.update(fase='trabajando', quietos=0)
                return nuevo, 'trabajando'
        else:
            nuevo['cambios'] = 0
            nuevo['quietos'] = st['quietos'] + 1
            if nuevo['quietos'] >= POLLS_PARA_QUIETO:
                nuevo.update(fase='idle', cambios=0, quietos=0)
        return nuevo, None

    if st['fase'] == 'idle':
        nuevo['cambios'] = st['cambios'] + 1 if cambio else 0
        if nuevo['cambios'] >= POLLS_PARA_TRABAJANDO:
            nuevo['fase'] = 'trabajando'
            nuevo['quietos'] = 0
            return nuevo, 'trabajando'
        return nuevo, None

    # trabajando
    if cambio:
        nuevo['quietos'] = 0
        return nuevo, None
    nuevo['quietos'] = st['quietos'] + 1
    if nuevo['quietos'] >= POLLS_PARA_QUIETO:
        nuevo.update(fase='idle', cambios=0, quietos=0)
        return nuevo, 'evaluar'
    return nuevo, None


def confirmar_fin(pendiente, hash_actual, ahora: float, confirmacion_s: float = None) -> str:
    """Un 'terminé' quedó PENDIENTE de confirmar (la máquina vio quietud). En
    este poll decide, MIRANDO SI EL PANE SIGUE IGUAL:
      'cancelar' → el pane cambió: el agente retomó → NO terminó (no suena).
      'emitir'   → siguió idéntico ≥ confirmacion_s → terminó de verdad → suena.
      'esperar'  → todavía dentro de la ventana de confirmación.
    Puro (recibe el reloj). `pendiente` = (desde_monotonic, hash_pane_al_quedar)."""
    if confirmacion_s is None:
        confirmacion_s = CONFIRMACION_FIN_S
    desde, hash0 = pendiente
    if hash_actual != hash0:
        return 'cancelar'
    if ahora - desde >= confirmacion_s:
        return 'emitir'
    return 'esperar'


# ─── Supresión: el protocolo de keywords ya sonó ──────────────────────────────

_ultimo_keyword: dict = {}  # terminal_id → time.monotonic() del último TASK_*


def registrar_keyword(terminal_id: int, ts: float = None):
    """Llamado por el monitor de keywords de terminals.py al detectar TASK_*."""
    _ultimo_keyword[terminal_id] = time.monotonic() if ts is None else ts


def suprimido(terminal_id: int, ahora: float = None) -> bool:
    """¿Hubo un keyword hace <SUPRESION_S? Entonces el sonido ya sonó."""
    t = _ultimo_keyword.get(terminal_id)
    if t is None:
        return False
    ahora = time.monotonic() if ahora is None else ahora
    return ahora - t < SUPRESION_S


# ─── Auto-rotación: capa impura (switch + WS) ─────────────────────────────────

def _auto_rotacion_on() -> bool:
    return os.getenv(AUTO_ROTACION_ENV, 'on').lower() != 'off'


def _label_cuenta(cuentas, cid) -> str:
    """Nombre legible de una cuenta para el toast (label > email > #id)."""
    if cid is None:
        return '—'
    c = next((x for x in cuentas if x['id'] == cid), None)
    if not c:
        return f'#{cid}'
    return (c.get('label') or '').strip() or c.get('email') or f'#{cid}'


def _rotar_cuenta_sync(tipo_ia: str):
    """SÍNCRONO (corre en to_thread: toca DB + filesystem del switch de cuentas).
    Marca la cuenta activa de `tipo_ia` como limitada, elige la próxima sana y la
    activa con el switch EXISTENTE (cli_accounts.usar) — NO toca el aislamiento de
    Codex ni reinventa el flujo de cuentas. Devuelve un dict para que la capa
    async emita el WS: {'a': id, ...} si rotó, o {'sin_cuenta': True} si no había
    candidata sana."""
    cuentas = [c for c in cli_accounts.listar() if c['tipo'] == tipo_ia]
    activa = next((c for c in cuentas if c['activa']), None)
    activa_id = activa['id'] if activa else None
    if activa_id is not None:
        registrar_limite_cuenta(activa_id)        # la activa pegó el límite: evitarla un rato
    siguiente = proxima_cuenta_sana(cuentas, activa_id, cuentas_limitadas())
    if siguiente is None:
        return {'sin_cuenta': True}
    cli_accounts.usar(siguiente)                  # ← switch que YA existe; no se toca
    return {
        'de': activa_id,
        'a': siguiente,
        'de_label': _label_cuenta(cuentas, activa_id),
        'a_label': _label_cuenta(cuentas, siguiente),
    }


async def _quizas_rotar(row, texto):
    """ADITIVO al ciclo del poller: si el pane muestra una firma de límite y venció
    el cooldown de la terminal, rota a otra cuenta sana del mismo tipo_ia y avisa
    por WS. NO toca la máquina de estados ni los sonidos.

    # TODO v2: re-inyectar/retry en la terminal del agente (hoy v1 = switch +
    #          aviso; el re-login/retry lo dispara el usuario o el próximo arranque
    #          del CLI, que ya levanta con la cuenta nueva)."""
    if not _auto_rotacion_on():
        return
    tipo_ia = (row.get('tipo_ia') or '').strip()
    if tipo_ia not in cli_accounts.TIPOS:
        return  # 'manual'/'gemini'/etc: no hay cuentas que rotar
    tid = row['tid']
    fase = (_estados.get(tid) or {}).get('fase')
    rotar, linea, tier = debe_rotar(texto, fase)
    if linea is None:
        return
    if not rotar:
        # Firma genérica con el agente trabajando = casi seguro contenido del
        # TRABAJO (no un límite real). Se audita (deduplicado: una vez por
        # terminal por ventana) para poder diagnosticar si algún día un límite
        # real quedara retenido por esta regla.
        if _descartar_logueable(tid):
            _logs.evento('auto_rotacion_descartada', nivel='warn', terminal_id=tid,
                         tipo_ia=tipo_ia, tier=tier, fase=fase, linea=linea[:200])
        return
    if not cooldown_rotacion_vencido(tid):
        return  # ya rotamos hace poco para esta terminal: no flapear
    registrar_rotacion(tid)   # sella el cooldown YA (aunque no haya candidata o falle)
    # Audit trail SIEMPRE que se decide rotar: la línea exacta que disparó, su
    # tier y la fase — sin esto, un falso positivo era indiagnosticable.
    _logs.evento('auto_rotacion_firma', terminal_id=tid, tipo_ia=tipo_ia,
                 tier=tier, fase=fase, linea=linea[:200])
    try:
        res = await asyncio.to_thread(_rotar_cuenta_sync, tipo_ia)
    except Exception as e:
        _logs.evento('auto_rotacion_error', nivel='error', terminal_id=tid,
                     tipo_ia=tipo_ia, error=str(e))
        return
    if res.get('sin_cuenta'):
        await broadcaster.broadcast(row['pid'], {
            'type': 'limite_sin_cuenta',
            'terminal_id': tid,
            'tipo_ia': tipo_ia,
        })
        _logs.evento('auto_rotacion_sin_cuenta', nivel='warn',
                     terminal_id=tid, tipo_ia=tipo_ia)
        return
    await broadcaster.broadcast(row['pid'], {
        'type': 'cuenta_rotada',
        'terminal_id': tid,
        'tipo_ia': tipo_ia,
        'de': res['de'],
        'a': res['a'],
        'de_label': res['de_label'],
        'a_label': res['a_label'],
    })
    _logs.evento('auto_rotacion_rotada', terminal_id=tid, tipo_ia=tipo_ia,
                 de=res['de_label'], a=res['a_label'])


# ─── Poller ────────────────────────────────────────────────────────────────────

_estados: dict = {}  # terminal_id → estado de la máquina
_fin_pendiente: dict = {}  # terminal_id → (desde_monotonic, hash_pane): 'terminé' a confirmar


async def _capture_pane(terminal_id: int) -> str:
    """Pane tmux vía la captura COMPARTIDA con cache: una sola captura por terminal por tick sirve
    a los tres pollers (antes cada uno forkeaba su propio capture-pane). Es el mismo bloque que el
    viejo `-S -120`; el estado/keyword se evalúa sobre la COLA, así que ver un poco más de scrollback
    es inocuo (la detección de TASK_DONE es un monitor APARTE y no pasa por acá)."""
    return await pane_capture.capturar(terminal_id, ttl=TTL_CAPTURA_PROPIA)


async def poller_agentes():
    """Background task: nunca crashea el servidor (patrón STATE.md)."""
    while True:
        await asyncio.sleep(INTERVALO_S)
        try:
            await _ciclo()
        except Exception as e:
            print(f'[agent-watch] Error en ciclo: {e}')


def _rows_activas():
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id AS tid, nombre AS tnombre, project_id AS pid, '
            'tipo_ia AS tipo_ia, fecha_creacion AS creada '
            'FROM terminals WHERE activa = 1'
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


async def reconciliar_al_boot():
    """Siembra `_estados` desde los panes VIVOS al arrancar el server (una sola
    vez, en el startup). Sin esto, tras un reload por actualización una terminal
    que YA venía trabajando se recreaba en 'arrancando' y quedaba atrapada (ver
    sembrar_estado) → la Live/las cards la daban por idle. Detecta "activa"
    comparando DOS capturas separadas (un pane que trabaja redibuja su spinner
    ~cada 1s → cambia); siembra 'trabajando' a esas y 'idle' al resto, y emite
    agente_trabajando (para el cliente que reconecta; el handshake 'hola' también
    lleva la lista, así que no depende del timing del broadcast). Best-effort:
    nunca rompe el startup."""
    try:
        rows = await asyncio.to_thread(_rows_activas)
        if not rows:
            return
        # Dos capturas frescas (ttl=0 fuerza captura real, no cache) separadas lo
        # suficiente para que el spinner de una CLI activa cambie con seguridad.
        cap1 = await asyncio.gather(*[pane_capture.capturar(r['tid'], ttl=0) for r in rows])
        await asyncio.sleep(1.3)
        cap2 = await asyncio.gather(*[pane_capture.capturar(r['tid'], ttl=0) for r in rows])
        ahora_mono = time.monotonic()
        ahora_wall = time.time()
        trabajando = []
        for row, t1, t2 in zip(rows, cap1, cap2):
            tid = row['tid']
            activo = bool(t1) and bool(t2) and _norm_pane(t1) != _norm_pane(t2)
            edad = edad_desde_creacion(row.get('creada'), ahora_wall)
            _estados[tid] = sembrar_estado(activo, hash(_norm_pane(t2)), edad, ahora_mono)
            if activo:
                trabajando.append(row)
        for row in trabajando:
            await broadcaster.broadcast(row['pid'], {
                'type': 'agente_trabajando',
                'terminal_id': row['tid'],
                'terminal_nombre': row['tnombre'],
            })
        print(f'[agent-watch] boot reconcile: {len(rows)} terminales, '
              f'{len(trabajando)} trabajando')
    except Exception as e:
        print(f'[agent-watch] reconciliar_al_boot falló: {e}')


def _norm_pane(texto: str) -> str:
    """Limpia ANSI para comparar dos capturas por CONTENIDO (no por bytes de
    escape/cursor que cambian sin que el agente trabaje)."""
    return _ANSI_RE.sub('', texto or '')


async def _ciclo():
    # DB SÍNCRONA fuera del event loop (to_thread): bajo WAL + busy_timeout, una colisión de
    # writers bloquearía el ÚNICO loop hasta 5s (terminales/voz/chat/navegación a la vez). Este
    # poller corre cada 1s → sin esto, cada tick mete una query sync en el loop.
    rows = await asyncio.to_thread(_rows_activas)

    # Purga: terminales eliminadas no dejan estado huérfano en memoria
    vivas = {r['tid'] for r in rows}
    for tid in list(_estados.keys()):
        if tid not in vivas:
            _estados.pop(tid, None)
            _ultimo_keyword.pop(tid, None)
            _fin_pendiente.pop(tid, None)       # fin a confirmar de una terminal muerta
            _rotacion_terminal.pop(tid, None)   # cooldown de rotación por terminal
            _descarte_logueado.pop(tid, None)   # dedupe del audit de descartes
            # _limite_cuenta NO se purga acá: está keyado por account_id (no por
            # terminal) y se auto-expira por LIMITE_CUENTA_TTL_S.

    if not rows:
        return   # sin terminales activas: nada que pollear (CPU idle ≈ 0)

    # Capturas EN PARALELO: antes era un await secuencial por terminal → con N
    # terminales el ciclo tardaba N×captura y agregaba latencia constante. gather
    # corta el wall-clock al de la captura más lenta (auditoría perf #7).
    textos = await asyncio.gather(*[_capture_pane(r['tid']) for r in rows])
    for row, texto in zip(rows, textos):
        tid = row['tid']
        if not texto:
            continue  # tmux caído / sesión inexistente: saltear sin romper
        # Fast-path workflow: si la cola del pane ya muestra un TASK_* real y el
        # monitor de keywords todavía no lo procesó (no suprimido), despertarlo
        # para que avance el workflow en ~1s en vez de esperar su sleep de 2s.
        # SOLO una pista barata: agent_watch NO inserta el evento ni llama al
        # orquestador (eso es exclusivo del monitor de terminals.py, único
        # escritor). Best-effort: si falla, el monitor detecta igual a <=2s.
        if hay_keyword_protocolo(texto) and not suprimido(tid):
            try:
                from plotspace.routers.terminals import solicitar_chequeo_inmediato
                solicitar_chequeo_inmediato(tid)
            except Exception:
                pass
        # Auto-rotación de cuenta al pegar el rate-limit. ADITIVO: corre ANTES de
        # la máquina de estados y NO la toca (ni a los sonidos). Short-circuita
        # barato (un regex sobre la cola) cuando no hay firma de límite.
        await _quizas_rotar(row, texto)
        st = _estados.get(tid) or estado_inicial(ts=time.monotonic())
        nuevo, evento = transicionar(st, hash(texto))
        # Sello de cuándo entró a la fase ACTUAL ("lleva N min trabajando"). transicionar es pura (no ve el reloj),
        # así que el timestamp se pone acá, en la capa impura.
        if nuevo.get('fase') != st.get('fase'):
            nuevo['fase_desde'] = time.monotonic()
        elif nuevo.get('fase_desde') is None:
            nuevo['fase_desde'] = st.get('fase_desde', time.monotonic())
        nuevo = actualizar_esperando(nuevo, evento, texto)
        _estados[tid] = nuevo
        if evento == 'trabajando':
            # El agente retomó actividad: el frontend apaga el aura de esa
            # card. Sin supresión ni gracia (apagar de más es inocuo). Y cancela
            # cualquier 'terminé' que estuviera confirmándose (no terminó).
            _fin_pendiente.pop(tid, None)
            await broadcaster.broadcast(row['pid'], {
                'type': 'agente_trabajando',
                'terminal_id': tid,
                'terminal_nombre': row['tnombre'],
            })
            continue

        # ¿Hay un 'terminé' PENDIENTE de confirmar? (la máquina ya vio quietud en
        # un poll anterior). Solo suena si el pane siguió IGUAL CONFIRMACION_FIN_S;
        # si volvió a cambiar (el agente retomó tras un commit/pausa), se cancela.
        pend = _fin_pendiente.get(tid)
        if pend is not None:
            accion = confirmar_fin(pend, hash(texto), time.monotonic())
            if accion == 'esperar':
                continue
            _fin_pendiente.pop(tid, None)
            if accion == 'emitir' and not (suprimido(tid) or hay_keyword_protocolo(texto)):
                await broadcaster.broadcast(row['pid'], {
                    'type': 'agente_termino',
                    'terminal_id': tid,
                    'terminal_nombre': row['tnombre'],
                })
                print(f'[agent-watch] agente_termino en terminal {tid} (confirmado)')
            continue

        if (evento != 'evaluar' or suprimido(tid) or en_gracia(nuevo)
                or hay_keyword_protocolo(texto)):
            # hay_keyword_protocolo: el final con TASK_* lo suena el monitor
            # de keywords (terminals.py), no el heurístico — sin esto, al
            # acelerar el poller sonaban las dos campanas en los workflows.
            continue
        if hay_pregunta(texto):
            # Prompt interactivo a la vista = está esperando AHORA: inmediato, sin
            # confirmar (no es un "terminé", es "necesito respuesta").
            await broadcaster.broadcast(row['pid'], {
                'type': 'agente_espera',
                'terminal_id': tid,
                'terminal_nombre': row['tnombre'],
            })
            print(f'[agent-watch] agente_espera en terminal {tid}')
        else:
            # Candidato a 'terminé': NO suena todavía. Queda pendiente de que el
            # pane siga quieto CONFIRMACION_FIN_S (se confirma/cancela arriba).
            _fin_pendiente[tid] = (time.monotonic(), hash(texto))

    # Anillo verde del orbe por PROYECTO (franja): recomputar el estado de trabajo
    # de cada proyecto de este tick y avisar GLOBAL solo si CAMBIÓ (dedupe adentro).
    # Se hace acá, sobre el estado ya actualizado, para atrapar TODAS las
    # transiciones —incluidas las que no emiten sonido (suprimidas/keyword)— y que
    # el anillo se apague apenas el último agente del workspace deja de trabajar.
    for pid in {r['pid'] for r in rows}:
        await _emitir_trabajo_proyecto(pid, rows)
    # Purga del dedupe: proyectos sin terminales activas ya no pueden estar
    # trabajando (evita fugas de memoria y un estado 'True' clavado).
    _pids_vivos = {r['pid'] for r in rows}
    for pid in [p for p in _proyecto_trabajo_last if p not in _pids_vivos]:
        _proyecto_trabajo_last.pop(pid, None)
