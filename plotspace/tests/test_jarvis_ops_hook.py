# plotspace/tests/test_jarvis_ops_hook.py
"""El hook de provenance corre en CADA edición de CADA agente: si se cuelga o
se rompe, arrastra a todo el enjambre. Estos tests fijan sus dos garantías.

1. CORTA-CORRIENTE. En WSL, conectar a un puerto cerrado no se rechaza: se
   descarta y el intento se come el timeout entero. Medido: con Jarvis caído,
   cada edición pagaba 2.165 ms de nada. Con el corta-corriente, el primer
   fallo marca y los siguientes se saltean en ~50 ms.
2. NUNCA ROMPER. Sin terminal, con basura por stdin o con el server abajo, el
   hook termina en 0 y sin escribir nada — el agente ni se entera.
"""
import io
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

import jarvis_ops_hook as hook   # noqa: E402

RUTA = os.path.join(os.path.dirname(__file__), '..', '..', 'scripts',
                    'jarvis_ops_hook.py')
EDIT = {'hook_event_name': 'PostToolUse', 'tool_name': 'Edit',
        'tool_input': {'file_path': 'a.py', 'old_string': 'x', 'new_string': 'y'}}


def _correr(payload, env_extra=None, timeout=20):
    env = dict(os.environ, JARVIS_DATA_DIR=env_extra.pop('_data', '/tmp/no-existe-jv')
               if env_extra else '/tmp/no-existe-jv')
    env.update(env_extra or {})
    return subprocess.run([sys.executable, RUTA],
                          input=json.dumps(payload).encode(),
                          capture_output=True, env=env, timeout=timeout)


# ─── Garantía 2: nunca romper ─────────────────────────────────────────────────

def test_sin_terminal_id_es_noop():
    r = _correr(EDIT, {'JARVIS_TERMINAL_ID': ''})
    assert r.returncode == 0 and r.stdout == b''


def test_stdin_basura_no_rompe():
    r = subprocess.run([sys.executable, RUTA], input=b'no soy json',
                       capture_output=True, timeout=20,
                       env=dict(os.environ, JARVIS_TERMINAL_ID='1'))
    assert r.returncode == 0 and r.stdout == b''


def test_payload_que_no_es_objeto_no_rompe():
    r = _correr([1, 2, 3], {'JARVIS_TERMINAL_ID': '1'})
    assert r.returncode == 0


def test_server_caido_no_rompe_ni_escribe(tmp_path):
    r = _correr(EDIT, {'JARVIS_TERMINAL_ID': '1', 'JARVIS_PORT': '59999',
                       '_data': str(tmp_path)})
    assert r.returncode == 0
    assert r.stdout == b''          # nada que el CLI pueda malinterpretar


def test_pretooluse_con_server_caido_deja_pasar(tmp_path):
    """Falla ABIERTO: si no puedo preguntar, no bloqueo."""
    r = _correr({'hook_event_name': 'PreToolUse', 'tool_name': 'Write',
                 'tool_input': {'file_path': 'a.py', 'content': 'x'}},
                {'JARVIS_TERMINAL_ID': '1', 'JARVIS_PORT': '59999',
                 '_data': str(tmp_path)})
    assert r.returncode == 0
    assert b'deny' not in r.stdout


# ─── Garantía 1: corta-corriente ──────────────────────────────────────────────

def test_corta_corriente_cerrado_por_defecto(tmp_path):
    assert hook._corta_corriente_abierto(str(tmp_path)) is False


def test_corta_corriente_se_abre_al_marcar(tmp_path):
    hook._marcar_caido(str(tmp_path), True)
    assert hook._corta_corriente_abierto(str(tmp_path)) is True


def test_corta_corriente_se_cierra_al_limpiar(tmp_path):
    hook._marcar_caido(str(tmp_path), True)
    hook._marcar_caido(str(tmp_path), False)
    assert hook._corta_corriente_abierto(str(tmp_path)) is False


def test_corta_corriente_expira(tmp_path):
    """Se cura solo: pasado el minuto vuelve a intentar (el server puede haber
    vuelto tras un reinicio)."""
    hook._marcar_caido(str(tmp_path), True)
    p = os.path.join(str(tmp_path), hook.CAIDO_NOMBRE)
    viejo = time.time() - hook.CAIDO_S - 5
    os.utime(p, (viejo, viejo))
    assert hook._corta_corriente_abierto(str(tmp_path)) is False


def test_corta_corriente_evita_la_espera(tmp_path):
    """La prueba que importa: con el corta-corriente abierto, el hook contra un
    puerto muerto termina rápido en vez de comerse el timeout."""
    hook._marcar_caido(str(tmp_path), True)
    t0 = time.perf_counter()
    r = _correr(EDIT, {'JARVIS_TERMINAL_ID': '1', 'JARVIS_PORT': '59999',
                       '_data': str(tmp_path)})
    ms = (time.perf_counter() - t0) * 1000
    assert r.returncode == 0
    assert ms < 900, f'tardó {ms:.0f}ms: el corta-corriente no cortó'


def test_marcar_caido_en_dir_inexistente_no_rompe():
    hook._marcar_caido('/proc/no/existe', True)     # no debe tirar


# ─── Salida de bloqueo y de contexto ──────────────────────────────────────────

