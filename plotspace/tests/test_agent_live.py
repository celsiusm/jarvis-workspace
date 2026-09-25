# plotspace/tests/test_agent_live.py
"""Tests de la lógica pura de Agents Live (plotspace/core/agent_live.py).
Nada de tmux, red ni DB — espejo de test_dev_detect.py / test_agent_watch.py."""
from plotspace.core.agent_live import operaciones_nuevas


def extraer_operaciones(texto):
    """Captura aislada (sin historia previa) = todas las ops del texto."""
    return operaciones_nuevas(texto, {})


# ─── extraer_operaciones ───────────────────────────────────────────────────────

def test_update_claude_code():
    pane = '● Update(frontend/shell/workspace.html)\n'
    assert extraer_operaciones(pane) == [('write', 'frontend/shell/workspace.html')]


def test_write_y_read():
    pane = ('● Write(plotspace/core/agent_live.py)\n'
            '● Read(plotspace/main.py)\n')
    assert extraer_operaciones(pane) == [
        ('write', 'plotspace/core/agent_live.py'),
        ('read', 'plotspace/main.py'),
    ]


def test_create_y_edit_son_write():
    pane = '● Create(a.py)\n● Edit(b.py)\n'
    assert [op for op, _ in extraer_operaciones(pane)] == ['write', 'write']


def test_con_ansi():
    pane = '\x1b[32m●\x1b[0m Update(\x1b[1mfrontend/shared/ui.js\x1b[0m)\n'
    assert extraer_operaciones(pane) == [('write', 'frontend/shared/ui.js')]


def test_bash_no_es_operacion():
    # Bash(...) toca archivos pero no sabemos cuáles: no es una op de archivo.
    pane = '● Bash(node --check frontend/shell/workspace.js && echo OK)\n'
    assert extraer_operaciones(pane) == []


def test_linea_truncada_sin_parentesis_no_matchea():
    # El pane corta líneas largas: sin ')' no hay match (preferimos perder
    # una op a registrar un path mutilado).
    pane = '● Bash(git add CLAUDE.md .jarvis/memory/aura-notificacion\n'
    assert extraer_operaciones(pane) == []


def test_instruccion_en_prosa_no_matchea():
    # "el agente debe usar Update(archivo)" citado en texto de tarea:
    # exigimos que la línea ARRANQUE con el bullet de la CLI.
    pane = 'Cuando edites usá Update(no-existe.py) como herramienta\n'
    assert extraer_operaciones(pane) == []


def test_path_relativo_se_limpia():
    pane = '● Update(./src/app.js)\n'
    assert extraer_operaciones(pane) == [('write', 'src/app.js')]


def test_vacio():
    assert extraer_operaciones('') == []
    assert extraer_operaciones(None) == []


# ─── operaciones_nuevas (dedup entre capturas solapadas) ──────────────────────

def test_dedup_misma_linea_dos_capturas():
    vistos = {}
    pane = '● Update(a.py)\n'
    assert operaciones_nuevas(pane, vistos) == [('write', 'a.py')]
    # misma captura otra vez (ventanas solapadas) → nada nuevo
    assert operaciones_nuevas(pane, vistos) == []


def test_dedup_linea_nueva_si_pasa():
    vistos = {}
    operaciones_nuevas('● Update(a.py)\n', vistos)
    assert operaciones_nuevas('● Update(a.py)\n● Write(b.py)\n', vistos) == [('write', 'b.py')]


def test_reedicion_misma_linea_cuenta():
    # Una re-edición REAL del mismo archivo imprime una línea idéntica:
    # el dedup por presencia la perdía → la propiedad del dueño expiraba
    # aunque siguiera editando, y las escrituras repetidas de un intruso
    # pasaban sin alerta.
    vistos = {}
    assert operaciones_nuevas('● Update(a.py)\n', vistos) == [('write', 'a.py')]
    assert operaciones_nuevas('● Update(a.py)\n● Update(a.py)\n', vistos) == [('write', 'a.py')]
    # ventana solapada de esas dos mismas líneas → nada nuevo
    assert operaciones_nuevas('● Update(a.py)\n● Update(a.py)\n', vistos) == []


def test_reedicion_tras_scroll_cuenta():
    # La línea scrolleó fuera del pane y el agente re-edita el archivo
    # después: la nueva ocurrencia debe contarse.
    vistos = {}
    operaciones_nuevas('● Update(a.py)\n', vistos)
    assert operaciones_nuevas('● Write(b.py)\n', vistos) == [('write', 'b.py')]
    assert operaciones_nuevas('● Write(b.py)\n● Update(a.py)\n', vistos) == [('write', 'a.py')]


def test_vistos_refleja_solo_la_ultima_captura():
    # El estado de dedup queda acotado por la ventana del pane: no crece
    # sin límite con la historia (antes: FIFO de 600 hashes sin expirar).
    vistos = {}
    for i in range(700):
        operaciones_nuevas(f'● Update(f{i}.py)\n', vistos)
    assert len(vistos) == 1


