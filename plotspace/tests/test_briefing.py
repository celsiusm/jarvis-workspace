# plotspace/tests/test_briefing.py
"""Briefing del enjambre: que saber del otro NO dependa de que el agente pregunte.

EL PROBLEMA QUE RESUELVE
`jv estado` tiene el dato fresco y completo, pero es PULL: el agente tiene que
acordarse de correrlo, y no se acuerda. La única entrega garantizada de contexto
(`bloque_pendientes_para_tarea`) solo corre cuando Jarvis entrega una tarea, y el trabajo real
de este repo es en terminales directas — ahí no había ningún momento en el que
el sistema garantizara que el agente sabe con quién está trabajando.

Acá el briefing se ARMA en el server y se ENTREGA sin iniciativa del agente
(UserPromptSubmit en los CLIs que lo tienen; piggyback en el resultado de su
primera herramienta en los que no).

DOS REGLAS QUE NO SE NEGOCIAN
  · Si no hay nada que decir (solo en el proyecto, sin mensajes, sin herencia),
    NO se inyecta NADA. Un briefing vacío en cada prompt es ruido que entrena al
    agente a ignorar el bloque entero.
  · Es CORTO y accionable. Viaja en cada prompt: si crece, se vuelve caro.
"""
from plotspace.core import briefing


def _estado(**kw):
    """Estado base tal como lo devuelve swarm_cli.estado()."""
    base = {
        'yo': 'Claude Code #4',
        'mis_archivos': [],
        'pares': [],
        'otros': {},
        'mensajes_sin_leer': 0,
        'mi_territorio': [],
        'territorio_ajeno': [],
        'herencia': [],
        'salud_provenance': {'muda': False},
    }
    base.update(kw)
    return base


# ─── Cuándo NO hablar ─────────────────────────────────────────────────────────

def test_solo_en_el_proyecto_y_sin_nada_pendiente_no_dice_nada():
    """El caso más común (un agente solo) no debe costar ni un token."""
    assert briefing.armar_briefing(_estado()) == ''


def test_solo_pero_con_mensajes_si_habla():
    txt = briefing.armar_briefing(_estado(mensajes_sin_leer=2))
    assert 'jv inbox' in txt


def test_solo_pero_con_herencia_si_habla():
    """Un muerto que dejó trabajo sin commitear importa aunque estés solo."""
    txt = briefing.armar_briefing(_estado(herencia=[
        {'nombre': 'Claude Code #2', 'archivos': ['plotspace/x.py']}]))
    assert 'Claude Code #2' in txt and 'plotspace/x.py' in txt


# ─── Qué dice cuando hay con quién chocar ─────────────────────────────────────

def test_nombra_a_los_pares_vivos_con_su_estado():
    txt = briefing.armar_briefing(_estado(pares=[
        {'nombre': 'Claude Code #1', 'tipo_ia': 'claude', 'estado': 'trabajando'},
        {'nombre': 'Codex #2', 'tipo_ia': 'codex', 'estado': 'idle'}]))
    assert 'Claude Code #1' in txt and 'Codex #2' in txt
    assert 'trabajando' in txt


def test_un_par_caido_se_marca_como_caido_no_como_idle():
    """Es el bug entero: un muerto que se ve idle hace que el otro le respete
    el territorio y le espere los commits para siempre."""
    txt = briefing.armar_briefing(_estado(pares=[
        {'nombre': 'Claude Code #1', 'tipo_ia': 'claude', 'estado': 'caido'}]))
    assert 'caído' in txt.lower()
    assert 'idle' not in txt.lower()


def test_dice_que_archivos_toca_cada_par():
    txt = briefing.armar_briefing(_estado(
        pares=[{'nombre': 'Claude Code #1', 'tipo_ia': 'claude', 'estado': 'trabajando'}],
        otros={'Claude Code #1': ['frontend/shared/ui.js']}))
    assert 'frontend/shared/ui.js' in txt


def test_el_territorio_ajeno_viaja_con_su_dueno():
    txt = briefing.armar_briefing(_estado(
        pares=[{'nombre': 'Claude Code #1', 'tipo_ia': 'claude', 'estado': 'idle'}],
        territorio_ajeno=[('Claude Code #1', 'aplicarIdioma')]))
    assert 'aplicarIdioma' in txt and 'Claude Code #1' in txt


def test_con_pares_recuerda_como_commitear_sin_llevarse_lo_ajeno():
    txt = briefing.armar_briefing(_estado(pares=[
        {'nombre': 'Claude Code #1', 'tipo_ia': 'claude', 'estado': 'idle'}]))
    assert 'jv commit' in txt


def test_avisa_si_la_provenance_esta_muda():
    """Si los hooks están caídos NADIE está protegido: hay que decirlo."""
    txt = briefing.armar_briefing(_estado(
        pares=[{'nombre': 'X', 'tipo_ia': 'claude', 'estado': 'idle'}],
        salud_provenance={'muda': True}))
    assert 'tracking' in txt.lower() or 'protegid' in txt.lower()


# ─── Tamaño: viaja en CADA prompt ─────────────────────────────────────────────

