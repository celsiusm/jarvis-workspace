"""Tests de la lógica pura de agent_watch (plotspace/core/agent_watch.py).

Clasificador de prompts interactivos + máquina de estados + supresión.
Nada de tmux ni asyncio.
"""

from plotspace.core.agent_watch import (
    _ultimo_keyword,
    actualizar_esperando,
    edad_desde_creacion,
    en_gracia,
    estado_inicial,
    hay_agentes_ocupados,
    hay_keyword_protocolo,
    hay_pregunta,
    confirmar_fin,
    registrar_keyword,
    sembrar_estado,
    suprimido,
    terminales_trabajando,
    transicionar,
    GRACIA_ARRANQUE_S,
    POLLS_PARA_QUIETO,
    POLLS_PARA_TRABAJANDO,
)


# ─── hay_pregunta ──────────────────────────────────────────────────────────────

def test_permiso_claude_code():
    pane = (
        'Bash command\n'
        '  rm -rf node_modules\n'
        'Do you want to proceed?\n'
        '\x1b[36m❯ 1. Yes\x1b[0m\n'
        '  2. No, and tell Claude what to do differently (esc)\n'
    )
    assert hay_pregunta(pane) is True


def test_prompt_yn_generico():
    assert hay_pregunta('Overwrite existing file? [y/N] ') is True
    assert hay_pregunta('¿Continuar? (y/n)') is True


def test_menu_numerado_con_esc():
    pane = (
        'Choose an option:\n'
        '  1. Apply this change\n'
        '  2. Skip\n'
        'Esc to cancel\n'
    )
    assert hay_pregunta(pane) is True


def test_output_normal_no_es_pregunta():
    pane = (
        'Compiling…\n'
        'Build OK in 3.2s\n'
        'All 14 tests passed\n'
    )
    assert hay_pregunta(pane) is False


def test_codigo_que_menciona_yes_no_es_pregunta():
    # Una línea de código/log con la palabra "yes" suelta no debe disparar
    pane = "    config.set('autoApprove', 'yes')\nDone.\n"
    assert hay_pregunta(pane) is False


def test_pregunta_vieja_fuera_de_la_cola_no_cuenta():
    # El prompt quedó 30 líneas arriba: el agente ya siguió trabajando
    vieja = 'Do you want to proceed?\n' + ('línea de output normal\n' * 30)
    assert hay_pregunta(vieja) is False


def test_vacio():
    assert hay_pregunta('') is False
    assert hay_pregunta(None) is False


# ─── transicionar ──────────────────────────────────────────────────────────────
# Polls de 1s: armar = 4 polls seguidos cambiando (~4s); quieto = 4 polls
# sin cambios (~4s). Latencia del "terminé": 4-5s desde el último output.

def _correr(hashes, st=None):
    """Pasa una secuencia de hashes por la máquina; devuelve los eventos."""
    st, eventos = st or estado_inicial(), []
    for h in hashes:
        st, ev = transicionar(st, h)
        eventos.append(ev)
    return st, eventos


def _armada():
    """Estado de una terminal ya armada: vista estable al menos una vez
    (como toda terminal que lleva un rato viva)."""
    st, _ = _correr(['x'] * 5)
    return st


def test_boot_de_cli_no_dispara():
    # Terminal recién creada: la CLI bootea (banner/loading cambiando varios
    # polls) y se asienta en su input. Eso NO es un trabajo terminado.
    _, eventos = _correr(['a', 'b', 'c', 'd'] + ['d'] * 5)
    assert eventos == [None] * 9


def test_despues_del_boot_si_arma_y_dispara():
    # Boot + asentarse (arma la máquina) → trabajo real → quietud → evaluar
    _, eventos = _correr(['a', 'b', 'c'] + ['c'] * 4 +        # boot + settle (arma)
                         ['d', 'e', 'f', 'g'] + ['g'] * 4)    # trabajo real → evaluar
    assert eventos == [None] * 10 + ['trabajando'] + [None] * 3 + ['evaluar']


