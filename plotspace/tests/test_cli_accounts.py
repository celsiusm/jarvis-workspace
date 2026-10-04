"""
Tests del core de Cuentas de CLIs (plotspace/core/cli_accounts.py).

La feature permite tener MÚLTIPLES cuentas por CLI (claude/codex/qwen/
opencode) y switchear la activa sin re-loguear, vía snapshot+restore de los
archivos de credencial del HOME. Lo MÁS delicado es Claude: su login mezcla
credencial con config no-credencial (projects/mcps) en ~/.claude.json, así que
el switch debe MERGEAR claves quirúrgicamente, no pisar el archivo entero.

Aislamiento: monkeypatch de HOME_DIR + SNAPSHOTS_DIR a tempdirs (nunca toca el
HOME real ni data/). fresh_db() para la metadata. Corre como script o por pytest.
"""
import base64
import json
import os
import stat
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from plotspace.tests._harness import fresh_db
import plotspace.core.cli_accounts as ca


# ── helpers de fixture ──────────────────────────────────────────────────────

def _entorno(monkeypatch):
    """Repunta HOME_DIR + SNAPSHOTS_DIR a tempdirs, limpia CODEX_HOME y las vars
    de config de Cursor (XDG_CONFIG_HOME / CURSOR_CONFIG_DIR) con sus defaults."""
    fresh_db()
    home = tempfile.mkdtemp(prefix="jarvis_home_")
    snaps = tempfile.mkdtemp(prefix="jarvis_snaps_")
    monkeypatch.setattr(ca, "HOME_DIR", home)
    monkeypatch.setattr(ca, "SNAPSHOTS_DIR", snaps)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("CURSOR_CONFIG_DIR", raising=False)
    return home, snaps


