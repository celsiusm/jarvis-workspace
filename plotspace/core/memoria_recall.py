"""
memoria_recall — la memoria compartida va SOLA al prompt de cada paso.

El eslabón que faltaba en la lectura: el protocolo del CLAUDE.md dice "leé el
INDEX", pero eso es opt-in y los agentes que lanza Jarvis no recibían NINGUNA
instrucción de memoria en su tarea. Este módulo puntúa las memorias del
proyecto contra los archivos y el texto de un paso — determinista, cero tokens
de API — y arma el bloque "memorias relevantes" que el engine inyecta al
prompt (`_tarea_engine_para_terminal`). Un paso que toca
`frontend/sections/preview/*` recibe las memorias de preview sin pedirlas.

Señales de relevancia (en orden de peso):
  1. RUTAS: las memorias citan archivos (`plotspace/core/x.py`); match exacto o
     por directorio contra los `archivos` del paso. La señal fuerte.
  2. TAGS del frontmatter presentes en el texto de la tarea.
  3. Palabras del título presentes en la tarea (débil, desempata).

Estados: `obsoleta` nunca se recomienda; `lapida` SOLO con match de ruta (es
la advertencia "esto se eliminó, no lo reintroduzcas" — vale oro justo ahí).
"""
import math
import os
import re

# _TOKEN_RE/_STOP viven en memoria_lint (tokenización compartida del
# subsistema); se re-exportan acá porque memoria_endurecimiento los importa
# de este módulo. BM25 usa tokens 3+ chars sin palabras-función.
from plotspace.core.memoria_lint import _PATH_RE, _FRONT_RE, _TOKEN_RE, _STOP
from plotspace.core import memoria_categorias as mcat

_TAGS_RE   = re.compile(r'^tags:\s*\[([^\]]*)\]', re.MULTILINE)
_TITULO_RE = re.compile(r'^titulo:\s*(.+)$', re.MULTILINE)
_ESTADO_RE = re.compile(r'^estado:\s*(\S+)', re.MULTILINE)
_CAT_RE    = re.compile(r'^categoria:\s*(\S+)', re.MULTILINE)
_RESUMEN_RE = re.compile(r'^resumen:\s*(.+)$', re.MULTILINE)
_PALABRA_RE = re.compile(r'[a-zá-úñ0-9-]{5,}')

# Palabras de título demasiado comunes para señalar relevancia.
_RUIDO = {'jarvis', 'workspace', 'proyecto', 'usuario', 'gotcha', 'regla',
          'sistema', 'terminal', 'terminales', 'agente', 'agentes', 'cuando',
          'siempre', 'nunca', 'antes', 'despues', 'sobre', 'entre', 'para'}

UMBRAL = 2.0     # puntaje mínimo para recomendar (evita ruido)


def _dir(p: str) -> str:
    return p.rsplit('/', 1)[0] if '/' in p else ''


def _normalizar_patron(p: str) -> str:
    """'./plotspace/auth/*' → 'plotspace/auth' (los `archivos` de un paso pueden
    ser globs de directorio)."""
    p = (p or '').strip()
    if p.startswith('./'):
        p = p[2:]
    return p.rstrip('*').rstrip('/')


def _puntaje_rutas(rutas_memoria, archivos_paso) -> float:
    score = 0.0
    for a in archivos_paso:
        na = _normalizar_patron(a)
        if not na:
            continue
        mejor = 0.0
        for r in rutas_memoria:
            if r == na:
                mejor = max(mejor, 3.0)               # mismo archivo
            elif r.startswith(na + '/'):
                mejor = max(mejor, 2.0)               # el paso es dueño del dir citado
            elif _dir(r) and _dir(r) == _dir(na):
                mejor = max(mejor, 1.5)               # mismo directorio
        score += mejor
    return min(score, 4.0)


