# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is
Local web dashboard for orchestrating multiple AI agents (Claude Code, Codex, Gemini, etc.) working in parallel on coding projects. Runs on localhost:3000. The central orchestrator runs on the Claude SUBSCRIPTION (`claude -p` headless, `core/orq_cli.py`) to interpret natural-language orders and coordinate agents via tmux sessions — zero paid API tokens. **All agents work directly on the project's main branch** (in this repo, `master`; no worktrees, no feature branches, no merge): they coordinate by reading the project's `CLAUDE.md` and editing disjoint files; the anti-conflict defense is the coordination layer (`.jarvis/LIVE.md`).

> **This file loads in EVERY session of EVERY agent — keep it at HIGH STANDARD.** Here go actionable rules + the architecture map. The historical "why" and fine detail (selectors, CSS classes, dated changelogs) do NOT go here: they live in the shared memory `.jarvis/memory/` (the `[[pointers]]` point there) and in each section's `AGENTS.md`. Before fattening this file, ask yourself whether the info fits better in a memory.

## Way of working
Run the requests **DIRECTLY**: no spec, no design doc, no plan in `docs/`. Design and trade-offs you resolve yourself in the best way; asking the user is fine ONLY when the decision is genuinely theirs (visible behavior, product choice). Quality is non-negotiable: TDD, running the WHOLE suite and verifying before calling anything done stay mandatory.

## Commits (when your task is done)
When you finish your task, **commit ONLY YOUR files** (the ones you touched for that task), explicitly: `git add <path1> <path2> && git commit -m "..."`.
**NEVER `git add -A` nor `git commit -am`**: all agents share the same working tree on `main`, so `git status` shows EVERYONE'S changes mixed — sweeping them with `-A`/`-am` takes away the half-done work of other agents that haven't finished. If you see files at commit time that aren't yours, leave them out (they're someone else's — they'll commit them).
- **Conventional Commits message** (`feat:` / `fix:` / `refactor:`, with scope):
  the "Update available" popup builds the news from the commit subject (`plotspace/routers/system.py`). Avoid `chore:` / `wip` for things the user should see listed (those types filter out as noise). Without a commit, the popup falls to the generic "Improvements and misc fixes".
