# plotspace/tests/test_memoria_crear_contrato.py
"""POST /memory (crear desde la UI) escribe un frontmatter que pasa el guard
de admisión (scripts/guard_memoria.py): categoria, resumen, actualizado,
estado. Y un título/tag con saltos de línea NO inyecta claves al frontmatter."""
import os
import re
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, 'scripts'))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import guard_memoria as gm
from plotspace.tests._harness import fresh_db, make_client_and_project
from plotspace.routers import memory

_FRONT_RE = re.compile(r'^---\s*\n(.*?)\n---\s*\n?', re.DOTALL)


def _cliente():
    fresh_db()
    d = tempfile.mkdtemp()
    _, pid = make_client_and_project(d)
    app = FastAPI()
    app.include_router(memory.router)
    return TestClient(app), pid, d


def _leer(d, slug):
    with open(os.path.join(d, '.jarvis', 'memory', slug + '.md'), encoding='utf-8') as f:
        return f.read()


def test_memoria_creada_pasa_el_guard():
    client, pid, d = _cliente()
    r = client.post(f"/api/projects/{pid}/memory", json={
        'titulo': 'Xterm pierde el foco al maximizar',
        'contenido': '\n\n  El canvas se re-crea y hay que refitear.\nDetalle extra.\n',
        'tags': ['terminal', 'xterm'],
    })
    assert r.status_code == 201, r.text
    slug = r.json()['slug']
    src = _leer(d, slug)
    assert gm.validar(slug, src, True) == [], src
    front = _FRONT_RE.match(src).group(1)
    campos = dict(l.split(':', 1) for l in front.splitlines())
    assert campos['resumen'].strip() == 'El canvas se re-crea y hay que refitear.'
    assert campos['categoria'].strip() == 'terminales', front
    assert campos['estado'].strip() == 'vigente'
    assert campos['actualizado'].strip() == campos['creado'].strip()


def test_titulo_y_tags_con_salto_no_inyectan_frontmatter():
    client, pid, d = _cliente()
    r = client.post(f"/api/projects/{pid}/memory", json={
        'titulo': 'Algo\nestado: lapida',
        'contenido': 'cuerpo',
        'tags': ['ui\ncategoria: desktop', 'ok'],
    })
    assert r.status_code == 201, r.text
    src = _leer(d, r.json()['slug'])
    front = _FRONT_RE.match(src).group(1)
    claves = [l.split(':', 1)[0].strip() for l in front.splitlines()]
    assert claves.count('estado') == 1 and claves.count('categoria') == 1, front
    assert 'estado: vigente' in front
    for l in front.splitlines():
        assert not l.startswith('estado: lapida') and not l.startswith('categoria: desktop'), front


def test_memoria_sin_tags_pasa_el_guard():
    # La UI puede crear una memoria sin tags: `tags: []` también lo bloquea el
    # guard, así que la categoría inferida queda como tag mínimo.
    client, pid, d = _cliente()
    r = client.post(f"/api/projects/{pid}/memory", json={
        'titulo': 'Nota sin etiquetas', 'contenido': 'algo que saber', 'tags': [],
    })
    assert r.status_code == 201, r.text
    slug = r.json()['slug']
    assert gm.validar(slug, _leer(d, slug), True) == []