def test_comando_manual_rapido_no_dispara():
    # 1 solo poll con cambio (ls + enter: x→a) y después quieto: nunca "trabajando"
    _, eventos = _correr(['a', 'a', 'a', 'a', 'a', 'a'], st=_armada())
    assert eventos == [None] * 6


def test_tipeo_breve_no_dispara():
    # 3 polls cambiando (tipear un comando ~3s) no llega a armar "trabajando":
    # con polls de 1s el umbral es 4 cambios seguidos
    _, eventos = _correr(['a', 'b', 'c', 'c', 'c', 'c', 'c'], st=_armada())
    assert eventos == [None] * 7


def test_trabajo_sostenido_y_quietud_evalua_una_vez():
    # 4 polls cambiando (a→b→c→d→e... x→a cuenta) = trabajando; 4 quietos = evaluar
    _, eventos = _correr(['a', 'b', 'c', 'd'] + ['d'] * 4, st=_armada())
    assert eventos == [None] * 3 + ['trabajando'] + [None] * 3 + ['evaluar']


def test_tres_polls_quietos_aun_no_evalua():
    # A los 3 polls quietos todavía no suena (recién al 4to): asegura que la
    # latencia es exactamente POLLS_PARA_QUIETO
    _, eventos = _correr(['a', 'b', 'c', 'd'] + ['d'] * 3, st=_armada())
    assert eventos == [None] * 3 + ['trabajando'] + [None] * 3


def test_armarse_emite_trabajando():
    # Al pasar idle→trabajando la máquina lo avisa (el frontend apaga el aura)
    _, eventos = _correr(['a', 'b', 'c', 'd'], st=_armada())
    assert eventos == [None, None, None, 'trabajando']


def test_cada_ciclo_emite_trabajando_una_vez():
    _, eventos = _correr(['a', 'b', 'c', 'd'] + ['d'] * 4 +
                         ['e', 'f', 'g', 'h'] + ['h'] * 4, st=_armada())
    assert eventos.count('trabajando') == 2


def test_un_solo_evento_por_ciclo_de_trabajo():
    # Tras evaluar vuelve a idle: más quietud no re-dispara
    _, eventos = _correr(['a', 'b', 'c', 'd'] + ['d'] * 7, st=_armada())
    assert eventos.count('evaluar') == 1


def test_pausa_breve_no_corta_el_trabajo():
    # Hasta 3 polls quietos en el medio (el agente piensa) no disparan; al
    # retomar y quedarse quieto al final, sí
    _, eventos = _correr(['a', 'b', 'c', 'd', 'd', 'd', 'd', 'e'] + ['e'] * 4,
                         st=_armada())
    assert eventos == [None] * 3 + ['trabajando'] + [None] * 7 + ['evaluar']


def test_nuevo_ciclo_despues_de_evaluar():
    # Después de un ciclo completo puede arrancar otro
    _, eventos = _correr(['a', 'b', 'c', 'd'] + ['d'] * 4 +     # ciclo 1 → evaluar
                         ['e', 'f', 'g', 'h'] + ['h'] * 4,      # ciclo 2 → evaluar
                         st=_armada())
    assert eventos.count('evaluar') == 2


# ─── hay_keyword_protocolo ─────────────────────────────────────────────────────
# Con el poller acelerado (1s), el heurístico puede ganarle la carrera al
# monitor de keywords (5s): si la cola del pane tiene un TASK_* real, el
# heurístico calla — ese sonido le corresponde al protocolo.

def test_task_done_solo_en_linea_suprime():
    assert hay_keyword_protocolo('hice todo lo pedido\nTASK_DONE\n') is True


def test_task_done_con_adorno_suprime():
    assert hay_keyword_protocolo('listo\n✅ TASK_DONE\n') is True


def test_blocked_y_error_suprimen():
    assert hay_keyword_protocolo('no pude\nTASK_BLOCKED\n') is True
    assert hay_keyword_protocolo('explotó\nTASK_ERROR\n') is True


def test_instruccion_no_suprime():
    # "Cuando termines escribí TASK_DONE" es la instrucción de Jarvis, no
    # output del agente (mismo filtro que _linea_es_keyword en terminals.py)
    pane = 'Tu tarea: arreglar el bug. Cuando termines escribí TASK_DONE\n'
    assert hay_keyword_protocolo(pane) is False


