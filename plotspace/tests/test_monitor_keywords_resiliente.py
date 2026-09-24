# plotspace/tests/test_monitor_keywords_resiliente.py
"""_monitor_keywords sobrevive a un error TRANSITORIO (sqlite locked): antes el
`except Exception` estaba fuera del while y un solo fallo mataba el monitor
para siempre (el TASK_DONE posterior ya no se veía). Sigue saliendo cuando la
terminal deja de estar activa."""
import asyncio
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from plotspace.routers import terminals


class _EvInmediato:
    async def wait(self):
        return True

    def clear(self):
        pass


class _Cur:
    def __init__(self, fila):
        self.fila = fila

    def execute(self, *a):
        pass

    def fetchone(self):
        return self.fila


class _Conn:
    def __init__(self, fila):
        self.fila = fila

    def cursor(self):
        return _Cur(self.fila)

    def close(self):
        pass


def test_error_transitorio_no_mata_el_monitor(monkeypatch):
    tid = 987654
    # activa: 1ª vuelta → sqlite locked, 2ª → activa, 3ª → inactiva (sale)
    secuencia = ['error', {'activa': 1}, {'activa': 0}]
    llamadas = {'n': 0}

    def _get_db():
        paso = secuencia[min(llamadas['n'], len(secuencia) - 1)]
        llamadas['n'] += 1
        if paso == 'error':
            raise sqlite3.OperationalError('database is locked')
        return _Conn(paso)
    monkeypatch.setattr(terminals, 'get_db', _get_db)

    capturas = iter(['$ prompt', '$ prompt\nTASK_DONE'])

    async def _capturar(_tid):
        return next(capturas, '$ prompt\nTASK_DONE')
    monkeypatch.setattr(terminals, '_capture_tmux_output', _capturar)

    eventos = []

    async def _procesar(t, p, kw, motivo=None):
        eventos.append(kw)
    monkeypatch.setattr(terminals, '_procesar_keyword_evento', _procesar)
    monkeypatch.setattr(terminals, '_MONITOR_PAUSA_ERROR', 0.0, raising=False)
    terminals._monitor_wakeups[tid] = _EvInmediato()

    async def _correr():
        await asyncio.wait_for(terminals._monitor_keywords(tid, 1), timeout=10)

    try:
        asyncio.run(_correr())
    finally:
        terminals._monitor_wakeups.pop(tid, None)
    assert eventos == ['TASK_DONE'], eventos
    assert llamadas['n'] == 3, llamadas
