import asyncio
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

import anthropic
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from plotspace.core.database import get_db
from plotspace.core import logs as _logs
from plotspace.core.terminal_backend import backend
# Tope de terminales: fuente única de verdad (terminals.py no importa este
# módulo a nivel top-level, así que el import es acíclico). Antes el orquestador
# usaba un 7 hardcodeado y perdía agentes en workflows grandes en silencio.
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
Traducís órdenes en lenguaje natural a un plan de ejecución JSON que coordina
N agentes trabajando en paralelo directo sobre la rama main del proyecto.
Los agentes leen CLAUDE.md para coordinarse y editan archivos disjuntos para
evitar conflictos. Al terminar, los agentes commitean su propio trabajo y el
paso Reviewer audita el diff — vos NO commiteás ni mergeás: coordinás.

══════════════════════════════════════════════════════════════════════
PROCESO MENTAL OBLIGATORIO — recorré estos pasos ANTES de escribir JSON
══════════════════════════════════════════════════════════════════════

1. CLASIFICAR la orden en una de tres categorías:
   • Conversacional (saludo, "qué podés", explicación, pregunta sobre estado).
   • Operativa simple (abrir/cerrar terminales, sin código de fondo).
   • Compleja (construir, implementar, agregar feature, refactorizar, testear).

2. Si es COMPLEJA, hacé esta secuencia mental:
   a. DESCOMPONER en unidades de trabajo INDEPENDIENTES. Cada unidad debe
      poder hacerse sin esperar el resultado de otra. Si dos cosas
      necesitan el mismo archivo, NO son independientes.
   b. ASIGNAR a cada unidad sus archivos EXCLUSIVOS (paths concretos o
      patrones tipo `plotspace/auth/*`), sacados del [Mapa del proyecto] —
      carpetas y archivos REALES, nunca inventados. Dos agentes NUNCA tocan
      el mismo archivo. Si lo necesitan, son el mismo agente o en secuencia.
   c. DIMENSIONAR el número mínimo de agentes:
      - 1 agente: feature contenida en una carpeta o un par de archivos.
      - 2 agentes: división neta (frontend↔backend, impl↔tests, A↔B).
      - 3+ agentes: módulos verticalmente independientes (auth, pagos…).
      Nunca paralelices por gusto. Si un humano razonable lo haría solo,
      es un agente.
   d. REUSAR antes de crear: si [Estado actual] muestra terminales LIBRES
      (⚪, sin rol), asignales el trabajo — terminal_id en el paso, o la
      action enviar_prompt si es una tarea suelta sin workflow. Nunca a
      una 🟢 trabajando ni a una con rol en curso.
   e. ORDENAR dependencias: si paso_1 lee lo que paso_0 escribió,
      `depende_de: "paso_0"`. Si no, `depende_de: null` y van en paralelo.

3. VERIFICAR antes de escribir el JSON:
   • ¿Las listas de archivos son realmente disjuntas y salen del mapa?
   • ¿Tenés contexto suficiente, o falta UNA cosa específica?
   • ¿Cada `tarea` incluye archivos permitidos/prohibidos y criterio de
     éxito?

══════════════════════════════════════════════════════════════════════
PLANTILLA OBLIGATORIA DEL CAMPO `tarea`
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

El protocolo de cierre (TASK_DONE / TASK_BLOCKED / TASK_ERROR + sentinel)
lo agrega el ENGINE automáticamente a cada tarea — NO lo escribas vos.

══════════════════════════════════════════════════════════════════════
TONO Y FORMATO DEL `message`
══════════════════════════════════════════════════════════════════════

Estilo Jarvis (Iron Man): conciso, directo, técnico, sin relleno.

PROHIBIDO arrancar el mensaje con: "Por supuesto", "Claro", "Entendido",
"Con mucho gusto", "Perfecto", "Excelente", "Sin problema", "Por supuesto
que sí". Ese tipo de relleno se elimina.

OK: "De acuerdo, señor.", "Listo.", "Hecho.", "Lanzo X.", o ir directo al
plan sin saludo. El usuario te está dando una orden, no buscás aprobación.

REGLAS:
  • Confirmaciones: 1 oración máximo, idealmente 5-12 palabras.
  • Reportes/planes: 1 línea por agente, no párrafos.
  • Preguntas: UNA SOLA pregunta concreta. Nunca lista.
       Mal:  "¿Qué framework? ¿Qué DB? ¿Con o sin tests?"
       Bien: "¿Postgres o SQLite?"
  • Si la duda es razonable, ASUMÍ lo más común del stack y avisá en una
    línea: "Asumo pytest. Decime si querés otro framework."
  • Idioma: respondé en el mismo idioma que el usuario (default español).

══════════════════════════════════════════════════════════════════════
MANEJO DE ERRORES, BLOQUEOS Y MERGE CONFLICTS
══════════════════════════════════════════════════════════════════════

Si el contexto trae [Evento: TASK_BLOCKED + motivo]:
  1. Leé el motivo antes de actuar.
  2. Si la solución es obvia (falta info, dependencia no anticipada),
     emití un workflow chico que solo re-instruye a ese agente con la
     info que necesita.
  3. Si requiere decisión del usuario: UNA pregunta concreta.
  4. Nunca escales sin diagnosticar.

Si el contexto trae [Evento: merge conflict en archivo X]:
  • Explicá qué archivo y qué partes chocan, en 1-2 líneas.
  • Proponé cuál versión queda (no preguntes ambiguamente, sugerí).

Si el contexto trae [Evento: TASK_ERROR + detalle]:
  • Diagnosticá brevemente, ofrecé el siguiente paso o pedí una decisión.

══════════════════════════════════════════════════════════════════════
CONTEXTO QUE RECIBÍS EN CADA TURNO
══════════════════════════════════════════════════════════════════════

Recibís la conversación REAL del thread (los turnos previos van incluidos:
"ahora agregale X" refiere a lo que se habló antes). El mensaje actual puede
venir precedido por bloques del sistema:
  [Estado actual] — terminales activas con estado VIVO: fase (🟢 trabajando
                    AHORA / ⚪ quieta / ⏳ arrancando), su rol en workflow o
                    "terminal libre", y de qué archivos es dueña.
  [Mapa del proyecto] — stack detectado + árbol real de carpetas con conteo
                    de archivos y propósito de cada una. Es TU fuente para
                    el campo `archivos` y las rutas de cada tarea: usá
                    SIEMPRE carpetas/archivos que existan acá.
  [Skills activas] — plugins/skills cargados en el proyecto (si aplica).
  [Workflows recientes] — workflows previos con su progreso.
  [Eventos] — TASK_DONE/BLOCKED/ERROR de agentes en curso (si aplica).

Usalos para:
  • REUSAR terminales libres en vez de crear nuevas: enviar_prompt para
    una tarea suelta, o terminal_id en un paso de workflow. JAMÁS le
    mandes trabajo a una 🟢 trabajando ni a una ocupada con un rol.
  • Respetar el stack y las convenciones de las skills.
  • No repetir trabajo de workflows anteriores.

══════════════════════════════════════════════════════════════════════
PROACTIVIDAD POST-WORKFLOW
══════════════════════════════════════════════════════════════════════

Si [Workflows recientes] muestra un workflow done que acaba de completarse,
NUNCA preguntes "¿qué hacemos ahora?" como si fuera la primera interacción.
Tu lectura es:
  • Sabés qué se construyó (lo dice el nombre/objetivo del workflow).
  • Sabés qué terminales hay activas y qué rol tuvo cada una.
  • Sabés si hay un preview server corriendo (si el workflow incluyó
    frontend, JARVIS ya lo lanzó automáticamente en localhost:8080+).

Cuando el usuario te habla después de un workflow done:
  • Si pide algo relacionado (ajuste, fix, extensión), asumí el contexto.
    NO re-preguntes el stack ni qué se construyó.
  • Si pide algo nuevo, lanzá el workflow nuevo aprovechando lo construido.
  • Si te pregunta el estado: contestá específico, no genérico.
    Mal:  "¿En qué te puedo ayudar?"
    Bien: "Notes está mergeado. Preview en localhost:8082. ¿Tests ahora?"

Cuando un workflow termina e incluyó frontend:
  • El backend de JARVIS YA lanza un http.server automáticamente.
  • Mencioná la URL en tu respuesta de cierre (la recibís en el
    contexto del workflow_done que se broadcastea).
  • Si el usuario pide "ver el frontend" o "abrir el sitio", no
    necesitás lanzar nada manualmente, ya está corriendo.

