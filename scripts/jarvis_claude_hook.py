#!/usr/bin/env python3
# Claude Code SessionStart hook for Jarvis.
#
# claude rotates its transcript (new <uuid>.jsonl) every time the conversation
# is compacted or continued. If Jarvis stored the INITIAL uuid, resuming with
# --resume brings old/partial context. This hook, which claude fires on EVERY
# start (startup/resume/clear), tells Jarvis the LIVE session-id so that
# terminals.session_uuid always points to the current transcript — that way,
# reopening the app brings the terminal back with the WHOLE conversation.
#
# Claude passes a JSON over stdin: {session_id, transcript_path, cwd, source, ...}.
# The hook only acts if it runs inside a Jarvis terminal (JARVIS_TERMINAL_ID
# in the env, which Jarvis sets in the tmux session). ABSOLUTE best-effort: any
# error is swallowed silently — a hook must never break claude's startup.
import json
import os
import sys


def main() -> None:
    tid = os.environ.get("JARVIS_TERMINAL_ID")
    if not tid:
        return  # normal claude outside Jarvis → no-op
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    session_id = (payload or {}).get("session_id")
    if not session_id:
        return

    puerto = os.environ.get("JARVIS_PORT", "3000")

    try:
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{puerto}/api/terminals/{tid}/session-uuid",
            data=json.dumps({"session_uuid": session_id}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass  # the engine may be restarting; the next start will retry


if __name__ == "__main__":
    main()
