# JARVIS — Memoria compartida del proyecto.
# Grafo de conocimiento en `.jarvis/memory/` del proyecto: archivos markdown
# versionables (se commitean con el repo) con wikilinks [[slug]] entre sí.
#
# Cómo lo usan los AGENTES (Claude Code, Codex, Gemini): un protocolo
# inyectado en el CLAUDE.md del proyecto (markers JARVIS_MEMORY_*) les dice
# que lean INDEX.md al arrancar y escriban memorias al descubrir algo.
# Los agentes escriben ARCHIVOS directamente (no esta API); el INDEX se
# regenera acá en cada lectura, así nunca queda stale.
#
# Cómo lo usa el USUARIO: la UI del workspace (sections/memory) consume esta
# API para listar, ver, crear, editar y visualizar el grafo.

import asyncio
import os
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from plotspace.core.database import get_db
from plotspace.core import memoria_categorias as mcat
from plotspace import protocolos

router = APIRouter(prefix="/api", tags=["memory"])

MEM_DIRNAME = os.path.join('.jarvis', 'memory')

_WIKILINK_RE   = re.compile(r'\[\[([^\]\n]+)\]\]')
_FRONT_RE      = re.compile(r'^---\s*\n(.*?)\n---\s*\n?', re.DOTALL)
_SLUG_LIMPIO   = re.compile(r'[^a-z0-9]+')

PROTOCOLO_MARKER_START = '<!-- JARVIS_MEMORY_START -->'
PROTOCOLO_MARKER_END   = '<!-- JARVIS_MEMORY_END -->'

# El TEXTO vive en `plotspace/protocolos/memoria.md`, no acá: es lo que recibe
# cada agente en cada sesión, y el motor Rust tiene que poder inyectar
# exactamente lo mismo. Dos copias de un texto largo se separan sin que nadie lo
# note, y ese día cada agente recibe instrucciones distintas según qué motor le
# armó la sesión. Ver plotspace/protocolos/__init__.py.
PROTOCOLO = protocolos.memoria(mcat.bloque_protocolo())


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _ruta_proyecto(project_id: int) -> str:
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT ruta FROM projects WHERE id = ?', (project_id,))
        row = cursor.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    return row['ruta']


def _mem_dir(project_path: str) -> str:
    return os.path.join(project_path, MEM_DIRNAME)


def _slugify(titulo: str) -> str:
    import unicodedata
    s = unicodedata.normalize('NFKD', titulo).encode('ascii', 'ignore').decode()
    s = _SLUG_LIMPIO.sub('-', s.lower()).strip('-')
    return s[:64] or 'memoria'


def _una_linea(texto: str) -> str:
    """Colapsa saltos de línea/espacios a un espacio: un valor del frontmatter
    con "\n" podría inyectar claves (p.ej. `estado: lapida`)."""
    return ' '.join((texto or '').split())


def _slug_seguro(slug: str) -> str:
    """Anti path-escape: el slug solo puede ser [a-z0-9-]."""
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', slug or ''):
        raise HTTPException(status_code=400, detail=f"Slug inválido: {slug}")
    return slug


def _sin_comillas(v: str) -> str:
    """Título YAML con comillas ("..."/'...') → texto limpio (antes las comillas
    literales se filtraban al INDEX)."""
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        return v[1:-1]
    return v


def _parsear(slug: str, src: str) -> dict:
    """Extrae frontmatter laxo + resumen + wikilinks de una memoria."""
    meta = {'titulo': slug, 'tags': [], 'creado': '', 'autor': '',
            'actualizado': '', 'estado': 'vigente'}
    categoria_raw = ''
    cuerpo = src
    m = _FRONT_RE.match(src)
    if m:
        cuerpo = src[m.end():]
        for linea in m.group(1).splitlines():
            if ':' not in linea:
                continue
            k, v = linea.split(':', 1)
            k, v = k.strip().lower(), v.strip()
            if k == 'titulo' and v:
                meta['titulo'] = _sin_comillas(v)
            elif k == 'tags':
                meta['tags'] = [t.strip() for t in v.strip('[]').split(',') if t.strip()]
            elif k in ('creado', 'fecha'):
                meta['creado'] = v
            elif k == 'autor':
                meta['autor'] = v
            elif k == 'actualizado' and v:
                meta['actualizado'] = v
            elif k == 'estado' and v:
                # vigente (default) | obsoleta | lapida ("X se removió, no reintroducir")
                meta['estado'] = v.lower()
            elif k == 'categoria' and v:
                categoria_raw = v.lower()
    # categoría explícita del frontmatter, o inferida (fallback determinista)
    explicita = bool(categoria_raw) and mcat.es_valida(categoria_raw)
    meta['categoria'] = categoria_raw if explicita else mcat.clasificar(meta['tags'], slug)
    meta['categoria_explicita'] = explicita
    resumen = next((l.strip() for l in cuerpo.splitlines() if l.strip() and not l.startswith('#')), '')
    links = []
    for destino in _WIKILINK_RE.findall(src):
        links.append(_slugify(destino))
    return {
        'slug': slug, **meta,
        'resumen': resumen[:140],
        'links': sorted(set(links)),
    }


