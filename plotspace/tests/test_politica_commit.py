# plotspace/tests/test_politica_commit.py
"""Qué es trabajo REAL y qué no — la regla que decide de qué hay que hacerse cargo.

EL PEDIDO (usuario, 2026-07-25)
-------------------------------
«Sí o sí deben commitear antes de terminar su trabajo... pero no commitear los
trabajos que no son procesos reales todavía, que son pruebas de localhost,
mockups y cosas así.»

Traducido a algo que una máquina pueda decidir sin un LLM, hay TRES cajones:

  artefacto  salida de build, capturas, binarios, runtime. NUNCA se commitea —
             va al .gitignore. (En este repo llegó a 927 MB sin trackear, y de
             paso mató el paracaídas del WIP: `git add -A` tardaba más que su
             timeout.)
  scratch    prototipos, mockups, demos, harnesses de QA. Todavía no es un
             proceso real: no se le reclama commit a nadie, pero tampoco se
             prohíbe (mañana puede volverse producto).
  real       todo lo demás: código, tests, memorias, config. Esto SÍ se commitea
             antes de cerrar la tarea.

EL SESGO ES DELIBERADO: ante la duda, `real`. Clasificar de más como artefacto
haría que un agente NO commitee algo que sí importaba, y esa pérdida es
silenciosa. Que sobre un recordatorio es barato; que falte, no.
"""
from plotspace.core import politica_commit as pol


# ─── Artefactos: nunca se commitean ───────────────────────────────────────────

def test_salida_de_build_es_artefacto():
    assert pol.clasificar('desktop/dist/jarvis.exe') == 'artefacto'
    assert pol.clasificar('desktop/dist/bundle.wsl') == 'artefacto'


def test_build_en_la_raiz_es_artefacto():
    # Los sufijos de build ('/dist/', '/build/'…) valen a CUALQUIER profundidad,
    # también en la raíz del repo: antes 'dist/app.js' caía como 'real' porque
    # el patrón exigía una barra delante (y Review lo mostraba como trabajo).
    assert pol.clasificar('dist/app.js') == 'artefacto'
    assert pol.clasificar('dist/') == 'artefacto'
    assert pol.clasificar('build/index.html') == 'artefacto'
    assert pol.clasificar('target/debug/x') == 'artefacto'
    # …pero un NOMBRE que empieza igual no es la carpeta.
    assert pol.clasificar('distribucion.md') == 'real'
    assert pol.clasificar('builder/x.py') == 'real'


def test_capturas_de_qa_son_artefacto():
    assert pol.clasificar('.jarvis/qa-shots/radio-8.png') == 'artefacto'


def test_estado_de_runtime_es_artefacto():
    for p in ('server.pid', 'data/jarvis.db', '.workspace/logs/terminal_1.log',
              'plotspace/__pycache__/x.pyc', 'node_modules/react/index.js'):
        assert pol.clasificar(p) == 'artefacto', p


def test_binarios_sueltos_son_artefacto():
    for p in ('instalador.msi', 'app.dmg', 'algo.exe'):
        assert pol.clasificar(p) == 'artefacto', p


# ─── Scratch: no se le reclama commit a nadie ─────────────────────────────────

def test_prototipos_y_mockups_son_scratch():
    for p in ('frontend/preview-settings/index.html',
              'frontend/terminal-chrome-redesign/index.html',
              'frontend/radio-studio/kit.js',
              'frontend/wb-mockup/demo.html',
              'frontend/complot-brand/index.html'):
        assert pol.clasificar(p) == 'scratch', p


def test_harness_de_qa_es_scratch():
    assert pol.clasificar('frontend/preview-wb-header/harness.html') == 'scratch'


def test_lo_que_vive_en_tmp_es_scratch():
    assert pol.clasificar('/tmp/claude-1000/x/scratchpad/plan.html') == 'scratch'


# ─── Real: esto SÍ se commitea ────────────────────────────────────────────────

def test_codigo_y_tests_son_trabajo_real():
    for p in ('plotspace/core/briefing.py', 'plotspace/tests/test_briefing.py',
              'frontend/sections/panel/panel.js', 'scripts/jv.py', 'CLAUDE.md'):
        assert pol.clasificar(p) == 'real', p


def test_las_memorias_del_enjambre_son_trabajo_real():
    """Se perdieron memorias por quedar sin commitear: son producto, no notas."""
    assert pol.clasificar('.jarvis/memory/cli-jv-enjambre.md') == 'real'


def test_un_asset_de_verdad_no_se_confunde_con_una_captura():
    """El sesgo: ante la duda, real. Un ícono del producto NO es un artefacto
    por ser una imagen — clasificar de más pierde trabajo en silencio."""
    assert pol.clasificar('frontend/shared/icons/claude.png') == 'real'


def test_los_helpers_de_test_son_producto_aunque_se_llamen_harness():
    """Falso positivo real encontrado barriendo los 696 archivos trackeados:
    `plotspace/tests/_harness.py` quedaba como scratch por la palabra 'harness'.
    Un test es producto, se llame como se llame."""
    assert pol.clasificar('plotspace/tests/_harness.py') == 'real'
    assert pol.clasificar('plotspace/tests/test_harness_smoke.py') == 'real'
    assert pol.clasificar('frontend/sections/panel/__tests__/x.test.js') == 'real'


def test_seccion_del_frontend_con_preview_en_el_nombre_sigue_siendo_real():
    """`sections/preview/` es la sección Web Preview del producto, no un mockup:
    el patrón de scratch es el prefijo `preview-`, no la palabra suelta."""
    assert pol.clasificar('frontend/sections/preview/preview.js') == 'real'


# ─── Helpers de uso ───────────────────────────────────────────────────────────

def test_hay_que_commitearlo_solo_para_lo_real():
    assert pol.hay_que_commitear('plotspace/core/x.py')
    assert not pol.hay_que_commitear('desktop/dist/app.exe')
    assert not pol.hay_que_commitear('frontend/preview-x/index.html')


def test_filtrar_deja_solo_lo_real():
    entrada = ['plotspace/x.py', 'desktop/dist/a.exe', 'frontend/preview-y/i.html',
               '.jarvis/memory/z.md']
    assert pol.solo_real(entrada) == ['plotspace/x.py', '.jarvis/memory/z.md']


def test_entradas_raras_no_explotan():
    for basura in (None, '', 123, [], {}):
        assert pol.clasificar(basura) == 'real'
    assert pol.solo_real(None) == []


def test_las_rutas_se_normalizan():
    assert pol.clasificar('./desktop/dist/x.exe') == 'artefacto'
    assert pol.clasificar('desktop\\dist\\x.exe') == 'artefacto'
