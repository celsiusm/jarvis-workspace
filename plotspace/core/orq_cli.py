"""
orq_cli — el orquestador corre con la SUSCRIPCIÓN de Claude, no con la API.

Transport del chat orquestador vía `claude -p` headless: el subproceso se
autentica con la cuenta de suscripción ACTIVA (OAuth de ~/.claude — la misma
que usan los agentes de las terminales), así que cada mensaje del orquestador
sale de la suscripción y no de tokens de API pagos. La API con key queda solo
como vía de escape (`ORQUESTADOR_MOTOR=api` en el router).

Receta medida en este box (2026-07-19, CLI 2.1.215): `--safe-mode` (sin
plugins/MCPs/hooks: 73s→26s de wall) + autoupdater/telemetría off por env
(26s→13s). `--output-format stream-json` EXIGE `--verbose`. `--json-schema`
da salida estructurada validada (campo `structured_output` en el result) —
la primera llamada con un schema nuevo paga compilación (~1 min, cache 24h).

Bonus de agencia: `--tools "Read,Glob,Grep"` con cwd=proyecto le da al
orquestador OJOS DE SOLO LECTURA — el harness de claude corre el loop de
tools solo; para mensajes triviales responde directo sin explorar.

Capa pura (testeable sin subproceso): `_env_suscripcion`, `_argv`,
`prompt_desde_mensajes`, `eventos_desde_lineas`. Capa impura: `stream()`.
"""
import asyncio
import json
import os
import shutil

BIN = 'claude'
TOOLS_LECTURA = 'Read,Glob,Grep'
TIMEOUT_S = float(os.environ.get('ORQ_CLI_TIMEOUT', '240'))


class OrqCliError(RuntimeError):
    """El CLI falló (no instalado, sin login, crash, timeout). El mensaje es
    apto para mostrarse en el chat."""


# ─── Capa pura ────────────────────────────────────────────────────────────────

def _env_suscripcion(base: dict = None) -> dict:
    """Env del subproceso: SIN credenciales de API (fuerza el OAuth de la
    suscripción activa) y sin ruido de red por llamada. No muta `base`."""
    env = dict(os.environ if base is None else base)
    for k in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN',
              'CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT'):
        env.pop(k, None)
    env.update(
        DISABLE_AUTOUPDATER='1',
        DISABLE_TELEMETRY='1',
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1',
    )
    return env


def _argv(prompt, system_prompt: str, model: str,
          schema: dict = None, tools: str = TOOLS_LECTURA) -> list:
    """Línea de comando del claude headless (receta verificada en vivo).

    `prompt=None` → el prompt viaja por STDIN (es lo que usa `stream`): con el
    contexto completo + historial + archivos pegados el prompt puede pasar los
    128 KB que Linux permite por argumento (MAX_ARG_STRLEN → E2BIG al spawnear)."""
    argv = [
        BIN, '-p', *([prompt] if prompt is not None else []),
        '--model', model,
        '--safe-mode',                       # sin plugins/MCPs/hooks/CLAUDE.md
        '--verbose',                         # requisito de stream-json
        '--output-format', 'stream-json',
        '--include-partial-messages',        # deltas de texto en vivo
        '--system-prompt', system_prompt,
        '--tools', tools if tools else '',
    ]
    if schema:
        # sort_keys → string estable → hit del cache de compilación del schema
        argv += ['--json-schema', json.dumps(schema, ensure_ascii=False,
                                             sort_keys=True)]
    return argv


def prompt_desde_mensajes(mensajes: list) -> str:
    """Aplana la lista `messages` multi-turno en UN prompt (claude -p recibe
    un solo texto): los turnos previos van como transcript etiquetado y el
    mensaje actual (con sus bloques de contexto) cierra el prompt."""
    def _texto(contenido) -> str:
        if isinstance(contenido, str):
            return contenido
        # bloques (p.ej. imagen por la vía API): concatenar solo el texto
        return '\n'.join(b.get('text', '') for b in contenido
                         if isinstance(b, dict) and b.get('type') == 'text')

    if not mensajes:
        return ''
    if len(mensajes) == 1:
        return _texto(mensajes[0].get('content'))

    partes = ['[Historial del thread — turnos previos de esta conversación]']
    for m in mensajes[:-1]:
        rol = 'Usuario' if m.get('role') == 'user' else 'Jarvis (vos)'
        partes.append(f'<{rol}>\n{_texto(m.get("content"))}\n</{rol}>')
    partes.append('[Fin del historial — el mensaje ACTUAL viene ahora]\n')
    partes.append(_texto(mensajes[-1].get('content')))
    return '\n'.join(partes)