def _listar(project_path: str) -> list:
    d = _mem_dir(project_path)
    if not os.path.isdir(d):
        return []
    memorias = []
    for nombre in sorted(os.listdir(d)):
        if not nombre.endswith('.md') or nombre == 'INDEX.md':
            continue
        slug = nombre[:-3]
        try:
            with open(os.path.join(d, nombre), encoding='utf-8') as f:
                memorias.append(_parsear(slug, f.read()))
        except Exception:
            continue
    return memorias


def _linea_index(m: dict) -> str:
    """Línea enriquecida del INDEX: título + #tags + fecha. Le da al agente
    señales de relevancia y frescura sin tener que abrir el archivo (antes
    solo había slug + título). Obsoletas/lápidas se marcan al frente."""
    titulo = m['titulo']
    estado = (m.get('estado') or 'vigente').lower()
    if estado != 'vigente':
        titulo = f"[{estado.upper()}] {titulo}"
    linea = f"- [[{m['slug']}]] — {titulo}"
    extras = []
    if m.get('tags'):
        extras.append(' '.join('#' + t for t in m['tags'][:6]))
    fecha = m.get('actualizado') or m.get('creado')
    if fecha:
        extras.append(fecha)
    if extras:
        linea += ' · ' + ' · '.join(extras)
    return linea + '\n'


def _regenerar_index(project_path: str, memorias: Optional[list] = None):
    """INDEX.md siempre derivado de los archivos: self-healing aunque los
    agentes se olviden de actualizarlo a mano. Compara antes de escribir:
    cero churn de mtime/git cuando nada cambió (apto para el janitor)."""
    d = _mem_dir(project_path)
    os.makedirs(d, exist_ok=True)
    if memorias is None:
        memorias = _listar(project_path)
    lineas = [
        '# Memoria compartida del proyecto\n',
        '<!-- Autogenerado por Jarvis: agrupado por categoría, una línea por memoria. -->\n',
    ]
    # estado: archivo = curada fuera de circulación (crónicas destiladas,
    # redundantes) — no ensucia el INDEX ni el recall, pero el archivo queda.
    activas = [m for m in memorias if (m.get('estado') or 'vigente') != 'archivo']
    archivadas = len(memorias) - len(activas)
    if not activas:
        lineas.append('\n_Vacía todavía: la primera memoria la escribe el primer agente que descubra algo._\n')
    else:
        # Agrupar por categoría: el agente escanea SU sección, no todo el índice.
        por_cat = {}
        for m in activas:
            por_cat.setdefault(m.get('categoria') or mcat.SIN_CLASIFICAR, []).append(m)
        orden = mcat.ORDEN + [mcat.SIN_CLASIFICAR]
        for cid in orden:
            grupo = por_cat.get(cid)
            if not grupo:
                continue
            lineas.append(f"\n## {mcat.NOMBRE.get(cid, cid)} ({len(grupo)})\n")
            for m in sorted(grupo, key=lambda x: x['slug']):
                lineas.append(_linea_index(m))
    if archivadas:
        lineas.append(f'\n_+{archivadas} archivada{"s" if archivadas != 1 else ""} '
                      f'(estado: archivo) — historia curada, no cargar salvo arqueología._\n')
    nuevo = ''.join(lineas)
    path = os.path.join(d, 'INDEX.md')
    try:
        with open(path, encoding='utf-8') as f:
            if f.read() == nuevo:
                return
    except OSError:
        pass
    with open(path, 'w', encoding='utf-8') as f:
        f.write(nuevo)


