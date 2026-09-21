// JARVIS — Icono de cada plugin en ⚙ → Extensiones.
// Antes las 10 filas del rack mostraban el MISMO enchufe: un icono repetido no
// informa nada, sólo hace ruido. Acá el id/nombre del plugin elige un glifo del
// set de shared/ui.js, así la lista se escanea de un vistazo.
// Reglas ORDENADAS: gana la primera que matchea (la más específica va primero,
// p.ej. `lsp` antes que `analyzer`, `tdd` antes que `workflow`).
(function (global) {
  'use strict';

  const FALLBACK = 'plug';

  const REGLAS = [
    // Tooling de lenguaje / servidores LSP  → chip
    [/(^|[-_ ])lsp([-_ ]|$)|language[-_ ]?server/, 'cpu'],
    // Documentación y contexto externo (MCP)  → globo
    [/context7|(^|[-_ ])mcp([-_ ]|$)|docs?([-_ ]|$)|documentation|reference/, 'globe'],
    // Móvil
    [/expo|react[-_ ]?native|mobile|android|(^|[-_ ])ios([-_ ]|$)|flutter/, 'phone'],
    // Git / forjas
    [/github|gitlab|(^|[-_ ])git([-_ ]|$)|pull[-_ ]?request|commit/, 'git-branch'],
    // Seguridad y análisis estático
    [/security|vulnerab|semgrep|codeql|sast|static[-_ ]?analysis|(^|[-_ ])scan/, 'eye'],
    // Tests
    [/(^|[-_ ])tdd([-_ ]|$)|test|spec([-_ ]|$)|jest|pytest|vitest|cypress/, 'list-checks'],
    // Revisión / auditoría de código
    [/review|audit|critique|inspect|lint/, 'search'],
    // Diseño / frontend
    [/(^|[-_ ])ui([-_ ]|$)|(^|[-_ ])ux([-_ ]|$)|design|frontend|css|theme|style/, 'sparkles'],
    // Andamiaje de skills / generadores
    [/skill|scaffold|generator|creator|template|boilerplate/, 'edit'],
    // Orquestación multi-agente
    [/superpower|orchestr|workflow|swarm|(^|[-_ ])agents?([-_ ]|$)/, 'zap'],
    // Memoria / conocimiento
    [/memor|brain|knowledge|recall|rag([-_ ]|$)/, 'brain'],
    // Voz
    [/voice|audio|speech|whisper|(^|[-_ ])tts([-_ ]|$)|(^|[-_ ])stt([-_ ]|$)/, 'mic'],
    // Credenciales / auth
    [/auth|oauth|login|credential|secret|token|(^|[-_ ])jwt([-_ ]|$)|password/, 'key'],
    // Accesibilidad (navegación por teclado, lectores de pantalla)
    [/accessib|a11y|wcag|screen[-_ ]?reader/, 'keyboard'],
    // Chat / bots
    [/chat|message|slack|discord|telegram|(^|[-_ ])bot([-_ ]|$)/, 'message'],
    // Deploy / CI
    [/deploy|docker|kubernetes|(^|[-_ ])ci([-_ ]|$)|pipeline|release|terraform/, 'workflow'],
    // Performance (va ANTES que datos: «data pipeline perf» es perf)
    [/performance|profil|optimi|latency|benchmark|caching/, 'chart'],
    // Datos
    [/database|(^|[-_ ])sql|postgres|sqlite|mongo|(^|[-_ ])data([-_ ]|$)/, 'chart'],
    // Browser / scraping
    [/browser|playwright|puppeteer|selenium|chromium|scrap/, 'monitor'],
  ];

  /**
   * iconoDePlugin('static-analysis@claude-plugins-official', 'Static Analysis')
   *   → 'eye'
   * Matchea SOLO contra id + nombre (la descripción es prosa larga y dispara
   * falsos positivos). Sin match → 'plug', el enchufe genérico de siempre.
   *
   * OJO con el `@`: el full_id es `<plugin>@<marketplace>` y el marketplace
   * NO describe al plugin. Con el sufijo adentro, las 174 cards de
   * `@claude-code-workflows` matcheaban "workflow" y salían TODAS con el rayo
   * de orquestación (Accessibility Compliance, Arm Cortex Microcontrollers…).
   * Se corta en el `@` antes de buscar.
   */
  function iconoDePlugin(fullId, nombre) {
    const id   = String(fullId || '').split('@')[0];
    const heno = `${id} ${nombre || ''}`.toLowerCase();
    if (!heno.trim()) return FALLBACK;
    for (const [re, ico] of REGLAS) if (re.test(heno)) return ico;
    return FALLBACK;
  }

  // ── Tono del chip (paleta .tone-* de shared/base.css) ──────────────────
  // El glifo dice QUÉ es el plugin; el tono dice DE QUÉ FAMILIA es. Misma
  // señal, otra capa: así el rack deja de ser una fila de chips grises y se
  // escanea por color + forma. El tono se DERIVA del glifo (una sola fuente de
  // verdad: las reglas de arriba), no de un segundo set de regex que se
  // desincronice. Todo sale de los tokens `--ic-bg/--ic-fg/--ic-edge`, así que
  // sobrevive a los 24 temas y al filtro de tonalidad (nada de hex clavados).
  const TONO_POR_GLIFO = {
    'cpu':         'tone-blue',    // tooling de lenguaje / LSP
    'globe':       'tone-cyan',    // docs / MCP
    'phone':       'tone-teal',    // móvil
    'git-branch':  'tone-green',   // git / forjas
    'eye':         'tone-rose',    // seguridad / análisis
    'list-checks': 'tone-green',   // tests
    'search':      'tone-blue',    // review / auditoría
    'sparkles':    'tone-violet',  // diseño / frontend
    'edit':        'tone-amber',   // skills / generadores
    'zap':         'tone-amber',   // orquestación
    'brain':       'tone-violet',  // memoria
    'mic':         'tone-rose',    // voz
    'key':         'tone-amber',   // credenciales / auth
    'keyboard':    'tone-blue',    // accesibilidad
    'message':     'tone-cyan',    // chat / bots
    'workflow':    'tone-teal',    // deploy / CI
    'chart':       'tone-blue',    // perf / datos
    'monitor':     'tone-cyan',    // browser / scraping
    'file':        'tone-violet',  // skills .md del proyecto
    'plug':        'tone-violet',  // genérico
  };
  const TONO_FALLBACK = 'tone-violet';

  function tonoDeGlifo(glifo) { return TONO_POR_GLIFO[glifo] || TONO_FALLBACK; }
  function tonoDePlugin(fullId, nombre) { return tonoDeGlifo(iconoDePlugin(fullId, nombre)); }

  const pure = { FALLBACK, TONO_FALLBACK, REGLAS, TONO_POR_GLIFO, iconoDePlugin, tonoDeGlifo, tonoDePlugin };
  global.JarvisPluginIcons = pure;
  if (typeof module !== 'undefined' && module.exports) module.exports = pure;
})(typeof window !== 'undefined' ? window : globalThis);
