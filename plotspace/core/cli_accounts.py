"""
Cuentas de CLIs — vincular múltiples cuentas por agente de IA (claude / codex /
qwen / opencode) y switchear la activa SIN re-loguear.

Idea: cada CLI persiste su sesión en archivos de texto plano del HOME (ninguno
usa keyring del SO ni cifra). "Vincular una cuenta" = tomar un SNAPSHOT de esos
archivos; "switchear" = restaurar ese snapshot sobre los archivos activos. El
login en sí sigue siendo el flujo OAuth interactivo de cada CLI (el usuario lo
hace una vez en una terminal); acá sólo guardamos/restauramos el resultado.

Persistencia (ver recomendación de arquitectura):
  - METADATA  → tabla `cli_accounts` (database.py). Sólo tipo/label/email/activa.
  - SNAPSHOTS → data/cli-accounts/<id>/ (gitignored por la regla `data/`). Los
    archivos se crean 0600 y el dir 0700: el secreto NUNCA toca la DB ni git.

Delicadezas resueltas:
  - CLAUDE mezcla credencial con config NO-credencial (projects/mcpServers) en
    ~/.claude.json. El snapshot extrae SÓLO las 3 claves de cuenta y el restore
    las MERGEA (no pisa el archivo entero → no se pierden proyectos ni MCPs).
  - CODEX comparte ~/.codex con otro producto (sqlite): se snapshotea SÓLO
    auth.json, jamás el dir.
  - Antes de switchear, se re-snapshotea el perfil que estaba activo si el HOME
    coincide con su email (los access tokens se refrescan solos → no perderlos).
  - Escrituras atómicas (tmp + os.replace) por si el CLI lee en paralelo.

Tests: plotspace/tests/test_cli_accounts.py.
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

from plotspace.core.database import get_db
from plotspace.core.datadir import ruta_data

# Repuntables en tests (los specs los leen en cada llamada, no se cachean).
HOME_DIR = os.path.expanduser("~")
SNAPSHOTS_DIR = ruta_data("cli-accounts")

# Orden CANÓNICO de la sección Cuentas (pedido del usuario 2026-07-10). El
# frontend pinta las cards tal cual llegan de estado(), así que este orden ES
# el de la UI; estado() además sube los CLIs CON cuentas guardadas (ver ahí).
# Pi se agregó 2026-09-06 al FINAL para no mover el orden conocido.
TIPOS = ["claude", "codex", "grok", "antigravity", "opencode", "qwen", "pi", "cursor"]


class TipoDesconocido(Exception):
    pass


class NoLogueado(Exception):
    """El CLI no tiene una sesión activa en el HOME para capturar."""


class PerfilNoEncontrado(Exception):
    pass


def _codex_home():
    return os.environ.get("CODEX_HOME") or os.path.join(HOME_DIR, ".codex")


def codex_home(account_id):
    """Home AISLADO (CODEX_HOME propio) de una cuenta de codex. Cada cuenta tiene
    el suyo → NUNCA comparten auth.json → cambiar de cuenta no dispara la
    revocación por reuso de refresh token de OpenAI (que pasaba al pisar el
    ~/.codex compartido). Reusa el dir del perfil; lo crea y le siembra un
    config.toml base (copiado del ~/.codex del usuario, si existe) la primera vez.
    Devuelve el path ABSOLUTO (el que va como CODEX_HOME a la terminal)."""
    home = os.path.abspath(_account_dir(account_id))
    os.makedirs(home, exist_ok=True)
    try:
        os.chmod(home, 0o700)
    except OSError:
        pass
    cfg = os.path.join(home, "config.toml")
    if not os.path.exists(cfg):
        base = os.path.join(HOME_DIR, ".codex", "config.toml")
        if os.path.isfile(base):
            try:
                shutil.copy2(base, cfg)
            except OSError:
                pass
    return home


def _codex_active_link():
    """Path del symlink ESTABLE que apunta al home de la cuenta de codex activa."""
    return os.path.join(SNAPSHOTS_DIR, "_codex_active")


def _crear_enlace_dir(destino, origen):
    """Enlace de DIRECTORIO `destino` → `origen`, del tipo que el sistema
    permita sin privilegios.

    En Unix: symlink, como siempre.
    En Windows: JUNCTION. Un symlink de directorio en Windows exige permisos
    de administrador o el Modo Desarrollador prendido, y no se le puede pedir
    eso a alguien que solo quiere cambiar de cuenta de Codex. Las junctions no
    piden nada, existen desde NTFS y para el CLI son indistinguibles: resuelve
    el directorio igual. `mklink` es un builtin de cmd, no un ejecutable — por
    eso hay que invocarlo a través de `cmd /c`, con argv y sin shell.
    """
    if os.name != 'nt':
        os.symlink(origen, destino)
        return
    subprocess.run(['cmd', '/c', 'mklink', '/J', destino, origen],
                   capture_output=True, check=True)


def _repuntar_codex_active(account_id):
    """(Re)apunta el enlace estable `_codex_active` → home real de la cuenta. Las
    terminales usan CODEX_HOME = ese enlace (no el dir real), así CAMBIAR de cuenta
    = repuntar el enlace, y `codex` re-ejecutado en CUALQUIER terminal ya existente
    usa la cuenta nueva SIN reabrir la terminal (verificado: codex resuelve el
    enlace en cada arranque). Cada cuenta sigue teniendo su dir real propio → no
    comparten auth.json → sin reuso/revocación. Devuelve el path (abs) del enlace,
    o el dir real si no se pudo crear (queda aislado igual)."""
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    link = _codex_active_link()
    target = codex_home(account_id)            # dir real, abs, asegurado + config
    tmp = link + ".tmp"
    try:
        _borrar_enlace(tmp)
        _crear_enlace_dir(tmp, target)
        # Repunte atómico. En Windows `os.replace` NO puede pisar un directorio
        # existente (y una junction ES un directorio), así que hay que sacar el
        # viejo primero. Queda una ventana de microsegundos sin enlace: un
        # `codex` arrancando justo ahí caería a su config por defecto, que es
        # infinitamente mejor que quedarse sin poder cambiar de cuenta.
        if os.name == 'nt':
            _borrar_enlace(link)
            os.rename(tmp, link)
        else:
            os.replace(tmp, link)
    except (OSError, subprocess.SubprocessError):
        return target
    return os.path.abspath(link)


def _borrar_enlace(p):
    """Saca un enlace de directorio sin llevarse su CONTENIDO.

    En Windows una junction se borra con `os.rmdir` (que la desengancha sin
    tocar el destino); `shutil.rmtree` la SEGUIRÍA y borraría el home real de
    la cuenta — o sea, las credenciales del usuario. No es una precaución
    teórica: es la diferencia entre cambiar de cuenta y perderla."""
    try:
        if os.path.islink(p):
            os.remove(p)
        elif os.path.isdir(p):
            os.rmdir(p)
        elif os.path.exists(p):
            os.remove(p)
    except OSError:
        pass


def codex_home_activo():
    """CODEX_HOME para lanzar terminales de codex: el symlink ESTABLE `_codex_active`
    apuntando a la cuenta activa. Que sea estable (no el dir real) es lo que permite
    cambiar de cuenta sin reabrir terminales — el switch solo repunta el symlink.
    None si no hay cuenta de codex activa."""
    p = _perfil_activo("codex")
    return _repuntar_codex_active(p["id"]) if p else None


# ── alta de cuenta codex NUEVA: login en un staging dir AISLADO y FRESCO ─────
# El login de una cuenta codex nueva (PTY oculto de cli_login) NO debe escribir en
# el home de la cuenta ACTIVA: loguear OTRA cuenta de OpenAI ahí reescribiría su
# auth.json y la revocaría por reuso de refresh token. En vez, el login va a un
# CODEX_HOME staging propio (`_codex_login`), que el watcher ADOPTA en un dir de
# cuenta real al detectar la credencial nueva. Nunca toca un dir vivo.

def _codex_login_dir():
    """Path del staging dir donde el login de una cuenta codex NUEVA escribe su
    auth.json fresco. Aislado de ~/.codex y de TODO dir de cuenta viva."""
    return os.path.join(SNAPSHOTS_DIR, "_codex_login")


def codex_login_home_nuevo():
    """Prepara y devuelve el CODEX_HOME aislado y FRESCO para loguear una cuenta
    codex NUEVA. Borra cualquier auth.json de un intento previo (arranca limpio para
    que la detección no confunda credenciales viejas) y siembra config.toml (codex
    exige que el CODEX_HOME exista con su config). Devuelve el path ABSOLUTO."""
    home = os.path.abspath(_codex_login_dir())
    os.makedirs(home, exist_ok=True)
    try:
        os.chmod(home, 0o700)
    except OSError:
        pass
    # arrancar limpio: un auth.json de un login abortado no debe falsear la detección
    try:
        os.remove(os.path.join(home, "auth.json"))
    except OSError:
        pass
    cfg = os.path.join(home, "config.toml")
    if not os.path.exists(cfg):
        base = os.path.join(HOME_DIR, ".codex", "config.toml")
        if os.path.isfile(base):
            try:
                shutil.copy2(base, cfg)
            except OSError:
                pass
    return home


def codex_login_logueado():
    """¿El login de cuenta nueva ya depositó una sesión en el staging dir?
    (auth.json con tokens). Es la señal de 'el login terminó'."""
    data = _read_json(os.path.join(_codex_login_dir(), "auth.json")) or {}
    return bool(data.get("tokens"))


def codex_login_email():
    """Email/identidad de la cuenta recién logueada en el staging dir (best-effort,
    misma lógica que email_actual('codex'))."""
    data = _read_json(os.path.join(_codex_login_dir(), "auth.json")) or {}
    tokens = data.get("tokens") or {}
    em = _jwt_email(tokens.get("id_token", "")) if tokens.get("id_token") else None
    return em or tokens.get("account_id") or None


def _instalar_codex_login_en(account_id):
    """Mueve la credencial recién logueada del staging al dir AISLADO de la cuenta,
    repunta el symlink activo y limpia el staging. El source es un login interactivo
    FRESCO (tokens recién emitidos por OpenAI), no un refresh token reusado → instalar
    estos tokens NO dispara la revocación por reuso (a diferencia de copiar un
    snapshot viejo de ~/.codex, que sí lo haría)."""
    src = os.path.join(_codex_login_dir(), "auth.json")
    if not os.path.isfile(src):
        raise NoLogueado("codex")
    dest_dir = codex_home(account_id)            # crea el dir + siembra config.toml
    dest = os.path.join(dest_dir, "auth.json")
    shutil.copy2(src, dest)
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    _repuntar_codex_active(account_id)           # el symlink estable → esta cuenta
    # limpiar el staging para que el próximo alta arranque fresco
    try:
        os.remove(src)
    except OSError:
        pass


def capturar_codex_login(label=""):
    """Da de alta la cuenta codex recién logueada en el staging dir como un perfil
    NUEVO: crea su fila, instala la credencial en su PROPIO dir aislado, repunta el
    symlink y la deja activa. NUNCA tocó ~/.codex ni el dir de otra cuenta."""
    if not codex_login_logueado():
        raise NoLogueado("codex")
    email = codex_login_email()
    now = datetime.now().isoformat()
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO cli_accounts (tipo, label, email, activa, created_at, updated_at) "
            "VALUES ('codex', ?, ?, 0, ?, ?)",
            ((label or "").strip(), email, now, now),
        )
        account_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    _instalar_codex_login_en(account_id)
    _set_activa("codex", account_id)
    return obtener_perfil(account_id)


def adoptar_codex_login(account_id):
    """Re-login (vía «agregar cuenta nueva») de una cuenta codex YA guardada: instala
    los tokens FRESCOS del staging en su dir, la activa y repunta el symlink, SIN
    duplicar el perfil. Source = login interactivo nuevo (no es reuse de refresh
    token) → reemplazar la credencial vieja por la fresca es correcto y seguro."""
    perfil = obtener_perfil(account_id)
    if not perfil or perfil["tipo"] != "codex":
        raise PerfilNoEncontrado(account_id)
    _instalar_codex_login_en(account_id)
    _set_activa("codex", account_id)
    return obtener_perfil(account_id)


def _specs():
    """Especificación por CLI, desde `plotspace/protocolos/clis.json`.

    La lista vive en un archivo y no acá porque el motor Rust necesita saber
    exactamente los mismos caminos: dónde está la credencial de cada CLI y cuál
    de ellas decide si hay sesión. Dos copias de esas rutas se separan sin que
    nadie lo note, y el día que se separen un motor dice "logueado" y el otro
    "no" para la misma cuenta.

    Se arma en CADA llamada a propósito: `HOME_DIR` lo repuntan los tests y
    `CODEX_HOME` cambia con la cuenta activa. Cachearlo rompe el switch.
    """
    from plotspace import protocolos

    def expandir(ruta):
        if ruta.startswith('$CODEX_HOME/'):
            return os.path.join(_codex_home(), *ruta[len('$CODEX_HOME/'):].split('/'))
        if ruta.startswith('$XDG_CONFIG_HOME/'):
            # Cursor (y otra app XDG) resuelven su config con `XDG_CONFIG_HOME`
            # o, por defecto, `~/.config`. Sin esto, sesión real pasaba por
            # desapercibida si el usuario tiene XDG_CONFIG_HOME set.
            base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME_DIR, ".config")
            return os.path.join(base, *ruta[len('$XDG_CONFIG_HOME/'):].split('/'))
        return os.path.join(HOME_DIR, *ruta.lstrip('~/').split('/'))

    salida = {}
    for c in protocolos.clis():
        spec = {k: v for k, v in c.items()
                if k not in ('id', '_nota', 'files', 'principales',
                             'cred_file', 'config_file', 'cuenta_file')}
        for clave in ('files', 'principales'):
            if clave in c:
                spec[clave] = [expandir(r) for r in c[clave]]
        for clave in ('cred_file', 'config_file', 'cuenta_file'):
            if clave in c:
                spec[clave] = expandir(c[clave])
        salida[c['id']] = spec
    # Claude Code respeta CLAUDE_CONFIG_DIR: con esa variable la credencial y su config
    # viven ahí, NO en ~/.claude (antes Jarvis miraba siempre el HOME y no veía el login).
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if cfg_dir and "claude" in salida and salida["claude"].get("modo") == "claude":
        cfg_dir = os.path.expanduser(cfg_dir)
        salida["claude"]["cred_file"] = os.path.join(cfg_dir, ".credentials.json")
        en_dir = os.path.join(cfg_dir, ".claude.json")
        if os.path.isfile(en_dir):
            salida["claude"]["config_file"] = en_dir
    return salida


def antigravity_token_file():
    """Ruta del token OAuth de agy 1.1.x — la credencial principal del spec. La
    usa cli_login para apartarlo durante el alta (con sesión activa, `agy auth
    login` se auto-re-loguea con este token y nunca muestra el menú de login)."""
    return os.path.join(HOME_DIR, ".gemini", "antigravity-cli", "antigravity-oauth-token")


# ── helpers de bajo nivel ───────────────────────────────────────────────────

def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _atomic_write(path, texto, modo=0o600):
    """Escribe `texto` de forma atómica (tmp + replace) y deja el archivo 0600."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".jarvistmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texto)
    try:
        os.chmod(tmp, modo)
    except OSError:
        pass
    os.replace(tmp, path)


