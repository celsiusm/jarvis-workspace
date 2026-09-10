// JARVIS — Gestión de instancias xterm.js y WebSocket por terminal

// Mapa global: terminalId (int) → { term, ws, fitAddon, observer }
const terminales = new Map();
// Handle de QA/debug (read-only): los recorridos Playwright lo usan para
// asseverar sobre el buffer/selección de xterm sin tocar el módulo.
window.terminalesXterm = terminales;

const _termT = (s) => (window.JarvisI18n && window.JarvisI18n.t) ? window.JarvisI18n.t(s) : s;

// Comandos que se lanzan automáticamente según el tipo de IA
const AUTO_CMDS = {
  claude:      'claude --permission-mode auto',
  codex:       'codex',
  opencode:    'opencode',    // open source, modelos gratis incluidos
  qwen:        'qwen',        // Qwen Code (Qwen3-Coder)
  antigravity: 'agy',         // Google Antigravity CLI (agy) — TUI interactiva
};

// Shells que indican que NO hay una IA corriendo (prompt limpio)
const SHELLS_VACIOS = new Set(['bash', 'sh', 'zsh', 'fish', 'dash']);

// Modo observador (QA): una página abierta con ?qa=1 (los recorridos Playwright
// de los agentes — skill qa-browser-jarvis) mira las terminales SIN tocar nada:
// el backend attachea read-only,ignore-size y sin -d → no desplaza el attach
// del usuario, no le redimensiona la ventana tmux y no puede tipear. Sin esto,
// cada QA de frontend robaba la sesión al tamaño del viewport headless y dejaba
// las terminales del usuario congeladas / con el scrollback triturado.
// Ver [[tmux-size-clamping]].
const ES_OBSERVADOR = new URLSearchParams(location.search).has('qa');

// Al VOLVER el foco/visibilidad de la página (app minimizada/tapada → primer
// plano): repintado de cortesía de las terminales visibles. En segundo plano
// Chromium/WebView2 puede descartar el backing store de los canvas — el buffer
// está sano pero las letras "desaparecen" hasta que algo repinta (el usuario
// scrolleaba para curarlo). Tercera capa anti-negro, ver terminal-render.js.
// Debounce corto: focus y visibilitychange suelen llegar juntos.
(function _repintarAlVolver() {
  let _ultima = 0;
  const pasada = () => {
    const ahora = performance.now();
    if (ahora - _ultima < 500) return;
    _ultima = ahora;
    terminales.forEach(inst => {
      try {
        if (inst.container && inst.container.offsetParent !== null) {
          window.TerminalRender?.pintarYa?.(inst.term);
        }
      } catch (_) { /* instancia a medio morir: la próxima pasada la salta */ }
    });
  };
  window.addEventListener('focus', pasada);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') pasada();
  });
})();

// Vigilante del transcript en blanco (2026-07-18, foto v2 del usuario): el
// blanco de claude fullscreen también NACE SIN resize (follow roto durante el
// streaming, reconexión sobre un pane ya roto) — el nudge post-resize no lo ve.
// Cada 2.5s, si una terminal alt-screen con mouse-tracking muestra la firma del
// blanco sostenida, escala en dos pasos que además DIAGNOSTICAN al culpable:
// 'seed' (refresh = verdad de tmux; si cura era bug de NUESTRA vista) →
// 'rueda' (tmux también en blanco ⇒ claude idle; la rueda neto-cero lo
// despierta) → 'rendido' (registrar, no spamear). Cada episodio queda en
// window.__nudgeEpisodios (ring ×20) con qué paso lo curó — la materia prima
// para cazar la causa raíz de los que sobrevivan. Kill-switch compartido:
// window._jarvisNudgeOff = 1. Ver terminal-nudge.js + [[negro-fullscreen-frames-2026]].
(function _vigilanteTranscriptBlanco() {
  if (typeof document === 'undefined' || typeof setInterval === 'undefined') return;
  const _log = (id, accion, extra) => {
    const ep = Object.assign({ t: new Date().toISOString(), id, accion }, extra || {});
    (window.__nudgeEpisodios = window.__nudgeEpisodios || []).push(ep);
    if (window.__nudgeEpisodios.length > 20) window.__nudgeEpisodios.shift();
    try { console.warn('[vigilante-blanco]', JSON.stringify(ep)); } catch (_) {}
  };
  setInterval(() => {
    if (ES_OBSERVADOR || window._jarvisNudgeOff) return;
    if (document.visibilityState !== 'visible') return;
    const TN = window.TerminalNudge;
    if (!TN || !TN.vigilanteTick) return;
    if (window.TerminalLayout?.isInteracting?.()) return;
    terminales.forEach((inst, id) => {
      try {
        if (inst._cerrando) return;
        const term = inst.term;
        let firma = false, alt = false, cms = null;
        try {
          alt = term.buffer.active.type === 'alternate';
          cms = term._core.coreMouseService;
          if (alt && cms.areMouseEventsActive && inst.container &&
              inst.container.offsetParent !== null) {
            const buf = term.buffer.active, filas = [];
            for (let i = 0; i < term.rows; i++) {
              const ln = buf.getLine(buf.viewportY + i);
              filas.push(ln ? ln.translateToString(true) : '');
            }
            firma = TN.firmaTranscriptVacio(filas);
          }
        } catch (_) { return; }
        const prev = inst._vigBlanco;
        const est = TN.vigilanteTick(prev, firma);
        inst._vigBlanco = est;
        // Cura observada tras haber actuado: dejar el diagnóstico escrito.
        if (!firma && prev && prev.fase && prev.fase !== 'idle') {
          _log(id, 'curado', { tras: prev.fase });
          return;
        }
        if (!est.accion) return;
        if (est.accion === 'seed') {
          // Verdad de tmux: si esto cura, el blanco era de NUESTRA vista (bug
          // del relay/seed — queda registrado para cazarlo). Solo alt-screen:
          // un seed sin reset en buffer normal duplicaría scrollback.
          _log(id, 'seed', { rows: term.rows });
          if (inst.ws && inst.ws.readyState === WebSocket.OPEN) {
            try { inst.ws.send(JSON.stringify({ type: 'refresh' })); } catch (_) {}
          }
        } else if (est.accion === 'rueda') {
          _log(id, 'rueda', { rows: term.rows });
          inst._nudgeTs = performance.now();   // cooldown compartido con el post-resize
          const evs = TN.eventosRueda(term.cols, term.rows);
          try { cms.triggerMouseEvent(evs[0]); } catch (_) {}
          setTimeout(() => { try { if (!inst._cerrando) cms.triggerMouseEvent(evs[1]); } catch (_) {} }, 90);
        } else if (est.accion === 'rendido') {
          _log(id, 'rendido', { rows: term.rows });
        }
      } catch (_) { /* instancia a medio morir: el próximo tick la saltea */ }
    });
  }, 2500);
})();

// Modo medición de latencia: con ?lat=1 en la URL del workspace, cada terminal
// muestra un badge con el round-trip REAL del eco (tecla → eco en pantalla) en
// el browser del usuario — el número que ninguna verificación headless puede
// dar. Es la herramienta que vuelve medible el lag (deja de ser "se siente
// lento" y pasa a "p50 220ms"). Apagado por default (no molesta en uso normal).
const ES_LAT = new URLSearchParams(location.search).get('lat') === '1';  // badge OFF por default; ?lat=1 lo muestra (diagnóstico)

// Eco local predictivo: con ?echo=1 la tecla se pinta al instante (0ms, local),
// sin esperar el round-trip — y se reconcilia con el eco real del server. OFF por
// default: con el flag apagado el camino del tipeo no cambia en NADA. Se auto-
// apaga donde no acierta (TUIs). Experimental hasta verificarlo en browser real.
const ES_ECO = new URLSearchParams(location.search).get('echo') !== '0';  // ON por default (solo en shells, ver _eco); ?echo=0 lo apaga

// Visibilidad → watermark del flow control. (2026-07-02: acá vivía además un
// {'type':'refresh'} ciego al volver visible. Con el CONTRATO NUEVO de refresh
// — el backend re-captura el pane y manda un SEED completo — un refresh sin
// term.reset() previo DUPLICARÍA el contenido en el scrollback. La sanación
// on-demand quedó en los caminos que sí resetean: cota dura, context-loss y el
// botón de reset de terminales → sanearTerminales().)
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    const visible = document.visibilityState === 'visible';
    let saneos = 0;
    for (const inst of terminales.values()) {
      const s = inst.ws;
      if (!s || s.readyState !== WebSocket.OPEN) continue;
      // Reportar visibilidad SIEMPRE: el backend ensancha el watermark del flow
      // control con la pestaña visible (el eco del tipeo no queda atrapado detrás
      // del flood del agente) y lo ajusta con la oculta (protege la cola de xterm).
      try { s.send(JSON.stringify({ type: 'visible', v: visible })); } catch (_) {}
      // Al VOLVER: la terminal que desbordó su backlog estando oculta (descarte
      // de decidirBacklogOculto) se sanea con reset+seed — escalonado 150ms
      // entre cards para no juntar N seeds en el mismo frame (el freeze que
      // este camino justamente elimina).
      if (visible && inst._seedOculto) {
        setTimeout(() => { try { inst._sanearTrasOculto?.(); } catch (_) {} }, 150 * saneos++);
      }
    }
  });
}

// Piso DEGENERADO del contenedor (px): por debajo de esto un fit() calcularía
// dimensiones patológicas (1×1 / 0 cols) que mandadas a tmux reformatean TODO el
// output una letra por línea — y queda roto aunque después se agrande. SOLO ahí
// congelamos (no refiteamos): es el último tamaño bueno hasta recuperar medida.
//
// OJO histórico: este piso era 360×180 ("modo chico") y congelaba en CUALQUIER
// dimensión sub-legible. Eso causaba el bug del canvas "desfigurado": al abrir el
// dock / con varias terminales una card baja de 360 en UN eje y el fit se saltaba
// en AMBOS, dejando el canvas con el tamaño VIEJO — más ancho que la card (texto
// recortado por overflow:hidden) o más angosto (fondo punteado a la derecha). El
// canvas SIEMPRE debe seguir a su card; bajamos el piso a la zona realmente
// degenerada (alineado con el guard width<60/height<40 del ResizeObserver y con
// cols<20/rows<5 de onResize) para que a cualquier tamaño legible-pero-chico el
// canvas calce con la card. fit() a un tamaño chico reflowea: es el comportamiento
// correcto de una terminal, no un defecto.
const MIN_FIT_W = 60, MIN_FIT_H = 40;
// FUENTE FIJA (política nativa, 2026-07-02): la terminal usa SIEMPRE la fontSize de
// sus opciones — una card angosta tiene menos columnas, jamás letra más chica. El
// auto-achique de fuente (piso 50×14 cols + escala hasta 5px) era un workaround del
// motor viejo (reflowear una TUI a pocas columnas la garblaba); con el motor de UN
// emulador ese daño no existe y el achique solo dejaba terminales microscópicas.
// El tamaño mínimo usable lo garantiza el LAYOUT (MIN_PX de terminal-layout.js).

// ¿El contenedor es tan chico que un fit() daría dimensiones degeneradas?
function _modoChico(container) {
  if (!container) return false;
  return container.clientWidth < MIN_FIT_W || container.clientHeight < MIN_FIT_H;
}

/**
 * Crea e inicializa una instancia de xterm.js dentro del elemento containerId
 * y conecta el WebSocket al backend.
 */
// Tema de xterm derivado de los tokens --ob-term-* (shared/tokens.css).
// Se resuelve UNA vez por carga. La terminal es SIEMPRE de lienzo oscuro:
// en los temas oscuros los --ob-term-* trackean los tokens del chrome, y
// en los CLAROS (papel/alba) el tema override --ob-bg-terminal a grafito
// + una paleta --ob-term-* vívida propia (los tokens del chrome ahí son
// oscuros-sobre-claro y desaparecerían). Los ANSI restantes quedan fijos:
// son la paleta del CONTENIDO de la terminal, pensada para fondo oscuro.
let _xtermTheme = null;
function _temaXterm() {
  if (_xtermTheme) return _xtermTheme;
  const css = getComputedStyle(document.documentElement);
  const tok = (n, fb) => (css.getPropertyValue(n) || '').trim() || fb;
  _xtermTheme = {
    background:          tok('--ob-bg-terminal', '#141416'),
    foreground:          tok('--ob-term-fg',     '#e0e0e0'),
    cursor:              tok('--ob-term-cursor', '#7c3aed'),
    cursorAccent:        tok('--ob-bg-terminal', '#141416'),
    selectionBackground: tok('--ob-term-sel',    'rgba(124,58,237,0.24)'),
    black:        '#1a1a1a', brightBlack:   '#555555',
    red:          tok('--ob-term-red', '#ff5555'),      brightRed:     tok('--ob-term-red', '#ff6e6e'),
    green:        tok('--ob-term-green', '#50fa7b'),    brightGreen:   tok('--ob-term-green', '#69ff94'),
    yellow:       '#f1fa8c',                            brightYellow:  '#ffffa5',
    blue:         tok('--ob-term-blue', '#6272a4'),     brightBlue:    tok('--ob-term-blue-b', '#7b92d9'),
    magenta:      tok('--ob-term-magenta', '#7c3aed'),  brightMagenta: tok('--ob-term-magenta-b', '#9d4edd'),
    cyan:         tok('--ob-term-cyan', '#8be9fd'),     brightCyan:    tok('--ob-term-cyan', '#a4ffff'),
    white:        '#e0e0e0',                            brightWhite:   '#ffffff',
  };
  return _xtermTheme;
}

if (typeof window !== 'undefined') {
  window.addEventListener('theme-changed', () => {
    // Esperar al siguiente macro-tick (20ms) para que los estilos del nuevo tema
    // se hayan propagado y computado completamente en el DOM.
    setTimeout(() => {
      _xtermTheme = null;
      const nuevo = _temaXterm();
      for (const inst of terminales.values()) {
        try { inst.term.options.theme = nuevo; } catch (_) {}
      }
    }, 20);
  });
}

