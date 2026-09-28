import asyncio
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime
from typing import Optional

import anthropic
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from plotspace.core.database import get_db
from plotspace.core import logs as _logs
from plotspace.core import idioma_ui
from plotspace.core.idioma_ui import L
from plotspace.core.terminal_backend import backend
# Tope de terminales: fuente única de verdad (terminals.py no importa este
# módulo a nivel top-level, así que el import es acíclico). Antes el orquestador
# usaba un 7 hardcodeado y perdía agentes en planes grandes en silencio.
from plotspace.routers.terminals import MAX_TERMINALES

router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])

# Motor del orquestador: 'suscripcion' (default — claude -p headless con la
# cuenta OAuth activa, cero tokens de API pagos; ver core/orq_cli.py) o 'api'
# (el camino viejo con ANTHROPIC_API_KEY, vía de escape).
ORQUESTADOR_MOTOR = os.environ.get('ORQUESTADOR_MOTOR', 'suscripcion')


def _modelo_default(motor: str) -> str:
    """En suscripción el costo por token desaparece → sonnet de fábrica
    (alias del CLI, resuelve al sonnet vigente). La vía API mantiene haiku."""
    return 'claude-haiku-4-5' if motor == 'api' else 'sonnet'


# Modelo del orquestador — fuente ÚNICA. Override por ORQUESTADOR_MODEL.
ORQUESTADOR_MODEL = os.environ.get('ORQUESTADOR_MODEL') or _modelo_default(ORQUESTADOR_MOTOR)

# ─── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Sos JARVIS, orquestador de agentes de IA para desarrollo de software.
Traducís órdenes en lenguaje natural a acciones JSON que coordinan N agentes
trabajando en paralelo directo sobre la rama main del proyecto. Los agentes
leen CLAUDE.md para coordinarse y editan archivos disjuntos para evitar
conflictos. Al terminar, los agentes commitean su propio trabajo — vos NO
commiteás ni mergeás: repartís el trabajo y lo seguís.

══════════════════════════════════════════════════════════════════════
CÓMO PLANIFICAR
══════════════════════════════════════════════════════════════════════

1. CLASIFICAR la orden en una de tres categorías:
   • Conversacional (saludo, "qué podés", explicación, pregunta sobre estado).
   • Operativa simple (abrir/cerrar terminales, sin código de fondo).
   • Compleja (construir, implementar, agregar feature, refactorizar, testear).

2. Si es COMPLEJA, el plan respeta estas reglas:
   a. DESCOMPONER en unidades de trabajo INDEPENDIENTES. Cada unidad debe
      poder hacerse sin esperar el resultado de otra. Si dos cosas
      necesitan el mismo archivo, NO son independientes.
   b. ASIGNAR a cada unidad sus archivos EXCLUSIVOS (paths concretos o
      patrones tipo `plotspace/auth/*`), sacados del [Mapa del proyecto] —
      carpetas y archivos REALES, nunca inventados. Dos agentes NUNCA tocan
      el mismo archivo. Si lo necesitan, son el mismo agente.
   c. DIMENSIONAR el número mínimo de agentes:
      - 1 agente: feature contenida en una carpeta o un par de archivos.
      - 2 agentes: división neta (frontend↔backend, impl↔tests, A↔B).
      - 3+ agentes: módulos verticalmente independientes (auth, pagos…).
      Nunca paralelices por gusto. Si un humano razonable lo haría solo,
      es un agente.
   d. REUSAR antes de crear: si [Estado actual] marca terminales LIBRES
      (la cabecera las lista), dales el trabajo con enviar_prompt en vez de
      spawnear. Nunca a una "ocupada": 🟢 trabajando, ❓ esperando
      respuesta o 💀 caída (el sistema igual la rechaza). Mirá su "tarea",
      lo que "editó" y su "pantalla": si una terminal ya trabajó en esos
      archivos, es la candidata natural para seguir.
   e. LANZAR: por cada agente nuevo, UNA action spawn_terminal con su
      `name` (rol corto: "Backend", "Tests"), `ia_type`, `tarea` y
      `archivos`. Jarvis crea la terminal, espera a que su CLI esté listo y
      le entrega la tarea solo. No hay dependencias entre agentes: todos
      arrancan YA. Si el trabajo es SECUENCIAL (B necesita lo que A
      escribe), lanzá solo la primera parte y decí en el `message` qué
      sigue después ("cuando termine, lanzo los tests").

══════════════════════════════════════════════════════════════════════
PLANTILLA OBLIGATORIA DE LA `tarea` (y del `prompt` de enviar_prompt)
══════════════════════════════════════════════════════════════════════

Cada `tarea` es el prompt que recibe el agente al arrancar en la rama main.
Tiene que ser AUTOSUFICIENTE y debe incluir, en este orden:

  1. OBJETIVO (1-2 oraciones de qué hay que lograr).
  2. ARCHIVOS PERMITIDOS — paths o patrones que ESTE agente puede editar.
  3. ARCHIVOS PROHIBIDOS — paths que otros agentes están tocando, si
     existe riesgo de overlap. Omitir si trabaja solo.
  4. CRITERIO DE ÉXITO — cómo sabe que terminó (endpoint que devuelve X,
     tests que pasan, archivo creado, etc.).
  5. PREVIEW — SOLO si el resultado es algo visual (página web, app, UI):
     "Al terminar, levantá el dev server del proyecto EN BACKGROUND
     (npm run dev &, nohup, o el run-in-background de tu CLI — nunca en
     foreground, que te bloquea) y dejalo corriendo: Jarvis detecta la
     URL y se la muestra al usuario automáticamente. REGLA DE PUERTOS:
     el 3000 está PROHIBIDO (ahí corre Jarvis Workspace); antes de
     levantar el server listá los puertos ocupados (ss -tlnp o
     lsof -iTCP -sTCP:LISTEN -P -n) y elegí uno libre, pasándolo
     explícito (--port/-p/PORT=)." Omitir si no hay nada visual que
     mostrar.

El protocolo de cierre (el archivo sentinel con done/blocked/error + motivo)
lo agrega el SISTEMA automáticamente a cada tarea — NO lo escribas vos.

══════════════════════════════════════════════════════════════════════
TONO Y FORMATO DEL `message`
══════════════════════════════════════════════════════════════════════

Estilo Jarvis (Iron Man): conciso, directo, técnico, sin relleno.

Arrancá directo: "De acuerdo, señor.", "Listo.", "Hecho.", "Lanzo X.", o el
plan sin saludo. El usuario te está dando una orden, no buscás aprobación.

REGLAS:
  • Confirmaciones: una oración corta.
  • Reportes/planes: 1 línea por agente, no párrafos.
  • Preguntas: UNA SOLA pregunta concreta. Nunca lista.
       Mal:  "¿Qué framework? ¿Qué DB? ¿Con o sin tests?"
       Bien: "¿Postgres o SQLite?"
  • Si la duda es razonable, ASUMÍ lo más común del stack y avisá en una
    línea: "Asumo pytest. Decime si querés otro framework."
  • Idioma: respondé en el idioma que indica el bloque [Idioma] (es el de la
    interfaz que eligió el usuario — todo lo demás de la app está en ese
    idioma). Sin bloque: el mismo idioma que el usuario.

══════════════════════════════════════════════════════════════════════
MANEJO DE BLOQUEOS Y ERRORES
══════════════════════════════════════════════════════════════════════

Si [Eventos] o el "último cierre" de una terminal muestran TASK_BLOCKED o
TASK_ERROR con su motivo, y el usuario te pregunta o te pide seguir:
  1. Leé el motivo y la PANTALLA de ese agente antes de actuar.
  2. Si la solución es obvia (falta info, dependencia no anticipada),
     re-instruí a ESE agente con enviar_prompt (si está libre) con la
     info que le falta. No repitas la instrucción que ya falló.
  3. Si requiere decisión del usuario: UNA pregunta concreta.
  4. Nunca escales sin diagnosticar.

══════════════════════════════════════════════════════════════════════
CONTEXTO QUE RECIBÍS EN CADA TURNO
══════════════════════════════════════════════════════════════════════

Recibís la conversación REAL del thread (los turnos previos van incluidos:
"ahora agregale X" refiere a lo que se habló antes). El mensaje actual puede
venir precedido por bloques del sistema:
  [Estado actual] — cada terminal con su estado VIVO: 🟢 trabajando / ⚪
                    quieta / ⏳ arrancando (y hace cuánto), ❓ ESPERANDO
                    RESPUESTA (hay una pregunta en su pantalla), 💀 CAÍDA
                    (su CLI no corre), LIBRE u ocupada; si la lanzaste vos
                    (y para qué orden), la tarea que se le mandó, los
                    archivos que editó (🔒 = es dueña), su último cierre
                    (TASK_* + motivo), sus dev servers y las últimas líneas
                    de su PANTALLA.
  [Proyecto] — ruta, archivos de la raíz, rama git, lo que está sin
                    commitear y los últimos commits (qué ya se hizo).
  [Guía del proyecto] — intro + secciones del CLAUDE.md del proyecto: sus
                    reglas mandan sobre tus preferencias. Leé el archivo
                    con Read si necesitás una sección en detalle.
  [Mapa del proyecto] — stack detectado + árbol real de carpetas con conteo
                    de archivos y propósito de cada una. Es TU fuente para
                    los `archivos` y las rutas de cada tarea: usá SIEMPRE
                    carpetas/archivos que existan acá.
  [Eventos] — los últimos TASK_DONE/BLOCKED/ERROR del proyecto, con motivo.
  [Coordinación] — permisos pendientes, reservas y conflictos de archivos
                    entre agentes.
  [Dev servers] — todos los servers vivos y qué terminal levantó cada uno.
  [Skills activas] — plugins/skills cargados en el proyecto (si aplica).
  [Memoria relevante al pedido] — memorias del proyecto que tocan el pedido.

Usalos para:
  • REUSAR terminales libres en vez de crear nuevas (enviar_prompt).
    JAMÁS le mandes trabajo a una terminal ocupada.
  • Si una terminal ESPERA RESPUESTA, su pantalla dice qué pregunta: si el
    usuario te la contesta, mandásela con enviar_prompt + es_respuesta; si
    no, avisale.
  • Diagnosticar un bloqueo leyendo su motivo y la pantalla del agente
    ANTES de re-instruirlo.
  • No asignar archivos que otro agente está editando (🔒 / [Coordinación])
    ni rehacer lo que ya está en los últimos commits.
  • Respetar el stack, las convenciones de las skills y la guía del proyecto.
  • Contestar el estado con precisión: qué agentes lanzaste, cuáles ya
    cerraron (TASK_DONE) y qué preview está vivo en [Dev servers].
       Mal:  "¿En qué te puedo ayudar?"
       Bien: "Notes está listo en main. Preview en localhost:5173. ¿Tests ahora?"

