# plotspace/tests/test_orq_contexto.py
"""Motor de contexto del orquestador: lo que ve `claude -p` de cada terminal y
del proyecto. Lógica pura + recolección contra una DB temporal (conftest)."""
import asyncio
import json
import subprocess

from plotspace.core import orq_contexto as oc


# ─── Pura ─────────────────────────────────────────────────────────────────────

def test_cola_pane_sin_ansi_ni_marcos_de_tui():
    texto = ('\x1b[32mcompilando\x1b[0m\n\n╭────────╮\n│        │\n'
             '╰────────╯\nlinea útil 1\nlinea útil 2\n')
    assert oc.cola_pane(texto, lineas=3) == ['compilando', 'linea útil 1', 'linea útil 2']


def test_cola_pane_corta_lineas_largas():
    cola = oc.cola_pane('x' * 500, lineas=1, ancho=50)
    assert len(cola[0]) == 50 and cola[0].endswith('…')


def test_es_libre_exige_viva_quieta_sin_pregunta_ni_paso():
    assert oc.es_libre({'fase': 'idle'})
    assert oc.es_libre({})
    assert not oc.es_libre({'fase': 'trabajando'})
    assert not oc.es_libre({'fase': 'arrancando'})
    assert not oc.es_libre({'fase': 'idle', 'esperando': True})
    assert not oc.es_libre({'fase': 'idle', 'estado': 'caido'})
    assert not oc.es_libre({'fase': 'idle', 'estado': 'sin_sesion'})
    assert not oc.es_libre({'fase': 'idle', 'paso_activo': {'estado': 'pending'}})


def test_motivo_no_libre_explica_el_porque():
    assert oc.motivo_no_libre({'fase': 'idle'}) == ''
    assert 'caído' in oc.motivo_no_libre({'estado': 'caido'})
    assert 'pregunta' in oc.motivo_no_libre({'esperando': True})
    assert 'trabajando' in oc.motivo_no_libre({'fase': 'trabajando'})
    assert 'pending' in oc.motivo_no_libre({'paso_activo': {'estado': 'pending'}})


def test_terminal_caida_se_marca_y_no_figura_libre():
    t = {'id': 7, 'nombre': 'Back', 'tipo_ia': 'claude'}
    txt = oc.formatear_bloque_terminales([t], {7: {'fase': 'idle', 'estado': 'caido'}})
    assert 'CAÍDA' in txt and 'libres: ninguna' in txt


def test_terminal_muestra_tarea_archivos_evento_y_pantalla():
    t = {'id': 3, 'nombre': 'Front', 'tipo_ia': 'claude'}
    info = {'fase': 'trabajando', 'fase_hace_s': 240, 'esperando': False,
            'tarea': {'texto': 'armá el login', 'hace_s': 300},
            'archivos': [{'path': 'a.js', 'dueno': True}, {'path': 'b.css'}],
            'ultimo_evento': {'event': 'TASK_BLOCKED', 'hace_s': 90, 'motivo': 'falta la API key'},
            'dev_servers': ['http://localhost:5173/'],
            'cola': ['npm run dev', 'ready in 300ms']}
    txt = oc.formatear_terminal(t, info)
    assert '🟢 trabajando hace 4 min' in txt
    assert 'tarea: armá el login' in txt
    assert 'a.js 🔒' in txt and 'b.css' in txt
    assert 'TASK_BLOCKED hace 1 min: falta la API key' in txt
    assert 'http://localhost:5173/' in txt
    assert '| ready in 300ms' in txt
    assert 'ocupada' in txt


def test_bloque_respeta_el_presupuesto():
    ts = [{'id': i, 'nombre': f'T{i}', 'tipo_ia': 'claude'} for i in range(40)]
    infos = {i: {'fase': 'idle', 'cola': ['x' * 140] * 6} for i in range(40)}
    txt = oc.formatear_bloque_terminales(ts, infos, tope=3000)
    assert len(txt) < 3600
    assert 'sin detalle por presupuesto' in txt


def test_repartir_colas_prioriza_a_quien_espera_respuesta():
    infos = {1: {'fase': 'idle', 'cola': ['a' * 140] * 6},
             2: {'fase': 'idle', 'esperando': True, 'cola': ['b' * 140] * 6}}
    oc.repartir_colas(infos, tope=1000)
    assert len(infos[2]['cola']) == 6
    assert len(infos[1]['cola']) < 6


def test_guia_desde_markdown_da_intro_e_indice():
    md = '# Proyecto\n\nApp de notas en Flask.\n\n## Comandos\nx\n## Reglas\ny\n'
    g = oc.guia_desde_markdown(md)
    assert 'App de notas en Flask.' in g
    assert 'secciones: Comandos · Reglas' in g


