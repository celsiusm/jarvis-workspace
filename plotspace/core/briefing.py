# plotspace/core/briefing.py
"""Briefing del enjambre — que saber del otro no dependa de que el agente pregunte.

EL AGUJERO QUE TAPA
-------------------
`jv estado` tiene el dato fresco y completo, pero es PULL: el agente tiene que
acordarse de correrlo. No se acuerda. Y la única entrega GARANTIZADA de contexto
que existía (`mailbox.bloque_pendientes_para_tarea`) solo corre cuando Jarvis
entrega una tarea — pero el trabajo real de este repo pasa en terminales directas, donde el usuario
pega la tarea a mano. En ese camino no había ningún momento en el que el sistema
garantizara que el agente sabe con quién está compartiendo el árbol.

Resultado medido: los agentes se enteraban del otro CHOCÁNDOSE, y después
gastaban una saga de mensajes en repararlo (73% del tráfico del mailbox habla de
commits/índice; 24% es "tu trabajo sigue sin commitear, agarralo vos").

EL DISEÑO
---------
El briefing se ARMA acá (server, con el estado vivo) y se ENTREGA sin iniciativa
del agente. Dos canales, por orden de calidad:

  1. `UserPromptSubmit` — el CLI le pasa el briefing ANTES de que el agente
     piense. Cero turnos, cero latencia. Es el bueno.
  2. Piggyback en el resultado de su primera herramienta (`/swarm/op`,
     `/swarm/check`) — para los CLIs sin hook de prompt. Llega un poco tarde
     (el agente ya arrancó) pero antes de que pueda romper nada, porque el
     primer movimiento de cualquier agente es leer o escribir un archivo.

DOS REGLAS QUE NO SE NEGOCIAN
-----------------------------
  · **Si no hay nada que decir, no se dice nada.** Un agente solo en el proyecto
    y sin mensajes recibe '' — string vacío, cero tokens. Un briefing vacío
    repetido en cada prompt entrena al agente a saltear el bloque entero, y ahí
    perdimos el canal para cuando SÍ importa.
  · **Corto.** Viaja en cada prompt: todo tiene tope (pares, archivos por par,
    territorio) y hay un test que falla si se pasa de 2000 chars.

Capa pura: no toca DB, red ni tmux. `briefing_para()` es la única impura.
"""

MAX_PARES = 8               # más que esto es un muro de texto, no un briefing
MAX_ARCHIVOS_POR_PAR = 3    # con tres ya sabés si te cruzás con él
MAX_TERRITORIO = 6
MAX_HERENCIA = 3            # muertos distintos
MAX_ARCHIVOS_HERENCIA = 4

MARCA = '[Enjambre]'

_ESTADO_MARCA = {
    'trabajando': '🟢 trabajando',
    'caido':      '💀 caído (su CLI se cerró)',
}
_ESTADO_DEFAULT = '⚪ libre'


def _texto(v) -> str:
    """Cualquier cosa → str limpio. El briefing NUNCA puede romper un prompt por
    un dato raro, así que todo lo que entra pasa por acá."""
    if v is None:
        return ''
    return ' '.join(str(v).split())


def _lista(v) -> list:
    return list(v) if isinstance(v, (list, tuple, set)) else []


def _dic(v) -> dict:
    return v if isinstance(v, dict) else {}


def _recorte(items, tope) -> tuple:
    """(primeros `tope`, cuántos quedaron afuera)."""
    items = list(items)
    return items[:tope], max(0, len(items) - tope)


def armar_briefing(estado) -> str:
    """Texto del briefing a partir del estado de `swarm_cli.estado()`.

    '' cuando no hay nada que decir — que es el caso más común (un agente solo)
    y tiene que costar cero. Puro y defensivo: cualquier basura devuelve str."""
    e = _dic(estado)
    pares = [p for p in _lista(e.get('pares')) if isinstance(p, dict)]
    otros = _dic(e.get('otros'))
    herencia = [h for h in _lista(e.get('herencia')) if isinstance(h, dict)]
    try:
        sin_leer = int(e.get('mensajes_sin_leer') or 0)
    except (TypeError, ValueError):
        sin_leer = 0

    # Nada que decir: ni pares, ni mensajes, ni herencia de un muerto.
    if not pares and not sin_leer and not herencia:
        return ''

    yo = _texto(e.get('yo')) or 'vos'
    out = []

    if pares:
        visibles, resto = _recorte(pares, MAX_PARES)
        cab = (f'{MARCA} Sos {yo} · {len(pares)} agente'
               f"{'s' if len(pares) != 1 else ''} más en este proyecto:")
        out.append(cab)
        for p in visibles:
            nombre = _texto(p.get('nombre')) or '?'
            tipo = _texto(p.get('tipo_ia')) or 'manual'
            marca = _ESTADO_MARCA.get(_texto(p.get('estado')), _ESTADO_DEFAULT)
            linea = f'  · {nombre} ({tipo}) {marca}'
            archivos, mas = _recorte(
                [_texto(a) for a in _lista(otros.get(nombre))],
                MAX_ARCHIVOS_POR_PAR)
            if archivos:
                linea += ' — toca ' + ', '.join(archivos)
                if mas:
                    linea += f' (+{mas})'
            out.append(linea)
        if resto:
            out.append(f'  (+{resto} más)')
    else:
        out.append(f'{MARCA} Sos {yo}.')

    # Territorio ajeno: lo ÚNICO que el guard te va a frenar de verdad.
    terr = []
    for t in _lista(e.get('territorio_ajeno')):
        if isinstance(t, (list, tuple)) and len(t) >= 2:
            terr.append((_texto(t[0]), _texto(t[1])))
    if terr:
        visibles, mas = _recorte(terr, MAX_TERRITORIO)
        cuerpo = ', '.join(f'{pat} ({nom})' for nom, pat in visibles)
        out.append(f'No borres ni renombres territorio ajeno: {cuerpo}'
                   + (f' (+{mas})' if mas else ''))

    # Herencia: trabajo sin commitear de un agente que ya no está. Es lo que
    # antes quedaba huérfano en el árbol para siempre (o se lo llevaba el
    # próximo commit ajeno sin que nadie se enterara).
    for h in _recorte(herencia, MAX_HERENCIA)[0]:
        nombre = _texto(h.get('nombre')) or 'un agente que ya no está'
        archivos, mas = _recorte([_texto(a) for a in _lista(h.get('archivos'))],
                                 MAX_ARCHIVOS_HERENCIA)
        detalle = ', '.join(archivos) + (f' (+{mas})' if mas else '')
        out.append(f'⚠ Herencia: {nombre} se fue dejando sin commitear '
                   f'{detalle} — si tocás alguno, commitealo vos.')

    if sin_leer:
        out.append(f'📬 {sin_leer} mensaje{"s" if sin_leer != 1 else ""} sin leer '
                   f'→ .jarvis/jv inbox')

    if _dic(e.get('salud_provenance')).get('muda'):
        out.append('⚠ El tracking de ediciones no registró NADA todavía: si ya '
                   'hubo escrituras, nadie está protegido (avisale al usuario).')

    if pares:
        out.append('Commiteá SOLO lo tuyo: .jarvis/jv commit -m "..." '
                   '(git add a secas se lleva el trabajo del otro).')

    return '\n'.join(out)