def _escribir(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(obj if isinstance(obj, str) else json.dumps(obj))


def _jwt(payload):
    seg = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"aaa.{seg}.bbb"


def _claude_login(home, email, token):
    """Simula un login de Claude en el HOME: .credentials.json + .claude.json
    con config no-credencial (projects/mcps) que NO se debe perder al switchear."""
    _escribir(os.path.join(home, ".claude", ".credentials.json"), {
        "claudeAiOauth": {"accessToken": token, "refreshToken": "r-" + token,
                          "subscriptionType": "max"},
        "mcpOAuth": {"unsplash": {"token": "mcp-keep"}},
    })
    _escribir(os.path.join(home, ".claude.json"), {
        "oauthAccount": {"emailAddress": email, "accountUuid": "uuid-" + email},
        "userID": "user-" + email,
        "projects": {"/home/x": {"history": []}},
        "mcpServers": {"iconify": {"command": "x"}},
        "tipsHistory": {"a": 1},
    })


# ── specs / tipos ───────────────────────────────────────────────────────────

def test_tipos_soportados(monkeypatch):
    _entorno(monkeypatch)
    assert set(ca.TIPOS) == {"claude", "codex", "grok", "antigravity", "opencode", "qwen", "pi", "cursor"}
    specs = ca._specs()
    for t in ca.TIPOS:
        assert t in specs and "label" in specs[t]


# ── codex (homes AISLADOS por cuenta — CODEX_HOME propio) ────────────────────

def test_codex_capturar_y_home_aislado(monkeypatch):
    home, snaps = _entorno(monkeypatch)
    auth = os.path.join(home, ".codex", "auth.json")
    _escribir(auth, {"OPENAI_API_KEY": None,
                     "tokens": {"id_token": _jwt({"email": "dev@openai.com"}),
                                "account_id": "acc-1", "access_token": "AAA"}})

    assert ca.esta_logueado("codex") is True
    perfil = ca.capturar_actual("codex", "Trabajo")
    assert perfil["id"] > 0
    assert perfil["tipo"] == "codex"
    assert perfil["email"] == "dev@openai.com"   # extraído del id_token (JWT)
    # el dir del perfil (= su CODEX_HOME aislado) tiene el auth.json
    snap_file = os.path.join(snaps, str(perfil["id"]), "auth.json")
    assert os.path.isfile(snap_file)


def test_codex_usar_marca_activa_sin_pisar_home_compartido(monkeypatch):
    # Codex NO restaura sobre ~/.codex al switchear (eso compartía el auth.json y
    # disparaba la revocación por reuso). usar() solo marca activa; cada cuenta usa
    # su CODEX_HOME aislado (su dir de perfil).
    home, snaps = _entorno(monkeypatch)
    auth = os.path.join(home, ".codex", "auth.json")
    _escribir(auth, {"tokens": {"id_token": _jwt({"email": "a@openai.com"}),
                                "account_id": "acc-1", "access_token": "AAA"}})
    pa = ca.capturar_actual("codex", "A")

    # cuenta B logueada y capturada (queda activa, HOME = B)
    _escribir(auth, {"tokens": {"id_token": _jwt({"email": "b@openai.com"}),
                                "account_id": "acc-2", "access_token": "BBB"}})
    pb = ca.capturar_actual("codex", "B")

    # el usuario "se desloguea" del HOME compartido (no debería importar para codex)
    os.remove(auth)

    p2 = ca.usar(pa["id"])
    assert p2["activa"] is True                       # quedó activa A
    # NO se restauró nada sobre ~/.codex (homes aislados)
    assert not os.path.isfile(auth)
    # CODEX_HOME para las terminales = symlink estable, que RESUELVE al dir de A
    home_a = ca.codex_home(pa["id"])
    assert os.path.isabs(home_a) and os.path.isdir(home_a)
    assert os.path.realpath(ca.codex_home_activo()) == os.path.realpath(home_a)
    assert home_a != ca.codex_home(pb["id"])          # cada cuenta, su propio home
    # el home de A conserva su auth.json (capturado), independiente del de B
    assert os.path.isfile(os.path.join(home_a, "auth.json"))


def test_codex_switch_repunta_symlink_estable(monkeypatch):
    # El CODEX_HOME de las terminales es un symlink ESTABLE: su PATH no cambia entre
    # cuentas, pero RESUELVE a la cuenta activa. Cambiar de cuenta solo lo repunta →
    # una terminal ya abierta (con ese CODEX_HOME) usa la cuenta nueva al re-correr
    # codex, sin reabrir.
    home, _ = _entorno(monkeypatch)
    auth = os.path.join(home, ".codex", "auth.json")
    _escribir(auth, {"tokens": {"id_token": _jwt({"email": "a@x.com"}), "account_id": "1"}})
    pa = ca.capturar_actual("codex", "A")
    _escribir(auth, {"tokens": {"id_token": _jwt({"email": "b@x.com"}), "account_id": "2"}})
    pb = ca.capturar_actual("codex", "B")

    ca.usar(pa["id"])
    link = ca.codex_home_activo()
    assert os.path.realpath(link) == os.path.realpath(ca.codex_home(pa["id"]))

    ca.usar(pb["id"])
    link2 = ca.codex_home_activo()
    assert link2 == link                       # MISMO path (symlink estable)
    # resuelve a B ahora, sin que la terminal haya cambiado su CODEX_HOME
    assert os.path.realpath(link2) == os.path.realpath(ca.codex_home(pb["id"]))


def test_codex_recapturar_no_pisa_home_vivo(monkeypatch):
    # HUECO #1 (auditoría): re-capturar / preservar NO deben pisar un home aislado
    # que YA tiene tokens. Pisar ~/.codex (viejo) encima del token vigente del dir
    # dispara la revocación por reuso de OpenAI. Solo se puebla un dir vacío.
    home, snaps = _entorno(monkeypatch)
    auth = os.path.join(home, ".codex", "auth.json")
    _escribir(auth, {"tokens": {"id_token": _jwt({"email": "a@openai.com"}),
                                "account_id": "OLD", "refresh_token": "rt-OLD"}})
    p = ca.capturar_actual("codex", "A")          # dir vacío -> se puebla con OLD
    snap = os.path.join(snaps, str(p["id"]), "auth.json")
    assert json.load(open(snap))["tokens"]["account_id"] == "OLD"

    # codex (en su terminal aislada) rota el token EN EL DIR de la cuenta -> NEW
    _escribir(snap, {"tokens": {"account_id": "NEW", "refresh_token": "rt-NEW"}})
    # ~/.codex sigue con OLD (sesión vieja del HOME compartido). recapturar NO debe
    # pisar NEW con OLD.
    ca.recapturar(p["id"])
    assert json.load(open(snap))["tokens"]["account_id"] == "NEW"   # intacto


def test_codex_home_siembra_config_toml(monkeypatch):
    # codex_home() siembra un config.toml base desde ~/.codex la primera vez
    # (codex exige que el CODEX_HOME exista y tenga su config).
    home, snaps = _entorno(monkeypatch)
    _escribir(os.path.join(home, ".codex", "auth.json"), {"tokens": {"account_id": "1"}})
    _escribir(os.path.join(home, ".codex", "config.toml"), 'model = "gpt-5.5"\n')
    p = ca.capturar_actual("codex", "X")
    h = ca.codex_home(p["id"])
    cfg = os.path.join(h, "config.toml")
    assert os.path.isfile(cfg)
    assert 'gpt-5.5' in open(cfg, encoding="utf-8").read()


def test_codex_snapshot_solo_auth_no_dir_completo(monkeypatch):
    # ~/.codex comparte dir con sqlite de OTRO producto: el snapshot NO debe
    # llevarse goals.sqlite etc, SOLO auth.json.
    home, snaps = _entorno(monkeypatch)
    _escribir(os.path.join(home, ".codex", "auth.json"), {"tokens": {"account_id": "x"}})
    _escribir(os.path.join(home, ".codex", "goals_1.sqlite"), "BINARIO-SQLITE")
    p = ca.capturar_actual("codex", "X")
    contenido = os.listdir(os.path.join(snaps, str(p["id"])))
    assert contenido == ["auth.json"]


# ── alta de cuenta codex NUEVA (login en staging dir aislado) ────────────────

def test_alta_codex_nueva_no_toca_home_de_la_activa(monkeypatch):
    # HEADLINE: dar de alta una 2da cuenta codex DISTINTA NO debe tocar el home
    # aislado de la cuenta ACTIVA. El login de la nueva escribe en un staging dir
    # fresco; capturar_codex_login la instala en su PROPIO dir y repunta el symlink.
    # (Antes el login reusaba el home de la activa y reescribía su auth.json → la
    #  pisaba/revocaba al loguear otra cuenta de OpenAI.)
    home, snaps = _entorno(monkeypatch)
    # Cuenta A logueada y guardada, con su token vigente en su dir aislado
    _escribir(os.path.join(home, ".codex", "auth.json"),
              {"tokens": {"id_token": _jwt({"email": "a@openai.com"}),
                          "account_id": "ACC-A", "refresh_token": "rt-A-vigente"}})
    pa = ca.capturar_actual("codex", "A")
    dir_a = ca.codex_home(pa["id"])
    auth_a_antes = open(os.path.join(dir_a, "auth.json"), encoding="utf-8").read()
    ca.codex_home_activo()                       # symlink _codex_active → A
    assert os.path.realpath(ca._codex_active_link()) == os.path.realpath(dir_a)

    # El login de la cuenta NUEVA va a un staging AISLADO y FRESCO (≠ dir de A)
    staging = ca.codex_login_home_nuevo()
    assert os.path.realpath(staging) != os.path.realpath(dir_a)
    assert os.path.basename(staging) == "_codex_login"
    # simular que codex depositó la cuenta B en el staging
    _escribir(os.path.join(staging, "auth.json"),
              {"tokens": {"id_token": _jwt({"email": "b@openai.com"}),
                          "account_id": "ACC-B", "refresh_token": "rt-B"}})
    pb = ca.capturar_codex_login("B")

    # 1) el home de la ACTIVA (A) quedó intacto byte-a-byte (no fue pisado)
    assert open(os.path.join(dir_a, "auth.json"), encoding="utf-8").read() == auth_a_antes
    # 2) B es una cuenta nueva, distinta, en su propio dir aislado
    assert pb["id"] != pa["id"] and pb["email"] == "b@openai.com"
    dir_b = ca.codex_home(pb["id"])
    assert dir_b != dir_a
    assert json.load(open(os.path.join(dir_b, "auth.json")))["tokens"]["account_id"] == "ACC-B"
    # 3) ahora B es la activa y el symlink estable resuelve a B
    activos = [c for c in ca.listar() if c["tipo"] == "codex" and c["activa"]]
    assert len(activos) == 1 and activos[0]["id"] == pb["id"]
    assert os.path.realpath(ca._codex_active_link()) == os.path.realpath(dir_b)
    # 4) el staging quedó limpio (sin auth.json) para el próximo alta
    assert not os.path.isfile(os.path.join(staging, "auth.json"))


def test_adoptar_codex_relogin_instala_tokens_frescos(monkeypatch):
    # Re-login (vía «agregar cuenta nueva») de una cuenta codex YA guardada: instala
    # los tokens FRESCOS del staging en su dir, la activa y repunta el symlink, SIN
    # duplicar el perfil. Es un login interactivo nuevo (tokens recién emitidos), no
    # un reuse de refresh token → no dispara revocación.
    home, snaps = _entorno(monkeypatch)
    _escribir(os.path.join(home, ".codex", "auth.json"),
              {"tokens": {"id_token": _jwt({"email": "a@openai.com"}),
                          "account_id": "ACC-A", "refresh_token": "rt-VIEJO"}})
    pa = ca.capturar_actual("codex", "A")
    dir_a = ca.codex_home(pa["id"])

    # el usuario re-loguea A: el staging recibe tokens NUEVOS de A
    staging = ca.codex_login_home_nuevo()
    _escribir(os.path.join(staging, "auth.json"),
              {"tokens": {"id_token": _jwt({"email": "a@openai.com"}),
                          "account_id": "ACC-A", "refresh_token": "rt-NUEVO"}})
    p2 = ca.adoptar_codex_login(pa["id"])

    assert p2["id"] == pa["id"]                              # mismo perfil, no duplica
    assert len([c for c in ca.listar() if c["tipo"] == "codex"]) == 1
    # el dir de A ahora tiene los tokens frescos
    assert json.load(open(os.path.join(dir_a, "auth.json")))["tokens"]["refresh_token"] == "rt-NUEVO"
    assert p2["activa"] is True
    assert os.path.realpath(ca._codex_active_link()) == os.path.realpath(dir_a)


# ── claude (merge quirúrgico) ───────────────────────────────────────────────

def test_claude_switch_preserva_config_no_credencial(monkeypatch):
    home, snaps = _entorno(monkeypatch)
    # Cuenta A logueada -> capturar
    _claude_login(home, "a@mail.com", "tokA")
    pa = ca.capturar_actual("claude", "Personal")
    assert pa["email"] == "a@mail.com"

    # El usuario loguea la cuenta B en el HOME y AGREGA un proyecto/mcp nuevos
    # (el HOME evolucionó mientras estaba en B).
    _claude_login(home, "b@work.com", "tokB")
    cfg = json.load(open(os.path.join(home, ".claude.json"), encoding="utf-8"))
    cfg["projects"]["/home/nuevo"] = {"history": ["importante"]}
    cfg["mcpServers"]["magic"] = {"command": "y"}
    _escribir(os.path.join(home, ".claude.json"), cfg)
    pb = ca.capturar_actual("claude", "Trabajo")
    assert pb["email"] == "b@work.com"

    # Volver a la cuenta A
    ca.usar(pa["id"])

    cred = json.load(open(os.path.join(home, ".claude", ".credentials.json"), encoding="utf-8"))
    cfg2 = json.load(open(os.path.join(home, ".claude.json"), encoding="utf-8"))
    # la cuenta volvió a A
    assert cred["claudeAiOauth"]["accessToken"] == "tokA"
    assert cfg2["oauthAccount"]["emailAddress"] == "a@mail.com"
    assert cfg2["userID"] == "user-a@mail.com"
    # CRÍTICO: la config no-credencial que se agregó con B NO se perdió
    assert "/home/nuevo" in cfg2["projects"]
    assert cfg2["projects"]["/home/nuevo"]["history"] == ["importante"]
    assert "magic" in cfg2["mcpServers"]
    # el mcpOAuth (independiente de la cuenta) también se preserva
    assert cred["mcpOAuth"]["unsplash"]["token"] == "mcp-keep"


def test_claude_snapshot_no_guarda_config_ajena(monkeypatch):
    # El snapshot de claude debe contener SOLO las 3 claves de cuenta, nunca
    # projects/mcpServers (config gorda que no es de la cuenta).
    home, snaps = _entorno(monkeypatch)
    _claude_login(home, "a@mail.com", "tokA")
    p = ca.capturar_actual("claude", "A")
    snap = json.load(open(os.path.join(snaps, str(p["id"]), "claude-account.json"),
                       encoding="utf-8"))
    assert set(snap.keys()) == {"claudeAiOauth", "oauthAccount", "userID"}
    assert "projects" not in snap


# ── switch refresca el snapshot del perfil que estaba activo ────────────────

def test_usar_refresca_token_del_activo_antes_de_switchear(monkeypatch):
    home, snaps = _entorno(monkeypatch)
    _claude_login(home, "a@mail.com", "tokA")
    pa = ca.capturar_actual("claude", "A")
    _claude_login(home, "b@work.com", "tokB")
    pb = ca.capturar_actual("claude", "B")   # B queda activo, HOME = B

    # El CLI refresca el access token de B en el HOME (expiresAt vence).
    cred = json.load(open(os.path.join(home, ".claude", ".credentials.json"), encoding="utf-8"))
    cred["claudeAiOauth"]["accessToken"] = "tokB-REFRESCADO"
    _escribir(os.path.join(home, ".claude", ".credentials.json"), cred)

    # Switch a A: antes de pisar el HOME, debe re-snapshotear B (estaba activo y
    # el HOME coincide con su email) para no perder el token refrescado.
    ca.usar(pa["id"])

    # Ahora volver a B y verificar que recuperamos el token REFRESCADO, no el viejo
    ca.usar(pb["id"])
    cred_b = json.load(open(os.path.join(home, ".claude", ".credentials.json"), encoding="utf-8"))
    assert cred_b["claudeAiOauth"]["accessToken"] == "tokB-REFRESCADO"


# ── invariante: un solo activo por tipo ─────────────────────────────────────

def test_un_solo_activo_por_tipo(monkeypatch):
    home, _ = _entorno(monkeypatch)
    _escribir(os.path.join(home, ".codex", "auth.json"), {"tokens": {"account_id": "1"}})
    p1 = ca.capturar_actual("codex", "uno")
    _escribir(os.path.join(home, ".codex", "auth.json"), {"tokens": {"account_id": "2"}})
    p2 = ca.capturar_actual("codex", "dos")

    activos = [c for c in ca.listar() if c["tipo"] == "codex" and c["activa"]]
    assert len(activos) == 1 and activos[0]["id"] == p2["id"]

    ca.usar(p1["id"])
    activos = [c for c in ca.listar() if c["tipo"] == "codex" and c["activa"]]
    assert len(activos) == 1 and activos[0]["id"] == p1["id"]


# ── permisos restrictivos en los snapshots ──────────────────────────────────

def test_snapshot_con_permisos_restringidos(monkeypatch):
    home, snaps = _entorno(monkeypatch)
    _escribir(os.path.join(home, ".codex", "auth.json"), {"tokens": {"account_id": "1"}})
    p = ca.capturar_actual("codex", "x")
    d = os.path.join(snaps, str(p["id"]))
    f = os.path.join(d, "auth.json")
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700


# ── eliminar borra fila + dir de snapshot ───────────────────────────────────

def test_eliminar_borra_fila_y_snapshot(monkeypatch):
    home, snaps = _entorno(monkeypatch)
    _escribir(os.path.join(home, ".codex", "auth.json"), {"tokens": {"account_id": "1"}})
    p = ca.capturar_actual("codex", "x")
    d = os.path.join(snaps, str(p["id"]))
    assert os.path.isdir(d)
    ca.eliminar(p["id"])
    assert not os.path.isdir(d)
    assert all(c["id"] != p["id"] for c in ca.listar())


# ── capturar sin login -> error ─────────────────────────────────────────────

def test_capturar_sin_login_falla(monkeypatch):
    _entorno(monkeypatch)
    try:
        ca.capturar_actual("qwen", "x")
        assert False, "debió fallar: qwen no está logueado"
    except ca.NoLogueado:
        pass


# ── estado(): payload para la UI ────────────────────────────────────────────

def test_estado_adopta_sesion_nativa_sin_conectar(monkeypatch):
    """Grok (u otro CLI) logueado en el HOME, nunca tocado en ⚙ → Cuentas:
    estado() lo detecta y lo guarda solo. Pedido: no hace falta pulsar Conectar."""
    home, _ = _entorno(monkeypatch)
    _grok_login(home, "user@example.com")
    est = ca.estado()
    grok = next(c for c in est["clis"] if c["tipo"] == "grok")
    assert grok["logueado"] is True
    assert grok["home_sin_guardar"] is False
    assert len(grok["cuentas"]) == 1
    assert grok["cuentas"][0]["email"] == "user@example.com"
    assert grok["cuentas"][0]["activa"] is True
    # segunda lectura no duplica
    est2 = ca.estado()
    grok2 = next(c for c in est2["clis"] if c["tipo"] == "grok")
    assert len(grok2["cuentas"]) == 1


def test_estado_reporta_clis_y_home_sin_guardar(monkeypatch):
    home, _ = _entorno(monkeypatch)
    # codex logueado: estado() lo ADOPTA (ya no queda «sin guardar»)
    _escribir(os.path.join(home, ".codex", "auth.json"),
              {"tokens": {"id_token": _jwt({"email": "x@y.com"}), "account_id": "1"}})
    est = ca.estado()
    por_tipo = {c["tipo"]: c for c in est["clis"]}
    assert set(por_tipo) == set(ca.TIPOS)
    assert por_tipo["codex"]["logueado"] is True
    assert por_tipo["codex"]["home_sin_guardar"] is False
    assert len(por_tipo["codex"]["cuentas"]) == 1
    assert por_tipo["qwen"]["logueado"] is False


def test_estado_sin_email_con_perfil_activo_no_avisa(monkeypatch):
    # CLIs SIN email único (opencode/antigravity): con un perfil guardado y activo,
    # el HOME se asume suyo (su snapshot salió de ahí) → NO mostrar el aviso
    # «sesión sin guardar» a perpetuidad (bug de la comparación por email=None).
    home, _ = _entorno(monkeypatch)
    _escribir(os.path.join(home, ".local", "share", "opencode", "auth.json"),
              {"anthropic": {"type": "oauth"}})
    est = ca.estado()
    oc = next(c for c in est["clis"] if c["tipo"] == "opencode")
    assert oc["logueado"] is True
    assert oc["home_sin_guardar"] is False
    assert len(oc["cuentas"]) == 1                # sesión nativa adoptada sola

    est2 = ca.estado()
    oc2 = next(c for c in est2["clis"] if c["tipo"] == "opencode")
    assert oc2["perfil_activo_id"] is not None
    assert oc2["home_sin_guardar"] is False
    assert len(oc2["cuentas"]) == 1               # no duplica


# ── auto-rotación: el switch que usa agent_watch al pegar el rate-limit ──────
# Verifica que la capa impura de la rotación (agent_watch._rotar_cuenta_sync)
# reusa el switch EXISTENTE (cli_accounts.usar) y devuelve el shape del WS.

def test_rotar_cuenta_sync_switchea_a_la_proxima(monkeypatch):
    import plotspace.core.agent_watch as aw
    home, _ = _entorno(monkeypatch)
    aw._limite_cuenta.clear()
    aw._rotacion_terminal.clear()

    auth = os.path.join(home, ".codex", "auth.json")
    _escribir(auth, {"tokens": {"id_token": _jwt({"email": "a@x.com"}), "account_id": "1"}})
    pa = ca.capturar_actual("codex", "Cuenta A")
    _escribir(auth, {"tokens": {"id_token": _jwt({"email": "b@x.com"}), "account_id": "2"}})
    pb = ca.capturar_actual("codex", "Cuenta B")     # B queda activa

    res = aw._rotar_cuenta_sync("codex")
    # rotó de la activa (B) a la otra sana (A)
    assert res["de"] == pb["id"]
    assert res["a"] == pa["id"]
    assert res["a_label"] == "Cuenta A"
    assert res["de_label"] == "Cuenta B"
    # el switch EXISTENTE corrió: A es la activa ahora
    activos = [c for c in ca.listar() if c["tipo"] == "codex" and c["activa"]]
    assert len(activos) == 1 and activos[0]["id"] == pa["id"]
    # B quedó marcada como limitada (no se la elige de vuelta enseguida)
    assert pb["id"] in aw.cuentas_limitadas()


def test_rotar_cuenta_sync_una_sola_cuenta_sin_candidata(monkeypatch):
    import plotspace.core.agent_watch as aw
    home, _ = _entorno(monkeypatch)
    aw._limite_cuenta.clear()
    aw._rotacion_terminal.clear()

    auth = os.path.join(home, ".codex", "auth.json")
    _escribir(auth, {"tokens": {"id_token": _jwt({"email": "a@x.com"}), "account_id": "1"}})
    pa = ca.capturar_actual("codex", "Única")

    res = aw._rotar_cuenta_sync("codex")
    assert res == {"sin_cuenta": True}
    # sigue activa la única
    activos = [c for c in ca.listar() if c["tipo"] == "codex" and c["activa"]]
    assert len(activos) == 1 and activos[0]["id"] == pa["id"]


# ── router HTTP (TestClient sobre el router montado, sin token-gate) ─────────

def test_router_flujo_completo(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import plotspace.routers.cuentas as r

    home, _ = _entorno(monkeypatch)
    app = FastAPI()
    app.include_router(r.router)
    client = TestClient(app)

    est = client.get("/api/cuentas").json()
    assert len(est["clis"]) == len(ca.TIPOS)

    # POST sin login -> 409
    assert client.post("/api/cuentas", json={"tipo": "codex", "label": "x"}).status_code == 409
    # tipo inválido -> 400
    assert client.post("/api/cuentas", json={"tipo": "nope"}).status_code == 400

    _escribir(os.path.join(home, ".codex", "auth.json"),
              {"tokens": {"account_id": "1", "id_token": _jwt({"email": "a@b.com"})}})
    r1 = client.post("/api/cuentas", json={"tipo": "codex", "label": "Trabajo"})
    assert r1.status_code == 200
    pid = r1.json()["id"]
    assert r1.json()["email"] == "a@b.com"

    assert client.post(f"/api/cuentas/{pid}/usar").status_code == 200
    assert client.put(f"/api/cuentas/{pid}", json={"label": "Nuevo"}).json()["label"] == "Nuevo"
    assert client.delete(f"/api/cuentas/{pid}").status_code == 200
    assert client.delete(f"/api/cuentas/{pid}").status_code == 404


# ── antigravity (credencial binaria: credentials.enc + settings.json) ────────

def _antigravity_login(home, blob):
    """Simula el login de agy en el HOME: credentials.enc (BLOB binario cifrado)
    + settings.json. El .enc NO es texto → se escribe en bytes."""
    d = os.path.join(home, ".gemini", "antigravity-cli")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "credentials.enc"), "wb") as f:
        f.write(blob)
    _escribir(os.path.join(d, "settings.json"), {"theme": "dark"})


