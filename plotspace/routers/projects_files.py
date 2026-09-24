"""
Endpoints para explorar y editar archivos de un proyecto desde el editor Monaco.
"""
import asyncio
import io
import os
import re
import shutil
import subprocess
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel

from plotspace.core.database import get_db
from plotspace.core import datadir

router = APIRouter(prefix="/api/projects", tags=["files"])

# Executor dedicado para búsqueda en contenido: corre os.walk/rg (síncrono, con
# I/O bloqueante) FUERA del event loop para no congelar WS de terminales ni el
# monitor de workflows (mismo patrón que voice.py::_whisper_executor).
_search_executor = ThreadPoolExecutor(max_workers=2)

# Directorios a ignorar en el árbol de archivos
IGNORE_DIRS = {
    '.git', '.worktrees', '__pycache__', 'node_modules',
    'venv', '.venv', '.workspace', '.idea', '.vscode',
    'dist', 'build', '.next', '.cache',
}

# Archivos a ocultar (seguridad). Incluye los secretos del PROPIO Jarvis por si
# su repo está registrado como proyecto navegable (el caso real que filtraba el
# token y la API key vía el editor).
HIDDEN_FILES = {
    '.env', '.env.local', '.env.production', '.env.development',
    'jarvis_token.txt', 'telegram.json',
    'jarvis.db', 'jarvis.db-wal', 'jarvis.db-shm',
}

# Extensión → lenguaje Monaco
LANG_BY_EXT = {
    '.py':    'python',
    '.js':    'javascript',
    '.jsx':   'javascript',
    '.ts':    'typescript',
    '.tsx':   'typescript',
    '.html':  'html',
    '.htm':   'html',
    '.css':   'css',
    '.scss':  'scss',
    '.json':  'json',
    '.yaml':  'yaml',
    '.yml':   'yaml',
    '.toml':  'toml',
    '.md':    'markdown',
    '.sh':    'shell',
    '.bash':  'shell',
    '.sql':   'sql',
    '.xml':   'xml',
    '.go':    'go',
    '.rs':    'rust',
    '.java':  'java',
    '.cpp':   'cpp',
    '.c':     'c',
    '.h':     'c',
    '.rb':    'ruby',
    '.php':   'php',
    '.dockerfile': 'dockerfile',
}

# Tamaño máximo para leer un archivo en el editor (1 MB)
MAX_FILE_SIZE = 1 * 1024 * 1024

# Anti-ReDoS: tope de caracteres por línea que el motor Python de búsqueda
# pasa a finditer (una regex del usuario con backtracking catastrófico necesita
# input largo; ripgrep —preferido— es inmune).
_MAX_LINE_SCAN = 5000

# Tope duro al forzar la apertura de un archivo grande (5 MB)
HARD_MAX_FILE_SIZE = 5 * 1024 * 1024

# Extensiones tratadas como imagen (preview en vez de Monaco)
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.ico'}

# ─── Límites transversales (preparación; los consumen Fases 6 y 7) ──────────────
# Upload (carpetas + zip): tope global de bytes y de cantidad de archivos.
MAX_UPLOAD_TOTAL     = 200 * 1024 * 1024   # 200 MB sumando todos los archivos
MAX_UPLOAD_FILES     = 2000                # nº máximo de archivos por subida
# Búsqueda en contenido (rg / os.walk).
MAX_SEARCH_RESULTS   = 500                 # nº máximo de matches totales
MAX_MATCHES_PER_FILE = 50                  # nº máximo de matches por archivo
SEARCH_TIMEOUT_SECS  = 15                  # timeout del subprocess/walk
MAX_QUERY_LEN        = 200                 # largo máximo del query
# Zip: ratio máximo de descompresión (anti zip-bomb).
MAX_ZIP_RATIO        = 100                 # bytes escritos / bytes comprimidos


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_project_path(project_id: int) -> str:
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT ruta FROM projects WHERE id = ?', (project_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Proyecto no encontrado")
        ruta = row['ruta']
    finally:
        conn.close()

    if not os.path.isdir(ruta):
        raise HTTPException(status_code=404, detail="La ruta del proyecto no existe en disco")
    return ruta


def _safe_join(base: str, rel: str) -> str:
    """Une base + rel evitando escapes con .. (normpath) y con symlinks (realpath).

    Doble capa: normpath atrapa traversal léxico (../); realpath atrapa symlinks
    que apuntan fuera del proyecto (defensa anti zip-slip / anti-traversal, spec §4.1).
    """
    rel = rel.lstrip('/').replace('\\', '/')
    full = os.path.normpath(os.path.join(base, rel))
    base_norm = os.path.normpath(base)
    if not full.startswith(base_norm + os.sep) and full != base_norm:
        raise HTTPException(status_code=400, detail="Ruta inválida (fuera del proyecto)")

    # Anti-symlink: la ruta REAL (resuelta) debe seguir dentro de la base REAL.
    real_full = os.path.realpath(full)
    real_base = os.path.realpath(base)
    if not real_full.startswith(real_base + os.sep) and real_full != real_base:
        raise HTTPException(status_code=400, detail="Ruta inválida (fuera del proyecto)")

    return full


# ─── Guard de secretos (read/raw/stat/search/save/delete) ────────────────────
# El _safe_join confina al proyecto, pero si el proyecto ES el repo de Jarvis,
# data/jarvis_token.txt y plotspace/.env quedan DENTRO del proyecto y se servirían.
# Este guard los bloquea por nombre Y por ruta real (cubre symlinks y el caso
# repo-como-proyecto), pase lo que pase el _safe_join.
_JARVIS_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), '..', '..'))
_JARVIS_DATA = os.path.join(_JARVIS_ROOT, 'data')
_JARVIS_ENV  = os.path.join(_JARVIS_ROOT, 'backend', '.env')
# Con JARVIS_DATA_DIR los datos pueden vivir FUERA del repo: ese directorio
# activo (token/DB) tampoco debe poder servirse desde el editor.
_DATA_ACTIVO = os.path.realpath(datadir.DATA_DIR)