def _inyectar_protocolo_en(archivo_md: str):
    """Inserta/refresca el bloque del protocolo (markers JARVIS_MEMORY_*) en un
    archivo de instrucciones. Crea el archivo si no existe."""
    contenido = ''
    if os.path.exists(archivo_md):
        with open(archivo_md, encoding='utf-8') as f:
            contenido = f.read()
    if PROTOCOLO_MARKER_START in contenido:
        # Refrescar el bloque por si el protocolo evolucionó
        inicio = contenido.index(PROTOCOLO_MARKER_START)
        fin    = contenido.index(PROTOCOLO_MARKER_END) + len(PROTOCOLO_MARKER_END)
        nuevo  = contenido[:inicio] + PROTOCOLO + contenido[fin:]
    else:
        sep   = '' if (not contenido or contenido.endswith('\n\n')) else ('\n' if contenido.endswith('\n') else '\n\n')
        nuevo = contenido + sep + PROTOCOLO + '\n'
    if nuevo != contenido:
        with open(archivo_md, 'w', encoding='utf-8') as f:
            f.write(nuevo)
        print(f'[memoria] Protocolo inyectado en {archivo_md}')


def asegurar_memoria_proyecto(project_path: str):
    """Hook para _preparar_proyecto (terminals.py): crea .jarvis/memory/ con
    su INDEX e inyecta el protocolo en CLAUDE.md **y AGENTS.md** del proyecto
    (mismo patrón de markers que las skills). AGENTS.md es lo que leen
    Codex/qwen/opencode/grok — con CLAUDE.md solo, medio enjambre no se
    enteraba de que la memoria existe. Idempotente."""
    d = _mem_dir(project_path)
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
        _regenerar_index(project_path, [])

    # Semilla global: las lecciones de entorno (WSL/puertos/git) que valen para
    # CUALQUIER repo — así un proyecto nuevo no arranca amnésico. Idempotente y
    # sin pisar: en el propio Jarvis esos slugs ya existen, no duplica.
    try:
        from plotspace.core.memoria_global import sembrar
        sembrar(project_path)
    except Exception as e:
        print(f'[memoria] No pude sembrar la memoria global: {e}')

    # Repo Knowledge determinista: stack + comandos reales del repo como
    # memoria inicial (solo proyectos con corpus chico; nunca pisa).
    try:
        from plotspace.core.memoria_scan import sembrar_scan
        if sembrar_scan(project_path):
            print(f'[memoria] Scan del repo sembrado en {project_path}')
    except Exception as e:
        print(f'[memoria] No pude sembrar el scan del repo: {e}')

    for nombre in ('CLAUDE.md', 'AGENTS.md'):
        try:
            _inyectar_protocolo_en(os.path.join(project_path, nombre))
        except Exception as e:
            print(f'[memoria] No pude inyectar el protocolo en {nombre}: {e}')

    # Lecciones del enjambre: si el destilador ya escribió lecciones para este
    # proyecto, refrescar su bloque siempre-cargado (no-op si no hay archivo).
    try:
        from plotspace.core.memoria_lecciones import inyectar_lecciones
        inyectar_lecciones(project_path)
    except Exception as e:
        print(f'[memoria] No pude inyectar las lecciones: {e}')


# ─── Endpoints ────────────────────────────────────────────────────────────────

class MemoriaCreate(BaseModel):
    titulo: str
    contenido: str = ''
    tags: list[str] = []


class MemoriaUpdate(BaseModel):
    contenido: str


@router.get("/projects/{project_id}/memory")
async def listar_memorias(project_id: int):
    """Lista + grafo. Regenera INDEX.md de paso (self-healing)."""
    path = _ruta_proyecto(project_id)
    asegurar_memoria_proyecto(path)
    memorias = _listar(path)
    _regenerar_index(path, memorias)
    slugs = {m['slug'] for m in memorias}
    edges = []
    for m in memorias:
        for destino in m['links']:
            if destino in slugs and destino != m['slug']:
                edges.append({'from': m['slug'], 'to': destino})
    return {'memorias': memorias, 'edges': edges}