def _jwt_email(token):
    """Email del payload de un JWT (sin verificar firma: sólo lee el claim).
    Defensivo: ante cualquier problema devuelve None."""
    try:
        seg = token.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        data = json.loads(base64.urlsafe_b64decode(seg))
        return data.get("email") or data.get("preferred_username") or None
    except Exception:
        return None


def _grok_email(auth):
    """El email de grok vive DENTRO de cada valor de ~/.grok/auth.json (un mapa
    `<issuer>::<clientid>` → perfil), no en la raíz. Devuelve el primero."""
    if isinstance(auth, dict):
        for v in auth.values():
            if isinstance(v, dict) and v.get("email"):
                return v["email"]
    return None


def _opencode_proveedores(auth):
    """opencode no tiene email único: su auth.json es un mapa proveedor →
    credencial (openrouter, zai-coding-plan, anthropic, …). La identidad que se
    muestra es el/los proveedor(es) logueados (sus claves, ordenadas)."""
    if isinstance(auth, dict) and auth:
        return ", ".join(sorted(auth.keys())) or None
    return None


def _pi_proveedores(auth):
    """Pi (Earendil) tampoco tiene email único: ~/.pi/agent/auth.json es un mapa
    proveedor → credencial (api_key u OAuth; OAuth solo lleva access/refresh/
    expires, y `accountId` para el provider openai-codex — nunca email). Mismo
    criterio que opencode: la identidad mostrable son los proveedor(es)."""
    if isinstance(auth, dict) and auth:
        return ", ".join(sorted(auth.keys())) or None
    return None