def _es_servible(full: str) -> bool:
    """False si el archivo es un secreto que NO debe leerse/servirse/buscarse."""
    nombre = os.path.basename(full)
    if nombre in HIDDEN_FILES or nombre.startswith('.env'):
        return False
    real = os.path.realpath(full)
    if real == _JARVIS_ENV or real == _JARVIS_DATA or real.startswith(_JARVIS_DATA + os.sep):
        return False
    if real == _DATA_ACTIVO or real.startswith(_DATA_ACTIVO + os.sep):
        return False
    return True


def _guard_servible(full: str) -> None:
    """Aborta con 403 si la ruta no es servible (secreto protegido)."""
    if not _es_servible(full):
        raise HTTPException(status_code=403, detail="Archivo protegido")


def _leer_texto(full: str) -> str:
    """IO síncrona de lectura (se llama vía asyncio.to_thread)."""
    with open(full, 'r', encoding='utf-8', errors='replace') as f:
        return f.read()


def _guardar_texto(full: str, content: str) -> None:
    """IO síncrona de escritura (se llama vía asyncio.to_thread)."""
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'w', encoding='utf-8') as f:
        f.write(content)


def _detectar_lenguaje(path: str) -> str:
    name = os.path.basename(path).lower()
    if name == 'dockerfile':
        return 'dockerfile'
    if name in ('makefile', 'gnumakefile'):
        return 'makefile'
    _, ext = os.path.splitext(name)
    return LANG_BY_EXT.get(ext, 'plaintext')


def _construir_arbol(base: str, rel_path: str = '') -> list:
    """Construye recursivamente el árbol de archivos. Carpetas primero, después archivos, ambos alfabéticos."""
    abs_dir = os.path.join(base, rel_path) if rel_path else base
    if not os.path.isdir(abs_dir):
        return []

    try:
        entries = os.listdir(abs_dir)
    except PermissionError:
        return []

    dirs, files = [], []
    for entry in entries:
        if entry in IGNORE_DIRS or entry in HIDDEN_FILES:
            continue
        if entry.startswith('.env'):
            continue

        full_path = os.path.join(abs_dir, entry)
        if not _es_servible(full_path):   # no listar secretos (data/, .env, token, db…)
            continue
        rel       = os.path.join(rel_path, entry) if rel_path else entry
        rel       = rel.replace('\\', '/')

        try:
            if os.path.isdir(full_path):
                dirs.append({
                    'name':     entry,
                    'path':     rel,
                    'type':     'dir',
                    'children': _construir_arbol(base, rel),
                })
            elif os.path.isfile(full_path):
                files.append({
                    'name': entry,
                    'path': rel,
                    'type': 'file',
                })
        except (PermissionError, OSError):
            continue

    dirs.sort(key=lambda x: x['name'].lower())
    files.sort(key=lambda x: x['name'].lower())
    return dirs + files


def _git_run(base: str, *args: str) -> subprocess.CompletedProcess:
    """Corre git -C <base> <args> síncrono, stdout/stderr SEPARADOS, con timeout.
    Pensado para llamarse dentro de run_in_executor (NO en el handler async)."""
    return subprocess.run(
        ['git', '-C', base, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )


def _mapear_xy(xy: str) -> str:
    """Traduce los dos chars de estado XY de --porcelain a un código de 1 letra.
    X = index, Y = working tree. '??' = untracked → 'U'."""
    if xy == '??':
        return 'U'
    x, y = xy[0], xy[1]
    # Renombre/copia tienen prioridad de letra propia
    if 'R' in (x, y):
        return 'R'
    if 'C' in (x, y):
        return 'C'
    if 'D' in (x, y):
        return 'D'
    if 'A' in (x, y):
        return 'A'
    if 'M' in (x, y):
        return 'M'
    # Cualquier otra combinación con cambios la tratamos como modificada
    return 'M'


def _git_status_map(base: str) -> dict:
    """Devuelve {'git': bool, 'branch': str|None, 'files': {path: code}}.
    Síncrono — invocar vía run_in_executor."""
    # 1) ¿Es un repo?
    try:
        chk = _git_run(base, 'rev-parse', '--is-inside-work-tree')
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {'git': False, 'branch': None, 'files': {}}
    if chk.returncode != 0 or chk.stdout.decode('utf-8', 'replace').strip() != 'true':
        return {'git': False, 'branch': None, 'files': {}}

    # 2) Worktrees: si el toplevel del repo != la ruta del proyecto, los paths de
    #    --porcelain salen relativos al toplevel y no encajarían con el árbol.
    #    En ese caso reportamos git:false en vez de pintar badges desalineados.
    try:
        top = _git_run(base, 'rev-parse', '--show-toplevel')
        toplevel = top.stdout.decode('utf-8', 'replace').strip()
    except subprocess.TimeoutExpired:
        return {'git': False, 'branch': None, 'files': {}}
    if toplevel and os.path.realpath(toplevel) != os.path.realpath(base):
        return {'git': False, 'branch': None, 'files': {}}

    # 3) Rama actual (puede ser HEAD desprendido → None)
    branch = None
    try:
        b = _git_run(base, 'rev-parse', '--abbrev-ref', 'HEAD')
        if b.returncode == 0:
            name = b.stdout.decode('utf-8', 'replace').strip()
            branch = None if name == 'HEAD' else name
    except subprocess.TimeoutExpired:
        pass

    # 4) status --porcelain -z (registros separados por NUL; R/C consumen DOS)
    try:
        st = _git_run(base, 'status', '--porcelain', '-z')
    except subprocess.TimeoutExpired:
        return {'git': True, 'branch': branch, 'files': {}}
    if st.returncode != 0:
        return {'git': True, 'branch': branch, 'files': {}}

    raw = st.stdout.decode('utf-8', 'replace')
    registros = raw.split('\x00')
    files: dict = {}
    i = 0
    while i < len(registros):
        rec = registros[i]
        if not rec:
            i += 1
            continue
        xy = rec[:2]
        path = rec[3:]  # formato: 'XY <path>'
        code = _mapear_xy(xy)
        # R (rename) y C (copy) emiten DOS registros: el segundo es el origen.
        if 'R' in (xy[0], xy[1]) or 'C' in (xy[0], xy[1]):
            i += 1  # saltar el path de origen (no lo pintamos)
        files[path.replace('\\', '/')] = code
        i += 1

    return {'git': True, 'branch': branch, 'files': files}


# ─── Endpoints ────────────────────────────────────────────────────────────────

# Archivos/carpetas creados DESDE el creador del editor (franja). SOLO estos se
# pueden BORRAR desde el editor dentro del árbol protegido de Jarvis — los archivos
# reales de Jarvis no. En memoria (se pierde al reiniciar). project_id → set(rel).
_CREADOS_UI: dict = {}
def _marcar_creado(pid, rel):    _CREADOS_UI.setdefault(int(pid), set()).add(rel)
def _fue_creado_ui(pid, rel):    return rel in _CREADOS_UI.get(int(pid), set())
def _olvidar_creado(pid, rel):   _CREADOS_UI.get(int(pid), set()).discard(rel)


@router.get("/{project_id}/files/tree")
async def obtener_arbol(project_id: int):
    """Devuelve el árbol completo de archivos del proyecto."""
    ruta = _get_project_path(project_id)
    # En un thread: el walk recursivo (miles de stat()) en el event loop congelaba
    # todas las terminales/pollers al abrir el editor en repos grandes (auditoría perf).
    children = await asyncio.to_thread(_construir_arbol, ruta)
    from plotspace.routers.projects import _es_ruta_protegida   # lazy: evita import circular
    return {
        'root':      ruta,
        'name':      os.path.basename(ruta.rstrip('/')) or ruta,
        'children':  children,
        # True para el propio Jarvis (/home/user/jarvis): el front SOLO deja borrar
        # lo que se creó con el creador del editor (lista `creados`); el resto queda
        # protegido y el backend rechaza el DELETE (blindaje anti auto-destrucción).
        'protegido': _es_ruta_protegida(ruta),
        'creados':   sorted(_CREADOS_UI.get(int(project_id), set())),
    }


@router.get("/{project_id}/files/git-status")
async def git_status(project_id: int):
    """Estado git por archivo, para pintar el árbol. {git, branch, files:{path:code}}.
    Si no es repo → {git:false} con HTTP 200. Subprocess corre en executor."""
    base = _get_project_path(project_id)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _git_status_map, base)


