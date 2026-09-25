# plotspace/core/hooks_cli.py
"""Instalación de los hooks del CLI que alimentan la provenance del enjambre.

Gemelo de `terminals.asegurar_session_hook` (que instala el SessionStart), pero
para los dos eventos de herramientas:

  PostToolUse (Edit|Write|NotebookEdit) → registra la edición REAL en Jarvis.
  PreToolUse  (Edit|Write|NotebookEdit) → pregunta ANTES de escribir.

PreToolUse mira TODAS las escrituras, pero frena muy poco: la sobrescritura
total de un archivo que otro tocó recién, y las ediciones que borran o
renombran un símbolo de territorio ajeno. Editar otra zona del mismo archivo, o
llamar a la función de otro, pasan sin ruido — está medido que dos ediciones en
zonas distintas conviven perfecto, y frenarlas reintroduciría el 32% de tráfico
de permisos que este sistema vino a matar.

Costo: el hook agrega ~157 ms a cada escritura. Es el precio de que la colisión
no ocurra en vez de avisarla después.

Reglas del instalador (aprendidas de romper cosas ajenas):
  · Idempotente: correrlo mil veces deja UNA entrada por evento.
  · Actualiza en vez de duplicar si cambió la ruta del script (repo movido).
  · No toca hooks ajenos del mismo evento (un linter del usuario sigue vivo).
  · Ante un settings.json ilegible NO escribe nada: se prefiere no instalar
    antes que borrarle la configuración al usuario.
"""
import json
import os
import shlex
import tempfile
from typing import Optional

NOMBRE_SCRIPT = 'jarvis_ops_hook.py'

# Timeout corto y explícito: si Jarvis se cuelga, el agente sigue trabajando.
# (PreToolUse que expira = la herramienta procede; el CLI falla abierto.)
TIMEOUT_S = 5

# matcher None = evento que NO es de herramienta (no hay tool que matchear, y
# escribir un matcher inventado sería basura en el settings del usuario).
_EVENTOS = (
    ('PostToolUse', 'Edit|Write|NotebookEdit'),
    ('PreToolUse',  'Edit|Write|NotebookEdit'),
    ('UserPromptSubmit', None),
)


def _es_nuestro(comando) -> bool:
    return isinstance(comando, str) and NOMBRE_SCRIPT in comando


def _sincronizar_evento(grupos: list, matcher: str, hook_cmd: str) -> bool:
    """Deja exactamente UNA entrada nuestra (con el comando y matcher actuales)
    en la lista de grupos de un evento. Devuelve True si hubo cambios."""
    cambio = False
    encontrado = False
    for grupo in list(grupos):
        hooks = grupo.get('hooks') or []
        mios = [h for h in hooks if _es_nuestro(h.get('command'))]
        if not mios:
            continue
        ajenos = [h for h in hooks if not _es_nuestro(h.get('command'))]
        if ajenos:
            # Grupo mixto (el usuario metió el suyo al lado): sacamos el nuestro
            # de acá y lo dejamos en su propio grupo, sin tocar el ajeno.
            grupo['hooks'] = ajenos
            cambio = True
            continue
        if encontrado:
            grupos.remove(grupo)          # duplicado de una instalación previa
            cambio = True
            continue
        encontrado = True
        deseado = {'type': 'command', 'command': hook_cmd, 'timeout': TIMEOUT_S}
        if grupo.get('matcher') != matcher or mios[0] != deseado:
            if matcher is None:
                grupo.pop('matcher', None)
            else:
                grupo['matcher'] = matcher
            grupo['hooks'] = [deseado]
            cambio = True
    if not encontrado:
        nuevo = {'hooks': [{'type': 'command', 'command': hook_cmd,
                            'timeout': TIMEOUT_S}]}
        if matcher is not None:
            nuevo['matcher'] = matcher
        grupos.append(nuevo)
        cambio = True
    return cambio


