# JARVIS — Review Room.
# La decisión de ship con los ojos en el diff: qué archivos cambiaron en el
# proyecto (working tree vs HEAD), el diff completo coloreable, y el botón
# "Aprobar y commitear". Complementa al Reviewer del swarm: el agente revisa
# calidad; acá el HUMANO ve y decide el ship.

import os
import re
import subprocess
from fastapi import APIRouter, Body, HTTPException, Query

from plotspace.core.database import get_db

router = APIRouter(prefix="/api", tags=["review"])

MAX_DIFF_BYTES = 400_000       # cap defensivo: el diff GLOBAL se trunca para la UI
MAX_DIFF_FILE_BYTES = 80_000   # cap por archivo en la vista por-agente
MAX_UNTRACKED = 40             # tope de untracked a diffear (no reventar con node_modules)


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


def _git(cwd: str, *args: str) -> tuple[int, str]:
    """git síncrono (regla del proyecto: subprocess.run para git de control).
    timeout=15s: un `git diff` enorme o un repo trabado no debe colgar el handler/loop."""
    try:
        r = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, timeout=15)
        return r.returncode, (r.stdout or '') + (r.stderr or '')
    except subprocess.TimeoutExpired:
        return -1, 'git timeout'
    except Exception as e:
        return -1, str(e)


# ─── Atribución archivo→agente (lógica PURA, testeable sin git/DB) ────────────

def _norm_path(p: str) -> str:
    """Clave de comparación de paths entre git y agent_live: una CLI imprime
    './a.py' y git da 'a.py' — sin normalizar serían dos archivos distintos."""
    p = (p or '').strip()
    if p.startswith('./'):
        p = p[2:]
    return p


def duenos_desde_snapshot(snap: dict) -> list:
    """snapshot de agent_live → [{'terminal_id','nombre','archivos':[path,...]}]
    con SOLO los archivos de los que el agente es DUEÑO (no los que solo leyó).
    Esa es la fuente de atribución: el dueño de un archivo es quien lo escribió
    primero (ver plotspace/core/agent_live.py)."""
    out = []
    for a in snap.get('agentes', []):
        archivos = [f['path'] for f in a.get('archivos', []) if f.get('dueno')]
        out.append({'terminal_id': a['terminal_id'], 'nombre': a['nombre'],
                    'archivos': archivos})
    return out


def atribuir_cambios(archivos_cambiados: list, duenos: list) -> dict:
    """Agrupa los archivos cambiados del working tree por agente dueño.

    archivos_cambiados: [{'path':..., 'status':..., 'diff':...}] (dicts opacos;
        se agrupan por 'path' y se preservan tal cual — el 'diff' viaja adentro).
    duenos: [{'terminal_id':N, 'nombre':str, 'archivos':[path,...]}] — el dueño
        de cada archivo según el snapshot de agent_live.

    Devuelve {'agentes':[{'terminal_id','nombre','archivos':[...]}], 'sin_atribuir':[...]}.
    - Solo aparecen agentes con ≥1 archivo realmente cambiado.
    - Los agentes salen en el orden de `duenos`; sus archivos, en el orden de
      aparición en `archivos_cambiados`.
    - Un archivo cambiado sin dueño vigente va a 'sin_atribuir'."""
    indice = {}  # path normalizado → terminal_id (primer dueño gana)
    for d in duenos:
        for path in d.get('archivos', []):
            indice.setdefault(_norm_path(path), d['terminal_id'])

    buckets: dict = {}   # terminal_id → [file dict, ...]
    sin_atribuir = []
    for f in archivos_cambiados:
        tid = indice.get(_norm_path(f.get('path')))
        if tid is None:
            sin_atribuir.append(f)
        else:
            buckets.setdefault(tid, []).append(f)

    agentes = []
    emitidos = set()
    for d in duenos:
        tid = d['terminal_id']
        if tid in emitidos:
            continue
        emitidos.add(tid)
        archs = buckets.get(tid)
        if archs:
            agentes.append({'terminal_id': tid, 'nombre': d['nombre'],
                            'archivos': archs})
    return {'agentes': agentes, 'sin_atribuir': sin_atribuir}


