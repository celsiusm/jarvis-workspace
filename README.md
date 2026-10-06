<div align="center">

# Jarvis Workspace

A local web cockpit that runs several AI coding agents side by side — your keys, your machine.

[![License](https://img.shields.io/badge/license-MIT-22c55e.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.11+-3b82f6.svg)
![BYOK](https://img.shields.io/badge/model-BYOK-8b5cf6.svg)

**[Install · Linux / macOS](#linux--macos)** &nbsp;·&nbsp; **[Install · Windows](#windows)**

</div>

You open a project. Jarvis gives you a grid of live terminals. Claude Code, Codex, OpenCode, Qwen, Antigravity, Grok Build, Cursor, Pi, or a plain shell — each in its own pane, all on the same branch. You bring the accounts. Jarvis only orchestrates.

<table>
<tr>
<td width="50%" valign="top">

<h2 id="linux--macos">Linux / macOS</h2>

```bash
curl -fsSL https://raw.githubusercontent.com/celsiusm/jarvis-workspace/main/install.sh | bash
```

Then `jarvis`. Opens `http://localhost:3000`. macOS needs [Homebrew](https://brew.sh). Details: [Linux](docs/install/linux.md) · [macOS](docs/install/macos.md).

</td>
<td width="50%" valign="top">

<h2 id="windows">Windows</h2>

```powershell
irm https://raw.githubusercontent.com/celsiusm/jarvis-workspace/main/install.ps1 | iex
```

Engine runs in [WSL2](docs/install/windows.md). One reboot if WSL is new. Leaves **Jarvis.bat** on the Desktop. Details: [Windows](docs/install/windows.md).

</td>
</tr>
</table>

Full app (terminals, voice, preview, Mobile Studio, …). Link your own CLIs in ⚙ → Accounts. Docker: `cp .env.example .env && docker compose up -d --build` (large, experimental).

---

### Start here

The empty workspace. One project, nothing running yet. New terminal, talk to Jarvis, or open the editor.

<p align="center">
  <img src="docs/images/home-empty.png" alt="Empty Jarvis workspace — What are we building today? Welcome card with new terminal, talk to Jarvis and editor shortcuts" width="920">
</p>

### Launch a swarm

Pick agents, how many, and a layout. Up to 12 panes: several Claude Codes, a mix with OpenCode, or one shell. CLIs you haven't installed show up dimmed with the exact install command and an **Install** button — they slide into place once they are ready. Everything lands in a live grid.

<p align="center">
  <img src="docs/images/launcher-batch.png?v=2" alt="New terminal — pick Claude Code, Codex, OpenCode, Grok Build or a shell; Qwen, Antigravity, Cursor and Pi marked not installed with their install command; layout and launch" width="920">
</p>

<p align="center">
  <img src="docs/images/swarm-live.png?v=2" alt="Jarvis Workspace — a live grid of seven Claude Code agents on the same project" width="920">
</p>

### Editor and radio by your side

Edit your project while the agent works in its own pane — file tree, Monaco editor and a live terminal together. Or open the Radio: search YouTube music, your local files or Spotify while the swarm builds.

<p align="center">
  <img src="docs/images/editor.png?v=2" alt="Editor — file tree, the LICENSE file open in the editor, and a Claude Code terminal in the same workspace" width="920">
</p>

<p align="center">
  <img src="docs/images/radio.png?v=2" alt="Radio — Claude FM playing, with YouTube, Local and Spotify tabs and related tracks, open over the agent grid" width="920">
</p>

### Review, tasks and a real browser — in the dock

The right-hand dock keeps the rest of the loop next to the terminals. **Review** lists what every agent changed — grouped by who wrote it, with per-file diffs — and commits only what you tick. **Tasks** is the live monitor of your agents (running, waiting, finished). The **Browser** is a real server-side Chromium with split view, so it loads any site and your `localhost` dev servers.

<p align="center">
  <img src="docs/images/review.png?v=2" alt="Review — files changed on main grouped as Unattributed, with a commit message box, next to a Claude Code terminal" width="920">
</p>

<p align="center">
  <img src="docs/images/tasks.png?v=2" alt="Tasks — live agent monitor showing one idle Claude Code terminal" width="920">
</p>

<p align="center">
  <img src="docs/images/browser.png?v=2" alt="Browser — a real browser in the dock with tabs, split-view layouts and an address bar" width="920">
</p>

### Mobile Studio

Preview your app in a live phone frame — add phone frames, web browsers or project notes to the canvas, zoom to taste. Phones connect to the Expo/Metro the agent started: the empty canvas explains the three steps — start the project, run Metro, the app lands on the frame. Mobile Studio also **detects Expo projects** — when the agent starts Metro, the mobile tab opens on its own (⚙ → Appearance → auto-start).

<p align="center">
  <img src="docs/images/mobile-studio-signal.png" alt="Mobile Studio — empty canvas waiting for the signal, with the 3-step guide: Expo project, Metro --web, live preview" width="800">
</p>

<p align="center">
  <img src="docs/images/mobile-studio-live-frame.png" alt="Mobile Studio — the empty home and the studio side by side: iPhone 15 Pro frame, dock with phone, web, note" width="800">
</p>

### Memory, as a graph of constellations

The shared memory of the swarm. Each memory is a node, grouped into constellations by topic (here: *Environment · WSL & Git*); links between memories draw the lines. Switch between List, Graph, Live and Summary, zoom, pan and click a node to open it.

<p align="center">
  <img src="docs/images/memory-graph.png?v=2" alt="Memory graph — four memories grouped in an Environment · WSL & Git constellation, with List, Graph, Live and Summary tabs" width="800">
</p>

### Live on Discord

Windows only: the launcher (`Jarvis.exe`) pushes your fleet to Discord — live activity, agent count and uptime while the swarm works.

<p align="center">
  <img src="docs/images/discord-presence.png" alt="Discord Rich Presence — playing Jarvis, in Terminals, 1 agent (1 of 12), 2:38:07" width="450">
</p>

### Your accounts, not ours

⚙ → **Accounts**. Several logins per CLI — Claude Code, Codex, Grok Build, Antigravity, OpenCode, Qwen Code, Pi, Cursor — and you switch without logging in again. Native sessions show up even if you never clicked Connect. Rate-limit? It rotates. Can't see yours? **Run diagnostics** tells you where Jarvis looked.

<p align="center">
  <img src="docs/images/accounts-switchboard.png?v=2" alt="Settings → Accounts — the switchboard with the eight supported CLIs and a Run diagnostics button" width="920">
</p>

### Make it yours

⚙ → **Appearance**. 24 themes (two of them light), fine tint, language (English / Spanish) and scale. The bench at the top is the live workspace.

<p align="center">
  <img src="docs/images/appearance-themes.png?v=2" alt="Settings → Appearance — the live bench, 24 themes ordered by color wheel, tint sliders and scale" width="920">
</p>

**Liquid Glass** gives bars, panels and dialogs a translucent material, and an optional custom background (gradients or your own image) sits behind it, with blur, saturation, veil and opacity controls.

<p align="center">
  <img src="docs/images/appearance-glass.png?v=2" alt="Settings → Appearance — language, Liquid Glass and mobile preview toggles, and the custom background with Aurora, Dusk, Ocean, Ember and Forest presets" width="920">
</p>

### Extensions

⚙ → **Extensions** shows what each AI reads in your project — skills, commands, agents and rules for Claude Code, Codex, Gemini, Cursor, Copilot and more — detected in the repo and in your user folder, plus Claude Code plugins and the marketplace.

<p align="center">
  <img src="docs/images/extensions.png?v=2" alt="Settings → Extensions — a Claude Code skill and CLAUDE.md, and the AGENTS.md instruction files that Codex reads" width="920">
</p>

Hold your voice key to dictate (Groq's free Whisper API).

### Radio: local music & Spotify

The Radio searches **local music** (`data/music/` — upload via the UI or drop files into `data/music/audio/`) and **Spotify** (search with your own account, playback in the browser). For Spotify you need a client ID — free at [developer.spotify.com](https://developer.spotify.com/dashboard) → **Create app** → set the **Redirect URI** to `http://localhost:3000/api/radio/spotify/callback` — then put `SPOTIFY_CLIENT_ID` (and optionally `SPOTIFY_CLIENT_SECRET`) in your `.env` (see `.env.example`). Never commit those values.

## Use

| | |
|---|---|
| **Ctrl+T** | New project |
| **Ctrl+\\** | New terminals |
| **⚙** | Accounts, appearance, voice |
| **Ctrl+P** | Dock |

Also: `Ctrl+B` strip · `Ctrl+E` editor · `Ctrl+J` Jarvis chat · `Ctrl+1…9` jump to a project. Port **3000** is Jarvis — put dev servers on 5000–5999 or 8081–8999.

## Tests

```bash
source venv/bin/activate
python -m pytest
node frontend/sections/**/__tests__/*.test.js
```

Vanilla HTML/CSS/JS, no npm. PRs: [`CONTRIBUTING.md`](CONTRIBUTING.md). Keep secrets out of git (`data/`, `.env`).

## License

[MIT](LICENSE) © 2026 Jarvis Workspace contributors