def test_keyword_viejo_fuera_de_la_cola_no_suprime():
    # Un TASK_DONE de hace rato, 30 líneas arriba: el agente siguió trabajando
    # y este final es nuevo — el heurístico debe sonar
    pane = 'TASK_DONE\n' + ('línea de output normal\n' * 30)
    assert hay_keyword_protocolo(pane) is False


def test_keyword_con_ansi_suprime():
    assert hay_keyword_protocolo('fin\n\x1b[32mTASK_DONE\x1b[0m\n') is True


def test_keyword_vacio():
    assert hay_keyword_protocolo('') is False
    assert hay_keyword_protocolo(None) is False


# ─── gracia de arranque ────────────────────────────────────────────────────────
# El boot de las CLIs es multifase (banner → quieto → ráfaga final), así que
# una sola observación de estabilidad no alcanza: mientras la terminal es
# "joven" para el poller, no se emite nada aunque la máquina dispare evaluar.

def test_terminal_recien_nacida_esta_en_gracia():
    st = estado_inicial(ts=1000.0)
    assert en_gracia(st, ahora=1030.0) is True     # 30s < GRACIA_ARRANQUE_S


def test_pasada_la_gracia_emite():
    st = estado_inicial(ts=1000.0)
    assert en_gracia(st, ahora=1070.0) is False    # 70s > GRACIA_ARRANQUE_S


def test_gracia_sobrevive_las_transiciones():
    st = estado_inicial(ts=1000.0)
    st, _ = _correr(['a', 'b', 'c', 'c', 'c'], st=st)
    assert en_gracia(st, ahora=1030.0) is True


def test_estado_sin_nacimiento_no_tiene_gracia():
    # estado_inicial() sin ts (tests de la máquina pura): sin gracia
    assert en_gracia(estado_inicial(), ahora=1000.0) is False


# ─── esperando + hay_agentes_ocupados (gate del banner "Actualizar ahora") ────
# El banner del updater solo puede salir cuando los agentes terminaron POR
# COMPLETO: ni trabajando, ni frenados esperando una respuesta del usuario.

_PANE_PREGUNTA = (
    'Do you want to proceed?\n'
    '❯ 1. Yes\n'
    '  2. No, and tell Claude what to do differently (esc)\n'
)
_PANE_NORMAL = 'Build OK in 3.2s\nAll 14 tests passed\n'


def test_evaluar_con_pregunta_marca_esperando():
    st = actualizar_esperando(estado_inicial(), 'evaluar', _PANE_PREGUNTA)
    assert st['esperando'] is True


def test_evaluar_sin_pregunta_no_marca_esperando():
    # Terminó de verdad (sin prompt a la vista): no queda ocupado
    st = actualizar_esperando(estado_inicial(), 'evaluar', _PANE_NORMAL)
    assert st['esperando'] is False


def test_esperando_se_limpia_al_volver_a_trabajar():
    st = actualizar_esperando(estado_inicial(), 'evaluar', _PANE_PREGUNTA)
    st = actualizar_esperando(st, 'trabajando', _PANE_PREGUNTA)
    assert st['esperando'] is False


def test_esperando_se_limpia_cuando_la_pregunta_desaparece():
    # El usuario respondió y el agente terminó sin llegar a armar 'trabajando'
    # (respuesta corta): el prompt ya no está en la cola → libre. Sin esto el
    # flag quedaba clavado en True y el banner no salía nunca.
    st = actualizar_esperando(estado_inicial(), 'evaluar', _PANE_PREGUNTA)
    st = actualizar_esperando(st, None, _PANE_NORMAL)
    assert st['esperando'] is False


def test_esperando_persiste_mientras_la_pregunta_siga():
    st = actualizar_esperando(estado_inicial(), 'evaluar', _PANE_PREGUNTA)
    st = actualizar_esperando(st, None, _PANE_PREGUNTA)
    assert st['esperando'] is True


def test_ocupado_con_terminal_trabajando():
    assert hay_agentes_ocupados({1: {'fase': 'trabajando', 'esperando': False}}) is True