def _duenos_para(project_id: int) -> list:
    """Mapeo dueño→archivos del proyecto, leído del estado VIVO de agent_live.
    Aislado en una función para poder mockearlo en los tests (seam)."""
    from plotspace.core import agent_live
    rows = agent_live._rows_de(project_id)
    snap = agent_live.snapshot(project_id, rows)
    return duenos_desde_snapshot(snap)


def _cap_diff(diff: str) -> str:
    """Trunca el diff de UN archivo para no inflar el payload de la UI."""
    b = diff.encode()
    if len(b) <= MAX_DIFF_FILE_BYTES:
        return diff
    return b[:MAX_DIFF_FILE_BYTES].decode(errors='ignore') + \
        '\n\n… (diff truncado para la UI — revisalo completo en una terminal)'


def _path_relativo_seguro(base: str, rel: str):
    """Anti-traversal: devuelve el path relativo LIMPIO si cae dentro del
    proyecto (léxico + symlinks), o None. Reusa el patrón de projects_files._safe_join."""
    rel = (rel or '').replace('\\', '/').lstrip('/')
    if not rel:
        return None
    full = os.path.normpath(os.path.join(base, rel))
    base_norm = os.path.normpath(base)
    if full != base_norm and not full.startswith(base_norm + os.sep):
        return None
    real_full = os.path.realpath(full)
    real_base = os.path.realpath(base)
    if real_full != real_base and not real_full.startswith(real_base + os.sep):
        return None
    return os.path.relpath(full, base_norm)


@router.get("/projects/{project_id}/review/by-agent")
async def review_por_agente(project_id: int):
    """Mismo diff que GET /review pero SEGMENTADO por agente: atribuye cada
    archivo cambiado a su dueño (snapshot de agent_live) y agrupa. Los archivos
    sin dueño vigente caen en 'sin_atribuir'."""
    cwd = _ruta_proyecto(project_id)

    rc, _ = _git(cwd, 'rev-parse', '--git-dir')
    if rc != 0:
        raise HTTPException(status_code=400, detail="El proyecto no es un repo git")

    archivos_cambiados = []

    # Tracked: working tree vs HEAD (staged + unstaged), igual semántica que GET /review.
    # core.quotepath=false: paths unicode sin escapar a octal.
    _, name_status = _git(cwd, '-c', 'core.quotepath=false',
                          'diff', 'HEAD', '--name-status')
    for linea in name_status.splitlines():
        partes = linea.split('\t')
        if len(partes) < 2 or not partes[0]:
            continue
        status = partes[0][0]      # R100/C75 → 'R'/'C'; M/A/D/T quedan igual
        path = partes[-1].strip()  # en rename, el último campo es el destino
        if not path:
            continue
        _, diff = _git(cwd, 'diff', 'HEAD', '--no-color', '--', path)
        archivos_cambiados.append({'path': path, 'status': status,
                                   'diff': _cap_diff(diff)})

    # Untracked: archivos nuevos completos (status '??', como en git status --porcelain).
    _, porcelain = _git(cwd, '-c', 'core.quotepath=false', 'status', '--porcelain')
    untracked = [l[3:].strip() for l in porcelain.splitlines()
                 if l.startswith('??') and len(l) > 3]
    for path in untracked[:MAX_UNTRACKED]:
        _, diff = _git(cwd, 'diff', '--no-color', '--no-index', '/dev/null', path)
        archivos_cambiados.append({'path': path, 'status': '??',
                                   'diff': _cap_diff(diff)})

    duenos = _duenos_para(project_id)
    return atribuir_cambios(archivos_cambiados, duenos)


