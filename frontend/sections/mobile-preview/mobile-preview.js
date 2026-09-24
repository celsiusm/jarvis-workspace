// ─── Mobile Studio ─────────────────────────────────────────────────────────
// Lienzo interactivo (pan/zoom/multi-teléfono) que muestra la app Expo REAL del
// proyecto en iframes, sobre un board de vidrio Obsidian. El panel NUNCA arranca
// Metro: DETECTA el Expo web que ya corre en una terminal (poll a /detectar) y lo
// muestra; si no hay, espera. El INSPECTOR edita el CÓDIGO REAL: lista el copy de
// UI editable del fuente (GET /textos) y al guardar reemplaza el literal exacto
// (POST /patch-text) → Metro hot-reloadea. Ver [[mobile-studio-rediseno]].
// Expone window.MobilePreview = { init, abrir, cerrar, sincronizar, onActividad }.

(() => {
  // Catálogo de dispositivos con specs REALES (device-catalog.js, testeado en
  // Node): viewport lógico, DPR, safe areas, recorte (isla/notch/punch), radio
  // de pantalla y home indicator — la geometría se dibuja 1:1 sobre el iframe.
  const DC = () => window.DeviceCatalog;
  const DEVS = window.DeviceCatalog.DEVICES;
  const ORDEN = window.DeviceCatalog.ORDEN;
  const MAX_PHONES = 4;
  const MAX_WEBS = 3;
  const MAX_NOTAS = 40;
  const ZMIN = 0.3, ZMAX = 3, DET_INTERVALO = 3000;
  const P = () => window.MobilePreviewPure;
  const $ = (id) => document.getElementById(id);
  const _esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const _t = (s) => (window.JarvisI18n && window.JarvisI18n.t) ? window.JarvisI18n.t(s) : s;

  // ── Estado ────────────────────────────────────────────────────────────────
  let _projectId = null;
  let _url = null;              // origin del Expo web detectado (o null = espera)
  let _cerrado = true;
  let _ruta = '/';
  let _estadoDet = '';
  let _detTimer = null;
  let _beTimer = null;
  let _seq = 1;
  let _pane = null;            // pane de info abierto ('console'|'net'|'inspect'|null)
  let _inspectFetch = false;   // ya se pidió /textos para el proyecto actual
  let _lastBackend = null;     // último backend-status (para el pane Red)
  const _logs = [];            // buffer de la consola de actividad
  const B = { zoom: 1, panX: 0, panY: 0 };
  const S = { phones: [], sel: null, webs: [], notas: [], guias: false, encaje: true };
  // Encuadrar se pidió con el stage en 0×0 (dock hidden / acaba de maximizar):
  // el ResizeObserver lo reintenta cuando el panel tenga tamaño real.
  let _fitAlVer = false;
  // Notas SECRETAS destapadas en esta sesión (ids). A propósito NO se persiste:
  // recargar la página vuelve a taparlas.
  const _notasAbiertas = new Set();
  try {
    S.guias = localStorage.getItem('jarvis.mps.guias') === '1';
    S.encaje = localStorage.getItem('jarvis.mps.encaje') !== '0';   // default ON
  } catch { /* privado */ }
  // Pools por proyecto (patrón del Web Preview): al cambiar de proyecto, el
  // saliente se ESTACIONA con sus nodos DOM vivos (iframes conectados, ocultos)
  // y al volver reaparece al instante, sin reconectar el Expo ni recargar nada.
  // Los pools mueren solo al recargar la página.
  const _pools = {};
  let _recienRestaurado = false;

  // ── Ciclo de vida / montaje ────────────────────────────────────────────────
  function init(projectId) {
    if (_projectId != null && String(_projectId) !== String(projectId)) {
      // Cambio de proyecto: ESTACIONAR el saliente (url + teléfonos + cards +
      // vista del board) con sus nodos DOM vivos pero ocultos — volver es
      // instantáneo, sin reconectar. El inspector/logs sí se descartan (baratos).
      _pools[_projectId] = {
        url: _url, estadoDet: _estadoDet, ruta: _ruta,
        phones: S.phones, sel: S.sel, webs: S.webs, notas: S.notas,
        board: { zoom: B.zoom, panX: B.panX, panY: B.panY },
      };
      _detener();
      _parquearNodos();
      _url = null; _estadoDet = ''; _ruta = '/'; _inspectFetch = false; _lastBackend = null;
      _textos = []; _selIdx = -1;
      S.phones = []; S.sel = null; S.webs = []; S.notas = []; _logs.length = 0;
    }
    _projectId = projectId;
    const pool = _pools[_projectId];
    if (pool) {
      delete _pools[_projectId];
      _url = pool.url; _estadoDet = pool.estadoDet; _ruta = pool.ruta;
      S.phones = pool.phones; S.sel = pool.sel; S.webs = pool.webs || []; S.notas = pool.notas || [];
      B.zoom = pool.board.zoom; B.panX = pool.board.panX; B.panY = pool.board.panY;
      _recienRestaurado = true;   // abrir() NO debe recargar los frames restaurados
      const ri = $('mps-route'); if (ri) ri.value = _ruta;
      _applyBoard();
      _setConn(_url ? 'ok' : 'wait');
    }
    _restaurarWebs();
    _cargarNotas();
    const panel = $('mobile-preview-panel');
    if (panel && !panel.dataset.montado) {
      panel.dataset.montado = '1';
      _montar(panel);
    } else if (panel) {
      _renderPhones();
    }
  }

  function _montar(panel) {
    panel.setAttribute('data-conn', 'off');
    panel.innerHTML = `
      <header class="mps-bar">
        <span class="mps-conn" id="mps-conn" title="Estado del preview"><span class="pip"></span><span class="lbl" id="mps-conn-lbl">Detenido</span></span>
        <label class="mps-route" title="Ruta de la app — Enter para navegar (ej: /perfil)">
          <span class="globe" aria-hidden="true">${_svgGlobe()}</span>
          <input id="mps-route" value="/" placeholder="/" spellcheck="false" aria-label="Ruta">
        </label>
        <button class="mps-icobtn" id="mps-reload" title="Recargar" aria-label="Recargar">${_svgReload()}</button>
        <div class="mps-more-wrap">
          <button class="mps-icobtn" id="mps-more" title="Más" aria-label="Más" aria-haspopup="true" aria-expanded="false">${_svgDots()}</button>
          <div class="mps-menu" id="mps-menu" role="menu">
            <div class="mps-menu-h">Vista</div>
            <button class="mps-menu-item" role="menuitem" data-act="add">${_svgTwo()}Agregar teléfono<span class="k">A</span></button>
            <button class="mps-menu-item" role="menuitem" data-act="nota">${_svgNote()}Agregar nota<span class="k">N</span></button>
            <button class="mps-menu-item" role="menuitem" data-act="rotate">${_svgRotate()}Rotar el seleccionado<span class="k">R</span></button>
            <button class="mps-menu-item" role="menuitem" data-act="fit">${_svgFit()}Encuadrar todo<span class="k">F</span></button>
            <button class="mps-menu-item" role="menuitemcheckbox" data-act="encaje">${_svgPhone(15)}Encajar en zonas seguras</button>
            <button class="mps-menu-item" role="menuitemcheckbox" data-act="guides">${_svgTarget()}Zonas seguras</button>
            <div class="mps-menu-sep"></div>
            <button class="mps-menu-item" role="menuitem" data-act="tab">${_svgExt()}Abrir en pestaña</button>
          </div>
        </div>
      </header>
      <div class="mps-backend" id="mps-backend"></div>
      <main class="mps-stage" id="mps-stage" tabindex="-1">
        <div class="mps-board-bg"></div>
        <div class="mps-board" id="mps-board"></div>
        <div class="mps-empty" id="mps-empty">
          <div class="core"><span class="rings"></span><span class="rings r2"></span><span class="rings r3"></span><span class="heart">${_svgPhone()}</span></div>
          <h2>Esperando la señal.</h2>
          <p>Arrancá Metro en una terminal del proyecto y la app aparece acá sola, en el lienzo.</p>
          <div class="cmd"><span class="dolar">$</span> expo start --web</div>
          <div class="steps">
            <span class="st done"><span class="n">1</span> Proyecto Expo</span><span class="arw">→</span>
            <span class="st" id="mps-step2"><span class="n">2</span> Metro --web</span><span class="arw">→</span>
            <span class="st"><span class="n">3</span> Preview vivo</span>
          </div>
        </div>
        <div class="mps-tools" id="mps-tools" role="toolbar" aria-label="Herramientas del lienzo">
          <button class="mps-tl primary" id="mps-add" title="Agregar teléfono (A)">${_svgPlus()}<span class="tl-lbl">Teléfono</span></button>
          <button class="mps-tl" id="mps-addweb" title="Agregar un navegador al lienzo">${_svgGlobe()}<span class="tl-lbl">Web</span></button>
          <button class="mps-tl" id="mps-addnote" title="Agregar una nota del proyecto (N)">${_svgNote()}<span class="tl-lbl">Nota</span></button>
          <span class="mps-tl-sep" aria-hidden="true"></span>
          <button class="mps-tl ico" id="mps-zout" title="Alejar" aria-label="Alejar">${_svgMinus()}</button>
          <button class="mps-tl zv" id="mps-zval" title="Zoom — volver al 100%">100%</button>
          <button class="mps-tl ico" id="mps-zin" title="Acercar" aria-label="Acercar">${_svgPlus()}</button>
          <span class="mps-tl-sep" aria-hidden="true"></span>
          <button class="mps-tl ico" id="mps-fit" title="Encuadrar todo (F)" aria-label="Encuadrar">${_svgFit()}</button>
        </div>
        <div class="mps-gdock" id="mps-gdock">
          <span class="mps-gthumb" id="mps-gthumb" aria-hidden="true"></span>
          <button class="mps-gtab" data-pane="console" title="Consola" aria-label="Consola">${_svgConsole()}<span class="mps-gbadge" id="mps-cbadge"></span></button>
          <button class="mps-gtab" data-pane="net" title="Red / backend" aria-label="Red">${_svgNet()}</button>
          <button class="mps-gtab" data-pane="inspect" title="Inspector — editar textos" aria-label="Inspector">${_svgInspect()}</button>
        </div>
        <div class="mps-toast" id="mps-toast"></div>
        <div class="mps-flash" id="mps-flash"></div>
      </main>
      <footer class="mps-ipanel" id="mps-ipanel">
        <div class="mps-ihead"><span class="mps-ititle" id="mps-ititle">Consola</span><button class="mps-ix" id="mps-ix" title="Cerrar" aria-label="Cerrar">${_svgX()}</button></div>
        <div class="mps-ibody">
          <div class="mps-pane" data-pane="console" id="mps-pane-console"></div>
          <div class="mps-pane" data-pane="net" id="mps-pane-net"></div>
          <div class="mps-pane" data-pane="inspect" id="mps-pane-inspect"></div>
        </div>
      </footer>
      <div class="mps-scrim" id="mps-scrim"></div>
      <div class="mps-sheet" id="mps-sheet" role="dialog" aria-label="Elegir dispositivo"><h3>Dispositivo</h3><div class="mps-devgrid" id="mps-devgrid"></div></div>
      <div class="mps-ctx" id="mps-ctx" role="menu" aria-label="Opciones del teléfono"></div>`;

    _wireBar();
    _wireStage();
    _wireDock();
    _wireSheet();
    _renderPhones();

    // Browse nativo: la URL real de cada card (clicks dentro del sitio) vuelve
    // por acá — reflejarla en la barrita de la card y persistirla.
    if (_nativoWeb()) {
      window.NativeBrowse.onEstado((e) => {
        if (!e || !e.clave || !e.url) return;
        const pref = `mps-${_projectId}-`;
        if (!String(e.clave).startsWith(pref)) return;
        const id = Number(String(e.clave).slice(pref.length));
        const w = S.webs.find((x) => x.id === id);
        if (!w) return;
        if (w.url !== e.url) { w.url = e.url; _guardarWebs(); }
        const node = $('mps-board') && $('mps-board').querySelector(`.mps-web[data-id="${id}"]`);
        if (!node) return;
        const input = node.querySelector('.mps-wurl');
        if (input && document.activeElement !== input) input.value = e.url;
        _placeholderWeb(w, node);   // el dominio del placeholder sigue a la navegación
      });
    }
  }

  // ── Barra ───────────────────────────────────────────────────────────────────
  function _wireBar() {
    $('mps-route').addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      let v = e.target.value.trim();
      if (!v.startsWith('/')) v = '/' + v;
      _ruta = v; e.target.value = v;
      _refreshFrames(true);
    });
    $('mps-reload').addEventListener('click', () => _reloadFrames());
    const more = $('mps-more'), menu = $('mps-menu');
    more.addEventListener('click', (e) => {
      e.stopPropagation();
      const open = menu.classList.toggle('open');
      more.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    menu.addEventListener('click', (e) => {
      const it = e.target.closest('[data-act]'); if (!it) return;
      menu.classList.remove('open'); more.setAttribute('aria-expanded', 'false');
      const act = it.dataset.act;
      if (act === 'add') _addPhone();
      else if (act === 'nota') _addNota();
      else if (act === 'rotate') _rotate(S.sel);
      else if (act === 'fit') _fit();
      else if (act === 'guides') {
        S.guias = !S.guias;
        try { localStorage.setItem('jarvis.mps.guias', S.guias ? '1' : ''); } catch { /* privado */ }
        _renderPhones(); _syncChecks();
      }
      else if (act === 'encaje') {
        S.encaje = !S.encaje;
        try { localStorage.setItem('jarvis.mps.encaje', S.encaje ? '1' : '0'); } catch { /* privado */ }
        _renderPhones(); _syncChecks();
      }
      else if (act === 'tab') { const u = _urlDirecta(); if (u) window.open(u, '_blank', 'noopener'); }
    });
    document.addEventListener('click', () => { menu.classList.remove('open'); more.setAttribute('aria-expanded', 'false'); });
    _syncChecks();
  }

  // Estado visible de los toggles del menú ⋯ (dot de acento vía CSS).
  function _syncChecks() {
    const menu = $('mps-menu'); if (!menu) return;
    const set = (act, on) => { const it = menu.querySelector(`[data-act="${act}"]`); if (it) it.setAttribute('aria-checked', on ? 'true' : 'false'); };
    set('guides', S.guias); set('encaje', S.encaje);
  }

  // ── Lienzo: pan / zoom / drag de teléfonos ──────────────────────────────────
  function _wireStage() {
    const stage = $('mps-stage');
    $('mps-add').addEventListener('click', _addPhone);
    $('mps-addweb').addEventListener('click', () => _addWeb());
    $('mps-addnote').addEventListener('click', () => _addNota());
    $('mps-fit').addEventListener('click', _fit);
    if (stage && typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(() => { if (_fitAlVer) _fit(); }).observe(stage);
    }
    $('mps-zin').addEventListener('click', () => _zoomStep(1.2));
    $('mps-zout').addEventListener('click', () => _zoomStep(1 / 1.2));
    $('mps-zval').addEventListener('click', () => { B.zoom = 1; _applyBoard(); });

    // PAN: arrastrar el vacío del lienzo (no sobre teléfono/card/controles).
    stage.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      if (e.target.closest('.mps-phone, .mps-web, .mps-note, .mps-tools, .mps-gdock, .mps-ipanel, .mps-sheet, .mps-ctx, .mps-scrim')) return;
      _startPan(e);
    });
    // ZOOM: rueda sobre el lienzo (los iframes se comen su propia rueda → scrollean la app).
    stage.addEventListener('wheel', (e) => {
      if (e.target.closest('.mps-tools, .mps-gdock, .mps-ipanel')) return;
      e.preventDefault();
      _gestoZoomTick();   // ráfaga de zoom = gesto: webviews nativos congelados
      const r = stage.getBoundingClientRect();
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      const nb = P().zoomAt(B.zoom, B.panX, B.panY, factor, e.clientX - r.left, e.clientY - r.top, ZMIN, ZMAX);
      B.zoom = nb.zoom; B.panX = nb.panX; B.panY = nb.panY; _applyBoard();
    }, { passive: false });

    // Atajos del lienzo (A/R/F/N, los que anuncia el menú ⋯). El stage es
    // focusable solo por código (tabindex -1, fuera del orden de tabulación):
    // se enfoca al tocar el lienzo, así las teclas NUNCA le roban el tipeo a
    // una terminal ni a los campos de una nota.
    stage.addEventListener('pointerdown', (e) => {
      if (e.target.closest('input, textarea, .mps-ipanel')) return;
      stage.focus({ preventScroll: true });
    });
    stage.addEventListener('keydown', (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      const k = (e.key || '').toLowerCase();
      if (k === 'n') { e.preventDefault(); _addNota(); }
      else if (k === 'a') { e.preventDefault(); _addPhone(); }
      else if (k === 'r') { e.preventDefault(); _rotate(S.sel); }
      else if (k === 'f') { e.preventDefault(); _fit(); }
    });

    // cerrar menú contextual al hacer click/scroll afuera
    document.addEventListener('pointerdown', (e) => {
      const ctx = $('mps-ctx');
      if (ctx && ctx.classList.contains('show') && !ctx.contains(e.target)) _hideCtx();
    });
  }

  let _pan = null;
  function _startPan(e) {
    const stage = $('mps-stage');
    _pan = { x: e.clientX, y: e.clientY, px: B.panX, py: B.panY };
    stage.classList.add('panning');
    _gestoNativo(true);
    window.addEventListener('pointermove', _onPan);
    window.addEventListener('pointerup', _endPan);
    window.addEventListener('pointercancel', _endPan);
  }
  function _onPan(e) {
    if (!_pan) return;
    // deltas a píxeles CSS (el transform del lienzo se lee en esa unidad; con la
    // Escala de la app puesta, el pan se iba del cursor)
    const _ze = (window.JarvisEscala && window.JarvisEscala.zoom) ? window.JarvisEscala.zoom() : 1;
    B.panX = _pan.px + (e.clientX - _pan.x) / _ze;
    B.panY = _pan.py + (e.clientY - _pan.y) / _ze;
    _applyBoard();
  }
  function _endPan() {
    _pan = null;
    $('mps-stage').classList.remove('panning');
    _gestoNativo(false);
    window.removeEventListener('pointermove', _onPan);
    window.removeEventListener('pointerup', _endPan);
    window.removeEventListener('pointercancel', _endPan);
  }

  function _zoomStep(factor) {
    const stage = $('mps-stage'), r = stage.getBoundingClientRect();
    const nb = P().zoomAt(B.zoom, B.panX, B.panY, factor, r.width / 2, r.height / 2, ZMIN, ZMAX);
    B.zoom = nb.zoom; B.panX = nb.panX; B.panY = nb.panY; _applyBoard();
  }

  function _applyBoard() {
    const board = $('mps-board');
    if (board) board.style.transform = P().transformBoard(B.panX, B.panY, B.zoom);
    const zv = $('mps-zval'); if (zv) zv.textContent = Math.round(B.zoom * 100) + '%';
    // Los webviews nativos de las cards flotan sobre el DOM: al pan/zoomear el
    // board hay que re-sincronizar sus bounds YA (sin esperar el tick de 150ms).
    if (window.NativeBrowse && window.NativeBrowse.sincronizar) window.NativeBrowse.sincronizar();
  }

  // encuadra todo (teléfonos + cards web) en el viewport del stage
  function _fit() {
    const stage = $('mps-stage'); if (!stage) return;
    const r = stage.getBoundingClientRect();
    if (!P().viewportListo(r.width, r.height)) {
      _fitAlVer = true;
      return;
    }
    _fitAlVer = false;
    const boxes = S.phones.map((p) => { const o = _outer(p); return { x: p.x, y: p.y, w: o.w, h: o.h }; })
      .concat(S.webs.map((w) => ({ x: w.x, y: w.y, w: w.w, h: w.h })))
      .concat(S.notas.map((n) => ({ x: n.x, y: n.y, w: n.w, h: n.h })));
    if (!boxes.length) return;
    const f = P().fitAll(boxes, r.width, r.height, 56, 1);
    B.zoom = f.zoom; B.panX = f.panX; B.panY = f.panY; _applyBoard();
  }

  // ── Geometría de un teléfono ────────────────────────────────────────────────
  function _screenDims(p) {
    return DC().dims(p.dev, !!p.landscape);
  }
  // Sin chasis (2026-07-19): el teléfono ES su pantalla, así que la caja
  // exterior mide exactamente la pantalla — el bezel ya no suma nada. De esto
  // dependen el encuadre (_fit), dónde nace el próximo teléfono y la esquina
  // donde se ancla el handle de resize.
  function _outer(p) {
    const s = _screenDims(p);
    return { w: s.w * p.ps, h: s.h * p.ps };
  }

  // ── Render de teléfonos (reconcilia sin recrear iframes) ────────────────────
  // Oculta TODOS los nodos del board (se llama al estacionar un proyecto: sus
  // iframes siguen vivos y conectados, solo dejan de verse).
  function _parquearNodos() {
    const board = $('mps-board'); if (!board) return;
    Array.from(board.children).forEach((n) => { n.hidden = true; });
  }

  function _renderPhones() {
    const board = $('mps-board'); if (!board) return;
    const empty = $('mps-empty');
    const hayApp = !!_url;
    // El empty tapa el board entero (z-index 12). Si el usuario ya puso un
    // teléfono a mano (sin Metro), tiene que verse: si no, suma 4 fantasmas
    // y solo ve "Máximo 4 teléfonos".
    if (empty) empty.style.display = P().lienzoMuestraVacio(S.phones.length, S.webs.length, S.notas.length, hayApp) ? '' : 'none';
    // Limpieza SOLO de nodos del proyecto actual; los de otros proyectos están
    // ESTACIONADOS (pool): quedan en el DOM, ocultos, con sus iframes vivos.
    // Los ids de las notas vienen del SERVER (otra secuencia que la de phones/webs):
    // se prefijan con «n» para que no choquen en este set de limpieza.
    const vivos = new Set(S.phones.map((p) => String(p.id))
      .concat(S.webs.map((w) => String(w.id)))
      .concat(S.notas.map((n) => 'n' + n.id)));
    const pidStr = String(_projectId);
    Array.from(board.children).forEach((n) => {
      if (n.dataset.pid !== pidStr) { n.hidden = true; return; }
      if (!vivos.has(n.dataset.id)) n.remove();
    });
    // Cards web y notas viven con o sin Metro. Los teléfonos también: sin
    // app el marco se pinta vacío (pantalla oscura) y se llena al detectar.
    S.webs.forEach((w) => _layoutWeb(w, _ensureWeb(w)));
    S.notas.forEach((n) => _layoutNota(n, _ensureNota(n)));
    S.phones.forEach((p) => _layoutPhone(p, _ensurePhone(p)));
  }

  function _ensurePhone(p) {
    const board = $('mps-board');
    let node = board.querySelector(`.mps-phone[data-id="${p.id}"]`);
    if (node) return node;
    const d = DEVS[p.dev] || DEVS.ip15p;
    node = document.createElement('div');
    node.className = 'mps-phone';
    node.dataset.id = String(p.id);
    node.dataset.pid = String(_projectId);   // dueño (los pools estacionan por proyecto)
    node.innerHTML = `
      <span class="mps-glow"></span><span class="mps-grab" aria-hidden="true"></span>
      <div class="mps-pframe">
        <div class="mps-frame">
          <div class="mps-screen">
            <iframe class="mps-iframe" title="Preview móvil" loading="lazy"></iframe>
            <span class="mps-band" aria-hidden="true"></span>
            <div class="mps-offline">
              <div class="off-ic">${_svgWifiOff()}</div>
              <b>Sin conexión</b>
              <p>Este teléfono está sin internet — probá cómo responde tu app.</p>
              <button class="off-retry">Reintentar</button>
            </div>
            <span class="mps-wifi-off" title="Sin conexión">${_svgWifiOff(13)}</span>
            <span class="mps-cut"></span>
            <div class="mps-status" aria-hidden="true"><span class="st-t">9:41</span><span class="st-ics">${_svgSignal()}${_svgWifi(15)}${_svgBatt()}</span></div>
            <span class="mps-homebar"></span>
            <div class="mps-guides" aria-hidden="true" hidden></div>
            <span class="mps-scanline"></span>
          </div>
        </div>
      </div>
      <div class="mps-cap"><span class="grip">${_svgGrip()}</span><button class="mps-cap-main">${_esc(d.nombre)} <span class="ch">${_svgChevron()}</span></button><button class="mps-cap-x" title="Quitar">${_svgX(12)}</button></div>
      <div class="mps-dims"></div>
      <div class="mps-handle" title="Redimensionar"></div>`;
    board.appendChild(node);
    _wirePhone(p, node);
    // Red de seguridad del escape (si el bye no llegó a tiempo y una página de
    // Jarvis SE CARGÓ adentro): el sampler se marca con window.__mpsSampler —
    // same-origin, legible desde acá. Documento same-origin sin la marca =
    // dashboard/login/error adentro del teléfono → volver al espejo. El
    // umbral de propioTs evita el loop si el propio sampler responde error
    // (502 con Metro caído): esa carga es reciente y se deja quieta — el
    // poller de detección la resuelve. Cross-origin (Metro directo) no se toca.
    const ifr0 = node.querySelector('.mps-iframe');
    ifr0.addEventListener('load', () => {
      let esSampler;
      try { esSampler = !!ifr0.contentWindow.__mpsSampler; } catch { return; }
      if (esSampler || !ifr0.dataset.src) return;
      if (Date.now() - (Number(ifr0.dataset.propioTs) || 0) < 3000) return;
      _apuntar(ifr0, ifr0.dataset.src);
    });
    return node;
  }

  function _layoutPhone(p, node) {
    const d = DEVS[p.dev] || DEVS.ip15p, s = _screenDims(p), o = _outer(p);
    node.hidden = false;   // vuelve del estacionamiento (pool) si estaba oculto
    node.style.left = p.x + 'px'; node.style.top = p.y + 'px';
    node.style.width = o.w + 'px'; node.style.height = o.h + 'px';
    node.classList.toggle('sel', S.sel === p.id);
    node.dataset.net = p.net || 'on';   // 'off' → overlay "Sin conexión" (CSS)
    const pf = node.querySelector('.mps-pframe');
    pf.style.transform = `scale(${p.ps})`;
    // El radio de pantalla vive en el NODO (no en .mps-screen): sin chasis, el
    // halo de selección y el anillo de agarre lo heredan para curvarse igual
    // que el vidrio.
    node.style.setProperty('--dev-sr', d.radio + 'px');
    const screen = node.querySelector('.mps-screen');
    screen.style.setProperty('--dev-w', s.w + 'px');
    screen.style.setProperty('--dev-h', s.h + 'px');
    // Recorte (isla / notch / punch) con la geometría REAL del device, 1:1.
    // En landscape rota al borde izquierdo, como al girar el aparato.
    const cut = node.querySelector('.mps-cut');
    const cb = DC().cutoutBox(p.dev, !!p.landscape);
    if (cb) {
      cut.style.display = 'block';
      cut.dataset.tipo = cb.tipo;
      cut.style.left = cb.x + 'px'; cut.style.top = cb.y + 'px';
      cut.style.width = cb.w + 'px'; cut.style.height = cb.h + 'px';
    } else { cut.style.display = 'none'; }
    // Status bar del SO — OVERLAY (el contenido pasa por debajo, igual que una
    // app edge-to-edge real). Con recorte, hora e íconos viven en las "orejas".
    const st = node.querySelector('.mps-status');
    const sb = DC().statusBarBox(p.dev, !!p.landscape);
    if (sb) {
      st.style.display = '';
      st.style.height = sb.h + 'px';
      const conRecorte = !!(cb && !p.landscape);
      st.classList.toggle('con-recorte', conRecorte);
      st.style.setProperty('--ear', conRecorte ? ((s.w - cb.w) / 2) + 'px' : '0px');
      st.dataset.marca = d.marca;
    } else { st.style.display = 'none'; }
    // Home indicator con medidas reales (más ancho apaisado, como iOS).
    const hb = node.querySelector('.mps-homebar');
    const hbb = DC().homebarBox(p.dev, !!p.landscape);
    if (hbb) {
      hb.style.display = 'block';   // la base CSS es display:none — '' no alcanza
      hb.style.left = hbb.x + 'px'; hb.style.top = hbb.y + 'px';
      hb.style.width = hbb.w + 'px'; hb.style.height = hbb.h + 'px';
    } else { hb.style.display = 'none'; }
    // Guías de zonas seguras (toggle del menú ⋯): sombrea lo que el sistema
    // tapa/reserva en el device real, con su medida en pt.
    const g = node.querySelector('.mps-guides');
    if (S.guias) {
      g.hidden = false;
      g.innerHTML = DC().safeZones(p.dev, !!p.landscape).map((z) =>
        `<i style="left:${z.x}px;top:${z.y}px;width:${z.w}px;height:${z.h}px" data-lado="${z.lado}"><b>${Math.round(z.lado === 'izq' || z.lado === 'der' ? z.w : z.h)}pt</b></i>`).join('');
    } else { g.hidden = true; g.innerHTML = ''; }
    node.querySelector('.mps-cap-main').firstChild.textContent = d.nombre + ' ';
    node.querySelector('.mps-dims').textContent = `${s.w}×${s.h} · @${DC().dprLabel(p.dev)}x${p.ps !== 1 ? '  ·  ' + Math.round(p.ps * 100) + '%' : ''}`;
    _pintarColores(node, p.colores || null);   // colores vivos del sampler (por teléfono)
    // ENCAJE en zonas seguras (default ON): la app web ve env(safe-area)=0,
    // así que acá recibe el área de CONTENIDO real del sistema (misma
    // geometría que SafeAreaView en la mano) y la franja de abajo se pinta
    // del color muestreado de la app → fondo continuo bajo isla y home bar.
    const ifr = node.querySelector('.mps-iframe');
    const ins = S.encaje ? DC().insets(p.dev, !!p.landscape) : { top: 0, right: 0, bottom: 0, left: 0 };
    ifr.style.left = ins.left + 'px'; ifr.style.top = ins.top + 'px';
    ifr.style.width = (s.w - ins.left - ins.right) + 'px';
    ifr.style.height = (s.h - ins.top - ins.bottom) + 'px';
    const band = node.querySelector('.mps-band');
    band.style.display = (S.encaje && ins.bottom) ? 'block' : 'none';
    band.style.height = ins.bottom + 'px';
    // iframe: setear src solo si cambió (no recargar la app en cada relayout)
    const destino = _urlConRuta();
    if (destino && ifr.dataset.src !== destino) _apuntar(ifr, destino);
  }

  // ── Interacción por teléfono: seleccionar, arrastrar, redimensionar ─────────
  function _wirePhone(p, node) {
    const select = () => { if (S.sel !== p.id) { S.sel = p.id; _renderPhones(); } };
    // Drag por el anillo de agarre / cap / dims (NUNCA por la pantalla → el
    // iframe queda libre). .mps-grab es el margen invisible alrededor del
    // vidrio: reemplaza al bezel de ~11px que antes era la única zona de
    // agarre y que se erraba la mitad de las veces.
    node.querySelectorAll('.mps-grab, .mps-frame, .mps-cap, .mps-dims').forEach((h) => {
      h.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        if (e.target.closest('.mps-cap-x, .mps-cap-main, .mps-handle')) return;
        e.preventDefault(); select(); _startDrag(p, e);
      });
    });
    node.querySelector('.mps-handle').addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return; e.preventDefault(); e.stopPropagation(); select(); _startResize(p, e);
    });
    node.querySelector('.mps-cap-x').addEventListener('click', (e) => { e.stopPropagation(); _removePhone(p.id); });
    node.querySelector('.mps-cap-main').addEventListener('click', (e) => { e.stopPropagation(); select(); _openSheet(p.id); });
    node.querySelector('.off-retry').addEventListener('click', (e) => { e.stopPropagation(); _setNet(p.id, 'on'); });
    node.addEventListener('contextmenu', (e) => { e.preventDefault(); e.stopPropagation(); select(); _openCtx(p.id, e.clientX, e.clientY); });
  }

  let _drag = null;
  function _startDrag(p, e) {
    _drag = { p, x: e.clientX, y: e.clientY, px: p.x, py: p.y };
    $('mps-stage').classList.add('dragging');
    _iframesInteractivos(false);
    window.addEventListener('pointermove', _onDrag);
    window.addEventListener('pointerup', _endDrag);
    window.addEventListener('pointercancel', _endDrag);
  }
  function _onDrag(e) {
    if (!_drag) return;
    _drag.p.x = _drag.px + (e.clientX - _drag.x) / B.zoom;
    _drag.p.y = _drag.py + (e.clientY - _drag.y) / B.zoom;
    const node = $('mps-board').querySelector(`.mps-phone[data-id="${_drag.p.id}"]`);
    if (node) { node.style.left = _drag.p.x + 'px'; node.style.top = _drag.p.y + 'px'; }
  }
  function _endDrag() {
    _drag = null;
    $('mps-stage').classList.remove('dragging');
    _iframesInteractivos(true);
    window.removeEventListener('pointermove', _onDrag);
    window.removeEventListener('pointerup', _endDrag);
    window.removeEventListener('pointercancel', _endDrag);
  }

  let _resize = null;
  function _startResize(p, e) {
    _resize = { p, y: e.clientY, ps: p.ps };
    _iframesInteractivos(false);
    window.addEventListener('pointermove', _onResize);
    window.addEventListener('pointerup', _endResize);
    window.addEventListener('pointercancel', _endResize);
  }
  function _onResize(e) {
    if (!_resize) return;
    const dps = (e.clientY - _resize.y) / 260;   // 260px de arrastre = ×1 de escala
    _resize.p.ps = Math.max(0.4, Math.min(2, _resize.ps + dps));
    _layoutPhone(_resize.p, $('mps-board').querySelector(`.mps-phone[data-id="${_resize.p.id}"]`));
  }
  function _endResize() {
    _resize = null; _iframesInteractivos(true);
    window.removeEventListener('pointermove', _onResize);
    window.removeEventListener('pointerup', _endResize);
    window.removeEventListener('pointercancel', _endResize);
  }

  function _iframesInteractivos(on) {
    document.querySelectorAll('#mps-board .mps-iframe, #mps-board .mps-wiframe').forEach((f) => { f.style.pointerEvents = on ? '' : 'none'; });
  }

  // ── Cards de NAVEGADOR en el lienzo ─────────────────────────────────────────
  // Una card web = navegador REAL sobre el board: en la app de escritorio usa el
  // Browse nativo (NativeBrowse — el webview hijo sigue el rect del hueco,
  // pan/zoom incluidos); en browser normal cae a un iframe best-effort (los
  // sitios que bloquean el embebido solo se ven en la app). Persisten por
  // proyecto en localStorage con id ESTABLE: la clave del pane no cambia y al
  // volver al proyecto el sitio reaparece intacto, sin recargar.
  const _nativoWeb = () => !!(window.NativeBrowse && window.NativeBrowse.disponible);
  // Gesto continuo del lienzo (pan/zoom/drag/resize): mientras dura, los
  // webviews nativos se OCULTAN (una ventana del OS no puede seguir un canvas
  // a 60fps sin despegarse/cortarse) y al soltar se re-montan en su lugar.
  const _gestoNativo = (on) => {
    const cb = window.NativeBrowse;
    if (!cb || !cb.disponible || !cb.gestoInicio) return;
    if (on) cb.gestoInicio(); else cb.gestoFin();
  };
  let _zoomGestoTimer = null;
  // La rueda no tiene "fin de gesto": ráfaga → gesto ON, 200ms sin rueda → OFF.
  function _gestoZoomTick() {
    _gestoNativo(true);
    if (_zoomGestoTimer) clearTimeout(_zoomGestoTimer);
    _zoomGestoTimer = setTimeout(() => { _zoomGestoTimer = null; _gestoNativo(false); }, 200);
  }
  const _claveWeb = (w) => `mps-${_projectId}-${w.id}`;
  const _lsWebs = () => `jarvis_mps_webs_${_projectId}`;

  function _guardarWebs() {
    if (_projectId == null) return;
    try { localStorage.setItem(_lsWebs(), JSON.stringify(S.webs)); } catch { /* quota */ }
  }

  function _restaurarWebs() {
    if (_projectId == null || S.webs.length) return;
    let data = null;
    try { data = JSON.parse(localStorage.getItem(_lsWebs()) || 'null'); } catch { /* basura */ }
    if (!Array.isArray(data)) return;
    S.webs = data.map((w) => P().webSaneada(w)).filter(Boolean).slice(0, MAX_WEBS);
    for (const w of S.webs) _seq = Math.max(_seq, w.id + 1);
  }

  function _addWeb(url) {
    if (S.webs.length >= MAX_WEBS) { _toast(_t('Máximo {n} navegadores').replace('{n}', MAX_WEBS)); return; }
    const w = P().webNueva(_seq++, S.phones.map((p) => ({ x: p.x, y: p.y, w: _outer(p).w, h: _outer(p).h })).concat(S.webs));
    if (url) w.url = url;
    S.webs.push(w);
    _guardarWebs();
    _renderPhones();
    _fit();
    const node = $('mps-board').querySelector(`.mps-web[data-id="${w.id}"]`);
    if (node && !w.url) node.querySelector('.mps-wurl').focus();
  }

  function _removeWeb(id) {
    const w = S.webs.find((x) => x.id === id);
    if (w && _nativoWeb()) window.NativeBrowse.cerrar(_claveWeb(w));
    S.webs = S.webs.filter((x) => x.id !== id);
    _guardarWebs();
    _renderPhones();
  }

  function _ensureWeb(w) {
    const board = $('mps-board');
    let node = board.querySelector(`.mps-web[data-id="${w.id}"]`);
    if (node) return node;
    node = document.createElement('div');
    node.className = 'mps-web';
    node.dataset.id = String(w.id);
    node.dataset.pid = String(_projectId);   // dueño (los pools estacionan por proyecto)
    node.innerHTML = `
      <div class="mps-wcap">
        <span class="grip">${_svgGrip()}</span>
        <span class="wglobe">${_svgGlobe()}</span>
        <input class="mps-wurl" placeholder="claude.ai, github.com…" spellcheck="false" aria-label="URL">
        <button class="mps-wbtn wre" title="Recargar" aria-label="Recargar">${_svgReload()}</button>
        <button class="mps-wbtn wx" title="Quitar" aria-label="Quitar">${_svgX(12)}</button>
      </div>
      <div class="mps-wmid">
        <div class="mps-wrail l"></div>
        <div class="mps-wbody">
          <div class="mps-whueco" hidden></div>
          <div class="mps-wvacio">${_svgGlobe()}<p>Escribí una URL arriba y Enter</p></div>
        </div>
        <div class="mps-wrail r"></div>
      </div>
      <div class="mps-wfoot"><span class="grip">${_svgGrip()}</span></div>
      <div class="mps-whandle" title="Redimensionar"></div>`;
    board.appendChild(node);
    _wireWeb(w, node);
    if (w.url) _cargarWeb(w, node);
    return node;
  }

  function _layoutWeb(w, node) {
    node.hidden = false;   // vuelve del estacionamiento (pool) si estaba oculto
    node.style.left = w.x + 'px'; node.style.top = w.y + 'px';
    node.style.width = w.w + 'px'; node.style.height = w.h + 'px';
    const input = node.querySelector('.mps-wurl');
    if (input && document.activeElement !== input) input.value = w.url || '';
  }

  // El contenido del hueco se ve EXACTAMENTE cuando el webview nativo está
  // oculto (gesto de pan/zoom/resize, oclusión): un placeholder con el globo y
  // el dominio hace que el congelado se lea como intencional, no como "se puso
  // en negro" (feedback del usuario 2026-07-07).
  function _placeholderWeb(w, node) {
    const hueco = node.querySelector('.mps-whueco');
    if (!hueco) return;
    let host = '';
    try { host = new URL(w.url).hostname; } catch { /* url a medias */ }
    hueco.innerHTML = `<div class="mps-wfrio">${_svgGlobe()}<span>${_esc(host)}</span></div>`;
  }

  function _cargarWeb(w, node) {
    node.querySelector('.mps-wvacio').hidden = true;
    if (_nativoWeb()) {
      const hueco = node.querySelector('.mps-whueco');
      hueco.hidden = false;
      _placeholderWeb(w, node);
      window.NativeBrowse.abrir(_claveWeb(w), w.url, hueco);
      return;
    }
    // Browser normal: iframe best-effort (sitios que bloquean → solo en Jarvis).
    let ifr = node.querySelector('.mps-wiframe');
    if (!ifr) {
      ifr = document.createElement('iframe');
      ifr.className = 'mps-wiframe';
      ifr.title = 'Navegador';
      ifr.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms allow-popups allow-modals');
      ifr.setAttribute('referrerpolicy', 'no-referrer');
      node.querySelector('.mps-wbody').appendChild(ifr);
    }
    if (ifr.src !== w.url) ifr.src = w.url;
  }

  function _navegarWeb(w, node, input) {
    const norm = P().normalizarUrlWeb(input);
    if (!norm) { _toast('Eso no parece una URL'); return; }
    w.url = norm;
    _guardarWebs();
    _cargarWeb(w, node);
  }

  function _wireWeb(w, node) {
    // MOVER: por la barra superior, los rieles laterales y el pie — todo el
    // MARCO agarra (nunca el cuerpo: ahí vive el sitio). REDIMENSIONAR: la
    // manija de la esquina inferior-derecha (patrón de los teléfonos).
    node.querySelectorAll('.mps-wcap, .mps-wrail, .mps-wfoot').forEach((h) => {
      h.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        if (e.target.closest('.mps-wurl, .mps-wbtn, .mps-whandle')) return;
        e.preventDefault(); _startDragWeb(w, e);
      });
    });
    node.querySelector('.mps-whandle').addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return; e.preventDefault(); e.stopPropagation(); _startResizeWeb(w, e);
    });
    const input = node.querySelector('.mps-wurl');
    input.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      _navegarWeb(w, node, input.value);
      input.blur();
    });
    node.querySelector('.wre').addEventListener('click', (e) => {
      e.stopPropagation();
      if (_nativoWeb()) { window.NativeBrowse.recargar(_claveWeb(w)); return; }
      const ifr = node.querySelector('.mps-wiframe');
      if (ifr && w.url) { const sep = w.url.includes('?') ? '&' : '?'; ifr.src = `${w.url}${sep}_mps=${Date.now()}`; }
    });
    node.querySelector('.wx').addEventListener('click', (e) => { e.stopPropagation(); _removeWeb(w.id); });
  }

  let _dragW = null;
  function _startDragWeb(w, e) {
    _dragW = { w, x: e.clientX, y: e.clientY, px: w.x, py: w.y };
    $('mps-stage').classList.add('dragging');
    _iframesInteractivos(false);
    _gestoNativo(true);
    window.addEventListener('pointermove', _onDragWeb);
    window.addEventListener('pointerup', _endDragWeb);
    window.addEventListener('pointercancel', _endDragWeb);
  }
  function _onDragWeb(e) {
    if (!_dragW) return;
    _dragW.w.x = _dragW.px + (e.clientX - _dragW.x) / B.zoom;
    _dragW.w.y = _dragW.py + (e.clientY - _dragW.y) / B.zoom;
    const node = $('mps-board').querySelector(`.mps-web[data-id="${_dragW.w.id}"]`);
    if (node) { node.style.left = _dragW.w.x + 'px'; node.style.top = _dragW.w.y + 'px'; }
  }
  function _endDragWeb() {
    _dragW = null;
    $('mps-stage').classList.remove('dragging');
    _iframesInteractivos(true);
    _gestoNativo(false);
    _guardarWebs();
    window.removeEventListener('pointermove', _onDragWeb);
    window.removeEventListener('pointerup', _endDragWeb);
    window.removeEventListener('pointercancel', _endDragWeb);
  }

  let _resizeW = null;
  function _startResizeWeb(w, e) {
    _resizeW = { w, x: e.clientX, y: e.clientY, pw: w.w, ph: w.h };
    _iframesInteractivos(false);
    _gestoNativo(true);
    window.addEventListener('pointermove', _onResizeWeb);
    window.addEventListener('pointerup', _endResizeWeb);
    window.addEventListener('pointercancel', _endResizeWeb);
  }
  function _onResizeWeb(e) {
    if (!_resizeW) return;
    _resizeW.w.w = Math.max(320, _resizeW.pw + (e.clientX - _resizeW.x) / B.zoom);
    _resizeW.w.h = Math.max(220, _resizeW.ph + (e.clientY - _resizeW.y) / B.zoom);
    const node = $('mps-board').querySelector(`.mps-web[data-id="${_resizeW.w.id}"]`);
    if (node) _layoutWeb(_resizeW.w, node);
  }
  function _endResizeWeb() {
    _resizeW = null; _iframesInteractivos(true);
    _gestoNativo(false);
    _guardarWebs();
    window.removeEventListener('pointermove', _onResizeWeb);
    window.removeEventListener('pointerup', _endResizeWeb);
    window.removeEventListener('pointercancel', _endResizeWeb);
  }

  // ── NOTAS del proyecto en el lienzo ─────────────────────────────────────────
  // Papeles pegados al board: el saber operativo del proyecto (cuenta de Expo,
  // claves de EAS, comandos, pendientes). Se guardan en la DB LOCAL de Jarvis
  // (data/jarvis.db, gitignoreada) vía /api/mobile-preview/{pid}/notas — nunca
  // en el repo. Una nota marcada 🔒 nace TAPADA (velo) y se destapa a mano; eso
  // es privacidad de pantalla, NO cifrado (no se promete lo que no se hace).
  const _notasUrl = () => `/api/mobile-preview/${_projectId}/notas`;
  const _nota = (id) => S.notas.find((n) => n.id === id);
  const _nodoNota = (id) => {
    const b = $('mps-board');
    return b && b.querySelector(`.mps-note[data-id="n${id}"]`);
  };
  let _notasPedidas = null;      // proyecto cuyas notas ya se pidieron

  async function _cargarNotas(force) {
    if (_projectId == null) return;
    if (!force && _notasPedidas === _projectId) return;
    _notasPedidas = _projectId;
    const pid = _projectId;
    let d = null;
    try { d = await (await fetch(_notasUrl())).json(); }
    catch { if (_notasPedidas === pid) _notasPedidas = null; return; }
    if (pid !== _projectId) return;
    S.notas = (d && Array.isArray(d.notas) ? d.notas : [])
      .map((n) => P().notaSaneada(n)).filter(Boolean);
    _renderPhones();
  }

  async function _addNota() {
    if (S.notas.length >= MAX_NOTAS) { _toast(_t('Máximo {n} notas').replace('{n}', MAX_NOTAS)); return; }
    const base = P().notaNueva(
      S.phones.map((p) => { const o = _outer(p); return { x: p.x, y: p.y, w: o.w, h: o.h }; })
        .concat(S.webs).concat(S.notas));
    const pid = _projectId;
    let fila = null;
    try {
      const r = await fetch(_notasUrl(), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(base),
      });
      if (!r.ok) throw new Error('alta');
      fila = P().notaSaneada(await r.json());
    } catch { _toast('No se pudo crear la nota'); return; }
    if (!fila || pid !== _projectId) return;
    S.notas.push(fila);
    _renderPhones();
    _asegurarVisible(fila);
    const node = _nodoNota(fila.id);
    if (node) { node.classList.add('nueva'); node.querySelector('.mps-ntitle').focus(); }
  }

  async function _removeNota(id) {
    const n = _nota(id); if (!n) return;
    const vacia = !n.titulo.trim() && !n.cuerpo.trim();
    if (!vacia && window.confirmar) {
      const titulo = P().tituloNota(n);
      const ok = await window.confirmar(
        _t('Se elimina «{titulo}» y su contenido. No se puede deshacer.')
          .replace('{titulo}', titulo === 'Nota sin título' ? _t('Nota sin título') : titulo),
        { titulo: 'Eliminar la nota', confirmText: 'Eliminar', peligro: true });
      if (!ok) return;
    }
    const pid = _projectId;
    S.notas = S.notas.filter((x) => x.id !== id);
    _notasAbiertas.delete(id);
    _renderPhones();
    try {
      const r = await fetch(`/api/mobile-preview/${pid}/notas/${id}`, { method: 'DELETE' });
      if (!r.ok) throw new Error('borrado');
    } catch { _toast('No se pudo borrar en el servidor'); _cargarNotas(true); }
  }

  // Guardado con retardo (tipeo) o inmediato (soltar un drag, tocar un toggle).
  const _notaTimers = new Map();
  function _guardarNota(n, inmediato) {
    if (!n) return;
    const pid = _projectId;
    const previo = _notaTimers.get(n.id);
    if (previo) clearTimeout(previo);
    const enviar = async () => {
      _notaTimers.delete(n.id);
      _estadoNota(n.id, 'guardando');
      try {
        const r = await fetch(`/api/mobile-preview/${pid}/notas/${n.id}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            titulo: n.titulo, cuerpo: n.cuerpo, secreta: n.secreta,
            color: n.color, x: n.x, y: n.y, w: n.w, h: n.h,
          }),
        });
        _estadoNota(n.id, r.ok ? 'ok' : 'error');
      } catch { _estadoNota(n.id, 'error'); }
    };
    if (inmediato) enviar();
    else _notaTimers.set(n.id, setTimeout(enviar, 700));
  }

  let _estadoNotaTimers = new Map();
  function _estadoNota(id, estado) {
    const node = _nodoNota(id); if (!node) return;
    const el = node.querySelector('.mps-nsave'); if (!el) return;
    el.dataset.estado = estado;
    el.textContent = estado === 'guardando' ? 'Guardando…' : estado === 'error' ? 'Sin guardar' : 'Guardado';
    const t = _estadoNotaTimers.get(id); if (t) clearTimeout(t);
    if (estado === 'ok') {
      _estadoNotaTimers.set(id, setTimeout(() => {
        const e2 = _nodoNota(id) && _nodoNota(id).querySelector('.mps-nsave');
        if (e2 && e2.dataset.estado === 'ok') e2.dataset.estado = 'idle';
      }, 1800));
    }
  }

  // Trae la caja al viewport y la centra. NO reencuadra todo el lienzo (para eso
  // está «Encuadrar»): el zoom solo BAJA, y solo si la caja no entra —en el dock
  // angosto (~300px) una nota de 320 no entra al 100%— y nunca sube de vuelta.
  function _asegurarVisible(b) {
    const stage = $('mps-stage'); if (!stage) return;
    const r = stage.getBoundingClientRect();
    if (!(r.width > 0) || !(r.height > 0)) return;
    const m = 24;
    const x0 = B.panX + b.x * B.zoom, y0 = B.panY + b.y * B.zoom;
    const x1 = x0 + b.w * B.zoom, y1 = y0 + b.h * B.zoom;
    if (x0 >= m && y0 >= m && x1 <= r.width - m && y1 <= r.height - m) return;
    const cabe = Math.min((r.width - 2 * m) / b.w, (r.height - 2 * m) / b.h);
    if (cabe < B.zoom) B.zoom = P().clampZoom(cabe, ZMIN, ZMAX);
    B.panX = r.width / 2 - (b.x + b.w / 2) * B.zoom;
    B.panY = r.height / 2 - (b.y + b.h / 2) * B.zoom;
    _applyBoard();
  }

  function _ensureNota(n) {
    const board = $('mps-board');
    let node = board.querySelector(`.mps-note[data-id="n${n.id}"]`);
    if (node) return node;
    node = document.createElement('div');
    node.className = 'mps-note';
    node.dataset.id = 'n' + n.id;
    node.dataset.pid = String(_projectId);
    node.innerHTML = `
      <div class="mps-ncap">
        <span class="grip">${_svgGrip()}</span>
        <input class="mps-ntitle" maxlength="200" spellcheck="false" placeholder="Título de la nota" aria-label="Título de la nota">
        <button class="mps-nbtn nlock" title="Proteger: la nota nace tapada" aria-label="Proteger">${_svgLock()}</button>
        <button class="mps-nbtn ncopy" title="Copiar el contenido" aria-label="Copiar">${_svgCopy()}</button>
        <button class="mps-nbtn nx" title="Eliminar la nota" aria-label="Eliminar">${_svgX(12)}</button>
      </div>
      <div class="mps-nbody">
        <textarea class="mps-ntext" spellcheck="false" placeholder="Cuenta de Expo, claves, comandos, pendientes…&#10;&#10;Se guarda solo, en esta máquina."></textarea>
        <div class="mps-nveil">
          <span class="vic">${_svgLock(18)}</span>
          <b>Contenido protegido</b>
          <button class="nveil-btn" type="button">${_svgEye()}Mostrar</button>
        </div>
      </div>
      <div class="mps-nfoot">
        <span class="mps-ntints" role="group" aria-label="Color de la nota">
          ${P().NOTA_COLORES.map((c) => `<button class="mps-ntint" data-c="${c}" title="${_esc(_nombreColor(c))}" aria-label="${_esc(_nombreColor(c))}"></button>`).join('')}
        </span>
        <span class="mps-nsave" data-estado="idle">Guardado</span>
      </div>
      <div class="mps-nhandle" title="Redimensionar"></div>`;
    board.appendChild(node);
    _wireNota(n, node);
    return node;
  }

  function _nombreColor(c) {
    return { papel: 'Papel', ambar: 'Ámbar', violeta: 'Violeta', verde: 'Verde', cian: 'Cian', rosa: 'Rosa' }[c] || c;
  }

  function _layoutNota(n, node) {
    node.hidden = false;
    node.style.left = n.x + 'px'; node.style.top = n.y + 'px';
    node.style.width = n.w + 'px'; node.style.height = n.h + 'px';
    node.dataset.color = n.color;
    node.dataset.secreta = n.secreta ? '1' : '0';
    node.classList.toggle('tapada', !!n.secreta && !_notasAbiertas.has(n.id));
    const t = node.querySelector('.mps-ntitle');
    if (t && document.activeElement !== t) t.value = n.titulo;
    const b = node.querySelector('.mps-ntext');
    if (b && document.activeElement !== b) b.value = n.cuerpo;
    const lock = node.querySelector('.nlock');
    if (lock) {
      lock.classList.toggle('on', !!n.secreta);
      lock.title = n.secreta ? 'Protegida — se tapa al recargar' : 'Proteger: la nota nace tapada';
    }
    node.querySelectorAll('.mps-ntint').forEach((d) => d.classList.toggle('on', d.dataset.c === n.color));
  }

  function _wireNota(n, node) {
    // MOVER: la barra de arriba y el pie (nunca el cuerpo — ahí se escribe).
    node.querySelectorAll('.mps-ncap, .mps-nfoot').forEach((h) => {
      h.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        if (e.target.closest('.mps-ntitle, .mps-nbtn, .mps-ntint, .mps-nhandle')) return;
        e.preventDefault(); _startDragNota(n, e);
      });
    });
    node.querySelector('.mps-nhandle').addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return; e.preventDefault(); e.stopPropagation(); _startResizeNota(n, e);
    });
    // Al tocar la nota pasa al frente (papeles apilados).
    node.addEventListener('pointerdown', () => _alFrenteNota(n.id));

    const title = node.querySelector('.mps-ntitle');
    title.addEventListener('input', () => { n.titulo = title.value; _guardarNota(n); });
    title.addEventListener('blur', () => { if (_notaTimers.has(n.id)) _guardarNota(n, true); });
    title.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); node.querySelector('.mps-ntext').focus(); }
    });

    const body = node.querySelector('.mps-ntext');
    body.addEventListener('input', () => { n.cuerpo = body.value; _guardarNota(n); });
    body.addEventListener('blur', () => { if (_notaTimers.has(n.id)) _guardarNota(n, true); });

    node.querySelector('.nlock').addEventListener('click', (e) => {
      e.stopPropagation();
      n.secreta = n.secreta ? 0 : 1;
      if (n.secreta) _notasAbiertas.delete(n.id); else _notasAbiertas.add(n.id);
      _layoutNota(n, node);
      _guardarNota(n, true);
      _toast(n.secreta ? 'Nota protegida: se tapa al recargar' : 'Nota destapada');
    });
    node.querySelector('.nveil-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      _notasAbiertas.add(n.id);
      _layoutNota(n, node);
      node.querySelector('.mps-ntext').focus();
    });
    node.querySelector('.ncopy').addEventListener('click', async (e) => {
      e.stopPropagation();
      try { await navigator.clipboard.writeText(n.cuerpo || ''); _toast('Contenido copiado'); }
      catch { _toast('El navegador no dejó copiar'); }
    });
    node.querySelector('.nx').addEventListener('click', (e) => { e.stopPropagation(); _removeNota(n.id); });
    node.querySelector('.mps-ntints').addEventListener('click', (e) => {
      const d = e.target.closest('.mps-ntint'); if (!d) return;
      e.stopPropagation();
      n.color = d.dataset.c;
      _layoutNota(n, node);
      _guardarNota(n, true);
    });
  }

  // La nota tocada va arriba de las demás (sin tocar el z de teléfonos/webs).
  let _zNota = 20;
  function _alFrenteNota(id) {
    const node = _nodoNota(id); if (!node) return;
    node.style.zIndex = String(++_zNota);
  }

  let _dragN = null;
  function _startDragNota(n, e) {
    _dragN = { n, x: e.clientX, y: e.clientY, px: n.x, py: n.y };
    $('mps-stage').classList.add('dragging');
    _iframesInteractivos(false);
    _gestoNativo(true);
    window.addEventListener('pointermove', _onDragNota);
    window.addEventListener('pointerup', _endDragNota);
    window.addEventListener('pointercancel', _endDragNota);
  }
  function _onDragNota(e) {
    if (!_dragN) return;
    _dragN.n.x = _dragN.px + (e.clientX - _dragN.x) / B.zoom;
    _dragN.n.y = _dragN.py + (e.clientY - _dragN.y) / B.zoom;
    const node = _nodoNota(_dragN.n.id);
    if (node) { node.style.left = _dragN.n.x + 'px'; node.style.top = _dragN.n.y + 'px'; }
  }
  function _endDragNota() {
    const n = _dragN && _dragN.n;
    _dragN = null;
    $('mps-stage').classList.remove('dragging');
    _iframesInteractivos(true);
    _gestoNativo(false);
    if (n) _guardarNota(n, true);
    window.removeEventListener('pointermove', _onDragNota);
    window.removeEventListener('pointerup', _endDragNota);
    window.removeEventListener('pointercancel', _endDragNota);
  }

  let _resizeN = null;
  function _startResizeNota(n, e) {
    _resizeN = { n, x: e.clientX, y: e.clientY, pw: n.w, ph: n.h };
    _iframesInteractivos(false);
    _gestoNativo(true);
    window.addEventListener('pointermove', _onResizeNota);
    window.addEventListener('pointerup', _endResizeNota);
    window.addEventListener('pointercancel', _endResizeNota);
  }
  function _onResizeNota(e) {
    if (!_resizeN) return;
    _resizeN.n.w = Math.max(220, _resizeN.pw + (e.clientX - _resizeN.x) / B.zoom);
    _resizeN.n.h = Math.max(160, _resizeN.ph + (e.clientY - _resizeN.y) / B.zoom);
    const node = _nodoNota(_resizeN.n.id);
    if (node) _layoutNota(_resizeN.n, node);
  }
  function _endResizeNota() {
    const n = _resizeN && _resizeN.n;
    _resizeN = null; _iframesInteractivos(true);
    _gestoNativo(false);
    if (n) _guardarNota(n, true);
    window.removeEventListener('pointermove', _onResizeNota);
    window.removeEventListener('pointerup', _endResizeNota);
    window.removeEventListener('pointercancel', _endResizeNota);
  }

  // ── Alta/baja/rotación de teléfonos ─────────────────────────────────────────
  // «Quitar teléfono» es una decisión del usuario y se RESPETA: al quitar el
  // último queda un flag por proyecto (localStorage, sobrevive recargas) y la
  // detección de Metro deja de reponer el iPhone default (debeAutoAgregarTelefono
  // en el módulo puro). Agregar uno a mano levanta el flag.
  const _lsSinTel = () => `jarvis_mps_sin_tel_${_projectId}`;
  function _sinTel() { try { return localStorage.getItem(_lsSinTel()) === '1'; } catch { return false; } }
  function _setSinTel(on) {
    if (_projectId == null) return;
    try { if (on) localStorage.setItem(_lsSinTel(), '1'); else localStorage.removeItem(_lsSinTel()); } catch { /* privado */ }
  }
  function _addPhone(devKey) {
    if (S.phones.length >= MAX_PHONES) { _toast(_t('Máximo {n} teléfonos').replace('{n}', MAX_PHONES)); return; }
    _setSinTel(false);
    const dev = (typeof devKey === 'string' && DEVS[devKey]) ? devKey : (S.phones[0] ? S.phones[0].dev : 'ip15p');
    let x = 0, y = 0;
    if (S.phones.length) {
      const last = S.phones[S.phones.length - 1], o = _outer(last);
      x = last.x + o.w + 70; y = last.y;
    }
    const p = { id: _seq++, dev, x, y, ps: 1, landscape: false, net: 'on' };
    S.phones.push(p); S.sel = p.id;
    _renderPhones(); _fit();
  }
  function _removePhone(id) {
    S.phones = S.phones.filter((p) => p.id !== id);
    if (S.sel === id) S.sel = S.phones.length ? S.phones[0].id : null;
    if (!S.phones.length) _setSinTel(true);
    _renderPhones(); if (S.phones.length) _fit();
  }
  function _rotate(id) {
    const p = S.phones.find((x) => x.id === id) || S.phones[0]; if (!p) return;
    p.landscape = !p.landscape;
    _renderPhones(); _fit();
  }
  // Simula la conexión de ESE teléfono (independiente del Metro): 'off' tapa la
  // app con el overlay "Sin conexión" y la vuelve inusable en ese teléfono.
  function _setNet(id, estado) {
    const p = S.phones.find((x) => x.id === id); if (!p) return;
    p.net = estado;
    const node = $('mps-board').querySelector(`.mps-phone[data-id="${id}"]`);
    if (node) node.dataset.net = estado;
    _toast(estado === 'off' ? 'Teléfono sin conexión' : 'Conexión restaurada');
    if (_pane === 'net') _renderNet();
  }
  function _phone(id) { return S.phones.find((p) => p.id === id); }

  // Todo src que pone el WORKSPACE pasa por acá: el timestamp distingue estas
  // navegaciones propias de un escape de la app (ver _volverAlSampler).
  function _apuntar(ifr, u) {
    ifr.src = u; ifr.dataset.src = u; ifr.dataset.propioTs = String(Date.now());
  }
  // recarga TODOS los iframes (Metro sirve el bundle de nuevo)
  function _reloadFrames() {
    document.querySelectorAll('#mps-board .mps-phone').forEach((node) => {
      node.classList.remove('reloading'); void node.offsetWidth; node.classList.add('reloading');
      const ifr = node.querySelector('.mps-iframe'), u = _urlConRuta();
      if (u) _apuntar(ifr, u);
    });
  }
  // re-apunta los iframes a la ruta actual (cambio de /route)
  function _refreshFrames(force) {
    document.querySelectorAll('#mps-board .mps-iframe').forEach((ifr) => {
      const u = _urlConRuta();
      if (u && (force || ifr.dataset.src !== u)) _apuntar(ifr, u);
    });
  }
  // Deshace un ESCAPE del sampler: su URL vive reescrita a la ruta de la app,
  // así que un full reload adentro (HMR de Metro, `r`, location.reload())
  // navega esa ruta contra Jarvis → el dashboard adentro del teléfono. El
  // sampler avisa en beforeunload/pagehide (mps-sampler-bye) y acá se cancela
  // esa navegación re-apuntando al espejo instrumentado con la MISMA ruta.
  // Funciona también para teléfonos ESTACIONADOS (usa su dataset.src, no los
  // globales del proyecto activo). Si la descarga la inició el propio
  // workspace (reload/route/restauración: _apuntar recién selló propioTs), el
  // aviso se ignora — esa navegación es legítima.
  function _volverAlSampler(ifr, rutaApp) {
    if (Date.now() - (Number(ifr.dataset.propioTs) || 0) < 1500) return;
    const u = P().urlSamplerVuelta(ifr.dataset.src, rutaApp);
    if (u) _apuntar(ifr, u);
  }

  // ── Status bar adaptativa EN VIVO ───────────────────────────────────────────
  // Cada teléfono carga la app instrumentada (ver _urlConRuta): el script
  // inyectado postea el color de fondo real cada vez que CAMBIA — navegación
  // entre pantallas incluida (antes se muestreaba una copia oculta y quedaba
  // congelado el color de la primera pantalla). El reporte se enruta AL
  // teléfono que lo mandó (e.source): cada uno adapta su status bar y su home
  // indicator a SU pantalla, como aparatos reales. El estado viaja con el
  // teléfono (p.colores) y sobrevive relayouts y estacionamiento.
  function _analizarColores(d) {
    // MAYORÍA entre 3 puntos por franja: un toast tapa un punto, no el fondo.
    const cTop = P().colorMayoria([d.top, d.topIzq, d.topDer]);
    const cBot = P().colorMayoria([d.bottom, d.bottomIzq, d.bottomDer]);
    return {
      top: P().esFondoClaro(cTop) === true, bottom: P().esFondoClaro(cBot) === true,
      cTop, cBot,
      grad: P().esGradienteSeguro(d.grad) ? d.grad : null,
    };
  }
  function _pintarColores(node, c) {
    node.querySelector('.mps-status').classList.toggle('claro', !!(c && c.top));
    node.querySelector('.mps-homebar').classList.toggle('claro', !!(c && c.bottom));
    const scr = node.querySelector('.mps-screen');
    const band = node.querySelector('.mps-band');
    if (c && c.grad) {
      // Fondo con GRADIENTE de pantalla completa: se pinta detrás del iframe
      // ocupando TODO el vidrio → las franjas del status bar y del home son la
      // continuación real del degradado (un color plano lo "congelaba").
      scr.style.background = c.cTop || '#0a0a0c';
      scr.style.backgroundImage = c.grad;
      scr.style.backgroundSize = '100% 100%';
      scr.style.backgroundRepeat = 'no-repeat';
      band.style.background = 'transparent';   // deja ver el gradiente de atrás
    } else {
      scr.style.background = (c && c.cTop) || '';   // borra imagen previa también
      band.style.background = (c && c.cBot) || '';
    }
  }
  window.addEventListener('message', (e) => {
    if (e.origin !== location.origin || !e.data) return;
    if (e.data.tipo !== 'mps-sampler' && e.data.tipo !== 'mps-sampler-bye') return;
    const node = Array.from(document.querySelectorAll('#mps-board .mps-phone')).find((n) => {
      const f = n.querySelector('.mps-iframe');
      return f && f.contentWindow === e.source;
    });
    if (!node) return;
    if (e.data.tipo === 'mps-sampler-bye') {   // la app se está descargando (escape)
      _volverAlSampler(node.querySelector('.mps-iframe'), e.data.ruta);
      return;
    }
    const c = _analizarColores(e.data);
    const id = Number(node.dataset.id);
    // El dueño puede estar ESTACIONADO (otro proyecto): su estado vive en el pool.
    const p = String(_projectId) === node.dataset.pid
      ? _phone(id)
      : ((_pools[node.dataset.pid] || {}).phones || []).find((x) => x.id === id);
    if (p) p.colores = c;
    _pintarColores(node, c);
  });

  // ── Picker de dispositivo (hoja de vidrio) ──────────────────────────────────
  let _sheetFor = null;
  function _wireSheet() {
    const grid = $('mps-devgrid');
    const MARCAS = [['apple', 'iPhone'], ['samsung', 'Samsung'], ['google', 'Pixel'], ['tablet', 'Tablet']];
    grid.innerHTML = MARCAS.map(([m, titulo]) => {
      const keys = ORDEN.filter((k) => DEVS[k].marca === m);
      if (!keys.length) return '';
      return `<div class="mps-devgrp">${_esc(titulo)}</div>` + keys.map((k) => {
        const d = DEVS[k];
        return `<button class="mps-devopt" data-dev="${k}"><span class="mps-sil" data-cut="${d.cutout.tipo}" style="aspect-ratio:${d.vw}/${d.vh}"></span><span class="nm">${_esc(d.nombre)}</span><span class="dm">${d.vw}×${d.vh} · @${DC().dprLabel(k)}x</span></button>`;
      }).join('');
    }).join('');
    grid.addEventListener('click', (e) => {
      const b = e.target.closest('[data-dev]'); if (!b) return;
      const p = _phone(_sheetFor); if (p) { p.dev = b.dataset.dev; _renderPhones(); _fit(); }
      _closeSheet();
    });
    $('mps-scrim').addEventListener('click', _closeSheet);
  }
  function _openSheet(id) {
    _sheetFor = id; const p = _phone(id);
    $('mps-devgrid').querySelectorAll('.mps-devopt').forEach((o) => o.classList.toggle('on', p && o.dataset.dev === p.dev));
    $('mps-scrim').classList.add('open'); $('mps-sheet').classList.add('open');
  }
  function _closeSheet() { $('mps-scrim').classList.remove('open'); $('mps-sheet').classList.remove('open'); _sheetFor = null; }

  // ── Menú contextual del teléfono ────────────────────────────────────────────
  function _openCtx(id, cx, cy) {
    const ctx = $('mps-ctx'), p = _phone(id); if (!p) return;
    const offline = p.net === 'off';
    ctx.innerHTML = `
      <div class="mps-ctx-h">${_esc((DEVS[p.dev] || {}).nombre || 'Teléfono')}</div>
      <button class="mps-ctx-item" data-a="rotate">${_svgRotate()}Girar<span class="k">R</span></button>
      <button class="mps-ctx-item" data-a="device">${_svgPhone(15)}Cambiar dispositivo</button>
      <button class="mps-ctx-item" data-a="net">${offline ? _svgWifi() : _svgWifiOff()}${offline ? 'Restaurar conexión' : 'Simular sin conexión'}</button>
      <button class="mps-ctx-item" data-a="dup">${_svgTwo()}Duplicar</button>
      <button class="mps-ctx-item" data-a="reload">${_svgReload()}Recargar</button>
      <button class="mps-ctx-item" data-a="reset">${_svgFit()}Tamaño original</button>
      <div class="mps-ctx-sep"></div>
      <button class="mps-ctx-item danger" data-a="remove">${_svgX(15)}Quitar</button>`;
    // absolute dentro del panel (position:relative): convierto viewport→panel con
    // getBoundingClientRect (robusto ante transforms de ancestros del dock).
    const pr = $('mobile-preview-panel').getBoundingClientRect();
    ctx.style.left = Math.max(4, Math.min(cx - pr.left, pr.width - 208)) + 'px';
    ctx.style.top = Math.max(4, Math.min(cy - pr.top, pr.height - 318)) + 'px';
    ctx.classList.add('show');
    ctx.onclick = (e) => {
      const it = e.target.closest('[data-a]'); if (!it) return;
      _hideCtx();
      const a = it.dataset.a, ph = _phone(id);
      if (a === 'rotate') _rotate(id);
      else if (a === 'device') _openSheet(id);
      else if (a === 'net') _setNet(id, offline ? 'on' : 'off');
      else if (a === 'dup') _dupPhone(id);
      else if (a === 'reload') _reloadOne(id);
      else if (a === 'reset') { if (ph) { ph.ps = 1; ph.landscape = false; _renderPhones(); _fit(); } }
      else if (a === 'remove') _removePhone(id);
    };
  }
  function _hideCtx() { const c = $('mps-ctx'); if (c) c.classList.remove('show'); }
  function _dupPhone(id) {
    if (S.phones.length >= MAX_PHONES) { _toast(_t('Máximo {n} teléfonos').replace('{n}', MAX_PHONES)); return; }
    const p = _phone(id); if (!p) return;
    const o = _outer(p);
    S.phones.push({ id: _seq++, dev: p.dev, x: p.x + o.w + 70, y: p.y, ps: p.ps, landscape: p.landscape, net: p.net });
    S.sel = S.phones[S.phones.length - 1].id; _renderPhones(); _fit();
  }
  function _reloadOne(id) {
    const node = $('mps-board').querySelector(`.mps-phone[data-id="${id}"]`); if (!node) return;
    node.classList.remove('reloading'); void node.offsetWidth; node.classList.add('reloading');
    const ifr = node.querySelector('.mps-iframe'), u = _urlConRuta();
    if (u) _apuntar(ifr, u);
  }

  // ── Riel de vidrio + panel de info ──────────────────────────────────────────
  function _wireDock() {
    const gdock = $('mps-gdock');
    gdock.querySelectorAll('.mps-gtab').forEach((t, i) => {
      t.addEventListener('click', () => _togglePane(t.dataset.pane, i));
    });
    $('mps-ix').addEventListener('click', () => _closePane());
  }
  function _togglePane(pane, idx) {
    if (_pane === pane) { _closePane(); return; }
    _pane = pane;
    $('mps-ipanel').classList.add('open');
    $('mps-gdock').classList.add('active');
    $('mps-gthumb').style.transform = `translateY(${idx * 42}px)`;
    $('mps-gdock').querySelectorAll('.mps-gtab').forEach((t) => t.classList.toggle('on', t.dataset.pane === pane));
    document.querySelectorAll('#mps-ipanel .mps-pane').forEach((p) => p.classList.toggle('on', p.dataset.pane === pane));
    $('mps-ititle').textContent = pane === 'console' ? 'Consola' : pane === 'net' ? 'Red · backend de la app' : 'Inspector · editar textos';
    if (pane === 'console') _renderConsole();
    else if (pane === 'net') _renderNet();
    else if (pane === 'inspect') _renderInspector();
    if (pane === 'console') { const b = $('mps-cbadge'); if (b) b.classList.remove('show'); }
  }
  function _closePane() {
    _pane = null;
    $('mps-ipanel').classList.remove('open');
    $('mps-gdock').classList.remove('active');
    $('mps-gdock').querySelectorAll('.mps-gtab').forEach((t) => t.classList.remove('on'));
  }

  function _renderConsole() {
    const el = $('mps-pane-console'); if (!el) return;
    if (!_logs.length) { el.innerHTML = `<div class="mps-empty-note">Sin actividad todavía. Acá aparece lo que hacen los agentes y las recargas.</div>`; return; }
    el.innerHTML = _logs.slice(-60).map((l) =>
      `<div class="mps-log ${l.cls}"><span class="ts">${l.ts}</span><span class="tag">${_esc(l.tag)}</span><span class="msg">${l.msg}</span></div>`).reverse().join('');
  }
  function _renderNet() {
    const el = $('mps-pane-net'); if (!el) return;
    const rows = [];
    rows.push(`<div class="mps-netrow"><span class="dot ${_url ? 'ok' : ''}"></span><div>Metro Expo web: ${_url ? `<code>${_esc(_url)}</code>` : '<span class="mps-i18n">esperando…</span>'}</div></div>`);
    const b = _lastBackend;
    if (b && b.configurado) {
      if (!b.alcanzable) rows.push(`<div class="mps-netrow"><span class="dot err"></span><div><span class="mps-i18n">El backend de la app no responde</span> <code>${_esc(b.api_url)}</code></div></div>`);
      else if (b.cors_ok === false) rows.push(`<div class="mps-netrow"><span class="dot warn"></span><div><span class="mps-i18n">El backend bloquea el preview (CORS). Agregá</span> <code>${_esc(b.origen)}</code> <span class="mps-i18n">a CORS_ORIGIN y redesplegá; en el celular funciona igual.</span></div></div>`);
      else rows.push(`<div class="mps-netrow"><span class="dot ok"></span><div><span class="mps-i18n">Backend conectado</span> <code>${_esc(b.api_url)}</code></div></div>`);
    } else {
      rows.push(`<div class="mps-netrow"><span class="dot"></span><div><span class="mps-i18n">La app no declara backend</span> <code>EXPO_PUBLIC_API_URL</code>.</div></div>`);
    }
    // Conexión POR TELÉFONO (independiente): probá cómo responde tu app sin red.
    if (S.phones.length) {
      rows.push(`<div class="mps-net-h">Conexión de cada teléfono</div>`);
      S.phones.forEach((p, i) => {
        const off = p.net === 'off';
        rows.push(`<div class="mps-netphone" data-id="${p.id}">
          <span class="dot ${off ? 'err' : 'ok'}"></span>
          <div class="np-nm">${_esc((DEVS[p.dev] || {}).nombre || 'Teléfono')} <span class="np-i">#${i + 1}</span></div>
          <button class="mps-net-tog ${off ? 'off' : ''}" data-id="${p.id}">${off ? _svgWifiOff(13) : _svgWifi(13)}${off ? 'Sin conexión' : 'Con conexión'}</button>
        </div>`);
      });
    }
    el.innerHTML = rows.join('');
    el.querySelectorAll('.mps-net-tog').forEach((btn) => {
      btn.addEventListener('click', () => {
        const id = parseInt(btn.dataset.id, 10), p = _phone(id);
        _setNet(id, p && p.net === 'off' ? 'on' : 'off');
      });
    });
  }

  // ── Inspector: campo de selección + lista de textos + edición del código real ─
  // Como el iframe es cross-origin (no puedo leer el click sobre la app), el
  // inspector muestra un CAMPO DE SELECCIÓN prominente: elegís un texto de la lista
  // y el campo te dice EXACTO qué y de dónde (archivo:línea) estás por editar, y lo
  // editás ahí. Al guardar, reemplaza el literal en el fuente real (POST /patch-text).
  let _textos = [];
  let _selIdx = -1;   // índice del texto seleccionado en _textos (-1 = ninguno)

  function _renderInspector() {
    const el = $('mps-pane-inspect'); if (!el) return;
    el.innerHTML = `
      <div class="mps-insp-hint on">${_svgInspect()}<div>Elegí un texto de la lista y editalo: al guardar cambia el <b>código real</b> y Metro recarga. Los datos (números, nombres, fechas) no se editan porque se completan solos.</div></div>
      <div class="mps-insp-sel" id="mps-insp-sel"></div>
      <div class="mps-insp-search">${_svgSearch()}<input id="mps-insp-q" placeholder="Buscar un texto…" spellcheck="false"></div>
      <div id="mps-insp-list"></div>`;
    $('mps-insp-q').addEventListener('input', (e) => _fillInspList(e.target.value.trim().toLowerCase()));
    _renderSel();
    if (!_inspectFetch) {
      _inspectFetch = true;
      $('mps-insp-list').innerHTML = `<div class="mps-empty-note">Escaneando el código…</div>`;
      fetch(`/api/mobile-preview/${_projectId}/textos`).then((r) => r.json()).then((d) => {
        _textos = Array.isArray(d.textos) ? d.textos : [];
        _selIdx = -1; _renderSel(); _fillInspList('');
      }).catch(() => { $('mps-insp-list').innerHTML = `<div class="mps-empty-note">No pude leer los textos del proyecto.</div>`; });
    } else {
      _fillInspList('');
    }
  }

  // El CAMPO DE SELECCIÓN: qué texto y de dónde se está editando.
  function _renderSel() {
    const sel = $('mps-insp-sel'); if (!sel) return;
    const t = _selIdx >= 0 ? _textos[_selIdx] : null;
    if (!t) {
      sel.className = 'mps-insp-sel';
      sel.innerHTML = `<div class="sel-empty">${_svgTarget()}<span>Ningún texto seleccionado — tocá uno de la lista para editarlo.</span></div>`;
      return;
    }
    sel.className = 'mps-insp-sel active';
    sel.innerHTML = `
      <div class="sel-loc">${_svgTarget()} Editando <b>${_esc(t.file.split('/').pop())}</b><span class="sel-line">:${t.line}</span> <span class="sel-path">${_esc(t.file)}</span></div>
      <div class="sel-row">
        <input id="mps-sel-input" value="${_esc(t.text)}" spellcheck="false" autocomplete="off">
        <button class="sel-save" id="mps-sel-save">Guardar</button>
      </div>
      <div class="sel-hint">Se reemplaza este texto en el archivo real y Metro recarga la app.</div>`;
    const input = $('mps-sel-input');
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); _saveSel(); }
      else if (e.key === 'Escape') { _selIdx = -1; _renderSel(); _markSelRow(); }
    });
    $('mps-sel-save').addEventListener('click', _saveSel);
    // caret al final + foco
    input.focus();
    const v = input.value; input.value = ''; input.value = v;
  }

  function _selectText(idx) {
    _selIdx = idx; _renderSel(); _markSelRow();
  }
  function _markSelRow() {
    const list = $('mps-insp-list'); if (!list) return;
    list.querySelectorAll('.mps-txt').forEach((r) => r.classList.toggle('sel', parseInt(r.dataset.i, 10) === _selIdx));
  }

  function _fillInspList(q) {
    const list = $('mps-insp-list'); if (!list) return;
    const items = q ? _textos.filter((t) => t.text.toLowerCase().includes(q)) : _textos;
    if (!items.length) {
      list.innerHTML = `<div class="mps-empty-note">${_textos.length ? 'Nada coincide con la búsqueda.' : 'No encontré textos de UI editables en este proyecto.'}</div>`;
      return;
    }
    list.innerHTML = items.slice(0, 200).map((t) => {
      const idx = _textos.indexOf(t);
      return `<button class="mps-txt ${idx === _selIdx ? 'sel' : ''}" data-i="${idx}">
        <span class="val" title="${_esc(t.text)}">${_esc(t.text)}</span>
        <span class="loc">${_esc(t.file.split('/').pop())}:${t.line}</span>
        <span class="go">${_svgEdit()}</span>
      </button>`;
    }).join('');
    list.querySelectorAll('.mps-txt').forEach((row) => {
      row.addEventListener('click', () => _selectText(parseInt(row.dataset.i, 10)));
    });
  }

  function _saveSel() {
    const t = _selIdx >= 0 ? _textos[_selIdx] : null; if (!t) return;
    const input = $('mps-sel-input'); if (!input) return;
    const nuevo = input.value;
    if (nuevo === t.text) { _toast('Sin cambios'); return; }
    const btn = $('mps-sel-save'); btn.disabled = true; btn.textContent = '…';
    fetch(`/api/mobile-preview/${_projectId}/patch-text`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file: t.file, line: t.line, old: t.text, new: nuevo }),
    }).then(async (r) => {
      if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || 'no se pudo editar'); }
      return r.json();
    }).then(() => {
      _toast('Texto editado — Metro recarga la app');
      _log('ok', 'EDIT', `<b>${_esc(t.file.split('/').pop())}:${t.line}</b> → "${_esc(nuevo)}"`);
      t.text = nuevo;
      _fillInspList(($('mps-insp-q') && $('mps-insp-q').value.trim().toLowerCase()) || '');
      _renderSel();
    }).catch((err) => {
      _toast(String(err.message || err));
      const b = $('mps-sel-save'); if (b) { b.disabled = false; b.textContent = 'Guardar'; }
    });
  }

  // ── Detección del Expo (el panel NUNCA arranca Metro) ───────────────────────
  // La app visible carga INSTRUMENTADA (espejo same-origin del endpoint
  // /sampler, con <base> hacia Metro): el script inyectado reporta EN VIVO el
  // color de fondo de cada pantalla (navegación interna incluida) y re-apunta
  // el WS del HMR a Metro, así el hot reload sigue vivo. Antes se muestreaba
  // una copia oculta: quedaba congelado el color de la PRIMERA pantalla.
  function _urlConRuta() {
    if (!_url) return null;
    const origen = _url.replace(/\/+$/, '');
    return `/api/mobile-preview/${_projectId}/sampler?url=${encodeURIComponent(origen)}&ruta=${encodeURIComponent(_ruta || '/')}`;
  }
  // URL cruda de Metro (para abrir la app en una pestaña real del navegador).
  function _urlDirecta() {
    if (!_url) return null;
    try { return new URL(_ruta, _url).href; } catch { return _url; }
  }
  function _setConn(estado) {
    const map = { ok: ['ok', 'Conectado'], wait: ['wait', 'Esperando'], off: ['off', 'Detenido'] };
    const [d, txt] = map[estado] || map.off;
    const panel = $('mobile-preview-panel'); if (panel) panel.setAttribute('data-conn', d);
    const lbl = $('mps-conn-lbl'); if (lbl) lbl.textContent = txt;
  }
  function _setUrl(url) {
    const cambio = _url !== url;
    _url = url;
    if (P().debeAutoAgregarTelefono(S.phones.length, _sinTel())) _addPhone('ip15p');
    else if (S.phones.length && cambio) _refreshFrames(true);
    _renderPhones();
    if (S.phones.length) _fit();
    _setConn('ok');
    const st = $('mps-step2'); if (st) st.classList.add('done');
    if (cambio) _chequearBackend();
  }
  function _estadoEspera() {
    // No borrar teléfonos que el usuario agregó: Metro todavía no está, el
    // marco tiene que seguir en el lienzo. Solo se suelta la URL del iframe.
    _url = null;
    _renderPhones(); _setConn('wait');
    if (_pane === 'net') _renderNet();
  }
  function _estadoNativo() {
    _url = null;
    _renderPhones(); _setConn('wait');
    const p = $('mps-empty') && $('mps-empty').querySelector('p');
    if (p) p.textContent = 'Hay un Metro corriendo, pero sin --web. Arrancalo con expo start --web para verlo acá, o escaneá el QR de Expo Go.';
  }

  async function _detectarUnaVez() {
    if (_cerrado) { _detener(); return; }
    if (document.hidden) return;
    const pid = _projectId;
    let d;
    try { d = await (await fetch(`/api/mobile-preview/${_projectId}/detectar`)).json(); }
    catch { return; }
    if (_cerrado || pid !== _projectId) return;
    if (d && d.web && d.url) {
      const nuevo = _estadoDet !== 'web' || _url !== d.url;
      _estadoDet = 'web';
      if (nuevo) {
        _setUrl(d.url);
        _log('ok', 'METRO', `app detectada en <b>${_esc(d.url)}</b>`);
        if (window.JarvisDock && window.JarvisDock.activeTab && window.JarvisDock.activeTab() !== 'mobile') {
          window.JarvisDock.notify && window.JarvisDock.notify('mobile', 1);
        }
      }
    } else if (d && d.nativo) {
      if (_estadoDet !== 'nativo') { _estadoDet = 'nativo'; _estadoNativo(); }
    } else {
      if (_estadoDet !== 'espera') { _estadoDet = 'espera'; _estadoEspera(); }
    }
  }
  function _iniciar() { if (_detTimer) return; _detectarUnaVez(); _detTimer = setInterval(_detectarUnaVez, DET_INTERVALO); }
  function _detener() { if (_detTimer) { clearInterval(_detTimer); _detTimer = null; } }

  async function _chequearBackend() {
    if (_cerrado) return;
    if (_beTimer) { clearTimeout(_beTimer); _beTimer = null; }
    const pid = _projectId;
    try {
      const d = await (await fetch(`/api/mobile-preview/${_projectId}/backend-status`)).json();
      if (_cerrado || pid !== _projectId) return;
      _lastBackend = d;
      if (_pane === 'net') _renderNet();
      const el = $('mps-backend'); if (!el) return;
      if (!d.configurado) { el.className = 'mps-backend'; return; }
      let cls, html;
      if (!d.alcanzable) { cls = 'err'; html = `<span class="mps-i18n">El backend de la app no responde</span> <code>${_esc(d.api_url)}</code>`; }
      else if (d.cors_ok === false) { cls = 'warn'; html = `<span class="mps-i18n">El backend bloquea el preview (CORS). Agregá</span> <code>${_esc(d.origen)}</code> <span class="mps-i18n">a CORS_ORIGIN.</span>`; }
      else { cls = 'ok'; html = `<span class="mps-i18n">Backend conectado</span> <code>${_esc(d.api_url)}</code>`; }
      el.className = `mps-backend show ${cls}`; el.innerHTML = html;
      if (cls === 'ok') _beTimer = setTimeout(() => { el.className = 'mps-backend'; _beTimer = null; }, 8000);
    } catch { /* sin red hacia Jarvis */ }
  }

  // ── API pública ─────────────────────────────────────────────────────────────
  function abrir() {
    if (!_projectId) return;
    if (!_cerrado) {
      // Restaurado del pool: NO recargar los frames (la gracia del
      // estacionamiento es volver sin reconectar). Re-abrir el MISMO proyecto
      // (mostrar la pestaña de nuevo) sí refresca, como siempre.
      if (_url && !_recienRestaurado) _refreshFrames(true);
      _recienRestaurado = false;
      _iniciar();   // el estacionamiento detuvo el poller: rearmarlo (idempotente)
      return;
    }
    _cargarNotas();   // las notas del proyecto viven en el server (idempotente)
    _cerrado = false; _estadoDet = _recienRestaurado ? _estadoDet : '';
    _recienRestaurado = false;
    if (!_url) { _setConn('wait'); _renderPhones(); }
    _iniciar();
  }
  function cerrar() {
    _cerrado = true; _url = null; _estadoDet = ''; _ruta = '/';
    S.phones = []; S.sel = null;
    const ri = $('mps-route'); if (ri) ri.value = '/';
    _detener();
    if (_beTimer) { clearTimeout(_beTimer); _beTimer = null; }
    _renderPhones(); _setConn('off');
    const be = $('mps-backend'); if (be) be.className = 'mps-backend';
  }
  function sincronizar(d) { if (!d || !d.es_expo) return false; abrir(); return true; }

  const _PASO_FIN = new Set(['done', 'blocked', 'error', 'pending']);
  function onActividad(data) {
    if (_cerrado || !data) return;
    if (data.type === 'workflow_update') {
      if (data.estado === 'done') { _log('ok', 'DONE', 'workflow completado'); _flashConn(); return; }
      if (data.estado !== 'running') return;
      const pasos = Array.isArray(data.pasos) ? data.pasos : [];
      let act = pasos.filter((p) => p && !_PASO_FIN.has(p.estado)).map((p) => p.agente).filter(Boolean);
      if (!act.length && pasos[data.paso_actual] && pasos[data.paso_actual].agente) act = [pasos[data.paso_actual].agente];
      if (act.length) _log('info', 'WORK', `<b>${_esc(act.join(' · '))}</b> trabajando…`);
    } else if (data.type === 'task_event' && data.event === 'TASK_DONE') {
      _log('ok', 'STEP', `<b>${_esc(data.terminal_nombre || 'Agente')}</b> completó su paso`);
    } else if (data.type === 'workflow_done') {
      _log('ok', 'DONE', 'workflow completado'); _flashConn();
    }
  }
  function _flashConn() { const p = $('mobile-preview-panel'); if (p) { p.setAttribute('data-conn', 'ok'); } }

  // ── Utilidades UI ────────────────────────────────────────────────────────────
  let _toastTimer = null;
  function _toast(txt) {
    const el = $('mps-toast'); if (!el) return;
    el.innerHTML = `${_svgSpark()}<span>${_esc(txt)}</span>`;
    el.classList.add('show');
    if (_toastTimer) clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => el.classList.remove('show'), 3200);
  }
  function _log(cls, tag, msg) {
    const ts = _hora();
    _logs.push({ cls, tag, msg, ts });
    if (_logs.length > 200) _logs.shift();
    if (_pane === 'console') _renderConsole();
    else { const b = $('mps-cbadge'); if (b) b.classList.add('show'); }
  }
  function _hora() {
    const d = new Date();
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
  }

  // ── SVGs (inline, sin depender de icon() para el chrome propio) ──────────────
  function _svgGlobe() { return '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 2.5 15.3 0 18M12 3c-2.5 2.7-2.5 15.3 0 18"/></svg>'; }
  function _svgReload() { return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-2.64-6.36M21 4v5h-5"/></svg>'; }
  function _svgDots() { return '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>'; }
  function _svgTwo() { return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="7" height="14" rx="1.5"/><rect x="14" y="5" width="7" height="14" rx="1.5"/></svg>'; }
  function _svgRotate() { return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M2 12a10 10 0 0 1 17-7l3 3M22 12a10 10 0 0 1-17 7l-3-3"/><path d="M22 5v3h-3M2 19v-3h3"/></svg>'; }
  function _svgFit() { return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>'; }
  function _svgExt() { return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>'; }
  function _svgPlus() { return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>'; }
  function _svgMinus() { return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M5 12h14"/></svg>'; }
  function _svgConsole() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 17l5-5-5-5M12 19h7"/></svg>'; }
  function _svgNet() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M2 8.8a16 16 0 0 1 20 0M5 12.5a11 11 0 0 1 14 0M8.5 16.3a5.5 5.5 0 0 1 7 0M12 20h.01"/></svg>'; }
  function _svgWifi(s) { s = s || 15; return `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M2 8.8a16 16 0 0 1 20 0M5 12.5a11 11 0 0 1 14 0M8.5 16.3a5.5 5.5 0 0 1 7 0M12 20h.01"/></svg>`; }
  function _svgWifiOff(s) { s = s || 26; return `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M2 8.8a16 16 0 0 1 5.5-3.3M10.5 5.1a16 16 0 0 1 11.5 3.7M5 12.5a11 11 0 0 1 3-2M8.5 16.3a5.5 5.5 0 0 1 7 0M12 20h.01M2 2l20 20"/></svg>`; }
  function _svgInspect() { return '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="m3 3 7.6 18 2.4-7.4L20.6 11 3 3Z"/></svg>'; }
  function _svgX(s) { s = s || 15; return `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>`; }
  function _svgPhone(s) { s = s || 22; return `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="6" y="2" width="12" height="20" rx="3"/><path d="M11 18h2"/></svg>`; }
  function _svgGrip() { return '<svg width="10" height="14" viewBox="0 0 10 14" fill="currentColor"><circle cx="3" cy="3" r="1.2"/><circle cx="7" cy="3" r="1.2"/><circle cx="3" cy="7" r="1.2"/><circle cx="7" cy="7" r="1.2"/><circle cx="3" cy="11" r="1.2"/><circle cx="7" cy="11" r="1.2"/></svg>'; }
  function _svgSignal() { return '<svg width="17" height="11" viewBox="0 0 17 11" fill="currentColor"><rect x="0" y="7" width="3" height="4" rx="1"/><rect x="4.6" y="4.8" width="3" height="6.2" rx="1"/><rect x="9.2" y="2.4" width="3" height="8.6" rx="1"/><rect x="13.8" y="0" width="3" height="11" rx="1"/></svg>'; }
  function _svgBatt() { return '<svg width="25" height="12" viewBox="0 0 25 12" fill="none"><rect x="0.5" y="0.5" width="21" height="11" rx="3.2" stroke="currentColor" opacity=".38"/><path d="M23 3.9v4.2a2.3 2.3 0 0 0 0-4.2Z" fill="currentColor" opacity=".38"/><rect x="2" y="2" width="18" height="8" rx="2" fill="currentColor"/></svg>'; }
  function _svgChevron() { return '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg>'; }
  function _svgSearch() { return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>'; }
  function _svgEdit() { return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>'; }
  function _svgTarget() { return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="8"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/><circle cx="12" cy="12" r="2.5" fill="currentColor" stroke="none"/></svg>'; }
  function _svgNote() { return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 13.5V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h7.5L20 13.5Z"/><path d="M13.5 20v-4.5a2 2 0 0 1 2-2H20"/><path d="M8 9h8M8 13h4"/></svg>'; }
  function _svgLock(s) { s = s || 13; return `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="10" width="16" height="11" rx="2.5"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>`; }
  function _svgEye(s) { s = s || 13; return `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12Z"/><circle cx="12" cy="12" r="2.6"/></svg>`; }
  function _svgCopy() { return '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2.5"/><path d="M15 5.5A2.5 2.5 0 0 0 12.5 3H5.5A2.5 2.5 0 0 0 3 5.5v7A2.5 2.5 0 0 0 5.5 15"/></svg>'; }
  function _svgSpark() { return '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l1.6 6.4L20 10l-6.4 1.6L12 18l-1.6-6.4L4 10l6.4-1.6Z"/></svg>'; }

  window.MobilePreview = { init, abrir, cerrar, sincronizar, onActividad };
})();
