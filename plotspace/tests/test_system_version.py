"""
Test: in-app updater (plotspace/routers/system.py).

Detección git-based (_estado_git) + el endpoint /api/system/version vía TestClient.
La lógica pura de semver/changelog se conserva como fallback sin git.
/restart se prueba con reiniciar_servidor mockeado (no reinicia de verdad).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plotspace.routers import system


# ─── Lógica pura (fallback semver/changelog) ─────────────────────────────────

def test_semver_mayor():
    assert system._semver_mayor('4.1.0', '4.0.0')
    assert system._semver_mayor('4.0.1', '4.0.0')
    assert system._semver_mayor('5.0.0', '4.9.9')
    assert not system._semver_mayor('4.0.0', '4.0.0')
    assert not system._semver_mayor('4.0.0', '4.1.0')


def test_semver_tolera_basura():
    assert not system._semver_mayor('', '0.0.0')
    assert system._semver_mayor('4.1', '4.0.0')      # 4.1 → (4,1,0)
    assert not system._semver_mayor('x.y.z', '0.0.1')


def test_novedades_filtra_por_version():
    cl = "## 4.2.0\n- dos\n## 4.1.0\n- uno A\n- uno B\n## 4.0.0\n- viejo\n"
    nov = system._novedades_entre(cl, '4.0.0', '4.2.0')
    assert [n['version'] for n in nov] == ['4.2.0', '4.1.0']  # 4.0.0 (==corriendo) excluida
    assert nov[1]['items'] == ['uno A', 'uno B']


def test_novedades_caplimita_a_disponible():
    cl = "## 4.3.0\n- futuro\n## 4.1.0\n- bueno\n"
    nov = system._novedades_entre(cl, '4.0.0', '4.1.0')
    assert [n['version'] for n in nov] == ['4.1.0']  # 4.3.0 > disponible → fuera


def test_novedades_sin_update_vacio():
    cl = "## 4.0.0\n- algo\n"
    assert system._novedades_entre(cl, '4.0.0', '4.0.0') == []


# ─── Comando de arranque del server (loop=asyncio, anti-stall de uvloop) ─────

def test_comando_uvicorn_fuerza_asyncio():
    """El re-exec del updater debe levantar con `--loop asyncio`: uvloop sufre
    un stall periódico de ~400ms del event loop en este entorno (WSL2 + Py3.14)
    que se ve como cortes en el eco del tipeo. Sin este flag uvicorn elige uvloop."""
    cmd = system._comando_uvicorn()
    assert '--loop' in cmd
    assert cmd[cmd.index('--loop') + 1] == 'asyncio'
    # Sigue siendo un arranque de uvicorn sobre la app correcta, host/port default.
    assert 'uvicorn' in cmd
    assert 'plotspace.main:app' in cmd
    assert cmd[cmd.index('--port') + 1] == '3000'
    assert cmd[cmd.index('--host') + 1] == '0.0.0.0'


def test_comando_uvicorn_params_custom():
    cmd = system._comando_uvicorn(python='/x/py', host='127.0.0.1', port='5055')
    assert cmd[0] == '/x/py'
    assert cmd[cmd.index('--host') + 1] == '127.0.0.1'
    assert cmd[cmd.index('--port') + 1] == '5055'
    assert cmd[cmd.index('--loop') + 1] == 'asyncio'


# ─── Detección del event loop (uvloop = stall periódico → cortes de tipeo) ────
# uvloop (el default de uvicorn[standard]) traba el event loop ~0.4-1s de forma
# periódica en este entorno (WSL2 + Py3.14) bajo el workload subprocess/PTY →
# se congela el eco de TODAS las terminales a la vez (el "corte de un segundo").
# El guard de arranque lo detecta y lo hace VISIBLE en vez de silencioso.

def test_nombre_event_loop_detecta_uvloop():
    class _LoopFake:
        pass
    _LoopFake.__module__ = 'uvloop'
    assert system.nombre_event_loop(_LoopFake()) == 'uvloop'


def test_nombre_event_loop_detecta_asyncio():
    class _LoopFake:
        pass
    _LoopFake.__module__ = 'asyncio.unix_events'
    assert system.nombre_event_loop(_LoopFake()) == 'asyncio'


def test_nombre_event_loop_objeto_neutro_no_degrada():
    # Un loop cuyo módulo no es uvloop NO debe reportarse como degradado.
    assert system.nombre_event_loop(object()) == 'asyncio'


def test_nombre_event_loop_sin_running_loop_default_seguro():
    # Sin loop corriendo (contexto sync, loop=None) NO explota y asume el default
    # seguro 'asyncio' — jamás alarma de más por no poder medir.
    assert system.nombre_event_loop(None) == 'asyncio'


def test_loop_degradado():
    assert system.loop_degradado('uvloop') is True
    assert system.loop_degradado('asyncio') is False


def test_main_module_fuerza_asyncio():
    # `python -m backend` es el arranque a prueba de olvidos: hornea loop=asyncio.
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    src = open(os.path.join(repo, 'plotspace', '__main__.py'), encoding='utf-8').read()
    assert ("loop='asyncio'" in src) or ('loop="asyncio"' in src)


def test_dockerfile_fuerza_asyncio():
    # OSS: el contenedor NO debe quedar en uvloop. El CMD del Dockerfile fuerza
    # --loop asyncio (regresión-lock: que nadie lo saque sin querer).
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dockerfile = open(os.path.join(repo, 'Dockerfile'), encoding='utf-8').read()
    # Tiene que aparecer la dupla --loop asyncio dentro del archivo.
    assert '--loop' in dockerfile
    norm = dockerfile.replace('"', '').replace("'", '').replace(',', ' ')
    assert 'asyncio' in norm


# ─── Versionado automático (cada update aplicado avanza la versión) ──────────

def test_calcular_siguiente_version_patch():
    # Formato x.x.xx: el patch siempre con dos dígitos
    assert system.calcular_siguiente_version('1.5.0') == '1.5.01'
    assert system.calcular_siguiente_version('1.5.8') == '1.5.09'
    assert system.calcular_siguiente_version('1.5.09') == '1.5.10'
    # un update normal "cierra" la serie de hotfixes: el 4º segmento se descarta
    assert system.calcular_siguiente_version('1.5.01.3') == '1.5.02'


def test_calcular_siguiente_version_rollover():
    # el patch no pasa de dos dígitos: 99 desborda al minor
    assert system.calcular_siguiente_version('1.5.99') == '1.6.00'
    assert system.calcular_siguiente_version('1.6.00') == '1.6.01'


def test_calcular_siguiente_version_hotfix():
    # el hotfix suma 4º segmento y normaliza la base al formato x.x.xx
    assert system.calcular_siguiente_version('1.5.01', hotfix=True) == '1.5.01.1'
    assert system.calcular_siguiente_version('1.5.1.1', hotfix=True) == '1.5.01.2'
    assert system.calcular_siguiente_version('1.5.8', hotfix=True) == '1.5.08.1'


def test_calcular_siguiente_version_tolera_basura():
    assert system.calcular_siguiente_version('') == '0.0.01'
    assert system.calcular_siguiente_version('x.y.z') == '0.0.01'


def test_es_hotfix():
    fix   = {'subject': 'fix(voz): límite de tamaño', 'files': []}
    feat  = {'subject': 'feat: cosa nueva', 'files': []}
    ruido = {'subject': 'chore: deps', 'files': []}
    assert system._es_hotfix([fix]) is True
    assert system._es_hotfix([fix, ruido]) is True    # el ruido no vota
    assert system._es_hotfix([fix, feat]) is False    # hay una feature → update normal
    assert system._es_hotfix([]) is False             # solo ediciones sin commitear → normal
    assert system._es_hotfix([ruido]) is False


def test_semver_mayor_cuarto_segmento():
    # los hotfixes (1.5.1.1) tienen que ordenar por encima de su base (1.5.1)
    assert system._semver_mayor('1.5.1.1', '1.5.1')
    assert not system._semver_mayor('1.5.1', '1.5.1.1')


# ─── Canary de arranque (gate del restart) ───────────────────────────────────

def test_canary_import_real(tmp_path, monkeypatch):
    # Canary de verdad (subproceso): un módulo sano pasa, uno roto se reporta
    # con el error visible — es lo que protege el botón Actualizar.
    (tmp_path / 'mod_ok.py').write_text('x = 1\n')
    (tmp_path / 'mod_roto.py').write_text('def f(:\n')
    monkeypatch.setattr(system, '_REPO_ROOT', str(tmp_path))
    ok, err = system._canary_import('mod_ok')
    assert ok is True and err == ''
    ok, err = system._canary_import('mod_roto')
    assert ok is False and 'SyntaxError' in err


# ─── Detección git (_estado_git) ─────────────────────────────────────────────

def test_estado_git_detecta_commits(monkeypatch):
    monkeypatch.setattr(system, '_FIRMA_BOOT', ('aaa', 'firma-boot'))
    monkeypatch.setattr(system, '_COMMIT_BOOT', 'aaa')
    monkeypatch.setattr(system, '_firma_codigo', lambda: ('bbb', 'firma-nueva'))
    monkeypatch.setattr(system, '_commits_con_archivos', lambda: [
        {'subject': 'feat(automations): botón ⚡ en la barra', 'files': ['frontend/sections/automations/a.js']},
        {'subject': 'perf(db): índice nuevo', 'files': ['plotspace/core/database.py']},
        {'subject': 'fix(seguridad): límite de tamaño en voz', 'files': ['plotspace/routers/voice.py']},
        {'subject': 'chore: bump deps', 'files': ['x']},           # ruido → fuera
        {'subject': 'Merge branch main', 'files': []},             # merge → fuera
    ])
    hay, titulo, nov = system._estado_git()
    assert hay is True
    assert titulo == 'Hay una nueva versión'              # texto simple, fijo
    areas = {g['area']: g for g in nov}
    assert [g['area'] for g in nov] == ['Interfaz', 'Motor', 'Seguridad']   # orden fijo
    assert areas['Interfaz']['items'] == ['Botón ⚡ en la barra']           # prefijo limpio + capitalizado
    assert areas['Interfaz']['icon'] == '🎨'
    assert areas['Motor']['items'] == ['Índice nuevo']
    assert areas['Seguridad']['items'] == ['Límite de tamaño en voz']      # keyword 'segur' → Seguridad


# ─── Gate de app: unir "Actualizar ahora" con el build del shell ─────────────

def _mock_commits_desktop(monkeypatch):
    monkeypatch.setattr(system, '_FIRMA_BOOT', ('aaa', 'boot'))
    monkeypatch.setattr(system, '_COMMIT_BOOT', 'aaa')
    monkeypatch.setattr(system, '_firma_codigo', lambda: ('bbb', 'nueva'))


def test_area_de_clasifica_app_como_shell():
    assert system._area_de('feat: splash', ['desktop/ui/index.html']) == 'App'
    # desktop gana sobre frontend cuando el commit toca ambos
    assert system._area_de('feat: x', ['desktop/a', 'frontend/b']) == 'App'
    # seguridad sigue ganando por keyword
    assert system._area_de('fix(security): x', ['desktop/a']) == 'Seguridad'


def test_limpiar_subject():
    assert system._limpiar_subject('feat(x): hola mundo')[1] == 'Hola mundo'
    assert system._limpiar_subject('fix!: algo')[1] == 'Algo'
    assert system._limpiar_subject('sin prefijo convencional')[1] == 'Sin prefijo convencional'
    assert system._limpiar_subject('Merge branch a')[1] == ''       # merge → vacío
    assert system._limpiar_subject('chore: x')[0] == 'chore'        # tipo detectado
    largo = system._limpiar_subject('feat: ' + 'a' * 120)[1]
    assert len(largo) <= 72 and largo.endswith('…')                 # recortado y corto


def test_agrupar_novedades_categoriza_dedupe_y_orden():
    commits = [
        {'subject': 'feat(ui): nuevo ícono', 'files': ['frontend/a.js']},
        {'subject': 'fix(ui): nuevo ícono', 'files': ['frontend/a.js']},   # dup del texto → una sola vez
        {'subject': 'fix(api): bug del login', 'files': ['plotspace/b.py']},
        {'subject': 'docs(security): nota', 'files': ['README.md']},        # keyword → Seguridad aunque no toque backend
    ]
    nov = system._agrupar_novedades(commits)
    areas = {g['area']: g['items'] for g in nov}
    assert areas['Interfaz'] == ['Nuevo ícono']        # dedupe
    assert areas['Motor'] == ['Bug del login']
    assert areas['Seguridad'] == ['Nota']
    assert [g['area'] for g in nov] == ['Interfaz', 'Motor', 'Seguridad']


def test_commits_con_archivos_parsea(monkeypatch):
    monkeypatch.setattr(system, '_COMMIT_BOOT', 'aaa')
    salida = '\x1ffeat(x): uno\nfrontend/a.js\nplotspace/b.py\n\x1ffix: dos\nplotspace/c.py\n'
    monkeypatch.setattr(system, '_git', lambda *a: salida if a[0] == 'log' else '')
    commits = system._commits_con_archivos()
    assert len(commits) == 2
    assert commits[0]['subject'] == 'feat(x): uno'
    assert commits[0]['files'] == ['frontend/a.js', 'plotspace/b.py']
    assert commits[1]['files'] == ['plotspace/c.py']


def test_estado_git_sin_cambios(monkeypatch):
    monkeypatch.setattr(system, '_FIRMA_BOOT', ('aaa', 'misma'))
    monkeypatch.setattr(system, '_COMMIT_BOOT', 'aaa')          # HEAD no se movió
    monkeypatch.setattr(system, '_firma_codigo', lambda: ('aaa', 'misma'))
    hay, titulo, nov = system._estado_git()
    assert hay is False and nov == []


def test_estado_git_sin_commitear_no_dispara(monkeypatch):
    # Mismo HEAD, distinta firma → SOLO ediciones sin commitear (trabajo en curso):
    # el banner NO se prende. "Actualizar ahora" recién aparece cuando la tarea se
    # commitea (pedido del usuario 2026-06-18): así se aplica lo terminado aunque
    # otra terminal de Jarvis siga trabajando.
    monkeypatch.setattr(system, '_FIRMA_BOOT', ('aaa', 'boot'))
    monkeypatch.setattr(system, '_COMMIT_BOOT', 'aaa')
    monkeypatch.setattr(system, '_firma_codigo', lambda: ('aaa', 'nueva'))   # mismo HEAD
    hay, titulo, nov = system._estado_git()
    assert hay is False
    assert nov == []


def test_estado_git_none_si_no_es_repo(monkeypatch):
    monkeypatch.setattr(system, '_firma_codigo', lambda: None)
    assert system._estado_git() is None


def test_firma_detecta_ediciones_en_untracked(tmp_path, monkeypatch):
    # Los archivos sin trackear no aparecen en `git diff`: la firma tiene que
    # cubrir también sus EDICIONES (tamaño/mtime), no solo su aparición en status.
    f = tmp_path / 'plotspace' / 'nuevo.py'
    f.parent.mkdir(parents=True)
    f.write_text('a = 1\n')

    def fake_git(*a):
        if a[0] == 'rev-parse':
            return 'HEAD123\n'
        if a[0] == 'ls-files':
            return 'plotspace/nuevo.py\n'
        return ''
    monkeypatch.setattr(system, '_git', fake_git)
    monkeypatch.setattr(system, '_REPO_ROOT', str(tmp_path))

    firma_1 = system._firma_codigo()
    f.write_text('a = 2  # editado\n')
    os.utime(f, ns=(1, 1))   # mtime distinto garantizado
    firma_2 = system._firma_codigo()
    assert firma_1[1] != firma_2[1], 'editar un untracked debe cambiar la firma'


def test_firma_solo_mira_codigo(monkeypatch):
    # _firma_codigo acota diff/status a plotspace/ + frontend/ → los archivos que
    # la app reescribe en runtime (.jarvis/LIVE.md, etc.) NO alteran la firma.
    llamadas = []

    def fake_git(*a):
        llamadas.append(a)
        return 'HEAD123\n' if a[0] == 'rev-parse' else ''
    monkeypatch.setattr(system, '_git', fake_git)
    system._firma_codigo()
    diff_call   = next(a for a in llamadas if a[0] == 'diff')
    status_call = next(a for a in llamadas if a[0] == 'status')
    for call in (diff_call, status_call):
        assert '--' in call and 'plotspace' in call and 'frontend' in call


# ─── Endpoint /version (TestClient) ──────────────────────────────────────────

def _client():
    from plotspace.tests._harness import fresh_db
    fresh_db()
    import plotspace.main as main
    from fastapi.testclient import TestClient
    return TestClient(main.app)


def test_endpoint_hay_update(monkeypatch):
    monkeypatch.setattr(system, '_estado_git',
                        lambda: (True, 'Hay una nueva versión', [{'area': 'Interfaz', 'icon': '🎨', 'items': ['a', 'b']}]))
    client = _client()
    r = client.get('/api/system/version')
    assert r.status_code == 200, r.text
    d = r.json()
    assert d['hay_update'] is True
    assert d['titulo'] == 'Hay una nueva versión'
    assert d['novedades'][0]['area'] == 'Interfaz'
    assert d['novedades'][0]['items'] == ['a', 'b']


def test_endpoint_sin_update(monkeypatch):
    monkeypatch.setattr(system, '_estado_git', lambda: (False, '', []))
    client = _client()
    d = client.get('/api/system/version').json()
    assert d['hay_update'] is False
    assert d['novedades'] == []


def test_endpoint_fallback_sin_git(tmp_path, monkeypatch):
    monkeypatch.setattr(system, '_estado_git', lambda: None)   # fuerza el modo viejo
    vf = tmp_path / 'VERSION'; vf.write_text('4.1.0')
    cf = tmp_path / 'CHANGELOG.md'; cf.write_text('## 4.1.0\n- algo nuevo\n')
    monkeypatch.setattr(system, '_VERSION_PATH', str(vf))
    monkeypatch.setattr(system, '_CHANGELOG_PATH', str(cf))
    monkeypatch.setattr(system, '_VERSION_BOOT', '4.0.0')
    client = _client()
    d = client.get('/api/system/version').json()
    assert d['hay_update'] is True
    assert d['novedades'][0]['area'] == 'Novedades'        # fallback: un solo grupo
    assert 'algo nuevo' in d['novedades'][0]['items']


def test_endpoint_expone_event_loop(monkeypatch):
    # /version informa qué event loop corre el server para que el frontend pueda
    # avisar si quedó en uvloop. El TestClient corre sobre asyncio → loop sano.
    monkeypatch.setattr(system, '_estado_git', lambda: (False, '', []))
    client = _client()
    d = client.get('/api/system/version').json()
    assert d['event_loop'] == 'asyncio'
    assert d['loop_degradado'] is False


def test_endpoint_loop_degradado_cuando_uvloop(monkeypatch):
    # Si el server arrancó sin --loop asyncio y quedó en uvloop, /version lo marca
    # degradado → el banner de la franja ofrece reiniciar para optimizar el tipeo.
    monkeypatch.setattr(system, '_estado_git', lambda: (False, '', []))
    monkeypatch.setattr(system, 'nombre_event_loop', lambda loop=None: 'uvloop')
    client = _client()
    d = client.get('/api/system/version').json()
    assert d['event_loop'] == 'uvloop'
    assert d['loop_degradado'] is True


def test_reexec_reemplaza_proceso_in_place(monkeypatch):
    # El reinicio debe ser un re-exec del MISMO proceso (mismo PID, misma
    # terminal/sesión), no un relauncher detached: en WSL un proceso huérfano
    # muere cuando se cierra la última consola (se apaga el distro entero).
    llamadas = {}
    monkeypatch.setattr(system.os, 'chdir', lambda p: llamadas.__setitem__('cwd', p))
    monkeypatch.setattr(system.os, 'execv', lambda exe, argv: llamadas.__setitem__('exec', (exe, argv)))
    system._reexec()
    assert llamadas['cwd'] == system._REPO_ROOT
    exe, argv = llamadas['exec']
    assert exe == sys.executable
    assert argv[:3] == [sys.executable, '-m', 'uvicorn']
    assert 'plotspace.main:app' in argv
    assert '3000' in argv


def test_reiniciar_servidor_difiere_el_reexec(monkeypatch):
    # reiniciar_servidor() responde primero (la respuesta HTTP tiene que salir)
    # y hace el re-exec en un hilo aparte tras el retraso.
    import threading
    hecho = threading.Event()
    monkeypatch.setattr(system, '_reexec', hecho.set)
    system.reiniciar_servidor(retraso=0)
    assert hecho.wait(2), 'el re-exec diferido nunca corrió'


def test_endpoint_incluye_proxima(monkeypatch):
    # Con update pendiente, /version anuncia a qué versión se va a saltar
    # (el banner muestra "v1.5.0 → v1.5.1").
    monkeypatch.setattr(system, '_estado_git', lambda: (True, 'Hay una nueva versión', []))
    monkeypatch.setattr(system, '_commits_con_archivos', lambda: [{'subject': 'feat: x', 'files': []}])
    monkeypatch.setattr(system, '_VERSION_BOOT', '1.5.0')
    client = _client()
    d = client.get('/api/system/version').json()
    assert d['corriendo'] == '1.5.0'
    assert d['proxima'] == '1.5.01'


def test_endpoint_proxima_hotfix(monkeypatch):
    # Si TODO lo nuevo son fixes, la próxima es un hotfix: 4º segmento.
    monkeypatch.setattr(system, '_estado_git', lambda: (True, 'Hay una nueva versión', []))
    monkeypatch.setattr(system, '_commits_con_archivos', lambda: [{'subject': 'fix: bug feo', 'files': []}])
    monkeypatch.setattr(system, '_VERSION_BOOT', '1.5.1')
    client = _client()
    d = client.get('/api/system/version').json()
    assert d['proxima'] == '1.5.01.1'


def test_endpoint_proxima_sin_update_es_la_corriente(monkeypatch):
    monkeypatch.setattr(system, '_estado_git', lambda: (False, '', []))
    monkeypatch.setattr(system, '_VERSION_BOOT', '1.5.0')
    client = _client()
    d = client.get('/api/system/version').json()
    assert d['proxima'] == '1.5.0'


# ─── Gate por agentes trabajando (el banner espera a que terminen) ───────────

def test_agentes_trabajando_combina_fuentes(monkeypatch):
    # Scopeado al proyecto de Jarvis. agent_watch ocupado (en sus terminales) →
    # True; todo libre → False.
    monkeypatch.setattr(system, '_jarvis_project_id', lambda: 17)
    monkeypatch.setattr(system, '_terminales_activas_de', lambda pid: {1, 2})
    monkeypatch.setattr(system.agent_watch, 'hay_agentes_ocupados',
                        lambda estados=None, terminal_ids=None: True)
    assert system._agentes_trabajando() is True
    monkeypatch.setattr(system.agent_watch, 'hay_agentes_ocupados',
                        lambda estados=None, terminal_ids=None: False)
    assert system._agentes_trabajando() is False


def test_agentes_trabajando_ignora_otros_proyectos(monkeypatch):
    # Terminales de Jarvis (1,2) idle; otra (99, de otro proyecto) trabajando →
    # scopeado a Jarvis = libre (el otro proyecto NO bloquea el update).
    monkeypatch.setattr(system, '_jarvis_project_id', lambda: 17)
    monkeypatch.setattr(system, '_terminales_activas_de', lambda pid: {1, 2})
    monkeypatch.setattr(system.agent_watch, '_estados',
                        {1: {'fase': 'idle'}, 2: {'fase': 'idle'}, 99: {'fase': 'trabajando'}},
                        raising=False)
    assert system._agentes_trabajando() is False   # 99 (otro proyecto) no cuenta
    monkeypatch.setattr(system.agent_watch, '_estados',
                        {1: {'fase': 'idle'}, 2: {'fase': 'trabajando'}, 99: {'fase': 'idle'}},
                        raising=False)
    assert system._agentes_trabajando() is True     # una terminal de Jarvis trabaja


def test_endpoint_expone_agentes_trabajando(monkeypatch):
    # El endpoint sigue exponiendo agentes_trabajando como dato INFORMATIVO (¿hay
    # laburo en curso en Jarvis?). Ya NO gatea el banner: eso lo decide hay_update
    # = commit nuevo (ver _estado_git). El campo queda por si lo usa el modal.
    monkeypatch.setattr(system, '_estado_git', lambda: (True, 'Hay una nueva versión', []))
    monkeypatch.setattr(system, '_agentes_trabajando', lambda: True)
    client = _client()
    d = client.get('/api/system/version').json()
    assert d['hay_update'] is True
    assert d['agentes_trabajando'] is True


def test_endpoint_agentes_libres(monkeypatch):
    monkeypatch.setattr(system, '_estado_git', lambda: (True, 'Hay una nueva versión', []))
    monkeypatch.setattr(system, '_agentes_trabajando', lambda: False)
    client = _client()
    d = client.get('/api/system/version').json()
    assert d['agentes_trabajando'] is False


def test_endpoint_fallback_sin_git_tambien_expone_agentes(monkeypatch, tmp_path):
    monkeypatch.setattr(system, '_estado_git', lambda: None)   # modo viejo
    vf = tmp_path / 'VERSION'; vf.write_text('4.1.0')
    monkeypatch.setattr(system, '_VERSION_PATH', str(vf))
    monkeypatch.setattr(system, '_VERSION_BOOT', '4.0.0')
    monkeypatch.setattr(system, '_agentes_trabajando', lambda: True)
    client = _client()
    d = client.get('/api/system/version').json()
    assert d['agentes_trabajando'] is True


# ─── Endpoint /restart: canary + bump de versión ─────────────────────────────

def _preparar_restart(monkeypatch, tmp_path, canary=(True, ''),
                      estado=(True, 'Hay una nueva versión', []), commits=()):
    """Deja /restart inofensivo y observable: canary/git/VERSION mockeados."""
    llamado = {'reinicios': 0}
    vf = tmp_path / 'VERSION'
    vf.write_text('1.5.0\n')
    monkeypatch.setattr(system, '_canary_import', lambda modulo='plotspace.main': canary)
    monkeypatch.setattr(system, '_estado_git', lambda: estado)
    monkeypatch.setattr(system, '_commits_con_archivos', lambda: list(commits))
    monkeypatch.setattr(system, '_VERSION_BOOT', '1.5.0')
    monkeypatch.setattr(system, '_VERSION_PATH', str(vf))
    monkeypatch.setattr(system, 'reiniciar_servidor',
                        lambda: llamado.__setitem__('reinicios', llamado['reinicios'] + 1))
    return llamado, vf


def test_restart_endpoint_no_reinicia_de_verdad(monkeypatch, tmp_path):
    llamado, _ = _preparar_restart(monkeypatch, tmp_path, estado=(False, '', []))
    client = _client()
    r = client.post('/api/system/restart')
    assert r.status_code == 200
    assert r.json()['ok'] is True
    assert llamado['reinicios'] == 1   # se llamó al relauncher (mockeado)


def test_restart_bumpea_patch(monkeypatch, tmp_path):
    # Aplicar un update normal escribe VERSION con el patch siguiente.
    llamado, vf = _preparar_restart(monkeypatch, tmp_path,
                                    commits=[{'subject': 'feat: x', 'files': []}])
    client = _client()
    d = client.post('/api/system/restart').json()
    assert d['ok'] is True and d['version'] == '1.5.01'
    assert vf.read_text().strip() == '1.5.01'
    assert llamado['reinicios'] == 1


def test_restart_bumpea_hotfix(monkeypatch, tmp_path):
    llamado, vf = _preparar_restart(monkeypatch, tmp_path,
                                    commits=[{'subject': 'fix: urgente', 'files': []}])
    client = _client()
    d = client.post('/api/system/restart').json()
    assert d['version'] == '1.5.00.1'
    assert vf.read_text().strip() == '1.5.00.1'


def test_restart_sin_update_no_bumpea(monkeypatch, tmp_path):
    # Reinicio sin código nuevo (p. ej. para liberar memoria): la versión no se mueve.
    llamado, vf = _preparar_restart(monkeypatch, tmp_path, estado=(False, '', []))
    client = _client()
    d = client.post('/api/system/restart').json()
    assert d['ok'] is True and d['version'] == '1.5.0'
    assert vf.read_text().strip() == '1.5.0'
    assert llamado['reinicios'] == 1


def test_restart_canary_roto_no_reinicia(monkeypatch, tmp_path):
    # El gate que protege el botón Actualizar: si el código nuevo no arranca,
    # NO se reinicia, NO se bumpea, y el error viaja al frontend.
    llamado, vf = _preparar_restart(monkeypatch, tmp_path,
                                    canary=(False, 'SyntaxError: invalid syntax'))
    client = _client()
    r = client.post('/api/system/restart')
    assert r.status_code == 409
    d = r.json()
    assert d['ok'] is False
    assert 'SyntaxError' in d['detalle']
    assert vf.read_text().strip() == '1.5.0'
    assert llamado['reinicios'] == 0


# ─── /ready: gate del reload post-update ─────────────────────────────────────
# El frontend NO recarga apenas el server acepta conexiones sino cuando terminó
# de reconciliar las sesiones tmux (reconcile_listo). Recargar antes = la página
# fresca re-attachea las N terminales contra un tmux todavía arrancando → seeds
# vacíos/tardíos → cards negras + scroll muerto (el bug del update).

def test_server_listo_refleja_reconcile():
    from plotspace.routers import terminals
    ev = terminals.reconcile_listo
    prev = ev.is_set()
    try:
        ev.clear()
        assert system._server_listo() is False
        ev.set()
        assert system._server_listo() is True
    finally:
        (ev.set if prev else ev.clear)()


def test_server_listo_fail_open(monkeypatch):
    # Si el semáforo no se puede leer (import/atributo roto), fail-open = True:
    # mejor recargar que trabar la página esperando algo inaccesible.
    from plotspace.routers import terminals

    class _Boom:
        def is_set(self):
            raise RuntimeError('boom')

    monkeypatch.setattr(terminals, 'reconcile_listo', _Boom())
    assert system._server_listo() is True


def test_endpoint_ready_abierto_y_refleja_reconcile():
    from plotspace.routers import terminals
    client = _client()
    ev = terminals.reconcile_listo
    prev = ev.is_set()
    try:
        ev.clear()
        # Abierto (RUTAS_ABIERTAS, como /health): sin cookie NO da 401.
        r = client.get('/api/system/ready')
        assert r.status_code == 200, r.text
        assert r.json() == {'ready': False}
        ev.set()
        assert client.get('/api/system/ready').json() == {'ready': True}
    finally:
        (ev.set if prev else ev.clear)()


if __name__ == '__main__':
    import traceback
    fallos = 0
    # Solo los puros (0 args) corren como script; los de fixtures usan pytest.
    for nombre, fn in sorted(globals().items()):
        if (nombre.startswith('test_') and callable(fn)
                and getattr(fn, '__code__', None) and fn.__code__.co_argcount == 0):
            try:
                fn(); print(f'ok  {nombre}')
            except Exception:
                fallos += 1; print(f'FAIL {nombre}'); traceback.print_exc()
    sys.exit(1 if fallos else 0)
