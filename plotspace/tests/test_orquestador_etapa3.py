# plotspace/tests/test_orquestador_etapa3.py
"""Los arreglos que le faltaban al motor de workflows para ser usable.

El motor está construido entero pero en 19 días de esta DB no corrió ni una
vez. No falló: nunca se lo llamó. Igual tiene que quedar SANO para el día que
el usuario lo quiera, y tenía tres cosas rotas de raíz:

1. La tarea viajaba por `send-keys` crudo. Verificado: los saltos de línea
   llegan como LF al pty, así que un prompt largo puede fragmentarse en varios
   envíos. Ahora va como PASTE por el buffer de tmux, que además deja que tmux
   decida si envolver en bracketed paste según lo que pidió la app.
2. "El agente está listo" se detectaba matcheando el BANNER del CLI
   (`'bypass permissions on'`). Es la misma fragilidad que dejó ciego al parseo
   de panes: cambia el render y se rompe. Ahora se usa la máquina de estados de
   agent_watch, que no depende de ningún texto.
3. El Reviewer esperaba que TODOS los pasos estuvieran `done`. Con un solo
   paso bloqueado no arrancaba nunca y el workflow quedaba colgado sin cierre.
"""
from plotspace.routers.orchestrator import (
    comandos_pegar_tarea, _pasos_listos_para_arrancar, _paso_reviewer,
    listo_segun_fase,
)


# ─── 1. La tarea viaja como PASTE, no tipeada ────────────────────────────────

def test_pegar_usa_el_buffer_de_tmux():
    cmds = comandos_pegar_tarea('jarvis_7', 'hola')
    assert cmds[0][:3] == ['tmux', 'set-buffer', '-b']
    assert cmds[1][:2] == ['tmux', 'paste-buffer']


def test_pegar_pide_bracketed_paste_condicional():
    """`-p` = tmux envuelve en bracketed paste SOLO si la app lo pidió. Meter
    los escapes a mano sería peor: en una app que no los entiende se verían
    como basura."""
    assert '-p' in comandos_pegar_tarea('jarvis_7', 'hola')[1]


def test_pegar_borra_el_buffer_al_usarlo():
    """Sin `-d`, cada tarea deja un buffer colgado en el server de tmux."""
    assert '-d' in comandos_pegar_tarea('jarvis_7', 'hola')[1]


def test_pegar_manda_el_texto_ENTERO_de_una():
    """Es el punto: un prompt con saltos de línea no se puede fragmentar."""
    tarea = 'primera línea\n\nsegunda con salto\ntercera'
    cmds = comandos_pegar_tarea('jarvis_7', tarea)
    assert cmds[0][-1] == tarea, 'el texto viaja tal cual, sin partir'


def test_pegar_usa_doble_guion_antes_del_texto():
    """Sin `--`, una tarea que arranque con `-` la come tmux como flag."""
    cmds = comandos_pegar_tarea('jarvis_7', '-rf algo')
    assert cmds[0][-2] == '--'


def test_pegar_apunta_a_la_sesion_correcta():
    cmds = comandos_pegar_tarea('jarvis_42', 'x')
    assert '-t' in cmds[1] and '=jarvis_42:' in cmds[1]


def test_cada_terminal_usa_su_propio_buffer():
    """Dos agentes recibiendo tarea a la vez no se pisan el buffer."""
    a = comandos_pegar_tarea('jarvis_1', 'x')[0][3]
    b = comandos_pegar_tarea('jarvis_2', 'x')[0][3]
    assert a != b


# ─── 2. "Listo" sin depender del banner del CLI ──────────────────────────────

def test_listo_cuando_el_pane_se_asento():
    """agent_watch pasa a 'idle' cuando el CLI arrancó y quedó esperando."""
    assert listo_segun_fase({'fase': 'idle'}) is True


def test_listo_tambien_si_ya_esta_trabajando():
    """Un CLI que arranca produciendo output ya está listo para recibir."""
    assert listo_segun_fase({'fase': 'trabajando'}) is True


def test_no_listo_mientras_arranca():
    assert listo_segun_fase({'fase': 'arrancando'}) is False


def test_sin_estado_no_esta_listo():
    assert listo_segun_fase(None) is False
    assert listo_segun_fase({}) is False


# ─── 3. El Reviewer no se cuelga por un paso bloqueado ───────────────────────

def _pasos(*estados):
    p = [{'estado': e, 'agente': f'A{i}'} for i, e in enumerate(estados)]
    p.append({**_paso_reviewer('W', 'obj'), 'estado': 'pending'})
    return p


def test_reviewer_arranca_con_todos_done():
    assert _pasos_listos_para_arrancar(_pasos('done', 'done')) == [2]


def test_reviewer_arranca_AUNQUE_haya_un_paso_bloqueado():
    """Antes esperaba `done` de todos: un solo bloqueado y el workflow quedaba
    colgado para siempre, sin cierre ni review."""
    assert _pasos_listos_para_arrancar(_pasos('done', 'blocked')) == [2]


def test_reviewer_arranca_con_un_paso_en_error():
    assert _pasos_listos_para_arrancar(_pasos('done', 'error')) == [2]


def test_reviewer_NO_arranca_si_alguien_sigue_trabajando():
    assert _pasos_listos_para_arrancar(_pasos('done', 'running')) == []


def test_reviewer_NO_arranca_si_alguien_no_empezo():
    assert _pasos_listos_para_arrancar(_pasos('done', 'pending')) == [1]


def test_reviewer_solo_no_arranca():
    """Un workflow sin builders no tiene nada que revisar."""
    p = [{**_paso_reviewer('W', 'o'), 'estado': 'pending'}]
    assert _pasos_listos_para_arrancar(p) == []


def test_los_builders_sin_dependencia_arrancan_en_paralelo():
    p = [{'estado': 'pending', 'depende_de': None},
         {'estado': 'pending', 'depende_de': None}]
    assert _pasos_listos_para_arrancar(p) == [0, 1]


def test_la_dependencia_se_sigue_respetando():
    p = [{'estado': 'running', 'depende_de': None},
         {'estado': 'pending', 'depende_de': 'paso_0'}]
    assert _pasos_listos_para_arrancar(p) == []
    p[0]['estado'] = 'done'
    assert _pasos_listos_para_arrancar(p) == [1]


if __name__ == '__main__':
    import sys
    import pytest
    sys.exit(pytest.main([__file__, '-q']))
