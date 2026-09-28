import asyncio
import os
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from plotspace.core.database import get_db

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


@router.post("/mantenimiento")
async def mantenimiento():
    """Limpieza del estado local: purga task_events, mata sesiones tmux huérfanas y hace VACUUM. Corre en un
    thread (el VACUUM puede tardar). Devuelve un resumen de lo limpiado."""
    from plotspace.core.mantenimiento import ejecutar_mantenimiento
    resumen = await asyncio.to_thread(ejecutar_mantenimiento)
    return {'ok': True, 'resumen': resumen}


@router.get("/{project_id}/state")
async def estado_workspace(project_id: int):
    """Devuelve el estado completo del workspace: proyecto + terminales activas."""
    conn = get_db()
    try:
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM projects WHERE id = ?', (project_id,))
        project = cursor.fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="Proyecto no encontrado")

        ahora = datetime.now().isoformat()
        cursor.execute('UPDATE projects SET ultimo_acceso = ? WHERE id = ?', (ahora, project_id))
        conn.commit()

        cursor.execute(
            'SELECT * FROM terminals WHERE project_id = ? AND activa = 1 ORDER BY fecha_creacion ASC',
            (project_id,)
        )
        terminals = [dict(t) for t in cursor.fetchall()]

        return {
            "project":   dict(project),
            "terminals": terminals,
            "timestamp": ahora,
        }
    finally:
        conn.close()