Si recibís una IMAGEN: describí brevemente lo que ves (en 1 línea) y
plantéa el plan en base a eso (ej: "Veo un mockup de login con email +
password + botón. Lanzo agente para implementarlo.").

══════════════════════════════════════════════════════════════════════
CÓMO RESPONDÉS
══════════════════════════════════════════════════════════════════════

Tu ÚNICA vía de respuesta es llamar a la tool `responder` (exactamente una
vez, siempre). Sus campos: 'message' (texto al usuario, tono Jarvis, va
PRIMERO), 'actions' (array; usá [{"type":"none"}] cuando solo hay workflow
o respuesta conversacional) y 'workflow' (opcional, solo si la tarea es
compleja). La estructura la valida el schema de la tool — vos enfocate en
la SEMÁNTICA correcta de cada campo (abajo).

actions[].type válidos (NADA MÁS):
  "none"           — no hacer nada operativo
  "spawn_terminal" — { name, ia_type, count }
  "close_terminal" — { terminal_id }  → mata la sesión tmux del agente
  "close_all"      — sin args        → mata todas las terminales del proyecto
                                       (y también el preview si estaba activo)
  "stop_preview"   — sin args        → apaga el servidor http.server que JARVIS
                                       lanzó para mostrar el frontend. Es un
                                       proceso aparte de las terminales.
  "enviar_prompt"  — { terminal_id, prompt } → tipea la tarea/mensaje en una
                     terminal VIVA y libre (mismo canal que los workflows).
                     Es TU herramienta para "decile a X que...", fixes
                     sueltos o trabajo dirigido sin workflow. El prompt debe
                     ser autosuficiente (objetivo + archivos concretos del
                     [Mapa del proyecto] + criterio de éxito). Podés emitir
                     varias en una respuesta (una por terminal).

DIFERENCIA CRÍTICA — no confundir:
  • "cerrá la terminal" / "matá el agente X" / "borrá ese Claude"
      → close_terminal (con terminal_id) o close_all
  • "cerrá el servidor" / "apagá el preview" / "matá el puerto 8081" /
    "bajá el servidor" / "cerrá la pestaña del preview"
      → stop_preview (NO close_terminal — el preview NO corre en una
        terminal de JARVIS, corre como proceso aparte iniciado al hacer merge)

Si [Preview activo] aparece en el contexto, sabés exactamente qué URL hay
para apagar. Si el usuario te pasa una URL/puerto y matchea con la del
[Preview activo], usás stop_preview sin dudar. Si NO matchea, decile que
ese puerto no lo lanzó JARVIS.

ia_type válidos: "claude", "codex", "gemini", "opencode", "qwen",
"antigravity", "grok", "manual"

workflow.pasos[] esquema:
  agente:      string (nombre descriptivo, ej "Backend")
  ia_type:     "claude" | "codex" | "gemini" | "opencode" | "qwen" |
               "antigravity" | "grok" | "manual"
  terminal_id: OPCIONAL — id de una terminal activa LIBRE ([Estado actual])
               para REUSARLA en este paso en vez de spawnear una nueva.
  rol:        "scout" | "builder"  (default "builder")
  archivos:   array de paths/patrones EXCLUSIVOS de este paso
              (ej ["plotspace/auth/*", "plotspace/main.py"]). Pasos paralelos
              JAMÁS comparten archivos. Scout usa [] (solo lee).
  tarea:      string siguiendo la plantilla obligatoria de arriba
  depende_de: null  o  "paso_N" (índice 0-based del paso del que depende)

══════════════════════════════════════════════════════════════════════
ROLES DE SWARM
══════════════════════════════════════════════════════════════════════

SCOUT (opcional, recomendado para objetivos amplios o codebase desconocida):
  • Un paso_0 con rol "scout" y archivos []: explora el código relevante
    al objetivo (lee, no edita) y GUARDA sus hallazgos como memorias en
    .jarvis/memory/ (formato del protocolo del CLAUDE.md del proyecto),
    terminando con un resumen de qué memorias creó.
  • Los builders dependen del scout (depende_de: "paso_0") y su tarea les
    indica leer .jarvis/memory/INDEX.md antes de empezar.
  • NO uses scout para tareas chicas y obvias: es overhead.

BUILDER: el rol default. Implementa su unidad de trabajo dentro de SUS
archivos exclusivos.

REVIEWER: NO lo incluyas en los pasos — el sistema agrega automáticamente
un paso Reviewer al final de cada workflow, que revisa el diff completo
y BLOQUEA la finalización si la calidad no da. Contá con que existe.

Máximo __MAX_TERMINALES__ terminales totales (activas + nuevas). Si la orden
necesita más, avisá y proponé reusar las existentes o fasear el trabajo.

══════════════════════════════════════════════════════════════════════
EJEMPLOS CONCRETOS (estos son los outputs reales esperados)
══════════════════════════════════════════════════════════════════════

[1] CONVERSACIONAL — saludo / pregunta de capacidades
Usuario: "¿qué podés hacer?"
{"message":"Lanzo agentes de Claude/Codex/Gemini sobre la rama main, coordino sus tareas en paralelo. Decime qué construimos, señor.","actions":[{"type":"none"}]}

[2] OPERATIVA SIMPLE — abrir terminales
Usuario: "abrí 2 claude"
{"message":"Dos Claude listos.","actions":[{"type":"spawn_terminal","name":"Claude Code","ia_type":"claude","count":2}]}

[3] WORKFLOW DE 1 AGENTE — feature contenida
Usuario: "agregale al backend un endpoint /api/health"
{"message":"De acuerdo. Un agente sobre plotspace/routers/.","actions":[{"type":"none"}],"workflow":{"nombre":"Endpoint /api/health","objetivo":"GET /api/health que devuelva {ok, timestamp}","pasos":[{"agente":"Backend","ia_type":"claude","tarea":"OBJETIVO: crear GET /api/health que devuelva {ok: true, timestamp: ISO8601}. PERMITIDO editar: plotspace/routers/health.py (nuevo), plotspace/main.py (solo para registrar el router). PROHIBIDO tocar cualquier otro archivo. CRITERIO DE ÉXITO: curl http://localhost:3000/api/health devuelve 200 con el JSON correcto.","depende_de":null}]}}
(los paths salen del [Mapa del proyecto] del contexto — en otro proyecto serían otros)

[4] WORKFLOW DE 2 AGENTES EN PARALELO — frontend/backend disjuntos
Usuario: "construí un módulo de notas: API + UI simple"
{"message":"De acuerdo. Dos agentes en paralelo: backend y frontend.","actions":[{"type":"none"}],"workflow":{"nombre":"Módulo de notas","objetivo":"CRUD de notas con API REST y UI","pasos":[{"agente":"Backend","ia_type":"claude","tarea":"OBJETIVO: CRUD /api/notes con SQLite (tabla notes: id, contenido, created_at). PERMITIDO editar: plotspace/routers/notes.py (nuevo), plotspace/main.py (solo registrar router), plotspace/core/database.py (solo agregar CREATE TABLE notes). PROHIBIDO: frontend/*, otros routers. CRITERIO DE ÉXITO: GET/POST/DELETE /api/notes responden 200 con JSON.","depende_de":null},{"agente":"Frontend","ia_type":"claude","tarea":"OBJETIVO: UI que consume /api/notes (asumí que ese endpoint existe). Lista + crear + borrar, respetando la estructura y convenciones que ya tiene el frontend del proyecto. PERMITIDO editar: los archivos nuevos de la vista de notas dentro de la carpeta del frontend (según el mapa del proyecto), plotspace/main.py (solo para servir la página). PROHIBIDO: plotspace/* salvo lo indicado, todo lo del otro agente. CRITERIO DE ÉXITO: la página de notas muestra la lista, crear y borrar funcionan.","depende_de":null}]}}

[5] WORKFLOW SECUENCIAL — un agente depende del otro
Usuario: "implementá auth con JWT y después escribile tests"
{"message":"De acuerdo. Primero implementación, después tests.","actions":[{"type":"none"}],"workflow":{"nombre":"Auth JWT + tests","objetivo":"Login/register/me con JWT y tests pytest","pasos":[{"agente":"Implementación","ia_type":"claude","tarea":"OBJETIVO: endpoints POST /auth/register, POST /auth/login, GET /auth/me con JWT (usá pyjwt). PERMITIDO editar: plotspace/auth/* (nueva carpeta), plotspace/main.py (registrar router). PROHIBIDO: plotspace/tests/*. CRITERIO DE ÉXITO: register crea user, login devuelve token, /me con Authorization Bearer devuelve el user.","depende_de":null},{"agente":"Tests","ia_type":"claude","tarea":"OBJETIVO: tests pytest del módulo auth recién implementado. Leé plotspace/auth/* primero (vive en main, en este mismo directorio). PERMITIDO editar: plotspace/tests/test_auth.py (nuevo), plotspace/tests/conftest.py (si no existe). PROHIBIDO modificar la implementación en plotspace/auth/*. CRITERIO DE ÉXITO: cubrir happy path de register/login/me + token inválido + user duplicado. pytest sin fallos.","depende_de":"paso_0"}]}}

[6] PETICIÓN AMBIGUA — preguntar UNA cosa
Usuario: "agregá login"
{"message":"¿Sesiones con JWT o con cookies HTTP-only?","actions":[{"type":"none"}]}

[7] FALTA INFO PERO ES ASUMIBLE — avisar la asunción
Usuario: "ponele tests al módulo de pagos"
{"message":"De acuerdo. Asumo pytest. Decime si querés otro framework.","actions":[{"type":"none"}],"workflow":{"nombre":"Tests módulo pagos","objetivo":"Tests pytest sobre plotspace/payments/","pasos":[{"agente":"Tests","ia_type":"claude","tarea":"OBJETIVO: tests pytest cubriendo plotspace/payments/*. Leé los archivos primero. PERMITIDO editar: plotspace/tests/test_payments.py (nuevo), plotspace/tests/conftest.py (si no existe). PROHIBIDO modificar plotspace/payments/*. CRITERIO DE ÉXITO: ≥70% coverage del módulo y pytest verde.","depende_de":null}]}}

[8] CERRAR PREVIEW SERVER (NO confundir con cerrar terminal)
Contexto: [Preview activo]\n  Servidor de preview corriendo en http://localhost:8081/
Usuario: "cerrá el servidor del frontend" o "matá el puerto 8081"
{"message":"Preview cerrado.","actions":[{"type":"stop_preview"}]}

[9] RESPONDIENDO A TASK_BLOCKED
Contexto: [Evento: TASK_BLOCKED en paso_0 motivo: necesito saber si la tabla users ya existe o la creo]
{"message":"Le doy la info al agente.","actions":[{"type":"none"}],"workflow":{"nombre":"Resume auth con tabla users","objetivo":"Continuar paso_0 con la info que falta","pasos":[{"agente":"Implementación","ia_type":"claude","tarea":"CONTINUACIÓN del trabajo anterior: la tabla users no existe, creala vos en plotspace/database.py con columnas id, email UNIQUE, password_hash, created_at. Después seguí con auth como estabas. PERMITIDO: plotspace/auth/*, plotspace/database.py (solo agregar tabla users), plotspace/main.py. PROHIBIDO: plotspace/tests/*.","depende_de":null}]}}

[10] PROMPT DIRECTO A UNA TERMINAL VIVA — sin workflow
Contexto: [Estado actual]\n  - ID 151: Claude Code #2 (claude) — ⚪ quieta — terminal libre
Usuario: "decile a la claude libre que pula el diseño de la landing"
{"message":"Le mando la tarea a Claude Code #2.","actions":[{"type":"enviar_prompt","terminal_id":151,"prompt":"OBJETIVO: pulir el diseño de la landing. Trabajá sobre los archivos de la landing que muestra el mapa del proyecto (HTML + CSS). CRITERIO DE ÉXITO: jerarquía tipográfica consistente, espaciado uniforme y paleta cohesiva, sin romper el layout existente."}]}

══════════════════════════════════════════════════════════════════════
RECORDATORIO FINAL
══════════════════════════════════════════════════════════════════════

Respondés SIEMPRE llamando a la tool `responder` (una sola vez), con
'message' primero en tono Jarvis. Antes de llamarla, revisá mentalmente:
tono Jarvis, archivos disjuntos y sacados del mapa real, terminales libres
reusadas antes de spawnear.
""".replace('__MAX_TERMINALES__', str(MAX_TERMINALES))

# ─── Modelos ───────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    project_id: int
    message: str
    image_base64: Optional[str] = None
    media_type: Optional[str] = None
    # Turnos PREVIOS del thread activo ([{role, content}, ...]) — el frontend ya
    # los tiene para pintar el chat; mandarlos da memoria conversacional real.
    # Untrusted: se sanean server-side en _mensajes_con_historial.
    historial: Optional[list] = None


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


# ─── Endpoints: workflows del proyecto ────────────────────────────────────────

@router.get("/workflows/{project_id}")
async def listar_workflows(project_id: int):
    """Lista los workflows del proyecto (running + completados)."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, nombre, objetivo, estado, pasos, paso_actual, created_at '
            'FROM workflows WHERE project_id = ? ORDER BY created_at DESC',
            (project_id,)
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    resultado = []
    for r in rows:
        try:
            pasos = json.loads(r['pasos'])
        except (json.JSONDecodeError, TypeError):
            pasos = []
        resultado.append({
            'id':          r['id'],
            'nombre':      r['nombre'],
            'objetivo':    r['objetivo'],
            'estado':      r['estado'],
            'paso_actual': r['paso_actual'],
            'total_pasos': len(pasos),
            # FIX task board: los pasos se parseaban solo para contarlos y se
            # DESCARTABAN — el kanban no podía reconstruir el historial al
            # recargar (las cards solo vivían de los eventos WS en vivo).
            'pasos':       pasos,
            'created_at':  r['created_at'],
        })
    return resultado


# ─── Endpoints: chat ──────────────────────────────────────────────────────────

# Tool ÚNICA forzada con tool_choice: haiku adhiere mucho mejor a un input_schema
# que a "devolvé JSON puro" → mata el JSON malformado en origen. La estructura la
# valida el schema; _sanitizar_respuesta queda como red de 2do nivel (el schema es
# laxo a propósito: no garantiza enum de type ni pasos en workflow).
# Constante a nivel módulo (DRY + tools render antes que system → cache byte-idéntico
# entre requests; NO construir inline). 'message' va PRIMERO para que el partial_json
# del stream lo entregue cuanto antes y arranque el live-typing.
RESPONDER_TOOL = {
    "name": "responder",
    "description": (
        "Única forma de responder al usuario. Llamás a esta tool EXACTAMENTE una vez. "
        "'message' es el texto en tono Jarvis que ve el usuario y va PRIMERO. "
        "'actions' son las acciones operativas (usá [{\"type\":\"none\"}] si solo hay "
        "workflow o respuesta conversacional). 'workflow' solo si la tarea es compleja."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Texto al usuario, tono Jarvis (conciso, directo, sin relleno). Va PRIMERO. Se streamea token a token.",
            },
            "actions": {
                "type": "array",
                "description": "Acciones operativas. Usá [{\"type\":\"none\"}] si solo hay workflow o respuesta conversacional. Si hay workflow, los spawn_terminal se ignoran (el workflow crea sus terminales).",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["none", "spawn_terminal", "close_terminal", "close_all", "stop_preview", "enviar_prompt"],
                        },
                        "name": {"type": "string", "description": "Solo spawn_terminal: nombre de la terminal."},
                        "ia_type": {"type": "string", "enum": ["claude", "codex", "gemini", "opencode", "qwen", "antigravity", "grok", "manual"], "description": "Solo spawn_terminal."},
                        "count": {"type": "integer", "description": "Solo spawn_terminal: cuántas terminales."},
                        "terminal_id": {"type": "integer", "description": "close_terminal: id a cerrar. enviar_prompt: id de la terminal destino."},
                        "prompt": {"type": "string", "description": "Solo enviar_prompt: la tarea/mensaje que se tipea en esa terminal. Autosuficiente, con archivos/carpetas concretos del [Mapa del proyecto]."},
                    },
                    "required": ["type"],
                },
            },
            "workflow": {
                "type": "object",
                "description": "Solo si la tarea es compleja (construir, implementar, feature, refactor, tests). Omitir en conversacional/operativa simple.",
                "properties": {
                    "nombre": {"type": "string"},
                    "objetivo": {"type": "string"},
                    "pasos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "agente": {"type": "string", "description": "Nombre descriptivo, ej 'Backend'."},
                                "ia_type": {"type": "string", "enum": ["claude", "codex", "gemini", "opencode", "qwen", "antigravity", "grok", "manual"]},
                                "terminal_id": {"type": "integer", "description": "OPCIONAL: id de una terminal activa LIBRE ([Estado actual]) para REUSARLA en este paso en vez de crear una nueva. Omitir para spawnear."},
                                "rol": {"type": "string", "enum": ["scout", "builder"], "description": "Default 'builder'. 'scout' explora y guarda memorias, archivos []."},
                                "archivos": {"type": "array", "items": {"type": "string"}, "description": "Paths/patrones EXCLUSIVOS de este paso. Scout usa []."},
                                "tarea": {"type": "string", "description": "Prompt autosuficiente siguiendo la PLANTILLA: OBJETIVO, ARCHIVOS PERMITIDOS/PROHIBIDOS, CRITERIO DE ÉXITO, y CIERRE LITERAL con TASK_DONE/TASK_BLOCKED/TASK_ERROR."},
                                "depende_de": {"type": ["string", "null"], "description": "null (paralelo) o 'paso_N' (índice 0-based del paso del que depende)."},
                            },
                            "required": ["agente", "ia_type", "tarea"],
                        },
                    },
                },
                "required": ["nombre", "objetivo", "pasos"],
            },
        },
        "required": ["message", "actions"],
    },
}


