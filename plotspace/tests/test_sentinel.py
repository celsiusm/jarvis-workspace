"""Tests del sentinel-file (cierre estructurado, multi-CLI)."""
import json
import os
import tempfile
import time

import plotspace.core.sentinel as sen


# ─── parsear: schema mínimo + rechazo de half-write ───────────────────────────
def test_parsea_done():
    d = sen.parsear('{"estado":"done"}')
    assert d == {'estado': 'done', 'keyword': 'TASK_DONE', 'motivo': '',
                 'memorias_usadas': []}


def test_parsea_blocked_con_motivo():
    d = sen.parsear('{"estado":"blocked","motivo":"falta la API key"}')
    assert d['keyword'] == 'TASK_BLOCKED'
    assert d['motivo'] == 'falta la API key'


def test_parsea_error():
    assert sen.parsear('{"estado":"error"}')['keyword'] == 'TASK_ERROR'


def test_estado_invalido_es_none():
    assert sen.parsear('{"estado":"cualquiera"}') is None
    assert sen.parsear('{"otra":"cosa"}') is None


def test_half_write_es_none():
    assert sen.parsear('{"estado":"do') is None     # JSON cortado a la mitad
    assert sen.parsear('') is None
    assert sen.parsear('[]') is None                # no es dict


def test_estado_case_insensitive():
    assert sen.parsear('{"estado":"DONE"}')['keyword'] == 'TASK_DONE'


# ─── ruta_sentinel / instruccion_cierre ───────────────────────────────────────
def test_ruta_sentinel():
    r = sen.ruta_sentinel('/home/user/proj', 7)
    assert r == os.path.join('/home/user/proj', '.jarvis', 'signals', 'terminal_7.json')


def test_instruccion_menciona_el_archivo_y_los_dos():
    txt = sen.instruccion_cierre(7)
    assert 'terminal_7.json' in txt
    assert 'TASK_DONE' in txt          # recalca hacer las dos cosas
    assert '.jarvis/signals' in txt


def test_instruccion_pide_postmortem():
    """Capa Loop: el cierre pide motivo OBLIGATORIO en bloqueo/error, los
    slugs de memorias usadas (medición de lectura) y una lección si el
    tropiezo era prevenible."""
    txt = sen.instruccion_cierre(7)
    assert 'motivo' in txt and 'OBLIGATORIO' in txt
    assert 'memorias_usadas' in txt
    assert 'leccion' in txt


def test_parsea_memorias_usadas():
    d = sen.parsear('{"estado":"done","memorias_usadas":["regla-de-puertos","preview-pestanas"]}')
    assert d['memorias_usadas'] == ['regla-de-puertos', 'preview-pestanas']
    # default y basura: lista vacía
    assert sen.parsear('{"estado":"done"}')['memorias_usadas'] == []
    assert sen.parsear('{"estado":"done","memorias_usadas":"no-lista"}')['memorias_usadas'] == []
    assert sen.parsear('{"estado":"done","memorias_usadas":[1,"ok",null]}')['memorias_usadas'] == ['ok']


# ─── leer_y_consumir: one-shot + frescura + half-write ────────────────────────
def test_lee_y_borra_one_shot():
    with tempfile.TemporaryDirectory() as d:
        sig_dir = os.path.join(d, '.jarvis', 'signals')
        os.makedirs(sig_dir)
        path = os.path.join(sig_dir, 'terminal_3.json')
        with open(path, 'w') as f:
            f.write('{"estado":"done"}')
        out = sen.leer_y_consumir(d, 3, iniciado_ts=None)
        assert out['keyword'] == 'TASK_DONE'
        assert not os.path.exists(path)   # one-shot: se borró
        # segunda lectura: ya no hay nada
        assert sen.leer_y_consumir(d, 3, iniciado_ts=None) is None


def test_ignora_sentinel_viejo():
    with tempfile.TemporaryDirectory() as d:
        sig_dir = os.path.join(d, '.jarvis', 'signals')
        os.makedirs(sig_dir)
        path = os.path.join(sig_dir, 'terminal_4.json')
        with open(path, 'w') as f:
            f.write('{"estado":"done"}')
        viejo = os.path.getmtime(path)
        # el paso arrancó DESPUÉS de que se escribió el sentinel → es de otra corrida
        out = sen.leer_y_consumir(d, 4, iniciado_ts=viejo + 100)
        assert out is None
        assert os.path.exists(path)       # no se consume un sentinel viejo


def test_half_write_no_se_borra():
    with tempfile.TemporaryDirectory() as d:
        sig_dir = os.path.join(d, '.jarvis', 'signals')
        os.makedirs(sig_dir)
        path = os.path.join(sig_dir, 'terminal_5.json')
        with open(path, 'w') as f:
            f.write('{"estado":"do')     # a medio escribir
        assert sen.leer_y_consumir(d, 5, iniciado_ts=None) is None
        assert os.path.exists(path)       # se deja para reintentar el próximo ciclo


def test_sin_archivo_es_none():
    with tempfile.TemporaryDirectory() as d:
        assert sen.leer_y_consumir(d, 9, iniciado_ts=None) is None


