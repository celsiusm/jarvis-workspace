'use strict';
// Tests del ECO LOCAL predictivo: pinta la tecla del usuario al instante (0ms,
// local, como el kernel en una terminal nativa) y reconcilia con el eco real.
// Funciona en shells, donde el eco es el char tal cual → coincide con la
// predicción → se saltea (no se pinta dos veces).
//
// PERO el shell (readline) NO siempre hace "eco lineal": al navegar el historial
// (↑/↓), editar en el medio de la línea o envolver una línea larga, REDIBUJA con
// movimientos RELATIVOS de cursor (\b, \x1b[C…). Si dejáramos un char predicho
// pintado (con su avance de cursor) cuando llega uno de esos redibujos, el cursor
// queda 1 columna corrido y cada redibujo "come" una letra, acumulando garble
// (bug del historial, 2026-07-06). Por eso:
//   - conciliar() devuelve `undo` = cuántos chars predichos SIN confirmar hay que
//     despintar antes de dejar pasar un output que NO es su eco.
//   - flush() despinta TODO lo pendiente y devuelve el conteo (el caller lo usa
//     antes de mandar una tecla no imprimible: flechas/enter/backspace/ctrl…).
// Y NO todo lo que se tipea tiene eco (auditoría de tipeo 2026-10): una
// contraseña no vuelve → vencer() la despinta y SUSPENDE el predictor hasta que
// el server devuelva un eco real; puedePredecir() veda apps a pantalla completa,
// output sin volcar, borde derecho, edición en el medio y prompts de secreto.
// El wiring con xterm (write local / saltear bytes / secuencia de borrado)
// vive en terminal.js.
const assert = require('node:assert');
const E = require('../terminal-eco-local.js');

// ─── predecir: char imprimible de 1 code unit (ASCII + BMP: acentos/ñ); nada de control ───
const e = E.crearEcoLocal({ maxPendientes: 8 });
assert.strictEqual(e.predecir('a'), true);
assert.strictEqual(e.predecir('Z'), true);
assert.strictEqual(e.pendientesN(), 2);
assert.strictEqual(e.predecir('\r'), false);       // enter
assert.strictEqual(e.predecir('\x7f'), false);     // backspace (DEL)
assert.strictEqual(e.predecir('\x1b'), false);     // escape (secuencias)
assert.strictEqual(e.predecir('\x9f'), false);     // C1 control
assert.strictEqual(e.predecir('́'), false);   // combining mark (no se predice sola)
assert.strictEqual(e.predecir('ñ'), true);         // acento español (Latin-1, 1 code unit) → SÍ
assert.strictEqual(e.predecir('á'), true);         // idem
assert.strictEqual(e.predecir('ab'), false);       // más de un char
assert.strictEqual(e.pendientesN(), 4);            // a, Z, ñ, á

// ─── SHELL: el eco coincide (incluidos acentos) → esos bytes ya están pintados ──
let r = e.conciliar('aZñá');
assert.deepStrictEqual(r, { saltear: 4, undo: 0, salida: '' });
assert.strictEqual(e.pendientesN(), 0);

// ─── eco partido en dos chunks: cada parte consume su predicción (sin undo) ─────
e.predecir('h'); e.predecir('i');
r = e.conciliar('h');
assert.deepStrictEqual(r, { saltear: 1, undo: 0, salida: '' });
assert.strictEqual(e.pendientesN(), 1);
r = e.conciliar('i');
assert.deepStrictEqual(r, { saltear: 1, undo: 0, salida: '' });
assert.strictEqual(e.pendientesN(), 0);

// ─── sin pendientes (flood del agente) → no toca nada ─────────────────────────
assert.deepStrictEqual(e.conciliar('output del agente'),
  { saltear: 0, undo: 0, salida: 'output del agente' });

// ─── prefijo coincide y sigue más output relacionado: sólo se saltea lo predicho ─
e.predecir('x');
r = e.conciliar('xyz');                            // 'x' es el eco; 'yz' no había predicción
assert.deepStrictEqual(r, { saltear: 1, undo: 0, salida: 'yz' });
assert.strictEqual(e.pendientesN(), 0);

