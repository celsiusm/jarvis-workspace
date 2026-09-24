"""
Test: arranque VISIBLE de las terminales de IA (camino intermedio, 2026-07-10).

El usuario quería volver a VER el shell de WSL al crear una terminal de IA
(el prompt `user@DESKTOP-...`), pero sin el choclo del comando real
(`claude --session-id <uuid>...` wrappeando). El camino intermedio:

  - El pane nace como shell de login PELADO (sin programa) → prompt visible.
  - El CLI se tipea CORTO por send-keys (`claude`, `codex`, ...), sin flags.
  - La plomería de sesión no se pierde: el SessionStart hook de claude postea
    el uuid VIVO a la DB en cada arranque (_guardar_session_uuid), así que
    --resume tras un reboot sigue apuntando al transcript correcto.

Quedan en el arranque de PROGRAMA del pane (invisible, el de siempre):
  - workflows del orquestador (comando_cli explícito con flags; además el
    engine tipea la tarea por send-keys y no puede caer en un bash),
  - reanudaciones (reconciliar/attach post-reboot: --resume manda),
  - qwen (necesita --session-id + --chat-recording en la línea; no hay hook),
  - manual/shell (no hay CLI que lanzar),
  - TERMINALES_ARRANQUE=limpio (vía de escape al comportamiento previo).
"""
import os
import sys

import pytest
import asyncio
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import plotspace.routers.terminals as term
# El motor de terminales vive ahora detrás de una interfaz: el espía de
# subprocess sigue siendo el mismo (es el módulo compartido), pero la comprobación
# de "¿ya existe la sesión?" la hace el motor, no `term`.
import plotspace.core.terminal_backend as _tb

# Estos tests verifican el argv EXACTO de tmux: desde F5·2 el default es el
# termhost, así que hay que pedir ese motor explícitamente (ver conftest).
pytestmark = pytest.mark.usefixtures('motor_tmux')



# ─── Decisión pura ────────────────────────────────────────────────────────────

def test_visible_para_clis_de_arranque_pelado():
    for t in ('claude', 'codex', 'opencode', 'antigravity', 'grok'):
        assert term._arranque_visible(t, None, False, 'shell') is True, t


def test_no_visible_qwen_manual_ni_desconocido():
    for t in ('qwen', 'manual', 'gemini', '', None):
        assert term._arranque_visible(t, None, False, 'shell') is False, t


def test_no_visible_con_comando_explicito_de_workflow():
    assert term._arranque_visible(
        'claude', 'claude --dangerously-skip-permissions', False, 'shell') is False


def test_no_visible_en_reanudacion():
    assert term._arranque_visible('claude', None, True, 'shell') is False


def test_flag_limpio_restaura_el_arranque_invisible():
    assert term._arranque_visible('claude', None, False, 'limpio') is False
    # default (sin flag) = shell visible
    assert term._arranque_visible('claude', None, False, None) is True


def test_comando_corto_por_tipo():
    assert term._comando_corto('claude') == 'claude'
    assert term._comando_corto('codex') == 'codex'
    assert term._comando_corto('antigravity') == 'agy'
    assert term._comando_corto('manual') is None


# ─── Integración con _crear_sesion_tmux (subprocess mockeado) ────────────────

def _crear_con_mocks(tipo_ia, launch_cmd, env_flag=None):
    """Corre _crear_sesion_tmux con DB/subprocess mockeados; devuelve los argv.
    El tipeo visible (hilo daemon en prod) se ejecuta INLINE y sin espera para
    que las aserciones sean deterministas."""
    env_patch = {}
    if env_flag is not None:
        env_patch['TERMINALES_ARRANQUE'] = env_flag
    inline = lambda nombre, corto: term._tipear_cli_visible(nombre, corto, max_espera=0)
    with mock.patch.object(term, "_sesion_tmux_existe", return_value=False), \
         mock.patch.object(_tb.TmuxBackend, "existe", return_value=False), \
         mock.patch.object(term.os.path, "isdir", return_value=True), \
         mock.patch.object(term, "_instalar_bindings_copy_mode"), \
         mock.patch.object(term, "_aplicar_estilo_obsidian_tmux"), \
         mock.patch.object(term, "_tipo_ia_de", return_value=tipo_ia), \
         mock.patch.object(term, "_launch_command_de_terminal", return_value=launch_cmd), \
         mock.patch.object(term, "_lanzar_tipeo_visible", side_effect=inline), \
         mock.patch.dict(term.os.environ, env_patch, clear=False), \
         mock.patch.object(term.subprocess, "run") as m:
        if env_flag is None:
            term.os.environ.pop('TERMINALES_ARRANQUE', None)
        m.return_value = mock.Mock(returncode=0, stderr="", stdout="")
        asyncio.run(term._crear_sesion_tmux(999, "/tmp/x"))
    calls = []
    for c in m.call_args_list:
        argv = c.args[0] if c.args else c.kwargs.get("args")
        if isinstance(argv, (list, tuple)):
            calls.append(list(argv))
    return calls


