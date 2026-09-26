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
    p = {"message": "Hola", "actions": [{"type": "spawn_terminal", "name": "X"}],
         "workflow": {"pasos": [{"agente": "A", "tarea": "t"}]}}
    msg, acts, wf = san(p, "raw")
    assert msg == "Hola" and acts == p["actions"] and wf == p["workflow"]


def test_san_no_dict():
    assert san(None, "raw")    == ("raw", [{"type": "none"}], None)
    assert san([1, 2], "raw")  == ("raw", [{"type": "none"}], None)
    assert san("x", "")        == ("Procesado.", [{"type": "none"}], None)


def test_san_actions_invalidas():
    # null / no-lista → [none]
    assert san({"message": "m", "actions": None}, "r")[1] == [{"type": "none"}]
    # filtra items que no son dict-con-type-str
    msg, acts, wf = san({"message": "m", "actions": [{"type": "none"}, "basura", {"x": 1}, {"type": 5}]}, "r")
    assert acts == [{"type": "none"}]


def test_san_workflow_invalido():
    assert san({"message": "m", "workflow": {}}, "r")[2] is None
    assert san({"message": "m", "workflow": {"pasos": []}}, "r")[2] is None
    assert san({"message": "m", "workflow": {"pasos": "x"}}, "r")[2] is None
    # Fase 7: un paso DEBE tener 'tarea' válida (sino se descarta — anti-crash con truncado).
    wf = {"pasos": [{"agente": "A", "tarea": "hacer algo real"}]}
    assert san({"message": "m", "workflow": wf}, "r")[2] == wf


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


def test_tooluse_workflow_sanitiza():
    # input del tool con workflow → _sanitizar_respuesta lo deja pasar intacto
    tin = {"message": "Lanzo agentes.", "actions": [{"type": "none"}],
           "workflow": {"nombre": "X", "objetivo": "Y", "pasos": [{"agente": "A", "ia_type": "claude", "tarea": "t"}]}}
    msg, acts, wf = san(tin, "raw")
    assert msg == "Lanzo agentes." and acts == [{"type": "none"}]
    assert wf is not None and wf["pasos"][0]["agente"] == "A"


def test_responder_schema_valido():
    from plotspace.routers.orchestrator import RESPONDER_SCHEMA
    props = RESPONDER_SCHEMA["properties"]
    assert "message" in props and "actions" in props and "workflow" in props
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


def test_sanitiza_dropea_paso_sin_tarea():
    # workflow truncado por max_tokens: el último paso queda sin 'tarea' → se descarta (no crashea)
    tin = {"message": "ok", "actions": [],
           "workflow": {"nombre": "X", "objetivo": "Y", "pasos": [
               {"agente": "A", "ia_type": "claude", "tarea": "hacer algo real"},
               {"agente": "B", "ia_type": "claude"}]}}   # B truncado, sin tarea
    _, _, wf = san(tin, "raw")
    assert wf is not None and len(wf["pasos"]) == 1 and wf["pasos"][0]["agente"] == "A"
    # si NINGÚN paso tiene tarea → workflow None
    tin2 = {"message": "ok", "actions": [], "workflow": {"pasos": [{"agente": "B"}]}}
    assert san(tin2, "raw")[2] is None


def test_tarea_engine_usa_terminal_actual_y_refuerza_ownership():
    from plotspace.routers.orchestrator import _tarea_engine_para_terminal

    paso = {
        "tarea": "OBJETIVO: arreglar el bug.",
        "archivos": ["plotspace/routers/orchestrator.py"],
    }
    txt = _tarea_engine_para_terminal(paso, 42)

    assert "OBJETIVO: arreglar el bug." in txt
    assert "ARCHIVOS DE TU PROPIEDAD EXCLUSIVA" in txt
    assert "plotspace/routers/orchestrator.py" in txt
    assert "terminal_42.json" in txt