def firma(estado) -> str:
    """Firma estable del estado, para no repetir un briefing IDÉNTICO por el
    canal de piggyback (el de UserPromptSubmit sí se repite: cada prompt es una
    tarea nueva y el agente arranca sin ese contexto).

    Incluye lo que cambia la DECISIÓN del agente: quién está y en qué estado,
    qué territorio ajeno hay, qué herencia quedó y cuántos mensajes esperan."""
    e = _dic(estado)
    partes = []
    for p in _lista(e.get('pares')):
        if isinstance(p, dict):
            partes.append(f"{_texto(p.get('nombre'))}:{_texto(p.get('estado'))}")
    for t in _lista(e.get('territorio_ajeno')):
        if isinstance(t, (list, tuple)) and len(t) >= 2:
            partes.append(f'T{_texto(t[0])}/{_texto(t[1])}')
    for h in _lista(e.get('herencia')):
        if isinstance(h, dict):
            partes.append(f"H{_texto(h.get('nombre'))}:"
                          f"{len(_lista(h.get('archivos')))}")
    partes.append(f"M{_texto(e.get('mensajes_sin_leer')) or '0'}")
    return '|'.join(sorted(partes))


# ─── Capa impura: el estado vivo + memoria de qué ya se dijo ──────────────────

_ultima_firma: dict = {}       # tid → firma del último briefing entregado
_ultimo_ts: dict = {}          # tid → cuándo se calculó por última vez

# Freno del canal de PIGGYBACK. Ese canal cuelga de `/swarm/op`, o sea que corre
# una vez por CADA edición de CADA agente, y armar el estado no es gratis:
# `git status` + `tmux list-panes` + tres barridos del libro de provenance. Con
# un agente haciendo 50 ediciones seguidas eso es puro desperdicio, porque el
# enjambre no cambia 50 veces en un minuto. El canal bueno (UserPromptSubmit)
# NO se frena: ahí cada llamada es una tarea nueva y el agente necesita el dato.
THROTTLE_PIGGYBACK_S = 20


def briefing_para(terminal_id, solo_si_cambio=False, ahora=None) -> dict:
    """{texto, firma, nuevo}. `solo_si_cambio=True` (canal de piggyback) devuelve
    texto '' si el estado es idéntico al del último briefing entregado, y ni
    siquiera lo calcula si se calculó hace menos de THROTTLE_PIGGYBACK_S.

    Best-effort absoluto: si el estado no se puede armar, devuelve vacío. Este
    camino cuelga del prompt de un agente — jamás puede tirar."""
    import time as _time
    ahora = _time.monotonic() if ahora is None else ahora
    if solo_si_cambio:
        previo = _ultimo_ts.get(terminal_id)
        if previo is not None and (ahora - previo) < THROTTLE_PIGGYBACK_S:
            return {'texto': '', 'firma': '', 'nuevo': False}
    _ultimo_ts[terminal_id] = ahora
    try:
        from plotspace.core import swarm_cli
        estado = swarm_cli.estado(terminal_id)
    except Exception as ex:
        print(f'[briefing] no pude armar el estado de {terminal_id}: {ex}')
        return {'texto': '', 'firma': '', 'nuevo': False}
    if not isinstance(estado, dict) or estado.get('error'):
        return {'texto': '', 'firma': '', 'nuevo': False}

    f = firma(estado)
    nuevo = _ultima_firma.get(terminal_id) != f
    texto = armar_briefing(estado)
    if solo_si_cambio and not nuevo:
        return {'texto': '', 'firma': f, 'nuevo': False}
    _ultima_firma[terminal_id] = f
    return {'texto': texto, 'firma': f, 'nuevo': nuevo}


def olvidar_terminal(terminal_id):
    """La terminal murió: su memoria de briefing se va con ella (si el id se
    reusa, el agente nuevo merece el briefing completo)."""
    _ultima_firma.pop(terminal_id, None)
    _ultimo_ts.pop(terminal_id, None)


def reset():
    """Solo para tests."""
    _ultima_firma.clear()
    _ultimo_ts.clear()
