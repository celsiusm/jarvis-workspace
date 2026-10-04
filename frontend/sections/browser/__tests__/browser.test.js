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

// ── Paneles divididos: disposición según el espacio real ───────────────────
{
  const D = P.disposicion;
  assert.deepStrictEqual(D(1, 300, 200), { cols: 1, rows: 1, grande: false });
  // Dock angosto (la medida típica de un panel lateral): 2 paneles van UNO SOBRE OTRO
  assert.deepStrictEqual(D(2, 400, 800), { cols: 1, rows: 2, grande: false });
  assert.deepStrictEqual(D(2, 308, 760), { cols: 1, rows: 2, grande: false });   // dock por defecto (320)
  // Ancho de sobra: lado a lado
  assert.deepStrictEqual(D(2, 900, 700), { cols: 2, rows: 1, grande: false });
  // 3: tres columnas si entran, si no uno grande + dos, si no tres filas
  assert.deepStrictEqual(D(3, 1000, 600), { cols: 3, rows: 1, grande: false });
  assert.deepStrictEqual(D(3, 700, 600), { cols: 2, rows: 2, grande: true });
  assert.deepStrictEqual(D(3, 400, 800), { cols: 1, rows: 3, grande: false });
  // 4: cuadrícula 2×2 si entra; en angosto no entra (ni 4 filas de 240)
  assert.deepStrictEqual(D(4, 700, 500), { cols: 2, rows: 2, grande: false });
  assert.deepStrictEqual(D(4, 400, 800), { cols: 1, rows: 4, grande: false });   // 4 filas apiladas
  assert.strictEqual(D(4, 300, 700), null);                                       // 175px de alto: no entra
  assert.strictEqual(D(2, 200, 300), null);          // ni una celda de 260 de ancho
  assert.strictEqual(D(5, 900, 900), null);
  // Sin medida (pestaña oculta): disposición clásica, nunca null
  assert.deepStrictEqual(D(3, 0, 0), { cols: 2, rows: 2, grande: true });

  // el layout pedido baja al mayor que entra
  assert.strictEqual(P.mayorQueEntra(4, 400, 800), 4);
  assert.strictEqual(P.mayorQueEntra(4, 300, 700), 3);
  assert.strictEqual(P.mayorQueEntra(4, 700, 500), 4);
  assert.strictEqual(P.mayorQueEntra(3, 200, 300), 1);
  assert.strictEqual(P.mayorQueEntra(1, 2000, 2000), 1);
  assert.strictEqual(P.mayorQueEntra(9, 2000, 2000), 4);
}

// ── Slots: qué pestaña ocupa cada panel ────────────────────────────────────
{
  const A = P.asignarSlots, E = P.elegirTab;
  assert.deepStrictEqual(A([], [1, 2, 3], 2, 1), [1, 2]);
  assert.deepStrictEqual(A([2, 1], [1, 2, 3], 2, 2), [2, 1]);          // conserva el orden
  assert.deepStrictEqual(A([1, 9], [1, 2], 2, 1), [1, 2]);             // descarta cerradas
  assert.deepStrictEqual(A([1, 2], [1, 2, 3], 2, 3), [1, 3]);          // la activa SIEMPRE entra
  assert.deepStrictEqual(A([1, 2, 3], [1, 2, 3], 1, 1), [1]);          // achicar el layout
  // elegir una pestaña oculta ocupa el panel de la activa (swap, no re-ordena el resto)
  assert.deepStrictEqual(E([1, 2], 2, 3), [1, 3]);
  assert.deepStrictEqual(E([1, 2], 1, 3), [3, 2]);
  assert.deepStrictEqual(E([1, 2], 1, 2), [1, 2]);                     // ya visible: solo foco
  assert.deepStrictEqual(E([], null, 5), [5]);
}
console.log('browser layout: OK');