def test_formato_de_denegacion(capsys):
    hook._denegar('no reescribas eso')
    out = json.loads(capsys.readouterr().out)
    hso = out['hookSpecificOutput']
    assert hso['hookEventName'] == 'PreToolUse'
    assert hso['permissionDecision'] == 'deny'
    assert hso['permissionDecisionReason'] == 'no reescribas eso'


# ─── Antigravity: otro payload y otro contrato de salida ─────────────────────

def test_datos_herramienta_forma_clasica():
    tn, ti, agy = hook.datos_herramienta(
        {'tool_name': 'Edit', 'tool_input': {'file_path': 'a.py'}})
    assert tn == 'Edit' and ti == {'file_path': 'a.py'} and agy is False


def test_datos_herramienta_forma_antigravity():
    """Antigravity manda {toolCall:{name,args}} (protojson camelCase), NO
    {tool_name, tool_input} — sin traducirlo, el hook no vería la edición."""
    tn, ti, agy = hook.datos_herramienta(
        {'toolCall': {'name': 'write_to_file', 'args': {'path': 'a.py', 'content': 'x'}},
         'stepIdx': 3, 'conversationId': 'c1'})
    assert tn == 'write_to_file'
    assert ti == {'path': 'a.py', 'content': 'x'}
    assert agy is True


def test_datos_herramienta_basura_no_rompe():
    assert hook.datos_herramienta({}) == (None, None, False)
    assert hook.datos_herramienta({'toolCall': 'no-dict'})[2] is False
    assert hook.datos_herramienta({'toolCall': {'name': 'x', 'args': 'no-dict'}})[1] is None


def test_antigravity_siempre_contesta_allow(tmp_path):
    """Su hook corre SÍNCRONO y BLOQUEA el loop del agente si no recibe JSON
    válido: hay que contestar allow SIEMPRE, aun con Jarvis caído."""
    r = _correr({'toolCall': {'name': 'write_to_file',
                              'args': {'path': 'a.py', 'content': 'x'}}},
                {'JARVIS_TERMINAL_ID': '1', 'JARVIS_PORT': '59999',
                 '_data': str(tmp_path)})
    assert r.returncode == 0
    assert json.loads(r.stdout)['decision'] == 'allow'


def test_briefing_sin_server_no_rompe_ni_ensucia_el_prompt(tmp_path):
    """UserPromptSubmit corre ANTES de cada tarea. Si Jarvis está caído el
    agente tiene que arrancar igual, sin un solo byte raro en su contexto."""
    r = _correr({'hook_event_name': 'UserPromptSubmit', 'prompt': 'arreglá X'},
                {'JARVIS_TERMINAL_ID': '1', 'JARVIS_PORT': '9', '_data': str(tmp_path)})
    assert r.returncode == 0
    assert r.stdout.strip() == b''


def test_briefing_emite_contexto_en_el_evento_correcto(capsys):
    """El texto viaja como additionalContext de UserPromptSubmit — si el
    hookEventName no coincide, el CLI lo descarta y el briefing muere mudo."""
    hook._contexto('[Enjambre] Sos X', evento='UserPromptSubmit')
    d = json.loads(capsys.readouterr().out)
    assert d['additionalContext'] == '[Enjambre] Sos X'
    assert d['hookSpecificOutput']['hookEventName'] == 'UserPromptSubmit'
    assert d['hookSpecificOutput']['additionalContext'] == '[Enjambre] Sos X'


def test_briefing_vacio_no_escribe_nada(tmp_path, monkeypatch):
    """Un agente solo en el proyecto no debe pagar ni un token de briefing."""
    monkeypatch.setattr(hook, '_get', lambda *a, **k: {'texto': ''})
    monkeypatch.setenv('JARVIS_TERMINAL_ID', '1')
    monkeypatch.setattr('sys.stdin', io.StringIO(
        json.dumps({'hook_event_name': 'UserPromptSubmit'})))
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        hook.main()
    assert buf.getvalue().strip() == ''


def test_formato_de_contexto_emite_las_dos_formas(capsys):
    """El aviso viaja en las dos formas conocidas del contrato: si el CLI
    renombra una, la otra sigue llegando (la lección del parser muerto)."""
    hook._contexto('ojo con esto')
    out = json.loads(capsys.readouterr().out)
    assert out['additionalContext'] == 'ojo con esto'
    assert out['hookSpecificOutput']['additionalContext'] == 'ojo con esto'


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-q']))


def test_stop_avisa_el_fin_y_no_imprime_nada(monkeypatch, capsys):
    """Stop = el agente terminó de responder: POST /api/swarm/fin y silencio
    (un hook Stop que imprime puede mantener al agente en marcha)."""
    llamadas = []
    monkeypatch.setenv('JARVIS_TERMINAL_ID', '12')
    monkeypatch.setattr(hook, '_post', lambda ruta, cuerpo, t: llamadas.append((ruta, cuerpo)))
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({'hook_event_name': 'Stop'})))
    hook.main()
    assert llamadas == [('/api/swarm/fin', {'terminal_id': 12})]
    assert capsys.readouterr().out == ''


def test_stop_con_server_caido_no_rompe(monkeypatch, capsys):
    monkeypatch.setenv('JARVIS_TERMINAL_ID', '12')
    def _boom(*a, **k): raise OSError('caído')
    monkeypatch.setattr(hook, '_post', _boom)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({'hook_event_name': 'Stop'})))
    hook.main()
    assert capsys.readouterr().out == ''