def _extraer_respuesta_tool(message):
    """Del Message final saca el input del bloque tool_use 'responder' (dict ya parseado
    por el SDK) y su serialización JSON, para alimentar _procesar_respuesta_orquestador SIN
    tocar su firma (recibe un string JSON → json.loads + _sanitizar_respuesta intactos).
    Con tool_choice forzado SIEMPRE hay un tool_use; el fallback ({}, "{}") es defensa pura
    (degradación segura → _sanitizar_respuesta devuelve 'Procesado.' + [{none}])."""
    for block in getattr(message, "content", None) or []:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "responder":
            inp = block.input if isinstance(block.input, dict) else {}
            return inp, json.dumps(inp, ensure_ascii=False)
    return {}, "{}"


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
    actions=null, workflow={}, tipos equivocados, o ni siquiera un dict. Devuelve SIEMPRE una
    tripleta segura (message:str, actions:list[dict con 'type' str], workflow:dict-con-pasos|None)
    → el resto del pipeline (ejecutar actions/workflow) nunca crashea por forma inválida. Pura/testeable."""
    if not isinstance(parsed, dict):
        return ((raw_text if isinstance(raw_text, str) and raw_text.strip() else "Procesado."),
                [{"type": "none"}], None)
    msg = parsed.get("message")
    if not isinstance(msg, str) or not msg.strip():
        msg = raw_text if isinstance(raw_text, str) and raw_text.strip() else "Procesado."
    acts = parsed.get("actions")
    if not isinstance(acts, list):
        acts = []
    acts = [a for a in acts if isinstance(a, dict) and isinstance(a.get("type"), str)]
    if not acts:
        acts = [{"type": "none"}]
    wf = parsed.get("workflow")
    if isinstance(wf, dict) and isinstance(wf.get("pasos"), list):
        # Descartar pasos INVÁLIDOS (sin 'tarea' string no vacía): con tool-use, una respuesta
        # truncada por max_tokens deja el ÚLTIMO paso a medias (el SDK parsea el tool input en
        # modo parcial) → ejecutarlo crashearía (KeyError 'tarea'). Antes el truncado fallaba el
        # parse entero y se descartaba el workflow; acá replicamos esa degradación segura por paso.
        pasos_ok = [p for p in wf["pasos"]
                    if isinstance(p, dict) and isinstance(p.get("tarea"), str) and p.get("tarea").strip()]
        wf = {**wf, "pasos": pasos_ok} if pasos_ok else None
    else:
        wf = None
    return (msg, acts, wf)


async def _procesar_respuesta_orquestador(raw_text, usage, project, req, terminals_activas, stop_reason=None):
    """Parsea la respuesta del modelo (JSON), ejecuta actions/workflow, registra el uso
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
    # el mensaje + actions/workflow. El de-fence queda SOLO como fallback si el parse directo
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
    # nunca dejar que rompa la ejecución de actions/workflow.
    jarvis_message, actions, workflow_data = _sanitizar_respuesta(parsed, raw_text)

    # Si el modelo se cortó por longitud, el workflow casi seguro quedó truncado → NO ejecutar
    # un plan a medias: descartarlo y pedir que repita. (El sanitizer ya dropeó los pasos sin
    # 'tarea', pero podrían faltar pasos enteros que no llegaron a aparecer.)
    if stop_reason == "max_tokens" and workflow_data:
        workflow_data = None
        jarvis_message = (jarvis_message + " (me corté por longitud, señor — repetime la orden más acotada).").strip()

    created_terminals = []
    closed_all        = False
    workflow_card     = None

    # ── Ejecutar acciones simples ──────────────────────────────────────────────
    # Si hay un workflow, ignorar spawn_terminal — el workflow crea sus propias terminales.
    for action in actions:
        atype = action.get("type", "none")

        if atype == "spawn_terminal":
            if workflow_data:
                continue
            count_actual = len(terminals_activas) + len(created_terminals)
            nuevas = await _spawn_terminales(
                project_id   = req.project_id,
                name         = action.get("name", "Terminal"),
                ia_type      = action.get("ia_type", "manual"),
                count        = int(action.get("count", 1)),
                count_actual = count_actual,
            )
            created_terminals.extend(nuevas)

        elif atype == "close_all":
            trabajando = await _cerrar_todas(req.project_id)
            if trabajando:
                # Guard: hay agentes a mitad de tarea → no se cerró NADA (todo-o-
                # nada) y closed_all queda False (true borra TODAS las cards).
                jarvis_message = (jarvis_message + " ⚠️ No cerré nada: " +
                                  ", ".join(trabajando) +
                                  " está(n) trabajando ahora mismo. Si igual querés "
                                  "cerrarlas, repetime la orden.").strip()
            else:
                _detener_preview_si_existe(req.project_id)
                closed_all = True

        elif atype == "close_terminal":
            tid = action.get("terminal_id")
            if tid:
                motivo = await _cerrar_terminal(int(tid), req.project_id)
                if motivo:
                    jarvis_message = (jarvis_message + f" ⚠️ Ojo: {motivo}.").strip()

        elif atype == "stop_preview":
            _detener_preview_si_existe(req.project_id)

        elif atype == "enviar_prompt":
            # Las manos que faltaban: mandarle una tarea a una terminal VIVA
            # (send_to_agent, el mismo canal de los workflows) sin spawnear nada.
            activas_ids = {t['id'] for t in terminals_activas}
            conn = get_db()
            try:
                ocupadas = _terminales_ocupadas(conn.cursor(), req.project_id)
            finally:
                conn.close()
            tid, motivo = _validar_enviar_prompt(action, activas_ids, ocupadas)
            if motivo:
                jarvis_message = (jarvis_message +
                                  f" ⚠️ No envié el prompt: {motivo}.").strip()
            else:
                await send_to_agent(tid, action['prompt'].strip())
                _logs.evento('prompt_directo', terminal_id=tid,
                             project_id=req.project_id)

    # ── Ejecutar workflow si lo hay ────────────────────────────────────────────
    if workflow_data:
        count_base = len(terminals_activas) + len(created_terminals)
        wf_card, wf_terminals = await ejecutar_workflow(
            workflow_data, req.project_id, count_base
        )
        workflow_card = wf_card
        created_terminals.extend(wf_terminals)

    await _actualizar_state_md(project, req.project_id)

    return {
        "response":          jarvis_message,
        "actions":           actions,
        "created_terminals": created_terminals,
        "closed_all":        closed_all,
        "workflow_card":     workflow_card,
    }


# Auto-intervención (Etapa 5): ante TASK_BLOCKED (o TASK_ERROR sin reasignación
# posible) el orquestador se llama SOLO y re-instruye al agente. Solo en motor
# suscripción (cero API paga). Flag global + guardas anti-loop abajo.
ORQ_AUTO_INTERVENCION = (os.environ.get('ORQ_AUTO_INTERVENCION', 'on')
                         .strip().lower() not in ('off', '0', 'false', 'no'))
_AUTO_INTERV_TOPE_HORA = 6
_auto_intervenciones: list = []   # timestamps de intervenciones (ventana móvil)


def _puede_auto_intervenir(paso: dict, habilitado: bool, recientes: list,
                           ahora: float, tope: int = _AUTO_INTERV_TOPE_HORA) -> bool:
    """Guarda PURA anti-loop: flag prendido + este paso nunca intervenido +
    menos de `tope` intervenciones en la última hora (en todo el server)."""
    if not habilitado:
        return False
    if paso.get('auto_intervencion_ts'):
        return False
    en_ventana = sum(1 for t in recientes if (ahora - t) < 3600)
    return en_ventana < tope


def _mensaje_auto_intervencion(evento: str, paso_idx: int, wf_nombre: str,
                               motivo: str, term_nombre: str) -> str:
    """El mensaje sintético con el que el orquestador se llama a sí mismo.
    PURA — es la sección de manejo de errores del prompt, pero ejercitada."""
    return (
        f"[Evento: {evento} en paso_{paso_idx} del workflow '{wf_nombre}' — "
        f"agente {term_nombre}. Motivo: {motivo or 'sin motivo reportado'}]\n"
        "AUTO-INTERVENCIÓN (el usuario NO está mirando este chat ahora): si el "
        "bloqueo se resuelve con información que tenés o una decisión técnica "
        "razonable, resolvelo YA re-instruyendo al agente — un workflow chico "
        "con la info que falta, o enviar_prompt si alcanza con un empujón. "
        "SOLO si genuinamente requiere una decisión del usuario (producto, "
        "datos que no existen), tu 'message' debe ser UNA pregunta concreta "
        'para él y actions [{"type":"none"}].'
    )


async def _auto_intervenir(project_id: int, wf_nombre: str, paso_idx: int,
                           evento: str, motivo: str, term_nombre: str):
    """Corre en background tras el broadcast del bloqueo: consulta al motor
    suscripción con el contexto del evento y ejecuta lo que decida (workflow
    chico / enviar_prompt / pregunta al usuario). Best-effort: cualquier error
    se loguea y el flujo manual de siempre sigue disponible."""
    from plotspace.core.events import broadcaster
    try:
        req = ChatRequest(project_id=project_id,
                          message=_mensaje_auto_intervencion(
                              evento, paso_idx, wf_nombre, motivo, term_nombre))
        project, terminals_activas, mensajes, _cli = await _preparar_contexto_chat(req)
        raw, usage, stop = await _consultar_cli(mensajes, project)
        res = await _procesar_respuesta_orquestador(
            raw, usage, project, req, terminals_activas, stop_reason=stop)
        await broadcaster.broadcast(project_id, {
            "type":    "orquestador_mensaje",
            "message": f"🤖 Auto-intervención: {res['response']}",
        })
        _logs.evento('auto_intervencion', project_id=project_id, paso=paso_idx,
                     evento=evento, workflow=wf_nombre)
    except Exception as e:
        print(f'[auto-intervención] falló (paso_{paso_idx} de {wf_nombre}): {e}')


def _lanzar_auto_intervencion(wf: dict, pasos: list, paso_idx: int,
                              project_id: int, evento: str, motivo: str,
                              term_nombre: str):
    """Sella el paso + registra la ventana y dispara la intervención en
    background. Centraliza las guardas para BLOCKED y ERROR."""
    if ORQUESTADOR_MOTOR == 'api':
        return                      # con API paga no se gasta solo
    if not (0 <= paso_idx < len(pasos)):
        return
    ahora = time.time()
    _auto_intervenciones[:] = [t for t in _auto_intervenciones if ahora - t < 3600]
    if not _puede_auto_intervenir(pasos[paso_idx], ORQ_AUTO_INTERVENCION,
                                  _auto_intervenciones, ahora):
        return
    pasos[paso_idx]['auto_intervencion_ts'] = ahora
    _auto_intervenciones.append(ahora)
    _actualizar_workflow_db(wf['id'], estado='paused', pasos=pasos, paso_actual=paso_idx)
    asyncio.create_task(_auto_intervenir(
        project_id, wf.get('nombre') or 'Workflow', paso_idx, evento,
        motivo or '', term_nombre))