def test_ocupado_con_terminal_esperando_respuesta():
    # Frenado en un y/n: la tarea está a medias → cuenta como ocupado
    assert hay_agentes_ocupados({1: {'fase': 'idle', 'esperando': True}}) is True


def test_no_ocupado_todo_quieto():
    estados = {
        1: {'fase': 'idle', 'esperando': False},
        2: {'fase': 'arrancando', 'esperando': False},
        3: {'fase': 'idle'},                       # estado viejo sin el flag
    }
    assert hay_agentes_ocupados(estados) is False


def test_no_ocupado_sin_terminales():
    assert hay_agentes_ocupados({}) is False


# ─── supresión por keyword ─────────────────────────────────────────────────────

def test_suprimido_dentro_de_la_ventana():
    _ultimo_keyword.clear()
    registrar_keyword(7, ts=1000.0)
    assert suprimido(7, ahora=1005.0) is True      # 5s < SUPRESION_S


def test_no_suprimido_pasada_la_ventana():
    _ultimo_keyword.clear()
    registrar_keyword(7, ts=1000.0)
    assert suprimido(7, ahora=1011.0) is False     # 11s > SUPRESION_S


def test_terminal_sin_keyword_no_suprime():
    _ultimo_keyword.clear()
    assert suprimido(99, ahora=1000.0) is False


# ─── hay_agentes_ocupados con scope por terminales (gate del update por proyecto)

def test_ocupados_global_vs_scope():
    estados = {
        17: {'fase': 'trabajando'},   # terminal del proyecto Jarvis
        48: {'fase': 'idle'},          # terminal de otro proyecto
    }
    assert hay_agentes_ocupados(estados) is True               # global: alguno trabaja
    assert hay_agentes_ocupados(estados, terminal_ids={17}) is True   # Jarvis trabaja
    assert hay_agentes_ocupados(estados, terminal_ids={48}) is False  # otro proyecto no cuenta


def test_ocupados_otro_proyecto_no_bloquea_jarvis():
    # Jarvis idle, otro proyecto trabajando/esperando → scopeado a Jarvis = libre.
    estados = {10: {'fase': 'idle'}, 99: {'esperando': True}}
    assert hay_agentes_ocupados(estados, terminal_ids={10}) is False
    assert hay_agentes_ocupados(estados, terminal_ids={99}) is True
    assert hay_agentes_ocupados(estados, terminal_ids=set()) is False


# ─── Reset del estado tras un restart del server (reload por actualización) ────
# Causa raíz: al reiniciarse el server, _estados queda VACÍO. Una terminal que ya
# venía trabajando se (re)crea en 'arrancando', cuya ÚNICA salida es estabilidad
# (POLLS_PARA_QUIETO polls sin cambios) — que un pane activo nunca da → queda
# atrapada en 'arrancando' y NUNCA llega a 'trabajando' → terminales_trabajando()
# la deja afuera y la card no se enciende tras el reload.

def test_arrancando_arma_si_trabaja_sin_parar_desde_el_nacimiento():
    # ANTES 'arrancando' solo salía por estabilidad (POLLS_PARA_QUIETO quietos):
    # un pane que cambia SIEMPRE (agente que arranca trabajando ya, o el server
    # reinició mientras generaba) quedaba atrapado y nunca emitía 'trabajando'.
    # AHORA, si el churn se sostiene POLLS_PARA_TRABAJANDO polls, arma igual.
    st, eventos = _correr([str(i) for i in range(POLLS_PARA_TRABAJANDO + 3)])
    assert 'trabajando' in eventos
    assert st['fase'] == 'trabajando'


def test_boot_corto_todavia_no_arma_como_trabajo():
    # Un boot que churnea POCO (<POLLS_PARA_TRABAJANDO cambios) y se asienta NO
    # cuenta como trabajo — sigue yendo a 'idle' como antes (sin falso positivo).
    _, eventos = _correr(['a', 'b', 'c'] + ['c'] * 5)   # 2 cambios reales, luego estable
    assert 'trabajando' not in eventos


