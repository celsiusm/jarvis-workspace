"""Regresión del blindaje anti-autodestrucción de projects.py.

El 2026-07-03 Jarvis se movió/borró a sí mismo: su propio repo quedó registrado
como "proyecto" y un PATCH .../rename (os.rename) y un DELETE /api/projects
(shutil.rmtree) sobre /home/user/jarvis se llevaron el árbol entero. El fix es
`_es_ruta_protegida`: la raíz del repo, cualquier ancestro suyo y cualquier
subdirectorio interno son intocables. Este test lo blinda sin tocar el repo real.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from plotspace.routers import projects
from plotspace.routers.projects import _es_ruta_protegida, _REPO_ROOT, RUTAS_PROHIBIDAS


def test_es_ruta_protegida():
    # La raíz del propio Jarvis: intocable.
    assert _es_ruta_protegida(_REPO_ROOT) is True
    # Un subdirectorio interno del repo: intocable (no borrar plotspace/, .git, etc.).
    assert _es_ruta_protegida(os.path.join(_REPO_ROOT, 'plotspace')) is True
    assert _es_ruta_protegida(os.path.join(_REPO_ROOT, 'plotspace', 'routers')) is True
    # Un ancestro del repo (borrarlo se lleva el repo): intocable.
    padre = os.path.dirname(_REPO_ROOT)
    assert _es_ruta_protegida(padre) is True
    # Rutas del sistema y HOME: intocables.
    for r in RUTAS_PROHIBIDAS:
        assert _es_ruta_protegida(r) is True
    assert _es_ruta_protegida(os.path.expanduser('~')) is True
    # Un proyecto ajeno de verdad: SÍ se puede tocar (no es el repo ni ancestro).
    assert _es_ruta_protegida('/home/user/otro-proyecto') is False
    assert _es_ruta_protegida(_REPO_ROOT + '-oss') is False  # prefijo textual, distinto árbol
    assert _es_ruta_protegida('') is False  # ruta vacía: no aplica


def test_repo_root_apunta_a_la_raiz():
    # plotspace/routers/projects.py → ../../ = raíz del repo (contiene VERSION y plotspace/).
    assert os.path.isfile(os.path.join(_REPO_ROOT, 'VERSION'))
    assert os.path.isdir(os.path.join(_REPO_ROOT, 'plotspace'))


def test_prefijo_textual_no_confunde():
    # "/home/user/jarvis-oss" NO debe contar como dentro de "/home/user/jarvis":
    # el guard usa separador de path, no startswith crudo.
    hermano = _REPO_ROOT + '-hermano'
    assert _es_ruta_protegida(hermano) is False


def test_symlink_al_repo_no_esquiva_el_blindaje(tmp_path):
    # Un proyecto registrado bajo un symlink al repo (~/jw → ~/jarvis-workspace)
    # comparaba strings distintos y quedaba borrable: rmtree se llevaba el árbol.
    link = tmp_path / 'jw'
    os.symlink(_REPO_ROOT, link)
    assert _es_ruta_protegida(str(link)) is True
    assert _es_ruta_protegida(str(link / 'plotspace')) is True


def main():
    test_es_ruta_protegida()
    test_repo_root_apunta_a_la_raiz()
    test_prefijo_textual_no_confunde()
    print("OK")


if __name__ == "__main__":
    main()
