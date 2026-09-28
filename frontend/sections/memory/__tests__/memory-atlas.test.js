'use strict';
// Tests de las helpers puras del Atlas de memoria (rediseño 2026-09):
// categorías, filtro compartido Lista/Grafo, vecindario, marcas de salud,
// fechas relativas y el markdown mini (escapado + wikilinks).
// Corre con: node frontend/sections/memory/__tests__/memory-atlas.test.js
const assert = require('assert');
const M = require('../memory-meta.js');

const mems = [
  { slug: 'xterm-foco', titulo: 'Xterm pierde el foco', resumen: 'refit al maximizar', categoria: 'terminales', tags: ['xterm'], estado: 'vigente', autor: 'Ana', links: ['puertos'] },
  { slug: 'puertos', titulo: 'Regla de puertos', resumen: '3000 prohibido', categoria: 'entorno', tags: ['leccion'], estado: 'vigente', autor: 'Bo', links: ['no-existe'] },
  { slug: 'deck-viejo', titulo: 'Command Deck', resumen: 'removido', categoria: 'ui', tags: [], estado: 'lapida', autor: 'Ana', links: [] },
  { slug: 'raro', titulo: 'Sin cat', resumen: '', categoria: 'sin-clasificar', tags: [], estado: 'obsoleta', autor: '', links: [] },
];
const edges = [{ from: 'xterm-foco', to: 'puertos' }];

// ── categoria(): conocida, desconocida, sin-clasificar ─────────────
assert.strictEqual(M.categoria('terminales').nombre, 'Terminales & tmux');
assert.ok(typeof M.categoria('terminales').hue === 'number');
assert.strictEqual(M.categoria('sin-clasificar').neutra, true);
assert.strictEqual(M.categoria('inventada').id, 'inventada', 'desconocida conserva su id');
assert.strictEqual(M.categoria(undefined).id, 'sin-clasificar');
// matices distintos entre las 10 canónicas
assert.strictEqual(new Set(M.CATEGORIAS.map(c => c.hue)).size, M.CATEGORIAS.length);

// ── contarCategorias: orden canónico, sin-clasificar al final ─────
assert.deepStrictEqual(M.contarCategorias(mems).map(c => [c.id, c.n]),
  [['terminales', 1], ['ui', 1], ['entorno', 1], ['sin-clasificar', 1]]);

// ── estadoDe / contarEstados ─────────────────────────────────────
assert.strictEqual(M.estadoDe({ estado: 'Lápida' }), 'lapida');
assert.strictEqual(M.estadoDe({}), 'vigente');
const ce = M.contarEstados(mems);
assert.strictEqual(ce.todas, 4); assert.strictEqual(ce.vigente, 2);
assert.strictEqual(ce.lapida, 1); assert.strictEqual(ce.leccion, 1);

// ── filtrar: texto, categorías, estado, slugs ─────────────────────
assert.deepStrictEqual(M.filtrar(mems, { q: 'XTERM' }).map(m => m.slug), ['xterm-foco'], 'q busca tags/título sin mayúsculas');
assert.deepStrictEqual(M.filtrar(mems, { q: '3000' }).map(m => m.slug), ['puertos'], 'q busca en el resumen');
assert.deepStrictEqual(M.filtrar(mems, { q: 'ana' }).map(m => m.slug), ['xterm-foco', 'deck-viejo'], 'q busca autor');
assert.deepStrictEqual(M.filtrar(mems, { cats: ['ui', 'entorno'] }).map(m => m.slug), ['puertos', 'deck-viejo']);
assert.deepStrictEqual(M.filtrar(mems, { estado: 'lapida' }).map(m => m.slug), ['deck-viejo']);
assert.deepStrictEqual(M.filtrar(mems, { estado: 'leccion' }).map(m => m.slug), ['puertos']);
assert.deepStrictEqual(M.filtrar(mems, { slugs: ['raro'] }).map(m => m.slug), ['raro']);
assert.strictEqual(M.filtrar(mems, {}).length, 4, 'filtro vacío = todo');
assert.strictEqual(M.filtrar(mems, { cats: [] }).length, 4, 'cats vacío = todas');

// ── conexiones: salientes, entrantes (backlinks) y rotos ─────────
let c = M.conexiones('puertos', edges, mems);
assert.deepStrictEqual(c.entrantes, ['xterm-foco']);
assert.deepStrictEqual(c.salientes, []);
assert.deepStrictEqual(c.rotos, ['no-existe']);
c = M.conexiones('xterm-foco', edges, mems);
assert.deepStrictEqual(c.salientes, ['puertos']);
assert.deepStrictEqual(M.grados(edges), { 'xterm-foco': 1, puertos: 1 });
assert.deepStrictEqual(M.grados([...edges, { from: 'puertos', to: 'xterm-foco' }]), { 'xterm-foco': 1, puertos: 1 }, 'ida y vuelta = 1 vecino');