def test_antigravity_switch_round_trip_binario(monkeypatch):
    """El switch de cuentas de antigravity restaura el .enc BYTE-IDÉNTICO. Es el
    caso que rompía el restore genérico (lee utf-8): el .enc lleva bytes no-texto."""
    home, _ = _entorno(monkeypatch)
    enc = os.path.join(home, ".gemini", "antigravity-cli", "credentials.enc")

    blob_a = bytes([0, 1, 2, 255, 254, 0, 42])   # 0x00/0xff → un read utf-8 lo corrompe
    _antigravity_login(home, blob_a)
    assert ca.esta_logueado("antigravity") is True
    assert ca.email_actual("antigravity") is None     # sin email (credencial cifrada)
    pa = ca.capturar_actual("antigravity", "A")

    blob_b = bytes([9, 8, 7, 0, 255, 128])        # cuenta B pisa el HOME (re-login)
    _antigravity_login(home, blob_b)
    pb = ca.capturar_actual("antigravity", "B")
    with open(enc, "rb") as f:
        assert f.read() == blob_b                 # HOME = B tras capturar B

    ca.usar(pa["id"])                             # switch a A → restaura su .enc crudo
    with open(enc, "rb") as f:
        assert f.read() == blob_a

    ca.usar(pb["id"])                             # y de vuelta a B
    with open(enc, "rb") as f:
        assert f.read() == blob_b


