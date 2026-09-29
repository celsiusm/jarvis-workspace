"""Uso de la SUSCRIPCIÓN de cada cuenta de CLI (cuánto te queda).

Qué se puede medir y de dónde sale:

- **Claude Code** (Pro/Max): `GET https://api.anthropic.com/api/oauth/usage`
  con el access token OAuth de la cuenta. Devuelve `five_hour` y `seven_day`
  (+ `seven_day_opus`/`seven_day_sonnet` según el plan), cada uno con
  `utilization` (0-100) y `resets_at` (ISO). Es lo mismo que muestra `/usage`.
- **Codex** (ChatGPT Plus/Pro): `GET https://chatgpt.com/backend-api/wham/usage`
  con el access token de `auth.json` + `ChatGPT-Account-Id`. Devuelve
  `rate_limit.primary_window`/`secondary_window` con `used_percent`,
  `limit_window_seconds` y `reset_at` (epoch). Si falla, se cae al ÚLTIMO
  snapshot que el propio CLI escribió en sus sesiones
  (`<CODEX_HOME>/sessions/**/rollout-*.jsonl`, evento `token_count`).
- El resto de los CLIs no exponen su cupo: `no_soportado`.

Reglas:
- NUNCA se refresca un token acá. Refrescar rota el refresh token y dejaría
  inválida la copia que tiene el CLI (o el snapshot de la cuenta): con token
  vencido se informa `token_vencido` y el CLI lo renueva solo al usarse.
- El token no sale nunca de este módulo (ni al log, ni a la respuesta).
- Caché de 60 s por cuenta: la sección se abre y se cierra seguido y los
  endpoints de uso tienen su propio rate-limit.
"""
import glob
import json
import os
import time
from datetime import datetime, timezone
from typing import Optional

from plotspace.core import cli_accounts as ca

URL_CLAUDE = "https://api.anthropic.com/api/oauth/usage"
URL_CODEX = "https://chatgpt.com/backend-api/wham/usage"
TIPOS_SOPORTADOS = ("claude", "codex")
TTL_CACHE = 60.0
_TIMEOUT = 8.0

_cache: dict = {}   # account_id → (monotonic, resultado)


# ── normalización (PURA) ────────────────────────────────────────────────────

def _pct(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(100.0, f))


def _iso_desde_epoch(v) -> Optional[str]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f <= 0:
        return None
    if f > 1e12:          # milisegundos
        f /= 1000.0
    return datetime.fromtimestamp(f, tz=timezone.utc).isoformat()


def _clave_ventana(minutos: Optional[float]) -> str:
    """'sesion' para ventanas cortas (≤ 1 día), 'semana' para las largas."""
    if minutos is None:
        return "sesion"
    return "sesion" if minutos <= 24 * 60 else "semana"


def normalizar_claude(data: dict) -> list:
    """Respuesta de /api/oauth/usage → lista de ventanas."""
    if not isinstance(data, dict):
        return []
    ventanas = []
    for campo, clave, modelo in (
        ("five_hour", "sesion", None),
        ("seven_day", "semana", None),
        ("seven_day_opus", "semana", "Opus"),
        ("seven_day_sonnet", "semana", "Sonnet"),
    ):
        w = data.get(campo)
        if not isinstance(w, dict):
            continue
        usado = _pct(w.get("utilization"))
        if usado is None:
            continue
        ventanas.append({
            "clave": clave, "modelo": modelo, "usado": round(usado, 1),
            "reinicia": w.get("resets_at") or None,
        })
    return ventanas


def _ventana_codex(w, minutos_campo, reset_campos) -> Optional[dict]:
    if not isinstance(w, dict):
        return None
    usado = _pct(w.get("used_percent"))
    if usado is None:
        return None
    minutos = None
    if minutos_campo == "limit_window_seconds" and w.get(minutos_campo) is not None:
        try:
            minutos = float(w[minutos_campo]) / 60.0
        except (TypeError, ValueError):
            minutos = None
    elif w.get(minutos_campo) is not None:
        try:
            minutos = float(w[minutos_campo])
        except (TypeError, ValueError):
            minutos = None
    reinicia = None
    for campo in reset_campos:
        if w.get(campo) is not None:
            reinicia = _iso_desde_epoch(w[campo])
            if reinicia:
                break
    return {"clave": _clave_ventana(minutos), "modelo": None,
            "usado": round(usado, 1), "reinicia": reinicia}


