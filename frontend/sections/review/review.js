// JARVIS — Review Room (UI).
// Master-detail: lista de archivos AGRUPADA POR AGENTE (GET /review/by-agent) a la
// izquierda + diff del archivo activo a la derecha (Monaco diff, fallback a texto).
// Permite COMMITEAR una selección explícita de archivos (POST /review/commit —
// nunca `git add -A`) y ABRIR el archivo en el Editor.
// Expone window.JarvisReview = { init, onProjectChanged, mostrarEnPane }.

(() => {
  let _projectId = null;
  let _data = null;        // GET /review (branch, ultimo, archivos con stats, diff)
  let _byAgent = null;     // GET /review/by-agent (grupos por agente)
  let _selected = new Set();
  let _busqueda = '';
  let _activo = null;      // path con el diff abierto
  let _cargadoEn = 0;
  let _diffEditor = null;
  let _diffModelos = null;
  let _agoTimer = null;
  let _pollTimer = null;

  const esc = (s) => { const d = document.createElement('div'); d.textContent = String(s ?? ''); return d.innerHTML; };
  const escAttr = (s) => esc(s).replace(/"/g, '&quot;');
  const _t = (s) => (window.JarvisI18n && window.JarvisI18n.t) ? window.JarvisI18n.t(s) : s;
  const _L = (es, en) => (window.JarvisI18n?.lang?.() === 'en' ? en : es);
  const _pane = () => document.getElementById('jw-pane-review');
  const $ = (sel) => _pane()?.querySelector(sel) || null;

  // ── Carga ───────────────────────────────────────────────────────────
  async function _cargar() {
    const [r1, r2] = await Promise.all([
      fetch(`/api/projects/${_projectId}/review`),
      fetch(`/api/projects/${_projectId}/review/by-agent`).catch(() => null),
    ]);
    if (!r1.ok) {
      const e = await r1.json().catch(() => ({}));
      throw new Error(e.detail || `HTTP ${r1.status}`);
    }
    _data = await r1.json();
    _byAgent = (r2 && r2.ok) ? await r2.json() : null;
    _cargadoEn = Date.now();
    // Depurar la selección de paths que ya no existen.
    const vivos = new Set((_data.archivos || []).map(a => a.path));
    for (const p of [..._selected]) if (!vivos.has(p)) _selected.delete(p);
  }

  // ── Datos derivados ─────────────────────────────────────────────────
  function _statDe(path) {
    const a = (_data?.archivos || []).find(x => x.path === path);
    return { mas: a?.mas ?? '·', menos: a?.menos ?? '·', estado: a?.estado || 'M' };
  }

  function _grupos() {
    const grupos = [];
    if (_byAgent && ((_byAgent.agentes || []).length || (_byAgent.sin_atribuir || []).length)) {
      for (const ag of (_byAgent.agentes || [])) {
        grupos.push({
          id: 'ag' + ag.terminal_id,
          nombre: ag.nombre || `${_L('Agente', 'Agent')} ${ag.terminal_id}`,
          archivos: (ag.archivos || []).map(f => ({ path: f.path, diff: f.diff })),
        });
      }
      if ((_byAgent.sin_atribuir || []).length) {
        grupos.push({
          id: 'sin', nombre: _L('Sin atribuir', 'Unattributed'),
          archivos: (_byAgent.sin_atribuir || []).map(f => ({ path: f.path, diff: f.diff })),
        });
      }
    } else {
      grupos.push({
        id: 'all', nombre: _L('Cambios', 'Changes'),
        archivos: (_data?.archivos || []).map(a => ({ path: a.path })),
      });
    }
    return grupos;
  }

  function _total() {
    let n = 0, mas = 0, menos = 0;
    for (const a of (_data?.archivos || [])) {
      n++; mas += Number(a.mas) || 0; menos += Number(a.menos) || 0;
    }
    return { n, mas, menos };
  }

  function _coincide(path) { return !_busqueda || path.toLowerCase().includes(_busqueda); }

  // ── Render: topbar ──────────────────────────────────────────────────
  function _renderTop() {
    const b = $('.rv-branch');
    if (b) b.innerHTML = `${_L('en', 'on')} <b>${esc(_data?.branch || '?')}</b> · ${esc(_data?.ultimo || '')}`;
    const s = $('.rv-sum');
    if (s) {
      const t = _total();
      s.textContent = `${t.n} ${_L('archivos', 'files')} · +${t.mas} −${t.menos}`;
    }
    _renderAgo();
  }

  function _renderAgo() {
    const el = $('.rv-ago');
    if (!el || !_cargadoEn) return;
    const s = Math.max(0, Math.round((Date.now() - _cargadoEn) / 1000));
    el.textContent = s < 60
      ? _L(`actualizado hace ${s}s`, `updated ${s}s ago`)
      : _L(`actualizado hace ${Math.floor(s / 60)}m`, `updated ${Math.floor(s / 60)}m ago`);
  }

  // ── Render: lista agrupada ──────────────────────────────────────────
  function _renderLista() {
    const cont = $('.rv-lista');
    if (!cont) return;
    const html = [];
    for (const g of _grupos()) {
      const files = g.archivos.filter(f => _coincide(f.path));
      if (!files.length) continue;
      const todos = files.every(f => _selected.has(f.path));
      html.push(
        `<div class="rv-grupo">` +
        `<div class="rv-ghead">` +
        `<button type="button" class="rv-gsel${todos ? ' on' : ''}" data-sel="${escAttr(g.id)}" ` +
        `aria-pressed="${todos}" title="${_L('Marcar/desmarcar grupo', 'Toggle group')}">${todos ? '✓' : ''}</button>` +
        `<span class="rv-gname">${esc(g.nombre)}</span>` +
        `<span class="rv-gcount">${files.length}</span>` +
        `</div>`
      );
      for (const f of files) {
        const st = _statDe(f.path);
        const activo = f.path === _activo ? ' activo' : '';
        const marcado = _selected.has(f.path) ? ' checked' : '';
        html.push(
          `<div class="rv-fila${activo}" data-path="${escAttr(f.path)}" title="${escAttr(f.path)}">` +
          `<input type="checkbox" class="rv-check" data-path="${escAttr(f.path)}"${marcado} aria-label="${_L('Seleccionar', 'Select')}">` +
          `<span class="rv-estado ${esc(st.estado)}">${esc(st.estado)}</span>` +
          `<span class="rv-path">&lrm;${esc(f.path)}</span>` +
          `<span class="rv-stat"><span class="mas">+${esc(st.mas)}</span> <span class="menos">−${esc(st.menos)}</span></span>` +
          `<button type="button" class="rv-open" data-open="${escAttr(f.path)}" title="${_L('Abrir en el editor', 'Open in editor')}" aria-label="${_L('Abrir en el editor', 'Open in editor')}">${icon('file', 12)}</button>` +
          `</div>`
        );
      }
      html.push('</div>');
    }
    cont.innerHTML = html.join('') || `<div class="rv-vacio">${_L('Sin resultados', 'No results')}</div>`;
    _wireLista();
    _renderCommitBtn();
  }

  function _wireLista() {
    const cont = $('.rv-lista');
    if (!cont) return;
    cont.querySelectorAll('.rv-check').forEach(ch =>
      ch.addEventListener('change', () => {
        if (ch.checked) _selected.add(ch.dataset.path); else _selected.delete(ch.dataset.path);
        const row = ch.closest('.rv-fila');
        if (row) row.classList.toggle('sel', ch.checked);
        _renderCommitBtn();
      }));
    cont.querySelectorAll('.rv-fila').forEach(row =>
      row.addEventListener('click', (e) => {
        if (e.target.closest('.rv-check, .rv-open')) return;
        _seleccionar(row.dataset.path);
      }));
    cont.querySelectorAll('.rv-open').forEach(btn =>
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        window.JarvisEditor?.abrirArchivoEnLinea?.(btn.dataset.open, 1);
      }));
    cont.querySelectorAll('.rv-gsel').forEach(btn =>
      btn.addEventListener('click', () => {
        const g = _grupos().find(x => x.id === btn.dataset.sel);
        if (!g) return;
        const files = g.archivos.filter(f => _coincide(f.path));
        const todos = files.every(f => _selected.has(f.path));
        files.forEach(f => todos ? _selected.delete(f.path) : _selected.add(f.path));
        _renderLista();
      }));
  }

  function _renderCommitBtn() {
    const btn = $('#rv-commit');
    if (!btn) return;
    btn.disabled = _selected.size === 0;
    btn.textContent = `${_L('Commit', 'Commit')} (${_selected.size})`;
  }

  // ── Diff ────────────────────────────────────────────────────────────
  async function _monaco() {
    if (window.monaco) return window.monaco;
    try { await window.JarvisEditor?.cargarMonaco?.(); } catch (_) {}
    // El loader es asíncrono; esperamos a que el global esté disponible.
    for (let i = 0; i < 20 && !window.monaco; i++) {
      await new Promise((r) => setTimeout(r, 150));
    }
    return window.monaco || null;
  }

  function _diffsMap() {
    const map = new Map();
    if (_byAgent) {
      for (const ag of (_byAgent.agentes || [])) for (const f of (ag.archivos || [])) map.set(f.path, f.diff || '');
      for (const f of (_byAgent.sin_atribuir || [])) map.set(f.path, f.diff || '');
    }
    return map;
  }

  function _diffTextoDe(path) {
    const m = _diffsMap();
    if (m.has(path)) return m.get(path);
    const full = _data?.diff || '';
    for (const sec of full.split(/(?=^diff --git )/m)) {
      const mm = /^diff --git a\/(.+?) b\/(.+)$/m.exec(sec);
      if (mm && (mm[2] === path || mm[1] === path)) return sec;
    }
    return '';
  }

  function _renderDiffTexto(diff) {
    const out = [];
    for (const linea of diff.split('\n')) {
      if (linea.startsWith('diff --git')) {
        const path = (linea.split(' b/')[1] || '').trim();
        out.push(`<div class="rv-file-sec" data-path="${escAttr(path)}"><div class="rv-file-head">${esc(path)}</div>`);
      } else if (linea.startsWith('@@')) {
        out.push(`<div class="rv-linea hunk">${esc(linea)}</div>`);
      } else if (linea.startsWith('+') && !linea.startsWith('+++')) {
        out.push(`<div class="rv-linea add">${esc(linea)}</div>`);
      } else if (linea.startsWith('-') && !linea.startsWith('---')) {
        out.push(`<div class="rv-linea del">${esc(linea)}</div>`);
      } else if (linea.startsWith('+++') || linea.startsWith('---') || linea.startsWith('index ')
              || linea.startsWith('new file') || linea.startsWith('deleted file')
              || linea.startsWith('similarity') || linea.startsWith('rename ')) {
        out.push(`<div class="rv-linea meta">${esc(linea)}</div>`);
      } else {
        out.push(`<div class="rv-linea">${esc(linea)}</div>`);
      }
    }
    return `<div>${out.join('')}</div>`;
  }

  function _seleccionar(path) {
    _activo = path;
    _renderLista();
    _mostrarDiff(path);
  }

  async function _mostrarDiff(path) {
    const bar = $('.rv-filebar');
    if (bar) bar.innerHTML = `<span class="rv-fpath">${esc(path)}</span>`;
    const host = $('#rv-diff-host');
    const text = $('.rv-textdiff');
    if (text) { text.hidden = true; text.innerHTML = ''; }
    // El placeholder SOLO se pinta ANTES de crear el editor: una vez creado, su
    // DOM vive dentro de #rv-diff-host y NO se debe vaciar (lo desengancharía y
    // el panel quedaría en blanco en la 2ª selección).
    if (host && !_diffEditor) {
      host.hidden = false;
      host.innerHTML = `<div class="rv-vacio">${_L('Cargando diff…', 'Loading diff…')}</div>`;
    }

    let pair = null;
    try {
      const r = await fetch(`/api/projects/${_projectId}/review/file?path=${encodeURIComponent(path)}`);
      if (r.ok) pair = await r.json();
    } catch (_) {}
    if (_activo !== path) return;

    const monaco = await _monaco();
    if (_activo !== path) return;

    const sinContenido = !pair || (!pair.original && !pair.modified);
    if (!monaco || sinContenido || pair.binary || !host) {
      if (host) host.hidden = true;
      if (text) {
        text.hidden = false;
        const d = _diffTextoDe(path);
        text.innerHTML = d ? _renderDiffTexto(d)
          : `<div class="rv-vacio">${_L('Sin diff para este archivo', 'No diff for this file')}</div>`;
      }
      return;
    }

    host.hidden = false;
    if (!_diffEditor) {
      host.innerHTML = '';   // limpiar el placeholder SOLO antes de crear
      _diffEditor = monaco.editor.createDiffEditor(host, {
        readOnly: true, automaticLayout: true, theme: 'jarvis-dark',
        renderSideBySide: false, fontSize: 12, scrollBeyondLastLine: false,
        renderOverviewRuler: false, minimap: { enabled: false },
        originalEditable: false,
      });
    }
    if (_diffModelos) {
      try { _diffModelos.original.dispose(); } catch (_) {}
      try { _diffModelos.modified.dispose(); } catch (_) {}
    }
    const lang = pair.language || 'plaintext';
    _diffModelos = {
      original: monaco.editor.createModel(pair.original || '', lang),
      modified: monaco.editor.createModel(pair.modified || '', lang),
    };
    _diffEditor.setModel(_diffModelos);
    _diffEditor.layout();
  }

  // ── Commit ──────────────────────────────────────────────────────────
  async function _commit() {
    const inp = $('#rv-msg');
    const btn = $('#rv-commit');
    const msg = (inp?.value || '').trim();
    if (!msg) {
      toast(_L('Escribí un mensaje de commit', 'Write a commit message'), 'warning');
      inp?.focus();
      return;
    }
    if (!_selected.size) return;
    if (btn) btn.disabled = true;
    try {
      const r = await fetch(`/api/projects/${_projectId}/review/commit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ archivos: [..._selected], mensaje: msg }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.ok === false) throw new Error(d.error || d.detail || `HTTP ${r.status}`);
      toast(_L('Commiteado', 'Committed') + (d.commit ? ' ' + d.commit : ''), 'ok');
      _selected.clear();
      _activo = null;
      if (inp) inp.value = '';
      await _cargar();
      _render();
    } catch (e) {
      toast(String(e.message || e), 'error');
    } finally {
      _renderCommitBtn();
    }
  }

  // ── Render global ───────────────────────────────────────────────────
  function _render() {
    if (!_data) return;
    const body = $('.rv-body');
    const vacio = $('.rv-emptystate');
    if (_data.limpio) {
      if (body) body.hidden = true;
      if (vacio) vacio.hidden = false;
      _renderTop();
      return;
    }
    if (body) body.hidden = false;
    if (vacio) vacio.hidden = true;
    _renderTop();
    _renderLista();
    if (_activo) _mostrarDiff(_activo);
  }

  // Re-traduce los textos fijos (placeholders/títulos) al cambiar de idioma sin
  // reconstruir los inputs (no se pierde lo que el usuario escribió).
  function _alCambiarIdioma() {
    const pane = _pane();
    if (!pane || !pane.querySelector('.rv-modal')) return;
    const s = $('#rv-search');
    if (s) s.placeholder = _L('Filtrar archivos…', 'Filter files…');
    const m = $('#rv-msg');
    if (m) m.placeholder = _L('Mensaje de commit…', 'Commit message…');
    const r = pane.querySelector('.rv-refresh');
    if (r) { const t = _L('Refrescar', 'Refresh'); r.title = t; r.setAttribute('aria-label', t); }
    const emp = pane.querySelector('.rv-emptystate div');
    if (emp) emp.innerHTML = _L('Working tree limpio.<br>No hay nada para revisar — todo está commiteado.',
                                'Working tree clean.<br>Nothing to review — everything is committed.');
    if (!_activo) {
      const fb = pane.querySelector('.rv-fpath');
      if (fb) fb.textContent = _L('Elegí un archivo', 'Pick a file');
    }
    if (_data) { _renderTop(); _renderLista(); if (_activo) _mostrarDiff(_activo); }
  }

  function _montar() {
    const pane = _pane();
    if (!pane || pane.querySelector('.rv-modal')) return;    pane.innerHTML = `
      <div class="rv-modal" role="region" aria-label="Review Room">
        <div class="rv-top">
          <span class="rv-titulo">Review.</span>
          <span class="rv-branch"></span>
          <span class="rv-sum"></span>
          <span class="rv-spacer"></span>
          <span class="rv-ago"></span>
          <button class="rv-refresh" type="button" title="${_L('Refrescar', 'Refresh')}" aria-label="${_L('Refrescar', 'Refresh')}">${icon('refresh', 13)}</button>
        </div>
        <div class="rv-body">
          <div class="rv-side">
            <input type="text" class="rv-search" id="rv-search" placeholder="${_L('Filtrar archivos…', 'Filter files…')}" autocomplete="off" spellcheck="false">
            <div class="rv-lista"></div>
            <div class="rv-commit">
              <input type="text" class="rv-msg" id="rv-msg" placeholder="${_L('Mensaje de commit…', 'Commit message…')}" autocomplete="off">
              <button type="button" class="rv-commitbtn" id="rv-commit" disabled>Commit (0)</button>
            </div>
          </div>
          <div class="rv-main">
            <div class="rv-filebar"><span class="rv-fpath">${_L('Elegí un archivo', 'Pick a file')}</span></div>
            <div class="rv-diffhost" id="rv-diff-host"></div>
            <div class="rv-textdiff" hidden></div>
          </div>
        </div>
        <div class="rv-emptystate" hidden>
          <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12.5l5 5L20 6.5"/></svg>
          <div>${_L('Working tree limpio.<br>No hay nada para revisar — todo está commiteado.',
                    'Working tree clean.<br>Nothing to review — everything is committed.')}</div>
        </div>
      </div>`;
    pane.querySelector('.rv-refresh').addEventListener('click', async () => {
      try { await _cargar(); _render(); } catch (e) { toast(e.message, 'error'); }
    });
    pane.querySelector('#rv-search').addEventListener('input', (e) => {
      _busqueda = e.target.value.trim().toLowerCase();
      _renderLista();
    });
    pane.querySelector('#rv-commit').addEventListener('click', _commit);
    pane.querySelector('#rv-msg').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); _commit(); }
    });
    window.addEventListener('jarvis:lang', _alCambiarIdioma);
  }

  function _arrancarTimers() {
    if (!_agoTimer) _agoTimer = setInterval(_renderAgo, 1000);
    if (!_pollTimer) {
      _pollTimer = setInterval(async () => {
        const p = _pane();
        if (!p || p.hidden) return;
        if ($('#rv-msg')?.value) return;   // no recargar mientras se escribe el commit
        try { await _cargar(); _renderTop(); _renderLista(); } catch (_) {}
      }, 12000);
    }
  }

  async function mostrarEnPane() {
    _montar();
    const pane = _pane();
    if (!pane) return;
    _arrancarTimers();
    const lista = $('.rv-lista');
    if (lista) lista.innerHTML = `<div class="rv-vacio">${_L('Cargando…', 'Loading…')}</div>`;
    const pid = _projectId;
    try {
      await _cargar();
      if (pid !== _projectId) return;
      _render();
    } catch (e) {
      if (lista) lista.innerHTML = `<div class="rv-vacio">${_L('No se pudo cargar el review', 'Could not load review')}:<br>${esc(e.message)}</div>`;
    }
  }

  window.JarvisReview = {
    init(projectId) { _projectId = projectId; },
    onProjectChanged(projectId) {
      _projectId = projectId;
      _data = null; _byAgent = null; _activo = null; _selected = new Set();
      if (_diffModelos) {
        try { _diffModelos.original.dispose(); } catch (_) {}
        try { _diffModelos.modified.dispose(); } catch (_) {}
        _diffModelos = null;
      }
      const pane = _pane();
      if (pane && !pane.hidden) mostrarEnPane();
    },
    mostrarEnPane,
  };
})();
