"""Tests de la AUTO-ROTACIÓN de cuentas de CLI al pegar contra el rate-limit.

Lógica PURA de plotspace/core/agent_watch.py (nada de tmux ni asyncio):
  - detectar_limite(texto, tipo_ia): firma estricta de rate/usage limit en el pane
  - proxima_cuenta_sana(cuentas, activa_id, excluidas): selección round-robin
  - cooldown_rotacion_vencido / registrar_rotacion: anti-flap por terminal
  - cuentas_limitadas / registrar_limite_cuenta: TTL de cuenta limitada

Corre como script suelto o por pytest.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from plotspace.core.agent_watch import (
    COOLDOWN_ROTACION_S,
    LIMITE_CUENTA_TTL_S,
    _rotacion_terminal,
    _limite_cuenta,
    cooldown_rotacion_vencido,
    cuentas_limitadas,
    linea_limite,
    proxima_cuenta_sana,
    registrar_limite_cuenta,
    registrar_rotacion,
)


def detectar_limite(texto, tipo_ia=None):
    return linea_limite(texto)[0] is not None


# ─── detectar_limite: firmas que SÍ deben matchear ────────────────────────────

def test_claude_usage_limit_reached():
    assert detectar_limite('Claude usage limit reached. Your limit will reset at 3pm.', 'claude') is True


def test_claude_youve_reached_usage_limit():
    assert detectar_limite("You've reached your usage limit.", 'claude') is True


def test_claude_5_hour_limit():
    assert detectar_limite('5-hour limit reached ∙ resets 8pm', 'claude') is True
    assert detectar_limite('You have reached your 5 hour limit', 'claude') is True


def test_approaching_usage_limit():
    assert detectar_limite('Approaching usage limit · resets at 5pm', 'claude') is True


def test_generico_rate_limit_reached():
    assert detectar_limite('Rate limit reached for requests', 'codex') is True
    assert detectar_limite('rate limit exceeded', 'codex') is True


def test_generico_rate_limit_error_anthropic():
    # El error type de la API de Anthropic: rate_limit_error
    assert detectar_limite('API error: rate_limit_error', 'claude') is True


def test_generico_quota_exceeded():
    assert detectar_limite('Error: quota exceeded', 'qwen') is True
    assert detectar_limite('Resource has been exhausted (e.g. check quota).', 'qwen') is True


def test_generico_too_many_requests():
    assert detectar_limite('429 Too Many Requests', 'codex') is True


def test_try_again_at_reset_time():
    assert detectar_limite('Please try again at 14:30 UTC', 'codex') is True
    assert detectar_limite('try again in 25 minutes', 'codex') is True


# ─── detectar_limite: prosa/tarea que NO debe matchear (conservador) ──────────

def test_tarea_que_pide_rate_limiting_no_matchea():
    # Instrucción de Jarvis al agente: "implementá rate limiting" NO es un límite
    assert detectar_limite('Tu tarea: implementá rate limiting en el endpoint /api', 'claude') is False
    assert detectar_limite('agregar middleware de rate-limiting', 'codex') is False


def test_prosa_casual_no_matchea():
    assert detectar_limite('I need to limit the rate of outgoing requests here', 'claude') is False
    assert detectar_limite('the rate limit is 100 req/s per the spec', 'codex') is False


def test_try_again_later_solo_no_matchea():
    # "please try again later" es transitorio genérico (blip de red), NO un límite
    assert detectar_limite('Connection failed, please try again later', 'codex') is False


def test_vacio_no_matchea():
    assert detectar_limite('', 'claude') is False
    assert detectar_limite(None, 'claude') is False


def test_limite_viejo_fuera_de_la_cola_no_matchea():
    # Un mensaje de límite de hace rato, 30 líneas arriba: el agente ya siguió.
    pane = 'usage limit reached\n' + ('línea de output normal\n' * 30)
    assert detectar_limite(pane, 'claude') is False


def test_limite_con_ansi_matchea():
    assert detectar_limite('\x1b[31mUsage limit reached\x1b[0m', 'claude') is True


# ─── proxima_cuenta_sana: selección round-robin ───────────────────────────────

def _cuentas(*ids):
    return [{'id': i, 'tipo': 'claude', 'activa': False} for i in ids]


def test_proxima_round_robin_basico():
    assert proxima_cuenta_sana(_cuentas(1, 2, 3), 1) == 2
    assert proxima_cuenta_sana(_cuentas(1, 2, 3), 2) == 3


def test_proxima_envuelve_al_principio():
    assert proxima_cuenta_sana(_cuentas(1, 2, 3), 3) == 1


def test_proxima_salta_excluidas():
    # activa=2, la siguiente (3) está limitada → envuelve a 1
    assert proxima_cuenta_sana(_cuentas(1, 2, 3), 2, excluidas={3}) == 1


def test_proxima_unica_cuenta_es_none():
    assert proxima_cuenta_sana(_cuentas(1), 1) is None


def test_proxima_todas_excluidas_es_none():
    assert proxima_cuenta_sana(_cuentas(1, 2, 3), 1, excluidas={1, 2, 3}) is None


def test_proxima_activa_fuera_de_lista_toma_la_primera_sana():
    assert proxima_cuenta_sana(_cuentas(5, 6), 99) == 5
    assert proxima_cuenta_sana(_cuentas(5, 6), 99, excluidas={5}) == 6


def test_proxima_lista_vacia_es_none():
    assert proxima_cuenta_sana([], 1) is None


def test_proxima_excluye_la_activa_aunque_no_este_excluida():
    # Con 2 cuentas y la otra excluida, no queda candidata
    assert proxima_cuenta_sana(_cuentas(1, 2), 1, excluidas={2}) is None


# ─── cooldown de rotación por terminal (anti-flap) ────────────────────────────

def test_cooldown_sin_rotacion_previa_esta_vencido():
    _rotacion_terminal.clear()
    assert cooldown_rotacion_vencido(7, ahora=1000.0) is True


def test_cooldown_dentro_de_la_ventana_no_vencido():
    _rotacion_terminal.clear()
    registrar_rotacion(7, ts=1000.0)
    assert cooldown_rotacion_vencido(7, ahora=1000.0 + COOLDOWN_ROTACION_S - 1) is False


def test_cooldown_pasada_la_ventana_vencido():
    _rotacion_terminal.clear()
    registrar_rotacion(7, ts=1000.0)
    assert cooldown_rotacion_vencido(7, ahora=1000.0 + COOLDOWN_ROTACION_S + 1) is True


# ─── cuentas limitadas con TTL ────────────────────────────────────────────────

def test_cuenta_limitada_dentro_del_ttl():
    _limite_cuenta.clear()
    registrar_limite_cuenta(42, ts=1000.0)
    assert 42 in cuentas_limitadas(ahora=1000.0 + LIMITE_CUENTA_TTL_S - 1)


def test_cuenta_limitada_expira_pasado_el_ttl():
    _limite_cuenta.clear()
    registrar_limite_cuenta(42, ts=1000.0)
    assert 42 not in cuentas_limitadas(ahora=1000.0 + LIMITE_CUENTA_TTL_S + 1)


def test_sin_cuentas_limitadas_es_set_vacio():
    _limite_cuenta.clear()
    assert cuentas_limitadas(ahora=1000.0) == set()


# ─── Firma endurecida en dos niveles (auditoría 2026-07-02) ───────────────────
# Falso positivo real: un agente DEBUGGEANDO los 429 de la app del usuario tiene
# "429 Too Many Requests" en su pane → la rotación reescribía la credencial
# GLOBAL de claude y vetaba la cuenta sana 5 horas. Regla nueva: las firmas
# GENÉRICAS (prosa HTTP común) solo rotan si el agente NO está 'trabajando'
# (una CLI limitada de verdad se frena); las FUERTES (jerga inequívoca del
# proveedor) rotan en cualquier fase.

from plotspace.core.agent_watch import linea_limite, debe_rotar


def test_linea_limite_clasifica_fuerte():
    linea, tier = linea_limite("You've reached your usage limit. Resets at 3pm.")
    assert tier == 'fuerte' and 'usage limit' in linea


def test_linea_limite_clasifica_generica():
    linea, tier = linea_limite('GET /api/pedidos -> 429 Too Many Requests')
    assert tier == 'generica' and '429' in linea


def test_linea_limite_sin_firma():
    assert linea_limite('npm test\n42 passing\n') == (None, None)


def test_debe_rotar_generica_trabajando_NO_rota():
    # El agente corre los tests del rate-limiter del USUARIO: pane en movimiento.
    pane = 'FAIL espera 429\nAssertionError: expected "429 Too Many Requests"'
    rotar, linea, tier = debe_rotar(pane, 'trabajando')
    assert rotar is False and tier == 'generica' and linea


def test_debe_rotar_generica_quieto_SI_rota():
    pane = 'Error: 429 Too Many Requests. Please try again in 25 minutes.'
    assert debe_rotar(pane, 'idle')[0] is True
    assert debe_rotar(pane, None)[0] is True     # sin estado aún: no bloquear


def test_debe_rotar_fuerte_rota_en_cualquier_fase():
    pane = 'Claude usage limit reached. Your limit will reset at 3pm.'
    assert debe_rotar(pane, 'trabajando')[0] is True
    assert debe_rotar(pane, 'idle')[0] is True


def test_debe_rotar_rate_limit_error_es_fuerte():
    # Error type verbatim de la API de Anthropic: inequívoco aunque el pane siga vivo.
    assert debe_rotar('API Error: rate_limit_error', 'trabajando')[0] is True


def test_debe_rotar_sin_firma_no_rota():
    assert debe_rotar('todo verde, 120 tests passing', 'idle') == (False, None, None)


def test_detectar_limite_sigue_cubriendo_ambos_niveles():
    # Compat: la puerta barata del poller no cambió de semántica.
    assert detectar_limite('rate limit exceeded', 'codex') is True
    assert detectar_limite("You've reached your usage limit.", 'claude') is True


if __name__ == '__main__':
    import traceback
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith('test_') and callable(fn):
            try:
                fn()
                print('ok', nombre)
            except Exception:
                fallos += 1
                print('FAIL', nombre)
                traceback.print_exc()
    sys.exit(1 if fallos else 0)
