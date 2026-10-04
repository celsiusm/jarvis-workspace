# JARVIS — Cuentas de CLIs.
# Vincular múltiples cuentas por agente de IA (claude/codex/qwen/opencode)
# y switchear la activa sin re-loguear. La lógica sensible (snapshot/restore de
# los archivos de credencial del HOME) vive en plotspace/core/cli_accounts.py; este
# router es la capa HTTP fina.
# de main.py (todo /api/* lo está). Ver CLAUDE.md → sección Cuentas.

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from plotspace.core import cli_accounts as ca
from plotspace.core import cuenta_watch as cw
from plotspace.core import cli_login
from plotspace.core import uso_suscripcion

router = APIRouter(prefix="/api/cuentas", tags=["cuentas"])

# Watchers de alta de cuenta vivos (uno por tipo, re-disparable). Efímeros (~5 min):
# detectan la cuenta nueva tras el login y se autolimpian. Ver plotspace/core/cuenta_watch.py.
_watchers: dict = {}


class CrearCuenta(BaseModel):
    tipo: str
    label: str = ""


class RenombrarCuenta(BaseModel):
    label: str


class IniciarLogin(BaseModel):
    tipo: str
    label: str = ""


class CodigoLogin(BaseModel):
    tipo: str
    codigo: str


class CancelarLogin(BaseModel):
    tipo: str


async def _avisar():
    """Notifica a los clientes abiertos que cambió el estado de cuentas."""
    try:
        from plotspace.core.events import broadcaster
        await broadcaster.broadcast_global({"type": "cuentas_update"})
    except Exception:
        pass  # el aviso es best-effort; nunca debe romper la operación


@router.get("")
def estado():
    return ca.estado()


@router.get("/diagnostico/{tipo}")
def diagnostico(tipo: str):
    """Por qué Jarvis ve o no la sesión de un CLI: rutas que mira, si existen, claves (nunca
    valores) y pistas. Solo lectura."""
    try:
        return ca.diagnostico(tipo)
    except ca.TipoDesconocido:
        raise HTTPException(status_code=400, detail="Tipo de CLI desconocido")


@router.get("/uso")
async def uso(refrescar: bool = False):
    """Cuánto le queda a la suscripción de cada cuenta (claude/codex).
    Caché de 60 s; `?refrescar=1` la saltea."""
    return {"cuentas": await uso_suscripcion.uso_todas(forzar=refrescar)}


@router.post("")
async def crear(body: CrearCuenta):
    try:
        perfil = ca.capturar_actual(body.tipo, body.label)
    except ca.TipoDesconocido:
        raise HTTPException(status_code=400, detail="Tipo de CLI desconocido")
    except ca.NoLogueado:
        raise HTTPException(
            status_code=409,
            detail="No hay una cuenta logueada para capturar. Logueate primero "
                   "en una terminal de ese CLI.",
        )
    await _avisar()
    return perfil


@router.post("/{account_id}/usar")
async def usar(account_id: int):
    try:
        perfil = ca.usar(account_id)
    except ca.PerfilNoEncontrado:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")
    await _avisar()
    return perfil


@router.post("/{account_id}/recapturar")
async def recapturar(account_id: int):
    try:
        perfil = ca.recapturar(account_id)
    except ca.PerfilNoEncontrado:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")
    except ca.NoLogueado:
        raise HTTPException(
            status_code=409,
            detail="No hay una cuenta logueada en el HOME para re-capturar.",
        )
    await _avisar()
    return perfil


@router.put("/{account_id}")
async def renombrar(account_id: int, body: RenombrarCuenta):
    try:
        perfil = ca.renombrar(account_id, body.label)
    except ca.PerfilNoEncontrado:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")
    await _avisar()
    return perfil


@router.delete("/{account_id}")
async def eliminar(account_id: int):
    try:
        ca.eliminar(account_id)
    except ca.PerfilNoEncontrado:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")
    await _avisar()
    return {"ok": True}


# ── Alta de cuenta nueva: "abrir la página de login + detectar solo" ────────
# SIN terminal: el backend corre el login del CLI en un PTY OCULTO (cli_login),
# captura la URL de autorización y la devuelve para que el front la abra directo
# en el navegador. Para los CLIs de "pegar código" (Claude), el usuario pega el
# código con POST /login/codigo. Un watcher detecta la cuenta nueva y la guarda,
# avisando por WS `cuenta_agregada`.

