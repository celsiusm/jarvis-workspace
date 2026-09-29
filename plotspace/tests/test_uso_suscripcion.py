"""Tests del uso de suscripción por cuenta (plotspace/core/uso_suscripcion.py).

Sin red: la llamada HTTP (`_get_json`) se monkeypatchea. HOME y snapshots en
tempdirs (mismo patrón que test_cli_accounts)."""
import asyncio
import json
import os
import sys
import tempfile
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from plotspace.tests._harness import fresh_db
import plotspace.core.cli_accounts as ca
import plotspace.core.uso_suscripcion as us


def _escribir(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(obj if isinstance(obj, str) else json.dumps(obj))


@pytest.fixture
def entorno(monkeypatch):
    fresh_db()
    home = tempfile.mkdtemp(prefix="jarvis_home_")
    snaps = tempfile.mkdtemp(prefix="jarvis_snaps_")
    monkeypatch.setattr(ca, "HOME_DIR", home)
    monkeypatch.setattr(ca, "SNAPSHOTS_DIR", snaps)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    us.limpiar_cache()
    llamadas = []

    async def falso_get(url, headers):
        llamadas.append((url, headers))
        return falso_get.respuesta
    falso_get.respuesta = (200, {})
    monkeypatch.setattr(us, "_get_json", falso_get)
    yield home, snaps, falso_get, llamadas
    us.limpiar_cache()


def _correr(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── normalización ──────────────────────────────────────────────────────────

def test_normalizar_claude_ventanas_y_modelos():
    data = {
        "five_hour": {"utilization": 23.4, "resets_at": "2026-09-29T18:00:00Z"},
        "seven_day": {"utilization": 61, "resets_at": "2026-10-02T05:00:00Z"},
        "seven_day_opus": {"utilization": 140, "resets_at": None},
        "seven_day_sonnet": None,
        "extra_usage": {"is_enabled": False},
    }
    v = us.normalizar_claude(data)
    assert [x["clave"] for x in v] == ["sesion", "semana", "semana"]
    assert v[0]["usado"] == 23.4 and v[0]["reinicia"] == "2026-09-29T18:00:00Z"
    assert v[2]["modelo"] == "Opus" and v[2]["usado"] == 100.0   # se acota a 100


def test_normalizar_claude_basura():
    assert us.normalizar_claude(None) == []
    assert us.normalizar_claude({"five_hour": {"utilization": "x"}}) == []


def test_normalizar_codex_wham_clasifica_por_duracion():
    data = {"plan_type": "plus", "rate_limit": {
        "primary_window": {"used_percent": 12, "limit_window_seconds": 18000,
                           "reset_after_seconds": 100, "reset_at": 1790000000},
        "secondary_window": {"used_percent": 55, "limit_window_seconds": 604800,
                             "reset_after_seconds": 100, "reset_at": 1790500000},
    }}
    v = us.normalizar_codex_wham(data)
    assert [(x["clave"], x["usado"]) for x in v] == [("sesion", 12.0), ("semana", 55.0)]
    assert v[0]["reinicia"].startswith("2026-")


def test_normalizar_codex_rollout_reset_relativo():
    rl = {"primary": {"used_percent": 40.0, "window_minutes": 300, "resets_in_seconds": 600},
          "secondary": {"used_percent": 5.0, "window_minutes": 10080, "resets_at": 1790000000}}
    v = us.normalizar_codex_rollout(rl, visto_ts=1790000000)
    assert v[0]["clave"] == "sesion" and v[1]["clave"] == "semana"
    assert v[0]["reinicia"] is not None


# ── consulta por cuenta ────────────────────────────────────────────────────

def _cuenta_claude(home, token="tok-1", expira_ms=None):
    _escribir(os.path.join(home, ".claude", ".credentials.json"), {"claudeAiOauth": {
        "accessToken": token, "refreshToken": "r", "subscriptionType": "max",
        "expiresAt": expira_ms if expira_ms is not None else int((time.time() + 3600) * 1000)}})
    _escribir(os.path.join(home, ".claude.json"), {"oauthAccount": {"emailAddress": "a@b.c"}})
    return ca.capturar_actual("claude", "Personal")


def test_claude_ok_usa_token_del_home_y_header_beta(entorno):
    home, _, falso, llamadas = entorno
    perfil = _cuenta_claude(home)
    falso.respuesta = (200, {"five_hour": {"utilization": 30, "resets_at": "2026-09-29T20:00:00Z"}})
    r = _correr(us.uso_de(perfil))
    assert r["estado"] == "ok" and r["plan"] == "max"
    assert r["ventanas"][0]["usado"] == 30.0
    url, headers = llamadas[0]
    assert url == us.URL_CLAUDE
    assert headers["Authorization"] == "Bearer tok-1"
    assert headers["anthropic-beta"] == "oauth-2025-04-20"
    assert "tok-1" not in json.dumps(r)          # el token no sale en la respuesta


def test_claude_token_vencido_no_llama_ni_refresca(entorno):
    home, _, _, llamadas = entorno
    perfil = _cuenta_claude(home, expira_ms=int((time.time() - 60) * 1000))
    r = _correr(us.uso_de(perfil))
    assert r["estado"] == "token_vencido"
    assert llamadas == []


def test_claude_401_es_token_vencido(entorno):
    home, _, falso, _ = entorno
    perfil = _cuenta_claude(home)
    falso.respuesta = (401, None)
    assert _correr(us.uso_de(perfil))["estado"] == "token_vencido"


def test_cache_y_forzar(entorno):
    home, _, falso, llamadas = entorno
    perfil = _cuenta_claude(home)
    falso.respuesta = (200, {"five_hour": {"utilization": 1}})
    _correr(us.uso_de(perfil))
    _correr(us.uso_de(perfil))
    assert len(llamadas) == 1
    _correr(us.uso_de(perfil, forzar=True))
    assert len(llamadas) == 2


def _cuenta_codex(home, snaps):
    _escribir(os.path.join(home, ".codex", "auth.json"),
              {"tokens": {"access_token": "cx-tok", "account_id": "acc-9", "id_token": "x.e30.y"}})
    return ca.capturar_actual("codex", "Trabajo")


def test_codex_api_ok_con_account_id(entorno):
    home, snaps, falso, llamadas = entorno
    perfil = _cuenta_codex(home, snaps)
    falso.respuesta = (200, {"plan_type": "pro", "rate_limit": {
        "primary_window": {"used_percent": 70, "limit_window_seconds": 18000, "reset_at": 1790000000}}})
    r = _correr(us.uso_de(perfil))
    assert r["estado"] == "ok" and r["plan"] == "pro" and r["fuente"] == "api"
    assert llamadas[0][1]["ChatGPT-Account-Id"] == "acc-9"


def test_codex_cae_al_snapshot_de_sesion(entorno):
    home, snaps, falso, _ = entorno
    perfil = _cuenta_codex(home, snaps)
    falso.respuesta = (500, None)
    ev = {"timestamp": "2026-09-29T10:00:00Z", "type": "event_msg",
          "payload": {"type": "token_count", "rate_limits": {
              "primary": {"used_percent": 33.0, "window_minutes": 300, "resets_at": 1790000000}}}}
    _escribir(os.path.join(ca._account_dir(perfil["id"]), "sessions", "2026", "09", "29",
                           "rollout-x.jsonl"), '{"type":"session_meta"}\n' + json.dumps(ev) + "\n")
    r = _correr(us.uso_de(perfil))
    assert r["estado"] == "ok" and r["fuente"] == "sesion"
    assert r["ventanas"][0]["usado"] == 33.0
    assert r["visto"].startswith("2026-09-29T10:00")


def test_cli_sin_cupo_expuesto(entorno):
    r = _correr(us.uso_de({"id": 99, "tipo": "qwen", "activa": True}))
    assert r["estado"] == "no_soportado"


def test_uso_todas_solo_soportados(entorno):
    home, snaps, falso, _ = entorno
    p = _cuenta_claude(home)
    falso.respuesta = (200, {"seven_day": {"utilization": 10}})
    todas = _correr(us.uso_todas())
    assert list(todas) == [str(p["id"])]
    assert todas[str(p["id"])]["ventanas"][0]["clave"] == "semana"


def test_endpoint_http(entorno):
    from fastapi.testclient import TestClient
    from plotspace.main import app
    home, _, falso, _ = entorno
    p = _cuenta_claude(home)
    falso.respuesta = (200, {"five_hour": {"utilization": 50}})
    with TestClient(app) as c:
        r = c.get("/api/cuentas/uso?refrescar=1")
    assert r.status_code == 200
    assert r.json()["cuentas"][str(p["id"])]["estado"] == "ok"