def test_claude_visible_shell_pelado_y_tipeo_corto():
    calls = _crear_con_mocks('claude', 'claude --session-id abc-123')
    ns = [c for c in calls if c[:2] == ['tmux', 'new-session']][0]
    # El pane nace SIN programa: ningún argumento lleva el comando del CLI
    assert not any('claude' in a for a in ns), ns
    assert not any('exec bash -l' in a for a in ns), ns
    # ...y el CLI se tipea corto, literal (-l --) + Enter aparte
    sk = [c for c in calls if c[:2] == ['tmux', 'send-keys']]
    assert ['tmux', 'send-keys', '-t', '=jarvis_999:', '-l', '--', 'claude'] in sk, sk
    assert ['tmux', 'send-keys', '-t', '=jarvis_999:', 'Enter'] in sk, sk


def test_qwen_sigue_como_programa_del_pane():
    calls = _crear_con_mocks('qwen', 'qwen --session-id abc --chat-recording')
    ns = [c for c in calls if c[:2] == ['tmux', 'new-session']][0]
    assert any('qwen --session-id abc --chat-recording' in a for a in ns), ns
    sk = [c for c in calls if c[:2] == ['tmux', 'send-keys']]
    assert not sk, sk


def test_flag_limpio_vuelve_al_programa_del_pane():
    calls = _crear_con_mocks('claude', 'claude --session-id abc', env_flag='limpio')
    ns = [c for c in calls if c[:2] == ['tmux', 'new-session']][0]
    assert any('claude --session-id abc' in a for a in ns), ns
    sk = [c for c in calls if c[:2] == ['tmux', 'send-keys']]
    assert not sk, sk


def test_sin_fila_no_se_tipea_nada():
    """Terminal sin fila en DB (launch_cmd None): shell pelado, cero send-keys."""
    calls = _crear_con_mocks('claude', None)
    sk = [c for c in calls if c[:2] == ['tmux', 'send-keys']]
    assert not sk, sk


# ─── _tipear_cli_visible: espera del prompt ───────────────────────────────────

def test_tipeo_espera_el_prompt_antes_de_mandar():
    """El send-keys sale recién cuando el pane pintó algo (la PS1): sin esto el
    kernel pre-echoaba el input ARRIBA del prompt (línea `claude` suelta)."""
    seq = [
        mock.Mock(returncode=0, stdout=''),                       # pane vacío aún
        mock.Mock(returncode=0, stdout=''),                       # sigue vacío
        mock.Mock(returncode=0, stdout='user@host:~/x$ '),        # apareció la PS1
        mock.Mock(returncode=0, stdout='', stderr=''),            # send-keys -l
        mock.Mock(returncode=0, stdout='', stderr=''),            # send-keys Enter
    ]
    with mock.patch.object(term.subprocess, "run", side_effect=seq) as m, \
         mock.patch.object(term.time, "sleep"):
        term._tipear_cli_visible(999, 'claude', max_espera=5)
    argvs = [list(c.args[0]) for c in m.call_args_list]
    assert argvs[0][:3] == ['tmux', 'capture-pane', '-t']
    assert ['tmux', 'send-keys', '-t', '=jarvis_999:', '-l', '--', 'claude'] in argvs
    assert ['tmux', 'send-keys', '-t', '=jarvis_999:', 'Enter'] in argvs
    # y el tipeo fue DESPUÉS del prompt (3 capturas primero)
    assert argvs[3][:2] == ['tmux', 'send-keys'], argvs


def test_tipeo_aborta_si_la_sesion_murio():
    seq = [mock.Mock(returncode=1, stdout='', stderr="can't find session")]
    with mock.patch.object(term.subprocess, "run", side_effect=seq) as m:
        term._tipear_cli_visible(999, 'claude', max_espera=5)
    assert len(m.call_args_list) == 1     # solo la captura; ningún send-keys
