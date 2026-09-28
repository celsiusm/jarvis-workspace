'use strict';
// Tests de la lógica pura del monitor de agentes (tasks.js `_pure`). Sin DOM.
// Corre con: node frontend/sections/tasks/__tests__/tasks.test.js
const assert = require('node:assert');
const P = require('../tasks.js');

// ── normEstado / claseEstado ──────────────────────────────────────
assert.strictEqual(P.normEstado('esperando'), 'esperando');
assert.strictEqual(P.normEstado('raro'), 'quieto');
assert.strictEqual(P.normEstado(undefined), 'quieto');
assert.strictEqual(P.claseEstado('caido'), 'tk-st-caido');
for (const e of P.ESTADOS) assert.ok(P.ETIQUETA[e], `etiqueta para ${e}`);

// ── textoConteo: singular/plural ─────────────────────────────────
assert.strictEqual(P.textoConteo('termino', 1), '1 terminó');
assert.strictEqual(P.textoConteo('termino', 3), '3 terminaron');
assert.strictEqual(P.textoConteo('quieto', 2), '2 quietos');
assert.strictEqual(P.textoConteo('caido', 1), '1 caído');
assert.strictEqual(P.textoConteo('trabajando', 4), '4 trabajando');

// ── partesResumen: orden canónico, sin ceros ─────────────────────
assert.deepStrictEqual(
  P.partesResumen({ quieto: 1, trabajando: 2, esperando: 0, termino: 1 }).map(p => p.texto),
  ['2 trabajando', '1 terminó', '1 quieto']);
assert.deepStrictEqual(P.partesResumen(null), []);
assert.strictEqual(P.partesResumen({ esperando: 1, caido: 1 })[0].estado, 'esperando');

// ── fmtDur ───────────────────────────────────────────────────────
assert.strictEqual(P.fmtDur(null), '');
assert.strictEqual(P.fmtDur(undefined), '');
assert.strictEqual(P.fmtDur(0), '0s');
assert.strictEqual(P.fmtDur(59.9), '59s');
assert.strictEqual(P.fmtDur(125), '2 min');
assert.strictEqual(P.fmtDur(7200), '2 h');
assert.strictEqual(P.fmtDur(90000), '1 d');
assert.strictEqual(P.fmtDur(-5), '0s');

// ── ordenar: esperando arriba, el resto estable por id ───────────
const ag = [
  { id: 3, estado: 'trabajando' }, { id: 1, estado: 'quieto' },
  { id: 5, estado: 'esperando' }, { id: 2, estado: 'caido' }, { id: 4, estado: 'esperando' },
];
assert.deepStrictEqual(P.ordenar(ag).map(a => a.id), [4, 5, 1, 2, 3]);
assert.deepStrictEqual(ag.map(a => a.id), [3, 1, 5, 2, 4], 'no muta la entrada');

// ── agrupar ──────────────────────────────────────────────────────
const data = {
  agentes: [
    { id: 1, nombre: 'Back', origen: 'jarvis', orquestacion: 'Login', estado: 'trabajando' },
    { id: 2, nombre: 'Front', origen: 'jarvis', orquestacion: 'Login', estado: 'termino' },
    { id: 3, nombre: 'Mía', origen: 'usuario', estado: 'esperando' },
    { id: 4, nombre: 'Shell', origen: 'usuario', estado: 'quieto' },
    { id: 5, nombre: 'Suelto', origen: 'jarvis', orquestacion: null, estado: 'quieto' },
  ],
  orquestaciones: [{ objetivo: 'Login', agentes: [1, 2] }],
};
const v = P.agrupar(data);
assert.strictEqual(v.total, 5);
assert.deepStrictEqual(v.grupos.map(g => g.clave), ['j:Login', 'j:', 'u']);
assert.deepStrictEqual(v.grupos[0].resumen, { trabajando: 1, termino: 1 });
assert.strictEqual(v.grupos[0].tipo, 'jarvis');
assert.strictEqual(v.grupos[0].titulo, 'Login');
assert.deepStrictEqual(v.grupos[2].agentes.map(a => a.id), [3, 4]);
assert.deepStrictEqual(v.resumen, { trabajando: 1, termino: 1, esperando: 1, quieto: 2 });
// orquestación con ids que ya no existen: no genera grupo vacío
assert.deepStrictEqual(P.agrupar({ agentes: [], orquestaciones: [{ objetivo: 'X', agentes: [9] }] }).grupos, []);
assert.deepStrictEqual(P.agrupar(null), { grupos: [], resumen: {}, total: 0 });

// ── nuevosEsperando: solo transiciones hacia «esperando» ─────────
const prev = new Map([[1, 'trabajando'], [2, 'esperando']]);
assert.deepStrictEqual(P.nuevosEsperando(prev, [
  { id: 1, estado: 'esperando' }, { id: 2, estado: 'esperando' }, { id: 3, estado: 'esperando' },
  { id: 4, estado: 'trabajando' },
]), [1, 3]);
assert.deepStrictEqual(P.nuevosEsperando(null, [{ id: 7, estado: 'esperando' }]), [7]);

// ── helpers ──────────────────────────────────────────────────────
assert.strictEqual(P.basename('plotspace/routers/tasks.py'), 'tasks.py');
assert.strictEqual(P.basename('x.js'), 'x.js');
assert.strictEqual(P.hostDe('http://localhost:5173/app'), 'localhost:5173');
assert.strictEqual(P.hostDe('no-es-url'), 'no-es-url');

// ── firmaAgente: el tiempo NO cambia la firma, el estado sí ──────
const a1 = { id: 1, nombre: 'A', tipo_ia: 'claude', estado: 'trabajando', estado_hace_s: 3 };
assert.strictEqual(P.firmaAgente(a1), P.firmaAgente(Object.assign({}, a1, { estado_hace_s: 99 })));
assert.notStrictEqual(P.firmaAgente(a1), P.firmaAgente(Object.assign({}, a1, { estado: 'termino' })));
assert.notStrictEqual(P.firmaAgente(a1), P.firmaAgente(Object.assign({}, a1, { titulo: 'otro' })));

console.log('tasks.test.js OK');
