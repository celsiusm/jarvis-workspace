"""
memoria_lecciones — el destilador de lecciones del enjambre (patrón wb_gusto).

Cierra el loop de aprendizaje del sistema de memoria: los motivos de
TASK_BLOCKED/TASK_ERROR que la capa Captura persiste en `task_events` son
señales directas de QUÉ le sale mal al enjambre. Este módulo las junta por
proyecto y un destilador (API Anthropic, modelo barato) mantiene
`.jarvis/memory/lecciones-del-enjambre.md`: ≤20 reglas cortas de prevención
que se inyectan entre markers en el CLAUDE.md y AGENTS.md del proyecto — las
lecciones se cargan SIEMPRE en cada sesión de cada agente, no son opt-in.
Error → motivo → lección → regla siempre activa → no se repite.

Disparo: `tal_vez_destilar_todos()` desde el janitor (poller 30 min), con
UMBRAL de señales nuevas para no llamar a la API por cada tropiezo suelto.
Sin ANTHROPIC_API_KEY degrada en silencio (mismo diseño que wb_gusto).
Flags: `MEMORIA_LECCIONES` (on) · `MEMORIA_LECCIONES_MODEL` (claude-haiku-4-5)
· `MEMORIA_LECCIONES_UMBRAL` (6).
"""
import json
import os
import re
from datetime import datetime
from typing import Optional

from plotspace.core.database import get_db
from plotspace.core.datadir import ruta_data

LECCIONES_BASENAME = 'lecciones-del-enjambre.md'
ESTADO_PATH  = ruta_data('memoria-lecciones.json')
MARKER_START = '<!-- JARVIS_LECCIONES_START -->'
MARKER_END   = '<!-- JARVIS_LECCIONES_END -->'
ENCABEZADO   = '# Lecciones del enjambre'
MODELO  = os.environ.get('MEMORIA_LECCIONES_MODEL', 'claude-haiku-4-5')


def _umbral_default() -> int:
    try:
        return max(1, int(os.environ.get('MEMORIA_LECCIONES_UMBRAL', '6')))
    except ValueError:
        return 6


def _activo() -> bool:
    return os.environ.get('MEMORIA_LECCIONES', 'on').lower() != 'off'


def _ruta_lecciones(project_path: str) -> str:
    return os.path.join(project_path, '.jarvis', 'memory', LECCIONES_BASENAME)


# ─── Señales: los motivos de fallo persistidos por la capa Captura ───────────

def senales_nuevas(project_id: int, desde_id: int = 0) -> tuple:
    """(señales, max_id) — motivos con id > desde_id (cronológicos): fallos
    (BLOCKED/ERROR) y también ÉXITOS (TASK_DONE con motivo — el agente puede
    dejar en una línea el enfoque que funcionó; patrón SWE-Exp: las
    trayectorias exitosas enseñan tanto como las fallidas). El texto lleva el
    contexto mínimo para que el destilador entienda el patrón sin abrir nada."""
    conn = get_db()
    try:
        filas = conn.execute(
            "SELECT id, event, terminal_id, motivo FROM task_events "
            "WHERE project_id = ? AND id > ? AND motivo IS NOT NULL AND motivo != '' "
            "AND event IN ('TASK_BLOCKED', 'TASK_ERROR', 'TASK_DONE') ORDER BY id",
            (project_id, desde_id)).fetchall()
    finally:
        conn.close()
    senales, max_id = [], desde_id
    for f in filas:
        senales.append(f"{f['event']} (terminal {f['terminal_id']}): {f['motivo'][:400]}")
        max_id = max(max_id, f['id'])
    return senales, max_id


# ─── Estado (qué señales ya se destilaron) ───────────────────────────────────