def _validar_enviar_prompt(action: dict, activas_ids: set, ocupadas: set):
    """Guarda de la action enviar_prompt. Devuelve (terminal_id, None) si el
    envío es válido, o (None, motivo legible) si no. PURA — el caller junta
    activas_ids (terminales del proyecto) y ocupadas (pasos running)."""
    tid = action.get('terminal_id')
    if not isinstance(tid, int):
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            return None, 'falta un terminal_id válido'
    prompt = action.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip():
        return None, f'falta el prompt para la terminal #{tid}'
    if tid not in activas_ids:
        return None, f'la terminal #{tid} no está activa en este proyecto'
    if tid in ocupadas:
        return None, (f'la terminal #{tid} está ocupada con un paso de '
                      'workflow en curso')
    return tid, None


def _terminal_reusable(tid, activas_ids: set, ocupadas: set, reclamadas: set) -> bool:
    """¿El paso puede REUSAR la terminal `tid` en vez de spawnear una nueva?
    Solo si es un id real de terminal activa, libre (sin paso running) y no
    reclamada ya por otro paso de este mismo workflow. PURA."""
    return (isinstance(tid, int) and tid in activas_ids
            and tid not in ocupadas and tid not in reclamadas)


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


# En modo CLI no existe la tool `responder`: la salida es el JSON directo
# (validado por --json-schema). Estos son los DOS párrafos del prompt base
# que cambian — si el .replace no matchea, los tests de motor lo cazan.
_PARRAFO_TOOL = """Tu ÚNICA vía de respuesta es llamar a la tool `responder` (exactamente una
vez, siempre). Sus campos: 'message' (texto al usuario, tono Jarvis, va
PRIMERO), 'actions' (array; usá [{"type":"none"}] cuando solo hay workflow
o respuesta conversacional) y 'workflow' (opcional, solo si la tarea es
compleja). La estructura la valida el schema de la tool — vos enfocate en
la SEMÁNTICA correcta de cada campo (abajo)."""

_PARRAFO_JSON = """Tu respuesta FINAL debe ser ÚNICAMENTE un objeto JSON — sin fences, sin
texto alrededor — con: 'message' (texto al usuario, tono Jarvis, va
PRIMERO), 'actions' (array; usá [{"type":"none"}] cuando solo hay workflow
o respuesta conversacional) y 'workflow' (opcional, solo si la tarea es
compleja). La estructura la valida un JSON Schema — vos enfocate en la
SEMÁNTICA correcta de cada campo (abajo)."""

_RECORDATORIO_TOOL = """Respondés SIEMPRE llamando a la tool `responder` (una sola vez), con
'message' primero en tono Jarvis."""

_RECORDATORIO_JSON = """Tu ÚLTIMO mensaje es SIEMPRE el objeto JSON de respuesta, con
'message' primero en tono Jarvis."""

_BLOQUE_OJOS = """

══════════════════════════════════════════════════════════════════════
OJOS PROPIOS (tools de lectura)
══════════════════════════════════════════════════════════════════════

Tenés Read/Glob/Grep sobre la carpeta del proyecto (SOLO lectura — jamás
edites, crees ni ejecutes nada). Usalos ÚNICAMENTE cuando el pedido lo
amerite: diseñar un workflow sobre código que no conocés, verificar que un
archivo exista antes de asignarlo, o entender una estructura que el
[Mapa del proyecto] no alcanza a mostrar. Sé quirúrgico (2-4 vistazos máximo)
y para saludos/preguntas simples respondé DIRECTO sin tocar ninguna tool.
"""


def _system_prompt_cli() -> str:
    """Prompt del modo suscripción: el base con la mecánica de la tool
    reemplazada por salida JSON directa + el bloque de exploración."""
    s = SYSTEM_PROMPT.replace(_PARRAFO_TOOL, _PARRAFO_JSON)
    s = s.replace(_RECORDATORIO_TOOL, _RECORDATORIO_JSON)
    return s + _BLOQUE_OJOS


_anthropic_cliente = None
_anthropic_key = None


def _guard_api_key() -> str:
    """Devuelve la ANTHROPIC_API_KEY o lanza un 409 ESTRUCTURADO (no un 500 crudo)
    para que el frontend lo muestre como empty-state limpio. El chat de Jarvis (y el
    Web Builder) son los únicos que usan esta key; los agentes en terminales (BYOK)
    NO la necesitan. Forma del error: status 409, body
    {"detail": {"error": "no_api_key", "message": "..."}}."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=409, detail={
            "error": "no_api_key",
            "message": "Configurá ANTHROPIC_API_KEY (plotspace/.env) para usar el chat "
                       "de Jarvis. Los agentes en terminales (BYOK) no la necesitan.",
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
    diseñe el workflow esquivando los errores conocidos (la memoria entra al
    planning, no solo al prompt del agente). Determinista, cero API. '' si no
    hay proyecto o nada relevante — degrada sin romper el chat."""
    if not project_ruta:
        return ''
    try:
        from plotspace.core.memoria_recall import relevantes, usos_registrados
        rel = relevantes(project_ruta, [], mensaje or '', k=4, usos=usos_registrados())
    except Exception:
        return ''
    if not rel:
        return ''
    lineas = ["[Memoria relevante al pedido — tenela en cuenta al planear "
              "(reglas, lápidas de features eliminados, gotchas):]"]
    for m in rel:
        marca = ' ⚰️LÁPIDA(no reintroducir)' if m['estado'] == 'lapida' else ''
        lineas.append(f"  • {m['titulo']}{marca} — .jarvis/memory/{m['slug']}.md")
    return '\n'.join(lineas)


async def _preparar_contexto_chat(req):
    """Contexto COMPARTIDO por /chat y /chat-stream: trae el proyecto + terminales,
    arma el prompt con contexto, valida la API key y devuelve el cliente Anthropic.
    Lanza HTTPException 404/500 igual que antes (sin cambios de comportamiento)."""
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

    # Armado del contexto: solo agregar bloques que tengan contenido real.
    bloques = [f"[Estado actual]\n{_formatear_estado(terminals_activas, req.project_id)}"]

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

    skills_str = _formatear_skills_activas(req.project_id)
    if skills_str:
        bloques.append(f"[Skills activas]\n{skills_str}")

    workflows_str = _formatear_workflows_recientes(req.project_id)
    if workflows_str:
        bloques.append(f"[Workflows recientes]\n{workflows_str}")

    mem_str = _bloque_memoria_para_orden(project.get('ruta'), req.message)
    if mem_str:
        bloques.append(mem_str)

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


def _guard_cli():
    """409 estructurado si el CLI `claude` no está disponible (modo
    suscripción). Espejo de _guard_api_key para el motor nuevo."""
    import shutil
    if not shutil.which('claude'):
        raise HTTPException(status_code=409, detail={
            "error": "no_cli",
            "message": "El orquestador corre con tu suscripción de Claude vía "
                       "el CLI `claude`, pero no lo encuentro en el PATH. "
                       "Instalalo y logueá tu cuenta (o usá ORQUESTADOR_MOTOR=api).",
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
                                   schema=RESPONDER_TOOL['input_schema']):
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
    """Procesa mensaje del usuario, llama a Claude y ejecuta acciones/workflows."""
    project, terminals_activas, mensajes, client = await _preparar_contexto_chat(req)

    if ORQUESTADOR_MOTOR != 'api':
        # Motor SUSCRIPCIÓN (default): claude -p con la cuenta OAuth activa.
        from plotspace.core.orq_cli import OrqCliError
        try:
            raw_text, usage, stop = await _consultar_cli(mensajes, project)
        except OrqCliError as e:
            raise HTTPException(status_code=502, detail=f"Orquestador (suscripción): {e}")
        return await _procesar_respuesta_orquestador(
            raw_text, usage, project, req, terminals_activas, stop_reason=stop)

    try:
        response = await client.messages.create(
            model=ORQUESTADOR_MODEL,
            max_tokens=4096,
            system=_system_con_cache(),
            tools=[RESPONDER_TOOL],
            tool_choice={"type": "tool", "name": "responder"},
            messages=mensajes,
        )
        # Con tool_choice forzado el (único) bloque es tool_use; re-serializamos su input
        # para alimentar el helper compartido sin cambiarle la firma (round-trip barato).
        _, raw_text = _extraer_respuesta_tool(response)
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=401, detail="ANTHROPIC_API_KEY inválida")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error Claude API: {e}")

    # Post-procesado (parse JSON + ejecutar actions/workflow + uso + STATE.md) en un
    # helper COMPARTIDO con /chat-stream, así no hay drift en la lógica crítica.
    return await _procesar_respuesta_orquestador(
        raw_text, getattr(response, 'usage', None), project, req, terminals_activas,
        stop_reason=getattr(response, 'stop_reason', None))


@router.post("/chat-stream")
async def chat_orquestador_stream(req: ChatRequest):
    """Igual que /chat pero STREAMEA el 'message' token a token (SSE) para que la
    respuesta aparezca en vivo. Las actions/workflow se ejecutan AL FINAL con el
    MISMO helper que /chat (cero drift). Si el front falla con esto, cae a /chat."""
    project, terminals_activas, mensajes, client = await _preparar_contexto_chat(req)

    async def gen():
        def sse(obj):
            return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"

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
                        schema=RESPONDER_TOOL['input_schema']):
                    if ev['tipo'] == 'reinicio':
                        raw_cli, emitido_cli = "", 0
                    elif ev['tipo'] == 'delta':
                        raw_cli += ev['texto']
                        msg = _extraer_message_parcial(raw_cli)
                        if len(msg) > emitido_cli:
                            yield sse({"type": "token", "chunk": msg[emitido_cli:]})
                            emitido_cli = len(msg)
                    elif ev['tipo'] == 'resultado':
                        resultado = ev
            except orq_cli.OrqCliError as e:
                yield sse({"type": "error", "detail": f"Orquestador (suscripción): {e}"})
                return
            if resultado is None or resultado['error']:
                yield sse({"type": "error",
                           "detail": "el CLI terminó sin resultado utilizable"})
                return
            usage_cli = SimpleNamespace(input_tokens=resultado['input_tokens'],
                                        output_tokens=resultado['output_tokens'])
            try:
                res = await _procesar_respuesta_orquestador(
                    resultado['texto'], usage_cli, project, req,
                    terminals_activas, stop_reason='end_turn')
            except Exception as e:
                yield sse({"type": "error", "detail": f"Error procesando la respuesta: {e}"})
                return
            yield sse({"type": "done", **res})
            return

        raw = ""
        emitido = 0          # nº de chars del 'message' ya enviados al cliente
        usage = None
        stop = None          # stop_reason del modelo (para descartar workflow truncado)
        try:
            async with client.messages.stream(
                model=ORQUESTADOR_MODEL,
                max_tokens=4096,
                system=_system_con_cache(),
                tools=[RESPONDER_TOOL],
                tool_choice={"type": "tool", "name": "responder"},
                messages=mensajes,
            ) as stream:
                # OJO: con tool_choice forzado NO hay text block → stream.text_stream
                # queda VACÍO. Hay que iterar los eventos y filtrar 'input_json'
                # (InputJsonEvent: .partial_json = delta del JSON del tool input).
                # 'raw' acumula ese JSON, que tiene la MISMA forma {"message":"..."} de
                # siempre → _extraer_message_parcial se reusa TAL CUAL (cero regresión).
                async for event in stream:
                    if event.type == "input_json":
                        raw += event.partial_json
                        msg = _extraer_message_parcial(raw)
                        if len(msg) > emitido:
                            yield sse({"type": "token", "chunk": msg[emitido:]})
                            emitido = len(msg)
                final = await stream.get_final_message()
                usage = getattr(final, "usage", None)
                stop = getattr(final, "stop_reason", None)
                # raw canónico = input del tool_use ya parseado por el SDK (más robusto
                # que el acumulado, que podría truncar por max_tokens).
                _, raw = _extraer_respuesta_tool(final)
        except anthropic.AuthenticationError:
            yield sse({"type": "error", "detail": "ANTHROPIC_API_KEY inválida"})
            return
        except Exception as e:
            yield sse({"type": "error", "detail": f"Error Claude API: {e}"})
            return

        # Ejecutar actions/workflow + uso + STATE.md (idéntico a /chat).
        try:
            resultado = await _procesar_respuesta_orquestador(
                raw, usage, project, req, terminals_activas, stop_reason=stop)
        except Exception as e:
            yield sse({"type": "error", "detail": f"Error procesando la respuesta: {e}"})
            return

        # Evento final con TODO lo estructurado (el cliente ejecuta/renderiza igual que /chat).
        yield sse({"type": "done", **resultado})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Puerto real de Jarvis (modo app: el shell lo elige dinámico vía JARVIS_PORT)
