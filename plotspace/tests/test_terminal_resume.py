"""Resume de terminales tras corte de luz / reboot.

Cuando se corta la luz muere el server de tmux y TODOS los procesos de los
agentes: al volver, reconciliar recrea un shell pelado. Para que claude vuelva
CON su contexto, se lo lanza atado a un id determinista: `--session-id <uuid>`
en frío y `--resume <uuid>` si su transcript (`<uuid>.jsonl`) ya está en disco.
El borrado explícito (✕ → activa=0) NUNCA se reanuda: reconciliar solo resucita
activa=1. Ver [[persistencia-resume-terminales]]."""
import asyncio
import os
from unittest import mock

from plotspace.routers import terminals as term
# El "¿ya existe la sesión?" lo resuelve el motor (core/terminal_backend), no term.
import plotspace.core.terminal_backend as _tb

# Estos tests verifican el argv EXACTO de tmux: desde F5·2 el default es el
# termhost, así que hay que pedir ese motor explícitamente (ver conftest).
import pytest

pytestmark = pytest.mark.usefixtures('motor_tmux')



# ─── _comando_lanzamiento (PURO) ──────────────────────────────────────────────

def test_grok_arranca_fresco_siempre():
    # Grok Build no tiene resume documentado: en frío y en reanudación va pelado.
    assert term._comando_lanzamiento('grok', None, jsonl_existe=False) == 'grok'
    assert term._comando_lanzamiento('grok', None, jsonl_existe=False, es_reanudacion=True) == 'grok'


def test_claude_frio_usa_session_id():
    """Primer arranque (sin transcript): fija el id → el <uuid>.jsonl queda con
    nombre determinista y por ende resumible después. SIN --permission-mode auto
    (lo pone el settings.json del usuario) → claude sale pelado + el id oculto."""
    cmd = term._comando_lanzamiento('claude', 'abc-123', jsonl_existe=False)
    assert cmd == 'claude --session-id abc-123'


def test_claude_con_transcript_usa_resume():
    """Tras un reboot el transcript sigue en disco → se reanuda con TODO el
    contexto en vez de arrancar una conversación nueva."""
    cmd = term._comando_lanzamiento('claude', 'abc-123', jsonl_existe=True)
    assert cmd == 'claude --resume abc-123'


def test_claude_sin_uuid_cae_a_plano():
    """Terminales creadas antes de la feature (session_uuid NULL): claude pelado,
    sin resume (su claude viejo corre con un id aleatorio que no sabemos)."""
    cmd = term._comando_lanzamiento('claude', None, jsonl_existe=False)
    assert cmd == 'claude'


def test_qwen_id_determinista_con_chat_recording():
    """qwen también fija id (`--session-id`), pero NECESITA --chat-recording para
    guardar (sin él, --resume no anda). Al reanudar: `--resume <id> --chat-recording`."""
    assert term._comando_lanzamiento('qwen', 'q-1', False, es_reanudacion=False) == \
        'qwen --session-id q-1 --chat-recording'
    assert term._comando_lanzamiento('qwen', 'q-1', False, es_reanudacion=True) == \
        'qwen --resume q-1 --chat-recording'


def test_qwen_sin_uuid_usa_continue():
    """qwen legacy (sin uuid): guarda igual (--chat-recording) y al reanudar toma
    la más reciente del proyecto (--continue)."""
    assert term._comando_lanzamiento('qwen', None, False) == 'qwen --chat-recording'
    assert term._comando_lanzamiento('qwen', None, False, es_reanudacion=True) == \
        'qwen --continue --chat-recording'


def test_codex_opencode_agy_reanudan_la_mas_reciente():
    """codex/opencode/agy no fijan id → en frío arrancan pelados y al reanudar
    toman la sesión MÁS RECIENTE."""
    assert term._comando_lanzamiento('codex', None, False) == 'codex'
    assert term._comando_lanzamiento('codex', None, False, es_reanudacion=True) == 'codex resume --last'
    assert term._comando_lanzamiento('opencode', None, False) == 'opencode'
    assert term._comando_lanzamiento('opencode', None, False, es_reanudacion=True) == 'opencode --continue'
    assert term._comando_lanzamiento('antigravity', None, False) == 'agy'
    assert term._comando_lanzamiento('antigravity', None, False, es_reanudacion=True) == 'agy --continue'