def eventos_desde_lineas(lineas) -> 'list[dict]':
    """Parser PURO del stream-json de claude -p. Por cada línea emite:
      {'tipo': 'reinicio'}                  → empezó un mensaje nuevo del
                                              asistente (resetear acumulador)
      {'tipo': 'delta', 'texto': str}       → chunk de texto del mensaje
      {'tipo': 'resultado', ...}            → final: texto canónico (prefiere
                                              structured_output), tokens,
                                              num_turns, error, subtype
    Las líneas no-JSON (stderr mezclado) o de tipos irrelevantes se saltean."""
    for linea in lineas:
        linea = (linea or '').strip()
        if not linea:
            continue
        try:
            d = json.loads(linea)
        except (json.JSONDecodeError, ValueError):
            continue
        t = d.get('type')
        if t == 'stream_event':
            ev = d.get('event') or {}
            et = ev.get('type')
            if et == 'message_start':
                yield {'tipo': 'reinicio'}
            elif et == 'content_block_delta':
                delta = ev.get('delta') or {}
                if delta.get('type') == 'text_delta' and delta.get('text'):
                    yield {'tipo': 'delta', 'texto': delta['text']}
        elif t == 'assistant':
            # Mensaje completo del asistente: de acá salen las tools que usa
            # (Read/Glob/Grep) para mostrarle al usuario qué está mirando.
            for b in ((d.get('message') or {}).get('content') or []):
                if isinstance(b, dict) and b.get('type') == 'tool_use':
                    yield {'tipo': 'herramienta', 'nombre': b.get('name') or '?',
                           'detalle': _detalle_tool(b.get('input') or {})}
        elif t == 'result':
            estructurado = d.get('structured_output')
            if isinstance(estructurado, dict):
                texto = json.dumps(estructurado, ensure_ascii=False)
            else:
                texto = d.get('result') or ''
            uso = d.get('usage') or {}
            yield {
                'tipo':          'resultado',
                'texto':         texto,
                'estructurado':  estructurado if isinstance(estructurado, dict) else None,
                'input_tokens':  uso.get('input_tokens') or 0,
                'output_tokens': uso.get('output_tokens') or 0,
                'num_turns':     d.get('num_turns') or 0,
                'error':         bool(d.get('is_error')),
                'subtype':       d.get('subtype') or '',
            }


def _detalle_tool(entrada: dict) -> str:
    """Lo más legible del input de una tool: el archivo, el patrón o la ruta."""
    for clave in ('file_path', 'pattern', 'path', 'query'):
        v = entrada.get(clave)
        if isinstance(v, str) and v:
            return v[:160]
    return ''


# ─── Capa impura: el subproceso ──────────────────────────────────────────────

async def stream(prompt: str, system_prompt: str, model: str, cwd: str = None,
                 schema: dict = None, tools: str = TOOLS_LECTURA,
                 timeout_s: float = None):
    """Async generator sobre `claude -p`: re-emite los eventos del parser.
    stderr va mezclado a stdout (el parser saltea lo no-JSON) y se retiene
    la cola de líneas crudas para diagnosticar si no llega `result`.
    Timeout DURO de pared: al vencer se mata el proceso (sin --max-turns en
    esta versión del CLI, este es el freno real)."""
    if not shutil.which(BIN):
        raise OrqCliError('el CLI `claude` no está instalado o no está en el PATH')
    if timeout_s is None:
        timeout_s = TIMEOUT_S

    proc = await asyncio.create_subprocess_exec(
        *_argv(None, system_prompt, model, schema=schema, tools=tools),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd if cwd and os.path.isdir(cwd) else None,
        env=_env_suscripcion(),
    )
    try:
        proc.stdin.write((prompt or '').encode('utf-8'))
        await proc.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass            # el CLI murió al arrancar: el diagnóstico sale de stdout
    finally:
        proc.stdin.close()

    deadline = asyncio.get_event_loop().time() + timeout_s
    cola_cruda: list = []       # últimas líneas no parseadas (diagnóstico)
    hubo_resultado = False
    try:
        while True:
            restante = deadline - asyncio.get_event_loop().time()
            if restante <= 0:
                raise OrqCliError(f'el orquestador tardó más de {int(timeout_s)}s — corté la llamada')
            try:
                linea = await asyncio.wait_for(proc.stdout.readline(), timeout=restante)
            except asyncio.TimeoutError:
                raise OrqCliError(f'el orquestador tardó más de {int(timeout_s)}s — corté la llamada')
            if not linea:
                break
            texto_linea = linea.decode('utf-8', errors='replace')
            emitido = False
            for ev in eventos_desde_lineas([texto_linea]):
                emitido = True
                if ev['tipo'] == 'resultado':
                    hubo_resultado = True
                yield ev
            if not emitido and texto_linea.strip():
                cola_cruda.append(texto_linea.strip())
                del cola_cruda[:-6]
        rc = await proc.wait()
        if not hubo_resultado:
            detalle = ' · '.join(cola_cruda[-3:]) or f'exit={rc} sin salida'
            raise OrqCliError(f'claude -p terminó sin resultado ({detalle})')
    finally:
        if proc.returncode is None:
            proc.kill()