@router.post("/login/iniciar")
async def iniciar_login(body: IniciarLogin):
    tipo = body.tipo
    if tipo not in ca.TIPOS:
        raise HTTPException(status_code=400, detail="Tipo de CLI desconocido")
    if tipo not in cli_login.LOGIN_ARGV and tipo not in cli_login.LOGIN_MANUAL:
        raise HTTPException(status_code=400, detail="Ese CLI no tiene login web")
    # cancelar un watcher previo del mismo tipo (re-disparo limpio)
    prev = _watchers.pop(tipo, None)
    if prev and not prev.done():
        prev.cancel()
    # snapshot del estado ANTES del login (cli_accounts es síncrono → thread)
    email_previo = None
    if await asyncio.to_thread(ca.esta_logueado, tipo):
        email_previo = await asyncio.to_thread(ca.email_actual, tipo)
    huella_previa = await asyncio.to_thread(cw._huella, tipo)
    # preservar la sesión activa actual antes de que el login la pise (best-effort)
    await asyncio.to_thread(cw.preservar_sesion_actual, tipo)
    conocidos = await asyncio.to_thread(cw.emails_conocidos, tipo)

    if tipo in cli_login.LOGIN_MANUAL:
        # Login de wizard interactivo (qwen/opencode): no hay PTY oculto — el
        # usuario corre el comando en una terminal y el watcher detecta la
        # credencial nueva en el HOME y la guarda sola (mismo canal WS).
        _watchers[tipo] = asyncio.create_task(
            _watch_y_emitir(tipo, conocidos, email_previo, huella_previa, body.label))
        return {"ok": True, "tipo": tipo, "manual": True,
                "comando": cli_login.LOGIN_MANUAL[tipo], "email_previo": email_previo}

    # spawnear el login en un PTY oculto y capturar la URL de la página de la IA
    # (+ el device-code si el CLI usa device-auth, p.ej. codex).
    url, paste, codigo = await cli_login.iniciar(tipo)
    if not url:
        raise HTTPException(status_code=502,
                            detail="No se pudo obtener la página de login del CLI")
    _watchers[tipo] = asyncio.create_task(
        _watch_y_emitir(tipo, conocidos, email_previo, huella_previa, body.label))
    return {"ok": True, "tipo": tipo, "url": url, "paste": paste, "codigo": codigo,
            "email_previo": email_previo}


@router.post("/login/cancelar")
async def login_cancelar(body: CancelarLogin):
    """Corta el alta en curso: cancela el watcher — su handler cierra el PTY de
    login y, para antigravity, repone el token apartado. Lo llama el front al
    cerrar el modal de alta (solo flujos web); sin esto un login abandonado
    dejaba el PTY oculto vivo (y la sesión de agy apartada) hasta el timeout de
    5 min del watcher."""
    prev = _watchers.pop(body.tipo, None)
    if prev and not prev.done():
        prev.cancel()
        return {"ok": True, "cancelado": True}
    cli_login.cerrar(body.tipo)   # por si quedó un PTY colgado sin watcher
    return {"ok": True, "cancelado": False}


@router.post("/login/codigo")
async def login_codigo(body: CodigoLogin):
    """Pasa el código de autorización (el que la IA le dio al usuario) al login en
    curso. Lo usan los CLIs de flujo "pegar código" (Claude)."""
    ok = await cli_login.enviar_codigo(body.tipo, body.codigo)
    if not ok:
        raise HTTPException(status_code=409, detail="No hay un login en curso para ese CLI")
    return {"ok": True}


async def _watch_y_emitir(tipo, conocidos, email_previo, huella_previa, label):
    """Vive el task del watcher y emite el resultado por WS global. Nunca crashea
    (try/except) — patrón de los background tasks del repo."""
    try:
        if tipo == "codex":
            # Codex va por un watcher dedicado: el login escribe en un staging dir
            # AISLADO (no en ~/.codex ni en el home de la activa), y se adopta en un
            # dir de cuenta propio al detectarlo. Ver cuenta_watch.vigilar_login_codex_nuevo.
            perfil = await cw.vigilar_login_codex_nuevo(conocidos, label)
        else:
            perfil = await cw.vigilar_login(tipo, conocidos, email_previo, huella_previa, label)
    except asyncio.CancelledError:
        cli_login.cerrar(tipo)
        raise
    except Exception as e:
        print(f"[cuenta-watch] error vigilando {tipo}: {e}")
        perfil = None
    finally:
        _watchers.pop(tipo, None)
        cli_login.cerrar(tipo)   # cerrar el PTY de login (terminó o venció)
    try:
        from plotspace.core.events import broadcaster
        if perfil:
            await broadcaster.broadcast_global({
                "type": "cuenta_agregada", "tipo": tipo,
                "account_id": perfil["id"], "email": perfil["email"],
                "label": perfil["label"],
            })
        else:
            await broadcaster.broadcast_global({"type": "cuenta_watch_timeout", "tipo": tipo})
    except Exception:
        pass
