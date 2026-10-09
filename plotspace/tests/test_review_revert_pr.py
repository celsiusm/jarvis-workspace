# plotspace/tests/test_review_revert_pr.py
"""Review v2 (plotspace/routers/review.py): descartar cambios por archivo y
abrir un PR de la rama actual.

Repos git temporales reales; el remoto 'origin' es un repo bare local y `gh`
se reemplaza por el seam `review._gh` (monkeypatch), así nada sale a la red.
"""
import os
import subprocess
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plotspace.tests._harness import fresh_db, make_client_and_project
from plotspace.routers import review


def _git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def _escribir(d, rel, texto):
    full = os.path.join(d, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'w', encoding='utf-8') as f:
        f.write(texto)


def _leer(d, rel):
    with open(os.path.join(d, rel), encoding='utf-8') as f:
        return f.read()


def _client_repo(con_origin=False):
    fresh_db()
    d = tempfile.mkdtemp()
    _git(d, 'init', '-q', '-b', 'main')
    _git(d, 'config', 'user.email', 'test@jarvis.local')
    _git(d, 'config', 'user.name', 'Test')
    _escribir(d, 'a.txt', 'uno\n')
    _escribir(d, 'b.txt', 'dos\n')
    _git(d, 'add', 'a.txt', 'b.txt')
    _git(d, 'commit', '-q', '-m', 'init')
    remoto = None
    if con_origin:
        remoto = tempfile.mkdtemp()
        _git(remoto, 'init', '-q', '--bare')
        _git(d, 'remote', 'add', 'origin', remoto)
        _git(d, 'push', '-q', 'origin', 'main')
    app = FastAPI()
    app.include_router(review.router)
    _, pid = make_client_and_project(d)
    return TestClient(app), pid, d, remoto


def _porcelain(d):
    return _git(d, 'status', '--porcelain')


# ─── POST /review/revert ──────────────────────────────────────────────────────

def test_revert_solo_los_archivos_indicados():
    client, pid, d, _ = _client_repo()
    _escribir(d, 'a.txt', 'uno cambiado\n')
    _escribir(d, 'b.txt', 'dos cambiado\n')
    r = client.post(f"/api/projects/{pid}/review/revert", json={'archivos': ['a.txt']})
    assert r.status_code == 200, r.text
    assert r.json()['ok'] is True, r.json()
    assert _leer(d, 'a.txt') == 'uno\n'
    # b.txt (trabajo de otro) queda intacto
    assert _leer(d, 'b.txt') == 'dos cambiado\n'
    print('  revert acotado a los archivos indicados OK')


def test_revert_staged_y_borrado():
    client, pid, d, _ = _client_repo()
    _escribir(d, 'a.txt', 'staged\n')
    _git(d, 'add', 'a.txt')
    os.remove(os.path.join(d, 'b.txt'))
    r = client.post(f"/api/projects/{pid}/review/revert",
                    json={'archivos': ['a.txt', 'b.txt']})
    assert r.json()['ok'] is True, r.json()
    assert _leer(d, 'a.txt') == 'uno\n' and _leer(d, 'b.txt') == 'dos\n'
    assert _porcelain(d) == ''
    print('  revert de staged + archivo borrado OK')


def test_revert_untracked_y_nuevo_en_indice():
    client, pid, d, _ = _client_repo()
    _escribir(d, 'nuevo.txt', 'x\n')
    _escribir(d, 'dir/f.txt', 'y\n')
    _escribir(d, 'agregado.txt', 'z\n')
    _git(d, 'add', 'agregado.txt')
    _escribir(d, 'queda.txt', 'otro agente\n')
    r = client.post(f"/api/projects/{pid}/review/revert",
                    json={'archivos': ['nuevo.txt', 'dir/', 'agregado.txt']})
    data = r.json()
    assert data['ok'] is True, data
    assert not os.path.exists(os.path.join(d, 'nuevo.txt'))
    assert not os.path.exists(os.path.join(d, 'dir'))
    assert not os.path.exists(os.path.join(d, 'agregado.txt'))
    assert _porcelain(d) == '?? queda.txt\n'
    print('  revert de untracked + agregado al índice OK')


def test_revert_rename_porcelain():
    client, pid, d, _ = _client_repo()
    _git(d, 'mv', 'a.txt', 'c.txt')
    r = client.post(f"/api/projects/{pid}/review/revert",
                    json={'archivos': ['a.txt -> c.txt']})
    assert r.json()['ok'] is True, r.json()
    assert _leer(d, 'a.txt') == 'uno\n'
    assert not os.path.exists(os.path.join(d, 'c.txt'))
    assert _porcelain(d) == ''
    print('  revert de rename (viejo -> nuevo) OK')


