import asyncio
import os
import shutil
import subprocess

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from plotspace.core.database import get_db


# Paths "peligrosos" — no permitir registrar proyecto apuntando ahí
RUTAS_PROHIBIDAS = {
    '/', '/home', '/root', '/etc', '/var', '/usr', '/bin', '/sbin',
    '/tmp', '/dev', '/proc', '/sys',
}

# Raíz del propio repo Jarvis (este archivo vive en plotspace/routers/ → ../../ = raíz).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _es_ruta_protegida(ruta_abs: str) -> bool:
    """True si `ruta_abs` NO puede borrarse ni renombrarse: rutas del sistema,
    el HOME, o CUALQUIER parte del árbol del propio Jarvis (la raíz, un ancestro
    suyo, o un subdirectorio interno).

    El blindaje del árbol de Jarvis evita que el dashboard se destruya a sí mismo
    cuando su propio repo quedó registrado como "proyecto": el 2026-07-03 un
    DELETE /api/projects (rmtree) y luego un PATCH .../rename (os.rename) sobre
    /home/user/jarvis se llevaron el repo entero. Falla cerrado."""
    if not ruta_abs:
        return False
    # Se evalúa la ruta tal cual Y resuelta (realpath): un proyecto registrado
    # bajo un symlink (~/jw → ~/jarvis-workspace) esquivaba el blindaje por
    # comparación de strings, y rmtree solo rechaza el symlink en el tope.
    raiz_real = os.path.realpath(_REPO_ROOT)
    for ruta in {ruta_abs, os.path.realpath(ruta_abs)}:
        if ruta in RUTAS_PROHIBIDAS or ruta == os.path.expanduser('~'):
            return True
        for raiz in {_REPO_ROOT, raiz_real}:
            if (ruta == raiz
                    or raiz.startswith(ruta + os.sep)      # ruta es ancestro del repo
                    or ruta.startswith(raiz + os.sep)):    # ruta está dentro del repo
                return True
    return False

async def _avisar_projects_update():
    """Push global: la lista de proyectos cambió → todo workspace abierto
    refresca su sidebar en vivo (sin F5). Nunca rompe el endpoint."""
    try:
        from plotspace.core.events import broadcaster
        await broadcaster.broadcast_global({'type': 'projects_update'})
    except Exception:
        pass


router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    nombre: str
    ruta: str


class ProjectResponse(BaseModel):
    id: int
    nombre: str
    ruta: str
    fecha_creacion: str
    ultimo_acceso: str
    terminales_activas: Optional[int] = 0
    seccion: Optional[str] = 'active'
    orden: Optional[int] = 0
    branch: Optional[str] = ''
    status: Optional[str] = 'idle'  # 'run' | 'work' | 'err' | 'idle'


class SectionPatch(BaseModel):
    seccion: Optional[str] = None  # 'pinned' | 'active' | 'archived'
    orden:   Optional[int] = None


class RenameRequest(BaseModel):
    nombre: str


def _git_branch_actual(ruta: str) -> str:
    """Devuelve el nombre del branch actual del proyecto, o '' si no es repo git."""
    if not os.path.isdir(os.path.join(ruta, '.git')):
        return ''
    try:
        result = subprocess.run(
            ['git', 'branch', '--show-current'],
            cwd=ruta, capture_output=True, text=True, timeout=2,
        )
        return result.stdout.strip()
    except Exception:
        return ''


def _calcular_status(seccion: str, trabajando: bool) -> str:
    """Status del proyecto para el ANILLO del orbe en la franja.
    - archived → idle (gris, sin anillo)
    - con agentes TRABAJANDO (nivel agent_watch) → run (anillo verde animado)
    - resto (incluye tener terminales pero quietas) → idle (sin anillo)
    El usuario pidió que el anillo salga SOLO mientras hay agentes trabajando en
    ese workspace y se apague al terminar (no por el mero hecho de tener panes).
    """
    if seccion == 'archived':
        return 'idle'
    return 'run' if trabajando else 'idle'


def _proyectos_trabajando() -> set:
    """PIDs de proyectos con ≥1 terminal 'trabajando' EN NIVEL (agent_watch).
    Fuente única y consistente con el brillo Liquid Glass de las cards."""
    try:
        from plotspace.core import agent_watch
        tids = set(agent_watch.terminales_trabajando())
    except Exception:
        return set()
    if not tids:
        return set()
    conn = get_db()
    try:
        ph = ','.join('?' * len(tids))
        cur = conn.cursor()
        cur.execute(
            f'SELECT DISTINCT project_id FROM terminals WHERE activa = 1 AND id IN ({ph})',
            tuple(tids),
        )
        return {r['project_id'] for r in cur.fetchall()}
    finally:
        conn.close()