from plotspace.core.agent_live import (
    PROPIEDAD_TTL_S,
    ALERTA_THROTTLE_S,
    registrar_escritura,
    dueno_vigente,
    debe_alertar,
)


# ─── propiedad ────────────────────────────────────────────────────────────────

def test_primer_escritor_es_dueno():
    duenos = {}
    res, d = registrar_escritura(duenos, (1, 'a.py'), 10, 'Backend', ahora=100.0)
    assert res == 'nueva'
    assert d['tid'] == 10 and d['nombre'] == 'Backend'


def test_dueno_reescribe_renueva():
    duenos = {}
    registrar_escritura(duenos, (1, 'a.py'), 10, 'Backend', ahora=100.0)
    res, d = registrar_escritura(duenos, (1, 'a.py'), 10, 'Backend', ahora=200.0)
    assert res == 'propia'
    assert d['ultima'] == 200.0


def test_otro_agente_es_conflicto():
    duenos = {}
    registrar_escritura(duenos, (1, 'a.py'), 10, 'Backend', ahora=100.0)
    res, d = registrar_escritura(duenos, (1, 'a.py'), 20, 'Frontend', ahora=150.0)
    assert res == 'conflicto'
    assert d['tid'] == 10  # devuelve al dueño vigente


def test_propiedad_expira_por_ttl():
    duenos = {}
    registrar_escritura(duenos, (1, 'a.py'), 10, 'Backend', ahora=100.0)
    # pasaron >10 min sin que Backend lo toque → Frontend hereda
    res, d = registrar_escritura(duenos, (1, 'a.py'), 20, 'Frontend',
                                 ahora=100.0 + PROPIEDAD_TTL_S + 1)
    assert res == 'nueva'
    assert d['tid'] == 20


def test_dueno_vigente():
    d = {'tid': 10, 'nombre': 'B', 'desde': 100.0, 'ultima': 100.0}
    assert dueno_vigente(d, ahora=100.0 + PROPIEDAD_TTL_S - 1)
    assert not dueno_vigente(d, ahora=100.0 + PROPIEDAD_TTL_S + 1)
    assert not dueno_vigente(None, ahora=0.0)


def test_archivos_distintos_no_chocan():
    duenos = {}
    registrar_escritura(duenos, (1, 'a.py'), 10, 'Backend', ahora=100.0)
    res, _ = registrar_escritura(duenos, (1, 'b.py'), 20, 'Frontend', ahora=100.0)
    assert res == 'nueva'


# ─── throttle de alertas ──────────────────────────────────────────────────────

def test_throttle_primera_alerta_pasa():
    alertas = {}
    assert debe_alertar(alertas, (1, 'a.py', frozenset({10, 20})), ahora=100.0)


def test_throttle_segunda_no():
    alertas = {}
    clave = (1, 'a.py', frozenset({10, 20}))
    debe_alertar(alertas, clave, ahora=100.0)
    assert not debe_alertar(alertas, clave, ahora=100.0 + ALERTA_THROTTLE_S - 1)
    assert debe_alertar(alertas, clave, ahora=100.0 + ALERTA_THROTTLE_S + 1)


from plotspace.core.agent_live import (
    parsear_permiso,
    aplicar_permiso,
)


# ─── parsear_permiso ──────────────────────────────────────────────────────────

def test_permiso_basico():
    p = parsear_permiso('PERMISO frontend/shared/ui.js — necesito agregar un helper')
    assert p == {'tipo': 'permiso', 'archivo': 'frontend/shared/ui.js',
                 'detalle': 'necesito agregar un helper'}


def test_ok_con_detalle():
    p = parsear_permiso('OK ui.js — yo toco solo cliLogo(), no toques eso')
    assert p['tipo'] == 'ok' and p['archivo'] == 'ui.js'
    assert 'cliLogo' in p['detalle']


def test_no():
    p = parsear_permiso('NO ui.js — refactor entero, esperá mi TASK_DONE')
    assert p['tipo'] == 'no'


def test_case_insensitive_y_sin_detalle():
    assert parsear_permiso('permiso a.py')['tipo'] == 'permiso'
    assert parsear_permiso('PERMISO a.py')['detalle'] == ''


def test_separadores_variados():
    assert parsear_permiso('OK a.py: dale')['detalle'] == 'dale'
    assert parsear_permiso('OK a.py - dale')['detalle'] == 'dale'


def test_mensaje_comun_no_es_permiso():
    assert parsear_permiso('el endpoint quedó en /api/v2') is None
    assert parsear_permiso('OK, lo miro ahora') is None  # sin archivo
    assert parsear_permiso('') is None
    assert parsear_permiso(None) is None