def test_antigravity_token_111_round_trip(monkeypatch):
    """agy 1.1.x guarda la credencial en `antigravity-oauth-token` (JSON plano),
    ya NO en credentials.enc — con el spec viejo, una sesión REAL daba
    esta_logueado=False (la UI mostraba «sin sesión», preservar no preservaba y
    el watcher del alta quedaba ciego). El token nuevo debe contar como sesión
    y hacer el round-trip de switch completo."""
    home, _ = _entorno(monkeypatch)
    d = os.path.join(home, ".gemini", "antigravity-cli")
    os.makedirs(d, exist_ok=True)
    tok = os.path.join(d, "antigravity-oauth-token")
    with open(tok, "w", encoding="utf-8") as f:
        f.write('{"access_token": "AAA"}')
    assert ca.esta_logueado("antigravity") is True     # el spec viejo daba False
    pa = ca.capturar_actual("antigravity", "A")

    with open(tok, "w", encoding="utf-8") as f:
        f.write('{"access_token": "BBB"}')
    pb = ca.capturar_actual("antigravity", "B")

    ca.usar(pa["id"])
    with open(tok, encoding="utf-8") as f:
        assert f.read() == '{"access_token": "AAA"}'
    ca.usar(pb["id"])
    with open(tok, encoding="utf-8") as f:
        assert f.read() == '{"access_token": "BBB"}'