// ─── SHELL REDIBUJA (historial ↑/↓, wrap): el output NO es el eco simple del char
//     predicho → hay que BORRAR el char pintado (undo) para que el redibujo caiga
//     sobre un cursor sincronizado. saltear = lo que coincidió (0 acá). ──────────
e.predecir('q');
r = e.conciliar('\r\x1b[K> NO');                   // redibujo de la línea, no 'q'
assert.deepStrictEqual(r, { saltear: 0, undo: 1, salida: '\r\x1b[K> NO' });
assert.strictEqual(e.pendientesN(), 0);            // soltadas: el redibujo manda

// ─── desajuste tras coincidencia parcial: saltea lo confirmado, BORRA el resto ──
e.predecir('a'); e.predecir('b');
r = e.conciliar('aX');                             // 'a' coincide (confirmada), 'b' no
assert.deepStrictEqual(r, { saltear: 1, undo: 1, salida: 'X' }); // saltear 'a', despintar 'b'
assert.strictEqual(e.pendientesN(), 0);

// ─── flush(): despinta TODO lo pendiente y devuelve el conteo (para el caller,
//     antes de mandar una tecla no imprimible: flechas/enter/backspace/ctrl…) ────
const f = E.crearEcoLocal({ maxPendientes: 8 });
f.predecir('t'); f.predecir('x');
assert.strictEqual(f.flush(), 2);
assert.strictEqual(f.pendientesN(), 0);
assert.strictEqual(f.flush(), 0);                  // idempotente sin pendientes

// ─── REGRESIÓN (el historial ↑/↓ "comía una letra del nombre"): sin predicciones
//     pendientes, un recall de TEXTO PLANO ('tput cols', como emite readline en
//     una línea vacía) NO pierde su primer byte. El fix real es que flush() corre
//     al presionar ↑ (terminal.js), así que acá pendientes ya está vacío. ────────
const g = E.crearEcoLocal({ maxPendientes: 8 });
assert.deepStrictEqual(g.conciliar('tput cols'),
  { saltear: 0, undo: 0, salida: 'tput cols' });

// ─── maxPendientes: no acumular sin confirmar más allá de la cota ─────────────
const c = E.crearEcoLocal({ maxPendientes: 3 });
assert.strictEqual(c.predecir('1'), true);
assert.strictEqual(c.predecir('2'), true);
assert.strictEqual(c.predecir('3'), true);
assert.strictEqual(c.predecir('4'), false);       // cota llena → no predice más
assert.strictEqual(c.pendientesN(), 3);

// ─── CONTRASEÑA / app sin eco: la predicción nunca vuelve ─────────────────────
// sudo/ssh/gpg no devuelven lo tipeado. Sin plazo, la clave quedaba PINTADA en
// pantalla hasta el Enter. Pasado `esperaMs` sin eco: se despinta y se suspende.
const p = E.crearEcoLocal({ maxPendientes: 16, esperaMs: 300 });
assert.strictEqual(p.predecir('s', 1000), true);          // se pinta (aún no se sabe)
assert.strictEqual(p.predecir('e', 1040), true);
assert.strictEqual(p.vencer(1200), 0);                    // dentro del plazo: nada
assert.strictEqual(p.suspendido(), false);
assert.strictEqual(p.vencer(1300), 2);                    // venció el más viejo → despintar 2
assert.strictEqual(p.suspendido(), true);
assert.strictEqual(p.pendientesN(), 0);
assert.strictEqual(p.vencer(9999), 0);                    // sin pendientes: no-op

// Suspendido: las teclas siguientes son SONDAS — no se pintan (la clave no se ve).
assert.strictEqual(p.predecir('c', 2000), false);
assert.strictEqual(p.predecir('r', 2010), false);
assert.strictEqual(p.pendientesN(), 2);
assert.strictEqual(p.pintadosN(), 0);                     // nada que despintar
assert.strictEqual(p.flush(), 0);                         // Enter: no hay nada pintado
assert.strictEqual(p.suspendido(), true);                 // y sigue suspendido (no hubo eco)

// Un eco REAL reactiva el predictor (la sonda coincide): se escribe NORMAL (nunca
// se pintó, no se saltea) y de ahí en más se vuelve a predecir con 0ms.
assert.strictEqual(p.predecir('l', 3000), false);         // sonda
r = p.conciliar('l');
assert.deepStrictEqual(r, { saltear: 0, undo: 0, salida: 'l' });
assert.strictEqual(p.suspendido(), false);
assert.strictEqual(p.predecir('s', 3100), true);          // pintada otra vez

