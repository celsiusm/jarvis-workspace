"""
EventBroadcaster — singleton de WebSocket para broadcast por project_id.
Importado desde orchestrator.py, terminals.py y main.py.
No importa nada de los routers → sin riesgo de circular import.
"""
import asyncio
from typing import Callable, Dict, List
from fastapi import WebSocket


class EventBroadcaster:
    def __init__(self):
        self._conns: Dict[int, List[WebSocket]] = {}
        self._listeners: List[Callable] = []

    def escuchar(self, cb):
        """Registra un listener interno: async def cb(data: dict).
        Recibe TODOS los eventos (broadcast y broadcast_global) exactamente
        una vez, incluso sin ningún WS conectado. Para módulos backend
        (gateway Telegram, futuro daemon proactivo)."""
        self._listeners.append(cb)

    async def _send_to_project(self, project_id: int, data: dict):
        conns = list(self._conns.get(project_id, []))
        if not conns:
            return
        # En PARALELO con timeout: antes era en SERIE → un cliente con la cola TCP llena
        # (tab en background, red lenta) colgaba el await y RETENÍA el broadcast (sonidos,
        # live_update, dev_server_detectado) a TODOS los demás del proyecto. Ahora un socket
        # lento no bloquea a los rápidos y se da de baja solo.
        async def _enviar(ws):
            try:
                await asyncio.wait_for(ws.send_json(data), timeout=2.0)
                return None
            except Exception:
                return ws
        for ws in await asyncio.gather(*[_enviar(w) for w in conns]):
            if ws is not None:
                self.disconnect(ws, project_id)
                self._cerrar_en_fondo(ws)

    # Tareas de cierre vivas (referencia fuerte: asyncio no garantiza que una
    # tarea suelta sobreviva al GC antes de terminar).
    _cierres: set = set()

    def _cerrar_en_fondo(self, ws):
        """Cierra un socket dado de baja. Sin esto la pestaña quedaba SORDA: el
        browser lo seguía viendo abierto (ws_events espera en receive_text), no
        reconectaba y dejaba de recibir sonidos/task_event/live_update hasta un
        F5. Con el cierre (1011) el cliente reconecta con su backoff normal.
        En una tarea aparte y con timeout: un socket trabado no frena el broadcast."""
        async def _cerrar():
            try:
                await asyncio.wait_for(ws.close(code=1011), timeout=2.0)
            except Exception:
                pass
        t = asyncio.ensure_future(_cerrar())
        self._cierres.add(t)
        t.add_done_callback(self._cierres.discard)

    async def _notificar(self, data: dict):
        for cb in list(self._listeners):
            try:
                await cb(data)
            except Exception as e:
                print(f'[events] Listener interno falló: {e}')

    # Tope global de sockets de eventos: un cliente que abre miles de WS agotaría
    # FDs/RAM del server. 200 es holgado para un uso single-user (varias pestañas).
    MAX_WS_GLOBAL = 200

    def _total_conns(self) -> int:
        return sum(len(v) for v in self._conns.values())

    async def connect(self, ws: WebSocket, project_id: int) -> bool:
        """Acepta el WS y lo registra. Devuelve False (y cierra) si se superó el
        tope global — el caller debe abortar."""
        if self._total_conns() >= self.MAX_WS_GLOBAL:
            try:
                # accept() ANTES de close(): un WS cerrado SIN aceptar no entrega code de
                # aplicación al browser (ve 1006). Aceptando, el cliente recibe el 1013 real
                # y puede mostrar "demasiadas conexiones, cerrá pestañas" + backoff largo.
                await ws.accept()
                await ws.close(code=1013)   # 1013 = try again later
            except Exception:
                pass
            return False
        await ws.accept()
        self._conns.setdefault(project_id, []).append(ws)
        return True

    def disconnect(self, ws: WebSocket, project_id: int):
        conns = self._conns.get(project_id, [])
        if ws in conns:
            conns.remove(ws)

    async def broadcast(self, project_id: int, data: dict):
        """Envía data como JSON a todos los clientes del proyecto.
        Elimina silenciosamente los sockets caídos."""
        await self._send_to_project(project_id, data)
        await self._notificar(data)

    async def broadcast_global(self, data: dict):
        """Envía data a TODOS los clientes conectados, sin importar qué
        proyecto miran. Para eventos que afectan la lista de proyectos
        (crear/renombrar/archivar/borrar): cualquier workspace abierto
        refresca su sidebar en vivo, sin F5."""
        for project_id in list(self._conns.keys()):
            await self._send_to_project(project_id, data)
        await self._notificar(data)


broadcaster = EventBroadcaster()