@router.get("/{project_id}/files/read")
async def leer_archivo(project_id: int, path: str = Query(...), force: bool = Query(False)):
    """Lee un archivo del proyecto. Devuelve contenido + lenguaje detectado."""
    base     = _get_project_path(project_id)
    full     = _safe_join(base, path)
    _guard_servible(full)

    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    try:
        size = os.path.getsize(full)
    except OSError:
        raise HTTPException(status_code=500, detail="No se pudo leer el archivo")

    # Detectar imagen (por extensión) o binario (NUL en los primeros 1024 bytes).
    # Estos no se mandan a Monaco: el front usa /files/raw para previsualizar.
    _, ext = os.path.splitext(os.path.basename(full).lower())
    es_imagen = ext in IMAGE_EXTS
    es_binario = False
    if not es_imagen:
        try:
            with open(full, 'rb') as f:
                if b'\x00' in f.read(1024):
                    es_binario = True
        except Exception as e:
            # No filtrar el mensaje crudo al cliente: log server-side, detail genérico.
            print(f'[files] Error sondeando binario {full}: {e}')
            raise HTTPException(status_code=500, detail="No se pudo leer el archivo")

    if es_imagen or es_binario:
        return {
            'path':   path,
            'size':   size,
            'binary': True,
            'kind':   'image' if es_imagen else 'binary',
        }

    limite = HARD_MAX_FILE_SIZE if force else MAX_FILE_SIZE
    if size > limite:
        raise HTTPException(status_code=413, detail=f"Archivo demasiado grande (>{limite // 1024} KB)")

    try:
        content = await asyncio.to_thread(_leer_texto, full)   # IO fuera del loop
    except Exception as e:
        # No filtrar el mensaje crudo al cliente: log server-side, detail genérico.
        print(f'[files] Error leyendo {full}: {e}')
        raise HTTPException(status_code=500, detail="No se pudo leer el archivo")

    return {
        'path':     path,
        'content':  content,
        'language': _detectar_lenguaje(full),
        'size':     size,
        'mtime':    os.path.getmtime(full),
    }


@router.get("/{project_id}/files/raw")
async def archivo_crudo(project_id: int, path: str = Query(...)):
    """Sirve el archivo crudo (para previsualizar imágenes en el editor)."""
    base = _get_project_path(project_id)
    full = _safe_join(base, path)
    _guard_servible(full)

    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    return FileResponse(full)


@router.get("/{project_id}/files/stat")
async def stat_archivo(project_id: int, path: str = Query(...)):
    """Stat liviano de un archivo: {path, mtime, size} sin transferir contenido.
    Usado por el frontend para validar frescura del cache (mtime) sin descargar el archivo."""
    base = _get_project_path(project_id)
    full = _safe_join(base, path)
    _guard_servible(full)

    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    try:
        st = os.stat(full)
    except OSError:
        raise HTTPException(status_code=500, detail="No se pudo statear el archivo")

    return {
        'path':  path,
        'mtime': st.st_mtime,
        'size':  st.st_size,
    }


class FileSaveRequest(BaseModel):
    path:    str
    content: str


