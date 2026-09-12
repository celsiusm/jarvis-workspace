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
    if (tipo === 'radio') return 'https://music.youtube.com/';
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
  const _pure = { normalizarUrl, interpretarEntrada, urlBusqueda, linkAlPreview, faviconSrc };

  // Bilingüe para strings COMPUESTAS (números/valores adentro → el observer de
  // i18n no las matchea por clave). Las estáticas van en español y las traduce
  // el observer; éstas se arman a mano.
  const _L = (es, en) => (root.JarvisI18n?.lang?.() === 'en' ? en : es);

  // ── Estado ──────────────────────────────────────────────────────
  let _cont = null, _montado = false;
  let _grid = null, _tabsEl = null, _input = null, _status = null, _dd = null, _spin = null;
  let _btnBack = null, _btnFwd = null;
  let _tabs = [], _activaId = null, _focoId = null, _nextId = 1;
  let _layout = '1', _pid = null, _resizeTimer = null, _ddSel = -1;
  let _pendienteUrl = null;
  const MAX_TABS = 4;

  const $ = (sel) => _cont ? _cont.querySelector(sel) : null;
  const _tabDe = (id) => _tabs.find((t) => t.id === id) || null;
  const _activa = () => _tabDe(_activaId) || _tabs[0] || null;
  const _foco = () => _tabDe(_focoId) || _activa();

  const SVG = {
    back: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3.5 5.5 8l4.5 4.5"/></svg>',
    fwd: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3.5 10.5 8 6 12.5"/></svg>',
    reload: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2v3h-3"/></svg>',
    lupa: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="7" cy="7" r="4.5"/><path d="m13.5 13.5-3-3"/></svg>',
    globo: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><circle cx="8" cy="8" r="6"/><path d="M2 8h12M8 2c1.7 1.8 1.7 10.2 0 12M8 2c-1.7 1.8-1.7 10.2 0 12"/></svg>',
    play: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M5 3.5 12.5 8 5 12.5z"/></svg>',
    music: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M6 12V4l7-1.5v8"/><circle cx="4.3" cy="12" r="1.7"/><circle cx="11.3" cy="10.5" r="1.7"/></svg>',
    plus: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M8 3.5v9M3.5 8h9"/></svg>',
    x: '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="m2 2 8 8M10 2l-8 8"/></svg>',
    monitor: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8m-4-4v4"/></svg>',
    layout: (n) => {
      const celdas = n === 1
        ? '<rect x="4" y="5" width="16" height="14" rx="2"/>'
        : n === 2
          ? '<rect x="4" y="5" width="7" height="14" rx="1.5"/><rect x="13" y="5" width="7" height="14" rx="1.5"/>'
          : n === 3
            ? '<rect x="3.5" y="5" width="5" height="14" rx="1.3"/><rect x="9.5" y="5" width="5" height="14" rx="1.3"/><rect x="15.5" y="5" width="5" height="14" rx="1.3"/>'
            : '<rect x="4" y="5" width="7" height="6.5" rx="1.5"/><rect x="13" y="5" width="7" height="6.5" rx="1.5"/><rect x="4" y="12.5" width="7" height="6.5" rx="1.5"/><rect x="13" y="12.5" width="7" height="6.5" rx="1.5"/>';
      return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">${celdas}</svg>`;
    },
  };

  function init(containerEl) {
    if (_montado) return;
    _cont = containerEl || document.getElementById('jw-pane-preview');
    if (!_cont) return;
    _montado = true;
    _montar();
    window.addEventListener('jarvis:lang', () => { if (_montado) { _renderTabs(); } });
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
      'Disposición de paneles': 'Panel layout',
      'Atrás': 'Back', 'Adelante': 'Forward', 'Recargar': 'Reload',
      'Pestañas abiertas': 'Open tabs', 'Reciente': 'Recent', 'Ir a': 'Go to',
    });
    _cont.innerHTML = `
      <div class="br-wrap">
        <div class="br-chrome">
          <div class="br-tabs" id="br-tabs" role="tablist" aria-label="Pestañas"></div>
          <button class="br-tab-new" id="br-new" title="Nueva pestaña" aria-label="Nueva pestaña">${SVG.plus}</button>
          <div class="br-layouts" role="group" aria-label="Disposición de paneles" id="br-layouts">
            ${[1, 2, 3, 4].map((n) => `<button class="br-ls" data-layout="${n}" aria-pressed="${n === 1}" title="${_L(n + ' panel' + (n > 1 ? 'es' : ''), n + (n > 1 ? ' panels' : ' panel'))}" aria-label="${_L(n + ' paneles', n + ' panels')}">${SVG.layout(n)}</button>`).join('')}
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
        <div class="br-grid" id="br-grid" data-layout="1"></div>
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
      if (b) _setLayout(b.dataset.layout);
    });
    if (root.ResizeObserver) new ResizeObserver(_programarResize).observe(_grid);
    else window.addEventListener('resize', _programarResize);

    _nuevaTab();  // arranca con una pestaña vacía (start page; sin robar foco)
    _render();
    // Menú de "Localhost activos": su botón vive en ESTE chrome.
    root.JarvisDevServers?.init?.();
    if (_pid != null) root.JarvisDevServers?.cargar?.(_pid);
  }

  // ── Tabs ────────────────────────────────────────────────────────
  function _nuevaTab(url, enfocar) {
    if (_tabs.length >= MAX_TABS) { _estado(_L('Máximo de pestañas alcanzado', 'Too many tabs'), true); return null; }
    const id = _nextId++;
    const tab = { id, url: null, titulo: null, favicon: null, ws: null, img: null, cell: null, start: null, listo: false, cargando: false };
    _tabs.push(tab);
    _crearCelda(tab);
    _activaId = id; _focoId = id;
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
      <img class="br-frame" alt="" hidden>
      <div class="br-start">
        <div class="logo">${SVG.monitor}</div>
        <h2>${_L('Un browser de verdad', 'A real browser')}</h2>
        <p>${_L('Escribí una URL o una búsqueda arriba. Entra cualquier sitio: X, YouTube, Google…',
                'Type a URL or a search above. Loads any site: X, YouTube, Google…')} <code>localhost:5173</code> ${_L('también.', 'too.')}</p>
        <div class="br-chips">
          <button class="br-chip" type="button" data-chip="youtube">${SVG.play} YouTube</button>
          <button class="br-chip" type="button" data-chip="radio">${SVG.music} ${_L('Radio', 'Radio')}</button>
          <button class="br-chip" type="button" data-chip="busqueda">${SVG.lupa} ${_L('Buscá en la web', 'Search the web')}</button>
        </div>
      </div>`;
    tab.cell = cell;
    tab.img = cell.querySelector('.br-frame');
    tab.start = cell.querySelector('.br-start');

    cell.addEventListener('mousedown', () => { _focoId = tab.id; cell.focus(); _marcarFoco(); });
    tab.img.addEventListener('mousemove', (e) => _mouse(tab, 'move', e));
    tab.img.addEventListener('mousedown', (e) => { cell.focus(); _mouse(tab, 'down', e); });
    tab.img.addEventListener('mouseup', (e) => _mouse(tab, 'up', e));
    tab.img.addEventListener('contextmenu', (e) => e.preventDefault());
    cell.addEventListener('wheel', (e) => {
      e.preventDefault();
      const r = tab.img.getBoundingClientRect();
      _enviar(tab, { t: 'wheel', x: e.clientX - r.left, y: e.clientY - r.top, dx: e.deltaX, dy: e.deltaY });
    }, { passive: false });
    cell.tabIndex = 0;
    cell.addEventListener('keydown', (e) => _tecla(tab, e));
    cell.addEventListener('keyup', (e) => _tecla(tab, e));
    cell.addEventListener('click', (e) => {
      const chip = e.target.closest('[data-chip]');
      if (!chip) return;
      // La Radio es la vía de audio del workspace (el video del Browser va mudo
      // en el server): el chip la abre en vez de navegar.
      if (chip.dataset.chip === 'radio') { root.JarvisRadio?.open?.(); return; }
      setUrl(urlBusqueda(chip.dataset.chip, ''), tab);
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
    if (_activaId === id) _activaId = _tabs[Math.min(i, _tabs.length - 1)]?.id || null;
    if (_focoId === id) _focoId = _activaId;
    if (!_tabs.length) _nuevaTab();
    _aplicarLayout(); _render();
  }

  function _aplicarLayout() {
    if (!_grid) return;
    _grid.dataset.layout = _layout;
    const n = parseInt(_layout, 10);
    // Si el layout pide más paneles que pestañas, crear vacías (sin sesión).
    while (_tabs.length < n && _tabs.length < MAX_TABS) {
      const id = _nextId++;
      const tab = { id, url: null, titulo: null, favicon: null, ws: null, img: null, cell: null, start: null, listo: false, cargando: false };
      _tabs.push(tab); _crearCelda(tab);
    }
    _tabs.forEach((t, i) => { if (t.cell) t.cell.hidden = i >= n; });
    _renderTabs();
    _programarResize();
  }

  function _setLayout(l) {
    _layout = String(l);
    _cont.querySelectorAll('.br-ls').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.layout === _layout)));
    _aplicarLayout();
  }

  function _renderTabs() {
    if (!_tabsEl) return;
    _tabsEl.innerHTML = _tabs.map((t) => {
      const lbl = t.titulo || _hostDe(t.url) || _L('Nueva pestaña', 'New tab');
      const fav = t.favicon
        ? `<img class="fav" src="${t.favicon}" alt="">`
        : `<span class="fav fallback">${SVG.globo}</span>`;
      return `<div class="br-tab${t.id === _activaId ? ' activa' : ''}" data-id="${t.id}" role="tab" title="${_esc(lbl)}">
        ${fav}<span class="lbl">${_esc(lbl)}</span>
        <button class="x" data-cerrar="${t.id}" title="Cerrar pestaña" aria-label="Cerrar pestaña">${SVG.x}</button>
      </div>`;
    }).join('');
    _tabsEl.querySelectorAll('.br-tab').forEach((el) => {
      el.addEventListener('click', (e) => {
        const cerrar = e.target.closest('[data-cerrar]');
        if (cerrar) { e.stopPropagation(); _cerrarTab(+cerrar.dataset.cerrar); return; }
        _activarTab(+el.dataset.id);
      });
    });
  }

  function _activarTab(id) {
    _activaId = id; _focoId = id;
    _render();
    setTimeout(() => _input?.focus(), 20);
  }

  function _render() {
    const t = _activa();
    _input.value = (t && t.url) || '';
    _renderTabs();
    _marcarFoco();
  }

  function _marcarFoco() {
    _tabs.forEach((t) => t.cell?.classList.toggle('foco', t.id === _focoId));
    _setSpin(_foco()?.cargando);
    _setNav(_foco());
  }

  function _setSpin(v) { if (_spin) _spin.hidden = !v; }
  function _setNav(t) {
    if (_btnBack) _btnBack.disabled = true;
    if (_btnFwd) _btnFwd.disabled = true;
  }
  function _hostDe(url) { try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return null; } }

  // ── Sesión por tab ──────────────────────────────────────────────
  function _medir(cell) {
    const r = (cell || _grid).getBoundingClientRect();
    return { w: Math.max(320, Math.round(r.width)), h: Math.max(240, Math.round(r.height)) };
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
      _estado('', false);
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
      _renderTabs();
    } else if (m.t === 'err') {
      _estado(m.msg || _L('Error', 'Error'), true);
      tab.cargando = false; _setSpin(false);
    }
  }

  function _enviar(tab, obj) {
    if (tab && tab.ws && tab.ws.readyState === 1) tab.ws.send(JSON.stringify(obj));
  }
  function _accionFoco(obj) { const t = _foco(); if (t) _enviar(t, obj); }

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
    const norm = normalizarUrl(url);
    const t = _tabs.find((x) => x.url === norm);
    if (!t) return false;
    _activarTab(t.id); refresh(); return true;
  }
  function onProjectChanged(pid) {
    _pid = pid;
    if (!_montado) { root.JarvisDevServers?.cargar?.(pid); return; }
    _cerrarWs();
    _tabs.forEach((t) => t.cell?.remove());
    _tabs = []; _activaId = null; _focoId = null; _nextId = 1;
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
