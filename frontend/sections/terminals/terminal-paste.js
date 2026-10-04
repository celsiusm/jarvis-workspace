// JARVIS — Lógica pura del paste (Ctrl+V) en terminales.
//
// Por qué existe: pegar una imagen mandando \x16 a la CLI (su paste nativo)
// la obligaba a leer el clipboard de Windows vía interop WSL→powershell.exe:
// 3.5s+ medidos SOLO el arranque de .NET, más la conversión/transferencia de
// la imagen → el "Pasting..." eterno de Claude Code. El browser YA tiene los
// bytes de la imagen en el evento paste, así que el camino rápido es subirla
// a POST /api/terminals/{id}/upload-image (milisegundos) y pegar la ruta con
// bracketed paste. El \x16 queda como FALLBACK si la subida falla.
//
// Acá vive la parte decisoria (testeada en Node); el wiring con el DOM/WS
// está en terminal.js (handler 'paste' del container).
(function (global) {
  'use strict';

  // Decide qué hacer con el contenido de un evento paste.
  //   texto  → pegar el texto (bracketed paste)
  //   imagen → subir el item `indice` y pegar la ruta
  //   nada   → ignorar el evento
  // TEXTO SIEMPRE GANA sobre imagen: Windows pega image/*+text/plain a la vez
  // al copiar desde Slack/Excel/web (lo común al programar); si ganara la
  // imagen se perdería el texto copiado.
  function planDePaste({ texto, items } = {}) {
    if (texto) return { accion: 'texto' };
    const lista = items || [];
    for (let i = 0; i < lista.length; i++) {
      const it = lista[i];
      if (it && it.kind === 'file' && typeof it.type === 'string' && it.type.startsWith('image/')) {
        return { accion: 'imagen', indice: i };
      }
    }
    return { accion: 'nada' };
  }

  // Nombre de archivo para la imagen pegada: los screenshots del clipboard
  // vienen sin file.name → clipboard-<ts>.<ext del mime> (default png).
  function nombreImagenPegada({ nombre, mime, ts } = {}) {
    if (nombre) return nombre;
    const ext = ((mime || '').split('/')[1] || 'png').toLowerCase();
    return `clipboard-${ts}.${ext}`;
  }

  // Qué mandar al PTY si la subida de la imagen falla. En terminales de
  // agente, \x16 dispara el paste nativo de la CLI (lee el clipboard del OS
  // vía interop — lento, pero la imagen sigue ahí: red de seguridad). En bash
  // (`manual`) no hay quién lea el clipboard → null (mostrar error).
  function fallbackImagenPaste(tipoIa) {
    return tipoIa === 'manual' ? null : '\x16';
  }

  // ¿Envolver el texto pegado con los marcadores de bracketed paste (ESC[200~ …
  // ESC[201~)? Solo tienen sentido si la app del pane los pidió (ESC[?2004h):
  // una que NO — el prompt de contraseña de sudo/ssh, `cat`, `read`, un REPL
  // sin readline — los recibe como teclas literales y el paste sale
  // "^[[200~clave^[[201~" (la contraseña pegada nunca coincidía).
  //   visto  → xterm ya vio al menos un ESC[?2004h/l en ESTE stream (sabe el
  //            estado real). Tras un re-attach (seed) NO lo sabe — tmux absorbe
  //            ese modo y el seed no lo re-enuncia — y ahí se conserva el
  //            default histórico: envolver (readline y los agentes lo piden).
  //   activo → term.modes.bracketedPasteMode (el estado conocido).
  function debeEnvolverPaste({ visto, activo } = {}) {
    return visto ? !!activo : true;
  }

  // Texto pegado → lo que viaja al PTY. Sanea los marcadores embebidos (un
  // ESC[201~ adentro cerraría el paste antes de tiempo y el resto se leería
  // como teclado → inyección de comandos) y normaliza los saltos a \r.
  function prepararPaste(texto, envolver) {
    const limpio = String(texto).replace(/\r?\n/g, '\r').replace(/\x1b\[20[01]~/g, '');
    return envolver === false ? limpio : '\x1b[200~' + limpio + '\x1b[201~';
  }

  const api = { planDePaste, nombreImagenPegada, fallbackImagenPaste, debeEnvolverPaste, prepararPaste };
  global.TerminalPaste = Object.assign(global.TerminalPaste || {}, api);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