def _slugs_usados() -> set:
    """Slugs con uso registrado — eximen de la cuarentena: lo que se lee,
    vive (cualquier resultado: leer es leer, aunque el paso haya fallado).
    Fuente primaria la tabla memoria_uso; se suma el audit trail legacy."""
    usados = set()
    try:
        from plotspace.core.database import conteo_uso_memorias
        # 'inyectada' NO exime: que el recall la haya sugerido no prueba que
        # alguien la leyera — solo el cierre de un agente cuenta como lectura.
        usados.update(conteo_uso_memorias(n=5000, excluir='inyectada').keys())
    except Exception:
        pass
    try:
        from plotspace.core import logs
        for ev in logs.leer_recientes(500, tipo='memorias_usadas'):
            usados.update(ev.get('slugs') or [])
    except Exception:
        pass
    return usados


@router.get("/projects/{project_id}/memory/salud")
async def salud_memoria(project_id: int):
    """Lint determinista de la memoria: wikilinks rotos, citas a archivos
    borrados, huérfanas, obsoletas, contrato de admisión, choques
    lápida-vs-vigente y cuarentena. Declarado ANTES de /{slug} para que
    'salud' no lo capture la ruta genérica (gotcha de orden en FastAPI)."""
    from plotspace.core.memoria_lint import lint_memorias
    path = _ruta_proyecto(project_id)
    usados = await asyncio.to_thread(_slugs_usados)
    salud = await asyncio.to_thread(lint_memorias, path, None, usados)
    # Escalera de endurecimiento: lecciones violadas N+ veces → candidatas a
    # guard determinista (lectura del estado persistido, sin recalcular).
    try:
        from plotspace.core.memoria_endurecimiento import candidatas_actuales
        salud['candidatas_guard'] = await asyncio.to_thread(candidatas_actuales)
    except Exception:
        salud['candidatas_guard'] = []
    # Estado del loop de lecciones (por qué corre o no corre el destilador):
    # visible en vez de degradar en silencio.
    try:
        from plotspace.core.memoria_lecciones import estado_lecciones
        salud['lecciones'] = await asyncio.to_thread(estado_lecciones, project_id, path)
    except Exception:
        salud['lecciones'] = {}
    # Candidatas a memoria GLOBAL (lecciones de entorno + reincidencia
    # cruzada): el sistema propone, promover es del usuario.
    try:
        from plotspace.core.memoria_global import sugerir_promociones
        salud['candidatas_global'] = await asyncio.to_thread(
            sugerir_promociones, path, None, None, project_id)
    except Exception:
        salud['candidatas_global'] = []
    # Altímetro: inyectadas vs leídas vs en pasos done (últimos 7 días) —
    # el número que dice si el recall rinde de verdad.
    try:
        from plotspace.core.database import metricas_memoria_uso
        salud['altimetro'] = await asyncio.to_thread(metricas_memoria_uso)
    except Exception:
        salud['altimetro'] = {}
    return salud


@router.get("/projects/{project_id}/memory/{slug}")
async def leer_memoria(project_id: int, slug: str):
    path = _ruta_proyecto(project_id)
    archivo = os.path.join(_mem_dir(path), _slug_seguro(slug) + '.md')
    if not os.path.exists(archivo):
        raise HTTPException(status_code=404, detail="Memoria no encontrada")
    with open(archivo, encoding='utf-8') as f:
        contenido = f.read()
    return {'slug': slug, 'contenido': contenido, **_parsear(slug, contenido)}


