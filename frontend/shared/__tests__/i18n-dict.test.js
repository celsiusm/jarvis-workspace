// Sanidad del diccionario es→en (frontend/shared/i18n-dict.js). Corre con:
//   node frontend/shared/__tests__/i18n-dict.test.js
'use strict';
const assert = require('assert');
const { _pure } = require('../i18n.js');

const DICT = {};
global.window = { JarvisI18n: { agregar: (o) => { for (const k in o) DICT[_pure.normalizar(k)] = o[k]; } } };
require('../i18n-dict.js');

const MARCA = /\{([A-Za-z_]\w*)\}/g;
const marcas = (s) => [...String(s).matchAll(MARCA)].map((m) => m[1]).sort();

let n = 0;
for (const [k, v] of Object.entries(DICT)) {
  n++;
  assert.ok(typeof v === 'string' && v.trim(), `traducción vacía para "${k}"`);
  // Plantillas: los mismos marcadores de los dos lados (si no, el valor
  // capturado se pierde o aparece un "{x}" crudo en la UI).
  assert.deepStrictEqual(marcas(v), marcas(k), `marcadores distintos en "${k}" → "${v}"`);
}
assert.ok(n > 1500, 'el diccionario se cargó entero');

// Las plantillas compilan y se resuelven con valores reales.
const P = _pure.compilarPlantillas(DICT);
assert.ok(P.length > 20, 'hay plantillas compiladas');
for (const p of P) {
  const ejemplo = p.clave.replace(MARCA, 'Zq9');
  const tr = _pure.traducir(ejemplo, 'en', DICT, P);
  if (p.en === p.clave) continue;   // plantilla identidad ("Error: {msg}"): traduce solo el valor
  assert.ok(tr != null, `la plantilla "${p.clave}" no se traduce a sí misma`);
  assert.ok(!/\{[A-Za-z_]\w*\}/.test(tr), `quedó un marcador crudo traduciendo "${ejemplo}" → "${tr}"`);
}
console.log(`i18n-dict: ${n} entradas, ${P.length} plantillas OK`);
