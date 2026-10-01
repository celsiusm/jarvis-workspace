'use strict';
const assert = require('node:assert');
const F = require('../../../shared/fondo.js');
const U = require('../fondo-ui.js');

const cfg = (o) => F.normalizar(o);
const base = (extra = {}) => U.html(cfg(extra.cfg || { on: true }), { F, glass: true, reducir: false, existeImagen: false, ...extra });

// Un slider por ajuste de JarvisFondo, con su rango real.
assert.deepStrictEqual(U.SLIDERS.map(s => s[0]), ['blur', 'dim', 'sat', 'term', 'panel']);
const html = base();
for (const [k] of U.SLIDERS) {
  const r = F.RANGOS[k];
  assert.ok(html.includes(`data-k="${k}" min="${r.min}" max="${r.max}"`), `slider ${k}`);
}
assert.ok(html.includes('16px') && html.includes('40%') && html.includes('30%'), 'valores por defecto visibles');

// Un tile por preset + el de la imagen propia; el elegido queda marcado.
for (const p of F.PRESETS) assert.ok(html.includes(`data-fuente="${p.id}"`), p.id);
assert.ok(html.includes('data-fuente="custom"'));
const oc = base({ cfg: { on: true, fuente: 'ocean' } });
assert.ok(/data-fuente="ocean"[^>]*>|aria-checked="true"[^>]*data-fuente="ocean"/.test(oc));
assert.strictEqual((oc.match(/aria-checked="true"/g) || []).length, 1, 'un solo preset marcado');

// Sin imagen propia: tile vacío y sin botón "Quitar"; con imagen: miniatura versionada.
assert.ok(html.includes('fd-custom vacio'));
assert.ok(/id="fd-quitar" type="button" hidden/.test(html));
const conImg = base({ cfg: { on: true, fuente: 'custom', version: 9 }, existeImagen: true });
assert.ok(conImg.includes('/api/fondo/imagen?v=9'));
assert.ok(!conImg.includes('fd-custom vacio'));
assert.ok(!/id="fd-quitar" type="button" hidden/.test(conImg));

// El interruptor refleja `on`.
assert.ok(/id="fd-on" checked/.test(html));
assert.ok(!/id="fd-on" checked/.test(base({ cfg: { on: false } })));

// Sin Liquid Glass o con "reducir transparencias": controles bloqueados + aviso.
const sinGlass = base({ glass: false });
assert.ok(sinGlass.includes('Activá Liquid Glass'));
assert.ok((sinGlass.match(/ disabled/g) || []).length >= 5 + 1 + 6, 'sliders, interruptor y tiles bloqueados');
assert.ok(base({ reducir: true }).includes('reducir transparencias'));
assert.ok(!html.includes('fd-aviso'), 'sin avisos cuando todo está bien');
assert.deepStrictEqual(U.avisos({ glass: true, reducir: false }), []);
assert.strictEqual(U.avisos({ glass: false, reducir: true }).length, 2);

// Valores con su unidad.
assert.strictEqual(U.etiquetaValor('blur', 20, F.RANGOS), '20px');
assert.strictEqual(U.etiquetaValor('term', 66, F.RANGOS), '66%');

// El contenido que viene de datos se escapa (la versión nunca rompe el atributo).
assert.ok(!base({ cfg: { on: true, fuente: 'custom', version: '9"><script>' }, existeImagen: true }).includes('<script>'));

console.log('fondo-ui: ok');
