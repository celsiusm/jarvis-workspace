"""Tests del extractor de 'message' parcial usado por /chat-stream (orchestrator.py).

El modelo emite un JSON {"message": "...", "actions": [...]} y lo streameamos token a
token: _extraer_message_parcial() saca el valor de 'message' que haya hasta ahora para
mostrarlo en vivo. Es PURA → testeable sin red ni API.
"""
from plotspace.routers.orchestrator import _extraer_message_parcial as ex


def test_vacio_y_antes_del_valor():
    assert ex("") == ""
    assert ex("{") == ""
    assert ex('{"messa') == ""              # todavía no llegó el valor
    assert ex('{"message":') == ""
    assert ex('{"message": "') == ""        # comilla de apertura, sin contenido aún


def test_parcial_progresivo():
    assert ex('{"message": "Hola') == "Hola"
    assert ex('{"message": "Hola, voy a crear') == "Hola, voy a crear"


def test_completo_y_con_cola():
    # message cerrado + sigue el resto del JSON → solo el message
    assert ex('{"message": "Listo.", "actions": [{"type":"none"}]}') == "Listo."


def test_escapes_json():
    assert ex('{"message": "Con \\"comillas\\" ok') == 'Con "comillas" ok'
    assert ex('{"message": "linea1\\nlinea2') == "linea1\nlinea2"
    # \\ en JSON = UN backslash literal (secuencia COMPLETA)
    assert ex('{"message": "ruta C:\\\\tmp') == "ruta C:\\tmp"


def test_escape_a_medias_no_rompe():
    # input termina en UN backslash suelto (mitad de un escape que aún no llegó):
    # no debe romper ni incluir el backslash colgante.
    assert ex('{"message": "abc\\') == "abc"


def test_fence_markdown():
    assert ex('```json\n{"message": "Dentro de fence') == "Dentro de fence"


def test_message_no_primero():
    # aunque 'message' suele ir primero, si viene después igual se encuentra
    assert ex('{"actions": [], "message": "despues') == "despues"


if __name__ == "__main__":
    # corrida directa: python -m backend.tests.test_orquestador_stream
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"  OK {name}")
    print("test_orquestador_stream: TODOS OK")


# ── _sanitizar_respuesta: defensa contra salida del LLM mal formada ──────────
from plotspace.routers.orchestrator import _sanitizar_respuesta as san


def test_san_valida_passthrough():
    p = {"message": "Hola", "actions": [{"type": "spawn_terminal", "name": "X"}]}
    msg, acts = san(p, "raw")
    assert msg == "Hola" and acts == p["actions"]


def test_san_no_dict():
    assert san(None, "raw")    == ("raw", [{"type": "none"}])
    assert san([1, 2], "raw")  == ("raw", [{"type": "none"}])
    assert san("x", "")        == ("Procesado.", [{"type": "none"}])


def test_san_actions_invalidas():
    # null / no-lista → [none]
    assert san({"message": "m", "actions": None}, "r")[1] == [{"type": "none"}]
    # filtra items que no son dict-con-type-str
    msg, acts = san({"message": "m", "actions": [{"type": "none"}, "basura", {"x": 1}, {"type": 5}]}, "r")
    assert acts == [{"type": "none"}]


def test_san_ignora_un_workflow_legacy():
    # El motor de workflows ya no existe: un 'workflow' en la salida se ignora.
    assert san({"message": "m", "actions": [], "workflow": {"pasos": [{"tarea": "x"}]}}, "r") \
        == ("m", [{"type": "none"}])


def test_san_message_fallback():
    assert san({"message": None, "actions": []}, "raw-txt")[0] == "raw-txt"
    assert san({"message": "   ", "actions": []}, "raw-txt")[0] == "raw-txt"
    assert san({"actions": []}, "")[0] == "Procesado."


# ── Tool-use (Fase 7): el 'message' se streamea desde el JSON PARCIAL del tool input ──
# El input del tool_use tiene la MISMA forma {"message":"...","actions":[...]} que el JSON
# que el modelo emitía como texto → _extraer_message_parcial sigue sirviendo sin cambios.
def test_tooluse_message_incremental():
    # prefijos crecientes del partial_json del tool input
    assert ex('{"message":"De acu') == "De acu"
    assert ex('{"message":"De acuerdo, señor.","actions":[') == "De acuerdo, señor."
    assert ex('{"message":"Listo.","actions":[{"type":"none"}]}') == "Listo."


def test_responder_schema_valido():
    from plotspace.routers.orchestrator import RESPONDER_SCHEMA
    props = RESPONDER_SCHEMA["properties"]
    assert "message" in props and "actions" in props and "workflow" not in props
    # message es required (siempre hay texto al usuario)
    assert "message" in RESPONDER_SCHEMA["required"]


def test_schema_no_pide_escribir_el_cierre():
    """El cierre TASK_* lo agrega el engine; si el modelo lo escribía en la
    tarea (como pedía la descripción del campo), el engine salteaba el suyo."""
    import json
    from plotspace.routers.orchestrator import RESPONDER_SCHEMA
    assert "CIERRE LITERAL" not in json.dumps(RESPONDER_SCHEMA, ensure_ascii=False)


def test_formato_api_cierra_todos_los_objetos():
    """Structured outputs exige additionalProperties:false en cada objeto; el
    schema original (el que usa el CLI) no se toca."""
    from plotspace.routers.orchestrator import RESPONDER_SCHEMA, _FORMATO_SALIDA_API

    def objetos(n):
        if isinstance(n, dict):
            if n.get("type") == "object":
                yield n
            for v in n.values():
                yield from objetos(v)
        elif isinstance(n, list):
            for v in n:
                yield from objetos(v)
    schema = _FORMATO_SALIDA_API["format"]["schema"]
    assert _FORMATO_SALIDA_API["format"]["type"] == "json_schema"
    assert all(o.get("additionalProperties") is False for o in objetos(schema))
    assert "additionalProperties" not in RESPONDER_SCHEMA


def test_texto_respuesta_lee_el_json_del_bloque_de_texto():
    from types import SimpleNamespace as NS
    from plotspace.routers.orchestrator import _texto_respuesta
    msg = NS(content=[NS(type="text", text='{"message": "ok"}')])
    assert _texto_respuesta(msg) == '{"message": "ok"}'
    assert _texto_respuesta(NS(content=[])) == "{}"


def test_fence_dentro_del_message_no_se_strippea():
    # Fase 7 fix: un ``` DENTRO del valor de 'message' (Jarvis explicando código) se PRESERVA
    # (antes el strip de fences descartaba el prefijo "message": y el stream se congelaba).
    assert ex('{"message": "Usá ```python aquí') == "Usá ```python aquí"
    assert ex('{"message": "x ```js","actions":[]}') == "x ```js"


def test_tarea_del_agente_usa_su_terminal_y_refuerza_ownership():
    from plotspace.routers.orchestrator import _tarea_para_agente

    txt = _tarea_para_agente("OBJETIVO: arreglar el bug.",
                             ["plotspace/routers/orchestrator.py"], 42)

    assert "OBJETIVO: arreglar el bug." in txt
    assert "ARCHIVOS DE TU PROPIEDAD EXCLUSIVA" in txt
    assert "plotspace/routers/orchestrator.py" in txt
    assert "terminal_42.json" in txt