Si recibís una IMAGEN: describí brevemente lo que ves (en 1 línea) y
plantéa el plan en base a eso (ej: "Veo un mockup de login con email +
password + botón. Lanzo un agente para implementarlo.").

══════════════════════════════════════════════════════════════════════
CÓMO RESPONDÉS
══════════════════════════════════════════════════════════════════════

Tu respuesta FINAL debe ser ÚNICAMENTE un objeto JSON — sin fences, sin
texto alrededor — con: 'message' (texto al usuario, tono Jarvis, va
PRIMERO) y 'actions' (array; usá [{"type":"none"}] en una respuesta
conversacional). La estructura la valida un JSON Schema — vos enfocate en
la SEMÁNTICA correcta de cada campo (abajo).

actions[].type válidos (NADA MÁS):
  "none"           — no hacer nada operativo
  "spawn_terminal" — { name, ia_type, tarea, archivos } → lanza UN agente y
                     le entrega su tarea apenas su CLI esté listo. Es TU
                     herramienta para repartir trabajo entre agentes nuevos
                     (una action por agente).
                     Sin `tarea` → { name, ia_type, count }: abre terminales
                     vacías (el usuario las va a usar a mano).
  "close_terminal" — { terminal_id }  → mata la sesión tmux del agente
  "close_all"      — sin args        → mata todas las terminales del proyecto
                                       (y también el preview si estaba activo)
  "stop_preview"   — sin args        → apaga el servidor de preview activo.
                                       Es un proceso aparte de las terminales.
  "enviar_prompt"  — { terminal_id, prompt } → tipea la tarea/mensaje en una
                     terminal VIVA y LIBRE. Es TU herramienta para "decile a
                     X que...", fixes sueltos o re-instruir a un agente que
                     ya existe. El prompt sigue la misma plantilla que la
                     `tarea`. Podés emitir varias (una por terminal).
                     Con `es_respuesta: true` el prompt es la RESPUESTA a la
                     pregunta que esa terminal tiene en pantalla (❓ ESPERANDO
                     RESPUESTA): se tipea tal cual ("y", "2", "usá JWT"), sin
                     envoltorio. Solo vale para una terminal que espera.

Cerrar una terminal no es cerrar el preview:
  • "cerrá la terminal" / "matá el agente X" / "borrá ese Claude"
      → close_terminal (con terminal_id) o close_all
  • "cerrá el servidor" / "apagá el preview" / "matá el puerto 8081" /
    "bajá el servidor" / "cerrá la pestaña del preview"
      → stop_preview (NO close_terminal)

Si [Preview activo] aparece en el contexto, sabés exactamente qué URL hay
para apagar. Si el usuario te pasa una URL/puerto y matchea con la del
[Preview activo], usás stop_preview sin dudar. Si NO matchea, decile que
ese puerto no lo lanzó JARVIS.

ia_type válidos: "claude", "codex", "gemini", "opencode", "qwen",
"antigravity", "grok", "manual"

Máximo __MAX_TERMINALES__ terminales totales (activas + nuevas). Si la orden
necesita más, avisá y proponé reusar las existentes o fasear el trabajo.

══════════════════════════════════════════════════════════════════════
EJEMPLOS CONCRETOS (estos son los outputs reales esperados)
══════════════════════════════════════════════════════════════════════

[1] CONVERSACIONAL — saludo / pregunta de capacidades
Usuario: "¿qué podés hacer?"
{"message":"Lanzo agentes de Claude/Codex/Gemini sobre la rama main y les reparto el trabajo en paralelo. Decime qué construimos, señor.","actions":[{"type":"none"}]}

[2] OPERATIVA SIMPLE — abrir terminales vacías
Usuario: "abrí 2 claude"
{"message":"Dos Claude listos.","actions":[{"type":"spawn_terminal","name":"Claude Code","ia_type":"claude","count":2}]}

[3] UN AGENTE — feature contenida
Usuario: "agregale al backend un endpoint /api/health"
{"message":"De acuerdo. Un agente sobre plotspace/routers/.","actions":[{"type":"spawn_terminal","name":"Backend","ia_type":"claude","archivos":["plotspace/routers/health.py","plotspace/main.py"],"tarea":"OBJETIVO: crear GET /api/health que devuelva {ok: true, timestamp: ISO8601}. PERMITIDO editar: plotspace/routers/health.py (nuevo), plotspace/main.py (solo para registrar el router). PROHIBIDO tocar cualquier otro archivo. CRITERIO DE ÉXITO: un test con TestClient de GET /api/health devuelve 200 con el JSON correcto."}]}
(los paths salen del [Mapa del proyecto] del contexto — en otro proyecto serían otros)

[4] DOS AGENTES EN PARALELO — frontend/backend disjuntos
Usuario: "construí un módulo de notas: API + UI simple"
{"message":"De acuerdo. Dos agentes en paralelo: backend y frontend.","actions":[{"type":"spawn_terminal","name":"Backend","ia_type":"claude","archivos":["plotspace/routers/notes.py","plotspace/main.py","plotspace/core/database.py"],"tarea":"OBJETIVO: CRUD /api/notes con SQLite (tabla notes: id, contenido, created_at). PERMITIDO editar: plotspace/routers/notes.py (nuevo), plotspace/main.py (solo registrar router), plotspace/core/database.py (solo agregar CREATE TABLE notes). PROHIBIDO: frontend/*, otros routers. CRITERIO DE ÉXITO: GET/POST/DELETE /api/notes responden 200 con JSON."},{"type":"spawn_terminal","name":"Frontend","ia_type":"claude","archivos":["frontend/notes/*"],"tarea":"OBJETIVO: UI que consume /api/notes (asumí que ese endpoint existe). Lista + crear + borrar, respetando la estructura y convenciones que ya tiene el frontend del proyecto. PERMITIDO editar: frontend/notes/* (nuevo). PROHIBIDO: plotspace/*, todo lo del otro agente. CRITERIO DE ÉXITO: la página de notas muestra la lista, crear y borrar funcionan."}]}

[5] TRABAJO SECUENCIAL — lanzar la primera parte y decir qué sigue
Usuario: "implementá auth con JWT y después escribile tests"
{"message":"De acuerdo. Arranco la implementación; cuando cierre, lanzo los tests.","actions":[{"type":"spawn_terminal","name":"Auth","ia_type":"claude","archivos":["plotspace/auth/*","plotspace/main.py"],"tarea":"OBJETIVO: endpoints POST /auth/register, POST /auth/login, GET /auth/me con JWT (usá pyjwt). PERMITIDO editar: plotspace/auth/* (nueva carpeta), plotspace/main.py (registrar router). PROHIBIDO: plotspace/tests/*. CRITERIO DE ÉXITO: register crea user, login devuelve token, /me con Authorization Bearer devuelve el user."}]}

[6] PETICIÓN AMBIGUA — preguntar UNA cosa
Usuario: "agregá login"
{"message":"¿Sesiones con JWT o con cookies HTTP-only?","actions":[{"type":"none"}]}

[7] FALTA INFO PERO ES ASUMIBLE — avisar la asunción
Usuario: "ponele tests al módulo de pagos"
{"message":"De acuerdo. Asumo pytest. Decime si querés otro framework.","actions":[{"type":"spawn_terminal","name":"Tests","ia_type":"claude","archivos":["plotspace/tests/test_payments.py"],"tarea":"OBJETIVO: tests pytest cubriendo plotspace/payments/*. Leé los archivos primero. PERMITIDO editar: plotspace/tests/test_payments.py (nuevo). PROHIBIDO modificar plotspace/payments/*. CRITERIO DE ÉXITO: happy path + errores de cada función pública, pytest verde."}]}

[8] CERRAR PREVIEW SERVER (NO confundir con cerrar terminal)
Contexto: [Preview activo]\n  Servidor de preview corriendo en http://localhost:8081/
Usuario: "cerrá el servidor del frontend" o "matá el puerto 8081"
{"message":"Preview cerrado.","actions":[{"type":"stop_preview"}]}

[9] DESTRABAR UN AGENTE BLOQUEADO
Contexto: [Estado actual]\n  - ID 42: Auth (claude) — ⚪ quieta hace 2 min — LIBRE\n    último cierre: TASK_BLOCKED hace 2 min: necesito saber si la tabla users ya existe o la creo
Usuario: "la tabla no existe, que la cree"
{"message":"Le paso la info a Auth.","actions":[{"type":"enviar_prompt","terminal_id":42,"prompt":"CONTINUACIÓN: la tabla users no existe — creala vos en plotspace/database.py con columnas id, email UNIQUE, password_hash, created_at. Después seguí con auth como estabas. PERMITIDO: plotspace/auth/*, plotspace/database.py (solo agregar la tabla), plotspace/main.py. PROHIBIDO: plotspace/tests/*."}]}

[10] PROMPT DIRECTO A UNA TERMINAL VIVA
Contexto: [Estado actual]\n  - ID 151: Claude Code #2 (claude) — ⚪ quieta hace 12 min — LIBRE
Usuario: "decile a la claude libre que pula el diseño de la landing"
{"message":"Le mando la tarea a Claude Code #2.","actions":[{"type":"enviar_prompt","terminal_id":151,"prompt":"OBJETIVO: pulir el diseño de la landing. Trabajá sobre los archivos de la landing que muestra el mapa del proyecto (HTML + CSS). CRITERIO DE ÉXITO: jerarquía tipográfica consistente, espaciado uniforme y paleta cohesiva, sin romper el layout existente."}]}

══════════════════════════════════════════════════════════════════════
RECORDATORIO FINAL
══════════════════════════════════════════════════════════════════════

Tu ÚLTIMO mensaje es SIEMPRE el objeto JSON de respuesta, con
'message' primero en tono Jarvis. Antes de emitirlo, revisá mentalmente:
tono Jarvis, archivos disjuntos y sacados del mapa real, terminales libres
reusadas antes de spawnear, una tarea autosuficiente por agente.
""".replace('__MAX_TERMINALES__', str(MAX_TERMINALES))

# ─── Modelos ───────────────────────────────────────────────────────────────────

def _bloque_idioma() -> str:
    """Directiva de idioma por turno (no en el SYSTEM_PROMPT: así el prompt
    fijo sigue siendo cacheable y el cambio de idioma aplica al instante)."""
    if idioma_ui.actual() == 'en':
        return ("[Idioma]\nEnglish — the user's interface is in English: write your "
                "`message` in English (address the user as \"sir\"). Tasks for the "
                "agents can stay in whatever language fits the project.")
    return "[Idioma]\nEspañol — la interfaz del usuario está en español: tu `message` va en español."


class ChatRequest(BaseModel):
    project_id: int
    message: str
    image_base64: Optional[str] = None
    media_type: Optional[str] = None
    # Turnos PREVIOS del thread activo ([{role, content}, ...]) — el frontend ya
    # los tiene para pintar el chat; mandarlos da memoria conversacional real.
    # Untrusted: se sanean server-side en _mensajes_con_historial.
    historial: Optional[list] = None
    # Idioma de la interfaz ('es'|'en'): el orquestador responde en ese idioma
    # y los avisos que arma el server también (core/idioma_ui).
    lang: Optional[str] = None


class HistorialThread(BaseModel):
    thread_id: str
    mensajes:  list


# ─── Endpoints: historial del orquestador ─────────────────────────────────────

@router.get("/historial/{project_id}")
async def listar_historial(project_id: int):
    """Lista los threads del proyecto. Cada uno incluye un preview del primer mensaje."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, thread_id, mensajes, created_at, updated_at '
            'FROM orquestador_historial WHERE project_id = ? ORDER BY updated_at DESC',
            (project_id,)
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    resultado = []
    for r in rows:
        try:
            mensajes = json.loads(r['mensajes'])
        except (json.JSONDecodeError, TypeError):
            mensajes = []
        preview = ''
        for m in mensajes:
            if m.get('rol') == 'user' and m.get('texto'):
                preview = m['texto'][:80]
                break
        if not preview and mensajes:
            preview = (mensajes[0].get('texto') or '')[:80]
        resultado.append({
            'id':         r['id'],
            'thread_id':  r['thread_id'],
            'preview':    preview or '(sin mensajes)',
            'created_at': r['created_at'],
            'updated_at': r['updated_at'],
            'count':      len(mensajes),
        })
    return resultado


@router.get("/historial/{project_id}/{thread_id}")
async def obtener_thread(project_id: int, thread_id: str):
    """Devuelve los mensajes completos de un thread específico."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT mensajes, created_at, updated_at FROM orquestador_historial '
            'WHERE project_id = ? AND thread_id = ?',
            (project_id, thread_id)
        )
        row = cursor.fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Thread no encontrado")
    try:
        mensajes = json.loads(row['mensajes'])
    except (json.JSONDecodeError, TypeError):
        mensajes = []
    return {
        'thread_id':  thread_id,
        'mensajes':   mensajes,
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }


@router.post("/historial/{project_id}", status_code=201)
async def guardar_thread(project_id: int, thread: HistorialThread):
    """Guarda un thread del orquestador. Si thread_id ya existe, actualiza."""
    ahora = datetime.now().isoformat()
    payload = json.dumps(thread.mensajes, ensure_ascii=False)

    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM projects WHERE id = ?', (project_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Proyecto no encontrado")

        cursor.execute(
            'SELECT id FROM orquestador_historial WHERE project_id = ? AND thread_id = ?',
            (project_id, thread.thread_id)
        )
        existente = cursor.fetchone()
        if existente:
            cursor.execute(
                'UPDATE orquestador_historial SET mensajes = ?, updated_at = ? WHERE id = ?',
                (payload, ahora, existente['id'])
            )
        else:
            cursor.execute(
                'INSERT INTO orquestador_historial (project_id, thread_id, mensajes, created_at, updated_at) '
                'VALUES (?, ?, ?, ?, ?)',
                (project_id, thread.thread_id, payload, ahora, ahora)
            )
        conn.commit()
    finally:
        conn.close()
    return {'ok': True, 'thread_id': thread.thread_id, 'count': len(thread.mensajes)}


@router.delete("/historial/{project_id}", status_code=204)
async def limpiar_historial(project_id: int):
    """Borra TODOS los threads de un proyecto."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM orquestador_historial WHERE project_id = ?', (project_id,))
        conn.commit()
    finally:
        conn.close()


@router.delete("/historial/{project_id}/{thread_id}", status_code=204)
async def eliminar_thread(project_id: int, thread_id: str):
    """Borra un thread individual."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'DELETE FROM orquestador_historial WHERE project_id = ? AND thread_id = ?',
            (project_id, thread_id)
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Thread no encontrado")
        conn.commit()
    finally:
        conn.close()


# ─── Endpoints: chat ──────────────────────────────────────────────────────────

# Schema de la respuesta del orquestador: {message, actions}. Lo usan los DOS
# motores — el CLI con --json-schema y la API con structured outputs
# (output_config.format), que garantiza JSON válido sin forzar una tool. El schema
# es laxo a propósito; _sanitizar_respuesta queda como red de 2do nivel.
# 'message' va PRIMERO para que el stream lo entregue antes.
_IA_TYPES = ["claude", "codex", "gemini", "opencode", "qwen", "antigravity", "grok", "manual"]

RESPONDER_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {
            "type": "string",
            "description": "Texto al usuario, tono Jarvis (conciso, directo, sin relleno). Va PRIMERO. Se streamea token a token.",
        },
        "actions": {
            "type": "array",
            "description": "Acciones operativas. Usá [{\"type\":\"none\"}] en una respuesta conversacional. Para trabajo de código: un spawn_terminal CON tarea por agente, o enviar_prompt a una terminal LIBRE.",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["none", "spawn_terminal", "close_terminal", "close_all", "stop_preview", "enviar_prompt"],
                    },
                    "name": {"type": "string", "description": "Solo spawn_terminal: nombre de la terminal (ej 'Backend')."},
                    "ia_type": {"type": "string", "enum": _IA_TYPES, "description": "Solo spawn_terminal."},
                    "count": {"type": "integer", "description": "Solo spawn_terminal SIN tarea: cuántas terminales vacías. Con tarea se ignora (1 agente por action)."},
                    "tarea": {"type": "string", "description": "Solo spawn_terminal: prompt AUTOSUFICIENTE del agente siguiendo la PLANTILLA (OBJETIVO, ARCHIVOS PERMITIDOS/PROHIBIDOS, CRITERIO DE ÉXITO, PREVIEW si aplica). Jarvis se lo entrega apenas su CLI esté listo. El cierre (sentinel) lo agrega el sistema: no lo incluyas."},
                    "archivos": {"type": "array", "items": {"type": "string"}, "description": "Solo spawn_terminal con tarea: paths/patrones EXCLUSIVOS de este agente (del [Mapa del proyecto]). Dos agentes JAMÁS comparten archivos."},
                    "terminal_id": {"type": "integer", "description": "close_terminal: id a cerrar. enviar_prompt: id de la terminal destino."},
                    "prompt": {"type": "string", "description": "Solo enviar_prompt: la tarea/mensaje que se tipea en esa terminal. Autosuficiente, con archivos/carpetas concretos del [Mapa del proyecto]."},
                    "es_respuesta": {"type": "boolean", "description": "Solo enviar_prompt: true si el prompt RESPONDE la pregunta que la terminal tiene en pantalla (❓ ESPERANDO RESPUESTA) — se tipea tal cual."},
                },
                "required": ["type"],
            },
        },
    },
    "required": ["message", "actions"],
}


def _con_objetos_cerrados(schema):
    """Copia del schema con additionalProperties:false en cada objeto: lo exige
    structured outputs. El CLI recibe el schema original (--json-schema no lo pide)."""
    if isinstance(schema, dict):
        out = {k: _con_objetos_cerrados(v) for k, v in schema.items()}
        if out.get('type') == 'object':
            out.setdefault('additionalProperties', False)
        return out
    if isinstance(schema, list):
        return [_con_objetos_cerrados(v) for v in schema]
    return schema


_FORMATO_SALIDA_API = {"format": {"type": "json_schema",
                                  "schema": _con_objetos_cerrados(RESPONDER_SCHEMA)}}


def _texto_respuesta(message) -> str:
    """El JSON de la respuesta: con structured outputs llega como bloque de texto."""
    return ''.join(getattr(b, "text", "") for b in (getattr(message, "content", None) or [])
                   if getattr(b, "type", None) == "text") or "{}"


_MSG_RE = re.compile(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)', re.DOTALL)


def _extraer_message_parcial(texto: str) -> str:
    """Del texto (posiblemente PARCIAL) del modelo —un JSON
    {"message": "...", "actions": [...], ...} donde 'message' va SIEMPRE primero—
    extrae el valor de 'message' que haya hasta ahora, para streamearlo en vivo.
    Tolera escapes JSON. Pura y testeable. "" si aún no apareció."""
    # Tool-use: el partial_json NUNCA trae fences de wrapping; un ``` solo puede aparecer DENTRO
    # del valor de 'message' (Jarvis explicando código) y NO debe tocarse — strippearlo descartaría
    # el prefijo "message": y el stream se congelaría. Corremos la regex directo sobre el texto.
    m = _MSG_RE.search(texto)
    if not m:
        return ""
    crudo = m.group(1)
    # No cortar en un escape a la mitad (un '\' impar al final rompe el decode).
    if (len(crudo) - len(crudo.rstrip("\\"))) % 2 == 1:
        crudo = crudo[:-1]
    try:
        return json.loads('"' + crudo + '"')
    except Exception:
        return crudo


def _sanitizar_respuesta(parsed, raw_text):
    """Defiende la ejecución de una respuesta del LLM MAL FORMADA: el modelo a veces devuelve
    actions=null, tipos equivocados, o ni siquiera un dict. Devuelve SIEMPRE un par seguro
    (message:str, actions:list[dict con 'type' str]) → el resto del pipeline nunca crashea por
    forma inválida. En spawn_terminal una `tarea` que no es string no vacío se descarta (queda
    una terminal vacía, sin `archivos`) y con tarea `archivos` se normaliza a lista de strings.
    Pura/testeable."""
    if not isinstance(parsed, dict):
        return ((raw_text if isinstance(raw_text, str) and raw_text.strip() else "Procesado."),
                [{"type": "none"}])
    msg = parsed.get("message")
    if not isinstance(msg, str) or not msg.strip():
        msg = raw_text if isinstance(raw_text, str) and raw_text.strip() else "Procesado."
    acts = parsed.get("actions")
    if not isinstance(acts, list):
        acts = []
    acts = [dict(a) for a in acts if isinstance(a, dict) and isinstance(a.get("type"), str)]
    for a in acts:
        if a["type"] != "spawn_terminal":
            continue
        tarea = a.get("tarea")
        if not (isinstance(tarea, str) and tarea.strip()):
            a.pop("tarea", None)
            a.pop("archivos", None)     # sin tarea no hay territorio que reclamar
            continue
        a["tarea"] = tarea.strip()
        archivos = a.get("archivos")
        a["archivos"] = ([x.strip() for x in archivos if isinstance(x, str) and x.strip()]
                         if isinstance(archivos, list) else [])
    if not acts:
        acts = [{"type": "none"}]
    return (msg, acts)


async def _procesar_respuesta_orquestador(raw_text, usage, project, req, terminals_activas, stop_reason=None):
    """Parsea la respuesta del modelo (JSON), ejecuta las actions, registra el uso
    y actualiza STATE.md. Devuelve el dict de respuesta. COMPARTIDO por /chat y
    /chat-stream → fuente ÚNICA de la lógica de ejecución (sin drift)."""
    try:
        if usage is not None:
            from plotspace.core.database import registrar_uso_orquestador
            # DB sync fuera del loop: es el path caliente del chat (cada mensaje).
            await asyncio.to_thread(registrar_uso_orquestador, req.project_id,
                                    getattr(usage, 'input_tokens', 0) or 0,
                                    getattr(usage, 'output_tokens', 0) or 0)
    except Exception as e:
        print(f'[uso] no pude registrar uso: {e}')

    # Con tool-use el raw es JSON PURO (re-serializado del tool input) → parsear DIRECTO.
    # NO strippear fences acá: si 'message' contiene un ``` de código (Jarvis explicándole
    # código a un dev), el split partiría el JSON serializado → json.loads falla → se perdería
    # el mensaje + actions. El de-fence queda SOLO como fallback si el parse directo
    # falla (compat con un eventual fallback de texto crudo legacy).
    try:
        parsed = json.loads(raw_text)
    except (json.JSONDecodeError, ValueError):
        parsed = None
        if isinstance(raw_text, str) and "```" in raw_text:
            try:
                t = raw_text.split("```json", 1)[1] if "```json" in raw_text else raw_text.split("```", 1)[1]
                parsed = json.loads(t.split("```")[0].strip())
            except Exception:
                parsed = None
        # Último recurso (motor CLI sin structured_output): el modelo pudo
        # meter texto alrededor del JSON — probar del primer '{' al último '}'.
        if parsed is None and isinstance(raw_text, str):
            ini, fin = raw_text.find('{'), raw_text.rfind('}')
            if 0 <= ini < fin:
                try:
                    parsed = json.loads(raw_text[ini:fin + 1])
                except Exception:
                    parsed = None

    # Saneo defensivo: el LLM puede mandar JSON con tipos equivocados o incompleto →
    # nunca dejar que rompa la ejecución de las actions.
    jarvis_message, actions = _sanitizar_respuesta(parsed, raw_text)

    # Si el modelo se cortó por longitud, las tareas casi seguro quedaron truncadas → NO
    # lanzar agentes con un plan a medias: descartar lo que entrega trabajo y pedir que repita.
    if stop_reason == "max_tokens":
        antes = len(actions)
        actions = [a for a in actions
                   if not (a['type'] == 'enviar_prompt'
                           or (a['type'] == 'spawn_terminal' and a.get('tarea')))]
        if len(actions) != antes:
            actions = actions or [{"type": "none"}]
            jarvis_message = (jarvis_message + L(
                " (me corté por longitud, señor — repetime la orden más acotada).",
                " (I got cut off by length, sir — give me the order again, narrower).")).strip()

    created_terminals = []
    closed_all        = False
    # Objetivo corto de esta orden: agrupa en el monitor de Tasks a los agentes
    # que Jarvis lanzó (o re-instruyó) por el mismo pedido.
    orquestacion = _objetivo_orquestacion(req.message)
    orquestacion_ts = datetime.now().isoformat(timespec='seconds')
    origen_escrito = False

    # ── Ejecutar acciones ──────────────────────────────────────────────────────
    for action in actions:
        atype = action.get("type", "none")

        if atype == "spawn_terminal":
            count_actual = len(terminals_activas) + len(created_terminals)
            tarea = action.get("tarea")
            nuevas = await _spawn_terminales(
                project_id   = req.project_id,
                name         = action.get("name", "Terminal"),
                ia_type      = action.get("ia_type", "manual"),
                # Con tarea: UN agente por action (cada agente lleva su tarea).
                count        = 1 if tarea else max(1, _entero(action.get("count"), 1)),
                count_actual = count_actual,
            )
            if tarea and nuevas:
                await _lanzar_agente_con_tarea(
                    req.project_id, nuevas[0], tarea, action.get("archivos") or [],
                    orquestacion, orquestacion_ts)
                origen_escrito = True
            elif tarea:
                jarvis_message = (jarvis_message + L(
                    f" ⚠️ No lancé a {action.get('name') or 'un agente'}: tope de "
                    f"{MAX_TERMINALES} terminales.",
                    f" ⚠️ I didn't launch {action.get('name') or 'an agent'}: "
                    f"{MAX_TERMINALES}-terminal limit reached.")).strip()
            created_terminals.extend(nuevas)

        elif atype == "close_all":
            trabajando = await _cerrar_todas(req.project_id)
            if trabajando:
                # Guard: hay agentes a mitad de tarea → no se cerró NADA (todo-o-
                # nada) y closed_all queda False (true borra TODAS las cards).
                jarvis_message = (jarvis_message + L(
                    " ⚠️ No cerré nada: {q} está(n) trabajando ahora mismo. Si igual "
                    "querés cerrarlas, repetime la orden.",
                    " ⚠️ I didn't close anything: {q} is/are working right now. If you "
                    "still want them closed, repeat the order.",
                ).replace('{q}', ", ".join(trabajando))).strip()
            else:
                _detener_preview_si_existe(req.project_id)
                closed_all = True

        elif atype == "close_terminal":
            tid = _entero(action.get("terminal_id"), None)
            if tid:
                motivo = await _cerrar_terminal(tid, req.project_id)
                if motivo:
                    jarvis_message = (jarvis_message + L(f" ⚠️ Ojo: {motivo}.",
                                                         f" ⚠️ Heads up: {motivo}.")).strip()

        elif atype == "stop_preview":
            _detener_preview_si_existe(req.project_id)

        elif atype == "enviar_prompt":
            # Mandarle una tarea (o la respuesta a una pregunta en pantalla) a
            # una terminal VIVA sin spawnear nada.
            activas_ids = {t['id'] for t in terminals_activas}
            tid, motivo = _validar_enviar_prompt(action, activas_ids)
            es_respuesta = action.get('es_respuesta') is True
            if not motivo:
                # La regla "nunca a una ocupada" la decide el estado VIVO
                # (trabajando, pregunta en pantalla, CLI caído).
                try:
                    from plotspace.core import orq_contexto
                    fila = next(t for t in terminals_activas if t['id'] == tid)
                    info = await asyncio.to_thread(orq_contexto.info_terminal,
                                                   req.project_id, fila)
                    motivo = _motivo_rechazo_envio(info, es_respuesta)
                except Exception as e:
                    print(f'[enviar_prompt] sin estado vivo de #{tid}: {e}')
            if motivo:
                jarvis_message = (jarvis_message + L(
                    f" ⚠️ No envié el prompt: {motivo}.",
                    f" ⚠️ I didn't send the prompt: {motivo}.")).strip()
            else:
                texto = action['prompt'].strip()
                if es_respuesta:
                    ok = await send_to_agent(tid, texto, crudo=True)
                else:
                    ok = await send_to_agent(tid, texto + _cierre_prompt_directo(tid))
                if ok is False:
                    jarvis_message = (jarvis_message + L(
                        f" ⚠️ No pude entregar el prompt a la terminal #{tid} (su "
                        "sesión no responde).",
                        f" ⚠️ I couldn't deliver the prompt to terminal #{tid} (its "
                        "session isn't responding).")).strip()
                else:
                    _logs.evento('prompt_directo', terminal_id=tid,
                                 project_id=req.project_id,
                                 respuesta=es_respuesta)
                    if not es_respuesta:
                        # Desde ahora es un agente de Jarvis con esta tarea.
                        await asyncio.to_thread(_registrar_origen, tid, texto,
                                                orquestacion, orquestacion_ts)
                        origen_escrito = True

    if origen_escrito:
        await _avisar_agentes(req.project_id)

    await _actualizar_state_md(project, req.project_id)

    return {
        "response":          jarvis_message,
        "actions":           actions,
        "created_terminals": created_terminals,
        "closed_all":        closed_all,
    }


# Referencias FUERTES a las tareas en background: el event loop solo guarda
# referencias débiles, así que un `asyncio.create_task` suelto puede ser
# recolectado a mitad de camino (la auto-intervención moría en silencio).
_tareas_fondo: set = set()


def _en_fondo(coro) -> asyncio.Task:
    tarea = asyncio.create_task(coro)
    _tareas_fondo.add(tarea)
    tarea.add_done_callback(_tareas_fondo.discard)
    return tarea


def _entero(valor, default):
    """int() tolerante para campos que escribe el modelo ("2", 2.0, "dos"):
    un ValueError a mitad de las acciones cortaba el resto, y el frontend
    reintentaba por /chat repitiendo las que ya se habían ejecutado."""
    try:
        return int(valor)
    except (TypeError, ValueError):
        return default


def _validar_enviar_prompt(action: dict, activas_ids: set):
    """Guarda de la action enviar_prompt. Devuelve (terminal_id, None) si el
    envío es válido, o (None, motivo legible) si no. PURA — el caller junta
    activas_ids (terminales del proyecto); el estado VIVO (ocupada, pregunta
    en pantalla, CLI caído) lo decide después _motivo_rechazo_envio."""
    tid = action.get('terminal_id')
    if not isinstance(tid, int):
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            return None, L('falta un terminal_id válido', 'a valid terminal_id is missing')
    prompt = action.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip():
        return None, L(f'falta el prompt para la terminal #{tid}',
                       f'the prompt for terminal #{tid} is missing')
    if tid not in activas_ids:
        return None, L(f'la terminal #{tid} no está activa en este proyecto',
                       f'terminal #{tid} is not active in this project')
    return tid, None


def _motivo_rechazo_envio(info: dict, es_respuesta: bool) -> str:
    """'' si se puede mandar; si no, el porqué legible. PURA.
    Una respuesta solo va a quien ESPERA una; una tarea, solo a una libre."""
    from plotspace.core.orq_contexto import motivo_no_libre
    if info.get('estado') in ('caido', 'sin_sesion'):
        return motivo_no_libre(info)
    if es_respuesta:
        return '' if info.get('esperando') else L('no tiene ninguna pregunta en pantalla',
                                                   'it has no question on screen')
    if info.get('esperando'):
        return L('tiene una pregunta en pantalla esperando respuesta — contestala '
                 'con es_respuesta o avisale al usuario',
                 'it has a question on screen waiting for an answer — answer it '
                 'with es_respuesta or tell the user')
    return motivo_no_libre(info)


def _cierre_prompt_directo(terminal_id: int) -> str:
    """Cierre estructurado de toda tarea que entrega Jarvis (spawn_terminal con
    tarea o enviar_prompt): el sentinel lo registra y el orquestador lo ve en
    [Eventos] / 'último cierre' en el próximo turno. Pide el post-mortem
    mínimo: motivo OBLIGATORIO al fallar (materia prima de las lecciones) y
    memorias_usadas (medición de lectura de la memoria compartida)."""
    return (
        "\n\nAl terminar, señalá tu cierre: "
        f"mkdir -p .jarvis/signals && printf '%s' '{{\"estado\":\"done\",\"motivo\":\"\","
        f"\"memorias_usadas\":[]}}' > .jarvis/signals/terminal_{terminal_id}.json "
        "(estado blocked o error si no pudiste — el motivo es OBLIGATORIO y concreto; "
        "en memorias_usadas listá los slugs de .jarvis/memory/ que te sirvieron; si "
        "el tropiezo se podía prevenir con una regla corta, guardá además una "
        "memoria con tags [leccion])."
    )


_HISTORIAL_MAX_TURNOS = 12    # turnos previos que viajan a la API por mensaje
_HISTORIAL_MAX_CHARS  = 4000  # tope por turno: un turno gigante no se come el contexto


def _mensajes_con_historial(historial, user_content,
                            max_turnos: int = _HISTORIAL_MAX_TURNOS,
                            max_chars: int = _HISTORIAL_MAX_CHARS) -> list:
    """Arma la lista `messages` multi-turno: historial saneado + mensaje actual.

    El historial viene del BROWSER (untrusted) → se sanea acá: solo roles
    user/assistant con contenido string no vacío, truncado a `max_chars`,
    tope de `max_turnos` (los más recientes), primer mensaje siempre user y
    roles consecutivos mergeados (la API exige alternancia). El mensaje actual
    (string con contexto, o bloques si hay imagen) va SIEMPRE último. Pura."""
    previos = []
    for h in historial or []:
        if not isinstance(h, dict):
            continue
        rol, cont = h.get('role'), h.get('content')
        if rol not in ('user', 'assistant') or not isinstance(cont, str):
            continue
        cont = cont.strip()
        if not cont:
            continue
        if len(cont) > max_chars:
            cont = cont[:max_chars] + '…'
        previos.append({'role': rol, 'content': cont})
    previos = previos[-max_turnos:]
    while previos and previos[0]['role'] == 'assistant':
        previos.pop(0)

    mensajes = []
    for m in previos:
        if mensajes and mensajes[-1]['role'] == m['role']:
            mensajes[-1] = {'role': m['role'],
                            'content': mensajes[-1]['content'] + '\n\n' + m['content']}
        else:
            mensajes.append(dict(m))

    if isinstance(user_content, str):
        if mensajes and mensajes[-1]['role'] == 'user':
            mensajes[-1] = {'role': 'user',
                            'content': mensajes[-1]['content'] + '\n\n' + user_content}
        else:
            mensajes.append({'role': 'user', 'content': user_content})
    else:
        bloques = list(user_content)
        if mensajes and mensajes[-1]['role'] == 'user':
            colgado = mensajes.pop()
            bloques = [{'type': 'text', 'text': colgado['content']}] + bloques
        mensajes.append({'role': 'user', 'content': bloques})
    return mensajes


def _system_con_cache() -> list:
    """System prompt como bloque con cache_control: el prefijo estable
    (tools + system) se cachea entre mensajes → el costo de input de cada
    turno del chat baja ~90% en la porción cacheada."""
    return [{"type": "text", "text": SYSTEM_PROMPT,
             "cache_control": {"type": "ephemeral"}}]


_BLOQUE_OJOS = """

══════════════════════════════════════════════════════════════════════
OJOS PROPIOS (tools de lectura)
══════════════════════════════════════════════════════════════════════

Tenés Read/Glob/Grep sobre la carpeta del proyecto (SOLO lectura — jamás
edites, crees ni ejecutes nada). Usalos ÚNICAMENTE cuando el pedido lo
amerite: planificar agentes sobre código que no conocés, verificar que un
archivo exista antes de asignarlo, o entender una estructura que el
[Mapa del proyecto] no alcanza a mostrar. Cada lectura suma latencia al chat:
leé solo lo que cambia el plan, y para saludos/preguntas simples respondé
directo sin tocar ninguna tool.
"""


def _system_prompt_cli() -> str:
    """Prompt del modo suscripción: el base + el bloque de exploración
    (solo el CLI tiene tools de lectura)."""
    return SYSTEM_PROMPT + _BLOQUE_OJOS


_anthropic_cliente = None
_anthropic_key = None


def _guard_api_key() -> str:
    """Devuelve la ANTHROPIC_API_KEY o lanza un 409 ESTRUCTURADO (no un 500 crudo)
    para que el frontend lo muestre como empty-state limpio. El chat de Jarvis (motor
    `api`) es el único que usa esta key; los agentes en terminales (BYOK)
    NO la necesitan. Forma del error: status 409, body
    {"detail": {"error": "no_api_key", "message": "..."}}."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=409, detail={
            "error": "no_api_key",
            "message": L("Configurá ANTHROPIC_API_KEY (plotspace/.env) para usar el chat "
                         "de Jarvis. Los agentes en terminales (BYOK) no la necesitan.",
                         "Set ANTHROPIC_API_KEY (plotspace/.env) to use the Jarvis chat. "
                         "Agents in terminals (BYOK) don't need it."),
        })
    return api_key


