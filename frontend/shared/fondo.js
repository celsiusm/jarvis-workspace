// JARVIS — Fondo personalizado del modo Glass (⚙ → Apariencia → Fondo).
//
// Una imagen (o un degradado de la casa) se pinta en UNA capa fija detrás de toda
// la app, ya desenfocada; franja, barra, dock y terminales pasan a ser vidrio
// translúcido encima (shared/fondo.css). Por qué el blur va en la capa del fondo y
// no como backdrop-filter en cada panel: el fondo es estático, así que el navegador
// lo desenfoca UNA vez y lo reutiliza; un backdrop-filter sobre una card con un
// canvas de xterm vivo se recalcula en cada frame (regla de perf del proyecto).
//
// Ajustes en localStorage (como el resto de Apariencia); la imagen propia vive en
// el servidor (data/fondo/, plotspace/routers/fondo.py). Solo se activa con
// Liquid Glass ON y respetando prefers-reduced-transparency.
//
// Este archivo carga en el <head> junto a glass.js: aplica data-fondo y las
// variables --gw-* de forma SÍNCRONA para no parpadear al entrar.
(function (global) {
  'use strict';

  const KEY = 'jarvis.fondo';
  const URL_IMAGEN = '/api/fondo/imagen';
  const LADO_MAX = 2560;          // px del lado mayor tras achicar la imagen subida

  // Rangos de cada control (los usa la UI y los tests).
  const RANGOS = {
    blur:  { min: 0,  max: 40,  def: 14, unidad: 'px' },   // desenfoque del fondo
    dim:   { min: 0,  max: 80,  def: 30, unidad: '%' },    // velo (oscurece en temas oscuros, aclara en claros)
    sat:   { min: 60, max: 180, def: 115, unidad: '%' },   // saturación del fondo
    term:  { min: 20, max: 100, def: 66, unidad: '%' },    // opacidad del fondo de las terminales
    panel: { min: 20, max: 100, def: 58, unidad: '%' },    // opacidad de franja / barra / dock
  };

  // Degradados de la casa: arte fijo (oklch literal a propósito: son ilustraciones, no
  // chrome). NO siguen al tema: con un fondo puesto, el tema solo pone los acentos.
  const PRESETS = [
    { id: 'aurora', nombre: 'Aurora', css: [
      'radial-gradient(62% 72% at 14% 18%, oklch(56% 0.21 292), transparent 70%)',
      'radial-gradient(56% 66% at 88% 14%, oklch(62% 0.15 238), transparent 70%)',
      'radial-gradient(72% 82% at 72% 92%, oklch(54% 0.21 340), transparent 72%)',
      'radial-gradient(48% 58% at 8% 88%, oklch(50% 0.16 262), transparent 72%)',
      'linear-gradient(160deg, oklch(22% 0.06 285), oklch(13% 0.04 275))'].join(',') },
    { id: 'dusk', nombre: 'Atardecer', css: [
      'radial-gradient(60% 70% at 16% 20%, oklch(52% 0.22 300), transparent 70%)',
      'radial-gradient(58% 66% at 86% 18%, oklch(66% 0.21 20), transparent 70%)',
      'radial-gradient(70% 80% at 62% 96%, oklch(40% 0.14 262), transparent 72%)',
      'linear-gradient(165deg, oklch(24% 0.07 285), oklch(15% 0.04 270))'].join(',') },
    { id: 'ocean', nombre: 'Océano', css: [
      'radial-gradient(64% 72% at 18% 16%, oklch(56% 0.14 215), transparent 70%)',
      'radial-gradient(56% 64% at 88% 24%, oklch(68% 0.12 188), transparent 70%)',
      'radial-gradient(76% 84% at 60% 98%, oklch(34% 0.11 252), transparent 72%)',
      'linear-gradient(165deg, oklch(22% 0.06 235), oklch(14% 0.04 245))'].join(',') },
    { id: 'ember', nombre: 'Brasa', css: [
      'radial-gradient(60% 70% at 14% 18%, oklch(64% 0.19 48), transparent 70%)',
      'radial-gradient(56% 66% at 90% 20%, oklch(52% 0.21 18), transparent 70%)',
      'radial-gradient(72% 80% at 55% 98%, oklch(38% 0.13 32), transparent 72%)',
      'linear-gradient(165deg, oklch(22% 0.05 28), oklch(13% 0.03 30))'].join(',') },
    { id: 'forest', nombre: 'Bosque', css: [
      'radial-gradient(62% 72% at 16% 18%, oklch(55% 0.13 152), transparent 70%)',
      'radial-gradient(54% 62% at 88% 22%, oklch(64% 0.12 118), transparent 70%)',
      'radial-gradient(74% 82% at 58% 98%, oklch(34% 0.09 178), transparent 72%)',
      'linear-gradient(165deg, oklch(21% 0.045 162), oklch(13% 0.03 165))'].join(',') },
  ];
  const IDS_PRESET = PRESETS.map(p => p.id);

  const DEFAULTS = Object.freeze({
    on: false, fuente: 'aurora', version: 0,
    blur: RANGOS.blur.def, dim: RANGOS.dim.def, sat: RANGOS.sat.def,
    term: RANGOS.term.def, panel: RANGOS.panel.def,
  });

  // ── Lógica PURA (sin DOM) ──────────────────────────────────────────────────

  function _num(v, r) {
    const n = Math.round(Number(v));
    if (!Number.isFinite(n)) return r.def;
    return Math.min(r.max, Math.max(r.min, n));
  }

  /** Ajustes válidos desde cualquier cosa (JSON viejo, a mano, roto). */
  function normalizar(o) {
    const src = (o && typeof o === 'object') ? o : {};
    const fuente = (src.fuente === 'custom' || IDS_PRESET.includes(src.fuente)) ? src.fuente : DEFAULTS.fuente;
    const version = Number.isFinite(Number(src.version)) && Number(src.version) > 0 ? Math.floor(Number(src.version)) : 0;
    return {
      on: src.on === true,
      fuente, version,
      blur: _num(src.blur ?? RANGOS.blur.def, RANGOS.blur),
      dim: _num(src.dim ?? RANGOS.dim.def, RANGOS.dim),
      sat: _num(src.sat ?? RANGOS.sat.def, RANGOS.sat),
      term: _num(src.term ?? RANGOS.term.def, RANGOS.term),
      panel: _num(src.panel ?? RANGOS.panel.def, RANGOS.panel),
    };
  }

  /** ¿Se pinta el fondo? Necesita: activado + Liquid Glass + (si es propia) imagen
   *  subida + que el sistema no pida reducir transparencias. */
  function activo(cfg, o = {}) {
    const c = normalizar(cfg);
    if (!c.on) return false;
    if (o.glass === false) return false;
    if (o.reducirTransparencia === true) return false;
    if (c.fuente === 'custom' && !(c.version > 0)) return false;
    return true;
  }

  /** Valor CSS de `background` de la capa de imagen. */
  function fondoCss(cfg) {
    const c = normalizar(cfg);
    if (c.fuente === 'custom') {
      return c.version > 0
        ? `url("${URL_IMAGEN}?v=${c.version}") center / cover no-repeat`
        : 'none';
    }
    return (PRESETS.find(p => p.id === c.fuente) || PRESETS[0]).css;
  }

  /** Variables CSS que fondo.css consume (todas bajo :root). */
  function variables(cfg) {
    const c = normalizar(cfg);
    return {
      '--gw-blur': `${c.blur}px`,
      '--gw-dim': `${c.dim}%`,
      '--gw-sat': `${c.sat}%`,
      '--gw-term': `${c.term}%`,
      '--gw-panel': `${c.panel}%`,
      '--gw-img': fondoCss(c),
    };
  }

  /** Medidas de la imagen achicada: el lado mayor nunca pasa de LADO_MAX. */
  function medidasAchicadas(ancho, alto, lado = LADO_MAX) {
    const w = Math.max(1, Math.round(Number(ancho) || 0));
    const h = Math.max(1, Math.round(Number(alto) || 0));
    const k = Math.min(1, lado / Math.max(w, h));
    return { w: Math.max(1, Math.round(w * k)), h: Math.max(1, Math.round(h * k)) };
  }

  // ── Estado + DOM (solo navegador) ──────────────────────────────────────────

  const _ls = () => { try { return typeof localStorage !== 'undefined' ? localStorage : null; } catch { return null; } };
  const _root = () => (typeof document !== 'undefined' ? document.documentElement : null);

  function cargar() {
    try { return normalizar(JSON.parse(_ls()?.getItem(KEY) || 'null')); } catch { return normalizar(null); }
  }
  function guardar(cfg) {
    try { _ls()?.setItem(KEY, JSON.stringify(normalizar(cfg))); } catch {}
  }

  const _reducirTransp = () => {
    try { return !!global.matchMedia?.('(prefers-reduced-transparency: reduce)').matches; } catch { return false; }
  };
  const _glassOn = () => (global.JarvisGlass?.actual?.() ?? 'on') === 'on';

  let _cfg = null;
  let _eraActivo = null;
  const actual = () => (_cfg || (_cfg = cargar()));
  const estaActivo = () => activo(actual(), { glass: _glassOn(), reducirTransparencia: _reducirTransp() });

  function _capa() {
    if (typeof document === 'undefined' || !document.body) return null;
    let el = document.getElementById('jw-fondo');
    if (!el) {
      el = document.createElement('div');
      el.id = 'jw-fondo';
      el.setAttribute('aria-hidden', 'true');
      el.innerHTML = '<div class="jw-fondo-img"></div><div class="jw-fondo-vel"></div>';
      document.body.insertBefore(el, document.body.firstChild);
    }
    return el;
  }

  /** Vuelca los ajustes al documento. Idempotente; no persiste. */
  function pintar() {
    const root = _root();
    if (!root) return;
    const on = estaActivo();
    const vars = variables(actual());
    for (const [k, v] of Object.entries(vars)) root.style.setProperty(k, v);
    root.setAttribute('data-fondo', on ? 'on' : 'off');
    if (on) _capa();
    // Solo cuando cambia SI hay fondo (las terminales recrean su renderer): mover un
    // slider solo cambia variables CSS y no debe repintar 12 terminales por tick.
    if (on !== _eraActivo) {
      _eraActivo = on;
      try { global.dispatchEvent(new CustomEvent('fondo-changed', { detail: { activo: on } })); } catch {}
    }
  }

  /** Cambia ajustes (parcial), persiste y repinta. */
  function set(parcial) {
    _cfg = normalizar({ ...actual(), ...(parcial || {}) });
    guardar(_cfg);
    pintar();
    return _cfg;
  }

  function restablecer() {
    // Conserva si está activo, la fuente y la imagen propia: solo vuelve los controles finos.
    const a = actual();
    return set({ ...DEFAULTS, on: a.on, fuente: a.fuente, version: a.version });
  }

  // ── Imagen propia ──────────────────────────────────────────────────────────

  /** Decodifica, achica a LADO_MAX y recodifica en webp (jpeg si no hay) → Blob. */
  async function procesarArchivo(file) {
    if (!file || !/^image\//.test(file.type || '')) throw new Error('El archivo no es una imagen');
    let bmp;
    try { bmp = await createImageBitmap(file); }
    catch { throw new Error('No se pudo leer la imagen'); }
    const { w, h } = medidasAchicadas(bmp.width, bmp.height);
    const cv = document.createElement('canvas');
    cv.width = w; cv.height = h;
    cv.getContext('2d').drawImage(bmp, 0, 0, w, h);
    try { bmp.close?.(); } catch {}
    const aBlob = (tipo, q) => new Promise(ok => cv.toBlob(ok, tipo, q));
    let blob = await aBlob('image/webp', 0.86);
    if (!blob || blob.type !== 'image/webp') blob = await aBlob('image/jpeg', 0.88);
    if (!blob) throw new Error('No se pudo preparar la imagen');
    return blob;
  }

  async function _json(res) { try { return await res.json(); } catch { return null; } }

  /** Sube la imagen elegida y la deja como fuente activa. Devuelve el estado. */
  async function subir(file) {
    const blob = await procesarArchivo(file);
    const res = await fetch(`${URL_IMAGEN}`, { method: 'PUT', headers: { 'Content-Type': blob.type }, body: blob });
    const d = await _json(res);
    if (!res.ok) throw new Error((d && d.detail) || 'No se pudo guardar la imagen');
    set({ fuente: 'custom', version: d.version, on: true });
    return d;
  }

  async function quitarImagen() {
    try { await fetch(URL_IMAGEN, { method: 'DELETE' }); } catch {}
    const a = actual();
    set({ version: 0, fuente: a.fuente === 'custom' ? 'aurora' : a.fuente });
  }

  /** Alinea el estado local con lo que el servidor realmente tiene. */
  async function reconciliar() {
    try {
      const res = await fetch('/api/fondo');
      if (!res.ok) return null;
      const d = await res.json();
      const a = actual();
      if (d.existe && d.version !== a.version) set({ version: d.version });
      else if (!d.existe && (a.version > 0 || a.fuente === 'custom')) set({ version: 0, fuente: a.fuente === 'custom' ? 'aurora' : a.fuente });
      return d;
    } catch { return null; }
  }

  const pure = {
    KEY, URL_IMAGEN, LADO_MAX, RANGOS, PRESETS, IDS_PRESET, DEFAULTS,
    normalizar, activo, fondoCss, variables, medidasAchicadas,
  };
  const api = {
    ...pure, cargar, guardar, actual, estaActivo, pintar, set, restablecer,
    procesarArchivo, subir, quitarImagen, reconciliar,
  };
  global.JarvisFondo = api;
  if (typeof module !== 'undefined' && module.exports) { module.exports = pure; return; }

  // Boot síncrono (sin persistir): atributo + variables antes del primer pintado.
  try { pintar(); } catch {}
  try {
    document.addEventListener('DOMContentLoaded', () => {
      if (estaActivo()) _capa();
      if (actual().fuente === 'custom') reconciliar();
    });
    // Otra pestaña cambió los ajustes / Liquid Glass: seguirla.
    global.addEventListener('glass-changed', pintar);
    global.addEventListener('storage', (e) => {
      if (e.key === KEY) { _cfg = null; pintar(); }
      else if (e.key === 'jarvis.glass') pintar();
    });
    global.matchMedia?.('(prefers-reduced-transparency: reduce)').addEventListener?.('change', pintar);
  } catch {}
})(typeof window !== 'undefined' ? window : globalThis);