@router.post("/projects/{project_id}/memory", status_code=201)
async def crear_memoria(project_id: int, datos: MemoriaCreate):
    # Una línea: un "\n" en el título/tag inyectaría claves al frontmatter.
    titulo = _una_linea(datos.titulo)
    if not titulo:
        raise HTTPException(status_code=400, detail="La memoria necesita un título")
    path = _ruta_proyecto(project_id)
    asegurar_memoria_proyecto(path)
    slug = _slugify(titulo)
    archivo = os.path.join(_mem_dir(path), slug + '.md')
    if os.path.exists(archivo):
        raise HTTPException(status_code=409, detail=f"Ya existe una memoria '{slug}' — editala")
    lista_tags = [t for t in (_una_linea(x).replace(',', ' ').replace('[', '')
                              .replace(']', '').strip() for x in datos.tags) if t]
    tags = ', '.join(lista_tags)
    # Contrato de admisión (scripts/guard_memoria.py): categoria + resumen +
    # actualizado + estado — sin eso el pre-commit bloquea la memoria.
    categoria = mcat.clasificar(lista_tags, slug)
    # Sin tags el guard también la rechaza (`tags:` vacío): la categoría
    # inferida sirve de tag mínimo.
    if not lista_tags and categoria:
        lista_tags = [categoria]
        tags = categoria
    cuerpo = datos.contenido.strip()
    resumen = next((_una_linea(l).lstrip('#').strip() for l in cuerpo.splitlines()
                    if l.strip()), '') or titulo
    hoy = datetime.now().strftime('%Y-%m-%d')
    src = (f"---\ntitulo: {titulo}\ntags: [{tags}]\n"
           f"categoria: {categoria}\nresumen: {resumen}\n"
           f"creado: {hoy}\nactualizado: {hoy}\nautor: usuario\n"
           f"estado: vigente\n---\n\n"
           f"{cuerpo}\n")
    # Reconciliación al escribir: si ya hay una memoria vigente que cubre este
    # tema, avisar (la UI puede sugerir "actualizá esa"). No bloquea.
    try:
        from plotspace.core.memoria_lint import similares
        parecidas = [s for s in similares(path, titulo + ' ' + datos.contenido)
                     if s != slug]
    except Exception:
        parecidas = []
    with open(archivo, 'w', encoding='utf-8') as f:
        f.write(src)
    _regenerar_index(path)
    return {'slug': slug, 'similares': parecidas}


def _bumpear_actualizado(contenido: str, hoy: str) -> str:
    """Setea/reemplaza `actualizado: <hoy>` en el frontmatter. Sin frontmatter
    no toca nada. Puro: hace visible la frescura de cada memoria (antes una
    edición de julio pesaba igual que una intacta de junio)."""
    m = _FRONT_RE.match(contenido)
    if not m:
        return contenido
    front = m.group(1)
    lineas = front.splitlines()
    idx_act = next((i for i, l in enumerate(lineas)
                    if l.split(':', 1)[0].strip().lower() == 'actualizado'), None)
    if idx_act is not None:
        lineas[idx_act] = f'actualizado: {hoy}'
    else:
        idx_creado = next((i for i, l in enumerate(lineas)
                           if l.split(':', 1)[0].strip().lower() in ('creado', 'fecha')), None)
        pos = (idx_creado + 1) if idx_creado is not None else len(lineas)
        lineas.insert(pos, f'actualizado: {hoy}')
    return contenido[:m.start(1)] + '\n'.join(lineas) + contenido[m.end(1):]


@router.put("/projects/{project_id}/memory/{slug}")
async def actualizar_memoria(project_id: int, slug: str, datos: MemoriaUpdate):
    path = _ruta_proyecto(project_id)
    archivo = os.path.join(_mem_dir(path), _slug_seguro(slug) + '.md')
    if not os.path.exists(archivo):
        raise HTTPException(status_code=404, detail="Memoria no encontrada")
    contenido = _bumpear_actualizado(datos.contenido, datetime.now().strftime('%Y-%m-%d'))
    with open(archivo, 'w', encoding='utf-8') as f:
        f.write(contenido)
    _regenerar_index(path)
    return {'ok': True}


@router.delete("/projects/{project_id}/memory/{slug}")
async def borrar_memoria(project_id: int, slug: str):
    path = _ruta_proyecto(project_id)
    archivo = os.path.join(_mem_dir(path), _slug_seguro(slug) + '.md')
    if not os.path.exists(archivo):
        raise HTTPException(status_code=404, detail="Memoria no encontrada")
    os.remove(archivo)
    _regenerar_index(path)
    return {'ok': True}


@router.post("/projects/{project_id}/memory/{slug}/promover")
async def promover_memoria(project_id: int, slug: str):
    """Promueve una lección local a la memoria global — la heredan los proyectos
    NUEVOS del workspace (semilla cross-proyecto)."""
    path = _ruta_proyecto(project_id)
    from plotspace.core.memoria_global import promover
    ok = await asyncio.to_thread(promover, _slug_seguro(slug), path)
    if not ok:
        raise HTTPException(status_code=404, detail="Memoria no encontrada o no se pudo promover")
    return {'ok': True, 'slug': slug}