# ─── aplicar_permiso ──────────────────────────────────────────────────────────

def test_pedido_crea_pendiente():
    permisos = []
    p = aplicar_permiso(permisos, 'Frontend', 'Backend',
                        {'tipo': 'permiso', 'archivo': 'ui.js', 'detalle': 'helper'},
                        ahora=100.0)
    assert p['estado'] == 'pendiente' and p['pide'] == 'Frontend' and p['dueno'] == 'Backend'
    assert permisos == [p]


def test_ok_resuelve_el_pendiente():
    permisos = []
    aplicar_permiso(permisos, 'Frontend', 'Backend',
                    {'tipo': 'permiso', 'archivo': 'frontend/shared/ui.js', 'detalle': ''},
                    ahora=100.0)
    # el dueño responde con el basename: matchea por sufijo
    p = aplicar_permiso(permisos, 'Backend', 'Frontend',
                        {'tipo': 'ok', 'archivo': 'ui.js', 'detalle': 'solo no toques cliLogo'},
                        ahora=150.0)
    assert p['estado'] == 'ok'
    assert permisos[0]['estado'] == 'ok'
    assert 'cliLogo' in permisos[0]['respuesta']


def test_no_resuelve_como_denegado():
    permisos = []
    aplicar_permiso(permisos, 'F', 'B', {'tipo': 'permiso', 'archivo': 'a.py', 'detalle': ''}, ahora=1.0)
    p = aplicar_permiso(permisos, 'B', 'F', {'tipo': 'no', 'archivo': 'a.py', 'detalle': 'esperá'}, ahora=2.0)
    assert p['estado'] == 'no'


def test_respuesta_sin_pedido_no_rompe():
    permisos = []
    assert aplicar_permiso(permisos, 'B', 'F', {'tipo': 'ok', 'archivo': 'x.py', 'detalle': ''}, ahora=1.0) is None
    assert permisos == []


from plotspace.core.agent_live import (
    armar_snapshot,
    generar_live_md,
    asegurar_live,
    PROTOCOLO_MARKER_START,
)


def _estado_ejemplo():
    archivos = {
        10: {'plotspace/core/x.py': {'reads': 0, 'writes': 3, 'primera': 50.0, 'ultima': 90.0}},
        20: {'plotspace/core/x.py': {'reads': 2, 'writes': 0, 'primera': 60.0, 'ultima': 80.0}},
    }
    duenos = {(1, 'plotspace/core/x.py'): {'tid': 10, 'nombre': 'Backend', 'desde': 50.0, 'ultima': 90.0}}
    permisos = [{'archivo': 'plotspace/core/x.py', 'pide': 'Frontend', 'dueno': 'Backend',
                 'detalle': 'helper', 'respuesta': '', 'estado': 'pendiente',
                 'ts': 70.0}]
    rows = [
        {'tid': 10, 'tnombre': 'Backend', 'tipo_ia': 'claude'},
        {'tid': 20, 'tnombre': 'Frontend', 'tipo_ia': 'codex'},
    ]
    return archivos, duenos, permisos, rows


# ─── armar_snapshot ───────────────────────────────────────────────────────────

def test_snapshot_estructura():
    archivos, duenos, permisos, rows = _estado_ejemplo()
    snap = armar_snapshot(1, rows, archivos, duenos, permisos,
                          fases={10: 'trabajando', 20: 'idle'},
                          actividad=[{'hora': '12:00:00', 'texto': 'x', 'clase': 'op'}],
                          ahora=100.0)
    assert [a['nombre'] for a in snap['agentes']] == ['Backend', 'Frontend']
    backend = snap['agentes'][0]
    assert backend['estado'] == 'trabajando'
    assert backend['archivos'][0] == {
        'path': 'plotspace/core/x.py', 'reads': 0, 'writes': 3,
        'hace_s': 10, 'dueno': True,
    }
    # Frontend solo leyó: no es dueño
    assert snap['agentes'][1]['archivos'][0]['dueno'] is False
    assert snap['permisos'][0]['estado'] == 'pendiente'
    assert snap['permisos'][0]['hace_s'] == 30
    assert len(snap['actividad']) == 1


def test_snapshot_dueno_expirado_no_marca():
    archivos, duenos, permisos, rows = _estado_ejemplo()
    snap = armar_snapshot(1, rows, archivos, duenos, [], fases={},
                          actividad=[], ahora=90.0 + PROPIEDAD_TTL_S + 1)
    assert snap['agentes'][0]['archivos'][0]['dueno'] is False


# ─── generar_live_md ──────────────────────────────────────────────────────────