function crearTerminal(containerId, terminalId, tipoIa = 'manual', intentoAuto = 0) {
  const container = document.getElementById(containerId);
  if (!container) return null;

  // Presupuesto de auto-reintentos de reconexión de ESTA cadena de instancias.
  // Lo arma el caller (0 en alta normal; +1 en cada auto-reintento del onclose).
  // Se resetea a 0 cuando llega el primer dato (conexión sana) para que una caída
  // posterior vuelva a tener sus 3 intentos. Ver onclose más abajo.
  let _autoIntento = intentoAuto;

  // Links clickeables: OSC 8 (hyperlinks explícitos de gh/eza/ls --hyperlink) por el
  // linkHandler, y URLs de texto plano (http://localhost:5xxx que escupen los agentes)
  // por WebLinksAddon (más abajo, tras term.open).
  // Los links LOCALES (dev server del agente, demo /static de Jarvis) se abren EN el
  // Web Preview del dock — que es donde el usuario mira los diseños — reusando la
  // pestaña del mismo origen (linkAlPreview decide; el propio workspace queda afuera).
  // El resto va al browser externo en pestaña nueva, opener limpio.
  // En alt-screen el mouse va a la app (claude) → los links viven en buffer normal.
  const _abrirLink = (uri) => {
    try {
      const local = window.WebPreview?._pure?.linkAlPreview?.(uri, location.origin);
      if (local && window.JarvisDock?.open) {
        window.WebPreview.init?.(document.getElementById('jw-pane-preview'));
        window.JarvisDock.open('preview');
        window.WebPreview.abrirLink(local);
        return;
      }
    } catch (_) { /* cualquier falla del preview → browser externo */ }
    try { window.open(uri, '_blank', 'noopener,noreferrer'); } catch (_) {}
  };
  const term = new Terminal({
    theme: _temaXterm(),
    linkHandler: { activate: (_e, uri) => _abrirLink(uri) },
    fontFamily:         '"Cascadia Code", "JetBrains Mono", "Fira Code", Consolas, monospace',
    fontSize:           13,
    lineHeight:         1.4,
    cursorBlink:        false,   // 9 terminales parpadeando = render idle constante; el bloque estático se ve igual de bien
    cursorStyle:        'block',
    scrollback:         5000,   /* buffer NORMAL (bash y TUI default/inline): el scrollback ES lo seleccionable con la rueda. claude en FULLSCREEN corre en alt-screen (sin scrollback), así que ahí no aplica; sirve para shells y para el modo inline. */
    convertEol:         true,
    allowProposedApi:   true,
    fastScrollModifier: 'alt',
    scrollSensitivity:  3,
  });

  const fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  // Anchos Unicode 11: bash (glibc) y tmux miden los emoji como 2 columnas; las
  // tablas default de xterm (U6) los miden 1. Con una entrada de historial que
  // traía un emoji, cada redibujo RELATIVO de readline (↑/↓ del historial)
  // aterrizaba 1 columna corrido y se comía el prompt letra a letra (reproducido
  // byte a byte contra el pane real, 2026-07-17). Con U11 el grid local mide
  // EXACTAMENTE igual que tmux y el desfase desaparece.
  if (window.Unicode11Addon?.Unicode11Addon) {
    try {
      term.loadAddon(new window.Unicode11Addon.Unicode11Addon());
      term.unicode.activeVersion = '11';
    } catch (_) {}
  }
  term.open(container);
  // Después de open(): recién acá existen los servicios internos que la Escala
  // necesita corregir — el dpr del renderer (píxeles del canvas) y el mapeo de
  // píxel a celda del mouse (selección, doble-click, links, clicks en las TUIs).
  _engancharDprEscala(term);
  _engancharMouseEscala(term);

  // La scrollbar del viewport está oculta por CSS (.xterm-viewport, base.css):
  // el scroll va solo con la ruedita. PERO xterm 5.3 mide el ancho de barra como
  // `offsetWidth - scrollArea.offsetWidth || 15` → con la barra oculta mide 0 y
  // cae al fallback de 15px, que el FitAddon RESTA del ancho disponible (franja
  // muerta a la derecha). Forzamos 0 para que las columnas usen todo el ancho.
  // Se setea una sola vez (xterm no lo re-mide después del open).
  try { term._core.viewport.scrollBarWidth = 0; } catch (_) {}

  // ── Renderer acelerado: WebGL (GPU) → Canvas → DOM, en ese orden ──
  // FIX de performance con 9 terminales: el renderer DOM default redibuja
  // nodos del DOM en cada frame de cada TUI; con muchos agentes corriendo
  // a la vez (spinners de Claude Code × 9) el navegador se arrastra.
  // WebGL/Canvas pintan en GPU/bitmap: órdenes de magnitud más rápido.
  // Debe cargarse DESPUÉS de term.open().
  let _rendererActivo = 'dom';   // 'webgl' | 'canvas' | 'dom' — dato clave del diagnóstico de garble
  // Renderer: CANVAS (2D) por DEFAULT, NO WebGL. Cada WebGL abre 1 contexto y el
  // browser topea ~16: con varias terminales + un preview/Web-Builder con WebGL
  // (Three.js) se agotan → el browser DROPEA los contextos más viejos = las
  // terminales → quedan NEGRAS (forense 2026-07-04). Canvas es bitmap (rápido, fue
  // el renderer estándar pre-WebGL) y NO cuenta contra ese límite → INMUNE al
  // agotamiento. Lo que motivaba no usar DOM era el renderer DOM lento, no Canvas.
  // WebGL sigue disponible pero opt-in por ?webgl=1 (perf/diagnóstico), con la
  // recuperación de contexto ARREGLADA (refresh del buffer intacto, sin reset).
  (() => {
    const _webglOptIn = (() => { try { return new URLSearchParams(location.search).has('webgl'); } catch (_) { return false; } })();
    if (_webglOptIn) {
      try {
        if (window.WebglAddon?.WebglAddon) {
          const gl = new window.WebglAddon.WebglAddon();
          gl.onContextLoss(() => {
            // Recuperar SIN term.reset(): la pérdida de contexto NO borra el buffer
            // (vive en memoria JS, no en la GPU) — el reset() ERA lo que lo vaciaba y
            // dejaba la card en blanco si el reseed no llegaba. Cargar Canvas y
            // repintar LOCAL el buffer intacto, sin depender de ningún reseed.
            try { gl.dispose(); } catch (_) {}
            try { term.loadAddon(new window.CanvasAddon.CanvasAddon()); _rendererActivo = 'canvas'; } catch (_) {}
            try { term._core.viewport.scrollBarWidth = 0; } catch (_) {}
            // Los canvas de capa del addon nuevo nacen SIN el blindaje anti-wipe /
            // contextrestored (los listeners viven en los canvas viejos, ya muertos).
            try {
              const inst = terminales.get(terminalId);
              if (inst) {
                try { inst.moWipe?.disconnect(); } catch (_) {}
                inst.moWipe = window.TerminalRender?.blindarWipeCanvas?.(term, container) || null;
              }
              window.TerminalRender?.blindarContextoCanvas?.(term, container);
            } catch (_) {}
            // Pintar SINCRÓNICO: el swap de renderer deja el canvas nuevo vacío y el
            // rAF del debouncer puede starvarse justo cuando más carga hay (que es
            // cuando se pierden contextos). Ver [[negro-al-maximizar-raf-starvation]].
            try { window.TerminalRender ? window.TerminalRender.pintarYa(term) : term.refresh(0, term.rows - 1); } catch (_) {}
          });
          term.loadAddon(gl);
          _rendererActivo = 'webgl';
          return;
        }
      } catch (_) { /* GPU/WebGL no disponible */ }
    }
    try {
      if (window.CanvasAddon?.CanvasAddon) { term.loadAddon(new window.CanvasAddon.CanvasAddon()); _rendererActivo = 'canvas'; }
    } catch (_) { /* último recurso: DOM renderer */ }
  })();

  // URLs de texto plano clickeables (además del OSC 8 del linkHandler de arriba).
  try {
    if (window.WebLinksAddon?.WebLinksAddon) {
      term.loadAddon(new window.WebLinksAddon.WebLinksAddon((_e, uri) => _abrirLink(uri)));
    }
  } catch (_) { /* addon no disponible: degrada sin romper la terminal */ }

  // Blindaje anti "terminal NEGRA hasta scrollear": el repintado de xterm vive en
  // UN rAF que se starva bajo carga (terminales ocultas parseando) — si no corre
  // en ~50ms, un timer pinta en su lugar. Ver terminal-render.js +
  // [[negro-al-maximizar-raf-starvation]].
  try { window.TerminalRender?.blindarRenderStarvation?.(term); } catch (_) {}
  // Tercera capa anti-negro (2026-07-11, "letras desaparecidas hasta scrollear"):
  // el bitmap de un canvas oculto/tapado puede no sobrevivir (hibernación de
  // canvas de Chromium/WebView2, GPU reset) y xterm no se entera → repintar en
  // la transición a visible + ante contextrestored. Ver terminal-render.js.
  let _ioRepintar = null;
  try { _ioRepintar = window.TerminalRender?.repintarAlMostrar?.(term, container) || null; } catch (_) {}
  try { window.TerminalRender?.blindarContextoCanvas?.(term, container); } catch (_) {}
  // Cuarta capa (2026-07-18): el ResizeObserver device-pixel del PROPIO CanvasAddon
  // re-setea canvas.width 1-2 frames después del refit (redondeo sub-pixel) y BORRA
  // el bitmap con su redraw perdible (pausa/carrera) → letras invisibles hasta
  // scrollear, gatillado por eliminar/maximizar. Vigilar width/height de las capas
  // y repintar en el microtask (el compositor nunca muestra el canvas vacío).
  // Ver terminal-render.js (blindarWipeCanvas) + [[negro-al-maximizar-raf-starvation]].
  let _moWipe = null;
  try { _moWipe = window.TerminalRender?.blindarWipeCanvas?.(term, container) || null; } catch (_) {}

  term.onScroll(() => {
    try { term.refresh(0, term.rows - 1); } catch (_) {}
  });

  // ── Selección con mouse: NATIVA de la app cuando trackea el mouse, LOCAL si no ──
  // Claude Code fullscreen (flicker-free) maneja su PROPIA selección: arrastrás y
  // claude resalta + AUTO-COPIA (vía OSC 52), y la selección se EXTIENDE al scrollear
  // su transcript (verificado E2E). Para eso el drag tiene que LLEGAR a la app (no
  // forzar selección local). Discriminador robusto: `coreMouseService.areMouseEventsActive`
  // (claude fullscreen / vim / htop / less = true; bash/shell = false), evaluado POR
  // evento porque las apps lo togglean en vivo. NOTA: el motor `control` NO mete a bash
  // en copy-mode de tmux (el input va por send-keys -H al PTY), así que bash reporta
  // NONE limpio — el viejo "arrastrar cae en copy-mode" ya no aplica. Reglas:
  //   • App trackea mouse + SIN Shift → el mouse va a la app → su selección NATIVA
  //     (claude: highlight + auto-copy + scroll-extend; el click vuelve a mover el
  //      cursor / expandir tool-results de claude).
  //   • Shift apretado → selección LOCAL de xterm (lo que claude llama "your terminal's
  //     native copy"). Bloqueamos button+motion para que los reportes de la app no la
  //     borren (los motion ignoran shift y contaban como user-input → mataban la selección).
  //   • App NO trackea (bash/shell) → selección LOCAL siempre (drag normal selecciona).
  //   • Rueda (button 4) → siempre pasa al PTY (scroll de la app), con clearSelection
  //     anulado mientras el reporte viaja (scroll ≠ tipeo: no debe desarmar la selección).
  // disable() solo apaga el flag (NO clearSelection): los TUIs re-assertan DECSET varias
  // veces/seg y si no, la selección moría sola. El OSC 52 de la auto-copia de claude se
  // puentea a navigator.clipboard (sin esto el "copied" de claude no aterriza en el sistema).
  try {
    const _ss  = term._core._selectionService;
    const _cms = term._core.coreMouseService;
    const _tme = _cms.triggerMouseEvent.bind(_cms);
    _ss.disable = () => { _ss._enabled = false; };
    _ss.shouldForceSelection = ev => (!!(ev && ev.shiftKey)) || !_cms.areMouseEventsActive;
    _cms.triggerMouseEvent = ev => {
      if (ev.button === 4) {                       // rueda: pasa al PTY sin desarmar la selección
        const _clear = _ss.clearSelection;
        _ss.clearSelection = () => {};
        try { return _tme(ev); }
        finally { _ss.clearSelection = _clear; }
      }
      if (ev.shift) return false;                  // Shift ⇒ selección LOCAL: bloquear button+motion
      return _tme(ev);                             // app trackea + sin Shift ⇒ selección NATIVA de la app
    };
    // Puente OSC 52: la auto-copia de claude (y cualquier app que copie por OSC 52)
    // aterriza en el clipboard del sistema (UTF-8 correcto). Sin esto, claude dice
    // "copied" pero el texto no sale de xterm.
    term.parser.registerOscHandler(52, data => {
      try {
        const b64 = String(data).split(';').pop() || '';
        if (b64 && b64 !== '?') {
          const bin = atob(b64);
          const txt = new TextDecoder().decode(Uint8Array.from(bin, c => c.charCodeAt(0)));
          if (txt) navigator.clipboard?.writeText(txt).catch(() => {});
        }
      } catch (_) {}
      return true;
    });
  } catch (_) { /* interna de xterm 5.3 — si cambia el bundle, degrada a Shift+drag */ }

  // El WebSocket se abre más abajo DIFERIDO a un requestAnimationFrame (tras el
  // fit), pero los handlers de teclado/paste/menú de acá abajo lo capturan por
  // clausura → hay que declararlo ya. Hasta que _abrirWS() lo asigne queda null;
  // todo handler que pueda dispararse antes chequea `ws && ws.readyState`.
  let ws = null;

  // Timestamp (performance.now) de la última tecla que el usuario mandó al PTY.
  // Lo usa _agendarFlush: si llega data poco después, es el ECO de lo tipeado y
  // se vuelca al instante (sin esperar el frame de rAF) para que la letra aparezca
  // ya. Arranca muy atrás para no tratar el primer output del attach como eco.
  let _ultimoInput = -1e9;

  // Medidor de latencia de eco (round-trip real de lo que tipeás). Vive en el
  // scope de la terminal: sobrevive a reconexiones del WS y acumula muestras.
  // marcarInput() en onData, marcarOutput() en el primer dato que vuelve.
  const _medidor = (window.TerminalLatencia)
    ? window.TerminalLatencia.crearMedidor({ ventana: 50 }) : null;
  // Eco local a 0ms. DOS técnicas según el tipo de terminal:
  //  - Shell (tipo 'manual'): se escribe el char al buffer de xterm (eco lineal;
  //    coincide con el eco real → se saltea). Anda perfecto.
  //  - TUI (claude/codex): NO se puede tocar el buffer/cursor (la TUI los rastrea
  //    para redibujar → si los movés, garble: letras rotas + espacios). Se usa un
  //    OVERLAY: una decoración 'top' de xterm muestra el char predicho ENCIMA, sin
  //    mover el cursor real. Al llegar el redibujo de la TUI (con el char), se
  //    descarta el overlay. Es la técnica de mosh. ?echo=0 apaga todo.
  const _esTui = (tipoIa !== 'manual');
  const _eco = (ES_ECO && !_esTui && window.TerminalEcoLocal)
    ? window.TerminalEcoLocal.crearEcoLocal({ maxPendientes: 16 }) : null;
  let _latBadge = null;
  function _pintarLatBadge() {
    if (!ES_LAT || !_medidor) return;
    const s = _medidor.stats();
    if (s.n === 0) return;
    if (!_latBadge) {
      _latBadge = document.createElement('div');
      _latBadge.className = 't-lat-badge';
      _latBadge.style.cssText =
        'position:absolute;bottom:4px;right:6px;z-index:6;pointer-events:none;' +
        'font:11px/1.4 ui-monospace,monospace;padding:1px 6px;border-radius:6px;' +
        'background:rgba(0,0,0,.62);color:#cdeacd;letter-spacing:.2px;' +
        'box-shadow:0 1px 4px rgba(0,0,0,.4)';
      const host = container.querySelector('.terminal-body') || container;
      if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
      host.appendChild(_latBadge);
    }
    // Color por umbral perceptual: <16ms imperceptible, <50 bien, <120 tolerable, peor = rojo.
    const c = s.ultima < 16 ? '#9ff7a6' : s.ultima < 50 ? '#cdeacd'
            : s.ultima < 120 ? '#f2d98c' : '#f4a3a3';
    _latBadge.style.color = c;
    _latBadge.textContent = `eco ${s.ultima}ms · p50 ${s.p50} · p90 ${s.p90}`;
  }

  // Captura de diagnóstico de GARBLE (atajo Ctrl+Shift+G): junta lo que xterm
  // MUESTRA, lo que tmux DIBUJA y el renderer activo, los compara y descarga un
  // JSON. Es para cazar el garble con evidencia en la GPU real del usuario —
  // cuando lo veas, apretá el atajo y mandame el archivo. No lo cura, lo retrata.
  async function _capturarGarble() {
    try {
      const buf = term.buffer.active;
      const xtermLineas = [];
      for (let y = 0; y < term.rows; y++) {
        const ln = buf.getLine(buf.viewportY + y);
        xtermLineas.push(ln ? ln.translateToString(true) : '');
      }
      let tmuxLineas = [];
      try {
        const resp = await fetch(`/api/terminals/${terminalId}/snapshot`);
        if (resp.ok) tmuxLineas = (await resp.json()).lineas || [];
      } catch (_) {}
      const cmp = window.TerminalDiagnostico
        ? window.TerminalDiagnostico.compararGrid(xtermLineas, tmuxLineas)
        : { iguales: null, diferencias: [] };
      const reporte = {
        capturado: new Date().toISOString(),
        terminalId, renderer: _rendererActivo,
        cols: term.cols, rows: term.rows, viewportY: buf.viewportY,
        garble: cmp.iguales === false,
        filasRotas: cmp.diferencias.length,
        diferencias: cmp.diferencias,
        xterm: xtermLineas, tmux: tmuxLineas,
        userAgent: navigator.userAgent,
      };
      const blob = new Blob([JSON.stringify(reporte, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `garble-term${terminalId}-${Date.now()}.json`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => { try { URL.revokeObjectURL(a.href); } catch (_) {} }, 1000);
      const msg = cmp.iguales === false
        ? _termT('Garble capturado: {n} fila(s) rotas · renderer {r}').replace('{n}', cmp.diferencias.length).replace('{r}', _rendererActivo)
        : _termT('Sin garble ahora (xterm == tmux) · renderer {r}').replace('{r}', _rendererActivo);
      if (window.toast) window.toast(msg); else console.log('[garble]', msg);
    } catch (err) {
      console.error('[garble] captura falló', err);
      if (window.toast) window.toast('No se pudo capturar el diagnóstico');
    }
  }

  // ── Gesto Ctrl+A: seleccionar el input tipeado → copiar o borrar ─────────
  // inputSel != null ⇔ la selección visible la armó Ctrl+A (no el mouse).
  // Con eso armado: Ctrl+C copia el texto limpio, Backspace/Supr borra todo,
  // Esc cancela, cualquier otra tecla desarma y sigue normal.
  let inputSel = null;

  // Secuencia que vacía el input línea por línea: (Ctrl+E → ir al fin,
  // Ctrl+U → matar hasta el inicio, Backspace → unir con la línea anterior)
  // × 20. Verificada en vivo (tmux 2026-06-05) con bash y Claude Code:
  // limpia multilínea completo, es no-op con input vacío, NO interrumpe una
  // generación en curso y NO arma el "Ctrl-C again to exit" ni el Rewind
  // (los riesgos de \x03 / Esc Esc). En Claude Code, Ctrl+Y recupera.
  const _SEQ_BORRAR_INPUT = '\x05\x15\x7f'.repeat(20);

  function _armarSeleccionInput() {
    const buf = term.buffer.active;
    const absCursor = buf.baseY + buf.cursorY;   // fila absoluta en el buffer
    const desde = Math.max(0, absCursor - 60);
    const hasta = Math.min(buf.length - 1, absCursor + 30);
    const lineas = [];
    for (let r = desde; r <= hasta; r++) {
      const l = buf.getLine(r);
      const next = buf.getLine(r + 1);
      // Si la fila de abajo es continuación (wrap), NO trimear la cola de esta:
      // el espacio del borde de wrap es contenido real de la línea lógica.
      lineas.push({
        text: l ? l.translateToString(!(next && next.isWrapped)) : '',
        isWrapped: !!(l && l.isWrapped),
      });
    }
    const tipo = document.getElementById(`ia-logo-${terminalId}`)?.dataset.tipo || tipoIa;

    // Ghost text: las CLIs (Claude Code) muestran una sugerencia de auto-
    // completado ATENUADA a partir de la celda del cursor cuando no tipeaste
    // nada (se acepta con Tab). En el buffer parece texto pero NO es input
    // del usuario: si la celda bajo el cursor tiene estilo de hint (dim o
    // gris de paleta 232-255/8) en vez del fg default del texto tipeado,
    // cortamos la fila del cursor ahí — la sugerencia no se selecciona, no
    // se copia, y con input vacío el gesto directamente no se arma.
    if (tipo !== 'manual') {
      const lc = buf.getLine(absCursor);
      // OJO: la celda EXACTA del cursor no sirve para detectar el hint — la
      // TUI pinta su cursor "fake" como video inverso con fg default
      // ([7m[39m, capturado en vivo de tmux) ENCIMA del primer caracter de
      // la sugerencia. El gris del ghost recién se ve en las celdas que
      // siguen: chequeamos las primeras 3 desde el cursor.
      const esHintCell = (c) => !!c && c.getChars().trim() !== '' &&
        (c.isDim() ||
         (c.isFgPalette() && (c.getFgColor() >= 232 || c.getFgColor() === 8)));
      const esHint = !!lc && (esHintCell(lc.getCell(buf.cursorX)) ||
                              esHintCell(lc.getCell(buf.cursorX + 1)) ||
                              esHintCell(lc.getCell(buf.cursorX + 2)));
      if (esHint) {
        lineas[absCursor - desde].text = lc.translateToString(true, 0, buf.cursorX);
        // La cola wrapeada de la sugerencia (si era larga) tampoco es input.
        for (let r = absCursor + 1; r <= hasta; r++) {
          const lr = lineas[r - desde];
          if (!lr || !lr.isWrapped) break;
          lr.text = '';
          lr.isWrapped = false;
        }
      }
    }

    const bloque = window.TerminalInputBlock?.detectarBloqueInput(lineas, absCursor - desde, tipo);
    if (!bloque) return;
    const texto = window.TerminalInputBlock.extraerTextoInput(lineas, bloque, tipo);
    if (!texto) return;                         // input vacío: nada que armar
    term.selectLines(desde + bloque.start, desde + bloque.end);
    inputSel = { texto };                       // (después de selectLines: ver onSelectionChange)
  }

  // Cualquier cambio de selección externo al gesto (click, drag, clear ajeno)
  // desarma: que un Backspace posterior no borre el input por sorpresa.
  // _armarSeleccionInput asigna inputSel DESPUÉS de selectLines justamente
  // porque este handler (sincrónico) pisa con null durante el selectLines.
  term.onSelectionChange(() => { inputSel = null; });

  // Bridge con controles globales (push-to-talk) + atajos de copy/paste +
  // Shift+Enter para insertar un newline sin ejecutar el comando.
  term.attachCustomKeyEventHandler((e) => {
    if (window._jarvisHandleControlKey?.(e)) return false;
    if (e.type !== 'keydown') return true;

    // Modificadores solos (Ctrl, Shift, Alt, Meta sin tecla acompañante):
    // xterm por default trata cualquier keydown como input y limpia la
    // selección activa. Bloqueamos para preservar la selección hasta que
    // venga la tecla real (típicamente la "C" de Ctrl+C).
    if (_esModificadorPuro(e)) return false;

    // ── Selección de input armada por Ctrl+A: resolver el gesto ──
    if (inputSel) {
      if (!term.hasSelection()) {
        inputSel = null;                        // murió por otro lado
      } else if (e.key === 'Escape') {
        e.preventDefault();
        term.clearSelection();
        return false;
      } else if ((e.key === 'Backspace' || e.key === 'Delete')
                 && !e.altKey && !e.metaKey) {
        // Con o SIN Ctrl: el gesto natural es mantener Ctrl, tocar A y darle
        // Backspace sin soltar — Ctrl+Backspace armado también borra todo.
        e.preventDefault();
        term.clearSelection();
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'input', data: _SEQ_BORRAR_INPUT }));
        }
        return false;
      } else {
        const esCopy  = e.ctrlKey && !e.altKey && !e.metaKey
                        && (e.code === 'KeyC' || e.key === 'c' || e.key === 'C');
        const esCtrlA = e.ctrlKey && !e.shiftKey && !e.altKey && !e.metaKey
                        && (e.code === 'KeyA' || e.key === 'a');
        // Copy cae al branch de Ctrl+C de abajo (usa el texto limpio) y la
        // selección sobrevive (podés copiar Y después borrar). Ctrl+A re-arma.
        // Cualquier otra tecla desarma y sigue su curso normal.
        if (!esCopy && !esCtrlA) { term.clearSelection(); inputSel = null; }
      }
    }

    // Ctrl+Shift+C / Ctrl+Shift+V: copy/paste garantizados aun dentro de
    // apps TUI donde Ctrl+C tiene otro significado (estilo gnome-terminal).
    if (e.ctrlKey && e.shiftKey && !e.altKey && !e.metaKey) {
      if (e.code === 'KeyC' || e.key === 'C' || e.key === 'c') {
        // Mismo criterio que Ctrl+C: si la selección la armó Ctrl+A, copiar
        // el texto limpio del input (sin glifo ❯ ni sangrías).
        if (inputSel?.texto) _copiarTextoAlClipboard(inputSel.texto);
        else _copiarSeleccion(term);
        e.preventDefault();
        return false;
      }
      if (e.code === 'KeyV' || e.key === 'V' || e.key === 'v') {
        _pasteDesdeClipboard(ws);
        e.preventDefault();
        return false;
      }
      if (e.code === 'KeyG' || e.key === 'G' || e.key === 'g') {
        // Diagnóstico de garble: retrata xterm vs tmux + renderer y lo descarga.
        _capturarGarble();
        e.preventDefault();
        return false;
      }
    }

    // Ctrl+C: si hay selección, copiar; si no, dejar pasar (SIGINT).
    // No limpiamos la selección — sirve como feedback visual extra, y si el
    // copy falla el usuario puede reintentar sin tener que reseleccionar.
    if (e.ctrlKey && !e.shiftKey && !e.altKey && !e.metaKey
        && (e.code === 'KeyC' || e.key === 'c')) {
      if (term.hasSelection()) {
        // Selección armada por Ctrl+A: copiar el texto LIMPIO del input
        // (sin glifo ❯ ni sangrías), no la selección cruda de pantalla.
        if (inputSel?.texto) _copiarTextoAlClipboard(inputSel.texto);
        else _copiarSeleccion(term);
        e.preventDefault();
        return false;
      }
      return true;
    }

    // Ctrl+A: seleccionar lo tipeado en el input, como el select-all de un
    // chat. Resalta el bloque (detección en terminal-input-block.js, anclada
    // al cursor); después: Ctrl+C copia · Backspace/Supr borra todo · Esc
    // cancela · cualquier otra tecla desarma. El Ctrl+A nativo de readline
    // ("ir al inicio") se pierde adrede — ←/Home siguen haciendo eso.
    if (e.ctrlKey && !e.shiftKey && !e.altKey && !e.metaKey
        && (e.code === 'KeyA' || e.key === 'a')) {
      e.preventDefault();
      _armarSeleccionInput();
      return false;
    }

    // Ctrl+V (sin Shift): dejar que el browser dispare su paste NATIVO.
    // xterm por default convierte Ctrl+V en \x16 (SYN) hacia el PTY y hace
    // preventDefault del keydown, lo que mata el paste del browser: el texto
    // nunca llegaba al listener de 'paste' del container. (Las imágenes
    // "funcionaban" solo porque el \x16 llegaba a Claude Code, que tiene su
    // propio Ctrl+V de imágenes vía clipboard del OS.) Devolver false SIN
    // preventDefault → xterm ignora la tecla, el browser emite 'paste' con
    // clipboardData completo (texto + imagen) y el handler de abajo lo
    // procesa con su prioridad documentada (texto gana sobre imagen).
    if (e.ctrlKey && !e.shiftKey && !e.altKey && !e.metaKey
        && (e.code === 'KeyV' || e.key === 'v')) {
      return false;
    }

    // Shift+Enter → newline sin submit.
    //
    // El único protocolo que funciona universalmente para "newline dentro
    // del input sin ejecutar" es BRACKETED PASTE: envolver el \r en
    //   \x1b[200~ ... \x1b[201~
    // Los TUIs modernos (Claude Code, Codex, Gemini) y bash ≥4.4 con readline
    // ≥6.1 interpretan ese bloque como "esto es texto pegado, no como
    // teclado", así que el \r de adentro queda como newline insertado sin
    // disparar submit.
    //
    // Aceptamos también NumpadEnter por compatibilidad con teclados extendidos.
    const isEnterKey = e.key === 'Enter' || e.code === 'Enter' || e.code === 'NumpadEnter';
    if (isEnterKey && e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey) {
      e.preventDefault();
      e.stopPropagation();
      if (ws && ws.readyState === WebSocket.OPEN) {
        const tipo = document.getElementById(`ia-logo-${terminalId}`)?.dataset.tipo || tipoIa;
        // bash en modo no-readline (algún heredoc raro) podría no respetar
        // bracketed paste, mantenemos \x16\x0a (Ctrl+V + LF) que readline
        // entiende como quoted-insert. Para TUIs, bracketed paste universal.
        const seq = tipo === 'manual'
          ? '\x16\x0a'
          : '\x1b[200~\r\x1b[201~';
        ws.send(JSON.stringify({ type: 'input', data: seq }));
      }
      return false;
    }

    return true;
  });

  // Click = foco + marca la terminal como "activa" (aura violeta). El aura es
  // EXCLUSIVA del click: el hover también muda el foco del teclado tras un dwell
  // corto (workspace.js → _enfocarPorHover / shell/foco-hover.js) pero NO
  // enciende la card — pasar el mouse no es seleccionar. Ver
  // [[foco-teclado-por-hover]].
  container.addEventListener('click', () => {
    term.focus();
    document.querySelectorAll('.terminal-card.activa').forEach(c => c.classList.remove('activa'));
    container.closest('.terminal-card')?.classList.add('activa');
  });

  // ─── Rueda → scroll ──────────────────────────────────────────────────────
  // Si la APP pidió mouse-tracking (Grok, Claude fullscreen, htop…): la rueda
  //   viaja al PTY. En primary SIN mouse (bash): interceptamos y scrolleamos
  //   xterm.js — si no, tmux (mouse on) entra en copy-mode invisible.
  // En app-mode se REENVÍAN copias sintéticas (xterm no chequea isTrusted)
  //   → FACTOR_RUEDA reportes por notch.
  //
  // VELOCIDAD (pedido del usuario 2026-07-02): sin throttle — el de 40ms
  // comía la mitad de los notches en giro rápido y TODO trackpad. Ahora los
  // deltas se ACUMULAN (con fracción) y se aplica el entero una vez por
  // frame: nada se pierde y el factor vive en TerminalFlow.lineasDeRueda
  // (puro, testeado). Alt apretado = turbo (FACTOR_RUEDA_TURBO).
  //
  // Selección + scroll a la par (SOLO buffer NORMAL: bash y el TUI default/inline):
  // durante un drag de selección la rueda scrollea Y re-extiende la selección hasta
  // el cursor (reusa _handleMouseMove del SelectionService con el último mousemove
  // real). El drag-scroll de borde es nativo de xterm (solo buffer principal).
  // En claude FULLSCREEN (alt-screen) NO hay scrollback: la selección nativa alcanza
  // SOLO lo visible (arrastrar + Ctrl+C copia la pantalla). "Seleccionar mientras
  // scrolleás" EN EL LUGAR es físicamente imposible ahí (el alt-screen no guarda
  // historia y la app repinta) — eso es propio del TUI default/inline. Se dejó
  // NATIVO a propósito: sin overlay, sin buffer-switch, sin frame-diff (todos esos
  // intentos de "magia" se removieron por pedido del usuario 2026-07-03).
  let _selUltimoMove = null;
  let _botonAbajo = false;
  const _selArrastrando = () => _botonAbajo;   // botón primario apretado = arrastrando
  container.addEventListener('mousemove', e => { _selUltimoMove = e; });
  container.addEventListener('mousedown', e => {
    if (e.button === 0) { _botonAbajo = true; }
  }, true);
  // El mouseup puede caer FUERA de la card → escucha global, auto-limpiante.
  const _soltarBoton = () => {
    if (!terminales.has(terminalId)) { window.removeEventListener('mouseup', _soltarBoton); return; }
    _botonAbajo = false;
  };
  window.addEventListener('mouseup', _soltarBoton);
  let _ruedaAcum = 0, _ruedaRAF = 0, _ruedaHealTs = -Infinity;
  container.addEventListener('wheel', e => {
    if (e._jarvisRuedaSint) return;               // copia sintética nuestra: dejarla llegar a xterm
    // Destino: si la APP pidió mouse-tracking, la rueda es de ella — también
    // en buffer NORMAL (Grok Build y otros TUI no usan alt-screen). Antes se
    // interceptaba SIEMPRE en primary y se scrolleaba xterm → Grok no veía
    // el wheel. Ver decidirDestinoRueda. Alt sin mouse sigue el heal de
    // seed degradado; primary sin mouse = scrollback local (bash).
    const _alt = term.buffer.active.type === 'alternate';
    let _mouseActivo = true;
    try { _mouseActivo = !!term._core.coreMouseService.areMouseEventsActive; }
    catch (_) { /* interna de xterm 5.3: si cambia, asumir activo (camino normal) */ }
    const _dec = window.TerminalFlow?.decidirDestinoRueda?.({
      alt: _alt,
      mouseActivo: _mouseActivo,
      wsAbierto: !!(ws && ws.readyState === WebSocket.OPEN),
      observador: ES_OBSERVADOR,
      msDesdeHeal: performance.now() - _ruedaHealTs,
    }) ?? (_alt ? 'app' : 'xterm');
    if (_dec === 'heal') {
      _ruedaHealTs = performance.now();
      // Contrato refresh (2026-07-02): reset ANTES — el seed cae en terminal virgen.
      try { term.reset(); } catch (_) {}
      try { ws.send(JSON.stringify({ type: 'refresh' })); } catch (_) {}
      return;
    }
    if (_dec === 'nada') return;
    if (_dec === 'app') {
      const extra = (window.TerminalFlow?.FACTOR_RUEDA || 3) - 1;
      for (let i = 0; i < extra; i++) {
        const ev = new WheelEvent('wheel', e);
        ev._jarvisRuedaSint = true;
        try { e.target.dispatchEvent(ev); } catch (_) { break; }
      }
      return;                                     // el original sigue su camino a xterm
    }
    e.preventDefault();
    e.stopPropagation();
    _ruedaAcum += window.TerminalFlow?.lineasDeRueda?.({
      deltaY: e.deltaY, deltaMode: e.deltaMode, rows: term.rows, turbo: e.altKey,
    }) ?? (e.deltaY > 0 ? 3 : -3);
    if (_ruedaRAF) return;
    _ruedaRAF = requestAnimationFrame(() => {
      _ruedaRAF = 0;
      const n = Math.trunc(_ruedaAcum);
      if (!n) return;
      _ruedaAcum -= n;                            // la fracción queda para el próximo evento
      term.scrollLines(n);
      if (_selArrastrando() && _selUltimoMove) {
        try { term._core._selectionService._handleMouseMove(_selUltimoMove); } catch (_) {}
      }
    });
  }, { passive: false, capture: true });

  // ─── Drag & drop: texto, archivo, imagen o video → pegar en terminal ─────
  // Imágenes/videos: se suben al backend y se manda la ruta /tmp/... (Claude lee
  //   la imagen; la ruta del video sirve para ffmpeg/etc).
  // Archivos de otro tipo: se manda solo el nombre (el browser no expone la ruta).
  // Texto: se pega tal cual.
  // Al arrastrar un ARCHIVO aparece un overlay CENTRAL que anuncia qué soltar
  // (imagen / video / archivo), traducido por i18n. La clase se decide con los
  // tipos MIME que el browser expone en dragover (TerminalDrop.clasificarArrastre).

  // Overlay central de "soltá acá" (creado perezosamente en el primer arrastre;
  // absolute → no altera el fit del xterm). NUNCA backdrop-filter: el canvas de
  // xterm vive debajo (regla de oro).
  let _dropOv = null;
  function _asegurarDropOverlay() {
    if (_dropOv) return _dropOv;
    const ov = document.createElement('div');
    ov.className = 'term-drop';
    ov.setAttribute('aria-hidden', 'true');
    ov.innerHTML = `
      <div class="term-drop__box">
        <span class="term-drop__ico" aria-hidden="true">
          <svg class="td-ic td-ic-img" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2.5"/><circle cx="8.5" cy="9.5" r="1.6"/><path d="M21 15.5l-5-5L5 21"/></svg>
          <svg class="td-ic td-ic-vid" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M10 9.2l4.6 2.8L10 14.8z" fill="currentColor" stroke="none"/></svg>
          <svg class="td-ic td-ic-file" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3v5h5"/><path d="M14 3H6.6A1.6 1.6 0 0 0 5 4.6v14.8A1.6 1.6 0 0 0 6.6 21h10.8a1.6 1.6 0 0 0 1.6-1.6V8z"/></svg>
        </span>
        <span class="term-drop__tit"></span>
        <span class="term-drop__hint"></span>
      </div>`;
    container.appendChild(ov);
    _dropOv = ov;
    return ov;
  }
  function _mostrarDrop(clase) {
    container.classList.add('drag-over');
    const TD  = window.TerminalDrop;
    const msg = TD ? TD.mensajeDrop(clase) : null;
    if (!msg) { if (container.dataset.drop) delete container.dataset.drop; return; }
    if (container.dataset.drop !== clase) {          // sólo re-pintar al cambiar de clase
      const ov = _asegurarDropOverlay();
      const tr = s => (window.JarvisI18n ? window.JarvisI18n.t(s) : s);
      ov.querySelector('.term-drop__tit').textContent  = tr(msg.titulo);
      ov.querySelector('.term-drop__hint').textContent = tr(msg.hint);
      container.dataset.drop = clase;
    }
  }
  function _ocultarDrop() {
    container.classList.remove('drag-over');
    if (container.dataset.drop) delete container.dataset.drop;
  }

  container.addEventListener('dragenter', e => {
    e.preventDefault();
    const TD = window.TerminalDrop;
    _mostrarDrop(TD ? TD.clasificarArrastre(e.dataTransfer) : null);
  });
  container.addEventListener('dragover', e => {
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    // Reclasificar en cada dragover mantiene el cartel sincronizado y lo re-muestra
    // si un dragleave espurio (al pasar sobre el canvas de xterm, que es hijo del
    // container) lo apagó un instante antes.
    const TD = window.TerminalDrop;
    _mostrarDrop(TD ? TD.clasificarArrastre(e.dataTransfer) : null);
  });
  container.addEventListener('dragleave', e => {
    // Sólo apagar cuando el puntero abandona la card ENTERA (no al pasar de un
    // hijo a otro: xterm dispara dragleave/dragenter espurios entre sus capas).
    if (!container.contains(e.relatedTarget)) _ocultarDrop();
  });
  container.addEventListener('drop', async e => {
    e.preventDefault();
    _ocultarDrop();

    const enviar = txt => {
      if (txt && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'input', data: txt }));
      }
    };

    // 1) Imagen o video: subir al backend → recibir ruta absoluta → pegar la ruta
    const TD = window.TerminalDrop;
    const media = [...(e.dataTransfer.files || [])].find(
      f => TD ? TD.esSubible(f.type) : (f.type || '').startsWith('image/'),
    );
    if (media) {
      const esVid = /^video\//i.test(media.type || '');
      try {
        const path = await _subirImagenTerminal(media, terminalId, media.name);
        enviar(path);
      } catch (err) {
        term.write(`\r\n\x1b[31mError subiendo ${esVid ? 'el video' : 'la imagen'}: ${err.message}\x1b[0m\r\n`);
      }
      term.focus();
      term.scrollToBottom();
      return;
    }

    // 2) Texto drageado (selección, URL, snippet del editor)
    const texto = e.dataTransfer.getData('text/plain');
    if (texto) {
      enviar(texto);
      term.focus();
      term.scrollToBottom();
      return;
    }

    // 3) Otros archivos: solo el nombre (el browser no expone la ruta absoluta)
    for (const file of e.dataTransfer.files) {
      enviar(file.name);
    }
    term.focus();
    term.scrollToBottom();
  });

  // ─── Paste (Ctrl+V / Cmd+V / click derecho → Pegar) ─────────────────────
  // Prioridad: TEXTO siempre gana sobre imagen (Windows pega image/*+text/plain
  // a la vez desde Slack/Excel/web). Imagen pura (screenshot): el browser YA
  // tiene los bytes acá → se sube a /upload-image (ms) y se pega la ruta con
  // bracketed paste. NO delegar a la CLI con \x16: eso la obligaba a leer el
  // clipboard de Windows vía interop WSL (powershell.exe, 3.5s+ solo el
  // arranque de .NET) → el "Pasting..." eterno de Claude Code. El \x16 queda
  // solo como fallback si la subida falla (en agentes; bash no tiene quién
  // lea el clipboard del OS). Decisiones en terminal-paste.js (puro, testeado).
  container.addEventListener('paste', async e => {
    e.preventDefault();
    e.stopPropagation();

    const TP = window.TerminalPaste;
    const texto = e.clipboardData?.getData('text/plain');
    const items = [...(e.clipboardData?.items || [])];
    // Sin el módulo puro (carga rota): degradar a solo-texto, jamás romper el paste.
    const plan = TP ? TP.planDePaste({ texto, items })
                    : (texto ? { accion: 'texto' } : { accion: 'nada' });

    // 1) Texto plano — mandar con bracketed paste markers.
    //    \x1b[200~ ... \x1b[201~ es el protocolo que Claude Code y bash con
    //    readline esperan: "todo entre estos markers es texto pegado, no lo
    //    interpretes como comandos". Sin markers, Claude Code queda en
    //    "Pasting..." esperando un final que nunca llega.
    if (plan.accion === 'texto') {
      _enviarTextoConBracketedPaste(texto, ws);
      term.scrollToBottom();
      return;
    }
    if (plan.accion !== 'imagen') return;

    // 2) Imagen en el clipboard: subir y pegar la ruta (mismo camino que el
    //    drag&drop; Claude Code adjunta la ruta pegada como [Image #N]).
    const tipo = document.getElementById(`ia-logo-${terminalId}`)?.dataset.tipo || tipoIa;
    try {
      const file = items[plan.indice].getAsFile();
      if (!file) throw new Error('clipboard sin archivo');
      const filename = TP.nombreImagenPegada({ nombre: file.name, mime: file.type, ts: Date.now() });
      const path = await _subirImagenTerminal(file, terminalId, filename);
      _enviarTextoConBracketedPaste(path, ws);
      term.scrollToBottom();
    } catch (err) {
      // Red de seguridad: en agentes la imagen sigue en el clipboard del OS
      // → \x16 dispara el paste nativo de la CLI (lento pero funciona).
      const fb = TP.fallbackImagenPaste(tipo);
      if (fb && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'input', data: fb }));
        term.scrollToBottom();
      } else {
        term.write(`\r\n\x1b[31mError pegando imagen: ${err.message}\x1b[0m\r\n`);
      }
    }
  }, true);

  // ─── Menú contextual (click derecho) ─────────────────────────────────────
  // Ctrl+click derecho conserva el menú nativo del browser como escape hatch.
  container.addEventListener('contextmenu', e => {
    if (e.ctrlKey) return;
    e.preventDefault();
    _mostrarMenuContextual(e.clientX, e.clientY, term, ws);
  });

  // (NO hay copy-on-select — pedido del usuario 2026-06-11: seleccionar con
  // el mouse NO debe pisar el clipboard solo; la copia la decide él con
  // Ctrl+C / Ctrl+Shift+C / menú contextual, que ya manejan la selección.)

  // ─── Skeleton de conexión: shimmer hasta el primer dato del WS ───────────
  const _skel = document.createElement('div');
  _skel.className = 'term-conectando';
  _skel.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
  container.appendChild(_skel);
  let _skelVivo = true;
  function _quitarSkeleton() {
    if (!_skelVivo) return;
    _skelVivo = false;
    _skel.remove();
  }
  // Failsafe: si en 6s no llegó nada, quitar igual (no tapar el cursor)
  setTimeout(_quitarSkeleton, 6000);

  // ─── WebSocket (apertura DIFERIDA: recién cuando la card tiene su tamaño) ───
  // El WS se abre dentro de un requestAnimationFrame, DESPUÉS de fitear. Motivo:
  // workspace.js llama TerminalLayout.add() JUSTO DESPUÉS de crearTerminal(), y
  // es ese add() el que aplica el template del grid (posiciona la card a su
  // tamaño real). Recién entonces fitAddon.fit() calcula los cols/rows
  // verdaderos. Si abríamos el WS síncrono, term.cols/rows todavía eran el
  // default 80×24 de xterm → el PTY del attach nacía 80×24, tmux redibujaba a 80
  // y el fit posterior (rAF) forzaba un SEGUNDO redraw al tamaño real: el TUI
  // refloweaba dos veces y xterm pintaba ambos frames encimados → "texto
  // desparramado". Naciendo el WS ya al tamaño real hay UN solo redraw, idéntico
  // en tmux y en xterm. Ver [[tmux-size-clamping]].
  function _abrirWS() {
    if (instancia._cerrando) {         // lo desconectaron mientras esperábamos el layout
      window.TerminalAttach?.listo?.(terminalId);   // no retener el slot de la cola
      return;
    }
    // Clamp al mismo piso degenerado que onResize por si fit() devolvió algo patológico.
    const _c = (term.cols >= 20) ? term.cols : 80;
    const _r = (term.rows >= 5)  ? term.rows : 24;
    const _obs = ES_OBSERVADOR ? '&observer=1' : '';
    // fc=1 ⇔ este cliente ackea bytes parseados (flow control, séptima capa de
    // [[tmux-size-clamping]]): sin TerminalFlow cargado NO se declara, así el
    // backend no espera acks que nunca van a llegar.
    const _fc = window.TerminalFlow ? '&fc=1' : '';
    ws = new WebSocket(`ws://${location.host}/ws/terminal/${terminalId}?cols=${_c}&rows=${_r}${_obs}${_fc}`);
    instancia.ws = ws;
    instancia._lastCols = _c;
    instancia._lastRows = _r;
    // Contador NUEVO por conexión (la reconexión arranca la cuenta de cero,
    // igual que el _FlujoWS del backend para ese WS).
    const _ack = window.TerminalFlow ? window.TerminalFlow.crearContadorAck() : null;

    ws.onopen = () => {
      // Evitar enviar resize redundante en la apertura (el PTY nace al tamaño de la URL)
      if (term.cols !== instancia._lastCols || term.rows !== instancia._lastRows) {
        _enviarResize(term, terminalId, ws);
      }
      // Estado inicial de visibilidad para el watermark adaptativo del flow
      // control (el backend arranca conservador/oculto hasta que se lo decimos).
      try { ws.send(JSON.stringify({ type: 'visible', v: document.visibilityState === 'visible' })); } catch (_) {}

      // El CLI lo lanza el BACKEND al crear la sesión tmux, como PROGRAMA del pane
      // (sin eco — ver _crear_sesion_tmux). El front YA NO lo tipea: hacerlo (a)
      // mostraba el comando `claude --session-id <uuid> …` escribiéndose en la
      // terminal y (b) en una reconexión podía caer como MENSAJE DENTRO del claude
      // ya vivo (el input con el comando de las fotos del usuario, 2026-07-06). Sin
      // tipeo del front, ninguno de los dos pasa. Si el launch-at-creation fallara y
      // el pane quedara en un shell, el usuario tipea el CLI a mano (caso raro).
    };

    // tmux envía el estado real del shell al hacer attach — output directo sin
    // JSON. El callback de term.write corre cuando xterm YA PARSEÓ el chunk
    // (en pestaña oculta, eso puede ser minutos después): ackear recién ahí es
    // lo que le dice al backend "el browser va al día" — si los acks se
    // atrasan, el backend frena la lectura del PTY y la cola de xterm nunca
    // llega a los 50MB donde tira datos. _w captura ESTE socket: un callback
    // rezagado de antes de una reconexión no contamina la cuenta del WS nuevo.
    const _w = ws;
    // COALESCING por frame: con agentes escupiendo output llegan cientos de chunks/seg.
    // Un term.write() (con su closure de ack) por chunk × 9 terminales = GC + overhead.
    // Juntamos los chunks del frame y los volcamos en UN solo write por requestAnimationFrame
    // → mucho menos trabajo, y todas las terminales SIGUEN mostrando output en vivo.
    // El ack se manda igual desde el callback de write (bytes YA PARSEADOS) → el flow
    // control no se toca. En pestaña oculta rAF se frena: el buffer queda acotado por el
    // watermark del backend (que frena la lectura al no recibir acks) — ver terminal-flow.js.
    const _CAP_INBUF  = 8 * 1024 * 1024;   // backlog máximo antes de healear (8MB)
    const _FLUSH_SIZE = 512 * 1024;        // en tab visible, drenar por tamaño además del frame
    // Retención de frames DEC 2026 (fix "franjas negras / negro al salir de
    // fullscreen", 2026-07-08): claude envuelve cada redraw en \x1b[?2026h...l;
    // xterm 5.3 ignora los marcadores, así que pintar un flush que cae a MITAD
    // de un frame grande muestra medio redraw (negro donde falta). Acá NUNCA se
    // pinta un frame abierto: cortarFrameSync (terminal-flow.js, pura+testeada)
    // retiene la cola sin cerrar hasta el próximo chunk, con dos válvulas: el
    // hold vence a los 150ms (app rota a mitad de frame) o el resto supera 2MB.
    const _HOLD_MS  = 150;
    const _HOLD_CAP = 2 * 1024 * 1024;
    // Tope de hold TOTAL (deep work 2026-07-08): el hold de 150ms se RE-ARMA
    // por chunk — un frame abierto que gotea chunks espaciados <150ms podría
    // retener la pantalla indefinidamente. Si venimos SIN pintar nada hace
    // más de este tope (sequía de paint por frame abierto), se suelta igual.
    const _HOLD_TOTAL_MS = 1000;
    let _inbuf = '', _inbufN = 0, _flushRAF = 0, _healBytes = 0, _holdVencido = false, _holdDesde = 0;
    let _rzHold = 0, _lastChunk = 0;   // cortina post-resize (ver abajo)
    const _flush = () => {
      _flushRAF = 0;
      if (instancia._cerrando || !_inbuf) { _inbuf = ''; _inbufN = 0; return; }
      // CORTINA DE RESIZE (2026-07-12, "colapsar la franja parpadea"): tras
      // avisarle un resize a tmux, su redraw llega como CLEAR + contenido en
      // chunks SEPARADOS — pintar el clear solo dejaba TODAS las cards en negro
      // ~1-3 frames (medido en video 30fps) hasta que llegaba el contenido.
      // Mientras la cortina esté armada y el stream siga activo (<45ms desde el
      // último chunk), retener y pintar TODO junto, atómico. Vence sola (400ms);
      // los acks acompañan al write real (igual que el hold DEC 2026).
      if (_rzHold) {
        const _ah = performance.now();
        if (_ah < _rzHold && _ah - _lastChunk < 45) {
          clearTimeout(instancia._rzTimer);
          instancia._rzTimer = setTimeout(_flush, 48);
          return;
        }
        _rzHold = 0;
      }
      let data = _inbuf, n = _inbufN;
      _inbuf = ''; _inbufN = 0;
      // COTA DURA: si el cliente quedó MUY atrás (tab oculta + flood largo), volcarle este
      // backlog enorme a xterm dispararía el descarte de su cola de 50MB → ANSI partido →
      // garble PERMANENTE. En vez de eso, repintar LIMPIO: term.reset() (parser + pantalla)
      // + refresh. CONTRATO 2026-07-02: refresh = el backend re-captura el pane y manda
      // un SEED completo (pantalla + scrollback + cursor + modos) — por eso el reset va
      // SIEMPRE inmediatamente antes (el seed cae en terminal virgen, sin duplicar).
      if (data.length > _CAP_INBUF) {
        try { term.reset(); } catch (_) {}
        if (_w.readyState === WebSocket.OPEN) {
          try { _w.send(JSON.stringify({ type: 'refresh' })); } catch (_) {}
          if (_ack) { const b = _ack.procesado(n); if (b > 0) { try { _w.send(JSON.stringify({ type: 'ack', bytes: b })); } catch (_) {} } }
        }
        _holdVencido = false;
        clearTimeout(instancia._holdTimer);
        return;
      }
      // Nunca pintar un frame 2026 a medias: retener lo no-cerrado en _inbuf.
      // El ack acompaña: se confirma SOLO lo que efectivamente se escribe ahora;
      // el resto se ackea cuando salga (los bytes retenidos siguen acá, no se pierden).
      const _ahora = performance.now();
      const _corte = window.TerminalFlow?.cortarFrameSync?.(data,
        { forzar: _holdVencido || data.length > _HOLD_CAP ||
                  (_holdDesde > 0 && _ahora - _holdDesde > _HOLD_TOTAL_MS) });
      _holdVencido = false;
      if (_corte && _corte.resto) {
        _inbuf = _corte.resto; _inbufN = _corte.resto.length;
        clearTimeout(instancia._holdTimer);
        instancia._holdTimer = setTimeout(() => { _holdVencido = true; _flush(); }, _HOLD_MS);
        if (!_corte.listo) {
          if (!_holdDesde) _holdDesde = _ahora;   // arranca la sequía de paint
          return;                                  // nada pintable aún: esperar el cierre
        }
        _holdDesde = 0;                            // pintamos algo: la sequía se corta
        data = _corte.listo; n = _corte.listo.length;
      } else {
        _holdDesde = 0;
        clearTimeout(instancia._holdTimer);
      }
      if (!_ack) { term.write(data); _programarHeal(n); return; }
      term.write(data, () => {
        const bytes = _ack.procesado(n);
        if (bytes > 0 && _w.readyState === WebSocket.OPEN) _w.send(JSON.stringify({ type: 'ack', bytes }));
      });
      _programarHeal(n);
    };
    // Auto-heal de RENDER (el "tipear lo cura" automatizado). El flow control evita
    // pérdida de bytes (terminal-flow.js) → el buffer/parser de xterm queda en sync
    // con tmux. Pero en GPUs reales una RÁFAGA PESADA puede dejar el CANVAS con un
    // frame viejo encimado (el render se atrasó, no el buffer) → se ve garble hasta
    // que algo repinta (tipear, F5). Tras una ráfaga pesada, cuando el output se
    // aquieta (debounce), repintamos el viewport UNA vez: local (term.refresh, sin
    // ida a tmux, sin flicker), no-op si ya estaba bien. Headless (SwiftShader) NO
    // reproduce esto; por eso el deep work no lo veía. Respeta visibilidad e interacción.
    const _HEAL_UMBRAL = 128 * 1024;   // solo tras ráfaga pesada — output liviano no dispara
    const _programarHeal = (n) => {
      _healBytes += n;
      if (_healBytes < _HEAL_UMBRAL) return;
      clearTimeout(instancia._healTimer);
      instancia._healTimer = setTimeout(() => {
        _healBytes = 0;
        if (instancia._cerrando || document.visibilityState !== 'visible') return;
        if (window.TerminalLayout?.isInteracting?.()) return;   // no durante drag/resize
        // pintarYa: el heal existe justo porque el render se atrasó — repintar por
        // el mismo rAF atrasado era pedirle el favor al que está en el piso.
        try { window.TerminalRender ? window.TerminalRender.pintarYa(term) : term.refresh(0, term.rows - 1); } catch (_) {}
      }, 650);
    };
    const _agendarFlush = () => {
      if (_flushRAF) return;   // ya hay un flush agendado (rAF o microtask)
      // decidirDrenado (terminal-flow.js, testeado): drenar YA por microtask cuando
      // (a) tab visible + backlog grande (flood: evita inflar _inbuf entre frames y un
      // write gigante a xterm), o (b) tab visible + el usuario acaba de tipear → ECO
      // interactivo: su tecla aparece sin esperar el frame de rAF. En tab OCULTA jamás
      // forzamos (rAF frenado protege la cola de 50MB de xterm; el backlog lo acota la cota dura).
      const yaMismo = window.TerminalFlow?.decidirDrenado?.({
        inbufN: _inbufN,
        flushSize: _FLUSH_SIZE,
        visible: document.visibilityState === 'visible',
        msDesdeInput: performance.now() - _ultimoInput,
      });
      if (yaMismo) {
        _flushRAF = -1; queueMicrotask(_flush);
      } else {
        _flushRAF = requestAnimationFrame(_flush);
      }
    };
    // Saneo al VOLVER de una pestaña oculta cuyo backlog se descartó (ver
    // decidirBacklogOculto en onmessage): reset + refresh → el backend
    // re-captura el pane y manda un SEED completo (contrato 2026-07-02, el
    // mismo camino que la cota dura de _flush). Lo dispara el handler global
    // de visibilitychange, ESCALONADO entre cards para que N seeds no se
    // parseen todos en el mismo frame.
    instancia._seedOculto = false;
    instancia._sanearTrasOculto = () => {
      if (!instancia._seedOculto || instancia._cerrando) return;
      if (document.visibilityState !== 'visible') return;   // volvió a ocultarse: queda pendiente
      instancia._seedOculto = false;
      // Restos post-descarte en _inbuf: pre-seed, descartarlos (ackeados igual).
      if (_ack && _inbufN > 0) {
        const b = _ack.procesado(_inbufN);
        if (b > 0 && _w.readyState === WebSocket.OPEN) { try { _w.send(JSON.stringify({ type: 'ack', bytes: b })); } catch (_) {} }
      }
      _inbuf = ''; _inbufN = 0;
      _holdVencido = false;
      clearTimeout(instancia._holdTimer);
      if (_w.readyState !== WebSocket.OPEN) return;   // la reconexión trae su propio seed
      try { term.reset(); } catch (_) {}
      try { _w.send(JSON.stringify({ type: 'refresh' })); } catch (_) {}
    };
    // Arma la cortina post-resize (la usa refitTerminal al mandar un resize real).
    // OJO: se asigna DESDE este scope — el literal de `instancia` vive afuera del
    // closure de _flush y desde ahí `_rzHold = ...` creaba un GLOBAL implícito
    // (la cortina nunca enganchaba; probado en QA con window._rzHold apareciendo).
    instancia.cortinaResize = (ms) => { _rzHold = performance.now() + ms; };
    // Post-resize de TUI sparse en primary (Grok): xterm ya refloweó el viewport
    // y los diffs 2026 no tapan los fragmentos. Tiramos el buffer local + el
    // inbuf de diffs y pedimos el seed de tmux (verdad del pane post-SIGWINCH).
    instancia._sanearSparsePrimary = () => {
      if (instancia._cerrando) return;
      _inbuf = ''; _inbufN = 0;
      _rzHold = 0;
      _holdVencido = false;
      clearTimeout(instancia._holdTimer);
      if (!_w || _w.readyState !== WebSocket.OPEN) return;
      try { term.reset(); } catch (_) {}
      try { _w.send(JSON.stringify({ type: 'refresh' })); } catch (_) {}
    };
    let _resynced = false;
    ws.onmessage = e => {
      // Señal para el diagnóstico post-update (shell/diag-update.js): bytes que
      // LLEGAN de las terminales, independiente del pintado. Costo: una suma.
      if (window._jarvisDiagRx != null) window._jarvisDiagRx += (e.data && e.data.length) || 0;
      _quitarSkeleton();
      // Re-sync de tamaño UNA vez al primer dato: si _fitYAbrir agotó sus reintentos y
      // abrió el WS con el default 80×24 (renderer no midió a tiempo), para cuando llega
      // y se pinta el primer chunk el renderer YA midió → un refit forzado corrige el cols
      // y manda el resize real a tmux (idempotente: no-op si ya estaba bien). Cierra el
      // caso "terminal clavada a 80 cols hasta F5".
      if (!_resynced) {
        _resynced = true;
        // El seed ya viajó: soltar el slot de la cola de attaches (el próximo
        // attach en espera arranca ahora — escalonado, no todos juntos).
        window.TerminalAttach?.listo?.(terminalId);
        // Recuperar el presupuesto de auto-reintentos SOLO si el WS sobrevive ~5s: si lo
        // reseteáramos con el primer byte, un server que flapea (acepta, manda el frame
        // inicial y muere) reattachearía infinito y el tope _MAX_AUTO dejaría de ser techo.
        setTimeout(() => { if (terminales.get(terminalId) === instancia && ws.readyState === WebSocket.OPEN) _autoIntento = 0; }, 5000);
        requestAnimationFrame(() => { try { refitTerminal(terminalId, true); } catch (_) {} });
      }
      // Medición: el PRIMER dato que vuelve tras una tecla es su eco → round-trip.
      // (marcarOutput ignora el output sin tecla previa: el flood del agente no cuenta.)
      if (_medidor) { const ms = _medidor.marcarOutput(performance.now()); if (ms != null) _pintarLatBadge(); }
      let datos = e.data;
      if (_eco) {
        // Shell: saltar los bytes ya pintados por el eco local (evita el doble char).
        // Y si el output es un REDIBUJO (no el eco de lo predicho), despintar los
        // chars predichos sin confirmar ANTES de escribirlo (undo), para que caiga
        // sobre un cursor sincronizado y no arrastre garble en el historial. El undo
        // va PREPENDIDO a `datos` (no un term.write suelto) para no adelantarse a lo
        // que ya esté encolado en _inbuf sin drenar.
        const plan = _eco.conciliar(datos);
        if (plan.saltear > 0) datos = datos.slice(plan.saltear);
        if (plan.undo > 0) datos = `\x1b[${plan.undo}D\x1b[K` + datos;
      }
      _inbuf += datos;
      _inbufN += datos.length;
      _lastChunk = performance.now();   // la cortina de resize mide el settle con esto
      // Firma del pintor: Grok (2026, sin alt-screen) vs Claude (1049) vs bash.
      if (window.TerminalFlow?.marcarPintorTui) {
        instancia._pintor = window.TerminalFlow.marcarPintorTui(instancia._pintor, datos);
      }
      // Pestaña OCULTA con backlog desbordado (2026-07-12): descartar en vez de
      // acumular MB que al volver se parseaban de un SAQUE en el main thread
      // (~10s de app congelada tras minutos de idle — el failsafe FC_TIMEOUT del
      // backend gotea bytes aunque el rAF congelado no ackee). Los bytes
      // descartados se ACKEAN igual (contabilidad del flow control intacta) y
      // queda marcado el saneo: al volver a visible, _sanearTrasOculto pide
      // reset+refresh (SEED completo) en vez de parsear la historia muerta.
      const _backlog = window.TerminalFlow?.decidirBacklogOculto?.({
        visible: document.visibilityState === 'visible',
        inbufN: _inbufN,
        seedPendiente: instancia._seedOculto,
        observador: ES_OBSERVADOR,
      });
      if (_backlog === 'descartar') {
        if (_ack) {
          const b = _ack.procesado(_inbufN);
          if (b > 0 && _w.readyState === WebSocket.OPEN) { try { _w.send(JSON.stringify({ type: 'ack', bytes: b })); } catch (_) {} }
        }
        _inbuf = ''; _inbufN = 0;
        instancia._seedOculto = true;
        return;
      }
      _agendarFlush();
    };

    ws.onerror = () => { /* el onclose subsiguiente muestra el overlay */ };

    ws.onclose = (ev) => {
      _quitarSkeleton();
      // Un WS que muere sin haber entregado su seed no debe retener el slot de
      // la cola de attaches (listo es idempotente si ya se liberó).
      window.TerminalAttach?.listo?.(terminalId);
      // Decisión pura (terminal-flow.js, testeada): intencional → nada; 4010 →
      // desplazado por otra vista dueña (sin auto-retry: sería ping-pong); página
      // recargándose (updater/boot_id) → nada, el reload hace el attach definitivo
      // (antes retry+reload corrían en paralelo = DOBLE re-attach por reinicio);
      // presupuesto disponible → programar; agotado → overlay manual.
      const dec = window.TerminalFlow?.decidirReintento?.({
        cerrando: !!instancia._cerrando, codigo: ev?.code,
        recargando: !!window.JarvisUpdater?.recargando, autoIntento: _autoIntento,
      }) ?? (instancia._cerrando ? 'nada' : 'overlay');   // sin TerminalFlow (cache vieja): overlay manual
      if (dec === 'nada') return;
      if (dec === 'desplazado') { _mostrarOverlayDesplazado(container, terminalId, tipoIa); return; }
      if (dec === 'terminada') { _mostrarOverlayTerminada(container, terminalId); return; }
      if (dec === 'programar') {
        // Auto-reintento ACOTADO: tmux persiste, así que reconectar es SIEMPRE seguro y
        // deseable. Caso típico: el server se reinicia (botón Actualizar / un agente) con
        // 6-9 terminales abiertas → sin esto el usuario clickea 'Reconectar' una por una.
        // Backoff corto (reusa el delay del WS de eventos: 1.5s→3s→6s).
        const delay = window.WsStatus?._pure?.proximoDelay?.(_autoIntento) ?? 1500;
        setTimeout(() => {
          if (!container.isConnected) return;                 // la card ya no está en el DOM
          if (instancia._cerrando || terminales.get(terminalId) !== instancia) return; // ya la reemplazaron/cerraron
          // La página decidió recargarse mientras esperábamos el backoff (el updater
          // detecta la caída ~1s después del onclose): abortar el reintento acá
          // también — este re-check en el disparo es el que gana la carrera.
          if (window.JarvisUpdater?.recargando) return;
          // En pestaña oculta NO reconectamos en silencio (N attaches a ciegas): dejamos
          // el overlay manual; al volver a la pestaña el usuario reconecta cuando quiera.
          if (document.visibilityState !== 'visible') { _mostrarOverlayReconexion(container, terminalId, tipoIa); return; }
          desconectarTerminal(terminalId);                    // limpia la instancia muerta
          crearTerminal(container.id, terminalId, tipoIa, _autoIntento + 1); // re-attach a tmux
        }, delay);
        return;
      }
      _mostrarOverlayReconexion(container, terminalId, tipoIa);
    };
  }

  // Apertura ESCALONADA (attach-queue.js): con N terminales re-attacheando a la
  // vez (post-update, F5, cambio de proyecto), abrir los N WS juntos parseaba N
  // seeds de 2000 líneas en el mismo instante = workspace congelado 3-5s. La
  // cola limita a CONCURRENCIA attaches en vuelo; el slot se suelta con el
  // primer dato (onmessage), el onclose, el cancelar o su timeout. Sin el
  // módulo (cache vieja): apertura directa, como siempre.
  const _abrirEnCola = () => {
    if (window.TerminalAttach) window.TerminalAttach.pedir(terminalId, _abrirWS);
    else _abrirWS();
  };

  // Fitear y abrir cuando el RENDERER de xterm ya midió la celda. No alcanza con
  // que el container tenga tamaño: en los primeros frames el renderer (WebGL/
  // Canvas) todavía no midió charWidth/Height → fitAddon.fit() es un NO-OP y term
  // queda en el default 80×24, así que el WS nacía a 80 y venía el doble redraw.
  // fitAddon.proposeDimensions() es la señal real de "renderer listo": devuelve
  // undefined mientras la celda mide 0 y {cols,rows} cuando ya midió. Reintento
  // acotado (~20 frames ≈ 320ms) + failsafe. Siempre vía rAF → `instancia`
  // (definido abajo) ya existe cuando _abrirWS() corre. Ver [[tmux-size-clamping]].
  (function _fitYAbrir(intentos) {
    requestAnimationFrame(() => {
      let dims = null;
      try { dims = fitAddon.proposeDimensions(); } catch (_) {}
      if (dims && dims.cols >= 20 && dims.rows >= 5) {
        _fitReal(term, container, fitAddon);   // aplica el tamaño REAL (resta el padding del body)
        // El gate validó con proposeDimensions (NO resta el padding H de 8px del
        // .terminal-body), pero _fitReal SÍ lo resta → puede quedar ~1 col/fila MENOS.
        // En el boundary (propose=20 / real=19) NO hay que abrir: _abrirWS clamparía
        // term.cols<20 a 80 → la URL del WS iría cols=80, el PTY nacería 80 y tmux
        // dibujaría a 80 mientras xterm renderiza 19 → desync persistente = garble.
        // Reintentamos para que term.cols (lo que va a la URL) === lo que ve tmux.
        if (term.cols >= 20 && term.rows >= 5) { _abrirEnCola(); return; }
      }
      if (intentos > 0) { _fitYAbrir(intentos - 1); return; }
      _fitReal(term, container, fitAddon);     // failsafe: si quedó degenerado, _abrirWS clampa a 80
      _abrirEnCola();
    });
  })(20);

  // Input del usuario → backend; bajar al final para ver lo que escribe
  term.onData(data => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      // Marca el instante del tecleo: el eco que vuelve enseguida se vuelca sin
      // esperar el frame de rAF (ver _agendarFlush / decidirDrenado).
      _ultimoInput = performance.now();
      if (_medidor) _medidor.marcarInput(_ultimoInput);
      // Eco local: el char imprimible aparece YA (0ms) en shells — se escribe al
      // buffer y se saltea cuando vuelve el eco real del server. PERO una tecla NO
      // imprimible (flechas ↑/↓, enter, backspace, ctrl, tab, secuencias/paste)
      // gatilla un REDIBUJO del shell (readline) con cursor RELATIVO; si dejamos
      // predicciones pintadas, el cursor queda corrido y el redibujo "come" letras
      // (bug del historial). Antes de mandarla, despintamos lo pendiente.
      if (_eco) {
        if (window.TerminalEcoLocal.esCharImprimible(data)) {
          if (_eco.predecir(data)) { try { term.write(data); } catch (_) {} }
        } else {
          const n = _eco.flush();
          if (n > 0) { try { term.write(`\x1b[${n}D\x1b[K`); } catch (_) {} }
        }
      }
      ws.send(JSON.stringify({ type: 'input', data }));
    }
    term.scrollToBottom();
  });

  // Resize de xterm → notificar al backend.
  // Filtramos tamaños degenerados: si el container está colapsado (browser
  // angosto, panel oculto), fit() puede calcular 1×1 o 0×0; mandar eso a
  // tmux hace que reformatee TODO el output una letra por línea y queda así
  // aunque después restauremos el tamaño.
  term.onResize(({ rows, cols }) => {
    // Durante un refit interno (doble-fit del escalado) NO mandamos cada fit: el
    // intermedio chico desincronizaba tmux. refitTerminal manda UN resize final.
    if (terminales.get(terminalId)?._suppressResize) return;
    if (cols < 20 || rows < 5) return;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'resize', rows, cols }));
    }
  });

  // ─── ResizeObserver: ajusta si cambia el tamaño del panel ─────────────────
  // Debounce 80ms para evitar tormentas de fit() durante el drag del resize.
  // Si el container está demasiado chico no hacemos fit — esperamos a que
  // recupere dimensiones razonables antes de pedirle a tmux nuevo tamaño.
  const observer = new ResizeObserver((entries) => {
    // No fitear mientras se arrastra/redimensiona: el reflow del grid dispara
    // este observer por frame y fit() es carísimo (lag ~0.5s). El fit final lo
    // hace el motor en pointerup vía _refit().
    if (window.TerminalLayout && window.TerminalLayout.isInteracting && window.TerminalLayout.isInteracting()) return;
    const { width, height } = entries[0].contentRect;
    if (width < 60 || height < 40) return;
    // Modo chico: NO refitear (no reformatear el output del agente → no se rompe).
    // La terminal queda en su último tamaño bueno; al agrandar vuelve a fitear.
    if (_modoChico(container)) return;
    // _resizeTimer vive en la instancia para poder cancelarlo en desconectarTerminal
    // (evita un refit fantasma tras el dispose si el observer disparó justo antes).
    clearTimeout(instancia._resizeTimer);
    instancia._resizeTimer = setTimeout(() => { refitTerminal(terminalId); }, 80);   // pasa por el escalado
  });
  observer.observe(container);

  const instancia = { term, ws, fitAddon, observer, container, ioRepintar: _ioRepintar, moWipe: _moWipe };
  terminales.set(terminalId, instancia);
  return instancia;
}