def test_antigravity_credentials_enc_sigue_valiendo(monkeypatch):
    # Legacy agy 1.0.x: credentials.enc solo (sin token nuevo) sigue contando
    # como sesión — snapshots viejos no quedan huérfanos tras el update del spec.
    home, _ = _entorno(monkeypatch)
    _antigravity_login(home, bytes([1, 2, 3]))
    assert ca.esta_logueado("antigravity") is True


# ── identidad para la UI: grok / opencode / antigravity ─────────────────────
# Bug: la columna `email` quedaba NULL para estos 3 CLIs → la UI mostraba «no
# email detected» aunque la cuenta funcione. Cada uno guarda su identidad en un
# lugar distinto (grok: dentro del auth.json; opencode: no hay email, la
# identidad son los proveedores; antigravity: el token es opaco, el email vive
# en los logs del CLI).

def _grok_login(home, email, clientid="cid-1"):
    """Login de grok: ~/.grok/auth.json es un mapa `<issuer>::<clientid>` → perfil,
    con el email DENTRO de cada valor (no en la raíz)."""
    _escribir(os.path.join(home, ".grok", "auth.json"), {
        f"https://auth.x.ai::{clientid}": {
            "email": email, "user_id": "u-" + clientid,
            "refresh_token": "rt", "auth_mode": "oidc",
        },
    })


def test_grok_email_desde_auth_json(monkeypatch):
    home, _ = _entorno(monkeypatch)
    _grok_login(home, "agent@example.com")
    assert ca.esta_logueado("grok") is True
    # el email de grok SÍ identifica la cuenta (está en la credencial) → sirve
    # para deduplicar igual que claude/codex
    assert ca.email_actual("grok") == "agent@example.com"
    p = ca.capturar_actual("grok", "xAI")
    assert p["email"] == "agent@example.com"            # y persistido para la UI


