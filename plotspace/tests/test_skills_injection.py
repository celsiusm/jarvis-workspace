"""
Test: inyección del bloque de skills en el CLAUDE.md de cada proyecto
(`_inyectar_skills_en_proyecto`).

Cubre el des-embebido de `qa-browser-jarvis`: la skill es larga (~75 líneas)
y el bloque se reinyecta en el CLAUDE.md que se carga en CADA sesión de CADA
agente, así que debe emitir un PUNTERO al .md en vez de inlinear el cuerpo.
Las demás skills .md SÍ se inlinean completas. Mismo contrato idempotente que
mailbox/puertos: preserva el contenido del CLAUDE.md fuera de los markers.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plotspace.routers.terminals import (
    _inyectar_skills_en_proyecto,
    _SKILLS_MARKER_START,
    _SKILLS_MARKER_END,
)

# project_id inexistente → sin filas en project_skills (sin plugins ni skills
# manuales): el bloque refleja solo las skills .md del tmp_path.
PROJ_INEXISTENTE = 999_999_999

CUERPO_QA = (
    '---\n'
    'name: qa-browser-jarvis\n'
    'description: como verificar en browser\n'
    '---\n\n'
    '# QA en browser\n' + ('linea de relleno larga del cuerpo\n' * 60)
)
CUERPO_OTRA = '# Otra skill\n\nEsta SI se inlinea completa.\n'


def _setup_proyecto(tmp_path):
    skills_dir = os.path.join(str(tmp_path), '.claude', 'skills')
    os.makedirs(skills_dir, exist_ok=True)
    with open(os.path.join(skills_dir, 'qa-browser-jarvis.md'), 'w', encoding='utf-8') as f:
        f.write(CUERPO_QA)
    with open(os.path.join(skills_dir, 'otra.md'), 'w', encoding='utf-8') as f:
        f.write(CUERPO_OTRA)
    with open(os.path.join(str(tmp_path), 'CLAUDE.md'), 'w', encoding='utf-8') as f:
        f.write('# Proyecto\n\nInstrucciones del usuario (cabecera).\n')


def _leer(tmp_path):
    with open(os.path.join(str(tmp_path), 'CLAUDE.md'), encoding='utf-8') as f:
        return f.read()


def test_qa_browser_se_des_embebe_a_puntero(tmp_path):
    _setup_proyecto(tmp_path)
    _inyectar_skills_en_proyecto(PROJ_INEXISTENTE, str(tmp_path))
    md = _leer(tmp_path)
    # el puntero está presente...
    assert 'Skill qa-browser-jarvis — ver `.claude/skills/qa-browser-jarvis.md`' in md
    # ...y el cuerpo completo de la skill NO (ni su frontmatter ni el relleno)
    assert 'name: qa-browser-jarvis' not in md
    assert 'linea de relleno larga del cuerpo' not in md


def test_otras_skills_si_se_inlinean(tmp_path):
    _setup_proyecto(tmp_path)
    _inyectar_skills_en_proyecto(PROJ_INEXISTENTE, str(tmp_path))
    md = _leer(tmp_path)
    assert '#### otra' in md
    assert 'Esta SI se inlinea completa.' in md


def test_preserva_cabecera_y_markers(tmp_path):
    _setup_proyecto(tmp_path)
    _inyectar_skills_en_proyecto(PROJ_INEXISTENTE, str(tmp_path))
    md = _leer(tmp_path)
    assert 'Instrucciones del usuario (cabecera).' in md  # contenido fuera de markers intacto
    assert md.count(_SKILLS_MARKER_START) == 1
    assert md.count(_SKILLS_MARKER_END) == 1


def test_idempotente(tmp_path):
    _setup_proyecto(tmp_path)
    _inyectar_skills_en_proyecto(PROJ_INEXISTENTE, str(tmp_path))
    primero = _leer(tmp_path)
    _inyectar_skills_en_proyecto(PROJ_INEXISTENTE, str(tmp_path))
    segundo = _leer(tmp_path)
    assert primero == segundo
    assert segundo.count(_SKILLS_MARKER_START) == 1