def test_formatear_workflows_activos_muestra_pasos_y_motivos():
    wf = {'nombre': 'Login', 'estado': 'paused', 'objetivo': 'auth',
          'pasos': [{'estado': 'done', 'agente': 'Back', 'terminal_id': 1},
                    {'estado': 'blocked', 'agente': 'Front', 'terminal_id': 2,
                     'depende_de': 'paso_0', 'motivo': 'falta diseño'}]}
    txt = oc.formatear_workflows_activos([wf])
    assert "'Login' (paused, 1/2 done)" in txt
    assert 'paso_1 [blocked] Front (terminal #2) ← paso_0' in txt
    assert 'motivo: falta diseño' in txt


def test_hace_humano():
    assert oc.hace(5) == '5s'
    assert oc.hace(125) == '2 min'
    assert oc.hace(7200) == '2 h'
    assert oc.hace(None) == ''


# ─── Recolección (DB temporal + git real) ─────────────────────────────────────

def _proyecto(tmp_path):
    from plotspace.core.database import get_db
    ruta = tmp_path / 'proy'
    ruta.mkdir()
    conn = get_db()
    conn.execute("INSERT INTO projects (id,nombre,ruta,fecha_creacion,ultimo_acceso) "
                 "VALUES (1,'P',?,'2026-01-01','2026-01-01')", (str(ruta),))
    for tid, n in ((11, 'Back'), (12, 'Front')):
        conn.execute("INSERT INTO terminals (id,project_id,nombre,tipo_ia,activa,"
                     "fecha_creacion) VALUES (?,1,?,'claude',1,'2026-01-01')", (tid, n))
    conn.commit()
    conn.close()
    return ruta


def test_infos_terminales_cruza_workflow_eventos_y_envios(tmp_path):
    _proyecto(tmp_path)
    from datetime import datetime
    from plotspace.core.database import get_db
    conn = get_db()
    pasos = [{'agente': 'Back', 'estado': 'running', 'terminal_id': 11, 'tarea': 'API de login'}]
    conn.execute("INSERT INTO workflows (id,project_id,nombre,objetivo,estado,pasos,"
                 "paso_actual,created_at) VALUES ('w',1,'Login','',?,?,0,?)",
                 ('running', json.dumps(pasos), '2026-01-01T00:00:00'))
    conn.execute("INSERT INTO task_events (terminal_id,project_id,event,timestamp,motivo) "
                 "VALUES (12,1,'TASK_BLOCKED',?,'no hay mock')", (datetime.now().isoformat(),))
    conn.commit()
    conn.close()
    oc.registrar_envio(12, 'arreglá el header')
    ts = [{'id': 11, 'nombre': 'Back', 'tipo_ia': 'claude'},
          {'id': 12, 'nombre': 'Front', 'tipo_ia': 'claude'}]
    infos = oc.infos_terminales(1, ts, con_live=False)
    assert infos[11]['paso_activo']['estado'] == 'running'
    assert infos[11]['tarea']['texto'] == 'API de login'
    assert not oc.es_libre(infos[11])
    assert infos[12]['tarea']['texto'] == 'arreglá el header'
    assert infos[12]['ultimo_evento']['motivo'] == 'no hay mock'


def test_info_git_ve_rama_cambios_y_commits(tmp_path):
    ruta = tmp_path / 'repo'
    ruta.mkdir()
    def g(*a):
        subprocess.run(['git', *a], cwd=ruta, check=True, capture_output=True)
    g('init', '-q', '-b', 'main')
    g('config', 'user.email', 't@t')
    g('config', 'user.name', 't')
    (ruta / 'a.txt').write_text('1')
    g('add', 'a.txt')
    g('commit', '-qm', 'feat: primero')
    (ruta / 'b.txt').write_text('2')
    info = oc.info_git(str(ruta))
    assert info['rama'] == 'main'
    assert info['cambios'] == ['b.txt']
    assert 'feat: primero' in info['commits'][0]
    assert 'rama: main' in oc.formatear_git(info)


def test_construir_bloques_arma_todo_y_degrada_sin_tmux(tmp_path, monkeypatch):
    ruta = _proyecto(tmp_path)
    (ruta / 'CLAUDE.md').write_text('# P\n\nUsá pytest.\n\n## Tests\nx\n')
    from plotspace.core import pane_capture

    async def falso(tid, ttl=0):
        return f'salida de {tid}\n'
    monkeypatch.setattr(pane_capture, 'capturar', falso)
    from plotspace.core.database import get_db
    conn = get_db()
    conn.execute("INSERT INTO tasks (project_id,titulo,estado) VALUES (1,'Dark mode','backlog')")
    conn.commit()
    conn.close()
    ts = [{'id': 11, 'nombre': 'Back', 'tipo_ia': 'claude'}]
    bloques = asyncio.run(oc.construir_bloques({'id': 1, 'nombre': 'P', 'ruta': str(ruta)}, ts))
    txt = '\n\n'.join(bloques)
    assert txt.startswith('[Estado actual]')
    assert '| salida de 11' in txt
    assert '[Proyecto]' in txt and 'CLAUDE.md' in txt
    assert '[Guía del proyecto]' in txt and 'Usá pytest.' in txt
    assert '[Tablero de tareas]' in txt and 'Dark mode' in txt


