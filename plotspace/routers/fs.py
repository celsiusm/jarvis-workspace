"""Explorador de carpetas del disco — alimenta el picker de "Agregar proyecto".

NO es un file browser genérico: es un SELECTOR DE PROYECTOS. Solo lista nombres de
directorios (nunca contenido) y filtra para que se vean SOLO carpetas relevantes:
- oculta ruido (dotfolders, node_modules, venv, dist/build, caches…),
- oculta los proyectos YA agregados al workspace (no se re-agregan),
- muestra únicamente carpetas que SON un proyecto (tienen marcador) o que CONTIENEN
  proyectos (para poder navegar hacia ellos).
Token-gated como todo /api. Herramienta local, single-user.
"""
import os
import asyncio
from fastapi import APIRouter, HTTPException, Query

from plotspace.core.database import get_db

router = APIRouter(prefix="/api/fs", tags=["fs"])

# Pseudo-FS del kernel: no navegar.
_BLOQUEADAS = {'/proc', '/sys', '/dev'}

# Ruido que NO es un proyecto (se oculta del picker).
_OCULTAR = {
    'node_modules', '__pycache__', '.git', '.venv', 'venv', 'env', 'dist', 'build',
    'out', 'target', '.next', '.nuxt', '.svelte-kit', '.cache', '.idea', '.vscode',
    '.pytest_cache', '.mypy_cache', '.ruff_cache', 'coverage', '.turbo',
    '.parcel-cache', 'vendor', 'bower_components', '.gradle', 'Pods',
}
# Marcadores de que una carpeta ES un proyecto.
_MARCADORES = (
    '.git', 'package.json', 'pyproject.toml', 'requirements.txt', 'setup.py',
    'Cargo.toml', 'go.mod', 'tsconfig.json', 'pom.xml', 'build.gradle',
    'composer.json', 'Gemfile', 'CMakeLists.txt', '.jarvis', 'CLAUDE.md',
)


def _bloqueada(ruta: str) -> bool:
    return any(ruta == b or ruta.startswith(b + os.sep) for b in _BLOQUEADAS)


def _es_proyecto(path: str) -> bool:
    """La carpeta tiene algún marcador de proyecto en su raíz."""
    for m in _MARCADORES:
        try:
            if os.path.exists(os.path.join(path, m)):
                return True
        except OSError:
            pass
    return False


def _tiene_git(path: str) -> bool:
    return os.path.isdir(os.path.join(path, '.git'))


def _contiene_proyecto(path: str) -> bool:
    """Shallow: algún subdirectorio DIRECTO (no-ruido) es un proyecto. Sirve para
    que las carpetas contenedoras (proyectos/, repos/…) se vean y se pueda navegar."""
    try:
        with os.scandir(path) as it:
            for e in it:
                if e.name.startswith('.') or e.name in _OCULTAR:
                    continue
                try:
                    if e.is_dir(follow_symlinks=False) and _es_proyecto(os.path.join(path, e.name)):
                        return True
                except OSError:
                    continue
    except OSError:
        return False
    return False


def _rutas_agregadas() -> set:
    """Rutas absolutas de los proyectos YA agregados al workspace (para excluirlos)."""
    try:
        conn = get_db()
        try:
            filas = conn.execute('SELECT ruta FROM projects').fetchall()
        finally:
            conn.close()
        out = set()
        for f in filas:
            try:
                out.add(os.path.abspath(os.path.expanduser(f[0])).rstrip('/'))
            except Exception:
                pass
        return out
    except Exception:
        return set()


def _listar(base: str) -> dict:
    """Parte bloqueante (scandir + stats + DB) — se corre en un thread."""
    agregadas = _rutas_agregadas()
    dirs = []
    with os.scandir(base) as it:
        for e in it:
            if e.name.startswith('.') or e.name in _OCULTAR:
                continue                         # ruido / dotfolders (.bs, .cache…)
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            full = os.path.join(base, e.name)
            if full.rstrip('/') in agregadas:
                continue                         # ya es un proyecto agregado
            proj = _es_proyecto(full)
            if not (proj or _contiene_proyecto(full)):
                continue                         # solo carpetas que SON o CONTIENEN proyectos
            dirs.append({'name': e.name, 'path': full, 'git': _tiene_git(full), 'proyecto': proj})
    dirs.sort(key=lambda d: d['name'].lower())
    parent = os.path.dirname(base.rstrip('/')) or '/'
    return {'path': base, 'parent': None if base == '/' else parent, 'dirs': dirs}


@router.get("/list")
async def listar_dir(path: str = Query('')):
    """Subdirectorios navegables de `path` (default HOME): solo proyectos o carpetas
    que contienen proyectos, sin ruido ni proyectos ya agregados. `{path, parent,
    dirs:[{name, path, git, proyecto}]}`."""
    base = os.path.abspath(os.path.expanduser((path or '').strip() or '~'))
    if _bloqueada(base):
        raise HTTPException(status_code=403, detail="Ruta no navegable")
    if not os.path.isdir(base):
        raise HTTPException(status_code=404, detail="No es una carpeta")
    try:
        return await asyncio.to_thread(_listar, base)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Sin permiso para leer la carpeta")
    except OSError:
        raise HTTPException(status_code=500, detail="No se pudo leer la carpeta")


def hogar_de_la_maquina() -> dict:
    """Rutas del usuario REAL de esta máquina (expanduser, nunca las de Jarvis):
    home y la carpeta por-defecto para proyectos nuevos. El front la usa para
    sembrar sus valores por-defecto y dejar de hardcodear /home/user — en una
    máquina ajena (o macOS) el launcher debe proponer el homedir de ESA gente."""
    home = os.path.expanduser('~')
    return {'home': home, 'proyectos': os.path.join(home, 'projects')}


@router.get("/hogar")
async def ruta_hogar():
    return hogar_de_la_maquina()