def _opencode_login(home, provs):
    """opencode auth.json = mapa proveedor → credencial (api key / oauth). Sin email."""
    _escribir(os.path.join(home, ".local", "share", "opencode", "auth.json"), provs)


def test_opencode_identidad_es_proveedores(monkeypatch):
    home, _ = _entorno(monkeypatch)
    _opencode_login(home, {"openrouter": {"type": "api", "key": "k"},
                           "zai-coding-plan": {"type": "api", "key": "k2"}})
    # opencode NO tiene email único → email_actual sigue None (el watcher dedupea
    # por huella, no lo tocamos)
    assert ca.email_actual("opencode") is None
    p = ca.capturar_actual("opencode", "Router")
    # pero la UI muestra el/los proveedor(es) como identidad (ordenados)
    assert p["email"] == "openrouter, zai-coding-plan"


# ── pi (Earendil) — auth.json mapa proveedor→credencial, sin email ────────────
# Pi persiste ~/.pi/agent/auth.json como `{}` tras /logout → la existencia del
# archivo NO es sesión (verificado en el package 0.85.1: el delete de /logout
# re-escribe el archivo con el objeto vacío). Las credenciales guardadas:
# api_key {type,key} u oauth {type,access,refresh,expires} (+accountId solo para
# openai-codex) — nunca email.

def _pi_login(home, proveedores):
    auth = os.path.join(home, ".pi", "agent", "auth.json")
    os.makedirs(os.path.dirname(auth), exist_ok=True)
    _escribir(auth, proveedores)
    return auth


def test_pi_auth_vacio_tras_logout_no_es_sesion(monkeypatch):
    home, _ = _entorno(monkeypatch)
    _pi_login(home, {})                     # /logout deja el archivo como {}
    assert ca.esta_logueado("pi") is False
    _pi_login(home, {"anthropic": {"type": "api_key", "key": "sk-ant-..."}})
    assert ca.esta_logueado("pi") is True


def test_pi_identidad_es_proveedores(monkeypatch):
    home, _ = _entorno(monkeypatch)
    _pi_login(home, {"openai": {"type": "oauth", "access": "a", "refresh": "r",
                                "expires": 1, "accountId": "acc-1"},
                     "anthropic": {"type": "api_key", "key": "K"}})
    assert ca.email_actual("pi") is None    # sin email → el watcher dedupea por huella
    p = ca.capturar_actual("pi", "Earendil")
    assert p["email"] == "anthropic, openai"


def test_pi_switch_round_trip(monkeypatch):
    home, snaps = _entorno(monkeypatch)
    auth = _pi_login(home, {"anthropic": {"type": "oauth", "access": "AAA",
                                          "refresh": "R-AAA", "expires": 999}})
    pa = ca.capturar_actual("pi", "A")

    _pi_login(home, {"openai": {"type": "oauth", "access": "BBB",
                                "refresh": "R-BBB", "expires": 999}})
    pb = ca.capturar_actual("pi", "B")

    ca.usar(pa["id"])
    assert json.load(open(auth, encoding="utf-8"))["anthropic"]["access"] == "AAA"
    ca.usar(pb["id"])
    assert json.load(open(auth, encoding="utf-8"))["openai"]["access"] == "BBB"
    ca.recapturar(pb["id"])
    assert ca.obtener_perfil(pb["id"])["email"] == "openai"


def test_estado_adopta_sesion_nativa_de_pi(monkeypatch):
    home, _ = _entorno(monkeypatch)
    _pi_login(home, {"xai": {"type": "oauth", "access": "x", "refresh": "r",
                             "expires": 1}})
    est = ca.estado()
    pi = next(c for c in est["clis"] if c["tipo"] == "pi")
    assert pi["logueado"] is True
    assert pi["home_sin_guardar"] is False
    assert len(pi["cuentas"]) == 1
    assert pi["cuentas"][0]["email"] == "xai"
    est2 = ca.estado()
    assert len(next(c for c in est2["clis"] if c["tipo"] == "pi")["cuentas"]) == 1


def _agy_con_log(home, email, token='{"token": {"access_token": "ya29.OPACO"}}'):
    """agy: token opaco (SIN email) + una línea de auth en el log — de ahí es
    recuperable el email."""
    d = os.path.join(home, ".gemini", "antigravity-cli")
    os.makedirs(os.path.join(d, "log"), exist_ok=True)
    with open(os.path.join(d, "antigravity-oauth-token"), "w", encoding="utf-8") as f:
        f.write(token)
    with open(os.path.join(d, "log", "cli-1.log"), "w", encoding="utf-8") as f:
        f.write("I0710 server_oauth.go:219] OAuth: authenticated successfully as %s\n" % email)


def test_antigravity_email_desde_log(monkeypatch):
    home, _ = _entorno(monkeypatch)
    _agy_con_log(home, "user@example.com")
    # el token es opaco → email_actual (dedup) SIGUE None (no rompe el watcher)
    assert ca.email_actual("antigravity") is None
    p = ca.capturar_actual("antigravity", "Google")
    # pero la identidad para MOSTRAR sale del log
    assert p["email"] == "user@example.com"


def test_antigravity_email_log_mas_reciente_gana(monkeypatch):
    # Con varios logs, gana el email del log MÁS reciente (última autenticación).
    home, _ = _entorno(monkeypatch)
    d = os.path.join(home, ".gemini", "antigravity-cli", "log")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(home, ".gemini", "antigravity-cli",
                           "antigravity-oauth-token"), "w", encoding="utf-8") as f:
        f.write('{"token": {"access_token": "x"}}')
    viejo = os.path.join(d, "cli-viejo.log")
    nuevo = os.path.join(d, "cli-nuevo.log")
    for p, em in ((viejo, "viejo@gmail.com"), (nuevo, "nuevo@gmail.com")):
        with open(p, "w", encoding="utf-8") as f:
            f.write("authenticated successfully as %s\n" % em)
    os.utime(viejo, (1000, 1000))
    os.utime(nuevo, (2000, 2000))
    assert ca.identidad_mostrable("antigravity") == "nuevo@gmail.com"


