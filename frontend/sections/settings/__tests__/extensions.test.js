// Tests de la lógica pura de ⚙ → Extensiones (sections/settings/extensions.js).
// Correr: node frontend/sections/settings/__tests__/extensions.test.js
'use strict';
const assert = require('assert');
const path = require('path');
const X = require(path.join(__dirname, '..', 'extensions.js'));

let n = 0;
const t = (nombre, fn) => { fn(); n++; };

const CAT = [
  { id: 'claude', label: 'Claude Code', lee: ['.claude/skills/'] },
  { id: 'codex', label: 'Codex', lee: ['AGENTS.md'] },
  { id: 'gemini', label: 'Gemini CLI', lee: ['GEMINI.md'] },
  { id: 'cursor', label: 'Cursor', lee: ['.cursor/rules/'] },
];
const ITEMS = [
  { ia: 'claude', ia_label: 'Claude Code', kind: 'skill', nombre: 'deploy', descripcion: 'Cómo desplegar a producción', path: '.claude/skills/deploy/SKILL.md', alcance: 'proyecto', detalle: '' },
  { ia: 'claude', ia_label: 'Claude Code', kind: 'skill', nombre: 'global', descripcion: 'x', path: '~/.claude/skills/global/SKILL.md', alcance: 'usuario', detalle: '' },
  { ia: 'codex', ia_label: 'Codex', kind: 'instructions', nombre: 'AGENTS.md', descripcion: 'Guía', path: 'AGENTS.md', alcance: 'proyecto', detalle: '' },
  { ia: 'cursor', ia_label: 'Cursor', kind: 'rules', nombre: 'react', descripcion: 'Convenciones React', path: '.cursor/rules/react.mdc', alcance: 'proyecto', detalle: 'src/**/*.tsx' },
];

t('coincide: sin tildes, sin mayúsculas, todos los términos', () => {
  assert.ok(X.coincide(['Configuración del Proyecto'], 'configuracion'));
  assert.ok(X.coincide(['deploy a prod'], 'PROD deploy'));
  assert.ok(!X.coincide(['deploy'], 'deploy test'));
  assert.ok(X.coincide(['lo que sea'], '   '));
});

t('filtrarItems por IA y por texto (nombre, descripción, ruta, tipo, detalle)', () => {
  assert.strictEqual(X.filtrarItems(ITEMS).length, 4);
  assert.deepStrictEqual(X.filtrarItems(ITEMS, { ia: 'claude' }).map(i => i.nombre), ['deploy', 'global']);
  assert.deepStrictEqual(X.filtrarItems(ITEMS, { q: 'produccion' }).map(i => i.nombre), ['deploy']);
  assert.deepStrictEqual(X.filtrarItems(ITEMS, { q: 'tsx' }).map(i => i.nombre), ['react']);
  assert.deepStrictEqual(X.filtrarItems(ITEMS, { q: 'reglas' }).map(i => i.nombre), ['react']);
  assert.deepStrictEqual(X.filtrarItems(ITEMS, { ia: 'codex', q: 'react' }), []);
  assert.deepStrictEqual(X.filtrarItems(null), []);
});

t('agrupar respeta el orden del catálogo y no pierde IAs desconocidas', () => {
  const extra = ITEMS.concat([{ ia: 'zeta', ia_label: 'Zeta', kind: 'rules', nombre: 'z', path: 'z' }]);
  const g = X.agrupar(extra, CAT);
  assert.deepStrictEqual(g.map(x => x.herr.id), ['claude', 'codex', 'cursor', 'zeta']);
  assert.strictEqual(g[0].items.length, 2);
  assert.strictEqual(g[3].herr.label, 'Zeta');
});

t('sinArchivos: las IAs del catálogo que no aparecen', () => {
  const faltan = X.sinArchivos(CAT, [{ id: 'claude' }, { id: 'codex' }]);
  assert.deepStrictEqual(faltan.map(h => h.id), ['gemini', 'cursor']);
});

const MK = [
  { full_id: 'a@m', nombre: 'Alpha', descripcion: 'lint', marketplace: 'm', source: 'official', instalado: true },
  { full_id: 'b@m', nombre: 'Beta', descripcion: 'docs', marketplace: 'm', source: 'external', instalado: false },
  { full_id: 'c@m', nombre: 'Gamma', descripcion: 'tests', marketplace: 'm', source: 'official', instalado: false },
];

