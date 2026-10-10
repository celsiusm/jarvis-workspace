# Security

Jarvis Workspace **runs arbitrary commands** on your machine. Anyone who can
talk to the engine can run anything as your user.

- Default bind is **127.0.0.1**. `0.0.0.0` is explicit and warned.
- When it listens on the network (Docker, `--host 0.0.0.0`), requests from
  another machine need the **access token** (printed at startup, stored 0600 in
  `data/acceso-token`). `JARVIS_TOKEN=off` disables it — don't, on a shared network.
- WebSockets and state-changing requests must come from the **same origin** as
  the server (anti cross-site WebSocket hijacking / CSRF); an IP-literal origin is
  not enough.
- The Docker image runs as a non-root user (`PUID`/`PGID`).

CLI credentials live in `data/cli-accounts/` (mode 0600), never in git. The
pre-commit/pre-push scanner (`scripts/scan_secretos.py`) blocks keys, `.env`
files, and known secret patterns. Don't bypass it with `--no-verify`.

## Report a vulnerability

Open a [private advisory](https://github.com/celsiusm/jarvis-workspace/security/advisories/new),
not a public issue. We aim to reply within 7 days.
