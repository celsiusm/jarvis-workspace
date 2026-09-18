"""/api/fs/hogar — la ubicación por-defecto de proyectos sale del HOGAR del
usuario de la máquina, no de Jarvis: en una computadora ajena (otro user,
macOS) el launcher tiene que proponer su ~/projects, no /home/user pegado
en el frontend.

La regla de oro: el frontend JAMÁS hardcodea una ruta de la máquina del
autor; pide al backend y el backend usa expanduser('~').

Los endpoints /api son token-gated (no dan TestClient fácil acá): lo que se
prueba es la lógica pura que el endpoint sirve.
"""
import os

from plotspace.routers import fs as fs_router


def test_hogar_es_el_expanduser_de_esta_maquina():
    h = fs_router.hogar_de_la_maquina()
    assert h['home'] == os.path.abspath(os.path.expanduser('~'))


def test_proyectos_cuelga_del_hogar():
    h = fs_router.hogar_de_la_maquina()
    assert h['proyectos'] == os.path.join(h['home'], 'projects')