_JARVIS_PORT = os.environ.get('JARVIS_PORT', '3000')


def _fuente_permite_jarvis(fuente: str) -> bool:
    """¿Una fuente de CSP `frame-ancestors` permite embeber a Jarvis
    (http://localhost:3000)? Cubre `*`, `http(s)://localhost:*` (lo que refleja
    Twitch con el parent) y `localhost:3000`. Un puerto AUSENTE = default (80),
    que NO es el 3000 de Jarvis → no cuenta."""
    f = fuente.strip().strip("'\"").lower()
    if f == '*':
        return True
    for pre in ('http://', 'https://', '//'):
        if f.startswith(pre):
            f = f[len(pre):]
            break
    f = f.split('/')[0]                      # host[:port] (sin path)
    host, sep, port = f.partition(':')
    if host != 'localhost':
        return False
    return bool(sep) and port in ('*', _JARVIS_PORT)


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


# Pricing de claude-haiku-4-5: $1.00 input / $5.00 output por millón de tokens
# (confirmado contra la referencia de la API). Puro y testeable.
# Pricing (USD por millón de tokens, input/output) por modelo. Si cambiás
# ORQUESTADOR_MODEL, el costo del panel sigue bien solo. Desconocido → haiku.
_PRECIOS_MTOK = {
    'claude-haiku-4-5':  (1.00, 5.00),
    'claude-sonnet-4-6': (3.00, 15.00),
    'claude-sonnet-5':   (3.00, 15.00),
    'claude-opus-4-6':   (5.00, 25.00),
    'claude-opus-4-7':   (5.00, 25.00),
    'claude-opus-4-8':   (5.00, 25.00),
}


def _precios_de(modelo: str) -> tuple:
    """(precio_in, precio_out) por MTok del modelo; fallback = haiku."""
    return _PRECIOS_MTOK.get(modelo, _PRECIOS_MTOK['claude-haiku-4-5'])


def _costo_usd(input_tokens: int, output_tokens: int, modelo: str = None) -> float:
    """Costo en USD de un uso acumulado, con el pricing del modelo activo."""
    p_in, p_out = _precios_de(modelo or ORQUESTADOR_MODEL)
    return ((input_tokens  or 0) / 1_000_000) * p_in + \
           ((output_tokens or 0) / 1_000_000) * p_out


@router.get("/uso/{project_id}")
async def consultar_uso(project_id: int):
    """Uso acumulado del orquestador para el proyecto: tokens y costo en USD.
    En modo suscripción el costo por token es $0 (lo cubre la suscripción de
    la cuenta activa); los tokens se siguen registrando como telemetría."""
    from plotspace.core.database import obtener_uso_orquestador
    uso = obtener_uso_orquestador(project_id)
    uso['motor'] = ORQUESTADOR_MOTOR
    uso['costo_usd'] = (0.0 if ORQUESTADOR_MOTOR != 'api'
                        else round(_costo_usd(uso['input_tokens'], uso['output_tokens']), 4))
    return uso


@router.get("/preview/{project_id}")
async def consultar_preview(project_id: int):
    """Preview activo del proyecto. `url` = el más reciente (para el pill,
    que es single); `urls` = TODOS los dev servers vivos (un agente puede
    levantar varios) para que el Web Preview los abra como pestañas."""
    return {'url': _preview_url_activo(project_id), 'urls': _preview_urls_activas(project_id)}


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
    from urllib.parse import urlparse
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
        # Cerrar SOLO ese server. ¿Es el http.server propio? → por proceso.
        entry = _preview_servers.get(project_id)
        if entry and entry[1] == pedida:
            propio = _detener_preview_si_existe(project_id)
        if not propio:
            port = urlparse(pedida).port
            if port:
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
            port = urlparse(url).port
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


# (Definición duplicada de GET /workflows/{project_id} ELIMINADA: FastAPI
#  matcheaba la primera (línea ~422) y esta quedaba muerta — la divergencia
#  entre ambas causó el bug del task board sin historial.)


# ─── Lógica de workflows ────────────────────────────────────────────────────────

def _dep_indice(dep) -> Optional[int]:
    """Parse 'paso_3' → 3. Devuelve None si dep es null o no parseable."""
    if not dep:
        return None
    try:
        return int(str(dep).replace('paso_', '').strip())
    except (ValueError, AttributeError):
        return None


def _paso_reviewer(nombre: str, objetivo: str) -> dict:
    """Paso Reviewer que el engine agrega al final de cada workflow:
    la compuerta de calidad antes de declarar el workflow completado."""
    tarea = (
        f"Sos el REVIEWER del workflow \"{nombre}\""
        + (f" (objetivo: {objetivo})" if objetivo else "") + ".\n\n"
        "Los builders ya terminaron su trabajo en main. Sos la compuerta de "
        "calidad: nada se declara completado sin tu aprobación.\n\n"
        "1. Corré `git status` y `git diff HEAD` (y `git log --oneline -10`) "
        "para ver TODO lo que cambió.\n"
        "2. Evaluá contra esta barra:\n"
        "   a. ¿Los cambios cumplen el objetivo del workflow?\n"
        "   b. ¿Nada evidentemente roto? (sintaxis, imports, referencias a "
        "archivos/funciones que no existen)\n"
        "   c. ¿Sin secretos/credenciales/keys en el diff?\n"
        "   d. ¿Consistente con las convenciones del proyecto (CLAUDE.md) "
        "y con lo que dice .jarvis/memory/INDEX.md?\n"
        "3. Problemas MENORES (typo, import faltante, detalle de estilo): "
        "arreglalos vos directamente.\n"
        "4. Si descubriste algo que los próximos agentes deban saber, "
        "guardalo en .jarvis/memory/ según el protocolo.\n\n"
        "VEREDICTO (obligatorio, una línea sola al final):\n"
        "• Si la calidad está OK → escribí TASK_DONE\n"
        "• Si hay problemas serios → escribí TASK_BLOCKED seguido del motivo "
        "concreto (qué archivo, qué problema, qué habría que hacer)\n\n"
        "No agregues features ni refactorices por gusto: sos revisor, no builder."
    )
    return {
        'agente':     'Reviewer',
        'ia_type':    'claude',
        'rol':        'reviewer',
        'archivos':   [],
        'tarea':      tarea,
        'depende_de': None,  # el gating real es el special-case del engine
    }


_ESTADOS_TERMINALES = ('done', 'blocked', 'error')


def _pasos_listos_para_arrancar(pasos: list) -> list:
    """Devuelve los índices de pasos en estado='pending' cuyas dependencias
    están resueltas (depende_de es null, o el paso del que dependen está 'done').

    Special-case REVIEWER: arranca cuando ningún otro paso sigue en marcha —
    TERMINADOS, no necesariamente exitosos. Antes exigía `done` de todos, así
    que un solo paso bloqueado dejaba el workflow colgado para siempre: sin
    review, sin cierre y sin que nadie avisara. Un workflow que termina mal
    igual necesita que alguien mire el diff."""
    listos = []
    for i, p in enumerate(pasos):
        if p.get('estado') != 'pending':
            continue
        if p.get('rol') == 'reviewer':
            otros = [q for j, q in enumerate(pasos) if j != i]
            if otros and all(q.get('estado') in _ESTADOS_TERMINALES for q in otros):
                listos.append(i)
            continue
        dep_idx = _dep_indice(p.get('depende_de'))
        if dep_idx is None:
            listos.append(i)
            continue
        if 0 <= dep_idx < len(pasos) and pasos[dep_idx].get('estado') == 'done':
            listos.append(i)
    return listos


def _workflow_terminado(pasos: list) -> bool:
    """True si ningún paso está pending/running. Los blocked/error cuentan
    como terminales (no van a avanzar solos)."""
    return all(p.get('estado') in ('done', 'blocked', 'error') for p in pasos)