def indexar(project_path: str) -> list:
    """[{slug, titulo, tags, rutas, estado}] de todas las memorias del proyecto."""
    d = os.path.join(project_path, '.jarvis', 'memory')
    out = []
    try:
        nombres = sorted(os.listdir(d))
    except OSError:
        return out
    for nombre in nombres:
        if not nombre.endswith('.md') or nombre == 'INDEX.md':
            continue
        try:
            with open(os.path.join(d, nombre), encoding='utf-8', errors='replace') as f:
                src = f.read()
        except OSError:
            continue
        front = ''
        m = _FRONT_RE.match(src)
        if m:
            front = m.group(1)
        mt = _TITULO_RE.search(front)
        titulo = (mt.group(1).strip().strip('"\'') if mt else nombre[:-3])
        mtags = _TAGS_RE.search(front)
        tags = [t.strip().lower() for t in mtags.group(1).split(',') if t.strip()] if mtags else []
        me = _ESTADO_RE.search(front)
        estado = me.group(1).lower() if me else 'vigente'
        mc = _CAT_RE.search(front)
        categoria = (mc.group(1).strip().lower() if mc and mcat.es_valida(mc.group(1).strip().lower())
                     else mcat.clasificar(tags, nombre[:-3]))
        cuerpo = src[m.end():] if m else src
        # resumen: del frontmatter (contrato nuevo) o primera línea del cuerpo
        # — es lo que el bloque inyecta al prompt (el hecho, no solo el puntero)
        mr = _RESUMEN_RE.search(front)
        resumen = (mr.group(1).strip() if mr else next(
            (l.strip() for l in cuerpo.splitlines()
             if l.strip() and not l.startswith('#')), ''))
        out.append({
            'slug':      nombre[:-3],
            'titulo':    titulo,
            'tags':      tags,
            'rutas':     sorted(set(_PATH_RE.findall(src))),
            'estado':    estado,
            'categoria': categoria,
            'resumen':   resumen[:180],
            'tokens':    _TOKEN_RE.findall((titulo + ' ' + cuerpo).lower()),
        })
    return out


# ─── BM25 sobre los cuerpos: rescata el match que no está en título/ruta ─────

_BM25_K1 = 1.5
_BM25_B  = 0.75


def _bm25_scores(memorias: list, consulta: str) -> dict:
    """{slug → score BM25} de la consulta contra los cuerpos. BM25 clásico:
    premia el término raro (idf) y satura la frecuencia; normaliza por largo.
    Puro Python — con ~130 memorias es instantáneo, sin embeddings ni API."""
    q = {t for t in _TOKEN_RE.findall((consulta or '').lower()) if t not in _STOP}
    if not q or not memorias:
        return {}
    N = len(memorias)
    largos = [len(m['tokens']) for m in memorias]
    avg = (sum(largos) / N) or 1.0
    # document frequency por término de la consulta
    df = {t: 0 for t in q}
    tfs = []
    for m in memorias:
        tf = {}
        for tok in m['tokens']:
            if tok in q:
                tf[tok] = tf.get(tok, 0) + 1
        for t in tf:
            df[t] += 1
        tfs.append(tf)
    scores = {}
    for i, m in enumerate(memorias):
        s, dl = 0.0, largos[i] or 1
        for t, f in tfs[i].items():
            idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * (f * (_BM25_K1 + 1)) / (f + _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / avg))
        if s > 0:
            scores[m['slug']] = s
    return scores


def _categoria_del_paso(archivos: list, tarea: str) -> str:
    """Clasifica el paso mismo por sus archivos + palabras de la tarea, para
    dar un empujón a las memorias de la misma categoría (desempate suave)."""
    toks = list(_PALABRA_RE.findall((tarea or '').lower()))
    for a in archivos or []:
        toks += re.split(r'[/_.\-]', str(a).lower())
    return mcat.clasificar(toks, '')


