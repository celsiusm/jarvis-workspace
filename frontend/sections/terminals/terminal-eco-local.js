// JARVIS — Eco local predictivo de la terminal.
//
// Pinta la tecla del usuario al INSTANTE, local, sin esperar el round-trip
// (tecla → WS → PTY → tmux → eco → WS → xterm). Es el mismo mecanismo que hace
// que una terminal nativa se sienta a 0ms: el eco no viaja. Crítico cuando el
// round-trip tiene PICOS (p90 ~600ms en el cruce browser↔WSL): la tecla aparece
// ya y el pico se vuelve invisible.
//
// Reconciliación con el eco REAL del server, que llega ~12-600ms después:
//  - Eco lineal: el server devuelve el char tal cual → coincide con lo predicho
//    → se saltea (no se pinta dos veces).
//  - REDIBUJO: el shell (readline) NO siempre hace eco lineal. Al navegar el
//    historial (↑/↓), editar en el medio de la línea o envolver una línea larga,
//    REDIBUJA con movimientos RELATIVOS de cursor (\b, \x1b[C, \x1b[K…). Ese
//    output NO es el eco del char predicho. Como nuestra predicción se pintó al
//    buffer y AVANZÓ el cursor real, dejarla ahí desincroniza el cursor y cada
//    redibujo relativo queda 1 columna corrido → "come" una letra y acumula
//    garble (bug del historial cazado 2026-07-06). Por eso, ante un output que no
//    es el eco de lo pendiente, devolvemos `undo` = cuántos chars predichos SIN
//    confirmar hay que DESPINTAR antes de dejarlo pasar; el caller emite la
//    secuencia de borrado y así el redibujo cae sobre un cursor sincronizado.
//
// NO TODO LO QUE SE TIPEA TIENE ECO (auditoría de tipeo 2026-10):
//  - Una contraseña (sudo/ssh/gpg/`read -s`): el tty NO la devuelve. La
//    predicción la dejaba PINTADA en pantalla hasta el Enter. Ahora una
//    predicción sin confirmar tras `esperaMs` se despinta (`vencer`) y el eco se
//    SUSPENDE: las teclas siguientes se anotan como SONDA (no se pintan) y
//    recién cuando el server devuelve un eco real el predictor se reactiva. Un
//    prompt de secreto conocido ni siquiera arranca pintando (esPromptSecreto).
//  - Una app a pantalla completa / con mouse (vim, less, htop, un TUI dentro de
//    un shell): escribir el char al buffer pisaba la pantalla de la app y el
//    undo borraba el resto de la línea. `puedePredecir` lo veda.
//  - Output del server todavía sin volcar a xterm (`_inbuf`), borde derecho de
//    la fila o texto a la derecha del cursor (edición en el medio): el char
//    predicho caería en otro lugar que el eco real → solo se predice AL FINAL
//    de la línea y con el stream al día.
//
// Contabilidad pura (testeada en Node); el wiring con xterm (write local del
// char predicho, saltear bytes ya pintados, secuencia de borrado del `undo` y
// `flush()` antes de las teclas no imprimibles) vive en terminal.js.
(function (global) {
  'use strict';

  function esCharImprimible(ch) {
    if (typeof ch !== 'string' || ch.length !== 1) return false;
    const code = ch.charCodeAt(0);
    // Imprimible de 1 code unit: ASCII + Latin-1 (á é í ó ú ñ ü ¿ ¡) + resto del BMP.
    // El eco real del shell devuelve el MISMO char → conciliar() lo saltea igual que
    // el ASCII. Se excluye: C0/DEL/C1 (control), mitades de surrogate (no son 1 char
    // válido) y combining marks (se predicen mal solas — los acentos del español son
    // precompuestos en Latin-1, no combinantes).
    if (code < 0x20) return false;
    if (code >= 0x7f && code <= 0x9f) return false;
    if (code >= 0xd800 && code <= 0xdfff) return false;
    if (code >= 0x0300 && code <= 0x036f) return false;
    return true;
  }

  // ¿El texto que hay ANTES del cursor es el prompt de un secreto (contraseña,
  // passphrase, PIN, token…)? Falso positivo = una tecla sin eco local (espera el
  // round-trip real, inofensivo); falso negativo = la contraseña se ve un instante
  // → se prefiere el primero. Ancla al final (el cursor está justo tras el prompt).
  const RE_SECRETO = new RegExp(
    '\\b(?:password|passwd|passphrase|passcode|contrase[ñn]a|secret|token|pin|otp|' +
    'verification code|c[oó]digo)\\b[^\\n]{0,80}[:：?]\\s*$', 'i');
  function esPromptSecreto(textoAntesDelCursor) {
    return typeof textoAntesDelCursor === 'string' && RE_SECRETO.test(textoAntesDelCursor);
  }

  // ¿Es seguro predecir una tecla AHORA? Decisión pura sobre el estado de xterm:
  //   alt       buffer alterno activo (app a pantalla completa)
  //   mouse     la app pidió mouse-tracking (TUI)
  //   inbufN    bytes del server aún sin volcar a xterm (el char predicho se
  //             pintaría ANTES de ese output → orden invertido)
  //   cursorX / cols / textoLinea  posición del cursor y texto de su fila
  function puedePredecir({ alt, mouse, inbufN, cursorX, cols, textoLinea } = {}) {
    if (alt) return false;
    if (mouse) return false;
    if (inbufN > 0) return false;
    if (Number.isFinite(cols) && Number.isFinite(cursorX) && cursorX >= cols - 1) return false;
    const txt = typeof textoLinea === 'string' ? textoLinea : '';
    const cx = Number.isFinite(cursorX) ? cursorX : txt.length;
    // Texto a la derecha del cursor → edición en el medio: el eco real inserta y
    // repinta la cola; el char predicho lo SOBRESCRIBIRÍA y el undo borraría el resto.
    if (txt.replace(/\s+$/, '').length > cx) return false;
    if (esPromptSecreto(txt.slice(0, cx))) return false;
    return true;
  }

  function crearEcoLocal(opts) {
    const o = opts || {};
    const maxPendientes = Number.isFinite(o.maxPendientes) ? o.maxPendientes : 16;
    // Plazo para que vuelva el eco de una predicción antes de darla por "no hay
    // eco" (contraseña). Un eco normal vuelve en <50ms; los picos del cruce
    // browser↔WSL llegan a ~600ms — pasados esos 300ms un pico solo cuesta un
    // parpadeo (se despinta y reaparece con el eco real), nunca un dato mal.
    const esperaMs = Number.isFinite(o.esperaMs) ? o.esperaMs : 300;
    // {ch, pintado, t}: pintado=false → SONDA (se anotó para detectar el eco
    // pero NO se escribió al buffer porque el predictor está suspendido).
    let pendientes = [];
    let suspendido = false;

    // ¿predecir este char? true = el caller lo PINTA (term.write). Las sondas
    // devuelven false: se registran adentro y no se pintan.
    function predecir(ch, ahora) {
      if (pendientes.length >= maxPendientes) return false;
      if (!esCharImprimible(ch)) return false;
      const t = Number.isFinite(ahora) ? ahora : 0;
      if (suspendido) { pendientes.push({ ch, pintado: false, t }); return false; }
      pendientes.push({ ch, pintado: true, t });
      return true;
    }

    // Procesa output del server contra las predicciones pendientes.
    //   saltear: nº de chars de `data` ya pintados por el eco local (se sacan).
    //   salida:  `data` SIN esos chars — lo que el caller debe escribir a xterm.
    //            (Una sonda que coincide NO se saca: nunca se pintó.)
    //   undo:    nº de chars predichos PINTADOS y sin confirmar que el caller debe
    //            DESPINTAR (borrado + retroceso de cursor) antes de escribir
    //            `salida`, porque ese output no es su eco lineal (redibujo con
    //            cursor relativo). Si no se despintan, el cursor queda corrido y
    //            el redibujo garbla.
    function conciliar(data) {
      if (pendientes.length === 0) return { saltear: 0, undo: 0, salida: data };
      let i = 0;
      let confirmo = false;
      let salida = '';
      let saltear = 0;
      while (pendientes.length > 0 && i < data.length && data[i] === pendientes[0].ch) {
        const p = pendientes.shift();
        confirmo = true;
        if (p.pintado) saltear++; else salida += data[i];
        i++;
      }
      if (confirmo) suspendido = false;        // el server SÍ hace eco: predictor de vuelta
      let undo = 0;
      if (pendientes.length > 0 && i < data.length) {
        // El output ya no es el eco de lo que queda predicho (redibujo de readline
        // u output no relacionado): las predicciones aún pintadas hay que borrarlas
        // para que el output caiga sobre un buffer/cursor sincronizado.
        for (const p of pendientes) if (p.pintado) undo++;
        pendientes = [];
      }
      // Si i >= data.length con predicciones restantes: eco partido en chunks — las
      // dejamos pendientes (el próximo chunk las confirma), sin undo ni flicker.
      salida += data.slice(i);
      return { saltear, undo, salida };
    }

    // Descarta TODA predicción pendiente y devuelve cuántas PINTADAS había. El
    // caller lo usa ANTES de mandar una tecla no imprimible (flechas/enter/
    // backspace/ctrl/tab): esas teclas gatillan un redibujo del shell, así que
    // despintamos primero para que el redibujo no arrastre un cursor corrido.
    function flush() {
      let n = 0;
      for (const p of pendientes) if (p.pintado) n++;
      pendientes = [];
      return n;
    }

    // ¿Venció el plazo del eco más viejo? Si sí: se despinta TODO lo pendiente
    // (devuelve cuántas PINTADAS — el caller emite el borrado) y se SUSPENDE el
    // predictor hasta que el server devuelva un eco real. 0 = nada que hacer.
    function vencer(ahora) {
      if (pendientes.length === 0) return 0;
      if (!(ahora - pendientes[0].t >= esperaMs)) return 0;
      let n = 0;
      for (const p of pendientes) if (p.pintado) n++;
      pendientes = [];
      suspendido = true;
      return n;
    }

    return {
      predecir,
      conciliar,
      flush,
      vencer,
      esperaMs,
      pendientesN() { return pendientes.length; },
      pintadosN() { let n = 0; for (const p of pendientes) if (p.pintado) n++; return n; },
      suspendido() { return suspendido; },
    };
  }

  const api = { crearEcoLocal, esCharImprimible, esPromptSecreto, puedePredecir };
  global.TerminalEcoLocal = Object.assign(global.TerminalEcoLocal || {}, api);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