def test_sembrar_activo_es_trabajando():
    # El fix: sembrar_estado NO pasa por 'arrancando'. Pane activo → 'trabajando'
    # de una, así terminales_trabajando() lo incluye apenas arranca el server.
    st = sembrar_estado(activo=True, hash_actual=123, edad_seg=300, ahora=1000.0)
    assert st['fase'] == 'trabajando'
    assert terminales_trabajando({7: st}) == [7]


def test_sembrar_quieto_es_idle_no_arrancando():
    # Pane quieto → 'idle' (NO 'arrancando'): si después retoma, arma normal y
    # NO cae en la trampa. Nunca sembramos 'arrancando' para terminales vivas.
    st = sembrar_estado(activo=False, hash_actual=1, edad_seg=300, ahora=1000.0)
    assert st['fase'] == 'idle'


def test_sembrado_idle_arma_normal_no_queda_atrapado():
    # Un idle sembrado que retoma trabajo llega a 'trabajando' en
    # POLLS_PARA_TRABAJANDO polls cambiando (a diferencia de 'arrancando').
    st = sembrar_estado(activo=False, hash_actual='base', edad_seg=300, ahora=1000.0)
    st, eventos = _correr([f'x{i}' for i in range(POLLS_PARA_TRABAJANDO)], st=st)
    assert eventos[-1] == 'trabajando'


def test_sembrar_reconstruye_gracia_de_la_edad():
    # Terminal vieja (edad > gracia) → nacido lejos en el pasado → SIN gracia
    # (para que un 'terminó' real post-restart no se calle 60s).
    vieja = sembrar_estado(activo=True, hash_actual=1, edad_seg=GRACIA_ARRANQUE_S + 200, ahora=10_000.0)
    assert not en_gracia(vieja, ahora=10_000.0)
    # Terminal recién creada (edad < gracia) → conserva la gracia residual (podía
    # estar booteando cuando reinició el server → no disparar 'terminó' falso).
    joven = sembrar_estado(activo=False, hash_actual=1, edad_seg=5, ahora=10_000.0)
    assert en_gracia(joven, ahora=10_000.0)


def test_edad_desde_creacion_parsea_iso_y_acota():
    # fecha_creacion ISO de la DB → segundos de antigüedad (>=0), robusto a None.
    assert edad_desde_creacion('2026-07-06T10:00:00', ahora_wall=_wall('2026-07-06T10:05:00')) == 300
    assert edad_desde_creacion(None, ahora_wall=0.0) == 0.0
    assert edad_desde_creacion('basura', ahora_wall=0.0) == 0.0


def _wall(iso):
    from datetime import datetime
    return datetime.fromisoformat(iso).timestamp()


# ─── reconciliar_al_boot (integración con mocks; siembra _estados) ────────────

def test_reconciliar_al_boot_siembra_trabajando_y_idle(monkeypatch):
    import asyncio
    from plotspace.core import agent_watch as aw
    from plotspace.core import pane_capture

    filas = [
        {'tid': 1, 'tnombre': 'A', 'pid': 9, 'tipo_ia': 'claude', 'creada': '2020-01-01T00:00:00'},
        {'tid': 2, 'tnombre': 'B', 'pid': 9, 'tipo_ia': 'claude', 'creada': '2020-01-01T00:00:00'},
    ]
    llamadas = {1: 0, 2: 0}
    async def fake_capturar(tid, ttl=None):
        llamadas[tid] += 1
        if tid == 1:                       # tid 1: cambia entre cap1 y cap2 → trabajando
            return 'spinner frame ' + str(llamadas[tid])
        return 'prompt quieto'             # tid 2: idéntico en ambas → idle
    emitidos = []
    async def fake_broadcast(pid, data):
        emitidos.append((pid, data['type'], data.get('terminal_id')))
    async def fast_sleep(_s):              # no esperar 1.3s reales en el test
        return None

    monkeypatch.setattr(aw, '_rows_activas', lambda: filas)
    monkeypatch.setattr(pane_capture, 'capturar', fake_capturar)
    monkeypatch.setattr(aw.broadcaster, 'broadcast', fake_broadcast)
    monkeypatch.setattr(aw.asyncio, 'sleep', fast_sleep)
    aw._estados.clear()

    asyncio.run(aw.reconciliar_al_boot())

    assert aw._estados[1]['fase'] == 'trabajando'
    assert aw._estados[2]['fase'] == 'idle'          # NO 'arrancando' (sin trampa)
    assert terminales_trabajando(aw._estados) == [1]
    assert ('claude' or True) and (9, 'agente_trabajando', 1) in emitidos
    assert not any(t == 2 for _, _, t in emitidos)   # el idle no emite
    aw._estados.clear()