def test_live_md_contenido():
    archivos, duenos, permisos, rows = _estado_ejemplo()
    snap = armar_snapshot(1, rows, archivos, duenos, permisos,
                          fases={10: 'trabajando', 20: 'idle'}, actividad=[], ahora=100.0)
    md = generar_live_md(snap, '2026-06-07 14:32:10')
    assert 'NO editar' in md
    assert '## Backend (claude, terminal 10) — 🟢 trabajando' in md
    assert '`plotspace/core/x.py` — write ×3 (hace 10s) 🔒 dueño' in md
    assert '## Permisos' in md
    assert '⏳ Frontend pidió PERMISO sobre `plotspace/core/x.py` (dueño: Backend)' in md
    # los reads solos NO se publican
    assert md.count('plotspace/core/x.py') == 2  # 1 en Backend + 1 en permisos


def test_live_md_sin_actividad():
    snap = {'agentes': [], 'permisos': [], 'actividad': []}
    md = generar_live_md(snap, '2026-06-07 14:00:00')
    assert 'Sin agentes activos' in md


# ─── Muerte visible: LIVE.md es de donde lee el guard de propiedad ───────────
# Mientras el snapshot colapsaba todo a trabajando|idle, un agente cuyo CLI se
# había cerrado seguía figurando idle: le respetaban el territorio y el guard
# bloqueaba commits en su nombre. Un muerto tiene que VERSE muerto.

def test_el_snapshot_no_disfraza_de_idle_a_un_caido():
    archivos, duenos, permisos, rows = _estado_ejemplo()
    snap = armar_snapshot(1, rows, archivos, duenos, [],
                          fases={10: 'caido', 20: 'sin_sesion'},
                          actividad=[], ahora=100.0)
    assert [a['estado'] for a in snap['agentes']] == ['caido', 'sin_sesion']


def test_live_md_marca_al_caido_para_que_el_guard_lo_vea():
    archivos, duenos, permisos, rows = _estado_ejemplo()
    snap = armar_snapshot(1, rows, archivos, duenos, [], fases={10: 'caido'},
                          actividad=[], ahora=100.0)
    md = generar_live_md(snap, '2026-06-07 14:32:10')
    assert '💀' in md and 'caído' in md
    assert '🟢' not in md.split('\n')[2]


def test_live_md_de_una_terminal_sin_sesion():
    archivos, duenos, permisos, rows = _estado_ejemplo()
    snap = armar_snapshot(1, rows, archivos, duenos, [], fases={10: 'sin_sesion'},
                          actividad=[], ahora=100.0)
    assert '💀' in generar_live_md(snap, '2026-06-07 14:32:10')


def test_un_estado_desconocido_cae_a_idle_no_a_basura():
    """Falla abierta: si algún día aparece una fase nueva, un agente vivo no debe
    quedar renderizado como un estado inventado."""
    archivos, duenos, permisos, rows = _estado_ejemplo()
    snap = armar_snapshot(1, rows, archivos, duenos, [], fases={10: 'vaya-a-saber'},
                          actividad=[], ahora=100.0)
    assert snap['agentes'][0]['estado'] == 'idle'


def test_protocolo_explica_que_msg_no_despierta():
    """El cambio de comportamiento (solo `ask`/HANDOFF despiertan) TIENE que estar
    en el protocolo que leen los agentes: si no, mandan un `jv msg` urgente y se
    extrañan de que el otro no reaccione. Y avisa que `@jarvis` no es destinatario
    (36 de los 54 mensajes huérfanos medidos iban ahí)."""
    from plotspace.core.agent_live import PROTOCOLO
    assert 'does NOT wake' in PROTOCOLO
    assert 'jv ask' in PROTOCOLO and 'HANDOFF' in PROTOCOLO
    assert '@jarvis' in PROTOCOLO


def test_protocolo_dice_que_hacer_con_un_muerto_y_su_herencia():
    """Sin esto, el agente ve el 💀 y no sabe si esperarlo; y ve la herencia y no
    sabe que es suya para commitear. La marca sin instrucción no sirve de nada."""
    from plotspace.core.agent_live import PROTOCOLO
    assert '💀' in PROTOCOLO and 'is not coming back' in PROTOCOLO
    assert 'Inheritance' in PROTOCOLO


def test_protocolo_fija_que_se_commitea_y_que_no():
    """El pedido explícito del usuario: commitear SIEMPRE el trabajo real antes
    de cerrar, y NUNCA las pruebas/mockups/artefactos."""
    from plotspace.core.agent_live import PROTOCOLO
    assert 'Commit before closing' in PROTOCOLO
    assert 'mockups' in PROTOCOLO and 'gitignore' in PROTOCOLO.lower()


# ─── asegurar_live ────────────────────────────────────────────────────────────

