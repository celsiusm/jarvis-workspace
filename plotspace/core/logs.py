"""Logging ESTRUCTURADO del swarm (JSON-lines) — audit trail consultable.

El `print()` disperso no se puede consultar, filtrar ni correlacionar. Acá cada evento importante
(tareas que Jarvis entrega, detección de keyword, errores del orquestador) se escribe como UNA línea
JSON a `data/jarvis.log` (con rotación por tamaño) Y se ecoa a consola (no se pierde la visibilidad
actual). Lo consume `GET /api/system/metrics` (eventos recientes) y sirve para diagnosticar qué hizo
el swarm. Stdlib pura, thread-safe (los pollers ahora corren en threads vía to_thread). El logging
NUNCA rompe el flujo: cualquier error al escribir se traga.
"""
import json
import os
import threading
from datetime import datetime

from plotspace.core.datadir import ruta_data

_LOG_PATH = ruta_data('jarvis.log')
_MAX_BYTES = 5 * 1024 * 1024     # rota a .1 al pasar 5MB (acota el disco)
_lock = threading.Lock()


def evento(tipo: str, nivel: str = 'info', **campos):
    """Registra un evento estructurado.
    tipo: clave del evento (ej 'tarea_jarvis', 'task_event', 'orquestador_error').
    nivel: 'info' | 'warn' | 'error'.
    campos: contexto arbitrario (terminal_id, project_id, keyword, etc.)."""
    rec = {'ts': datetime.now().isoformat(timespec='seconds'), 'nivel': nivel, 'evento': tipo}
    rec.update(campos)
    # Consola: preserva la visibilidad de los print() de antes.
    extra = ' '.join(f'{k}={v}' for k, v in campos.items())
    print(f'[{tipo}] {extra}' if extra else f'[{tipo}]')
    try:
        linea = json.dumps(rec, ensure_ascii=False, default=str)
    except Exception:
        return
    try:
        with _lock:
            try:
                if os.path.getsize(_LOG_PATH) > _MAX_BYTES:
                    # Dos generaciones (.1 → .2): el log es el corpus de las
                    # lecciones del enjambre — pisar .1 perdía historia útil.
                    try:
                        if os.path.exists(_LOG_PATH + '.1'):
                            os.replace(_LOG_PATH + '.1', _LOG_PATH + '.2')
                    except OSError:
                        pass
                    os.replace(_LOG_PATH, _LOG_PATH + '.1')   # atómico
            except OSError:
                pass
            with open(_LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(linea + '\n')
    except Exception:
        pass   # el logging jamás debe romper el flujo del swarm


def leer_recientes(n: int = 100, tipo: str = None, nivel: str = None) -> list:
    """Últimos n eventos del log (más reciente primero), filtrables por tipo/nivel.
    Lo usa el endpoint de métricas/diagnóstico. Robusto: líneas corruptas se saltan."""
    try:
        with _lock:
            with open(_LOG_PATH, encoding='utf-8') as f:
                lineas = f.readlines()[-(n * 4 + 50):]   # leer de más por si se filtra
    except FileNotFoundError:
        return []
    except Exception:
        return []
    out = []
    for l in reversed(lineas):
        try:
            rec = json.loads(l)
        except Exception:
            continue
        if tipo and rec.get('evento') != tipo:
            continue
        if nivel and rec.get('nivel') != nivel:
            continue
        out.append(rec)
        if len(out) >= n:
            break
    return out