# ─── confirmar_fin: el 'terminé' se CONFIRMA antes de sonar ────────────────────
# Un turno que termina y el agente retoma poco después (commitea una parte y
# sigue) NO es "terminó de verdad". Antes de sonar se confirma que el pane siguió
# quieto; si cambia, se cancela.

def test_confirmar_fin_espera_dentro_de_la_ventana():
    assert confirmar_fin((100.0, 42), hash_actual=42, ahora=105.0, confirmacion_s=12) == 'esperar'


def test_confirmar_fin_emite_si_siguio_igual_lo_suficiente():
    assert confirmar_fin((100.0, 42), hash_actual=42, ahora=112.0, confirmacion_s=12) == 'emitir'


def test_confirmar_fin_cancela_si_el_pane_cambio():
    # El agente retomó (hash del pane distinto) → NO terminó, aunque haya pasado
    # el tiempo. Cancelar gana sobre emitir.
    assert confirmar_fin((100.0, 42), hash_actual=99, ahora=200.0, confirmacion_s=12) == 'cancelar'


def test_confirmar_fin_usa_el_default_del_modulo():
    from plotspace.core.agent_watch import CONFIRMACION_FIN_S
    assert confirmar_fin((0.0, 1), hash_actual=1, ahora=CONFIRMACION_FIN_S) == 'emitir'
    assert confirmar_fin((0.0, 1), hash_actual=1, ahora=CONFIRMACION_FIN_S - 0.5) == 'esperar'


# ─── _ciclo: cableado del debounce (mockeando confirmar_fin, sin tocar reloj) ──

def _seed_por_confirmar(aw, tid=1, pane='q'):
    """Deja _estados[tid] a UN poll de la quietud (quietos = umbral-1), viejo
    (sin gracia), para que el próximo poll con el MISMO pane dispare 'evaluar'."""
    import time as _t
    aw._estados.clear(); aw._fin_pendiente.clear(); aw._ultimo_keyword.clear()
    aw._estados[tid] = {'fase': 'trabajando', 'hash': hash(pane), 'cambios': 0,
                        'quietos': POLLS_PARA_QUIETO - 1, 'nacido': _t.monotonic() - 999,
                        'esperando': False, 'fase_desde': _t.monotonic() - 999}


def _mock_ciclo(monkeypatch, aw, pane, emitidos, confirmar_ret=None):
    from plotspace.core import pane_capture
    async def cap(tid, ttl=None): return pane
    async def broadcast(pid, data): emitidos.append(data['type'])
    async def norotar(row, texto): return None
    monkeypatch.setattr(aw, '_rows_activas',
                        lambda: [{'tid': 1, 'tnombre': 'A', 'pid': 9, 'tipo_ia': 'claude', 'creada': None}])
    monkeypatch.setattr(pane_capture, 'capturar', cap)
    monkeypatch.setattr(aw.broadcaster, 'broadcast', broadcast)
    monkeypatch.setattr(aw, '_quizas_rotar', norotar)
    if confirmar_ret is not None:
        monkeypatch.setattr(aw, 'confirmar_fin', lambda *a, **k: confirmar_ret)


def test_ciclo_quietud_no_suena_al_toque_queda_pendiente(monkeypatch):
    import asyncio
    from plotspace.core import agent_watch as aw
    _seed_por_confirmar(aw)
    emitidos = []
    _mock_ciclo(monkeypatch, aw, 'q', emitidos)     # pane quieto → evaluar
    asyncio.run(aw._ciclo())
    assert 'agente_termino' not in emitidos          # NO suena todavía
    assert 1 in aw._fin_pendiente                    # quedó a confirmar
    aw._estados.clear(); aw._fin_pendiente.clear()