@router.get("", response_model=List[ProjectResponse])
async def listar_proyectos():
    """Lista todos los proyectos enriquecidos con branch real, status y agents.
    Orden: primero por sección (pinned > active > archived), después por orden, después por último acceso."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT p.*, COUNT(t.id) AS terminales_activas
            FROM projects p
            LEFT JOIN terminals t ON t.project_id = p.id AND t.activa = 1
            GROUP BY p.id
            ORDER BY
              CASE p.seccion WHEN 'pinned' THEN 0 WHEN 'active' THEN 1 ELSE 2 END,
              p.orden ASC,
              p.id ASC
        ''')
        rows = [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

    # Enriquecer con branch (git) + status. El git de cada proyecto va EN PARALELO y en
    # THREAD: antes era un subprocess git SÍNCRONO por proyecto EN SERIE dentro del handler
    # async → con varios proyectos bloqueaba el event loop al listar (camino de navegación).
    branches = await asyncio.gather(*[asyncio.to_thread(_git_branch_actual, r['ruta']) for r in rows])
    trabajando = _proyectos_trabajando()
    for r, br in zip(rows, branches):
        r['branch'] = br
        r['status'] = _calcular_status(r.get('seccion', 'active'), r['id'] in trabajando)
    return rows


def _contar_terminales_activas() -> dict:
    """{project_id: terminales activas} de TODOS los proyectos. Un solo GROUP BY,
    sin git ni enriquecido — es la fuente del contador de la franja para los
    workspaces que NO son el activo."""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            'SELECT project_id, COUNT(*) AS n FROM terminals WHERE activa = 1 GROUP BY project_id'
        )
        return {r['project_id']: r['n'] for r in cur.fetchall()}
    finally:
        conn.close()


@router.get("/working")
async def proyectos_con_trabajo():
    """PIDs de proyectos con agentes trabajando AHORA (nivel agent_watch) + cuántas
    terminales activas tiene CADA proyecto. BARATO (sin git ni el resto del
    enriquecido) → el frontend lo pollea cada pocos segundos para RECONCILIAR por
    NIVEL (self-healing): el anillo verde del orbe y el CONTADOR de la franja.
    Sin esto, el contador de los workspaces que no son el activo se congelaba en
    el valor del último `GET /api/projects` (nada avisa cuando otro workspace
    abre o cierra terminales) — se veía como "Derlis-APP no muestra terminales".
    Definido ANTES de /{project_id} para no ser sombreado."""
    return {
        "ids": sorted(_proyectos_trabajando()),
        "counts": {str(k): v for k, v in _contar_terminales_activas().items()},
    }


@router.patch("/{project_id}/section", response_model=ProjectResponse)
async def cambiar_seccion_proyecto(project_id: int, datos: SectionPatch):
    """Mueve un proyecto entre Pinned/Active/Archived o cambia su orden interno."""
    if datos.seccion and datos.seccion not in ('pinned', 'active', 'archived'):
        raise HTTPException(status_code=400, detail="Sección inválida")

    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT id, seccion, orden FROM projects WHERE id = ?', (project_id,))
        actual = cursor.fetchone()
        if not actual:
            raise HTTPException(status_code=404, detail="Proyecto no encontrado")

        nueva_seccion = datos.seccion if datos.seccion is not None else actual['seccion']
        nuevo_orden   = datos.orden   if datos.orden   is not None else actual['orden']

        cursor.execute(
            'UPDATE projects SET seccion = ?, orden = ? WHERE id = ?',
            (nueva_seccion, nuevo_orden, project_id),
        )
        conn.commit()

        cursor.execute('SELECT * FROM projects WHERE id = ?', (project_id,))
        row = dict(cursor.fetchone())
        row['terminales_activas'] = 0
        row['branch'] = _git_branch_actual(row['ruta'])
        row['status'] = _calcular_status(row['seccion'], project_id in _proyectos_trabajando())
        await _avisar_projects_update()
        return row
    finally:
        conn.close()


@router.patch("/{project_id}/rename", response_model=ProjectResponse)
async def renombrar_proyecto(project_id: int, datos: RenameRequest):
    """Renombra el proyecto: cambia SÓLO el `nombre` visible en el dashboard.
    NO toca la carpeta en disco — la `ruta` queda intacta (rename cosmético)."""
    nuevo_nombre = datos.nombre.strip()
    if not nuevo_nombre:
        raise HTTPException(status_code=400, detail="El nombre no puede estar vacío")
    if '/' in nuevo_nombre or '\\' in nuevo_nombre:
        raise HTTPException(status_code=400, detail="El nombre no puede contener barras")
    if len(nuevo_nombre) > 200:
        raise HTTPException(status_code=400, detail="Nombre demasiado largo")

    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM projects WHERE id = ?', (project_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Proyecto no encontrado")

        # El rename es SÓLO cosmético: cambia el nombre visible en el dashboard y
        # NUNCA toca la carpeta en disco (la `ruta` queda intacta). Así personalizar
        # el nombre del workspace no puede mover ni romper el proyecto — a diferencia
        # del viejo os.rename, que con el propio repo registrado como proyecto lo
        # movió entero (incidente 2026-07-03). Ver [[blindaje-autodestruccion-projects]].
        cursor.execute(
            'UPDATE projects SET nombre = ? WHERE id = ?',
            (nuevo_nombre, project_id),
        )
        conn.commit()

        cursor.execute('''
            SELECT p.*, COUNT(t.id) AS terminales_activas
            FROM projects p
            LEFT JOIN terminals t ON t.project_id = p.id AND t.activa = 1
            WHERE p.id = ?
            GROUP BY p.id
        ''', (project_id,))
        result = dict(cursor.fetchone())
        result['branch'] = _git_branch_actual(result['ruta'])
        result['status'] = _calcular_status(result.get('seccion', 'active'), project_id in _proyectos_trabajando())
        await _avisar_projects_update()
        return result
    finally:
        conn.close()


@router.post("", response_model=ProjectResponse, status_code=201)
async def crear_proyecto(project: ProjectCreate):
    """Crea un nuevo proyecto. Si la ruta no existe, la crea.
    Falla con error claro si la ruta apunta a una ubicación peligrosa o
    no se puede crear la carpeta."""
    nombre = project.nombre.strip()
    ruta = os.path.abspath(os.path.expanduser(project.ruta.strip()))

    if not nombre:
        raise HTTPException(status_code=400, detail="El nombre no puede estar vacío")
    if not ruta:
        raise HTTPException(status_code=400, detail="La ruta no puede estar vacía")

    # Bloquear paths "peligrosos" (raíz, /home, /etc, etc.)
    # Permitir /home/user (homedir del usuario) pero no /home a secas.
    if ruta in RUTAS_PROHIBIDAS:
        raise HTTPException(
            status_code=400,
            detail=f"La ruta '{ruta}' está en el sistema. Usá una subcarpeta tuya."
        )

    # Auto-crear la carpeta si no existe. Si falla, el usuario lo necesita saber.
    if not os.path.isdir(ruta):
        try:
            os.makedirs(ruta, exist_ok=True)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"No se pudo crear la carpeta '{ruta}': {e}"
            )

    ahora = datetime.now().isoformat()
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO projects (nombre, ruta, fecha_creacion, ultimo_acceso) VALUES (?, ?, ?, ?)',
            (nombre, ruta, ahora, ahora)
        )
        conn.commit()
        project_id = cursor.lastrowid

        cursor.execute('SELECT * FROM projects WHERE id = ?', (project_id,))
        row = dict(cursor.fetchone())
        row['terminales_activas'] = 0
        await _avisar_projects_update()
        return row
    finally:
        conn.close()


@router.get("/{project_id}", response_model=ProjectResponse)
async def obtener_proyecto(project_id: int):
    """Obtiene un proyecto por ID y actualiza el timestamp de acceso"""
    conn = get_db()
    try:
        cursor = conn.cursor()
        ahora = datetime.now().isoformat()

        cursor.execute(
            'UPDATE projects SET ultimo_acceso = ? WHERE id = ?',
            (ahora, project_id)
        )
        conn.commit()

        cursor.execute('''
            SELECT p.*, COUNT(t.id) AS terminales_activas
            FROM projects p
            LEFT JOIN terminals t ON t.project_id = p.id AND t.activa = 1
            WHERE p.id = ?
            GROUP BY p.id
        ''', (project_id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Proyecto no encontrado")

        return dict(row)
    finally:
        conn.close()


@router.delete("/{project_id}")
async def eliminar_proyecto(
    project_id: int,
    keep_folder: bool = Query(False, description="Si true, NO borra la carpeta del disco — solo lo saca del workspace"),
):
    """Elimina un proyecto: DB rows, sesiones tmux, preview server.
    Si keep_folder=false (default), también borra la CARPETA del disco.
    Si keep_folder=true, la carpeta queda intacta en disco y solo lo quita del workspace.

    Orden importante (para que el rmtree no falle en silencio):
      1. Buscar terminal_ids del proyecto
      2. Matar TODAS las sesiones tmux jarvis_{id} (sino mantienen file handles
         abiertos en el proyecto)
      3. Parar el preview server si está activo
      4. Limpiar DB
      5. shutil.rmtree de la carpeta (solo si keep_folder=false)
    """
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT id, ruta FROM projects WHERE id = ?', (project_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Proyecto no encontrado")
        ruta = row['ruta']

        cursor.execute('SELECT id FROM terminals WHERE project_id = ?', (project_id,))
        terminal_ids = [r['id'] for r in cursor.fetchall()]
    finally:
        conn.close()

    # 1. Teardown completo de cada terminal: mata su sesión tmux (sino los
    #    archivos abiertos bloquean rmtree) y, además, para su monitor de
    #    keywords y limpia el proceso de attach en memoria (antes quedaban
    #    polleando sesiones muertas).
    try:
        from plotspace.routers.terminals import teardown_terminal
        for tid in terminal_ids:
            await teardown_terminal(tid)
    except Exception as e:
        print(f'[delete] teardown de terminales falló, mato tmux directo: {e}')
        from plotspace.core.terminal_backend import backend
        for tid in terminal_ids:
            backend().matar_sesion_por_nombre(backend().nombre_sesion(tid))

    # 2. Parar el preview server si está corriendo (lazy import por circular)
    try:
        from plotspace.routers.orchestrator import _detener_preview_si_existe
        _detener_preview_si_existe(project_id)
    except Exception as e:
        print(f'[delete] No pude parar preview: {e}')

    # 3. Limpiar DB
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM terminals WHERE project_id = ?', (project_id,))
        cursor.execute('DELETE FROM project_skills WHERE project_id = ?', (project_id,))
        cursor.execute('DELETE FROM workflows WHERE project_id = ?', (project_id,))
        cursor.execute('DELETE FROM orquestador_historial WHERE project_id = ?', (project_id,))
        cursor.execute('DELETE FROM task_events WHERE project_id = ?', (project_id,))
        cursor.execute('DELETE FROM tasks WHERE project_id = ?', (project_id,))
        cursor.execute('DELETE FROM projects WHERE id = ?', (project_id,))
        conn.commit()
    finally:
        conn.close()

    # 4. Borrar la carpeta del disco (a menos que keep_folder=true)
    folder_deleted = False
    folder_error   = None
    ruta_abs = os.path.abspath(ruta) if ruta else ''

    if keep_folder:
        folder_deleted = False  # explícitamente no se tocó
    elif not ruta_abs or not os.path.isdir(ruta_abs):
        folder_deleted = True  # no había nada que borrar, OK
    elif _es_ruta_protegida(ruta_abs):
        folder_error = f"No borré '{ruta_abs}': es el propio Jarvis o una ruta protegida. Proyecto sí quitado del workspace."
    else:
        # rmtree con onerror para forzar permisos de write si hace falta
        import stat
        def _force_remove(func, path, exc_info):
            try:
                os.chmod(path, stat.S_IWUSR | stat.S_IRUSR | stat.S_IXUSR)
                func(path)
            except Exception as e:
                print(f'[delete] No pude forzar borrado de {path}: {e}')
                raise
        try:
            # En un thread: rmtree de un proyecto con node_modules/ puede tardar
            # decenas de segundos y clavaba el event loop entero.
            await asyncio.to_thread(shutil.rmtree, ruta_abs, onerror=_force_remove)
            folder_deleted = True
            print(f'[delete] Carpeta borrada: {ruta_abs}')
        except Exception as e:
            folder_error = f"Error borrando carpeta: {e}"
            print(f'[delete] {folder_error}')

    await _avisar_projects_update()
    return {
        'ok':             True,
        'folder_deleted': folder_deleted,
        'folder_error':   folder_error,
        'folder_kept':    keep_folder,
        'ruta':           ruta,
    }


class ReorderRequest(BaseModel):
    seccion: str  # 'pinned' | 'active' | 'archived'
    ids:     List[int]


@router.post("/reorder")
async def reordenar_proyectos(req: ReorderRequest):
    """Reasigna seccion y orden a una lista de proyectos en bloque.
    Útil para drag & drop: el frontend manda el nuevo orden completo
    de una sección y atomicamente se actualiza."""
    if req.seccion not in ('pinned', 'active', 'archived'):
        raise HTTPException(status_code=400, detail="Sección inválida")
    conn = get_db()
    try:
        cursor = conn.cursor()
        for idx, pid in enumerate(req.ids):
            cursor.execute(
                'UPDATE projects SET seccion = ?, orden = ? WHERE id = ?',
                (req.seccion, idx, int(pid)),
            )
        conn.commit()
    finally:
        conn.close()
    await _avisar_projects_update()
    return {'ok': True, 'seccion': req.seccion, 'total': len(req.ids)}