_AGY_EMAIL_RE = re.compile(r"authenticated successfully as (\S+@\S+)")


def _antigravity_log_dir():
    return os.path.join(HOME_DIR, ".gemini", "antigravity-cli", "log")


def _antigravity_email():
    """El token de antigravity es opaco (access/refresh de Google, SIN id_token
    JWT): no se puede sacar el email de la credencial. El CLI sí lo escribe en su
    log al autenticar → devuelve el email del log MÁS reciente que tenga una línea
    de auth. Best-effort para MOSTRAR (no para deduplicar: solo es fiable para la
    cuenta recién logueada / activa)."""
    log_dir = _antigravity_log_dir()
    try:
        logs = sorted(
            (os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith(".log")),
            key=os.path.getmtime, reverse=True,
        )
    except OSError:
        return None
    for lf in logs:
        encontrado = None
        try:
            with open(lf, encoding="utf-8", errors="ignore") as f:
                for linea in f:
                    m = _AGY_EMAIL_RE.search(linea)
                    if m:
                        encontrado = m.group(1)   # la última del archivo
        except OSError:
            continue
        if encontrado:
            return encontrado
    return None


def _account_dir(account_id):
    return os.path.join(SNAPSHOTS_DIR, str(account_id))


# ── Cursor CLI: configuración y credencial ──────────────────────────────────
# Verificado contra el paquete real (cursor-agent 2026.09.02, bundle webpack):
#   - credencial (cli-credentials): `(XDG_CONFIG_HOME || ~/.config)/cursor/auth.json`
#     con {accessToken, refreshToken, apiKey, bedrockCredentials} en plano —
#     SIN email ni identidad de cuenta.
#   - config (cursor-config): `(CURSOR_CONFIG_DIR || XDG_CONFIG_HOME/cursor ||
#     ~/.cursor)/cli-config.json`; `authInfo.email` se escribe ahí al terminar
#     `agent login` (GetMe del dashboard) → identidad best-effort para la UI.
#   - `auth.json` se borra con `agent logout` → existencia ≈ sesión.