// ── marcasSalud / slugsDe / puntajeSalud ─────────────────────────
const salud = {
  rotos: [{ slug: 'puertos', destino: 'no-existe' }],
  huerfanas: ['deck-viejo', 'raro'],
  choques: [{ a: 'deck-viejo', b: 'xterm-foco' }],
  contrato: [{ slug: 'raro', faltas: ['sin-tags'] }],
};
const mk = M.marcasSalud(salud);
assert.deepStrictEqual(mk['deck-viejo'].sort(), ['choques', 'huerfanas']);
assert.deepStrictEqual(mk.raro.sort(), ['contrato', 'huerfanas']);
assert.deepStrictEqual(M.slugsDe(salud, 'huerfanas'), ['deck-viejo', 'raro']);
assert.deepStrictEqual(M.marcasSalud(null), {});
assert.deepStrictEqual(M.puntajeSalud(salud, 4), { pct: 0, sanas: 0, conProblemas: 4 });
assert.deepStrictEqual(M.puntajeSalud({ huerfanas: ['raro'] }, 4), { pct: 75, sanas: 3, conProblemas: 1 });
assert.strictEqual(M.puntajeSalud(salud, 0), null, 'sin memorias = sin puntaje');

// ── fechaRelativa / haceCorto ────────────────────────────────────
const hoy = new Date('2026-09-28T15:00:00');
assert.strictEqual(M.fechaRelativa('2026-09-28', hoy), 'hoy');
assert.strictEqual(M.fechaRelativa('2026-09-27', hoy), 'ayer');
assert.strictEqual(M.fechaRelativa('2026-09-23', hoy), 'hace 5 d');
assert.strictEqual(M.fechaRelativa('2026-09-01', hoy), 'hace 4 sem');
assert.strictEqual(M.fechaRelativa('2026-06-01', hoy), 'hace 4 mes');
assert.strictEqual(M.fechaRelativa('2024-09-01', hoy), 'hace 2 a');
assert.strictEqual(M.fechaRelativa('', hoy), '');
assert.strictEqual(M.fechaRelativa('cualquiera', hoy), 'cualquiera', 'fecha no parseable se muestra tal cual');
const ahora = new Date('2026-09-28T15:00:00').getTime();
assert.strictEqual(M.haceCorto('2026-09-28T14:59:30', ahora), '30s');
assert.strictEqual(M.haceCorto('2026-09-28T14:00:00', ahora), '1h');
assert.strictEqual(M.haceCorto('2026-09-26T15:00:00', ahora), '2d');
assert.strictEqual(M.haceCorto('nope', ahora), '');

// ── resultadoUso ─────────────────────────────────────────────────
assert.strictEqual(M.resultadoUso('inyectada').k, 'inyectada');
assert.strictEqual(M.resultadoUso('done').k, 'done');
assert.strictEqual(M.resultadoUso('raro').label, 'raro');

// ── slugDeLink: mismo criterio que _slugify del router ───────────
assert.strictEqual(M.slugDeLink('Regla de Puertos'), 'regla-de-puertos');
assert.strictEqual(M.slugDeLink('Diseño & Craft'), 'diseno-craft');
assert.strictEqual(M.slugDeLink('flow-control'), 'flow-control');

// ── textoPlano: el resumen de la card sin sintaxis markdown ──────
assert.strictEqual(M.textoPlano('El canvas de **xterm** usa `fit()`. Ver [[tmux-attach]].'),
  'El canvas de xterm usa fit(). Ver tmux-attach.');
assert.strictEqual(M.textoPlano('## Título'), 'Título');
assert.strictEqual(M.textoPlano('[docs](https://x.dev)  y\n más'), 'docs y más');
assert.strictEqual(M.textoPlano(null), '');

// ── markdown: escapa ANTES de marcar ─────────────────────────────
let h = M.markdown('<script>alert(1)</script>');
assert.ok(!h.includes('<script>'), 'HTML del usuario escapado');
assert.ok(h.includes('&lt;script&gt;'));
h = M.markdown('---\ntitulo: X\n---\n\n# Título\n\nHola **mundo** y `code <b>`.');
assert.ok(!h.includes('titulo: X'), 'sin frontmatter');
assert.ok(h.includes('<h1>Título</h1>'));
assert.ok(h.includes('<b>mundo</b>'));
assert.ok(h.includes('<code>code &lt;b&gt;</code>'), 'código inline escapado');
h = M.markdown('Ver [[regla-de-puertos]] ya.', { wikiIcon: '<i></i>' });
assert.ok(h.includes('class="mem-wikilink" data-link="regla-de-puertos"'), 'wikilink = botón');
assert.ok(h.includes('<i></i>'), 'ícono del wikilink');
h = M.markdown('`[[no-link]]`');
assert.ok(!h.includes('mem-wikilink'), 'dentro de código no hay wikilink');
h = M.markdown('- uno\n- dos\n\n1. a\n2. b');
assert.ok(h.includes('<ul><li>uno</li><li>dos</li></ul>'), 'lista agrupada en <ul>');
assert.ok(h.includes('<ol><li>a</li><li>b</li></ol>'));
h = M.markdown('```js\nconst a = "<x>";\n```');
assert.ok(h.includes('<pre data-lang="js"><code>const a = &quot;&lt;x&gt;&quot;;</code></pre>'), 'bloque de código');
h = M.markdown('> ojo con esto');
assert.ok(h.includes('<blockquote>ojo con esto</blockquote>'));
h = M.markdown('[docs](https://x.dev/a) y [malo](javascript:alert(1))');
assert.ok(h.includes('<a href="https://x.dev/a" target="_blank" rel="noopener noreferrer">docs</a>'));
assert.ok(!h.includes('href="javascript'), 'solo links http(s)');
assert.strictEqual(M.markdown(null), '', 'memoria vacía no rompe');
h = M.markdown('línea 1\nlínea 2');
assert.strictEqual(h, '<p>línea 1<br>línea 2</p>');

console.log('memory-atlas.test.js OK');
