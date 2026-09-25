# plotspace/tests/test_hooks_cli.py
"""Instalación de los hooks de provenance en el settings.json del CLI.

Es la pieza que revive la propiedad de archivos, así que tiene que ser
idempotente y NO destructiva: el settings.json del usuario tiene su propia
configuración (incluido el SessionStart hook que Jarvis ya instalaba) y un
instalador que la pise sería peor que el problema que arregla.
"""
import json

from plotspace.core import hooks_cli
from plotspace.core.hooks_cli import asegurar_hooks_provenance, NOMBRE_SCRIPT


def _leer(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def test_instala_ambos_eventos(tmp_path):
    p = tmp_path / 'settings.json'
    assert asegurar_hooks_provenance(str(p), 'python3 /r/scripts/jarvis_ops_hook.py') is True
    d = _leer(p)
    assert 'PostToolUse' in d['hooks'] and 'PreToolUse' in d['hooks']


def test_es_idempotente(tmp_path):
    p = tmp_path / 'settings.json'
    cmd = 'python3 /r/scripts/jarvis_ops_hook.py'
    assert asegurar_hooks_provenance(str(p), cmd) is True
    assert asegurar_hooks_provenance(str(p), cmd) is False
    d = _leer(p)
    assert len(d['hooks']['PostToolUse']) == 1
    assert len(d['hooks']['PreToolUse']) == 1


def test_preserva_configuracion_existente(tmp_path):
    """El settings del usuario tiene el SessionStart de Jarvis y lo suyo: nada
    de eso se puede perder."""
    p = tmp_path / 'settings.json'
    original = {
        'model': 'opus',
        'permissions': {'allow': ['Bash(ls)']},
        'hooks': {'SessionStart': [{'hooks': [
            {'type': 'command', 'command': 'python3 /r/scripts/jarvis_claude_hook.py'}]}]},
    }
    p.write_text(json.dumps(original), encoding='utf-8')
    asegurar_hooks_provenance(str(p), 'python3 /r/scripts/jarvis_ops_hook.py')
    d = _leer(p)
    assert d['model'] == 'opus'
    assert d['permissions']['allow'] == ['Bash(ls)']
    assert d['hooks']['SessionStart'][0]['hooks'][0]['command'].endswith('jarvis_claude_hook.py')
    assert 'PostToolUse' in d['hooks']


def test_reemplaza_ruta_vieja_del_mismo_hook(tmp_path):
    """Si el repo se movió, el comando cambia: hay que ACTUALIZAR la entrada,
    no dejar dos (una apuntando a una ruta muerta que tira error en cada edit)."""
    p = tmp_path / 'settings.json'
    asegurar_hooks_provenance(str(p), f'python3 /viejo/scripts/{NOMBRE_SCRIPT}')
    assert asegurar_hooks_provenance(str(p), f'python3 /nuevo/scripts/{NOMBRE_SCRIPT}') is True
    d = _leer(p)
    posts = [h for g in d['hooks']['PostToolUse'] for h in g['hooks']]
    assert len(posts) == 1
    assert posts[0]['command'] == f'python3 /nuevo/scripts/{NOMBRE_SCRIPT}'


def test_convive_con_hooks_ajenos_del_mismo_evento(tmp_path):
    """Otro hook PostToolUse del usuario (un linter, por ejemplo) no se toca."""
    p = tmp_path / 'settings.json'
    p.write_text(json.dumps({'hooks': {'PostToolUse': [
        {'matcher': 'Edit', 'hooks': [{'type': 'command', 'command': 'mi-linter.sh'}]}]}}),
        encoding='utf-8')
    asegurar_hooks_provenance(str(p), f'python3 /r/{NOMBRE_SCRIPT}')
    cmds = [h['command'] for g in _leer(p)['hooks']['PostToolUse'] for h in g['hooks']]
    assert 'mi-linter.sh' in cmds
    assert any(NOMBRE_SCRIPT in c for c in cmds)


def test_matchers_correctos(tmp_path):
    """Los dos eventos miran las tres herramientas de escritura. PreToolUse
    necesita ver los Edit para poder frenar el borrado de un símbolo ajeno
    ANTES de que ocurra; lo que evita los falsos bloqueos no es el matcher sino
    lo poco que frena el chequeo (ver core/territorio.py)."""
    p = tmp_path / 'settings.json'
    asegurar_hooks_provenance(str(p), f'python3 /r/{NOMBRE_SCRIPT}')
    d = _leer(p)
    post = d['hooks']['PostToolUse'][0]['matcher']
    pre = d['hooks']['PreToolUse'][0]['matcher']
    for t in ('Edit', 'Write', 'NotebookEdit'):
        assert t in post, t
        assert t in pre, t


def test_tiene_timeout_corto(tmp_path):
    """Sin timeout, un Jarvis colgado congelaría la herramienta del agente."""
    p = tmp_path / 'settings.json'
    asegurar_hooks_provenance(str(p), f'python3 /r/{NOMBRE_SCRIPT}')
    d = _leer(p)
    for evento in ('PostToolUse', 'PreToolUse'):
        for g in d['hooks'][evento]:
            for h in g['hooks']:
                assert 0 < h.get('timeout', 999) <= 15, (evento, h)


def test_json_corrupto_no_rompe_ni_borra(tmp_path):
    """Un settings ilegible NO se pisa: se devuelve False y se deja como está
    (borrarle la config al usuario por un JSON con una coma de más, jamás)."""
    p = tmp_path / 'settings.json'
    p.write_text('{ esto no es json', encoding='utf-8')
    assert asegurar_hooks_provenance(str(p), 'python3 /r/x.py') is False
    assert p.read_text(encoding='utf-8') == '{ esto no es json'


def test_ruta_imposible_devuelve_false(tmp_path):
    assert asegurar_hooks_provenance('/proc/no/se/puede/settings.json', 'x') is False


# ─── UserPromptSubmit: el canal del briefing ─────────────────────────────────
# Es el evento que convierte "saber del otro" de pull (el agente tiene que
# acordarse de correr `jv estado`) a push garantizado, sin gastar un turno.

def test_instala_el_evento_del_briefing(tmp_path):
    p = tmp_path / 'settings.json'
    asegurar_hooks_provenance(str(p), f'python3 /r/{NOMBRE_SCRIPT}')
    grupos = _leer(p)['hooks']['UserPromptSubmit']
    assert len(grupos) == 1
    assert NOMBRE_SCRIPT in grupos[0]['hooks'][0]['command']


def test_el_briefing_no_lleva_matcher(tmp_path):
    """UserPromptSubmit no es un evento de herramienta: no hay tool que matchear,
    y escribir un matcher inventado ahí es basura en el settings del usuario."""
    p = tmp_path / 'settings.json'
    asegurar_hooks_provenance(str(p), f'python3 /r/{NOMBRE_SCRIPT}')
    assert 'matcher' not in _leer(p)['hooks']['UserPromptSubmit'][0]


def test_el_briefing_tambien_es_idempotente(tmp_path):
    p = tmp_path / 'settings.json'
    cmd = f'python3 /r/{NOMBRE_SCRIPT}'
    asegurar_hooks_provenance(str(p), cmd)
    assert asegurar_hooks_provenance(str(p), cmd) is False
    assert len(_leer(p)['hooks']['UserPromptSubmit']) == 1


def test_matcher_none_no_pisa_hooks_ajenos_del_mismo_evento(tmp_path):
    """El usuario puede tener SU propio UserPromptSubmit: no se toca."""
    p = tmp_path / 'settings.json'
    p.write_text(json.dumps({'hooks': {'UserPromptSubmit': [
        {'hooks': [{'type': 'command', 'command': 'mi-cosa.sh'}]}]}}),
        encoding='utf-8')
    asegurar_hooks_provenance(str(p), f'python3 /r/{NOMBRE_SCRIPT}')
    comandos = [h['command'] for g in _leer(p)['hooks']['UserPromptSubmit']
                for h in g['hooks']]
    assert 'mi-cosa.sh' in comandos
    assert any(NOMBRE_SCRIPT in c for c in comandos)


if __name__ == '__main__':
    import sys
    import pytest
    sys.exit(pytest.main([__file__, '-q']))


# ── El comando del hook cambia según el sistema ──────────────────────────
# El hook de provenance es lo que registra QUIÉN tocó cada archivo. Si el
# comando no arranca, falla en silencio en cada escritura y el enjambre se
# queda ciego justo en la plataforma nueva.

def test_en_windows_el_interprete_no_es_python3(tmp_path):
    """En Windows `python3` no existe: el instalador oficial deja `python` y
    el lanzador `py`. Registrar `python3` deja el hook muerto para siempre."""
    cmd = hooks_cli.comando_hook(str(tmp_path), sistema='nt')
    assert ' -S ' in cmd, cmd
    assert not cmd.startswith('python3'), cmd
    assert cmd.startswith('python '), cmd


def test_en_windows_la_ruta_va_entre_comillas(tmp_path):
    # En Windows la ruta tiene espacios casi siempre (C:\Users\Juan Pérez\…):
    # sin comillas, el CLI parte el comando y el hook nunca corre.
    cmd = hooks_cli.comando_hook(str(tmp_path), sistema='nt')
    assert cmd.count('"') == 2, cmd
    assert cmd.rstrip().endswith('"'), cmd


def test_el_session_hook_usa_el_mismo_trato_que_el_de_provenance(tmp_path):
    """REGRESIÓN (2026-07-27): el SessionStart hook se armaba aparte en
    main.py, con `python3` y SIN comillas. En Windows el CLI ejecuta el hook a
    través de un shell, y ahí las barras invertidas son escapes:
    `C:\\Users\\USER\\...` llegaba como `C:UsersUSER...` y Python lo buscaba
    relativo al cwd. En pantalla: "SessionStart:startup hook error · can't open
    file 'C:\\proyectos\\jarvis\\UsersUSER…'".

    Dos hooks con dos formas de armar el mismo comando es la causa: uno se
    arregló y el otro no. Ahora salen de la misma función."""
    cmd = hooks_cli.comando_session_hook(str(tmp_path), sistema='nt')
    assert cmd.startswith('python '), cmd
    assert cmd.count('"') == 2, f'sin comillas el shell se come las barras: {cmd}'
    assert cmd.rstrip().endswith('"'), cmd


def test_el_session_hook_apunta_a_su_script(tmp_path):
    for sistema in ('nt', 'posix'):
        cmd = hooks_cli.comando_session_hook(str(tmp_path), sistema=sistema)
        assert 'jarvis_claude_hook.py' in cmd, cmd


def test_el_session_hook_en_unix_no_cambia(tmp_path):
    cmd = hooks_cli.comando_session_hook(str(tmp_path), sistema='posix')
    assert '|| python3 /' in cmd, cmd
    assert '"' not in cmd


def test_en_unix_se_conserva_el_comando_de_siempre(tmp_path):
    cmd = hooks_cli.comando_hook(str(tmp_path), sistema='posix')
    assert '|| python3 -S /' in cmd, cmd
    assert '"' not in cmd, 'en Unix no hacían falta comillas y no se agregan'


def test_sin_tolerancia_el_comando_es_el_crudo(tmp_path):
    """Antigravity no documenta que corra el hook con un shell: le va el
    comando pelado."""
    cmd = hooks_cli.comando_hook(str(tmp_path), sistema='posix', tolerante=False)
    assert cmd.startswith('python3 -S /'), cmd


def test_el_script_apuntado_es_el_de_provenance(tmp_path):
    for sistema in ('nt', 'posix'):
        assert hooks_cli.NOMBRE_SCRIPT in hooks_cli.comando_hook(str(tmp_path), sistema)


# ── Un script que desaparece NO puede trabar al CLI ──────────────────────
# REGRESIÓN (2026-09-25): el server se arrancó una vez desde una copia en
# /tmp/.../scratchpad/old/ y registró el hook con ESA ruta. Al borrarse la
# copia, `python3 -S <ruta>` salía con código 2 ("can't open file") y en
# UserPromptSubmit el código 2 BLOQUEA el prompt: el usuario no podía mandar
# ni un mensaje. El hook es best-effort: si no está, tiene que ser un no-op.

import subprocess  # noqa: E402


def _correr(cmd):
    return subprocess.run(['sh', '-c', cmd], capture_output=True, text=True,
                          input='{}', timeout=10)


def test_script_ausente_sale_con_cero(tmp_path):
    for armar in (hooks_cli.comando_hook, hooks_cli.comando_session_hook):
        cmd = armar(str(tmp_path / 'no-existe'), sistema='posix')
        r = _correr(cmd)
        assert r.returncode == 0, (cmd, r.returncode, r.stderr)


def test_script_presente_conserva_su_codigo_de_salida(tmp_path):
    """El guard no se come el resultado del script real."""
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts' / NOMBRE_SCRIPT).write_text('import sys; sys.exit(3)\n')
    r = _correr(hooks_cli.comando_hook(str(tmp_path), sistema='posix'))
    assert r.returncode == 3, r.stderr


def test_ruta_con_espacios_en_unix(tmp_path):
    raiz = tmp_path / 'con espacio'
    (raiz / 'scripts').mkdir(parents=True)
    (raiz / 'scripts' / NOMBRE_SCRIPT).write_text('import sys; sys.exit(4)\n')
    r = _correr(hooks_cli.comando_hook(str(raiz), sistema='posix'))
    assert r.returncode == 4, r.stderr


def test_raiz_en_tmp_es_efimera(tmp_path):
    """Una copia del repo bajo el directorio temporal NO instala hooks en el
    settings del usuario: cuando la copia se borre, el hook queda apuntando a
    la nada."""
    import tempfile
    assert hooks_cli.raiz_es_efimera('/tmp/x/scratchpad/old') is True
    assert hooks_cli.raiz_es_efimera(os.path.join(tempfile.gettempdir(), 'r')) is True
    assert hooks_cli.raiz_es_efimera('/home/user/jarvis-workspace') is False
    assert hooks_cli.raiz_es_efimera('/tmpfoo/repo') is False


import os  # noqa: E402