def test_session_uuid_para_claude_y_qwen():
    """Se genera uuid determinista SOLO para los CLIs que dejan fijar el id."""
    assert term._session_uuid_para('claude') is not None
    assert term._session_uuid_para('qwen') is not None
    assert term._session_uuid_para('codex') is None
    assert term._session_uuid_para('opencode') is None
    assert term._session_uuid_para('manual') is None


def test_manual_no_autolanza():
    assert term._comando_lanzamiento('manual', None, False) is None
    assert term._comando_lanzamiento(None, None, False) is None


# ─── _transcript_claude_existe (IMPURO: glob en disco) ────────────────────────

def test_transcript_existe_detecta_por_glob(tmp_path, monkeypatch):
    """El uuid es único global → un glob por nombre esquiva la codificación
    exacta del cwd (/ y . → -) que hace claude para la carpeta del proyecto."""
    home = tmp_path
    proj = home / '.claude' / 'projects' / '-home-user-jarvis'
    proj.mkdir(parents=True)
    (proj / 'aaaa-bbbb.jsonl').write_text('{}')
    monkeypatch.setenv('HOME', str(home))

    assert term._transcript_claude_existe('aaaa-bbbb') is True
    assert term._transcript_claude_existe('no-existe') is False
    assert term._transcript_claude_existe(None) is False


# ─── launch-at-creation: el CLI arranca como PROGRAMA del pane (SIN eco) ──────

def _argv_new_session(term_id, comando_cli=None, launch_auto=None):
    """Corre _crear_sesion_tmux con todo mockeado y devuelve el argv del
    new-session capturado."""
    with mock.patch.object(term, "_sesion_tmux_existe", return_value=False), \
         mock.patch.object(_tb.TmuxBackend, "existe", return_value=False), \
         mock.patch.object(term.os.path, "isdir", return_value=True), \
         mock.patch.object(term, "_instalar_bindings_copy_mode"), \
         mock.patch.object(term, "_aplicar_estilo_obsidian_tmux"), \
         mock.patch.object(term, "_launch_command_de_terminal", return_value=launch_auto), \
         mock.patch.object(term, "_nvm_bin_dir", return_value=None), \
         mock.patch.object(term.subprocess, "run") as m:
        m.return_value = mock.Mock(returncode=0, stderr="", stdout="")
        asyncio.run(term._crear_sesion_tmux(term_id, "/tmp/x", comando_cli=comando_cli))
    for c in m.call_args_list:
        argv = c.args[0] if c.args else None
        if isinstance(argv, (list, tuple)) and list(argv[:2]) == ["tmux", "new-session"]:
            return list(argv)
    raise AssertionError("no hubo new-session")


def test_cli_arranca_como_programa_del_pane_sin_eco():
    """El comando (con el uuid largo) va como último arg del new-session → corre
    como programa del pane, NO se tipea → cero eco en la terminal."""
    cmd = "claude --session-id abc-123"
    ns = _argv_new_session(999, launch_auto=cmd)
    assert ns[-1] == f"{cmd}; exec bash -l", f"el CLI no se lanzó como programa del pane. Argv: {ns}"


def test_comando_explicito_gana_sobre_autocomputado():
    """comando_cli explícito (agente de Jarvis: --dangerously-skip-permissions) tiene
    prioridad sobre el que se computa de la fila."""
    explicito = "claude --session-id z9 --dangerously-skip-permissions"
    ns = _argv_new_session(999, comando_cli=explicito,
                           launch_auto="claude")
    assert ns[-1] == f"{explicito}; exec bash -l", f"el explícito no ganó. Argv: {ns}"


def test_terminal_sin_cli_queda_shell_pelado():
    """Terminal manual/shell (sin CLI): new-session sin comando extra — shell
    pelado, comportamiento de siempre (la Command Room le tipea después)."""
    ns = _argv_new_session(999, launch_auto=None)
    assert not any("exec bash" in str(a) for a in ns), f"no debía lanzar CLI. Argv: {ns}"


# ─── Persistencia de SHELLS: snapshot del scrollback ──────────────────────────

