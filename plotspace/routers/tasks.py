# JARVIS — Task board kanban.
# Tareas manuales del usuario que se pueden ASIGNAR a un agente: al asignar,
# la tarea viaja por tmux send-keys a la terminal elegida (con la instrucción
# de TASK_DONE) y el monitor de keywords la mueve sola a Hecho/Bloqueado.
# Los pasos de workflows del orquestador NO se duplican acá: el frontend los
# proyecta en vivo desde los workflow_update que ya emite el broadcaster.

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from plotspace.core.database import get_db

router = APIRouter(prefix="/api", tags=["tasks"])

ESTADOS = ('backlog', 'running', 'blocked', 'done')


class TaskCreate(BaseModel):
    titulo: str
    descripcion: str = ''


class TaskUpdate(BaseModel):
    titulo: Optional[str] = None
    descripcion: Optional[str] = None
    estado: Optional[str] = None


class TaskAsignar(BaseModel):
    terminal_id: int


def _fila(cursor, task_id: int) -> dict:
    cursor.execute('SELECT * FROM tasks WHERE id = ?', (task_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return dict(row)


async def _broadcast_tasks(project_id: int):
    """Avisa al frontend que el board cambió (refetch barato)."""
    from plotspace.core.events import broadcaster
    await broadcaster.broadcast(project_id, {"type": "tasks_update"})


@router.get("/projects/{project_id}/tasks")
async def listar_tasks(project_id: int):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM tasks WHERE project_id = ? ORDER BY orden, id DESC',
            (project_id,),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


@router.post("/projects/{project_id}/tasks", status_code=201)
async def crear_task(project_id: int, datos: TaskCreate):
    titulo = datos.titulo.strip()
    if not titulo:
        raise HTTPException(status_code=400, detail="La tarea necesita un título")
    ahora = datetime.now().isoformat()
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            '''INSERT INTO tasks (project_id, titulo, descripcion, estado, created_at, updated_at)
               VALUES (?, ?, ?, 'backlog', ?, ?)''',
            (project_id, titulo, datos.descripcion.strip(), ahora, ahora),
        )
        conn.commit()
        nueva = _fila(cursor, cursor.lastrowid)
    finally:
        conn.close()
    await _broadcast_tasks(project_id)
    return nueva


@router.patch("/tasks/{task_id}")
async def actualizar_task(task_id: int, datos: TaskUpdate):
    if datos.estado is not None and datos.estado not in ESTADOS:
        raise HTTPException(status_code=400, detail=f"Estado inválido: {datos.estado}")
    conn = get_db()
    try:
        cursor = conn.cursor()
        actual = _fila(cursor, task_id)
        cursor.execute(
            '''UPDATE tasks SET titulo = ?, descripcion = ?, estado = ?,
               terminal_id = CASE WHEN ? IN ('backlog','done') THEN NULL ELSE terminal_id END,
               updated_at = ? WHERE id = ?''',
            (
                (datos.titulo or actual['titulo']).strip(),
                actual['descripcion'] if datos.descripcion is None else datos.descripcion.strip(),
                datos.estado or actual['estado'],
                datos.estado or actual['estado'],
                datetime.now().isoformat(),
                task_id,
            ),
        )
        conn.commit()
        actualizada = _fila(cursor, task_id)
    finally:
        conn.close()
    await _broadcast_tasks(actualizada['project_id'])
    return actualizada


@router.delete("/tasks/{task_id}")
async def borrar_task(task_id: int):
    conn = get_db()
    try:
        cursor = conn.cursor()
        tarea = _fila(cursor, task_id)
        cursor.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
        conn.commit()
    finally:
        conn.close()
    await _broadcast_tasks(tarea['project_id'])
    return {'ok': True}


@router.post("/tasks/{task_id}/asignar")
async def asignar_task(task_id: int, datos: TaskAsignar):
    """Manda la tarea a un agente: send-keys del texto + monitor de TASK_DONE.
    El monitor moverá la card sola (done/blocked) cuando el agente avise."""
    # Lazy import: regla del proyecto para el ciclo orchestrator ↔ terminals
    from plotspace.routers.orchestrator import send_to_agent
    from plotspace.routers.terminals import iniciar_monitor

    conn = get_db()
    try:
        cursor = conn.cursor()
        tarea = _fila(cursor, task_id)
        # La terminal tiene que ser del MISMO proyecto que la tarea.
        cursor.execute(
            'SELECT id FROM terminals WHERE id = ? AND project_id = ? AND activa = 1',
            (datos.terminal_id, tarea['project_id']),
        )
        if not cursor.fetchone():
            raise HTTPException(status_code=404,
                                detail="Terminal no encontrada, inactiva o de otro proyecto")
    finally:
        conn.close()

    texto = tarea['titulo']
    if tarea['descripcion']:
        texto += f"\n\n{tarea['descripcion']}"
    texto += ("\n\nAntes de empezar leé .jarvis/memory/INDEX.md (memoria "
              "compartida del proyecto) y guardá ahí lo que descubras. "
              "Cuando termines escribí TASK_DONE. "
              "Si te bloqueás escribí TASK_BLOCKED.")

    # 'running' SOLO si el pegado llegó: si no, la card quedaría en running
    # sin que ningún agente la haya recibido.
    if not await send_to_agent(datos.terminal_id, texto):
        raise HTTPException(status_code=409,
                            detail="No se pudo enviar la tarea a la terminal (¿sesión caída?)")

    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE tasks SET estado = 'running', terminal_id = ?, updated_at = ? WHERE id = ?",
            (datos.terminal_id, datetime.now().isoformat(), task_id),
        )
        conn.commit()
        tarea = _fila(cursor, task_id)
    finally:
        conn.close()
    iniciar_monitor(datos.terminal_id, tarea['project_id'])

    await _broadcast_tasks(tarea['project_id'])
    return tarea


async def resolver_tasks_por_evento(terminal_id: int, event: str, project_id: int):
    """Hook del monitor de keywords (lazy-llamado desde orchestrator):
    TASK_DONE → done · TASK_BLOCKED/TASK_ERROR → blocked, para las tareas
    running asignadas a esa terminal."""
    nuevo = 'done' if event == 'TASK_DONE' else 'blocked'
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE tasks SET estado = ?, updated_at = ? "
            "WHERE terminal_id = ? AND project_id = ? AND estado = 'running'",
            (nuevo, datetime.now().isoformat(), terminal_id, project_id),
        )
        cambiadas = cursor.rowcount
        conn.commit()
    finally:
        conn.close()
    if cambiadas:
        await _broadcast_tasks(project_id)
