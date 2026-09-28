"""core/idioma_ui: el idioma de la interfaz que usan los textos que nacen en el
server (avisos del orquestador al chat, respuesta del LLM)."""
from plotspace.core import idioma_ui


def test_default_del_conftest_es_espanol_y_L_elige():
    assert idioma_ui.actual() == 'es'
    assert idioma_ui.L('hola', 'hi') == 'hola'


def test_fijar_normaliza_y_persiste(tmp_path):
    assert idioma_ui.fijar('EN-us') == 'en'
    assert idioma_ui.L('hola', 'hi') == 'hi'
    assert open(idioma_ui._archivo()).read() == 'en'
    assert idioma_ui.fijar('cualquiera') == 'en', 'desconocido → inglés (default de la UI)'
    assert idioma_ui.fijar('es') == 'es'
    # un proceso nuevo (sin caché) relee el archivo
    idioma_ui._actual = None
    assert idioma_ui.actual() == 'es'


def test_sin_archivo_el_default_es_ingles(monkeypatch, tmp_path):
    monkeypatch.setattr(idioma_ui, '_archivo', lambda: str(tmp_path / 'no' / 'existe'))
    monkeypatch.setattr(idioma_ui, '_actual', None)
    assert idioma_ui.actual() == 'en'


def test_presence_reporta_el_idioma():
    from plotspace.routers.system import _set_presence
    _set_presence({'locale': 'en'})
    assert idioma_ui.actual() == 'en'
    _set_presence({'locale': 'es'})
    assert idioma_ui.actual() == 'es'


def test_bloque_idioma_del_orquestador_sigue_a_la_ui():
    from plotspace.routers.orchestrator import _bloque_idioma
    assert 'español' in _bloque_idioma()
    idioma_ui.fijar('en')
    assert 'English' in _bloque_idioma()


def test_motivos_del_chat_salen_en_el_idioma_de_la_ui():
    from plotspace.routers.orchestrator import _validar_enviar_prompt
    _, m = _validar_enviar_prompt({'terminal_id': 7, 'prompt': 'x'}, set())
    assert 'no está activa' in m
    idioma_ui.fijar('en')
    _, m = _validar_enviar_prompt({'terminal_id': 7, 'prompt': 'x'}, set())
    assert m == 'terminal #7 is not active in this project'