/**
 * Desconecta el WebSocket y destruye el terminal visualmente,
 * pero NO mata la sesión tmux — el agente sigue corriendo.
 */
function desconectarTerminal(terminalId) {
  const inst = terminales.get(terminalId);
  if (!inst) return;

  inst._cerrando = true; // evita que onclose muestre el overlay de reconexión
  window.TerminalAttach?.cancelar?.(terminalId);   // fuera de la cola de attaches (espera o slot activo)
  clearTimeout(inst._resizeTimer);   // matar el debounce pendiente del ResizeObserver (sin refit fantasma post-dispose)
  clearTimeout(inst._nudgeTimer);    // y el scroll fantasma pendiente (sin rueda post-dispose)
  clearTimeout(inst._rzTimer);       // y el flush pendiente de la cortina de resize
  clearTimeout(inst._healTimer);     // matar el debounce del auto-heal de render (sin refresh post-dispose)
  clearTimeout(inst._holdTimer);     // matar la válvula del frame 2026 retenido (sin flush post-dispose)
  clearTimeout(inst._sparseSanearTimer); // sanear Grok post-resize (sin reset+seed post-dispose)
  inst.observer.disconnect();
  try { inst.ioRepintar?.disconnect(); } catch (_) {}  // observer del repintado-al-mostrar
  try { inst.moWipe?.disconnect(); }    catch (_) {}  // observer del wipe de canvas (4ta capa)
  try { inst.ws?.close(); }    catch (_) {}  // ws puede ser null si aún no se abrió (espera de layout)
  try { inst.term.dispose(); } catch (_) {}
  terminales.delete(terminalId);
}