@router.post("/{project_id}/files/save")
async def guardar_archivo(project_id: int, datos: FileSaveRequest):
    """Guarda un archivo del proyecto. Crea directorios padre si no existen."""
    base = _get_project_path(project_id)
    full = _safe_join(base, datos.path)
    # No permitir leer NI sobrescribir secretos (token/API key/db del propio Jarvis)
    _guard_servible(full)

    era_nuevo = not os.path.isfile(full)   # crear un archivo nuevo (vs editar uno existente)
    try:
        await asyncio.to_thread(_guardar_texto, full, datos.content)   # IO fuera del loop
    except Exception as e:
        # No filtrar el mensaje crudo (rutas absolutas, errno) al cliente: log
        # server-side, detail genérico.
        print(f'[files] Error guardando {full}: {e}')
        raise HTTPException(status_code=500, detail="No se pudo guardar el archivo")
    if era_nuevo:
        _marcar_creado(project_id, datos.path)   # creado desde la UI → borrable en el editor aun en Jarvis

    return {
        'ok':    True,
        'path':  datos.path,
        'size':  len(datos.content),
        'mtime': os.path.getmtime(full),
    }


# ─── Upload (file picker + drag&drop desde el escritorio) ────────────────────

# Tamaño máximo por archivo subido (50 MB — "cualquier tipo de archivo":
# fotos, PDFs, videos cortos; el tope global MAX_UPLOAD_TOTAL sigue mandando)
MAX_UPLOAD_SIZE = 50 * 1024 * 1024

# Anti zip-bomb: límite de bytes DESCOMPRIMIDOS escritos (no los del header)
MAX_UNZIP_TOTAL = 200 * 1024 * 1024    # 200 MB descomprimidos por zip
_ZIP_CHUNK = 1024 * 1024                # lectura por miembro en trozos de 1 MB


@router.post("/{project_id}/files/upload")
async def subir_archivos(
    project_id: int,
    files: List[UploadFile] = File(...),
    rel_paths: List[str] = Form([]),
    target_dir: str = Query("", description="Subcarpeta relativa (vacío = raíz del proyecto)"),
):
    """Sube uno o más archivos al proyecto. Acepta multipart/form-data.
    rel_paths[i] (si viene y es truthy) preserva la estructura relativa del archivo i;
    si no, se usa el basename. Útil para webkitdirectory, drag&drop de carpetas y picker manual."""
    base = _get_project_path(project_id)
    full_target = _safe_join(base, target_dir) if target_dir else base

    if not os.path.isdir(full_target):
        try:
            os.makedirs(full_target, exist_ok=True)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"No pude crear la carpeta destino: {e}")

    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(status_code=413,
                            detail=f"Demasiados archivos (>{MAX_UPLOAD_FILES})")

    subidos = []
    rechazados = []
    total_bytes = 0
    for i, f in enumerate(files):
        basename = os.path.basename(f.filename or "").strip()
        rel = rel_paths[i] if i < len(rel_paths) and rel_paths[i] else basename
        rel = (rel or "").replace('\\', '/').strip()
        if not rel:
            continue

        # Rechazar cualquier segmento en IGNORE_DIRS (no subir .git/node_modules/venv…)
        segmentos = [s for s in rel.split('/') if s and s != '.']
        if any(s in IGNORE_DIRS for s in segmentos):
            rechazados.append({'archivo': rel, 'motivo': 'carpeta ignorada'})
            continue

        nombre = segmentos[-1] if segmentos else basename
        # Bloquear sensibles (por nombre de archivo final)
        if nombre in HIDDEN_FILES or nombre.startswith('.env'):
            rechazados.append({'archivo': rel, 'motivo': 'archivo sensible'})
            continue

        try:
            contenido = await f.read()
        except Exception as e:
            rechazados.append({'archivo': rel, 'motivo': f'no se pudo leer: {e}'})
            continue

        if len(contenido) > MAX_UPLOAD_SIZE:
            rechazados.append({'archivo': rel,
                               'motivo': f'demasiado grande (>{MAX_UPLOAD_SIZE // 1024} KB)'})
            continue

        total_bytes += len(contenido)
        if total_bytes > MAX_UPLOAD_TOTAL:
            rechazados.append({'archivo': rel,
                               'motivo': f'supera el total permitido (>{MAX_UPLOAD_TOTAL // (1024 * 1024)} MB)'})
            break

        try:
            dest = _safe_join(full_target, rel)   # anti-traversal
        except HTTPException:
            rechazados.append({'archivo': rel, 'motivo': 'ruta inválida'})
            continue
        if not _es_servible(dest):   # no escribir dentro de data/ ni sobre .env (realpath)
            rechazados.append({'archivo': rel, 'motivo': 'destino protegido'})
            continue

        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, 'wb') as out:
                out.write(contenido)
            rel_final = os.path.relpath(dest, base).replace('\\', '/')
            subidos.append(rel_final)
            _marcar_creado(project_id, rel_final)   # subido desde la UI → borrable en el editor (aun en Jarvis)
        except Exception as e:
            rechazados.append({'archivo': rel, 'motivo': f'no se pudo escribir: {e}'})

    return {'ok': True, 'subidos': subidos, 'rechazados': rechazados}


def _nombre_carpeta_zip(filename: str) -> str:
    """Nombre de carpeta seguro derivado del nombre del zip ('' nunca)."""
    stem = os.path.basename(filename or '').replace('\\', '/').split('/')[-1]
    if stem.lower().endswith('.zip'):
        stem = stem[:-4]
    stem = stem.strip().strip('.').strip()
    return stem or 'zip-extraido'


def _analizar_raiz_zip(nombres: list) -> tuple:
    """(envolver, top_unico): envolver=True si el zip NO tiene una única carpeta
    raíz que contenga todo (archivos sueltos arriba, o varias raíces)."""
    tops = set()
    archivo_arriba = False
    for nombre in nombres:
        rel = nombre.replace('\\', '/').strip()
        segmentos = [s for s in rel.split('/') if s and s != '.']
        if not segmentos:
            continue
        tops.add(segmentos[0])
        if len(segmentos) == 1 and not rel.endswith('/'):
            archivo_arriba = True
    if not tops:
        return False, ''
    if len(tops) == 1 and not archivo_arriba:
        return False, next(iter(tops))
    return True, ''


