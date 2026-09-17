# Install on macOS

One command (needs [Homebrew](https://brew.sh); Xcode CLT if you don't have git yet):

```bash
curl -fsSL https://raw.githubusercontent.com/celsiusm/jarvis-workspace/main/install.sh | bash
```

Same script as Linux: full app, `jarvis` on your PATH, opens `http://127.0.0.1:3000`.

There is no DMG / notarized app yet. Terminals are **tmux** sessions.

## Native (from source, manual)

```bash
# Xcode CLT if you don't have them yet (git, clang, …)
xcode-select --install

brew install python@3.12 tmux git ffmpeg

git clone https://github.com/celsiusm/jarvis-workspace
cd jarvis-workspace

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

## What you need besides the app

**Your own agent CLIs** (Claude Code, Codex, opencode, …). Link them in
⚙ → **Accounts** (BYOK). Jarvis does not redistribute those products.

**tmux** and **git** are required.

Optional: `ANTHROPIC_API_KEY` / `GROQ_API_KEY` in `plotspace/.env` for
orchestrator-chat extras and cloud STT.

## Where data lives

| | |
|---|---|
| Default (dev checkout) | `<repo>/data` |
| Relocated | `JARVIS_DATA_DIR` (e.g. `~/.local/share/jarvis`) |

```bash
mkdir -p ~/.local/share/jarvis
jarvis --datos ~/.local/share/jarvis
```

## Docker (optional)

Install Docker Desktop for Mac, then the same compose flow as the README /
[`windows.md`](windows.md) Path B. First build is slow/large; full e2e build
verification is not claimed here.
