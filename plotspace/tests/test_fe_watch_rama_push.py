"""El auto-push de fe_watch tiene que ir a la rama que existe en origin.

QUÉ PASÓ (2026-10-09)
=====================
`_push_origen` hacía `git push origin master`, pero la rama principal del repo
es `main` (origin no tiene `master`). Con el checkout en `main`, git respondía
«src refspec master does not match any» en cada commit y el backup a GitHub
nunca ocurría — fallaba en silencio, solo un print en el log del server.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plotspace.core import fe_watch


def _capturar_push(monkeypatch):
    llamadas = []

    def falso_run(cmd, **kw):
        llamadas.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, '', '')

    monkeypatch.setattr(subprocess, 'run', falso_run)
    assert fe_watch._push_origen() is True
    assert len(llamadas) == 1
    return llamadas[0]


def test_pushea_a_main(monkeypatch):
    cmd = _capturar_push(monkeypatch)
    assert cmd[-3:] == ['push', 'origin', 'main']


def test_nunca_a_master(monkeypatch):
    cmd = _capturar_push(monkeypatch)
    assert 'master' not in cmd


def test_nunca_force(monkeypatch):
    cmd = _capturar_push(monkeypatch)
    assert not any(a.startswith('-f') or a.startswith('--force') for a in cmd)