def _cliente_anthropic(api_key: str):
    """Cliente AsyncAnthropic GLOBAL reutilizado entre requests: su pool httpx mantiene
    la conexión TLS viva (keep-alive) → ahorra el handshake (~100-200ms) en el
    time-to-first-token de cada mensaje. Patrón recomendado del SDK (crear una vez, reusar).
    Si la API key CAMBIA (el usuario la reconfiguró), se reconstruye → no queda cacheada la vieja."""
    global _anthropic_cliente, _anthropic_key
    if _anthropic_cliente is None or _anthropic_key != api_key:
        _anthropic_cliente = anthropic.AsyncAnthropic(api_key=api_key, timeout=60.0)
        _anthropic_key = api_key
    return _anthropic_cliente


def _bloque_memoria_para_orden(project_ruta: str, mensaje: str) -> str:
    """Memorias relevantes al PEDIDO del usuario, para que el orquestador
    planifique los agentes esquivando los errores conocidos (la memoria entra al
    planning, no solo al prompt del agente). Determinista, cero API. '' si no
    hay proyecto o nada relevante — degrada sin romper el chat."""
    if not project_ruta:
        return ''
    try:
        from plotspace.core.memoria_recall import relevantes, usos_registrados
        # Las rutas que nombra el pedido son la señal MÁS fuerte del recall (y
        # la única que despierta una lápida): antes se pasaba [] siempre.
        rel = relevantes(project_ruta, _rutas_en_texto(mensaje), mensaje or '',
                         k=5, usos=usos_registrados())
    except Exception as e:
        print(f'[orquestador] recall de memorias falló: {e}')
        return ''
    if not rel:
        return ''
    lineas = ["[Memoria relevante al pedido — tenela en cuenta al planear "
              "(reglas, lápidas de features eliminados, gotchas):]"]
    for m in rel:
        marca = ' ⚰️LÁPIDA(no reintroducir)' if m['estado'] == 'lapida' else ''
        lineas.append(f"  • {m['titulo']}{marca} — .jarvis/memory/{m['slug']}.md")
        if m.get('resumen') and m['resumen'].lower() != m['titulo'].lower():
            lineas.append(f"      ↳ {m['resumen']}")
    return '\n'.join(lineas)