def _cursor_cred_file():
    return os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME_DIR, ".config"),
        "cursor", "auth.json")


def _cursor_cli_config():
    d = os.environ.get("CURSOR_CONFIG_DIR")
    if d:
        return os.path.join(d, "cli-config.json")
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return os.path.join(xdg, "cursor", "cli-config.json")
    return os.path.join(HOME_DIR, ".cursor", "cli-config.json")


def _cursor_authinfo_email():
    """Email que Cursor CLI guarda en `authInfo` de su cli-config.json después de
    un login exitoso. Best-effort para MOSTRAR (igual caso que agy/opencode): el
    email no vive en la credencial (auth.json), así que no sirve para deduplicar.
    El config lo reescribe el propio CLI y puede traer email vacío."""
    cfg = _read_json(_cursor_cli_config()) or {}
    em = (cfg.get("authInfo") or {}).get("email")
    return em or None


# ── detección de sesión activa en el HOME ───────────────────────────────────

def esta_logueado(tipo):
    spec = _specs().get(tipo)
    if not spec:
        return False
    if spec["modo"] == "claude":
        data = _read_json(spec["cred_file"]) or {}
        return bool(data.get("claudeAiOauth"))
    if tipo == "pi":
        # Pi persiste ~/.pi/agent/auth.json como `{}` tras /logout (el archivo
        # nunca se borra) → la existencia del archivo NO es sesión; hay sesión
        # real cuando el JSON es un dict con al menos una credencial.
        data = _read_json(spec["files"][0]) or {}
        return bool(isinstance(data, dict) and any(data.values()))
    return any(os.path.isfile(p) for p in spec.get("principales", spec["files"]))