// Overlay de reconexión (banner glass con botón) — reemplaza el viejo
// mensaje ANSI "⚠ Sesión desconectada". tmux sigue vivo: reconectar es
// destruir la instancia xterm local y volver a crear el attach.
function _mostrarOverlayReconexion(container, terminalId, tipoIa) {
  if (!container.isConnected) return; // la card ya no está en el DOM
  container.querySelector('.term-reconnect')?.remove();
  const ov = document.createElement('div');
  ov.className = 'term-reconnect';
  ov.innerHTML = `
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.5L2.5 20h19L12 3.5zM12 10v4.5M12 17.5v.01"/></svg>
    <div class="term-reconnect-txt">
      <b>Conexión interrumpida</b>
      <span>Tu agente sigue trabajando — esto solo reconecta la vista</span>
    </div>
    <button class="term-reconnect-btn" type="button">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6"/></svg>
      Reconectar
    </button>`;
  ov.querySelector('.term-reconnect-btn').addEventListener('click', () => {
    ov.remove();
    desconectarTerminal(terminalId);          // limpia la instancia muerta
    crearTerminal(container.id, terminalId, tipoIa); // re-attach a tmux
  });
  container.appendChild(ov);
}

// Overlay "otra ventana tomó el control" (close 4010, contrato con el backend:
// al conectar un dueño nuevo sobre la misma terminal, el anterior es desplazado
// — un solo dueño de tamaño por sesión). Sin auto-retry: reintentar solo sería
// un ping-pong de desplazamientos entre las dos vistas. "Retomar acá" reconecta
// (y desplaza a la otra). Reusa el patrón/estilos del overlay de reconexión.
function _mostrarOverlayDesplazado(container, terminalId, tipoIa) {
  if (!container.isConnected) return;
  container.querySelector('.term-reconnect')?.remove();
  const ov = document.createElement('div');
  ov.className = 'term-reconnect';
  ov.innerHTML = `
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2.5" y="4" width="13" height="10" rx="1.5"/><rect x="8.5" y="10" width="13" height="10" rx="1.5"/></svg>
    <div class="term-reconnect-txt">
      <b>Esta terminal se está viendo en otra ventana</b>
      <span>El agente sigue trabajando — la otra vista tiene el control del tamaño</span>
    </div>
    <button class="term-reconnect-btn" type="button">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6"/></svg>
      Retomar acá
    </button>`;
  ov.querySelector('.term-reconnect-btn').addEventListener('click', () => {
    ov.remove();
    desconectarTerminal(terminalId);
    crearTerminal(container.id, terminalId, tipoIa);
  });
  container.appendChild(ov);
}

