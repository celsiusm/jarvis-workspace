"""
Endpoints para plugins de Claude Code y skills .md del proyecto.

Plugins (formato Claude Code):
  - Instalados: ~/.claude/plugins/installed_plugins.json
  - Marketplace: ~/.claude/plugins/marketplaces/claude-plugins-official/{plugins,external_plugins}/*

Skills del proyecto (formato .md):
  - {project_path}/.claude/skills/{nombre}.md   (flat)
  - {project_path}/.claude/skills/{nombre}/SKILL.md  (convención Claude Code)

Toggle de plugins por proyecto se persiste en la tabla project_skills:
  nombre = "{plugin_id}@{marketplace}" (contiene "@")
  activa = 0/1
"""
import json
import os
import re
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from plotspace.core.database import get_db

router = APIRouter(tags=["plugins"])

# Paths
PLUGINS_DIR    = os.path.expanduser('~/.claude/plugins')
INSTALLED_JSON = os.path.join(PLUGINS_DIR, 'installed_plugins.json')
MARKETPLACES   = os.path.join(PLUGINS_DIR, 'marketplaces')


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _humanize(plugin_id: str) -> str:
    """superpowers → Superpowers, frontend-design → Frontend Design"""
    base = plugin_id.split('@')[0]
    return ' '.join(p.capitalize() for p in re.split(r'[-_]', base) if p)


