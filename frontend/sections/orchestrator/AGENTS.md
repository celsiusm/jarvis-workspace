# sections/orchestrator/ — orchestrator panel

UI of the orchestrator chat (messages, state, mic). The **logic** for sending,
recording and TTS lives in the shell (`workspace.js`) via callbacks `window._orchOn*`.

- **Files:** `orchestrator.js`, `orchestrator.css`
- **Served at:** `/static/sections/orchestrator/`
- **Public global:** `window.jarvisPanel` (the panel instance). The shell adds
  messages and changes state via the instance directly
  (`jarvisPanel.addMessage(...)` / `jarvisPanel.setSphereState(state)`).
- **Consumes from the shell:** `window._orchOnSend`, `_orchOnMicHold`, `_orchOnMicRelease`,
  `_orchGetFiles`, `_orchOnHeaderAction` (defined in `workspace.js`). Don't reimplement
  network/voice logic here: this is UI only.

## Orchestrator chrome (CONSTELLATION redesign)
Jarvis is the **central node of a living neural network** that reacts to voice.
- **Constellation** (`<canvas class="orch-net">`, panel background): ~62 fixed-position nodes
  (deterministic PRNG) joined by edges; the center pulses. When listening, radial waves cross
  the network and light up nodes, and the network shifts to cyan. Real voice reactivity comes
  from `window._orchVoiceLevel` + `_orchVoiceBins` (0..64), **published by `workspace.js`** from
  the PTT's AnalyserNode (`_iniciarWaveform`). The rAF lives in `_initConstellation()`;
  respects `prefers-reduced-motion` and stops when the panel is hidden (`clientWidth===0`).
