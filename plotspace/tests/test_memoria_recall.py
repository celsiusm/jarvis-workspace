"""
Test: memoria_recall — la memoria va SOLA al prompt de cada paso.

Antes la lectura de memorias era opt-in (el protocolo del CLAUDE.md decía
"leé el INDEX") y los builders no recibían NINGUNA instrucción de memoria en
su tarea. Ahora el engine puntúa las memorias del proyecto contra los archivos
y el texto del paso (cero tokens de API, determinista) e inyecta las top-K al
prompt — un paso que toca frontend/sections/preview/* recibe las memorias de
preview sin pedirlas.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plotspace.core.memoria_recall import relevantes, bloque_relevantes


def _mem(d, slug, cuerpo, titulo=None, tags='', estado='vigente'):
    mdir = os.path.join(d, '.jarvis', 'memory')
    os.makedirs(mdir, exist_ok=True)
    with open(os.path.join(mdir, slug + '.md'), 'w', encoding='utf-8') as f:
        f.write(f"---\ntitulo: {titulo or slug}\ntags: [{tags}]\ncreado: 2026-07-01\n"
                f"estado: {estado}\n---\n\n{cuerpo}\n")


def test_match_exacto_de_ruta_gana():
    with tempfile.TemporaryDirectory() as d:
        _mem(d, 'preview-pestanas', 'El módulo `frontend/sections/preview/preview.js` maneja pestañas.')
        _mem(d, 'otra-cosa', 'Nada que ver, habla de `plotspace/core/voice.py`.')
        r = relevantes(d, ['frontend/sections/preview/preview.js'], 'ajustar el preview')
        assert r and r[0]['slug'] == 'preview-pestanas'
        assert all(m['slug'] != 'otra-cosa' for m in r)


def test_match_por_directorio_con_glob():
    with tempfile.TemporaryDirectory() as d:
        _mem(d, 'dev-servers', 'El menú vive en `frontend/sections/preview/dev-servers.js`.')
        r = relevantes(d, ['frontend/sections/preview/*'], 'tocar la toolbar')
        assert r and r[0]['slug'] == 'dev-servers'


def test_match_por_tags_y_titulo_en_la_tarea():
    with tempfile.TemporaryDirectory() as d:
        _mem(d, 'regla-de-puertos', 'El 3000 es de Jarvis.', tags='puertos, dev-server',
             titulo='Regla de puertos: el 3000 es de Jarvis')
        _mem(d, 'i18n-idioma', 'Diccionario.', tags='i18n')
        r = relevantes(d, [], 'levantá un dev-server en un puerto libre (regla de puertos)')
        slugs = [m['slug'] for m in r]
        assert 'regla-de-puertos' in slugs
        assert 'i18n-idioma' not in slugs


def test_obsoletas_se_excluyen_lapidas_solo_con_ruta():
    with tempfile.TemporaryDirectory() as d:
        _mem(d, 'vieja', 'Habla de `plotspace/main.py`.', estado='obsoleta')
        _mem(d, 'lapida-scrollback', 'El scrollback en `plotspace/main.py` se ELIMINÓ.',
             estado='lapida')
        r = relevantes(d, ['plotspace/main.py'], 'tocar main')
        slugs = [m['slug'] for m in r]
        assert 'vieja' not in slugs
        assert 'lapida-scrollback' in slugs          # advertencia útil: no reintroducir
        # sin match de ruta, la lápida no aparece por tags/título débiles
        r2 = relevantes(d, [], 'algo sin relación')
        assert r2 == []


def test_bloque_formato_y_tope():
    with tempfile.TemporaryDirectory() as d:
        for i in range(8):
            _mem(d, f'm{i}', f'Cita `plotspace/core/x{i}.py` y `plotspace/core/comun.py`.')
        b = bloque_relevantes(d, ['plotspace/core/comun.py'], 'refactor', k=5)
        assert 'MEMORIAS DEL PROYECTO' in b
        assert b.count('  • ') == 5                  # top-K, no las 8
        assert 'INDEX.md' in b                       # siempre apunta al índice completo


def test_bloque_incluye_resumen_del_frontmatter():
    # el puntero solo ("slug — título") dependía de que el agente abriera el
    # archivo; con el resumen, hasta el agente vago se lleva el hecho
    with tempfile.TemporaryDirectory() as d:
        mdir = os.path.join(d, '.jarvis', 'memory')
        os.makedirs(mdir)
        with open(os.path.join(mdir, 'flow-control.md'), 'w', encoding='utf-8') as f:
            f.write("---\ntitulo: Flow control de websockets\ntags: [terminales]\n"
                    "resumen: El watermark se adapta por visibilidad para no floodear xterm.\n"
                    "creado: 2026-07-01\nestado: vigente\n---\n\n"
                    "Detalle largo de `plotspace/core/flow.py`.\n")
        b = bloque_relevantes(d, ['plotspace/core/flow.py'], 'tocar el flow control')
        assert 'El watermark se adapta por visibilidad' in b


def test_bloque_resumen_fallback_primera_linea():
    with tempfile.TemporaryDirectory() as d:
        _mem(d, 'sin-resumen', 'La primera línea del cuerpo es el hecho.\n\nMás detalle.',
             tags='preview')
        b = bloque_relevantes(d, [], 'algo del preview con sin-resumen en el texto',
                              usos={'sin-resumen': 0})
        if b:      # si superó el umbral, el fallback tiene que estar
            assert 'La primera línea del cuerpo es el hecho' in b


def test_bloque_vacio_sin_relevantes():
    with tempfile.TemporaryDirectory() as d:
        _mem(d, 'a', 'Nada de `plotspace/core/otro.py`.')
        assert bloque_relevantes(d, ['frontend/x.js'], 'nada que ver') == ''
        assert bloque_relevantes('/ruta/inexistente', ['a.py'], 'x') == ''


# ─── Integración: el bloque viaja en la tarea del agente ─────────────────────

def test_tarea_del_agente_inyecta_memorias():
    from plotspace.routers.orchestrator import _tarea_para_agente
    with tempfile.TemporaryDirectory() as d:
        _mem(d, 'gotcha-preview', 'Ojo con `frontend/sections/preview/preview.js`.')
        tarea = _tarea_para_agente('Ajustá el preview.',
                                   ['frontend/sections/preview/preview.js'], 7, project_ruta=d)
        assert '.jarvis/memory/gotcha-preview.md' in tarea
        assert '.jarvis/signals/terminal_7.json' in tarea     # el cierre sentinel sigue

        # sin ruta de proyecto: degrada sin bloque, sin romper
        tarea2 = _tarea_para_agente('Ajustá el preview.',
                                    ['frontend/sections/preview/preview.js'], 7)
        assert 'MEMORIAS DEL PROYECTO' not in tarea2
        assert 'gotcha-preview.md' not in tarea2
        assert '.jarvis/signals/terminal_7.json' in tarea2


def test_planning_recibe_memoria_relevante_al_pedido():
    """El orquestador que PLANIFICA agentes lo hace con las memorias relevantes
    al pedido — para que las tareas nazcan esquivando los errores conocidos."""
    from plotspace.routers.orchestrator import _bloque_memoria_para_orden
    with tempfile.TemporaryDirectory() as d:
        _mem(d, 'regla-de-puertos', 'El 3000 es de Jarvis; elegí un puerto libre '
             'antes de levantar un dev server.', titulo='Regla de puertos',
             tags='puertos, dev-server')
        b = _bloque_memoria_para_orden(d, 'levantá un dev server para el preview')
        assert 'Memoria' in b
        assert 'regla-de-puertos' in b


def test_planning_sin_memoria_no_agrega_bloque():
    from plotspace.routers.orchestrator import _bloque_memoria_para_orden
    with tempfile.TemporaryDirectory() as d:
        _mem(d, 'algo', 'tema sin relación alguna con el pedido.', titulo='Algo', tags='x')
        assert _bloque_memoria_para_orden(d, 'construí un login con OAuth') == ''
        # ruta inexistente degrada sin romper
        assert _bloque_memoria_para_orden('/no/existe', 'cualquier cosa') == ''


# ─── BM25 sobre los cuerpos: encuentra por contenido, no solo título/ruta ────

def test_bm25_encuentra_por_cuerpo_no_por_titulo():
    from plotspace.core.memoria_recall import relevantes
    with tempfile.TemporaryDirectory() as d:
        # el título NO menciona 'BlockingIOError', el cuerpo SÍ
        _mem(d, 'gotcha-paste', 'El paste largo tira BlockingIOError sobre el fd '
             'no-bloqueante y mata el pane si no se drena.', titulo='Cicatriz del PTY',
             tags='terminales')
        _mem(d, 'otra', 'Nada que ver, habla de colores y temas.', titulo='Temas', tags='ui')
        # sin match de ruta ni de título; solo el cuerpo tiene 'blockingioerror'
        r = relevantes(d, [], 'se rompe la terminal con un BlockingIOError al pegar')
        assert r and r[0]['slug'] == 'gotcha-paste'


def test_bm25_penaliza_terminos_comunes():
    from plotspace.core.memoria_recall import relevantes
    with tempfile.TemporaryDirectory() as d:
        # 'terminal' aparece en muchas → pesa poco; 'parakeet' es raro → pesa mucho
        for i in range(6):
            _mem(d, f'term-{i}', f'nota {i} sobre la terminal comun', titulo=f'Term {i}', tags='x')
        _mem(d, 'voz-parakeet', 'El motor parakeet en la terminal transcribe mejor.',
             titulo='Motor', tags='x')
        r = relevantes(d, [], 'parakeet en la terminal')
        assert r and r[0]['slug'] == 'voz-parakeet'


# ─── Ranking por uso: lo que demostró servir sube ────────────────────────────

def test_uso_desempata_a_favor_de_la_usada():
    from plotspace.core.memoria_recall import relevantes
    with tempfile.TemporaryDirectory() as d:
        # dos memorias casi idénticas en score de contenido
        _mem(d, 'guia-a', 'Cómo tocar el preview con cuidado.', titulo='Preview A', tags='preview')
        _mem(d, 'guia-b', 'Cómo tocar el preview con cuidado.', titulo='Preview B', tags='preview')
        sin_uso = [m['slug'] for m in relevantes(d, [], 'tocar el preview')]
        con_uso = [m['slug'] for m in relevantes(d, [], 'tocar el preview',
                                                 usos={'guia-b': 9})]
        assert sin_uso[:2] == ['guia-a', 'guia-b']       # empate → orden por slug
        assert con_uso[0] == 'guia-b', "la usada debe subir con el boost de uso"


if __name__ == '__main__':
    import traceback
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith('test_') and callable(fn):
            try:
                fn(); print(f'ok  {nombre}')
            except Exception:
                fallos += 1; print(f'FAIL {nombre}'); traceback.print_exc()
    sys.exit(1 if fallos else 0)
