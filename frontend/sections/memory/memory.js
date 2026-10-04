// JARVIS — Memoria compartida del proyecto (UI) · «Atlas» (rediseño 2026-09).
//
// La memoria como un ATLAS vivo del conocimiento del proyecto:
//   · Lista  — riel con buscador, filtro por estado, pulso de salud y cards
//              agrupadas por constelación (categoría); lector tipo documento
//              con vecindario (enlaza a / citada por) y editor con preview.
//   · Grafo  — constelaciones por categoría (force layout animado que se
//              APAGA al asentarse), nebulosas de color, foco por hover,
//              click abre, arrastrar mueve, rueda = zoom.
//   · Live   — el enjambre en vivo (Pulso = carriles por agente / Mapa =
//              agentes↔archivos) + línea de tiempo de Recall (qué memorias
//              sugirió el sistema y cuáles leyeron los agentes) y Actividad.
// La barra de constelaciones (filtro por categoría) la comparten Lista y Grafo.
//
// Los agentes escriben las memorias como archivos (.jarvis/memory/) según el
// protocolo inyectado en CLAUDE.md; esta UI es la ventana del humano.
// Expone window.JarvisMemory = { init, onProjectChanged, abrir, onLiveEvent }.
// Lógica pura: memory-meta.js (JarvisMemoryMeta), memory-graph.js
// (JarvisMemoryGraph), live-state.js (JarvisLiveState).

