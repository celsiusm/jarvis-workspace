'use strict';
// Tests del sonido "un agente terminó / necesita respuesta": tonos disponibles,
// volumen y preferencias. El motor WebAudio vive en workspace.js; acá solo la
// decisión pura de QUÉ tocar y a qué ganancia.
const assert = require('node:assert');
const S = require('../sonido-fin.js');

// ─── el default es EXACTO el chime histórico (quien no toca nada oye lo mismo) ───
const def = S.plan('TASK_DONE', { perfil: S.PERFIL_DEFAULT, vol: S.VOL_DEFAULT });
assert.strictEqual(def.tipo, 'sine');
assert.deepStrictEqual(def.notas.map(n => n.freq), [523.25, 659.25, 783.99]);   // C5 E5 G5
assert.ok(Math.abs(def.gain - 0.16) < 1e-9, 'ganancia histórica 0.16');
const atencion = S.plan('TASK_BLOCKED', { perfil: 'campana', vol: S.VOL_DEFAULT });
assert.strictEqual(atencion.tipo, 'triangle');
assert.deepStrictEqual(atencion.notas.map(n => n.freq), [392.00, 311.13]);
assert.ok(Math.abs(atencion.gain - 0.18) < 1e-9, 'atención histórica 0.18');
// el aviso de atención NO cambia con el tono elegido (es otra señal)
for (const id of S.ORDEN) {
  assert.deepStrictEqual(S.plan('TASK_ERROR', { perfil: id, vol: 40 }).notas, atencion.notas);
}

// ─── cada tono existe, tiene notas válidas y su suma de pesos no pasa de 1 ──────
assert.deepStrictEqual(S.ORDEN, Object.keys(S.PERFILES));
assert.ok(S.ORDEN.length >= 4, 'hay varios tonos para elegir');
for (const id of S.ORDEN) {
  const p = S.PERFILES[id];
  assert.ok(p.label && p.notas.length > 0, id);
  assert.ok(['sine', 'triangle', 'square', 'sawtooth'].includes(p.tipo), id);
  for (const n of p.notas) {
    assert.ok(n.freq > 100 && n.freq < 5000, `${id}: freq audible`);
    assert.ok(n.start >= 0 && n.dur > 0.05, `${id}: tiempos`);
    assert.ok((n.g ?? 1) > 0 && (n.g ?? 1) <= 1, `${id}: peso 0-1`);
  }
  // Parciales SIMULTÁNEOS (mismo start): la suma de pesos no puede pasar de 1 o clippea.
  const porInicio = {};
  for (const n of p.notas) porInicio[n.start] = (porInicio[n.start] || 0) + (n.g ?? 1);
  for (const [t, suma] of Object.entries(porInicio)) assert.ok(suma <= 1.0001, `${id}@${t}: suma ${suma}`);
  // plan() de un tono devuelve ese tono
  assert.deepStrictEqual(S.plan('TASK_DONE', { perfil: id, vol: 40 }).notas, p.notas);
}

// ─── volumen: clamp, redondeo y defaults ─────────────────────────────────────────
assert.strictEqual(S.volValido(50), 50);
assert.strictEqual(S.volValido('75'), 75);          // el storage devuelve strings
assert.strictEqual(S.volValido(0), S.VOL_MIN);      // nunca 0: el aviso apagado es el switch
assert.strictEqual(S.volValido(-20), S.VOL_MIN);
assert.strictEqual(S.volValido(999), S.VOL_MAX);
assert.strictEqual(S.volValido('abc'), S.VOL_DEFAULT);
assert.strictEqual(S.volValido(undefined), S.VOL_DEFAULT);
assert.strictEqual(S.volValido(42.6), 43);
assert.ok(S.gananciaDe(100) <= 0.4 + 1e-9, 'techo de ganancia: sin clip');
assert.ok(S.gananciaDe(5) > 0, 'jamás ganancia 0 (exponentialRamp no admite 0)');
assert.ok(S.gananciaDe(80) > S.gananciaDe(40), 'más volumen → más ganancia');

// ─── tono inválido → default; evento sin sonido → null ───────────────────────────
assert.strictEqual(S.perfilValido('campana'), 'campana');
assert.strictEqual(S.perfilValido('inexistente'), S.PERFIL_DEFAULT);
assert.strictEqual(S.perfilValido(null), S.PERFIL_DEFAULT);
assert.strictEqual(S.plan('TASK_WHATEVER', { perfil: 'ding', vol: 40 }), null);
assert.strictEqual(S.plan('TASK_DONE').tipo, 'sine');   // sin prefs → defaults, no lanza

// ─── preferencias: leer / guardar contra un storage ──────────────────────────────
const mem = () => { const d = {}; return { getItem: k => (k in d ? d[k] : null), setItem: (k, v) => { d[k] = String(v); }, d }; };
const st = mem();
assert.deepStrictEqual(S.leerPrefs(st), { perfil: S.PERFIL_DEFAULT, vol: S.VOL_DEFAULT });
S.guardarPrefs(st, { perfil: 'digital', vol: 70 });
assert.deepStrictEqual(S.leerPrefs(st), { perfil: 'digital', vol: 70 });
S.guardarPrefs(st, { vol: 20 });                       // parcial: no pisa el tono
assert.deepStrictEqual(S.leerPrefs(st), { perfil: 'digital', vol: 20 });
S.guardarPrefs(st, { perfil: 'basura' });              // inválido: se guarda el default
assert.strictEqual(S.leerPrefs(st).perfil, S.PERFIL_DEFAULT);
// valores corruptos en el storage no rompen
st.setItem('jarvis.sonidoTareas.vol', 'NaN%');
assert.strictEqual(S.leerPrefs(st).vol, S.VOL_DEFAULT);
// storage bloqueado (getItem/setItem tiran): defaults, nunca lanza
const roto = { getItem() { throw new Error('bloqueado'); }, setItem() { throw new Error('bloqueado'); } };
assert.deepStrictEqual(S.leerPrefs(roto), { perfil: S.PERFIL_DEFAULT, vol: S.VOL_DEFAULT });
S.guardarPrefs(roto, { perfil: 'ding', vol: 60 });     // no lanza

console.log('OK sonido-fin');
