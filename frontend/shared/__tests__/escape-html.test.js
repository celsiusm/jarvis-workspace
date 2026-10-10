'use strict';
// Los helpers de escape HTML (esc, _esc, orchEsc, …) se usan DENTRO de atributos
// (data-path="…", title="…"). Si no escapan comillas, un nombre de archivo como
// `x" onmouseover="…` abre un atributo nuevo y ejecuta JS en el dashboard (que
// escribe en las terminales). El truco textContent→innerHTML NO escapa comillas.
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const RAIZ = path.join(__dirname, '..', '..');   // frontend/

function archivos(dir) {
  const out = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.name === 'vendor' || e.name === '__tests__' || e.name === 'node_modules') continue;
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...archivos(p));
    else if (/\.(js|html)$/.test(e.name)) out.push(p);
  }
  return out;
}

const fuentes = archivos(RAIZ).map(p => ({ p: path.relative(RAIZ, p), s: fs.readFileSync(p, 'utf8') }));

// 1) Nadie escapa con textContent→innerHTML (no escapa comillas).
const trucos = fuentes.filter(f => /textContent\s*=[^;]+;\s*return\s+\w+\.innerHTML/.test(f.s)).map(f => f.p);
assert.deepStrictEqual(trucos, [], 'escape con textContent→innerHTML (no escapa comillas): ' + trucos.join(', '));

// 2) Todo helper de escape que reemplaza `&` también escapa `"` y `'`.
const flojos = [];
for (const f of fuentes) {
  const re = /(?:function\s+(\w*[eE]sc\w*)\s*\([^)]*\)\s*\{|(?:const|let|var)\s+(\w*[eE]sc\w*)\s*=\s*\([^)]*\)\s*=>)/g;
  let m;
  while ((m = re.exec(f.s))) {
    const cuerpo = f.s.slice(m.index, m.index + 400);
    if (!cuerpo.includes('&amp;')) continue;   // no es un escape HTML (o delega en otro)
    if (!cuerpo.includes('&quot;') || !cuerpo.includes('&#39;')) flojos.push(`${f.p}:${m[1] || m[2]}`);
  }
}
assert.deepStrictEqual(flojos, [], 'helpers de escape sin comillas: ' + flojos.join(', '));

// 3) El esc() del shell (window.esc, el que más se usa en atributos) neutraliza un atributo inyectado.
const ws = fuentes.find(f => f.p === path.join('shell', 'workspace.js')).s;
const def = ws.match(/function esc\(str\) \{[\s\S]*?\n\}/);
assert.ok(def, 'no encontré function esc(str) en shell/workspace.js');
const esc = new Function(def[0] + '; return esc;')();
const malo = 'x" onmouseover="alert(1)\' y <img>&';
const salida = esc(malo);
assert.ok(!/["'<>]/.test(salida), 'esc() dejó pasar un caracter peligroso: ' + salida);
assert.strictEqual(salida, 'x&quot; onmouseover=&quot;alert(1)&#39; y &lt;img&gt;&amp;');
assert.strictEqual(esc(null), '');
assert.strictEqual(esc(42), '42');

console.log('escape-html: ok');
