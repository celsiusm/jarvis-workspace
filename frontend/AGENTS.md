# frontend/ — map for agents

Vanilla frontend (HTML/CSS/JS, no frameworks, no build). Organized **by section**:
everything of a section lives in its folder, with its `.js` and `.css` together.

```
frontend/
├── index.html          # home, served on GET /
├── shell/              # workspace shell (NOT a section)
│   ├── workspace.html  # served on GET /workspace
│   └── workspace.js    # "glue": boots everything, WebSockets, navigation, TTS
├── shared/
│   ├── tokens.css      # ★ CANONICAL DESIGN SYSTEM (--ob-*) + utilities — see below
│   ├── ui.js           # ★ global icon() / toast() / confirmar() / pedirTexto() / cliLogo()
│   ├── themes.js       # ★ theme system (html[data-theme], localStorage jarvis.theme)
│   ├── icons/          # vendored CLI logos (lobehub 1.91.0): claude, codex, gemini…
│   ├── base.css        # reset, base layout + shell/terminal styles
│   └── __tests__/themes.test.js  # Node suite for pure theme logic
└── sections/
    ├── home/           # landing (home.js + home.css)
    ├── terminals/      # terminal grid (terminal.js, terminal-layout.js)
    ├── orchestrator/   # orchestrator panel (orchestrator.js + .css)
    ├── editor/         # file editor Monaco (editor.js + .css)
    └── mobile-preview/ # Expo preview in a phone frame
```

## Serving (important)
All of `frontend/` is mounted at `/static`. A section is served under
`/static/sections/<name>/<file>`. When you change JS or CSS, **bump the `?v=N`**
of its `<link>`/`<script>` in `shell/workspace.html` (or `index.html` for home).

## "Obsidian Glass" design system (MANDATORY)

`shared/tokens.css` is the ONLY source of truth for tokens. Hard rules:

- **Colors**: ALWAYS `var(--ob-*)` (oklch). Never hex/oklch literals in chrome.
  Legacy names (`--bg-app`, `--sb-*`, `--hc-*`, `--violet`…) are compatibility aliases —
  don't use them in new code.
- **Color is SIGNAL, not decoration**: violet = life/accent (agent thinking,
  focus, CTA); green/amber/red = ONLY states; cyan = info/voice; **magenta =
  the Home aurora**. Glow comes from a 1px border or text, never from filled blocks.
- **Text**: floor `--ob-fg-2` for normal text (AA). `--ob-fg-3` only icons and
  big labels; `--ob-fg-4` only disabled/placeholder.
- **Typography**: `--font-ui` (Inter) UI · `--font-display` (Instrument Serif,
  ONLY italic ≥20px) editorial moments · `--font-mono` (JetBrains Mono) data;
  technical labels in mono UPPERCASE + `--ls-caps`. Sizes ONLY from the `--text-*` scale.
- **Performance (live xterm.js)**: `backdrop-filter` ONLY on plane-2
  (modals, floating menus, header), max 2 visible; FORBIDDEN in terminal containers.
  Infinite animations ONLY transform/opacity/box-shadow. Never animate panel width/height.
- **Forbidden**: emojis as icons (use `icon()` from shared/ui.js), native `alert()`/
  `confirm()`/`prompt()` (use `toast()`/`confirmar()`/`pedirTexto()`),
  sci-fi fonts, scanlines, clip-path on scrolling containers.
- **Utilities**: `.ob-glass` (plane-2 glass), `.ob-edge` (plane-1 gradient border),
  `.ob-pip[data-state]` (breathing state), `.skeleton`, `.ob-spinner`, `.ob-label`;
  motion via `--dur-1/2/3` + `--ease-out/snap`.

## Themes and logos

- **`shared/themes.js`** exposes `window.JarvisThemes` with `aplicar(nombre)`, `actual()`,
  `init()`, `TEMAS` (array), `normalizarTema`. `DEFAULT_TEMA = 'violeta'`. The theme is
  written to `html[data-theme=<name>]`; the violet default sets no attribute. Tested in
  `shared/__tests__/themes.test.js`.
- **`shared/icons/`** — CLI SVGs (lobehub 1.91.0): `claude-color.svg`, `codex-color.svg`,
  `gemini-color.svg`, `opencode.svg`, `qwen-color.svg`, etc. Add new CLIs here.
- **`window.cliLogo(tipo, size)`** in `shared/ui.js` — helper returning `<img>` of the logo
  (or text fallback if missing). Use anywhere a CLI type is shown (cards, launcher, quick picker).
- **Accent/backgrounds**: ONLY via `var(--ob-*)` in `tokens.css`, including theme overrides
  under `[data-theme=<name>]`. Never hardcode hex in new sections.

## Language (i18n ES⇆EN)

The UI is authored in Spanish; `shared/i18n.js` translates the DOM at runtime against
`shared/i18n-dict.js` (+ `JarvisI18n.agregar({...})` tables a module registers itself).
Every new visible text needs its entry — with English on, nothing may stay in Spanish.
- Text nodes and `title`/`placeholder`/`aria-label` are matched whole (whitespace
  collapsed). Dynamic texts use **templates**: `"hace {t}": "{t} ago"` matches
  "hace 5 min". Same markers on both sides (`shared/__tests__/i18n-dict.test.js` checks
  it). A template with <6 fixed chars only matches when every captured value has a digit
  (so "hace {t}" never rewrites user text like "hace falta…").
- The engine never sees: xterm content (`term.write`), `.monaco-editor`, textarea
  content, canvas, `[data-i18n-skip]`, `document.title`, OS notifications, TTS, text
  sent to the backend. There, call `JarvisI18n.t('frase {x}').replace('{x}', v)`.
- Text born on the SERVER and shown as-is (chat notices, orchestrator
  reply) is localized there with `plotspace.core.idioma_ui.L(es, en)`; the frontend
  reports the UI language via presence and each chat request. Plain error `detail`s
  just need a dict entry.

## Rules
- No frameworks, npm or node as runtime dependency.
- Each section exposes one global and consumes only the public surface of the others
  (see the `AGENTS.md` of each folder). Don't touch another section's internals.
- `workspace.js` (shell) wires sections together; it's the only one that knows all of them.
