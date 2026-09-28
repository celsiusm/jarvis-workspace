"""Detección multi-IA de skills, comandos, agentes y reglas (⚙ → Extensiones).

Cada IA de código lee SUS archivos: Claude Code sus skills/commands/agents y el
CLAUDE.md, Codex el AGENTS.md, Gemini CLI el GEMINI.md, Cursor sus `.mdc`,
Copilot su `copilot-instructions.md`… Este módulo responde "¿qué va a leer cada
IA en este proyecto?" con una lista normalizada, sin depender de ninguna CLI
instalada: es pura lectura del disco.

Contrato de cada item (ver `detectar`):
    {ia, ia_label, kind, nombre, path, descripcion, editable, alcance,
     formato, tambien, detalle, bytes}
  · kind ∈ skill | command | agent | rules | instructions | config
  · alcance ∈ proyecto | usuario   (usuario = el home, SOLO lectura)
  · path relativo al proyecto, o `~/…` para el alcance usuario

Reglas de seguridad (no negociables, con test):
  · Nunca se devuelve el cuerpo de un archivo: sólo una descripción corta
    (frontmatter `description`, primer heading o primera línea), recortada a
    MAX_DESC y con los formatos típicos de API keys redactados.
  · Nunca se sale de la raíz (proyecto o home) por un symlink: todo se valida
    con realpath; os.walk no sigue links.
  · Se ignoran node_modules/.git/venv/…, archivos > MAX_BYTES y hay topes de
    items por IA y de carpetas recorridas.
  · Del home sólo se leen rutas de instrucciones conocidas (nunca auth.json ni
    nada fuera de la lista).
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

MAX_DESC = 160              # caracteres de la descripción
MAX_BYTES = 1_000_000       # archivos más grandes no se listan
MAX_HEAD = 8192             # bytes leídos para sacar la descripción
MAX_POR_IA = 200            # items por (IA, alcance)
MAX_DIRS = 4000             # carpetas recorridas buscando AGENTS.md/CLAUDE.md anidados
MAX_PROF = 6                # profundidad de esa búsqueda
MAX_CONTENIDO = 64 * 1024   # tope de leer_contenido()

IGNORAR_DIRS = {
    'node_modules', '.git', 'venv', '.venv', 'env', '__pycache__', 'dist',
    'build', '.next', '.nuxt', '.expo', 'target', '.cache', 'vendor',
    '.workspace', '.jarvis', 'coverage', '.turbo', '.svelte-kit', 'Pods',
}

# Catálogo de herramientas. `logo` = clave de window.cliLogo (None → glifo).
# `lee` = lo que esa IA lee (texto para la UI, no se usa para detectar).
HERRAMIENTAS = [
    {'id': 'claude', 'label': 'Claude Code', 'logo': 'claude', 'glifo': 'sparkles',
     'lee': ['.claude/skills/', '.claude/commands/', '.claude/agents/', 'CLAUDE.md']},
    {'id': 'codex', 'label': 'Codex', 'logo': 'codex', 'glifo': 'terminal',
     'lee': ['AGENTS.md', '.codex/prompts/', '~/.codex/prompts/']},
    {'id': 'gemini', 'label': 'Gemini CLI', 'logo': None, 'glifo': 'sparkles',
     'lee': ['GEMINI.md', '.gemini/commands/*.toml']},
    {'id': 'antigravity', 'label': 'Antigravity', 'logo': 'antigravity', 'glifo': 'zap',
     'lee': ['.agent/rules/', '.agent/workflows/']},
    {'id': 'cursor', 'label': 'Cursor', 'logo': 'cursor', 'glifo': 'edit',
     'lee': ['.cursor/rules/*.mdc', '.cursorrules', '.cursor/commands/']},
    {'id': 'qwen', 'label': 'Qwen Code', 'logo': 'qwen', 'glifo': 'terminal',
     'lee': ['QWEN.md', '.qwen/commands/', '.qwen/agents/']},
    {'id': 'opencode', 'label': 'OpenCode', 'logo': 'opencode', 'glifo': 'terminal',
     'lee': ['.opencode/agent/', '.opencode/command/', 'opencode.json', 'AGENTS.md']},
    {'id': 'copilot', 'label': 'GitHub Copilot', 'logo': None, 'glifo': 'git-branch',
     'lee': ['.github/copilot-instructions.md', '.github/prompts/', '.github/instructions/']},
    {'id': 'windsurf', 'label': 'Windsurf', 'logo': None, 'glifo': 'sparkle',
     'lee': ['.windsurfrules', '.windsurf/rules/', '.windsurf/workflows/']},
    {'id': 'cline', 'label': 'Cline', 'logo': None, 'glifo': 'terminal',
     'lee': ['.clinerules']},
    {'id': 'roo', 'label': 'Roo Code', 'logo': None, 'glifo': 'terminal',
     'lee': ['.roo/rules/', '.roo/rules-{modo}/', '.roorules']},
]
_LABEL = {h['id']: h['label'] for h in HERRAMIENTAS}
_ORDEN_IA = {h['id']: n for n, h in enumerate(HERRAMIENTAS)}
_ORDEN_KIND = {k: n for n, k in enumerate(
    ['skill', 'command', 'agent', 'rules', 'instructions', 'config'])}

# AGENTS.md es un estándar abierto: además de Codex lo leen estas.
_TAMBIEN_AGENTS = ['opencode', 'cursor', 'copilot', 'windsurf']

# Archivos de instrucciones que se buscan en la raíz Y anidados en subcarpetas.
_ANIDADOS = {
    'CLAUDE.md': 'claude', 'CLAUDE.local.md': 'claude',
    'AGENTS.md': 'codex', 'GEMINI.md': 'gemini', 'QWEN.md': 'qwen',
}

# Reglas de carpeta: (ia, kind, carpeta, sufijo, recursivo, estilo_nombre)
#   estilo_nombre: 'stem' | 'cmd' (/a:b) | 'fm' (frontmatter name o stem)
_CARPETAS_PROYECTO = [
    ('claude', 'command', '.claude/commands', '.md', True, 'cmd'),
    ('claude', 'agent', '.claude/agents', '.md', True, 'fm'),
    ('codex', 'command', '.codex/prompts', '.md', True, 'cmd'),
    ('gemini', 'command', '.gemini/commands', '.toml', True, 'cmd'),
    ('antigravity', 'rules', '.agent/rules', '.md', True, 'stem'),
    ('antigravity', 'command', '.agent/workflows', '.md', True, 'cmd'),
    ('cursor', 'rules', '.cursor/rules', '.mdc', True, 'stem'),
    ('cursor', 'command', '.cursor/commands', '.md', True, 'cmd'),
    ('qwen', 'command', '.qwen/commands', '.md', True, 'cmd'),
    ('qwen', 'command', '.qwen/commands', '.toml', True, 'cmd'),
    ('qwen', 'agent', '.qwen/agents', '.md', True, 'fm'),
    ('opencode', 'agent', '.opencode/agent', '.md', True, 'fm'),
    ('opencode', 'agent', '.opencode/agents', '.md', True, 'fm'),
    ('opencode', 'command', '.opencode/command', '.md', True, 'cmd'),
    ('opencode', 'command', '.opencode/commands', '.md', True, 'cmd'),
    ('copilot', 'command', '.github/prompts', '.prompt.md', True, 'cmd'),
    ('copilot', 'rules', '.github/instructions', '.instructions.md', True, 'stem'),
    ('copilot', 'agent', '.github/chatmodes', '.chatmode.md', True, 'stem'),
    ('windsurf', 'rules', '.windsurf/rules', '.md', True, 'stem'),
    ('windsurf', 'command', '.windsurf/workflows', '.md', True, 'cmd'),
    ('cline', 'rules', '.clinerules', '.md', True, 'stem'),
    ('roo', 'rules', '.roo/rules', '.md', True, 'stem'),
]

# Archivos sueltos: (ia, kind, ruta)
_ARCHIVOS_PROYECTO = [
    ('claude', 'instructions', '.claude/CLAUDE.md'),
    ('gemini', 'instructions', '.gemini/GEMINI.md'),
    ('qwen', 'instructions', '.qwen/QWEN.md'),
    ('cursor', 'rules', '.cursorrules'),
    ('opencode', 'config', 'opencode.json'),
    ('copilot', 'instructions', '.github/copilot-instructions.md'),
    ('windsurf', 'rules', '.windsurfrules'),
    ('cline', 'rules', '.clinerules'),          # si es archivo (puede ser carpeta)
    ('roo', 'rules', '.roorules'),
    ('roo', 'config', '.roomodes'),
]

_CARPETAS_HOME = [
    ('claude', 'command', '.claude/commands', '.md', True, 'cmd'),
    ('claude', 'agent', '.claude/agents', '.md', True, 'fm'),
    ('codex', 'command', '.codex/prompts', '.md', True, 'cmd'),
    ('gemini', 'command', '.gemini/commands', '.toml', True, 'cmd'),
    ('qwen', 'command', '.qwen/commands', '.md', True, 'cmd'),
    ('qwen', 'command', '.qwen/commands', '.toml', True, 'cmd'),
    ('opencode', 'agent', '.config/opencode/agent', '.md', True, 'fm'),
    ('opencode', 'command', '.config/opencode/command', '.md', True, 'cmd'),
]
_ARCHIVOS_HOME = [
    ('claude', 'instructions', '.claude/CLAUDE.md'),
    ('codex', 'instructions', '.codex/AGENTS.md'),
    ('gemini', 'instructions', '.gemini/GEMINI.md'),
    ('qwen', 'instructions', '.qwen/QWEN.md'),
    ('opencode', 'instructions', '.config/opencode/AGENTS.md'),
    ('windsurf', 'rules', '.codeium/windsurf/memories/global_rules.md'),
]

# Formatos típicos de secretos: la descripción es un extracto del archivo y
# NO puede filtrar una key pegada en la primera línea.
_SECRETO_RE = re.compile(
    r'(sk-[A-Za-z0-9_\-]{16,}'
    r'|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}'
    r'|AKIA[0-9A-Z]{16}|xox[abprs]-[A-Za-z0-9\-]{10,}'
    r'|AIza[0-9A-Za-z_\-]{30,}|gsk_[A-Za-z0-9]{20,}'
    r'|[A-Za-z0-9_\-+/=]{40,})')


# ─── Lectura segura ──────────────────────────────────────────────────────────

def _dentro(real: str, raiz_real: str) -> bool:
    return real == raiz_real or real.startswith(raiz_real.rstrip(os.sep) + os.sep)


def _archivo_ok(path: str, raiz_real: str) -> Optional[int]:
    """Tamaño en bytes si `path` es un archivo regular DENTRO de la raíz
    (resolviendo symlinks) y no es gigante; None si no califica."""
    try:
        real = os.path.realpath(path)
        if not _dentro(real, raiz_real) or not os.path.isfile(real):
            return None
        tam = os.path.getsize(real)
    except OSError:
        return None
    return tam if tam <= MAX_BYTES else None


def _dir_ok(path: str, raiz_real: str) -> bool:
    try:
        real = os.path.realpath(path)
    except OSError:
        return False
    return _dentro(real, raiz_real) and os.path.isdir(real)


def _cabeza(path: str) -> str:
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read(MAX_HEAD)
    except OSError:
        return ''


def _frontmatter(texto: str) -> tuple[dict, str]:
    """(campos simples `clave: valor`, cuerpo sin frontmatter)."""
    if not texto.startswith('---'):
        return {}, texto
    m = re.match(r'^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(\r?\n|$)', texto, flags=re.DOTALL)
    if not m:
        return {}, texto
    campos = {}
    for linea in m.group(1).splitlines():
        mm = re.match(r'^([A-Za-z_][\w\-]*)\s*:\s*(.*)$', linea)
        if mm:
            campos[mm.group(1)] = mm.group(2).strip().strip('"\'')
    return campos, texto[m.end():]


def _limpiar(s: str) -> str:
    s = re.sub(r'^#+\s*', '', s.strip())
    s = re.sub(r'[*_`>]+', '', s)
    s = _SECRETO_RE.sub('[redactado]', s)
    s = re.sub(r'\s+', ' ', s).strip()
    if len(s) > MAX_DESC:
        s = s[:MAX_DESC - 1].rstrip() + '…'
    return s


def _recortar(s: str) -> str:
    """Como _limpiar pero SIN tocar el markdown: los globs (`**/*.py`) viven acá."""
    s = _SECRETO_RE.sub('[redactado]', re.sub(r'\s+', ' ', str(s)).strip())
    return s if len(s) <= 80 else s[:79].rstrip() + '…'


def _primera_linea(cuerpo: str) -> str:
    for linea in cuerpo.splitlines():
        if linea.strip() and not re.fullmatch(r'\s*(---+|===+|<!--.*-->)\s*', linea):
            return linea
    return ''


def _describir(path: str, kind: str) -> tuple[str, dict]:
    """(descripción corta, frontmatter) de un archivo."""
    texto = _cabeza(path)
    if path.endswith('.json') or os.path.basename(path) == '.roomodes':
        try:
            data = json.loads(texto) if len(texto) < MAX_HEAD else None
        except ValueError:
            data = None
        if isinstance(data, dict) and data:
            claves = [k for k in data.keys() if isinstance(k, str) and not k.startswith('$')]
            return _limpiar('Configura: ' + ', '.join(claves[:8])), {}
        return '', {}
    if path.endswith('.toml'):
        m = re.search(r'^\s*description\s*=\s*"(.*?)"\s*$', texto, flags=re.MULTILINE)
        return (_limpiar(m.group(1)) if m else ''), {}
    fm, cuerpo = _frontmatter(texto)
    desc = fm.get('description') or _primera_linea(cuerpo)
    return _limpiar(desc), fm


# ─── Recolección ─────────────────────────────────────────────────────────────

class _Colector:
    def __init__(self):
        self.items: list[dict] = []
        self._vistos: set = set()
        self._cuenta: dict = {}

    def add(self, *, ia, kind, nombre, path_abs, rel, alcance, raiz_real,
            editable=False, formato=None, detalle=''):
        clave = (alcance, rel)
        if clave in self._vistos:
            return
        tam = _archivo_ok(path_abs, raiz_real)
        if tam is None:
            return
        n = self._cuenta.get((ia, alcance), 0)
        if n >= MAX_POR_IA:
            return
        self._cuenta[(ia, alcance)] = n + 1
        self._vistos.add(clave)
        desc, fm = _describir(path_abs, kind)
        if kind == 'agent' and fm.get('name'):
            nombre = fm['name']
        if not detalle:
            detalle = _detalle(ia, rel, fm)
        self.items.append({
            'ia': ia,
            'ia_label': _LABEL.get(ia, ia),
            'kind': kind,
            'nombre': nombre,
            'path': rel,
            'descripcion': desc,
            'editable': bool(editable),
            'alcance': alcance,
            'formato': formato,
            'tambien': list(_TAMBIEN_AGENTS) if os.path.basename(rel) == 'AGENTS.md' else [],
            'detalle': _recortar(detalle) if detalle else '',
            'bytes': tam,
        })


def _detalle(ia: str, rel: str, fm: dict) -> str:
    if ia == 'cursor' and rel.endswith('.mdc'):
        if str(fm.get('alwaysApply', '')).lower() == 'true':
            return 'siempre'
        return fm.get('globs', '')
    if ia == 'copilot' and rel.endswith('.instructions.md'):
        return fm.get('applyTo', '')
    m = re.match(r'^\.roo/rules-([^/]+)/', rel)
    if m:
        return f'modo {m.group(1)}'
    return ''


def _nombre(estilo: str, rel_en_carpeta: str, sufijo: str) -> str:
    base = rel_en_carpeta[:-len(sufijo)] if rel_en_carpeta.endswith(sufijo) else rel_en_carpeta
    base = base.replace(os.sep, '/')
    if estilo == 'cmd':
        return '/' + base.replace('/', ':')
    return base.rsplit('/', 1)[-1]


def _mostrar(rel: str, alcance: str) -> str:
    rel = rel.replace(os.sep, '/')
    return f'~/{rel}' if alcance == 'usuario' else rel


def _walk_carpeta(col, raiz, raiz_real, alcance, ia, kind, carpeta, sufijo, recursivo, estilo):
    base = os.path.join(raiz, carpeta)
    if not _dir_ok(base, raiz_real):
        return
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORAR_DIRS and not d.startswith('.'))
        if not recursivo:
            dirnames[:] = []
        # Roo: .roo/rules-{modo}/ vive al lado de .roo/rules/ → regla aparte abajo
        for fn in sorted(filenames):
            if not fn.endswith(sufijo):
                continue
            # `.md` no debe tragarse `.prompt.md`/`.instructions.md` de otras reglas
            abs_ = os.path.join(dirpath, fn)
            rel_c = os.path.relpath(abs_, base)
            col.add(ia=ia, kind=kind, nombre=_nombre(estilo, rel_c, sufijo), path_abs=abs_,
                    rel=_mostrar(os.path.relpath(abs_, raiz), alcance),
                    alcance=alcance, raiz_real=raiz_real)


def _skills_claude(col, raiz, raiz_real, alcance, editable):
    base = os.path.join(raiz, '.claude', 'skills')
    if not _dir_ok(base, raiz_real):
        return
    try:
        entradas = sorted(os.listdir(base))
    except OSError:
        return
    for e in entradas:
        p = os.path.join(base, e)
        if e.endswith('.md') and os.path.isfile(p):
            col.add(ia='claude', kind='skill', nombre=e[:-3], path_abs=p,
                    rel=_mostrar(os.path.relpath(p, raiz), alcance), alcance=alcance,
                    raiz_real=raiz_real, editable=editable, formato='flat')
        elif os.path.isdir(p) and not os.path.islink(p):
            sm = os.path.join(p, 'SKILL.md')
            col.add(ia='claude', kind='skill', nombre=e, path_abs=sm,
                    rel=_mostrar(os.path.relpath(sm, raiz), alcance), alcance=alcance,
                    raiz_real=raiz_real, editable=editable, formato='carpeta')


def _anidados(col, raiz, raiz_real):
    dirs = 0
    raiz_n = raiz.rstrip(os.sep)
    for dirpath, dirnames, filenames in os.walk(raiz, followlinks=False):
        dirs += 1
        prof = 0 if dirpath == raiz_n else dirpath[len(raiz_n) + 1:].count(os.sep) + 1
        if dirs > MAX_DIRS or prof >= MAX_PROF:
            dirnames[:] = []
        else:
            dirnames[:] = sorted(d for d in dirnames
                                 if d not in IGNORAR_DIRS and not d.startswith('.'))
        for fn in sorted(filenames):
            ia = _ANIDADOS.get(fn)
            if not ia:
                continue
            abs_ = os.path.join(dirpath, fn)
            rel = os.path.relpath(abs_, raiz).replace(os.sep, '/')
            carpeta = os.path.dirname(rel)
            col.add(ia=ia, kind='instructions', nombre=fn, path_abs=abs_, rel=rel,
                    alcance='proyecto', raiz_real=raiz_real,
                    detalle=f'aplica a {carpeta}/' if carpeta else '')


def _roo_modos(col, raiz, raiz_real):
    base = os.path.join(raiz, '.roo')
    if not _dir_ok(base, raiz_real):
        return
    for d in sorted(os.listdir(base)):
        if d.startswith('rules-'):
            _walk_carpeta(col, raiz, raiz_real, 'proyecto', 'roo', 'rules',
                          os.path.join('.roo', d), '.md', True, 'stem')


def _ordenar(items: list) -> list:
    return sorted(items, key=lambda i: (
        _ORDEN_IA.get(i['ia'], 99), 0 if i['alcance'] == 'proyecto' else 1,
        _ORDEN_KIND.get(i['kind'], 9), i['path'].count('/'), i['path'].lower()))


def detectar(project_path: str, home: Optional[str] = None) -> list[dict]:
    """Lista normalizada de lo que cada IA lee en el proyecto (+ home si se pasa).

    Pura (sólo lee disco), segura (no sale de las raíces, no devuelve cuerpos)
    y acotada (topes de tamaño, items y carpetas)."""
    if not project_path or not os.path.isdir(project_path):
        return []
    col = _Colector()
    raiz = os.path.abspath(project_path)
    raiz_real = os.path.realpath(raiz)

    _skills_claude(col, raiz, raiz_real, 'proyecto', editable=True)
    for ia, kind, carpeta, suf, rec, est in _CARPETAS_PROYECTO:
        _walk_carpeta(col, raiz, raiz_real, 'proyecto', ia, kind, carpeta, suf, rec, est)
    _roo_modos(col, raiz, raiz_real)
    for ia, kind, rel in _ARCHIVOS_PROYECTO:
        p = os.path.join(raiz, rel)
        col.add(ia=ia, kind=kind, nombre=os.path.basename(rel), path_abs=p, rel=rel,
                alcance='proyecto', raiz_real=raiz_real)
    _anidados(col, raiz, raiz_real)

    if home and os.path.isdir(home):
        h = os.path.abspath(home)
        h_real = os.path.realpath(h)
        if h_real != raiz_real:
            _skills_claude(col, h, h_real, 'usuario', editable=False)
            for ia, kind, carpeta, suf, rec, est in _CARPETAS_HOME:
                _walk_carpeta(col, h, h_real, 'usuario', ia, kind, carpeta, suf, rec, est)
            for ia, kind, rel in _ARCHIVOS_HOME:
                col.add(ia=ia, kind=kind, nombre=os.path.basename(rel),
                        path_abs=os.path.join(h, rel), rel=_mostrar(rel, 'usuario'),
                        alcance='usuario', raiz_real=h_real)

    # Los sufijos compuestos (.prompt.md) no deben aparecer duplicados bajo una
    # regla `.md` genérica: _Colector ya deduplica por ruta, y las reglas de
    # sufijo compuesto van en carpetas propias.
    return _ordenar(col.items)


def resumen(items: list) -> list[dict]:
    """Conteo por herramienta (sólo las que tienen algo), Claude primero."""
    por: dict = {}
    for it in items:
        h = por.setdefault(it['ia'], {'total': 0, 'kinds': {}, 'usuario': 0})
        h['total'] += 1
        h['kinds'][it['kind']] = h['kinds'].get(it['kind'], 0) + 1
        if it['alcance'] == 'usuario':
            h['usuario'] += 1
    out = []
    for herr in HERRAMIENTAS:
        if herr['id'] in por:
            out.append({**herr, **por[herr['id']]})
    return out


def leer_contenido(project_path: str, rel: str) -> Optional[str]:
    """Contenido (truncado) de un archivo que `detectar` lista en el PROYECTO.
    None si la ruta no fue detectada, escapa de la raíz o es del home."""
    if not rel or rel.startswith('~') or os.path.isabs(rel) or '..' in rel.replace('\\', '/').split('/'):
        return None
    if not project_path or not os.path.isdir(project_path):
        return None
    if rel not in {i['path'] for i in detectar(project_path, home=None)}:
        return None
    raiz_real = os.path.realpath(project_path)
    p = os.path.join(project_path, rel)
    if _archivo_ok(p, raiz_real) is None:
        return None
    try:
        with open(p, 'r', encoding='utf-8', errors='replace') as f:
            return f.read(MAX_CONTENIDO)
    except OSError:
        return None