def email_actual(tipo):
    """Email/identidad de la cuenta logueada AHORA en el HOME (best-effort)."""
    spec = _specs().get(tipo)
    if not spec:
        return None
    if spec["modo"] == "claude":
        cfg = _read_json(spec["config_file"]) or {}
        return (cfg.get("oauthAccount") or {}).get("emailAddress")
    if tipo == "qwen":
        ga = _read_json(spec["cuenta_file"]) or {}
        return ga.get("active") or None
    if tipo == "codex":
        data = _read_json(spec["files"][0]) or {}
        tokens = data.get("tokens") or {}
        em = _jwt_email(tokens.get("id_token", "")) if tokens.get("id_token") else None
        return em or tokens.get("account_id") or None
    if tipo == "grok":
        # grok guarda el email DENTRO de la credencial (por-cuenta) → identifica la
        # cuenta igual que claude/codex: sirve para deduplicar en el watcher.
        return _grok_email(_read_json(_home_path_para(spec, "auth.json")) or {})
    # opencode/antigravity/pi/cursor: sin identidad ÚNICA fiable en la credencial (el
    # watcher dedupea por huella). La etiqueta para la UI la resuelve
    # identidad_mostrable().
    return None


def identidad_mostrable(tipo):
    """Etiqueta de identidad para la UI (columna `email`). A diferencia de
    email_actual() (identidad para DEDUPLICAR), acá se hace best-effort también
    para los CLIs sin email único: opencode → proveedor(es), antigravity → email
    del log. Es lo que se persiste al capturar/recapturar. Lee el HOME vivo."""
    em = email_actual(tipo)
    if em:
        return em
    spec = _specs().get(tipo)
    if not spec:
        return None
    if tipo == "opencode":
        return _opencode_proveedores(_read_json(_home_path_para(spec, "auth.json")) or {})
    if tipo == "pi":
        return _pi_proveedores(_read_json(_home_path_para(spec, "auth.json")) or {})
    if tipo == "antigravity":
        return _antigravity_email()
    if tipo == "cursor":
        return _cursor_authinfo_email()
    return None


def identidad_desde_snapshot(tipo, account_id):
    """Identidad leída del SNAPSHOT del perfil (no del HOME) — para rellenar
    cuentas viejas con email NULL sin re-loguear. grok/opencode guardan su
    identidad en el snapshot (auth.json copiado); el resto no (o ya la tienen)."""
    d = _account_dir(account_id)
    if tipo == "grok":
        return _grok_email(_read_json(os.path.join(d, "auth.json")) or {})
    if tipo == "opencode":
        return _opencode_proveedores(_read_json(os.path.join(d, "auth.json")) or {})
    if tipo == "pi":
        return _pi_proveedores(_read_json(os.path.join(d, "auth.json")) or {})
    return None


# ── snapshot (capturar) / restore ───────────────────────────────────────────

def _capturar_a_dir(tipo, dest_dir):
    """Copia la credencial ACTUAL del HOME a dest_dir. Devuelve los nombres
    guardados. Para claude extrae SÓLO las claves de cuenta."""
    spec = _specs()[tipo]
    os.makedirs(dest_dir, exist_ok=True)
    try:
        os.chmod(dest_dir, 0o700)
    except OSError:
        pass
    guardados = []
    # CODEX: el dest_dir ES el home AISLADO y VIVO de la cuenta (codex escribe ahí
    # vía CODEX_HOME en sus terminales). Si ya tiene auth.json, NO pisarlo con
    # ~/.codex: copiaríamos un refresh_token viejo/rotado encima del vigente y
    # OpenAI lo revoca por reuso (HUECO #1 de la auditoría). Solo se copia para
    # POBLAR un dir vacío (alta inicial de una cuenta nueva).
    if tipo == "codex" and os.path.isfile(os.path.join(dest_dir, "auth.json")):
        return []
    if spec["modo"] == "claude":
        cred = _read_json(spec["cred_file"]) or {}
        cfg = _read_json(spec["config_file"]) or {}
        snap = {
            "claudeAiOauth": cred.get("claudeAiOauth"),
            "oauthAccount": cfg.get("oauthAccount"),
            "userID": cfg.get("userID"),
        }
        _atomic_write(os.path.join(dest_dir, "claude-account.json"), json.dumps(snap))
        guardados.append("claude-account.json")
    else:
        for p in spec["files"]:
            if os.path.isfile(p):
                dest = os.path.join(dest_dir, os.path.basename(p))
                shutil.copy2(p, dest)
                try:
                    os.chmod(dest, 0o600)
                except OSError:
                    pass
                guardados.append(os.path.basename(p))
    return guardados