def test_asegurar_live_inyecta_protocolo(tmp_path):
    (tmp_path / 'CLAUDE.md').write_text('# Mi proyecto\n', encoding='utf-8')
    asegurar_live(str(tmp_path))
    contenido = (tmp_path / 'CLAUDE.md').read_text(encoding='utf-8')
    assert PROTOCOLO_MARKER_START in contenido
    assert 'LIVE.md' in contenido
    # idempotente
    asegurar_live(str(tmp_path))
    contenido2 = (tmp_path / 'CLAUDE.md').read_text(encoding='utf-8')
    assert contenido2.count(PROTOCOLO_MARKER_START) == 1
    # gitignore del proyecto gana la línea
    assert '.jarvis/LIVE.md' in (tmp_path / '.gitignore').read_text(encoding='utf-8')


def test_asegurar_live_gitignore_idempotente(tmp_path):
    (tmp_path / '.gitignore').write_text('node_modules/\n.jarvis/LIVE.md\n', encoding='utf-8')
    asegurar_live(str(tmp_path))
    gi = (tmp_path / '.gitignore').read_text(encoding='utf-8')
    assert gi.count('.jarvis/LIVE.md') == 1


def test_ok_resuelve_solo_al_solicitante_correcto():
    # Dos pendientes sobre el mismo archivo de DISTINTOS agentes: el OK
    # resuelve solo el del destinatario de la respuesta, el otro sigue.
    permisos = []
    aplicar_permiso(permisos, 'Frontend', 'Backend',
                    {'tipo': 'permiso', 'archivo': 'ui.js', 'detalle': ''}, ahora=1.0)
    aplicar_permiso(permisos, 'Docs', 'Backend',
                    {'tipo': 'permiso', 'archivo': 'ui.js', 'detalle': ''}, ahora=2.0)
    p = aplicar_permiso(permisos, 'Backend', 'Frontend',
                        {'tipo': 'ok', 'archivo': 'ui.js', 'detalle': ''}, ahora=3.0)
    assert p['pide'] == 'Frontend' and p['estado'] == 'ok'
    assert permisos[1]['pide'] == 'Docs' and permisos[1]['estado'] == 'pendiente'


def test_escribir_live_md_atomico(tmp_path):
    from plotspace.core.agent_live import escribir_live_md, LIVE_NOMBRE
    import os
    escribir_live_md(str(tmp_path), 'contenido uno\n')
    destino = tmp_path / LIVE_NOMBRE
    assert destino.read_text(encoding='utf-8') == 'contenido uno\n'
    # reescritura: pisa el contenido y no deja tmps colgados
    escribir_live_md(str(tmp_path), 'contenido dos\n')
    assert destino.read_text(encoding='utf-8') == 'contenido dos\n'
    assert [f for f in os.listdir(destino.parent) if f.endswith('.tmp')] == []


def test_match_archivo_no_pela_dotfiles():
    # lstrip('./') pelaba '.gitignore' a 'gitignore' (set de chars, no prefijo)
    from plotspace.core.agent_live import _match_archivo
    assert not _match_archivo('.gitignore', 'gitignore')
    assert _match_archivo('./src/app.js', 'src/app.js')
    assert _match_archivo('.env', 'plotspace/.env')


# ─── Jarvis pasivo: LIVE nunca escribe en la terminal de un agente ────────────

def test_modulo_no_inyecta_a_terminales():
    # Contrato 2026-06-07: el agente se entera por .jarvis/LIVE.md (protocolo
    # del CLAUDE.md), Jarvis NO le inyecta mensajes. El único que entrega a
    # terminales es el mailbox agente-a-agente.
    import inspect
    from plotspace.core import agent_live
    fuente = inspect.getsource(agent_live)
    assert 'send_to_agent' not in fuente
    assert 'RECORDATORIO' not in fuente  # el recordatorio de 5 min murió con esto


def test_permiso_sin_dueno_trackeado_espera_su_timeout():
    # Caso real (MAILBOX 2026-06-07): el dueño corre FUERA de un pane tmux,
    # o el @Para no es el nombre exacto de su terminal → no hay registro en
    # _duenos que matchee. Antes: dueno_vigente(None) → False → el pendiente
    # pasaba a 'expirado' en el primer ciclo (≤2s) y el OK posterior del
    # dueño no encontraba pendiente que resolver.
    from plotspace.core import agent_live
    permisos_bak, duenos_bak = agent_live._permisos, agent_live._duenos
    try:
        agent_live._permisos = {1: [{'archivo': 'x.py', 'pide': 'Frontend',
                                     'dueno': 'ClaudeCode', 'detalle': '', 'respuesta': '',
                                     'estado': 'pendiente', 'ts': 70.0}]}
        agent_live._duenos = {}
        # recién pedido, sin dueño trackeado → sigue pendiente
        assert agent_live._revisar_permisos(ahora=72.0) == set()
        assert agent_live._permisos[1][0]['estado'] == 'pendiente'
        # nadie respondió en todo el TTL → expira por timeout propio
        assert agent_live._revisar_permisos(ahora=70.0 + PROPIEDAD_TTL_S + 1) == {1}
        assert agent_live._permisos[1][0]['estado'] == 'expirado'
    finally:
        agent_live._permisos, agent_live._duenos = permisos_bak, duenos_bak