def _extraer_zip_bytes(project_id: int, base: str, full_target: str,
                       raw: bytes, nombre_zip: str) -> dict:
    """Núcleo SÍNCRONO de extracción (correr vía to_thread: una extracción de
    decenas de MB clavaría el event loop). Compartido por upload-zip (multipart)
    y extract-zip (zip ya en disco).

    Regla (pedido del usuario 2026-07-16): el zip SIEMPRE se convierte en una
    carpeta FRESCA con su nombre (sufijo -2/-3 si colisiona) y el contenido va
    adentro. Única excepción al anidado: si el zip ya trae una única carpeta
    raíz con el MISMO nombre (zipear una carpeta), esa raíz ES la carpeta —
    nunca <nombre>/<nombre>/.

    Anti zip-slip: cada miembro pasa por _safe_join. Anti zip-bomb: cuenta bytes
    ESCRITOS (acumulados vs MAX_UNZIP_TOTAL y ratio vs MAX_ZIP_RATIO)."""
    comprimido = len(raw)
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        raise HTTPException(status_code=400, detail="El archivo no es un .zip válido")

    subidos = []
    rechazados = []
    total_escrito = 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            nombres = z.namelist()
            if len(nombres) > MAX_UPLOAD_FILES:
                raise HTTPException(status_code=413,
                                    detail=f"El zip tiene demasiados archivos (>{MAX_UPLOAD_FILES})")

            stem = _nombre_carpeta_zip(nombre_zip)
            carpeta, n = stem, 2
            while os.path.exists(os.path.join(full_target, carpeta)):
                carpeta = f"{stem}-{n}"
                n += 1
            # ¿El zip ya ES una carpeta con el mismo nombre (única raíz == stem)?
            # → esa raíz pasa a ser `carpeta` (renombrada si hubo sufijo): así
            # nunca queda <nombre>/<nombre>/.
            envolver, top = _analizar_raiz_zip(nombres)
            raiz_es_stem = (not envolver) and top == stem

            for nombre in nombres:
                rel = nombre.replace('\\', '/').strip()
                if not rel or rel.endswith('/'):
                    continue  # entradas de directorio

                segmentos = [s for s in rel.split('/') if s and s != '.']
                if '..' in segmentos:
                    # Rechazo EXPLÍCITO: un '..' podría colapsar dentro de la
                    # carpeta envoltorio y escaparse de ella (aunque _safe_join
                    # igual frene cualquier escape del proyecto).
                    rechazados.append({'archivo': rel, 'motivo': 'ruta inválida'})
                    continue
                if raiz_es_stem:
                    segmentos = segmentos[1:]   # la raíz del zip ES la carpeta
                segmentos = [carpeta] + segmentos
                if len(segmentos) < 2:
                    continue                    # quedó solo la carpeta, sin archivo
                rel = '/'.join(segmentos)
                if any(s in IGNORE_DIRS for s in segmentos):
                    rechazados.append({'archivo': rel, 'motivo': 'carpeta ignorada'})
                    continue

                final = segmentos[-1]
                if final in HIDDEN_FILES or final.startswith('.env'):
                    rechazados.append({'archivo': rel, 'motivo': 'archivo sensible'})
                    continue

                try:
                    dest = _safe_join(full_target, rel)   # anti zip-slip
                except HTTPException:
                    rechazados.append({'archivo': rel, 'motivo': 'ruta inválida'})
                    continue
                if not _es_servible(dest):   # no extraer dentro de data/ ni sobre .env
                    rechazados.append({'archivo': rel, 'motivo': 'destino protegido'})
                    continue

                # Anti zip-bomb ANTES de descomprimir: el tamaño declarado en el
                # header ya alcanza para rechazar (z.read() inflaría todo en RAM).
                try:
                    info = z.getinfo(nombre)
                except KeyError as e:
                    rechazados.append({'archivo': rel, 'motivo': f'no se pudo leer del zip: {e}'})
                    continue
                if info.file_size > MAX_UPLOAD_SIZE:
                    rechazados.append({'archivo': rel,
                                       'motivo': f'demasiado grande (>{MAX_UPLOAD_SIZE // 1024} KB)'})
                    continue
                if total_escrito + info.file_size > MAX_UNZIP_TOTAL:
                    rechazados.append({'archivo': rel,
                                       'motivo': f'el zip descomprimido supera {MAX_UNZIP_TOTAL // (1024 * 1024)} MB'})
                    break

                # Lectura en streaming con tope: aunque el header mienta, nunca
                # se descomprime más de MAX_UPLOAD_SIZE + 1 bytes por miembro.
                try:
                    partes = []
                    leidos = 0
                    with z.open(info) as src:
                        while leidos <= MAX_UPLOAD_SIZE:
                            chunk = src.read(min(_ZIP_CHUNK, MAX_UPLOAD_SIZE + 1 - leidos))
                            if not chunk:
                                break
                            partes.append(chunk)
                            leidos += len(chunk)
                    data = b''.join(partes)
                except Exception as e:
                    rechazados.append({'archivo': rel, 'motivo': f'no se pudo leer del zip: {e}'})
                    continue

                if len(data) > MAX_UPLOAD_SIZE:
                    rechazados.append({'archivo': rel,
                                       'motivo': f'demasiado grande (>{MAX_UPLOAD_SIZE // 1024} KB)'})
                    continue

                total_escrito += len(data)
                if total_escrito > MAX_UNZIP_TOTAL:
                    rechazados.append({'archivo': rel,
                                       'motivo': f'el zip descomprimido supera {MAX_UNZIP_TOTAL // (1024 * 1024)} MB'})
                    break
                # Anti zip-bomb por ratio: bytes ESCRITOS / bytes comprimidos
                if comprimido > 0 and total_escrito / comprimido > MAX_ZIP_RATIO:
                    rechazados.append({'archivo': rel,
                                       'motivo': f'ratio de descompresión excesivo (>{MAX_ZIP_RATIO}x)'})
                    break

                try:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, 'wb') as out:
                        out.write(data)
                    rel_final = os.path.relpath(dest, base).replace('\\', '/')
                    subidos.append(rel_final)
                    _marcar_creado(project_id, rel_final)   # extraído desde la UI → borrable en el editor
                except Exception as e:
                    rechazados.append({'archivo': rel, 'motivo': f'no se pudo escribir: {e}'})
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="El archivo no es un .zip válido")

    return {'subidos': subidos, 'rechazados': rechazados, 'carpeta': carpeta}


