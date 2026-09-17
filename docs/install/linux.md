# Install on Linux

One command (full app: Python venv, tmux, ffmpeg, every Jarvis feature):

```bash
curl -fsSL https://raw.githubusercontent.com/celsiusm/jarvis-workspace/main/install.sh | bash
```

That clones to `~/jarvis-workspace`, puts `jarvis` on your PATH (`~/.local/bin`), and opens `http://127.0.0.1:3000`. Next time: `jarvis`.

Already cloned? `./install.sh` from the repo root. `--no-start` skips launching the server. `--dry-run` prints the plan.

No `.deb` / AppImage yet. Terminals are **tmux** sessions.

## Native (from source, manual)

```bash
git clone https://github.com/celsiusm/jarvis-workspace
cd jarvis-workspace

sudo apt update
sudo apt install -y python3 python3-venv python3-pip tmux git curl ffmpeg
# Fedora: sudo dnf install python3 python3-pip tmux git curl ffmpeg
# Arch:   sudo pacman -S python python-pip tmux git curl ffmpeg

python3 -m venv venv
source venv/bin/activate
pip install -r plotspace/requirements.txt
pip install -e .                 # registers the `jarvis` command

bash scripts/setup-hooks.sh      # optional: anti-secret pre-commit/pre-push
```

### Run

```bash
source venv/bin/activate
jarvis
# until `pip install -e .`:
# python3 -m uvicorn plotspace.main:app --host 127.0.0.1 --port 3000 --loop asyncio
```

Open `http://127.0.0.1:3000`.

Use `--host 0.0.0.0` only if you intentionally want LAN access.

## What you need besides the app

**Your own agent CLIs** (Claude Code, Codex, opencode, …), installed and logged
in. Link them in ⚙ → **Accounts** (BYOK). Jarvis orchestrates; it does not ship
those products.

**tmux** and **git** are required — the terminal grid is tmux, nothing else.

Optional:

- `ANTHROPIC_API_KEY` in `plotspace/.env` — only for orchestrator-chat extras.
- `GROQ_API_KEY` — cloud STT for push-to-talk (otherwise local STT extras apply).

## Where data lives

| | |
|---|---|
| Default (dev checkout) | `<repo>/data` |
| App-style / relocated | `JARVIS_DATA_DIR` — recommended: `~/.local/share/jarvis` |

Example:

```bash
mkdir -p ~/.local/share/jarvis
export JARVIS_DATA_DIR=~/.local/share/jarvis
jarvis
# or: jarvis --datos ~/.local/share/jarvis
```

That directory holds `jarvis.db`, CLI account secrets, and logs. Never commit it.

## Docker (optional)

Same compose flow as other platforms — see the README and
[`windows.md`](windows.md) Path B. Set `PROYECTOS_DIR` in `.env` to the host
folder that should appear as `/proyectos` inside the container. First build is
slow/large; full e2e build verification is not claimed here.
