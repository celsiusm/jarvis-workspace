'use strict';
const assert = require('node:assert');
const G = require('../glass.js');

// Default ON: Liquid Glass es el lenguaje visual de la app.
assert.strictEqual(G.DEFAULT, 'on');
assert.strictEqual(G.KEY, 'jarvis.glass');

// normalizar: solo 'on' | 'off'; basura / null → default.
assert.strictEqual(G.normalizar('on'), 'on');
assert.strictEqual(G.normalizar('off'), 'off');
assert.strictEqual(G.normalizar(' OFF '), 'off');
assert.strictEqual(G.normalizar(null), 'on');
assert.strictEqual(G.normalizar('x'), 'on');
assert.strictEqual(G.normalizar(false), 'off');
assert.strictEqual(G.normalizar(true), 'on');

// aplicar sobre un <html> falso: pone data-glass y persiste.
{
  const attrs = {};
  const doc = { documentElement: { setAttribute: (k, v) => { attrs[k] = v; } } };
  const store = {};
  const ls = { setItem: (k, v) => { store[k] = v; }, getItem: (k) => store[k] ?? null };
  assert.strictEqual(G.aplicar('off', { doc, ls }), 'off');
  assert.strictEqual(attrs['data-glass'], 'off');
  assert.strictEqual(store['jarvis.glass'], 'off');
  assert.strictEqual(G.actual({ ls }), 'off');
  G.aplicar('basura', { doc, ls });
  assert.strictEqual(attrs['data-glass'], 'on');
}
// localStorage roto (modo privado / thumbnail): no explota, cae al default.
{
  const ls = { getItem: () => { throw new Error('bloqueado'); }, setItem: () => { throw new Error('bloqueado'); } };
  assert.strictEqual(G.actual({ ls }), 'on');
  const doc = { documentElement: { setAttribute: () => {} } };
  assert.strictEqual(G.aplicar('off', { doc, ls }), 'off');
}
console.log('glass.test.js OK');