// Overlay "esta sesión terminó" (close 4404: el motor está sano y dice que la
// sesión ya no existe). NO ofrece reconectar a propósito — no hay a qué. El
// overlay de reconexión promete "tu agente sigue trabajando", y acá sería
// mentira: mandaba al usuario a apretar un botón que no podía funcionar nunca.
function _mostrarOverlayTerminada(container, terminalId) {
  if (!container.isConnected) return;
  container.querySelector('.term-reconnect')?.remove();
  const ov = document.createElement('div');
  ov.className = 'term-reconnect';
  ov.innerHTML = `
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9 12h6"/></svg>
    <div class="term-reconnect-txt">
      <b>Esta sesión terminó</b>
      <span>El proceso de la terminal se cerró — podés eliminar la card o abrir una nueva</span>
    </div>
    <button class="term-reconnect-btn" type="button">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>
      Eliminar
    </button>`;
  ov.querySelector('.term-reconnect-btn').addEventListener('click', () => {
    ov.remove();
    // El mismo camino que la ✕ de la card: un solo punto de eliminación.
    document.getElementById(`terminal-card-${terminalId}`)
      ?.querySelector('.t-btn-close')?.click();
  });
  container.appendChild(ov);
}

/**
 * Sana TODAS las terminales vivas con el contrato de refresh (2026-07-02):
 * term.reset() (pantalla + parser + scrollback local limpios) + {'type':'refresh'}
 * → el backend re-captura el pane y manda un SEED completo (pantalla + scrollback
 * de tmux + cursor + modos). Es el "F5 sin F5" real: lo usa el botón de reset de
 * terminales (workspace.js) para que resetear ARREGLE la vista, no solo el layout.
 */
