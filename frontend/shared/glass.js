// JARVIS — Interruptor del material Liquid Glass (⚙ → Apariencia).
// Pone html[data-glass="on|off"]; shared/liquid-glass.css se aplica solo con
// "on". Se carga en el <head> (junto a themes.js) para no parpadear al entrar.
(function (global) {
  'use strict';

  const KEY = 'jarvis.glass';
  const DEFAULT = 'on';

  function normalizar(v) {
    if (v === false) return 'off';
    if (v === true) return 'on';
    return String(v ?? '').trim().toLowerCase() === 'off' ? 'off' : DEFAULT;
  }

  function _ls(o) { return (o && o.ls) || (typeof localStorage !== 'undefined' ? localStorage : null); }
  function _doc(o) { return (o && o.doc) || (typeof document !== 'undefined' ? document : null); }

  function actual(o) {
    try { return normalizar(_ls(o)?.getItem(KEY)); } catch { return DEFAULT; }
  }

  function aplicar(v, o) {
    const val = normalizar(v);
    try { _doc(o)?.documentElement?.setAttribute('data-glass', val); } catch {}
    try { _ls(o)?.setItem(KEY, val); } catch {}
    return val;
  }

  const pure = { KEY, DEFAULT, normalizar, actual, aplicar };
  global.JarvisGlass = pure;
  if (typeof module !== 'undefined' && module.exports) { module.exports = pure; return; }
  // Boot: sin persistir (no escribir storage por solo cargar la página).
  try { document.documentElement.setAttribute('data-glass', actual()); } catch {}
})(typeof window !== 'undefined' ? window : globalThis);
