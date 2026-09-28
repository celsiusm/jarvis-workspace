# sections/settings/ — settings screen

Full-screen overlay covering the workspace when the user taps ⚙ (`#jw-gear`).
**"Liquid Glass · Datasheet"** redesign, which replaced the "Observatory": that one had changed the
*theme* over the same generic structure (side nav + identical rows with toggle); this one changes the
**structure**.

- **Files:** `settings.js`, `settings.css`
- **Served at:** `/static/sections/settings/`
- **Public global:** `window.JarvisSettings` with `init(pid)`, `onProjectChanged(pid)`,
  `open(seccion?)`, `close()`, `isOpen()`, `refrescar(seccion?)` and
  `onCuentaAgregada(data)`. Invoke only via this API.

## Anatomy

One **glass slab** (`.sx-slab`, ANCHORED frame 1120×790: doesn't change size between sections)
with the side rail and the body joined by a hairline. The body is a **data sheet**: each block
(`.sx-blk`) announces itself with its **band** (`.sx-blk-l`: mono label + track + rule to the
edge) and then the content takes the FULL width. Zero nested cards, zero grid of identical cards.

The band is **sticky**: when scrolling it stays pinned on top and `settings.js` puts `.fija`
(measured against the scrollport ceiling in an rAF), which lights the shelf and lifts the label
to `--ob-fg-0` — the "you are here" of the section. The `{ wide: true }` flag of `blk()` does
nothing anymore (it was for the side channel removed in the redesign); no need to pass it on
new blocks.

| Section | id | Shape |
|---|---|---|
| Voice | `voz` | The key as a physical object (keycap) + signal path + dictation & alerts |
| Keyboard | `atajos` | **Real keyboard map**: lit = occupied, click to reassign |
| Appearance | `apariencia` | Live test bench + the 24 themes as **spectrum** + tonality + language |
| Accounts | `cuentas` | **Switchboard**: one CLI per row, its accounts as buttons of a selector |
| Extensions | `skills` | Extensions studio (`extensions.js` + `extensions.css`): readers map per AI, skills/rules of every AI, Claude plugins + marketplace, side drawer |
| Memory | `memoria` | **Console**: pulse, altimeter, boxes per category, recent, lessons |
| Workflows | `workflows` | Timeline with the step track |

The rail shows the **live value** of each section (the key, the theme, how many
accounts / plugins / memories / workflows) — you see the config without entering.
The four server-dependent ones are fetched by `_cargarResumen()` on open.

## Hard rules

- **Max TWO layers with `backdrop-filter`** (veil + slab). Everything inside is
  faux-glass with gradients.
- **Glass RAISES, doesn't sink**: surfaces come off the card step
  (`--ob-bg-2/3`, local tokens `--sx-slab-*`, `--sx-well`), **never from
  `--ob-bg-void`** — on the darkest themes (ink L≈9%, neon) that left a black hole
  and reading tired.
- **`--ob-fg-4` is only disabled/placeholder**: no text on this screen goes below `--ob-fg-3`.
- **No hardcoded color** (24 themes + tonality filter).
- **No `animation-fill-mode: forwards`/`both` on the band's ancestors**:
  a retained transform (even identity matrix) breaks `position: sticky` inside.
  That's why `.sx-in > *` uses `backwards`.
- **The theme spectrum doesn't animate layout on hover**: hover = preview on the bench
  (`--bk-*` inline vars; NOT touching the document's `data-theme`, that would change the
  theme for real); the grow (`flex-grow`) is the **click** response.
  `contain: layout paint style` encloses the recalc.
- **No serif italic display** in product UI.

## Bridges with the real engine (don't rename)

- The Voice keycap and reassign buttons **are `.set-keybind[data-id]`**: the
  capture engine in `workspace.js` looks them up by that class and rewrites their
  `innerHTML` while capturing (`.settings-keybind-listening` / `-hint`). The value
  lives in `.settings-kbd`.
- Extensions is its own module: `settings.js` calls
  `window.JarvisExtensiones.montar(el, {projectId, onActivos})` and `desmontar()`
  on section change/close. Data: `GET /api/projects/{id}/skills/detectadas`
  (`plotspace/core/skills_ia.py` — skills, commands, agents and rules of Claude,
  Codex, Gemini, Antigravity, Cursor, Qwen, OpenCode, Copilot, Windsurf, Cline,
  Roo; only a ≤160-char description leaves the backend, never home-file
  contents) + the existing plugins/marketplace endpoints. Only Claude project
  skills are editable; other AIs' files are read-only (open in the editor).
- The account-linking modal (`.cta-alta-*`) keeps its DOM and its **4 flows**
  (manual, device-code, paste code, callback) + abort on close.
- Esc yields to sub-modals (the Extensions drawer `.ex-drawer.open`, `.cta-alta-overlay`,
  `.ob-confirm-overlay`) and to the search box with text.

## i18n

New texts live in `shared/i18n-dict.js` (block "Configuración · Liquid Glass redesign").
Every new string of this screen goes there or English mode gets mixed.

## Verification

Smoke at `localhost:3000` with `?qa=1` (observer): open with ⚙, walk all 7 sections,
search a setting and verify the jump+pulse, change theme and tonality
(that computed `--ob-accent` changes and persists), reassign a shortcut. **Don't touch
"Usar" of an account in QA: it changes the CLI's active account for real.** Bump the
`?v=N` of `settings.js`/`.css` (and `i18n-dict.js` if touched) in
`shell/workspace.html`. Tests: all `frontend/**/__tests__` suites +
`python -m pytest plotspace/tests/`.
