'use strict';
const assert = require('node:assert');
const F = require('../fondo.js');

// ── normalizar: cualquier basura vuelve a valores válidos ──
const d = F.normalizar(null);
assert.deepStrictEqual(d, { on: false, fuente: 'aurora', version: 0, blur: 14, dim: 30, sat: 115, term: 66, panel: 58 });
assert.deepStrictEqual(F.normalizar('x'), d);
assert.deepStrictEqual(F.normalizar([]), d);

const n = F.normalizar({ on: 'yes', fuente: 'nope', version: -3, blur: 999, dim: -5, sat: 'abc', term: 5, panel: 500 });
assert.strictEqual(n.on, false, 'solo true estricto activa');
assert.strictEqual(n.fuente, 'aurora');
assert.strictEqual(n.version, 0);
assert.strictEqual(n.blur, F.RANGOS.blur.max);
assert.strictEqual(n.dim, F.RANGOS.dim.min);
assert.strictEqual(n.sat, F.RANGOS.sat.def);
assert.strictEqual(n.term, F.RANGOS.term.min);
assert.strictEqual(n.panel, F.RANGOS.panel.max);

assert.strictEqual(F.normalizar({ fuente: 'custom', version: 7.9 }).version, 7);
assert.strictEqual(F.normalizar({ fuente: 'ocean' }).fuente, 'ocean');
assert.strictEqual(F.normalizar({ blur: 12.6 }).blur, 13, 'redondea');
assert.strictEqual(F.normalizar({ blur: 0 }).blur, 0, 'cero es válido (sin blur)');

// ── presets: ids únicos, todos con css y la aurora sigue al tema ──
assert.strictEqual(new Set(F.IDS_PRESET).size, F.PRESETS.length);
for (const p of F.PRESETS) {
  assert.ok(p.nombre && p.css.includes('gradient'), p.id);
  assert.ok(!/#[0-9a-fA-F]{3,8}\b/.test(p.css), `${p.id}: nada de hex`);
}
for (const p of F.PRESETS) assert.ok(!p.css.includes('var(--ob-'), `${p.id}: el fondo no depende del tema`);

// ── activo(): prendido + glass + (imagen propia) + sin reducir transparencias ──
assert.strictEqual(F.activo({ on: false }), false);
assert.strictEqual(F.activo({ on: true }), true);
assert.strictEqual(F.activo({ on: true }, { glass: false }), false, 'sin Liquid Glass no hay fondo');
assert.strictEqual(F.activo({ on: true }, { glass: true }), true);
assert.strictEqual(F.activo({ on: true }, { reducirTransparencia: true }), false);
assert.strictEqual(F.activo({ on: true, fuente: 'custom', version: 0 }), false, 'propia sin imagen: inactivo');
assert.strictEqual(F.activo({ on: true, fuente: 'custom', version: 5 }), true);

// ── fondoCss: preset = su degradado; propia = url versionada ──
assert.strictEqual(F.fondoCss({ fuente: 'ocean' }), F.PRESETS.find(p => p.id === 'ocean').css);
assert.strictEqual(F.fondoCss({ fuente: 'custom', version: 42 }), 'url("/api/fondo/imagen?v=42") center / cover no-repeat');
assert.strictEqual(F.fondoCss({ fuente: 'custom', version: 0 }), 'none');
assert.strictEqual(F.fondoCss(null), F.PRESETS[0].css, 'basura → aurora');

// ── variables: lo que consume fondo.css ──
const v = F.variables({ blur: 20, dim: 40, sat: 130, term: 55, panel: 70, fuente: 'dusk' });
assert.strictEqual(v['--gw-blur'], '20px');
assert.strictEqual(v['--gw-dim'], '40%');
assert.strictEqual(v['--gw-sat'], '130%');
assert.strictEqual(v['--gw-term'], '55%');
assert.strictEqual(v['--gw-panel'], '70%');
assert.ok(v['--gw-img'].includes('gradient'));
assert.deepStrictEqual(Object.keys(v).sort(), ['--gw-blur', '--gw-dim', '--gw-img', '--gw-panel', '--gw-sat', '--gw-term']);

// ── medidasAchicadas: nunca agranda, respeta el aspecto ──
assert.deepStrictEqual(F.medidasAchicadas(1920, 1080), { w: 1920, h: 1080 });
assert.deepStrictEqual(F.medidasAchicadas(5120, 2880), { w: 2560, h: 1440 });
assert.deepStrictEqual(F.medidasAchicadas(1000, 6000), { w: 427, h: 2560 });
assert.deepStrictEqual(F.medidasAchicadas(0, 0), { w: 1, h: 1 });
assert.deepStrictEqual(F.medidasAchicadas(4000, 3000, 1000), { w: 1000, h: 750 });

console.log('fondo: ok');
