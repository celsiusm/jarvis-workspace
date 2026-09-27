# plotspace/tests/test_review_artefactos.py
"""Review: los archivos nuevos traen su conteo de líneas y cada archivo viene
clasificado (real / artefacto / scratch) para que el panel separe lo que
generan las herramientas de los cambios de verdad."""
import os
import subprocess

import pytest

from plotspace.core import mailbox
from plotspace.routers import review


def _repo(tmp_path):
    def git(*a):
        subprocess.run(['git', *a], cwd=tmp_path, check=True, capture_output=True)
    git('init', '-q')
    (tmp_path / 'app.py').write_text('print(1)\n')
    git('add', '.')
    git('-c', 'user.email=a@b', '-c', 'user.name=t', 'commit', '-qm', 'init')
    return tmp_path


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = _repo(tmp_path)
    monkeypatch.setattr(review, '_ruta_proyecto', lambda pid: str(r))
    monkeypatch.setattr(review, '_duenos_para', lambda pid: [])
    return r


def test_untracked_trae_lineas_nuevas(repo):
    (repo / 'nuevo.py').write_text('a\nb\nc\n')
    data = review.estado_review(1)
    nuevo = next(a for a in data['archivos'] if a['path'] == 'nuevo.py')
    assert nuevo['mas'] == '3'
    assert nuevo['menos'] == '0'


def test_clase_por_archivo(repo):
    (repo / 'app.py').write_text('print(2)\n')
    (repo / '.workspace').mkdir()
    (repo / '.workspace' / 'STATE.md').write_text('x\n')
    data = review.estado_review(1)
    clases = {a['path']: a['clase'] for a in data['archivos']}
    assert clases['app.py'] == 'real'
    assert clases['.workspace/'] == 'artefacto'


def test_by_agent_trae_clase(repo):
    (repo / '.workspace').mkdir()
    (repo / '.workspace' / 'STATE.md').write_text('x\n')
    (repo / 'app.py').write_text('print(3)\n')
    data = review.review_por_agente(1)
    clases = {f['path']: f['clase'] for f in data['sin_atribuir']}
    assert clases['app.py'] == 'real'
    assert clases['.workspace/'] == 'artefacto'


def test_asegurar_mailbox_ignora_workspace(tmp_path):
    mailbox.asegurar_mailbox(str(tmp_path))
    gi = (tmp_path / '.gitignore').read_text()
    assert '.workspace/' in gi.splitlines()
    # idempotente: no la duplica
    mailbox.asegurar_mailbox(str(tmp_path))
    assert (tmp_path / '.gitignore').read_text().splitlines().count('.workspace/') == 1
