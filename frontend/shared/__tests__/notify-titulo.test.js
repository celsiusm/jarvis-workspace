// notify.js no puede pisar el título de la pestaña en cada focus (bug: tomaba el
// título al CARGAR, antes de que el workspace pusiera "JARVIS — <proyecto>").
'use strict';
const assert = require('assert');

const oyentes = {};
globalThis.document = { title: 'Jarvis Workspace', hidden: true,
  addEventListener: (ev, fn) => { (oyentes[ev] = oyentes[ev] || []).push(fn); } };
globalThis.window = globalThis;
globalThis.addEventListener = (ev, fn) => { (oyentes['w:' + ev] = oyentes['w:' + ev] || []).push(fn); };
globalThis.localStorage = { getItem: () => null };
const intervalos = [];
globalThis.setInterval = (fn) => { intervalos.push(fn); return intervalos.length; };
globalThis.clearInterval = () => {};
require('../notify.js');
const enfocar = () => { document.hidden = false; (oyentes['w:focus'] || []).forEach(f => f()); };

// El workspace pone su título DESPUÉS de cargar notify.js
document.title = 'JARVIS — Mi proyecto';
enfocar();
assert.strictEqual(document.title, 'JARVIS — Mi proyecto', 'un focus sin flash no toca el título');

// Con un aviso en segundo plano: flashea y al volver restaura el título vigente
document.hidden = true;
globalThis.JarvisNotify.avisar({ tipo: 'termino', nombre: 'Backend', sonidoOn: true });
intervalos[intervalos.length - 1]();
assert.ok(document.title.includes('🔔'), 'flash con campanita: ' + document.title);
enfocar();
assert.strictEqual(document.title, 'JARVIS — Mi proyecto', 'al volver restaura el título del proyecto');

// Cambio de proyecto sin flash en curso: el siguiente focus no lo revierte
document.title = 'JARVIS — Otro';
enfocar();
assert.strictEqual(document.title, 'JARVIS — Otro');

console.log('notify-titulo: OK');