# ─── _ciclo: resuelve el paso por la vía del monitor (_procesar_keyword_evento) ─
def test_ciclo_procesa_sentinel_de_paso_running():
    import asyncio
    import plotspace.routers.terminals as term

    with tempfile.TemporaryDirectory() as d:
        sig = os.path.join(d, '.jarvis', 'signals')
        os.makedirs(sig)
        with open(os.path.join(sig, 'terminal_8.json'), 'w') as f:
            f.write('{"estado":"done"}')

        wf = {'id': 'wf1', 'project_id': 7, 'ruta': d, 'pasos': [
            {'agente': 'Backend', 'estado': 'running', 'terminal_id': 8, 'iniciado_ts': 1.0},
            {'agente': 'Tests', 'estado': 'done', 'terminal_id': 9, 'iniciado_ts': 1.0},
        ]}
        llamadas = []

        async def _fake_proc(tid, pid, kw, motivo=None):
            llamadas.append((tid, pid, kw))

        orig_wf = sen._workflows_running
        orig_proc = term._procesar_keyword_evento
        orig_to_thread = asyncio.to_thread
        orig_libres = sen._terminales_activas
        sen._workflows_running = lambda: [wf]
        sen._terminales_activas = lambda: []      # la pasada libre no toca la DB real acá
        term._procesar_keyword_evento = _fake_proc

        async def _direct_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        asyncio.to_thread = _direct_to_thread
        try:
            asyncio.run(sen._ciclo())
        finally:
            sen._workflows_running = orig_wf
            sen._terminales_activas = orig_libres
            term._procesar_keyword_evento = orig_proc
            asyncio.to_thread = orig_to_thread

        # resolvió SOLO el paso running con sentinel; el 'done' no se re-procesa
        assert llamadas == [(8, 7, 'TASK_DONE')]
        # one-shot: el archivo se consumió
        assert not os.path.exists(os.path.join(sig, 'terminal_8.json'))


# ─── Cierre estructurado FUERA de workflows (pasada libre) ────────────────────
# El enjambre trabaja mayormente en terminales directas: sin esta pasada, la
# telemetría (motivos → lecciones, memorias_usadas → salience) solo existía
# para workflows — task_events VACÍA fue el síntoma.

def test_terminales_libres_excluye_pasos_running():
    terminales = [{'id': 8, 'project_id': 7, 'ruta': '/p'},
                  {'id': 9, 'project_id': 7, 'ruta': '/p'}]
    wfs = [{'pasos': [{'estado': 'running', 'terminal_id': 8},
                      {'estado': 'done', 'terminal_id': 9}]}]
    libres = sen.terminales_libres(terminales, wfs)
    assert [t['id'] for t in libres] == [9], \
        'el paso running lo consume la pasada de workflows; el done ya es libre'


def test_procesar_senal_libre_registra_evento_y_uso():
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from plotspace.tests._harness import fresh_db
    from plotspace.core import database as db

    fresh_db()
    with tempfile.TemporaryDirectory() as d:
        conn = db.get_db()
        try:
            conn.execute("INSERT INTO projects (id, nombre, ruta, fecha_creacion, ultimo_acceso) "
                         "VALUES (7, 'p', ?, '2026-07-19', '2026-07-19')", (d,))
            conn.execute("INSERT INTO terminals (id, project_id, nombre, fecha_creacion) "
                         "VALUES (8, 7, 'Shell', '2026-07-19')")
            conn.commit()
        finally:
            conn.close()
        sig = os.path.join(d, '.jarvis', 'signals')
        os.makedirs(sig)
        with open(os.path.join(sig, 'terminal_8.json'), 'w') as f:
            f.write(json.dumps({'estado': 'blocked', 'motivo': 'puerto ocupado por otro server',
                                'memorias_usadas': ['regla-de-puertos']}))

        r = sen.procesar_senal_libre(d, 7, 8)
        assert r and r['keyword'] == 'TASK_BLOCKED'
        assert not os.path.exists(os.path.join(sig, 'terminal_8.json')), 'one-shot'

        conn = db.get_db()
        try:
            fila = conn.execute("SELECT event, motivo, workflow_id FROM task_events "
                                "WHERE terminal_id = 8").fetchone()
        finally:
            conn.close()
        assert fila['event'] == 'TASK_BLOCKED'
        assert fila['motivo'] == 'puerto ocupado por otro server'
        assert fila['workflow_id'] is None
        assert db.conteo_uso_memorias().get('regla-de-puertos') == 1


def test_procesar_senal_libre_sin_archivo_es_none():
    with tempfile.TemporaryDirectory() as d:
        assert sen.procesar_senal_libre(d, 7, 99) is None


def test_motivo_null_no_se_guarda_como_texto_None():
    # `"motivo": null` en el JSON del agente se guardaba como el string "None"
    # (str(None)) y llegaba así al destilador de lecciones.
    from plotspace.core import sentinel as _s
    r = _s.parsear('{"estado": "blocked", "motivo": null}')
    assert r is not None and r['motivo'] == ''