def test_backfill_rellena_email_null_de_cuentas_viejas(monkeypatch):
    # Cuentas guardadas ANTES del fix quedaron con email NULL en la DB. estado()
    # las rellena best-effort desde el snapshot (grok/opencode), sin re-loguear.
    home, _ = _entorno(monkeypatch)
    _grok_login(home, "agent@example.com")
    p = ca.capturar_actual("grok", "xAI")
    # simular el estado viejo: la fila quedó con email NULL (pero el snapshot SÍ
    # tiene la identidad — se capturó el auth.json entero)
    conn = ca.get_db()
    conn.execute("UPDATE cli_accounts SET email = NULL WHERE id = ?", (p["id"],))
    conn.commit()
    conn.close()
    assert ca.obtener_perfil(p["id"])["email"] is None

    ca.estado()   # dispara el backfill
    assert ca.obtener_perfil(p["id"])["email"] == "agent@example.com"


def test_backfill_no_pisa_email_ya_detectado(monkeypatch):
    # El backfill es idempotente: no toca filas que ya tienen email.
    home, _ = _entorno(monkeypatch)
    _grok_login(home, "agent@example.com")
    p = ca.capturar_actual("grok", "xAI")
    assert ca.obtener_perfil(p["id"])["email"] == "agent@example.com"
    ca.estado()
    ca.estado()
    assert ca.obtener_perfil(p["id"])["email"] == "agent@example.com"


# ── cursor — auth.json plano {accessToken,refreshToken,apiKey}, sin email ───
# Cursor CLI (curl installer): la credencial vive en ~/.config/cursor/auth.json
# (o $XDG_CONFIG_HOME/cursor/auth.json) y es JSON plano SIN identidad de cuenta;
# agent logout la BORRA → la existencia ≈ sesión. El email best-effort sale del
# authInfo de ~/.cursor/cli-config.json (lo escribe `agent login`), solo para
# mostrar, igual patrón que antigravity.

def _cursor_login(home, tokens=None, email=None):
    """Login de cursor simulado: auth.json con access/refresh (+ opcionalmente
    authInfo.email en el cli-config.json que el CLI escribe al terminar login)."""
    auth = os.path.join(home, ".config", "cursor", "auth.json")
    os.makedirs(os.path.dirname(auth), exist_ok=True)
    _escribir(auth, tokens or {"accessToken": "csk-AAA", "refreshToken": "r-AAA"})
    if email is not None:
        cfg = os.path.join(home, ".cursor", "cli-config.json")
        os.makedirs(os.path.dirname(cfg), exist_ok=True)
        _escribir(cfg, {"version": 1, "authInfo": {"email": email}})
    return auth


def test_cursor_credencial_en_xdg_config(monkeypatch):
    home, _ = _entorno(monkeypatch)
    a = _cursor_login(home, {"accessToken": "csk-AAA"})
    assert ca.esta_logueado("cursor") is True
    # la credencial vive en el XDG default y el spec la expande igual
    assert os.path.basename(a) == "auth.json"
    # sin identidad en la credencial → email_actual None (el watcher dedupea por
    # huella, NO se le mete un email inestable al igual que opencode/antigravity)
    assert ca.email_actual("cursor") is None


def test_cursor_identidad_para_mostrar_desde_authinfo(monkeypatch):
    home, _ = _entorno(monkeypatch)
    _cursor_login(home, {"accessToken": "csk-AAA"}, email="dev@cursor.dev")
    assert ca.email_actual("cursor") is None        # dedup sigue por huella
    p = ca.capturar_actual("cursor", "Cursor")
    # la UI muestra el email que el CLI guarda en su cli-config.json (best-effort)
    assert p["email"] == "dev@cursor.dev"


def test_cursor_identidad_sin_authinfo_es_none(monkeypatch):
    home, _ = _entorno(monkeypatch)
    _cursor_login(home, {"accessToken": "csk-AAA"})
    p = ca.capturar_actual("cursor", "Cursor")
    assert p["email"] is None


def test_cursor_switch_round_trip(monkeypatch):
    home, _ = _entorno(monkeypatch)
    auth = _cursor_login(home, {"accessToken": "AAA", "refreshToken": "R-AAA"})
    pa = ca.capturar_actual("cursor", "A")

    _escribir(auth, {"accessToken": "BBB", "refreshToken": "R-BBB"})
    pb = ca.capturar_actual("cursor", "B")

    ca.usar(pa["id"])                                # switch → restaura A
    assert json.load(open(auth, encoding="utf-8"))["accessToken"] == "AAA"
    ca.usar(pb["id"])
    assert json.load(open(auth, encoding="utf-8"))["accessToken"] == "BBB"
    # el cli-config.json (config grande, no credencial) NO se toca en el switch
    assert ca._cursor_cli_config() == os.path.join(home, ".cursor", "cli-config.json")


def test_cursor_xdg_config_home_override(monkeypatch):
    home, snaps = _entorno(monkeypatch)
    xdg = os.path.join(home, "xdg-config")
    monkeypatch.setenv("XDG_CONFIG_HOME", xdg)
    auth = os.path.join(xdg, "cursor", "auth.json")
    os.makedirs(os.path.dirname(auth), exist_ok=True)
    _escribir(auth, {"accessToken": "csk-APIKEY"})
    assert ca.esta_logueado("cursor") is True
    p = ca.capturar_actual("cursor", "XDG")
    assert os.path.isfile(os.path.join(snaps, str(p["id"]), "auth.json"))


def test_estado_adopta_sesion_nativa_de_cursor(monkeypatch):
    home, _ = _entorno(monkeypatch)
    _cursor_login(home, {"accessToken": "csk-NAV"}, email="luca@example.com")
    est = ca.estado()
    cur = next(c for c in est["clis"] if c["tipo"] == "cursor")
    assert cur["logueado"] is True
    assert cur["home_sin_guardar"] is False
    assert len(cur["cuentas"]) == 1
    assert cur["cuentas"][0]["email"] == "luca@example.com"
    est2 = ca.estado()
    assert len(next(c for c in est2["clis"] if c["tipo"] == "cursor")["cuentas"]) == 1


if __name__ == "__main__":
    import traceback

    class _MP:
        """Mini-monkeypatch para correr como script suelto (sin pytest)."""
        def __init__(self): self._undo = []
        def setattr(self, obj, name, val):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)
        def delenv(self, name, raising=True):
            if name in os.environ:
                v = os.environ.pop(name)
                self._undo.append(("env", name, v))
        def undo(self):
            for obj, name, val in reversed(self._undo):
                if obj == "env": os.environ[name] = val
                else: setattr(obj, name, val)

    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            mp = _MP()
            try:
                fn(mp)
                print("ok", nombre)
            except Exception:
                fallos += 1
                print("FAIL", nombre)
                traceback.print_exc()
            finally:
                mp.undo()
    sys.exit(1 if fallos else 0)


