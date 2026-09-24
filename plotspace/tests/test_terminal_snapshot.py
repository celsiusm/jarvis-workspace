"""
Test: snapshot del pane visible de tmux para diagnóstico de GARBLE.

El garble del proyecto es render-side: tmux dibuja bien pero xterm muestra
basura. Para cazarlo hace falta la pantalla que tmux DIBUJA tal cual, fila por
fila — `capture-pane -p` SIN `-J` (no juntar líneas wrapeadas) y SIN `-S` (solo
lo visible, no el scrollback). El endpoint /history existente usa -J + scrollback
y no sirve para comparar grid contra grid.
"""
import os
import sys

import pytest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import plotspace.routers.terminals as term

# Estos tests verifican el argv EXACTO de tmux: desde F5·2 el default es el
# termhost, así que hay que pedir ese motor explícitamente (ver conftest).
pytestmark = pytest.mark.usefixtures('motor_tmux')



def test_snapshot_pane_captura_visible_sin_join():
    salida = "linea uno\nlinea dos   \n\n"
    with mock.patch.object(term.subprocess, 'run') as m:
        m.return_value = mock.Mock(returncode=0, stdout=salida, stderr='')
        lineas = term._snapshot_pane(5)
    # fila por fila, preservando la fila vacía del medio (es parte del grid)
    assert lineas == ['linea uno', 'linea dos   ', '']
    argv = m.call_args.args[0]
    assert argv[:2] == ['tmux', 'capture-pane']
    assert '-t' in argv and '=jarvis_5:' in argv
    assert '-p' in argv          # plano (texto)
    assert '-J' not in argv      # NO join: el grid se compara fila a fila
    assert '-S' not in argv      # solo la pantalla visible, no el scrollback


def test_snapshot_pane_tmux_caido_devuelve_vacio():
    with mock.patch.object(term.subprocess, 'run') as m:
        m.return_value = mock.Mock(returncode=1, stdout='', stderr='no server')
        assert term._snapshot_pane(5) == []
