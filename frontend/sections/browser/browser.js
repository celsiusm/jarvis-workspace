'use strict';
// ─── JARVIS — Browser (panel del dock) ───────────────────────────────────────
// Motor: Chromium server-side (`plotspace/core/remote_browser.py`) por
// WebSocket `/ws/browser/{sid}`. Acá vive SOLO el render del chrome + el envío
// de input. A diferencia del viejo Web Preview (iframe), este navega CUALQUIER
// sitio (X, YouTube, Google…) porque el framing lo hace el server, no el browser.
//
// Expone `window.WebPreview` con la MISMA superficie que consumían el shell y
// los módulos vecinos (init, setUrl, getUrl, openTab, abrirLink, detectar,
// refresh, refrescarSiExiste, openExternal, onProjectChanged, _pure), así la
// integración (terminal→browser, dev servers, radio) no cambia.
//
// API de `_pure` (testeable sin DOM): normalizarUrl, interpretarEntrada,
// urlBusqueda, linkAlPreview, faviconSrc.

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

  // Ya NO se reescribe a reproductores embebibles: el motor carga la web real.
  function normalizarUrl(input) {
    const norm = _normalizarBase(input);
    return norm == null ? null : _canonLoopback(norm);
  }

  // ¿Es una URL (trae host/esquema) o texto para buscar?
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

  // Un link de la terminal entra al Browser solo si es un server local / demo.
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
    try {
      const u = new URL(url);
      return `https://icons.duckduckgo.com/ip3/${u.host}.ico`;
    } catch { return null; }
  }

  const _pure = { normalizarUrl, interpretarEntrada, urlBusqueda, linkAlPreview, faviconSrc };

  // ── Estado del módulo ───────────────────────────────────────────
  let _cont = null, _montado = false;
  let _body = null, _img = null, _input = null, _empty = null, _status = null;
  let _btnBack = null, _btnFwd = null;
  let _ws = null, _sid = 0, _url = null, _titulo = null, _pid = null;
  let _pendiente = null, _ancho = 0, _alto = 0, _resizeTimer = null;
  let _hist = [], _histIdx = -1;

  const $ = (sel) => _cont ? _cont.querySelector(sel) : null;

  const SVG_BACK = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3.5 5.5 8l4.5 4.5"/></svg>';
  const SVG_FWD = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3.5 10.5 8 6 12.5"/></svg>';
  const SVG_RELOAD = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2v3h-3"/></svg>';
  const SVG_LUPA = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="7" cy="7" r="4.5"/><path d="m13.5 13.5-3-3"/></svg>';

  function init(containerEl) {
    if (_montado) return;
    _cont = containerEl || document.getElementById('jw-pane-preview');
    if (!_cont) return;
    _montado = true;
    _montar();
    // Si ya había una URL pedida antes de montar (init tardío), conectar.
    if (_url || _pendiente) _conectar();
  }

  function _montar() {
    _cont.innerHTML = `
      <div class="br-wrap">
        <div class="br-bar">
          <button class="br-nav" id="br-back" title="Atrás" aria-label="Atrás" disabled>${SVG_BACK}</button>
          <button class="br-nav" id="br-fwd" title="Adelante" aria-label="Adelante" disabled>${SVG_FWD}</button>
          <div class="br-omni">
            <span class="br-omni-ic">${SVG_LUPA}</span>
            <input id="br-url" type="text" spellcheck="false" autocomplete="off"
                   placeholder="Buscá o pegá una URL…" aria-label="Buscá o pegá una URL">
          </div>
          <button class="br-nav" id="br-reload" title="Recargar" aria-label="Recargar">${SVG_RELOAD}</button>
        </div>
        <div class="br-body" id="br-body" tabindex="0">
          <img class="br-frame" id="br-frame" alt="" hidden>
          <div class="br-empty" id="br-empty">
            <div>
              <h2>Un browser de verdad</h2>
              <p>Escribí una URL o una búsqueda arriba. Entra <b>cualquier</b> sitio:
                 X, YouTube, Google… <code>localhost:5173</code> también.</p>
            </div>
          </div>
          <span class="br-status" id="br-status" hidden></span>
        </div>
      </div>`;

    _body = $('#br-body'); _img = $('#br-frame'); _input = $('#br-url');
    _empty = $('#br-empty'); _status = $('#br-status');
    _btnBack = $('#br-back'); _btnFwd = $('#br-fwd');

    _input.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      const r = interpretarEntrada(_input.value);
      if (r.tipo === 'invalida' || r.tipo === 'vacia') {
        _input.classList.add('error');
        setTimeout(() => _input.classList.remove('error'), 300);
        return;
      }
      setUrl(r.tipo === 'url' ? r.url : urlBusqueda(r.tipo, r.q));
    });
    _btnBack.addEventListener('click', _atras);
    _btnFwd.addEventListener('click', _adelante);
    $('#br-reload').addEventListener('click', refresh);

    // Input del motor: mouse, rueda y teclado sobre el frame.
    _img.addEventListener('mousemove', (e) => _mouse('move', e));
    _img.addEventListener('mousedown', (e) => { _body.focus(); _mouse('down', e); });
    _img.addEventListener('mouseup', (e) => _mouse('up', e));
    _img.addEventListener('contextmenu', (e) => e.preventDefault());
    _body.addEventListener('wheel', (e) => {
      e.preventDefault();
      _enviar({ t: 'wheel', x: e.offsetX, y: e.offsetY, dx: e.deltaX, dy: e.deltaY });
    }, { passive: false });
    _body.addEventListener('keydown', _tecla);
    _body.addEventListener('keyup', _tecla);

    if (root.ResizeObserver) {
      new ResizeObserver(_programarResize).observe(_body);
    } else {
      window.addEventListener('resize', _programarResize);
    }
    _render();
  }

  // ── Sesión WS ───────────────────────────────────────────────────
  function _medir() {
    const r = _body.getBoundingClientRect();
    return { w: Math.max(320, Math.round(r.width)), h: Math.max(240, Math.round(r.height)) };
  }

  function _programarResize() {
    clearTimeout(_resizeTimer);
    _resizeTimer = setTimeout(() => {
      const { w, h } = _medir();
      if (Math.abs(w - _ancho) < 12 && Math.abs(h - _alto) < 12) return;
      _ancho = w; _alto = h;
      if (_ws && _ws.readyState === 1) _enviar({ t: 'resize', w, h });
      else if (_url) _conectar();
    }, 180);
  }

  function _conectar() {
    _cerrarWs();
    const { w, h } = _medir();
    _ancho = w; _alto = h;
    _sid++;
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws/browser/${Date.now()}-${_sid}?w=${w}&h=${h}`);
    _ws = ws;
    _estado('Conectando…', false);
    ws.addEventListener('open', () => {
      _estado('', false);
      const destino = _pendiente || _url;
      _pendiente = null;
      if (destino) _enviar({ t: 'nav', url: destino });
    });
    ws.addEventListener('message', _onMensaje);
    ws.addEventListener('close', () => { if (_ws === ws) _estado('Conexión cerrada', true); });
    ws.addEventListener('error', () => { if (_ws === ws) _estado('Error de conexión', true); });
  }

  function _cerrarWs() {
    if (_ws) {
      try { _ws.close(); } catch { /* noop */ }
      _ws = null;
    }
  }

  function _onMensaje(ev) {
    let m;
    try { m = JSON.parse(ev.data); } catch { return; }
    if (m.t === 'frame') {
      _img.src = 'data:image/jpeg;base64,' + m.data;
      if (_img.hidden) _img.hidden = false;
      if (!_empty.hidden) _empty.hidden = true;
    } else if (m.t === 'nav') {
      // Solo registramos la navegación REAL del motor (no la que tipeamos).
      _url = m.url;
      if (document.activeElement !== _input) _input.value = m.url || '';
      _hist = _hist.slice(0, _histIdx + 1);
      if (_hist[_histIdx] !== m.url) { _hist.push(m.url); _histIdx = _hist.length - 1; }
      _render();
      if (document.title !== undefined) { /* el título de la app no se toca */ }
    } else if (m.t === 'err') {
      _estado(m.msg || 'Error', true);
    }
  }

  function _enviar(obj) {
    if (_ws && _ws.readyState === 1) _ws.send(JSON.stringify(obj));
  }

  // ── API pública ─────────────────────────────────────────────────
  function setUrl(url) {
    const norm = url && url.tipo ? url : normalizarUrl(url) || url;
    if (!norm) return;
    _url = norm;
    if (_input) _input.value = norm;
    if (_ws && _ws.readyState === 1) _enviar({ t: 'nav', url: norm });
    else { _pendiente = norm; if (_montado) _conectar(); }
  }
  function getUrl() { return _url; }
  function refresh() { _enviar({ t: 'reload' }); }
  function openTab(url) { setUrl(url); }
  function abrirLink(url) { setUrl(url); }
  function openExternal() { if (_url) window.open(_url, '_blank', 'noopener'); }
  function refrescarSiExiste(url) {
    const norm = url ? (normalizarUrl(url) || url) : null;
    if (norm && _url === norm) { refresh(); return true; }
    return false;
  }
  function onProjectChanged(pid) {
    _pid = pid;
    if (!_montado) { root.JarvisDevServers?.cargar?.(pid); return; }
    _cerrarWs();
    _url = null; _hist = []; _histIdx = -1;
    _img.hidden = true; _empty.hidden = false;
    root.JarvisDevServers?.cargar?.(pid);
  }
  function detectar(pid) { root.JarvisDevServers?.cargar?.(pid); }
  function _atras() { _enviar({ t: 'back' }); }
  function _adelante() { _enviar({ t: 'fwd' }); }

  // ── Input ───────────────────────────────────────────────────────
  function _mouse(a, e) {
    const r = _img.getBoundingClientRect();
    const escX = _ancho ? r.width / _ancho : 1;
    const escY = _alto ? r.height / _alto : 1;
    _enviar({ t: 'mouse', a, x: Math.round(e.clientX - r.left) / (escX || 1),
              y: Math.round(e.clientY - r.top) / (escY || 1),
              b: e.button === 2 ? 'right' : e.button === 1 ? 'middle' : 'left' });
  }
  function _tecla(e) {
    if (e.target !== _body) return;  // no robarle teclas al omnibar
    const mods = (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0);
    // Meta/Ctrl combinaciones del sistema (atajos del workspace) se dejan pasar.
    if (e.metaKey || (e.ctrlKey && e.key !== 'v' && e.key !== 'c')) return;
    e.preventDefault();
    _enviar({ t: 'key', a: e.type === 'keyup' ? 'up' : 'down',
              key: e.key, code: e.code, mods });
  }

  function _estado(txt, esErr) {
    if (!_status) return;
    if (!txt) { _status.hidden = true; return; }
    _status.hidden = false;
    _status.textContent = txt;
    _status.classList.toggle('err', !!esErr);
  }

  function _render() {
    _btnBack.disabled = true;   // historial real lo maneja el motor (CDP)
    _btnFwd.disabled = true;
  }

  root.WebPreview = {
    init, setUrl, getUrl, openTab, abrirLink, detectar, refresh,
    refrescarSiExiste, openExternal, onProjectChanged, _pure,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = _pure;
})(typeof window !== 'undefined' ? window : globalThis);
