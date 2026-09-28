"""Endpoints de ⚙ → Extensiones: detección multi-IA + edición de skills.

1. GET /skills/detectadas devuelve items + resumen por IA + catálogo.
2. `usuario=false` no mira el home.
3. GET /skills/detectadas/contenido sólo sirve lo detectado (404 al resto).
4. POST /skills-md sobre una skill en formato carpeta la edita EN SU LUGAR
   (antes creaba un {nombre}.md flat duplicado).
5. Los endpoints viejos siguen respondiendo igual (compatibilidad).
"""
import os
import tempfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plotspace.tests._harness import fresh_db, make_client_and_project
from plotspace.routers import plugins


def _w(base, rel, texto):
    p = os.path.join(base, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(texto)
    return p


def _cliente(monkeypatch):
    fresh_db()
    d = tempfile.mkdtemp()
    home = tempfile.mkdtemp()
    monkeypatch.setenv('HOME', home)
    _, pid = make_client_and_project(d)
    app = FastAPI()
    app.include_router(plugins.router)
    return TestClient(app), pid, d, home


def test_detectadas_items_resumen_y_catalogo(monkeypatch):
    c, pid, d, home = _cliente(monkeypatch)
    _w(d, '.claude/skills/a.md', '# A')
    _w(d, 'AGENTS.md', '# Agentes')
    _w(home, '.codex/prompts/yo.md', '# mío')
    r = c.get(f'/api/projects/{pid}/skills/detectadas')
    assert r.status_code == 200
    data = r.json()
    paths = {i['path'] for i in data['items']}
    assert {'.claude/skills/a.md', 'AGENTS.md', '~/.codex/prompts/yo.md'} <= paths
    ids = [h['id'] for h in data['herramientas']]
    assert ids[0] == 'claude' and 'codex' in ids
    assert any(h['id'] == 'cursor' for h in data['catalogo'])


def test_detectadas_sin_usuario(monkeypatch):
    c, pid, d, home = _cliente(monkeypatch)
    _w(home, '.codex/prompts/yo.md', '# mío')
    r = c.get(f'/api/projects/{pid}/skills/detectadas?usuario=false')
    assert r.json()['items'] == []


def test_contenido_solo_de_lo_detectado(monkeypatch):
    c, pid, d, _ = _cliente(monkeypatch)
    _w(d, 'GEMINI.md', '# Gemini\ncuerpo')
    _w(d, 'otro.txt', 'x')
    ok = c.get(f'/api/projects/{pid}/skills/detectadas/contenido', params={'path': 'GEMINI.md'})
    assert ok.status_code == 200 and ok.json()['content'] == '# Gemini\ncuerpo'
    assert c.get(f'/api/projects/{pid}/skills/detectadas/contenido',
                 params={'path': 'otro.txt'}).status_code == 404
    assert c.get(f'/api/projects/{pid}/skills/detectadas/contenido',
                 params={'path': '../../etc/passwd'}).status_code == 404


def test_detectadas_proyecto_inexistente(monkeypatch):
    c, _, _, _ = _cliente(monkeypatch)
    assert c.get('/api/projects/9999/skills/detectadas').status_code == 404


def test_guardar_skill_carpeta_edita_en_su_lugar(monkeypatch):
    c, pid, d, _ = _cliente(monkeypatch)
    sm = _w(d, '.claude/skills/deploy/SKILL.md', '# viejo')
    r = c.post(f'/api/projects/{pid}/skills-md', json={'nombre': 'deploy', 'content': '# nuevo'})
    assert r.status_code == 200
    assert r.json()['path'] == os.path.join('.claude', 'skills', 'deploy', 'SKILL.md')
    assert open(sm, encoding='utf-8').read() == '# nuevo'
    assert not os.path.exists(os.path.join(d, '.claude', 'skills', 'deploy.md'))


def test_guardar_skill_nueva_sigue_siendo_flat(monkeypatch):
    c, pid, d, _ = _cliente(monkeypatch)
    r = c.post(f'/api/projects/{pid}/skills-md', json={'nombre': 'nueva', 'content': '# x'})
    assert r.status_code == 200
    assert os.path.isfile(os.path.join(d, '.claude', 'skills', 'nueva.md'))
    lista = c.get(f'/api/projects/{pid}/skills-md').json()
    assert [s['nombre'] for s in lista['skills']] == ['nueva']
    assert c.get(f'/api/projects/{pid}/skills-md/nueva').json()['content'] == '# x'
