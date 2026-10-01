'use strict';
const assert = require('node:assert');
const I = require('../cli-instalar.js');

const falta = (extra = {}) => ({ id: 'claude', nombre: 'Claude Code', instalado: false, instalable: true,
                                 comando: 'npm install -g @anthropic-ai/claude-code', con_node: false, url: null, ...extra });

// ── planInstalacion: qué hace el botón ──
// con comando + proyecto → terminal a la vista
assert.deepStrictEqual(I.planInstalacion(falta(), { hayProyecto: true }),
  { accion: 'terminal', comando: 'npm install -g @anthropic-ai/claude-code' });
// con comando pero SIN proyecto → segundo plano (si se puede por npm)
assert.deepStrictEqual(I.planInstalacion(falta(), { hayProyecto: false }),
  { accion: 'silenciosa', comando: 'npm install -g @anthropic-ai/claude-code' });
// Cursor (curl, no npm) sin proyecto: no hay forma de hacerlo → se explica, no hay botón muerto
const cursor = falta({ id: 'cursor', instalable: false, comando: 'curl https://cursor.com/install -fsS | bash' });
assert.deepStrictEqual(I.planInstalacion(cursor, { hayProyecto: false }), { accion: 'ninguna', motivo: 'sin_proyecto' });
assert.strictEqual(I.planInstalacion(cursor, { hayProyecto: true }).accion, 'terminal');
// Antigravity: sin comando → su sitio oficial
const agy = falta({ id: 'antigravity', instalable: false, comando: null, url: 'https://antigravity.google' });
assert.deepStrictEqual(I.planInstalacion(agy, { hayProyecto: true }), { accion: 'sitio', url: 'https://antigravity.google' });
assert.deepStrictEqual(I.planInstalacion(agy, { hayProyecto: false }), { accion: 'sitio', url: 'https://antigravity.google' });
// ya instalado / en curso / sin dato / sin vía
assert.strictEqual(I.planInstalacion({ ...falta(), instalado: true }, { hayProyecto: true }).accion, 'ninguna');
assert.deepStrictEqual(I.planInstalacion(falta(), { hayProyecto: true, enCurso: true }), { accion: 'ninguna', motivo: 'en_curso' });
assert.strictEqual(I.planInstalacion(null, {}).accion, 'ninguna');
assert.deepStrictEqual(I.planInstalacion(falta({ comando: null, url: null }), { hayProyecto: true }), { accion: 'ninguna', motivo: 'sin_via' });

// ── estadoFila ──
let e = I.estadoFila(falta({ con_node: true }), { hayProyecto: true });
assert.strictEqual(e.falta, true); assert.strictEqual(e.accionable, true); assert.strictEqual(e.conNode, true);
assert.strictEqual(e.comando, 'npm install -g @anthropic-ai/claude-code');
e = I.estadoFila(falta(), { hayProyecto: true, enCurso: true });
assert.strictEqual(e.instalando, true); assert.strictEqual(e.accionable, false, 'en curso: el botón solo muestra el estado');
e = I.estadoFila({ ...falta(), instalado: true }, { hayProyecto: true });
assert.deepStrictEqual([e.falta, e.instalando], [false, false]);
assert.strictEqual(I.estadoFila(undefined, {}).falta, false, 'sin dato: no se pinta nada de "falta"');
assert.strictEqual(I.estadoFila(agy, { hayProyecto: false }).accionable, true, 'el sitio oficial siempre se puede abrir');
assert.strictEqual(I.estadoFila(cursor, { hayProyecto: false }).accionable, false);

// ── nombre de la terminal ──
assert.strictEqual(I.nombreTerminal(falta()), 'Instalar Claude Code');
assert.strictEqual(I.nombreTerminal(null), 'Instalar');

// ── recienInstalados: avisa solo lo que PASÓ de faltar a estar ──
const antes = [{ id: 'claude', instalado: false }, { id: 'codex', instalado: false }, { id: 'qwen', instalado: true }];
const ahora = [{ id: 'claude', instalado: true }, { id: 'codex', instalado: false }, { id: 'qwen', instalado: true }];
assert.deepStrictEqual(I.recienInstalados(antes, ahora, new Set(['claude', 'codex'])), ['claude']);
assert.deepStrictEqual(I.recienInstalados(antes, ahora, new Set(['qwen'])), [], 'ya estaba: no se felicita de nuevo');
assert.deepStrictEqual(I.recienInstalados(antes, ahora, new Set()), []);
assert.deepStrictEqual(I.recienInstalados([], ahora, new Set(['claude'])), ['claude'], 'sin estado previo cuenta como cambio');
assert.deepStrictEqual(I.recienInstalados(antes, [], new Set(['claude'])), []);

// ── polling: rápido al principio, después más espaciado ──
assert.strictEqual(I.esperaPoll(0), 3000);
assert.strictEqual(I.esperaPoll(11), 3000);
assert.strictEqual(I.esperaPoll(12), 6000);
assert.ok(I.POLL_MAX_MS >= 5 * 60 * 1000, 'una instalación con Node incluido tarda minutos');

console.log('cli-instalar: ok');
