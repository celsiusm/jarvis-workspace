'use strict';
// ─── JARVIS — Browser (panel del dock) ───────────────────────────────────────
// Motor: Chromium server-side (`plotspace/core/remote_browser.py`) por
// WebSocket `/ws/browser/{sid}`. Acá vive el chrome estilo "Nave" (tabs +
// omnibar con sugerencias + switch de layouts + start page) y el reenvío de
// input. Navega CUALQUIER sitio (X, YouTube, Google…) porque el framing lo hace
// el server, no el browser.
//
// Expone `window.WebPreview` con la MISMA superficie que consumían el shell y
// los vecinos (init, setUrl, getUrl, openTab, abrirLink, detectar, refresh,
// refrescarSiExiste, openExternal, onProjectChanged, _pure).
//
// i18n: el DOM va en español canónico y en `_montar` se registran las
// traducciones ES→EN con JarvisI18n.agregar (patrón del repo).

(function (root) {

  // ── Lógica pura ─────────────────────────────────────────────────
  function _normalizarBase(input) {
    if (input == null) return null;
    const s = String(input).trim();
    if (!s) return null;
    if (s.startsWith('/')) return null;
    const m = /^([a-z][a-z0-9+.-]*):(\/\/)?(.*)$/i.exec(s);
    if (m && (m[2] || !/^\d+(\/|$)/.test(m[3]))) {
      const esq = m[1].toLowerCase();
      if ((esq === 'http' || esq === 'https') && m[2]) return s;
      return null;
    }
    if (/^\d+$/.test(s)) return `http://localhost:${s}`;
    return `http://${s}`;
  }
  function _canonLoopback(url) {
    return url
      .replace(/^(\w+:\/\/)127\.0\.0\.1(?=[:/]|$)/i, '$1localhost')
      .replace(/^(\w+:\/\/)0\.0\.0\.0(?=[:/]|$)/i, '$1localhost')
      .replace(/^(\w+:\/\/)\[::1\](?=[:/]|$)/i, '$1localhost');
  }
  function normalizarUrl(input) {
    const norm = _normalizarBase(input);
    return norm == null ? null : _canonLoopback(norm);
  }
  function _pareceUrl(s) {
    if (/^https?:\/\//i.test(s)) return true;
    return /localhost|^[\w-]+(\.[\w-]+)+([:/]|$)|:\d+/.test(s);
  }
  function interpretarEntrada(valor) {
    const s = String(valor == null ? '' : valor).trim();
    if (!s) return { tipo: 'vacia' };
    if (/^yt\s+/i.test(s)) return { tipo: 'youtube', q: s.replace(/^yt\s+/i, '').trim() };
    if (_pareceUrl(s)) {
      const url = normalizarUrl(s);
      return url ? { tipo: 'url', url } : { tipo: 'invalida' };
    }
    return { tipo: 'busqueda', q: s };
  }
  function urlBusqueda(tipo, q) {
    const t = encodeURIComponent(q || '');
    if (tipo === 'youtube') return `https://www.youtube.com/results?search_query=${t}`;
    return `https://www.google.com/search?q=${t}`;
  }
  function linkAlPreview(uri, jarvisOrigin) {
    let u;
    try { u = new URL(String(uri)); } catch { return null; }
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
    const host = u.hostname.toLowerCase();
    if (!['localhost', '127.0.0.1', '0.0.0.0', '::1', '[::1]'].includes(host)) return null;
    u.hostname = 'localhost';
    const puerto = u.port || (u.protocol === 'https:' ? '443' : '80');
    try {
      const j = new URL(jarvisOrigin);
      const puertoJarvis = j.port || (j.protocol === 'https:' ? '443' : '80');
      if (puerto === puertoJarvis && !u.pathname.startsWith('/static/')) return null;
    } catch { /* origin raro */ }
    return u.toString();
  }
  function faviconSrc(url) {
    try { return `https://icons.duckduckgo.com/ip3/${new URL(url).host}.ico`; }
    catch { return null; }
  }
  // ── Paneles divididos (puro) ─────────────────────────────────────
  // El server no baja de 240×160 por sesión: una celda menor se vería estirada. Por
  // eso la disposición se ELIGE según el espacio real: 2 paneles van lado a lado si
  // entran, y si no, uno sobre otro; 3 = tres columnas, o uno grande + dos; 4 = 2×2.
  // Si ni así entran, ese layout se deshabilita en vez de mostrarse deformado.
  const CELDA_MIN = { w: 260, h: 200 };   // celda entera (cabecera de 26px incluida)
  const _cabe = (cols, rows, w, h, min) => (w / cols) >= min.w && (h / rows) >= min.h;
  function disposicion(n, w, h, min) {
    min = min || CELDA_MIN;
    if (!(n >= 1 && n <= 4)) return null;
    if (n === 1) return { cols: 1, rows: 1, grande: false };
    if (!(w > 0 && h > 0)) {                       // sin medida (pestaña oculta): la clásica
      return n === 2 ? { cols: 2, rows: 1, grande: false }
        : n === 3 ? { cols: 2, rows: 2, grande: true } : { cols: 2, rows: 2, grande: false };
    }
    const opciones = n === 2
      ? [[2, 1, false], [1, 2, false]]
      : n === 3
        ? [[3, 1, false], [2, 2, true], [1, 3, false]]
        : [[2, 2, false], [4, 1, false], [1, 4, false]];
    for (const [cols, rows, grande] of opciones) {
      if (_cabe(cols, rows, w, h, min)) return { cols, rows, grande };
    }
    return null;
  }
  /** El layout pedido, o el mayor menor que entra (siempre ≥ 1). */
  function mayorQueEntra(pedido, w, h, min) {
    for (let n = Math.min(4, Math.max(1, pedido | 0)); n > 1; n--) if (disposicion(n, w, h, min)) return n;
    return 1;
  }
  /** Qué pestaña ocupa cada panel. Conserva lo que ya estaba, completa con las
   *  pestañas libres y garantiza que la ACTIVA esté a la vista. */
  function asignarSlots(slots, ids, n, activa) {
    const vivos = new Set(ids);
    const out = [];
    for (const id of (slots || [])) if (vivos.has(id) && !out.includes(id) && out.length < n) out.push(id);
    for (const id of ids) if (out.length < n && !out.includes(id)) out.push(id);
    if (activa != null && vivos.has(activa) && !out.includes(activa) && out.length) out[out.length - 1] = activa;
    return out;
  }
  /** Elegir una pestaña: si ya se ve, se enfoca; si no, ocupa el panel de la activa. */
  function elegirTab(slots, activa, id) {
    if (slots.includes(id)) return slots.slice();
    if (!slots.length) return [id];
    const i = slots.indexOf(activa);
    const out = slots.slice();
    out[i >= 0 ? i : out.length - 1] = id;
    return out;
  }
  const _pure = { normalizarUrl, interpretarEntrada, urlBusqueda, linkAlPreview, faviconSrc,
    disposicion, mayorQueEntra, asignarSlots, elegirTab, CELDA_MIN };

  // Bilingüe para strings COMPUESTAS (números/valores adentro → el observer de
  // i18n no las matchea por clave). Las estáticas van en español y las traduce
  // el observer; éstas se arman a mano.
  const _L = (es, en) => (root.JarvisI18n?.lang?.() === 'en' ? en : es);

  // ── Estado ──────────────────────────────────────────────────────
  // UN solo concepto de foco: la pestaña ACTIVA es la del panel enfocado, la que
  // muestra la barra de direcciones y la que recibe Atrás/Adelante/Recargar.
  // (Antes había "activa" y "foco" por separado: hacer click en el panel 2 y
  // escribir una URL la cargaba en el panel 1.)
  let _cont = null, _montado = false;
  let _grid = null, _tabsEl = null, _input = null, _status = null, _dd = null, _spin = null;
  let _btnBack = null, _btnFwd = null;
  let _tabs = [], _activaId = null, _nextId = 1;
  let _layoutPref = '1', _n = 1, _slots = [], _pid = null, _resizeTimer = null, _ddSel = -1;
  let _pendienteUrl = null, _layoutRaf = 0;
  const MAX_TABS = 4;
  const LS_LAYOUT = 'jarvis.browser.layout';

  const $ = (sel) => _cont ? _cont.querySelector(sel) : null;
  const _tabDe = (id) => _tabs.find((t) => t.id === id) || null;
  const _activa = () => _tabDe(_activaId) || _tabs[0] || null;

  const SVG = {
    back: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3.5 5.5 8l4.5 4.5"/></svg>',
    fwd: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3.5 10.5 8 6 12.5"/></svg>',
    reload: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2v3h-3"/></svg>',
    lupa: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="7" cy="7" r="4.5"/><path d="m13.5 13.5-3-3"/></svg>',
    globo: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><circle cx="8" cy="8" r="6"/><path d="M2 8h12M8 2c1.7 1.8 1.7 10.2 0 12M8 2c-1.7 1.8-1.7 10.2 0 12"/></svg>',
    play: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M5 3.5 12.5 8 5 12.5z"/></svg>',
    plus: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M8 3.5v9M3.5 8h9"/></svg>',
    x: '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="m2 2 8 8M10 2l-8 8"/></svg>',
    monitor: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8m-4-4v4"/></svg>',
    // Iconos del selector de paneles: DIBUJAN la disposición (2 = lado a lado,
    // 3 = uno grande + dos, 4 = cuadrícula) para entenderse sin leer.
    layout: (n) => {
      const celdas = n === 1
        ? '<rect x="4" y="5" width="16" height="14" rx="2"/>'
        : n === 2
          ? '<rect x="4" y="5" width="7" height="14" rx="1.5"/><rect x="13" y="5" width="7" height="14" rx="1.5"/>'
          : n === 3
            ? '<rect x="4" y="5" width="9" height="14" rx="1.5"/><rect x="15" y="5" width="5" height="6" rx="1.3"/><rect x="15" y="13" width="5" height="6" rx="1.3"/>'
            : '<rect x="4" y="5" width="7" height="6.5" rx="1.5"/><rect x="13" y="5" width="7" height="6.5" rx="1.5"/><rect x="4" y="12.5" width="7" height="6.5" rx="1.5"/><rect x="13" y="12.5" width="7" height="6.5" rx="1.5"/>';
      return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">${celdas}</svg>`;
    },
  };

  function init(containerEl) {
    if (_montado) return;
    _cont = containerEl || document.getElementById('jw-pane-preview');
    if (!_cont) return;
    _montado = true;
    try { const g = localStorage.getItem(LS_LAYOUT); if (g && /^[1-4]$/.test(g)) _layoutPref = g; } catch { /* noop */ }
    _montar();
    window.addEventListener('jarvis:lang', () => { if (_montado) { _renderTabs(); _renderLayoutBtns(); } });
    if (_pendienteUrl) { const u = _pendienteUrl; _pendienteUrl = null; setUrl(u); }
  }

  function _montar() {
    // Traducciones ES→EN de las etiquetas nuevas (runtime, sin tocar el dict).
    root.JarvisI18n?.agregar?.({
      'Buscá o pegá una URL…': 'Search or paste a URL…',
      'Un browser de verdad': 'A real browser',
      'Entra cualquier sitio: X, YouTube, Google…': 'Loads any site: X, YouTube, Google…',
      'Buscá en la web': 'Search the web',
      'Nueva pestaña': 'New tab',
      'Cerrar pestaña': 'Close tab',
      'Dividir pantalla': 'Split view',
      'Atrás': 'Back', 'Adelante': 'Forward', 'Recargar': 'Reload',
      'Pestañas abiertas': 'Open tabs', 'Pestañas': 'Tabs', 'Buscá o pegá una URL': 'Search or paste a URL', 'Reciente': 'Recent', 'Ir a': 'Go to',
    });
    _cont.innerHTML = `
      <div class="br-wrap">
        <div class="br-chrome">
          <div class="br-tabs" id="br-tabs" role="tablist" aria-label="Pestañas"></div>
          <button class="br-tab-new" id="br-new" title="Nueva pestaña" aria-label="Nueva pestaña">${SVG.plus}</button>
          <div class="br-layouts" role="group" aria-label="Dividir pantalla" id="br-layouts">
            ${[1, 2, 3, 4].map((n) => `<button class="br-ls" data-layout="${n}" aria-pressed="${n === 1}">${SVG.layout(n)}</button>`).join('')}
          </div>
        </div>
        <div class="br-bar">
          <button class="br-nav" id="br-back" title="Atrás" aria-label="Atrás" disabled>${SVG.back}</button>
          <button class="br-nav" id="br-fwd" title="Adelante" aria-label="Adelante" disabled>${SVG.fwd}</button>
          <div class="br-omni-wrap">
            <div class="br-omni">
              <span>${SVG.lupa}</span>
              <input id="br-url" type="text" spellcheck="false" autocomplete="off"
                     placeholder="Buscá o pegá una URL…" aria-label="Buscá o pegá una URL">
            </div>
            <div class="br-dd" id="br-dd" hidden></div>
          </div>
          <button class="br-nav" id="br-reload" title="Recargar" aria-label="Recargar">${SVG.reload}</button>
          <button class="br-nav wp-localhosts" id="jw-localhosts-btn" type="button" hidden
                  title="Localhost activos" aria-label="Localhost activos" aria-haspopup="menu" aria-expanded="false"></button>
        </div>
        <div class="br-grid" id="br-grid" data-n="1"></div>
        <span class="br-status" id="br-status" hidden></span>
        <span class="br-spin" id="br-spin" hidden></span>
      </div>`;

    _tabsEl = $('#br-tabs'); _grid = $('#br-grid'); _input = $('#br-url');
    _status = $('#br-status'); _dd = $('#br-dd'); _spin = $('#br-spin');
    _btnBack = $('#br-back'); _btnFwd = $('#br-fwd');

    _input.addEventListener('keydown', _onOmniKey);
    _input.addEventListener('input', () => _renderDD());
    _input.addEventListener('focus', () => _renderDD());
    _input.addEventListener('blur', () => setTimeout(_cerrarDD, 150));
    _btnBack.addEventListener('click', () => _accionFoco({ t: 'back' }));
    _btnFwd.addEventListener('click', () => _accionFoco({ t: 'fwd' }));
    $('#br-reload').addEventListener('click', refresh);
    $('#br-new').addEventListener('click', () => { _nuevaTab(null, true); });
    $('#br-layouts').addEventListener('click', (e) => {
      const b = e.target.closest('.br-ls');
      if (b && !b.disabled) _setLayout(b.dataset.layout);
    });
    // El espacio disponible decide qué disposiciones entran (ver `disposicion`).
    if (root.ResizeObserver) new ResizeObserver(_pedirLayout).observe(_grid);
    else window.addEventListener('resize', _pedirLayout);

    _nuevaTab();  // arranca con una pestaña vacía (start page; sin robar foco)
    _render();
    // Menú de "Localhost activos": su botón vive en ESTE chrome.
    root.JarvisDevServers?.init?.();
    if (_pid != null) root.JarvisDevServers?.cargar?.(_pid);
  }

  // ── Tabs ────────────────────────────────────────────────────────
  function _crearTab() {
    const id = _nextId++;
    const tab = { id, url: null, titulo: null, favicon: null, ws: null, img: null, cell: null, start: null, head: null, listo: false, cargando: false };
    _tabs.push(tab);
    _crearCelda(tab);
    return tab;
  }

  function _nuevaTab(url, enfocar) {
    if (_tabs.length >= MAX_TABS) { _estado(_L('Máximo de pestañas alcanzado', 'Too many tabs'), true); return null; }
    const tab = _crearTab();
    // Una pestaña nueva se ve YA: ocupa el panel de la que estaba activa.
    _slots = elegirTab(_slots.length ? _slots : [], _activaId, tab.id);
    _activaId = tab.id;
    _aplicarLayout();
    if (url) setUrl(url, tab);
    _render();
    if (enfocar) setTimeout(() => _input?.focus(), 30);
    return tab;
  }

  function _crearCelda(tab) {
    const cell = document.createElement('div');
    cell.className = 'br-cell';
    cell.dataset.id = tab.id;
    cell.innerHTML = `
      <div class="br-pane-h">
        <span class="fav fallback">${SVG.globo}</span>
        <span class="t"></span>
        <button class="x" type="button" title="Cerrar pestaña" aria-label="Cerrar pestaña">${SVG.x}</button>
      </div>
      <div class="br-body">
        <img class="br-frame" alt="" hidden>
        <div class="br-start">
          <div class="logo">${SVG.monitor}</div>
          <h2>${_L('Un browser de verdad', 'A real browser')}</h2>
          <p>${_L('Escribí una URL o una búsqueda arriba. Entra cualquier sitio: X, YouTube, Google…',
                  'Type a URL or a search above. Loads any site: X, YouTube, Google…')} <code>localhost:5173</code> ${_L('también.', 'too.')}</p>
          <div class="br-chips">
            <button class="br-chip" type="button" data-chip="youtube">${SVG.play} YouTube</button>
            <button class="br-chip" type="button" data-chip="busqueda">${SVG.lupa} ${_L('Buscá en la web', 'Search the web')}</button>
          </div>
        </div>
      </div>`;
    tab.cell = cell;
    tab.head = cell.querySelector('.br-pane-h');
    tab.body = cell.querySelector('.br-body');
    tab.img = cell.querySelector('.br-frame');
    tab.start = cell.querySelector('.br-start');

    // Tocar un panel lo ENFOCA de verdad (barra, Atrás/Recargar y pestañas lo siguen).
    cell.addEventListener('mousedown', () => { if (_activaId !== tab.id) _activarTab(tab.id, false); cell.focus({ preventScroll: true }); });
    tab.head.querySelector('.x').addEventListener('click', (e) => { e.stopPropagation(); _cerrarTab(tab.id); });
    tab.img.addEventListener('mousemove', (e) => _mouse(tab, 'move', e));
    tab.img.addEventListener('mousedown', (e) => { cell.focus({ preventScroll: true }); _mouse(tab, 'down', e); });
    tab.img.addEventListener('mouseup', (e) => _mouse(tab, 'up', e));
    tab.img.addEventListener('contextmenu', (e) => e.preventDefault());
    tab.body.addEventListener('wheel', (e) => {
      e.preventDefault();
      const r = tab.img.getBoundingClientRect();
      _enviar(tab, { t: 'wheel', x: e.clientX - r.left, y: e.clientY - r.top, dx: e.deltaX, dy: e.deltaY });
    }, { passive: false });
    cell.tabIndex = 0;
    cell.addEventListener('keydown', (e) => _tecla(tab, e));
    cell.addEventListener('keyup', (e) => _tecla(tab, e));
    cell.addEventListener('click', (e) => {
      const chip = e.target.closest('[data-chip]');
      if (chip) { setUrl(urlBusqueda(chip.dataset.chip, ''), tab); }
    });
    _grid.appendChild(cell);
  }

  function _cerrarTab(id) {
    const i = _tabs.findIndex((t) => t.id === id);
    if (i === -1) return;
    const tab = _tabs[i];
    _cerrarWsDe(tab);
    tab.cell?.remove();
    _tabs.splice(i, 1);
    _slots = _slots.filter((x) => x !== id);
    if (_activaId === id) _activaId = (_slots[Math.min(i, _slots.length - 1)] ?? _tabs[Math.min(i, _tabs.length - 1)]?.id) ?? null;
    // Cerrar un panel de una vista dividida la ACHICA (antes se re-creaba vacío en el
    // acto y el ✕ parecía no hacer nada).
    if (_tabs.length && _tabs.length < parseInt(_layoutPref, 10)) _guardarLayout(String(_tabs.length));
    if (!_tabs.length) { _nuevaTab(); return; }
    _aplicarLayout(); _render();
  }

  function _guardarLayout(l) {
    _layoutPref = String(l);
    try { localStorage.setItem(LS_LAYOUT, _layoutPref); } catch { /* noop */ }
  }

  function _medidaGrid() {
    const r = _grid ? _grid.getBoundingClientRect() : { width: 0, height: 0 };
    return { w: r.width, h: r.height };
  }

  function _pedirLayout() {
    if (_layoutRaf) return;
    _layoutRaf = requestAnimationFrame(() => { _layoutRaf = 0; if (_montado) { _aplicarLayout(); _programarResize(); } });
  }

  // Decide cuántos paneles hay y cómo se reparten, crea las pestañas que falten
  // y asigna una pestaña a cada panel.
  function _aplicarLayout() {
    if (!_grid) return;
    const { w, h } = _medidaGrid();
    const pedido = parseInt(_layoutPref, 10) || 1;
    _n = w > 0 ? mayorQueEntra(pedido, w, h) : pedido;
    while (_tabs.length < _n && _tabs.length < MAX_TABS) _crearTab();
    if (!_activaId && _tabs.length) _activaId = _tabs[0].id;
    _slots = asignarSlots(_slots, _tabs.map((t) => t.id), _n, _activaId);
    const d = disposicion(_n, w, h) || disposicion(_n, 0, 0);
    _grid.dataset.n = String(_n);
    _grid.style.gridTemplateColumns = `repeat(${d.cols}, minmax(0, 1fr))`;
    _grid.style.gridTemplateRows = `repeat(${d.rows}, minmax(0, 1fr))`;
    _tabs.forEach((t) => {
      const i = _slots.indexOf(t.id);
      if (!t.cell) return;
      t.cell.hidden = i === -1;
      t.cell.style.order = String(i);
      t.cell.style.gridRow = d.grande && i === 0 ? 'span 2' : '';
      t.cell.dataset.slot = String(i);
    });
    _renderLayoutBtns();
    _renderTabs();
  }

  function _renderLayoutBtns() {
    if (!_cont) return;
    const { w, h } = _medidaGrid();
    _cont.querySelectorAll('.br-ls').forEach((b) => {
      const n = parseInt(b.dataset.layout, 10);
      const cabe = w <= 0 || n === 1 || !!disposicion(n, w, h);
      b.setAttribute('aria-pressed', String(n === _n));
      b.disabled = !cabe;
      const nombre = n === 1 ? _L('1 panel', '1 pane') : _L(`${n} paneles`, `${n} panes`);
      b.setAttribute('aria-label', nombre);
      b.title = cabe ? nombre : `${nombre} — ${_L('no entra en este ancho: agrandá el panel', 'doesn\'t fit this width: make the panel bigger')}`;
    });
  }

  function _setLayout(l) {
    _guardarLayout(l);
    _aplicarLayout();
    _render();
  }

  function _renderTabs() {
    if (!_tabsEl) return;
    // Con vista dividida y TODAS las pestañas a la vista, la barra de pestañas sobra:
    // cada panel ya lleva su cabecera con título y ✕.
    _tabsEl.hidden = _n > 1 && _tabs.every((t) => _slots.includes(t.id));
    _tabsEl.innerHTML = _tabs.map((t) => {
      const lbl = t.titulo || _hostDe(t.url) || _L('Nueva pestaña', 'New tab');
      const fav = t.favicon
        ? `<img class="fav" src="${_esc(t.favicon)}" alt="">`
        : `<span class="fav fallback">${SVG.globo}</span>`;
      const visible = _slots.includes(t.id);
      return `<div class="br-tab${t.id === _activaId ? ' activa' : ''}${visible ? ' visible' : ''}" data-id="${t.id}" role="tab" aria-selected="${t.id === _activaId}" title="${_esc(lbl)}">
        ${fav}<span class="lbl">${_esc(lbl)}</span>
        <button class="x" data-cerrar="${t.id}" title="Cerrar pestaña" aria-label="Cerrar pestaña">${SVG.x}</button>
      </div>`;
    }).join('');
    _tabsEl.querySelectorAll('.br-tab').forEach((el) => {
      el.addEventListener('click', (e) => {
        const cerrar = e.target.closest('[data-cerrar]');
        if (cerrar) { e.stopPropagation(); _cerrarTab(+cerrar.dataset.cerrar); return; }
        _activarTab(+el.dataset.id, true);
      });
    });
    // Cabecera de cada panel (solo se muestra con vista dividida).
    _tabs.forEach((t) => {
      if (!t.head) return;
      const lbl = t.titulo || _hostDe(t.url) || _L('Nueva pestaña', 'New tab');
      t.head.querySelector('.t').textContent = lbl;
      const fav = t.head.querySelector('.fav');
      if (t.favicon) {
        if (fav.tagName !== 'IMG') { const im = document.createElement('img'); im.className = 'fav'; im.alt = ''; fav.replaceWith(im); }
        const im = t.head.querySelector('.fav'); if (im.getAttribute('src') !== t.favicon) im.src = t.favicon;
      }
    });
  }

  function _activarTab(id, enfocarBarra) {
    if (!_tabDe(id)) return;
    _slots = elegirTab(_slots, _activaId, id);
    _activaId = id;
    _aplicarLayout();
    _render();
    if (enfocarBarra) setTimeout(() => _input?.focus(), 20);
  }

  function _render() {
    const t = _activa();
    if (_input && document.activeElement !== _input) _input.value = (t && t.url) || '';
    else if (_input && t) _input.value = t.url || _input.value;
    _marcarFoco();
    _renderTabs();
  }

  function _marcarFoco() {
    _tabs.forEach((t) => t.cell?.classList.toggle('foco', t.id === _activaId));
    _setSpin(_activa()?.cargando);
    _setNav(_activa());
  }

  function _setSpin(v) { if (_spin) _spin.hidden = !v; }
  // Atrás/Adelante: el server no informa del historial, así que se habilitan en
  // cuanto el panel enfocado tiene una página (antes quedaban SIEMPRE deshabilitados).
  function _setNav(t) {
    const hay = !!(t && t.url && t.ws);
    if (_btnBack) _btnBack.disabled = !hay;
    if (_btnFwd) _btnFwd.disabled = !hay;
  }
  function _hostDe(url) { try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return null; } }

  // ── Sesión por tab ──────────────────────────────────────────────
  function _medir(cell) {
    const r = (cell?.querySelector?.('.br-body') || cell || _grid).getBoundingClientRect();
    return { w: Math.max(240, Math.round(r.width)), h: Math.max(160, Math.round(r.height)) };
  }
  function _programarResize() {
    clearTimeout(_resizeTimer);
    _resizeTimer = setTimeout(() => {
      _tabs.forEach((t) => {
        if (!t.ws || t.ws.readyState !== 1 || !t.cell || t.cell.hidden) return;
        const { w, h } = _medir(t.cell);
        if (Math.abs(w - (t._w || 0)) < 12 && Math.abs(h - (t._h || 0)) < 12) return;
        t._w = w; t._h = h;
        _enviar(t, { t: 'resize', w, h });
      });
    }, 180);
  }

  function _conectar(tab) {
    _cerrarWsDe(tab);
    const { w, h } = _medir(tab.cell);
    tab._w = w; tab._h = h;
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws/browser/${Date.now()}-${tab.id}?w=${w}&h=${h}`);
    tab.ws = ws;
    tab.cargando = true; _setSpin(true); _estado(_L('Conectando…', 'Connecting…'), false);
    ws.addEventListener('open', () => {
      _estado('', false); _setNav(_activa());
      if (tab._pendiente) { _enviar(tab, { t: 'nav', url: tab._pendiente }); tab._pendiente = null; }
    });
    ws.addEventListener('message', (ev) => _onMensaje(tab, ev));
    ws.addEventListener('close', () => { if (tab.ws === ws) { tab.cargando = false; _setSpin(false); } });
    ws.addEventListener('error', () => { if (tab.ws === ws) _estado(_L('Error de conexión', 'Connection error'), true); });
  }
  function _cerrarWsDe(tab) {
    if (tab.ws) { try { tab.ws.close(); } catch { /* noop */ } tab.ws = null; }
  }
  function _cerrarWs() { _tabs.forEach(_cerrarWsDe); }

  function _onMensaje(tab, ev) {
    let m;
    try { m = JSON.parse(ev.data); } catch { return; }
    if (m.t === 'frame') {
      tab.img.src = 'data:image/jpeg;base64,' + m.data;
      if (tab.img.hidden) tab.img.hidden = false;
      if (tab.start) tab.start.hidden = true;
      if (tab.cargando) { tab.cargando = false; _setSpin(false); }
    } else if (m.t === 'nav') {
      tab.url = m.url;
      tab.titulo = m.titulo || _hostDe(m.url) || '';
      tab.favicon = faviconSrc(m.url);
      if (tab.id === _activaId && document.activeElement !== _input) _input.value = m.url || '';
      _recordar(m.url);
      _renderTabs(); _setNav(_activa());
    } else if (m.t === 'err') {
      _estado(m.msg || _L('Error', 'Error'), true);
      tab.cargando = false; _setSpin(false);
    }
  }

  function _enviar(tab, obj) {
    if (tab && tab.ws && tab.ws.readyState === 1) tab.ws.send(JSON.stringify(obj));
  }
  function _accionFoco(obj) { const t = _activa(); if (t) _enviar(t, obj); }

  // ── API pública ─────────────────────────────────────────────────
  function setUrl(url, tab) {
    if (!_montado) { _pendienteUrl = url; return; }
    const norm = normalizarUrl(url);
    if (!norm) return;
    const t = tab || _activa() || _nuevaTab();
    t.url = norm;
    if (t.id === _activaId) _input.value = norm;
    if (t.ws && t.ws.readyState === 1) _enviar(t, { t: 'nav', url: norm });
    else { t._pendiente = norm; _conectar(t); }
    t.cargando = true; _setSpin(true);
    _renderTabs();
  }
  function getUrl() { return _activa()?.url || null; }
  function refresh() { _accionFoco({ t: 'reload' }); }
  function openTab(url) { const t = _nuevaTab(null, true); if (url) setUrl(url, t); return t; }
  function abrirLink(url) { if (!_montado) { _pendienteUrl = url; return; } setUrl(url); }
  function openExternal() { const u = getUrl(); if (u) window.open(u, '_blank', 'noopener'); }
  function refrescarSiExiste(url) {
    // Sin la barra final: el server reporta `page.url` de Chromium
    // (`http://localhost:5173/`) y dev_detect manda `http://localhost:5173` —
    // con === nunca matcheaban y el auto-refresh no andaba.
    const sinBarra = (u) => String(u || '').replace(/\/+$/, '');
    const norm = sinBarra(normalizarUrl(url));
    const t = norm ? _tabs.find((x) => sinBarra(x.url) === norm) : null;
    if (!t) return false;
    // Activar SIN robar el foco: esto lo dispara un evento de fondo (un agente
    // reinició su dev server) mientras el usuario puede estar tipeando.
    _activarTab(t.id, false);
    refresh(); return true;
  }
  function onProjectChanged(pid) {
    _pid = pid;
    if (!_montado) { root.JarvisDevServers?.cargar?.(pid); return; }
    _cerrarWs();
    _tabs.forEach((t) => t.cell?.remove());
    _tabs = []; _slots = []; _activaId = null; _nextId = 1;
    _nuevaTab();
    root.JarvisDevServers?.cargar?.(pid);
  }
  function detectar(pid) { root.JarvisDevServers?.cargar?.(pid); }

  // ── Input ───────────────────────────────────────────────────────
  function _mouse(tab, a, e) {
    const r = tab.img.getBoundingClientRect();
    const escX = tab._w ? r.width / tab._w : 1;
    const escY = tab._h ? r.height / tab._h : 1;
    _enviar(tab, { t: 'mouse', a, x: Math.round((e.clientX - r.left) / (escX || 1)),
                   y: Math.round((e.clientY - r.top) / (escY || 1)),
                   b: e.button === 2 ? 'right' : e.button === 1 ? 'middle' : 'left' });
  }
  function _tecla(tab, e) {
    const mods = (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0);
    if (e.metaKey || (e.ctrlKey && e.key !== 'v' && e.key !== 'c')) return;
    e.preventDefault();
    _enviar(tab, { t: 'key', a: e.type === 'keyup' ? 'up' : 'down', key: e.key, code: e.code, mods });
  }

  // ── Omnibar + sugerencias ───────────────────────────────────────
  function _onOmniKey(e) {
    if (e.key === 'Enter') {
      const r = _ddSel >= 0 && _sugerencias.length ? _sugerencias[_ddSel] : interpretarEntrada(_input.value);
      _cerrarDD();
      if (r.tipo === 'invalida' || r.tipo === 'vacia') return;
      setUrl(r.tipo === 'url' ? r.url : urlBusqueda(r.tipo, r.q));
      return;
    }
    if (e.key === 'ArrowDown' && _sugerencias.length) { _ddSel = Math.min(_ddSel + 1, _sugerencias.length - 1); _pintarDD(); e.preventDefault(); return; }
    if (e.key === 'ArrowUp' && _sugerencias.length) { _ddSel = Math.max(_ddSel - 1, -1); _pintarDD(); e.preventDefault(); return; }
    if (e.key === 'Escape') { _cerrarDD(); _input.blur(); }
  }

  let _sugerencias = [];
  function _recientes() {
    try { return JSON.parse(localStorage.getItem('jarvis.browser.recientes') || '[]'); } catch { return []; }
  }
  function _recordar(url) {
    try {
      const l = _recientes().filter((u) => u !== url);
      l.unshift(url);
      localStorage.setItem('jarvis.browser.recientes', JSON.stringify(l.slice(0, 8)));
    } catch { /* noop */ }
  }
  function _renderDD() {
    const q = _input.value.trim();
    const filas = [];
    if (q) {
      filas.push({ g: _L('Ir a', 'Go to') });
      filas.push({ tipo: 'busqueda', q, ic: 'lupa', t: _L(`Buscar «${q}» en la web`, `Search "${q}" on the web`), s: 'Google', tag: '↵' });
      filas.push({ tipo: 'youtube', q, ic: 'play', t: _L(`«${q}» en YouTube`, `"${q}" on YouTube`), s: 'youtube.com', tag: 'yt' });
      if (_pareceUrl(q)) { const u = normalizarUrl(q); if (u) filas.push({ tipo: 'url', url: u, ic: 'lupa', t: q, s: _L('abrir como URL', 'open as URL'), tag: 'URL' }); }
    } else {
      if (_tabs.length) { filas.push({ g: _L('Pestañas abiertas', 'Open tabs') }); _tabs.forEach((t) => filas.push({ tipo: 'tab', tab: t, ic: 'monitor', t: t.titulo || _hostDe(t.url) || _L('Nueva pestaña', 'New tab'), s: t.url || '' })); }
      const rec = _recientes();
      if (rec.length) { filas.push({ g: _L('Reciente', 'Recent') }); rec.slice(0, 5).forEach((u) => filas.push({ tipo: 'url', url: u, ic: 'reload', t: _hostDe(u) || u, s: u })); }
    }
    _sugerencias = filas.filter((f) => !f.g);
    _ddSel = -1;
    if (!_sugerencias.length) { _cerrarDD(); return; }
    _dd.hidden = false;
    _dd.innerHTML = filas.map((f, i) => {
      if (f.g) return `<div class="br-dd-group">${_esc(f.g)}</div>`;
      const idx = _sugerencias.indexOf(f);
      return `<div class="br-dd-item${idx === _ddSel ? ' sel' : ''}" data-i="${idx}">
        <span class="ic">${SVG[f.ic] || SVG.globo}</span>
        <span class="tx"><b>${_esc(f.t)}</b>${f.s ? `<span>${_esc(f.s)}</span>` : ''}</span>
        ${f.tag ? `<span class="tag">${_esc(f.tag)}</span>` : ''}</div>`;
    }).join('');
    _dd.querySelectorAll('.br-dd-item').forEach((el) => {
      el.addEventListener('mousedown', (e) => {
        e.preventDefault();
        const f = _sugerencias[+el.dataset.i];
        if (!f) return;
        _cerrarDD();
        if (f.tipo === 'tab' && f.tab) _activarTab(f.tab.id);
        else setUrl(f.tipo === 'url' ? f.url : urlBusqueda(f.tipo, f.q));
      });
    });
  }
  function _pintarDD() { _dd.querySelectorAll('.br-dd-item').forEach((el, i) => el.classList.toggle('sel', i === _ddSel)); }
  function _cerrarDD() { if (_dd) { _dd.hidden = true; _dd.innerHTML = ''; _sugerencias = []; _ddSel = -1; } }

  function _estado(txt, esErr) {
    if (!_status) return;
    if (!txt) { _status.hidden = true; return; }
    _status.hidden = false; _status.textContent = txt; _status.classList.toggle('err', !!esErr);
  }
  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  root.WebPreview = {
    init, setUrl, getUrl, openTab, abrirLink, detectar, refresh,
    refrescarSiExiste, openExternal, onProjectChanged, _pure,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = _pure;
})(typeof window !== 'undefined' ? window : globalThis);
