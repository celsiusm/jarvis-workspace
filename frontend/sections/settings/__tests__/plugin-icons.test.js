'use strict';
const assert = require('node:assert');
const P = require('../plugin-icons.js');
const ico = P.iconoDePlugin;

// Los 10 plugins realmente instalados en este box: CADA UNO con su glifo, y
// ninguno cae al enchufe genérico (el bug era justamente 10 enchufes iguales).
const REALES = {
  'comprehensive-review@claude-plugins-official': ['Comprehensive Review', 'search'],
  'context7@claude-plugins-official':             ['Context7',             'globe'],
  'expo@claude-plugins-official':                 ['Expo',                 'phone'],
  'github@claude-plugins-official':               ['Github',               'git-branch'],
  'rust-analyzer-lsp@claude-plugins-official':    ['Rust Analyzer Lsp',    'cpu'],
  'skill-creator@claude-plugins-official':        ['Skill Creator',        'edit'],
  'static-analysis@claude-plugins-official':      ['Static Analysis',      'eye'],
  'superpowers@claude-plugins-official':          ['Superpowers',          'zap'],
  'tdd-workflows@claude-plugins-official':        ['Tdd Workflows',        'list-checks'],
  'ui-ux-pro-max@ui-ux-pro-max-skill':            ['Ui Ux Pro Max',        'sparkles'],
};
const vistos = new Set();
for (const [fullId, [nombre, esperado]] of Object.entries(REALES)) {
  assert.strictEqual(ico(fullId, nombre), esperado, `${fullId} → ${esperado}`);
  assert.notStrictEqual(ico(fullId, nombre), P.FALLBACK, `${fullId} no debería caer al fallback`);
  vistos.add(esperado);
}
assert.strictEqual(vistos.size, Object.keys(REALES).length, 'los 10 iconos son distintos entre sí');

// El ORDEN de las reglas es la parte frágil: estos ids matchean más de una.
assert.strictEqual(ico('rust-analyzer-lsp', ''), 'cpu');          // lsp gana a "analyzer"/lint
assert.strictEqual(ico('tdd-workflows', ''), 'list-checks');      // tdd gana a "workflow"
assert.strictEqual(ico('static-analysis', ''), 'eye');            // seguridad gana a "analysis"
assert.strictEqual(ico('skill-creator', ''), 'edit');             // creator no es "superpower"
assert.strictEqual(ico('comprehensive-review', ''), 'search');    // review no es "scan"

// El sufijo `@<marketplace>` NO describe al plugin: con él adentro, las 174
// cards de `@claude-code-workflows` matcheaban "workflow" y salían todas con
// el rayo de orquestación. Se corta en el `@` (regresión vista en browser).
assert.strictEqual(ico('accessibility-compliance@claude-code-workflows', 'Accessibility Compliance'), 'keyboard');
assert.strictEqual(ico('arm-cortex-microcontrollers@claude-code-workflows', 'Arm Cortex Microcontrollers'), 'plug');
assert.strictEqual(ico('application-performance@claude-code-workflows', 'Application Performance'), 'chart');
// …pero el que SÍ orquesta sigue siendo un rayo.
assert.strictEqual(ico('agent-orchestration@claude-code-workflows', 'Agent Orchestration'), 'zap');
assert.strictEqual(ico('agent-teams@claude-code-workflows', 'Agent Teams'), 'zap');
// El marketplace tampoco debe teñir por «skill», «official» ni «data».
assert.strictEqual(ico('mi-cosa@ui-ux-pro-max-skill', 'Mi Cosa'), 'plug');
assert.strictEqual(ico('mi-cosa@claude-plugins-official', 'Mi Cosa'), 'plug');

// Sin match → el enchufe de siempre (nunca undefined ni string vacío).
assert.strictEqual(ico('algo-rarisimo@nadie', 'Algo Rarísimo'), 'plug');
assert.strictEqual(ico('', ''), 'plug');
assert.strictEqual(ico(null, undefined), 'plug');
assert.strictEqual(ico(undefined, undefined), P.FALLBACK);

// Case-insensitive: los nombres vienen capitalizados desde el backend.
assert.strictEqual(ico('GITHUB@Claude-Plugins-Official', 'GitHub'), 'git-branch');
assert.strictEqual(ico('', 'Expo Router'), 'phone');

// Sólo id + nombre: la descripción no participa (es prosa y da falsos positivos).
assert.strictEqual(ico('mi-plugin', 'Mi Plugin'), 'plug');

// Todo icono devuelto tiene que existir en el set de shared/ui.js — si alguien
// borra un glifo de ahí, este test lo caza antes de que el rack quede vacío.
const fs = require('node:fs');
const path = require('node:path');
const uiSrc = fs.readFileSync(path.join(__dirname, '..', '..', '..', 'shared', 'ui.js'), 'utf8');
const bloque = uiSrc.slice(uiSrc.indexOf('const ICONS'), uiSrc.indexOf('function icon('));
const disponibles = new Set([...bloque.matchAll(/'([a-z0-9-]+)':\s*'</g)].map(m => m[1]));
assert.ok(disponibles.size > 20, 'se leyó el set de iconos de ui.js');
for (const [, ic] of P.REGLAS) {
  assert.ok(disponibles.has(ic), `icono '${ic}' no existe en shared/ui.js`);
}
assert.ok(disponibles.has(P.FALLBACK), `fallback '${P.FALLBACK}' no existe en shared/ui.js`);

// ── Tonos del chip ────────────────────────────────────────────────────────
// El tono categoriza igual que el glifo (paleta .tone-*). Todo glifo de las
// reglas tiene que tener tono — si alguien agrega una regla y se olvida, el
// chip cae al violeta en silencio y la lista pierde la pista de color.
const TONOS_VALIDOS = new Set(['tone-violet','tone-blue','tone-teal','tone-cyan','tone-amber','tone-rose','tone-green']);
assert.ok(TONOS_VALIDOS.has(P.TONO_FALLBACK), 'el tono de fallback es una clase .tone-* real');
assert.strictEqual(P.tonoDeGlifo('no-existe'), P.TONO_FALLBACK);
for (const [, ic] of P.REGLAS) {
  const t = P.TONO_POR_GLIFO[ic];
  assert.ok(t, `glifo '${ic}' sin tono asignado`);
  assert.ok(TONOS_VALIDOS.has(t), `tono '${t}' (glifo '${ic}') no es una clase .tone-* válida`);
}
// La clase .tone-* tiene que existir en el CSS: si la renombran, los chips
// quedan sin color (el fallback de var() los deja grises).
const baseCss = fs.readFileSync(path.join(__dirname, '..', '..', '..', 'shared', 'base.css'), 'utf8');
for (const t of TONOS_VALIDOS) assert.ok(baseCss.includes(`.${t} `), `falta .${t} en shared/base.css`);

// El tono DERIVA del glifo (una sola fuente de verdad), no de un segundo set
// de reglas que podría desincronizarse.
assert.strictEqual(P.tonoDePlugin('github', 'Github'), 'tone-green');
assert.strictEqual(P.tonoDePlugin('expo', 'Expo'), 'tone-teal');
assert.strictEqual(P.tonoDePlugin('static-analysis', ''), 'tone-rose');
assert.strictEqual(P.tonoDePlugin('algo-rarisimo@nadie', ''), 'tone-violet');
for (const [, [nombre, glifo]] of Object.entries(REALES)) {
  assert.strictEqual(P.tonoDePlugin('x', nombre), P.tonoDeGlifo(glifo), `${nombre}: tono no deriva del glifo`);
}

console.log('plugin-icons.test.js OK');