@router.post("/{project_id}/files/upload-zip")
async def subir_zip(
    project_id: int,
    file: UploadFile = File(...),
    target_dir: str = Query("", description="Subcarpeta relativa (vacío = raíz del proyecto)"),
):
    """Sube un .zip y lo extrae EN MODO CARPETA: siempre queda una carpeta
    fresca <nombre-del-zip>/ (sufijo -2/-3 si ya existe) con el contenido
    adentro — nunca se desparrama suelto en el destino."""
    base = _get_project_path(project_id)
    full_target = _safe_join(base, target_dir) if target_dir else base
    os.makedirs(full_target, exist_ok=True)

    # Tope de lectura: nunca más de MAX_UPLOAD_TOTAL (+1 para detectar exceso)
    # en RAM — un upload sin límite agota la memoria del server.
    raw = await file.read(MAX_UPLOAD_TOTAL + 1)
    if len(raw) > MAX_UPLOAD_TOTAL:
        raise HTTPException(status_code=413,
                            detail=f"El zip supera el total permitido (>{MAX_UPLOAD_TOTAL // (1024 * 1024)} MB)")
    resultado = await asyncio.to_thread(
        _extraer_zip_bytes, project_id, base, full_target, raw, file.filename or '')
    return {'ok': True, **resultado}


class ExtractZipRequest(BaseModel):
    path: str


@router.post("/{project_id}/files/extract-zip")
async def extraer_zip(project_id: int, datos: ExtractZipRequest):
    """Extrae un .zip que YA vive en el proyecto (click derecho en el árbol),
    en modo carpeta y AL LADO del zip. El .zip NO se toca (pedido del usuario
    2026-07-16): queda la carpeta nueva y el zip original conviviendo."""
    base = _get_project_path(project_id)
    rel = (datos.path or '').strip()
    if not rel:
        raise HTTPException(status_code=400, detail="Ruta vacía")

    full = _safe_join(base, rel)
    _guard_servible(full)
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    raw = await asyncio.to_thread(lambda: open(full, 'rb').read())
    full_target = os.path.dirname(full)   # extraer al lado del zip
    resultado = await asyncio.to_thread(
        _extraer_zip_bytes, project_id, base, full_target, raw, os.path.basename(full))

    return {'ok': True, **resultado}


# ─── Mkdir / Rename / Delete-dir (árbol CRUD) ───────────────────────────────

class MkdirRequest(BaseModel):
    path: str


@router.post("/{project_id}/files/mkdir")
async def crear_directorio(project_id: int, datos: MkdirRequest):
    """Crea una carpeta (y sus padres). 409 si ya existe, 403 si el basename es sensible."""
    base = _get_project_path(project_id)
    rel = (datos.path or '').strip()
    if not rel:
        raise HTTPException(status_code=400, detail="Ruta vacía")

    full = _safe_join(base, rel)
    _guard_servible(full)   # nombre + realpath (no crear dentro de data/ ni .env)

    if os.path.exists(full):
        raise HTTPException(status_code=409, detail="Ya existe")

    try:
        os.makedirs(full, exist_ok=False)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Ya existe")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creando carpeta: {e}")

    _marcar_creado(project_id, rel)   # creada desde la UI → borrable en el editor aun en Jarvis
    return {'ok': True, 'path': rel}


class RenameRequest(BaseModel):
    src: str
    dst: str


@router.post("/{project_id}/files/rename")
async def renombrar_ruta(project_id: int, datos: RenameRequest):
    """Renombra o mueve un archivo/carpeta. Guard de sensibles en AMBOS extremos.
    404 si src no existe, 409 si dst ya existe."""
    base = _get_project_path(project_id)
    src_rel = (datos.src or '').strip()
    dst_rel = (datos.dst or '').strip()
    if not src_rel or not dst_rel:
        raise HTTPException(status_code=400, detail="src y dst son obligatorios")

    src_full = _safe_join(base, src_rel)
    dst_full = _safe_join(base, dst_rel)

    # Guard de secretos en AMBOS extremos por nombre Y realpath: sin esto se podía
    # renombrar data/browser_profile/.../Cookies a otro nombre y leerlo después
    # (bypass del fix de secretos — hallazgo 2ª pasada). _guard_servible cubre
    # data/, plotspace/.env y los nombres conocidos.
    _guard_servible(src_full)
    _guard_servible(dst_full)

    if not os.path.exists(src_full):
        raise HTTPException(status_code=404, detail="Origen no encontrado")
    if os.path.exists(dst_full):
        raise HTTPException(status_code=409, detail="El destino ya existe")

    try:
        os.makedirs(os.path.dirname(dst_full), exist_ok=True)
        os.rename(src_full, dst_full)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error renombrando: {e}")

    return {'ok': True, 'src': src_rel, 'dst': dst_rel}


def _arbol_tiene_sensibles(root: str) -> bool:
    """Camina el árbol y devuelve True si encuentra un archivo/carpeta sensible
    (protege secretos anidados antes de un borrado recursivo). Chequea por
    realpath (_es_servible), no solo por basename: cubre data/ y plotspace/.env."""
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            if not _es_servible(os.path.join(dirpath, name)):
                return True
        for name in dirnames:
            if not _es_servible(os.path.join(dirpath, name)):
                return True
    return False


