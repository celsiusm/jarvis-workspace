'use strict';
// Tests de la lógica pura del Browser (browser.js `_pure`). Sin DOM.
const assert = require('node:assert');
const P = require('../browser.js');

// ── normalizarUrl: esquema/host/puerto; SIN reescritura a reproductores ─────
assert.strictEqual(P.normalizarUrl('localhost:3000'), 'http://localhost:3000');
assert.strictEqual(P.normalizarUrl('3000'), 'http://localhost:3000');
assert.strictEqual(P.normalizarUrl('https://x.com'), 'https://x.com');
assert.strictEqual(P.normalizarUrl('http://localhost:5173/app'), 'http://localhost:5173/app');
// YouTube/afines quedan como la web REAL (el motor la navega, no la reescribe)
assert.strictEqual(P.normalizarUrl('youtube.com/watch?v=abc'), 'http://youtube.com/watch?v=abc');
assert.strictEqual(P.normalizarUrl('https://www.youtube.com/watch?v=abc'),
  'https://www.youtube.com/watch?v=abc');
// alias de loopback → localhost
assert.strictEqual(P.normalizarUrl('http://127.0.0.1:8080'), 'http://localhost:8080');
// basura / esquemas no web
assert.strictEqual(P.normalizarUrl('javascript:alert(1)'), null);
assert.strictEqual(P.normalizarUrl('/etc/passwd'), null);
assert.strictEqual(P.normalizarUrl(''), null);
assert.strictEqual(P.normalizarUrl(null), null);

// ── interpretarEntrada: URL vs búsqueda vs yt ──────────────────────────────
assert.deepStrictEqual(P.interpretarEntrada(''), { tipo: 'vacia' });
assert.strictEqual(P.interpretarEntrada('lofi beats').tipo, 'busqueda');
assert.strictEqual(P.interpretarEntrada('lofi beats').q, 'lofi beats');
assert.strictEqual(P.interpretarEntrada('yt lofi girl').tipo, 'youtube');
assert.strictEqual(P.interpretarEntrada('yt lofi girl').q, 'lofi girl');
assert.strictEqual(P.interpretarEntrada('x.com').tipo, 'url');
assert.strictEqual(P.interpretarEntrada('x.com').url, 'http://x.com');
assert.strictEqual(P.interpretarEntrada('https://github.com/a/b').tipo, 'url');

// ── urlBusqueda ────────────────────────────────────────────────────────────
assert.strictEqual(P.urlBusqueda('youtube', 'lofi'),
  'https://www.youtube.com/results?search_query=lofi');
assert.strictEqual(P.urlBusqueda('busqueda', 'a b'),
  'https://www.google.com/search?q=a%20b');

// ── linkAlPreview: solo servidores locales entran al Browser ────────────────
assert.strictEqual(P.linkAlPreview('http://localhost:5173/', 'http://localhost:3000'),
  'http://localhost:5173/');
assert.strictEqual(P.linkAlPreview('http://127.0.0.1:8080/x', 'http://localhost:3000'),
  'http://localhost:8080/x');
// el propio Jarvis (mismo puerto) NO se embebe salvo /static
assert.strictEqual(P.linkAlPreview('http://localhost:3000/', 'http://localhost:3000'), null);
assert.strictEqual(P.linkAlPreview('http://localhost:3000/static/demo.html', 'http://localhost:3000'),
  'http://localhost:3000/static/demo.html');
// sitios externos no son asunto de la terminal → null
assert.strictEqual(P.linkAlPreview('https://x.com', 'http://localhost:3000'), null);

console.log('browser.test.js OK');
