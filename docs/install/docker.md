# Install with Docker (servers, NAS, Unraid)

For a machine that is always on — a home server, a NAS, Unraid, a VPS. On your
own PC the [one-command installer](linux.md) is simpler.

The image is published at `ghcr.io/celsiusm/jarvis-workspace` (x86_64): you pull
it, nothing is built on the server.

## Docker Compose

```bash
git clone https://github.com/celsiusm/jarvis-workspace && cd jarvis-workspace
cp .env.example .env        # set PROYECTOS_DIR, and PUID/PGID to your user (`id -u` / `id -g`)
docker compose up -d
docker compose logs         # look for the line with ?token=
```

Open the link from the log (`http://<server-ip>:3000/?token=…`) once: the browser
remembers it. In the UI, create projects with paths under `/proyectos` (that is
your `PROYECTOS_DIR`).

## Unraid

1. **Docker → Add Container → Template**, paste this URL and press Apply:
   `https://raw.githubusercontent.com/celsiusm/jarvis-workspace/main/packaging/unraid/jarvis-workspace.xml`
2. Set **Projects** to the share with your code (e.g. `/mnt/user/projects`).
   The rest of the defaults are fine: data and CLI logins go to
   `/mnt/user/appdata/jarvis-workspace/`, and it runs as `99:100` like other apps.
3. Start it, open the container's **Log** and open the `?token=` link.

Opening it by name (`http://tower:3000`) works once you have entered with the token.

## Access token

Jarvis has terminals, so whoever opens it from **another machine** needs the
token. It is printed in the log at startup and kept in `data/acceso-token`
(it survives updates). Requests from the server itself (the agents' hooks, a
browser on the same machine) do not need it.

| `JARVIS_TOKEN` | Behaviour |
| --- | --- |
| empty (default) | a random token is generated and required from other machines |
| `your-own-value` | that value is the token |
| `off` | no token — anyone on your network reaches the terminals |

Scripts can send it as a header: `X-Jarvis-Token: <token>`.

## Users and permissions

The container runs as the user `PUID`:`PGID` (default `1000:1000`; Unraid `99:100`),
so the files the agents create in your projects belong to you. It is also needed
for Claude Code's autonomous mode, which refuses to run as root. `PUID=0` keeps
the container as root (not recommended).

## Updating

`docker compose pull && docker compose up -d` (Unraid: **Check for updates**).
Your data, the CLI logins and the token live in the volumes, not in the image.

## Notes

- The image includes Claude Code. Other CLIs install from the UI (the **Install**
  pill in the terminal picker).
- Voice dictation: set `GROQ_API_KEY` (free key) or set it from the UI; it is
  stored in the data volume.
- Build the image yourself: `docker compose up -d --build`.