def test_ciclo_confirma_y_suena_si_sigue_quieto(monkeypatch):
    import asyncio
    from plotspace.core import agent_watch as aw
    _seed_por_confirmar(aw)
    emitidos = []
    _mock_ciclo(monkeypatch, aw, 'q', emitidos)      # poll 1: deja pendiente
    asyncio.run(aw._ciclo())
    _mock_ciclo(monkeypatch, aw, 'q', emitidos, confirmar_ret='emitir')  # poll 2: confirma
    asyncio.run(aw._ciclo())
    assert emitidos.count('agente_termino') == 1     # sonó UNA vez, al confirmar
    assert 1 not in aw._fin_pendiente
    aw._estados.clear(); aw._fin_pendiente.clear()


def test_ciclo_cancela_el_fin_si_el_agente_retoma(monkeypatch):
    import asyncio
    from plotspace.core import agent_watch as aw
    _seed_por_confirmar(aw)
    emitidos = []
    _mock_ciclo(monkeypatch, aw, 'q', emitidos)      # poll 1: deja pendiente
    asyncio.run(aw._ciclo())
    _mock_ciclo(monkeypatch, aw, 'q', emitidos, confirmar_ret='cancelar')  # el pane cambió
    asyncio.run(aw._ciclo())
    assert 'agente_termino' not in emitidos          # NUNCA sonó (retomó)
    assert 1 not in aw._fin_pendiente
    aw._estados.clear(); aw._fin_pendiente.clear()


def test_ciclo_espera_visible_suena_inmediato_sin_debounce(monkeypatch):
    import asyncio
    from plotspace.core import agent_watch as aw
    _seed_por_confirmar(aw, pane='Do you want to proceed? (y/n)')
    emitidos = []
    _mock_ciclo(monkeypatch, aw, 'Do you want to proceed? (y/n)', emitidos)
    asyncio.run(aw._ciclo())
    assert 'agente_espera' in emitidos               # inmediato (prompt a la vista)
    assert 1 not in aw._fin_pendiente                # espera NO se debouncing
    aw._estados.clear(); aw._fin_pendiente.clear()


def test_terminales_trabajando_incluye_las_que_confirman_fin():
    # El brillo y la Live siguen mostrando 'trabajando' mientras el fin se
    # confirma (pendiente) → brillo + Live + sonido se apagan/suenan JUNTOS.
    estados = {1: {'fase': 'trabajando'}, 2: {'fase': 'idle'}, 3: {'fase': 'idle'}}
    pendientes = {3: (100.0, 42)}          # tid 3: quieto pero confirmando el fin
    assert set(terminales_trabajando(estados, pendientes)) == {1, 3}
    # sin pendientes, solo la que está en fase trabajando
    assert terminales_trabajando(estados, {}) == [1]


def test_terminales_trabajando_incluye_las_que_rearman_desde_idle():
    # Idle con cambios>0 = volvió a producir output (retomó): el brillo se
    # enciende YA, sin esperar los 4 polls de re-armado → no parpadea.
    estados = {1: {'fase': 'idle', 'cambios': 2}, 2: {'fase': 'idle', 'cambios': 0}}
    assert terminales_trabajando(estados, {}) == [1]


# ─── Stop del hook: el sonido sale al instante y el heurístico no lo repite ───

def test_stop_del_hook_suprime_el_heuristico_por_40s():
    from plotspace.core import agent_watch as aw
    aw._ultimo_hook.clear(); aw._fin_pendiente.clear()
    aw._fin_pendiente[7] = (1.0, 123)               # un 'terminé' por confirmar
    aw.registrar_fin_hook(7, ts=100.0)
    assert 7 not in aw._fin_pendiente               # se cancela el pendiente
    assert aw.suprimido(7, ahora=100.0 + 20) is True   # cubre los ~16 s del heurístico
    assert aw.suprimido(7, ahora=100.0 + aw.SUPRESION_HOOK_S + 1) is False
    assert aw.suprimido(8, ahora=100.0) is False    # solo esa terminal
    aw._ultimo_hook.clear()
