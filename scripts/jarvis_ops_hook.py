#!/usr/bin/env python3
# SWARM PROVENANCE hook (PreToolUse + PostToolUse) for Claude Code.
#
# WHY
# ---
# Jarvis inferred which file each agent touched by parsing the tmux pane looking
# for `● Update(file)`. Claude Code 2.1.x stopped printing that: it collapses
# tool-calls into a summary that doesn't even name the file. Measured: 4.8 MB of
# log from two agents → ZERO operations detected, and with that, file ownership,
# the commit guard and the conflict alerts died silently.
#
# This hook reads the data from the tool CONTRACT (documented and stable)
# instead of the screen (a presentation surface that changes every week).
#
# TWO EVENTS, TWO RESPONSIBILITIES
# --------------------------------
#   PostToolUse  → POST /api/swarm/op    : records the edit ALREADY made.
#   PreToolUse   → POST /api/swarm/check : asks BEFORE writing; if Jarvis
#                  answers no, the tool is DENIED with the reason.
#
# DOCTRINE: absolute best-effort. Without JARVIS_TERMINAL_ID (claude outside
# Jarvis), without server, with timeout or with any exception → NO-OP that lets
# it through. A hook must never stop an agent because of a problem of its own:
# the server may be restarting right when the agent writes.
#
# It installs itself (plotspace/core/hooks_cli.py, idempotent, at server boot).
import json
import os
import sys

TIMEOUT_POST_S = 1.0     # record: if it takes long, not worth waiting
TIMEOUT_CHECK_S = 1.5    # block: worth waiting a bit longer, but not much
TIMEOUT_BRIEF_S = 1.5    # briefing: runs once per task, not per edit

# Circuit breaker: on WSL, connecting to a closed port is NOT rejected — it's
# dropped, and the attempt eats the whole timeout. Without this, with Jarvis
# down EVERY edit of EVERY agent would pay ~1s of nothing. Measured: 2.165s per
# edit with the server down vs 50ms when the hook skips itself.
CAIDO_NOMBRE = ".hook-swarm-caido"
CAIDO_S = 60             # after a failure, don't retry for a minute


def _dir_datos():
    return os.environ.get("JARVIS_DATA_DIR") or os.path.join(
        os.path.expanduser("~"), "jarvis", "data")


def _corta_corriente_abierto(data_dir):
    """True if there was a failure recently → skip without touching the network."""
    try:
        import time
        return (time.time() - os.path.getmtime(
            os.path.join(data_dir, CAIDO_NOMBRE))) < CAIDO_S
    except OSError:
        return False


def _marcar_caido(data_dir, caido=True):
    p = os.path.join(data_dir, CAIDO_NOMBRE)
    try:
        if caido:
            with open(p, "w") as f:
                f.write("")
        elif os.path.exists(p):
            os.unlink(p)
    except OSError:
        pass


def _anfitriones():
    """Which addresses to try, in order.

    `127.0.0.1` first: it's the normal case (agent and engine on the same
    machine) and the only one that existed until now.

    The second case appears with the port to Windows: an agent running INSIDE
    a WSL distro and the engine on the Windows side. There `127.0.0.1` is the
    distro, not the host — and without this fallback the hook fails silently on
    every write, leaving the swarm without provenance precisely on WSL-profile
    terminals. With `networkingMode=mirrored` the first one is enough; without
    it, the host IP is the one WSL leaves as nameserver in /etc/resolv.conf.

    It can be set by hand with JARVIS_HOST, which wins over everything else.
    """
    fijo = (os.environ.get("JARVIS_HOST") or "").strip()
    if fijo:
        return [fijo]
    hosts = ["127.0.0.1"]
    try:
        # It only makes sense to look for the host if we're inside WSL.
        with open("/proc/version") as f:
            if "microsoft" not in f.read().lower():
                return hosts
        with open("/etc/resolv.conf") as f:
            for linea in f:
                if linea.startswith("nameserver"):
                    ip = linea.split()[1].strip()
                    if ip and ip not in hosts:
                        hosts.append(ip)
                    break
    except OSError:
        pass
    return hosts


def _conectar(puerto, timeout):
    """Socket to the engine, trying each host. None if none responds —
    the hook is absolute best-effort and NEVER stops the agent."""
    import socket
    for host in _anfitriones():
        try:
            return socket.create_connection((host, int(puerto)), timeout)
        except OSError:
            continue
    return None


