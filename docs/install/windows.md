# Install on Windows

Jarvis Workspace is a **Linux** app (Python + uvicorn + **tmux**). On Windows
the engine runs inside **WSL2 Ubuntu**. One command does the rest:

```powershell
irm https://raw.githubusercontent.com/celsiusm/jarvis-workspace/main/install.ps1 | iex
```

If WSL is missing, that installs Ubuntu and asks you to **reboot**, then run
the same line again. After it finishes you get **Jarvis.bat** on the Desktop —
double-click next time (warms WSL, starts the server, opens Chrome as an app
window at `http://localhost:3000`).

You bring your own agent CLIs (Claude Code, Codex, etc.) and link them in
⚙ → **Accounts**. Jarvis does not ship those products.

Docker Desktop is Path B below (still uses WSL2 under the hood).

---

## Path A — WSL2, step by step

Skip this if the one-liner above already ran.

### 1. Install WSL2 + Ubuntu

In PowerShell (Admin):

```powershell
wsl --install
```

Reboot when Windows asks. Open **Ubuntu** from the Start menu and finish the
first-boot user setup.

### 2. Clone inside the Linux filesystem

**Important:** the clone must live under the Linux home (e.g. `~/…`), **not**
under `/mnt/c/...`. tmux + heavy I/O on NTFS is painful.

```bash
git clone https://github.com/celsiusm/jarvis-workspace ~/jarvis-workspace
cd ~/jarvis-workspace
```

If the repo already lives somewhere else, set `JARVIS_WSL_DIR` (Linux path) before
using the Windows `.bat` launcher. Another WSL distro than the default:
`JARVIS_WSL_DISTRO`.

### 3. System packages + Python venv

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip tmux git curl ffmpeg

python3 -m venv venv
source venv/bin/activate
pip install -r plotspace/requirements.txt

# Optional: install the `jarvis` CLI entry point
pip install -e .

# Optional: anti-secret git hooks
bash scripts/setup-hooks.sh
```

### 4. First start (from inside WSL)

```bash
source ~/jarvis-workspace/venv/bin/activate
jarvis
# until the CLI is installed:
# python3 -m uvicorn plotspace.main:app --host 0.0.0.0 --port 3000 --loop asyncio
```
On first boot the server is ready at `http://localhost:3000`.

### 5. Open it from Windows

- **Desktop launcher (what `install.ps1` copies):** `scripts/abrir-jarvis-app.bat`
  as `Jarvis.bat` on the Desktop. It warms WSL, starts the server if needed
  (`~/jarvis-workspace/scripts/reiniciar-server.sh`), and opens Chrome in app
  mode at `http://localhost:3000`.
- **Compiled window (optional):** from WSL, `bash scripts/compilar-lanzador-windows.sh`
  builds a thin WebView2 window (`Jarvis.exe`) around the same Linux server.

Then open `http://localhost:3000`.

> Health checks should use `http://127.0.0.1:3000` (the name `localhost` often
> resolves IPv6-first; WSL may only be listening on IPv4). The browser URL can
> stay `http://localhost:3000`.

### 6. Link your CLI accounts (BYOK)

In the UI: ⚙ → **Accounts** → link Claude / Codex / etc. with **your** logins.
No API key is required for the main BYOK flow. An optional `ANTHROPIC_API_KEY`
in `plotspace/.env` is only for the orchestrator chat extras.

---

## Path B — Docker Desktop

Works if you prefer not to manage a venv inside WSL. You still need
[Docker Desktop](https://www.docker.com/products/docker-desktop/) with the WSL2
backend enabled.

```powershell
git clone https://github.com/celsiusm/jarvis-workspace
cd jarvis-workspace
copy .env.example .env
```

Edit `.env` and set `PROYECTOS_DIR` to a **Windows path Docker can mount**, for
example:

```env
PROYECTOS_DIR=C:\Users\you\Projects
```

Inside the container that mount appears as `/proyectos` — when you create a
project in the UI, use container paths like `/proyectos/mi-app`.

```powershell
docker compose up -d --build
docker compose logs -f
```

Open `http://localhost:3000`.

**Honesty check:** the first `docker compose build` is **slow and large**
(base image + Python deps; historically also heavy ML/browser layers in the
Dockerfile). The compose file and image layout are maintained in-repo, but a
full end-to-end `docker build` on a clean machine is **not claimed as verified**
here — if a system step fails, the sensitive spots are commented in the
`Dockerfile`.

---

## Where things live

| | WSL2 path | Docker |
|---|---|---|
| Code / engine | `~/jarvis-workspace` | image + bind mounts |
| App data (DB, CLI account secrets) | `<repo>/data` by default, or `JARVIS_DATA_DIR` (e.g. `~/.local/share/jarvis`) | `./data` → `/app/data` |
| Your projects | anywhere under the Linux FS (prefer `~/proyectos`, not `/mnt/c`) | host folder from `PROYECTOS_DIR` → `/proyectos` |

## What you still need

- **Your agent CLIs** installed and logged in (Jarvis does not redistribute them).
- **tmux** and **git** (Path A installs them via apt; Path B ships them in the image).
- Optional: `GROQ_API_KEY` in `plotspace/.env` / `.env` if you want cloud STT for
  push-to-talk dictation.
