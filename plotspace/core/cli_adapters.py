# plotspace/core/cli_adapters.py
"""Instaladores de los ADAPTADORES de provenance por CLI no-Claude.

Claude Code reporta cada edición por su hook PostToolUse (core/hooks_cli.py). Los
demás CLIs necesitan su propio adaptador que POStee al MISMO `/api/swarm/op` —
así un swarm mixto (Claude + Codex + opencode) coordina de verdad y los no-Claude
dejan de ser fantasmas (sin propiedad, sin colisiones, invisibles en `jv estado`,
sus hunks arrastrados por `commit_propio`).

  opencode → un PLUGIN JS (hook `tool.execute.after`) en `~/.config/opencode/plugin/`.
             Un solo archivo cubre a TODOS los agentes opencode que se lancen en
             tmux. Fuente: `scripts/opencode_jarvis_plugin.js`.

(Codex NO va acá: sus ediciones van por la herramienta `apply_patch`, FUERA del
gate de los hooks de tool → se resuelve tailando el rollout JSONL, en un módulo
aparte. Ver el roadmap del enjambre.)

Reglas del instalador (las mismas que hooks_cli, aprendidas de romper cosas):
  · Idempotente: correrlo mil veces deja UN archivo, y solo escribe si cambió.
  · No toca plugins ajenos del usuario: escribe un archivo con NOMBRE propio
    (`jarvis-swarm.js`), nunca un dir compartido — el glob de opencode
    (`{plugin,plugins}/*.js`) los carga a todos, así que conviven.
  · Best-effort ABSOLUTO: cualquier error devuelve False sin romper el boot.
"""
import json
import os

NOMBRE_PLUGIN_SRC = 'opencode_jarvis_plugin.js'      # fuente en scripts/
NOMBRE_PLUGIN_DEST = 'jarvis-swarm.js'               # nombre instalado (propio)


def _dir_plugin_opencode() -> str:
    return os.path.join(os.path.expanduser('~'), '.config', 'opencode', 'plugin')


def asegurar_opencode_plugin(raiz_repo: str, destino_dir: str = None) -> bool:
    """Copia `scripts/opencode_jarvis_plugin.js` a `<destino_dir>/jarvis-swarm.js`
    (default `~/.config/opencode/plugin/`). Idempotente: solo escribe si el
    contenido cambió (el repo pudo moverse o actualizarse). Best-effort: cualquier
    error → False, nunca rompe el arranque. Devuelve True si escribió."""
    try:
        source = os.path.join(raiz_repo, 'scripts', NOMBRE_PLUGIN_SRC)
        with open(source, encoding='utf-8') as f:
            contenido = f.read()
        destino_dir = destino_dir or _dir_plugin_opencode()
        destino = os.path.join(destino_dir, NOMBRE_PLUGIN_DEST)
        try:
            with open(destino, encoding='utf-8') as f:
                if f.read() == contenido:
                    return False                     # ya está al día
        except OSError:
            pass                                     # no existe / ilegible → escribir
        os.makedirs(destino_dir, exist_ok=True)
        with open(destino, 'w', encoding='utf-8') as f:
            f.write(contenido)
        return True
    except Exception:
        return False


# ─── qwen: hook estilo Claude (reusa jarvis_ops_hook.py) ──────────────────────
# qwen es un fork del Gemini CLI con hooks COMPATIBLES con Claude Code: mismos
# nombres de evento (PostToolUse/PreToolUse) y payload {tool_name, tool_input} por
# stdin. Así que se reusa TAL CUAL el mismo hook (scripts/jarvis_ops_hook.py) — solo
# cambian el archivo de settings y los nombres de tool del matcher.
_QWEN_EVENTOS = (
    ('PostToolUse', 'write_file|edit|replace'),
    ('PreToolUse',  'write_file|edit|replace'),
    # Briefing del enjambre. Si esta versión de qwen no conoce el evento, lo
    # ignora y el agente igual se entera por el piggyback del PostToolUse — el
    # costo de declararlo de más es cero, el de no declararlo es quedarse sin el
    # canal bueno (el que llega ANTES de que el agente piense).
    ('UserPromptSubmit', None),
)


