// JARVIS — Review: lógica pura de agrupado (testeada en Node).
(function (global) {
  'use strict';

  // Saca los artefactos (salida de herramientas, clasificada en el backend con
  // core/politica_commit) de cada grupo y los junta en un grupo propio al final.
  // Así no se mezclan con el trabajo real ni se commitean por reflejo.
  function separarArtefactos(grupos, claseDe) {
    const gen = [];
    const out = [];
    for (const g of grupos || []) {
      const reales = [];
      for (const f of g.archivos || []) {
        if (claseDe(f.path) === 'artefacto') gen.push(f); else reales.push(f);
      }
      if (reales.length) out.push(Object.assign({}, g, { archivos: reales }));
    }
    if (gen.length) out.push({ id: 'gen', nombre: '', artefacto: true, archivos: gen });
    return out;
  }

  // «+N −M» solo cuando git dio números; carpetas nuevas y binarios no tienen.
  function statTexto(mas, menos) {
    const num = v => (typeof v === 'number' || /^\d+$/.test(String(v ?? ''))) ? String(v) : null;
    const a = num(mas), b = num(menos);
    if (a === null || b === null) return null;
    return { mas: '+' + a, menos: '−' + b };
  }

  const pure = { separarArtefactos, statTexto };
  global.JarvisReviewGrupos = pure;
  if (typeof module !== 'undefined' && module.exports) module.exports = pure;
})(typeof window !== 'undefined' ? window : globalThis);