_RUTA_EN_TEXTO_RE = re.compile(
    r'(?<![\w@])((?:[\w.-]+/)+[\w.-]+|[\w-]+\.(?:py|js|ts|tsx|jsx|css|html|md|json|'
    r'toml|yaml|yml|sh|go|rs|java|rb|php|vue|svelte|sql))(?![\w/])')


def _rutas_en_texto(texto: str) -> list:
    """Paths que el usuario nombró ('frontend/shell/workspace.js', 'main.py')."""
    vistos = []
    sin_urls = re.sub(r'\w+://\S+', ' ', texto or '')
    for m in _RUTA_EN_TEXTO_RE.findall(sin_urls):
        if m.startswith('./'):
            m = m[2:]
        if '://' in m or m in vistos:
            continue
        vistos.append(m)
    return vistos[:12]


def _formatear_dev_servers(project_id: int) -> str:
    """TODOS los dev servers vivos del proyecto con la terminal que los
    levantó (antes se veía una sola URL, sin dueño)."""
    try:
        from plotspace.core.dev_detect import servers_detectados
        servers = servers_detectados(project_id)
    except Exception:
        return ''
    lineas = []
    for sv in servers[:10]:
        quien = (f" — levantado por #{sv['terminal_id']} {sv.get('terminal_nombre') or ''}"
                 if sv.get('terminal_id') else '')
        lineas.append(f"  - {sv['url']}{quien}".rstrip())
    return '\n'.join(lineas)