@router.delete("/{project_id}/files/dir")
async def borrar_directorio(project_id: int, path: str = Query("")):
    """Borra una carpeta de forma recursiva y SEGURA:
    rechaza la raíz, rechaza IGNORE_DIRS y aborta si hay secretos anidados."""
    base = _get_project_path(project_id)
    rel = (path or '').strip()

    # Rechazar raíz (vacío, ".", "/")
    if not rel or rel in ('.', '/', './'):
        raise HTTPException(status_code=400, detail="No se puede borrar la raíz del proyecto")

    full = _safe_join(base, rel)

    # Defensa extra: el resultado no puede ser la propia base
    if os.path.normpath(full) == os.path.normpath(base):
        raise HTTPException(status_code=400, detail="No se puede borrar la raíz del proyecto")

    # Blindaje anti auto-destrucción: en el árbol del propio Jarvis SOLO se borra lo
    # que se creó desde el creador del editor; el resto (archivos reales) queda intocable.
    from plotspace.routers.projects import _es_ruta_protegida
    if _es_ruta_protegida(full) and not _fue_creado_ui(project_id, rel):
        raise HTTPException(status_code=403, detail="Protegido: solo se pueden borrar archivos creados desde el editor")

    nombre = os.path.basename(full)
    if nombre in IGNORE_DIRS:
        raise HTTPException(status_code=403, detail="No se puede borrar esta carpeta")
    _guard_servible(full)   # nombre + realpath (no borrar data/ ni plotspace/.env)

    if not os.path.exists(full):
        raise HTTPException(status_code=404, detail="Carpeta no encontrada")
    if not os.path.isdir(full):
        raise HTTPException(status_code=400, detail="No es una carpeta. Usá el borrado de archivo.")

    if _arbol_tiene_sensibles(full):
        raise HTTPException(status_code=403, detail="La carpeta contiene archivos protegidos; borralos manualmente")

    try:
        # En un thread: rmtree de node_modules/ puede tardar 10-60s y clavaba
        # el event loop entero (todas las terminales + pollers congelados).
        await asyncio.to_thread(shutil.rmtree, full)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error borrando carpeta: {e}")

    return {'ok': True, 'path': rel}


# ─── Delete file ─────────────────────────────────────────────────────────────

@router.delete("/{project_id}/files")
async def borrar_archivo(project_id: int, path: str = Query(...)):
    """Borra un archivo del proyecto. NO borra carpetas."""
    base = _get_project_path(project_id)
    full = _safe_join(base, path)
    _guard_servible(full)   # nombre + realpath (no borrar secretos de data/ ni .env)

    # Blindaje anti auto-destrucción: en Jarvis SOLO se borra lo creado desde el editor.
    from plotspace.routers.projects import _es_ruta_protegida
    if _es_ruta_protegida(full) and not _fue_creado_ui(project_id, path):
        raise HTTPException(status_code=403, detail="Protegido: solo se pueden borrar archivos creados desde el editor")

    if not os.path.exists(full):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    if os.path.isdir(full):
        raise HTTPException(status_code=400, detail="Es una carpeta, no un archivo. Usá el shell.")

    try:
        os.remove(full)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error borrando: {e}")
    _olvidar_creado(project_id, path)

    return {'ok': True, 'path': path}


# ─── Búsqueda en contenido ───────────────────────────────────────────────────

def _es_binario(sample: bytes) -> bool:
    """Heurística: hay un NUL byte en los primeros 1024 bytes → binario."""
    return b'\x00' in sample[:1024]


def _col_utf16(linea: str, byte_off: int, linea_bytes: bytes) -> int:
    """Convierte un offset de BYTES UTF-8 dentro de la línea a una columna 1-based
    en unidades UTF-16 (las que usa Monaco). Para líneas ASCII es byte_off+1, pero
    en líneas con acentos/emoji hay que decodificar el prefijo y medir en UTF-16."""
    prefijo = linea_bytes[:byte_off].decode('utf-8', errors='replace')
    # len(str) en Python cuenta code points; Monaco cuenta unidades UTF-16.
    # Sumar +1 por cada code point fuera del BMP (que en UTF-16 ocupa 2 unidades).
    extra = sum(1 for ch in prefijo if ord(ch) > 0xFFFF)
    return len(prefijo) + extra + 1


def _buscar_python(base: str, patron, q_raw: str) -> dict:
    """Motor PRIMARIO (os.walk). `patron` es un re.Pattern ya compilado.
    Devuelve el contrato JSON estándar."""
    resultados = []
    total = 0
    truncated = False
    deadline = time.monotonic() + SEARCH_TIMEOUT_SECS

    base_norm = os.path.normpath(base)
    for dirpath, dirnames, filenames in os.walk(base):
        # Podar directorios ignorados in-place (os.walk respeta esta mutación).
        # Además podar el dir de secretos del propio Jarvis (data/) por ruta real.
        dirnames[:] = [
            d for d in dirnames
            if d not in IGNORE_DIRS and not d.startswith('.env')
            and _es_servible(os.path.join(dirpath, d))
        ]

        if time.monotonic() > deadline:
            truncated = True
            break

        for nombre in sorted(filenames):
            if total >= MAX_SEARCH_RESULTS:
                truncated = True
                break
            if time.monotonic() > deadline:
                truncated = True
                break

            full = os.path.join(dirpath, nombre)
            if not _es_servible(full):    # nunca indexar secretos (token/.env/db)
                continue
            try:
                if os.path.getsize(full) > MAX_FILE_SIZE:
                    continue
                with open(full, 'rb') as fh:
                    raw = fh.read()
            except (OSError, PermissionError):
                continue

            if _es_binario(raw):
                continue

            try:
                texto = raw.decode('utf-8', errors='replace')
            except Exception:
                continue

            matches = []
            for i, linea in enumerate(texto.splitlines(), start=1):
                # Anti-ReDoS: chequear el deadline también DENTRO del archivo y
                # recortar líneas larguísimas antes de finditer (el backtracking
                # catastrófico de una regex del usuario necesita input largo).
                if (i & 0x1FF) == 0 and time.monotonic() > deadline:
                    truncated = True
                    break
                if len(linea) > _MAX_LINE_SCAN:
                    linea = linea[:_MAX_LINE_SCAN]
                linea_bytes = linea.encode('utf-8')
                for mm in patron.finditer(linea):
                    byte_off = len(linea[:mm.start()].encode('utf-8'))
                    col = _col_utf16(linea, byte_off, linea_bytes)
                    matches.append({
                        'line':   i,
                        'col':    col,
                        'text':   linea[:500],
                        'length': len(mm.group(0)),
                    })
                    total += 1
                    if len(matches) >= MAX_MATCHES_PER_FILE:
                        break
                    if total >= MAX_SEARCH_RESULTS:
                        truncated = True
                        break
                if len(matches) >= MAX_MATCHES_PER_FILE or total >= MAX_SEARCH_RESULTS:
                    break

            if matches:
                rel = os.path.relpath(full, base_norm).replace('\\', '/')
                resultados.append({'file': rel, 'matches': matches})

        if total >= MAX_SEARCH_RESULTS:
            truncated = True
            break

    return {'query': q_raw, 'results': resultados, 'total': total,
            'truncated': truncated, 'error': None}


