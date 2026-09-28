'use strict';
// ─── Memoria · Atlas: simulación de fuerzas del Grafo (lógica pura) ─────────
// SIN DOM (patrón UMD _pure, como live-state.js). memory.js la anima con
// requestAnimationFrame y la APAGA cuando se asienta (alpha < ALPHA_MIN), al
// cambiar de pestaña o al cerrar: en reposo cuesta cero.
//
// Metáfora: cada categoría es una CONSTELACIÓN con su ancla en un anillo; los
// nodos se atraen suavemente a su ancla, los [[wikilinks]] son resortes y todo
// se repele. Resultado: racimos por tema unidos por puentes entre temas.

(function (root) {

  const ALPHA_MIN = 0.004;
  const DECAY = 0.976;          // ~220 pasos de 1 → ALPHA_MIN
  const REPOSO = 96;            // largo de reposo de un wikilink
  const REPULSION = 5200;
  const TIRON_ANCLA = 0.05;
  const RESORTE_MISMA = 0.05;   // wikilink dentro de la constelación
  const RESORTE_PUENTE = 0.012; // wikilink entre constelaciones: puente largo, no arrastra
  const FRICCION = 0.58;

  // Hash determinista (FNV-1a) → [0,1): mismas memorias = mismo layout.
  function _h(s) {
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return ((h >>> 0) % 100000) / 100000;
  }

  // Anclas de las categorías en un anillo (una sola = el centro).
  function anclas(cats) {
    const out = {};
    const n = cats.length;
    if (n === 1) { out[cats[0]] = { x: 0, y: 0 }; return out; }
    const R = 120 + 42 * n;
    cats.forEach((c, i) => {
      const a = i / n * Math.PI * 2 - Math.PI / 2;
      out[c] = { x: Math.cos(a) * R, y: Math.sin(a) * R * 0.78 };
    });
    return out;
  }

  // nodos: [{id, cat, grado}] · aristas: [{a, b}] (ids) · previas: {id: {x,y}}
  function crear(nodos, aristas, previas) {
    previas = previas || {};
    const cats = [...new Set(nodos.map(n => n.cat))];
    const anc = anclas(cats);
    let conocidos = 0;
    const ns = nodos.map(n => {
      const p = previas[n.id];
      if (p) conocidos++;
      const a = anc[n.cat];
      const ang = _h(n.id) * Math.PI * 2, rad = 30 + _h(n.id + '#') * 60;
      return {
        id: n.id, cat: n.cat, grado: n.grado || 0,
        r: 6 + Math.min((n.grado || 0) * 2.2, 12),
        x: p ? p.x : a.x + Math.cos(ang) * rad,
        y: p ? p.y : a.y + Math.sin(ang) * rad,
        vx: 0, vy: 0, fijo: false,
      };
    });
    const idx = Object.fromEntries(ns.map((n, i) => [n.id, i]));
    const ar = [];
    for (const e of aristas) {
      const i = idx[e.a], j = idx[e.b];
      if (i === undefined || j === undefined || i === j) continue;
      ar.push([i, j]);
    }
    // Si casi todo ya tenía posición (re-render por filtro), arranca tibio:
    // solo lo nuevo se acomoda, el resto no se re-baraja.
    const alpha = ns.length && conocidos / ns.length > 0.8 ? 0.25 : 1;
    return { nodos: ns, aristas: ar, anclas: anc, alpha: alpha };
  }

  // Un paso de integración. Devuelve el alpha nuevo.
  function paso(sim) {
    const ns = sim.nodos, a = sim.alpha;
    const n = ns.length;
    for (let i = 0; i < n; i++) {
      const p = ns[i];
      for (let j = i + 1; j < n; j++) {
        const q = ns[j];
        let dx = p.x - q.x, dy = p.y - q.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = (_h(p.id + q.id) - 0.5); dy = 0.5; d2 = 1; }
        const d = Math.sqrt(d2);
        const f = REPULSION / d2 * a;
        const fx = dx / d * f, fy = dy / d * f;
        p.vx += fx; p.vy += fy; q.vx -= fx; q.vy -= fy;
        // colisión dura: nunca se pisan
        const min = p.r + q.r + 14;
        if (d < min) {
          const push = (min - d) / d * 0.5;
          p.vx += dx * push; p.vy += dy * push; q.vx -= dx * push; q.vy -= dy * push;
        }
      }
      const anc = sim.anclas[p.cat] || { x: 0, y: 0 };
      p.vx += (anc.x - p.x) * TIRON_ANCLA * a;
      p.vy += (anc.y - p.y) * TIRON_ANCLA * a;
    }
    for (const [i, j] of sim.aristas) {
      const p = ns[i], q = ns[j];
      const dx = q.x - p.x, dy = q.y - p.y;
      const d = Math.max(Math.hypot(dx, dy), 1);
      const f = (d - REPOSO) * (p.cat === q.cat ? RESORTE_MISMA : RESORTE_PUENTE) * a;
      const fx = dx / d * f, fy = dy / d * f;
      p.vx += fx; p.vy += fy; q.vx -= fx; q.vy -= fy;
    }
    for (const p of ns) {
      if (p.fijo) { p.vx = 0; p.vy = 0; continue; }
      p.x += p.vx * 0.5; p.y += p.vy * 0.5;
      p.vx *= FRICCION; p.vy *= FRICCION;
    }
    sim.alpha = a * DECAY;
    return sim.alpha;
  }

  const asentada = (sim) => sim.alpha < ALPHA_MIN;

  // Corre hasta asentarse (reduced-motion y tests): sin animación.
  function asentar(sim, max) {
    let i = 0;
    while (!asentada(sim) && i++ < (max || 600)) paso(sim);
    return sim;
  }

  // Recalienta (arrastre de un nodo, "reordenar").
  function recalentar(sim, a) { sim.alpha = Math.max(sim.alpha, a || 0.35); return sim; }

  // Caja que envuelve a todos los nodos (para encuadrar). Con conNebulosas,
  // también las constelaciones y el rótulo que va arriba de cada una.
  function limites(sim, pad, conNebulosas) {
    pad = pad == null ? 40 : pad;
    if (!sim.nodos.length) return { x: -100, y: -100, w: 200, h: 200 };
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of sim.nodos) {
      x0 = Math.min(x0, p.x - p.r); y0 = Math.min(y0, p.y - p.r);
      x1 = Math.max(x1, p.x + p.r); y1 = Math.max(y1, p.y + p.r + 16);
    }
    if (conNebulosas) {
      for (const c of constelaciones(sim)) {
        y0 = Math.min(y0, c.y - c.r - 18);
        x0 = Math.min(x0, c.x - c.r * 0.6); x1 = Math.max(x1, c.x + c.r * 0.6);
      }
    }
    return { x: x0 - pad, y: y0 - pad, w: (x1 - x0) + pad * 2, h: (y1 - y0) + pad * 2 };
  }

  // Centroide + radio de cada constelación (para la nebulosa y su rótulo).
  function constelaciones(sim) {
    const acc = {};
    for (const p of sim.nodos) {
      const c = acc[p.cat] || (acc[p.cat] = { cat: p.cat, x: 0, y: 0, n: 0, nodos: [] });
      c.x += p.x; c.y += p.y; c.n++; c.nodos.push(p);
    }
    return Object.values(acc).map(c => {
      const cx = c.x / c.n, cy = c.y / c.n;
      let r = 0;
      for (const p of c.nodos) r = Math.max(r, Math.hypot(p.x - cx, p.y - cy) + p.r);
      return { cat: c.cat, x: cx, y: cy, r: Math.max(46, r + 26), n: c.n };
    });
  }

  // Encuadre: transform {k, x, y} para que la caja entre en (W×H).
  function encuadre(caja, W, H, kMin, kMax) {
    const k = Math.max(kMin || 0.25, Math.min(kMax || 1.8, Math.min(W / caja.w, H / caja.h)));
    return { k: k, x: W / 2 - (caja.x + caja.w / 2) * k, y: H / 2 - (caja.y + caja.h / 2) * k };
  }

  const api = { ALPHA_MIN, anclas, crear, paso, asentada, asentar, recalentar, limites, constelaciones, encuadre };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.JarvisMemoryGraph = api;

})(typeof window !== 'undefined' ? window : globalThis);