def relevantes(project_path: str, archivos: list, tarea: str, k: int = 5,
               usos: dict = None) -> list:
    """Top-K memorias relevantes al paso, con puntaje ≥ UMBRAL.

    Señales: rutas citadas (fuerte) · tags/título en la tarea · BM25 sobre los
    cuerpos (rescata el match que no está en título/ruta) · categoría del paso
    (desempate) · uso histórico (`usos`: slug→veces leída, del audit trail —
    lo que demostró servir sube)."""
    texto = (tarea or '').lower()
    palabras_tarea = set(_PALABRA_RE.findall(texto))
    cat_paso = _categoria_del_paso(archivos, tarea)
    usos = usos or {}
    memorias = [m for m in indexar(project_path) if m['estado'] not in ('obsoleta', 'archivo')]
    # BM25 contra la tarea + los basenames de los archivos del paso (más señal)
    consulta = tarea or ''
    for a in archivos or []:
        consulta += ' ' + re.sub(r'[/_.\-]', ' ', str(a))
    bm25 = _bm25_scores(memorias, consulta)
    bm_max = max(bm25.values()) if bm25 else 0.0

    candidatas = []
    for m in memorias:
        p_rutas = _puntaje_rutas(m['rutas'], archivos or [])
        # BM25 normalizado a [0, 2.5]: fuerte, pero por debajo de un match de ruta exacto
        p_bm = (bm25.get(m['slug'], 0.0) / bm_max * 2.5) if bm_max else 0.0
        if m['estado'] == 'lapida' and p_rutas <= 0:
            continue     # una lápida solo avisa donde pega su ruta
        p_tags = min(sum(1.0 for t in m['tags'] if t and t in texto), 3.0)
        toks = {w for w in _PALABRA_RE.findall(m['titulo'].lower())} - _RUIDO
        p_titulo = min(sum(0.5 for w in toks if w in palabras_tarea), 1.5)
        base = p_rutas + p_tags + p_titulo + p_bm
        if base <= 0:
            continue
        # empujón por categoría compartida (desempata las que ya matchean)
        p_cat = 0.75 if (cat_paso != mcat.SIN_CLASIFICAR and m.get('categoria') == cat_paso) else 0.0
        # uso histórico: log suave, tope 1.0 (no deja que una muy usada tape todo)
        p_uso = min(math.log1p(usos.get(m['slug'], 0)) * 0.4, 1.0)
        score = base + p_cat + p_uso
        if score >= UMBRAL:
            candidatas.append((score, m))
    candidatas.sort(key=lambda x: (-x[0], x[1]['slug']))
    return [m for _, m in candidatas[:k]]


def usos_registrados(n: int = 500) -> dict:
    """{slug → veces leída en pasos que terminaron BIEN}. Fuente primaria: la
    tabla memoria_uso (persistente, cruzada con el resultado del paso — solo
    'done' sube el ranking; una memoria leída únicamente en pasos que fallan
    no demuestra servir). Fallback: el audit trail legacy (rotativo)."""
    try:
        from plotspace.core.database import conteo_uso_memorias
        conteo = conteo_uso_memorias(resultado='done', n=max(n * 4, 2000))
        if conteo:
            return conteo
    except Exception:
        pass
    try:
        from plotspace.core import logs
        conteo = {}
        for ev in logs.leer_recientes(n, tipo='memorias_usadas'):
            for slug in ev.get('slugs') or []:
                conteo[slug] = conteo.get(slug, 0) + 1
        return conteo
    except Exception:
        return {}


def bloque_relevantes(project_path: str, archivos: list, tarea: str, k: int = 5,
                      usos: dict = None, terminal_id: int = None) -> str:
    """Bloque de texto para inyectar al prompt del paso ('' si no hay nada).
    Con `terminal_id`, registra QUÉ se inyectó (resultado='inyectada') — la
    mitad del Altímetro: inyectadas vs. leídas dice si el recall rinde."""
    if usos is None:
        usos = usos_registrados()
    rel = relevantes(project_path, archivos, tarea, k=k, usos=usos)
    if not rel:
        return ''
    if terminal_id is not None:
        try:
            from plotspace.core.database import get_db, registrar_uso_memorias
            conn = get_db()
            try:
                fila = conn.execute('SELECT id FROM projects WHERE ruta = ?',
                                    (project_path,)).fetchone()
            finally:
                conn.close()
            if fila:
                registrar_uso_memorias(fila['id'], terminal_id,
                                       [m['slug'] for m in rel], 'inyectada')
        except Exception:
            pass
    lineas = ["\n\nMEMORIAS DEL PROYECTO probablemente relevantes — leé las que "
              "toquen tu tarea ANTES de empezar:"]
    for m in rel:
        marca = ' [LAPIDA: algo acá se eliminó — no reintroducir]' if m['estado'] == 'lapida' else ''
        lineas.append(f"  • .jarvis/memory/{m['slug']}.md — {m['titulo']}{marca}")
        # el hecho en una línea, no solo el puntero: hasta el agente que no
        # abre el archivo se lleva el resumen
        if m.get('resumen') and m['resumen'].lower() != m['titulo'].lower():
            lineas.append(f"      ↳ {m['resumen']}")
    lineas.append("(índice completo: .jarvis/memory/INDEX.md)")
    return '\n'.join(lineas)
