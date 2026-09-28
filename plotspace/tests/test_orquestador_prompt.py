"""
Test: higiene del system prompt del orquestador (Etapa 6 del rework).

El prompt le MENTÍA al modelo sobre el sistema real: decía que el orquestador
commitea (falso: commitean los agentes), que el tope es 7 terminales
(es MAX_TERMINALES=12), ofrecía solo 4 CLIs (el producto corre 7+manual) y
obligaba a repetir el protocolo de cierre en cada tarea (duplicado con el
sentinel del engine). Estos tests fijan que el drift no vuelva.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plotspace.routers.orchestrator import (
    MAX_TERMINALES,
    RESPONDER_SCHEMA,
    SYSTEM_PROMPT,
    _tarea_para_agente,
)


# ─── El prompt dice la verdad sobre el sistema ───────────────────────────────

def test_tope_de_terminales_es_el_real():
    assert str(MAX_TERMINALES) in SYSTEM_PROMPT
    assert 'Máximo 7 terminales' not in SYSTEM_PROMPT


def test_no_dice_que_el_orquestador_commitea():
    # El orquestador NO commitea: commitea cada agente.
    assert 'vos commiteás' not in SYSTEM_PROMPT
    assert 'commitean' in SYSTEM_PROMPT


def test_clis_completos():
    for cli in ('opencode', 'qwen', 'antigravity'):
        assert cli in SYSTEM_PROMPT, f'falta {cli} en el prompt'


# ─── Nuevas capacidades documentadas ─────────────────────────────────────────

def test_documenta_enviar_prompt_y_reuso():
    assert 'enviar_prompt' in SYSTEM_PROMPT
    assert 'terminal_id' in SYSTEM_PROMPT


def test_documenta_mapa_del_proyecto():
    assert '[Mapa del proyecto]' in SYSTEM_PROMPT


# ─── El cierre es del ENGINE, no del LLM (fuente única) ──────────────────────

def test_prompt_no_exige_cierre_literal():
    assert 'CIERRE LITERAL' not in SYSTEM_PROMPT


def test_tarea_del_agente_lleva_el_cierre_sentinel():
    tarea = _tarea_para_agente('hacer X', [], 42)
    assert tarea.startswith('hacer X')
    assert '.jarvis/signals/terminal_42.json' in tarea
    assert tarea.count('.jarvis/signals/terminal_42.json') == 1


def test_tarea_del_agente_lleva_su_territorio():
    tarea = _tarea_para_agente('hacer X', ['src/a.py', 'src/b/*'], 7)
    assert 'src/a.py, src/b/*' in tarea
    assert 'EXCLUSIVA' in tarea


# ─── El motor de workflows ya no existe (2026-09-28) ─────────────────────────

def test_prompt_sin_workflows_ni_reviewer():
    bajo = SYSTEM_PROMPT.lower()
    for palabra in ('workflow', 'reviewer', 'depende_de', 'paso_0'):
        assert palabra not in bajo, f'el prompt todavía menciona {palabra!r}'


def test_schema_sin_workflow_y_spawn_con_tarea():
    assert 'workflow' not in RESPONDER_SCHEMA['properties']
    props = RESPONDER_SCHEMA['properties']['actions']['items']['properties']
    assert props['tarea']['type'] == 'string'
    assert props['archivos']['type'] == 'array'


def test_prompt_documenta_spawn_con_tarea():
    assert '"type":"spawn_terminal","name":"Backend"' in SYSTEM_PROMPT
    assert '"tarea":' in SYSTEM_PROMPT and '"archivos":' in SYSTEM_PROMPT


# ─── Los ejemplos no sesgan con la estructura de Jarvis ──────────────────────

def test_ejemplos_sin_estructura_de_jarvis():
    assert 'frontend/sections/' not in SYSTEM_PROMPT