def test_restore_shell_sin_snapshot_es_none(tmp_path):
    """Sin snapshot guardado → None (el shell arranca pelado, sin restaurar nada)."""
    from plotspace.core import terminal_snapshot as ts
    assert ts.comando_restore_shell(str(tmp_path), 999) is None


def test_restore_shell_con_snapshot_reimprime(tmp_path):
    """Con snapshot → comando que lo re-imprime (cat) + una marca de 'restaurada'.
    _crear_sesion_tmux le agrega '; exec bash -l' → queda un shell usable."""
    from plotspace.core import terminal_snapshot as ts
    snap = ts.ruta_snapshot(str(tmp_path), 999)
    with open(snap, 'w') as f:
        f.write('COMANDO_VIEJO\n')
    cmd = ts.comando_restore_shell(str(tmp_path), 999)
    assert cmd is not None
    assert 'cat ' in cmd and snap in cmd and 'restaurada' in cmd


def test_snapshot_no_pisa_con_pane_vacio(tmp_path, monkeypatch):
    """Si el capture sale vacío (pane en blanco / tmux trabado), NO pisa un
    snapshot bueno con nada — así no perdés el historial por un capture fallido."""
    from plotspace.core import terminal_snapshot as ts
    snap = ts.ruta_snapshot(str(tmp_path), 5)
    with open(snap, 'w') as f:
        f.write('HISTORIAL_BUENO\n')
    monkeypatch.setattr(ts.subprocess, 'run',
                        lambda *a, **k: type('R', (), {'stdout': '  \n \n'})())
    ts._snapshot_uno(5, str(tmp_path))
    with open(snap) as f:
        assert 'HISTORIAL_BUENO' in f.read()


def test_shell_solo_restaura_al_reanudar():
    """En la creación normal (es_reanudacion=False) un shell arranca pelado; el
    restore del snapshot es SOLO al reanudar tras un reboot."""
    # manual sin reanudación → None (shell pelado)
    assert term._comando_lanzamiento('manual', None, False, es_reanudacion=False) is None
    assert term._comando_lanzamiento('manual', None, False, es_reanudacion=True) is None
    # (el restore del snapshot vive en _launch_command_de_terminal, no en el puro)


# ─── Captura del session-id vivo (SessionStart hook) ─────────────────────────
# claude ROTA su transcript (nuevo <uuid>.jsonl) cada vez que la conversación se
# compacta/continúa. Si la DB guarda el uuid INICIAL, al reanudar --resume trae
# contexto viejo/parcial. El hook actualiza terminals.session_uuid al uuid VIVO
# cada arranque, así --resume siempre apunta al transcript actual (es lo que
# permite mostrar la conversación completa al reabrir).

def test_guardar_session_uuid_actualiza_la_fila():
    from plotspace.tests._harness import fresh_db
    from plotspace.core.database import get_db
    fresh_db()
    conn = get_db()
    conn.execute("INSERT INTO projects (id, nombre, ruta, fecha_creacion, ultimo_acceso)"
                 " VALUES (1, 'p', '/tmp', '2026-01-01', '2026-01-01')")
    conn.execute(
        "INSERT INTO terminals (id, project_id, nombre, tipo_ia, activa, session_uuid,"
        " fecha_creacion) VALUES (7, 1, 't', 'claude', 1, 'viejo', '2026-01-01')")
    conn.commit(); conn.close()
    nuevo = 'a1507686-8cd1-4d07-8e82-12c1b8cbd903'
    assert term._guardar_session_uuid(7, nuevo) is True
    conn = get_db()
    got = conn.execute("SELECT session_uuid FROM terminals WHERE id=7").fetchone()[0]
    conn.close()
    assert got == nuevo


def test_guardar_session_uuid_rechaza_uuid_invalido():
    from plotspace.tests._harness import fresh_db
    fresh_db()
    # no es un uuid → no toca la DB (defensa: el hook manda lo que sea)
    assert term._guardar_session_uuid(7, 'no-es-uuid') is False
    assert term._guardar_session_uuid(7, '') is False
    assert term._guardar_session_uuid(7, 'a1507686') is False   # incompleto


