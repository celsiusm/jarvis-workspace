# JARVIS — Fondo personalizado del modo Glass.
# Guarda UNA imagen que la persona elige (⚙ → Apariencia → Fondo) y la sirve de
# vuelta para pintarla detrás de franja, barra, dock y terminales. La imagen vive
# en data/fondo/ (gitignored); los ajustes (blur, oscurecer, opacidades) viven en
# el navegador (localStorage, shared/fondo.js) como el resto de Apariencia.
#
# El front ya la entrega achicada y recodificada (canvas → webp/jpeg ≤ 2560px),
# pero este router NO confía en eso: valida el tipo por los bytes mágicos, topa el
# tamaño y nunca sirve SVG/HTML (una imagen subida no puede ejecutar nada).

import os
import tempfile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from plotspace.core.datadir import ruta_data

router = APIRouter(prefix="/api/fondo", tags=["fondo"])

MAX_BYTES = 12 * 1024 * 1024
FONDO_DIR = None          # los tests lo repuntan a un tempdir
_ARCHIVO = "imagen.bin"


def _dir() -> str:
    return FONDO_DIR or ruta_data("fondo")


def _ruta() -> str:
    return os.path.join(_dir(), _ARCHIVO)


def detectar_tipo(cabecera: bytes):
    """Tipo MIME según los bytes mágicos, o None si no es jpeg/png/webp."""
    if cabecera[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if cabecera[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if cabecera[:4] == b"RIFF" and cabecera[8:12] == b"WEBP":
        return "image/webp"
    return None


def _leer_tipo():
    try:
        with open(_ruta(), "rb") as f:
            return detectar_tipo(f.read(16))
    except OSError:
        return None


def _estado():
    ruta = _ruta()
    try:
        st = os.stat(ruta)
    except OSError:
        return {"existe": False, "version": 0, "tipo": None, "bytes": 0}
    tipo = _leer_tipo()
    if not tipo:       # archivo corrupto/ajeno: se trata como inexistente
        return {"existe": False, "version": 0, "tipo": None, "bytes": 0}
    return {"existe": True, "version": int(st.st_mtime_ns // 1_000_000),
            "tipo": tipo, "bytes": st.st_size}


@router.get("")
def estado():
    return _estado()


@router.put("/imagen")
async def subir(request: Request):
    """Cuerpo = los bytes crudos de la imagen (jpeg/png/webp, ≤ 12 MB)."""
    declarado = request.headers.get("content-length")
    if declarado and declarado.isdigit() and int(declarado) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="La imagen pesa más de 12 MB")
    datos = await request.body()
    if len(datos) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="La imagen pesa más de 12 MB")
    if not datos:
        raise HTTPException(status_code=400, detail="Imagen vacía")
    if not detectar_tipo(datos[:16]):
        raise HTTPException(status_code=415, detail="Formato no soportado (usá JPG, PNG o WebP)")
    os.makedirs(_dir(), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=_dir(), prefix=".fondo-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(datos)
        os.replace(tmp, _ruta())
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail="No se pudo guardar la imagen")
    return _estado()


@router.get("/imagen")
def imagen():
    tipo = _leer_tipo()
    if not tipo:
        raise HTTPException(status_code=404, detail="No hay fondo guardado")
    with open(_ruta(), "rb") as f:
        datos = f.read()
    # La URL lleva ?v=<version>: cambia al subir otra imagen → cache largo seguro.
    return Response(content=datos, media_type=tipo, headers={
        "Cache-Control": "private, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    })


@router.delete("/imagen")
def borrar():
    try:
        os.remove(_ruta())
    except FileNotFoundError:
        pass
    except OSError:
        raise HTTPException(status_code=500, detail="No se pudo borrar la imagen")
    return _estado()
