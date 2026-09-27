'use strict';
const assert = require('node:assert');
const R = require('../review-grupos.js');

// separarArtefactos: lo que generan las herramientas (.workspace/, dist/, logs)
// sale de su grupo y va a un grupo propio AL FINAL, marcado como artefacto.
{
  const clase = { 'a.py': 'real', '.workspace/': 'artefacto', 'dist/x.js': 'artefacto', 'b.md': 'real' };
  const out = R.separarArtefactos([
    { id: 'ag1', nombre: 'Backend', archivos: [{ path: 'a.py' }, { path: 'dist/x.js' }] },
    { id: 'sin', nombre: 'Sin atribuir', archivos: [{ path: '.workspace/' }, { path: 'b.md' }] },
  ], p => clase[p]);
  assert.deepStrictEqual(out.map(g => g.id), ['ag1', 'sin', 'gen']);
  assert.deepStrictEqual(out[0].archivos.map(f => f.path), ['a.py']);
  assert.deepStrictEqual(out[1].archivos.map(f => f.path), ['b.md']);
  assert.strictEqual(out[2].artefacto, true);
  assert.deepStrictEqual(out[2].archivos.map(f => f.path), ['dist/x.js', '.workspace/']);
}
// Grupos que quedan vacíos desaparecen; sin artefactos no hay grupo 'gen'.
{
  const out = R.separarArtefactos([
    { id: 'sin', nombre: 'Sin atribuir', archivos: [{ path: '.workspace/' }] },
    { id: 'ag2', nombre: 'Tests', archivos: [{ path: 't.py' }] },
  ], p => (p === '.workspace/' ? 'artefacto' : 'real'));
  assert.deepStrictEqual(out.map(g => g.id), ['ag2', 'gen']);
  const sin = R.separarArtefactos([{ id: 'all', nombre: 'C', archivos: [{ path: 'x' }] }], () => 'real');
  assert.deepStrictEqual(sin.map(g => g.id), ['all']);
}
// Clase desconocida (backend viejo sin 'clase') = real: nunca esconder trabajo.
{
  const out = R.separarArtefactos([{ id: 'all', nombre: 'C', archivos: [{ path: 'x' }] }], () => undefined);
  assert.deepStrictEqual(out.map(g => g.id), ['all']);
}

// statTexto: sin números reales (carpeta nueva, binario) NO se inventa «+· −·».
assert.deepStrictEqual(R.statTexto('3', '0'), { mas: '+3', menos: '−0' });
assert.deepStrictEqual(R.statTexto(12, 4), { mas: '+12', menos: '−4' });
assert.strictEqual(R.statTexto('·', '·'), null);
assert.strictEqual(R.statTexto('-', '-'), null);          // git numstat de binarios
assert.strictEqual(R.statTexto(undefined, undefined), null);
console.log('review-grupos.test.js OK');