(() => {
  let _projectId = null;
  let _memorias  = [];
  let _edges     = [];
  let _salud     = null;          // lint del backend (/memory/salud)
  let _uso       = { eventos: [], conteo: {} };   // stream de recall (/memory/uso)
  let _cargando  = false;
  let _error     = false;
  let _errorDetalle = "";

  let _tab       = 'lista';       // 'lista' | 'grafo' | 'live' | 'resumen'
  const TABS = ['lista', 'grafo', 'live', 'resumen'];
  let _slugAbierta = null;
  let _modo      = 'ver';         // lector: 'ver' | 'editar' | 'nueva'
  let _leyendo   = false;         // ancho angosto: el lector tapa al riel
  let _query     = '';
  let _cats      = [];            // filtro de constelaciones ([] = todas)
  let _estado    = 'todas';       // filtro de estado
  let _saludFiltro = null;        // { k, slugs } — chip de salud activo
  let _visibles  = [];            // orden visible de la lista (navegación con flechas)

  // Grafo
  let _grafPos   = {};            // slug → {x,y} (layout persistente entre renders)
  let _grafT     = null;          // {k,x,y} zoom + paneo
  let _grafRaf   = 0;
  let _grafTimer = null;          // disparos neuronales
  let _grafAC    = null;          // listeners de window del grafo
  let _grafRO    = null;
  let _grafEtiq  = false;         // forzar todas las etiquetas

  // Live
  let _liveEstado = null;         // estado de JarvisLiveState
  let _liveVista  = 'pulso';      // 'pulso' | 'mapa'
  let _liveRail   = 'recall';     // 'recall' | 'actividad'
  let _liveTimer  = null;         // limpieza de flashes
  let _livePoll   = null;         // refresco del recall mientras Live está a la vista
  let _mapaPos    = {};           // id de nodo → {x,y} del Mapa del enjambre

  const esc = (s) => { const d = document.createElement('div'); d.textContent = String(s ?? ''); return d.innerHTML; };
  const _t = (s) => (window.JarvisI18n && window.JarvisI18n.t) ? window.JarvisI18n.t(s) : s;
  const Meta = () => window.JarvisMemoryMeta;
  const _reducido = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const _ls = {
    get(k, def) { try { const v = localStorage.getItem(k); return v === null ? def : v; } catch { return def; } },
    set(k, v)   { try { localStorage.setItem(k, v); } catch { /* storage bloqueado */ } },
  };

  const LOCK = '<svg class="mem-lock" viewBox="0 0 24 24" width="10" height="10" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>';
  const LINK = '<svg viewBox="0 0 24 24" width="11" height="11" aria-hidden="true"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></svg>';
  const CHEV = '<svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>';
  const ORBE = `<svg viewBox="0 0 32 32" width="20" height="20" aria-hidden="true">
      <path class="l" d="M7 22L14 9l11 5-6 11z"/><path class="l" d="M14 9l5 16"/>
      <circle cx="7" cy="22" r="2.4"/><circle cx="14" cy="9" r="3"/><circle cx="25" cy="14" r="2.2"/><circle cx="19" cy="25" r="2.6"/></svg>`;

  const NOMBRES_SALUD = {
    rotos: 'links rotos', citas: 'citas muertas', huerfanas: 'huérfanas',
    contrato: 'no cumplen contrato', choques: 'choques', cuarentena: 'en cuarentena',
    guard: 'candidatas a guard', duplicados: 'duplicadas', global: 'candidatas a global',
  };
  const NOMBRE_MARCA = {
    rotos: 'link roto', citas: 'cita muerta', huerfanas: 'huérfana', contrato: 'contrato incompleto',
    choques: 'choque', cuarentena: 'en cuarentena', duplicados: 'duplicada',
  };
  const FILTRABLES = new Set(['rotos', 'citas', 'huerfanas', 'contrato', 'choques', 'cuarentena', 'duplicados']);

  /* ══ Datos ═══════════════════════════════════════════════════════ */
  async function _cargar() {
    const pid = _projectId;
    _cargando = true; _error = false; _errorDetalle = '';
    // Los tres pedidos salen JUNTOS (antes eran en fila: la pantalla tardaba la suma y
    // el esqueleto se veía parpadear antes del contenido).
    const pMem = fetch(`/api/projects/${pid}/memory`).catch(() => null);
    const pSalud = fetch(`/api/projects/${pid}/memory/salud`).catch(() => null);
    const pUso = _cargarUso();
    const r = await pMem;
    try {
      if (!r) throw new Error('sin respuesta del servidor');
      if (!r.ok) {
        let det = '';
        try { det = (await r.json()).detail || ''; } catch { /* cuerpo no JSON */ }
        throw new Error(`HTTP ${r.status}${det ? ' · ' + det : ''}`);
      }
      const data = await r.json();
      if (pid !== _projectId) return;
      _memorias = data.memorias || [];
      _edges    = data.edges || [];
    } catch (e) { _error = true; _errorDetalle = String((e && e.message) || e); }
    try {
      const rs = await pSalud;
      _salud = rs && rs.ok ? await rs.json() : null;
    } catch { _salud = null; }
    await pUso;
    _cargando = false;
  }

  async function _cargarUso() {
    const pid = _projectId;
    try {
      const r = await fetch(`/api/projects/${pid}/memory/uso?n=90`);
      if (r.ok && pid === _projectId) _uso = await r.json();
    } catch { /* red: el stream queda como estaba */ }
  }

  const _mem = (slug) => _memorias.find(m => m.slug === slug);
  const _hue = (m) => Meta().categoria(m?.categoria).hue;
  const _catAttr = (catId) => {
    const c = Meta().categoria(catId);
    return `style="--h:${c.hue}"${c.neutra ? ' data-neutra' : ''}`;
  };
  const _filtradas = () => Meta().filtrar(_memorias, {
    q: _query, cats: _cats, estado: _estado, slugs: _saludFiltro ? _saludFiltro.slugs : null,
  });
  const _lecturas = (slug) => ((_uso.conteo || {})[slug] || {}).leida || 0;

  const _badgesHTML = (m) =>
    (Meta() ? Meta().badges(m) : [])
      .map(b => `<span class="mem-badge mem-badge-${b.k}">${b.k === 'leccion' ? icon('sparkle', 9) : ''}${b.label}</span>`).join('');

  function _md(src) {
    return Meta().markdown(src, { wikiIcon: icon('sparkle', 9) });
  }

  /* ══ Modal ═══════════════════════════════════════════════════════ */
  function _onKey(e) {
    if (!document.querySelector('.mem-overlay')) return;
    if (document.querySelector('.ob-confirm-overlay')) return;   // el confirm maneja su Esc
    const enCampo = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target?.tagName || '') || e.target?.isContentEditable;
    if (e.key === 'Escape') {
      e.preventDefault();
      if (_tab === 'lista' && (_modo === 'editar' || _modo === 'nueva')) { _modo = 'ver'; _renderReader(); return; }
      if (enCampo && e.target.value) { e.target.value = ''; e.target.dispatchEvent(new Event('input')); return; }
      // Angosto: el lector tapa al riel → Esc vuelve a la lista antes de cerrar.
      const rail = document.querySelector('.mem-lista-v .mem-rail');
      if (_tab === 'lista' && _leyendo && rail && getComputedStyle(rail).display === 'none') {
        _leyendo = false; _aplicarLeyendo(); return;
      }
      _cerrar();
      return;
    }
    if (enCampo || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === '/') {
      const s = document.getElementById(_tab === 'grafo' ? 'mem-graf-buscar' : 'mem-search');
      if (s) { e.preventDefault(); s.focus(); s.select(); }
      return;
    }
    if (e.key >= '1' && e.key <= '4' && e.key.length === 1) { _setTab(TABS[+e.key - 1]); return; }
    if (_tab === 'lista' && _modo === 'ver') {
      if (e.key === 'ArrowDown' || e.key === 'j') { e.preventDefault(); _mover(1); }
      else if (e.key === 'ArrowUp' || e.key === 'k') { e.preventDefault(); _mover(-1); }
      else if (e.key === 'e' && _slugAbierta) { e.preventDefault(); _modo = 'editar'; _renderReader(); }
      else if (e.key === 'n') { e.preventDefault(); _crear(); }
    }
  }

  function _mover(d) {
    if (!_visibles.length) return;
    const i = _visibles.indexOf(_slugAbierta);
    const j = i < 0 ? 0 : Math.max(0, Math.min(_visibles.length - 1, i + d));
    _abrirSlug(_visibles[j], { scroll: true });
  }

  function _pararGrafo() {
    cancelAnimationFrame(_grafRaf); _grafRaf = 0;
    clearInterval(_grafTimer); _grafTimer = null;
    _grafAC?.abort(); _grafAC = null;
    _grafRO?.disconnect(); _grafRO = null;
  }
  function _pararLive() {
    clearTimeout(_liveTimer); _liveTimer = null;
    clearInterval(_livePoll); _livePoll = null;
  }

  // A dónde volver al cerrar (✕, Esc o click afuera): si se abrió desde
  // ⚙ → Memoria, el usuario vuelve a Configuración, que es de donde venía.
  let _alCerrar = null;

  function _cerrar() {
    _pararGrafo(); _pararLive();
    document.removeEventListener('keydown', _onKey);
    const volver = _alCerrar; _alCerrar = null;
    const ov = document.querySelector('.mem-overlay');
    if (!ov) return;
    if (typeof volver === 'function') { try { volver(); } catch (_) {} }
    if (_reducido()) { ov.remove(); return; }
    ov.classList.add('saliendo');
    setTimeout(() => ov.remove(), 160);
  }

  // opts.tab: abrir directo en una vista ('resumen' desde ⚙ → Memoria, etc.).
  // opts.alCerrar: callback al cerrar (⚙ lo usa para que la ✕ vuelva a Configuración).
  async function abrir(opts) {
    _alCerrar = (opts && typeof opts.alCerrar === 'function') ? opts.alCerrar : null;
    document.querySelector('.mem-overlay')?.remove();
    _pararGrafo(); _pararLive();
    document.removeEventListener('keydown', _onKey);
    _tab = (opts && TABS.includes(opts.tab)) ? opts.tab : _ls.get('jarvis.mem.tab', 'lista');
    if (!TABS.includes(_tab)) _tab = 'lista';
    _liveRail = _ls.get('jarvis.mem.rail', 'recall') === 'actividad' ? 'actividad' : 'recall';
    _modo = 'ver'; _leyendo = false; _saludFiltro = null;

    const ov = document.createElement('div');
    ov.className = 'mem-overlay' + (opts && opts.instantaneo ? ' sin-entrada' : '');
    ov.innerHTML = `
      <div class="mem-modal" role="dialog" aria-modal="true" aria-label="Memoria del proyecto">
        <div class="mem-aura" aria-hidden="true"></div>
        <header class="mem-top">
          <div class="mem-marca">
            <span class="mem-orbe">${ORBE}</span>
            <div class="mem-marca-tx">
              <h2 class="mem-titulo">Memoria</h2>
              <span class="mem-count" id="mem-count">…</span>
            </div>
          </div>
          <nav class="mem-tabs" role="tablist" aria-label="Vistas de la memoria">
            <span class="mem-tabs-thumb" aria-hidden="true"></span>
            <button class="mem-tab" data-tab="lista" role="tab" type="button" title="Lista (1)">${icon('list-checks', 13)}<span>Lista</span></button>
            <button class="mem-tab" data-tab="grafo" role="tab" type="button" title="Grafo (2)">${icon('brain', 13)}<span>Grafo</span></button>
            <button class="mem-tab" data-tab="live" role="tab" type="button" title="Live (3)"><span class="mem-live-dot" aria-hidden="true"></span><span>Live</span></button>
            <button class="mem-tab" data-tab="resumen" role="tab" type="button" title="Resumen (4)">${icon('chart', 13)}<span>Resumen</span></button>
          </nav>
          <div class="mem-top-acc">
            <button class="mem-nueva" id="mem-nueva" type="button" title="Nueva memoria (N)">${icon('plus', 12)}<span>Nueva</span></button>
            <button class="mem-cerrar" id="mem-cerrar" type="button" aria-label="Cerrar" title="Cerrar (Esc)">${icon('x', 14)}</button>
          </div>
        </header>
        <div class="mem-constel" id="mem-constel"></div>
        <main class="mem-body" id="mem-body">
          <div class="mem-cargando" role="status"></div>
        </main>
      </div>`;
    document.body.appendChild(ov);

    ov.addEventListener('pointerdown', (e) => { ov._downFuera = e.target === ov; });
    ov.addEventListener('click', (e) => { if (e.target === ov && ov._downFuera) _cerrar(); });
    ov.querySelector('#mem-cerrar').addEventListener('click', _cerrar);
    ov.querySelector('#mem-nueva').addEventListener('click', _crear);
    ov.querySelectorAll('.mem-tab').forEach(t => t.addEventListener('click', () => _setTab(t.dataset.tab)));
    document.addEventListener('keydown', _onKey);
    _marcarTabs();

    // Esqueleto SOLO si la carga tarda (>220 ms): con respuesta rápida el contenido entra
    // directo, sin el flash esqueleto → contenido que se veía como un parpadeo.
    const cuerpo = ov.querySelector('.mem-cargando');
    const tSkel = setTimeout(() => { if (_cargando && cuerpo && cuerpo.isConnected) cuerpo.innerHTML = _skeleton(); }, 220);
    ov.classList.add('inicial');           // sin animaciones de entrada por tarjeta en el 1.er pintado
    await _cargar();
    clearTimeout(tSkel);
    if (!document.body.contains(ov)) return;
    if (!_slugAbierta || !_mem(_slugAbierta)) _slugAbierta = _ordenLista(_filtradas())[0]?.slug || null;
    _renderTodo();
    setTimeout(() => ov.classList.remove('inicial'), 700);
  }

  function _skeleton() {
    return `<div class="mem-skel">${Array.from({ length: 6 }, (_, i) =>
      `<span style="--i:${i}"></span>`).join('')}</div>`;
  }

  function _marcarTabs() {
    const i = TABS.indexOf(_tab);
    const nav = document.querySelector('.mem-tabs');
    if (!nav) return;
    nav.style.setProperty('--i', i);
    nav.querySelectorAll('.mem-tab').forEach(t => {
      const on = t.dataset.tab === _tab;
      t.classList.toggle('activo', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    document.querySelector('.mem-modal')?.setAttribute('data-vista', _tab);
  }

  function _setTab(t) {
    if (!t || t === _tab && document.getElementById('mem-body')?.dataset.vista === t) return;
    _tab = t;
    _ls.set('jarvis.mem.tab', t);
    _pararGrafo(); _pararLive();
    _marcarTabs();
    _renderTodo();
  }

  function _renderTodo() {
    _renderCount();
    _renderConstel();
    _renderBody();
  }

  function _renderCount() {
    const c = document.getElementById('mem-count');
    if (!c) return;
    const nCats = Meta().contarCategorias(_memorias).length;
    c.textContent = _t('{n} memorias · {e} enlaces · {c} constelaciones')
      .replace('{n}', _memorias.length).replace('{e}', _edges.length).replace('{c}', nCats);
  }

  /* ── Barra de constelaciones (filtro por categoría, Lista + Grafo) ── */
  function _renderConstel() {
    const bar = document.getElementById('mem-constel');
    if (!bar) return;
    if (_tab === 'live' || _tab === 'resumen' || !_memorias.length) { bar.hidden = true; bar.innerHTML = ''; return; }
    bar.hidden = false;
    const M = Meta();
    const probCat = {};
    for (const f of M.categoriasSalud(_salud)) probCat[f.id] = f.problemas;
    bar.innerHTML = `
      <div class="mem-constel-scroll" role="toolbar" aria-label="Constelaciones">
        <button type="button" class="mem-cat mem-cat-todo${_cats.length ? '' : ' activo'}" data-cat="">
          <span>Todo</span><b>${_memorias.length}</b>
        </button>
        ${M.contarCategorias(_memorias).map(c => `
          <button type="button" class="mem-cat mc${_cats.includes(c.id) ? ' activo' : ''}" data-cat="${esc(c.id)}"
                  style="--h:${c.hue}"${c.neutra ? ' data-neutra' : ''} aria-pressed="${_cats.includes(c.id)}">
            <i class="mem-cat-dot" aria-hidden="true"></i><span>${esc(c.nombre)}</span><b>${c.n}</b>
            ${probCat[c.id] ? `<em class="mem-cat-warn" title="${esc(_t('{n} problemas de salud').replace('{n}', probCat[c.id]))}">${probCat[c.id]}</em>` : ''}
          </button>`).join('')}
      </div>`;
    bar.querySelectorAll('.mem-cat').forEach(b => b.addEventListener('click', (ev) => {
      const id = b.dataset.cat;
      if (!id) _cats = [];
      else if (ev.shiftKey || ev.ctrlKey || ev.metaKey) {
        _cats = _cats.includes(id) ? _cats.filter(x => x !== id) : [..._cats, id];
      } else {
        _cats = (_cats.length === 1 && _cats[0] === id) ? [] : [id];
      }
      _renderConstel();
      if (_tab === 'grafo') _grafAplicarFiltro();
      else { _renderItems(); }
    }));
  }

  function _renderBody() {
    const body = document.getElementById('mem-body');
    if (!body) return;
    body.dataset.vista = _tab;
    if (_error && !_memorias.length && _tab !== 'live' && _tab !== 'resumen') {
      body.innerHTML = `<div class="mem-estado-vacio mem-error">
          <span class="mem-vacio-icono">${icon('alert', 22)}</span>
          <b>No pude leer la memoria del proyecto.</b>
          <span>Revisá que el servidor esté vivo y probá de nuevo.</span>
          ${_errorDetalle ? `<code class="mem-error-det" data-i18n-skip>${esc(_errorDetalle)}</code>` : ''}
          <button type="button" class="mem-btn" id="mem-reintentar">${icon('refresh', 12)} Reintentar</button>
        </div>`;
      body.querySelector('#mem-reintentar').addEventListener('click', async () => {
        body.innerHTML = `<div class="mem-cargando">${_skeleton()}</div>`;
        await _cargar(); _renderTodo();
      });
      return;
    }
    if (_tab === 'grafo') { _renderGrafo(body); return; }
    if (_tab === 'live')  { _renderLive(body);  return; }
    if (_tab === 'resumen') { _renderResumen(body); return; }
    _renderLista(body);
  }

  /* ══ RESUMEN ═════════════════════════════════════════════════════
     Lo que antes vivía en ⚙ → Memoria (y obligaba a tocar "Abrir" para
     llegar al panel): el pulso de un vistazo, la salud, los tableros por
     constelación, lo último tocado y el destilador de lecciones. Todo es
     navegable: una constelación filtra la Lista, una memoria se abre, un
     problema de salud filtra la Lista por esas memorias. */
  function _renderResumen(body) {
    const M = Meta();
    const total = _memorias.length;
    const punt = M.puntajeSalud(_salud, total);
    const probs = M.problemasSalud(_salud);
    const alt = (_salud && _salud.altimetro) || {};
    const hayTel = (alt.inyecciones || 0) > 0;
    const tasa = hayTel && alt.tasa_lectura != null ? Math.round(alt.tasa_lectura * 100) : null;
    const lec = (_salud && _salud.lecciones) || null;
    const cats = M.resumenCategorias(_memorias, _salud);
    const maxCat = Math.max(1, ...cats.map(c => c.total));
    const rec = M.recientes(_memorias, 7);
    const C = 2 * Math.PI * 22;
    const nivel = !punt ? 'medio' : punt.pct >= 90 ? 'ok' : punt.pct >= 60 ? 'medio' : 'bajo';

    const tiles = `
      <div class="mem-res-tiles">
        <div class="mem-res-tile">
          <span class="mem-res-k">Memorias</span>
          <b class="mem-res-num">${total}</b>
          <span class="mem-res-sub">${_t('{e} enlaces · {c} constelaciones').replace('{e}', _edges.length).replace('{c}', cats.length)}</span>
        </div>
        <div class="mem-res-tile nivel-${nivel}">
          <span class="mem-res-k">Salud</span>
          <div class="mem-res-anillo">
            <svg viewBox="0 0 50 50" width="54" height="54" aria-hidden="true">
              <circle class="pista" cx="25" cy="25" r="22"/>
              <circle class="valor" cx="25" cy="25" r="22" style="stroke-dasharray:${(C * (punt ? punt.pct : 0) / 100).toFixed(1)} ${C.toFixed(1)}"/>
            </svg>
            <b>${punt ? punt.pct : '—'}<small>${punt ? '%' : ''}</small></b>
          </div>
          <span class="mem-res-sub">${punt ? _t('{s} de {n} sin problemas').replace('{s}', punt.sanas).replace('{n}', total) : _t('sin datos')}</span>
        </div>
        <div class="mem-res-tile">
          <span class="mem-res-k" title="De las memorias que el recall inyectó a los prompts, cuántas leyeron de verdad los agentes">${_t('Recall · {d} días').replace('{d}', alt.dias || 7)}</span>
          <b class="mem-res-num">${tasa != null ? tasa + '<small>%</small>' : '—'}</b>
          ${tasa != null ? `<span class="mem-res-bar"><i style="width:${Math.min(100, tasa)}%"></i></span>` : ''}
          <span class="mem-res-sub">${hayTel
            ? _t('{i} inyectadas · {l} leídas · {d} en pasos OK').replace('{i}', alt.inyecciones || 0).replace('{l}', alt.lecturas || 0).replace('{d}', alt.lecturas_en_done || 0)
            : _t('todavía no se registraron inyecciones en esta ventana')}</span>
        </div>
      </div>`;

    const salud = `
      <section class="mem-res-sec">
        <h3 class="mem-res-h">Salud <span>lo que el linter encontró</span></h3>
        <div class="mem-res-probs">${probs.length
          ? probs.map(p => FILTRABLES.has(p.k)
              ? `<button type="button" class="mem-prob" data-k="${p.k}"><b>${p.n}</b><span>${NOMBRES_SALUD[p.k] || p.k}</span></button>`
              : `<span class="mem-prob estatico"><b>${p.n}</b><span>${NOMBRES_SALUD[p.k] || p.k}</span></span>`).join('')
          : `<span class="mem-res-ok">${icon('check', 12)} Sin links rotos, huérfanas ni choques.</span>`}</div>
      </section>`;

    const tableros = cats.length ? `
      <section class="mem-res-sec">
        <h3 class="mem-res-h">Constelaciones <span>memorias por categoría</span></h3>
        <div class="mem-res-cats">${cats.map(c => `
          <button type="button" class="mem-res-cat mc" data-cat="${esc(c.id)}" style="--h:${c.hue}"${c.neutra ? ' data-neutra' : ''}>
            <i class="mem-cat-dot" aria-hidden="true"></i>
            <span class="mem-res-cat-n">${esc(c.nombre)}</span>
            <span class="mem-res-cat-b"><i style="width:${Math.round(c.total / maxCat * 100)}%"></i></span>
            ${c.problemas ? `<em class="mem-cat-warn" title="${esc(_t('{n} problemas de salud').replace('{n}', c.problemas))}">${c.problemas}</em>` : ''}
            <b>${c.total}</b>
          </button>`).join('')}</div>
      </section>` : '';

    const recientes = `
      <section class="mem-res-sec">
        <h3 class="mem-res-h">Recientes <span>lo último que se tocó</span></h3>
        ${rec.length ? `<div class="mem-res-rec">${rec.map(m => `
          <button type="button" class="mem-res-mem mc" data-slug="${esc(m.slug)}" ${_catAttr(m.categoria)}>
            <i class="mem-cat-dot" aria-hidden="true"></i>
            <span class="mem-res-mem-t"><b data-i18n-skip>${esc(M.textoPlano(m.titulo) || m.slug)}</b>
              <span data-i18n-skip>${esc(M.textoPlano(m.resumen || ''))}</span></span>
            <span class="mem-res-fecha">${esc(M.fechaRelativa(m.actualizado))}</span>
          </button>`).join('')}</div>`
          : `<div class="mem-res-vacio">Todavía no hay memorias.</div>`}
      </section>`;

    const lecciones = `
      <section class="mem-res-sec">
        <h3 class="mem-res-h">Lecciones <span>lo que el enjambre aprendió</span></h3>
        <div class="mem-res-lec${lec && lec.activo ? '' : ' off'}">
          <span class="mem-res-lec-ico">${icon('sparkle', 16)}</span>
          <span class="mem-res-lec-tx">
            <b>Destilador de lecciones</b>
            <span>Junta los motivos de los pasos trabados y los convierte en reglas cortas que se inyectan a TODOS los agentes.</span>
            ${lec ? `<span class="mem-res-lec-n">${_t('{n} lecciones · {s} señales pendientes (destila a las {u})')
              .replace('{n}', lec.lecciones_memoria || 0).replace('{s}', lec.senales_pendientes || 0).replace('{u}', lec.umbral || 6)}</span>` : ''}
          </span>
          <span class="mem-res-pill ${lec && lec.activo ? 'on' : ''}">${lec && lec.activo ? 'activo' : 'apagado'}</span>
        </div>
      </section>`;

    body.innerHTML = `<div class="mem-resumen">${tiles}<div class="mem-res-grid">
        <div class="mem-res-col">${tableros}${salud}</div>
        <div class="mem-res-col">${recientes}${lecciones}</div>
      </div></div>`;

    body.querySelectorAll('.mem-res-cat').forEach(b => b.addEventListener('click', () => {
      _cats = [b.dataset.cat]; _query = ''; _estado = 'todas'; _saludFiltro = null;
      _setTab('lista');
    }));
    body.querySelectorAll('.mem-res-mem').forEach(b => b.addEventListener('click', () => _abrirSlug(b.dataset.slug)));
    body.querySelectorAll('.mem-res-probs .mem-prob[data-k]').forEach(b => b.addEventListener('click', () => {
      _cats = []; _query = ''; _estado = 'todas';
      _saludFiltro = { k: b.dataset.k, slugs: Meta().slugsDe(_salud, b.dataset.k) };
      _setTab('lista');
    }));
  }

  /* ══ LISTA ═══════════════════════════════════════════════════════ */
  function _renderLista(body) {
    const est = Meta().contarEstados(_memorias);
    const ESTADOS = [['todas', 'Todas'], ['vigente', 'Vigentes'], ['leccion', 'Lecciones'],
      ['obsoleta', 'Obsoletas'], ['lapida', 'Lápidas'], ['archivo', 'Archivo']]
      .filter(([k]) => k === 'todas' || est[k]);
    if (!ESTADOS.some(([k]) => k === _estado)) _estado = 'todas';

    body.innerHTML = `
      <div class="mem-lista-v${_leyendo ? ' leyendo' : ''}" id="mem-lista-v">
        <aside class="mem-rail">
          <div class="mem-rail-top">
            <label class="mem-search">
              ${icon('search', 13)}
              <input type="text" id="mem-search" placeholder="Buscar en la memoria…" value="${esc(_query)}"
                     autocomplete="off" spellcheck="false" aria-label="Buscar en la memoria">
              <kbd>/</kbd>
            </label>
            ${ESTADOS.length > 1 ? `<div class="mem-estados" role="group" aria-label="Filtrar por estado">
              ${ESTADOS.map(([k, l]) => `<button type="button" class="mem-est${_estado === k ? ' activo' : ''}" data-est="${k}" data-k="${k}">
                <span>${l}</span><b>${est[k]}</b></button>`).join('')}
            </div>` : ''}
          </div>
          <div class="mem-rail-scroll" id="mem-rail-scroll">
            ${_saludHTML()}
            <div class="mem-items" id="mem-items" role="listbox" aria-label="Memorias"></div>
          </div>
        </aside>
        <section class="mem-reader" id="mem-reader" aria-live="polite"></section>
      </div>`;

    const inp = body.querySelector('#mem-search');
    inp.addEventListener('input', () => { _query = inp.value; _renderItems(); });
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); inp.blur(); _mover(_slugAbierta ? 0 : 1); }
      if (e.key === 'Enter' && _visibles[0]) { _abrirSlug(_visibles[0]); }
    });
    body.querySelectorAll('.mem-est').forEach(b => b.addEventListener('click', () => {
      _estado = b.dataset.est;
      body.querySelectorAll('.mem-est').forEach(x => x.classList.toggle('activo', x === b));
      _renderItems();
    }));
    _bindSalud(body);
    _renderItems();
    _renderReader();
  }

  // Pulso de salud del atlas: anillo con % de memorias sanas + chips que
  // FILTRAN la lista (huérfanas, links rotos…) + loop de lecciones,
  // altímetro del recall y candidatas a memoria global (Promover).
  function _saludHTML() {
    const M = Meta();
    if (!M || !_memorias.length) return '';
    const probs = M.problemasSalud(_salud);
    const lecc = M.estadoLecciones(_salud);
    const alti = M.altimetro(_salud);
    const globales = (_salud && _salud.candidatas_global) || [];
    const punt = M.puntajeSalud(_salud, _memorias.length);
    if (!_salud || !punt) return '';
    const plegada = _ls.get('jarvis.mem.salud', 'abierta') === 'plegada';
    const C = 2 * Math.PI * 15;
    const nivel = punt.pct >= 90 ? 'ok' : punt.pct >= 60 ? 'medio' : 'bajo';
    return `
      <section class="mem-salud-card nivel-${nivel}${plegada ? ' plegada' : ''}${lecc?.alerta ? ' alerta' : ''}" id="mem-salud-card">
        <button type="button" class="mem-salud-head" aria-expanded="${!plegada}">
          <svg class="mem-anillo" viewBox="0 0 36 36" width="34" height="34" aria-hidden="true">
            <circle class="pista" cx="18" cy="18" r="15"/>
            <circle class="valor" cx="18" cy="18" r="15" style="stroke-dasharray:${(C * punt.pct / 100).toFixed(1)} ${C.toFixed(1)}"/>
          </svg>
          <span class="mem-salud-pct">${punt.pct}<small>%</small></span>
          <span class="mem-salud-tx">
            <b>Salud del atlas</b>
            <span>${_t('{s} de {n} sin problemas').replace('{s}', punt.sanas).replace('{n}', _memorias.length)}</span>
          </span>
          <span class="mem-salud-chev">${CHEV}</span>
        </button>
        <div class="mem-salud-body">
          ${probs.length ? `<div class="mem-salud-probs" title="Salud de .jarvis/memory/ — links rotos, citas a archivos borrados, huérfanas, contrato de admisión, choques lápida-vs-vigente, cuarentena por no-uso, duplicadas (misma memoria escrita dos veces), lecciones violadas N veces (candidatas a guard) y lecciones que merecen ser globales">
            ${probs.map(p => FILTRABLES.has(p.k)
              ? `<button type="button" class="mem-prob${_saludFiltro?.k === p.k ? ' activo' : ''}" data-k="${p.k}"><b>${p.n}</b><span>${NOMBRES_SALUD[p.k] || p.k}</span></button>`
              : `<span class="mem-prob estatico"><b>${p.n}</b><span>${NOMBRES_SALUD[p.k] || p.k}</span></span>`).join('')}
          </div>` : `<div class="mem-salud-linea ok">${icon('check', 11)}<span>Sin links rotos, huérfanas ni choques.</span></div>`}
          ${lecc ? `<div class="mem-salud-linea${lecc.alerta ? ' alerta' : ''}" title="Loop de lecciones: cuántas reglas están siempre-cargadas en CLAUDE.md/AGENTS.md y el estado del destilador de fallos">
            ${icon('sparkle', 11)}<span>${esc(lecc.texto)}</span></div>` : ''}
          ${alti ? `<div class="mem-salud-linea" title="¿El recall rinde? De las memorias que el sistema inyectó a los prompts (7 días), cuántas leyeron de verdad los agentes según su cierre, y cuántas lecturas fueron en pasos que terminaron bien">
            ${icon('chart', 11)}<span>${esc(alti.texto)}</span></div>` : ''}
          ${globales.length ? `<div class="mem-salud-global" title="Lecciones de este proyecto que valen para TODOS (entorno compartido o fallo repetido en otro proyecto) — promover las siembra en cada proyecto nuevo">
            <span class="mem-salud-sub">${icon('globe', 11)} Candidatas a memoria global</span>
            ${globales.slice(0, 4).map(c => `
              <span class="mem-global-chip"><span data-i18n-skip>${esc(c.slug)}</span>
                <button class="mem-promover" data-slug="${esc(c.slug)}" type="button">Promover</button>
              </span>`).join('')}
          </div>` : ''}
        </div>
      </section>`;
  }

  function _bindSalud(root) {
    const card = root.querySelector('#mem-salud-card');
    if (!card) return;
    card.querySelector('.mem-salud-head').addEventListener('click', () => {
      const pleg = card.classList.toggle('plegada');
      card.querySelector('.mem-salud-head').setAttribute('aria-expanded', String(!pleg));
      _ls.set('jarvis.mem.salud', pleg ? 'plegada' : 'abierta');
    });
    card.querySelectorAll('.mem-prob[data-k]').forEach(b => b.addEventListener('click', () => {
      const k = b.dataset.k;
      _saludFiltro = _saludFiltro?.k === k ? null : { k, slugs: Meta().slugsDe(_salud, k) };
      card.querySelectorAll('.mem-prob[data-k]').forEach(x => x.classList.toggle('activo', x.dataset.k === _saludFiltro?.k));
      _renderItems();
    }));
    card.querySelectorAll('.mem-promover').forEach(btn =>
      btn.addEventListener('click', () => _promover(btn.dataset.slug, btn)));
  }

  async function _promover(slug, btn) {
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    try {
      const r = await fetch(`/api/projects/${_projectId}/memory/${encodeURIComponent(slug)}/promover`,
                            { method: 'POST' });
      if (!r.ok) throw new Error(await r.text());
      window.toast?.(_t('Globo · {slug} promovida a memoria global').replace('{slug}', slug), 'ok');
      await _cargar();
      _renderTodo();
    } catch (e) {
      window.toast?.(_t('No pude promover {slug}').replace('{slug}', slug), 'error');
      if (btn) { btn.disabled = false; btn.textContent = _t('Promover'); }
    }
  }

  // Orden de la lista: por constelación (orden canónico), dentro de cada una
  // lo más fresco arriba.
  function _ordenLista(mems) {
    const orden = Meta().contarCategorias(_memorias).map(c => c.id);
    const fecha = (m) => m.actualizado || m.creado || '';
    return [...mems].sort((a, b) =>
      orden.indexOf(a.categoria || 'sin-clasificar') - orden.indexOf(b.categoria || 'sin-clasificar')
      || fecha(b).localeCompare(fecha(a)) || a.titulo.localeCompare(b.titulo));
  }

  function _renderItems() {
    const cont = document.getElementById('mem-items');
    if (!cont) return;
    const M = Meta();
    const marcas = M.marcasSalud(_salud);
    const filtradas = _ordenLista(_filtradas());
    _visibles = filtradas.map(m => m.slug);

    const filtroPill = _saludFiltro
      ? `<div class="mem-filtro-pill"><span>${_t('Mostrando: {k}').replace('{k}', _t(NOMBRES_SALUD[_saludFiltro.k] || _saludFiltro.k))}</span>
           <button type="button" id="mem-quitar-filtro" aria-label="Quitar filtro">${icon('x', 10)}</button></div>`
      : '';

    if (!_memorias.length) {
      cont.innerHTML = `<div class="mem-vacio">
          <b>Todavía no hay memorias.</b>
          <span>Las escriben tus agentes al descubrir cosas del proyecto (el protocolo ya está en el CLAUDE.md) — o creá la primera vos.</span>
        </div>`;
      return;
    }
    if (!filtradas.length) {
      cont.innerHTML = `${filtroPill}<div class="mem-vacio">
          <b>Sin coincidencias.</b>
          <button type="button" class="mem-btn" id="mem-limpiar">${icon('x', 11)} Limpiar filtros</button>
        </div>`;
      cont.querySelector('#mem-limpiar').addEventListener('click', _limpiarFiltros);
      cont.querySelector('#mem-quitar-filtro')?.addEventListener('click', _quitarFiltroSalud);
      return;
    }

    let html = filtroPill, catActual = null, i = 0;
    const porCat = {};
    for (const m of filtradas) porCat[m.categoria || 'sin-clasificar'] = (porCat[m.categoria || 'sin-clasificar'] || 0) + 1;
    for (const m of filtradas) {
      const cid = m.categoria || 'sin-clasificar';
      if (cid !== catActual) {
        if (catActual !== null) html += '</div>';
        const c = M.categoria(cid);
        html += `<div class="mem-grupo mc" ${_catAttr(cid)}>
          <div class="mem-grupo-head"><i aria-hidden="true"></i><span>${esc(c.nombre)}</span><b>${porCat[cid]}</b></div>`;
        catActual = cid;
      }
      const est = M.estadoDe(m);
      const mk = marcas[m.slug] || [];
      const nLinks = (m.links || []).length;
      html += `
        <button type="button" class="mem-card mc${m.slug === _slugAbierta ? ' activo' : ''}${est !== 'vigente' ? ' apagada est-' + est : ''}"
                data-slug="${esc(m.slug)}" role="option" aria-selected="${m.slug === _slugAbierta}" ${_catAttr(cid)} data-i="${i}">
          <span class="mem-card-t" data-i18n-skip>${esc(m.titulo)}</span>
          ${m.resumen ? `<span class="mem-card-r" data-i18n-skip>${esc(M.textoPlano(m.resumen))}</span>` : ''}
          <span class="mem-card-pie">
            ${_badgesHTML(m)}
            ${nLinks ? `<span class="mem-pie-links" title="${esc(_t('{n} enlaces').replace('{n}', nLinks))}">${LINK}${nLinks}</span>` : ''}
            ${mk.length ? `<span class="mem-pie-warn" title="${esc(mk.map(k => _t(NOMBRE_MARCA[k] || k)).join(' · '))}">${icon('alert', 10)}</span>` : ''}
            <span class="mem-pie-fecha">${esc(M.fechaRelativa(m.actualizado || m.creado))}</span>
          </span>
        </button>`;
      i++;
    }
    if (catActual !== null) html += '</div>';
    cont.innerHTML = html;
    if (!_reducido()) {
      cont.querySelectorAll('.mem-card').forEach(c => {
        const n = +c.dataset.i;
        if (n < 16) c.style.setProperty('--d', (n * 18) + 'ms'); else c.classList.add('sin-anim');
      });
    }
    cont.querySelector('#mem-quitar-filtro')?.addEventListener('click', _quitarFiltroSalud);
    cont.querySelectorAll('.mem-card').forEach(it =>
      it.addEventListener('click', () => _abrirSlug(it.dataset.slug)));
  }

  function _quitarFiltroSalud() {
    _saludFiltro = null;
    document.querySelectorAll('.mem-prob[data-k]').forEach(x => x.classList.remove('activo'));
    _renderItems();
  }
  function _limpiarFiltros() {
    _query = ''; _cats = []; _estado = 'todas'; _saludFiltro = null;
    _renderConstel();
    const body = document.getElementById('mem-body');
    if (body) _renderLista(body);
  }

  function _abrirSlug(slug, opts) {
    if (!slug) return;
    if (_tab !== 'lista') {
      _slugAbierta = slug; _modo = 'ver'; _leyendo = true;
      _setTab('lista');
      return;
    }
    _slugAbierta = slug; _modo = 'ver'; _leyendo = true;
    document.querySelectorAll('.mem-card').forEach(c => {
      const on = c.dataset.slug === slug;
      c.classList.toggle('activo', on);
      c.setAttribute('aria-selected', String(on));
      if (on && opts?.scroll) c.scrollIntoView({ block: 'nearest' });
    });
    _aplicarLeyendo();
    _renderReader();
  }

  function _aplicarLeyendo() {
    document.getElementById('mem-lista-v')?.classList.toggle('leyendo', _leyendo);
  }

  /* ── Lector ── */
  async function _renderReader() {
    const v = document.getElementById('mem-reader');
    if (!v) return;
    if (_modo === 'nueva') { _renderComposer(v); return; }
    if (!_slugAbierta || !_mem(_slugAbierta)) { _renderBienvenida(v); return; }
    // slug/proyecto CONGELADOS: si mientras viajaba la respuesta se eligió otra
    // memoria, esta respuesta ya no vale — y Borrar/Guardar deben actuar sobre
    // la memoria MOSTRADA, no sobre la global _slugAbierta.
    const slug = _slugAbierta, pid = _projectId, modo = _modo;
    if (!v.querySelector('.mem-doc') || v.dataset.slug !== slug) {
      v.innerHTML = `<div class="mem-doc-cargando">${_skeleton()}</div>`;
    }
    let mem;
    try {
      const r = await fetch(`/api/projects/${pid}/memory/${encodeURIComponent(slug)}`);
      if (slug !== _slugAbierta || pid !== _projectId || modo !== _modo) return;
      if (!r.ok) {
        v.innerHTML = `<div class="mem-estado-vacio"><span class="mem-vacio-icono">${icon('alert', 20)}</span><b>No se pudo cargar.</b></div>`;
        return;
      }
      mem = await r.json();
    } catch { return; }
    if (slug !== _slugAbierta || pid !== _projectId || modo !== _modo) return;
    v.dataset.slug = slug;
    if (_modo === 'editar') _renderEditor(v, mem, slug, pid);
    else _renderDoc(v, mem, slug, pid);
  }

  function _renderBienvenida(v) {
    const vacio = !_memorias.length;
    v.dataset.slug = '';
    v.innerHTML = `
      <div class="mem-bienvenida">
        <svg class="mem-bienv-arte" viewBox="0 0 220 140" aria-hidden="true">
          <g class="lineas"><path d="M30 100L78 52 128 74 176 34M78 52l22 64 28-42M128 74l52 40"/></g>
          <circle class="mc" style="--h:220" cx="30" cy="100" r="6"/><circle class="mc" style="--h:290" cx="78" cy="52" r="9"/>
          <circle class="mc" style="--h:155" cx="128" cy="74" r="7"/><circle class="mc" style="--h:55" cx="176" cy="34" r="5"/>
          <circle class="mc" style="--h:330" cx="100" cy="116" r="5"/><circle class="mc" style="--h:88" cx="180" cy="114" r="6"/>
        </svg>
        <h3>${vacio ? 'Tu atlas está vacío' : 'Elegí una memoria'}</h3>
        <p>${vacio
          ? 'Las memorias son hechos del proyecto que tus agentes leen antes de trabajar. Las escriben ellos al descubrir algo — o creá la primera vos.'
          : 'Cada memoria es un hecho del proyecto que los agentes leen antes de trabajar. Navegá con ↑ ↓, buscá con /, editá con E.'}</p>
        ${vacio ? `<button type="button" class="mem-btn primario" id="mem-bienv-nueva">${icon('plus', 12)} Crear la primera</button>` : ''}
      </div>`;
    v.querySelector('#mem-bienv-nueva')?.addEventListener('click', _crear);
  }

  function _renderDoc(v, mem, slug, pid) {
    const M = Meta();
    const c = M.categoria(mem.categoria);
    const marcas = M.marcasSalud(_salud)[slug] || [];
    const vec = M.conexiones(slug, _edges, _memorias);
    const leidas = _lecturas(slug);
    const autor = mem.autor || '—';
    const vecino = (s) => {
      const m = _mem(s);
      if (!m) return '';
      const cc = M.categoria(m.categoria);
      return `<button type="button" class="mem-vecino mc" data-slug="${esc(s)}" ${_catAttr(m.categoria)}>
          <i aria-hidden="true"></i><span class="t" data-i18n-skip>${esc(m.titulo)}</span><span class="c">${esc(cc.nombre)}</span></button>`;
    };
    v.innerHTML = `
      <div class="mem-doc-bar">
        <button type="button" class="mem-volver" id="mem-volver">${icon('undo', 12)}<span>Lista</span></button>
        <span class="mem-doc-ruta" data-i18n-skip title=".jarvis/memory/${esc(slug)}.md">.jarvis/memory/<b>${esc(slug)}</b>.md</span>
        <div class="mem-doc-acc">
          <button type="button" class="mem-btn" id="mem-copiar" title="Copiar [[${esc(slug)}]]">${icon('copy', 12)}<span>Copiar enlace</span></button>
          <button type="button" class="mem-btn" id="mem-editar" title="Editar (E)">${icon('edit', 12)}<span>Editar</span></button>
          <button type="button" class="mem-btn peligro" id="mem-borrar">${icon('trash', 12)}<span>Borrar</span></button>
        </div>
      </div>
      <div class="mem-doc-scroll">
        <article class="mem-doc mc est-${esc(M.estadoDe(mem))}" ${_catAttr(mem.categoria)}>
          <div class="mem-doc-eyebrow">
            <span class="mem-catpill"><i aria-hidden="true"></i>${esc(c.nombre)}</span>
            ${_badgesHTML(mem)}
            ${marcas.map(k => `<span class="mem-marca-salud">${icon('alert', 9)}${NOMBRE_MARCA[k] || k}</span>`).join('')}
          </div>
          <h1 class="mem-doc-titulo" data-i18n-skip>${esc(mem.titulo)}</h1>
          <div class="mem-doc-meta">
            <span class="mem-avatar" data-i18n-skip aria-hidden="true">${esc(autor.trim().charAt(0).toUpperCase() || '·')}</span>
            <span class="mem-doc-autor" data-i18n-skip>${esc(autor)}</span>
            ${mem.creado ? `<span class="sep">·</span><span title="${esc(mem.creado)}">${esc(_t('creada {f}').replace('{f}', mem.creado))}</span>` : ''}
            ${mem.actualizado ? `<span class="sep">·</span><span title="${esc(mem.actualizado)}">${esc(_t('act. {f}').replace('{f}', M.fechaRelativa(mem.actualizado)))}</span>` : ''}
            ${leidas ? `<span class="sep">·</span><span class="mem-doc-uso">${icon('eye', 11)}${esc(_t('leída {n}×').replace('{n}', leidas))}</span>` : ''}
          </div>
          ${(mem.tags || []).length ? `<div class="mem-doc-tags" data-i18n-skip>${mem.tags.map(t => `<span class="mem-tag">#${esc(t)}</span>`).join('')}</div>` : ''}
          <div class="mem-prosa" data-i18n-skip>${_md(mem.contenido) || `<p class="mem-prosa-vacia">—</p>`}</div>
          ${(vec.salientes.length || vec.entrantes.length || vec.rotos.length) ? `
          <footer class="mem-vecinos">
            <div class="mem-vec-col">
              <h4>${icon('send', 11)} Enlaza a <b>${vec.salientes.length + vec.rotos.length}</b></h4>
              ${vec.salientes.map(vecino).join('')}
              ${vec.rotos.map(s => `<span class="mem-vecino roto" title="No existe (todavía)"><i aria-hidden="true"></i><span class="t" data-i18n-skip>${esc(s)}</span><span class="c">no existe</span></span>`).join('')}
              ${!(vec.salientes.length + vec.rotos.length) ? '<span class="mem-vec-nada">No enlaza a otras memorias.</span>' : ''}
            </div>
            <div class="mem-vec-col">
              <h4>${icon('history', 11)} Citada por <b>${vec.entrantes.length}</b></h4>
              ${vec.entrantes.map(vecino).join('') || '<span class="mem-vec-nada">Ninguna memoria la cita todavía.</span>'}
            </div>
          </footer>` : ''}
        </article>
      </div>`;

    v.querySelector('#mem-volver').addEventListener('click', () => { _leyendo = false; _aplicarLeyendo(); });
    v.querySelectorAll('.mem-wikilink').forEach(b => b.addEventListener('click', () => {
      const t = b.dataset.link;
      const destino = _mem(M.slugDeLink(t)) || _memorias.find(m => m.titulo === t);
      if (destino) _abrirSlug(destino.slug);
      else window.toast?.(_t('No existe (todavía) la memoria "{s}"').replace('{s}', t), 'info');
    }));
    v.querySelectorAll('.mem-vecino[data-slug]').forEach(b => b.addEventListener('click', () => _abrirSlug(b.dataset.slug, { scroll: true })));
    v.querySelector('#mem-copiar').addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(`[[${slug}]]`); window.toast?.(_t('Copiado: [[{s}]]').replace('{s}', slug), 'success'); }
      catch { window.toast?.(_t('No pude copiar al portapapeles'), 'error'); }
    });
    v.querySelector('#mem-borrar').addEventListener('click', async () => {
      if (!(await confirmar(_t('¿Borrar la memoria "{t}"?').replace('{t}', mem.titulo),
        { peligro: true, confirmText: _t('Borrar'), titulo: _t('Borrar memoria') }))) return;
      const r = await fetch(`/api/projects/${pid}/memory/${encodeURIComponent(slug)}`, { method: 'DELETE' }).catch(() => null);
      if (!r || !r.ok) { window.toast?.(_t('No se pudo borrar.'), 'error'); return; }
      const i = _visibles.indexOf(slug);
      _slugAbierta = _visibles[i + 1] || _visibles[i - 1] || null;
      if (_slugAbierta === slug) _slugAbierta = null;
      _leyendo = false;
      await _cargar(); _renderTodo();
      window.toast?.(_t('Memoria borrada.'), 'success');
    });
    v.querySelector('#mem-editar').addEventListener('click', () => { _modo = 'editar'; _renderReader(); });
  }

  // Editor: fuente markdown (con frontmatter) + preview en vivo.
  function _renderEditor(v, mem, slug, pid) {
    v.innerHTML = `
      <div class="mem-ed">
        <div class="mem-ed-bar">
          <span class="mem-ed-tit">${icon('edit', 12)}<span>Editando</span><b data-i18n-skip>${esc(mem.titulo)}</b></span>
          <span class="mem-ed-hint"><kbd>Ctrl S</kbd><span>Guardar</span><kbd>Esc</kbd><span>Cancelar</span></span>
          <button class="mem-btn" id="mem-cancelar" type="button">Cancelar</button>
          <button class="mem-btn primario" id="mem-guardar" type="button">${icon('check', 12)} Guardar</button>
        </div>
        <div class="mem-ed-split">
          <div class="mem-ed-col">
            <span class="mem-ed-label">Markdown</span>
            <textarea class="mem-ed-src" id="mem-textarea" spellcheck="false" aria-label="Contenido de la memoria"></textarea>
          </div>
          <div class="mem-ed-col mem-ed-prevcol">
            <span class="mem-ed-label">Vista previa</span>
            <div class="mem-ed-prev mem-prosa" id="mem-ed-prev" data-i18n-skip></div>
          </div>
        </div>
      </div>`;
    const ta = v.querySelector('#mem-textarea');
    const prev = v.querySelector('#mem-ed-prev');
    ta.value = mem.contenido || '';
    let tPrev = null;
    const refrescar = () => { prev.innerHTML = _md(ta.value) || '<p class="mem-prosa-vacia">—</p>'; };
    refrescar();
    ta.addEventListener('input', () => { clearTimeout(tPrev); tPrev = setTimeout(refrescar, 120); });
    ta.focus();
    ta.setSelectionRange(0, 0);
    const guardar = async () => {
      const btn = v.querySelector('#mem-guardar');
      btn.disabled = true;
      const r = await fetch(`/api/projects/${pid}/memory/${encodeURIComponent(slug)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ contenido: ta.value }),
      }).catch(() => null);
      if (!r || !r.ok) { btn.disabled = false; window.toast?.(_t('No se pudo guardar.'), 'error'); return; }
      _modo = 'ver';
      await _cargar(); _renderTodo();
      window.toast?.(_t('Memoria guardada.'), 'success');
    };
    ta.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); guardar(); }
    });
    v.querySelector('#mem-cancelar').addEventListener('click', () => { _modo = 'ver'; _renderReader(); });
    v.querySelector('#mem-guardar').addEventListener('click', guardar);
  }

  // Nueva memoria: compositor inline (título + tags + contenido + preview).
  function _crear() {
    if (_tab !== 'lista') { _modo = 'nueva'; _leyendo = true; _setTab('lista'); return; }
    _modo = 'nueva'; _leyendo = true; _aplicarLeyendo();
    _renderReader();
  }

  function _renderComposer(v) {
    v.dataset.slug = '';
    v.innerHTML = `
      <div class="mem-ed mem-nuevo">
        <div class="mem-ed-bar">
          <span class="mem-ed-tit">${icon('sparkle', 12)}<span>Nueva memoria</span></span>
          <span class="mem-ed-hint"><kbd>Ctrl ↵</kbd><span>Crear</span><kbd>Esc</kbd><span>Cancelar</span></span>
          <button class="mem-btn" id="mem-cancelar" type="button">Cancelar</button>
          <button class="mem-btn primario" id="mem-crear" type="button" disabled>${icon('check', 12)} Crear</button>
        </div>
        <div class="mem-ed-split">
          <div class="mem-ed-col mem-nuevo-form">
            <input type="text" class="mem-nuevo-titulo" id="mem-nuevo-titulo" maxlength="120"
                   placeholder="Título corto y específico" autocomplete="off" aria-label="Título">
            <input type="text" class="mem-nuevo-tags" id="mem-nuevo-tags" autocomplete="off"
                   placeholder="tags, separados por coma — ej. xterm, foco" aria-label="Tags">
            <textarea class="mem-ed-src" id="mem-nuevo-cont" spellcheck="false" aria-label="Contenido"
                      placeholder="Qué hay que saber… (markdown; enlazá otras memorias con [[slug]])"></textarea>
            <p class="mem-nuevo-nota">${icon('info', 11)}<span>La categoría se infiere de los tags · autor: usuario · estado: vigente</span></p>
          </div>
          <div class="mem-ed-col mem-ed-prevcol">
            <span class="mem-ed-label">Vista previa</span>
            <div class="mem-ed-prev" id="mem-ed-prev"></div>
          </div>
        </div>
      </div>`;
    const tit = v.querySelector('#mem-nuevo-titulo');
    const tags = v.querySelector('#mem-nuevo-tags');
    const cont = v.querySelector('#mem-nuevo-cont');
    const prev = v.querySelector('#mem-ed-prev');
    const btn = v.querySelector('#mem-crear');
    const listaTags = () => tags.value.split(/[,\n]/).map(s => s.trim().replace(/^#/, '')).filter(Boolean);
    const refrescar = () => {
      btn.disabled = !tit.value.trim();
      prev.innerHTML = `
        <div class="mem-doc-eyebrow"><span class="mem-catpill mem-catpill-auto"><i aria-hidden="true"></i><span>categoría automática</span></span></div>
        <h1 class="mem-doc-titulo" data-i18n-skip>${esc(tit.value.trim()) || `<span class="mem-fantasma">${esc(_t('Sin título'))}</span>`}</h1>
        ${listaTags().length ? `<div class="mem-doc-tags" data-i18n-skip>${listaTags().map(t => `<span class="mem-tag">#${esc(t)}</span>`).join('')}</div>` : ''}
        <div class="mem-prosa" data-i18n-skip>${_md(cont.value)}</div>`;
    };
    refrescar();
    let tPrev = null;
    [tit, tags, cont].forEach(el => el.addEventListener('input', () => { clearTimeout(tPrev); tPrev = setTimeout(refrescar, 90); }));
    tit.addEventListener('input', () => { btn.disabled = !tit.value.trim(); });
    tit.focus();
    const crear = async () => {
      const titulo = tit.value.trim();
      if (!titulo) { tit.focus(); return; }
      btn.disabled = true;
      const r = await fetch(`/api/projects/${_projectId}/memory`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ titulo, contenido: cont.value || '', tags: listaTags() }),
      }).catch(() => null);
      if (!r || !r.ok) {
        const e = r ? await r.json().catch(() => ({})) : {};
        window.toast?.(e.detail || _t('No se pudo crear.'), 'error');
        btn.disabled = false;
        return;
      }
      const { slug, similares } = await r.json();
      _modo = 'ver';
      await _cargar();
      _slugAbierta = slug;
      _renderTodo();
      window.toast?.(_t('Memoria creada.'), 'success');
      if ((similares || []).length) {
        window.toast?.(_t('Ojo: se parece a {s} — ¿no convenía actualizar esa?').replace('{s}', similares.slice(0, 3).join(', ')), 'warning', 7000);
      }
    };
    [tit, tags, cont].forEach(el => el.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); crear(); }
      else if (e.key === 'Enter' && el === tit) { e.preventDefault(); tags.focus(); }
      else if (e.key === 'Enter' && el === tags) { e.preventDefault(); cont.focus(); }
    }));
    btn.addEventListener('click', crear);
    v.querySelector('#mem-cancelar').addEventListener('click', () => {
      _modo = 'ver'; _leyendo = !!_slugAbierta && _leyendo; _renderReader();
    });
  }

  /* ══ GRAFO — constelaciones vivas ═══════════════════════════════════ */
  function _renderGrafo(body) {
    _pararGrafo();
    const M = Meta(), G = window.JarvisMemoryGraph;
    if (!G) {
      // Red de seguridad: si el <script> de memory-graph.js no está en el HTML,
      // se carga a demanda (misma carpeta que este archivo).
      body.innerHTML = `<div class="mem-cargando">${_skeleton()}</div>`;
      const src = (document.querySelector('script[src*="sections/memory/memory.js"]')?.src || '/static/sections/memory/memory.js')
        .replace(/memory\.js(\?.*)?$/, 'memory-graph.js');
      const s = document.createElement('script');
      s.src = src;
      s.onload = () => { if (_tab === 'grafo' && document.body.contains(body)) _renderGrafo(body); };
      document.head.appendChild(s);
      return;
    }
    if (!_memorias.length) {
      body.innerHTML = `<div class="mem-estado-vacio">
          <span class="mem-vacio-icono">${icon('brain', 22)}</span>
          <b>El grafo aparece cuando haya memorias.</b>
          <span>El grafo aparece cuando haya memorias enlazadas con [[wikilinks]].</span></div>`;
      return;
    }
    const grados = M.grados(_edges);
    const sim = G.crear(
      _memorias.map(m => ({ id: m.slug, cat: m.categoria || 'sin-clasificar', grado: grados[m.slug] || 0 })),
      _edges.map(e => ({ a: e.from, b: e.to })), _grafPos);
    const por = Object.fromEntries(sim.nodos.map(n => [n.id, n]));
    const aristas = sim.aristas.map(([i, j]) => [sim.nodos[i], sim.nodos[j]]);
    const cats = M.contarCategorias(_memorias);
    const aisladas = sim.nodos.filter(n => !n.grado).length;

    body.innerHTML = `
      <div class="mem-grafo" id="mem-grafo" tabindex="-1">
        <svg class="mem-graf-svg" id="mem-graf-svg" role="img" aria-label="Grafo de memorias">
          <defs>
            ${cats.map(c => `<radialGradient id="mem-neb-${esc(c.id)}" class="mc" style="--h:${c.hue}"${c.neutra ? ' data-neutra' : ''}>
                <stop offset="0%" class="s0"/><stop offset="70%" class="s1"/><stop offset="100%" class="s2"/></radialGradient>`).join('')}
          </defs>
          <g class="mem-graf-v" id="mem-graf-v">
            <g class="mem-nebs">${cats.map(c => `<circle class="mem-neb" data-cat="${esc(c.id)}" fill="url(#mem-neb-${esc(c.id)})"/>`).join('')}</g>
            <g class="mem-ges">${aristas.map(([a, b], i) => `<g class="mem-ge" data-i="${i}" style="--ha:${_hue(_mem(a.id))};--hb:${_hue(_mem(b.id))}"><line class="glow"/><line class="hilo"/></g>`).join('')}</g>
            <g class="mem-nebl">${cats.map(c => `<text class="mem-neb-lbl mc" data-cat="${esc(c.id)}" style="--h:${c.hue}"${c.neutra ? ' data-neutra' : ''} text-anchor="middle">${esc(c.nombre)}</text>`).join('')}</g>
            <g class="mem-gns">${sim.nodos.map(n => {
              const m = _mem(n.id);
              const est = M.estadoDe(m);
              return `<g class="mem-gn mc${n.grado >= 2 ? ' mayor' : ''}${!n.grado ? ' aislada' : ''} est-${est}${M.esLeccion(m) ? ' leccion' : ''}" data-slug="${esc(n.id)}" ${_catAttr(n.cat)} tabindex="0" role="button" aria-label="${esc(m.titulo)}">
                  <circle class="halo" r="${(n.r * 2.3).toFixed(1)}"/>
                  <circle class="pulso" r="${(n.r + 4).toFixed(1)}"/>
                  <circle class="core" r="${n.r.toFixed(1)}"/>
                  <text class="lbl" y="${(n.r + 14).toFixed(1)}" text-anchor="middle" data-i18n-skip>${esc(m.titulo.length > 30 ? m.titulo.slice(0, 29) + '…' : m.titulo)}</text>
                </g>`; }).join('')}</g>
          </g>
        </svg>
        <div class="mem-graf-top">
          <label class="mem-graf-buscar">${icon('search', 12)}
            <input type="text" id="mem-graf-buscar" placeholder="Resaltar…" value="${esc(_query)}" autocomplete="off" spellcheck="false" aria-label="Resaltar memorias">
          </label>
          <div class="mem-graf-ctrl" role="toolbar" aria-label="Zoom">
            <button type="button" data-act="out" title="Alejar (−)" aria-label="Alejar">−</button>
            <span class="mem-graf-zoom" id="mem-graf-zoom">100%</span>
            <button type="button" data-act="in" title="Acercar (+)" aria-label="Acercar">+</button>
            <span class="sep" aria-hidden="true"></span>
            <button type="button" data-act="fit" title="Ajustar (0)" aria-label="Ajustar">${icon('maximize', 13)}</button>
            <button type="button" data-act="shake" title="Reordenar" aria-label="Reordenar">${icon('refresh', 13)}</button>
            <button type="button" data-act="etiq" class="${_grafEtiq ? 'activo' : ''}" title="Mostrar todas las etiquetas" aria-label="Mostrar todas las etiquetas" aria-pressed="${_grafEtiq}">Aa</button>
          </div>
        </div>
        <div class="mem-graf-card" id="mem-graf-card" hidden></div>
        <div class="mem-graf-pie">
          <span class="mem-graf-stats">${_t('{n} memorias · {e} enlaces · {a} aisladas').replace('{n}', sim.nodos.length).replace('{e}', aristas.length).replace('{a}', aisladas)}</span>
          <span class="mem-graf-hint">${_t('click = abrir · arrastrar = mover · rueda = zoom · shift+click en constelación = sumar')}</span>
        </div>
      </div>`;

    const view = body.querySelector('#mem-grafo');
    const svg  = body.querySelector('#mem-graf-svg');
    const grp  = body.querySelector('#mem-graf-v');
    const zlbl = body.querySelector('#mem-graf-zoom');
    const card = body.querySelector('#mem-graf-card');
    const nodeEls = {};
    body.querySelectorAll('.mem-gn').forEach(g => { nodeEls[g.dataset.slug] = g; });
    const edgeEls = [...body.querySelectorAll('.mem-ge')].map(g => ({ g, l: g.querySelectorAll('line') }));
    const nebs = {}, nebl = {};
    body.querySelectorAll('.mem-neb').forEach(c => { nebs[c.dataset.cat] = c; });
    body.querySelectorAll('.mem-neb-lbl').forEach(t => { nebl[t.dataset.cat] = t; });
    const vecinos = {};
    for (const [a, b] of aristas) {
      (vecinos[a.id] = vecinos[a.id] || new Set()).add(b.id);
      (vecinos[b.id] = vecinos[b.id] || new Set()).add(a.id);
    }

    let W = view.clientWidth || 800, H = view.clientHeight || 500;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);

    function pintar() {
      for (const n of sim.nodos) nodeEls[n.id].setAttribute('transform', `translate(${n.x.toFixed(1)},${n.y.toFixed(1)})`);
      aristas.forEach(([a, b], i) => {
        for (const l of edgeEls[i].l) {
          l.setAttribute('x1', a.x.toFixed(1)); l.setAttribute('y1', a.y.toFixed(1));
          l.setAttribute('x2', b.x.toFixed(1)); l.setAttribute('y2', b.y.toFixed(1));
        }
      });
      for (const c of G.constelaciones(sim)) {
        const el = nebs[c.cat], lb = nebl[c.cat];
        if (el) { el.setAttribute('cx', c.x.toFixed(1)); el.setAttribute('cy', c.y.toFixed(1)); el.setAttribute('r', (c.r * 1.25).toFixed(1)); }
        if (lb) { lb.setAttribute('x', c.x.toFixed(1)); lb.setAttribute('y', (c.y - c.r - 6).toFixed(1)); }
      }
    }
    // Declutter de etiquetas (greedy, por grado): abajo del nodo → arriba →
    // se oculta (vuelve con hover/foco o con «Aa»). Los rótulos de las
    // constelaciones se reservan primero. Corre al asentarse y al hacer zoom.
    const lblEls = {};
    body.querySelectorAll('.mem-gn').forEach(g => { lblEls[g.dataset.slug] = g.querySelector('.lbl'); });
    function declutter() {
      const lk = Math.max(0.85, Math.min(2.2, 1 / _grafT.k));
      const cajas = [];
      const choca = (b) => cajas.some(o => b.x < o.x + o.w && b.x + b.w > o.x && b.y < o.y + o.h && b.y + b.h > o.y);
      for (const c of G.constelaciones(sim)) {
        const nom = M.categoria(c.cat).nombre;
        const w = nom.length * 7.2 * lk;
        cajas.push({ x: c.x - w / 2, y: c.y - c.r - 6 - 11 * lk, w, h: 13 * lk });
      }
      for (const n of sim.nodos) cajas.push({ x: n.x - n.r, y: n.y - n.r, w: n.r * 2, h: n.r * 2 });
      const orden = [...sim.nodos].sort((a, b) => b.grado - a.grado || a.id.localeCompare(b.id));
      for (const n of orden) {
        const el = lblEls[n.id];
        if (!el) continue;
        const w = Math.min(30, el.textContent.length) * 5.7 * lk, h = 13 * lk;
        const abajo = { x: n.x - w / 2, y: n.y + n.r + 3, w, h };
        const arriba = { x: n.x - w / 2, y: n.y - n.r - 3 - h, w, h };
        let pos = 'abajo', caja = abajo;
        if (choca(abajo)) { if (!choca(arriba)) { pos = 'arriba'; caja = arriba; } else pos = 'oculta'; }
        el.setAttribute('y', pos === 'arriba' ? (-(n.r + 6)).toFixed(1) : (n.r + 14).toFixed(1));
        el.classList.toggle('oculta', pos === 'oculta');
        if (pos !== 'oculta') cajas.push(caja);
      }
    }
    let _tDecl = null;
    function aplicarT() {
      grp.setAttribute('transform', `translate(${_grafT.x.toFixed(1)},${_grafT.y.toFixed(1)}) scale(${_grafT.k.toFixed(3)})`);
      svg.style.setProperty('--lk', Math.max(0.85, Math.min(2.2, 1 / _grafT.k)).toFixed(3));
      svg.classList.toggle('sin-etiquetas', _grafT.k < 0.8 && !_grafEtiq);
      svg.classList.toggle('todas-etiquetas', _grafEtiq);
      zlbl.textContent = Math.round(_grafT.k * 100) + '%';
      clearTimeout(_tDecl);
      _tDecl = setTimeout(() => { if (!_grafRaf) declutter(); }, 60);
    }
    function fit(anim) {
      // área útil: debajo de la barra flotante (52px) y arriba del pie (32px)
      _grafT = G.encuadre(G.limites(sim, 24, true), W, H - 84, 0.3, 1.6);
      _grafT.y += 52;
      view.classList.toggle('suave', !!anim && !_reducido());
      aplicarT();
      if (anim) setTimeout(() => view.classList.remove('suave'), 320);
    }
    function guardarPos() { for (const n of sim.nodos) _grafPos[n.id] = { x: n.x, y: n.y }; declutter(); }
    let _frames = 0;
    function loop() {
      _grafRaf = 0;
      G.paso(sim); G.paso(sim);
      pintar();
      if (++_frames % 10 === 0) declutter();
      if (!G.asentada(sim)) _grafRaf = requestAnimationFrame(loop);
      else guardarPos();
    }
    function arrancar(a) {
      if (a) G.recalentar(sim, a);
      if (_reducido()) { G.asentar(sim); pintar(); guardarPos(); return; }
      if (!_grafRaf && !G.asentada(sim)) _grafRaf = requestAnimationFrame(loop);
    }

    // Primer encuadre: se precalcula casi todo el layout para que el fit
    // encuadre la forma FINAL (y la animación sea solo el último asentamiento).
    const nuevo = sim.alpha >= 1;
    if (nuevo) { for (let i = 0; i < 170; i++) G.paso(sim); }
    pintar();
    if (!_grafT || nuevo) fit(false); else aplicarT();
    declutter();
    arrancar();

    // ── filtro compartido (constelaciones + buscador) ──
    _grafAplicarFiltro = () => {
      const vis = new Set(_filtradas().map(m => m.slug));
      const todo = vis.size === _memorias.length;
      for (const id in nodeEls) nodeEls[id].classList.toggle('fuera', !todo && !vis.has(id));
      aristas.forEach(([a, b], i) => edgeEls[i].g.classList.toggle('fuera', !todo && !(vis.has(a.id) && vis.has(b.id))));
      for (const c in nebs) {
        const on = !_cats.length || _cats.includes(c);
        nebs[c].classList.toggle('fuera', !on); nebl[c].classList.toggle('fuera', !on);
      }
      view.classList.toggle('filtrado', !todo);
    };
    _grafAplicarFiltro();
    const bus = body.querySelector('#mem-graf-buscar');
    bus.addEventListener('input', () => { _query = bus.value; _grafAplicarFiltro(); });

    // ── controles ──
    const zoomEn = (f, cx, cy) => {
      cx = cx ?? W / 2; cy = cy ?? H / 2;
      const k2 = Math.max(0.25, Math.min(3.5, _grafT.k * f));
      _grafT = { k: k2, x: cx - (cx - _grafT.x) * (k2 / _grafT.k), y: cy - (cy - _grafT.y) * (k2 / _grafT.k) };
      aplicarT();
    };
    view.querySelector('[data-act="in"]').addEventListener('click', () => { view.classList.add('suave'); zoomEn(1.25); setTimeout(() => view.classList.remove('suave'), 300); });
    view.querySelector('[data-act="out"]').addEventListener('click', () => { view.classList.add('suave'); zoomEn(1 / 1.25); setTimeout(() => view.classList.remove('suave'), 300); });
    view.querySelector('[data-act="fit"]').addEventListener('click', () => fit(true));
    view.querySelector('[data-act="shake"]').addEventListener('click', () => {
      for (const n of sim.nodos) { n.vx += (Math.random() - 0.5) * 30; n.vy += (Math.random() - 0.5) * 30; }
      arrancar(0.6);
      setTimeout(() => fit(true), _reducido() ? 0 : 900);
    });
    view.querySelector('[data-act="etiq"]').addEventListener('click', (e) => {
      _grafEtiq = !_grafEtiq;
      e.currentTarget.classList.toggle('activo', _grafEtiq);
      e.currentTarget.setAttribute('aria-pressed', String(_grafEtiq));
      aplicarT();
    });

    const aPx = (ev) => { const r = svg.getBoundingClientRect(); return { x: ev.clientX - r.left, y: ev.clientY - r.top }; };
    view.addEventListener('wheel', (ev) => {
      ev.preventDefault();
      const p = aPx(ev);
      zoomEn(ev.deltaY < 0 ? 1.14 : 1 / 1.14, p.x, p.y);
    }, { passive: false });

    _grafAC = new AbortController();
    const sig = { signal: _grafAC.signal };
    let down = null;
    svg.addEventListener('pointerdown', (ev) => {
      if (ev.button !== 0) return;
      const g = ev.target.closest('.mem-gn');
      down = { x: ev.clientX, y: ev.clientY, moved: false, nodo: g ? por[g.dataset.slug] : null, t0: _grafT };
      if (down.nodo) { down.nodo.fijo = true; ocultarCard(); }
    });
    window.addEventListener('pointermove', (ev) => {
      if (!down) return;
      if (!down.moved && Math.hypot(ev.clientX - down.x, ev.clientY - down.y) > 4) {
        down.moved = true;
        view.classList.add(down.nodo ? 'arrastrando' : 'paneando');
      }
      if (!down.moved) return;
      ev.preventDefault();
      if (down.nodo) {
        const p = aPx(ev);
        down.nodo.x = (p.x - _grafT.x) / _grafT.k;
        down.nodo.y = (p.y - _grafT.y) / _grafT.k;
        pintar();
        arrancar(0.18);
      } else {
        _grafT = { k: _grafT.k, x: down.t0.x + (ev.clientX - down.x), y: down.t0.y + (ev.clientY - down.y) };
        aplicarT();
      }
    }, sig);
    window.addEventListener('pointerup', () => {
      if (!down) return;
      const d = down; down = null;
      view.classList.remove('paneando', 'arrastrando');
      if (d.nodo) {
        d.nodo.fijo = false;
        if (!d.moved) _abrirSlug(d.nodo.id);
        else guardarPos();
      }
    }, sig);

    // ── foco por hover: el vecindario brilla, el resto se apaga ──
    let hoverId = null;
    function ocultarCard() { card.hidden = true; }
    function enfocar(id) {
      hoverId = id;
      const vec = vecinos[id] || new Set();
      view.classList.add('foco');
      for (const k in nodeEls) {
        nodeEls[k].classList.toggle('vecino', vec.has(k));
        nodeEls[k].classList.toggle('hito', k === id);
      }
      aristas.forEach(([a, b], i) => edgeEls[i].g.classList.toggle('on', a.id === id || b.id === id));
      const n = por[id], m = _mem(id);
      const c = M.categoria(m.categoria);
      card.innerHTML = `
        <span class="mem-catpill mc" ${_catAttr(m.categoria)}><i aria-hidden="true"></i>${esc(c.nombre)}</span>
        <b data-i18n-skip>${esc(m.titulo)}</b>
        ${m.resumen ? `<p data-i18n-skip>${esc(M.textoPlano(m.resumen))}</p>` : ''}
        <span class="mem-graf-card-meta">${_badgesHTML(m)}<span data-i18n-skip>${esc(m.autor || '—')}</span> · ${esc(M.fechaRelativa(m.actualizado || m.creado))} · ${esc(_t('{n} conexiones').replace('{n}', n.grado))}</span>`;
      card.hidden = false;
      const sx = _grafT.x + n.x * _grafT.k, sy = _grafT.y + n.y * _grafT.k;
      const w = card.offsetWidth, h = card.offsetHeight;
      const dx = n.r * _grafT.k + 16;
      let left = sx + dx; if (left + w > W - 10) left = sx - dx - w;
      card.style.left = Math.max(10, left) + 'px';
      card.style.top = Math.max(56, Math.min(H - h - 44, sy - h / 2)) + 'px';
    }
    function desenfocar() {
      hoverId = null;
      view.classList.remove('foco');
      for (const k in nodeEls) nodeEls[k].classList.remove('vecino', 'hito');
      edgeEls.forEach(e => e.g.classList.remove('on'));
      ocultarCard();
    }
    Object.values(nodeEls).forEach(g => {
      g.addEventListener('pointerenter', () => { if (!down) enfocar(g.dataset.slug); });
      g.addEventListener('pointerleave', () => { if (!down) desenfocar(); });
      g.addEventListener('focus', () => enfocar(g.dataset.slug));
      g.addEventListener('blur', desenfocar);
      g.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _abrirSlug(g.dataset.slug); } });
    });
    view.addEventListener('keydown', (e) => {
      if (/^(INPUT|TEXTAREA)$/.test(e.target.tagName)) return;
      if (e.key === '+' || e.key === '=') zoomEn(1.2);
      else if (e.key === '-') zoomEn(1 / 1.2);
      else if (e.key === '0') fit(true);
    });

    // ── tamaño real: el viewBox sigue al contenedor ──
    _grafRO = new ResizeObserver(() => {
      const w2 = view.clientWidth, h2 = view.clientHeight;
      if (!w2 || !h2 || (w2 === W && h2 === H)) return;
      _grafT = { k: _grafT.k, x: _grafT.x + (w2 - W) / 2, y: _grafT.y + (h2 - H) / 2 };
      W = w2; H = h2;
      svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
      aplicarT();
    });
    _grafRO.observe(view);

    // ── disparos neuronales: un nodo late y su vecindario se enciende ──
    // Efímero (0.9s), cada 2.8s, solo con el grafo quieto, a la vista y sin
    // hover; nunca con reduced-motion.
    if (!_reducido()) {
      const conGrado = sim.nodos.filter(n => n.grado);
      _grafTimer = setInterval(() => {
        if (document.hidden || hoverId || down || _grafRaf || !conGrado.length) return;
        const n = conGrado[Math.floor(Math.random() * conGrado.length)];
        const g = nodeEls[n.id];
        if (!g || g.classList.contains('fuera')) return;
        g.classList.add('fire');
        const on = [];
        aristas.forEach(([a, b], i) => { if (a.id === n.id || b.id === n.id) { edgeEls[i].g.classList.add('sinapsis'); on.push(edgeEls[i].g); } });
        setTimeout(() => { g.classList.remove('fire'); on.forEach(e => e.classList.remove('sinapsis')); }, 900);
      }, 2800);
    }
  }
  let _grafAplicarFiltro = () => {};

  /* ══ LIVE — el enjambre y su recall ════════════════════════════════ */
  async function _cargarLive() {
    try {
      const r = await fetch(`/api/projects/${_projectId}/live`);
      if (!r.ok) return;
      _liveEstado = window.JarvisLiveState.aplicarSnapshot(
        _liveEstado || window.JarvisLiveState.crearEstado(), await r.json(), Date.now());
    } catch { /* red */ }
  }

  function onLiveEvent(data) {
    if (!data.snapshot) return;
    // Modal cerrado → no acumular nada: al abrir la pestaña se re-fetchea todo.
    if (!document.querySelector('.mem-overlay')) return;
    _liveEstado = window.JarvisLiveState.aplicarSnapshot(
      _liveEstado || window.JarvisLiveState.crearEstado(), data.snapshot, Date.now());
    if (_tab === 'live') {
      const body = document.getElementById('mem-body');
      if (body) _renderLive(body, /*sinFetch*/ true);
    }
  }

  async function _renderLive(body, sinFetch) {
    if (!body.querySelector('.mem-live')) {
      body.innerHTML = `
        <div class="mem-live">
          <div class="mem-live-main">
            <div class="mem-vitales" id="mem-vitales"></div>
            <div class="mem-live-head">
              <h3>Enjambre</h3>
              <div class="mem-seg" role="tablist" aria-label="Vista del enjambre">
                <button type="button" class="mem-live-v" data-v="pulso">Pulso</button>
                <button type="button" class="mem-live-v" data-v="mapa">Mapa</button>
              </div>
            </div>
            <div class="mem-live-cuerpo" id="mem-live-cuerpo"></div>
          </div>
          <aside class="mem-live-rail">
            <div class="mem-seg mem-rail-tabs" role="tablist" aria-label="Línea de tiempo">
              <button type="button" class="mem-rail-t" data-r="recall">${icon('brain', 12)} Recall</button>
              <button type="button" class="mem-rail-t" data-r="actividad">${icon('history', 12)} Actividad</button>
            </div>
            <div class="mem-tl" id="mem-tl"></div>
          </aside>
        </div>`;
      body.querySelectorAll('.mem-live-v').forEach(b => b.addEventListener('click', () => {
        _liveVista = b.dataset.v; _renderLive(body, true);
      }));
      body.querySelectorAll('.mem-rail-t').forEach(b => b.addEventListener('click', () => {
        _liveRail = b.dataset.r; _ls.set('jarvis.mem.rail', _liveRail); _renderLiveRail();
      }));
      body.querySelector('#mem-live-cuerpo').innerHTML = `<div class="mem-cargando">${_skeleton()}</div>`;
    }
    if (!sinFetch) {
      await Promise.all([_cargarLive(), _cargarUso()]);
      if (_tab !== 'live' || !document.body.contains(body)) return;
      clearInterval(_livePoll);
      _livePoll = setInterval(async () => {
        if (document.hidden || _tab !== 'live') return;
        await _cargarUso();
        if (_tab === 'live') { _renderLiveRail(); _renderVitales(); _renderLiveCuerpo(); }
      }, 8000);
    }
    body.querySelectorAll('.mem-live-v').forEach(b => b.classList.toggle('activo', b.dataset.v === _liveVista));
    _renderVitales();
    _renderLiveCuerpo();
    _renderLiveRail();
  }

  function _renderVitales() {
    const el = document.getElementById('mem-vitales');
    if (!el) return;
    const st = _liveEstado || window.JarvisLiveState.crearEstado();
    const nAg   = (st.agentes || []).length;
    const nTrab = (st.agentes || []).filter(a => a.estado === 'trabajando').length;
    const nArch = new Set((st.agentes || []).flatMap(a => (a.archivos || []).map(f => f.path))).size;
    const ev = _uso.eventos || [];
    const leidas = ev.filter(e => e.resultado !== 'inyectada').length;
    const sugeridas = ev.length - leidas;
    const alti = _salud && _salud.altimetro;
    el.innerHTML = `
      <div class="mem-vital run${nTrab ? ' vivo' : ''}"><i aria-hidden="true"></i><b>${nTrab}</b><span>${_t('trabajando')}</span></div>
      <div class="mem-vital"><i aria-hidden="true"></i><b>${nAg}</b><span>${_t('agente(s)')}</span></div>
      <div class="mem-vital"><i aria-hidden="true"></i><b>${nArch}</b><span>${_t('archivo(s)')}</span></div>
      <div class="mem-vital acento" title="${esc(_t('Memorias leídas por los agentes / sugeridas por el recall'))}"><i aria-hidden="true"></i>
        <b>${alti && (alti.lecturas || alti.inyecciones) ? `${alti.lecturas || 0}<small>/${alti.inyecciones || 0}</small>` : `${leidas}<small>/${sugeridas}</small>`}</b>
        <span>${alti && (alti.lecturas || alti.inyecciones) ? _t('leídas / sugeridas · 7d') : _t('leídas / sugeridas')}</span></div>`;
  }

  function _renderLiveCuerpo() {
    const cuerpo = document.getElementById('mem-live-cuerpo');
    if (!cuerpo) return;
    const L = window.JarvisLiveState;
    const st = _liveEstado || L.crearEstado();
    const flashes = new Set(L.flashesVigentes(st, Date.now()));
    cuerpo.classList.toggle('es-mapa', _liveVista === 'mapa');
    if (_liveVista === 'mapa') _renderLiveMapa(cuerpo, st, flashes);
    else _renderLivePulso(cuerpo, st, flashes);
    // los flashes decaen solos: un re-render diferido los apaga
    clearTimeout(_liveTimer);
    if (flashes.size) {
      _liveTimer = setTimeout(() => {
        if (_tab === 'live' && document.getElementById('mem-live-cuerpo')) _renderLiveCuerpo();
      }, L.FLASH_MS + 100);
    }
  }

  function _permisosDe(st, path) {
    return (st.permisos || []).filter(p =>
      p.archivo === path || path.endsWith('/' + p.archivo) || p.archivo.endsWith('/' + path));
  }

  const PERMISO_TX = { pendiente: 'permiso pendiente', ok: 'permiso concedido', no: 'permiso denegado', expirado: 'permiso expirado' };

  function _renderLivePulso(cuerpo, st, flashes) {
    const L = window.JarvisLiveState;
    const agentes = L.ordenarAgentes(st.agentes);
    if (!agentes.length) {
      cuerpo.innerHTML = `<div class="mem-estado-vacio chico">
          <span class="mem-vacio-icono">${icon('terminal', 20)}</span>
          <b>Sin agentes activos en este proyecto.</b>
          <span>Cuando abras terminales con agentes, acá vas a ver qué archivos tocan y qué memorias leen.</span></div>`;
      return;
    }
    // memorias recientes por terminal (del stream de recall)
    const memsDe = {};
    for (const e of _uso.eventos || []) {
      if (e.terminal_id == null) continue;
      const arr = memsDe[e.terminal_id] || (memsDe[e.terminal_id] = []);
      if (!arr.some(x => x.slug === e.slug) && arr.length < 5) arr.push(e);
    }
    cuerpo.innerHTML = `<div class="mem-lanes">${agentes.map((a, i) => {
      const arch = (a.archivos || []).filter(f => f.writes > 0 || f.reads > 0);
      const mems = memsDe[a.terminal_id] || [];
      const ult = arch.length ? Math.min(...arch.map(f => f.hace_s ?? 1e9)) : null;
      return `
      <article class="mem-lane" data-estado="${esc(a.estado)}" style="--i:${Math.min(i, 10)}">
        <div class="mem-lane-id">
          <span class="mem-live-anillo" data-estado="${esc(a.estado)}">${window.cliLogo ? window.cliLogo(a.tipo_ia, 18) : ''}</span>
          <span class="mem-lane-nom">
            <b data-i18n-skip>${esc(a.nombre)}</b>
            <span class="mem-lane-est">${a.estado === 'trabajando' ? _t('trabajando') : 'idle'}${ult != null && ult < 1e9 ? ` · ${_t('hace {t}').replace('{t}', L.hace(ult))}` : ''}</span>
          </span>
        </div>
        <div class="mem-lane-files">${arch.map(f => {
          const permisos = _permisosDe(st, f.path);
          const burbujas = permisos.map(p =>
            `<i class="mem-perm p-${esc(p.estado)}" title="${esc(_t(PERMISO_TX[p.estado] || p.estado))} — ${esc(p.pide)} → ${esc(p.dueno)}: ${esc(p.detalle || '')}${p.respuesta ? ' · ' + _t('resp: {r}').replace('{r}', esc(p.respuesta)) : ''}"></i>`).join('');
          return `<span class="mem-fchip ${flashes.has(f.path) ? 'flash' : ''} ${f.writes ? 'write' : 'read'}"
                        title="${esc(f.path)} — ${f.writes}w/${f.reads}r, ${esc(_t('hace {t}').replace('{t}', L.hace(f.hace_s)))}">
            ${f.dueno ? LOCK : ''}<span data-i18n-skip>${esc(f.path.split('/').pop())}</span>${f.writes > 1 ? `<i class="x">×${f.writes}</i>` : ''}${burbujas}
          </span>`;
        }).join('') || '<span class="mem-lane-nada">sin actividad de archivos</span>'}</div>
        ${mems.length ? `<div class="mem-lane-mems">${icon('brain', 11)}${mems.map(e => {
          const m = _mem(e.slug);
          return `<button type="button" class="mem-mchip mc" data-slug="${esc(e.slug)}" ${_catAttr(m?.categoria)} title="${esc(Meta().resultadoUso(e.resultado).label)}">
            <i aria-hidden="true"></i><span data-i18n-skip>${esc(m ? m.titulo : e.slug)}</span></button>`;
        }).join('')}</div>` : ''}
      </article>`; }).join('')}</div>`;
    cuerpo.querySelectorAll('.mem-mchip[data-slug]').forEach(b => b.addEventListener('click', () => {
      if (_mem(b.dataset.slug)) _abrirSlug(b.dataset.slug);
    }));
  }

  function _renderLiveRail() {
    const tl = document.getElementById('mem-tl');
    if (!tl) return;
    document.querySelectorAll('.mem-rail-t').forEach(b => b.classList.toggle('activo', b.dataset.r === _liveRail));
    const M = Meta();
    if (_liveRail === 'actividad') {
      const st = _liveEstado || window.JarvisLiveState.crearEstado();
      const act = st.actividad || [];
      tl.innerHTML = act.length ? `<ol class="mem-tl-list">${act.map((e, i) => `
        <li class="mem-tl-ev k-${esc(e.clase || 'normal')}" style="--i:${Math.min(i, 12)}">
          <i class="mem-tl-node" aria-hidden="true"></i>
          <div class="mem-tl-body">
            <span class="mem-tl-tx" data-i18n-skip>${esc(_t(e.texto))}</span>
            <span class="mem-tl-meta">${esc(e.hora)}</span>
          </div>
        </li>`).join('')}</ol>`
        : `<div class="mem-tl-vacio">${icon('history', 18)}<span>Todavía nada.</span></div>`;
      return;
    }
    // Recall: eventos de memoria_uso agrupados por (terminal, resultado, instante)
    const grupos = [];
    for (const e of _uso.eventos || []) {
      const g = grupos[grupos.length - 1];
      const clave = `${e.terminal_id}|${e.resultado}|${String(e.timestamp).slice(0, 19)}`;
      if (g && g.clave === clave) g.slugs.push(e.slug);
      else grupos.push({ clave, terminal: e.terminal, tipo_ia: e.tipo_ia, resultado: e.resultado, timestamp: e.timestamp, slugs: [e.slug] });
    }
    if (!grupos.length) {
      tl.innerHTML = `<div class="mem-tl-vacio">${icon('brain', 18)}
          <b>Sin lecturas registradas todavía.</b>
          <span>Aparecen cuando el recall le sugiere memorias a un agente y cuando un agente cierra su paso citando las que usó.</span></div>`;
      return;
    }
    tl.innerHTML = `<ol class="mem-tl-list">${grupos.slice(0, 40).map((g, i) => {
      const r = M.resultadoUso(g.resultado);
      return `
      <li class="mem-tl-ev k-${r.k}" style="--i:${Math.min(i, 12)}">
        <i class="mem-tl-node" aria-hidden="true"></i>
        <div class="mem-tl-body">
          <span class="mem-tl-meta"><span class="mem-res r-${r.k}">${r.label}</span>
            ${g.terminal ? `<span class="mem-tl-quien" data-i18n-skip>${esc(g.terminal)}</span>` : ''}
            <span class="mem-tl-hace" title="${esc(g.timestamp)}">${esc(M.haceCorto(g.timestamp))}</span></span>
          <span class="mem-tl-mems">${g.slugs.map(s => {
            const m = _mem(s);
            return m
              ? `<button type="button" class="mem-mchip mc" data-slug="${esc(s)}" ${_catAttr(m.categoria)}><i aria-hidden="true"></i><span data-i18n-skip>${esc(m.titulo)}</span></button>`
              : `<span class="mem-mchip borrada" title="${esc(_t('ya no existe'))}"><i aria-hidden="true"></i><span data-i18n-skip>${esc(s)}</span></span>`;
          }).join('')}</span>
        </div>
      </li>`; }).join('')}</ol>`;
    tl.querySelectorAll('.mem-mchip[data-slug]').forEach(b => b.addEventListener('click', () => _abrirSlug(b.dataset.slug)));
  }

  // El Mapa: constelación viva del enjambre (agentes ↔ archivos). Mide el
  // contenedor REAL (px) → llena el espacio disponible. Layout de fuerzas +
  // colisión + declutter de etiquetas; posiciones persistidas entre renders
  // (_mapaPos) para que un update no re-baraje todo.
  function _renderLiveMapa(cuerpo, st, flashes) {
    const agentes = (st.agentes || []).filter(a => (a.archivos || []).length || a.estado === 'trabajando');
    const paths = [...new Set((st.agentes || []).flatMap(a => (a.archivos || []).map(f => f.path)))];
    if (!agentes.length) {
      cuerpo.innerHTML = `<div class="mem-estado-vacio chico">
          <span class="mem-vacio-icono">${icon('workflow', 20)}</span>
          <b>El mapa aparece cuando los agentes tocan archivos.</b></div>`;
      return;
    }
    const rect = cuerpo.getBoundingClientRect();
    const W = Math.max(360, Math.round(rect.width)), H = Math.max(280, Math.round(rect.height));
    const R = Math.min(W, H);
    const SC = Math.max(0.8, Math.min(1.7, R / 470));
    const REST = 118 * SC;
    const AR = 18, FR = 6;
    const MXA = 76, MYT = 34, MYB = 58, MF = 34;
    const LEG = { w: 300, h: 52 };
    const seed = (id, fx, fy) => {
      const p = _mapaPos[id];
      return p ? { x: p.x, y: p.y } : { x: fx, y: fy };
    };

    const nodos = [
      ...agentes.map((a, i) => { const ang = i / agentes.length * Math.PI * 2 - Math.PI / 2;
        const s = seed('a:' + a.terminal_id, W / 2 + Math.cos(ang) * R * 0.38, H / 2 + Math.sin(ang) * R * 0.32);
        return { id: 'a:' + a.terminal_id, tipo: 'agente', label: a.nombre, estado: a.estado,
          tipo_ia: a.tipo_ia, r: AR, x: s.x, y: s.y, vx: 0, vy: 0 }; }),
      ...paths.map((p, i) => { const ang = i / Math.max(1, paths.length) * Math.PI * 2 + 0.6;
        const s = seed('f:' + p, W / 2 + Math.cos(ang) * R * 0.16, H / 2 + Math.sin(ang) * R * 0.14);
        return { id: 'f:' + p, tipo: 'archivo', path: p, label: p.split('/').pop(), r: FR, x: s.x, y: s.y, vx: 0, vy: 0 }; }),
    ];
    const porId = Object.fromEntries(nodos.map(n => [n.id, n]));
    const aristas = [];
    for (const a of st.agentes) {
      for (const f of a.archivos || []) {
        const na = porId['a:' + a.terminal_id], nf = porId['f:' + f.path];
        if (!na || !nf) continue;
        const permisos = _permisosDe(st, f.path);
        aristas.push({ a: na, b: nf, writes: f.writes, reads: f.reads, dueno: f.dueno,
          clase: !f.writes ? 'read'
            : permisos.some(p => p.estado === 'no') ? 'denegada'
            : (!f.dueno && permisos.some(p => p.estado === 'pendiente')) ? 'pendiente'
            : 'write',
          activa: flashes.has(f.path) });
      }
    }
    const springs = aristas.map(e => [e.a, e.b]);

    for (let it = 0; it < 520; it++) {
      const cool = Math.max(0.12, 1 - it / 520);
      for (const n of nodos) {
        for (const m of nodos) {
          if (n === m) continue;
          const dx = n.x - m.x, dy = n.y - m.y; let d2 = dx * dx + dy * dy; if (d2 < 1) d2 = 1;
          const rep = (n.tipo === 'agente' && m.tipo === 'agente' ? 19000
            : n.tipo === 'agente' || m.tipo === 'agente' ? 7600 : 5200) * SC * SC / d2;
          const d = Math.sqrt(d2); n.vx += dx / d * rep; n.vy += dy / d * rep;
        }
        n.vx += (W / 2 - n.x) * 0.015; n.vy += (H / 2 - n.y) * 0.015;
      }
      for (const [a, b] of springs) {
        const dx = b.x - a.x, dy = b.y - a.y, d = Math.max(Math.hypot(dx, dy), 1);
        const f = (d - REST) * 0.022; a.vx += dx / d * f; a.vy += dy / d * f; b.vx -= dx / d * f; b.vy -= dy / d * f;
      }
      for (const n of nodos) for (const m of nodos) {
        if (n === m) continue;
        const dx = n.x - m.x, dy = n.y - m.y, d = Math.hypot(dx, dy);
        const min = n.tipo === 'agente' && m.tipo === 'agente' ? AR * 2 + 62
          : (n.tipo === 'archivo' && m.tipo === 'archivo' ? 52 : n.r + m.r + 22);
        if (d > 0 && d < min) { const push = (min - d) / d * 0.5; n.vx += dx * push; n.vy += dy * push; }
      }
      for (const n of nodos) {
        const mx = n.tipo === 'agente' ? MXA : MF;
        const myT = n.tipo === 'agente' ? MYT : MF, myB = n.tipo === 'agente' ? MYB : MF;
        n.x = Math.min(W - mx, Math.max(mx, n.x + n.vx * 0.5 * cool));
        n.y = Math.min(H - myB, Math.max(myT, n.y + n.vy * 0.5 * cool));
        if (n.x < LEG.w && n.y > H - LEG.h) n.y = H - LEG.h - (n.tipo === 'agente' ? 18 : 6);
        n.vx *= 0.56; n.vy *= 0.56;
      }
    }

    for (const n of nodos) if (n.tipo === 'archivo') n.up = n.y < H * 0.5;
    const clamp = (n) => {
      const mx = n.tipo === 'agente' ? MXA : MF, myT = n.tipo === 'agente' ? MYT : MF, myB = n.tipo === 'agente' ? MYB : MF;
      n.x = Math.min(W - mx, Math.max(mx, n.x)); n.y = Math.min(H - myB, Math.max(myT, n.y));
      if (n.x < LEG.w && n.y > H - LEG.h) n.y = H - LEG.h - (n.tipo === 'agente' ? 18 : 6);
    };
    const lblBox = (n) => {
      const isA = n.tipo === 'agente';
      const w = n.label.length * (isA ? 6.6 : 6.0) + (isA ? 10 : 6), h = isA ? 15 : 13;
      const cy = isA ? n.y + AR + 21 : (n.up ? n.y - 16 : n.y + 16);
      return { x: n.x - w / 2, y: cy - h / 2, w, h, n };
    };
    for (let it = 0; it < 80; it++) {
      const bx = nodos.map(lblBox); let movido = false;
      for (let i = 0; i < bx.length; i++) for (let j = i + 1; j < bx.length; j++) {
        const a = bx[i], b = bx[j];
        const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
        const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
        if (ox > 0 && oy > 0) { movido = true;
          if (oy <= ox) { const p = (oy / 2 + 1) * (a.n.y <= b.n.y ? 1 : -1); a.n.y -= p; b.n.y += p; }
          else { const p = (ox / 2 + 1) * (a.n.x <= b.n.x ? 1 : -1); a.n.x -= p; b.n.x += p; }
        }
      }
      for (const n of nodos) clamp(n);
      if (!movido) break;
    }
    _mapaPos = {};
    for (const n of nodos) _mapaPos[n.id] = { x: n.x, y: n.y };

    const P = (e) => {
      const ax = e.a.x, ay = e.a.y, bx = e.b.x, by = e.b.y;
      const mx = (ax + bx) / 2, my = (ay + by) / 2, dx = bx - ax, dy = by - ay, len = Math.hypot(dx, dy) || 1;
      const off = Math.min(38, len * 0.16), cx = mx - dy / len * off, cy = my + dx / len * off;
      return `M${ax.toFixed(1)} ${ay.toFixed(1)} Q${cx.toFixed(1)} ${cy.toFixed(1)} ${bx.toFixed(1)} ${by.toFixed(1)}`;
    };

    cuerpo.innerHTML = `
      <div class="mem-live-mapa">
        <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
          <g class="mem-live-aristas">
            ${aristas.map(e => `<path class="mem-live-arista ${e.clase} ${e.activa ? 'activa' : ''}" d="${P(e)}"
                style="--w:${Math.min(3, 1 + (e.writes || 0) * 0.5)}px"/>`).join('')}
          </g>
          <g class="mem-live-nodos">
            ${nodos.filter(n => n.tipo === 'archivo').map(n => `
              <g class="mem-live-narchivo ${flashes.has(n.path) ? 'flash' : ''}" transform="translate(${n.x.toFixed(1)},${n.y.toFixed(1)})">
                <circle class="halo" r="11"/><circle class="dot" r="4.5"/>
                <text class="lbl" y="${n.up ? -11 : 16}" dominant-baseline="${n.up ? 'auto' : 'hanging'}" data-i18n-skip>${esc(n.label.slice(0, 26))}</text>
              </g>`).join('')}
            ${nodos.filter(n => n.tipo === 'agente').map(n => `
              <g class="mem-live-nagente" data-estado="${esc(n.estado)}" transform="translate(${n.x.toFixed(1)},${n.y.toFixed(1)})">
                <circle class="aura" r="${AR + 12}"/>
                <circle class="disco" r="${AR}"/>
                <circle class="anillo" r="${AR}"/>
                <foreignObject x="-12" y="-12" width="24" height="24"><div class="lg" xmlns="http://www.w3.org/1999/xhtml">${window.cliLogo ? window.cliLogo(n.tipo_ia, 19) : ''}</div></foreignObject>
                ${n.estado === 'trabajando' ? `<circle class="pip" r="3.6" cx="${(AR * 0.72).toFixed(1)}" cy="${(-AR * 0.72).toFixed(1)}"/>` : ''}
                <text class="lbl" y="${AR + 15}" data-i18n-skip>${esc(n.label)}</text>
              </g>`).join('')}
          </g>
        </svg>
        <div class="mem-live-legend">
          <span><i class="ln write"></i>escribe</span>
          <span><i class="ln read"></i>lee</span>
          <span><i class="ln pendiente"></i>permiso</span>
          <span><i class="ln denegada"></i>conflicto</span>
          <span>${LOCK}dueño</span>
        </div>
      </div>`;
  }

  /* ══ API pública ═════════════════════════════════════════════════ */
  window.JarvisMemory = {
    init(projectId) {
      _projectId = projectId;
      // El disparador vive en JarvisSettings (sección Memoria).
    },
    onProjectChanged(projectId) {
      _projectId = projectId;
      _memorias = []; _edges = []; _salud = null; _uso = { eventos: [], conteo: {} };
      _slugAbierta = null; _modo = 'ver'; _leyendo = false;
      _cats = []; _query = ''; _estado = 'todas'; _saludFiltro = null;
      _liveEstado = null; _mapaPos = {}; _grafPos = {}; _grafT = null;
      _pararGrafo(); _pararLive();
    },
    abrir,
    onLiveEvent,
  };
})();