async def _preparar_contexto_chat(req):
    """Contexto COMPARTIDO por /chat y /chat-stream: trae el proyecto + terminales,
    arma el prompt con contexto, valida la API key y devuelve el cliente Anthropic.
    Lanza HTTPException 404/500 igual que antes (sin cambios de comportamiento)."""
    if getattr(req, 'lang', None):
        idioma_ui.fijar(req.lang)
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM projects WHERE id = ?', (req.project_id,))
        project = cursor.fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="Proyecto no encontrado")
        project = dict(project)

        cursor.execute(
            'SELECT * FROM terminals WHERE project_id = ? AND activa = 1 ORDER BY fecha_creacion ASC',
            (req.project_id,)
        )
        terminals_activas = [dict(t) for t in cursor.fetchall()]
    finally:
        conn.close()

    # Armado del contexto: la foto COMPLETA del enjambre y del proyecto
    # (core/orq_contexto): estado vivo de cada terminal con su pantalla, git,
    # guía del proyecto, eventos y coordinación.
    try:
        from plotspace.core import orq_contexto
        bloques = await orq_contexto.construir_bloques(project, terminals_activas)
    except Exception as e:
        print(f'[orquestador] contexto enriquecido falló (sigo con lo mínimo): {e}')
        bloques = [f"[Estado actual]\nTerminales activas: {len(terminals_activas)} — "
                   + ', '.join(f"ID {t['id']}: {t['nombre']} ({t['tipo_ia']})"
                               for t in terminals_activas)]

    # Mapa del repo: el orquestador deja de adivinar rutas — planifica con las
    # carpetas REALES del proyecto (determinista, cacheado, degrada a nada).
    try:
        from plotspace.core.repo_map import bloque_mapa
        mapa = bloque_mapa(project.get('ruta') or '')
    except Exception as e:
        print(f'[orquestador] repo_map falló (sigo sin mapa): {e}')
        mapa = ''
    if mapa:
        bloques.append(f"[Mapa del proyecto]\n{mapa}")

    preview_url = _preview_url_activo(req.project_id)
    if preview_url:
        bloques.append(
            f"[Preview activo]\n  Servidor de preview corriendo en {preview_url}\n"
            f"  Para apagarlo usá la action 'stop_preview' (NO close_terminal — "
            f"el preview NO es una terminal)."
        )
    dev_str = _formatear_dev_servers(req.project_id)
    if dev_str:
        bloques.append(f"[Dev servers]\n{dev_str}")

    skills_str = _formatear_skills_activas(req.project_id)
    if skills_str:
        bloques.append(f"[Skills activas]\n{skills_str}")

    mem_str = _bloque_memoria_para_orden(project.get('ruta'), req.message)
    if mem_str:
        bloques.append(mem_str)

    bloques.append(_bloque_idioma())
    bloques.append(f"[Orden]\n{req.message}")
    msg_con_ctx = "\n\n".join(bloques)

    if ORQUESTADOR_MOTOR == 'api':
        api_key = _guard_api_key()
        if req.image_base64:
            user_content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": req.media_type or "image/jpeg",
                        "data": req.image_base64,
                    },
                },
                {"type": "text", "text": msg_con_ctx},
            ]
        else:
            user_content = msg_con_ctx
        # Cliente GLOBAL reutilizado: su pool httpx mantiene la conexión TLS
        # viva (keep-alive) → ahorra el handshake (~100-200ms) por mensaje.
        client = _cliente_anthropic(api_key)
    else:
        # Modo suscripción (claude -p): sin API key ni cliente. La imagen no
        # viaja inline — se guarda a archivo y el orquestador la mira con Read.
        _guard_cli()
        if req.image_base64:
            ruta_img = _guardar_imagen_temporal(req.image_base64, req.media_type)
            if ruta_img:
                msg_con_ctx += (f"\n\n[Imagen adjunta del usuario guardada en "
                                f"{ruta_img} — mirala con Read antes de responder]")
        user_content = msg_con_ctx
        client = None

    # Memoria conversacional: los turnos previos del thread (saneados) preceden
    # al mensaje actual — "ahora agregale X" por fin tiene ancla.
    mensajes = _mensajes_con_historial(req.historial, user_content)
    return project, terminals_activas, mensajes, client


_TITULO_BLOQUE_RE = re.compile(r'^\[([^\]\n]{2,80})\]', re.M)


def _titulos_contexto(mensajes: list) -> list:
    """Nombres de los bloques de contexto del mensaje actual ('Estado actual',
    'Proyecto', …) — para mostrarle al usuario qué está mirando el orquestador."""
    if not mensajes:
        return []
    contenido = mensajes[-1].get('content')
    if isinstance(contenido, list):
        contenido = '\n'.join(b.get('text', '') for b in contenido if isinstance(b, dict))
    titulos = []
    for t in _TITULO_BLOQUE_RE.findall(contenido or ''):
        t = t.split(' — ')[0].rstrip(':').strip()
        if t and t != 'Orden' and t not in titulos:
            titulos.append(t)
    return titulos


def _guard_cli():
    """409 estructurado si el CLI `claude` no está disponible (modo
    suscripción). Espejo de _guard_api_key para el motor nuevo."""
    import shutil
    if not shutil.which('claude'):
        raise HTTPException(status_code=409, detail={
            "error": "no_cli",
            "message": L("El orquestador corre con tu suscripción de Claude vía "
                         "el CLI `claude`, pero no lo encuentro en el PATH. "
                         "Instalalo y logueá tu cuenta (o usá ORQUESTADOR_MOTOR=api).",
                         "The orchestrator runs on your Claude subscription through "
                         "the `claude` CLI, but I can't find it in PATH. "
                         "Install it and log in (or use ORQUESTADOR_MOTOR=api)."),
        })


def _guardar_imagen_temporal(image_base64: str, media_type: str = None):
    """Imagen del chat → archivo temporal legible por el Read del orquestador.
    None si no se pudo (la llamada sigue sin imagen, jamás rompe el chat)."""
    import base64
    import tempfile
    try:
        ext = {'image/png': '.png', 'image/jpeg': '.jpg',
               'image/webp': '.webp', 'image/gif': '.gif'}.get(media_type or '', '.png')
        fd, ruta = tempfile.mkstemp(prefix='jarvis-orq-img-', suffix=ext)
        with os.fdopen(fd, 'wb') as f:
            f.write(base64.b64decode(image_base64))
        return ruta
    except Exception as e:
        print(f'[orquestador] no pude guardar la imagen adjunta: {e}')
        return None


async def _consultar_cli(mensajes: list, project: dict):
    """Una consulta completa al motor suscripción (drena el stream). Devuelve
    (raw_text, usage_namespace, stop_reason) con la MISMA forma que espera
    _procesar_respuesta_orquestador — el pipeline de parseo/ejecución no
    distingue motores."""
    from types import SimpleNamespace
    from plotspace.core import orq_cli
    prompt = orq_cli.prompt_desde_mensajes(mensajes)
    resultado = None
    async for ev in orq_cli.stream(prompt, _system_prompt_cli(), ORQUESTADOR_MODEL,
                                   cwd=(project.get('ruta') or None),
                                   schema=RESPONDER_SCHEMA):
        if ev['tipo'] == 'resultado':
            resultado = ev
    if resultado is None or resultado['error']:
        detalle = (resultado or {}).get('texto') or 'sin resultado'
        raise orq_cli.OrqCliError(f'el CLI terminó con error: {detalle[:300]}')
    usage = SimpleNamespace(input_tokens=resultado['input_tokens'],
                            output_tokens=resultado['output_tokens'])
    return resultado['texto'], usage, 'end_turn'


@router.post("/chat")
async def chat_orquestador(req: ChatRequest):
    """Procesa mensaje del usuario, llama a Claude y ejecuta sus acciones."""
    project, terminals_activas, mensajes, client = await _preparar_contexto_chat(req)

    if ORQUESTADOR_MOTOR != 'api':
        # Motor SUSCRIPCIÓN (default): claude -p con la cuenta OAuth activa.
        from plotspace.core.orq_cli import OrqCliError
        try:
            raw_text, usage, stop = await _consultar_cli(mensajes, project)
        except OrqCliError as e:
            raise HTTPException(status_code=502, detail=L("Orquestador (suscripción): ", "Orchestrator (subscription): ") + str(e))
        return await _procesar_respuesta_orquestador(
            raw_text, usage, project, req, terminals_activas, stop_reason=stop)

    try:
        response = await client.messages.create(
            model=ORQUESTADOR_MODEL,
            max_tokens=16000,
            system=_system_con_cache(),
            output_config=_FORMATO_SALIDA_API,
            messages=mensajes,
        )
        raw_text = _texto_respuesta(response)
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=401, detail=L("ANTHROPIC_API_KEY inválida", "Invalid ANTHROPIC_API_KEY"))
    except Exception as e:
        raise HTTPException(status_code=502, detail=L("Error de la API de Claude: ", "Claude API error: ") + str(e))

    # Post-procesado (parse JSON + ejecutar actions + uso + STATE.md) en un
    # helper COMPARTIDO con /chat-stream, así no hay drift en la lógica crítica.
    return await _procesar_respuesta_orquestador(
        raw_text, getattr(response, 'usage', None), project, req, terminals_activas,
        stop_reason=getattr(response, 'stop_reason', None))


@router.post("/chat-stream")
async def chat_orquestador_stream(req: ChatRequest):
    """Igual que /chat pero STREAMEA el 'message' token a token (SSE) para que la
    respuesta aparezca en vivo. Las actions se ejecutan AL FINAL con el
    MISMO helper que /chat (cero drift). Si el front falla con esto, cae a /chat."""
    project, terminals_activas, mensajes, client = await _preparar_contexto_chat(req)

    async def gen():
        def sse(obj):
            return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"

        # Qué vio el orquestador (los bloques de contexto de este turno): el
        # usuario lo ve mientras espera, en vez de tres puntitos mudos.
        yield sse({"type": "contexto", "bloques": _titulos_contexto(mensajes)})

        if ORQUESTADOR_MOTOR != 'api':
            # Motor SUSCRIPCIÓN: streamear el 'message' desde los deltas del
            # claude -p. En 'reinicio' (mensaje nuevo del asistente, p.ej. tras
            # una tool de lectura) el acumulador arranca de cero: solo el
            # ÚLTIMO mensaje es la respuesta JSON.
            from types import SimpleNamespace
            from plotspace.core import orq_cli
            raw_cli, emitido_cli, resultado = "", 0, None
            try:
                prompt = orq_cli.prompt_desde_mensajes(mensajes)
                async for ev in orq_cli.stream(
                        prompt, _system_prompt_cli(), ORQUESTADOR_MODEL,
                        cwd=(project.get('ruta') or None),
                        schema=RESPONDER_SCHEMA):
                    if ev['tipo'] == 'reinicio':
                        # El cliente también resetea: antes seguía acumulando
                        # y el texto se veía duplicado hasta el 'done'.
                        if emitido_cli:
                            yield sse({"type": "reinicio"})
                        raw_cli, emitido_cli = "", 0
                    elif ev['tipo'] == 'herramienta':
                        yield sse({"type": "progreso", "herramienta": ev['nombre'],
                                   "detalle": ev['detalle']})
                    elif ev['tipo'] == 'delta':
                        raw_cli += ev['texto']
                        msg = _extraer_message_parcial(raw_cli)
                        if len(msg) > emitido_cli:
                            yield sse({"type": "token", "chunk": msg[emitido_cli:]})
                            emitido_cli = len(msg)
                    elif ev['tipo'] == 'resultado':
                        resultado = ev
            except orq_cli.OrqCliError as e:
                yield sse({"type": "error", "detail": L("Orquestador (suscripción): ", "Orchestrator (subscription): ") + str(e)})
                return
            if resultado is None or resultado['error']:
                yield sse({"type": "error",
                           "detail": L("el CLI terminó sin resultado utilizable", "the CLI finished without a usable result")})
                return
            usage_cli = SimpleNamespace(input_tokens=resultado['input_tokens'],
                                        output_tokens=resultado['output_tokens'])
            try:
                # Blindado: si el usuario cancela o se corta la conexión a mitad
                # de las acciones, un enjambre a medio spawnear queda peor que
                # uno terminado — las acciones se completan igual.
                res = await asyncio.shield(_en_fondo(_procesar_respuesta_orquestador(
                    resultado['texto'], usage_cli, project, req,
                    terminals_activas, stop_reason='end_turn')))
            except Exception as e:
                yield sse({"type": "error", "detail": L("Error procesando la respuesta: ", "Error processing the response: ") + str(e)})
                return
            yield sse({"type": "done", **res})
            return

        raw = ""
        emitido = 0          # nº de chars del 'message' ya enviados al cliente
        usage = None
        stop = None          # stop_reason del modelo (para descartar tareas truncadas)
        try:
            async with client.messages.stream(
                model=ORQUESTADOR_MODEL,
                max_tokens=16000,
                system=_system_con_cache(),
                output_config=_FORMATO_SALIDA_API,
                messages=mensajes,
            ) as stream:
                # Structured outputs: el JSON llega como texto ({"message": ...}
                # primero) → _extraer_message_parcial lo streamea igual que en el CLI.
                async for texto in stream.text_stream:
                    raw += texto
                    msg = _extraer_message_parcial(raw)
                    if len(msg) > emitido:
                        yield sse({"type": "token", "chunk": msg[emitido:]})
                        emitido = len(msg)
                final = await stream.get_final_message()
                usage = getattr(final, "usage", None)
                stop = getattr(final, "stop_reason", None)
                raw = _texto_respuesta(final)
        except anthropic.AuthenticationError:
            yield sse({"type": "error", "detail": L("ANTHROPIC_API_KEY inválida", "Invalid ANTHROPIC_API_KEY")})
            return
        except Exception as e:
            yield sse({"type": "error", "detail": L("Error de la API de Claude: ", "Claude API error: ") + str(e)})
            return

        # Ejecutar actions + uso + STATE.md (idéntico a /chat).
        try:
            resultado = await asyncio.shield(_en_fondo(_procesar_respuesta_orquestador(
                raw, usage, project, req, terminals_activas, stop_reason=stop)))
        except Exception as e:
            yield sse({"type": "error", "detail": L("Error procesando la respuesta: ", "Error processing the response: ") + str(e)})
            return

        # Evento final con TODO lo estructurado (el cliente ejecuta/renderiza igual que /chat).
        yield sse({"type": "done", **resultado})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ELIMINADO (2026-09-12): _es_embebible + GET /preview/probe. El Browser ahora
