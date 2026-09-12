"""Jarvis Workspace — motor (FastAPI + terminales tmux).

La versión del paquete sale del archivo ``VERSION`` de la raíz: la MISMA fuente
que lee el motor en runtime (ver ``plotspace/routers/system.py``). ``pyproject.toml``
la toma de acá con ``dynamic = ["version"]``, así no puede quedar desincronizada.
"""
import os


def _version_de_archivo() -> str:
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(raiz, 'VERSION'), encoding='utf-8') as f:
            return f.read().strip() or '0.0.0'
    except OSError:
        return '0.0.0'


__version__ = _version_de_archivo()