def test_asegurar_session_hook_idempotente(tmp_path):
    import json as _json
    settings = tmp_path / 'settings.json'
    settings.write_text(_json.dumps({'model': 'x', 'permissions': {}}))
    cmd = 'python3 /home/user/jarvis/scripts/jarvis_claude_hook.py'

    # 1ª vez: agrega el SessionStart hook preservando lo existente
    assert term.asegurar_session_hook(str(settings), cmd) is True
    d = _json.loads(settings.read_text())
    assert d['model'] == 'x'                       # no pisó lo demás
    cmds = [h['command'] for grp in d['hooks']['SessionStart'] for h in grp['hooks']]
    assert cmd in cmds

    # 2ª vez: ya está → no duplica
    assert term.asegurar_session_hook(str(settings), cmd) is False
    d2 = _json.loads(settings.read_text())
    cmds2 = [h['command'] for grp in d2['hooks']['SessionStart'] for h in grp['hooks']]
    assert cmds2.count(cmd) == 1


def test_un_comando_viejo_del_hook_se_REEMPLAZA(tmp_path):
    """REGRESIÓN (2026-07-27): el usuario tenía registrado el comando viejo

        python3 C:\\Users\\USER\\...\\jarvis_claude_hook.py

    (sin comillas y con `python3`, que en Windows no existe). Fallaba en CADA
    arranque de agente: "SessionStart:startup hook error".

    El arreglo del comando no le servía de nada: esta función solo miraba si el
    comando EXACTO ya estaba, así que con uno distinto agregaba un SEGUNDO hook
    y dejaba el roto vivo. Ahora reemplaza cualquier entrada que apunte a este
    script — es el único modo de que un arreglo llegue a quien ya lo tenía mal.
    """
    import json as _json
    settings = tmp_path / 'settings.json'
    viejo = 'python3 C:\\Users\\USER\\AppData\\Local\\Jarvis Workspace\\motor\\scripts\\jarvis_claude_hook.py'
    settings.write_text(_json.dumps({
        'model': 'x',
        'hooks': {'SessionStart': [{'hooks': [{'type': 'command', 'command': viejo}]}]},
    }))
    nuevo = 'python "C:/proyectos/jarvis/scripts/jarvis_claude_hook.py"'

    assert term.asegurar_session_hook(str(settings), nuevo) is True
    d = _json.loads(settings.read_text())
    cmds = [h['command'] for grp in d['hooks']['SessionStart'] for h in grp['hooks']]
    assert nuevo in cmds
    assert viejo not in cmds, 'dejó vivo el comando roto'
    assert len(cmds) == 1, f'duplicó el hook en vez de reemplazarlo: {cmds}'
    assert d['model'] == 'x'


def test_no_toca_hooks_de_otros(tmp_path):
    # settings.json es del USUARIO: puede tener hooks suyos en SessionStart.
    # Reemplazar el nuestro no puede llevarse los ajenos por delante.
    import json as _json
    settings = tmp_path / 'settings.json'
    ajeno = 'echo hola'
    settings.write_text(_json.dumps({
        'hooks': {'SessionStart': [{'hooks': [
            {'type': 'command', 'command': ajeno},
            {'type': 'command', 'command': 'python3 /viejo/scripts/jarvis_claude_hook.py'},
        ]}]},
    }))
    nuevo = 'python3 /nuevo/scripts/jarvis_claude_hook.py'

    assert term.asegurar_session_hook(str(settings), nuevo) is True
    d = _json.loads(settings.read_text())
    cmds = [h['command'] for grp in d['hooks']['SessionStart'] for h in grp['hooks']]
    assert ajeno in cmds, 'se llevó puesto un hook del usuario'
    assert nuevo in cmds
    assert not any('viejo' in c for c in cmds)


def test_asegurar_session_hook_settings_inexistente(tmp_path):
    import json as _json
    settings = tmp_path / 'nuevo' / 'settings.json'   # ni el dir existe
    cmd = 'python3 /x/hook.py'
    assert term.asegurar_session_hook(str(settings), cmd) is True
    d = _json.loads(settings.read_text())
    assert d['hooks']['SessionStart'][0]['hooks'][0]['command'] == cmd


if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main([__file__, '-q']))