@router.post("/projects/{project_id}/review/commit")
async def commit_por_agente(project_id: int, body: dict = Body(default=None)):
    """Commitea SOLO los archivos indicados (git add explícito + commit acotado
    a esos paths). Respeta la regla del árbol compartido: NUNCA `git add -A`, así
    no se barre el trabajo a medio hacer de otros agentes. Los hooks corren (sin
    --no-verify): el candado de propiedad/secretos sigue activo."""
    body = body or {}
    archivos = body.get('archivos') or []
    mensaje = str(body.get('mensaje') or '').strip()

    cwd = _ruta_proyecto(project_id)

    rc, _ = _git(cwd, 'rev-parse', '--git-dir')
    if rc != 0:
        raise HTTPException(status_code=400, detail="El proyecto no es un repo git")
    if not isinstance(archivos, list) or not archivos:
        raise HTTPException(status_code=400, detail="Faltan archivos para commitear")
    if not mensaje:
        raise HTTPException(status_code=400, detail="Falta el mensaje de commit")

    # Anti-traversal: cada path debe caer dentro del proyecto.
    rels = []
    for raw in archivos:
        rel = _path_relativo_seguro(cwd, str(raw))
        if rel is None:
            raise HTTPException(status_code=400,
                                detail=f"Ruta fuera del proyecto: {raw}")
        rels.append(rel)

    # git add explícito de ESOS archivos.
    rc, out = _git(cwd, 'add', '--', *rels)
    if rc != 0:
        return {"ok": False, "error": out.strip() or "git add falló"}

    # Commit acotado a esos paths (no arrastra otra cosa staged por otro agente).
    rc, out = _git(cwd, 'commit', '-m', mensaje, '--', *rels)
    if rc != 0:
        return {"ok": False, "error": out.strip() or "git commit falló"}

    _, hash_ = _git(cwd, 'rev-parse', '--short', 'HEAD')
    return {"ok": True, "commit": hash_.strip()}


# TODO v2: revert (git restore --source=HEAD -- <paths> / git checkout) + PR (gh pr create)


@router.get("/projects/{project_id}/review")
async def estado_review(project_id: int):
    """Archivos cambiados + diff unificado del working tree contra HEAD
    (incluye untracked vía intent-to-add virtual con --no-index fallback)."""
    cwd = _ruta_proyecto(project_id)

    rc, _ = _git(cwd, 'rev-parse', '--git-dir')
    if rc != 0:
        raise HTTPException(status_code=400, detail="El proyecto no es un repo git")

    # Lista de archivos con su estado
    _, porcelain = _git(cwd, 'status', '--porcelain')
    archivos = []
    untracked = []
    for linea in porcelain.splitlines():
        if len(linea) < 4:
            continue
        estado, path = linea[:2], linea[3:].strip()
        if estado == '??':
            untracked.append(path)
            archivos.append({'path': path, 'estado': 'A', 'untracked': True})
        else:
            letra = (estado.strip() or 'M')[0]
            archivos.append({'path': path, 'estado': letra, 'untracked': False})

    # Diff de tracked (working tree vs HEAD)
    _, diff = _git(cwd, 'diff', 'HEAD', '--no-color')

    # Untracked: mostrarlos como archivos nuevos completos
    for path in untracked[:40]:  # cap: no reventar con node_modules sueltos
        rc2, d2 = _git(cwd, 'diff', '--no-color', '--no-index', '/dev/null', path)
        if d2.strip():
            diff += '\n' + d2

    truncado = False
    if len(diff.encode()) > MAX_DIFF_BYTES:
        diff = diff.encode()[:MAX_DIFF_BYTES].decode(errors='ignore')
        diff += '\n\n… (diff truncado para la UI — revisalo completo en una terminal)'
        truncado = True

    # Stats por archivo (numstat: añadidas/borradas)
    stats = {}
    _, numstat = _git(cwd, 'diff', 'HEAD', '--numstat')
    for linea in numstat.splitlines():
        partes = linea.split('\t')
        if len(partes) == 3:
            mas, menos, path = partes
            stats[path] = {'mas': mas, 'menos': menos}
    for a in archivos:
        s = stats.get(a['path'], {})
        a['mas']   = s.get('mas', '·')
        a['menos'] = s.get('menos', '·')

    # Branch + último commit para contexto
    _, branch = _git(cwd, 'branch', '--show-current')
    _, ultimo = _git(cwd, 'log', '--oneline', '-1')

    return {
        'branch':   branch.strip(),
        'ultimo':   ultimo.strip(),
        'archivos': archivos,
        'diff':     diff,
        'truncado': truncado,
        'limpio':   not archivos,
    }


# ─── Editor: gutter de cambios + blame por línea ─────────────────────────────

