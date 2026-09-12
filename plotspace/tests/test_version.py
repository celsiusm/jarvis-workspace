"""La versión tiene UNA sola fuente: el archivo VERSION de la raíz.

Contexto (2026-09-12): pyproject.toml tenía un `version` hardcodeado (1.8.52)
que se desincronizó del VERSION que lee el motor en runtime (1.7.2). Ahora
pyproject lo declara `dynamic` y lo resuelve vía `plotspace.__version__` (que
lee VERSION). Este test caza la regresión: si alguien vuelve a hardcodear la
versión, o si el paquete deja de coincidir con el archivo, falla.
"""
import os
import sys
import tomllib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import plotspace  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _leer(nombre: str) -> str:
    with open(os.path.join(RAIZ, nombre), encoding='utf-8') as f:
        return f.read()


def test_la_version_del_paquete_sale_del_archivo():
    assert plotspace.__version__ == _leer('VERSION').strip()


def test_pyproject_no_hardcodea_la_version():
    cfg = tomllib.loads(_leer('pyproject.toml'))
    proj = cfg['project']
    assert 'version' not in proj, (
        'pyproject.toml hardcodea version: la fuente única es VERSION '
        '(debe declararse dynamic = ["version"])')
    assert 'version' in proj.get('dynamic', []), (
        'pyproject.toml debe declarar "version" en [project].dynamic')
    attr = cfg['tool']['setuptools']['dynamic']['version']['attr']
    assert attr == 'plotspace.__version__', attr


if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main([__file__, '-q']))