def _buscar_ripgrep(base: str, q: str, case_sensitive: bool, regex: bool,
                    whole_word: bool, q_raw: str) -> dict:
    """Motor opcional (rg). Solo se invoca si shutil.which('rg'). Reusa la
    columna byte→UTF-16 de Monaco re-leyendo cada línea reportada."""
    import subprocess
    args = ['rg', '--json', '--no-config']
    if not case_sensitive:
        args.append('--ignore-case')
    if whole_word:
        args.append('--word-regexp')
    if not regex:
        args.append('--fixed-strings')
    for d in IGNORE_DIRS:
        args += ['--glob', f'!{d}/**']
    args += ['--', q, base]

    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              timeout=SEARCH_TIMEOUT_SECS)
    except subprocess.TimeoutExpired:
        return {'query': q_raw, 'results': [], 'total': 0,
                'truncated': True, 'error': None}

    import json as _json
    por_archivo = {}
    orden = []
    total = 0
    truncated = False
    base_norm = os.path.normpath(base)
    for raw_line in proc.stdout.splitlines():
        if total >= MAX_SEARCH_RESULTS:
            truncated = True
            break
        try:
            ev = _json.loads(raw_line)
        except ValueError:
            continue
        if ev.get('type') != 'match':
            continue
        data = ev['data']
        full = data['path'].get('text') or ''
        if not _es_servible(full):    # nunca devolver matches de secretos
            continue
        rel = os.path.relpath(full, base_norm).replace('\\', '/')
        linea_txt = (data['lines'].get('text') or '').rstrip('\n')
        linea_bytes = linea_txt.encode('utf-8')
        if rel not in por_archivo:
            por_archivo[rel] = []
            orden.append(rel)
        bucket = por_archivo[rel]
        for sm in data.get('submatches', []):
            if len(bucket) >= MAX_MATCHES_PER_FILE:
                break
            byte_off = sm['start']
            col = _col_utf16(linea_txt, byte_off, linea_bytes)
            bucket.append({
                'line':   data['line_number'],
                'col':    col,
                'text':   linea_txt[:500],
                'length': len((sm['match'].get('text') or '')),
            })
            total += 1

    resultados = [{'file': rel, 'matches': por_archivo[rel]} for rel in orden if por_archivo[rel]]
    return {'query': q_raw, 'results': resultados, 'total': total,
            'truncated': truncated, 'error': None}


def _buscar(base: str, q: str, case_sensitive: bool, regex: bool, whole_word: bool) -> dict:
    """Dispatcher. Compila el patrón (devuelve {error} si la regex es inválida),
    luego usa os.walk como motor PRIMARIO; rg solo si está en el PATH."""
    flags = 0 if case_sensitive else re.IGNORECASE
    if regex:
        cuerpo = q
    else:
        cuerpo = re.escape(q)
    if whole_word:
        cuerpo = r'\b' + cuerpo + r'\b'
    try:
        patron = re.compile(cuerpo, flags)
    except re.error as e:
        return {'query': q, 'results': [], 'total': 0,
                'truncated': False, 'error': f'regex inválida: {e}'}

    if shutil.which('rg'):
        try:
            return _buscar_ripgrep(base, q, case_sensitive, regex, whole_word, q)
        except Exception:
            pass  # caer al motor python si rg falla por cualquier motivo
    # Fallback Python. Para regex=False el patrón está escapado (re.escape) → seguro.
    # Para regex=True NO corremos el motor Python con la regex del usuario: el
    # backtracking catastrófico (ReDoS) tomaría un worker de búsqueda y CPU. rg es
    # inmune; si no está, rechazamos la búsqueda con regex (anti-ReDoS, auditoría).
    if regex:
        return {'query': q, 'results': [], 'total': 0, 'truncated': False,
                'error': 'La búsqueda con expresiones regulares requiere ripgrep (rg) instalado'}
    return _buscar_python(base, patron, q)


@router.get("/{project_id}/files/search")
async def buscar_contenido(
    project_id: int,
    q: str = Query(..., description="Texto o patrón a buscar"),
    case_sensitive: bool = Query(False),
    regex: bool = Query(False),
    whole_word: bool = Query(False),
):
    """Búsqueda full-text recursiva. Corre en executor dedicado para no bloquear
    el event loop. Motor primario os.walk; rg si está disponible."""
    base = _get_project_path(project_id)
    if not q:
        return {'query': q, 'results': [], 'total': 0, 'truncated': False, 'error': None}
    if len(q) > MAX_QUERY_LEN:
        raise HTTPException(status_code=400, detail=f"Query demasiado larga (>{MAX_QUERY_LEN})")

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _search_executor, _buscar, base, q, case_sensitive, regex, whole_word
    )
