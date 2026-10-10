"""El git de FONDO de Jarvis no puede tomar `.git/index.lock`.

`git status` refresca el índice y, si puede, lo reescribe tomando
`index.lock` un instante. Jarvis lo corre solo (STATE.md cada 10 s, Review,
herencia, contexto del orquestador, fotos WIP…): si eso coincide con el
`git add`/`git commit` de un agente, el commit del agente FALLA con
"index.lock exists". `--no-optional-locks` (git ≥ 2.15) hace que estos
comandos no tomen locks opcionales; no cambia lo que devuelven.
"""
import asyncio
import subprocess
import types

FLAG = '--no-optional-locks'


class _Captura:
    def __init__(self):
        self.argvs = []

    def __call__(self, argv, *a, **k):
        self.argvs.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout='' if k.get('text') else b'', stderr='' if k.get('text') else b'')


def _assert_flag(cap):
    assert cap.argvs, 'el helper no llamó a git'
    for argv in cap.argvs:
        assert argv[0] == 'git'
        assert FLAG in argv, f'git sin {FLAG}: {argv}'


def _con_captura(monkeypatch, modulo):
    cap = _Captura()
    monkeypatch.setattr(modulo, 'subprocess', types.SimpleNamespace(
        run=cap, TimeoutExpired=subprocess.TimeoutExpired, PIPE=subprocess.PIPE,
        CompletedProcess=subprocess.CompletedProcess))
    return cap


def test_state_md_run_git(monkeypatch):
    import plotspace.main as main
    cap = _con_captura(monkeypatch, main)
    asyncio.run(main._run_git('/tmp', 'status', '--porcelain'))
    _assert_flag(cap)


def test_system_git(monkeypatch):
    from plotspace.routers import system
    cap = _con_captura(monkeypatch, system)
    system._git('status', '--porcelain')
    _assert_flag(cap)


def test_review_git(monkeypatch):
    from plotspace.routers import review
    cap = _con_captura(monkeypatch, review)
    review._git('/tmp', 'status', '--porcelain')
    _assert_flag(cap)


def test_projects_files_git_run(monkeypatch):
    from plotspace.routers import projects_files
    cap = _con_captura(monkeypatch, projects_files)
    projects_files._git_run('/tmp', 'status', '--porcelain', '-z')
    _assert_flag(cap)


def test_herencia_status(monkeypatch, tmp_path):
    from plotspace.core import herencia
    cap = _con_captura(monkeypatch, herencia)
    herencia._cache.clear()
    (tmp_path / '.git').mkdir()
    herencia.sucios_de(str(tmp_path))
    _assert_flag(cap)


def test_orq_contexto_git(monkeypatch):
    from plotspace.core import orq_contexto
    cap = _con_captura(monkeypatch, orq_contexto)
    orq_contexto._git('/tmp', 'status', '--porcelain')
    _assert_flag(cap)


def test_wip_snapshots_git(monkeypatch):
    from plotspace.core import wip_snapshots
    cap = _con_captura(monkeypatch, wip_snapshots)
    wip_snapshots._git('/tmp', 'status', '--porcelain')
    _assert_flag(cap)
