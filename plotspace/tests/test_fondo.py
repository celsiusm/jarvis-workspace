"""Tests del fondo personalizado del modo Glass (plotspace/routers/fondo.py).

La imagen vive en un tempdir (FONDO_DIR repuntado); nunca toca data/."""
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest
from fastapi.testclient import TestClient

from plotspace.main import app
import plotspace.routers.fondo as fondo

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setattr(fondo, "FONDO_DIR", tempfile.mkdtemp(prefix="jarvis_fondo_"))
    with TestClient(app) as c:
        yield c


def test_detectar_tipo_por_bytes_magicos():
    assert fondo.detectar_tipo(PNG[:16]) == "image/png"
    assert fondo.detectar_tipo(JPG[:16]) == "image/jpeg"
    assert fondo.detectar_tipo(WEBP[:16]) == "image/webp"
    assert fondo.detectar_tipo(b"<svg xmlns=") is None
    assert fondo.detectar_tipo(b"<html><script>") is None
    assert fondo.detectar_tipo(b"GIF89a") is None
    assert fondo.detectar_tipo(b"") is None


def test_sin_fondo_al_inicio(cli):
    r = cli.get("/api/fondo")
    assert r.status_code == 200 and r.json()["existe"] is False
    assert cli.get("/api/fondo/imagen").status_code == 404


@pytest.mark.parametrize("datos,mime", [(PNG, "image/png"), (JPG, "image/jpeg"), (WEBP, "image/webp")])
def test_subir_y_servir(cli, datos, mime):
    r = cli.put("/api/fondo/imagen", content=datos)
    assert r.status_code == 200
    est = r.json()
    assert est["existe"] is True and est["tipo"] == mime and est["bytes"] == len(datos)
    g = cli.get("/api/fondo/imagen")
    assert g.status_code == 200 and g.content == datos
    assert g.headers["content-type"] == mime
    assert g.headers["x-content-type-options"] == "nosniff"
    assert "immutable" in g.headers["cache-control"]


def test_subir_otra_reemplaza_y_cambia_version(cli):
    v1 = cli.put("/api/fondo/imagen", content=PNG).json()["version"]
    os.utime(fondo._ruta(), ns=(1, 1))            # fuerza otra marca de tiempo
    v2 = cli.put("/api/fondo/imagen", content=JPG).json()
    assert v2["tipo"] == "image/jpeg" and v2["version"] != v1


@pytest.mark.parametrize("basura", [
    b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
    b"<html><script>alert(1)</script></html>",
    b"GIF89a" + b"\x00" * 20,
    b"texto cualquiera",
])
def test_rechaza_formatos_no_soportados(cli, basura):
    r = cli.put("/api/fondo/imagen", content=basura)
    assert r.status_code == 415
    assert cli.get("/api/fondo").json()["existe"] is False


def test_rechaza_vacio(cli):
    assert cli.put("/api/fondo/imagen", content=b"").status_code == 400


def test_rechaza_demasiado_grande(cli, monkeypatch):
    monkeypatch.setattr(fondo, "MAX_BYTES", 100)
    r = cli.put("/api/fondo/imagen", content=PNG + b"\x00" * 200)
    assert r.status_code == 413
    assert cli.get("/api/fondo").json()["existe"] is False


def test_rechazo_no_pisa_el_fondo_vigente(cli):
    cli.put("/api/fondo/imagen", content=PNG)
    assert cli.put("/api/fondo/imagen", content=b"<svg/>").status_code == 415
    assert cli.get("/api/fondo/imagen").content == PNG


def test_borrar(cli):
    cli.put("/api/fondo/imagen", content=PNG)
    r = cli.delete("/api/fondo/imagen")
    assert r.status_code == 200 and r.json()["existe"] is False
    assert cli.get("/api/fondo/imagen").status_code == 404
    assert cli.delete("/api/fondo/imagen").status_code == 200   # idempotente


def test_archivo_corrupto_se_trata_como_inexistente(cli):
    os.makedirs(fondo._dir(), exist_ok=True)
    with open(fondo._ruta(), "wb") as f:
        f.write(b"no soy una imagen")
    assert cli.get("/api/fondo").json()["existe"] is False
    assert cli.get("/api/fondo/imagen").status_code == 404