def _settings_qwen() -> str:
    return os.path.join(os.path.expanduser('~'), '.qwen', 'settings.json')


def asegurar_qwen_hook(raiz_repo: str, settings_path: str = None) -> bool:
    """Instala el hook de provenance de qwen en ~/.qwen/settings.json (reusa el
    mismo jarvis_ops_hook.py que Claude, con los tool names de qwen). Idempotente,
    best-effort: cualquier error → False, nunca rompe el boot. True si escribió."""
    try:
        from plotspace.core import hooks_cli
        return hooks_cli.asegurar_hooks_provenance(
            settings_path or _settings_qwen(), hooks_cli.comando_hook(raiz_repo),
            eventos=_QWEN_EVENTOS)
    except Exception:
        return False


# NOTA (2026-07-23): NO hay adaptador para el Gemini CLI standalone, a propósito.
# Acá lo que está instalado es ANTIGRAVITY, que lleva Gemini adentro (su
# `~/.gemini/antigravity-cli/settings.json` declara `model: "Gemini 3.5 Flash"`).
# El `gemini` suelto no existe en este entorno, así que un adaptador para él sería
# código muerto escribiendo un `~/.gemini/settings.json` que nadie lee. Si algún
# día se instala el CLI standalone, su contrato quedó documentado en la memoria
# del proyecto ([[provenance-por-hook]]): hooks AfterTool/BeforeTool y timeout en
# MILISEGUNDOS.


# ─── Antigravity (agy): el más distinto de todos ─────────────────────────────
# Tres cosas lo sacan del molde de los otros:
#   1. Su archivo NO es {"hooks": {evento: [...]}} sino un MAPA DE HOOKS CON
#      NOMBRE: {"<nombre>": {evento: [...]}} → se escribe bajo el NUESTRO, y los
#      del usuario quedan intactos (por eso no puede reusar hooks_cli).
#   2. Solo `PreToolUse` trae la ruta: su PostToolUse no trae ni tool ni path, así
#      que se registra en el previo — es eso o no ver NINGUNA edición.
#   3. Su hook corre SÍNCRONO y BLOQUEA el loop del agente si no recibe JSON, así
#      que jarvis_ops_hook contesta `{"decision":"allow"}` siempre.
_AGY_NOMBRE_HOOK = 'jarvis-provenance'
_AGY_MATCHER = 'write_to_file|replace_file_content|multi_replace_file_content'
_AGY_TIMEOUT = 10


def _hooks_antigravity() -> str:
    return os.path.join(os.path.expanduser('~'), '.gemini', 'config', 'hooks.json')


def asegurar_antigravity_hook(raiz_repo: str, hooks_path: str = None) -> bool:
    """Instala el hook de provenance de Antigravity en su hooks.json. Idempotente
    y best-effort; ante un archivo ILEGIBLE NO escribe (preferimos no instalar
    antes que borrarle la config al usuario). True si escribió."""
    try:
        from plotspace.core import hooks_cli
        destino = hooks_path or _hooks_antigravity()
        deseado = {'PreToolUse': [{
            'matcher': _AGY_MATCHER,
            'hooks': [{'type': 'command',
                       'command': hooks_cli.comando_hook(raiz_repo, tolerante=False),
                       'timeout': _AGY_TIMEOUT}]}]}
        data = {}
        if os.path.exists(destino):
            try:
                with open(destino, encoding='utf-8') as f:
                    data = json.load(f)
            except (OSError, ValueError):
                return False                 # ilegible: NO lo pisamos
        if not isinstance(data, dict):
            return False
        if data.get(_AGY_NOMBRE_HOOK) == deseado:
            return False                     # ya está al día
        data[_AGY_NOMBRE_HOOK] = deseado
        os.makedirs(os.path.dirname(destino) or '.', exist_ok=True)
        with open(destino, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False