def test_relativo_al_proyecto():
    # Una CLI puede imprimir el path absoluto y otra el relativo: si no se
    # normaliza, el mismo archivo tiene DOS claves de propiedad y el
    # conflicto entre sus dueños no se detecta.
    from plotspace.core.agent_live import relativo_al_proyecto
    assert relativo_al_proyecto('/home/u/proy/src/a.js', '/home/u/proy') == 'src/a.js'
    assert relativo_al_proyecto('/home/u/proy/src/a.js', '/home/u/proy/') == 'src/a.js'
    assert relativo_al_proyecto('src/a.js', '/home/u/proy') == 'src/a.js'
    # fuera del proyecto: queda como está (no inventar relativos falsos)
    assert relativo_al_proyecto('/otro/lado/x.py', '/home/u/proy') == '/otro/lado/x.py'
    # prefijo de nombre parecido no es subcarpeta
    assert relativo_al_proyecto('/home/u/proyecto2/a.js', '/home/u/proy') == '/home/u/proyecto2/a.js'


def test_permiso_pendiente_con_dueno_vencido_se_autoconcede():
    # Contrato v2 (2026-07-19): el pendiente cuyo dueño perdió la propiedad ya
    # NO muere 'expirado' (dejaba colgado al que pedía) — se auto-concede: una
    # propiedad vencida no puede seguir bloqueando a nadie.
    from plotspace.core import agent_live
    permisos_bak, duenos_bak = agent_live._permisos, agent_live._duenos
    try:
        agent_live._permisos = {1: [{'archivo': 'x.py', 'pide': 'Frontend',
                                     'dueno': 'Backend', 'detalle': '', 'respuesta': '',
                                     'estado': 'pendiente', 'ts': 70.0}]}
        agent_live._duenos = {(1, 'x.py'): {'tid': 10, 'nombre': 'Backend',
                                            'desde': 50.0, 'ultima': 90.0}}
        # dueño vigente → no pasa nada
        assert agent_live._revisar_permisos(ahora=100.0) == set()
        assert agent_live._permisos[1][0]['estado'] == 'pendiente'
        # propiedad vencida → auto-OK con constancia
        assert agent_live._revisar_permisos(ahora=90.0 + PROPIEDAD_TTL_S + 1) == {1}
        assert agent_live._permisos[1][0]['estado'] == 'ok'
        assert 'auto-OK' in agent_live._permisos[1][0]['respuesta']
    finally:
        agent_live._permisos, agent_live._duenos = permisos_bak, duenos_bak


# ─── cambios_de_roster (detección instantánea de altas/bajas/fase) ────────────
# Un agente recién creado (o que pasó idle↔trabajando) debe disparar un
# live_update aunque NO haya tocado ningún archivo. Antes: sin file-op no había
# cambios → el poller no emitía → el agente no aparecía en Live hasta tocar algo.

def test_roster_terminal_nueva_marca_su_proyecto():
    from plotspace.core.agent_live import cambios_de_roster
    prev   = {1: {10}}
    actual = {1: {10, 11}}                 # nació la terminal 11 en el proyecto 1
    tid_pid = {10: 1, 11: 1}
    assert cambios_de_roster(prev, actual, {}, {}, tid_pid) == {1}


def test_roster_terminal_eliminada_marca_su_proyecto():
    from plotspace.core.agent_live import cambios_de_roster
    prev   = {1: {10, 11}}
    actual = {1: {10}}                      # murió la 11
    tid_pid = {10: 1}
    assert cambios_de_roster(prev, actual, {}, {}, tid_pid) == {1}


def test_roster_sin_cambios_no_marca_nada():
    from plotspace.core.agent_live import cambios_de_roster
    prev   = {1: {10, 11}, 2: {20}}
    actual = {1: {10, 11}, 2: {20}}
    tid_pid = {10: 1, 11: 1, 20: 2}
    assert cambios_de_roster(prev, actual, {}, {}, tid_pid) == set()


def test_roster_solo_marca_el_proyecto_que_cambio():
    from plotspace.core.agent_live import cambios_de_roster
    prev   = {1: {10}, 2: {20}}
    actual = {1: {10, 11}, 2: {20}}        # solo el proyecto 1 sumó terminal
    tid_pid = {10: 1, 11: 1, 20: 2}
    assert cambios_de_roster(prev, actual, {}, {}, tid_pid) == {1}


def test_fase_cambio_marca_su_proyecto():
    from plotspace.core.agent_live import cambios_de_roster
    # mismo roster, pero la terminal 10 pasó idle → trabajando
    roster = {1: {10}}
    tid_pid = {10: 1}
    assert cambios_de_roster(roster, roster, {10: 'idle'}, {10: 'trabajando'}, tid_pid) == {1}


