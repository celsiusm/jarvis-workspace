"""Host y puerto en los que escucha ESTE server.

Lo usan el re-exec de "Update now" (volver con el mismo host/puerto) y el
candado de acceso (pedir token solo cuando el server escucha en la red).
Orden: JARVIS_BIND_HOST/JARVIS_PORT (los fija el CLI `jarvis`) → JARVIS_HOST →
--host/--port del argv de uvicorn → 127.0.0.1:3000.
"""
import os
import sys


def host_puerto_actuales(argv=None, env=None) -> tuple:
    argv = sys.argv if argv is None else argv
    env = os.environ if env is None else env

    def _arg(nombre):
        for i, a in enumerate(argv):
            if a == nombre and i + 1 < len(argv):
                return argv[i + 1]
            if a.startswith(nombre + '='):
                return a.split('=', 1)[1]
        return None

    host = ((env.get('JARVIS_BIND_HOST') or '').strip() or (env.get('JARVIS_HOST') or '').strip()
            or _arg('--host') or '127.0.0.1')
    port = (env.get('JARVIS_PORT') or '').strip() or _arg('--port') or '3000'
    return host, str(port)