def normalizar_codex_wham(data: dict) -> list:
    """Respuesta de /backend-api/wham/usage → lista de ventanas."""
    rl = (data or {}).get("rate_limit") if isinstance(data, dict) else None
    if not isinstance(rl, dict):
        return []
    out = []
    for campo in ("primary_window", "secondary_window"):
        v = _ventana_codex(rl.get(campo), "limit_window_seconds", ("reset_at",))
        if v:
            out.append(v)
    return out


def normalizar_codex_rollout(rate_limits: dict, visto_ts: Optional[float] = None) -> list:
    """`rate_limits` del evento token_count de una sesión de codex → ventanas.

    Formato: {primary: {used_percent, window_minutes, resets_at | resets_in_seconds}, secondary: …}.
    `resets_in_seconds` (versiones viejas) es relativo al momento del evento."""
    if not isinstance(rate_limits, dict):
        return []
    out = []
    for campo in ("primary", "secondary"):
        w = rate_limits.get(campo)
        if not isinstance(w, dict):
            continue
        w = dict(w)
        if w.get("resets_at") is None and w.get("resets_in_seconds") is not None and visto_ts:
            try:
                w["resets_at"] = float(visto_ts) + float(w["resets_in_seconds"])
            except (TypeError, ValueError):
                pass
        v = _ventana_codex(w, "window_minutes", ("resets_at",))
        if v:
            out.append(v)
    return out


def _vencido(epoch_ms, ahora=None) -> bool:
    try:
        f = float(epoch_ms)
    except (TypeError, ValueError):
        return False
    if f > 1e12:
        f /= 1000.0
    return f <= (ahora if ahora is not None else time.time())


# ── credenciales (solo lectura) ─────────────────────────────────────────────

def _cred_claude(perfil: dict) -> Optional[dict]:
    """claudeAiOauth de la cuenta: la del HOME si es la activa (la más fresca,
    el CLI la mantiene), la del snapshot si no."""
    if perfil.get("activa"):
        cred = ca._read_json(ca._specs()["claude"]["cred_file"]) or {}
        oauth = cred.get("claudeAiOauth")
        if isinstance(oauth, dict) and oauth.get("accessToken"):
            return oauth
    snap = ca._read_json(os.path.join(ca._account_dir(perfil["id"]), "claude-account.json")) or {}
    oauth = snap.get("claudeAiOauth")
    return oauth if isinstance(oauth, dict) and oauth.get("accessToken") else None


def _home_codex(perfil: dict) -> str:
    # El dir de la cuenta ES su CODEX_HOME vivo (cli_accounts.codex_home).
    return ca._account_dir(perfil["id"])


def _cred_codex(perfil: dict) -> Optional[dict]:
    auth = ca._read_json(os.path.join(_home_codex(perfil), "auth.json")) or {}
    tokens = auth.get("tokens")
    if isinstance(tokens, dict) and tokens.get("access_token"):
        return tokens
    return None


def ultimo_rollout_codex(home: str, max_archivos: int = 40) -> Optional[dict]:
    """Último `rate_limits` que el CLI escribió en sus sesiones → {ventanas, visto}."""
    patrones = [os.path.join(home, "sessions", "**", "*.jsonl"),
                os.path.join(home, "archived_sessions", "*.jsonl")]
    archivos = []
    for p in patrones:
        archivos.extend(glob.glob(p, recursive=True))
    archivos.sort(key=lambda f: os.path.getmtime(f) if os.path.exists(f) else 0, reverse=True)
    for ruta in archivos[:max_archivos]:
        try:
            with open(ruta, encoding="utf-8", errors="replace") as f:
                lineas = f.readlines()
        except OSError:
            continue
        for linea in reversed(lineas):
            if '"rate_limits"' not in linea:
                continue
            try:
                ev = json.loads(linea)
            except ValueError:
                continue
            payload = ev.get("payload") if isinstance(ev, dict) else None
            rl = payload.get("rate_limits") if isinstance(payload, dict) else None
            if not isinstance(rl, dict):
                continue
            visto = None
            ts = ev.get("timestamp")
            if isinstance(ts, str):
                try:
                    visto = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    visto = None
            if visto is None:
                visto = os.path.getmtime(ruta)
            ventanas = normalizar_codex_rollout(rl, visto)
            if ventanas:
                return {"ventanas": ventanas,
                        "visto": datetime.fromtimestamp(visto, tz=timezone.utc).isoformat()}
    return None


