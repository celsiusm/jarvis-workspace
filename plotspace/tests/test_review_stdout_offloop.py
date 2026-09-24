# plotspace/tests/test_review_stdout_offloop.py
"""Review Room: (1) los warnings de git en stderr NO se parsean como archivos
(solo stdout alimenta porcelain/numstat/name-status); (2) los endpoints corren
FUERA del event loop (def plano → threadpool); (3) el commit tiene timeout
largo (los hooks pueden tardar >15s)."""
import inspect
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

_REAL_RUN = subprocess.run
WARN = "warning: in the working copy of 'fantasma.txt', LF will be replaced by CRLF\n"


def _git(cwd, *args):
    _REAL_RUN(['git', *args], cwd=cwd, check=True,
              stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _client_repo():
    fresh_db()
    d = tempfile.mkdtemp()
    _git(d, 'init', '-q')
    _git(d, 'config', 'user.email', 'test@jarvis.local')
    _git(d, 'config', 'user.name', 'Test')
    with open(os.path.join(d, 'a.txt'), 'w', encoding='utf-8') as f:
        f.write('uno\n')
    _git(d, 'add', 'a.txt')
    _git(d, 'commit', '-q', '-m', 'init')
    app = FastAPI()
    app.include_router(review.router)
    _, pid = make_client_and_project(d)
    return TestClient(app), pid, d


def _run_con_warning(*a, **kw):
    r = _REAL_RUN(*a, **kw)
    return subprocess.CompletedProcess(r.args, r.returncode, r.stdout,
                                       WARN + (r.stderr or ''))


def test_warnings_de_stderr_no_son_archivos(monkeypatch):
    client, pid, d = _client_repo()
    with open(os.path.join(d, 'a.txt'), 'w', encoding='utf-8') as f:
        f.write('uno cambiado\n')
    monkeypatch.setattr(review.subprocess, 'run', _run_con_warning)
    monkeypatch.setattr(review, '_duenos_para', lambda pid: [])

    r = client.get(f"/api/projects/{pid}/review")
    assert r.status_code == 200, r.text
    data = r.json()
    assert [a['path'] for a in data['archivos']] == ['a.txt'], data['archivos']
    assert data['archivos'][0]['mas'] == '1', data['archivos']
    assert 'warning:' not in data['branch'] and 'warning:' not in data['ultimo']

    r = client.get(f"/api/projects/{pid}/review/by-agent")
    assert r.status_code == 200, r.text
    paths = [f['path'] for f in r.json()['sin_atribuir']]
    assert paths == ['a.txt'], paths


def test_endpoints_de_review_no_bloquean_el_loop():
    for fn in (review.review_por_agente, review.commit_por_agente,
               review.estado_review, review.git_diff_archivo,
               review.git_blame_linea, review.review_file):
        assert not inspect.iscoroutinefunction(fn), fn.__name__


def test_commit_usa_timeout_largo(monkeypatch):
    client, pid, d = _client_repo()
    with open(os.path.join(d, 'a.txt'), 'w', encoding='utf-8') as f:
        f.write('cambio\n')
    timeouts = {}

    def _run_espia(cmd, *a, **kw):
        if len(cmd) > 1:
            timeouts.setdefault(cmd[1], kw.get('timeout'))
        return _REAL_RUN(cmd, *a, **kw)
    monkeypatch.setattr(review.subprocess, 'run', _run_espia)

    r = client.post(f"/api/projects/{pid}/review/commit",
                    json={'archivos': ['a.txt'], 'mensaje': 'feat: x'})
    assert r.status_code == 200 and r.json()['ok'] is True, r.text
    assert timeouts['commit'] >= 120, timeouts


def test_commit_fallido_reporta_stdout_y_stderr():
    client, pid, d = _client_repo()
    r = client.post(f"/api/projects/{pid}/review/commit",
                    json={'archivos': ['a.txt'], 'mensaje': 'feat: nada'})
    data = r.json()
    assert data['ok'] is False and data['error'], data
