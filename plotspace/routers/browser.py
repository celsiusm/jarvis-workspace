"""JARVIS — Browser server-side: WebSocket `/ws/browser/{sid}`.

Puente entre el panel del Browser (frontend) y una `Sesion` de Chromium
(`core/remote_browser.py`). El cliente manda acciones (navegar, mouse, teclado,
resize) y recibe por el mismo socket los frames del screencast y los cambios de
URL/título. Ver el módulo del motor para el POR QUÉ y las reglas de seguridad.
"""
import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from plotspace.core import auth as jarvis_auth
from plotspace.core import remote_browser as rb

router = APIRouter(tags=['browser'])


@router.websocket('/ws/browser/{sid}')
async def ws_browser(websocket: WebSocket, sid: str):
    # El middleware http NO corre para websockets: Origin anti CSWSH.
    if not jarvis_auth.origen_permitido(websocket.headers.get('origin'),
                                        jarvis_auth.hosts_extra()):
        await websocket.close(code=4403)
        return
    await websocket.accept()

    ancho = websocket.query_params.get('w')
    alto = websocket.query_params.get('h')
    try:
        ses = await rb.Sesion.crear(
            int(ancho) if ancho and ancho.isdigit() else 1280,
            int(alto) if alto and alto.isdigit() else 800)
    except Exception as e:
        await websocket.send_json({'t': 'err', 'msg': f'no se pudo abrir el browser: {e}'})
        await websocket.close()
        return

    try:
        await ses.iniciar_screencast()
    except Exception as e:
        await websocket.send_json({'t': 'err', 'msg': f'screencast: {e}'})

    async def leer_cliente():
        try:
            async for msg in websocket.iter_json():
                await ses.input(msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception:
            pass

    tarea = asyncio.create_task(leer_cliente())
    try:
        while True:
            item = await ses.cola.get()
            await websocket.send_json(item)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:
        pass
    finally:
        tarea.cancel()
        try:
            await asyncio.wait_for(ses.cerrar(), timeout=10)
        except Exception:
            pass
