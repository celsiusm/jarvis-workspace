"""
Test: orq_cli — el orquestador corre con la SUSCRIPCIÓN de Claude.

El transport nuevo: en vez de la API de Anthropic (key paga), el orquestador
spawnea `claude -p` headless que se autentica con la cuenta de suscripción
activa (OAuth de ~/.claude), igual que los agentes de las terminales. Acá se
fija el contrato de las partes PURAS: el env del subproceso (sin credenciales
de API → fuerza OAuth), el argv (receta verificada en vivo: --safe-mode +
stream-json + --json-schema), el aplanado de mensajes multi-turno a UN prompt,
y el parser de eventos stream-json (fixtures de una corrida real).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plotspace.core import orq_cli


# ─── Env del subproceso: suscripción, no API ─────────────────────────────────

def test_env_sin_credenciales_de_api():
    base = {'PATH': '/usr/bin', 'ANTHROPIC_API_KEY': 'sk-ant-' + 'x' * 20,
            'ANTHROPIC_AUTH_TOKEN': 'tok', 'CLAUDECODE': '1',
            'CLAUDE_CODE_ENTRYPOINT': 'cli', 'HOME': '/home/user'}
    env = orq_cli._env_suscripcion(base)
    assert 'ANTHROPIC_API_KEY' not in env
    assert 'ANTHROPIC_AUTH_TOKEN' not in env
    assert 'CLAUDECODE' not in env
    assert env['HOME'] == '/home/user'
    # sin ruido de red en cada llamada
    assert env['DISABLE_AUTOUPDATER'] == '1'
    assert env['CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'] == '1'
    # no muta el dict base
    assert 'ANTHROPIC_API_KEY' in base


# ─── Argv (la receta medida: 73s→13s con safe-mode + sin autoupdater) ────────

def test_argv_receta_completa():
    argv = orq_cli._argv('hola', 'sos jarvis', 'sonnet',
                         schema={'type': 'object'})
    assert argv[0] == 'claude'
    assert '-p' in argv and 'hola' in argv
    assert '--safe-mode' in argv                 # sin plugins/MCPs/hooks
    assert '--verbose' in argv                   # stream-json lo exige
    assert '--output-format' in argv and 'stream-json' in argv
    assert '--include-partial-messages' in argv
    assert '--system-prompt' in argv and 'sos jarvis' in argv
    assert '--model' in argv and 'sonnet' in argv
    i = argv.index('--json-schema')
    assert json.loads(argv[i + 1]) == {'type': 'object'}


def test_argv_tools_lectura_default_y_sin_tools():
    argv = orq_cli._argv('x', 's', 'm')
    i = argv.index('--tools')
    assert argv[i + 1] == orq_cli.TOOLS_LECTURA          # Read,Glob,Grep
    argv2 = orq_cli._argv('x', 's', 'm', tools='')
    assert argv2[argv2.index('--tools') + 1] == ''


# ─── Aplanado de mensajes multi-turno ────────────────────────────────────────

def test_prompt_un_solo_mensaje():
    assert orq_cli.prompt_desde_mensajes(
        [{'role': 'user', 'content': '[Orden]\nhola'}]) == '[Orden]\nhola'


def test_prompt_con_historial_etiquetado():
    msgs = [
        {'role': 'user', 'content': 'armá notas'},
        {'role': 'assistant', 'content': 'De acuerdo.'},
        {'role': 'user', 'content': '[Orden]\nsumale tests'},
    ]
    p = orq_cli.prompt_desde_mensajes(msgs)
    assert 'armá notas' in p and 'De acuerdo.' in p
    assert p.rstrip().endswith('[Orden]\nsumale tests')
    assert 'Usuario' in p and 'Jarvis' in p


def test_prompt_contenido_en_bloques_extrae_texto():
    msgs = [{'role': 'user', 'content': [
        {'type': 'text', 'text': 'parte a'},
        {'type': 'image', 'source': {}},
        {'type': 'text', 'text': 'parte b'},
    ]}]
    p = orq_cli.prompt_desde_mensajes(msgs)
    assert 'parte a' in p and 'parte b' in p


# ─── Parser de eventos (fixtures de una corrida real del CLI 2.1.215) ────────

def _lineas_reales():
    return [
        '{"type":"system","subtype":"init","session_id":"abc"}',
        'esto no es json (stderr mezclado)',
        '{"type":"stream_event","event":{"type":"message_start","message":{}}}',
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"mmm"}}}',
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"{\\"message\\":\\"Ho"}}}',
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"la\\"}"}}}',
        '{"type":"assistant","message":{}}',
        '{"type":"result","subtype":"success","is_error":false,"num_turns":2,'
        '"result":"{\\"message\\":\\"Hola\\"}",'
        '"structured_output":{"message":"Hola","actions":[{"type":"none"}]},'
        '"usage":{"input_tokens":16,"output_tokens":242},"session_id":"abc"}',
    ]


def test_parser_emite_reinicio_deltas_y_resultado():
    evs = list(orq_cli.eventos_desde_lineas(_lineas_reales()))
    tipos = [e['tipo'] for e in evs]
    assert tipos == ['reinicio', 'delta', 'delta', 'resultado']
    assert evs[1]['texto'] == '{"message":"Ho'
    r = evs[-1]
    assert r['input_tokens'] == 16 and r['output_tokens'] == 242
    assert r['error'] is False and r['num_turns'] == 2
    # con structured_output, el texto canónico es ESE json (sin fences)
    assert json.loads(r['texto'])['message'] == 'Hola'
    assert json.loads(r['texto'])['actions'] == [{'type': 'none'}]


def test_parser_sin_structured_cae_al_result_crudo():
    lineas = ['{"type":"result","subtype":"success","is_error":false,'
              '"result":"```json\\n{\\"message\\":\\"x\\"}\\n```",'
              '"usage":{"input_tokens":1,"output_tokens":2}}']
    r = list(orq_cli.eventos_desde_lineas(lineas))[-1]
    assert r['tipo'] == 'resultado'
    assert '```json' in r['texto']       # el de-fence lo hace el pipeline de arriba


def test_parser_lineas_basura_no_rompen():
    evs = list(orq_cli.eventos_desde_lineas(['', 'basura', '{"type":"rate_limit_event"}']))
    assert evs == []


# ─── Prompt por stdin + eventos de herramientas ──────────────────────────────

def test_argv_sin_prompt_no_lo_pone_como_argumento():
    argv = orq_cli._argv(None, 's', 'm')
    assert argv[:2] == ['claude', '-p'] and argv[2] == '--model'


def test_parser_emite_las_tools_que_usa():
    linea = json.dumps({'type': 'assistant', 'message': {'content': [
        {'type': 'text', 'text': 'miro'},
        {'type': 'tool_use', 'name': 'Read', 'input': {'file_path': '/p/main.py'}},
        {'type': 'tool_use', 'name': 'Grep', 'input': {'pattern': 'def login'}}]}})
    evs = list(orq_cli.eventos_desde_lineas([linea]))
    assert evs == [{'tipo': 'herramienta', 'nombre': 'Read', 'detalle': '/p/main.py'},
                   {'tipo': 'herramienta', 'nombre': 'Grep', 'detalle': 'def login'}]


def test_stream_manda_el_prompt_por_stdin_aunque_sea_enorme(tmp_path, monkeypatch):
    """Un prompt de 300 KB como argumento revienta con E2BIG (tope de 128 KB
    por argumento en Linux). Por stdin llega entero."""
    import asyncio
    falso = tmp_path / 'claude'
    falso.write_text(
        '#!/usr/bin/env python3\n'
        'import sys, json\n'
        'dato = sys.stdin.read()\n'
        'print(json.dumps({"type": "result", "result": str(len(dato)), "usage": {}}))\n')
    falso.chmod(0o755)
    monkeypatch.setattr(orq_cli, 'BIN', str(falso))
    prompt = 'x' * 300_000

    async def correr():
        return [ev async for ev in orq_cli.stream(prompt, 's', 'm', timeout_s=30)]
    evs = asyncio.run(correr())
    assert evs[-1]['tipo'] == 'resultado'
    assert evs[-1]['texto'] == '300000'