# es server-side (core/remote_browser.py) y ya no decide nada por XFO/CSP.

@router.get("/preview/buscar")
async def preview_buscar(q: str = '', modo: str = 'yt', token: str = ''):
    """Búsqueda de la Radio (server-side, sin API keys).

    modo 'yt' parsea el ytInitialData de la página de resultados de YouTube
    vía httpx; modo 'ytmas' (token = el `token` que devolvió una tanda
    anterior) trae la tanda SIGUIENTE de esa misma búsqueda — el "mostrar más"
    de los dots de la Radio; modo 'ytrel' (q = id de video) trae los
    relacionados REALES de un video vía youtubei/v1/next — las continuaciones
    de la Radio. `modo=local` busca en la biblioteca local de música
    (core/musica_local.py, data/music) y `modo=spotify` en Spotify con el
    token del usuario (core/spotify_api.py); ambos usan `q` como filtro y
    devuelven el MISMO shape (id/url/titulo/canal/duracion/thumb, sin vistas).
    Su ÚNICO consumidor es la Radio (sections/radio/radio.js).

    (Hasta 2026-07-26 servía además los modos 'web' —DuckDuckGo scrapeado con
    el Chromium de Playwright— y 'twitch' para serp.html, el buscador viejo del
    Web Preview. Se eliminaron con él: buscar ahora es navegar a Google /
    YouTube de verdad. La ruta conserva el prefijo /preview/ por compatibilidad
    del cliente.)

    Fallas esperables (timeout, formato nuevo de YouTube) NO son 5xx: van como
    {'resultados': [], 'error': texto} para que la Radio las muestre
    amigables."""
    from plotspace.core import web_search
    consulta = (q or '').strip()
    cont = (token or '').strip()
    if modo not in ('yt', 'ytmas', 'ytrel', 'local', 'spotify'):
        raise HTTPException(status_code=400,
                            detail='modo inválido (yt|ytmas|ytrel|local|spotify)')
    if modo == 'ytmas':
        if not cont:
            raise HTTPException(status_code=400, detail='falta el parámetro token')
        if len(cont) > 4000:
            raise HTTPException(status_code=400, detail='token demasiado largo')
    elif modo in ('yt', 'ytrel', 'spotify'):
        if not consulta:
            raise HTTPException(status_code=400, detail='falta el parámetro q')
        if len(consulta) > 200:
            raise HTTPException(status_code=400, detail='consulta demasiado larga')
    elif len(consulta) > 200:
        # modo=local: sin q se lista TODO (el filtro es opcional)
        raise HTTPException(status_code=400, detail='consulta demasiado larga')
    try:
        if modo == 'yt':
            # Solo videos reproducibles EMBEBIDOS ("inline"): la Radio los
            # reproduce adentro — un resultado no-embebible es un resultado
            # roto (VEVO/sellos bloquean el embed; pedido 2026-07-08).
            pagina = await web_search.buscar_youtube_pagina(consulta)
            return {'resultados': await web_search.filtrar_embebibles(pagina['resultados']),
                    'token': pagina.get('token'), 'error': None}
        elif modo == 'ytmas':
            pagina = await web_search.buscar_youtube_mas(cont)
            return {'resultados': await web_search.filtrar_embebibles(pagina['resultados']),
                    'token': pagina.get('token'), 'error': None}
        elif modo == 'local':
            from plotspace.core import musica_local
            items = await asyncio.to_thread(musica_local.listar, '', consulta)
            return {'resultados': [{k: it[k] for k in ('id', 'url', 'titulo', 'canal',
                                                       'duracion', 'thumb')} for it in items],
                    'token': None, 'error': None}
        elif modo == 'spotify':
            from plotspace.core import spotify_api
            return {'resultados': await spotify_api.buscar(consulta),
                    'token': None, 'error': None}
        else:   # 'ytrel'
            resultados = await web_search.filtrar_embebibles(
                await web_search.relacionados_youtube(consulta))
        return {'resultados': resultados, 'error': None}
    except web_search.BusquedaError as e:
        return {'resultados': [], 'error': str(e)}
    except Exception as e:
        from plotspace.core import musica_local, spotify_api
        # Los errores catalogables de las fuentes propias (música local,
        # Spotify) van al MISMO shape que BusquedaError: {resultados, error}.
        if isinstance(e, (musica_local.MusicaError, spotify_api.SpotifyError)):
            return {'resultados': [], 'error': str(e)}
        raise


@router.get("/preview/{project_id}/servers")
async def listar_servers_preview(project_id: int):
    """Lo activo del proyecto para el menú de la barra: SOLO los localhost vivos
    (el http.server propio de Jarvis + cada dev server de agente detectado, con
    su puerto y qué terminal lo levantó). NO lista terminales/shells de agentes
    — el menú es exclusivamente de localhost detectados en los previews; las
    terminales que están trabajando NO cuentan acá (pedido del usuario
    2026-06-20)."""
    from urllib.parse import urlparse
    from plotspace.core.dev_detect import servers_detectados

    servers = []
    entry = _preview_servers.get(project_id)
    if entry:
        proc, url = entry
        if proc.poll() is None:
            servers.append({'url': url, 'port': urlparse(url).port,
                            'terminal_id': None, 'terminal_nombre': 'Jarvis', 'propio': True})
        else:
            _preview_servers.pop(project_id, None)
    for s in servers_detectados(project_id):
        servers.append({'url': s['url'], 'port': urlparse(s['url']).port,
                        'terminal_id': s.get('terminal_id'),
                        'terminal_nombre': s.get('terminal_nombre'), 'propio': False,
                        'tipo': s.get('tipo') or 'server'})

    return {'servers': servers}


@router.get("/preview/{project_id}/terminal/{terminal_id}/localhost")
async def localhost_de_terminal(project_id: int, terminal_id: int):
    """El localhost más reciente que levantó ESA terminal (server o demo) —
    lo usa el salto del Web Preview al maximizar/seleccionar la card del
    agente. Mira el snapshot vivo y, si no está (reinicio del server = estado
    en memoria perdido, o la URL scrolleó fuera de la ventana del poller),
    escanea el scrollback completo del pane con chequeo de liveness.
    {'url': None} si la terminal no tiene ningún localhost vivo."""
    from plotspace.core.dev_detect import buscar_url_de_terminal
    s = await buscar_url_de_terminal(project_id, terminal_id)
    return s or {'url': None}


def _puerto_de_url(url: str) -> Optional[int]:
    """Puerto de la url; malformado (p.ej. :99999) → 400 en vez de un 500."""
    from urllib.parse import urlparse
    try:
        return urlparse(url).port
    except ValueError:
        raise HTTPException(status_code=400, detail=f"URL con puerto inválido: {url}")


def _puertos_del_proyecto(project_id: int) -> set:
    """Puertos que el ✕ puede matar para ESTE proyecto: su http.server propio
    (si vive) + los dev servers detectados (no los demos, que no tienen proceso)."""
    from urllib.parse import urlparse
    from plotspace.core.dev_detect import servers_detectados
    urls = []
    entry = _preview_servers.get(project_id)
    if entry and entry[0].poll() is None:
        urls.append(entry[1])
    urls += [s['url'] for s in servers_detectados(project_id) if s.get('tipo') != 'demo']
    puertos = set()
    for u in urls:
        try:
            p = urlparse(u).port
        except ValueError:
            continue
        if p:
            puertos.add(p)
    return puertos


class StopPreviewBody(BaseModel):
    url: Optional[str] = None


@router.post("/preview/{project_id}/stop")
async def detener_preview(project_id: int, body: Optional[StopPreviewBody] = None):
    """Cierra un server de localhost de ese puerto (mata el proceso) y lo saca
    del pill/menú. Con `url` en el body cierra ESE server puntual (el ✕ de cada
    fila del menú de localhost); sin body cierra el más reciente (el ✕ del pill).
    El http.server propio se termina por su proceso; un dev server detectado se
    mata por el puerto. NUNCA toca el 3000 (Jarvis) — guard en matar_puerto.
    Un DEMO del propio Jarvis (:3000/static/<dir>/…) no tiene proceso: acá solo
    se OCULTA del menú (descartar) sin tocar absolutamente nada del workspace."""
    from plotspace.core.dev_detect import descartar, es_demo_jarvis
    from plotspace.core.puertos import matar_puerto

    pedida = body.url if (body and body.url) else None
    matado = None
    propio = None

    objetivo = pedida or _preview_url_activo(project_id)
    if objetivo and es_demo_jarvis(objetivo):
        descartar(project_id, objetivo)
        return {'ok': True, 'mensaje': f'Demo oculto ({objetivo})'}

    if pedida:
        port = _puerto_de_url(pedida)
        # Cerrar SOLO ese server. ¿Es el http.server propio? → por proceso.
        entry = _preview_servers.get(project_id)
        if entry and entry[1] == pedida:
            propio = _detener_preview_si_existe(project_id)
        if not propio and port:
            # Anti "mata-lo-que-sea": el body es input del cliente — solo se
            # mata un puerto que ES de este proyecto (dev server detectado o
            # su preview propio). Cualquier otro puerto no se toca.
            if port not in _puertos_del_proyecto(project_id):
                return {'ok': False,
                        'mensaje': f'El puerto {port} no es un server de este proyecto — no se cerró'}
            r = matar_puerto(port)
            if r.get('ok'):
                matado = r
        descartar(project_id, pedida)
        url = pedida
    else:
        # El ✕ del pill: el más reciente (+ el http.server propio si hay).
        url = _preview_url_activo(project_id)
        propio = _detener_preview_si_existe(project_id)
        if url:
            port = _puerto_de_url(url)
            if port:
                r = matar_puerto(port)
                if r.get('ok'):
                    matado = r
        descartar(project_id, url)

    if matado:
        return {'ok': True, 'mensaje': f'Server cerrado ({url})', 'pids': matado['pids']}
    if propio:
        return {'ok': True, 'mensaje': f'Preview detenido ({propio})'}
    if url:
        return {'ok': True, 'mensaje': f'Preview cerrado ({url})'}
    return {'ok': True, 'mensaje': 'No había preview activo'}


# ─── Entrega de tareas a los agentes ─────────────────────────────────────────

def listo_segun_fase(estado) -> bool:
    """¿El CLI ya está para recibir una tarea, según la máquina de agent_watch?

    Reemplaza al matcheo del BANNER del CLI (`'bypass permissions on'`, …), que
    es la misma fragilidad que dejó ciego al parseo de panes: el render cambia
    cada versión y la detección se rompe en silencio. La fase no depende de
    ningún texto — se calcula del movimiento del pane."""
    fase = (estado or {}).get('fase')
    return fase in ('idle', 'trabajando')


def comandos_pegar_tarea(session: str, texto: str) -> list:
    """Comandos tmux para entregar una tarea larga como PASTE, no tipeada.

    `send-keys -l` manda los saltos de línea como LF crudos al pty (verificado),
    así que un prompt largo puede fragmentarse en varios envíos. Con el buffer
    de tmux el texto viaja entero y de una; `-p` deja que TMUX decida si lo
    envuelve en bracketed paste, según lo que haya pedido la app — meter los
    escapes a mano se vería como basura en una app que no los entiende.
    `-d` borra el buffer al usarlo (si no, queda uno colgado por tarea).
    El buffer lleva un sufijo único: dos entregas concurrentes a la misma
    terminal (mailbox + asignar tarea) se pisaban el texto. Target `=sesión:`
    exacto (sin '=' tmux resuelve por prefijo: la 1 muerta pegaba en la 12)."""
    buf = f'jarvis_tarea_{session}_{uuid.uuid4().hex[:8]}'
    return [
        ['tmux', 'set-buffer', '-b', buf, '--', texto],
        ['tmux', 'paste-buffer', '-b', buf, '-t', f'={session}:', '-p', '-d'],
    ]