def _home_path_para(spec, basename):
    for p in spec["files"]:
        if os.path.basename(p) == basename:
            return p
    return None


def _restaurar_de_dir(tipo, src_dir):
    """Restaura el snapshot de src_dir sobre el HOME. Para claude MERGEA las
    claves de cuenta sin pisar projects/mcpServers/mcpOAuth."""
    spec = _specs()[tipo]
    if spec["modo"] == "claude":
        snap = _read_json(os.path.join(src_dir, "claude-account.json"))
        if snap is None:
            raise PerfilNoEncontrado("snapshot de claude ausente o ilegible")
        # credencial: mergear claudeAiOauth, preservar mcpOAuth/designOauth
        cred = _read_json(spec["cred_file"]) or {}
        if snap.get("claudeAiOauth") is not None:
            cred["claudeAiOauth"] = snap["claudeAiOauth"]
        _atomic_write(spec["cred_file"], json.dumps(cred), 0o600)
        # config: mergear oauthAccount/userID, preservar projects/mcpServers/...
        cfg = _read_json(spec["config_file"]) or {}
        if snap.get("oauthAccount") is not None:
            cfg["oauthAccount"] = snap["oauthAccount"]
        if snap.get("userID") is not None:
            cfg["userID"] = snap["userID"]
        _atomic_write(spec["config_file"], json.dumps(cfg), 0o600)
        return
    # modo files: reescribir cada archivo guardado a su lugar en el HOME
    if not os.path.isdir(src_dir):
        raise PerfilNoEncontrado("snapshot ausente")
    for fname in os.listdir(src_dir):
        dest = _home_path_para(spec, fname)
        if not dest:
            continue
        src = os.path.join(src_dir, fname)
        if spec.get("binario"):
            # Credenciales cifradas (ej. antigravity/credentials.enc): copia atómica
            # de bytes crudos — leerlas como utf-8 las corrompería.
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + ".jarvistmp"
            shutil.copy2(src, tmp)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, dest)
            continue
        with open(src, encoding="utf-8") as f:
            contenido = f.read()
        _atomic_write(dest, contenido, 0o600)


# ── CRUD de metadata (tabla cli_accounts) ───────────────────────────────────

def _fila(row):
    return {
        "id": row["id"], "tipo": row["tipo"], "label": row["label"],
        "email": row["email"], "activa": bool(row["activa"]),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def listar():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM cli_accounts ORDER BY tipo, created_at, id"
        ).fetchall()
    finally:
        conn.close()
    return [_fila(r) for r in rows]


def obtener_perfil(account_id):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM cli_accounts WHERE id = ?", (account_id,)
        ).fetchone()
    finally:
        conn.close()
    return _fila(row) if row else None


def _perfil_activo(tipo):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM cli_accounts WHERE tipo = ? AND activa = 1 ORDER BY id DESC LIMIT 1",
            (tipo,),
        ).fetchone()
    finally:
        conn.close()
    return _fila(row) if row else None


def _set_activa(tipo, account_id):
    now = datetime.now().isoformat()
    conn = get_db()
    try:
        conn.execute("UPDATE cli_accounts SET activa = 0 WHERE tipo = ?", (tipo,))
        conn.execute(
            "UPDATE cli_accounts SET activa = 1, updated_at = ? WHERE id = ?",
            (now, account_id),
        )
        conn.commit()
    finally:
        conn.close()


# ── operaciones de alto nivel ───────────────────────────────────────────────

def capturar_actual(tipo, label=""):
    """Vincula la cuenta logueada AHORA en el HOME como un perfil nuevo (y la
    deja activa, porque refleja el estado actual). Lanza NoLogueado si no hay
    sesión que capturar."""
    if tipo not in TIPOS:
        raise TipoDesconocido(tipo)
    if not esta_logueado(tipo):
        raise NoLogueado(tipo)
    email = identidad_mostrable(tipo)
    now = datetime.now().isoformat()
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO cli_accounts (tipo, label, email, activa, created_at, updated_at) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            (tipo, (label or "").strip(), email, now, now),
        )
        account_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    _capturar_a_dir(tipo, _account_dir(account_id))
    _set_activa(tipo, account_id)  # el snapshot recién tomado == estado del HOME
    return obtener_perfil(account_id)


