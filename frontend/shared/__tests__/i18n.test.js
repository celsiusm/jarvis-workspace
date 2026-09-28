// Tests de la lógica pura del motor i18n. Corre con:
//   node frontend/shared/__tests__/i18n.test.js
'use strict';
const assert = require('assert');
const { _pure } = require('../i18n.js');
const { traducir, normalizar, partes, origenAtributo } = _pure;

const DICT = { 'Nuevo proyecto': 'New project', 'Buscar…': 'Search…', 'Guardar': 'Save' };

// ── normalizar: colapsa espacios y recorta ──
assert.strictEqual(normalizar('  Nuevo   proyecto \n'), 'Nuevo proyecto');
assert.strictEqual(normalizar(''), '');
assert.strictEqual(normalizar(null), '');

// ── traducir: solo en inglés, contra el dict ──
assert.strictEqual(traducir('Nuevo proyecto', 'en', DICT), 'New project');
assert.strictEqual(traducir('  Nuevo   proyecto  ', 'en', DICT), 'New project', 'normaliza antes de buscar');
assert.strictEqual(traducir('Nuevo proyecto', 'es', DICT), null, 'en español no traduce');
assert.strictEqual(traducir('Inexistente', 'en', DICT), null, 'sin entrada → null');
assert.strictEqual(traducir('', 'en', DICT), null);
assert.strictEqual(traducir('Buscar…', 'en', DICT), 'Search…');
// Traducción igual al original → null (no hace falta tocar el DOM)
assert.strictEqual(traducir('Guardar', 'en', { 'Guardar': 'Guardar' }), null);

// ── partes: separa whitespace para conservar el que rodea al núcleo ──
let p = partes('\n  Crear cuenta  ');
assert.strictEqual(p.pre, '\n  ');
assert.strictEqual(p.core, 'Crear cuenta');
assert.strictEqual(p.post, '  ');
// Sin espacios alrededor
p = partes('Hola');
assert.strictEqual(p.pre, ''); assert.strictEqual(p.core, 'Hola'); assert.strictEqual(p.post, '');
// Reconstrucción exacta
['  x  ', 'y', '\t z\n', ''].forEach(v => {
  const q = partes(v);
  assert.strictEqual(q.pre + q.core + q.post, v, 'pre+core+post reconstruye el original');
});

console.log('i18n: OK');

// ── origenAtributo: un atributo que la APP cambia no se revierte ──
const D2 = { 'Enviar': 'Send', 'Detener': 'Stop' };
assert.strictEqual(origenAtributo(null, 'Enviar', 'en', D2), 'Enviar', 'primera vez: el actual');
assert.strictEqual(origenAtributo('Enviar', 'Send', 'en', D2), 'Enviar', 'ya traducido: sigue el guardado');
assert.strictEqual(origenAtributo('Enviar', 'Enviar', 'en', D2), 'Enviar');
assert.strictEqual(origenAtributo('Enviar', 'Detener', 'en', D2), 'Detener', 'la app lo cambió: manda el nuevo');
assert.strictEqual(origenAtributo('Enviar', 'Stop', 'en', D2), 'Stop', 'cambiado ya traducido: no se pisa');
console.log('origenAtributo ok');

// ── Plantillas: claves con {marcadores} traducen textos con valores adentro ──
{
  const { compilarPlantillas } = _pure;
  const D = {
    'hace {t}': '{t} ago',
    'Error creando: {msg}': 'Error creating: {msg}',
    '⚠️ {a} está bloqueado en el paso {n}{d}. ¿Cómo continuamos?': '⚠️ {a} is blocked at step {n}{d}. How do we proceed?',
    'Crear con {n} terminales': 'Create with {n} terminals',
    'Crear con {n} terminal': 'Create with {n} terminal',
    'trabajando': 'working',
    'Estado: {e}': 'Status: {e}',
    '{a} {b}': 'nunca',          // sin ancla literal → no se compila
    'Guardar': 'Save',
  };
  const P = compilarPlantillas(D);
  assert.ok(P.every(p => p.clave !== '{a} {b}'), 'plantilla sin texto fijo no se compila');
  assert.ok(P.every(p => p.clave !== 'Guardar'), 'solo las claves con marcadores');
  const tr = (s) => traducir(s, 'en', D, P);
  assert.strictEqual(tr('hace 5 min'), '5 min ago');
  assert.strictEqual(tr('Error creando: permiso denegado'), 'Error creating: permiso denegado', 'el valor se conserva');
  assert.strictEqual(tr('⚠️ Backend está bloqueado en el paso 2: falta la key. ¿Cómo continuamos?'),
    '⚠️ Backend is blocked at step 2: falta la key. How do we proceed?');
  assert.strictEqual(tr('⚠️ Backend está bloqueado en el paso 2. ¿Cómo continuamos?'),
    '⚠️ Backend is blocked at step 2. How do we proceed?', 'marcador vacío');
  assert.strictEqual(tr('Crear con 3 terminales'), 'Create with 3 terminals');
  assert.strictEqual(tr('Crear con 1 terminal'), 'Create with 1 terminal');
  assert.strictEqual(tr('Estado: trabajando'), 'Status: working', 'el valor capturado también se traduce si es frase conocida');
  assert.strictEqual(tr('Guardar'), 'Save', 'lo exacto sigue ganando');
  assert.strictEqual(tr('nada que ver'), null);
  assert.strictEqual(traducir('hace 5 min', 'es', D, P), null, 'en español no toca nada');
  assert.strictEqual(traducir('hace 5 min', 'en', D), null, 'sin plantillas compiladas = solo exacto (compat)');
  // Regex-safe: los literales con símbolos no rompen la compilación
  const D3 = { '({n}) archivos [x] * + ?': '({n}) files [x] * + ?' };
  assert.strictEqual(traducir('(4) archivos [x] * + ?', 'en', D3, compilarPlantillas(D3)), '(4) files [x] * + ?');
  // Espacios colapsados como el resto del motor
  assert.strictEqual(tr('hace   5   min'), '5 min ago');
  console.log('plantillas ok');
}
{
  const { compilarPlantillas } = _pure;
  const D = { 'Copiar [[{s}]]': 'Copy [[{s}]]' };
  const P = compilarPlantillas(D);
  assert.strictEqual(origenAtributo('Copiar [[a-b]]', 'Copy [[a-b]]', 'en', D, P), 'Copiar [[a-b]]', 'traducido por plantilla: sigue el guardado');
  console.log('origenAtributo + plantillas ok');
}
{
  const { compilarPlantillas } = _pure;
  const D = { 'hace {t}': '{t} ago', 'en {branch}': 'on {branch}', 'Tarea: {x}': 'Task: {x}' };
  const P = compilarPlantillas(D);
  assert.strictEqual(traducir('hace 5 min', 'en', D, P), '5 min ago');
  assert.strictEqual(traducir('hace falta el login', 'en', D, P), null, 'plantilla corta no pisa texto del usuario');
  assert.strictEqual(traducir('en el camino', 'en', D, P), null);
  assert.strictEqual(traducir('Tarea: arreglar el login', 'en', D, P), 'Task: arreglar el login', 'con texto fijo suficiente no pide número');
  console.log('plantillas cortas ok');
}
