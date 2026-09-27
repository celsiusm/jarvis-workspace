'use strict';
const assert = require('node:assert');
const G = require('../groq-setup.js');

assert.strictEqual(G.CLAVE_URL, 'https://console.groq.com/keys');

assert.strictEqual(G.clavePareceGroq('gsk_' + 't'.repeat(32)), true);
assert.strictEqual(G.clavePareceGroq('  gsk_' + 'a'.repeat(32) + '  '), true);
assert.strictEqual(G.clavePareceGroq(''), false);
assert.strictEqual(G.clavePareceGroq('sk-ant-no'), false);
assert.strictEqual(G.clavePareceGroq('gsk_corta'), false);

assert.strictEqual(G.siguientePaso({ groq: false, teclaLista: false }), 'clave');
assert.strictEqual(G.siguientePaso({ groq: true, teclaLista: false }), 'tecla');
assert.strictEqual(G.siguientePaso({ groq: true, teclaLista: true }), 'listo');

assert.deepStrictEqual(G.MOUSE_BOTONES.map(b => b.n), [1, 2, 3, 4]);
assert.strictEqual(G.MOUSE_BOTONES[0].button, 0, 'Mouse 1 = left');
assert.strictEqual(G.MOUSE_BOTONES[1].button, 2, 'Mouse 2 = right');
assert.strictEqual(G.MOUSE_BOTONES[2].button, 1, 'Mouse 3 = middle');
assert.strictEqual(G.MOUSE_BOTONES[3].button, 3, 'Mouse 4 = back');
assert.deepStrictEqual(G.MOUSE_BOTONES.map(b => b.label), ['Mouse 1', 'Mouse 2', 'Mouse 3', 'Mouse 4']);

assert.strictEqual(G.chipSeleccionado({ type: 'mouse', value: 2 }, 2), true);
assert.strictEqual(G.chipSeleccionado({ type: 'mouse', value: 2 }, '2'), true);
assert.strictEqual(G.chipSeleccionado({ type: 'mouse', value: 0 }, 2), false);
assert.strictEqual(G.chipSeleccionado({ type: 'key', value: 'AltLeft' }, 0), false);
assert.strictEqual(G.chipSeleccionado(null, 0), false);

// introAlCargar: al entrar al workspace YA NO se abre la modal (tapaba las
// terminales antes de verlas, aunque nunca uses la voz). Sale un aviso liviano
// UNA vez; la modal aparece recién al usar la voz (iniciarGrabacion).
assert.strictEqual(G.introAlCargar({ groq: false, introVisto: false }), 'aviso');
assert.strictEqual(G.introAlCargar({ groq: false, introVisto: true }), null);
assert.strictEqual(G.introAlCargar({ groq: true, introVisto: false }), null);
console.log('ok  groq-setup');