def test_el_briefing_se_mantiene_corto_aun_con_un_enjambre_grande():
    txt = briefing.armar_briefing(_estado(
        pares=[{'nombre': f'Agente #{i}', 'tipo_ia': 'claude', 'estado': 'trabajando'}
               for i in range(12)],
        otros={f'Agente #{i}': [f'src/mod{j}.py' for j in range(9)] for i in range(12)},
        territorio_ajeno=[(f'Agente #{i}', f'simbolo{i}') for i in range(30)],
        mensajes_sin_leer=5))
    assert len(txt) < 2000, f'briefing de {len(txt)} chars: demasiado caro por prompt'
    assert txt.count('\n') < 25


def test_marcadores_para_que_el_agente_sepa_que_es_contexto_de_jarvis():
    txt = briefing.armar_briefing(_estado(pares=[
        {'nombre': 'X', 'tipo_ia': 'claude', 'estado': 'idle'}]))
    assert txt.startswith('[Enjambre]')


# ─── Firma: evitar repetir lo idéntico en el canal de piggyback ───────────────

def test_la_firma_no_cambia_si_el_estado_no_cambio():
    e = _estado(pares=[{'nombre': 'X', 'tipo_ia': 'claude', 'estado': 'idle'}])
    assert briefing.firma(e) == briefing.firma(_estado(
        pares=[{'nombre': 'X', 'tipo_ia': 'claude', 'estado': 'idle'}]))


def test_la_firma_cambia_cuando_un_par_se_cae():
    vivo = _estado(pares=[{'nombre': 'X', 'tipo_ia': 'claude', 'estado': 'idle'}])
    caido = _estado(pares=[{'nombre': 'X', 'tipo_ia': 'claude', 'estado': 'caido'}])
    assert briefing.firma(vivo) != briefing.firma(caido)


def test_la_firma_cambia_con_mensajes_nuevos():
    assert briefing.firma(_estado()) != briefing.firma(_estado(mensajes_sin_leer=1))


def test_estado_vacio_no_rompe_la_firma():
    assert isinstance(briefing.firma({}), str)


# ─── Robustez: el briefing NUNCA puede romper el prompt de un agente ──────────

def test_estado_con_basura_no_explota():
    for basura in (None, {}, {'pares': None, 'otros': None},
                   {'pares': [{'nombre': None}], 'territorio_ajeno': [()]}):
        assert isinstance(briefing.armar_briefing(basura), str)


# ─── Freno del piggyback: corre una vez por EDICIÓN ──────────────────────────
# `/swarm/op` se llama en cada edición de cada agente, y armar el estado cuesta
# `git status` + `tmux list-panes` + tres barridos del libro de provenance. El
# enjambre no cambia 50 veces por minuto: no hay por qué recalcularlo 50 veces.

def test_el_piggyback_no_recalcula_en_cada_edicion(monkeypatch):
    llamadas = []

    def _estado_espia(tid):
        llamadas.append(tid)
        return _estado()
    from plotspace.core import swarm_cli
    monkeypatch.setattr(swarm_cli, 'estado', _estado_espia)
    briefing.reset()

    briefing.briefing_para(1, solo_si_cambio=True, ahora=1000.0)
    for i in range(20):                       # 20 ediciones seguidas
        briefing.briefing_para(1, solo_si_cambio=True, ahora=1000.0 + i * 0.5)
    assert len(llamadas) == 1, f'recalculó {len(llamadas)} veces'


def test_pasado_el_freno_el_piggyback_vuelve_a_mirar(monkeypatch):
    llamadas = []
    from plotspace.core import swarm_cli
    monkeypatch.setattr(swarm_cli, 'estado',
                        lambda tid: (llamadas.append(tid), _estado())[1])
    briefing.reset()
    briefing.briefing_para(1, solo_si_cambio=True, ahora=1000.0)
    briefing.briefing_para(1, solo_si_cambio=True,
                           ahora=1000.0 + briefing.THROTTLE_PIGGYBACK_S + 1)
    assert len(llamadas) == 2


def test_el_canal_bueno_nunca_se_frena(monkeypatch):
    """UserPromptSubmit = tarea nueva. Ahí el agente arranca sin contexto y
    SIEMPRE tiene que recibirlo, por seguido que sea."""
    llamadas = []
    from plotspace.core import swarm_cli
    monkeypatch.setattr(swarm_cli, 'estado',
                        lambda tid: (llamadas.append(tid), _estado(
                            pares=[{'nombre': 'X', 'tipo_ia': 'claude',
                                    'estado': 'idle'}]))[1])
    briefing.reset()
    for i in range(5):
        r = briefing.briefing_para(1, ahora=1000.0 + i)
        assert r['texto'], 'el canal bueno se quedó mudo'
    assert len(llamadas) == 5


def test_el_freno_es_por_terminal_no_global(monkeypatch):
    llamadas = []
    from plotspace.core import swarm_cli
    monkeypatch.setattr(swarm_cli, 'estado',
                        lambda tid: (llamadas.append(tid), _estado())[1])
    briefing.reset()
    briefing.briefing_para(1, solo_si_cambio=True, ahora=1000.0)
    briefing.briefing_para(2, solo_si_cambio=True, ahora=1000.0)
    assert llamadas == [1, 2]