def test_revert_traversal_y_vacio_400():
    client, pid, d, _ = _client_repo()
    r = client.post(f"/api/projects/{pid}/review/revert", json={'archivos': ['../x']})
    assert r.status_code == 400
    r = client.post(f"/api/projects/{pid}/review/revert", json={'archivos': []})
    assert r.status_code == 400
    print('  revert rechaza traversal y lista vacía OK')


def test_revert_archivo_sin_cambios_ok_false():
    client, pid, d, _ = _client_repo()
    r = client.post(f"/api/projects/{pid}/review/revert", json={'archivos': ['nada.txt']})
    assert r.status_code == 200
    assert r.json()['ok'] is False
    print('  revert de un path inexistente ok:false OK')


# ─── POST /review/pr ──────────────────────────────────────────────────────────

def _con_gh(fake):
    orig = review._gh
    review._gh = fake
    return orig


def test_pr_en_rama_base_400():
    client, pid, d, _ = _client_repo(con_origin=True)
    r = client.post(f"/api/projects/{pid}/review/pr", json={})
    assert r.status_code == 400
    assert 'main' in r.json()['detail']
    print('  PR en la rama base rechazado OK')


def test_pr_sin_origin_400():
    client, pid, d, _ = _client_repo()
    _git(d, 'checkout', '-q', '-b', 'feat/x')
    r = client.post(f"/api/projects/{pid}/review/pr", json={'base': 'main'})
    assert r.status_code == 400
    print('  PR sin remoto origin rechazado OK')


def test_pr_pushea_y_crea():
    client, pid, d, remoto = _client_repo(con_origin=True)
    _git(d, 'checkout', '-q', '-b', 'feat/x')
    _escribir(d, 'a.txt', 'cambio\n')
    _git(d, 'commit', '-q', '-am', 'feat: x')
    llamadas = []

    def fake(cwd, *args):
        llamadas.append(args)
        return 0, 'https://github.com/o/r/pull/7\n', ''
    orig = _con_gh(fake)
    try:
        r = client.post(f"/api/projects/{pid}/review/pr", json={})
    finally:
        review._gh = orig
    data = r.json()
    assert data == {'ok': True, 'url': 'https://github.com/o/r/pull/7',
                    'existente': False, 'rama': 'feat/x', 'base': 'main'}, data
    # la rama llegó al remoto
    assert _git(remoto, 'rev-parse', 'feat/x').strip() == _git(d, 'rev-parse', 'HEAD').strip()
    assert llamadas == [('pr', 'create', '--base', 'main', '--head', 'feat/x', '--fill')]
    print('  PR: push + gh pr create --fill OK')


def test_pr_con_titulo_y_existente():
    client, pid, d, _ = _client_repo(con_origin=True)
    _git(d, 'checkout', '-q', '-b', 'feat/y')
    llamadas = []

    def fake(cwd, *args):
        llamadas.append(args)
        return 1, '', 'a pull request for branch "feat/y" into branch "main" already exists:\nhttps://github.com/o/r/pull/3\n'
    orig = _con_gh(fake)
    try:
        r = client.post(f"/api/projects/{pid}/review/pr",
                        json={'titulo': 'feat: y', 'cuerpo': 'detalle'})
    finally:
        review._gh = orig
    data = r.json()
    assert data['ok'] is True and data['existente'] is True, data
    assert data['url'] == 'https://github.com/o/r/pull/3'
    assert llamadas[0][-4:] == ('--title', 'feat: y', '--body', 'detalle')
    print('  PR ya existente devuelve su URL OK')


def test_pr_gh_falla_ok_false():
    client, pid, d, _ = _client_repo(con_origin=True)
    _git(d, 'checkout', '-q', '-b', 'feat/z')
    orig = _con_gh(lambda cwd, *a: (-1, '', 'gh no está instalado'))
    try:
        r = client.post(f"/api/projects/{pid}/review/pr", json={})
    finally:
        review._gh = orig
    data = r.json()
    assert data['ok'] is False and 'gh' in data['error'], data
    print('  PR: error de gh → ok:false OK')


def test_estado_review_informa_base():
    client, pid, d, _ = _client_repo(con_origin=True)
    _escribir(d, 'a.txt', 'cambio\n')
    r = client.get(f"/api/projects/{pid}/review")
    assert r.status_code == 200, r.text
    assert r.json()['base'] == 'main' and r.json()['branch'] == 'main'
    print('  GET /review informa la rama base OK')


if __name__ == '__main__':
    test_revert_solo_los_archivos_indicados()
    test_revert_staged_y_borrado()
    test_revert_untracked_y_nuevo_en_indice()
    test_revert_rename_porcelain()
    test_revert_traversal_y_vacio_400()
    test_revert_archivo_sin_cambios_ok_false()
    test_pr_en_rama_base_400()
    test_pr_sin_origin_400()
    test_pr_pushea_y_crea()
    test_pr_con_titulo_y_existente()
    test_pr_gh_falla_ok_false()
    test_estado_review_informa_base()
    print('OK')