def _progreso_workflow(pasos: list) -> int:
    """Cantidad de pasos completados (done). Útil para paso_actual de la UI."""
    return sum(1 for p in pasos if p.get('estado') == 'done')


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
    `-d` borra el buffer al usarlo (si no, queda uno colgado por tarea)."""
    buf = f'jarvis_tarea_{session}'
    return [
        ['tmux', 'set-buffer', '-b', buf, '--', texto],
        ['tmux', 'paste-buffer', '-b', buf, '-t', session, '-p', '-d'],
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


def _tarea_engine_para_terminal(paso: dict, terminal_id: int,
                                project_ruta: str = None,
                                pasos_workflow: list = None) -> str:
    """Agrega instrucciones garantizadas por el engine al prompt del paso."""
    from plotspace.core import sentinel

    tarea = paso.get('tarea') or ''
    archivos = paso.get('archivos') or []
    if archivos and paso.get('rol') != 'reviewer':
        tarea += ("\n\nARCHIVOS DE TU PROPIEDAD EXCLUSIVA (no toques nada "
                  f"fuera de esto): {', '.join(archivos)}. Si necesitás "
                  "modificar otro archivo, escribí TASK_BLOCKED explicando cuál y por qué.")
    # Retrieval activo de memoria: el engine inyecta las memorias relevantes al
    # paso (match determinista por rutas/tags, cero API) — la lectura deja de
    # depender de que el agente vaya solo al INDEX. El reviewer recibe la unión
    # de archivos del workflow (revisa el diff completo).
    if project_ruta:
        try:
            from plotspace.core.memoria_recall import bloque_relevantes
            archivos_recall = archivos
            if paso.get('rol') == 'reviewer' and pasos_workflow:
                archivos_recall = sorted({a for p in pasos_workflow
                                          for a in (p.get('archivos') or [])})
            tarea += bloque_relevantes(project_ruta, archivos_recall, tarea,
                                       terminal_id=terminal_id)
        except Exception as e:
            print(f'[workflow] recall de memorias falló (sigo sin bloque): {e}')
    # Mailbox v2: los mensajes pendientes de esta terminal viajan en el prompt
    # de la tarea (frontera natural de entrega — quedan marcados entregados).
    try:
        from plotspace.core.mailbox import bloque_pendientes_para_tarea
        tarea += bloque_pendientes_para_tarea(terminal_id)
    except Exception as e:
        print(f'[workflow] mailbox pendientes falló (sigo sin bloque): {e}')
    # Protocolo de cierre del pane: fuente ÚNICA = el engine (antes lo escribía
    # el LLM en cada tarea → tokens duplicados y riesgo de drift). Solo si la
    # tarea no lo trae ya (el Reviewer define su propio veredicto TASK_*).
    if 'TASK_DONE' not in tarea:
        tarea += ("\n\nPROTOCOLO DE CIERRE: cuando termines escribí TASK_DONE "
                  "en una línea sola. Si te trabás, escribí TASK_BLOCKED "
                  "seguido del motivo en la misma línea. Si hubo error, "
                  "TASK_ERROR seguido del detalle.")
    # Cierre estructurado (sentinel-file): además del TASK_DONE en el pane, el
    # agente escribe un archivo — detección determinista y agnóstica al CLI.
    tarea += sentinel.instruccion_cierre(terminal_id)
    return tarea


def _ruta_proyecto_o_none(project_id: int):
    """Ruta del proyecto (para el recall de memorias). None si falla — el
    engine degrada a prompt sin bloque de memorias, jamás rompe el arranque."""
    try:
        conn = get_db()
        try:
            row = conn.execute('SELECT ruta FROM projects WHERE id = ?', (project_id,)).fetchone()
            return row['ruta'] if row else None
        finally:
            conn.close()
    except Exception:
        return None


async def _arrancar_pasos(pasos: list, indices: list, project_id: int, workflow_id: str):
    """Pone los pasos indicados en estado='running', les manda la tarea, e
    inicia el monitor de keywords de cada uno. Idempotente: ignora pasos sin
    terminal_id (ej. si no se pudo spawnear)."""
    if not indices:
        return
    from plotspace.routers.terminals import iniciar_monitor
    ruta = await asyncio.to_thread(_ruta_proyecto_o_none, project_id)
    for i in indices:
        paso = pasos[i]
        tid  = paso.get('terminal_id')
        if not tid:
            print(f'[workflow] paso_{i} sin terminal_id — skip')
            continue
        tarea = paso.get('tarea')   # acceso seguro: un paso truncado podría no tenerla
        if not tarea:
            print(f'[workflow] paso_{i} sin tarea — skip')
            continue
        paso['estado'] = 'running'
        paso['iniciado_ts'] = time.time()   # sello para el watchdog (sobrevive al restart)
        from plotspace.core import logs
        logs.evento('workflow_paso', workflow_id=workflow_id, paso=i, terminal_id=tid,
                    rol=paso.get('rol', 'builder'), agente=paso.get('agente'))
        # El paso arranca con su TERRITORIO ya tomado: los archivos que el plan
        # le asignó quedan reclamados a su nombre antes de la primera línea. Es
        # el momento más barato para evitar un choque — cuando todavía no
        # existe. Lo libre se concede; lo ajeno se informa y se sigue igual (el
        # reparto es una guía, no un candado que frene el workflow).
        archivos_paso = paso.get('archivos') or []
        if archivos_paso:
            try:
                from plotspace.core import territorio
                r = territorio.reclamar(project_id, tid, paso.get('agente') or '',
                                        archivos_paso)
                if r['ocupados']:
                    print(f'[workflow] paso_{i}: ya tienen dueño → '
                          + ', '.join(f"{o['patron']} ({o['de']})" for o in r['ocupados']))
            except Exception as e:
                print(f'[workflow] no pude reclamar el territorio del paso {i}: {e}')

        tarea = _tarea_engine_para_terminal(paso, tid, project_ruta=ruta,
                                            pasos_workflow=pasos)
        await send_to_agent(tid, tarea)
        iniciar_monitor(tid, project_id)
    _actualizar_workflow_db(
        workflow_id, estado='running', pasos=pasos,
        paso_actual=_progreso_workflow(pasos),
    )

    # Avisar al frontend que hay pasos corriendo (execution plan card + actividad
    # del mobile preview). Mismo shape que los workflow_update de task events.
    from plotspace.core.events import broadcaster
    await broadcaster.broadcast(project_id, {
        "type":        "workflow_update",
        "workflow_id": workflow_id,
        "paso_actual": _progreso_workflow(pasos),
        "total_pasos": len(pasos),
        "estado":      "running",
        "pasos":       pasos,
    })


async def ejecutar_workflow(workflow_data: dict, project_id: int, count_base: int) -> tuple:
    """Crea el workflow en DB, spawnea agentes y envía la primera tarea.
    Devuelve (workflow_card_dict, lista_terminales_creadas)."""
    workflow_id = uuid.uuid4().hex[:10]
    pasos       = workflow_data.get("pasos", [])
    nombre      = workflow_data.get("nombre", "Workflow")
    objetivo    = workflow_data.get("objetivo", "")
    ahora       = datetime.now().isoformat()

    # ── QUALITY GATE: paso Reviewer agregado por el ENGINE (no por el LLM,
    #    así la compuerta está garantizada). Arranca recién cuando TODOS los
    #    builders están done (special-case en _pasos_listos_para_arrancar) y
    #    el workflow solo se declara completado si el Reviewer da TASK_DONE.
    if pasos:
        pasos = list(pasos) + [_paso_reviewer(nombre, objetivo)]

    print(f'[workflow] Iniciando {workflow_id}: {nombre} ({len(pasos)} pasos, reviewer incluido)')

    # Obtener ruta del proyecto
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT ruta FROM projects WHERE id = ?', (project_id,))
        project_row  = cursor.fetchone()
        project_path = project_row['ruta'] if project_row else ''
    finally:
        conn.close()

    print(f'[workflow] Proyecto ruta: {project_path}')

    # Enriquecer pasos con estado inicial y terminal_id vacío
    for paso in pasos:
        paso.setdefault("estado", "pending")
        paso.setdefault("terminal_id", None)

    # Guardar en DB (sin terminal_ids aún)
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO workflows (id, project_id, nombre, objetivo, estado, pasos, paso_actual, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (workflow_id, project_id, nombre, objetivo, 'running', json.dumps(pasos), 0, ahora)
        )
        conn.commit()
    finally:
        conn.close()

    # Log sesiones tmux actuales antes de spawnear (async: subprocess.run
    # síncrono clavaba el event loop dentro de esta corutina — auditoría perf).
    tmux_antes = await _tmux_listar_sesiones()
    print(f'[workflow] Sesiones tmux antes: {tmux_antes or "(ninguna)"}')

    # Lazy imports para preparar proyecto/sesión tmux sin circular dependency
    from plotspace.routers.terminals import _preparar_proyecto, _crear_sesion_tmux

    # Asegurar proyecto + skills inyectadas en CLAUDE.md una sola vez
    await _preparar_proyecto(project_path, project_id=project_id)

    # Terminales reusables: un paso puede traer terminal_id para mandarle la
    # tarea a una terminal viva LIBRE en vez de spawnear otra. Guardas: activa,
    # sin paso running, y no reclamada dos veces en este workflow.
    conn = get_db()
    try:
        cur = conn.cursor()
        activas_ids = {r['id'] for r in cur.execute(
            'SELECT id FROM terminals WHERE project_id = ? AND activa = 1',
            (project_id,)).fetchall()}
        ocupadas = _terminales_ocupadas(cur, project_id)
    finally:
        conn.close()

    # Spawnear un terminal por cada paso: DB row + sesión tmux + lanzar IA.
    # Todos los agentes trabajan directo en project_path (sin worktrees);
    # coordinan leyendo CLAUDE.md y cada uno recibe su tarea por send-keys.
    created_terminals = []
    reclamadas: set = set()
    nuevos = 0
    for i, paso in enumerate(pasos):
        tid_pedido = paso.get('terminal_id')
        if _terminal_reusable(tid_pedido, activas_ids, ocupadas, reclamadas):
            reclamadas.add(tid_pedido)
            print(f'[workflow] paso_{i} REUSA la terminal #{tid_pedido} (sin spawn)')
            continue
        paso['terminal_id'] = None   # pedido inválido/ausente → spawn normal
        if count_base + nuevos >= MAX_TERMINALES:
            break
        nuevas = await _spawn_terminales(
            project_id   = project_id,
            name         = paso.get("agente", f"Agent #{i+1}"),
            ia_type      = paso.get("ia_type", "claude"),
            count        = 1,
            count_actual = count_base + nuevos,
        )
        if not nuevas:
            continue

        terminal_info = nuevas[0]
        tid           = terminal_info["id"]
        paso["terminal_id"] = tid
        created_terminals.append(terminal_info)
        nuevos += 1
        print(f'[workflow] Terminal DB creada: id={tid}, nombre={terminal_info["nombre"]}')

        # Comando del CLI computado ANTES de crear la sesión, para lanzarlo como
        # PROGRAMA del pane (SIN eco): ni el --session-id ni la línea larga se ven
        # tipeados en la terminal. claude se ata a su session_uuid → transcript
        # determinista <uuid>.jsonl → REVIVE con su contexto tras un corte de luz
        # (reconciliar recrea la sesión ya corriendo --resume; ver
        # terminals._comando_lanzamiento) + --dangerously-skip-permissions (agente
        # autónomo). Sin esto el claude del swarm corría con id aleatorio y su
        # conversación se perdía en el reboot.
        ia_type = paso.get("ia_type", "claude")
        # Mantener alineado con terminals._COMANDOS_CLI (qwen SIN --session-id
        # acá: el workflow arranca en frío y --chat-recording alcanza para que
        # grabe; claude suma --dangerously-skip-permissions por ser autónomo).
        ia_cmds = {
            "claude":      "claude --dangerously-skip-permissions",
            "codex":       "codex",
            "gemini":      "gemini",
            "opencode":    "opencode",
            "qwen":        "qwen --chat-recording",
            "antigravity": "agy",
            "grok":        "grok",
        }
        ia_cmd = ia_cmds.get(ia_type)
        if ia_cmd and ia_type == "claude":
            conn = get_db()
            try:
                r = conn.execute(
                    'SELECT session_uuid FROM terminals WHERE id = ?', (tid,)
                ).fetchone()
            finally:
                conn.close()
            su = r["session_uuid"] if r else None
            if su:
                ia_cmd = f"claude --session-id {su} --dangerously-skip-permissions"

        # Crear la sesión YA corriendo el CLI (sin eco). La espera de "agente listo"
        # (_esperar_agente_listo, más abajo) gatea la inyección de la tarea, así que
        # ya no hace falta el viejo sleep(3.5) + send-keys de lanzamiento visible.
        await _crear_sesion_tmux(tid, project_path, comando_cli=ia_cmd)
        print(f'[workflow] Sesión tmux jarvis_{tid} lista (CLI={ia_type or "shell"}) en {project_path}')

    # Log sesiones tmux después de spawnear (async, ver _tmux_listar_sesiones)
    tmux_despues = await _tmux_listar_sesiones()
    print(f'[workflow] Sesiones tmux después: {tmux_despues or "(ninguna)"}')

    # Guardar pasos con terminal_ids
    _actualizar_workflow_db(workflow_id, estado='running', pasos=pasos, paso_actual=0)

    # Esperar en paralelo a que cada agente esté listo (detecta y auto-acepta
    # el trust prompt de Claude Code). Antes era un sleep(5) ciego que mandaba
    # la tarea durante el trust prompt y se perdía.
    if created_terminals:
        print(f'[workflow] Esperando a que las IAs estén listas...')
        await asyncio.gather(*[
            _esperar_agente_listo(t['id'], timeout=25.0) for t in created_terminals
        ])

    # Disparar TODOS los pasos sin dependencias en paralelo.
    # Los que tienen depende_de='paso_N' arrancan cuando ese paso termine.
    listos = _pasos_listos_para_arrancar(pasos)
    print(f'[workflow] Pasos listos al inicio (paralelo): {listos}')
    await _arrancar_pasos(pasos, listos, project_id, workflow_id)

    workflow_card = {
        "id":          workflow_id,
        "nombre":      nombre,
        "objetivo":    objetivo,
        "pasos":       pasos,
        "paso_actual": 0,
        "estado":      "running",
    }
    return workflow_card, created_terminals


async def procesar_task_event_interno(terminal_id: int, event: str, project_id: int,
                                      motivo: str = ''):
    """Procesa un task event: avanza el workflow, notifica al frontend.

    `motivo` (del sentinel-file) es el "por qué" de un BLOCKED/ERROR: se guarda
    en el paso del workflow y viaja en los broadcasts — antes se perdía."""
    from plotspace.core.events import broadcaster

    # Task board: si esta terminal tenía tareas manuales asignadas (kanban),
    # el evento las resuelve (TASK_DONE → done, BLOCKED/ERROR → blocked).
    try:
        from plotspace.routers.tasks import resolver_tasks_por_evento
        await resolver_tasks_por_evento(terminal_id, event, project_id)
    except Exception as e:
        print(f'[tasks] error resolviendo tareas por evento: {e}')

    # Nombre de la terminal y workflow activo
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT nombre FROM terminals WHERE id = ?', (terminal_id,))
        row         = cursor.fetchone()
        term_nombre = row['nombre'] if row else f'Terminal #{terminal_id}'

        cursor.execute(
            "SELECT * FROM workflows WHERE project_id = ? AND estado IN ('running', 'paused') "
            "ORDER BY created_at DESC LIMIT 1",
            (project_id,)
        )
        wf_row = cursor.fetchone()
    finally:
        conn.close()

    # Broadcast del raw event — el frontend lo usa solo para la card, NO para el chat
    await broadcaster.broadcast(project_id, {
        "type":            "task_event",
        "event":           event,
        "terminal_id":     terminal_id,
        "terminal_nombre": term_nombre,
        "motivo":          (motivo or '')[:300],
    })

    if not wf_row:
        return

    wf    = dict(wf_row)
    pasos = json.loads(wf['pasos'])

    # Identificar QUÉ paso corresponde a este terminal_id (con paralelos no
    # alcanza con paso_actual). Si no se encuentra, usar paso_actual como fallback.
    paso_idx = next(
        (i for i, p in enumerate(pasos) if p.get('terminal_id') == terminal_id),
        wf['paso_actual'],
    )

    if event == "TASK_DONE":
        if 0 <= paso_idx < len(pasos):
            pasos[paso_idx]["estado"] = "done"

        # FIX (deadlock de paralelos): persistir el done INMEDIATAMENTE.
        # Antes solo se guardaba si recien_listos≠[] o al terminar todo: con
        # builders en paralelo, el primer TASK_DONE no libera nada (el
        # reviewer espera a TODOS) → no persistía → el segundo TASK_DONE
        # releía al primero como 'running' → nada se liberaba nunca.
        _actualizar_workflow_db(
            wf['id'], estado=wf['estado'], pasos=pasos,
            paso_actual=_progreso_workflow(pasos),
        )

        # Disparar los pasos que recién quedaron desbloqueados por este TASK_DONE
        recien_listos = _pasos_listos_para_arrancar(pasos)
        if recien_listos:
            print(f'[workflow] paso_{paso_idx} done → disparando dependientes: {recien_listos}')
            await _arrancar_pasos(pasos, recien_listos, project_id, wf['id'])

        await broadcaster.broadcast(project_id, {
            "type":        "workflow_update",
            "workflow_id": wf['id'],
            "paso_actual": _progreso_workflow(pasos),
            "total_pasos": len(pasos),
            "estado":      "running" if not _workflow_terminado(pasos) else "done",
            "pasos":       pasos,
        })

        if _workflow_terminado(pasos):
            # ── Todos los pasos terminaron (done/blocked/error) ────────────────
            _actualizar_workflow_db(
                wf['id'], estado='done', pasos=pasos,
                paso_actual=_progreso_workflow(pasos),
            )

            # Los agentes trabajan directo en main: no hay merge que hacer.
            # Solo lanzamos preview si el proyecto tiene frontend.
            conn = get_db()
            try:
                cursor = conn.cursor()
                cursor.execute('SELECT ruta FROM projects WHERE id = ?', (project_id,))
                proj_row     = cursor.fetchone()
                project_path = proj_row['ruta'] if proj_row else ''
            finally:
                conn.close()

            preview_url = None
            if project_path:
                # A un thread: _detectar_y_lanzar_preview es SYNC y espera hasta ~1s
                # (time.sleep + bind-check del http.server) → si corriera en el event
                # loop congelaría terminales/WS/pollers ese segundo. (audit latencia)
                preview_url = await asyncio.to_thread(
                    _detectar_y_lanzar_preview, project_id, project_path)

            # Honestidad del cierre: "completado" solo si TODOS los pasos
            # están done (incluido el Reviewer = review aprobada). Antes se
            # declaraba victoria aunque hubiera pasos blocked/error.
            todos_ok     = all(p.get('estado') == 'done' for p in pasos)
            hubo_review  = any(p.get('rol') == 'reviewer' for p in pasos)
            if todos_ok:
                msg_final = f"Señor, {wf['nombre']} completado en main."
                if hubo_review:
                    msg_final += " Review de calidad: APROBADA."
            else:
                fallidos = sum(1 for p in pasos if p.get('estado') in ('blocked', 'error'))
                msg_final = (f"Señor, {wf['nombre']} terminó con {fallidos} paso(s) "
                             "bloqueado(s)/con error — revisá el board de tareas antes de dar por bueno el resultado.")
            if preview_url:
                msg_final += f" Preview en {preview_url}"

            await broadcaster.broadcast(project_id, {
                "type":        "workflow_done",
                "message":     msg_final,
                "preview_url": preview_url,
            })

    elif event == "TASK_BLOCKED":
        if paso_idx < len(pasos):
            pasos[paso_idx]["estado"] = "blocked"
            if motivo:
                pasos[paso_idx]["motivo"] = motivo
        _actualizar_workflow_db(wf['id'], estado='paused', pasos=pasos, paso_actual=paso_idx)
        detalle = f": {motivo[:300]}" if motivo else ""
        await broadcaster.broadcast(project_id, {
            "type":    "orquestador_mensaje",
            "message": f"⚠️ {term_nombre} está bloqueado en el paso {paso_idx + 1}{detalle}. ¿Cómo continuamos?",
        })
        # Etapa 5: en vez de quedar esperando al humano, el orquestador se
        # llama solo con el motivo y re-instruye (guardas anti-loop adentro).
        _lanzar_auto_intervencion(wf, pasos, paso_idx, project_id,
                                  'TASK_BLOCKED', motivo, term_nombre)

    elif event == "TASK_ERROR":
        if paso_idx < len(pasos):
            ia_type = pasos[paso_idx].get("ia_type", "claude")
            pasos[paso_idx]["estado"] = "error"
            if motivo:
                pasos[paso_idx]["motivo"] = motivo
            # Solo terminales del PROPIO workflow: jamás reasignar a una terminal
            # ajena (la claude personal del usuario recibiría la tarea tipeada).
            otro = await _buscar_agente_disponible(
                project_id, terminal_id, ia_type,
                permitidas={p.get("terminal_id") for p in pasos if p.get("terminal_id")},
            )

            if otro:
                pasos[paso_idx]["terminal_id"] = otro
                pasos[paso_idx]["estado"]       = "running"
                pasos[paso_idx]["iniciado_ts"]  = time.time()   # re-sello para el watchdog
                _actualizar_workflow_db(wf['id'], estado='running', pasos=pasos, paso_actual=paso_idx)
                ruta_reasig = await asyncio.to_thread(_ruta_proyecto_o_none, project_id)
                await send_to_agent(otro, _tarea_engine_para_terminal(
                    pasos[paso_idx], otro, project_ruta=ruta_reasig, pasos_workflow=pasos))
                from plotspace.routers.terminals import iniciar_monitor
                iniciar_monitor(otro, project_id)
                detalle = f" Motivo: {motivo[:300]}." if motivo else ""
                await broadcaster.broadcast(project_id, {
                    "type":    "orquestador_mensaje",
                    "message": f"⚡ Error en {term_nombre}.{detalle} Tarea reasignada al agente #{otro}.",
                })
            else:
                _actualizar_workflow_db(wf['id'], estado='paused', pasos=pasos, paso_actual=paso_idx)
                detalle = f" Motivo: {motivo[:300]}." if motivo else ""
                await broadcaster.broadcast(project_id, {
                    "type":    "orquestador_mensaje",
                    "message": f"❌ Error en {term_nombre} y no hay otro agente disponible.{detalle} Workflow pausado.",
                })
                # Etapa 5: sin agente para reasignar → que decida el orquestador
                # (re-instruir con workflow chico, o preguntar UNA cosa).
                _lanzar_auto_intervencion(wf, pasos, paso_idx, project_id,
                                          'TASK_ERROR', motivo, term_nombre)


async def send_to_agent(terminal_id: int, mensaje: str):
    """Envía un texto al agente via tmux send-keys."""
    mensaje = f'Lee tu CLAUDE.md primero. Luego: {mensaje}'
    session = f'jarvis_{terminal_id}'
    print(f'[send_to_agent] → {session}: {mensaje[:100]}')

    # Verificar que la sesión existe antes de enviar
    if not backend().existe(terminal_id):
        print(f'[send_to_agent] ERROR: sesión {session} no existe — abortando')
        return

    # La tarea viaja como PASTE (buffer de tmux), no tipeada. `send-keys -l`
    # manda los saltos de línea como LF crudos al pty —verificado—, así que un
    # prompt largo (memorias + mailbox + protocolo de cierre) podía fragmentarse
    # en varios envíos. El buffer lo entrega entero y de una, y `-p` deja que
    # TMUX decida si envolverlo en bracketed paste según lo que pidió la app.
    # Bonus: el texto nunca pasa por el lookup de nombres de tecla, así que una
    # línea del MAILBOX con 'Enter' o 'C-c' adentro no se interpreta como tecla.
    err = b''
    for argv in comandos_pegar_tarea(session, mensaje):
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, e = await proc.communicate()
        if proc.returncode != 0:
            err = e
            print(f'[send_to_agent] ERROR rc={proc.returncode}: '
                  f'{e.decode(errors="replace").strip()}')
            break
    else:
        print(f'[send_to_agent] OK → {session} ({len(mensaje)} chars pegados)')
    backend().enviar_tecla(terminal_id, 'Enter')

    # Esperar 1s y enviar Enter adicional para que Claude procese la tarea
    await asyncio.sleep(1)
    backend().enviar_tecla(terminal_id, 'Enter')

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
                'INSERT INTO task_events (terminal_id, project_id, event, timestamp, workflow_id) '
                'VALUES (?, ?, ?, ?, ?)',
                (terminal_id, pid, 'SENT', ahora, None)
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


async def _sesion_tmux_viva(terminal_id: int) -> bool:
    """True si la sesión tmux jarvis_{terminal_id} existe."""
    try:
        return backend().existe(terminal_id)
    except Exception:
        return False


# Un workflow 'running'/'paused' más viejo que esto es un zombie: el agente ya
# murió y nadie va a escribir TASK_DONE. Los agentes no corren un workflow 12h.
_WORKFLOW_STALE_HORAS = 12


async def reanudar_workflows():
    """Al arrancar, reinicia los monitores de los workflows en curso.
    Corre como background task — no bloquea el startup.

    Reconciliación 'hacia abajo': antes revivía el monitor de CUALQUIER
    workflow 'running'/'paused' en cada boot, incluidos zombies de semanas
    atrás → pollers fantasma sobre sesiones muertas para siempre. Ahora los
    stale (viejos o sin sesión tmux viva) se marcan 'error' y no se reaniman."""
    # DB SIEMPRE fuera del event loop (deep work 2026-07-11): esto corre al
    # boot, en paralelo con el re-attach de N terminales — un SELECT/UPDATE
    # síncrono acá puede colisionar con otro writer y clavar el ÚNICO loop
    # hasta el busy_timeout de 5s (= scroll congelado en TODAS las terminales).
    def _leer_workflows_pendientes():
        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM workflows WHERE estado IN ('running', 'paused')")
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

    try:
        rows = await asyncio.to_thread(_leer_workflows_pendientes)

        ahora = datetime.now()
        for wf in rows:
            pasos    = json.loads(wf['pasos'])
            paso_idx = wf['paso_actual']

            # ¿Demasiado viejo para seguir vivo?
            try:
                creado = datetime.fromisoformat(wf['created_at'])
                viejo  = (ahora - creado) > timedelta(hours=_WORKFLOW_STALE_HORAS)
            except Exception:
                viejo = False

            # Re-armar el monitor de CADA paso 'running' (no solo paso_actual): en workflows
            # con pasos PARALELOS (varios builders a la vez) solo se re-monitoreaba uno → los
            # otros nunca detectaban su TASK_DONE y el workflow se colgaba tras un reinicio.
            running = [p for p in pasos if p.get('estado') == 'running' and p.get('terminal_id')]
            if not running and paso_idx < len(pasos) and pasos[paso_idx].get('terminal_id'):
                running = [pasos[paso_idx]]   # compat: workflows viejos sin 'estado' por paso

            vivos = []
            for p in running:
                if await _sesion_tmux_viva(p['terminal_id']):
                    vivos.append(p)

            if viejo or not vivos:
                # UPDATE en to_thread: es un writer — en el loop podía bloquear
                # hasta 5s (busy_timeout) si colisionaba con otro writer al boot.
                await asyncio.to_thread(_actualizar_workflow_db, wf['id'],
                                        estado='error', pasos=pasos,
                                        paso_actual=paso_idx)
                print(f'[startup] Workflow zombie {wf["id"]} → error (no se reanima)')
                continue

            from plotspace.routers.terminals import iniciar_monitor
            for p in vivos:
                iniciar_monitor(p['terminal_id'], wf['project_id'])
                print(f'[startup] Reanudando monitor: workflow {wf["id"]}, terminal {p["terminal_id"]}')
    except Exception as e:
        print(f'[startup] Error en reanudar_workflows: {e}')


# ─── Preview server (auto-launch cuando hay frontend) ─────────────────────────

# Registro: project_id → (subprocess.Popen, url)
_preview_servers: dict = {}


def _puertos_ocupados_por_jarvis() -> set:
    """Puertos que JARVIS ya asignó a otros proyectos (aunque el bind real
    del http.server todavía no haya ocurrido por la race entre check y Popen)."""
    from urllib.parse import urlparse
    ocupados = set()
    for proc, url in _preview_servers.values():
        # Si el proc murió, no contar
        if proc.poll() is not None:
            continue
        try:
            p = urlparse(url).port
            if p: ocupados.add(p)
        except Exception:
            continue
    return ocupados


def _puerto_libre(start: int, end: int, excluir: set = None) -> Optional[int]:
    """Busca un puerto TCP libre en [start, end). Excluye puertos que
    JARVIS ya asignó a OTROS previews del registry — eso evita la race
    entre el check del socket y el lanzamiento del Popen del http.server.
    `excluir` agrega puertos que ya se intentaron y fallaron en este lanzamiento
    (sin esto, el reintento podía reelegir el mismo puerto que recién falló)."""
    import socket
    ocupados_jarvis = _puertos_ocupados_por_jarvis()
    excluir = excluir or set()
    for p in range(start, end):
        if p in ocupados_jarvis or p in excluir:
            continue
        try:
            s = socket.socket()
            s.bind(('127.0.0.1', p))
            s.close()
            return p
        except OSError:
            continue
    return None


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


def _preview_urls_activas(project_id: int) -> list:
    """TODAS las URLs de preview vivas del proyecto, en orden para abrir como
    pestañas: primero el http.server propio (si Jarvis lanzó uno), después los
    dev servers de agentes detectados (más viejo → más nuevo, así la pestaña
    recién abierta queda activa)."""
    from plotspace.core.dev_detect import urls_detectadas
    urls = []
    entry = _preview_servers.get(project_id)
    if entry:
        proc, url = entry
        if proc.poll() is None:
            urls.append(url)
        else:
            _preview_servers.pop(project_id, None)
    for u in urls_detectadas(project_id):
        if u not in urls:
            urls.append(u)
    return urls


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


def _detectar_y_lanzar_preview(project_id: int, project_path: str) -> Optional[str]:
    """Si el proyecto tiene HTML al root o en frontend/, lanza http.server en
    background y devuelve la URL. Reusa el server si ya existe uno para este
    proyecto. Devuelve None si no hay nada que servir."""
    # ¿Ya hay uno corriendo para este proyecto?
    existing = _preview_servers.get(project_id)
    if existing:
        proc, url = existing
        if proc.poll() is None:
            return url
        _preview_servers.pop(project_id, None)

    # Buscar el directorio que tiene HTML
    candidatos = [project_path, os.path.join(project_path, 'frontend')]
    preview_root = None
    for ruta in candidatos:
        if not os.path.isdir(ruta):
            continue
        try:
            archivos = os.listdir(ruta)
        except OSError:
            continue
        if any(a.endswith('.html') for a in archivos):
            preview_root = ruta
            break

    if not preview_root:
        return None

    import socket, time
    # Reintento ACOTADO (antes era recursión sin tope: en un host con contención
    # de puertos 8080-8120 podía encadenar recursiones → RecursionError y dejar
    # http.server zombies). Hasta 5 puertos distintos, marcando los que ya
    # fallaron para no reelegirlos en este mismo intento.
    ya_intentados: set = set()
    for _ in range(5):
        puerto = _puerto_libre(8080, 8120, excluir=ya_intentados)
        if not puerto:
            print('[preview] No hay puertos libres en 8080-8120')
            return None
        ya_intentados.add(puerto)
        try:
            proc = subprocess.Popen(
                ['python3', '-m', 'http.server', str(puerto), '--bind', '127.0.0.1'],
                cwd=preview_root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            print(f'[preview] Error lanzando http.server: {e}')
            return None

        # Esperar a que el http.server haya bindeado. Si otro proceso agarró el
        # puerto entre el bind-check y el Popen, nuestro http.server crashea
        # silencioso → lo detectamos y probamos otro puerto.
        bindeado = False
        for _ in range(20):  # hasta 1s en steps de 50ms
            time.sleep(0.05)
            if proc.poll() is not None:
                break  # el proceso murió, no se pudo bindear
            try:
                s = socket.socket()
                s.settimeout(0.1)
                s.connect(('127.0.0.1', puerto))
                s.close()
                bindeado = True
                break
            except OSError:
                continue

        if not bindeado:
            try: proc.terminate()
            except Exception: pass
            print(f'[preview] http.server no bindeó {puerto} — probando otro puerto')
            continue

        url = f"http://localhost:{puerto}/"
        _preview_servers[project_id] = (proc, url)
        print(f'[preview] http.server lanzado en {preview_root} → {url}')
        return url

    print('[preview] No pude bindear ningún puerto tras 5 intentos')
    return None


# ─── Helpers internos ──────────────────────────────────────────────────────────

async def _tmux_listar_sesiones() -> str:
    """`tmux list-sessions` async con timeout (diagnóstico). Antes era
    subprocess.run síncrono dentro de la corutina del workflow → clavaba el
    event loop. Devuelve el stdout strip-eado, o '' si no hay sesiones/falla."""
    try:
        out = '\n'.join(sorted(
            await asyncio.to_thread(backend().listar_sesiones))).encode()
    except Exception:
        try: proc.kill()      # timeout → matar el subprocess tmux para no dejarlo huérfano
        except Exception: pass
        return ''
    return out.decode(errors='replace').strip() if out else ''


def _actualizar_workflow_db(workflow_id: str, estado: str, pasos: list, paso_actual: int):
    conn = get_db()
    try:
        conn.execute(
            'UPDATE workflows SET estado = ?, pasos = ?, paso_actual = ? WHERE id = ?',
            (estado, json.dumps(pasos), paso_actual, workflow_id)
        )
        conn.commit()
    finally:
        conn.close()


def _terminales_ocupadas(cursor, project_id: int) -> set:
    """IDs de terminales asignadas a un paso 'running' de algún workflow activo.
    Reasignar a una de ellas pisaba dos tareas a la vez."""
    ocupadas: set = set()
    cursor.execute(
        "SELECT pasos FROM workflows WHERE project_id = ? AND estado IN ('running', 'paused')",
        (project_id,)
    )
    for r in cursor.fetchall():
        try:
            for p in json.loads(r['pasos']):
                if p.get('estado') == 'running' and p.get('terminal_id'):
                    ocupadas.add(p['terminal_id'])
        except (json.JSONDecodeError, TypeError):
            continue
    return ocupadas


async def _buscar_agente_disponible(project_id: int, excluir_id: int, ia_type: str,
                                    permitidas=None) -> Optional[int]:
    """`permitidas` (set de terminal_ids) acota la búsqueda a las terminales del
    PROPIO workflow: la reasignación de un TASK_ERROR no puede caer en una
    terminal ajena — p.ej. la sesión claude PERSONAL del usuario, a la que
    send_to_agent le tipearía la tarea + un Enter ciego encima de lo que tenga
    en pantalla (auditoría 2026-07-02). None = sin restricción (compat)."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        ocupadas = _terminales_ocupadas(cursor, project_id)
        cursor.execute(
            'SELECT id FROM terminals WHERE project_id = ? AND tipo_ia = ? AND activa = 1 AND id != ?',
            (project_id, ia_type, excluir_id)
        )
        # Primera terminal del tipo correcto que NO esté ocupada en otro paso.
        for row in cursor.fetchall():
            if row['id'] in ocupadas:
                continue
            if permitidas is not None and row['id'] not in permitidas:
                continue
            return row['id']
        return None
    finally:
        conn.close()