def _parse_diff_hunks(diff: str) -> list:
    """Parsea `git diff --unified=0` y devuelve rangos del archivo NUEVO:
    [{'start','end','type'}] con type add|mod|del. Puro y testeable."""
    hunks = []
    cur = None

    def cerrar(h):
        start = int(h['start']); count = int(h['count'])
        if count <= 0:
            ln = max(1, start)          # borrado puro: marcar la línea donde cayó
            return {'start': ln, 'end': ln, 'type': 'del'}
        tipo = 'add' if (h['mas'] and not h['menos']) else 'mod'
        return {'start': start, 'end': start + count - 1, 'type': tipo}

    for ln in diff.split('\n'):
        if ln.startswith('@@'):
            if cur:
                hunks.append(cerrar(cur))
            m = re.match(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', ln)
            cur = ({'start': int(m.group(1)), 'count': int(m.group(2) or 1),
                    'mas': 0, 'menos': 0} if m else None)
        elif cur is not None and ln[:1] in ('+', '-'):
            cur['mas' if ln[0] == '+' else 'menos'] += 1
    if cur:
        hunks.append(cerrar(cur))
    return hunks


def _parse_blame(out: str) -> dict:
    """Extrae autor/tiempo/resumen del `git blame --porcelain -L n,n`."""
    lineas = out.split('\n')
    if not lineas or not lineas[0]:
        return {}
    info = {'hash': lineas[0].split(' ')[0][:8], 'autor': '',
            'tiempo': 0, 'resumen': ''}
    for ln in lineas[1:]:
        if ln.startswith('author '):
            info['autor'] = ln[7:].strip()
        elif ln.startswith('author-time '):
            try:
                info['tiempo'] = int(ln[12:].strip())
            except ValueError:
                pass
        elif ln.startswith('summary '):
            info['resumen'] = ln[8:].strip()
        elif ln.startswith('\t'):
            break
    return info


@router.get("/projects/{project_id}/files/git-diff")
async def git_diff_archivo(project_id: int, path: str = Query(...)):
    """Rangos de líneas cambiadas de UN archivo vs HEAD (gutter del editor)."""
    cwd = _ruta_proyecto(project_id)
    rel = _path_relativo_seguro(cwd, path)
    if rel is None:
        raise HTTPException(status_code=400, detail="Ruta fuera del proyecto")
    rc, _ = _git(cwd, 'rev-parse', '--git-dir')
    if rc != 0:
        return {'git': False, 'hunks': []}

    rc_track, _ = _git(cwd, 'ls-files', '--error-unmatch', '--', rel)
    if rc_track != 0:   # untracked: el archivo entero es nuevo
        full = os.path.join(cwd, rel)
        if os.path.isfile(full):
            try:
                with open(full, 'r', encoding='utf-8', errors='replace') as f:
                    n = sum(1 for _ in f)
            except OSError:
                n = 0
            return {'git': True, 'hunks': [{'start': 1, 'end': max(1, n), 'type': 'add'}]}
        return {'git': True, 'hunks': []}

    _, diff = _git(cwd, '-c', 'core.quotepath=false',
                   'diff', '--unified=0', '--no-color', 'HEAD', '--', rel)
    return {'git': True, 'hunks': _parse_diff_hunks(diff)}


@router.get("/projects/{project_id}/files/git-blame")
async def git_blame_linea(project_id: int, path: str = Query(...), line: int = Query(1)):
    """Blame de UNA línea (blame inline del editor)."""
    cwd = _ruta_proyecto(project_id)
    rel = _path_relativo_seguro(cwd, path)
    if rel is None:
        raise HTTPException(status_code=400, detail="Ruta fuera del proyecto")
    rc, _ = _git(cwd, 'rev-parse', '--git-dir')
    if rc != 0:
        return {'git': False}
    ln = max(1, int(line))
    rc, out = _git(cwd, 'blame', '--porcelain', '-L', f'{ln},{ln}', '--', rel)
    if rc != 0:
        return {'git': False}
    return {'git': True, 'blame': _parse_blame(out)}


# NOTA: el endpoint POST /review/aprobar (git add -A + commit) se quitó: hacía
# justo lo que el CLAUDE.md prohíbe ("NUNCA git add -A") y el hook pre-commit
# bloquea, así que estaba roto por diseño en el árbol compartido sobre main. La
# Review Room queda como VISOR de diff (GET de arriba); el commit lo hace cada
# agente con sus archivos explícitos.