function sanearTerminales() {
  for (const inst of terminales.values()) {
    const s = inst.ws;
    if (!s || s.readyState !== WebSocket.OPEN) continue;
    try { inst.term.reset(); } catch (_) {}
    try { s.send(JSON.stringify({ type: 'refresh' })); } catch (_) {}
  }
}
window.sanearTerminales = sanearTerminales;

/* ─────────────────────────────────────────────────────────────────────────────
 * La Escala de la app cuenta como devicePixelRatio (arreglo del "chat que no se
 * ve" al 80%).
 *
 * Con `zoom` en <html>, Chrome le da al canvas un buffer de píxeles REALES
 * escalado por el zoom (a 80%: 940px donde el CSS dice 1175), pero xterm 5.3
 * dibuja usando su celda en píxeles de dispositivo calculada con
 * window.devicePixelRatio — que el zoom NO mueve. Resultado a escalas < 100%:
 * xterm pinta más alto de lo que el buffer puede guardar y las últimas filas —el
 * composer del agente, justo donde uno escribe— NO SE DIBUJAN NUNCA. Están en el
 * buffer y las lee la API, pero el píxel no existe: ningún refresh las revive.
 *
 * Arreglo: el dpr que ve xterm pasa a ser devicePixelRatio × zoom, así su celda
 * de dispositivo queda en la misma unidad que el buffer del canvas. A escala 100%
 * el factor es 1 y todo queda EXACTAMENTE como antes.
 * ───────────────────────────────────────────────────────────────────────────── */