def test_fase_estable_no_marca():
    from plotspace.core.agent_live import cambios_de_roster
    roster = {1: {10}}
    tid_pid = {10: 1}
    assert cambios_de_roster(roster, roster,
                             {10: 'trabajando'}, {10: 'trabajando'}, tid_pid) == set()


def test_fase_default_idle_para_tid_desconocido():
    from plotspace.core.agent_live import cambios_de_roster
    # terminal nueva SIN entrada de fase todavía (agent_watch no la vio):
    # default 'idle' en ambos lados → la fase no dispara (pero el roster sí).
    roster = {1: {10}}
    tid_pid = {10: 1}
    assert cambios_de_roster(roster, roster, {}, {}, tid_pid) == set()


# ─── _on_evento_broadcast (push instantáneo ante cambios de fase) ─────────────

def test_listener_ignora_eventos_que_no_son_de_fase():
    # No debe reaccionar a NUESTROS propios live_update (evita loop) ni a otros.
    import asyncio
    from plotspace.core import agent_live
    llamados = []
    pr_bak, pid_bak = agent_live.publicar_roster, agent_live._pid_de_terminal
    try:
        async def _fake_pr(pid): llamados.append(pid)
        agent_live.publicar_roster = _fake_pr
        agent_live._pid_de_terminal = lambda tid: 7
        asyncio.run(agent_live._on_evento_broadcast({'type': 'live_update', 'snapshot': {}}))
        asyncio.run(agent_live._on_evento_broadcast({'type': 'dev_server_detectado'}))
        assert llamados == []
    finally:
        agent_live.publicar_roster, agent_live._pid_de_terminal = pr_bak, pid_bak


def test_listener_empuja_snapshot_ante_cambio_de_fase():
    import asyncio
    from plotspace.core import agent_live
    llamados = []
    pr_bak, pid_bak = agent_live.publicar_roster, agent_live._pid_de_terminal
    try:
        async def _fake_pr(pid): llamados.append(pid)
        agent_live.publicar_roster = _fake_pr
        agent_live._pid_de_terminal = lambda tid: 7 if tid == 42 else None
        for tipo in ('agente_trabajando', 'agente_espera', 'agente_termino'):
            asyncio.run(agent_live._on_evento_broadcast({'type': tipo, 'terminal_id': 42}))
        assert llamados == [7, 7, 7]        # el pid mapeado, una vez por evento
    finally:
        agent_live.publicar_roster, agent_live._pid_de_terminal = pr_bak, pid_bak


def test_listener_sin_terminal_id_no_empuja():
    import asyncio
    from plotspace.core import agent_live
    llamados = []
    pr_bak = agent_live.publicar_roster
    try:
        async def _fake_pr(pid): llamados.append(pid)
        agent_live.publicar_roster = _fake_pr
        asyncio.run(agent_live._on_evento_broadcast({'type': 'agente_trabajando'}))
        assert llamados == []
    finally:
        agent_live.publicar_roster = pr_bak


# ─── Mailbox v2: propiedad vencida no bloquea + permisos trabados escalan ────

def test_permiso_dueno_vencido_es_auto_ok():
    # una propiedad VENCIDA (10 min sin tocar el archivo) no puede seguir
    # frenando al que pidió permiso: auto-OK con constancia (el guard lee el
    # '→ OK' del LIVE.md y desbloquea su commit)
    from plotspace.core import agent_live
    permisos_bak, duenos_bak = agent_live._permisos, agent_live._duenos
    esc_bak = list(agent_live._escalaciones)
    try:
        agent_live._escalaciones.clear()
        agent_live._permisos = {1: [{'archivo': 'x.py', 'pide': 'Frontend',
                                     'dueno': 'Backend', 'detalle': '', 'respuesta': '',
                                     'estado': 'pendiente', 'ts': 70.0}]}
        agent_live._duenos = {(1, 'x.py'): {'tid': 9, 'nombre': 'Backend', 'ultima': 10.0}}
        cambios = agent_live._revisar_permisos(ahora=700.0)   # 690s sin tocar > TTL
        assert cambios == {1}
        p = agent_live._permisos[1][0]
        assert p['estado'] == 'ok'
        assert 'auto-OK' in p['respuesta']
        assert agent_live._escalaciones == [], 'auto-OK no molesta al usuario'
    finally:
        agent_live._permisos, agent_live._duenos = permisos_bak, duenos_bak
        agent_live._escalaciones.clear()
        agent_live._escalaciones.extend(esc_bak)