t('filtrarMarket por origen y texto', () => {
  assert.strictEqual(X.filtrarMarket(MK).length, 3);
  assert.deepStrictEqual(X.filtrarMarket(MK, { origen: 'oficial' }).map(p => p.nombre), ['Alpha', 'Gamma']);
  assert.deepStrictEqual(X.filtrarMarket(MK, { origen: 'externo' }).map(p => p.nombre), ['Beta']);
  assert.deepStrictEqual(X.filtrarMarket(MK, { origen: 'instalados' }).map(p => p.nombre), ['Alpha']);
  assert.deepStrictEqual(X.filtrarMarket(MK, { q: 'test' }).map(p => p.nombre), ['Gamma']);
});

t('contarMarket', () => {
  assert.deepStrictEqual(X.contarMarket(MK), { todos: 3, oficial: 2, externo: 1, instalados: 1 });
  assert.deepStrictEqual(X.contarMarket([]), { todos: 0, oficial: 0, externo: 0, instalados: 0 });
});

t('etiquetaCuenta: singular/plural para el encabezado de cada IA', () => {
  assert.strictEqual(X.etiquetaCuenta('rules', 1), 'regla');
  assert.strictEqual(X.etiquetaCuenta('rules', 3), 'reglas');
  assert.strictEqual(X.etiquetaCuenta('command', 2), 'comandos');
  assert.strictEqual(X.etiquetaCuenta('raro', 2), 'raro');
});

t('cmdInstalar arma el comando de Claude Code', () => {
  assert.strictEqual(X.cmdInstalar({ full_id: 'superpowers@claude-plugins-official' }),
    '/plugin install superpowers@claude-plugins-official');
});

t('nombreSkillValido = mismo contrato que el backend', () => {
  assert.ok(X.nombreSkillValido('deploy-a_prod2'));
  assert.ok(!X.nombreSkillValido(''));
  assert.ok(!X.nombreSkillValido('con espacio'));
  assert.ok(!X.nombreSkillValido('../x'));
  assert.ok(!X.nombreSkillValido('ñandú'));
});

t('plantillaSkill + sincronizarNombre siguen al nombre mientras no se toque', () => {
  let c = X.plantillaSkill('');
  assert.ok(c.startsWith('---\nname: \ndescription: \n---'));
  c = X.sincronizarNombre(c, '', 'dep');
  assert.ok(/^name: dep$/m.test(c) && /^# dep$/m.test(c));
  c = X.sincronizarNombre(c, 'dep', 'deploy');
  assert.ok(/^name: deploy$/m.test(c) && /^# deploy$/m.test(c));
  // si el usuario editó el name a mano, no se pisa
  const manual = c.replace('name: deploy', 'name: otra-cosa');
  assert.strictEqual(X.sincronizarNombre(manual, 'deploy', 'deploy2'), manual);
});

t('nombreSkillDeItem: solo skills de Claude del proyecto son editables', () => {
  assert.strictEqual(X.nombreSkillDeItem(ITEMS[0]), 'deploy');
  assert.strictEqual(X.nombreSkillDeItem(ITEMS[1]), null);   // ~/ usuario
  assert.strictEqual(X.nombreSkillDeItem(ITEMS[2]), null);   // AGENTS.md
  assert.strictEqual(X.nombreSkillDeItem(null), null);
});

t('hueDePlugin cae en un token (sin plugin-icons cargado → acc)', () => {
  assert.strictEqual(X.hueDePlugin({ full_id: 'x@y', nombre: 'X' }), 'acc');
  const vals = new Set(Object.values(X.HUE_POR_TONO));
  for (const v of vals) assert.ok(['acc', 'info', 'run', 'work', 'err'].includes(v));
});

t('KINDS cubre todos los tipos del backend', () => {
  for (const k of ['skill', 'command', 'agent', 'rules', 'instructions', 'config']) {
    assert.ok(X.KINDS[k] && X.KINDS[k].label && X.KINDS[k].icon, k);
  }
});

console.log(`extensions.test.js: ${n} OK`);
