# plotspace/tests/test_orquestador_etapa3.py
"""Entrega de tareas a los agentes (spawn_terminal con tarea / enviar_prompt).

1. La tarea viajaba por `send-keys` crudo. Verificado: los saltos de línea
   llegan como LF al pty, así que un prompt largo puede fragmentarse en varios
   envíos. Ahora va como PASTE por el buffer de tmux, que además deja que tmux
   decida si envolver en bracketed paste según lo que pidió la app.
2. "El agente está listo" se detectaba matcheando el BANNER del CLI
   (`'bypass permissions on'`). Es la misma fragilidad que dejó ciego al parseo
   de panes: cambia el render y se rompe. Ahora se usa la máquina de estados de
   agent_watch, que no depende de ningún texto.
"""
from plotspace.routers.orchestrator import comandos_pegar_tarea, listo_segun_fase


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
