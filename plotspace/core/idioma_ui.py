"""Idioma de la interfaz (es/en) visto desde el backend.

El frontend traduce su propio chrome (shared/i18n.js), pero hay texto que NACE
en el server y se muestra tal cual: los avisos del orquestador al chat, el
cierre de un workflow (que además se lee en voz alta) y la respuesta misma del
orquestador (un LLM). Para esos, el server necesita saber qué idioma eligió el
usuario: lo reporta el frontend (presence + cada pedido al chat) y queda acá,
persistido en data/ui-lang para que un reinicio no vuelva al default.

`L(es, en)` elige la variante según el idioma actual.
"""
import os

from plotspace.core.datadir import ruta_data

_DEFAULT = 'en'          # mismo default que shared/i18n.js
_actual = None


def _archivo() -> str:
    return ruta_data('ui-lang')


def normalizar(lang) -> str:
    return 'es' if str(lang or '').strip().lower().startswith('es') else 'en'


def actual() -> str:
    global _actual
    if _actual is None:
        try:
            with open(_archivo(), encoding='utf-8') as f:
                _actual = normalizar(f.read())
        except OSError:
            _actual = _DEFAULT
    return _actual


def fijar(lang) -> str:
    """Registra el idioma que reporta el frontend. Escribe a disco solo si cambió."""
    global _actual
    nuevo = normalizar(lang)
    if nuevo != actual():
        _actual = nuevo
        try:
            os.makedirs(os.path.dirname(_archivo()), exist_ok=True)
            with open(_archivo(), 'w', encoding='utf-8') as f:
                f.write(nuevo)
        except OSError:
            pass
    return _actual


def L(es: str, en: str) -> str:
    return en if actual() == 'en' else es
