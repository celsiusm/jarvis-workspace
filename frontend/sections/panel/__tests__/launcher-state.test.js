'use strict';
const assert = require('node:assert');
const L = require('../launcher-state.js');

// Orden estable de CLIs
assert.deepStrictEqual(L.CLI_ORDEN, ['claude', 'codex', 'opencode', 'qwen', 'antigravity', 'grok', 'cursor', 'pi', 'manual']);

// loteDesdeContadores: arma el batch con nombres numerados desde `desde`
assert.deepStrictEqual(
  L.loteDesdeContadores({ claude: 2, codex: 1 }, 0),
  [
    { nombre: 'Claude Code #1', tipo_ia: 'claude' },
    { nombre: 'Claude Code #2', tipo_ia: 'claude' },
    { nombre: 'Codex #3',       tipo_ia: 'codex'  },
  ]);
assert.deepStrictEqual(L.loteDesdeContadores({}, 0), []);
assert.deepStrictEqual(
  L.loteDesdeContadores({ manual: 1 }, 4),
  [{ nombre: 'Shell #5', tipo_ia: 'manual' }]);

// totalContadores + etiqueta del CTA
assert.strictEqual(L.totalContadores({ claude: 2, qwen: 1 }), 3);
assert.strictEqual(L.etiquetaCrear({}), 'Crear proyecto');
assert.strictEqual(L.etiquetaCrear({ claude: 1 }), 'Crear con 1 terminal');
assert.strictEqual(L.etiquetaCrear({ claude: 2, codex: 1 }), 'Crear con 3 terminales');

// clamp contra MAX_TERMINALES (12, pedido 2026-07-03) considerando las ya existentes
assert.strictEqual(L.MAX_TERMINALES, 12);
assert.strictEqual(L.clampContador(8, 3, { claude: 8 }, 'claude'), 8);    // 8 ok: 3 usadas + 8 ≤ 12 (9 libres)
assert.strictEqual(L.clampContador(12, 3, { claude: 12 }, 'claude'), 9);  // tope: 12-3 = 9 libres
assert.strictEqual(L.clampContador(-1, 0, {}, 'claude'), 0);              // piso 0

// resumenCounts: legible y en orden CLI_ORDEN
assert.strictEqual(L.resumenCounts({ codex: 1, claude: 2 }), '2× Claude Code · 1× Codex');
assert.strictEqual(L.resumenCounts({}), 'Sin terminales');
assert.strictEqual(L.resumenCounts({ manual: 3 }), '3× Shell');

// aplicarTemplate: clamp acumulativo contra el cupo libre, set completo
assert.deepStrictEqual(
  L.aplicarTemplate({ claude: 2, codex: 1 }, 0),
  { claude: 2, codex: 1, opencode: 0, qwen: 0, antigravity: 0, grok: 0, cursor: 0, pi: 0, manual: 0 });
assert.deepStrictEqual(
  L.aplicarTemplate({ claude: 8, codex: 5 }, 3),   // 9 libres: 8 claude + 1 codex
  { claude: 8, codex: 1, opencode: 0, qwen: 0, antigravity: 0, grok: 0, cursor: 0, pi: 0, manual: 0 });
assert.deepStrictEqual(
  L.aplicarTemplate({ claude: 2 }, 12),            // sin cupo → todo 0
  { claude: 0, codex: 0, opencode: 0, qwen: 0, antigravity: 0, grok: 0, cursor: 0, pi: 0, manual: 0 });

// mismosCounts: faltantes equivalen a 0
assert.ok(L.mismosCounts({ claude: 1 }, { claude: 1, codex: 0 }));
assert.ok(!L.mismosCounts({ claude: 1 }, { claude: 2 }));
assert.ok(L.mismosCounts({}, null));

// templatesValidos: sanitiza basura de localStorage
assert.deepStrictEqual(L.templatesValidos(null), []);
assert.deepStrictEqual(L.templatesValidos('x'), []);
assert.deepStrictEqual(
  L.templatesValidos([
    { nombre: '  Mi squad  ', counts: { claude: 2, inventada: 5 } },
    { nombre: 'Mi squad', counts: { claude: 1 } },          // dup → fuera
    { nombre: '', counts: { claude: 1 } },                  // sin nombre → fuera
    { nombre: 'Vacío', counts: {} },                        // 0 terminales → fuera
    { nombre: 'Pasado', counts: { codex: 99 } },            // clamp a 12
  ]),
  [
    { nombre: 'Mi squad', counts: { claude: 2 } },
    { nombre: 'Pasado',   counts: { codex: 12 } },
  ]);
assert.strictEqual(
  L.templatesValidos(Array.from({ length: 30 }, (_, i) =>
    ({ nombre: `T${i}`, counts: { claude: 1 } }))).length,
  L.MAX_TEMPLATES);

// PRESETS integrados: shape válido y dentro del cupo
assert.ok(Array.isArray(L.PRESETS) && L.PRESETS.length >= 3);
for (const p of L.PRESETS) {
  assert.ok(p.nombre && L.totalContadores(p.counts) > 0);
  assert.ok(L.totalContadores(p.counts) <= L.MAX_TERMINALES);
}

// countsIniciales: TODOS los tipos de CLI_ORDEN presentes (fuente única — el
// reset del launcher enumeraba a mano y omitía grok), TODO en 0: el launcher no
// preselecciona agentes.
const ini = L.countsIniciales();
assert.deepStrictEqual(Object.keys(ini).sort(), [...L.CLI_ORDEN].sort());
assert.ok(L.CLI_ORDEN.every(t => ini[t] === 0));
assert.strictEqual(L.totalContadores(ini), 0);

// etiquetaAbrir: CTA del modo "Abrir de la PC" (no dice "Crear" al abrir)
assert.strictEqual(L.etiquetaAbrir({}), 'Abrir proyecto');
assert.strictEqual(L.etiquetaAbrir({ claude: 1 }), 'Abrir con 1 terminal');
assert.strictEqual(L.etiquetaAbrir({ claude: 2, codex: 1 }), 'Abrir con 3 terminales');
console.log('launcher-state.test.js OK');
