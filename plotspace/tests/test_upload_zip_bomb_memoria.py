# plotspace/tests/test_upload_zip_bomb_memoria.py
"""Anti zip-bomb en MEMORIA: un miembro cuyo header declara más que el tope se
rechaza SIN descomprimirlo, y el upload-zip no lee más de MAX_UPLOAD_TOTAL."""
import io
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from plotspace.tests._harness import fresh_db, make_client_and_project
from plotspace.routers import projects_files as pf


def _zip_bytes(miembros):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for nombre, data in miembros.items():
            z.writestr(nombre, data)
    return buf.getvalue()


def test_miembro_grande_se_rechaza_sin_descomprimir(monkeypatch):
    fresh_db()
    d = tempfile.mkdtemp()
    client, pid = make_client_and_project(d)
    monkeypatch.setattr(pf, "MAX_UPLOAD_SIZE", 1024)

    descomprimido = {"n": 0}
    orig_read = zipfile.ZipExtFile.read

    def _read_contado(self, n=-1):
        data = orig_read(self, n)
        descomprimido["n"] += len(data)
        return data
    monkeypatch.setattr(zipfile.ZipExtFile, "read", _read_contado)

    zb = _zip_bytes({"grande.bin": b"\x00" * (512 * 1024), "chico.txt": "hola"})
    r = client.post(f"/api/projects/{pid}/files/upload-zip",
                    files=[("file", ("b.zip", zb, "application/zip"))])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["subidos"] == ["b/chico.txt"], body
    assert any("demasiado grande" in x["motivo"] for x in body["rechazados"]), body
    # Solo se descomprimió el chico: el grande se rechazó por su header.
    assert descomprimido["n"] < 1024, descomprimido


def test_total_declarado_supera_tope_corta_antes_de_leer(monkeypatch):
    fresh_db()
    d = tempfile.mkdtemp()
    client, pid = make_client_and_project(d)
    monkeypatch.setattr(pf, "MAX_UNZIP_TOTAL", 1500)
    monkeypatch.setattr(pf, "MAX_ZIP_RATIO", 10 ** 9)

    descomprimido = {"n": 0}
    orig_read = zipfile.ZipExtFile.read

    def _read_contado(self, n=-1):
        data = orig_read(self, n)
        descomprimido["n"] += len(data)
        return data
    monkeypatch.setattr(zipfile.ZipExtFile, "read", _read_contado)

    zb = _zip_bytes({"a.txt": "x" * 1000, "b.txt": "y" * 1000})
    r = client.post(f"/api/projects/{pid}/files/upload-zip",
                    files=[("file", ("z.zip", zb, "application/zip"))])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["subidos"] == ["z/a.txt"], body
    assert any("supera" in x["motivo"] for x in body["rechazados"]), body
    assert descomprimido["n"] <= 1000, descomprimido


def test_upload_zip_mayor_al_total_da_413(monkeypatch):
    fresh_db()
    d = tempfile.mkdtemp()
    client, pid = make_client_and_project(d)
    monkeypatch.setattr(pf, "MAX_UPLOAD_TOTAL", 100)

    zb = _zip_bytes({"a.txt": os.urandom(400).hex()})
    assert len(zb) > 100
    r = client.post(f"/api/projects/{pid}/files/upload-zip",
                    files=[("file", ("z.zip", zb, "application/zip"))])
    assert r.status_code == 413, r.text
    assert not os.path.exists(os.path.join(d, "z"))