- **No session links in public history**: this repo is PUBLIC — never add a `Claude-Session:` line (or any claude.ai session URL) to commit messages, PR titles/bodies or comments. A `Co-Authored-By:` trailer is fine.
- **`git push` is NOT needed (nor appropriate)**: the server auto-pushes `master` to origin when it detects your commit (`fe_watch`, flag `AUTO_PUSH`) — the GitHub backup stays current without anyone remembering.
- **Before editing a file another agent may have touched**, look at its history: `git log --oneline -- <file>` and `git show <commit>`. If there's a recent commit, understand what was done and why, and build ON TOP without undoing it. That's the "past" layer complementing the live layer (`.jarvis/LIVE.md` = who touches what NOW).
- **Commit EARLY and SMALL — it's your defense, not a risk.** In this shared tree the only safe thing is HEAD: uncommitted work can be swept by someone else's `add -A`/stash. A unit that compiles and passes tests gets committed NOW.
- **The EASY way to commit well:** `python3 scripts/commit_propio.py -m "..."` — stages only YOUR files (per LIVE.md) under a lock that avoids stage↔commit races with other agents. Extra paths as arguments.
- **Hard enforcement (ownership lock):** this rule is not just discipline —
  the hook `.githooks/pre-commit` runs `scripts/guard_propiedad.py`, which identifies
  the agent by its tmux session (`jarvis_<id>`) and BLOCKS the commit if among the staged
  files there's one whose 🔒 owner (or active 🔖 reservation) in `LIVE.md` is ANOTHER agent
  (unless their «→ OK» permission). If it blocks you: stage only yours. Legit false positive
  (you have the owner's OK): exempt ONLY that file with
  `GUARD_OK="ruta.py" git commit -m "..."` — **NEVER `--no-verify`** (it also disables
  the secret scanner). Fails open (no tmux/LIVE.md).
- **Going to edit a key file with no owner? RESERVE IT first** (the queue forms before the
  clash): `- @You -> @jarvis: RESERVE <file> — what you'll do` in the MAILBOX (15 min read; see the LIVE protocol).

## Development commands

```bash
# Start the server (from the repo root)
source venv/bin/activate
# --loop asyncio is MANDATORY: uvloop (uvicorn's default) suffers a periodic
# ~400ms event-loop stall in some environments that shows as hiccups in the echo of terminal typing.
# With pure asyncio: no stall.
python3 -m uvicorn plotspace.main:app --host 0.0.0.0 --port 3000 --loop asyncio

# Install dependencies
pip install -r plotspace/requirements.txt

# Verify BACKEND changes without restarting (AGENTS DO NOT RESTART the server;
# the user applies the update with the "Update now" banner):
python -m pytest plotspace/tests/ -q        # pytest imports code from DISK
python -c "import plotspace.main"          # startup smoke (same as the canary)
# scripts/reiniciar-server.sh is a USER tool (or Jarvis's own), not agents'. And NEVER pkill -f uvicorn + relaunch.

# Frontend: no restart needed, but DO bump the ?v=N of the <script src>/<link> in the HTML.

# Tests (run ALL before calling a change good; the Node suites are ~27 and grow)
for t in $(find frontend -path '*__tests__*' -name '*.test.js' | sort); do node "$t" || break; done
python -m pytest plotspace/tests/ -q
```

No build step, no linter. Tests: pure Node suites (native assert, UMD `_pure` pattern) + pytest in `plotspace/tests/`. The frontend is vanilla HTML/CSS/JS served directly by FastAPI as static files. Default bind `127.0.0.1`.

## Fixed stack — don't change without asking
- **Backend**: Python + FastAPI + uvicorn
- **Frontend**: HTML + CSS + JS vanilla (no frameworks, no npm, no node); xterm.js 5.3 vendored in `frontend/vendor/xterm/`
- **DB**: SQLite (`data/jarvis.db`, WAL); **10 tables** (created by `init_db`): `projects`, `terminals` (+ origin columns `origen`/`tarea`/`orquestacion`/`orquestacion_ts`), `task_events`, `project_skills`, `project_notes`, `orquestador_historial`, `orquestador_uso`, `cli_accounts`, `mailbox_msgs`, `memoria_uso`. The Web Builder tables (`wb_*`, `web_pages`, `wb_chats`, `wb_chat_mensajes`) are no longer created, nor are `workflows` and `tasks` (the workflow engine and the manual kanban were removed 2026-09-28): if they're in your DB they're leftovers.
- **Terminals**: tmux sessions persistent (`jarvis_{terminal_id}`); attach WS via **tmux control-mode** with xterm.js as the only emulator (see tmux section)
- **Orchestrator**: **SUBSCRIPTION** engine (`ORQUESTADOR_MOTOR=suscripcion`, default): `claude -p` headless with the active OAuth account (`core/orq_cli.py` — `--safe-mode`, stream-json, `--json-schema`, READ-only tools Read/Glob/Grep with cwd=project). Default model `sonnet` (`ORQUESTADOR_MODEL` overrides it). `ORQUESTADOR_MOTOR=api` = escape hatch with `ANTHROPIC_API_KEY` + haiku. Also: multi-turn chat (real history), `[Project map]` block (`core/repo_map.py`), `spawn_terminal` with `tarea` (launches an agent and hands it its task) and `enviar_prompt` to live free terminals (`es_respuesta` answers a question on screen). **Per-turn context = `core/orq_contexto.py`**: each terminal's live state (phase + for how long, waiting-for-answer, crashed CLI, task sent, files edited, last TASK_* + reason, dev servers, last lines of its pane), git (branch, uncommitted, last commits), the project's CLAUDE.md guide, events and coordination — each block with a size budget; `orq_contexto.es_libre()` is the ONLY definition of a free terminal (enforced in code for `enviar_prompt`). The prompt goes to `claude -p` via **stdin** (argv caps at 128 KB).
- **STT**: `STT_MOTOR=groq` sends dictation to whisper-large-v3-turbo on Groq's LPUs (`core/stt_groq.py`; near-zero local CPU/RAM, ~1s, key in `plotspace/.env`, automatic fallback to the local engine if it fails). Local engine: parakeet-tdt-0.6b-v3 int8 (onnx-asr; `STT_MOTOR=whisper` goes back to faster-whisper `small`), on-demand load (PTT sends `/api/voice/prewarm` on press — no-op with Groq) and unloads itself after idle — never resident for fun. **The model lives in a WORKER process** (`core/stt_proc.py`; `STT_WORKER=off` = old in-proc): onnxruntime holds the GIL 5-20s when creating the session and loading it inside the server froze the whole event loop — that was the scroll freeze after "Update now". **TTS**: edge-tts, voice configurable in ⚙ → Voice.
- **Platform**: Linux / WSL (Ubuntu); on Windows the engine runs inside WSL2 (see `docs/install/windows.md`) and project paths must live under the Linux filesystem; no native file picker (a web one is used).

## Architecture

> **The native Windows app and the Rust engine were REMOVED by decision**: the workspace went back to 100% Linux (Python + uvicorn + tmux, the model that always was). Don't reintroduce `desktop/`, alternative terminal engines, or anything of the shell build circuit — if you need the historical detail it's in git and in the `estado: lapida` memories (category `desktop`).

### Folder structure
- `plotspace/` — `main.py` (entrypoint), `core/` (domain: database, events, auth, ssrf, mantenimiento, control_mode, agent_live, agent_watch, dev_detect, fe_watch, mailbox, puertos, pane_capture, logs; orchestrator: orq_cli, orq_contexto, repo_map; swarm: sentinel; cuentas: cli_accounts, cli_login, cuenta_watch; memoria: memoria_lint, memoria_recall, memoria_lecciones, memoria_categorias, memoria_global, memoria_endurecimiento), `routers/` (**18**, one per section), `tests/` (pytest + `__main__` scripts). See `plotspace/AGENTS.md`.
- `frontend/` — `index.html` (home, at `/`), `shell/` (workspace.html + workspace.js, the frame), `shared/` (tokens.css + base.css + ui.js with `icon()/toast()/confirmar()` + i18n), `sections/<x>/` (each section with its .js + .css), `vendor/`. See `frontend/AGENTS.md` and each section's `AGENTS.md`.
- Sections: `home`, `terminals`, `panel` (dock + strip), `preview` (dev-servers menu), `browser` (Web Preview), `radio`, `editor-slide`, `settings`, `orchestrator`, `editor`, `tasks`, `review`, `mobile-preview`, `memory`.
- `data/` — local state (gitignored). `.workspace/` is a per-project artifact (gitignored): `STATE.md` (written by Jarvis every 10s, read by agents) + per-terminal logs in `.workspace/logs/terminal_{id}_{name}.log` (useful for debugging terminals).
- **Serving:** all `frontend/` mounts at `/static`; the HTML references `/static/sections/<x>/...`.

### Workspace UI ("Single Panel")
Visual detail lives in the memory notes; here, the stable + the rules:
- **185px project strip** (`#jw-strip`, Ctrl+B hides it) with the **New workspace** button `.sb-new-ghost` (the old `#jw-strip-new` no longer exists). **40px top bar** (`#jw-bar`): left version chip + traffic light; center breadcrumb; right `#terminals-reset-btn` · ⚙ `#jw-gear` · ▣ `#jw-dock-toggle` (there is no quick-terminal button in the bar: the picker opens with Ctrl+\\ or from the empty-project card). (The sound toggle lives in ⚙→Voice, no longer in the bar; the live-localhosts menu `#jw-localhosts-btn` moved to the Web Preview toolbar.)
- **24 themes** (`frontend/shared/themes.js`, default `violeta`; includes 2 LIGHT — `papel`, `alba`) applied via `html[data-theme]`, overrides in `tokens.css`, + **Tonality filter** (hue/saturation/depth — inline OKLCH overrides via `JarvisThemes.setTinte`, persists in `jarvis.tinte`). **GOLDEN RULE: NEVER hardcode hex — always `var(--ob-*)`** (with light themes, a fixed dark color = unreadable text). CLI logos: `window.cliLogo(tipo, size)` (`shared/icons/`).
- **Liquid Glass** (`shared/liquid-glass.css`, loaded LAST; tokens `--lg-*` in `tokens.css`; toggle ⚙→Appearance → `html[data-glass]`, `shared/glass.js`): the ONE material for every surface — translucent body + rim as an INSET ring (never a border: zero reflow) + gloss. Fixed chrome and anything next to xterm: NO `backdrop-filter`; only transient floats (modals, popovers, toasts) use `--lg-blur`. New surfaces reuse the `--lg-*` recipe instead of inventing their own glass. **Custom background** (`shared/fondo.js` + `fondo.css`, ⚙→Appearance→Background, `GET/PUT/DELETE /api/fondo[/imagen]`): ON by default with Glass (Aurora gradient) — it's what makes Glass look like glass; the person can pick another gradient or upload an image. ONE fixed static layer `#jw-fondo` (a `filter` layer — NEVER add `transform: translateZ(0)`/`will-change` to it: measured key-echo 40 ms → 16 ms without) carries the blur (`--gw-blur`) + veil + saturation; strip, bar, dock and terminals are a veil over it whose OPACITY (`--gw-p` panels / `--gw-t` terminals, 0-1) also drives the COLOR: low opacity (the normal glass) = a NEUTRAL transparent veil, the appearance's color does NOT paint the environment (only accents); the theme-color weight is opacity², so at 100% the surfaces are exactly the theme's own solid colors (the image then only shows in the gaps). NEVER `backdrop-filter` on cards next to xterm. xterm turns `allowTransparency` on only while a background is active (the CanvasAddon is recreated live in `terminal.js`; grayscale AA is the price); Monaco and remote-browser pages stay solid. Honors `prefers-reduced-transparency`. Settings in localStorage `jarvis.fondo`; the image (client-resized to ≤2560px webp, ≤12 MB, jpeg/png/webp by magic bytes, never SVG) in `data/fondo/`.
- **App scale** (⚙→Appearance→scale, 70–150%): `shared/escala.js` sets `zoom` on `<html>` and scales EVERYTHING (including the terminals' font, which refits and notifies tmux). **RULE: viewport units do NOT adjust with zoom** — every screen height/width goes through `var(--jw-vh, 100vh)` / `var(--jw-vw, 100vw)` (defined in `tokens.css`), never bare `vh`/`vw`; and what must adapt to the usable width does it with *container queries*, not media queries.
- **i18n ES⇆EN** (`shared/i18n.js` + `i18n-dict.js`, selector in ⚙→Appearance): new frontend texts go into the dictionary; dynamic ones as `{marker}` templates, non-DOM sinks via `JarvisI18n.t()`, server-born text via `core/idioma_ui.L()` (rules in `frontend/AGENTS.md`).
- **Single right dock** (`window.JarvisDock`, `sections/panel/panel.js`): tabs preview · jarvis · editor · tasks · review · mobile (Expo projects only; detects the Metro the agent started, does NOT start it). Default 320px / MIN 300, splitter, maximize editor/preview only, unread badges, persistence per project. The render uses `hidden` — **NEVER animate width** (xterm rule).
- **Terminal cards** (`workspace.js`): chrome **"Glass Pro"** (faux-glass pill — translucent gradient + bevel + diagonal shine in `::before`; NEVER `backdrop-filter` over the xterm canvas). CLI logo with **state halo** (idle no halo · thinking/watching green · error red — hooked to `:has(.t-status-*)`, replaces the pip). ✕ removes, card maximize. `.t-name` carries the **live title** of the pane (what the agent is doing); the terminal's **real name** lives in the logo `title` (hover), not in a separate row — respect `t-name-live`/`dataset.nombre`. **The logo NEVER hides when shrinking** (identity + state); on narrow cards the grip drops first, then the name (`t-narrow` <250px · `t-xnarrow` <150px). (The History modal/button + `Ctrl+Shift+H` + the `/history` endpoint were removed.)
- **The mouse POINTS the terminal** (`shell/voice-target.js` + `shell/foco-hover.js`, wired in `workspace.js`): having the cursor over a card already makes it the dictation target, and after a 200ms dwell it also gets keyboard focus (you type and hit Enter without clicking). Guards you must not break: the voice target **freezes** when recording starts (`_activeVoiceSession`), focus is never stolen from a text field in use, and there's an 800ms grace while you type in another terminal.
- **Terminal layout** (`sections/terminals/terminal-layout.js`): modes **mosaic** (default) ⇄ **free/vertical** (drag + resize by 4 corners), min 280×160px per card, fixed 13px font. The xterm canvas ALWAYS follows its card (only freeze under 60×40px); whoever changes sizes fires `TerminalLayout.relayoutAll()` / `JarvisEditor.relayout()`.
- **Web Preview** (`sections/browser/browser.js`, exposes `window.WebPreview`): since 2026-09-12 it's a **server-side Chromium** (`core/remote_browser.py`, WebSocket `/ws/browser/{sid}`, screencast frames + forwarded input) — it replaced the old iframe preview (`sections/preview/preview.js` and `GET /preview/probe` no longer exist), so it loads ANY site. Up to 4 tabs with 1/2/3/4-panel **split view**: ONE focus concept (clicking a pane makes it the active tab — address bar, Back/Forward/Reload follow it); the arrangement is chosen from the REAL space (`disposicion()` in browser.js: side-by-side / stacked / big+2 / 2×2, and a layout that doesn't fit is disabled instead of shown stretched — the server session floor is 240×160); closing a pane shrinks the split; each pane has a slim header only while split. **Loopback**: the remote Chromium opens `localhost` ONLY on the ports of the dev servers Jarvis detected (`ssrf.es_loopback_permitido` + `dev_detect.puertos_vivos()`; never :3000; `JARVIS_BROWSER_LOOPBACK=all` opens any port) and a public page can never reach your loopback. **Search = navigate to the REAL search engine** (`urlBusqueda`): loose text → Google, `yt …` → YouTube. The **"Live localhost" menu** (`#jw-localhosts-btn`, `sections/preview/dev-servers.js`) lives in its toolbar: counter button opening the popover of live dev servers (hidden with 0). The home-made SERP (`serp.html`) stays removed.
- **Dev-server auto-detection** (`core/dev_detect.py`, 2s poller): scrapes the tmux panes AND scans LISTEN ports (attribution by process), with TCP-check anti false-positive; WS `dev_server_detectado`/`dev_server_caido`; excludes :3000 (Jarvis) and :8081 (Metro). Feeds the `#jw-localhosts-btn` menu (in the Web Preview toolbar) — **careful: its ✕ KILLS the port's process**.
- **Swarm environmental awareness**: `core/agent_watch.py` (1s poller) detects "was working and went quiet" without keywords → WS `agente_termino`/`agente_espera`/`agente_trabajando` (sounds, toggle `sonidoTareas`; the tone + volume of the «finished» chime are picked and tried in ⚙→Voice→Alerts — pure plan in `shared/sonido-fin.js`, audio engine in workspace.js, AudioContext primed on the first key/click so the first alert after an F5 is never mute) + **aura** on the inactive card (`sections/terminals/terminal-aura.js`). «Finished» = pane quiet ~4 s then CONFIRMED unchanged `CONFIRMACION_FIN_S` (12 s, agent_watch) — a deliberate trade-off (user wants it to sound only when the agent REALLY finished); TASK_DONE from the sentinel/protocol sounds at once, and so does Claude Code's `Stop` hook (installed with the provenance hooks → `POST /api/swarm/fin` → `agente_termino` immediately; `agent_watch.registrar_fin_hook` mutes the heuristic for 40 s so it never rings twice).
- **Settings** (⚙, full-screen overlay): voice-PTT / shortcuts / appearance (theme + language) / accounts / skills&plugins / memory.
- **Minimal screens** (Settings, Tasks monitor, Review/commit, Browser chrome — 2026-10): flat lists, no cards-in-cards, no ring/gloss/glow per row, sentence-case labels (no mono-caps + guide lines), status as dot + text (not capsules); accent ONLY for the active/focused thing. **Perf rule: full-screen overlays (Settings, Memory) get a plain translucent veil — NO `backdrop-filter`** (blurring the whole workspace with live xterm canvases under it took Extensions/Memory from 60 to ~5 fps); long card lists use `content-visibility: auto`. New screens follow the same recipe; glass stays on the frame (dock, modal), not on every row.
- **Unified creation:** the strip's **New workspace** or **Ctrl+T** opens the "Add project" modal (Create/Open modes + **the OS's own folder dialog** — `core/selector_nativo.py`, `GET /api/fs/nativo` + `POST /api/fs/elegir`: osascript on macOS, zenity/kdialog on Linux, `powershell.exe` from WSL (path translated with `wslpath`, starts in the Linux home, warns on `/mnt`); only when Jarvis is used from the same machine; falls back to Jarvis' own folder browser (`/api/fs/list`) when there is none — CLI grid + distribution, **12 slots** `MAX_TERMINALES`; pure logic in `sections/panel/launcher-state.js`). **Ctrl+\\** opens the quick terminal picker (**9 options**: claude/codex/opencode/qwen/antigravity/grok/cursor/pi/shell). **CLIs not installed are NOT hidden**: they show in place (dashed, greyscale) with «Falta instalar»; on hover the chip gives way to the exact command + an **Instalar** pill (`shared/cli-instalar.js`, `.cli-inst` in base.css) that opens a terminal and types it. The SERVER builds the command (`clis.comando_terminal`; `instalar_cli: <id>` in `terminals/batch` — the client never sends the command; no npm → prefixed with an nvm Node install; Antigravity → opens its site). Afterwards the UI polls `GET /api/clis?refrescar=1` and publishes `clis-estado`. **Order**: usable CLIs first (canonical order inside each group), the ones missing after; an installed one slides up to its place (`ordenarClis`/`reordenar`), and the 1-9 keycaps are the DISPLAYED positions.
- **Shortcuts:** Ctrl+B strip · Ctrl+T new project · Ctrl+\\ quick terminal · Ctrl+P dock (file palette if the editor is visible; Ctrl+Shift+P command palette) · Ctrl+E editor · Ctrl+J jarvis · Ctrl+K search project · Ctrl+1…9 jump to project N · Esc closes/de-maximizes. PTT voice configurable (default: hold AltLeft).
- **Standalone editor:** `GET /editor?project=N` (`#jw-dock-external`, only on the editor tab).
## UI del workspace ("Panel Único", desde 2026-06)
Detalle visual completo: [[arquitectura-panel-unico]] · [[rediseno-violeta-2026-06]]. Acá, lo estable + las reglas:
- **Franja de proyectos de 185px** (`#jw-strip`, Ctrl+B la oculta) con el botón **Nuevo workspace** `.sb-new-ghost` (el viejo `#jw-strip-new` ya no existe). **Barra de 40px** (`#jw-bar`): izquierda chip de versión + semáforo; centro breadcrumb; derecha `#terminals-reset-btn` · ⚙ `#jw-gear` · ▣ `#jw-dock-toggle` (no hay botón de terminal rápida en la barra: el picker se abre con Ctrl+\\ o desde la card del proyecto vacío). (El toggle de sonido vive en ⚙→Voz, ya no en la barra; el menú de localhost vivos `#jw-localhosts-btn` se mudó a la toolbar del Web Preview — salió de esta barra 2026-07-09.)
- **24 temas** (`frontend/shared/themes.js`, default `violeta`; incluye 2 CLAROS — `papel`, `alba`) aplicados vía `html[data-theme]`, overrides en `tokens.css`, + **filtro de Tonalidad** (matiz/saturación/profundidad — overrides OKLCH inline vía `JarvisThemes.setTinte`, persiste en `jarvis.tinte`). **REGLA DE ORO: NUNCA hardcodear hex — siempre `var(--ob-*)`** (con temas claros, un color oscuro fijo = texto ilegible). Logos de CLIs: `window.cliLogo(tipo, size)` (`shared/icons/`). Ver [[configuracion-rediseno-2026-07]].
- **Escala de la app** (⚙→Apariencia→escala, 70–150%): `shared/escala.js` pone `zoom` en `<html>` y escala TODO (incluida la letra de las terminales, que refitean y avisan a tmux). **REGLA: las unidades de viewport NO se ajustan por zoom** — todo alto/ancho de pantalla va por `var(--jw-vh, 100vh)` / `var(--jw-vw, 100vw)` (definidas en `tokens.css`), nunca `vh`/`vw` pelados; y lo que deba adaptarse al ancho útil se hace con *container queries*, no con media queries. Ver [[escala-app-zoom-viewport]].
- **i18n ES⇆EN** (`shared/i18n.js` + `i18n-dict.js`, selector en ⚙→Apariencia): los textos nuevos del frontend entran al diccionario. Ver [[i18n-idioma]].
- **Dock derecho único** (`window.JarvisDock`, `sections/panel/panel.js`): pestañas preview · jarvis · editor · tasks · review · mobile (solo proyectos Expo; detecta el Metro que levantó el agente, NO lo arranca). Default 320px / MIN 300, splitter, maximizar solo editor/preview, badges de no-visto, persistencia por proyecto. El render usa `hidden` — **NUNCA animar width** (regla xterm).
- **Cards de terminal** (`workspace.js`): chrome **"Glass Pro"** (píldora faux-glass — gradiente translúcido + bisel + brillo diagonal en `::before`; NUNCA `backdrop-filter` sobre el canvas de xterm). Logo de CLI con **halo de estado** (idle sin halo · thinking/watching verde · error rojo — hookeado a `:has(.t-status-*)`, reemplaza al pip). ✕ elimina, maximizar por card. `.t-name` lleva el **título vivo** del pane (qué hace el agente); el **nombre real** de la terminal vive en el `title` (hover) del logo, no en un renglón aparte — respetá `t-name-live`/`dataset.nombre`. **El logo NUNCA se oculta al achicarse** (identidad + estado); en cards angostas se suelta primero el grip y después el nombre (`t-narrow` <250px · `t-xnarrow` <150px). (El botón/modal Historial + `Ctrl+Shift+H` + el endpoint `/history` se removieron 2026-07-05.)
- **El mouse APUNTA la terminal** (`shell/voice-target.js` + `shell/foco-hover.js`, cableados en `workspace.js`): tener el cursor sobre una card ya la hace destino del dictado, y tras un dwell de 200ms le pasa también el foco del teclado (escribís y mandás Enter sin clickear). Guardas que NO hay que romper: el destino de la voz se **congela** al empezar a grabar (`_activeVoiceSession`), el foco nunca se le roba a un campo de texto en uso, y hay gracia de 800ms mientras tipeás en otra terminal. Ver [[voz-destino-por-hover]] · [[foco-teclado-por-hover]].
- **Layout de terminales** (`sections/terminals/terminal-layout.js`): modos **mosaico** (default) ⇄ **libre/vertical** (drag + resize por 4 esquinas), mínimo 280×160px por card, fuente fija 13px. El canvas xterm SIEMPRE sigue a su card (único freeze bajo 60×40px); quien cambia tamaños dispara `TerminalLayout.relayoutAll()` / `JarvisEditor.relayout()`. Ver [[terminales-modo-libre]].
- **Web Preview** (`sections/browser/browser.js`, expone `window.WebPreview`): desde el 2026-09-12 es un **Chromium server-side** (`core/remote_browser.py`, WebSocket `/ws/browser/{sid}`, frames de screencast + input reenviado) — reemplazó al preview por iframe (`sections/preview/preview.js` y `GET /preview/probe` ya no existen), así que carga CUALQUIER sitio. Hasta 4 pestañas con layouts de 1/2/3/4 paneles (la pestaña activa siempre se ve). **Buscar = navegar al buscador REAL** (`urlBusqueda`): texto suelto → Google, `yt …` → YouTube. El **menú de "Localhost activos"** (`#jw-localhosts-btn`, `sections/preview/dev-servers.js`) vive en su toolbar: botón con contador que abre el popover de dev servers vivos (se oculta con 0). El SERP casero (`serp.html`) sigue eliminado. Ver [[preview-pestanas]].
- **Auto-detección de dev servers** (`core/dev_detect.py`, poller 2s): raspa los panes tmux Y escanea puertos LISTEN (atribución por proceso), con TCP-check anti falso-positivo; WS `dev_server_detectado`/`dev_server_caido`; excluye :3000 (Jarvis) y :8081 (Metro). Alimenta el menú `#jw-localhosts-btn` (en la toolbar del Web Preview) — **ojo: su ✕ MATA el proceso del puerto** ([[preview-pill-cierra-server]]). Ver [[dev-server-autodetect]].
- **Conciencia ambiental del swarm**: `core/agent_watch.py` (poller 1s) detecta "trabajaba y se quedó quieto" sin keywords → WS `agente_termino`/`agente_espera`/`agente_trabajando` (sonidos, toggle `sonidoTareas`) + **aura** en la card no activa (`sections/terminals/terminal-aura.js`). Ver [[agent-watch-sonidos]] · [[aura-notificacion-cards]].
- **Configuración** (⚙, overlay full-screen): voz-PTT / atajos / apariencia (tema + idioma) / cuentas / skills&plugins / memoria.
- **Creación unificada:** el **Nuevo workspace** de la franja o **Ctrl+T** abre el modal "Agregar proyecto" (modos Crear/Abrir + explorador de carpetas + grid de CLIs + distribución, **12 cupos** `MAX_TERMINALES`; lógica pura en `sections/panel/launcher-state.js`). **Ctrl+\\** abre el picker de terminal rápida (**9 opciones**: claude/codex/opencode/qwen/antigravity/grok/cursor/pi/shell). Ver [[launcher-templates-y-grid]].
- **Atajos:** Ctrl+B franja · Ctrl+T nuevo proyecto · Ctrl+\\ terminal rápida · Ctrl+P dock (palette de archivo si el editor está a la vista; Ctrl+Shift+P palette de comandos) · Ctrl+E editor · Ctrl+J jarvis · Ctrl+K buscar proyecto · Ctrl+1…9 saltar al proyecto N · Esc cierra/des-maximiza. PTT de voz configurable (default: mantener AltLeft).
- **Editor standalone:** `GET /editor?project=N` (`#jw-dock-external`, solo en la pestaña editor).
### Swarm orchestration — subsystems
- **Jarvis-launched agents** (the swarm path of the orchestrator chat): for complex orders Jarvis plans N agents with DISJOINT files and emits one `spawn_terminal` per agent with `tarea` + `archivos`; the backend creates the terminal, claims its territory, waits for the CLI to be ready and pastes the task (+ sentinel closure). The origin is recorded on the terminal row (`origen='jarvis'`, `tarea`, `orquestacion` = the order trimmed, `orquestacion_ts`; also set by `enviar_prompt`) and WS `agentes_update` refreshes the **Tasks live monitor** (`routers/tasks.py` + `sections/tasks/`). No dependencies, no reviewer step: sequential work = launch the first part and say what comes next. (The **workflow engine**, its watchdog, auto-intervention and the manual kanban were REMOVED 2026-09-28 — don't reintroduce them. The Command Deck UI was removed earlier.)
- **Sentinel** (`core/sentinel.py`, every 2s, `SENTINEL=off`): task closure via `.jarvis/signals/terminal_<id>.json` (`{estado, motivo, memorias_usadas}`, one-shot) on EVERY active terminal — the **PRIMARY** closure source; parsing of `TASK_*` from the pane stays as fallback. Both go through `procesar_task_event_interno` (orchestrator.py), which persists the event in `task_events` (with its `motivo` — the raw material of lessons) and broadcasts `task_event`.
- **Swarm memory — active layers**: `core/memoria_recall.py` injects the relevant memories into each step's prompt AND the orchestrator's planning (deterministic zero-API signals: paths, tags, **BM25** over bodies, category, historical use) · `core/memoria_lint.py` + `GET /api/projects/{id}/memory/salud` (broken links, dead quotes, orphans, admission contract, lapida-vs-vigente clashes, quarantine, save candidates, health per category) · `core/memoria_categorias.py` (10 canonical boxes; grouped INDEX) · `core/memoria_lecciones.py` (wb_gusto pattern: distills reasons → ≤20 rules in `lecciones-del-enjambre.md`, ALWAYS injected between `JARVIS_LECCIONES_*` markers) · `core/memoria_global.py` (environment seed for new projects) · `core/memoria_endurecimiento.py` (lesson recurrence → deterministic save candidates). Frontmatter states: `vigente|obsoleta|lapida|archivo`; the protocol carries the authority hierarchy (code > CLAUDE.md > lápida > newest > verify). The janitor (30 min) regenerates INDEX, distills lessons and evaluates recurrences.
- **CLI accounts** (`core/cli_accounts.py` + `cli_login.py` + `cuenta_watch.py`, ⚙→Accounts): several accounts per CLI (**claude/codex/grok/qwen/opencode/antigravity**) and instant switch without re-logging in. Secrets 0600 in `data/cli-accounts/<id>/` (never in DB or git); Codex uses isolated homes (`CODEX_HOME` + symlink) to not trigger OpenAI's revocation. **Auto-rotation** (`AUTO_ROTACION`, default ON): agent_watch detects the rate-limit signature in the pane and rotates to the next healthy account alone (WS `cuenta_rotada`/`limite_sin_cuenta`, 10 min cooldown); the manual switch coexists. **Detection**: Claude = `~/.claude/.credentials.json` (`claudeAiOauth`) + `~/.claude.json`, honoring `CLAUDE_CONFIG_DIR`; ⚙→Accounts re-reads every 4 s while open and has a «Diagnosticar» button (`GET /api/cuentas/diagnostico/{tipo}`: paths, key NAMES, hints — never token values). **Subscription usage** (`core/uso_suscripcion.py`, `GET /api/cuentas/uso`, bars in ⚙→Accounts via `sections/settings/uso-cuentas.js`): what's left of each claude/codex account's 5 h / weekly windows — Claude from `api.anthropic.com/api/oauth/usage`, Codex from `chatgpt.com/backend-api/wham/usage` with fallback to the CLI's last session rollout; read-only (NEVER refreshes a token), 60 s cache, other CLIs = `no_soportado`.
- **Coordination identity:** terminal names are UNIQUE per project (`resolver_nombre_unico`, terminals.py) — the 1-to-1 mailbox and Agents Live ownership depend on that.

### Other backend modules
- `core/pane_capture.py` — SHARED capture of tmux panes with 0.8s TTL cache (120 lines); dedupes the `tmux capture-pane` of agent_watch/agent_live/dev_detect. The keyword monitor of `terminals.py` stays APART on purpose (don't touch its capture).
- `core/skills_ia.py` — read-only detector of the skills/commands/agents/rules EVERY AI tool reads in a project (Claude, Codex, Gemini, Antigravity, Cursor, Qwen, OpenCode, Copilot, Windsurf, Cline, Roo): feeds Settings → Extensions via `GET /api/projects/{id}/skills/detectadas`. Only a ≤160-char description leaves it; never home-file contents.
- `core/idioma_ui.py` — the UI language (es/en) as the server knows it (reported by presence + each chat request, persisted in `data/ui-lang`); `L(es, en)` localizes text the server writes for the user (chat notices, orchestrator reply language).
- `core/logs.py` — swarm audit trail in JSON-lines (`data/jarvis.log`, rotates at 5MB). `core/mantenimiento.py` — janitor: purges `.workspace/logs` every 30 min + old `task_events` at boot.
- Routers without their own frontend section: `voice.py` (STT via worker process `core/stt_proc.py` — serialized, one inference at a time — + TTS edge-tts + `/api/voice/translate`), `plugins.py` (plugins/skills per project, table `project_skills`), `live.py` (Agents Live snapshot), `projects_files.py` (Monaco editor backend).

### How work actually happens here

**The main path: the user opens terminals and pastes them the task.** Everything that
protects the swarm hangs off the TERMINAL, so it always works: provenance by hook,
territory, commit by hunk, collision notices and the `jv` CLI. **Jarvis mode** (the
orchestrator chat) rides the same rails: it launches agents with `spawn_terminal` +
`tarea` (or `enviar_prompt` to a free one) and each one closes via the sentinel.

### WebSocket events (emitted by the broadcaster in `plotspace/core/events.py`)
`hola` (handshake, carries `boot_id`) · `task_event` · `agentes_update` (Jarvis wrote a terminal's origin/tarea → Tasks live monitor) · `orquestador_mensaje` · `agente_termino/espera/trabajando` + `cuenta_rotada`/`limite_sin_cuenta` (agent_watch) · `dev_server_detectado/caido` · `conflicto_archivo`/`live_update`/`permiso_*` (Agents Live) · `mailbox_aviso` · `cuentas_update`/`cuenta_agregada`/`cuenta_watch_timeout` · `frontend_actualizado`/`codigo_commiteado` (fe_watch). The list grows — grep `broadcaster.broadcast(`. Any backend module can subscribe to EVERYTHING with `broadcaster.escuchar(cb)`.

### Startup (`plotspace/main.py`, lifespan)
STT model on demand (preload only with `WHISPER_PRELOAD=on`) → `reconciliar_sesiones_tmux()` → purge task_events → asyncio pollers: STATE.md 10s · mailbox · dev_detect 2s · agent_watch 1s · agent_live 2s · fe_watch 2s · sentinel 2s · log purge 30 min.

### In-app updater and automatic versioning (`routers/system.py` + `sections/panel/updater.js`)
- **`hay_update` = there is a NEW COMMIT since boot** (HEAD moved). Uncommitted edits don't light it. "Update now" banner at the strip bottom; **it does NOT hide while agents work** (`agentes_trabajando` from `/version` is informational, scope = Jarvis project).
- **Who updates: THE USER** (click) or Jarvis itself — agents verify with pytest + import smoke and do NOT restart the server.
- **Automatic versioning** (`VERSION`, format `x.x.xx`): patch +1 per update; if ALL new commits are `fix:` → hotfix (4th segment). **Canary:** `/api/system/restart` imports `plotspace.main` in a subprocess BEFORE the `os.execv`; if the new code doesn't start it answers 409 (modal with traceback) and the old server stays intact; canary OK → bump + re-exec in place (same PID).
- **fe_watch** (`core/fe_watch.py`): the browser reloads itself when the server restarts (the `boot_id` changes) or on editing `frontend/**` (reload held while agents work); a new commit emits `codigo_commiteado` → re-check of the banner. If the server ended up on uvloop the "Optimize typing" banner appears (restart with `--loop asyncio`).

### Circular import: `orchestrator.py` ↔ `terminals.py`
The keyword monitor in `terminals.py` needs to call `orchestrator.py` and vice versa. **Solution**: lazy imports inside the function body:
```python
from plotspace.routers.orchestrator import procesar_task_event_interno
```

## Critical implementation rules

**subprocess.run vs asyncio for tmux/git:**
`asyncio.create_subprocess_exec` with `tmux new-session -d` hangs indefinitely (inherits FDs and `communicate()` never returns). **Always use synchronous `subprocess.run` for control tmux and git commands.** Only exception: `_capture_tmux_output()` in `terminals.py` (long polling, yields the event loop).

**Keyword detection (false positives):**
The monitor distinguishes the TASK_DONE Jarvis sent as instruction from the agent's real TASK_DONE with 3 layers: `_ANSI_RE` (strips ANSI) → `_KW_SOLO_RE` (`^[^a-zA-Z]*TASK_DONE[^a-zA-Z]*$`, no letters around) → baseline at monitor start (ignores the pane's prior history). The sentinel-file is the primary closure source today; this stays as fallback.

**TTS:** mutex `ttsActivo` in `workspace.js`; voice only at the welcome (plus the ones the user triggers).

**Cache busting:** when changing JS or CSS, increase `?v=N` in the HTML's `<script src>` and `<link rel>`.

**Paths:** always use `os.path.join()`. The `ANTHROPIC_API_KEY` is excluded from the terminals' PTY environment so agents use their own credentials.

## tmux / terminal engine

- Session per terminal: `jarvis_{terminal_id}`. Create: `subprocess.run(['tmux','new-session','-d',...])` + `mouse on`, `focus-events off`, `window-size latest`, `status off` (and `CODEX_HOME` of the active account for Codex).
- **Terminal engine: tmux, the ONLY one**: **tmux control-mode** with **xterm.js as the only emulator**. The selection lives ONLY in `terminal_backend.backend()` — today it always returns `TmuxBackend`; the indirection stays as test seam (`set_backend`) and any gate derives from there. Sessions SURVIVE server restarts (tmux is another process). (The resume offer — WS close 4409 — and the shell-subselector subsystem were REMOVED: they only applied to the ephemeral engines of the Windows era.)
- **Control-mode client rules** (`core/control_mode.py`, the hot typing path): (1) EVERY command written to tmux's stdin yields one `%begin/%end` block — `_esperas` carries one entry per command (Future for `comando_con_respuesta`, `None` for fire-and-forget keystrokes/resize) and a block always consumes its entry, so a seed never receives another command's reply; (2) all stdin writes go through `_escribir`: a big paste leaves on a thread and anything else queues behind it (interleaving split `send-keys` lines → lost paste chunks); (3) targets are EXACT (`=jarvis_N:` / `=jarvis_N`) — a bare name resolves by prefix when the session is dead and types into `jarvis_N0..9`. **Local echo** (`terminal-eco-local.js`, shells only) predicts a key ONLY at the end of the line with the stream caught up, never in alt-screen/mouse apps or after a secret prompt, and an unconfirmed prediction is erased after 300 ms (the predictor then suspends until a real echo returns — passwords must never stay painted). **Paste** wraps in bracketed-paste markers only when xterm saw the app enable mode 2004 (unknown after a re-attach → wrap, as before).
- ✕ on the card = the single removal point in the UI: `DELETE /api/terminals/{id}` → `teardown_terminal()` (kill tmux + `activa=0`), reused by the orchestrator (`close_terminal`/`close_all`) and project deletion. The "disconnect without killing" stays internal for project switching.
- **Browser QA: ALWAYS `?qa=1`** (read-only observer mode that doesn't steal or resize the user's attach).

## Project navigation

Without reloading the page: disconnect the terminal WSs → `history.pushState` → `cargarProyecto()`. On project change, `onProjectChanged(projectId)` is notified to JarvisEditor, TerminalLayout, JarvisTasks, JarvisMemory, JarvisReview and JarvisDock (the last restores the target project's persisted state/tab).

## Anti-leak lock

API keys / tokens (Anthropic, MCPs, any provider) NEVER go to the repo. `scripts/scan_secretos.py` (pure stdlib) detects provider formats **and the real values** of the local secrets (token, `.env`, snapshots of `data/cli-accounts/`); the versioned hooks of `.githooks/` run it: pre-commit (staged) and pre-push (the WHOLE outgoing range — catches even what was committed with `--no-verify`). Activation post-clone: `bash scripts/setup-hooks.sh`. NEVER bypass it with real secrets. In tests, fake secrets are built by concatenation (`'sk-ant-' + ...`) so they don't auto-trigger.

## Environment variables

`plotspace/.env` — `ANTHROPIC_API_KEY` (never commit this file). Flags (default in parentheses):
- **Swarm:** `SENTINEL` (on) · `AUTO_ROTACION` (on) · `MAILBOX_ENTREGA_TERMINAL` (off) · `ORQUESTADOR_MOTOR` (`suscripcion` — the orchestrator chat runs with claude -p and the active OAuth account, $0 of API; `api` = escape hatch with key) · `ORQUESTADOR_MODEL` (`sonnet` on subscription / `claude-haiku-4-5` on api) · `ORQ_CLI_TIMEOUT` (240s wall per orchestrator call) · `MEMORIA_LECCIONES` (on — lesson distiller; `MEMORIA_LECCIONES_MODEL` `claude-haiku-4-5` · `MEMORIA_LECCIONES_UMBRAL` 6) · `MEMORIA_CUARENTENA_DIAS` (60 — active memory without refresh or use → quarantine in health)
- **Terminals:** `TERMINALES_ARRANQUE` (`shell` — on creating an AI terminal the pane is born as a visible WSL shell and the CLI is typed short (`claude`) when the prompt appears; `limpio` = the CLI starts as the pane's program, with nothing shown. Jarvis-launched agents, resumes and qwen ALWAYS go in clean mode)
- **Voice:** `STT_WORKER` (`on` — the STT model loads and runs in a WORKER process with nice(5), `core/stt_proc.py`; `off` = old in-proc, which freezes the event loop 5-20s from onnxruntime's GIL when creating the session) · `STT_MOTOR` (`parakeet` code default; `groq` = whisper-large-v3-turbo remote on Groq, near-zero local cost, fallback to parakeet — requires `GROQ_API_KEY`; `whisper` = escape hatch) · `GROQ_API_KEY` (free-tier Groq key — ONLY in `plotspace/.env`, never to the repo) · `GROQ_STT_MODEL` (`whisper-large-v3-turbo`; `whisper-large-v3` = more quality, slower) · `WHISPER_MODEL` (`small` — NOT `turbo`: 3.2GB RAM and ~48s per dictation on a modest laptop) · `WHISPER_COMPUTE` (`float32` — int8 via CTranslate2 is SLOWER on CPUs without VNNI; onnx/parakeet int8 doesn't suffer that) · `WHISPER_PRELOAD` (`off`; `on` = resident preload at startup as before) · `WHISPER_IDLE_UNLOAD` (`600` s idle before unloading the model/killing the worker — applies to the active engine; `0`/`off` doesn't unload)
- **Infra:** `JARVIS_ALLOWED_HOSTS` · `JARVIS_HOST` / `JARVIS_PORT` · `JARVIS_SELECTOR_NATIVO` (`off` = never open the OS folder dialog; use Jarvis' own browser) · `AUTO_PUSH` (on — fe_watch pushes `master` to origin when it detects commits; it's the automatic backup, don't push by hand)

<!-- JARVIS_SKILLS_START -->
## INSTRUCTION OBLIGATORIA

Before answering anything about your configuration, active plugins, skills or project state:
1. Read this CLAUDE.md file completely
2. Base your answer ONLY on what this file says
3. Do NOT use memory from previous conversations for this
ntes de responder cualquier pregunta sobre tu configuración, plugins activos, skills, o estado del proyecto:
1. Leer este archivo CLAUDE.md completo
2. Basar tu respuesta ÚNICAMENTE en lo que dice este archivo
3. NO usar memoria de conversaciones anteriores para esto

## Skills y plugins activos del proyecto

### 🔌 Plugins activos

- **ui-ux-pro-max**
- **superpowers**
- **static-analysis** — Static analysis toolkit with CodeQL, Semgrep, and SARIF parsing for security vulnerability detection
- **frontend-design** — Frontend design skill for UI/UX implementation
- **expo**
- **context7**

### 📋 Skills del proyecto

#### qa-browser-jarvis

Skill qa-browser-jarvis — ver `.claude/skills/qa-browser-jarvis.md` (cómo verificar en browser + correr los tests)
This section is regenerated by your editor's plugin system from the repo's local plugin/skill state. Don't edit between markers.
<!-- JARVIS_SKILLS_END -->

<!-- JARVIS_MEMORY_START -->
## 🧠 Shared project memory (Jarvis)

This project has a shared memory between ALL agents in `.jarvis/memory/`. **Before starting any task**: search `.jarvis/memory/INDEX.md` for the memories that touch your task and open them. Do NOT read the whole INDEX (it's ~7K tokens and grows): GREP it for your topics — `grep -i "terminal\|xterm" .jarvis/memory/INDEX.md` — or read only the category section (it's grouped; each line carries title, #tags and date). Reading it all is only worth it for a cross-cutting task.

When you discover something another agent should know (architecture decision, gotcha, convention, recurring bug, how something is run), **save it immediately**:

1. Create `.jarvis/memory/<kebab-case-slug>.md` with this exact format:

   ```markdown
   ---
   titulo: Short, specific title (NO date — this is not a log)
   tags: [topic1, topic2]
   categoria: <one of the list below>
   resumen: the fact in ONE line — this is what recall injects into other prompts
   creado: YYYY-MM-DD
   actualizado: YYYY-MM-DD
   autor: your agent name
   estado: vigente
   ---

   Concise content. Link related memories as [[other-slug]].
   ```

   CATEGORIES (pick ONE — the box where the memory lives):
   `terminales` (Terminals & tmux) · `ui` (UI · Workspace) · `swarm` (Backend & Swarm) · `diseno` (Design & Craft) · `preview` (Web Preview & Radio) · `cuentas` (Accounts & CLIs) · `voz` (Voice & Audio) · `desktop` (Desktop) · `entorno` (Environment · WSL & Git) · `producto` (Product & Roadmap). If unsure, Jarvis infers it from the tags; a memory that fits none is flagged `sin-clasificar` in the health check.

2. The INDEX (`.jarvis/memory/INDEX.md`) is regenerated by Jarvis alone, GROUPED by category and enriched — don't edit it by hand.

GOLDEN RULES (enforced by the pre-commit — guard_memoria blocks incomplete frontmatter, 150+ line reports and titles with dates):
- **One memory = ONE actionable fact** (~10-60 lines). A giant report drowns the context of whoever opens it: distill the conclusion, keep it short.
- **RECONCILE before creating**: check the INDEX whether a memory on this topic already exists. If it does, UPDATE THAT ONE (add your finding, bump `actualizado:`) — two memories on the same topic confuse more than one. Creating a new one is the option ONLY when the fact is genuinely new.
- **If a feature/code was removed**, do NOT delete its memory: set `estado: lapida` and say what replaced it (stops someone from reintroducing it). Old memory you can't verify: `estado: obsoleta`.
- **Link ONLY slugs from this folder** — no personal memories from your CLI (those links are born broken for the rest of the swarm).
- **Write a lesson** (`tags: [leccion]`) when an error — yours or someone else's — can be prevented with a short rule: what happened, why, how to avoid it.
- Memories belong to the PROJECT, not you: write so any future agent understands without prior context.

AUTHORITY HIERARCHY (when two sources clash, resolve in this order):
1. The **current code** wins over any memory.
2. The **CLAUDE.md/AGENTS.md** wins over the memories.
3. A **lápida** (`estado: lapida`) wins over a vigente one in its topic.
4. The **most recently updated** wins over an older one.
5. Still ambiguous? **Verify against the code — don't guess.**
And if a memory **lied to you** (describes something that's no longer true), fixing it or marking it `estado: obsoleta` is PART of your task — the clash you skip, the next agent eats it.

TASK CLOSURE — when finishing ANY task, signal closure; it's the telemetry this memory learns from (what worked, what failed). Run:

    TID=${JARVIS_TERMINAL_ID:-$(tmux display-message -p '#S' 2>/dev/null | sed 's/^jarvis_//')} && mkdir -p .jarvis/signals && printf '%s' '{"estado":"done","motivo":"","memorias_usadas":[]}' > .jarvis/signals/terminal_${TID}.json

In `memorias_usadas` list the slugs from `.jarvis/memory/` you read and used ([] if none). If you end up `blocked`/`error`, `motivo` is MANDATORY and concrete; in `done` it's optional (one line with the non-obvious approach that worked). When Jarvis launched you with a task, the instruction already carries your id — no need to figure it out then.
<!-- JARVIS_MEMORY_END -->

<!-- JARVIS_MAILBOX_START -->
## 📬 Agent mailbox (Jarvis)

To tell ANOTHER agent in this workspace something (you changed an interface it uses, a bug in its area), add ONE line at the end of `.jarvis/MAILBOX.md` with this exact format:

    - @YourName -> @OtherName: short, actionable message

The mailbox is 1-to-1: 1 line = 1 CONCRETE recipient, with the EXACT name of its terminal (your name is your task/terminal name, e.g. "Backend"). There is NO broadcast: messages to "everyone" reach no one. Zero idle chatter — no announcing progress, thanking or asking others to test: what you want verified, verify it yourself in your terminal. To read, read YOUR messages with `.jarvis/jv inbox` — NEVER re-read `.jarvis/MAILBOX.md` whole (the inbox gives you only yours, unread markers).
<!-- JARVIS_MAILBOX_END -->

<!-- JARVIS_PUERTOS_START -->
## 🔌 Port rule (Jarvis)

**Port 3000 is FORBIDDEN**: that's where Jarvis Workspace runs (the dashboard orchestrating you). Running anything on 3000 breaks it.

Before running ANY server (dev server, API, preview, http.server):

1. See which ports are already in use:

       ss -tlnp 2>/dev/null || lsof -iTCP -sTCP:LISTEN -P -n

2. Pick a FREE port that doesn't clash with any in use (for dev servers use the 5000-5999 or 8081-8999 range if free).
3. Pass the port explicitly to the command (`--port`, `-p`, `PORT=`); don't trust the tool's default.

NEVER kill a process on a port you didn't start: it can be Jarvis, another agent or a preview in use.
<!-- JARVIS_PUERTOS_END -->

<!-- JARVIS_LIVE_START -->
## 🔴 Swarm coordination — `.jarvis/jv`

You work with other agents on the SAME tree. Don't read state files to find out: ask when you need to.

    .jarvis/jv estado      what the others are touching and what arrived for you
    .jarvis/jv inbox       your new messages (do NOT read .jarvis/MAILBOX.md whole)
    .jarvis/jv msg "<agent>" "<text>"    leave a notice (does NOT interrupt)
    .jarvis/jv ask "<agent>" "<question>"  ask and WAIT for the answer
    .jarvis/jv claim "<symbol|file|folder>"  reserve YOUR zone
    .jarvis/jv commit -m "<message>"      commit only YOUR work, by hunk

The rules that matter:

1. **Claim your zone before starting**: `jv claim` on the functions, ids or files you're about to touch. Claim by NAME, never by line number (lines move). Whatever nobody claimed is granted on the spot; if you need more on the fly, claim it too.
2. **Never rewrite a whole file** that isn't yours (`Write` over something existing). Edit by zone: two agents in different zones of the same file coexist fine, but a full overwrite with your stale copy erases the other's work without a trace. Jarvis stops you if it happens.
3. **Don't delete or rename what another agent claimed.** Jarvis stops you before writing, naming the owner. If it really must go, leave them a `jv msg` and let them adapt it — yanking it out breaks their code without them knowing. (Using their function is fine; nobody stops you.)
4. **Commit with `jv commit`**: stages only your stuff, hunk by hunk. Bare `git add` sweeps in the other's uncommitted work that lives in the same file.
5. **`msg` leaves the notice; `ask` is what INTERRUPTS.** A `jv msg` lands in the other's inbox and they pick it up when they resume: it does NOT wake them (if they already closed their task, they keep resting). If you need a reaction NOW use `jv ask`, and if you're handing off work start the message with `HANDOFF` — those two do wake them. This exists because most messages landed on agents whose task was already closed, burning a whole turn for nothing. And the recipient is **another terminal, by its EXACT name**: writing to `@jarvis` or "the system" reaches NOBODY.
6. **A dead agent 💀 is not coming back.** If `jv estado` marks one like that, its territory is free and the guard won't block you on its files: don't ask its permission nor wait for it. And a **⚠ Inheritance** you see is someone's uncommitted work from an agent that left — nobody's coming for it: if you touch one of those files, commit it yourself with a message saying what it is.
7. **Commit before closing your task.** Real finished work left uncommitted in this tree is work another agent sweeps or inherits. What does NOT get committed: localhost trials, mockups, screenshots and build artifacts — those go to `.gitignore`, not to a commit.
8. **Your task is YOUR task — don't glom onto someone else's.** You verify YOUR work; theirs only if its owner asks you to or your task depends on it, and once. Acks (OK/thanks/received/"verified, all good") get NO reply: each message burns the other a whole turn. Cap: 2 of your messages per thread with the same agent on the same subject — after that decide alone with what you have, and if the disagreement matters leave it in a memory. The mailbox is not a chat: most of its traffic ends up being cross-checks and courtesy — one message per fact, and it's done, or it goes to a memory.
9. **Never wait for someone else's commit.** Intertwined uncommitted in the same file? `jv commit` stages ONLY your hunks (uses real provenance): commit NOW and continue. Asking «commit first and tell me» waits an average of an HOUR, when the tool solves it alone.

`.jarvis/LIVE.md` still exists (who owns what, permissions, reservations) if you want the detail, but `jv estado` gives you what you need.
<!-- JARVIS_LIVE_END -->

<!-- JARVIS_LECCIONES_START -->
## 📚 Swarm lessons (always loaded — from real failures and findings)


This block is generated by the app from your local swarm memory (`.jarvis/memory/lecciones-del-enjambre.md`): it distills the mistakes and findings of *your own* agents in ≤20 short rules, and refreshes on every session. Don't edit between markers — write a `[leccion]` memory instead.<!-- JARVIS_LECCIONES_END -->