def test_permiso_sin_respuesta_escala_al_usuario():
    from plotspace.core import agent_live
    permisos_bak, duenos_bak = agent_live._permisos, agent_live._duenos
    esc_bak = list(agent_live._escalaciones)
    try:
        agent_live._escalaciones.clear()
        agent_live._permisos = {1: [{'archivo': 'y.py', 'pide': 'Frontend',
                                     'dueno': 'Fantasma', 'detalle': '', 'respuesta': '',
                                     'estado': 'pendiente', 'ts': 70.0}]}
        agent_live._duenos = {}      # dueño no trackeado → expira por timeout
        cambios = agent_live._revisar_permisos(ahora=70.0 + PROPIEDAD_TTL_S + 1)
        assert cambios == {1}
        assert agent_live._permisos[1][0]['estado'] == 'expirado'
        assert len(agent_live._escalaciones) == 1
        assert 'y.py' in agent_live._escalaciones[0]['texto']
        assert agent_live._escalaciones[0]['pid'] == 1
    finally:
        agent_live._permisos, agent_live._duenos = permisos_bak, duenos_bak
        agent_live._escalaciones.clear()
        agent_live._escalaciones.extend(esc_bak)


# ─── RESERVA: la cola se forma ANTES del choque (mailbox v2) ─────────────────

def test_parsear_reserva():
    from plotspace.core.agent_live import parsear_reserva
    r = parsear_reserva('RESERVA frontend/builder.js — voy a tocar el viewport')
    assert r == {'archivo': 'frontend/builder.js', 'detalle': 'voy a tocar el viewport'}
    assert parsear_reserva('RESERVA dale nomás') is None    # no parece un path
    assert parsear_reserva('PERMISO x.py — otra cosa') is None


def test_aplicar_reserva_ok_y_ocupada():
    from plotspace.core.agent_live import aplicar_reserva
    reservas, duenos = {}, {}
    res, quien = aplicar_reserva(reservas, duenos, 1, 'a.js', 8, 'Frontend', 100.0)
    assert res == 'ok' and (1, 'a.js') in reservas
    # otro agente sobre el mismo archivo, reserva vigente → ocupada
    res2, quien2 = aplicar_reserva(reservas, duenos, 1, 'a.js', 9, 'Backend', 200.0)
    assert res2 == 'ocupada' and quien2 == 'Frontend'
    # con dueño VIGENTE ajeno también se rechaza
    duenos[(1, 'b.js')] = {'tid': 9, 'nombre': 'Backend', 'ultima': 190.0}
    res3, quien3 = aplicar_reserva({}, duenos, 1, 'b.js', 8, 'Frontend', 200.0)
    assert res3 == 'ocupada' and quien3 == 'Backend'


def test_reserva_expira_por_ttl():
    from plotspace.core import agent_live
    bak = dict(agent_live._reservas)
    try:
        agent_live._reservas.clear()
        agent_live._reservas[(1, 'a.js')] = {'tid': 8, 'nombre': 'F', 'ts': 100.0}
        assert agent_live._revisar_reservas(100.0 + agent_live.RESERVA_TTL_S - 1) == set()
        assert agent_live._revisar_reservas(100.0 + agent_live.RESERVA_TTL_S + 1) == {1}
        assert agent_live._reservas == {}
    finally:
        agent_live._reservas.clear()
        agent_live._reservas.update(bak)


def test_live_md_renderea_reservas():
    from plotspace.core.agent_live import armar_snapshot, generar_live_md
    snap = armar_snapshot(1, [], {}, {}, [], {}, [], ahora=200.0,
                          reservas={(1, 'a.js'): {'tid': 8, 'nombre': 'Frontend', 'ts': 100.0},
                                    (2, 'otro.js'): {'tid': 9, 'nombre': 'X', 'ts': 100.0}})
    assert len(snap['reservas']) == 1, 'solo las del proyecto'
    md = generar_live_md(snap, '2026-07-19 03:00:00')
    assert '## Reservas' in md
    assert '🔖 `a.js` — Frontend' in md


def test_protocolo_corta_el_ping_pong_de_verificacion():
    """Medido en este proyecto (2026-08-02): 74 mensajes entre DOS agentes en
    un feature, la mayoría re-verificaciones del trabajo ajeno y acuses de
    cortesía — cada uno despierta un turno entero y muchos re-corren la suite
    completa sobre commits del otro. El protocolo tiene que ponerle tope."""
    from plotspace.core.agent_live import PROTOCOLO
    assert 'YOUR task' in PROTOCOLO
    assert 'acks' in PROTOCOLO.lower()
    assert '2 of your messages' in PROTOCOLO


def test_protocolo_prohibe_esperar_el_commit_ajeno():
    """El interlock real del MAILBOX: «¿Podés COMMITEAR ya? Avisame cuando
    commiteaste» — con la entrega idle promediando UNA HORA, eso es un agente
    parado. jv commit (hunks por provenance) lo resuelve sin esperar a nadie."""
    from plotspace.core.agent_live import PROTOCOLO
    assert 'never wait for someone' in PROTOCOLO.lower()
    assert 'jv commit' in PROTOCOLO
