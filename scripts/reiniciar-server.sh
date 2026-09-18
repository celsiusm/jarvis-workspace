#!/usr/bin/env bash
# JARVIS — Server restart WITHOUT stealing the process from the user's terminal.
#
# ⚠️ USER TOOL (or Jarvis's own). AGENTS DO NOT RESTART THE
# SERVER: they verify with pytest (runs the code from disk) + the smoke
# `python -c "import plotspace.main"`, and the user applies the update
# from the "Update now" banner in the UI.
#
# Correct path (live server): POST /api/system/restart → startup canary
# (if the new code does not import, it rejects with 409 and does NOT restart) → VERSION
# bump (1.5.0→1.5.1; hotfix 1.5.1→1.5.1.1) → os.execv re-exec in place
# (plotspace/routers/system.py): same PID, same session, SAME TERMINAL.
# The server stays hosted where the user started it — exactly what the
# "Update" button in the UI does.
#
# `pkill -f uvicorn` + relaunch is FORBIDDEN: that re-hosts the server in YOUR shell
# and the user loses control of their terminal.
#
# Fallback (dead server): it starts it nohup'd in this shell — the only option if
# there is no live process — and warns that it is now hosted here.
set -u
cd "$(dirname "$0")/.." || exit 1

# 127.0.0.1 and NOT localhost: on this box the name resolves IPv6-first (::1) and
# uvicorn listens only on IPv4 — the house rule (see wsl-* memories).
BASE='http://127.0.0.1:3000'

vivo() { curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$BASE/api/health" 2>/dev/null; }

esperar_arriba() {  # polls /api/health up to 120s; exit code 0 if it came back
  for _ in $(seq 1 120); do
    [ "$(vivo)" = '200' ] && return 0
    sleep 1
  done
  return 1
}

if [ "$(vivo)" = '200' ]; then
  echo "[reiniciar-server] server alive → in-place restart via POST /api/system/restart"
  # --max-time 150: the endpoint runs a startup canary (imports backend.main
  # in a subprocess) before responding — it takes several seconds, it is not a hang.
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 150 -X POST \
         "$BASE/api/system/restart")
  if [ "$code" = '409' ]; then
    echo "[reiniciar-server] REJECTED: the new code does not start (canary). The old server is still alive." >&2
    exit 1
  fi
  if [ "$code" != '200' ]; then
    echo "[reiniciar-server] ERROR: /api/system/restart returned '$code'" >&2
    exit 1
  fi
  sleep 3   # the re-exec fires at ~1s; let it die before polling
  if esperar_arriba; then
    echo "[reiniciar-server] done: server back, same process/terminal as the user"
    exit 0
  fi
  echo "[reiniciar-server] ERROR: the server did not come back after ~120s — check the user's terminal" >&2
  exit 1
fi

echo "[reiniciar-server] no live server → starting it in this shell (fallback)"
echo "[reiniciar-server] WARNING: the server stays hosted HERE, not in the user's terminal."
source venv/bin/activate
# `setsid` and not just `nohup`: when the caller is Jarvis.exe (or the .bat),
# on the other side there is a `wsl.exe` that exits as soon as it fires this. With
# uvicorn as a child in the SAME session, that exit can take it down; with setsid
# it stays in its own session, with no controlling terminal, and survives the whole
# tree that launched it. It is the only thing needed for the double click to work.
# The log does NOT go to /tmp: in WSL it is tmpfs and is wiped on EVERY boot of
# the distro, exactly the case you need to debug (a motor that crashed almost always
# comes with a boot in between — on 2026-08-08 the failed-boot log was already
# gone). It goes to the repo's data/ (gitignored), with a simple truncation so it
# does not grow without a ceiling. Same lesson as data/lanzador.log.
LOG='data/uvicorn.log'
[ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt 5242880 ] && mv -f "$LOG" "$LOG.1"
setsid nohup python3 -m uvicorn plotspace.main:app --host 0.0.0.0 --port 3000 \
  --loop asyncio >>"$LOG" 2>&1 </dev/null &
if esperar_arriba; then
  echo "[reiniciar-server] done: server up (logs in $LOG)"
  exit 0
fi
echo "[reiniciar-server] ERROR: it did not start — see $LOG" >&2
exit 1
