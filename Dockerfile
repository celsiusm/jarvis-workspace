# syntax=docker/dockerfile:1

# ─────────────────────────────────────────────────────────────────────────────
# Jarvis Workspace — Docker image v1 (COMPLETE and working out of the box).
#
# Ships ALL the deps in plotspace/requirements.txt, including the heavy ones:
# openai-whisper + torch (STT) and playwright + chromium (the Web Preview remote
# browser). The code imports them at runtime —voice.py/main.py preload Whisper,
# remote_browser.py imports Playwright— and today it crashes without them, so v1
# brings them all. Result: a large image (several GB) that starts and works with
# no manual steps.
#   TODO v2: optional lightweight layer (lazy imports + a variant without
#            whisper/torch or chromium for those who only want to orchestrate
#            terminals).
# ─────────────────────────────────────────────────────────────────────────────

# Base: the project runs Python 3.14 (verified host: 3.14.4). python:3.14-slim
# exists on Docker Hub since the 3.14 release (Oct 2025). If it is NOT available
# in your registry, switch to `python:3.13-slim` — the code uses nothing
# exclusive to 3.14 (but torch/whisper DO need wheels for that version;
# see README → "Build notes").
FROM python:3.14-slim

# PYTHONUNBUFFERED is critical here: the ACCESS TOKEN is printed at startup and
# the user needs it to log in the first time — without this it can get stuck in
# stdout's buffer and never show up in `docker compose logs`.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    # Explicit: the CLIs' auth (claude/codex/…) and the .gitconfig are written
    # under $HOME; the HOME volume is mounted at /root. Without pinning it, a
    # tool that reads an empty $HOME could write to the cwd and lose auth on
    # restart.
    HOME=/root \
    # Playwright's Chromium OUTSIDE HOME: the container HOME is a persistent
    # volume (it keeps the CLIs' auth) and mounting it would SHADOW any browser
    # installed under ~/.cache. In /opt it stays baked into the image and the
    # runtime finds it with this same variable.
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

WORKDIR /app

# ─── 1. System deps (the NON-chromium ones) ───────────────────────────────────
# tmux        → persistent terminal sessions (jarvis_<id>), the heart of the swarm
# git         → agents commit in the user's repos
# ffmpeg      → REQUIRED by Whisper STT: voice.py converts and decodes audio
#               with ffmpeg; without it, /api/voice returns 500
# curl, ca-certificates, gnupg → HTTPS + NodeSource repo + healthcheck
# iproute2    → `ss` (agents check which ports are free before starting a server)
# procps      → ps/pkill used by the maintenance scripts
# Chromium's SYSTEM libs are intentionally NOT listed here: they are installed by
# `playwright install --with-deps chromium` (step 4), which is distro-aware. On
# Debian trixie those packages carry the t64 suffix (libasound2t64, libatk1.0-0t64,
# libnss3, libnspr4, libatspi2.0-0t64, libcups2t64, libgbm1, libxkbcommon0,
# libpango-1.0-0, libcairo2, libxcomposite1, libxdamage1, libxrandr2, libxfixes3,
# libxext6, libxcb1, libdrm2, libdbus-1-3…) and hardcoding them would break the
# build if the name set changes between Debian versions. Playwright resolves them
# on its own.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tmux \
        git \
        ffmpeg \
        curl \
        ca-certificates \
        gnupg \
        iproute2 \
        procps \
    && rm -rf /var/lib/apt/lists/*

# ─── 2. Node.js LTS + npm + Claude Code CLI ───────────────────────────────────
# The agents run Node CLIs. We install Node 22 LTS (NodeSource) and the primary
# CLI (Claude Code) globally. The global binary lives in /usr/lib/node_modules
# (NOT in HOME) → it survives the HOME volume.
# To ADD other CLIs (see README → "Other agent CLIs"): add an
# `npm i -g ...` here. Examples:
#     npm i -g @openai/codex        # Codex
#     npm i -g @qwen-code/qwen-code # Qwen Code
#     npm i -g opencode-ai          # opencode
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force

# ─── 3. Python dependencies ────────────────────────────────────────────────────
# Only requirements.txt is copied first: this way the heavy layer is cached and a
# code change (step 5) does not invalidate it.
COPY plotspace/requirements.txt /app/plotspace/requirements.txt

# torch first, from PyTorch's CPU index: a default `pip install torch` on Linux
# pulls the CUDA build (several GB of GPU libs USELESS in a GPU-less container).
# Pre-installing the +cpu build makes openai-whisper find it already satisfied
# and not drag in the CUDA one. If the CPU index does not have the wheel for this
# Python version, the `||` falls back to the default index (heavier build, but
# the build does NOT fail).
RUN python -m pip install --upgrade pip \
    && ( pip install "torch" --index-url https://download.pytorch.org/whl/cpu \
         || pip install "torch" ) \
    && pip install -r /app/plotspace/requirements.txt

# ─── 4. Chromium for the remote browser (Playwright) ───────────────────────────
# --with-deps installs the chromium binary + ALL its system libs in a distro-aware
# way (it solves the Debian trixie t64 package mess). It installs to
# PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright (outside the HOME volume).
# Its own `apt-get update`: the previous layers wiped /var/lib/apt/lists, and
# --with-deps runs `apt-get install` (it needs fresh lists to resolve).
RUN apt-get update \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# ─── 5. App code ───────────────────────────────────────────────────────────────
COPY . /app

# data/ is a runtime volume, BUT init_db() runs on IMPORT of backend.main
# (before the lifespan) and sqlite3 does NOT create the directory of the .db file
# → if it does not exist, the import blows up with "unable to open database file".
# Guarantee it:
RUN mkdir -p /app/data

# ─── 6. Git: default identity + safe.directory ────────────────────────────────
# Agents commit in the user's repos (mounted at /proyectos). Without an identity,
# `git commit` fails with "Author identity unknown". safe.directory='*' avoids the
# "detected dubious ownership" git throws when the bind-mount comes with a uid
# different from the container's. The user can override the identity via
# env/CLI inside their terminal. (It is written to /root/.gitconfig; on first boot
# Docker copies /root to the HOME volume, so it persists.)
RUN git config --global user.name "Jarvis Agent" \
    && git config --global user.email "agent@jarvis.local" \
    && git config --global --add safe.directory '*'

EXPOSE 3000

# FIXED CMD with --loop asyncio: uvloop (uvicorn[standard]'s default) suffers a
# periodic event-loop stall on this stack, visible as gaps in the terminals' echo.
# Pure asyncio eliminates it — it is a HARD project requirement, do NOT switch to
# uvloop or remove the flag.
CMD ["python", "-m", "uvicorn", "plotspace.main:app", \
     "--host", "0.0.0.0", "--port", "3000", "--loop", "asyncio"]