def _leer_installed_plugins() -> dict:
    """Devuelve el JSON crudo de installed_plugins.json o estructura vacía."""
    try:
        if not os.path.isfile(INSTALLED_JSON):
            return {'plugins': {}}
        with open(INSTALLED_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {'plugins': {}}
    except Exception as e:
        print(f'[plugins] Error leyendo installed_plugins.json: {e}')
        return {'plugins': {}}


def _leer_manifest_plugin(plugin_dir: str) -> dict:
    """Lee plugin.json o .claude-plugin/plugin.json. Devuelve {name, description, author} o {}."""
    candidatos = [
        os.path.join(plugin_dir, '.claude-plugin', 'plugin.json'),
        os.path.join(plugin_dir, 'plugin.json'),
    ]
    for ruta in candidatos:
        if os.path.isfile(ruta):
            try:
                with open(ruta, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
            except Exception:
                continue
    return {}


def _preview_md(path: str, max_chars: int = 200) -> str:
    """Lee las primeras líneas no vacías de un .md (saltando frontmatter YAML)."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            texto = f.read()
    except Exception:
        return ''
    # Strip frontmatter
    if texto.startswith('---'):
        m = re.search(r'^---\s*\n.*?\n---\s*\n', texto, flags=re.DOTALL)
        if m:
            texto = texto[m.end():]
    # Tomar primeras 2 líneas no vacías
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    preview = ' '.join(lineas[:3])
    # Sacar headers markdown
    preview = re.sub(r'^#+\s*', '', preview)
    return preview[:max_chars]


# ═══ ENDPOINTS: PLUGINS ══════════════════════════════════════════════════════

@router.get("/api/plugins/instalados")
async def listar_plugins_instalados():
    """Lista plugins instalados desde installed_plugins.json."""
    data = _leer_installed_plugins()
    resultado = []
    for full_id, instancias in (data.get('plugins') or {}).items():
        # full_id es "{plugin_id}@{marketplace}"
        if '@' in full_id:
            plugin_id, marketplace = full_id.split('@', 1)
        else:
            plugin_id, marketplace = full_id, ''
        # Cada plugin puede tener varias instancias; tomamos la primera (user scope)
        instancia = instancias[0] if isinstance(instancias, list) and instancias else {}

        # Buscar descripción en el manifest del marketplace
        manifest_dir = os.path.join(MARKETPLACES, marketplace, 'plugins', plugin_id)
        manifest = _leer_manifest_plugin(manifest_dir) if os.path.isdir(manifest_dir) else {}

        resultado.append({
            'full_id':      full_id,
            'plugin_id':    plugin_id,
            'marketplace':  marketplace,
            'nombre':       _humanize(plugin_id),
            'descripcion':  manifest.get('description', ''),
            'version':      instancia.get('version', 'unknown'),
            'install_path': instancia.get('installPath', ''),
            'installed_at': instancia.get('installedAt', ''),
        })
    resultado.sort(key=lambda x: x['nombre'].lower())
    return resultado


@router.get("/api/plugins/marketplace")
async def listar_marketplace():
    """Lista plugins disponibles en el marketplace oficial + external_plugins."""
    instalados = _leer_installed_plugins().get('plugins') or {}
    instalados_full_ids = set(instalados.keys())

    resultado = []
    if not os.path.isdir(MARKETPLACES):
        return resultado

    for marketplace in sorted(os.listdir(MARKETPLACES)):
        mk_dir = os.path.join(MARKETPLACES, marketplace)
        if not os.path.isdir(mk_dir):
            continue

        # Carpetas a inspeccionar: plugins/ y external_plugins/
        for source_dir, source_tipo in [('plugins', 'official'), ('external_plugins', 'external')]:
            full_source = os.path.join(mk_dir, source_dir)
            if not os.path.isdir(full_source):
                continue

            for plugin_id in sorted(os.listdir(full_source)):
                plugin_dir = os.path.join(full_source, plugin_id)
                if not os.path.isdir(plugin_dir) or plugin_id.startswith('.'):
                    continue

                manifest = _leer_manifest_plugin(plugin_dir)
                if not manifest:
                    # Sin manifest válido — intentar leer README para descripción
                    readme = os.path.join(plugin_dir, 'README.md')
                    descripcion = _preview_md(readme) if os.path.isfile(readme) else ''
                    manifest = {'name': plugin_id, 'description': descripcion}

                full_id = f'{plugin_id}@{marketplace}'
                resultado.append({
                    'full_id':       full_id,
                    'plugin_id':     plugin_id,
                    'marketplace':   marketplace,
                    'source':        source_tipo,
                    'nombre':        _humanize(plugin_id),
                    'descripcion':   manifest.get('description', ''),
                    'instalado':     full_id in instalados_full_ids,
                })

    return resultado


# ═══ ENDPOINTS: TOGGLE PLUGINS POR PROYECTO ══════════════════════════════════

class PluginToggleRequest(BaseModel):
    plugin_id: str   # acepta "superpowers" o "superpowers@claude-plugins-official"
    activo:    bool


def _resolver_full_id(plugin_id: str) -> str:
    """Si plugin_id no trae @marketplace, busca el primero que coincida en installed."""
    if '@' in plugin_id:
        return plugin_id
    instalados = _leer_installed_plugins().get('plugins') or {}
    for full_id in instalados.keys():
        if full_id.startswith(plugin_id + '@'):
            return full_id
    return plugin_id  # devolver tal cual si no se encuentra


@router.get("/api/projects/{project_id}/plugins/activos")
async def listar_plugins_activos(project_id: int):
    """Plugins activos PARA ESTE PROYECTO (filtrados de project_skills donde nombre contiene @)."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT nombre FROM project_skills "
            "WHERE project_id = ? AND activa = 1 AND nombre LIKE '%@%'",
            (project_id,)
        )
        activos = [r['nombre'] for r in cursor.fetchall()]
    finally:
        conn.close()
    return {'activos': activos}


@router.post("/api/projects/{project_id}/plugins/toggle")
async def toggle_plugin(project_id: int, req: PluginToggleRequest):
    """Activa/desactiva un plugin para un proyecto.

    UPSERT atómico vía ON CONFLICT(project_id, nombre). Después del INSERT/UPDATE
    re-consulta la DB y devuelve el estado real para que el frontend se reconcilie."""
    full_id = _resolver_full_id(req.plugin_id)

    # Sólo permitir toggle de plugins efectivamente instalados
    instalados = _leer_installed_plugins().get('plugins') or {}
    if full_id not in instalados:
        raise HTTPException(status_code=404, detail=f"Plugin no instalado: {full_id}")

    plugin_id_corto = full_id.split('@')[0]
    marketplace     = full_id.split('@')[1] if '@' in full_id else ''
    manifest_dir    = os.path.join(MARKETPLACES, marketplace, 'plugins', plugin_id_corto)
    manifest        = _leer_manifest_plugin(manifest_dir) if os.path.isdir(manifest_dir) else {}
    descripcion     = manifest.get('description', '')
    contenido       = f'Plugin activo: **{_humanize(plugin_id_corto)}** — {descripcion}' if req.activo else ''

    from datetime import datetime
    ahora = datetime.now().isoformat()
    activa = 1 if req.activo else 0

    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM projects WHERE id = ?', (project_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Proyecto no encontrado")

        # UPSERT atómico — requiere el UNIQUE INDEX idx_project_skills_unique
        # creado en database.py init_db()
        cursor.execute(
            '''INSERT INTO project_skills (project_id, nombre, descripcion, contenido, activa, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(project_id, nombre) DO UPDATE SET
                   activa      = excluded.activa,
                   descripcion = excluded.descripcion,
                   contenido   = excluded.contenido''',
            (project_id, full_id, descripcion, contenido, activa, ahora)
        )
        conn.commit()

        # Re-leer el estado guardado para devolver la verdad de la DB,
        # no lo que se mandó. Así el frontend nunca asume — verifica.
        cursor.execute(
            'SELECT activa FROM project_skills WHERE project_id = ? AND nombre = ?',
            (project_id, full_id)
        )
        row = cursor.fetchone()
        activo_real = bool(row['activa']) if row else False
    finally:
        conn.close()

    # Refresh de worktrees activos del proyecto (lazy import: evita circular)
    try:
        from plotspace.routers.terminals import refrescar_skills_en_proyecto
        import asyncio
        asyncio.create_task(refrescar_skills_en_proyecto(project_id))
    except Exception as e:
        print(f'[plugins] Error refrescando worktrees: {e}')

    return {'ok': True, 'plugin_id': full_id, 'full_id': full_id, 'activo': activo_real}


# ═══ ENDPOINTS: SKILLS .md DEL PROYECTO ═══════════════════════════════════════

def _project_path(project_id: int) -> str:
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


def _skills_md_dir(project_path: str) -> str:
    return os.path.join(project_path, '.claude', 'skills')


def _listar_skills_md(project_path: str) -> list:
    """Lista archivos de skill .md: flat en .claude/skills/ o {nombre}/SKILL.md."""
    skills_dir = _skills_md_dir(project_path)
    if not os.path.isdir(skills_dir):
        return []
    encontrados = []
    for entry in sorted(os.listdir(skills_dir)):
        entry_path = os.path.join(skills_dir, entry)
        if os.path.isfile(entry_path) and entry.endswith('.md'):
            encontrados.append({
                'nombre':   os.path.splitext(entry)[0],
                'archivo':  entry,
                'tipo':     'flat',
                'path':     entry,
                'preview':  _preview_md(entry_path),
            })
        elif os.path.isdir(entry_path):
            skill_md = os.path.join(entry_path, 'SKILL.md')
            if os.path.isfile(skill_md):
                encontrados.append({
                    'nombre':  entry,
                    'archivo': 'SKILL.md',
                    'tipo':    'folder',
                    'path':    f'{entry}/SKILL.md',
                    'preview': _preview_md(skill_md),
                })
    return encontrados


@router.get("/api/projects/{project_id}/skills-md")
async def listar_skills_md_proyecto(project_id: int):
    """Lista todas las skills .md del proyecto."""
    ruta = _project_path(project_id)
    return {
        'dir':    _skills_md_dir(ruta).replace(ruta, '').lstrip('/'),
        'skills': _listar_skills_md(ruta),
    }


@router.get("/api/projects/{project_id}/skills-md/{nombre}")
async def leer_skill_md(project_id: int, nombre: str):
    """Lee el contenido de una skill .md específica."""
    ruta = _project_path(project_id)
    nombre = nombre.replace('..', '').replace('/', '_')

    skills_dir = _skills_md_dir(ruta)
    # Probar primero flat, después folder
    candidatos = [
        os.path.join(skills_dir, f'{nombre}.md'),
        os.path.join(skills_dir, nombre, 'SKILL.md'),
    ]
    for path in candidatos:
        if os.path.isfile(path):
            try:
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    return {
                        'nombre':  nombre,
                        'path':    os.path.relpath(path, ruta),
                        'content': f.read(),
                    }
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error leyendo skill: {e}")
    raise HTTPException(status_code=404, detail="Skill no encontrada")


class SkillMdSave(BaseModel):
    nombre:  str
    content: str


@router.post("/api/projects/{project_id}/skills-md")
async def guardar_skill_md(project_id: int, datos: SkillMdSave):
    """Crea o sobreescribe una skill .md flat. Refresca worktrees al guardar."""
    ruta = _project_path(project_id)
    nombre = datos.nombre.strip().replace('..', '').replace('/', '_')
    if not nombre:
        raise HTTPException(status_code=400, detail="El nombre no puede estar vacío")
    if not re.match(r'^[a-zA-Z0-9_-]+$', nombre):
        raise HTTPException(status_code=400, detail="Solo letras, números, guiones y underscores")

    skills_dir = _skills_md_dir(ruta)
    os.makedirs(skills_dir, exist_ok=True)
    path = os.path.join(skills_dir, f'{nombre}.md')
    # Skill en formato carpeta ({nombre}/SKILL.md) sin flat homónimo: se edita
    # EN SU LUGAR. Antes se escribía un {nombre}.md nuevo y la skill quedaba
    # duplicada (la carpeta vieja + un flat con la edición).
    carpeta_md = os.path.join(skills_dir, nombre, 'SKILL.md')
    if not os.path.isfile(path) and os.path.isfile(carpeta_md) and not os.path.islink(carpeta_md):
        path = carpeta_md

    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(datos.content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error guardando: {e}")

    # Refresh worktrees
    try:
        from plotspace.routers.terminals import refrescar_skills_en_proyecto
        import asyncio
        asyncio.create_task(refrescar_skills_en_proyecto(project_id))
    except Exception:
        pass

    return {'ok': True, 'nombre': nombre, 'path': os.path.relpath(path, ruta)}


@router.delete("/api/projects/{project_id}/skills-md/{nombre}", status_code=204)
async def eliminar_skill_md(project_id: int, nombre: str):
    """Elimina una skill .md (solo flat — los folders SKILL.md no se tocan)."""
    ruta = _project_path(project_id)
    nombre = nombre.replace('..', '').replace('/', '_')
    path = os.path.join(_skills_md_dir(ruta), f'{nombre}.md')

    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Skill no encontrada")
    try:
        os.remove(path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error eliminando: {e}")

    try:
        from plotspace.routers.terminals import refrescar_skills_en_proyecto
        import asyncio
        asyncio.create_task(refrescar_skills_en_proyecto(project_id))
    except Exception:
        pass


# ═══ ENDPOINTS: DETECCIÓN MULTI-IA (core/skills_ia.py) ════════════════════════

@router.get("/api/projects/{project_id}/skills/detectadas")
async def listar_skills_detectadas(project_id: int, usuario: bool = True):
    """Lo que CADA IA de código va a leer en este proyecto: skills, comandos,
    agentes, reglas e instrucciones de Claude Code, Codex, Gemini CLI,
    Antigravity, Cursor, Qwen, OpenCode, Copilot, Windsurf, Cline y Roo.

    `usuario=true` suma el alcance del home (~/.claude/skills, ~/.codex/prompts…)
    en SOLO lectura. Nunca devuelve cuerpos: sólo descripción corta."""
    import asyncio
    from plotspace.core import skills_ia
    ruta = _project_path(project_id)
    home = os.path.expanduser('~') if usuario else None
    items = await asyncio.to_thread(skills_ia.detectar, ruta, home)
    return {
        'herramientas': skills_ia.resumen(items),
        'catalogo':     skills_ia.HERRAMIENTAS,
        'items':        items,
    }


@router.get("/api/projects/{project_id}/skills/detectadas/contenido")
async def leer_skill_detectada(project_id: int, path: str):
    """Contenido (truncado a 64 KB) de un archivo DETECTADO del proyecto.
    Rechaza cualquier ruta que el detector no liste (y todo el alcance ~/)."""
    import asyncio
    from plotspace.core import skills_ia
    ruta = _project_path(project_id)
    contenido = await asyncio.to_thread(skills_ia.leer_contenido, ruta, path)
    if contenido is None:
        raise HTTPException(status_code=404, detail="Archivo no detectado")
    return {
        'path':     path,
        'content':  contenido,
        'truncado': len(contenido) >= skills_ia.MAX_CONTENIDO,
    }
