'use strict';
// Tests de la simulación de fuerzas del Grafo de memoria (constelaciones).
// Corre con: node frontend/sections/memory/__tests__/memory-graph.test.js
const assert = require('assert');
const G = require('../memory-graph.js');

const nodos = [];
const cats = ['terminales', 'ui', 'swarm'];
for (let i = 0; i < 18; i++) nodos.push({ id: 'n' + i, cat: cats[i % 3], grado: 0 });
const aristas = [
  { a: 'n0', b: 'n3' }, { a: 'n3', b: 'n6' }, { a: 'n1', b: 'n4' },
  { a: 'n2', b: 'n5' }, { a: 'n0', b: 'n1' }, { a: 'n0', b: 'n0' }, { a: 'n0', b: 'zz' },
];

// ── crear: descarta aristas inválidas (auto-lazo / nodo inexistente) ──
let sim = G.crear(nodos, aristas);
assert.strictEqual(sim.aristas.length, 5, 'auto-lazo y destino inexistente fuera');
assert.strictEqual(sim.alpha, 1, 'layout nuevo arranca caliente');

// ── determinista: mismo input = mismo layout ─────────────────────
const a = G.asentar(G.crear(nodos, aristas));
const b = G.asentar(G.crear(nodos, aristas));
assert.deepStrictEqual(a.nodos.map(n => [n.x, n.y]), b.nodos.map(n => [n.x, n.y]));

// ── se asienta (el loop de animación se apaga solo) ──────────────
assert.ok(G.asentada(a), 'alpha bajo el mínimo tras asentar');
assert.ok(a.nodos.every(n => isFinite(n.x) && isFinite(n.y)), 'sin NaN');

// ── los enlazados quedan más cerca que el promedio ───────────────
const dist = (p, q) => Math.hypot(p.x - q.x, p.y - q.y);
const by = Object.fromEntries(a.nodos.map(n => [n.id, n]));
const enl = [['n0', 'n3'], ['n3', 'n6'], ['n1', 'n4'], ['n2', 'n5']].map(([x, y]) => dist(by[x], by[y]));
let tot = 0, cnt = 0;
for (let i = 0; i < a.nodos.length; i++) for (let j = i + 1; j < a.nodos.length; j++) { tot += dist(a.nodos[i], a.nodos[j]); cnt++; }
assert.ok(enl.reduce((s, d) => s + d, 0) / enl.length < tot / cnt, 'resortes acercan a los enlazados');

// ── constelaciones: cada nodo cerca de SU ancla más que de las otras (mayoría) ──
let propios = 0;
for (const n of a.nodos) {
  const dPropia = dist(n, a.anclas[n.cat]);
  if (cats.filter(c => c !== n.cat).every(c => dPropia < dist(n, a.anclas[c]))) propios++;
}
assert.ok(propios >= a.nodos.length * 0.75, `racimos por categoría (${propios}/${a.nodos.length})`);
const cons = G.constelaciones(a);
assert.strictEqual(cons.length, 3);
assert.ok(cons.every(c => c.r >= 46 && c.n === 6));

// ── sin colisiones: ningún par se pisa ───────────────────────────
for (let i = 0; i < a.nodos.length; i++) for (let j = i + 1; j < a.nodos.length; j++) {
  const p = a.nodos[i], q = a.nodos[j];
  assert.ok(dist(p, q) > p.r + q.r, `${p.id}/${q.id} no se superponen`);
}

// ── posiciones previas: arranca tibio y no re-baraja ─────────────
const previas = Object.fromEntries(a.nodos.map(n => [n.id, { x: n.x, y: n.y }]));
sim = G.crear(nodos, aristas, previas);
assert.ok(sim.alpha < 0.5, 're-render arranca tibio');
G.asentar(sim);
const mov = sim.nodos.reduce((s, n) => s + dist(n, previas[n.id]), 0) / sim.nodos.length;
assert.ok(mov < 20, `layout estable entre renders (mov medio ${mov.toFixed(1)})`);

// ── nodo fijo (arrastre) no se mueve ─────────────────────────────
sim = G.crear(nodos, aristas, previas);
sim.nodos[0].fijo = true; sim.nodos[0].x = 999; sim.nodos[0].y = 999;
G.recalentar(sim, 0.5);
for (let i = 0; i < 30; i++) G.paso(sim);
assert.deepStrictEqual([sim.nodos[0].x, sim.nodos[0].y], [999, 999]);

// ── una sola categoría: ancla al centro ──────────────────────────
assert.deepStrictEqual(G.anclas(['ui']), { ui: { x: 0, y: 0 } });

// ── limites + encuadre ───────────────────────────────────────────
const caja = G.limites(a, 20);
assert.ok(caja.w > 0 && caja.h > 0);
const t = G.encuadre(caja, 800, 600);
// el centro de la caja cae en el centro del viewport
assert.ok(Math.abs((caja.x + caja.w / 2) * t.k + t.x - 400) < 1e-6);
assert.ok(Math.abs((caja.y + caja.h / 2) * t.k + t.y - 300) < 1e-6);
assert.deepStrictEqual(G.limites({ nodos: [] }), { x: -100, y: -100, w: 200, h: 200 });

// ── grafo vacío no rompe ─────────────────────────────────────────
assert.ok(G.asentada(G.asentar(G.crear([], []))));

console.log('memory-graph.test.js OK');