// Pico de latencia legítimo (eco tarda >esperaMs): solo cuesta un parpadeo —
// se despinta, y cuando el eco llega se escribe normal sin duplicar nada.
const s = E.crearEcoLocal({ esperaMs: 300 });
assert.strictEqual(s.predecir('h', 0), true);
assert.strictEqual(s.vencer(350), 1);                     // despintada
assert.strictEqual(s.predecir('i', 360), false);          // sonda
r = s.conciliar('h');                                     // llega el eco TARDÍO de la 'h' vencida
assert.deepStrictEqual(r, { saltear: 0, undo: 0, salida: 'h' });    // se escribe normal (no estaba pintada)
assert.strictEqual(s.suspendido(), true);                 // no coincidió con la sonda → sigue suspendido
assert.strictEqual(s.predecir('o', 400), false);          // nueva sonda
r = s.conciliar('o');
assert.deepStrictEqual(r, { saltear: 0, undo: 0, salida: 'o' });
assert.strictEqual(s.suspendido(), false);                // el eco real reactivó el predictor

// Mezcla [sonda, pintada] en un mismo chunk: se saca SOLO lo pintado.
const m = E.crearEcoLocal({ esperaMs: 300 });
m.predecir('a', 0);
m.vencer(400);                                            // suspende
m.predecir('b', 410);                                     // sonda (b)
r = m.conciliar('b');                                     // eco de la sonda → reactiva
assert.strictEqual(r.salida, 'b');
m.predecir('c', 420);                                     // pintada
m.predecir('d', 421);                                     // pintada
r = m.conciliar('cdX');
assert.deepStrictEqual(r, { saltear: 2, undo: 0, salida: 'X' });

// ─── puedePredecir: estado de xterm donde NO se pinta ─────────────────────────
const ok = { alt: false, mouse: false, inbufN: 0, cursorX: 12, cols: 80, textoLinea: 'user@host:~$ ' };
assert.strictEqual(E.puedePredecir(ok), true);
assert.strictEqual(E.puedePredecir({ ...ok, alt: true }), false);        // vim / less / htop
assert.strictEqual(E.puedePredecir({ ...ok, mouse: true }), false);      // TUI con mouse
assert.strictEqual(E.puedePredecir({ ...ok, inbufN: 5 }), false);        // output del server sin volcar
assert.strictEqual(E.puedePredecir({ ...ok, cursorX: 79 }), false);      // borde derecho (wrap)
assert.strictEqual(E.puedePredecir({ ...ok, cursorX: 78 }), true);
// edición en el medio de la línea: hay texto A LA DERECHA del cursor
assert.strictEqual(E.puedePredecir({ ...ok, cursorX: 14, textoLinea: 'user@host:~$ ls -la' }), false);
// cursor al final del texto (lo normal al tipear): se predice
assert.strictEqual(E.puedePredecir({ ...ok, cursorX: 19, textoLinea: 'user@host:~$ ls -la' }), true);
// sin datos (estado raro): permisivo en lo que no sabe, nunca lanza
assert.strictEqual(E.puedePredecir({}), true);
assert.strictEqual(E.puedePredecir(), true);

// ─── prompts de secreto: ni siquiera se arranca pintando ──────────────────────
for (const prompt of [
  '[sudo] password for ana:',
  'Password:',
  'ana@srv\'s password:',
  'Enter passphrase for key \'/home/ana/.ssh/id_ed25519\':',
  'Enter passphrase (empty for no passphrase):',
  'Contraseña:',
  'contrasena:',
  'Verification code:',
  'Enter PIN:',
  'Token:',
  'Código de verificación:',
]) {
  assert.strictEqual(E.esPromptSecreto(prompt), true, 'debe ser secreto: ' + prompt);
  assert.strictEqual(E.esPromptSecreto(prompt + ' '), true, 'con espacio final: ' + prompt);
  assert.strictEqual(E.puedePredecir({ ...ok, cursorX: prompt.length + 1, textoLinea: prompt }), false, prompt);
}
// prompts normales (o con la palabra en otro contexto) NO se vedan
for (const prompt of [
  'user@host:~$',
  'user@host:~/proyectos/token-api$',
  '> ',
  'In [3]:',
  '(venv) ana@pc:~/spin$',
  'Enter your name:',
]) {
  assert.strictEqual(E.esPromptSecreto(prompt), false, 'NO es secreto: ' + prompt);
}

console.log('OK terminal-eco-local');