function _zoomApp() {
  try {
    const z = parseFloat(document.documentElement.style.zoom);
    return (Number.isFinite(z) && z > 0) ? z : 1;
  } catch (_) { return 1; }
}

function _engancharDprEscala(term) {
  try {
    const cbs = term._core?._coreBrowserService;
    if (!cbs) return;
    const propio = Object.getOwnPropertyDescriptor(cbs, 'dpr');
    if (propio && propio.get && propio.get._escala) return;   // ya enganchado
    const get = () => (window.devicePixelRatio || 1) * _zoomApp();
    get._escala = true;
    Object.defineProperty(cbs, 'dpr', { configurable: true, get });
  } catch (_) {}
}

/**
 * El mouse cae en la celda que uno ve (arreglo de "selecciono y me marca otra línea").
 *
 * xterm mapea píxel → celda restando el rect del elemento (que con `zoom` viene en
 * píxeles de PANTALLA) y dividiendo por la celda en píxeles CSS. Son dos unidades
 * distintas en cuanto la Escala no es 100%: el error crece con la distancia al
 * borde de arriba, así que al 80% la selección se corre hacia arriba una fila cada
 * ~5, y al 125% hacia abajo. Afecta a la selección, al doble-click, a los links y
 * al reporte de mouse que reciben las TUIs (los clicks dentro de claude).
 *
 * Acá le entregamos el evento ya traducido a píxeles CSS: misma unidad que la celda.
 * A escala 100% no se toca nada.
 */
function _engancharMouseEscala(term) {
  try {
    const ms = term._core?._mouseService;
    if (!ms) return;
    // Evento traducido a píxeles CSS respecto del elemento (Object.create conserva
    // el resto de las props: los originales solo leen clientX/Y, pero no le
    // escondemos nada por si eso cambia).
    const traducir = (event, element, z) => {
      const r = element.getBoundingClientRect();
      return Object.create(event, {
        clientX: { value: r.left + (event.clientX - r.left) / z },
        clientY: { value: r.top  + (event.clientY - r.top)  / z },
      });
    };
    // Los DOS caminos: getCoords (selección, doble-click, links) y
    // getMouseReportCoords (lo que se le reporta a la app que trackea el mouse —
    // el highlight propio de claude salía en otra fila justo por esto).
    for (const metodo of ['getCoords', 'getMouseReportCoords']) {
      const fn = ms[metodo];
      if (typeof fn !== 'function' || fn._escala) continue;
      const orig = fn.bind(ms);
      const conEscala = function (event, element, ...resto) {
        const z = _zoomApp();
        if (z === 1 || !element || !event) return orig(event, element, ...resto);
        return orig(traducir(event, element, z), element, ...resto);
      };
      conEscala._escala = true;
      ms[metodo] = conEscala;
    }
  } catch (_) {}
}

/** Avisa al renderer que su dpr efectivo cambió (lo llama el refit cuando detecta
 *  que la Escala se movió). Sin esto la celda queda medida a la escala vieja. */
function _avisarDprEscala(inst) {
  const z = _zoomApp();
  if (inst._zoomVisto === z) return false;
  inst._zoomVisto = z;
  try { inst.term._core._renderService.handleDevicePixelRatioChange(); } catch (_) {}
  return true;
}

/**
 * Fitea xterm al área de contenido REAL de la card (reemplaza fitAddon.fit()).
 * fitAddon.fit() de xterm 5.3 sobreestima ~1 fila porque toma el alto border-box
 * del `.terminal-body` (incluye los 39+4px de la píldora) sin restar ese padding
 * — la fila de más sobresale y la clipea overflow:hidden → "no se ve el fondo".
 * Acá restamos el padding real del contenedor antes de dividir por la celda y
 * aplicamos con term.resize (mismo mecanismo que fit(), dimensiones correctas).
 * Degrada a fitAddon.fit() si el renderer aún no midió la celda o no cargó
 * TerminalFit (cache vieja). Ver terminal-fit.js + [[tmux-size-clamping]].
 */
function _fitReal(term, container, fitAddon) {
  const TF = window.TerminalFit;
  // Celda en px de DISPOSITIVO (con la de CSS como red de seguridad para caches
  // viejas): es la unidad del buffer del canvas, la única que se dibuja de verdad.
  const leerCelda = () => {
    try {
      const d = term._core._renderService.dimensions;
      const dev = d.device && d.device.cell;
      const c = (dev && dev.width > 0 && dev.height > 0) ? dev : (d.css && d.css.cell);
      return (c && c.width > 0 && c.height > 0) ? { width: c.width, height: c.height } : null;
    } catch (_) { return null; }
  };
  // px de dispositivo por px CSS: el zoom de la Escala × el dpr de la pantalla.
  const factorDispositivo = () => {
    const f = _zoomApp() * (window.devicePixelRatio || 1);
    return (Number.isFinite(f) && f > 0) ? f : 1;
  };
  let cell = leerCelda();
  if (!TF || !cell) {
    try { fitAddon && fitAddon.fit(); } catch (_) {}   // fallback: el bug viejo, mejor que no fitear
    return;
  }
  const cs = getComputedStyle(container);
  const padW = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
  const padH = parseFloat(cs.paddingTop)  + parseFloat(cs.paddingBottom);

  // VERIFICADO, no de una sola pasada: al cambiar la Escala de la app (zoom en
  // <html>) xterm RE-MIDE su celda y el alto puede saltar (23 → 25px CSS a 125%).
  // Con una sola pasada las filas salen calculadas con la celda VIEJA y el canvas
  // termina más alto que la card: lo de abajo —el composer del agente, justo lo
  // que uno escribe— queda tapado por el overflow:hidden. Recalculamos mientras
  // la celda cambie bajo nuestros pies (tope 3, por si el renderer oscila).
  for (let intento = 0; intento < 3; intento++) {
    const { cols, rows } = (TF.dimsDispositivo
      ? TF.dimsDispositivo(container.clientWidth, container.clientHeight, padW, padH,
                           cell.width, cell.height, factorDispositivo())
      : TF.dimsReales(container.clientWidth, container.clientHeight, padW, padH,
                      cell.width, cell.height));
    if (cols !== term.cols || rows !== term.rows) {
      try { term.resize(cols, rows); } catch (_) { return; }
    }
    const nueva = leerCelda();
    if (!nueva || (nueva.width === cell.width && nueva.height === cell.height)) break;
    cell = nueva;
  }
  _recortarAlBuffer(term, container);
}

/**
 * Última palabra: la grilla no puede pedir más píxeles de los que tiene el buffer
 * del canvas. Con la Escala puesta, el buffer lo redondea el navegador (css × zoom)
 * y el cálculo por celdas puede pasarse por UN píxel — y ese píxel es una fila
 * entera que no se dibuja nunca (la del composer del agente). Acá medimos el
 * buffer REAL y, si no entra, soltamos una fila/columna. A escala 100% el buffer
 * es exacto y esto no toca nada.
 */
function _recortarAlBuffer(term, container) {
  try {
    const TF = window.TerminalFit;
    const cv = container.querySelector('canvas');
    const dev = term._core._renderService.dimensions.device.cell;
    if (!cv || !cv.height || !cv.width || !dev || !(dev.width > 0) || !(dev.height > 0)) return;
    const rows = Math.max(TF.MIN_ROWS, Math.min(term.rows, Math.floor((cv.height + 0.5) / dev.height)));
    const cols = Math.max(TF.MIN_COLS, Math.min(term.cols, Math.floor((cv.width + 0.5) / dev.width)));
    if (rows !== term.rows || cols !== term.cols) term.resize(cols, rows);
  } catch (_) {}
}

/**
 * Fuerza un fit en un terminal específico.
 * En modo chico NO fitea (no rompe el output), salvo force=true (fullscreen).
 */
