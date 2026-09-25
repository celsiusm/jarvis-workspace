# Contratos del "modo app" (shell de escritorio) — Hito 1 del Escalón A.
#
# El shell lanza el backend con JARVIS_PORT propio y se loguea solo vía
# GET /login?token=<token> (lee el token del data dir y abre el webview ya
# autenticado). Estos tests fijan ese contrato: si alguien lo rompe, el shell
# de escritorio deja de poder abrir la app.

import importlib
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))


def test_puerto_jarvis_sigue_env_en_dev_detect():
    """dev_detect excluye al puerto REAL de Jarvis (no al 3000 fijo) de la
    detección de dev servers y los demos /static."""
    import plotspace.core.dev_detect as dd
    previo = os.environ.get('JARVIS_PORT')
    try:
        os.environ['JARVIS_PORT'] = '5432'
        assert importlib.reload(dd).PUERTO_JARVIS == 5432
        os.environ.pop('JARVIS_PORT', None)
        assert importlib.reload(dd).PUERTO_JARVIS == 3000
    finally:
        if previo is None:
            os.environ.pop('JARVIS_PORT', None)
        else:
            os.environ['JARVIS_PORT'] = previo
        importlib.reload(dd)


def test_contrato_auto_login_del_shell():
    """GET /login (con o sin query vieja) → redirect a '/'."""
    from fastapi.testclient import TestClient

    import plotspace.main as main

    client = TestClient(main.app)
    ok = client.get('/login', follow_redirects=False)
    assert ok.status_code == 302
    assert ok.headers['location'] == '/'
    legacy = client.get('/login', params={'token': 'ignored'}, follow_redirects=False)
    assert legacy.status_code == 302
    assert legacy.headers['location'] == '/'
