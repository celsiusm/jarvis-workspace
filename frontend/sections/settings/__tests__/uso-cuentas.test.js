'use strict';
const assert = require('node:assert');
const U = require('../uso-cuentas.js');

assert.strictEqual(U.nivel(10), 'ok');
assert.strictEqual(U.nivel(70), 'medio');
assert.strictEqual(U.nivel(95), 'critico');
assert.strictEqual(U.restante(23.4), 77);
assert.strictEqual(U.restante(120), 0);

const ahora = Date.parse('2026-09-29T10:00:00Z');
assert.strictEqual(U.faltaPara('2026-09-29T12:10:00Z', ahora), '2 h 10 min');
assert.strictEqual(U.faltaPara('2026-10-02T14:00:00Z', ahora), '3 d 4 h');
assert.strictEqual(U.faltaPara('2026-09-29T10:12:00Z', ahora), '12 min');
assert.strictEqual(U.faltaPara('2026-09-29T09:00:00Z', ahora), null, 'fecha pasada');
assert.strictEqual(U.faltaPara(null, ahora), null);
assert.strictEqual(U.faltaPara('basura', ahora), null);

assert.strictEqual(U.etiqueta({ clave: 'sesion' }), 'Sesión (5 h)');
assert.strictEqual(U.etiqueta({ clave: 'semana', modelo: 'Opus' }), 'Semana · Opus');

const ventanas = [
  { clave: 'sesion', usado: 30, reinicia: '2026-09-29T12:10:00Z' },
  { clave: 'semana', usado: 92, reinicia: '2026-10-02T14:00:00Z' },
];
assert.strictEqual(U.masApretada(ventanas).clave, 'semana');
assert.strictEqual(U.masApretada([]), null);

// Bloque OK: una fila por ventana, lo que QUEDA en el texto, lo USADO en la barra.
const html = U.htmlBloque({ estado: 'ok', plan: 'max', fuente: 'api', ventanas }, { ahora });
assert.ok(html.includes('quedan 70%'));
assert.ok(html.includes('quedan 8%'));
assert.ok(html.includes('width:92%'));
assert.ok(html.includes('ct-uso-f critico'));
assert.ok(html.includes('se renueva en 2 h 10 min'));
assert.ok(html.includes('MAX'));
assert.ok(html.includes('data-act="uso-refrescar"'));
assert.ok(!html.includes('última sesión'));

// Fuente = sesión del CLI: se aclara que no es en vivo.
assert.ok(U.htmlBloque({ estado: 'ok', fuente: 'sesion', ventanas }, { ahora }).includes('última sesión'));

// Estados sin datos: mensaje, sin barras.
const vencido = U.htmlBloque({ estado: 'token_vencido', ventanas: [] });
assert.ok(vencido.includes('ct-uso vacio') && vencido.includes('Sesión vencida'));
assert.ok(!vencido.includes('ct-uso-bar'));
assert.ok(U.htmlBloque(null).includes('cargando'));

// Escapa el plan (viene del proveedor).
assert.ok(!U.htmlBloque({ estado: 'ok', plan: '<b>x', ventanas }).includes('<B>X'));

// Chip: lo que le queda a la ventana más apretada; nada si no hay datos.
assert.ok(U.htmlChip({ estado: 'ok', ventanas }).includes('>8%<'));
assert.strictEqual(U.htmlChip({ estado: 'error', ventanas: [] }), '');
assert.strictEqual(U.htmlChip(undefined), '');

console.log('uso-cuentas: ok');
