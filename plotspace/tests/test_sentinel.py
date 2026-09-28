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


# ─── ruta_sentinel / instrucción de cierre ─────────────────────────────────────
def test_ruta_sentinel():
    r = sen.ruta_sentinel('/home/user/proj', 7)
    assert r == os.path.join('/home/user/proj', '.jarvis', 'signals', 'terminal_7.json')


def test_cierre_de_la_tarea_menciona_el_archivo():
    from plotspace.routers.orchestrator import _cierre_prompt_directo
    txt = _cierre_prompt_directo(7)
    assert '.jarvis/signals/terminal_7.json' in txt


def test_cierre_de_la_tarea_pide_postmortem():
    """Capa Loop: el cierre pide motivo OBLIGATORIO en bloqueo/error, los
    slugs de memorias usadas (medición de lectura) y una lección si el
    tropiezo era prevenible."""
    from plotspace.routers.orchestrator import _cierre_prompt_directo
    txt = _cierre_prompt_directo(7)
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


# ─── _ciclo: TODAS las terminales activas, por la vía del monitor ─────────────
def test_ciclo_procesa_sentinel_de_cada_terminal_activa():
    import asyncio
    import plotspace.routers.terminals as term

    with tempfile.TemporaryDirectory() as d:
        sig = os.path.join(d, '.jarvis', 'signals')
        os.makedirs(sig)
        with open(os.path.join(sig, 'terminal_8.json'), 'w') as f:
            f.write('{"estado":"done"}')
        llamadas = []

        async def _fake_proc(tid, pid, kw, motivo=None):
            llamadas.append((tid, pid, kw))

        orig_act = sen._terminales_activas
        orig_proc = term._procesar_keyword_evento
        orig_to_thread = asyncio.to_thread
        sen._terminales_activas = lambda: [{'id': 8, 'project_id': 7, 'ruta': d},
                                           {'id': 9, 'project_id': 7, 'ruta': d}]
        term._procesar_keyword_evento = _fake_proc

        async def _direct_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        asyncio.to_thread = _direct_to_thread
        try:
            asyncio.run(sen._ciclo())
        finally:
            sen._terminales_activas = orig_act
            term._procesar_keyword_evento = orig_proc
            asyncio.to_thread = orig_to_thread

        # solo la 8 tenía señal; la 9 no se toca
        assert llamadas == [(8, 7, 'TASK_DONE')]
        # one-shot: el archivo se consumió
        assert not os.path.exists(os.path.join(sig, 'terminal_8.json'))


def test_consumir_senal_registra_uso_de_memorias():
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from plotspace.tests._harness import fresh_db
    from plotspace.core import database as db

    fresh_db()
    with tempfile.TemporaryDirectory() as d:
        sig = os.path.join(d, '.jarvis', 'signals')
        os.makedirs(sig)
        with open(os.path.join(sig, 'terminal_8.json'), 'w') as f:
            f.write(json.dumps({'estado': 'blocked', 'motivo': 'puerto ocupado por otro server',
                                'memorias_usadas': ['regla-de-puertos']}))

        r = sen.consumir_senal(d, 7, 8)
        assert r and r['keyword'] == 'TASK_BLOCKED'
        assert r['motivo'] == 'puerto ocupado por otro server'
        assert not os.path.exists(os.path.join(sig, 'terminal_8.json')), 'one-shot'
        assert db.conteo_uso_memorias().get('regla-de-puertos') == 1


def test_consumir_senal_sin_archivo_es_none():
    with tempfile.TemporaryDirectory() as d:
        assert sen.consumir_senal(d, 7, 99) is None


def test_motivo_null_no_se_guarda_como_texto_None():
    # `"motivo": null` en el JSON del agente se guardaba como el string "None"
    # (str(None)) y llegaba así al destilador de lecciones.
    from plotspace.core import sentinel as _s
    r = _s.parsear('{"estado": "blocked", "motivo": null}')
    assert r is not None and r['motivo'] == ''
