"""La ruta GET /editor sirve la página standalone del editor.

La página HTML NO está detrás del token-gate (el middleware de main.py sólo
protege /api/*, igual que /workspace y /). La auth ocurre cuando el JS de la
página hace fetch a /api/.... Por eso este test sólo
verifica que la ruta exista y devuelva el HTML esperado, sin cookie.

Patrón: importamos la app completa (backend.main) con la DB repuntada a un
tempfile vía fresh_db(). TestClient sin context-manager NO corre los eventos de
startup (Whisper, tmux, pollers), así que el import es liviano.
"""
import os
import sys

# Permitir correr el test como script suelto desde la raíz (mismo patrón que
# _harness.py / test_harness_smoke.py).
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi.testclient import TestClient

from plotspace.tests._harness import fresh_db


def _client() -> TestClient:
    fresh_db()  # repunta database.DB_PATH a un tempfile antes de importar la app
    import plotspace.main as main
    return TestClient(main.app)


def test_editor_standalone_sirve_html():
    client = _client()
    r = client.get("/editor")
    assert r.status_code == 200, f"status {r.status_code}: {r.text[:200]}"
    assert "panel-editor" in r.text
    assert "editor.js" in r.text


if __name__ == "__main__":
    test_editor_standalone_sirve_html()
    print("OK")