def _leer_estado(estado_path: Optional[str] = None) -> dict:
    try:
        with open(estado_path or ESTADO_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _guardar_estado(estado: dict, estado_path: Optional[str] = None):
    path = estado_path or ESTADO_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(estado, f)


# ─── Destilación por DELTAS (patrón ACE: nunca reescribir el playbook) ───────
# Reescribir el archivo entero en cada ciclo erosiona detalle (context
# collapse) y el resumen agresivo pierde reglas. El destilador opera con
# operaciones puntuales sobre una lista numerada; el rewrite completo queda
# como fallback de parsing.

_MAX_REGLAS   = 20
_OP_AGREGAR   = re.compile(r'^\s*AGREGAR:\s*(.+)$')
_OP_REEMPLAZAR = re.compile(r'^\s*REEMPLAZAR\s+(\d+):\s*(.+)$')
_OP_QUITAR    = re.compile(r'^\s*QUITAR\s+(\d+)\s*$')


def reglas_de(cuerpo: Optional[str]) -> list:
    """Las reglas del archivo de lecciones: líneas '- ...' sin el guion."""
    out = []
    for linea in (cuerpo or '').splitlines():
        s = linea.strip()
        if s.startswith('- ') and len(s) > 2:
            out.append(s[2:].strip())
    return out


def aplicar_deltas(reglas: list, salida: str) -> tuple:
    """Aplica las operaciones del destilador sobre la lista de reglas.
    Devuelve (reglas_nuevas, n_operaciones). Robusto: líneas no-operación se
    ignoran; índices fuera de rango también. Dedup exacto; cap _MAX_REGLAS
    tirando lo más viejo (grow-and-refine)."""
    quitar, reemplazos, agregar = set(), {}, []
    ops = 0
    for linea in (salida or '').splitlines():
        m = _OP_AGREGAR.match(linea)
        if m:
            agregar.append(m.group(1).strip())
            ops += 1
            continue
        m = _OP_REEMPLAZAR.match(linea)
        if m and 1 <= int(m.group(1)) <= len(reglas):
            reemplazos[int(m.group(1))] = m.group(2).strip()
            ops += 1
            continue
        m = _OP_QUITAR.match(linea)
        if m and 1 <= int(m.group(1)) <= len(reglas):
            quitar.add(int(m.group(1)))
            ops += 1
    nuevas = [reemplazos.get(i, r) for i, r in enumerate(reglas, 1) if i not in quitar]
    for a in agregar:
        if a and a not in nuevas:
            nuevas.append(a)
    return nuevas[-_MAX_REGLAS:], ops


def armar_prompt(existente: Optional[str], senales: list) -> str:
    reglas = reglas_de(existente)
    actual = ('\n'.join(f'{i}. {r}' for i, r in enumerate(reglas, 1))
              if reglas else '(ninguna todavía)')
    lista = '\n'.join(f'- {s}' for s in senales)
    return f"""Sos el curador de las "Lecciones del enjambre" de un equipo de agentes de código que trabajan en paralelo sobre un repo: una lista de hasta {_MAX_REGLAS} reglas cortas extraídas de sus fallos y aciertos reales.

Reglas actuales (numeradas):
{actual}

Señales nuevas (cronológicas; TASK_BLOCKED/TASK_ERROR = fallos, TASK_DONE = enfoques que FUNCIONARON):
{lista}

Respondé SOLO con operaciones, una por línea (nada más):
AGREGAR: <regla nueva — UNA línea imperativa y accionable, opcionalmente el porqué tras un guion>
REEMPLAZAR <n>: <texto afinado de la regla n (para fusionar un patrón repetido)>
QUITAR <n>
NADA   (si ninguna señal amerita cambio)

Criterios:
- Solo lecciones REUTILIZABLES: un patrón que otro agente pueda evitar o repetir con una regla corta. Un fallo puntual e irrepetible (typo, red caída una vez) NO es lección: ignoralo.
- Si varias señales repiten el patrón de una regla existente, REEMPLAZÁ esa regla afinándola — no dupliques.
- De un ÉXITO (TASK_DONE) solo sale regla si revela una práctica no-obvia y reutilizable.
- Máximo {_MAX_REGLAS} reglas en total: si vas a superar el tope, QUITÁ la menos valiosa.
- Operá por deltas puntuales — no intentes reescribir todo.
- Si una señal es ambigua o le falta contexto, no inventes una regla."""


def _llamar_destilador(prompt: str) -> str:
    """Llamada sync a la API (el caller corre en thread del janitor).
    Aislada para poder mockearla en tests."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'), timeout=120.0)
    r = client.messages.create(model=MODELO, max_tokens=1500,
                               messages=[{'role': 'user', 'content': prompt}])
    return ''.join(b.text for b in r.content if getattr(b, 'type', '') == 'text').strip()


def _cuerpo_actual(project_path: str) -> Optional[str]:
    try:
        with open(_ruta_lecciones(project_path), encoding='utf-8') as f:
            src = f.read()
        # sacar el frontmatter: el destilador ve solo el markdown de reglas
        i = src.find(ENCABEZADO)
        return src[i:] if i >= 0 else src
    except OSError:
        return None


def destilar_ahora(project_id: int, project_path: str,
                   estado_path: Optional[str] = None) -> dict:
    """Destila las señales nuevas del proyecto y reescribe la memoria de
    lecciones + los bloques inyectados. Nunca levanta: {'ok': bool, ...}."""
    if not os.environ.get('ANTHROPIC_API_KEY'):
        return {'ok': False, 'motivo': 'sin ANTHROPIC_API_KEY'}
    estado = _leer_estado(estado_path)
    desde = int(estado.get(str(project_id), {}).get('ultimo_evento_id', 0))
    senales, max_id = senales_nuevas(project_id, desde)
    if not senales:
        return {'ok': False, 'motivo': 'sin señales nuevas'}
    reglas = reglas_de(_cuerpo_actual(project_path))
    try:
        texto = _llamar_destilador(armar_prompt(_cuerpo_actual(project_path), senales))
    except Exception as e:
        return {'ok': False, 'motivo': f'API: {e}'}
    nuevas, ops = aplicar_deltas(reglas, texto)
    if ops == 0:
        if ENCABEZADO in texto:
            # fallback: el modelo devolvió el archivo completo estilo viejo —
            # mejor usar sus reglas que fallar (el delta es el camino feliz)
            nuevas = reglas_de(texto)[:_MAX_REGLAS]
        elif 'NADA' in texto.upper():
            nuevas = reglas          # señales sin lección: solo avanza el cursor
        else:
            return {'ok': False, 'motivo': 'salida inesperada del destilador'}

    if nuevas:
        hoy = datetime.now().strftime('%Y-%m-%d')
        archivo = _ruta_lecciones(project_path)
        os.makedirs(os.path.dirname(archivo), exist_ok=True)
        creado = _cuerpo_frontmatter_creado(archivo) or hoy
        cuerpo = ENCABEZADO + '\n\n' + '\n'.join(f'- {r}' for r in nuevas)
        with open(archivo, 'w', encoding='utf-8') as f:
            f.write(f"---\ntitulo: Lecciones del enjambre (destiladas de los fallos reales)\n"
                    f"tags: [leccion, enjambre]\ncreado: {creado}\nactualizado: {hoy}\n"
                    f"autor: Jarvis (destilador)\nestado: vigente\n---\n\n{cuerpo}\n")
        inyectar_lecciones(project_path)
    estado[str(project_id)] = {'ultimo_evento_id': max_id, 'actualizado': datetime.now().isoformat()}
    _guardar_estado(estado, estado_path)
    return {'ok': True, 'senales': len(senales), 'max_id': max_id,
            'reglas': len(nuevas), 'ops': ops}


def _cuerpo_frontmatter_creado(archivo: str) -> Optional[str]:
    try:
        with open(archivo, encoding='utf-8') as f:
            for linea in f.read().splitlines()[:8]:
                if linea.startswith('creado:'):
                    return linea.split(':', 1)[1].strip()
    except OSError:
        pass
    return None


def tal_vez_destilar(project_id: int, project_path: str,
                     umbral: Optional[int] = None,
                     estado_path: Optional[str] = None) -> Optional[dict]:
    """Destila solo si hay ≥ umbral señales nuevas (no llama a la API por cada
    tropiezo). None si no corresponde."""
    if not _activo():
        return None
    estado = _leer_estado(estado_path)
    desde = int(estado.get(str(project_id), {}).get('ultimo_evento_id', 0))
    senales, _ = senales_nuevas(project_id, desde)
    if len(senales) < (umbral if umbral is not None else _umbral_default()):
        return None
    return destilar_ahora(project_id, project_path, estado_path)


def estado_lecciones(project_id: int, project_path: str,
                     estado_path: Optional[str] = None) -> dict:
    """Estado del loop de lecciones para la salud de la memoria — nada de
    degradar en silencio: si el destilador no corre, esto dice POR QUÉ
    (señales bajo el umbral, sin API key, flag off)."""
    estado = _leer_estado(estado_path)
    desde = int(estado.get(str(project_id), {}).get('ultimo_evento_id', 0))
    try:
        senales, _ = senales_nuevas(project_id, desde)
    except Exception:
        senales = []
    return {
        'activo': _activo(),
        'api_ok': bool(os.environ.get('ANTHROPIC_API_KEY')),
        'senales_pendientes': len(senales),
        'umbral': _umbral_default(),
        'archivo_destilado': os.path.exists(_ruta_lecciones(project_path)),
        'lecciones_memoria': len(lecciones_de_memorias(project_path, k=99)),
        'ultimo_destilado': estado.get(str(project_id), {}).get('actualizado'),
    }


def tal_vez_destilar_todos() -> dict:
    """Pasada del janitor: evalúa cada proyecto con memoria compartida."""
    resumen = {'destilados': 0}
    if not _activo():
        return resumen
    try:
        conn = get_db()
        try:
            proyectos = [(r['id'], r['ruta']) for r in
                         conn.execute('SELECT id, ruta FROM projects').fetchall()]
        finally:
            conn.close()
    except Exception as e:
        print(f'[lecciones] no pude leer proyectos: {e}')
        return resumen
    for pid, ruta in proyectos:
        if not os.path.isdir(os.path.join(ruta or '', '.jarvis', 'memory')):
            continue
        try:
            r = tal_vez_destilar(pid, ruta)
            if r and r.get('ok'):
                resumen['destilados'] += 1
                print(f"[lecciones] proyecto {pid}: {r['senales']} señales destiladas")
        except Exception as e:
            print(f'[lecciones] proyecto {pid} falló: {e}')
    return resumen


# ─── Compilación determinista desde memorias [leccion] (cero API) ────────────

_FRONT_LECC_RE = re.compile(r'^---\s*\n(.*?)\n---\s*\n?', re.DOTALL)
_TAGS_LECC_RE  = re.compile(r'^tags:\s*\[([^\]]*)\]', re.MULTILINE)
_CAMPO_LECC_RE = {k: re.compile(rf'^{k}:\s*(.+)$', re.MULTILINE)
                  for k in ('titulo', 'resumen', 'estado', 'actualizado', 'creado')}


def lecciones_de_memorias(project_path: str, k: int = 12) -> list:
    """Una línea por memoria [leccion] VIGENTE (resumen: del frontmatter, o la
    primera línea del cuerpo) + puntero al archivo. Es la mitad del bloque
    siempre-cargado que NO depende de la API: task_events puede
    estar vacía (el enjambre trabaja en terminales directas), pero los agentes
    sí escriben lecciones como memorias — antes esas reglas eran opt-in.
    Las más frescas primero, cap `k` (el bloque vive en CADA sesión: corto)."""
    d = os.path.join(project_path, '.jarvis', 'memory')
    try:
        nombres = sorted(os.listdir(d))
    except OSError:
        return []
    lecciones = []
    for nombre in nombres:
        if (not nombre.endswith('.md') or nombre == 'INDEX.md'
                or nombre == LECCIONES_BASENAME):
            continue
        try:
            with open(os.path.join(d, nombre), encoding='utf-8', errors='replace') as f:
                src = f.read()
        except OSError:
            continue
        m = _FRONT_LECC_RE.match(src)
        front = m.group(1) if m else ''
        mt = _TAGS_LECC_RE.search(front)
        tags = [t.strip().lower() for t in mt.group(1).split(',')] if mt else []
        if 'leccion' not in tags:
            continue
        def campo(key):
            mm = _CAMPO_LECC_RE[key].search(front)
            return mm.group(1).strip() if mm else ''
        estado = campo('estado').lower() or 'vigente'
        if estado != 'vigente':
            continue
        cuerpo = src[m.end():] if m else src
        linea = campo('resumen') or next(
            (l.strip() for l in cuerpo.splitlines() if l.strip() and not l.startswith('#')), '')
        if not linea:
            continue
        fecha = campo('actualizado') or campo('creado')
        lecciones.append((fecha, f"- {linea[:200]} — .jarvis/memory/{nombre}"))
    lecciones.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [l for _, l in lecciones[:k]]


# ─── Inyección SIEMPRE-cargada (markers en CLAUDE.md / AGENTS.md) ────────────

def inyectar_lecciones(project_path: str):
    """Refresca el bloque de lecciones entre markers en CLAUDE.md y AGENTS.md.
    Dos fuentes: las reglas destiladas de fallos (API, si el destilador corrió)
    + la compilación determinista de las memorias [leccion]. Sin ninguna de las
    dos es no-op. Compare-before-write."""
    partes = []
    cuerpo = _cuerpo_actual(project_path)
    if cuerpo:
        partes.append(cuerpo.split(ENCABEZADO, 1)[-1].strip())
    compiladas = lecciones_de_memorias(project_path)
    if compiladas:
        partes.append("Lecciones escritas por el enjambre (abrí la memoria antes "
                      "de pisar su tema):\n" + '\n'.join(compiladas))
    if not partes:
        return
    bloque = (f"{MARKER_START}\n## 📚 Lecciones del enjambre (siempre cargadas — "
              f"de fallos y hallazgos reales)\n\n" + '\n\n'.join(partes) + f"\n{MARKER_END}")
    for nombre in ('CLAUDE.md', 'AGENTS.md'):
        archivo = os.path.join(project_path, nombre)
        try:
            contenido = ''
            if os.path.exists(archivo):
                with open(archivo, encoding='utf-8') as f:
                    contenido = f.read()
            if MARKER_START in contenido and MARKER_END in contenido:
                inicio = contenido.index(MARKER_START)
                fin    = contenido.index(MARKER_END) + len(MARKER_END)
                nuevo  = contenido[:inicio] + bloque + contenido[fin:]
            else:
                sep   = '' if (not contenido or contenido.endswith('\n\n')) else ('\n' if contenido.endswith('\n') else '\n\n')
                nuevo = contenido + sep + bloque + '\n'
            if nuevo != contenido:
                with open(archivo, 'w', encoding='utf-8') as f:
                    f.write(nuevo)
        except Exception as e:
            print(f'[lecciones] no pude inyectar en {nombre}: {e}')
