# plotspace/core/politica_commit.py
"""Qué es trabajo REAL y qué no — la regla que decide de qué hay que hacerse cargo.

EL PEDIDO (usuario, 2026-07-25)
-------------------------------
«Sí o sí deben commitear antes de terminar su trabajo... pero no commitear los
trabajos que no son procesos reales todavía, que son pruebas de localhost,
mockups y cosas así.»

Eso son dos reglas opuestas, y la segunda es la que faltaba. Traducidas a algo
que se pueda decidir sin gastar un LLM, hay tres cajones:

  artefacto  salida de build, capturas, binarios, estado de runtime. NUNCA se
             commitea: va al .gitignore. No es una preferencia estética — en
             este repo llegaron a 927 MB sin trackear (716 de `desktop/dist`,
             212 de `.jarvis/qa-shots`) y de paso mataron el paracaídas del WIP,
             porque su `git add -A` pasó a tardar más que su propio timeout.
  scratch    prototipos, mockups, demos, harnesses. Todavía no es un proceso
             real: no se le reclama commit a nadie, pero tampoco se prohíbe
             (mañana puede volverse producto).
  real       todo lo demás: código, tests, memorias, config. Esto SÍ se commitea
             antes de cerrar la tarea.

EL SESGO ES DELIBERADO: ante la duda, `real`. Clasificar de más como artefacto
haría que un agente NO commitee algo que sí importaba, y esa pérdida es
silenciosa e irreversible. Que sobre un recordatorio es barato; que falte, no.
Por eso no hay regla por extensión de imagen: un .png puede ser un ícono del
producto. Las capturas se reconocen por DÓNDE viven, no por qué son.
"""

ARTEFACTO, SCRATCH, REAL = 'artefacto', 'scratch', 'real'

# Carpetas cuyo contenido es SIEMPRE salida de una herramienta.
_DIRS_ARTEFACTO = (
    'desktop/dist/', '.jarvis/qa-shots/', '.workspace/', 'data/',
    'node_modules/', '__pycache__/', '.pytest_cache/', '.superpowers/',
    'venv/', '.venv/', 'coverage/',
)
# Sufijos de carpeta (matchean a cualquier profundidad): salida de build.
_SUFIJOS_DIR_ARTEFACTO = ('/dist/', '/build/', '/target/', '/node_modules/',
                          '/__pycache__/')
# Archivos que son estado de runtime o binarios distribuibles.
_EXT_ARTEFACTO = ('.exe', '.msi', '.dmg', '.appimage', '.wsl', '.vhdx',
                  '.pid', '.log', '.pyc', '.lnk', '.db', '.db-wal', '.db-shm')

# Prototipos: el patrón real de este repo. `preview-x/` y `x-redesign/` son
# galerías de exploración; `sections/preview/` (sin guion) es la sección del
# producto y NO entra acá.
_PREFIJOS_SCRATCH = ('preview-', 'demo-', 'proto-', 'complot-')
_SUFIJOS_SCRATCH = ('-redesign', '-studio', '-mockup', '-harness', '-proto')
_PALABRAS_SCRATCH = ('mockup', 'harness', 'scratchpad')
# En el NOMBRE del archivo el criterio es más estricto que en el directorio:
# 'harness' y 'scratchpad' ya están cubiertos por las reglas de ruta, y como
# palabras sueltas se llevaban puestos `plotspace/tests/_harness.py` y
# `test_harness_smoke.py`, que son producto.
_PALABRAS_SCRATCH_NOMBRE = ('mockup',)

# Carpetas que son trabajo real SIEMPRE, corte. Un test es producto: da igual
# cómo se llame el archivo.
_DIRS_SIEMPRE_REAL = ('tests', 'test', '__tests__')


def _normalizar(path) -> str:
    """Ruta comparable: separadores unix, sin './' inicial, en minúsculas."""
    if not isinstance(path, str):
        return ''
    p = path.strip().replace('\\', '/').strip('"\'')
    while p.startswith('./'):
        p = p[2:]
    return p.lower()


def clasificar(path) -> str:
    """'artefacto' | 'scratch' | 'real'. Ante cualquier duda (o basura), 'real'."""
    p = _normalizar(path)
    if not p:
        return REAL

    if p.startswith('/tmp/') or '/scratchpad/' in p:
        return SCRATCH

    for d in _DIRS_ARTEFACTO:
        if p.startswith(d) or f'/{d}' in p:
            return ARTEFACTO
    for d in _SUFIJOS_DIR_ARTEFACTO:
        # '/dist/' a cualquier profundidad, incluida la raíz ('dist/app.js').
        if d in p or p.startswith(d[1:]):
            return ARTEFACTO
    if p.endswith(_EXT_ARTEFACTO):
        return ARTEFACTO

    partes = p.split('/')
    if any(d in _DIRS_SIEMPRE_REAL for d in partes[:-1]):
        return REAL                       # un test es producto, se llame como se llame

    # Scratch se decide por el nombre de alguno de los DIRECTORIOS del camino:
    # así `frontend/preview-settings/index.html` es scratch pero
    # `frontend/sections/preview/preview.js` (el producto) no lo es.
    for parte in partes[:-1]:
        if parte.startswith(_PREFIJOS_SCRATCH) or parte.endswith(_SUFIJOS_SCRATCH):
            return SCRATCH
        if any(w in parte for w in _PALABRAS_SCRATCH):
            return SCRATCH
    if any(w in partes[-1] for w in _PALABRAS_SCRATCH_NOMBRE):
        return SCRATCH

    return REAL


def hay_que_commitear(path) -> bool:
    """¿De este archivo hay que hacerse cargo antes de cerrar la tarea?"""
    return clasificar(path) == REAL


def solo_real(paths) -> list:
    """Filtra una lista de rutas dejando el trabajo real, en el mismo orden."""
    return [p for p in (paths or []) if hay_que_commitear(p)]
