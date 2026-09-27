// JARVIS — Ruta de proyecto legible en poco espacio (home + breadcrumb).
// Abrevia el home a "~" y, si la ruta es larga, conserva la raíz y los dos
// últimos tramos (lo que identifica la carpeta) y cambia el medio por "…".
// Cortar por el final (text-overflow) dejaba a la vista solo el prefijo.
(function (global) {
  'use strict';

  const MAX_TRAMOS = 3;   // tramos después de la raíz antes de colapsar el medio

  function rutaCorta(ruta) {
    let r = String(ruta || '').trim().replace(/\/+$/, '');
    if (!r) return '';
    r = r.replace(/^\/home\/[^/]+(?=\/|$)/, '~').replace(/^\/root(?=\/|$)/, '~');
    const tilde = r === '~' || r.startsWith('~/');
    const tramos = r.split('/').filter(Boolean);
    const cabeza = tilde ? '~' : '/' + (tramos[0] || '');
    const resto = tramos.slice(1);
    if (resto.length <= MAX_TRAMOS) return tilde ? ['~', ...resto].join('/') : r;
    return [cabeza, '…', ...resto.slice(-2)].join('/');
  }

  const pure = { rutaCorta, MAX_TRAMOS };
  global.JarvisRuta = pure;
  if (typeof module !== 'undefined' && module.exports) module.exports = pure;
})(typeof window !== 'undefined' ? window : globalThis);
