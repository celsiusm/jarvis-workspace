'use strict';
// Tests de la lógica pura del paste de imágenes (Ctrl+V) en terminales.
// Contexto: pegar una imagen mandando \x16 a la CLI la obligaba a leer el
// clipboard de Windows vía interop WSL (powershell.exe, 3.5s+ medidos solo el
// arranque) → "Pasting..." eterno en Claude Code. El browser YA tiene los
// bytes en el evento paste: se suben a /upload-image (ms) y se pega la ruta.
// El \x16 queda solo como FALLBACK si la subida falla (en agentes; bash no
// tiene quién lea el clipboard del OS).
const assert = require('node:assert');
const P = require('../terminal-paste.js');

// ─── planDePaste: qué hacer con el contenido del clipboard ───────────────────
// Texto SIEMPRE gana sobre imagen: Windows pega image/*+text/plain a la vez al
// copiar desde Slack/Excel/web (lo común al programar); antes se subía la
// imagen y se perdía el texto.
assert.deepStrictEqual(
  P.planDePaste({ texto: 'hola', items: [{ kind: 'file', type: 'image/png' }] }),
  { accion: 'texto' },
);
assert.deepStrictEqual(P.planDePaste({ texto: 'x', items: [] }), { accion: 'texto' });

// Imagen pura (screenshot): subir. Devuelve el índice del item imagen.
assert.deepStrictEqual(
  P.planDePaste({ texto: '', items: [{ kind: 'file', type: 'image/png' }] }),
  { accion: 'imagen', indice: 0 },
);
assert.deepStrictEqual(
  P.planDePaste({
    texto: null,
    items: [{ kind: 'string', type: 'text/html' }, { kind: 'file', type: 'image/jpeg' }],
  }),
  { accion: 'imagen', indice: 1 },
);

// Archivos no-imagen o clipboard vacío: nada.
assert.deepStrictEqual(
  P.planDePaste({ texto: '', items: [{ kind: 'file', type: 'application/pdf' }] }),
  { accion: 'nada' },
);
assert.deepStrictEqual(P.planDePaste({ texto: '', items: [] }), { accion: 'nada' });
assert.deepStrictEqual(P.planDePaste({}), { accion: 'nada' });

// kind 'string' con type image/* (algunas apps ponen la URL así) NO es archivo.
assert.deepStrictEqual(
  P.planDePaste({ texto: '', items: [{ kind: 'string', type: 'image/png' }] }),
  { accion: 'nada' },
);

// ─── nombreImagenPegada: nombre para screenshots sin file.name ───────────────
// Con nombre propio se respeta.
assert.strictEqual(
  P.nombreImagenPegada({ nombre: 'captura.png', mime: 'image/png', ts: 123 }),
  'captura.png',
);
// Sin nombre (screenshot del clipboard): clipboard-<ts>.<ext del mime>.
assert.strictEqual(
  P.nombreImagenPegada({ nombre: '', mime: 'image/png', ts: 456 }),
  'clipboard-456.png',
);
assert.strictEqual(
  P.nombreImagenPegada({ nombre: null, mime: 'image/JPEG', ts: 7 }),
  'clipboard-7.jpeg',
);
// Mime raro o vacío: cae a png (el default histórico).
assert.strictEqual(
  P.nombreImagenPegada({ nombre: '', mime: '', ts: 9 }),
  'clipboard-9.png',
);

// ─── fallbackImagenPaste: qué mandar si la subida falla ──────────────────────
// Agentes (claude/codex/...): \x16 → la CLI hace su paste nativo leyendo el
// clipboard del OS (lento por el interop, pero funciona como red de seguridad).
assert.strictEqual(P.fallbackImagenPaste('claude'), '\x16');
assert.strictEqual(P.fallbackImagenPaste('codex'), '\x16');
assert.strictEqual(P.fallbackImagenPaste(undefined), '\x16');
// bash (manual): no hay quién lea el clipboard del OS → null (mostrar error).
assert.strictEqual(P.fallbackImagenPaste('manual'), null);

// ─── debeEnvolverPaste: marcadores de bracketed paste SOLO si la app los pidió ──
// Un prompt de contraseña (sudo/ssh), `cat`, `read` o un REPL sin readline NO los
// entiende: recibía "^[[200~clave^[[201~" y la contraseña pegada fallaba.
// Estado conocido (xterm vio ESC[?2004h/l): se respeta.
assert.strictEqual(P.debeEnvolverPaste({ visto: true, activo: true }), true);    // bash/readline, claude
assert.strictEqual(P.debeEnvolverPaste({ visto: true, activo: false }), false);  // sudo, cat, read…
// Estado DESCONOCIDO (tras un re-attach el seed no re-enuncia el modo): se conserva
// el default histórico — envolver (readline y los agentes lo piden; sin marcadores
// un paste multilínea se ejecutaría línea por línea).
assert.strictEqual(P.debeEnvolverPaste({ visto: false, activo: false }), true);
assert.strictEqual(P.debeEnvolverPaste({}), true);
assert.strictEqual(P.debeEnvolverPaste(), true);

// ─── prepararPaste: saneo + marcadores ───────────────────────────────────────
assert.strictEqual(P.prepararPaste('ls -la', true), '\x1b[200~ls -la\x1b[201~');
assert.strictEqual(P.prepararPaste('ls -la'), '\x1b[200~ls -la\x1b[201~');       // default: envolver
assert.strictEqual(P.prepararPaste('clave123', false), 'clave123');                // sin marcadores
// saltos de línea → \r (con o sin marcadores)
assert.strictEqual(P.prepararPaste('a\nb\r\nc', true), '\x1b[200~a\rb\rc\x1b[201~');
assert.strictEqual(P.prepararPaste('a\nb', false), 'a\rb');
// marcadores EMBEBIDOS fuera: un ESC[201~ adentro cerraría el paste antes de
// tiempo y el resto se leería como teclado (inyección de comandos).
assert.strictEqual(
  P.prepararPaste('x\x1b[201~rm -rf ~\x1b[200~y', true),
  '\x1b[200~xrm -rf ~y\x1b[201~',
);
assert.strictEqual(P.prepararPaste('x\x1b[201~y', false), 'xy');

console.log('terminal-paste.test.js OK');
