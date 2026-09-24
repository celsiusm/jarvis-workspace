/* ═══════════════════════════════════════════════════════════════════════
   window.JarvisSlideEditor — editor deslizante (visor de código)
   Reemplaza al editor Monaco del dock. Al tocar un archivo del árbol de la
   franja izquierda, el editor SALE POR LA IZQUIERDA (pegado a la franja), solo
   para VER código; las terminales se reacomodan. Movible izq⇄der (drag del
   header), resize (splitter), reorder de pestañas (drag). Portado del prototipo
   /static/preview-editor-left/ — contenido real vía GET /files/read.

   DOM esperado (en workspace.html, dentro de .jw-content):
     <section class="jw-ed" id="jw-editor">
       <div class="ed-tabs"  id="jw-ed-tabs"></div>
       <div class="ed-body"  id="jw-ed-body"></div>
       <div class="ed-status" id="jw-ed-status"></div>
     </section>
     <div id="jw-editor-split"></div>
   ═══════════════════════════════════════════════════════════════════════ */
(function (global) {
  'use strict';

  // ── iconos ──
  const IC = {
    file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 3v5h5"/><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/></svg>',
    max:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 9V5a1 1 0 0 1 1-1h4M20 9V5a1 1 0 0 0-1-1h-4M4 15v4a1 1 0 0 0 1 1h4M20 15v4a1 1 0 0 1-1 1h-4"/></svg>',
    close:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    x:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>',
    img:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-4.5-4.5L5 21"/></svg>',
    folderOpen: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M6 14l1.5-3.5A2 2 0 0 1 9.3 9H21l-2.4 6.3A2 2 0 0 1 16.7 17H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h4.5a2 2 0 0 1 1.4.6L11 6h5a2 2 0 0 1 2 2v1"/></svg>',
    up:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M18 15l-6-6-6 6"/></svg>',
    down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M6 9l6 6 6-6"/></svg>',
    search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>',
  };

  // ── resaltador de sintaxis (char-walker por línea; barato, sin deps) ──
  const KW = new Set(('def class return import from as async await if elif else for while try except finally with lambda True False None self and or not in is const let var function of new this typeof export default public private static void int float string bool null true false struct enum interface type match case switch break continue yield raise throw pass del global nonlocal').split(' '));
  // lenguaje de Monaco (/files/read) → modo del resaltador
  const LANGMAP = {
    python: 'py', shell: 'py', bash: 'py', shellscript: 'py', yaml: 'py', toml: 'py', ini: 'py', dockerfile: 'py', ruby: 'py', r: 'py', makefile: 'py', properties: 'py',
    javascript: 'js', typescript: 'js', javascriptreact: 'js', typescriptreact: 'js', json: 'js', jsonc: 'js', java: 'js', c: 'js', cpp: 'js', csharp: 'js', go: 'js', rust: 'js', php: 'js', swift: 'js', kotlin: 'js', scala: 'js', dart: 'js', sql: 'js',
    css: 'css', scss: 'css', less: 'css',
    markdown: 'md', plaintext: 'md', text: 'md', log: 'md',
    html: 'html', xml: 'html', svg: 'html', vue: 'html',
  };
  const hlLang = (m) => LANGMAP[m] || 'js';
  function esc(s) { return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function isW(c) { return c && /[A-Za-z0-9_@$]/.test(c); }
  function hl(raw, lang) {
    const py = lang === 'py', sl = (lang === 'js' || lang === 'css');
    let o = '', i = 0, n = raw.length;
    if (lang === 'md') { if (/^\s*#/.test(raw)) return '<span class="c-kw">' + esc(raw) + '</span>'; return esc(raw).replace(/(`[^`]+`)/g, '<span class="c-str">$1</span>'); }
    while (i < n) {
      const c = raw[i];
      if (py && c === '#') { o += '<span class="c-com">' + esc(raw.slice(i)) + '</span>'; break; }
      if (sl && c === '/' && raw[i + 1] === '*') { let e = raw.indexOf('*/', i + 2); e = e < 0 ? n : e + 2; o += '<span class="c-com">' + esc(raw.slice(i, e)) + '</span>'; i = e; continue; }
      if (sl && c === '/' && raw[i + 1] === '/') { o += '<span class="c-com">' + esc(raw.slice(i)) + '</span>'; break; }
      if (c === '"' || c === "'" || c === '`') { const q = c; let j = i + 1; while (j < n) { if (raw[j] === '\\') { j += 2; continue; } if (raw[j] === q) break; j++; } j = Math.min(j, n); o += '<span class="c-str">' + esc(raw.slice(i, j + 1)) + '</span>'; i = j + 1; continue; }
      if (/[0-9]/.test(c) && !isW(raw[i - 1])) { let j = i; while (j < n && /[0-9a-fA-FxX.%_]/.test(raw[j])) j++; o += '<span class="c-num">' + esc(raw.slice(i, j)) + '</span>'; i = j; continue; }
      if (c === '@' && /[A-Za-z_]/.test(raw[i + 1] || '')) { let j = i + 1; while (j < n && isW(raw[j])) j++; o += '<span class="c-dec">' + esc(raw.slice(i, j)) + '</span>'; i = j; continue; }
      if (/[A-Za-z_$]/.test(c)) { let j = i; while (j < n && isW(raw[j])) j++; const w = raw.slice(i, j); const cl = KW.has(w) ? 'c-kw' : (raw[j] === '(' ? 'c-fn' : ''); o += cl ? '<span class="' + cl + '">' + esc(w) + '</span>' : esc(w); i = j; continue; }
      if ('{}()[];:,.=+-*<>&|'.includes(c)) { o += '<span class="c-punc">' + esc(c) + '</span>'; i++; continue; }
      o += esc(c); i++;
    }
    return o;
  }
  function renderCode(body, lang) {
    return '<ol class="code">' + body.split('\n').map(l => '<li><code>' + (l.length ? hl(l, lang) : '<span class="empty">.</span>') + '</code></li>').join('') + '</ol>';
  }

  // ── estado ──
  const state = { open: [], active: null, editorOpen: false, dock: 'left', w: 520 };
  const _cache = {};          // path -> {content,language,size} | {binary,kind,size}
  let _projId = null;
  let _reorderEndAt = 0;

  const $ = (id) => document.getElementById(id);
  const contentEl = () => $('jw-content');
  const editorEl  = () => $('jw-editor');
  const termsEl   = () => document.querySelector('.panel-terminales');
  const relayoutTerms = () => { try { global.TerminalLayout?.relayoutAll?.(); } catch (_) {} };
  // Zoom de la Escala: el mouse llega en píxeles de PANTALLA y todo lo que se
  // escribe en un style (left/width) se lee en píxeles CSS. Sin dividir, con la
  // app escalada el editor no sigue al cursor al arrastrarlo ni al redimensionarlo.
  const _z = () => (global.JarvisEscala && global.JarvisEscala.zoom) ? global.JarvisEscala.zoom() : 1;
  const _rectCss = (r, z) => ({ left: r.left / z, right: r.right / z, top: r.top / z,
                                bottom: r.bottom / z, width: r.width / z, height: r.height / z });
  const setInteract = (v) => { try { global.TerminalLayout?.setInteracting?.(v); } catch (_) {} };

  // ════════════════ API ════════════════
  async function abrirArchivo(projId, path) {
    if (!path) return;
    _projId = projId;
    const name = path.split('/').pop();
    if (!state.open.find(t => t.path === path)) { state.open.push({ path, name }); if (state.open.length > 8) state.open.shift(); }
    state.active = path;
    const wasOpen = state.editorOpen;
    _ensureOpen();
    renderTabs();
    renderActive();
    if (!wasOpen) _slidein();
  }
  function cerrar() {
    state.editorOpen = false; state.active = null; state.open = [];
    const c = contentEl(); if (c) { c.removeAttribute('data-editor-open'); c.classList.remove('jw-ed-max'); }
    document.querySelectorAll('.sb-ffile.sel').forEach(f => f.classList.remove('sel'));
    try { global.JarvisStripFiles?.colapsar?.(); } catch (_) {}   // al salir del editor, el árbol de la franja se esconde de nuevo
    _animRefit();   // el width anima 520→0 (smooth, sin salto); refit de xterm UNA vez al terminar
  }
  // Gatea el refit de xterm mientras el width del editor anima (abrir/cerrar), y hace
  // UN refit al terminar la transición → sin tormenta de redraws ni salto/parpadeo.
  let _animT = null;
  function _animRefit() {
    const ed = editorEl(); if (!ed) { relayoutTerms(); return; }
    setInteract(true);
    if (_animT) clearTimeout(_animT);
    let done = false;
    const onEnd = (e) => { if (e && e.propertyName !== 'width') return; if (done) return; done = true;
      ed.removeEventListener('transitionend', onEnd); setInteract(false); relayoutTerms(); };
    ed.addEventListener('transitionend', onEnd);
    _animT = setTimeout(onEnd, 340);
  }
  function toggle() {
    if (state.editorOpen) cerrar();
    else if (state.open.length) { _ensureOpen(); renderTabs(); renderActive(); _slidein(); }
  }
  function onProjectChanged(projId) {
    _projId = projId;
    for (const k in _cache) delete _cache[k];
    cerrar();
  }
  function isOpen() { return state.editorOpen; }

  // ════════════════ apertura / layout ════════════════
  function _ensureOpen() {
    state.editorOpen = true;
    const c = contentEl(), ed = editorEl();
    if (!c || !ed) return;
    if (!c.dataset.editorDock) c.dataset.editorDock = state.dock;
    c.setAttribute('data-editor-open', '');
    if (!c.classList.contains('jw-ed-max')) ed.style.width = state.w + 'px';
    _animRefit();   // el width anima 0→state.w (smooth); refit de xterm UNA vez al terminar
  }
  function _slidein() {
    const ed = editorEl(); if (!ed) return;
    ed.classList.remove('jw-ed-slidein'); void ed.offsetWidth; ed.classList.add('jw-ed-slidein');
  }

  // ════════════════ render del archivo activo ════════════════
  function _headHTML(path, name, meta) {
    const segs = path.split('/');
    const crumb = segs.map((s, i) => i === segs.length - 1 ? `<span class="cur">${esc(s)}</span>` : `<span>${esc(s)}</span>`).join(' <span style="color:var(--ob-fg-4)">/</span> ');
    return `<div class="ed-head"><div style="min-width:0">
      <div class="eh-name">${esc(name)}</div>
      <div class="eh-path">${crumb}</div>
      ${meta ? `<div class="eh-meta">${meta}</div>` : ''}
    </div></div>`;
  }
  function renderActive() {
    const path = state.active; if (!path) return;
    const name = state.open.find(t => t.path === path)?.name || path.split('/').pop();
    const body = $('jw-ed-body'); if (!body) return;
    const cached = _cache[path];
    if (cached) { _paint(body, path, name, cached); return; }
    body.innerHTML = _headHTML(path, name, '') + `<div class="ed-msg">Cargando…</div>`;
    _renderStatus(null);
    fetch(`/api/projects/${encodeURIComponent(_projId)}/files/read?path=${encodeURIComponent(path)}`)
      .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(data => {
        _cache[path] = data.binary ? { binary: true, kind: data.kind, size: data.size } : { content: data.content, saved: data.content, language: data.language, size: data.size };
        if (state.active === path) _paint(body, path, name, _cache[path]);
      })
      .catch(() => { if (state.active === path) { body.innerHTML = _headHTML(path, name, '') + `<div class="ed-msg">${IC.warn}<span>No se pudo abrir el archivo</span></div>`; _renderStatus(null); } });
  }
  // capa resaltada PLANA (una línea por línea del textarea, para alinear el overlay)
  function renderCodeFlat(content, lang) {
    return content.split('\n').map(l => l.length ? hl(l, lang) : '').join('\n');
  }
  function _paint(body, path, name, data) {
    if (data.binary) {
      const meta = `<span class="lang">${(data.kind || 'BIN').toUpperCase()}</span><span>${_fmtSize(data.size)}</span>`;
      const inner = data.kind === 'image'
        ? `<div class="ed-msg">${IC.img}<span>Imagen — se ve en el navegador</span></div>`
        : `<div class="ed-msg">${IC.warn}<span>Archivo binario</span></div>`;
      body.innerHTML = _headHTML(path, name, meta) + inner;
      _renderStatus(data);
      return;
    }
    const lang = hlLang(data.language);
    const lines = data.content.split('\n').length;
    const meta = `<span class="lang">${(data.language || lang).toUpperCase()}</span><span>${lines} <span>líneas</span></span>`;
    body.innerHTML = _headHTML(path, name, meta) + `<div class="code-wrap" data-i18n-skip></div>`;   // data-i18n-skip: el i18n NO debe tocar el código
    _mountEditor(body.querySelector('.code-wrap'), path, data.content, lang);
    _renderStatus(data);
    if (data.dirty) _markDirty(path, true);
  }
  // Monta el editor: textarea transparente (front, editable) + capa resaltada (atrás)
  // + gutter de líneas + línea activa. El textarea es el scroller; los demás sincronizan.
  function _mountEditor(wrap, path, content, lang) {
    wrap.innerHTML = '';
    const gutter = document.createElement('div'); gutter.className = 'ce-gutter';
    const hlEl = document.createElement('div'); hlEl.className = 'ce-hl';
    const bg = document.createElement('div'); bg.className = 'ce-line-bg';
    const code = document.createElement('code'); code.className = 'ce-code';
    hlEl.append(bg, code);
    const ta = document.createElement('textarea'); ta.className = 'ce-input';
    ta.spellcheck = false; ta.value = content;
    ['wrap', 'autocomplete', 'autocapitalize', 'autocorrect'].forEach(a => ta.setAttribute(a, a === 'wrap' ? 'off' : 'off'));
    wrap.append(gutter, hlEl, ta);
    const cs = getComputedStyle(ta), lh = parseFloat(cs.lineHeight) || 21.25, pt = parseFloat(cs.paddingTop) || 14;
    let curLine = -1, gN = -1;
    const repaint = () => { code.innerHTML = renderCodeFlat(ta.value, lang) + '\n'; };
    const gutUpdate = () => {
      const n = ta.value.split('\n').length;
      if (n === gN) return; gN = n;
      let s = ''; for (let i = 1; i <= n; i++) s += '<i' + (i - 1 === curLine ? ' class="on"' : '') + '>' + i + '</i>';
      gutter.innerHTML = s;
    };
    const activeUpdate = () => {
      const line = ta.value.slice(0, ta.selectionStart).split('\n').length - 1;
      bg.style.top = (pt + line * lh) + 'px';
      if (line !== curLine) {
        gutter.children[curLine]?.classList.remove('on');
        gutter.children[line]?.classList.add('on');
        curLine = line;
      }
    };
    const syncScroll = () => { hlEl.scrollTop = ta.scrollTop; hlEl.scrollLeft = ta.scrollLeft; gutter.scrollTop = ta.scrollTop; };

    // ── Buscar en el archivo (Ctrl+F) ──────────────────────────────────────
    // Las cajas de coincidencia van como hijas directas de .ce-hl (como .ce-line-bg)
    // → scrollean con el contenido automáticamente.
    const _cw = (() => { const g = document.createElement('canvas').getContext('2d'); g.font = cs.fontSize + ' ' + cs.fontFamily; return g.measureText('0'.repeat(100)).width / 100 || 7.5; })();
    const gutPx = parseFloat(cs.paddingLeft) || 60;
    const clearMarks = () => hlEl.querySelectorAll('.ce-mark').forEach(m => m.remove());
    const findBar = document.createElement('div'); findBar.className = 'ce-find'; findBar.hidden = true;
    findBar.innerHTML = `<span class="ce-find-ico">${IC.search}</span><input class="ce-find-in" placeholder="Buscar…" spellcheck="false" autocomplete="off">` +
      `<span class="ce-find-count">0/0</span>` +
      `<button class="ce-find-btn" data-f="prev" title="Anterior (Shift+Enter)" tabindex="-1">${IC.up}</button>` +
      `<button class="ce-find-btn" data-f="next" title="Siguiente (Enter)" tabindex="-1">${IC.down}</button>` +
      `<button class="ce-find-btn" data-f="close" title="Cerrar (Esc)" tabindex="-1">${IC.x}</button>`;
    wrap.appendChild(findBar);
    const fIn = findBar.querySelector('.ce-find-in'), fCount = findBar.querySelector('.ce-find-count');
    let fMatches = [], fi = -1, fQ = '';
    const drawMarks = () => {
      clearMarks();
      for (let k = 0; k < fMatches.length; k++) {
        const m = fMatches[k], line = ta.value.slice(0, m).split('\n').length - 1, col = m - (ta.value.lastIndexOf('\n', m - 1) + 1);
        const box = document.createElement('div');
        box.className = 'ce-mark' + (k === fi ? ' cur' : '');
        box.style.cssText = `left:${gutPx + col * _cw}px;top:${pt + line * lh}px;width:${fQ.length * _cw}px;height:${lh}px`;
        hlEl.appendChild(box);
      }
    };
    const updCount = () => { fCount.textContent = fMatches.length ? (fi + 1) + '/' + fMatches.length : (fQ ? '0/0' : ''); };
    const gotoMatch = (i) => {
      if (!fMatches.length) return;
      fi = (i + fMatches.length) % fMatches.length;
      const line = ta.value.slice(0, fMatches[fi]).split('\n').length - 1;
      ta.scrollTop = Math.max(0, pt + line * lh - ta.clientHeight / 2);   // dispara syncScroll
      drawMarks(); updCount();
    };
    const runFind = () => {
      fQ = fIn.value; fMatches = [];
      if (fQ) { const hay = ta.value.toLowerCase(), nd = fQ.toLowerCase(); let idx = hay.indexOf(nd); while (idx !== -1) { fMatches.push(idx); idx = hay.indexOf(nd, idx + nd.length); } }
      fi = fMatches.length ? 0 : -1; drawMarks(); updCount();
      if (fi >= 0) gotoMatch(0);
      findBar.classList.toggle('nores', !!fQ && !fMatches.length);
    };
    const openFind = () => {
      findBar.hidden = false;
      const sel = ta.value.substring(ta.selectionStart, ta.selectionEnd);
      if (sel && !sel.includes('\n')) fIn.value = sel;
      fIn.focus(); fIn.select(); runFind();
    };
    const closeFind = () => { findBar.hidden = true; fMatches = []; fQ = ''; clearMarks(); findBar.classList.remove('nores'); ta.focus(); };
    fIn.addEventListener('input', runFind);
    fIn.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); gotoMatch(fi + (e.shiftKey ? -1 : 1)); }
      else if (e.key === 'Escape') { e.preventDefault(); closeFind(); }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') { e.preventDefault(); fIn.select(); }
    });
    findBar.querySelectorAll('.ce-find-btn').forEach(btn => btn.addEventListener('mousedown', e => {
      e.preventDefault();   // no robar el foco del input
      const f = btn.dataset.f;
      if (f === 'close') closeFind(); else if (f === 'next') gotoMatch(fi + 1); else gotoMatch(fi - 1);
    }));
    ta.addEventListener('input', () => {
      const buf = _cache[path];
      if (buf) buf.content = ta.value;
      repaint(); gutUpdate(); activeUpdate();
      // dirty = distinto del último GUARDADO. Si volvés el contenido a como estaba,
      // "sin guardar" se apaga solo (no basta con que hayas tocado algo).
      const nowDirty = !!(buf && ta.value !== buf.saved);
      const wasDirty = !!(buf && buf.dirty);
      if (nowDirty !== wasDirty) { _markDirty(path, nowDirty); _renderStatus(buf); }
    });
    ta.addEventListener('scroll', syncScroll);
    ta.addEventListener('keyup', activeUpdate);
    ta.addEventListener('click', activeUpdate);
    ta.addEventListener('keydown', e => {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === 's') { e.preventDefault(); e.stopPropagation(); _guardar(path); return; }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') { e.preventDefault(); e.stopPropagation(); openFind(); return; }
      if (e.key === 'Tab') {                          // Tab inserta 2 espacios (editor, no navegación)
        e.preventDefault();
        const s = ta.selectionStart, en = ta.selectionEnd;
        ta.value = ta.value.slice(0, s) + '  ' + ta.value.slice(en);
        ta.selectionStart = ta.selectionEnd = s + 2;
        ta.dispatchEvent(new Event('input'));
      }
    });
    repaint(); gutUpdate(); activeUpdate();
  }
  function _renderStatus(data) {
    const st = $('jw-ed-status'); if (!st) return;
    if (!data || data.binary) { st.innerHTML = `<span class="g"><span class="d"></span>master</span><span class="sp"></span>`; return; }
    const dirty = !!data.dirty;
    st.innerHTML = `<span class="g"><span class="d"></span>master</span>${dirty ? '<span class="dirty">sin guardar</span>' : ''}<span class="sp"></span><span>${esc(data.language || '')}</span><span>UTF-8</span><span>${_fmtSize(data.size)}</span>${dirty ? '<button class="ed-save" title="Guardar (Ctrl+S)">Guardar</button>' : ''}`;
    if (dirty) st.querySelector('.ed-save')?.addEventListener('click', () => _guardar(state.active));
  }
  function _markDirty(path, dirty) {
    const t = state.open.find(x => x.path === path); if (t) t.dirty = dirty;
    if (_cache[path]) _cache[path].dirty = dirty;
    const el = document.querySelector(`#jw-ed-tabs .ed-tab[data-tab="${CSS.escape(encodeURIComponent(path))}"]`);
    if (el) el.classList.toggle('dirty', dirty);
    // dot de "sin guardar" en el archivo del árbol de la franja (puede haber >1 nodo si varios proyectos comparten ruta)
    document.querySelectorAll(`.sb-ffile[data-path="${CSS.escape(path)}"]`).forEach(f => f.classList.toggle('jw-dirty', dirty));
  }
  function isDirty(path) { return !!(_cache[path] && _cache[path].dirty); }
  async function _guardar(path) {
    const buf = _cache[path];
    if (!buf || buf.binary || !buf.dirty) return;
    try {
      const res = await fetch(`/api/projects/${encodeURIComponent(_projId)}/files/save`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, content: buf.content }),
      });
      if (!res.ok) throw new Error(res.status);
      const d = await res.json();
      buf.dirty = false; buf.size = d.size; buf.saved = buf.content;   // nuevo punto de referencia = lo recién guardado
      _markDirty(path, false);
      if (state.active === path) _renderStatus(buf);
      try { window.toast?.('Guardado', 'ok'); } catch (_) {}
    } catch (_) {
      try { window.toast?.('No se pudo guardar (¿archivo protegido?)', 'error'); } catch (_) {}
    }
  }
  function _fmtSize(n) { n = +n || 0; return n < 1024 ? n + ' B' : (n / 1024).toFixed(1) + ' KB'; }

  // ════════════════ pestañas ════════════════
  function renderTabs(skipScroll) {
    const tabs = $('jw-ed-tabs'); if (!tabs) return;
    tabs.innerHTML = `<div class="ed-tablist" id="jw-ed-tablist">${state.open.map(t => `<button class="ed-tab ${t.path === state.active ? 'active' : ''} ${t.dirty ? 'dirty' : ''}" data-tab="${encodeURIComponent(t.path)}" title="${esc(t.path)}">${IC.file}<span class="tn">${esc(t.name)}</span><span class="x" data-close="${encodeURIComponent(t.path)}" aria-label="Cerrar ${esc(t.name)}">${IC.x}</span></button>`).join('')}</div>
      <div class="ed-drag-fill" title="Arrastrá para mover el editor"></div>
      <div class="ed-tools">
        <button class="ed-btn" id="jw-ed-maxbtn" title="Maximizar" aria-label="Maximizar">${IC.max}</button>
        <button class="ed-btn" id="jw-ed-closebtn" title="Cerrar editor" aria-label="Cerrar editor">${IC.close}</button>
      </div>`;
    tabs.querySelectorAll('.ed-tab').forEach(el => el.addEventListener('click', e => {
      if (e.target.closest('[data-close]')) return;
      if (performance.now() - _reorderEndAt < 260) return;
      state.active = decodeURIComponent(el.dataset.tab); renderActive(); renderTabs(true);
    }));
    tabs.querySelectorAll('[data-close]').forEach(x => x.addEventListener('click', e => { e.stopPropagation(); cerrarTab(decodeURIComponent(x.dataset.close)); }));
    $('jw-ed-maxbtn').addEventListener('click', () => {
      const c = contentEl(); c.classList.toggle('jw-ed-max');
      $('jw-ed-maxbtn').classList.toggle('on', c.classList.contains('jw-ed-max'));
      relayoutTerms();
    });
    $('jw-ed-closebtn').addEventListener('click', cerrar);
    wireDrag(); wireTabReorder();
    if (!skipScroll) $('jw-ed-tablist').querySelector('.ed-tab.active')?.scrollIntoView({ inline: 'nearest', block: 'nearest' });
  }
  function cerrarTab(path) {
    state.open = state.open.filter(t => t.path !== path);
    document.querySelectorAll('.sb-ffile.sel').forEach(f => { if (f.dataset.path === path) f.classList.remove('sel'); });
    if (state.active === path) {
      const nx = state.open[state.open.length - 1];
      // Cerrar la ÚLTIMA pestaña NO sale del editor (eso es solo el botón "cerrar editor"):
      // deja el editor abierto con un mensaje de vacío. Así tampoco redimensiona las terminales.
      if (nx) { state.active = nx.path; renderActive(); renderTabs(); }
      else { state.active = null; renderTabs(true); _renderEmpty(); }
    } else renderTabs();
  }
  function _renderEmpty() {
    const body = $('jw-ed-body');
    if (body) body.innerHTML = `<div class="ed-msg ed-empty">${IC.folderOpen}<div class="ed-empty-txt"><b>Ningún archivo abierto</b><span>Elegí un archivo del árbol de la izquierda para verlo o editarlo.</span></div></div>`;
    _renderStatus(null);
  }

  // ════════════════ drag del editor (dock izq⇄der, terminales en vivo) ════════════════
  let _dragWired = false;
  function wireDrag() {
    if (_dragWired) return; _dragWired = true;
    const ed = editorEl();
    ed.addEventListener('pointerdown', e => {
      const c = contentEl();
      if (c.classList.contains('jw-ed-max')) return;
      if (!e.target.closest('.ed-head') && !e.target.closest('.ed-drag-fill')) return;   // header o zona vacía de tabs — NUNCA la tira (su scrollbar)
      e.preventDefault();
      const terms = termsEl();
      const z = _z();
      const cr = _rectCss(c.getBoundingClientRect(), z);
      const rect = _rectCss(ed.getBoundingClientRect(), z);
      const grabX = e.clientX / z - rect.left, W = rect.width, x0 = e.clientX, y0 = e.clientY;
      const splitEl = $('jw-editor-split');
      const sw = (splitEl?.offsetWidth) || 6, travel = W + sw;
      // espacio que ocupa el dock a la derecha (si está abierto)
      const dockEl = $('jw-dock'), dsEl = $('jw-dock-splitter');
      const dockSpace = (dockEl && !dockEl.hidden ? dockEl.offsetWidth + (dsEl ? dsEl.offsetWidth : 0) : 0);
      const leftDock = cr.left, rightDock = Math.max(leftDock, cr.right - dockSpace - W), span = (rightDock - leftDock) || 1;
      const mid = (cr.left + (cr.right - dockSpace)) / 2;
      let started = false, clone = null, termsBase = 0, target = rect.left, cur = rect.left, raf;
      const setTerms = p => { const desired = cr.left + (1 - p) * travel; terms.style.transform = `translateX(${(desired - termsBase).toFixed(1)}px)`; };
      const begin = () => {
        started = true; c.classList.add('jw-ed-dragging'); setInteract(true);
        const realTab = $('jw-ed-tablist'), realScroll = realTab ? realTab.scrollLeft : 0;
        clone = ed.cloneNode(true); clone.id = ''; clone.classList.add('floating'); clone.classList.remove('jw-ed-slidein');
        clone.querySelectorAll('[id]').forEach(n => n.removeAttribute('id'));
        const ctab = clone.querySelector('.ed-tablist'); if (ctab) ctab.style.overflow = 'hidden';
        clone.style.cssText = `position:fixed;margin:0;top:${cr.top}px;height:${cr.height}px;width:${W}px;left:${rect.left}px;z-index:60;pointer-events:none`;
        document.body.appendChild(clone);
        if (ctab) ctab.scrollLeft = realScroll;
        ed.classList.remove('jw-ed-slidein');           // evita el re-trigger de la animación al flipear dock
        ed.style.visibility = 'hidden';
        terms.style.transition = 'none';
        termsBase = terms.getBoundingClientRect().left / z;
        const loop = () => {
          cur += (target - cur) * 0.16; clone.style.left = cur.toFixed(1) + 'px';
          clone.style.borderRadius = (cur + W / 2) < mid ? '0 16px 16px 0' : '16px 0 0 16px';
          setTerms(Math.max(0, Math.min(1, (cur - leftDock) / span)));
          raf = requestAnimationFrame(loop);
        };
        raf = requestAnimationFrame(loop);
      };
      const move = ev => {
        if (!started) { if (Math.abs(ev.clientX - x0) + Math.abs(ev.clientY - y0) < 5) return; begin(); }
        target = Math.max(leftDock, Math.min(rightDock, ev.clientX / z - grabX));
      };
      const up = () => {
        document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up);
        if (!started) return;
        cancelAnimationFrame(raf);
        const side = (cur + W / 2) < mid ? 'left' : 'right';
        clone.style.transition = 'left .22s var(--ez)';
        clone.style.left = (side === 'left' ? leftDock : rightDock) + 'px';
        terms.style.transition = 'transform .22s var(--ez)';
        setTerms(side === 'left' ? 0 : 1);
        const finish = () => {
          if (!clone) return;
          state.dock = side; c.dataset.editorDock = side;
          ed.style.visibility = ''; terms.style.transition = ''; terms.style.transform = '';
          clone.remove(); clone = null; c.classList.remove('jw-ed-dragging');
          setInteract(false); relayoutTerms();
        };
        clone.addEventListener('transitionend', finish, { once: true });
        setTimeout(finish, 280);
      };
      document.addEventListener('pointermove', move); document.addEventListener('pointerup', up);
    });
  }

  // ════════════════ reorder de pestañas ════════════════
  let _reorderWired = false;
  function wireTabReorder() {
    if (_reorderWired) return; _reorderWired = true;
    const host = $('jw-ed-tabs');
    host.addEventListener('pointerdown', e => {
      const tab = e.target.closest('.ed-tab');
      if (!tab || e.target.closest('[data-close]')) return;
      const list = $('jw-ed-tablist');
      const items = [...list.querySelectorAll('.ed-tab')];
      const from = items.indexOf(tab);
      if (items.length < 2 || from < 0) return;
      const GAP = 2;
      const rects = items.map(el => el.getBoundingClientRect());
      const centers0 = rects.map(r => r.left + r.width / 2);
      const outer = rects.map(r => r.width + GAP);
      const draggedOuter = outer[from];
      const scroll0 = list.scrollLeft, startX = e.clientX;
      let started = false, to = from, raf = null, px = startX;
      const frame = () => {
        const lr = list.getBoundingClientRect(), EDGE = 48, MAX = 17, maxScroll = list.scrollWidth - list.clientWidth;
        if (px < lr.left + EDGE && list.scrollLeft > 0) list.scrollLeft = Math.max(0, list.scrollLeft - MAX * Math.min(1, (lr.left + EDGE - px) / EDGE));
        else if (px > lr.right - EDGE && list.scrollLeft < maxScroll) list.scrollLeft = Math.min(maxScroll, list.scrollLeft + MAX * Math.min(1, (px - (lr.right - EDGE)) / EDGE));
        const sd = list.scrollLeft - scroll0;
        const dc = centers0[from] + (px - startX);
        let nt = from;
        for (let i = 0; i < centers0.length; i++) { if (i === from) continue; const ci = centers0[i] - sd; if (i < from && dc < ci) nt = Math.min(nt, i); if (i > from && dc > ci) nt = Math.max(nt, i); }
        to = nt;
        for (let i = 0; i < items.length; i++) { if (i === from) continue; let dx = 0; if (from < to && i > from && i <= to) dx = -draggedOuter; else if (from > to && i < from && i >= to) dx = draggedOuter; items[i].style.transform = dx ? `translateX(${dx}px)` : ''; }
        items[from].style.transform = `translateX(${(px - startX + sd).toFixed(1)}px)`;
        raf = requestAnimationFrame(frame);
      };
      const move = ev => {
        px = ev.clientX;
        if (!started) { if (Math.abs(ev.clientX - startX) < 4) return; started = true; tab.classList.add('reordering'); list.classList.add('reordering-active'); document.body.style.userSelect = 'none'; try { tab.setPointerCapture(ev.pointerId); } catch (_) {} raf = requestAnimationFrame(frame); }
        ev.preventDefault();
      };
      const up = () => {
        document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up);
        document.body.style.userSelect = '';
        if (!started) return;
        if (raf) { cancelAnimationFrame(raf); raf = null; }
        _reorderEndAt = performance.now();
        const sl = list.scrollLeft;
        const firsts = {}; items.forEach(el => { firsts[el.dataset.tab] = el.getBoundingClientRect().left; });
        if (to !== from) { const [it] = state.open.splice(from, 1); state.open.splice(to, 0, it); }
        renderTabs(true);
        const list2 = $('jw-ed-tablist'); list2.scrollLeft = sl; list2.classList.add('reordering-active');
        const news = [...list2.querySelectorAll('.ed-tab')];
        news.forEach(el => { const first = firsts[el.dataset.tab]; if (first == null) return; const dx = first - el.getBoundingClientRect().left; if (Math.abs(dx) < 0.5) return; el.style.transition = 'none'; el.style.transform = `translateX(${dx}px)`; });
        requestAnimationFrame(() => { news.forEach(el => { el.style.transition = 'transform .2s var(--ez)'; el.style.transform = ''; }); setTimeout(() => { news.forEach(el => { el.style.transition = ''; }); list2.classList.remove('reordering-active'); }, 230); });
      };
      document.addEventListener('pointermove', move); document.addEventListener('pointerup', up);
    });
  }

  // ════════════════ splitter (resize; invierte según el dock) ════════════════
  function wireSplitter() {
    const sp = $('jw-editor-split'), ed = editorEl(); if (!sp || !ed) return;
    let drag = false, x0, w0;
    sp.addEventListener('pointerdown', e => {
      drag = true; x0 = e.clientX; w0 = parseFloat(getComputedStyle(ed).width);
      sp.classList.add('drag'); try { sp.setPointerCapture(e.pointerId); } catch (_) {}
      document.body.style.userSelect = 'none'; setInteract(true);
    });
    sp.addEventListener('pointermove', e => {
      if (!drag) return;
      let d = (e.clientX - x0) / _z(); if (state.dock === 'right') d = -d;
      const cw = contentEl().getBoundingClientRect().width / _z();
      const w = Math.max(320, Math.min(cw * 0.72, w0 + d));
      ed.style.width = w + 'px'; state.w = w; relayoutTerms();
    });
    sp.addEventListener('pointerup', e => {
      if (!drag) return; drag = false; sp.classList.remove('drag');
      document.body.style.userSelect = ''; try { sp.releasePointerCapture(e.pointerId); } catch (_) {}
      setInteract(false); relayoutTerms();
    });
  }

  // ── init ──
  function init() {
    const c = contentEl(); if (c && !c.dataset.editorDock) c.dataset.editorDock = state.dock;
    wireSplitter();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  // Cierra la pestaña de un archivo (si está abierto) y limpia su buffer — lo usa el
  // borrado del árbol de la franja para que no quede una pestaña de un archivo ya eliminado.
  function cerrarArchivo(path) {
    if (_cache[path]) delete _cache[path];
    if (state.open.find(t => t.path === path)) cerrarTab(path);
  }
  global.JarvisSlideEditor = { abrirArchivo, cerrar, toggle, onProjectChanged, isOpen, isDirty, cerrarArchivo };
})(window);
