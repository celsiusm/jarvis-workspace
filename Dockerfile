# syntax=docker/dockerfile:1

# ─────────────────────────────────────────────────────────────────────────────
# Jarvis Workspace — Docker image (complete, works out of the box).
#
# Published as ghcr.io/celsiusm/jarvis-workspace (see .github/workflows/docker.yml),
# so a server (Unraid, a NAS, a VPS) just pulls it: no local build.
#
# What's inside: tmux + git + ffmpeg, Node 22 + Claude Code, the Python deps
# (incl. local dictation via onnx-asr) and Playwright's Chromium for the Web
# Preview remote browser. NO torch: nothing imports it any more (dictation runs
# on onnx-asr / faster-whisper / Groq), and it used to add several GB.
#
# Security when it listens on the network (it does: 0.0.0.0 inside the
# container): whoever opens Jarvis from ANOTHER machine needs the access token.
# The link is printed in `docker logs` at startup and kept in data/acceso-token.
# JARVIS_TOKEN=off disables it; JARVIS_TOKEN=<value> pins your own.
#
# It runs as the non-root user `jarvis` with the UID/GID you pass in PUID/PGID
# (Unraid: 99/100), so the files the agents create in /proyectos are yours, and
# Claude Code's autonomous mode works (it refuses to run that way as root).
# ─────────────────────────────────────────────────────────────────────────────

# Base: Python 3.14 (wheels exist for every dependency, incl. onnxruntime,
# ctranslate2, av, numpy, playwright, psutil, httptools). If your registry lacks
# it, python:3.13-slim works too: the code uses nothing exclusive to 3.14.
FROM python:3.14-slim

# PYTHONUNBUFFERED is critical: the startup lines (including the access link)
# must reach `docker logs` immediately, not sit in stdout's buffer.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    # The CLIs' auth (claude/codex/…) lives under $HOME; mount a volume there
    # (/home/jarvis) so logins survive container updates.
    HOME=/home/jarvis \
    # Playwright's Chromium OUTSIDE $HOME: a volume mounted on HOME would shadow
    # a browser installed under ~/.cache. In /opt it stays baked into the image.
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    PUID=1000 \
    PGID=1000

WORKDIR /app

# ─── 1. System deps (the non-Chromium ones) ───────────────────────────────────
# tmux → persistent terminal sessions · git → agents commit · ffmpeg → dictation
# audio · curl/ca-certificates/gnupg → HTTPS, NodeSource, healthcheck ·
# iproute2 (`ss`) / procps (`ps`) → agents check ports and processes.
# Chromium's system libs come with `playwright install --with-deps` (step 4).
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
# Global npm packages live in /usr/lib/node_modules (not in HOME) → they survive
# the HOME volume. Other CLIs install from the UI (Install pill) or add them here:
#     npm i -g @openai/codex   ·   npm i -g @qwen-code/qwen-code   ·   npm i -g opencode-ai
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force

# ─── 3. Python dependencies ────────────────────────────────────────────────────
# Only requirements.txt first: the heavy layer stays cached when only code changes.
COPY plotspace/requirements.txt /app/plotspace/requirements.txt
RUN python -m pip install --upgrade pip \
    && pip install -r /app/plotspace/requirements.txt

# ─── 4. Chromium for the Web Preview (Playwright) ─────────────────────────────
# --with-deps resolves the distro's system libs itself (Debian trixie renamed many
# to *t64). Own `apt-get update`: earlier layers wiped the lists.
RUN apt-get update \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX /opt/ms-playwright

# ─── 5. App code ───────────────────────────────────────────────────────────────
COPY . /app
# init_db() runs on import and sqlite does not create the .db's directory.
RUN mkdir -p /app/data \
    && install -m 0755 /app/docker/entrypoint.sh /usr/local/bin/jarvis-entrypoint

# ─── 6. Non-root user + git defaults ──────────────────────────────────────────
# The entrypoint re-maps this user to PUID/PGID at startup.
# Git: agents commit in your repos (/proyectos). Without an identity `git commit`
# fails ("Author identity unknown"); safe.directory='*' avoids "dubious
# ownership" when the bind mount's owner differs. System-level, so it applies to
# whatever UID the container ends up running as. Override inside a terminal.
RUN groupadd -g 1000 jarvis \
    && useradd -u 1000 -g jarvis -m -d /home/jarvis -s /bin/bash jarvis \
    && chown -R jarvis:jarvis /app/data /home/jarvis \
    && git config --system user.name "Jarvis Agent" \
    && git config --system user.email "agent@jarvis.local" \
    && git config --system --add safe.directory '*'

EXPOSE 3000

# /api/health needs no token and answers as soon as the process is up.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
    CMD curl -fsS http://127.0.0.1:3000/api/health || exit 1

ENTRYPOINT ["jarvis-entrypoint"]
# FIXED CMD with --loop asyncio: uvloop suffers a periodic event-loop stall on
# this stack, visible as gaps in the terminals' echo. Do NOT remove the flag.
CMD ["python", "-m", "uvicorn", "plotspace.main:app", \
     "--host", "0.0.0.0", "--port", "3000", "--loop", "asyncio"]