# ── consulta ────────────────────────────────────────────────────────────────

def _resultado(estado, ventanas=None, plan=None, fuente=None, visto=None):
    return {"estado": estado, "ventanas": ventanas or [], "plan": plan,
            "fuente": fuente, "visto": visto or datetime.now(timezone.utc).isoformat()}


async def _get_json(url: str, headers: dict):
    import httpx
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(url, headers=headers)
    return r.status_code, (r.json() if r.status_code == 200 else None)


async def _uso_claude(perfil: dict) -> dict:
    oauth = _cred_claude(perfil)
    if not oauth:
        return _resultado("sin_credencial")
    plan = oauth.get("subscriptionType") or None
    if _vencido(oauth.get("expiresAt")):
        return _resultado("token_vencido", plan=plan)
    try:
        status, data = await _get_json(URL_CLAUDE, {
            "Authorization": f"Bearer {oauth['accessToken']}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "jarvis-workspace",
        })
    except Exception:
        return _resultado("error", plan=plan)
    if status == 401:
        return _resultado("token_vencido", plan=plan)
    if status != 200:
        return _resultado("error", plan=plan)
    ventanas = normalizar_claude(data)
    return _resultado("ok" if ventanas else "sin_datos", ventanas, plan, "api")


async def _uso_codex(perfil: dict) -> dict:
    tokens = _cred_codex(perfil)
    home = _home_codex(perfil)
    plan = None
    if tokens:
        headers = {"Authorization": f"Bearer {tokens['access_token']}",
                   "User-Agent": "codex-cli"}
        if tokens.get("account_id"):
            headers["ChatGPT-Account-Id"] = str(tokens["account_id"])
        try:
            status, data = await _get_json(URL_CODEX, headers)
        except Exception:
            status, data = None, None
        if status == 200 and isinstance(data, dict):
            plan = data.get("plan_type") or None
            ventanas = normalizar_codex_wham(data)
            if ventanas:
                return _resultado("ok", ventanas, plan, "api")
    # Plan B: lo último que vio el propio CLI en su sesión.
    snap = ultimo_rollout_codex(home)
    if snap:
        return _resultado("ok", snap["ventanas"], plan, "sesion", snap["visto"])
    return _resultado("sin_credencial" if not tokens else "sin_datos", plan=plan)


async def uso_de(perfil: dict, forzar: bool = False) -> dict:
    tipo = perfil.get("tipo")
    if tipo not in TIPOS_SOPORTADOS:
        return _resultado("no_soportado")
    ahora = time.monotonic()
    hit = _cache.get(perfil["id"])
    if hit and not forzar and ahora - hit[0] < TTL_CACHE:
        return hit[1]
    res = await (_uso_claude(perfil) if tipo == "claude" else _uso_codex(perfil))
    _cache[perfil["id"]] = (ahora, res)
    return res


async def uso_todas(forzar: bool = False) -> dict:
    """{account_id: resultado} de todas las cuentas guardadas de CLIs soportados."""
    import asyncio
    perfiles = [p for p in ca.listar() if p["tipo"] in TIPOS_SOPORTADOS]
    resultados = await asyncio.gather(*(uso_de(p, forzar) for p in perfiles),
                                      return_exceptions=True)
    salida = {}
    for p, r in zip(perfiles, resultados):
        salida[str(p["id"])] = r if isinstance(r, dict) else _resultado("error")
    return salida


def limpiar_cache():
    _cache.clear()
