"""Detección multi-IA de skills / instrucciones (core/skills_ia.py).

Cada IA de código lee sus propios archivos de instrucciones: Claude Code sus
skills/commands/agents, Codex el AGENTS.md, Gemini el GEMINI.md, Cursor sus
.mdc, Copilot su copilot-instructions.md… La pantalla ⚙ → Extensiones muestra
qué va a leer CADA una en el proyecto. Estos tests fijan el contrato del
detector: forma normalizada, descripción corta (nunca el cuerpo), topes y
que jamás se salga del proyecto por un symlink.
"""
import os

from plotspace.core import skills_ia as sk


def _w(base, rel, texto):
    p = os.path.join(str(base), rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(texto)
    return p


def _por_path(items):
    return {i['path']: i for i in items}


def _det(proj, home=None):
    return sk.detectar(str(proj), home=str(home) if home else None)


# ─── Claude Code ─────────────────────────────────────────────────────────────

def test_claude_skills_flat_y_carpeta(tmp_path):
    _w(tmp_path, '.claude/skills/deploy/SKILL.md',
       '---\nname: deploy\ndescription: Cómo desplegar a prod\n---\n\n# Deploy\nbody')
    _w(tmp_path, '.claude/skills/qa.md', '# QA en browser\n\nPasos largos…')
    items = _por_path(_det(tmp_path))
    d = items['.claude/skills/deploy/SKILL.md']
    assert d['ia'] == 'claude' and d['kind'] == 'skill'
    assert d['nombre'] == 'deploy'
    assert d['descripcion'] == 'Cómo desplegar a prod'
    assert d['editable'] is True and d['formato'] == 'carpeta'
    q = items['.claude/skills/qa.md']
    assert q['nombre'] == 'qa' and q['descripcion'] == 'QA en browser'
    assert q['editable'] is True and q['formato'] == 'flat'


def test_claude_commands_agents_y_claude_md(tmp_path):
    _w(tmp_path, '.claude/commands/review.md', '---\ndescription: Revisa el diff\n---\nhaz x')
    _w(tmp_path, '.claude/commands/git/pr.md', 'Abre un PR')
    _w(tmp_path, '.claude/agents/tester.md', '---\nname: tester\ndescription: Corre tests\n---\n')
    _w(tmp_path, 'CLAUDE.md', '# Guía del repo\n\nreglas')
    items = _por_path(_det(tmp_path))
    assert items['.claude/commands/review.md']['kind'] == 'command'
    assert items['.claude/commands/review.md']['nombre'] == '/review'
    assert items['.claude/commands/git/pr.md']['nombre'] == '/git:pr'
    assert items['.claude/agents/tester.md']['kind'] == 'agent'
    assert items['.claude/agents/tester.md']['nombre'] == 'tester'
    c = items['CLAUDE.md']
    assert c['kind'] == 'instructions' and c['ia'] == 'claude'
    assert c['descripcion'] == 'Guía del repo'
    assert c['editable'] is False


# ─── Otras IAs ───────────────────────────────────────────────────────────────

def test_agents_md_raiz_y_anidado_es_de_codex_y_compartido(tmp_path):
    _w(tmp_path, 'AGENTS.md', '# Agentes\n')
    _w(tmp_path, 'backend/AGENTS.md', 'Reglas del backend')
    _w(tmp_path, '.codex/prompts/fix.md', '# Arregla el bug')
    items = _por_path(_det(tmp_path))
    a = items['AGENTS.md']
    assert a['ia'] == 'codex' and a['kind'] == 'instructions'
    assert 'opencode' in a['tambien'] and 'cursor' in a['tambien']
    assert items['backend/AGENTS.md']['descripcion'] == 'Reglas del backend'
    assert items['.codex/prompts/fix.md']['kind'] == 'command'
    assert items['.codex/prompts/fix.md']['nombre'] == '/fix'


def test_gemini_md_y_comandos_toml(tmp_path):
    _w(tmp_path, 'GEMINI.md', '# Contexto Gemini')
    _w(tmp_path, '.gemini/commands/refactor.toml',
       'description = "Refactoriza el módulo"\nprompt = """secreto largo"""\n')
    items = _por_path(_det(tmp_path))
    assert items['GEMINI.md']['ia'] == 'gemini'
    t = items['.gemini/commands/refactor.toml']
    assert t['kind'] == 'command' and t['nombre'] == '/refactor'
    assert t['descripcion'] == 'Refactoriza el módulo'


def test_antigravity_reglas_y_workflows(tmp_path):
    _w(tmp_path, '.agent/rules/estilo.md', '# Estilo de código')
    _w(tmp_path, '.agent/workflows/release.md', '---\ndescription: Publica\n---\n')
    items = _por_path(_det(tmp_path))
    assert items['.agent/rules/estilo.md']['ia'] == 'antigravity'
    assert items['.agent/rules/estilo.md']['kind'] == 'rules'
    assert items['.agent/workflows/release.md']['kind'] == 'command'


def test_cursor_mdc_con_frontmatter_y_cursorrules(tmp_path):
    _w(tmp_path, '.cursor/rules/react.mdc',
       '---\ndescription: Convenciones React\nglobs: src/**/*.tsx\nalwaysApply: false\n---\nbody')
    _w(tmp_path, '.cursorrules', 'Usá TypeScript estricto.')
    items = _por_path(_det(tmp_path))
    r = items['.cursor/rules/react.mdc']
    assert r['ia'] == 'cursor' and r['kind'] == 'rules'
    assert r['descripcion'] == 'Convenciones React'
    assert r['detalle'] == 'src/**/*.tsx'
    assert items['.cursorrules']['descripcion'] == 'Usá TypeScript estricto.'


def test_qwen_opencode_copilot_windsurf_cline_roo(tmp_path):
    _w(tmp_path, 'QWEN.md', '# Qwen')
    _w(tmp_path, '.qwen/commands/doc.md', 'Documenta')
    _w(tmp_path, '.opencode/agent/review.md', '---\ndescription: Revisor\n---\n')
    _w(tmp_path, '.opencode/command/test.md', 'corre tests')
    _w(tmp_path, 'opencode.json', '{"agent": {}, "instructions": ["x.md"], "provider": {"k": "sk-zzz"}}')
    _w(tmp_path, '.github/copilot-instructions.md', '# Copilot')
    _w(tmp_path, '.github/prompts/plan.prompt.md', '---\ndescription: Planifica\n---\n')
    _w(tmp_path, '.github/instructions/py.instructions.md', '---\napplyTo: "**/*.py"\n---\n# Python')
    _w(tmp_path, '.windsurfrules', 'reglas windsurf')
    _w(tmp_path, '.windsurf/rules/a.md', '# A')
    _w(tmp_path, '.clinerules/base.md', '# Base cline')
    _w(tmp_path, '.roo/rules/r.md', '# Roo')
    _w(tmp_path, '.roo/rules-code/c.md', '# Roo code mode')
    items = _por_path(_det(tmp_path))
    assert items['QWEN.md']['ia'] == 'qwen'
    assert items['.qwen/commands/doc.md']['kind'] == 'command'
    assert items['.opencode/agent/review.md']['ia'] == 'opencode'
    assert items['.opencode/agent/review.md']['kind'] == 'agent'
    assert items['.opencode/command/test.md']['kind'] == 'command'
    oc = items['opencode.json']
    assert oc['kind'] == 'config'
    assert 'sk-zzz' not in oc['descripcion']          # solo las claves de primer nivel
    assert 'agent' in oc['descripcion']
    assert items['.github/copilot-instructions.md']['ia'] == 'copilot'
    assert items['.github/prompts/plan.prompt.md']['nombre'] == '/plan'
    ins = items['.github/instructions/py.instructions.md']
    assert ins['kind'] == 'rules' and ins['detalle'] == '**/*.py'
    assert ins['nombre'] == 'py'
    assert items['.windsurfrules']['ia'] == 'windsurf'
    assert items['.windsurf/rules/a.md']['ia'] == 'windsurf'
    assert items['.clinerules/base.md']['ia'] == 'cline'
    assert items['.roo/rules/r.md']['ia'] == 'roo'
    assert items['.roo/rules-code/c.md']['detalle'] == 'modo code'


def test_clinerules_como_archivo(tmp_path):
    _w(tmp_path, '.clinerules', 'Una sola regla')
    items = _por_path(_det(tmp_path))
    assert items['.clinerules']['ia'] == 'cline'
    assert items['.clinerules']['kind'] == 'rules'


# ─── Forma normalizada y resumen ─────────────────────────────────────────────

def test_forma_normalizada(tmp_path):
    _w(tmp_path, 'GEMINI.md', '# G')
    it = _det(tmp_path)[0]
    for k in ('ia', 'ia_label', 'kind', 'nombre', 'path', 'descripcion',
              'editable', 'alcance', 'tambien', 'detalle', 'bytes'):
        assert k in it, k
    assert it['alcance'] == 'proyecto'
    assert it['ia_label'] == 'Gemini CLI'


def test_resumen_por_herramienta(tmp_path):
    _w(tmp_path, 'GEMINI.md', '# G')
    _w(tmp_path, '.claude/skills/a.md', '# A')
    _w(tmp_path, '.claude/skills/b.md', '# B')
    res = sk.resumen(_det(tmp_path))
    by = {h['id']: h for h in res}
    assert by['claude']['total'] == 2
    assert by['claude']['kinds'] == {'skill': 2}
    assert by['gemini']['total'] == 1
    assert res[0]['id'] == 'claude'                       # Claude primero siempre


def test_catalogo_cubre_todas_las_herramientas():
    ids = {h['id'] for h in sk.HERRAMIENTAS}
    assert {'claude', 'codex', 'gemini', 'antigravity', 'cursor', 'qwen',
            'opencode', 'copilot', 'windsurf', 'cline', 'roo'} <= ids
    for h in sk.HERRAMIENTAS:
        assert h['label'] and isinstance(h['lee'], list) and h['lee']


# ─── Seguridad y topes ───────────────────────────────────────────────────────

def test_descripcion_corta_y_sin_cuerpo(tmp_path):
    _w(tmp_path, 'AGENTS.md', 'x' * 5000 + '\nSEGUNDA LINEA')
    it = _por_path(_det(tmp_path))['AGENTS.md']
    assert len(it['descripcion']) <= sk.MAX_DESC
    assert 'SEGUNDA' not in it['descripcion']


def test_redacta_secretos_en_descripcion(tmp_path):
    clave = 'sk-ant-' + 'a1B2c3D4e5F6g7H8i9J0k1L2m3N4'
    _w(tmp_path, 'AGENTS.md', f'# usa la key {clave} para todo')
    it = _por_path(_det(tmp_path))['AGENTS.md']
    assert clave not in it['descripcion']


def test_ignora_node_modules_git_venv(tmp_path):
    _w(tmp_path, 'node_modules/pkg/AGENTS.md', '# no')
    _w(tmp_path, '.git/AGENTS.md', '# no')
    _w(tmp_path, 'venv/lib/CLAUDE.md', '# no')
    _w(tmp_path, 'src/AGENTS.md', '# sí')
    paths = {i['path'] for i in _det(tmp_path)}
    assert paths == {'src/AGENTS.md'}


def test_no_sigue_symlinks_fuera_del_proyecto(tmp_path):
    fuera = tmp_path / 'fuera'
    _w(fuera, 'secret.md', '# SECRETO')
    _w(fuera, 'dir/SKILL.md', '# SECRETO2')
    proj = tmp_path / 'proj'
    os.makedirs(proj / '.claude' / 'skills')
    os.symlink(fuera / 'secret.md', proj / '.claude' / 'skills' / 'leak.md')
    os.symlink(fuera / 'dir', proj / '.claude' / 'skills' / 'leakdir')
    os.symlink(fuera, proj / 'sub')
    _w(fuera, 'AGENTS.md', '# SECRETO3')
    todo = repr(_det(proj))
    assert 'SECRETO' not in todo


def test_symlink_interno_si_se_lista(tmp_path):
    _w(tmp_path, 'docs/reglas.md', '# Reglas internas')
    os.symlink(tmp_path / 'docs' / 'reglas.md', tmp_path / '.cursorrules')
    items = _por_path(_det(tmp_path))
    assert items['.cursorrules']['descripcion'] == 'Reglas internas'


def test_archivo_gigante_se_salta(tmp_path, monkeypatch):
    monkeypatch.setattr(sk, 'MAX_BYTES', 100)
    _w(tmp_path, 'GEMINI.md', '# ' + 'g' * 500)
    _w(tmp_path, 'QWEN.md', '# chico')
    paths = {i['path'] for i in _det(tmp_path)}
    assert paths == {'QWEN.md'}


def test_tope_de_items_por_herramienta(tmp_path, monkeypatch):
    monkeypatch.setattr(sk, 'MAX_POR_IA', 5)
    for n in range(12):
        _w(tmp_path, f'.cursor/rules/r{n:02d}.mdc', f'# r{n}')
    assert len([i for i in _det(tmp_path) if i['ia'] == 'cursor']) == 5


def test_proyecto_vacio_o_inexistente(tmp_path):
    assert _det(tmp_path) == []
    assert sk.detectar(str(tmp_path / 'no-existe')) == []


# ─── Alcance usuario (~) ─────────────────────────────────────────────────────

def test_alcance_usuario_solo_lectura(tmp_path):
    proj = tmp_path / 'p'
    os.makedirs(proj)
    home = tmp_path / 'home'
    _w(home, '.claude/skills/global/SKILL.md', '---\ndescription: Skill global\n---\n')
    _w(home, '.claude/CLAUDE.md', '# Mis reglas')
    _w(home, '.codex/prompts/yo.md', '# Prompt mío')
    _w(home, '.codex/auth.json', '{"token": "sk-no-leer"}')
    _w(home, '.gemini/GEMINI.md', '# Gemini global')
    items = _det(proj, home)
    por = {(i['alcance'], i['path']): i for i in items}
    g = por[('usuario', '~/.claude/skills/global/SKILL.md')]
    assert g['editable'] is False and g['descripcion'] == 'Skill global'
    assert ('usuario', '~/.codex/prompts/yo.md') in por
    assert ('usuario', '~/.gemini/GEMINI.md') in por
    assert ('usuario', '~/.claude/CLAUDE.md') in por
    assert 'sk-no-leer' not in repr(items)
    assert not any('auth.json' in i['path'] for i in items)


def test_home_none_no_lee_usuario(tmp_path):
    _w(tmp_path, 'GEMINI.md', '# g')
    assert all(i['alcance'] == 'proyecto' for i in sk.detectar(str(tmp_path), home=None))


# ─── Lectura de contenido (solo proyecto, solo lo detectado) ─────────────────

def test_leer_contenido_detectado(tmp_path):
    _w(tmp_path, 'GEMINI.md', '# Gemini\ncuerpo')
    assert sk.leer_contenido(str(tmp_path), 'GEMINI.md') == '# Gemini\ncuerpo'


def test_leer_contenido_rechaza_no_detectado_y_escape(tmp_path):
    _w(tmp_path, 'secreto.txt', 'x')
    _w(tmp_path, 'GEMINI.md', '# g')
    assert sk.leer_contenido(str(tmp_path), 'secreto.txt') is None
    assert sk.leer_contenido(str(tmp_path), '../GEMINI.md') is None
    assert sk.leer_contenido(str(tmp_path), '~/.claude/CLAUDE.md') is None


def test_leer_contenido_trunca(tmp_path, monkeypatch):
    monkeypatch.setattr(sk, 'MAX_CONTENIDO', 10)
    _w(tmp_path, 'QWEN.md', 'abcdefghijklmnop')
    assert sk.leer_contenido(str(tmp_path), 'QWEN.md').startswith('abcdefghij')