def usar(account_id):
    """Switchea la cuenta activa de un CLI restaurando el snapshot del perfil.
    Antes refresca el snapshot del perfil que estaba activo (si el HOME le
    corresponde) para no perder tokens refrescados.

    CODEX es la excepción: usa homes AISLADOS por cuenta (CODEX_HOME propio), así
    que cambiar de cuenta NO restaura nada sobre ~/.codex — solo marca activa. Las
    terminales de codex se lanzan con CODEX_HOME = home de la cuenta activa
    (codex_home_activo()). Pisar el ~/.codex compartido era lo que disparaba la
    revocación por reuso de refresh token al switchear."""
    perfil = obtener_perfil(account_id)
    if not perfil:
        raise PerfilNoEncontrado(account_id)
    tipo = perfil["tipo"]

    if tipo == "codex":
        codex_home(account_id)            # asegura el dir + config.toml base
        _set_activa(tipo, account_id)
        _repuntar_codex_active(account_id)  # repunta el symlink → terminales ya
        #   abiertas (con CODEX_HOME=symlink) usan la cuenta nueva al re-correr codex
        return obtener_perfil(account_id)

    activo = _perfil_activo(tipo)
    if activo and activo["id"] != account_id and esta_logueado(tipo):
        # ¿el HOME corresponde al perfil activo? (mismo email, o ambos sin email)
        if (email_actual(tipo) or None) == (activo["email"] or None):
            try:
                _capturar_a_dir(tipo, _account_dir(activo["id"]))
                _touch(activo["id"])
            except Exception:
                pass  # refrescar es best-effort; no debe bloquear el switch

    _restaurar_de_dir(tipo, _account_dir(account_id))
    _set_activa(tipo, account_id)
    return obtener_perfil(account_id)


def activar(account_id):
    """Marca un perfil como activo SIN tocar el HOME (para cuando el HOME ya ES
    esa cuenta, p.ej. tras un re-login detectado por el watcher). Devuelve el
    perfil actualizado, o None si no existe."""
    perfil = obtener_perfil(account_id)
    if not perfil:
        return None
    _set_activa(perfil["tipo"], account_id)
    return obtener_perfil(account_id)


def recapturar(account_id):
    """Refresca el snapshot de un perfil desde el HOME actual (útil cuando es el
    perfil activo y el CLI refrescó/relogueó). No cambia cuál está activo."""
    perfil = obtener_perfil(account_id)
    if not perfil:
        raise PerfilNoEncontrado(account_id)
    tipo = perfil["tipo"]
    if not esta_logueado(tipo):
        raise NoLogueado(tipo)
    _capturar_a_dir(tipo, _account_dir(account_id))
    nuevo_email = identidad_mostrable(tipo)
    conn = get_db()
    try:
        conn.execute(
            "UPDATE cli_accounts SET email = ?, updated_at = ? WHERE id = ?",
            (nuevo_email, datetime.now().isoformat(), account_id),
        )
        conn.commit()
    finally:
        conn.close()
    return obtener_perfil(account_id)


def renombrar(account_id, label):
    perfil = obtener_perfil(account_id)
    if not perfil:
        raise PerfilNoEncontrado(account_id)
    conn = get_db()
    try:
        conn.execute(
            "UPDATE cli_accounts SET label = ?, updated_at = ? WHERE id = ?",
            ((label or "").strip(), datetime.now().isoformat(), account_id),
        )
        conn.commit()
    finally:
        conn.close()
    return obtener_perfil(account_id)


def eliminar(account_id):
    perfil = obtener_perfil(account_id)
    if not perfil:
        raise PerfilNoEncontrado(account_id)
    conn = get_db()
    try:
        conn.execute("DELETE FROM cli_accounts WHERE id = ?", (account_id,))
        conn.commit()
    finally:
        conn.close()
    shutil.rmtree(_account_dir(account_id), ignore_errors=True)
    return True


def _touch(account_id):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE cli_accounts SET updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), account_id),
        )
        conn.commit()
    finally:
        conn.close()


# ── estado para la UI ───────────────────────────────────────────────────────