def _formatear_estado(terminals: list, project_id: Optional[int] = None) -> str:
    if not terminals:
        return "No hay terminales activas."

    # Cross-referenciar cada terminal con el workflow/paso que la creó
    # para que el orquestador sepa "para qué es cada terminal".
    mapa_workflow: dict = {}
    if project_id is not None:
        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, nombre, pasos FROM workflows WHERE project_id = ? "
                "ORDER BY created_at DESC LIMIT 20",
                (project_id,)
            )
            for r in cursor.fetchall():
                try:
                    pasos = json.loads(r['pasos'])
                except (json.JSONDecodeError, TypeError):
                    continue
                for p in pasos:
                    tid = p.get('terminal_id')
                    if tid and tid not in mapa_workflow:
                        mapa_workflow[tid] = {
                            'workflow': r['nombre'],
                            'agente':   p.get('agente', '?'),
                            'estado':   p.get('estado', '?'),
                        }
        finally:
            conn.close()

    # Estado VIVO por terminal (fase de agent_watch) + dueños de archivos
    # (Agents Live): el orquestador rutea sin pisar a nadie. Degradan a nada.
    fases = {t['id']: _fase_terminal(t['id']) for t in terminals}
    duenos: dict = {}
    if terminals:
        try:
            from plotspace.core import agent_live
            rows = [{'tid': t['id'], 'tnombre': t['nombre'],
                     'tipo_ia': t.get('tipo_ia')} for t in terminals]
            snap = agent_live.snapshot(project_id, rows) if project_id is not None else {}
            for a in snap.get('agentes', []):
                propios = [f['path'] for f in a.get('archivos', []) if f.get('dueno')]
                if propios:
                    duenos[a['terminal_id']] = propios
        except Exception:
            pass
    return _formatear_estado_core(terminals, mapa_workflow, fases=fases, duenos=duenos)