async def _esperar_agente_listo(terminal_id: int, timeout: float = 30.0) -> bool:
    """Espera a que el agente esté listo para recibir input.
    Detecta el trust prompt de Claude Code y lo auto-acepta.
    Devuelve True cuando ve el prompt activo del agente."""
    session = f'jarvis_{terminal_id}'
    inicio = asyncio.get_event_loop().time()
    trust_resuelto = False

    # Patrones del trust prompt de Claude Code v2.1.x. Cualquiera de estos
    # indica que estamos parados en el prompt de "¿confiás en esta carpeta?".
    TRUST_PATTERNS = (
        'quick safety check',
        'trust this folder',
        'do you trust',
        'trust the files',
        'is this a project you',
        'yes, i trust',
    )
    # Patrones de banner: quedan como RESPALDO, no como señal principal. Atar
    # la detección al render del CLI es lo que dejó ciego al parseo de panes
    # (cambia cada versión y se rompe sin que nadie se entere).
    READY_PATTERNS = (
        'bypass permissions on',
        'shift+tab to cycle',
        '/model to change',
    )

    while asyncio.get_event_loop().time() - inicio < timeout:
        await asyncio.sleep(1.0)

        # Señal PRINCIPAL: la máquina de agent_watch, que no depende de ningún
        # texto — se calcula del movimiento del pane. Cuando el CLI arrancó y se
        # asentó (o ya está produciendo), está para recibir.
        try:
            from plotspace.core import agent_watch
            if listo_segun_fase(agent_watch._estados.get(terminal_id)):
                print(f'[ready] {session}: listo por fase de agent_watch')
                await asyncio.sleep(1.5)
                return True
        except Exception:
            pass

        # Capturar las últimas líneas del pane (por el motor: en Windows no
        # hay tmux y sin esto el arranque de cada agente se colgaba esperando
        # un prompt que nunca podía ver).
        texto = (await backend().capturar_async(terminal_id, 40)).lower()

        # Trust prompt: la opción 1 ya viene seleccionada por default ("Yes, I
        # trust this folder ✓"), así que basta con Enter para confirmar.
        if not trust_resuelto and any(p in texto for p in TRUST_PATTERNS):
            print(f'[ready] {session}: trust prompt detectado, mandando Enter')
            backend().enviar_tecla(terminal_id, 'Enter')
            trust_resuelto = True
            await asyncio.sleep(2.5)  # esperar que Claude inicialice tras el trust
            continue

        if any(p in texto for p in READY_PATTERNS):
            # Margen extra: aún viendo el banner, Claude Code puede tardar
            # 1-2s en aceptar input via tmux send-keys.
            print(f'[ready] {session}: agente listo, dando 2s de gracia antes del send')
            await asyncio.sleep(2.0)
            return True

    print(f'[ready] {session}: timeout, voy a intentar el send igual')
    return False


def _objetivo_orquestacion(mensaje, tope: int = 120) -> str:
    """Etiqueta corta del objetivo de una orden (la orden del chat en UNA línea,
    recortada): agrupa en el monitor de Tasks a los agentes lanzados por ella. PURA."""
    t = ' '.join(str(mensaje or '').split())
    return t if len(t) <= tope else t[:tope - 1].rstrip() + '…'


def _tarea_para_agente(tarea: str, archivos: list, terminal_id: int,
                       project_ruta: str = None) -> str:
    """El prompt que recibe un agente que Jarvis lanzó con tarea: la tarea del
    plan + su territorio exclusivo + las memorias relevantes (recall
    determinista, cero API) + sus mensajes pendientes del mailbox + el cierre
    estructurado (sentinel). Cada agregado degrada a nada si falla."""
    texto = (tarea or '').strip()
    if archivos:
        texto += ("\n\nARCHIVOS DE TU PROPIEDAD EXCLUSIVA (no toques nada fuera de "
                  f"esto): {', '.join(archivos)}. Si necesitás modificar otro archivo, "
                  "cerrá como blocked explicando cuál y por qué.")
    if project_ruta:
        try:
            from plotspace.core.memoria_recall import bloque_relevantes
            texto += bloque_relevantes(project_ruta, archivos, texto,
                                       terminal_id=terminal_id)
        except Exception as e:
            print(f'[agente] recall de memorias falló (sigo sin bloque): {e}')
    try:
        from plotspace.core.mailbox import bloque_pendientes_para_tarea
        texto += bloque_pendientes_para_tarea(terminal_id)
    except Exception as e:
        print(f'[agente] mailbox pendientes falló (sigo sin bloque): {e}')
    return texto + _cierre_prompt_directo(terminal_id)


def _ruta_proyecto_o_none(project_id: int):
    """Ruta del proyecto. None si falla — el caller degrada, jamás rompe."""
    try:
        conn = get_db()
        try:
            row = conn.execute('SELECT ruta FROM projects WHERE id = ?', (project_id,)).fetchone()
            return row['ruta'] if row else None
        finally:
            conn.close()
    except Exception:
        return None


def _registrar_origen(terminal_id: int, tarea: str, orquestacion: str,
                      ts: str = None) -> None:
    """Marca la terminal como agente de Jarvis: origen='jarvis' + la tarea que
    se le entregó + el objetivo de la orden. Lo lee el monitor de Tasks.
    Best-effort (un fallo de DB no frena la entrega)."""
    ts = ts or datetime.now().isoformat(timespec='seconds')
    try:
        conn = get_db()
        try:
            conn.execute(
                "UPDATE terminals SET origen = 'jarvis', tarea = ?, orquestacion = ?, "
                "orquestacion_ts = ? WHERE id = ?",
                ((tarea or '').strip(), orquestacion or '', ts, terminal_id))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f'[agente] no pude registrar el origen de #{terminal_id}: {e}')


async def _avisar_agentes(project_id: int) -> None:
    """WS `agentes_update`: el monitor en vivo de Tasks re-lee los agentes."""
    try:
        from plotspace.core.events import broadcaster
        await broadcaster.broadcast(project_id, {"type": "agentes_update"})
    except Exception as e:
        print(f'[agente] broadcast agentes_update falló: {e}')


def _comando_cli_agente(ia_type: str, session_uuid: str = None):
    """Comando del CLI de un agente autónomo, lanzado como PROGRAMA del pane
    (sin eco): claude/qwen con su id determinista (revive con su contexto tras
    un reboot) y claude con --dangerously-skip-permissions (nadie aprueba
    permisos de un agente que trabaja solo). None para manual/shell. PURA."""
    from plotspace.routers.terminals import _comando_lanzamiento
    cmd = _comando_lanzamiento(ia_type, session_uuid, False, False)
    if cmd and ia_type == 'claude':
        cmd += ' --dangerously-skip-permissions'
    return cmd


async def _lanzar_agente_con_tarea(project_id: int, terminal: dict, tarea: str,
                                   archivos: list, orquestacion: str,
                                   orquestacion_ts: str = None) -> None:
    """spawn_terminal CON tarea: registra el origen, reclama el territorio,
    crea la sesión tmux ya corriendo el CLI y deja en background la entrega
    de la tarea (espera a que el CLI esté listo y la pega)."""
    tid = terminal['id']
    await asyncio.to_thread(_registrar_origen, tid, tarea, orquestacion, orquestacion_ts)
    ruta = await asyncio.to_thread(_ruta_proyecto_o_none, project_id)

    # El agente arranca con su TERRITORIO ya tomado: el momento más barato para
    # evitar un choque es cuando todavía no existe. Lo ajeno se informa y se
    # sigue igual (el reparto es una guía, no un candado).
    if archivos:
        try:
            from plotspace.core import territorio
            r = territorio.reclamar(project_id, tid, terminal.get('nombre') or '', archivos)
            if r['ocupados']:
                print(f'[agente] #{tid}: ya tienen dueño → '
                      + ', '.join(f"{o['patron']} ({o['de']})" for o in r['ocupados']))
        except Exception as e:
            print(f'[agente] no pude reclamar el territorio de #{tid}: {e}')

    try:
        from plotspace.routers.terminals import _preparar_proyecto, _crear_sesion_tmux
        if ruta:
            await _preparar_proyecto(ruta, project_id=project_id)
        cmd = _comando_cli_agente(terminal.get('tipo_ia') or '', terminal.get('session_uuid'))
        await _crear_sesion_tmux(tid, ruta or '', comando_cli=cmd)
    except Exception as e:
        print(f'[agente] no pude crear la sesión de #{tid}: {e}')

    texto = _tarea_para_agente(tarea, archivos, tid, project_ruta=ruta)
    _en_fondo(_entregar_tarea_cuando_listo(project_id, tid, terminal.get('nombre') or '',
                                           texto))
    _logs.evento('tarea_jarvis', terminal_id=tid, project_id=project_id,
                 archivos=len(archivos or []))


async def _entregar_tarea_cuando_listo(project_id: int, terminal_id: int,
                                       nombre: str, texto: str) -> bool:
    """Espera a que el CLI esté listo (auto-acepta el trust prompt) y le pega
    la tarea. Si no llega, avisa en el chat. Devuelve si se entregó."""
    await _esperar_agente_listo(terminal_id, timeout=30.0)
    ok = (await send_to_agent(terminal_id, texto)) is not False
    if not ok:
        try:
            from plotspace.core.events import broadcaster
            await broadcaster.broadcast(project_id, {
                "type": "orquestador_mensaje",
                "message": L(f"⚠️ No pude entregarle la tarea a {nombre or f'#{terminal_id}'} "
                             "(su sesión no responde).",
                             f"⚠️ I couldn't deliver the task to {nombre or f'#{terminal_id}'} "
                             "(its session isn't responding)."),
            })
        except Exception:
            pass
    await _avisar_agentes(project_id)
    return ok


def _nombre_terminal(terminal_id: int) -> str:
    conn = get_db()
    try:
        row = conn.execute('SELECT nombre FROM terminals WHERE id = ?',
                           (terminal_id,)).fetchone()
        return row['nombre'] if row else f'Terminal #{terminal_id}'
    finally:
        conn.close()


def _insertar_task_event(terminal_id: int, event: str, project_id: int,
                         motivo: str = '') -> None:
    conn = get_db()
    try:
        conn.execute(
            'INSERT INTO task_events (terminal_id, project_id, event, timestamp, motivo) '
            'VALUES (?, ?, ?, ?, ?)',
            (terminal_id, project_id, event, datetime.now().isoformat(), (motivo or None)))
        conn.commit()
    finally:
        conn.close()


async def procesar_task_event_interno(terminal_id: int, event: str, project_id: int,
                                      motivo: str = ''):
    """Un cierre de tarea (TASK_DONE/BLOCKED/ERROR) de una terminal: lo PERSISTE
    en task_events (con su motivo — la materia prima de las lecciones) y lo
    broadcastea como `task_event` (el monitor de Tasks y los avisos del front).
    Lo llaman el monitor de keywords (terminals.py) y el sentinel."""
    from plotspace.core.events import broadcaster
    try:
        await asyncio.to_thread(_insertar_task_event, terminal_id, event, project_id, motivo)
    except Exception as e:
        print(f'[task_event] no pude persistir {event} de #{terminal_id}: {e}')
    try:
        term_nombre = await asyncio.to_thread(_nombre_terminal, terminal_id)
    except Exception:
        term_nombre = f'Terminal #{terminal_id}'
    await broadcaster.broadcast(project_id, {
        "type":            "task_event",
        "event":           event,
        "terminal_id":     terminal_id,
        "terminal_nombre": term_nombre,
        "motivo":          (motivo or '')[:300],
    })


