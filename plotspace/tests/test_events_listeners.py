"""Tests del hook de listeners internos del EventBroadcaster (fase 2 Telegram)."""
import asyncio

from plotspace.core.events import EventBroadcaster


def test_listener_recibe_broadcast_global_sin_conexiones():
    b = EventBroadcaster()
    recibidos = []

    async def cb(data):
        recibidos.append(data)

    b.escuchar(cb)
    asyncio.run(b.broadcast_global({'type': 'x'}))
    assert recibidos == [{'type': 'x'}]


def test_listener_una_sola_vez_con_varios_proyectos():
    b = EventBroadcaster()

    class WSFake:
        async def send_json(self, d):
            raise RuntimeError('socket roto')

    b._conns = {1: [WSFake()], 2: [WSFake()], 3: [WSFake()]}
    recibidos = []

    async def cb(data):
        recibidos.append(data)

    b.escuchar(cb)
    asyncio.run(b.broadcast_global({'type': 'y'}))
    assert len(recibidos) == 1


def test_listener_roto_no_tira_el_broadcast():
    b = EventBroadcaster()

    async def malo(data):
        raise RuntimeError('bum')

    ok = []

    async def bueno(data):
        ok.append(data)

    b.escuchar(malo)
    b.escuchar(bueno)
    asyncio.run(b.broadcast(1, {'type': 'z'}))
    assert ok == [{'type': 'z'}]


def test_broadcast_por_proyecto_tambien_notifica():
    b = EventBroadcaster()
    tipos = []

    async def cb(data):
        tipos.append(data['type'])

    b.escuchar(cb)
    asyncio.run(b.broadcast(7, {'type': 'w'}))
    assert tipos == ['w']


# ─── Un socket dado de baja se CIERRA (si no, la pestaña queda sorda) ────────
# Antes: si un envío fallaba o tardaba >2 s, el socket solo salía de la lista.
# El browser lo seguía viendo abierto (ws_events sigue en receive_text) → nunca
# reconectaba y dejaba de recibir sonidos, task_event y live_update hasta un F5.

class _WsFalla:
    def __init__(self, falla=True):
        self.falla = falla
        self.cerrado_con = None
        self.enviados = []

    async def send_json(self, data):
        if self.falla:
            raise RuntimeError('cola TCP llena')
        self.enviados.append(data)

    async def close(self, code=1000):
        self.cerrado_con = code


def test_socket_que_falla_se_cierra_para_que_el_cliente_reconecte():
    import asyncio
    from plotspace.core.events import EventBroadcaster

    async def _run():
        b = EventBroadcaster()
        malo, bueno = _WsFalla(True), _WsFalla(False)
        b._conns[7] = [malo, bueno]
        await b.broadcast(7, {'type': 'x'})
        await asyncio.sleep(0.05)   # el cierre corre en una tarea aparte
        return b, malo, bueno

    b, malo, bueno = asyncio.run(_run())
    assert malo not in b._conns[7]
    assert malo.cerrado_con is not None, 'el socket caído quedó abierto: el cliente nunca reconecta'
    assert bueno.enviados == [{'type': 'x'}] and bueno.cerrado_con is None