# ─── Orden dinámico de la sección Cuentas (2026-07-10) ───────────────────────
# Canónico: claude, codex, grok, antigravity, opencode, qwen. Los CLIs con
# cuentas GUARDADAS suben arriba manteniendo el orden canónico dentro de cada
# grupo (partición estable).

def _perfil(i, tipo):
    return {"id": i, "tipo": tipo, "label": tipo, "email": None,
            "activa": True, "created_at": "", "updated_at": ""}


def test_estado_sin_cuentas_respeta_orden_canonico(monkeypatch):
    _entorno(monkeypatch)
    monkeypatch.setattr(ca, "listar", lambda: [])
    monkeypatch.setattr(ca, "esta_logueado", lambda t: False)
    assert [c["tipo"] for c in ca.estado()["clis"]] == \
        ["claude", "codex", "grok", "antigravity", "opencode", "qwen", "pi", "cursor"]


def test_estado_con_cuentas_suben_respetando_canonico(monkeypatch):
    _entorno(monkeypatch)
    monkeypatch.setattr(ca, "esta_logueado", lambda t: False)
    # claude, codex y qwen tienen cuenta; grok NO → qwen sube arriba de grok
    monkeypatch.setattr(ca, "listar", lambda: [
        _perfil(1, "claude"), _perfil(2, "codex"), _perfil(3, "qwen")])
    assert [c["tipo"] for c in ca.estado()["clis"]] == \
        ["claude", "codex", "qwen", "grok", "antigravity", "opencode", "pi", "cursor"]
    # al agregar una cuenta de grok, grok vuelve ARRIBA de qwen (canónico)
    monkeypatch.setattr(ca, "listar", lambda: [
        _perfil(1, "claude"), _perfil(2, "codex"), _perfil(3, "qwen"), _perfil(4, "grok")])
    assert [c["tipo"] for c in ca.estado()["clis"]] == \
        ["claude", "codex", "grok", "qwen", "antigravity", "opencode", "pi", "cursor"]


# ── Enlaces de cuenta: symlink en Unix, junction en Windows ──────────────
# El enlace estable `_codex_active` es lo que permite cambiar de cuenta sin
# reabrir terminales. En Windows un symlink de directorio pide privilegios de
# administrador; una junction no. Y borrar mal el enlace se lleva puestas las
# CREDENCIALES del usuario, así que esa parte tiene test propio.

def test_borrar_enlace_no_se_lleva_el_contenido(tmp_path):
    """`shutil.rmtree` sobre una junction SEGUIRÍA el enlace y borraría el home
    real de la cuenta. La diferencia entre cambiar de cuenta y perderla."""
    real = tmp_path / 'cuenta_real'
    real.mkdir()
    (real / 'auth.json').write_text('{"token": "de mentira"}')
    enlace = tmp_path / '_activo'
    ca._crear_enlace_dir(str(enlace), str(real))

    ca._borrar_enlace(str(enlace))

    assert not enlace.exists(), 'el enlace tenía que irse'
    assert (real / 'auth.json').exists(), 'SE LLEVÓ LAS CREDENCIALES'


def test_borrar_un_enlace_que_no_existe_no_explota(tmp_path):
    # Se llama antes de crear: el camino normal pasa por acá con nada del otro
    # lado, y una excepción dejaría al usuario sin poder cambiar de cuenta.
    ca._borrar_enlace(str(tmp_path / 'no-existe'))


def test_el_enlace_resuelve_al_home_real(tmp_path):
    real = tmp_path / 'cuenta'
    real.mkdir()
    (real / 'config.toml').write_text('modelo = "x"')
    enlace = tmp_path / 'activo'
    ca._crear_enlace_dir(str(enlace), str(real))
    # Para el CLI son indistinguibles: lee el archivo a través del enlace.
    assert (enlace / 'config.toml').read_text() == 'modelo = "x"'


def test_en_windows_se_usa_junction_y_no_symlink(monkeypatch, tmp_path):
    """Un symlink de directorio en Windows exige admin o Modo Desarrollador.
    Pedirle eso a alguien que solo quiere cambiar de cuenta no va."""
    llamadas = []
    monkeypatch.setattr(ca.os, 'name', 'nt')
    monkeypatch.setattr(ca.subprocess, 'run',
                        lambda argv, **kw: llamadas.append(argv) or None)
    monkeypatch.setattr(ca.os, 'symlink',
                        lambda *a: (_ for _ in ()).throw(
                            AssertionError('no debe usar symlink en Windows')))
    ca._crear_enlace_dir(str(tmp_path / 'x'), str(tmp_path / 'y'))
    assert llamadas and 'mklink' in llamadas[0] and '/J' in llamadas[0], llamadas


# ── CLAUDE_CONFIG_DIR + diagnóstico ──────────────────────────────────────────
def test_claude_config_dir_se_respeta(tmp_path, monkeypatch):
    from plotspace.core import cli_accounts as ca
    import json as _j
    cfg = tmp_path / "otro"
    cfg.mkdir()
    (cfg / ".credentials.json").write_text(_j.dumps({"claudeAiOauth": {"accessToken": "x"}}))
    (cfg / ".claude.json").write_text(_j.dumps({"oauthAccount": {"emailAddress": "a@b.c"}}))
    monkeypatch.setattr(ca, "HOME_DIR", str(tmp_path / "home_vacio"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    assert ca.esta_logueado("claude") is True
    assert ca.email_actual("claude") == "a@b.c"
    d = ca.diagnostico("claude")
    assert d["logueado"] is True and d["claude_config_dir"] == str(cfg)
    assert d["archivos"][0]["existe"] and "claudeAiOauth" in d["archivos"][0]["claves"]
    assert "accessToken" not in str(d)           # nunca valores de tokens


def test_diagnostico_sin_credencial_da_pistas(tmp_path, monkeypatch):
    from plotspace.core import cli_accounts as ca
    monkeypatch.setattr(ca, "HOME_DIR", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    d = ca.diagnostico("claude")
    assert d["logueado"] is False and d["pistas"]


def test_diagnostico_login_por_api_key_explica(tmp_path, monkeypatch):
    from plotspace.core import cli_accounts as ca
    import json as _j
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / ".credentials.json").write_text(_j.dumps({"otraCosa": 1}))
    monkeypatch.setattr(ca, "HOME_DIR", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    d = ca.diagnostico("claude")
    assert d["logueado"] is False
    assert any("API key" in p for p in d["pistas"])