# ─── Integración con el orquestador ──────────────────────────────────────────

def test_rutas_en_texto_alimentan_el_recall():
    from plotspace.routers.orchestrator import _rutas_en_texto
    r = _rutas_en_texto('arreglá frontend/shell/workspace.js y main.py, '
                        'mirá https://x.com/a/b y ./scripts/jv.py')
    assert 'frontend/shell/workspace.js' in r
    assert 'main.py' in r
    assert 'scripts/jv.py' in r
    assert not any('://' in x or x.startswith('x.com') for x in r)


def test_el_prompt_del_orquestador_lleva_el_contexto_completo(tmp_path, monkeypatch):
    ruta = _proyecto(tmp_path)
    (ruta / 'CLAUDE.md').write_text('# P\n\nReglas del proyecto.\n')
    import plotspace.routers.orchestrator as orch
    from plotspace.core import pane_capture

    async def falso(tid, ttl=0):
        return '❯ ¿Sobrescribo el archivo? (y/n)\n'
    monkeypatch.setattr(pane_capture, 'capturar', falso)
    monkeypatch.setattr(orch, 'ORQUESTADOR_MOTOR', 'suscripcion')
    monkeypatch.setattr(orch, '_guard_cli', lambda: None)
    req = orch.ChatRequest(project_id=1, message='revisá main.py')
    _, _, mensajes, _ = asyncio.run(orch._preparar_contexto_chat(req))
    txt = mensajes[-1]['content']
    assert '[Estado actual]' in txt and '¿Sobrescribo el archivo?' in txt
    assert '[Proyecto]' in txt and '[Guía del proyecto]' in txt
    assert txt.rstrip().endswith('revisá main.py')


def test_infos_con_workflows_explicitos_ignora_los_excluidos(tmp_path):
    """ejecutar_workflow valida el reuso SIN su propio workflow (ya guardado con
    pasos pending): si no, la terminal pedida siempre figuraba ocupada."""
    _proyecto(tmp_path)
    ts = [{'id': 11, 'nombre': 'Back', 'tipo_ia': 'claude'}]
    propio = {'id': 'nuevo', 'nombre': 'N', 'estado': 'running',
              'pasos': [{'terminal_id': 11, 'estado': 'pending', 'tarea': 't'}]}
    assert not oc.es_libre(oc.infos_terminales(1, ts, [propio], [], con_live=False)[11])
    assert oc.es_libre(oc.infos_terminales(1, ts, [], [], con_live=False)[11])


def test_titulos_contexto_para_mostrar_que_vio():
    from plotspace.routers.orchestrator import _titulos_contexto
    msg = ('[Estado actual]\nx\n\n[Proyecto]\ny\n\n[Memoria relevante al pedido — '
           'tenela en cuenta:]\nz\n\n[Orden]\nhola')
    assert _titulos_contexto([{'role': 'user', 'content': msg}]) == [
        'Estado actual', 'Proyecto', 'Memoria relevante al pedido']
    assert _titulos_contexto([]) == []


def test_stream_sse_emite_contexto_progreso_reinicio_y_done(tmp_path, monkeypatch):
    _proyecto(tmp_path)
    import plotspace.routers.orchestrator as orch
    from plotspace.core import orq_cli, pane_capture

    async def pane(tid, ttl=0):
        return ''
    monkeypatch.setattr(pane_capture, 'capturar', pane)
    monkeypatch.setattr(orch, 'ORQUESTADOR_MOTOR', 'suscripcion')
    monkeypatch.setattr(orch, '_guard_cli', lambda: None)

    async def falso_stream(*a, **k):
        yield {'tipo': 'delta', 'texto': '{"message": "mir'}
        yield {'tipo': 'herramienta', 'nombre': 'Read', 'detalle': 'main.py'}
        yield {'tipo': 'reinicio'}
        yield {'tipo': 'delta', 'texto': '{"message": "listo"'}
        yield {'tipo': 'resultado', 'texto': '{"message":"listo","actions":[]}',
               'error': False, 'input_tokens': 1, 'output_tokens': 1}
    monkeypatch.setattr(orq_cli, 'stream', falso_stream)

    async def procesar(*a, **k):
        return {'response': 'listo', 'actions': [], 'created_terminals': [],
                'closed_all': False, 'workflow_card': None}
    monkeypatch.setattr(orch, '_procesar_respuesta_orquestador', procesar)

    async def correr():
        resp = await orch.chat_orquestador_stream(orch.ChatRequest(project_id=1, message='hola'))
        return [json.loads(c[5:]) async for c in resp.body_iterator]
    evs = asyncio.run(correr())
    tipos = [e['type'] for e in evs]
    assert tipos[0] == 'contexto' and 'Estado actual' in evs[0]['bloques']
    assert {'type': 'progreso', 'herramienta': 'Read', 'detalle': 'main.py'} in evs
    assert tipos.index('reinicio') < len(tipos) - 1
    assert tipos[-1] == 'done'