function refitTerminal(terminalId, force = false) {
  const inst = terminales.get(terminalId);
  if (!inst) return;
  const cont = inst.container;
  // ¿Se movió la Escala de la app desde el último fit? Entonces el dpr efectivo
  // del renderer cambió: hay que re-medir la celda ANTES de calcular filas (y sin
  // dedupe, porque el contenedor puede medir lo mismo en px CSS).
  const escalaCambio = _avisarDprEscala(inst);
  if (escalaCambio) { inst._lastFitW = -1; inst._lastFitH = -1; }
  if (!force && _modoChico(cont)) return;
  // Gatear por interacción para TODO caller (no solo el ResizeObserver): durante un drag
  // del splitter del dock / resize de card, refitear por frame es carísimo y desincroniza.
  // El fit real ocurre al soltar. (force=true para maximize pasa igual.)
  if (!force && window.TerminalLayout?.isInteracting?.()) return;
  const W = cont.clientWidth, H = cont.clientHeight;
  if (W < MIN_FIT_W || H < MIN_FIT_H) return;
  // Dedupe (2026-07-12): tras un cambio de layout (colapsar franja, splitter del
  // dock) el MISMO tamaño llega por DOS caminos — el _refit del motor (60ms) y el
  // ResizeObserver por-card (80ms). La segunda pasada re-corría _fitReal+repaint
  // completos (~60-160ms de main thread POR TERMINAL bajo congestión) para no
  // cambiar nada → parpadeo/jank del toggle. Si el contenedor mide EXACTO lo del
  // último fit exitoso, no hay nada que hacer. force (maximizar/re-sync) pasa igual.
  if (!force && inst._lastFitW === W && inst._lastFitH === H) return;
  // (Limpieza legacy: ESCALAR el elemento con transform rompía la selección con
  // mouse — xterm mapea el pixel a celda sin saber del transform.)
  const xtermEl = cont.querySelector('.xterm');
  if (xtermEl && xtermEl.style.transform) { xtermEl.style.transform = ''; xtermEl.style.transformOrigin = ''; }

  // Fit en SILENCIO (sin disparar el resize a tmux desde onResize): medimos acá y
  // al final mandamos UN solo resize idempotente. La fuente es FIJA — un solo fit,
  // sin doble-fit ni escalado (ver el comentario de política arriba de _modoChico).
  inst._suppressResize = true;
  try {
    _fitReal(inst.term, cont, inst.fitAddon);
    inst._lastFitW = W; inst._lastFitH = H;   // contabilidad del dedupe (solo con fit OK)
  } catch (_) {
  } finally {
    inst._suppressResize = false;
  }

  // UN solo resize final, IDEMPOTENTE (no re-mandar si cols/rows no cambiaron → colapsa
  // refits redundantes) e INMEDIATO. El throttle de 150ms que vivía acá se ELIMINÓ
  // (2026-07-02): metía una ventana entre el tamaño de xterm y el aviso a tmux, y esa
  // ventana generó dos familias de bugs (duplicación al scrollear — capa 8 — y
  // "cortadas a media card" al interactuar con la contabilidad). En el motor de UN
  // emulador el resize es UNA orden barata (refresh-client -C); las ráfagas de drags
  // ya las frenan el gate de interacción y el debounce del ResizeObserver.
  //
  // CONTABILIDAD HONESTA (fix 2026-07-02, endurecido 2026-07-08): _lastCols se
  // marca SOLO cuando el send NO lanzó — antes se marcaba ANTES del send, y un
  // send fallido dejaba al gate creyendo que tmux ya estaba en esas medidas →
  // TODO refit futuro a ese tamaño se salteaba y tmux quedaba desincronizado
  // sin cura (deep work 2026-07-08, hueco #2). Con el WS aún CONECTANDO no se
  // marca nada y los reintentos existentes (onopen / re-sync) corrigen solos.
  const c = inst.term.cols, r = inst.term.rows;
  if (c >= 20 && r >= 5 && (c !== inst._lastCols || r !== inst._lastRows) &&
      inst.ws && inst.ws.readyState === WebSocket.OPEN) {
    try {
      inst.ws.send(JSON.stringify({ type: 'resize', rows: r, cols: c }));
      inst._lastCols = c; inst._lastRows = r;
      // El redraw de tmux viene en camino (clear + contenido): armar la cortina
      // para pintarlo atómico — sin el pantallazo negro del clear suelto.
      inst.cortinaResize?.(400);
      // Grok (TUI sparse en primary): xterm acaba de reflowear el viewport.
      // Dentro de la cortina, cuando Grok ya pintó tmux, reset+seed tapa los
      // fragmentos. Claude alt no entra (debeSanearSparsePrimary = false).
      let _alt = false;
      try { _alt = inst.term.buffer.active.type === 'alternate'; } catch (_) {}
      if (window.TerminalFlow?.debeSanearSparsePrimary?.({
        alt: _alt,
        vioSync2026: !!inst._pintor?.vioSync2026,
        vioAltScreen: !!inst._pintor?.vioAltScreen,
        observador: ES_OBSERVADOR,
        wsAbierto: true,
      })) {
        const espera = window.TerminalFlow.SPARSE_SANEAR_MS || 280;
        clearTimeout(inst._sparseSanearTimer);
        inst._sparseSanearTimer = setTimeout(() => {
          try { inst._sanearSparsePrimary?.(); } catch (_) {}
        }, espera);
      }
      // Scroll fantasma (2026-07-18): si tras este resize claude fullscreen queda
      // con el transcript EN BLANCO (idle no redibuja en SIGWINCH — upstream
      // #43273; solo repinta su franja de abajo + pill), la única cura es la
      // rueda — la mandamos nosotros (arriba+abajo, neto cero) cuando la firma
      // del blanco está presente. Chequeo a 650ms (post-cortina) y reintento a
      // ~1.4s (post re-seed del watchdog). Ver terminal-nudge.js.
      inst._nudgeHecho = false;
      clearTimeout(inst._nudgeTimer);
      inst._nudgeTimer = setTimeout(() => _nudgePostResize(terminalId, 0), 650);
    } catch (_) {}
  }
  // Repintar el viewport SINCRÓNICO (pintarYa, NO term.refresh): el term.resize de
  // _fitReal acaba de BORRAR el canvas (CanvasAddon reasigna canvas.width) y
  // term.refresh solo agenda el rAF del debouncer — que bajo carga se starva por
  // segundos → card NEGRA hasta scrollear (curado 2026-07-08). Pintando en esta
  // misma task el compositor jamás muestra el canvas vacío. Además el refresh
  // repinta el atlas nuevo si cambió la fuente (parte del garble visual).
  // Ver terminal-render.js + [[negro-al-maximizar-raf-starvation]].
  try {
    if (window.TerminalRender) window.TerminalRender.pintarYa(inst.term);
    else inst.term.refresh(0, inst.term.rows - 1);
  } catch (_) {}
}
window.refitTerminal = refitTerminal;

// ─── Helpers privados ─────────────────────────────────────────────────────────

/**
 * Scroll fantasma post-resize (ver terminal-nudge.js + [[negro-fullscreen-frames-2026]]):
 * si claude fullscreen quedó con el transcript en blanco (idle sordo al SIGWINCH),
 * mandarle la rueda que el usuario mandaría — arriba + abajo, neto cero — por el
 * MISMO camino que una rueda real. Gates duros: solo alt-screen + app trackeando
 * mouse + firma del blanco presente + one-shot por resize + cooldown. Kill-switch
 * runtime: window._jarvisNudgeOff = 1 (mismo criterio que cortarFrameSync).
 */
function _nudgePostResize(terminalId, intento) {
  const inst = terminales.get(terminalId);
  if (!inst || inst._cerrando || inst._nudgeHecho) return;
  if (ES_OBSERVADOR || window._jarvisNudgeOff) return;
  const TN = window.TerminalNudge;
  if (!TN) return;
  const reintentar = () => {
    // Un solo reintento (~1.4s post-resize): después del re-seed del watchdog,
    // que puede haber pintado la verdad de tmux (que TAMBIÉN trae el blanco).
    if (intento < 1) inst._nudgeTimer = setTimeout(() => _nudgePostResize(terminalId, intento + 1), 750);
  };
  if (window.TerminalLayout?.isInteracting?.()) { reintentar(); return; }
  const term = inst.term;
  let alt = false, mouse = false, cms = null, filas = [];
  try {
    alt = term.buffer.active.type === 'alternate';
    cms = term._core.coreMouseService;
    mouse = !!cms.areMouseEventsActive;
    if (alt && mouse) {
      const buf = term.buffer.active;
      for (let i = 0; i < term.rows; i++) {
        const ln = buf.getLine(buf.viewportY + i);
        filas.push(ln ? ln.translateToString(true) : '');
      }
    }
  } catch (_) { return; }
  const ahora = performance.now();
  const ok = TN.debeNudgear({
    alt, mouse,
    firma: alt && mouse && TN.firmaTranscriptVacio(filas),
    yaNudgeado: inst._nudgeHecho,
    msDesdeNudge: inst._nudgeTs ? ahora - inst._nudgeTs : null,
  });
  if (!ok) {
    // La firma puede aparecer recién con el re-seed (o el service seguir a medio
    // asentar): un reintento y listo — jamás un loop.
    if (alt && mouse) reintentar();
    return;
  }
  inst._nudgeHecho = true;
  inst._nudgeTs = ahora;
  const evs = TN.eventosRueda(term.cols, term.rows);
  try { cms.triggerMouseEvent(evs[0]); } catch (_) {}
  // Gap corto entre arriba y abajo: claude procesa el primero y re-layoutea;
  // el segundo lo devuelve al fondo (si estaba ahí) y re-engancha el follow.
  inst._nudgeTimer = setTimeout(() => {
    try { if (!inst._cerrando) cms.triggerMouseEvent(evs[1]); } catch (_) {}
  }, 90);
}

function _enviarResize(term, terminalId, ws) {
  const socket = ws || terminales.get(terminalId)?.ws;
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  socket.send(JSON.stringify({ type: 'resize', rows: term.rows, cols: term.cols }));
  // Contabilidad honesta también acá (auditoría 2026-07-02): sin esto, el envío
  // del onopen dejaba _lastCols en los valores de la URL (p.ej. el failsafe
  // 80×24) → si la card volvía a medir EXACTO ese tamaño, el gate idempotente
  // de refitTerminal lo salteaba y tmux quedaba desincronizado sin cura.
  const inst = terminales.get(terminalId);
  if (inst) { inst._lastCols = term.cols; inst._lastRows = term.rows; }
}

// ─── Helpers de copy/paste y menú contextual ────────────────────────────────

const _MOD_KEY_NAMES = new Set(['Control', 'Shift', 'Alt', 'Meta', 'AltGraph']);
function _esModificadorPuro(e) {
  return _MOD_KEY_NAMES.has(e.key);
}

// Copia robusta: intenta Clipboard API y cae a execCommand si falla
// (pasa cuando el browser perdió foco, hay otra pestaña activa, o el
// gesture context se diluyó). Muestra un toast como feedback visible.
function _copiarSeleccion(term) {
  const sel = term.getSelection();
  if (!sel) return false;
  _copiarTextoAlClipboard(sel);
  return true;
}

function _copiarTextoAlClipboard(texto) {
  if (!texto) return;
  // Intento moderno
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(texto).then(
      () => window.toast?.('Copiado', 'success', 1400),
      () => _copiarLegacy(texto),
    );
    return;
  }
  _copiarLegacy(texto);
}

function _copiarLegacy(texto) {
  const ta = document.createElement('textarea');
  ta.value = texto;
  ta.setAttribute('readonly', '');
  ta.style.position = 'fixed';
  ta.style.top      = '0';
  ta.style.left     = '-9999px';
  ta.style.opacity  = '0';
  document.body.appendChild(ta);
  const prevSelection = document.getSelection().rangeCount
    ? document.getSelection().getRangeAt(0) : null;
  ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (_) {}
  document.body.removeChild(ta);
  if (prevSelection) {
    document.getSelection().removeAllRanges();
    document.getSelection().addRange(prevSelection);
  }
  window.toast?.(ok ? 'Copiado' : 'No se pudo copiar', ok ? 'success' : 'error', 1400);
}

// Sube una imagen o video (File del clipboard o del drag&drop) al backend y
// devuelve su ruta absoluta en disco (/tmp/jarvis_uploads/...). El browser no
// expone file.path, así que el backend la materializa para que la CLI del pane
// pueda leerla. Dos transportes: imagen → base64-en-JSON a /upload-image (el
// camino del paste, chico y en milisegundos); video → multipart a /upload-media
// (streaming a disco: un video real revienta el tope de 15 MB del camino base64).
async function _subirImagenTerminal(file, terminalId, filename) {
  let resp;
  if (/^video\//i.test(file.type || '')) {
    const fd = new FormData();
    fd.append('archivo', file, filename || file.name || 'video.mp4');
    resp = await fetch(`/api/terminals/${terminalId}/upload-media`, { method: 'POST', body: fd });
  } else {
    const dataUrl = await new Promise((res, rej) => {
      const r = new FileReader();
      r.onload  = () => res(r.result);
      r.onerror = rej;
      r.readAsDataURL(file);
    });
    resp = await fetch(`/api/terminals/${terminalId}/upload-image`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ image_base64: dataUrl.split(',')[1], filename }),
    });
  }
  if (!resp.ok) {
    // Mostrar el detail del backend ("Video demasiado grande (máx 200 MB)")
    // en vez de un "HTTP 413" pelado — es lo que el usuario ve en el pane.
    let msg = `HTTP ${resp.status}`;
    try {
      const detail = (await resp.json()).detail;
      if (detail && typeof detail === 'string') msg = detail;
    } catch (_) { /* body no-JSON (proxy caído, etc.): queda el status */ }
    throw new Error(msg);
  }
  return (await resp.json()).path;
}

function _enviarTextoConBracketedPaste(texto, ws) {
  if (!texto || !ws || ws.readyState !== WebSocket.OPEN) return;
  // Sanear marcadores de bracketed paste embebidos en el texto: un \x1b[201~
  // adentro (típico al copiar logs/dumps con escapes ANSI) cerraría el paste
  // prematuramente y el resto se interpretaría como teclado → inyección de
  // comandos en el shell/TUI.
  const limpio = texto.replace(/\r?\n/g, '\r').replace(/\x1b\[20[01]~/g, '');
  const data = '\x1b[200~' + limpio + '\x1b[201~';
  ws.send(JSON.stringify({ type: 'input', data }));
}

async function _pasteDesdeClipboard(ws) {
  try {
    const texto = await navigator.clipboard.readText();
    _enviarTextoConBracketedPaste(texto, ws);
  } catch (_) {
    // El browser puede negar readText() si la pestaña no tiene foco;
    // silencioso, el usuario puede usar Ctrl+V tradicional como fallback.
  }
}

function _cerrarMenuContextual() {
  document.querySelectorAll('.term-ctxmenu').forEach(m => {
    if (m._desarmarCierre) m._desarmarCierre();
    m.remove();
  });
}

function _mostrarMenuContextual(x, y, term, ws, copiar) {
  _cerrarMenuContextual();
  const tieneSeleccion = term.hasSelection();
  const menu = document.createElement('div');
  menu.className = 'term-ctxmenu';
  menu.innerHTML = `
    <button class="term-ctxmenu-item" data-action="copy" ${tieneSeleccion ? '' : 'disabled'}>Copiar</button>
    <button class="term-ctxmenu-item" data-action="paste">Pegar</button>
    <button class="term-ctxmenu-item" data-action="select-all">Seleccionar todo</button>
    <button class="term-ctxmenu-item term-ctxmenu-sep" data-action="clear">Limpiar</button>
  `;
  document.body.appendChild(menu);

  // Posicionar evitando que se salga del viewport. x/y llegan del evento (píxeles
  // de PANTALLA) y left/top se escriben en píxeles CSS, que la Escala vuelve a
  // multiplicar: sin traducir, a 125% el menú aparecía 25% más abajo y a la derecha
  // del click. El viewport útil también se mide en px CSS.
  const z = window.JarvisEscala?.zoom?.() || 1;
  const { offsetWidth: mw, offsetHeight: mh } = menu;
  const px = Math.min(x / z, (window.innerWidth  / z) - mw - 4);
  const py = Math.min(y / z, (window.innerHeight / z) - mh - 4);
  menu.style.left = `${px}px`;
  menu.style.top  = `${py}px`;

  menu.addEventListener('click', async ev => {
    const btn = ev.target.closest('.term-ctxmenu-item');
    if (!btn || btn.disabled) return;
    const action = btn.dataset.action;
    if (action === 'copy')       (copiar ? copiar() : _copiarSeleccion(term));
    if (action === 'paste')      await _pasteDesdeClipboard(ws);
    if (action === 'select-all') term.selectAll();
    if (action === 'clear')      term.clear();
    _cerrarMenuContextual();
  });

  // Cerrar al mousedown afuera o Escape — listeners PERSISTENTES hasta que
  // el menú muera ({once:true} se consumía con un mousedown que no cerraba y
  // el menú quedaba clavado; ver terminal-ctxmenu.js). Armar acá es seguro:
  // el mousedown del click derecho que abre ya pasó (contextmenu llega después).
  if (window.TerminalCtxmenu) {
    menu._desarmarCierre = window.TerminalCtxmenu.armarCierre(document, menu, _cerrarMenuContextual);
  }
}