def _http_crudo(puerto, ruta, cuerpo, timeout):
    """POST JSON over a bare socket. Returns the response dict.

    Why not `urllib.request`: importing it costs 82 ms (it drags in http.client →
    the whole `email` package), and this hook runs TWICE per write of EVERY
    agent. With a raw socket the import takes 7 ms. Measured, not estimated.

    `Connection: close` avoids having to parse Content-Length or chunked:
    it's read until the server closes."""
    import socket
    body = json.dumps(cuerpo).encode()
    pedido = (
        f"POST {ruta} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{puerto}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode() + body

    s = _conectar(puerto, timeout)
    if s is None:
        return {}
    try:
        s.settimeout(timeout)
        s.sendall(pedido)
        trozos = []
        while True:
            c = s.recv(65536)
            if not c:
                break
            trozos.append(c)
    finally:
        s.close()
    datos = b"".join(trozos)
    corte = datos.find(b"\r\n\r\n")
    if corte < 0:
        raise ValueError("incomplete HTTP response")
    estado = datos[:datos.find(b"\r\n")].split()
    if len(estado) < 2 or not estado[1].startswith(b"2"):
        raise ValueError(f"HTTP {estado[1].decode() if len(estado) > 1 else '?'}")
    return json.loads(datos[corte + 4:].decode("utf-8", errors="replace") or "{}")


def _http_get(puerto, ruta, timeout):
    """GET JSON over a bare socket (same reason as _http_crudo: urllib costs
    82 ms to import and this hook runs on the agent's hot path)."""
    pedido = (
        f"GET {ruta} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{puerto}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode()
    s = _conectar(puerto, timeout)
    if s is None:
        return {}
    try:
        s.settimeout(timeout)
        s.sendall(pedido)
        trozos = []
        while True:
            c = s.recv(65536)
            if not c:
                break
            trozos.append(c)
    finally:
        s.close()
    datos = b"".join(trozos)
    corte = datos.find(b"\r\n\r\n")
    if corte < 0:
        raise ValueError("incomplete HTTP response")
    estado = datos[:datos.find(b"\r\n")].split()
    if len(estado) < 2 or not estado[1].startswith(b"2"):
        raise ValueError(f"HTTP {estado[1].decode() if len(estado) > 1 else '?'}")
    return json.loads(datos[corte + 4:].decode("utf-8", errors="replace") or "{}")


def _get(ruta, timeout):
    """GET to Jarvis with the same circuit breaker as _post. None if it's open."""
    data_dir = _dir_datos()
    if _corta_corriente_abierto(data_dir):
        return None
    try:
        out = _http_get(os.environ.get("JARVIS_PORT", "3000"), ruta, timeout)
    except Exception:
        _marcar_caido(data_dir, True)
        raise
    _marcar_caido(data_dir, False)
    return out


def _post(ruta, cuerpo, timeout):
    """POST JSON to Jarvis. Returns the response dict, or None if the circuit
    breaker is open. Propagates the exception if the call fails (the caller
    decides), but marks the failure so it won't retry on every edit."""
    data_dir = _dir_datos()
    if _corta_corriente_abierto(data_dir):
        return None
    try:
        out = _http_crudo(os.environ.get("JARVIS_PORT", "3000"), ruta, cuerpo, timeout)
    except Exception:
        _marcar_caido(data_dir, True)
        raise
    _marcar_caido(data_dir, False)
    return out


def _contexto(texto, evento="PostToolUse"):
    """Injects text into the agent's context. It's emitted in BOTH known forms
    of the contract (top-level and inside hookSpecificOutput): fields the CLI
    doesn't know are ignored, so the notice survives a format change — which is
    exactly what left us blind last time.

    `evento` must be the one that fired the hook: with a crossed hookEventName
    the CLI discards the block and the text dies mute."""
    json.dump({
        "additionalContext": texto,
        "hookSpecificOutput": {"hookEventName": evento,
                               "additionalContext": texto},
    }, sys.stdout)
    sys.stdout.write("\n")


def datos_herramienta(payload):
    """(tool_name, tool_input, is_antigravity) from the payload, whatever the CLI.

    Claude/qwen/Gemini send {tool_name, tool_input}; Antigravity sends
    {toolCall: {name, args}} (protojson, camelCase). Without translating it, the
    hook would see NONE of Antigravity's edits."""
    if not isinstance(payload, dict):
        return None, None, False
    tc = payload.get("toolCall")
    if isinstance(tc, dict):
        args = tc.get("args")
        return tc.get("name"), args if isinstance(args, dict) else None, True
    ti = payload.get("tool_input")
    return payload.get("tool_name"), ti if isinstance(ti, dict) else None, False


def _permitir(texto=None):
    """Antigravity requires valid JSON on stdout or it BLOCKS the agent's loop.
    This hook is an OBSERVER: it never denies, it always lets it through.

    If there's something to tell it (briefing / collision) it travels as
    `additionalContext` next to the decision: a field Antigravity doesn't know
    it ignores, and it's the ONLY channel it has — its hook doesn't run on the
    prompt. Same defensive criterion as `_contexto`: over-emitting never broke
    anything; staying mute did."""
    salida = {"decision": "allow"}
    if texto:
        salida["additionalContext"] = str(texto)
        salida["hookSpecificOutput"] = {"hookEventName": "PreToolUse",
                                        "additionalContext": str(texto)}
    json.dump(salida, sys.stdout)
    sys.stdout.write("\n")


def _denegar(motivo):
    """PreToolUse block format (Claude / qwen): exit 0 + JSON with the
    decision. (The agent reads the reason, so it has to tell it WHAT to do.)
    Antigravity never denies — this hook is an observer and always answers
    `allow` (see _permitir)."""
    json.dump({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": motivo,
    }}, sys.stdout)
    sys.stdout.write("\n")


def main():
    tid = os.environ.get("JARVIS_TERMINAL_ID")
    if not tid:
        return                      # claude outside Jarvis → no-op
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    if not isinstance(payload, dict):
        return

    tool_name, tool_input, es_agy = datos_herramienta(payload)
    cuerpo = {
        "terminal_id": tid,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cwd": payload.get("cwd"),
        "session_id": payload.get("session_id"),
    }
    evento = payload.get("hook_event_name") or ""

    if es_agy:
        # Antigravity: its PostToolUse brings neither tool nor path, so the ONLY
        # moment with the path is PreToolUse → it's RECORDED there (before the
        # edit happens: that or seeing nothing). And its hook runs SYNCHRONOUSLY
        # and BLOCKS the agent's loop, so the POST is fire-and-forget (circuit
        # breaker + usual timeout) and it always answers `allow`, no matter what.
        r = None
        try:
            r = _post("/api/swarm/op", cuerpo, TIMEOUT_POST_S)
        except Exception:
            pass
        partes = ([p for p in (r.get("aviso_texto"), r.get("briefing")) if p]
                  if isinstance(r, dict) else [])
        _permitir("\n\n".join(str(p) for p in partes) if partes else None)
        return

    # BRIEFING (UserPromptSubmit): the agent starts a new task. It's handed the
    # swarm state BEFORE it thinks — who else is alive, what each one touches,
    # what territory it can't step on. It's the good channel: zero turns, zero
    # latency, and it doesn't depend on the agent remembering to ask (which is
    # precisely what it didn't do).
    if evento == "UserPromptSubmit":
        try:
            r = _get(f"/api/swarm/briefing/{tid}", TIMEOUT_BRIEF_S)
        except Exception:
            return                  # server down → the agent starts without briefing
        texto = (r or {}).get("texto") if isinstance(r, dict) else None
        if texto:
            _contexto(str(texto), evento="UserPromptSubmit")
        return

    if evento == "PreToolUse":
        try:
            r = _post("/api/swarm/check", cuerpo, TIMEOUT_CHECK_S)
        except Exception:
            return                  # server down/slow → let it pass
        if isinstance(r, dict) and r.get("permitir") is False and r.get("motivo"):
            _denegar(str(r["motivo"]))
        return

    # PostToolUse (and any other event hooked here): record.
    try:
        r = _post("/api/swarm/op", cuerpo, TIMEOUT_POST_S)
    except Exception:
        return                      # this edit's provenance is lost; the
                                    # agent doesn't find out and keeps working

    if not isinstance(r, dict):
        return

    # What's injected into the agent, attached to its tool result:
    #   1. SURFACE COLLISION: it deleted something another one uses. Before, this
    #      was discovered by chance (or by breaking) and cost a saga of messages.
    #   2. Piggyback BRIEFING: for CLIs WITHOUT a prompt hook (Antigravity,
    #      opencode) this is the only channel that exists. It arrives a bit late
    #      —the agent already started— but before it can break anything, because
    #      any agent's first move is to touch a file. It only travels when the
    #      state CHANGED (the server dedupes by signature), so it doesn't repeat
    #      the same thing on every edit.
    partes = [p for p in (r.get("aviso_texto"), r.get("briefing")) if p]
    if partes:
        _contexto("\n\n".join(str(p) for p in partes))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass                        # never propagate: exit 0 = "no decision"
