"""
Test: enviar_prompt + reuso de terminales + estado vivo (Etapa 3 del rework).

El agujero que cierra: el orquestador VEÍA las terminales abiertas pero no
tenía manos — ninguna action permitía mandarle un prompt a una existente, y
los workflows spawneaban SIEMPRE terminales nuevas (la instrucción "reusá
terminales" del prompt era letra muerta). Acá se fija el contrato de:
  - la action `enviar_prompt` en el tool schema + su guarda pura
  - `_terminal_reusable` (pasos de workflow con terminal_id opcional)
  - el bloque [Estado actual] de core/orq_contexto (estado vivo + dueños)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plotspace.routers.orchestrator import (
    RESPONDER_TOOL,
    _terminal_reusable,
    _validar_enviar_prompt,
)


# ─── Contrato del tool schema ────────────────────────────────────────────────

def _props_action():
    return RESPONDER_TOOL['input_schema']['properties']['actions']['items']['properties']


def test_schema_incluye_enviar_prompt():
    assert 'enviar_prompt' in _props_action()['type']['enum']
    assert 'prompt' in _props_action()


def test_schema_paso_acepta_terminal_id():
    paso = (RESPONDER_TOOL['input_schema']['properties']['workflow']
            ['properties']['pasos']['items']['properties'])
    assert 'terminal_id' in paso


# ─── Guarda de enviar_prompt ─────────────────────────────────────────────────

ACTIVAS = {10, 11, 12}


def test_envio_valido():
    tid, motivo = _validar_enviar_prompt(
        {'type': 'enviar_prompt', 'terminal_id': 11, 'prompt': 'mejorá el diseño'},
        ACTIVAS, ocupadas=set())
    assert tid == 11 and motivo is None


def test_envio_terminal_id_como_string_numerico():
    tid, motivo = _validar_enviar_prompt(
        {'terminal_id': '12', 'prompt': 'x'}, ACTIVAS, set())
    assert tid == 12 and motivo is None


def test_envio_sin_terminal_id():
    tid, motivo = _validar_enviar_prompt({'prompt': 'x'}, ACTIVAS, set())
    assert tid is None and motivo


def test_envio_sin_prompt():
    tid, motivo = _validar_enviar_prompt({'terminal_id': 11}, ACTIVAS, set())
    assert tid is None and 'prompt' in motivo


def test_envio_a_terminal_inexistente():
    tid, motivo = _validar_enviar_prompt(
        {'terminal_id': 99, 'prompt': 'x'}, ACTIVAS, set())
    assert tid is None and '99' in motivo


def test_envio_a_terminal_ocupada_en_workflow():
    tid, motivo = _validar_enviar_prompt(
        {'terminal_id': 11, 'prompt': 'x'}, ACTIVAS, ocupadas={11})
    assert tid is None and 'ocupada' in motivo


# ─── Reuso de terminales en pasos de workflow ────────────────────────────────

def test_reusable_libre():
    assert _terminal_reusable(11, ACTIVAS, ocupadas=set(), reclamadas=set())


def test_no_reusable():
    assert not _terminal_reusable(None, ACTIVAS, set(), set())
    assert not _terminal_reusable(99, ACTIVAS, set(), set())          # no activa
    assert not _terminal_reusable(11, ACTIVAS, {11}, set())           # ocupada
    assert not _terminal_reusable(11, ACTIVAS, set(), {11})           # ya reclamada por otro paso
    assert not _terminal_reusable('11', ACTIVAS, set(), set())        # tipo raro: spawn normal


# ─── Estado enriquecido (núcleo puro) ────────────────────────────────────────

def _terminales():
    return [
        {'id': 10, 'nombre': 'Backend', 'tipo_ia': 'claude'},
        {'id': 11, 'nombre': 'Claude Code #2', 'tipo_ia': 'claude'},
    ]


def _estado(infos):
    from plotspace.core.orq_contexto import formatear_bloque_terminales
    return formatear_bloque_terminales(_terminales(), infos)


def _bloque_de(txt, tid):
    """Las líneas de UNA terminal (cabecera + detalle indentado)."""
    out, dentro = [], False
    for l in txt.splitlines():
        if l.startswith('- '):
            dentro = f'ID {tid}:' in l
        if dentro:
            out.append(l)
    return '\n'.join(out)


def test_estado_muestra_fase_viva():
    txt = _estado({10: {'fase': 'trabajando'}, 11: {'fase': 'idle'}})
    assert 'trabajando' in _bloque_de(txt, 10)
    assert 'quieta' in _bloque_de(txt, 11)


def test_estado_muestra_rol_de_workflow_y_libre():
    txt = _estado({10: {'fase': 'idle', 'rol': {'workflow': 'Notas', 'agente': 'Backend',
                                                'estado': 'running'},
                        'paso_activo': {'estado': 'running'}},
                   11: {'fase': 'idle'}})
    assert "rol: 'Backend' del workflow 'Notas' (paso running)" in _bloque_de(txt, 10)
    assert 'LIBRE' in _bloque_de(txt, 11)
    assert 'libres: #11 Claude Code #2' in txt


def test_estado_muestra_archivos_editados_con_tope():
    archivos = [{'path': f'{c}.py', 'dueno': c == 'a'} for c in 'abcdefgh']
    txt = _estado({10: {'fase': 'idle', 'archivos': archivos}, 11: {}})
    b10 = _bloque_de(txt, 10)
    assert 'editó: a.py 🔒, b.py' in b10 and '(+2 más)' in b10
    assert 'editó' not in _bloque_de(txt, 11)


def test_estado_sin_terminales():
    from plotspace.core.orq_contexto import formatear_bloque_terminales
    assert formatear_bloque_terminales([], {}) == 'No hay terminales activas.'


# ─── Guardas con estado VIVO (no solo pasos running) ──────────────────────────

from plotspace.routers.orchestrator import (  # noqa: E402
    _cierre_prompt_directo, _motivo_rechazo_envio,
)


def test_tarea_solo_a_una_terminal_libre():
    assert _motivo_rechazo_envio({'fase': 'idle'}, False) == ''
    assert 'trabajando' in _motivo_rechazo_envio({'fase': 'trabajando'}, False)
    assert 'caído' in _motivo_rechazo_envio({'fase': 'idle', 'estado': 'caido'}, False)
    assert 'pregunta' in _motivo_rechazo_envio({'fase': 'idle', 'esperando': True}, False)


def test_respuesta_solo_a_quien_espera_una():
    assert _motivo_rechazo_envio({'fase': 'idle', 'esperando': True}, True) == ''
    assert 'ninguna pregunta' in _motivo_rechazo_envio({'fase': 'idle'}, True)
    assert 'caído' in _motivo_rechazo_envio({'esperando': True, 'estado': 'sin_sesion'}, True)


def test_la_tarea_suelta_lleva_su_cierre_estructurado():
    c = _cierre_prompt_directo(42)
    assert '.jarvis/signals/terminal_42.json' in c
    assert '"estado":"done"' in c


def test_schema_acepta_es_respuesta():
    assert _props_action()['es_respuesta']['type'] == 'boolean'


def test_procesar_respuesta_rechaza_ocupada_y_responde_crudo(tmp_path, monkeypatch):
    """De punta a punta por _procesar_respuesta_orquestador: a la que trabaja
    no se le manda nada; a la que espera se le contesta crudo."""
    import asyncio
    import json as _json
    from types import SimpleNamespace
    import plotspace.routers.orchestrator as orch
    from plotspace.core import agent_watch
    from plotspace.core.database import get_db
    conn = get_db()
    conn.execute("INSERT INTO projects (id,nombre,ruta,fecha_creacion,ultimo_acceso) "
                 "VALUES (1,'P',?,'2026-01-01','2026-01-01')", (str(tmp_path),))
    for tid in (21, 22):
        conn.execute("INSERT INTO terminals (id,project_id,nombre,tipo_ia,activa,"
                     "fecha_creacion) VALUES (?,1,?,'claude',1,'2026-01-01')", (tid, f'T{tid}'))
    conn.commit(); conn.close()
    monkeypatch.setitem(agent_watch._estados, 21, {'fase': 'trabajando'})
    monkeypatch.setitem(agent_watch._estados, 22, {'fase': 'idle', 'esperando': True})
    enviados = []

    async def falso(tid, msg, crudo=False):
        enviados.append((tid, msg, crudo))
        return True
    monkeypatch.setattr(orch, 'send_to_agent', falso)

    async def nada(*a, **k):
        return None
    monkeypatch.setattr(orch, '_actualizar_state_md', nada)
    raw = _json.dumps({'message': 'ok', 'actions': [
        {'type': 'enviar_prompt', 'terminal_id': 21, 'prompt': 'hacé X'},
        {'type': 'enviar_prompt', 'terminal_id': 22, 'prompt': 'y', 'es_respuesta': True}]})
    ts = [{'id': 21, 'nombre': 'T21', 'tipo_ia': 'claude'},
          {'id': 22, 'nombre': 'T22', 'tipo_ia': 'claude'}]
    req = orch.ChatRequest(project_id=1, message='x')
    res = asyncio.run(orch._procesar_respuesta_orquestador(
        raw, SimpleNamespace(input_tokens=0, output_tokens=0),
        {'id': 1, 'ruta': str(tmp_path), 'nombre': 'P'}, req, ts, stop_reason='end_turn'))
    assert enviados == [(22, 'y', True)]
    assert 'trabajando' in res['response']
