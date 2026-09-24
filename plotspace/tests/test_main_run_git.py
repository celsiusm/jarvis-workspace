# plotspace/tests/test_main_run_git.py
"""main._run_git (STATE.md cada 10s): git por subprocess.run en un thread
(regla CLAUDE.md: nunca asyncio.create_subprocess_exec para git) y CON timeout
— un repo trabado no puede colgar el loop de STATE.md para siempre."""
import asyncio
import os
import subprocess
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import plotspace.main as main_mod


def _prohibido(*a, **kw):
    raise AssertionError('asyncio.create_subprocess_exec prohibido para git')


def test_run_git_usa_subprocess_run_con_timeout(monkeypatch):
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', _prohibido)
    vistos = {}
    real = subprocess.run

    def _espia(cmd, *a, **kw):
        vistos['timeout'] = kw.get('timeout')
        return real(cmd, *a, **kw)
    monkeypatch.setattr(main_mod.subprocess, 'run', _espia)

    d = tempfile.mkdtemp()
    real(['git', 'init', '-q'], cwd=d, check=True)
    open(os.path.join(d, 'nuevo.txt'), 'w').close()
    rc, out = asyncio.run(main_mod._run_git(d, 'status', '--porcelain'))
    assert rc == 0, out
    assert '?? nuevo.txt' in out
    assert vistos['timeout'] and vistos['timeout'] <= 30, vistos


def test_run_git_timeout_no_explota(monkeypatch):
    def _cuelga(cmd, *a, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get('timeout'))
    monkeypatch.setattr(main_mod.subprocess, 'run', _cuelga)
    rc, out = asyncio.run(main_mod._run_git(tempfile.mkdtemp(), 'status'))
    assert rc == -1
    assert 'timeout' in out