- **Header**: the **Jarvis** brand (orb + serif italic) is centered and prominent on TOP. No idle
  status chip, no loose history/new-session buttons — those live in the `⋯` menu
  (`onHeaderAction('new-thread'|'history'|'export'|'workflows'|'clear-history')`).
  State is communicated by the constellation color + the `.orch-orb` (`data-state` on
  `.orch-panel`: idle/listening/processing/responding). The **`⋯` menu and its options** are
  **LIQUID GLASS**: OPAQUE `background-color` base (NOT gradient `background-image`:
  with `backdrop-filter` it doesn't paint) + aurora + edge + gloss (`::before`) + bevel;
  item-hover = liquid glass lozenge. **`.orch-panel > header` carries `z-index:40`** so the open
  menu paints OVER messages/hero — before, they covered it (same `z-index:1`, later in the DOM,
  they won over the `z-index:300` menu trapped in the header context).
- **Hero** (empty state, `.orch-empty`): eyebrow "Agent network" + HUGE serif greeting
  "¿Qué hacemos, señor?" over the network + 3 **LIQUID GLASS** quick actions (`.orch-hero-chip`:
  translucent double layer + gloss `::before` + bevel; hover = accent edge + halo) (→ `onQuickReply`). The greeting scales
  with the panel WIDTH (`container-type: inline-size` + `cqw`; `max-width` in `cqw`, NOT `%`
  — the `%` stuck to the shrunk parent and split it). On the 1st message the greeting is
  replaced by the chat (`_syncConv()` toggles `data-conv` → dims the network for legibility).
- **The greeting IS the hero, NOT a bubble** (`_isHeroState()`): `workspace.js` injects the
  welcome greeting as a MESSAGE (`setMessages([{content:'¿Qué hacemos, señor?'}])` / `nuevoThread`). A
  chat that is ONLY that greeting renders as the HERO (bright constellation + giant greeting),
  until a REAL user turn arrives. Detects by `id` `'welcome-*'` or by content (EN).
  Without this the hero never showed in the real app (the welcome message always existed). The
  hero greeting IS translated by language (it's normal DOM, not a `data-i18n-skip` bubble).
- **Hero voice state** (`_updateHeroVoice`, toggles `.orch-empty[data-voice]`): with the hero
  visible, while LISTENING the greeting swaps to "Te estoy escuchando…" + live transcript
  (read from the `$textarea`, where `workspace.js` dumps partial STT — doesn't touch the voice
  pipeline); while PROCESSING → "Pensando". Reacts to `setSphereState`. The transcript echo runs
  in an rAF that stops itself if the node disconnects (hero → chat).
- **Telemetry + composer hints REMOVED** (2026-09-12, pedido del usuario): se quitó la fila
  `● RED · AGENTES · COSTO` (`.orch-telemetry`, `_refrescarUso`, `_updateRunningIndicator`) y la
  línea de atajos `.orch-hints` ("↵ enviar · ⇧↵ línea nueva · …"). No reintroducir sin pedido.
- **Composer** (ELITE redesign — no focus rectangle): bar INTEGRATED to the panel surface,
  NOT a box/pill. `.orch-input-box` flex row (attach/mention/slash icons ·
  `$textarea` · send) with no border or ring: focus is marked with an **accent hairline** that
  lights up center→outward (`.orch-input-box::after`, the signature motion), NEVER a rectangle
  around the field. **KEY:** `.orch-textarea:focus` explicitly kills the global keyboard
  focus-ring (`shared/tokens.css` `:where(...textarea...):focus-visible` draws a rectangular
  `box-shadow` = THE "rectangle" rejected as AI slop). The field shows no scrollbar
  (`scrollbar-width:none` + `::-webkit-scrollbar`): as text grows to `max-height`, the scroll
  follows the cursor WITHOUT a bar. NO language selector, no mic button (voice is global PTT).
  Partial STT cyan italic via `.orch-textarea.live-transcript`. Recording
  (`[data-recording]`) → the hairline turns cyan and breathes (no box). See the
  shared memory notes (`.jarvis/memory/`) for the background.
- **i18n ES⇆EN**: all chrome is written in Spanish and translated via `shared/i18n-dict.js`
  (the engine observes the DOM). Dynamic chat content carries `data-i18n-skip`.
- **Editorial chat** (fewer boxes): **Jarvis speaks WITHOUT a bubble-box**
  (`background:none`, raw text anchored by its glowing ORB-avatar + `text-shadow` for legibility
  over the network, which dims to `.30` with `data-conv`). User = subtle violet glass pill WITHOUT
  hard border (top inset bevel, not frame). Action-plan with gradient border (padding-box/border-box).
  The reason: rectangles/boxes are dismissed as AI slop — the chat removes boxes where it can.
- When adding state logic, use `setSphereState(state)` with the defined values.

## Responsive: side panel ↔ fullscreen
`.orch-panel` is a CONTAINER query (`container-type:inline-size`): the layout
blooms with the PANEL width, not the viewport, so it adapts equally narrow or
fullscreen. When touching the chrome, respect the tiers (`orchestrator.css`,
RESPONSIVE section at the end):
- **base (narrow, dock ~300-320px)**: compact chat — the default state, don't break it.
- **≥600cqw**: the conversation centers in a reading column (`.orch-messages > *`
  take `max-width` + `margin-inline:auto`) and the composer becomes a centered bar
  (its children take `max-width`). Without this, at full width bubbles stretch edge to
  edge and the composer leaves the placeholder at the left / button at the edge.
- **≥960cqw (fullscreen)**: full editorial (more air, pill-style input).
- The greeting scales fluid `clamp(2.05rem … 5.4rem)` by `cqw` (previously capped at 3.4rem).
- **Maximizable**: `panel.js` includes `'jarvis'` in `MAXIMIZABLE` → the ⤢ "Fullscreen"
  dock action appears on the Jarvis tab and expands it to `fixed inset:0`. The constellation
  (canvas) re-sizes itself via ResizeObserver.

## Verification
Manual smoke at `localhost:3000`: send a message, see the answer, orb state in header,
mic. Bump `?v=N` of `orchestrator.js`/`.css` in `shell/workspace.html`.