def asegurar_hooks_provenance(settings_path: str, hook_cmd: str, eventos=None) -> bool:
    """Instala/actualiza los hooks en settings_path. True si escribió algo.

    `eventos` = tupla [(nombre_evento, matcher)]; default = _EVENTOS (Claude). Se
    parametriza para reusar el instalador con OTROS CLIs de contrato compatible
    (qwen: mismos nombres de evento, distintos nombres de tool en el matcher).

    Best-effort absoluto: cualquier error devuelve False sin tocar el archivo
    (nunca rompe el arranque del server ni la config del usuario)."""
    try:
        data = {}
        if os.path.exists(settings_path):
            try:
                with open(settings_path, encoding='utf-8') as f:
                    data = json.load(f)
            except (OSError, ValueError):
                # Ilegible: NO lo pisamos — preferimos no instalar el hook antes
                # que borrarle la configuración al usuario.
                return False
        if not isinstance(data, dict):
            return False

        hooks = data.setdefault('hooks', {})
        if not isinstance(hooks, dict):
            return False
        cambio = False
        for evento, matcher in (eventos or _EVENTOS):
            grupos = hooks.setdefault(evento, [])
            if not isinstance(grupos, list):
                continue
            cambio |= _sincronizar_evento(grupos, matcher, hook_cmd)
        if not cambio:
            return False

        destino_dir = os.path.dirname(settings_path)
        if destino_dir:
            os.makedirs(destino_dir, exist_ok=True)
        with open(settings_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


def comando_hook(raiz_repo: str, sistema: Optional[str] = None,
                 tolerante: bool = True) -> str:
    """Comando que se registra en el settings (ruta absoluta al script).

    `-S` (no cargar site-packages) ahorra ~27 ms por llamada, y el hook corre
    DOS veces por cada escritura de CADA agente. Es stdlib pura, así que no
    necesita site para nada — medido, no supuesto.

    El intérprete cambia por sistema: en Windows no existe `python3` (el
    lanzador oficial es `py`, y `python` es lo que instala el propio Python).
    Sin esto, el hook falla en silencio en cada escritura y el enjambre se
    queda SIN provenance justo en la plataforma nueva — nadie sabría quién
    tocó qué. La ruta va entre comillas: en Windows tiene espacios casi
    siempre (`C:\\Program Files\\...`, `C:\\Users\\Juan Pérez\\...`).

    `tolerante=False` = comando crudo, para CLIs que no documentan correr el
    hook con un shell (Antigravity); el resto usa el guard de `_tolerante`.
    """
    ruta = os.path.abspath(os.path.join(raiz_repo, 'scripts', NOMBRE_SCRIPT))
    if (sistema or os.name) == 'nt':
        return f'python -S "{ruta}"'
    if not tolerante:
        return 'python3 -S ' + ruta
    return _tolerante('python3 -S', ruta)


def _tolerante(interprete: str, ruta: str) -> str:
    """`test ! -f R || <interprete> R`: si el script no está, sale con 0.

    Sin esto, un script borrado hace que Python salga con código 2 ("can't
    open file"), y en UserPromptSubmit el código 2 BLOQUEA el prompt: el
    usuario no puede mandar ni un mensaje (2026-09-25, server arrancado una
    vez desde una copia en /tmp que después se borró). Si el script existe,
    su código de salida pasa intacto. Solo Unix: ahí el CLI corre el hook
    con `sh -c`."""
    q = shlex.quote(ruta)
    return f'test ! -f {q} || {interprete} {q}'


def raiz_es_efimera(raiz_repo: str) -> bool:
    """True si la raíz vive bajo un directorio temporal (/tmp, /var/tmp o
    tempfile.gettempdir()). Desde ahí NO se instalan hooks en el settings del
    usuario: la copia se borra y el hook queda apuntando a la nada."""
    raiz = os.path.realpath(os.path.abspath(raiz_repo))
    bases = {'/tmp', '/var/tmp', tempfile.gettempdir()}
    for base in bases:
        base = os.path.realpath(base)
        if raiz == base or raiz.startswith(base.rstrip(os.sep) + os.sep):
            return True
    return False


# El SessionStart hook (captura el session-id vivo para --resume). Vive acá y
# no en main.py porque hasta el 2026-07-27 se armaba allá, aparte, con `python3`
# y SIN comillas: en Windows el CLI ejecuta el hook a través de un shell, y ahí
# las barras invertidas son escapes — `C:\Users\USER\…` llegaba como
# `C:UsersUSER…` y Python lo buscaba relativo al cwd. Dos formas de armar el
# mismo comando es la causa de fondo: se arregló una y la otra quedó rota.
SCRIPT_SESSION = 'jarvis_claude_hook.py'


def comando_session_hook(raiz_repo: str, sistema: Optional[str] = None) -> str:
    """Comando del SessionStart hook, con el mismo trato que el de provenance.

    Sin `-S`: este hook sí importa cosas fuera de la stdlib.
    """
    ruta = os.path.abspath(os.path.join(raiz_repo, 'scripts', SCRIPT_SESSION))
    if (sistema or os.name) == 'nt':
        return f'python "{ruta}"'
    return _tolerante('python3', ruta)