async def send_to_agent(terminal_id: int, mensaje: str, crudo: bool = False) -> bool:
    """Envía un texto al agente via tmux (paste). Devuelve True SOLO si el
    pegado llegó al pane (los callers que no lo necesitan lo ignoran).

    `crudo=True` (respuesta a una pregunta en pantalla): el texto va tal cual,
    sin el "Lee tu CLAUDE.md…" y con UN solo Enter — el segundo Enter a
    ciegas podía confirmar la opción por defecto de la siguiente pregunta."""
    if not crudo:
        try:
            from plotspace.core import orq_contexto
            orq_contexto.registrar_envio(terminal_id, mensaje)
        except Exception:
            pass
        mensaje = f'Lee tu CLAUDE.md primero. Luego: {mensaje}'
    session = f'jarvis_{terminal_id}'
    print(f'[send_to_agent] → {session}: {mensaje[:100]}')

    # Verificar que la sesión existe antes de enviar
    if not backend().existe(terminal_id):
        print(f'[send_to_agent] ERROR: sesión {session} no existe — abortando')
        return False

    # La tarea viaja como PASTE (buffer de tmux), no tipeada. `send-keys -l`
    # manda los saltos de línea como LF crudos al pty —verificado—, así que un
    # prompt largo (memorias + mailbox + protocolo de cierre) podía fragmentarse
    # en varios envíos. El buffer lo entrega entero y de una, y `-p` deja que
    # TMUX decida si envolverlo en bracketed paste según lo que pidió la app.
    # Bonus: el texto nunca pasa por el lookup de nombres de tecla, así que una
    # línea del MAILBOX con 'Enter' o 'C-c' adentro no se interpreta como tecla.
    # subprocess.run en un thread (regla del repo: nada de
    # create_subprocess_exec para tmux — `communicate()` puede no volver nunca)
    # y con timeout, para que un tmux trabado no cuelgue el dispatch.
    pegado = True
    for argv in comandos_pegar_tarea(session, mensaje):
        try:
            r = await asyncio.to_thread(
                subprocess.run, argv, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, timeout=5)
            rc, e = r.returncode, r.stderr or b''
        except subprocess.TimeoutExpired:
            rc, e = -1, b'timeout'
        if rc != 0:
            pegado = False
            print(f'[send_to_agent] ERROR rc={rc}: '
                  f'{e.decode(errors="replace").strip()}')
            break
    else:
        print(f'[send_to_agent] OK → {session} ({len(mensaje)} chars pegados)')
    if pegado:
        # Enter solo si el texto llegó: sin pegado, un Enter a ciegas manda
        # lo que el usuario tuviera a medio escribir en el prompt.
        await asyncio.to_thread(backend().enviar_tecla, terminal_id, 'Enter')
        if not crudo:
            # Esperar 1s y enviar Enter adicional para que Claude procese la tarea
            await asyncio.sleep(1)
            await asyncio.to_thread(backend().enviar_tecla, terminal_id, 'Enter')

    if not pegado:
        return False   # nada llegó al agente: no registrar un SENT que no ocurrió

    # Registrar en task_events con el project_id REAL de la terminal (antes se
    # insertaba 0 fijo → filas corruptas que no matcheaban ningún proyecto).
    ahora = datetime.now().isoformat()
    try:
        conn = get_db()
        try:
            fila = conn.execute(
                'SELECT project_id FROM terminals WHERE id = ?', (terminal_id,)
            ).fetchone()
            pid = fila['project_id'] if fila else 0
            conn.execute(
                'INSERT INTO task_events (terminal_id, project_id, event, timestamp) '
                'VALUES (?, ?, ?, ?)',
                (terminal_id, pid, 'SENT', ahora)
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
    return True


# ─── Preview server (auto-launch cuando hay frontend) ─────────────────────────

# Registro: project_id → (subprocess.Popen, url)
_preview_servers: dict = {}


def _preview_url_activo(project_id: int) -> Optional[str]:
    """Devuelve la URL del preview si está corriendo para este proyecto.
    Prioridad: http.server lanzado por Jarvis → dev server de un agente
    detectado por el poller (plotspace/core/dev_detect.py)."""
    entry = _preview_servers.get(project_id)
    if entry:
        proc, url = entry
        if proc.poll() is None:
            return url
        _preview_servers.pop(project_id, None)
    from plotspace.core.dev_detect import url_detectada
    return url_detectada(project_id)


def _detener_preview_si_existe(project_id: int) -> Optional[str]:
    """Mata el http.server del proyecto si está corriendo. Devuelve la URL
    que se detuvo, o None si no había nada activo."""
    entry = _preview_servers.pop(project_id, None)
    if not entry:
        return None
    proc, url = entry
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try: proc.kill()
        except Exception: pass
    print(f'[preview] Detenido {url}')
    return url


# ─── Helpers internos ──────────────────────────────────────────────────────────

def _formatear_skills_activas(project_id: int) -> str:
    """Skills + plugins activos del proyecto, formateados como bullet list.
    Devuelve string vacío si no hay nada activo."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT nombre, descripcion FROM project_skills '
            'WHERE project_id = ? AND activa = 1 ORDER BY created_at ASC',
            (project_id,)
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    if not rows:
        return ""
    lineas = []
    for r in rows:
        nombre = r['nombre']
        # Si nombre contiene '@' es un plugin (ej: superpowers@claude-plugins-official)
        if '@' in nombre:
            plugin_id = nombre.split('@')[0]
            desc = (r['descripcion'] or '').strip()
            lineas.append(f"  - 🔌 {plugin_id}" + (f" — {desc}" if desc else ""))
        else:
            desc = (r['descripcion'] or '').strip()
            lineas.append(f"  - 📋 {nombre}" + (f" — {desc}" if desc else ""))
    return "\n".join(lineas)


async def _spawn_terminales(project_id: int, name: str, ia_type: str,
                             count: int, count_actual: int) -> list:
    # Lazy import (circular orchestrator ↔ terminals, patrón documentado).
    # resolver_nombre_unico: ANTES se numeraba por CONTEO de activas
    # (count_actual + i + 1) — al borrar terminales los números se reusaban
    # y nacían homónimas ("Claude Code #3" ×2): el mailbox y los permisos
    # de Agents Live resuelven por nombre, así que la coordinación entera
    # quedaba ambigua.
    from plotspace.routers.terminals import (resolver_nombre_unico, _nombres_activos,
                                             _session_uuid_para)

    # Alineado con los CLIs reales del producto (terminals._COMANDOS_CLI):
    # antes antigravity/opencode/qwen caían a "manual" y el pane nacía SIN CLI.
    ia_types_validos = {"claude", "codex", "gemini", "opencode", "qwen",
                        "antigravity", "grok", "manual"}
    if ia_type not in ia_types_validos:
        ia_type = "manual"

    creadas = []
    conn    = get_db()
    try:
        cursor = conn.cursor()
        nombres = _nombres_activos(cursor, project_id)
        for i in range(count):
            if count_actual + i >= MAX_TERMINALES:
                break
            numero = count_actual + i + 1
            deseado = f"{name} #{numero}" if (count > 1 or count_actual > 0) else name
            nombre = resolver_nombre_unico(nombres, deseado)
            nombres.append(nombre)
            ahora  = datetime.now().isoformat()
            # session_uuid como crear_terminal: claude/qwen con id determinista
            # (revive con su conversación tras un reboot).
            cursor.execute(
                'INSERT INTO terminals (project_id, nombre, tipo_ia, activa, fecha_creacion, session_uuid) '
                'VALUES (?, ?, ?, 1, ?, ?)',
                (project_id, nombre, ia_type, ahora, _session_uuid_para(ia_type)),
            )
            conn.commit()
            tid = cursor.lastrowid
            cursor.execute('SELECT * FROM terminals WHERE id = ?', (tid,))
            creadas.append(dict(cursor.fetchone()))
    finally:
        conn.close()

    # Push instantáneo a Agents Live: los agentes que spawnea el orquestador
    # (spawn_terminal) aparecen YA en la pestaña Live, sin esperar
    # el backstop de 2s ni a que toquen un archivo. Fire-and-forget (no propaga).
    if creadas:
        from plotspace.core import agent_live
        await agent_live.publicar_roster(project_id)

    return creadas


# ─── Guardas de cierre (auditoría 2026-07-02) ─────────────────────────────────
# El JSON de acciones viene de haiku SIN confirmación: un close_all/close_terminal
# no puede matar un agente A MITAD DE TAREA por accidente (horas de trabajo sin
# commitear). Regla: si la víctima está 'trabajando' se niega la PRIMERA vez con
# motivo; si el usuario REPITE el pedido dentro de la ventana, se ejecuta (el
# guard es contra el accidente, no contra el usuario).

_INSISTENCIA_CIERRE_S = 600
_cierres_rechazados: dict = {}    # ('t', tid) | ('all', project_id) → monotonic


def _sellar_rechazo_cierre(clave, ts: float = None):
    _cierres_rechazados[clave] = time.monotonic() if ts is None else ts


def _insistencia_cierre(clave, ahora: float = None) -> bool:
    t = _cierres_rechazados.get(clave)
    if t is None:
        return False
    ahora = time.monotonic() if ahora is None else ahora
    return (ahora - t) < _INSISTENCIA_CIERRE_S


def _fase_terminal(terminal_id: int) -> str:
    """Fase según agent_watch ('trabajando'/'idle'/'arrancando'); '' sin estado.
    Defensivo: cualquier error cuenta como 'sin estado' (no bloquea el cierre)."""
    try:
        from plotspace.core import agent_watch
        return (agent_watch._estados.get(terminal_id) or {}).get('fase') or ''
    except Exception:
        return ''


async def _cerrar_todas(project_id: int) -> list:
    """Cierra las terminales activas del proyecto. TODO-O-NADA: si alguna está
    'trabajando' (y no hay insistencia previa) no se cierra NINGUNA y se
    devuelven los nombres bloqueantes — `closed_all` borra todas las cards en el
    frontend, así que un cierre parcial desincronizaría la UI. Devuelve [] si
    cerró (el caller usa la lista para armar el aviso y el flag closed_all)."""
    from plotspace.routers.terminals import teardown_terminal
    conn = get_db()
    try:
        rows = [dict(r) for r in conn.execute(
            'SELECT id, nombre FROM terminals WHERE project_id = ? AND activa = 1',
            (project_id,)
        ).fetchall()]
    finally:
        conn.close()
    if not rows:
        return []

    clave = ('all', project_id)
    if not _insistencia_cierre(clave):
        trabajando = [r['nombre'] for r in rows if _fase_terminal(r['id']) == 'trabajando']
        if trabajando:
            _sellar_rechazo_cierre(clave)
            _logs.evento('cierre_rechazado', nivel='warn', project_id=project_id,
                         accion='close_all', trabajando=trabajando)
            return trabajando
    _cierres_rechazados.pop(clave, None)

    # IDs ANTES de marcarlas inactivas: hay que matarles la sesión tmux
    # (antes solo se ponía activa=0 y los agentes seguían vivos para siempre).
    conn = get_db()
    try:
        conn.execute(
            'UPDATE terminals SET activa = 0 WHERE project_id = ? AND activa = 1',
            (project_id,)
        )
        conn.commit()
    finally:
        conn.close()
    for r in rows:
        await teardown_terminal(r['id'])
    return []


async def _cerrar_terminal(terminal_id: int, project_id: int = None):
    """Cierra una terminal pedida por el orquestador. Devuelve None si cerró, o
    un MOTIVO legible si se negó (el caller lo pega al mensaje del chat).
    Guardas: existencia + pertenencia al proyecto del chat (`project_id` no-None;
    un terminal_id alucinado no puede matar una terminal de OTRO proyecto) y el
    guard de 'trabajando' con insistencia."""
    from plotspace.routers.terminals import teardown_terminal
    conn = get_db()
    try:
        fila = conn.execute(
            'SELECT project_id, nombre, activa FROM terminals WHERE id = ?',
            (terminal_id,)
        ).fetchone()
    finally:
        conn.close()
    if fila is None or not fila['activa']:
        return L(f"no encontré la terminal #{terminal_id} activa",
                 f"I couldn't find an active terminal #{terminal_id}")
    if project_id is not None and fila['project_id'] != project_id:
        _logs.evento('cierre_rechazado', nivel='warn', terminal_id=terminal_id,
                     accion='close_terminal', motivo='otro_proyecto',
                     project_id=project_id)
        return L(f"no cerré la terminal #{terminal_id} ({fila['nombre']}): "
                 "pertenece a OTRO proyecto",
                 f"I didn't close terminal #{terminal_id} ({fila['nombre']}): "
                 "it belongs to ANOTHER project")

    clave = ('t', terminal_id)
    if _fase_terminal(terminal_id) == 'trabajando' and not _insistencia_cierre(clave):
        _sellar_rechazo_cierre(clave)
        _logs.evento('cierre_rechazado', nivel='warn', terminal_id=terminal_id,
                     accion='close_terminal', motivo='trabajando')
        return L(f"no cerré {fila['nombre']}: está TRABAJANDO ahora mismo — "
                 "si igual querés cerrarla, repetime la orden",
                 f"I didn't close {fila['nombre']}: it's WORKING right now — "
                 "if you still want it closed, repeat the order")
    _cierres_rechazados.pop(clave, None)

    conn = get_db()
    try:
        conn.execute('UPDATE terminals SET activa = 0 WHERE id = ?', (terminal_id,))
        conn.commit()
    finally:
        conn.close()
    # Teardown real: matar el agente tmux, no solo marcar activa=0.
    await teardown_terminal(terminal_id)
    return None


async def _actualizar_state_md(project: dict, project_id: int):
    """Regenera .workspace/STATE.md con estado actual."""
    ruta = project.get("ruta", "").strip()
    if not ruta:
        return
    workspace_dir = os.path.join(ruta, ".workspace")
    try:
        os.makedirs(workspace_dir, exist_ok=True)
    except OSError:
        return

    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM terminals WHERE project_id = ? AND activa = 1 ORDER BY fecha_creacion ASC',
            (project_id,)
        )
        terminals = [dict(t) for t in cursor.fetchall()]
    finally:
        conn.close()

    ahora  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lineas = [f"# Estado del workspace — {ahora}", "", "## Agentes activos"]
    if terminals:
        lineas += ["| ID | Nombre | IA | Estado | Desde |", "|----|--------|----|--------|-------|"]
        for t in terminals:
            desde = t["fecha_creacion"][11:16] if len(t["fecha_creacion"]) >= 16 else "-"
            lineas.append(f"| {t['id']} | {t['nombre']} | {t['tipo_ia']} | activo | {desde} |")
    else:
        lineas.append("_(sin agentes activos)_")
    lineas += ["", "## Archivos en uso", "_(sin cambios registrados)_", ""]

    state_path = os.path.join(workspace_dir, "STATE.md")
    try:
        with open(state_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lineas))
    except OSError:
        pass