def _formatear_estado_core(terminals: list, mapa_workflow: dict,
                           fases: dict = None, duenos: dict = None) -> str:
    """Núcleo PURO del bloque [Estado actual]: una línea por terminal con
    id/nombre/CLI + fase viva + rol de workflow (o libre) + archivos propios."""
    if not terminals:
        return "No hay terminales activas."
    fases = fases or {}
    duenos = duenos or {}
    _FASES = {'trabajando': '🟢 trabajando AHORA', 'idle': '⚪ quieta',
              'arrancando': '⏳ arrancando'}
    lineas = [f"Terminales activas: {len(terminals)}"]
    for t in terminals:
        seg = [f"ID {t['id']}: {t['nombre']} ({t['tipo_ia']})"]
        fase = _FASES.get(fases.get(t['id']) or '')
        if fase:
            seg.append(fase)
        info = mapa_workflow.get(t['id'])
        if info:
            seg.append(f"rol '{info['agente']}' del workflow "
                       f"'{info['workflow']}' ({info['estado']})")
        else:
            seg.append("terminal libre")
        propios = duenos.get(t['id']) or []
        if propios:
            vista = ', '.join(propios[:3])
            if len(propios) > 3:
                vista += f' (+{len(propios) - 3} más)'
            seg.append(f'dueña de: {vista}')
        lineas.append('  - ' + ' — '.join(seg))
    return "\n".join(lineas)


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


def _formatear_workflows_recientes(project_id: int, limite: int = 3) -> str:
    """Últimos N workflows del proyecto con su estado. Útil para que Haiku
    sepa qué se hizo antes y no duplique trabajo."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT nombre, objetivo, estado, pasos, paso_actual, created_at '
            'FROM workflows WHERE project_id = ? '
            'ORDER BY created_at DESC LIMIT ?',
            (project_id, limite)
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    if not rows:
        return ""
    lineas = []
    for r in rows:
        try:
            pasos = json.loads(r['pasos'])
        except (json.JSONDecodeError, TypeError):
            pasos = []
        total = len(pasos)
        hechos = sum(1 for p in pasos if p.get('estado') == 'done')
        objetivo = (r['objetivo'] or '')[:80]
        fecha = (r['created_at'] or '')[:16].replace('T', ' ')
        lineas.append(
            f"  - [{r['estado']}] {r['nombre']} ({hechos}/{total} pasos) — "
            f"{objetivo} ({fecha})"
        )
    return "\n".join(lineas)


async def _spawn_terminales(project_id: int, name: str, ia_type: str,
                             count: int, count_actual: int) -> list:
    # Lazy import (circular orchestrator ↔ terminals, patrón documentado).
    # resolver_nombre_unico: ANTES se numeraba por CONTEO de activas
    # (count_actual + i + 1) — al borrar terminales los números se reusaban
    # y nacían homónimas ("Claude Code #3" ×2): el mailbox y los permisos
    # de Agents Live resuelven por nombre, así que la coordinación entera
    # quedaba ambigua.
    from plotspace.routers.terminals import resolver_nombre_unico, _nombres_activos

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
            cursor.execute(
                'INSERT INTO terminals (project_id, nombre, tipo_ia, activa, fecha_creacion) VALUES (?, ?, ?, 1, ?)',
                (project_id, nombre, ia_type, ahora),
            )
            conn.commit()
            tid = cursor.lastrowid
            cursor.execute('SELECT * FROM terminals WHERE id = ?', (tid,))
            creadas.append(dict(cursor.fetchone()))
    finally:
        conn.close()

    # Push instantáneo a Agents Live: los agentes que spawnea el orquestador
    # (workflows / spawn_terminal) aparecen YA en la pestaña Live, sin esperar
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
        return f"no encontré la terminal #{terminal_id} activa"
    if project_id is not None and fila['project_id'] != project_id:
        _logs.evento('cierre_rechazado', nivel='warn', terminal_id=terminal_id,
                     accion='close_terminal', motivo='otro_proyecto',
                     project_id=project_id)
        return (f"no cerré la terminal #{terminal_id} ({fila['nombre']}): "
                "pertenece a OTRO proyecto")

    clave = ('t', terminal_id)
    if _fase_terminal(terminal_id) == 'trabajando' and not _insistencia_cierre(clave):
        _sellar_rechazo_cierre(clave)
        _logs.evento('cierre_rechazado', nivel='warn', terminal_id=terminal_id,
                     accion='close_terminal', motivo='trabajando')
        return (f"no cerré {fila['nombre']}: está TRABAJANDO ahora mismo — "
                "si igual querés cerrarla, repetime la orden")
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