def _backfill_identidades():
    """Rellena la columna `email` de cuentas guardadas ANTES de que detectáramos
    identidad para grok/opencode/antigravity (quedaron NULL → «no email detected»).
    Best-effort e idempotente: una vez rellenas, el SELECT vuelve vacío. grok y
    opencode se resuelven desde el snapshot; antigravity solo si es la ACTIVA (su
    email vive en los logs del HOME, no en el snapshot)."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, tipo, activa FROM cli_accounts "
            "WHERE email IS NULL OR email = ''"
        ).fetchall()
        if not rows:
            return
        cambios = []
        for r in rows:
            em = identidad_desde_snapshot(r["tipo"], r["id"])
            if not em and r["tipo"] == "antigravity" and r["activa"] \
                    and esta_logueado("antigravity"):
                em = _antigravity_email()
            if em:
                cambios.append((em, r["id"]))
        for em, aid in cambios:
            conn.execute("UPDATE cli_accounts SET email = ? WHERE id = ?", (em, aid))
        if cambios:
            conn.commit()
    finally:
        conn.close()


def adoptar_sesiones_nativas():
    """Si el HOME ya tiene un login y no hay perfil Jarvis que coincida, lo
    captura. Así ⚙ → Cuentas muestra Grok (etc.) aunque nunca se tocó Conectar."""
    adoptadas = []
    for tipo in TIPOS:
        if not esta_logueado(tipo):
            continue
        perfiles = [c for c in listar() if c["tipo"] == tipo]
        em = email_actual(tipo)
        if em and any(c.get("email") == em for c in perfiles):
            continue
        if not em and perfiles:
            continue
        try:
            adoptadas.append(capturar_actual(tipo))
        except (NoLogueado, TipoDesconocido, PerfilNoEncontrado):
            continue
    return adoptadas


def _info_archivo(path, claves=False):
    """Qué hay en `path` SIN exponer secretos: existe, tamaño, antigüedad y (si se pide)
    los NOMBRES de las claves de primer nivel del JSON — nunca valores."""
    info = {"ruta": path, "existe": os.path.isfile(path)}
    if not info["existe"]:
        return info
    try:
        st = os.stat(path)
        info["bytes"] = st.st_size
        info["modificado_hace_s"] = max(0, int(time.time() - st.st_mtime))
    except OSError:
        pass
    if claves:
        data = _read_json(path)
        info["json_valido"] = data is not None
        if isinstance(data, dict):
            info["claves"] = sorted(str(k) for k in data.keys())[:30]
    return info


def diagnostico(tipo):
    """Por qué Jarvis ve (o no) la sesión de `tipo`: qué rutas mira, cuáles existen y qué
    pistas hay (sesión hecha en Windows en vez de WSL, CLAUDE_CONFIG_DIR, API key en vez
    de suscripción…). Solo metadatos; nunca valores de tokens."""
    spec = _specs().get(tipo)
    if not spec:
        raise TipoDesconocido(tipo)
    out = {
        "tipo": tipo, "home": HOME_DIR,
        "logueado": bool(esta_logueado(tipo)), "email": email_actual(tipo),
        "archivos": [], "pistas": [],
    }
    if spec["modo"] == "claude":
        out["claude_config_dir"] = os.environ.get("CLAUDE_CONFIG_DIR") or None
        cred = _info_archivo(spec["cred_file"], claves=True)
        cfg = _info_archivo(spec["config_file"], claves=True)
        out["archivos"] = [cred, cfg]
        if not cred["existe"]:
            out["pistas"].append(
                "No existe el archivo de credenciales de Claude Code. Iniciá sesión con "
                "`claude` → /login en una terminal de Jarvis (la que corre en este mismo "
                "entorno que el servidor).")
            # ¿Se logueó del lado de Windows pero Jarvis corre en WSL?
            try:
                import glob
                for p in glob.glob("/mnt/c/Users/*/.claude/.credentials.json"):
                    out["pistas"].append(
                        f"Hay una sesión en Windows ({p}), pero Jarvis corre en WSL y usa "
                        "otra carpeta: logueate desde una terminal DE JARVIS.")
                    break
            except Exception:
                pass
            if sys.platform == "darwin":
                out["pistas"].append(
                    "En macOS Claude Code guarda la sesión en el Llavero, no en un archivo: "
                    "Jarvis todavía no la lee de ahí.")
        elif cred.get("json_valido") and "claudeAiOauth" not in (cred.get("claves") or []):
            out["pistas"].append(
                "El archivo existe pero NO trae `claudeAiOauth`: es un login por API key/Console, "
                "no por suscripción (Claude.ai). Hacé /login → «Claude account with subscription».")
    else:
        out["archivos"] = [_info_archivo(p) for p in spec.get("principales", spec.get("files", []))]
        if not out["logueado"]:
            out["pistas"].append(f"No se encontró sesión de {spec['label']}. Cómo loguear: "
                                 f"{spec.get('como_loguear', '')}")
    return out


def estado():
    """Payload de la sección Cuentas: por cada CLI, sus cuentas guardadas + qué
    cuenta está logueada AHORA en el HOME + si esa sesión no está guardada."""
    adoptar_sesiones_nativas()
    _backfill_identidades()
    perfiles = listar()
    por_tipo = {}
    for p in perfiles:
        por_tipo.setdefault(p["tipo"], []).append(p)

    specs = _specs()
    clis = []
    for tipo in TIPOS:
        cuentas = por_tipo.get(tipo, [])
        logued = esta_logueado(tipo)
        em = email_actual(tipo) if logued else None
        activo = next((c for c in cuentas if c["activa"]), None)
        emails = {c["email"] for c in cuentas if c["email"]}
        # Sin email (opencode/antigravity) no se puede comparar identidades: si hay
        # un perfil activo se asume que el HOME es suyo (su snapshot salió de ahí);
        # el aviso «sesión sin guardar» queda para HOME logueado sin NINGÚN perfil.
        coincide = (em in emails) if em else bool(logued and (activo or not cuentas))
        clis.append({
            "tipo": tipo,
            "label": specs[tipo]["label"],
            "como_loguear": specs[tipo].get("como_loguear", ""),
            "logueado": logued,
            "email_actual": em,
            "perfil_activo_id": activo["id"] if activo else None,
            "home_sin_guardar": bool(logued and not coincide),
            "cuentas": cuentas,
        })
    # Los CLIs CON cuentas guardadas suben arriba; dentro de cada grupo se
    # respeta el orden canónico de TIPOS (sort ESTABLE — la lista ya viene en
    # ese orden). Pedido del usuario 2026-07-10: "qwen con cuenta va arriba de
    # grok sin cuenta, pero si después agrego grok, grok vuelve arriba de qwen".
    clis.sort(key=lambda c: 0 if c["cuentas"] else 1)
    return {"clis": clis}
