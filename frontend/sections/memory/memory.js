// JARVIS — Memoria compartida del proyecto (UI).
// Modal glass: lista + viewer (markdown mini con wikilinks clickeables) +
// vista de GRAFO (force layout en SVG puro) + crear/editar/borrar.
// Los agentes escriben las memorias como archivos (.jarvis/memory/) según el
// protocolo inyectado en CLAUDE.md; esta UI es la ventana del humano.
// Expone window.JarvisMemory = { init, onProjectChanged, abrir }.

(() => {
  let _projectId = null;
  let _memorias  = [];
  let _edges     = [];
  let _slugAbierta = null;
  let _tab       = 'lista';   // 'lista' | 'grafo' | 'live'
  let _query     = '';
  let _liveEstado = null;          // estado de JarvisLiveState
  let _liveVista  = 'pulso';       // 'pulso' | 'mapa'
  let _liveTimer  = null;          // limpieza de flashes
  let _mapaPos    = {};            // id de nodo → {x,y} del último layout (persistencia del grafo)
  let _grafT      = { k: 1, x: 0, y: 0 };  // zoom + paneo de la vista Grafo
  let _grafTimer  = null;          // tick de disparos neuronales del grafo
  let _core       = null;          // centro de masa del grafo (núcleo central)
  let _salud      = null;          // lint del backend (/memory/salud)

  const esc = (s) => { const d = document.createElement('div'); d.textContent = String(s ?? ''); return d.innerHTML; };
  const _t = (s) => (window.JarvisI18n && window.JarvisI18n.t) ? window.JarvisI18n.t(s) : s;

  /* ── Datos ─────────────────────────────────────────────────── */
  async function _cargar() {
    try {
      const r = await fetch(`/api/projects/${_projectId}/memory`);
      if (!r.ok) return;
      const data = await r.json();
      _memorias = data.memorias || [];
      _edges    = data.edges || [];
    } catch { /* red */ }
    try {
      const rs = await fetch(`/api/projects/${_projectId}/memory/salud`);
      _salud = rs.ok ? await rs.json() : null;
    } catch { _salud = null; }
  }

  // Strip de salud del linter (solo si hay problemas): hace visible lo que
  // antes era invisible — links rotos, citas muertas, huérfanas, contrato de
  // admisión, choques lápida-vs-vigente, cuarentena y candidatas a guard.
  function _saludHTML() {
    const meta = window.JarvisMemoryMeta;
    const probs = meta ? meta.problemasSalud(_salud) : [];
    const lecc = meta ? meta.estadoLecciones(_salud) : null;
    const globales = (_salud && _salud.candidatas_global) || [];
    if (!probs.length && !lecc && !globales.length) return '';
    const NOMBRES = {
      rotos: 'links rotos', citas: 'citas muertas', huerfanas: 'huérfanas',
      contrato: 'no cumplen contrato', choques: 'choques', cuarentena: 'en cuarentena',
      guard: 'candidatas a guard', duplicados: 'duplicadas', global: 'candidatas a global',
    };
    let html = '';
    if (probs.length) {
      html += `<div class="mem-salud" title="Salud de .jarvis/memory/ — links rotos, citas a archivos borrados, huérfanas, contrato de admisión, choques lápida-vs-vigente, cuarentena por no-uso, duplicadas (misma memoria escrita dos veces), lecciones violadas N veces (candidatas a guard) y lecciones que merecen ser globales">
        ${icon('alert', 11)} ${probs.map(p => `${p.n}&nbsp;<span>${NOMBRES[p.k] || p.k}</span>`).join(' · ')}
      </div>`;
    }
    if (lecc) {
      html += `<div class="mem-salud mem-salud-lecciones${lecc.alerta ? ' mem-salud-alerta' : ''}"
        title="Loop de lecciones: cuántas reglas están siempre-cargadas en CLAUDE.md/AGENTS.md y el estado del destilador de fallos">
        ${icon('sparkle', 11)} <span>${esc(lecc.texto)}</span>
      </div>`;
    }
    const alti = meta ? meta.altimetro(_salud) : null;
    if (alti) {
      html += `<div class="mem-salud mem-salud-altimetro"
        title="¿El recall rinde? De las memorias que el sistema inyectó a los prompts (7 días), cuántas leyeron de verdad los agentes según su cierre, y cuántas lecturas fueron en pasos que terminaron bien">
        ${icon('chart', 11)} <span>${esc(alti.texto)}</span>
      </div>`;
    }
    if (globales.length) {
      // Candidatas a memoria GLOBAL: un click y los proyectos futuros la heredan.
      html += `<div class="mem-salud mem-salud-global" title="Lecciones de este proyecto que valen para TODOS (entorno compartido o fallo repetido en otro proyecto) — promover las siembra en cada proyecto nuevo">
        ${icon('globe', 11)} ${globales.slice(0, 4).map(c =>
          `<span class="mem-global-chip">${esc(c.slug)}
            <button class="mem-promover" data-slug="${esc(c.slug)}" type="button">Promover</button>
          </span>`).join(' ')}
      </div>`;
    }
    return html;
  }

  async function _promover(slug, btn) {
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    try {
      const r = await fetch(`/api/projects/${_projectId}/memory/${encodeURIComponent(slug)}/promover`,
                            { method: 'POST' });
      if (!r.ok) throw new Error(await r.text());
      window.toast?.(_t('Globo · {slug} promovida a memoria global').replace('{slug}', slug), 'ok');
      await _cargar();
      _renderBody();
    } catch (e) {
      window.toast?.(_t('No pude promover {slug}').replace('{slug}', slug), 'error');
      if (btn) { btn.disabled = false; btn.textContent = 'Promover'; }
    }
  }

  const _badgesHTML = (m) =>
    (window.JarvisMemoryMeta ? JarvisMemoryMeta.badges(m) : [])
      .map(b => `<span class="mem-badge mem-badge-${b.k}">${b.label}</span>`).join('');

  /* ── Markdown mini (escapado primero; wikilinks como chips) ── */
  function _render_md(src) {
    src = String(src ?? '');                               // memoria vacía/null no debe romper el viewer
    let h = esc(src.replace(/^---[\s\S]*?---\s*/, '')); // sin frontmatter
    h = h.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    h = h.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    h = h.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    h = h.replace(/^# (.+)$/gm, '<h1>$1</h1>');
    h = h.replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>');
    h = h.replace(/^- (.+)$/gm, '<li>$1</li>');
    h = h.replace(/\[\[([^\]\n]+)\]\]/g, (_, t) =>
      `<button class="mem-wikilink" data-link="${esc(t)}">${icon('sparkle', 9)} ${esc(t)}</button>`);
    return h.split(/\n{2,}/).map(p =>
      p.startsWith('<h') || p.startsWith('<li') ? p : `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');
  }

  /* ── Modal ─────────────────────────────────────────────────── */
  // Handler de Esc nombrado a nivel de módulo: se quita SIEMPRE en _cerrar()
  // (antes solo se autoremovía al apretar Escape → leak al cerrar con ✕/overlay).
  function _onKey(e) { if (e.key === 'Escape') _cerrar(); }
  function _cerrar() {
    clearTimeout(_liveTimer);
    clearInterval(_grafTimer); _grafTimer = null;
    document.removeEventListener('keydown', _onKey);
    document.querySelector('.mem-overlay')?.remove();
  }

  async function abrir() {
    _cerrar();
    await _cargar();

    const ov = document.createElement('div');
    ov.className = 'mem-overlay';
    ov.innerHTML = `
      <div class="mem-modal" role="dialog" aria-modal="true" aria-label="Memoria del proyecto">
        <div class="mem-top">
          <span class="mem-titulo">Memoria.</span>
          <span class="mem-count" id="mem-count"></span>
          <div class="mem-tabs" role="tablist">
            <button class="mem-tab activo" data-tab="lista" role="tab">Lista</button>
            <button class="mem-tab" data-tab="grafo" role="tab">Grafo</button>
            <button class="mem-tab" data-tab="live" role="tab"><span class="mem-live-dot"></span>Live</button>
          </div>
          <button class="mem-nueva" id="mem-nueva" type="button">${icon('plus', 11)} Nueva</button>
          <button class="mem-cerrar" id="mem-cerrar" type="button" aria-label="Cerrar">${icon('x', 13)}</button>
        </div>
        <div class="mem-body" id="mem-body"></div>
      </div>`;
    document.body.appendChild(ov);

    ov.addEventListener('click', (e) => { if (e.target === ov) _cerrar(); });
    ov.querySelector('#mem-cerrar').addEventListener('click', _cerrar);
    ov.querySelector('#mem-nueva').addEventListener('click', _crear);
    ov.querySelectorAll('.mem-tab').forEach(t =>
      t.addEventListener('click', () => {
        _tab = t.dataset.tab;
        ov.querySelectorAll('.mem-tab').forEach(x => x.classList.toggle('activo', x === t));
        _renderBody();
      }));
    document.addEventListener('keydown', _onKey);

    _slugAbierta = _memorias[0]?.slug || null;
    _renderBody();
  }

  function _renderBody() {
    const body = document.getElementById('mem-body');
    if (!body) return;
    document.getElementById('mem-count').textContent =
      _t('{n} memoria(s) · .jarvis/memory/').replace('{n}', _memorias.length);
    if (_tab === 'grafo') { _renderGrafo(body); return; }
    if (_tab === 'live')  { _renderLive(body);  return; }

    body.innerHTML = `
      <div class="mem-lista">
        <div class="mem-search">
          ${icon('search', 12)}
          <input type="text" id="mem-search" placeholder="Buscar…" value="${esc(_query)}"
                 autocomplete="off" spellcheck="false">
        </div>
        ${_saludHTML()}
        <div class="mem-items" id="mem-items"></div>
      </div>
      <div class="mem-viewer" id="mem-viewer"></div>`;

    document.getElementById('mem-search').addEventListener('input', (e) => {
      _query = e.target.value; _renderItems();
    });
    body.querySelectorAll('.mem-promover').forEach(btn =>
      btn.addEventListener('click', () => _promover(btn.dataset.slug, btn)));
    _renderItems();
    _renderViewer();
  }

  function _renderItems() {
    const cont = document.getElementById('mem-items');
    if (!cont) return;
    const q = _query.toLowerCase();
    const filtradas = _memorias.filter(m =>
      !q || m.titulo.toLowerCase().includes(q) || m.resumen.toLowerCase().includes(q)
        || (m.tags || []).some(t => t.toLowerCase().includes(q)));
    if (!filtradas.length) {
      cont.innerHTML = `<div class="mem-vacio">${_memorias.length
        ? 'Sin coincidencias.'
        : 'Todavía no hay memorias.<br><br>Las escriben tus agentes al descubrir cosas del proyecto (el protocolo ya está en el CLAUDE.md) — o creá la primera vos.'}</div>`;
      return;
    }
    cont.innerHTML = filtradas.map((m, i) => `
      <button class="mem-item ${m.slug === _slugAbierta ? 'activo' : ''} ${(m.estado && m.estado !== 'vigente') ? 'mem-item-apagada' : ''}" data-slug="${esc(m.slug)}" style="--i:${Math.min(i, 12)}">
        <span class="mem-item-titulo">${esc(m.titulo)}${_badgesHTML(m)}</span>
        <span class="mem-item-sub">${esc(window.JarvisMemoryMeta ? JarvisMemoryMeta.subLinea(m) : (m.autor || '—'))}</span>
      </button>`).join('');
    cont.querySelectorAll('.mem-item').forEach(it =>
      it.addEventListener('click', () => {
        _slugAbierta = it.dataset.slug;
        _renderItems(); _renderViewer();
      }));
  }

  async function _renderViewer() {
    const v = document.getElementById('mem-viewer');
    if (!v) return;
    if (!_slugAbierta) {
      v.innerHTML = '<div class="mem-vacio">Elegí una memoria de la lista.</div>';
      return;
    }
    // slug/proyecto CONGELADOS: si mientras viajaba la respuesta se eligió otra
    // memoria, esta respuesta ya no vale — y Borrar/Guardar deben actuar sobre
    // la memoria MOSTRADA, no sobre la global _slugAbierta (borraba la otra).
    const slug = _slugAbierta, pid = _projectId;
    let mem;
    try {
      const r = await fetch(`/api/projects/${pid}/memory/${slug}`);
      if (slug !== _slugAbierta || pid !== _projectId) return;
      if (!r.ok) { v.innerHTML = '<div class="mem-vacio">No se pudo cargar.</div>'; return; }
      mem = await r.json();
    } catch { return; }
    if (slug !== _slugAbierta || pid !== _projectId) return;

    v.innerHTML = `
      <div class="mem-view-head">
        <span class="mem-view-titulo">${esc(mem.titulo)}${_badgesHTML(mem)}</span>
        <span class="mem-view-meta">${esc(mem.autor || '')} · ${esc(mem.creado || '')}${mem.actualizado ? ` · ${_t('act. {f}').replace('{f}', esc(mem.actualizado))}` : ''}</span>
        <span class="mem-view-acciones">
          <button id="mem-editar" type="button">${icon('edit', 11)} Editar</button>
          <button id="mem-borrar" type="button" class="peligro">${icon('trash', 11)} Borrar</button>
        </span>
      </div>
      <div class="mem-contenido" id="mem-contenido">
        ${(mem.tags || []).map(t => `<span class="mem-tag">${esc(t)}</span>`).join('')}
        ${_render_md(mem.contenido)}
      </div>`;

    v.querySelectorAll('.mem-wikilink').forEach(b =>
      b.addEventListener('click', () => {
        const destino = _memorias.find(m =>
          m.slug === b.dataset.link.toLowerCase().replace(/[^a-z0-9]+/g, '-') || m.titulo === b.dataset.link);
        if (destino) { _slugAbierta = destino.slug; _renderItems(); _renderViewer(); }
        else toast(_t('No existe (todavía) la memoria "{s}"').replace('{s}', b.dataset.link), 'info');
      }));

    v.querySelector('#mem-borrar').addEventListener('click', async () => {
      if (!(await confirmar(_t('¿Borrar la memoria "{t}"?').replace('{t}', mem.titulo), { peligro: true, confirmText: 'Borrar' }))) return;
      await fetch(`/api/projects/${pid}/memory/${slug}`, { method: 'DELETE' });
      _slugAbierta = null;
      await _cargar(); _renderBody();
    });

    v.querySelector('#mem-editar').addEventListener('click', () => {
      v.innerHTML = `
        <div class="mem-editor">
          <textarea id="mem-textarea" spellcheck="false"></textarea>
          <div class="mem-editor-acciones">
            <button class="ob-confirm-btn" id="mem-cancelar" type="button">Cancelar</button>
            <button class="ob-confirm-btn primario" id="mem-guardar" type="button">Guardar</button>
          </div>
        </div>`;
      const ta = v.querySelector('#mem-textarea');
      ta.value = mem.contenido;
      ta.focus();
      v.querySelector('#mem-cancelar').addEventListener('click', _renderViewer);
      v.querySelector('#mem-guardar').addEventListener('click', async () => {
        await fetch(`/api/projects/${pid}/memory/${slug}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ contenido: ta.value }),
        });
        await _cargar(); _renderBody();
        toast('Memoria guardada.', 'success');
      });
    });
  }

  async function _crear() {
    const titulo = await pedirTexto('Título corto y específico:', {
      titulo: 'Nueva memoria', placeholder: 'ej. El backend usa cookies httpOnly', confirmText: 'Crear',
    });
    if (!titulo) return;
    const contenido = await pedirTexto('Contenido (después podés editarlo largo):', {
      titulo: 'Contenido', placeholder: 'qué hay que saber…', confirmText: 'Guardar',
    });
    const r = await fetch(`/api/projects/${_projectId}/memory`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ titulo, contenido: contenido || '' }),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      toast(e.detail || 'No se pudo crear.', 'error');
      return;
    }
    const { slug } = await r.json();
    await _cargar();
    _slugAbierta = slug;
    _renderBody();
  }

  /* ── Grafo (force layout mini, SVG puro + zoom/paneo) ──────────── */
  // Metáfora neuronal: el núcleo es la memoria en sí, las neuronas son las
  // memorias y las sinapsis los [[wikilinks]]. El mundo es grande (1400×900)
  // y SIN clamps de borde — el layout respira y el fit() inicial encuadra;
  // el svg es overflow:visible para que nada se corte al paneear.
  function _renderGrafo(body) {
    clearInterval(_grafTimer); _grafTimer = null;   // apagar disparos del render anterior
    if (!_memorias.length) {
      body.innerHTML = `<div class="mem-vacio" style="flex:1;align-self:center">${_t('El grafo aparece cuando haya memorias enlazadas con [[wikilinks]].')}</div>`;
      return;
    }
    const W = 1400, H = 900;
    const nodos = _memorias.map((m, i) => {
      const grado = _edges.filter(e => e.from === m.slug || e.to === m.slug).length;
      return {
        ...m,
        x: W / 2 + Math.cos(i * 2.4) * (120 + (i % 5) * 40),
        y: H / 2 + Math.sin(i * 2.4) * (90 + (i % 4) * 35),
        vx: 0, vy: 0,
        grado,
        mayor: grado >= 2,   // hierarchy: the declutter (sin-etiquetas) respects them
      };
    });
    const porSlug = Object.fromEntries(nodos.map(n => [n.slug, n]));
    const springs = _edges
      .map(e => [porSlug[e.from], porSlug[e.to]])
      .filter(([a, b]) => a && b);

    // Simulación precomputada (sin loop de animación: 0 costo en reposo).
    // Repulsión más fuerte + gravedad suave al centro; sin límites duros.
    for (let it = 0; it < 320; it++) {
      for (const a of nodos) {
        for (const b of nodos) {
          if (a === b) continue;
          const dx = a.x - b.x, dy = a.y - b.y;
          const d2 = Math.max(dx * dx + dy * dy, 64);
          const f = 8200 / d2;
          const d = Math.sqrt(d2);
          a.vx += (dx / d) * f; a.vy += (dy / d) * f;
        }
        a.vx += (W / 2 - a.x) * 0.010;
        a.vy += (H / 2 - a.y) * 0.010;
      }
      for (const [a, b] of springs) {
        const dx = b.x - a.x, dy = b.y - a.y;
        const d = Math.max(Math.hypot(dx, dy), 1);
        const f = (d - 130) * 0.02;
        a.vx += (dx / d) * f; a.vy += (dy / d) * f;
        b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
      }
      for (const n of nodos) {
        n.x += n.vx * 0.5; n.y += n.vy * 0.5;
        n.vx *= 0.6; n.vy *= 0.6;
      }
    }

    // Núcleo central: centro de masa promedio de las neuronas (memoria en sí).
    _core = {
      x: nodos.reduce((s, n) => s + n.x, 0) / Math.max(1, nodos.length),
      y: nodos.reduce((s, n) => s + n.y, 0) / Math.max(1, nodos.length),
    };

    body.innerHTML = `
      <div class="mem-grafo">
        <div class="mem-graf-ctrl">
          <button type="button" title="${_t('Alejar')}" data-act="out" aria-label="${_t('Alejar')}">−</button>
          <span class="mem-graf-zoom" id="mem-graf-zoom">100%</span>
          <button type="button" title="${_t('Acercar')}" data-act="in" aria-label="${_t('Acercar')}">+</button>
          <button type="button" title="${_t('Ajustar')}" data-act="fit" aria-label="${_t('Ajustar')}">⤢</button>
        </div>
        <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" style="overflow:visible">
          <g class="mem-graf-v">
            <g class="mem-core" transform="translate(${_core.x.toFixed(1)},${_core.y.toFixed(1)})">
              <circle class="mem-core-nucleo" r="30"/>
              <circle class="mem-core-anillo" r="44"/>
              <text class="mem-core-label" y="72" text-anchor="middle">${_t('Memoria')}</text>
            </g>
            ${springs.map(([a, b], i) =>
              `<line class="mem-edge" style="--d:${(i * 22).toFixed(0)}ms" x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}"/>`).join('')}
            ${nodos.map(n => `
              <g class="mem-nodo${n.mayor ? ' mayor' : ''}" data-slug="${esc(n.slug)}" transform="translate(${n.x.toFixed(1)},${n.y.toFixed(1)})">
                <circle r="${7 + Math.min(n.grado * 2.5, 14)}"/>
                <text x="0" y="${18 + Math.min(n.grado * 2.5, 14)}" text-anchor="middle">${esc(n.titulo.slice(0, 26))}</text>
              </g>`).join('')}
          </g>
        </svg>
        <div class="mem-tooltip" hidden style="position:fixed"></div>
        <span class="mem-grafo-hint">${_t('click en un nodo = abrir · tamaño = conexiones · rueda = zoom · arrastrar = mover')}</span>
      </div>`;

    body.querySelectorAll('.mem-nodo').forEach(g =>
      g.addEventListener('click', () => {
        _slugAbierta = g.dataset.slug;
        _tab = 'lista';
        document.querySelectorAll('.mem-tab').forEach(t =>
          t.classList.toggle('activo', t.dataset.tab === 'lista'));
        _renderBody();
      }));

    // ── Zoom + paneo ────────────────────────────────────────────
    const view = body.querySelector('.mem-grafo');
    const svg  = body.querySelector('svg');
    const grp  = body.querySelector('.mem-graf-v');
    const zlbl = body.querySelector('#mem-graf-zoom');
    const tip  = body.querySelector('.mem-tooltip');
    const nodeEls = [...body.querySelectorAll('.mem-nodo')];
    const edges   = [...body.querySelectorAll('.mem-edge')];

    function aplicar() {
      grp.setAttribute('transform', `translate(${_grafT.x.toFixed(1)},${_grafT.y.toFixed(1)}) scale(${_grafT.k.toFixed(3)})`);
      // Escala servida a las etiquetas: el CSS las agranda al alejar (1/k)
      // para que el nombre de cada memoria sea legible a cualquier zoom.
      svg.style.setProperty('--lk', 1 / _grafT.k);
      svg.classList.toggle('sin-etiquetas', _grafT.k < 0.95);
      if (zlbl) zlbl.textContent = Math.round(_grafT.k * 100) + '%';
    }
    function zoomEn(factor) {
      const cx = W / 2, cy = H / 2;
      const k2 = Math.max(0.3, Math.min(3.5, _grafT.k * factor));
      _grafT = { k: k2,
        x: cx - (cx - _grafT.x) * (k2 / _grafT.k),
        y: cy - (cy - _grafT.y) * (k2 / _grafT.k) };
      aplicar();
    }
    function fit() {
      const xs = nodos.map(n => n.x), ys = nodos.map(n => n.y);
      const minX = Math.min(...xs) - 50, maxX = Math.max(...xs) + 50;
      const minY = Math.min(...ys) - 50, maxY = Math.max(...ys) + 50;
      const k = Math.max(0.3, Math.min(2.5, Math.min(W / (maxX - minX), H / (maxY - minY)) * 0.92));
      _grafT = { k: k, x: (W - (maxX + minX) * k) / 2, y: (H - (maxY + minY) * k) / 2 };
      aplicar();
    }

    view.querySelector('[data-act="in"]').addEventListener('click', () => zoomEn(1.25));
    view.querySelector('[data-act="out"]').addEventListener('click', () => zoomEn(1 / 1.25));
    view.querySelector('[data-act="fit"]').addEventListener('click', fit);

    view.addEventListener('wheel', (ev) => {
      ev.preventDefault();
      const r = svg.getBoundingClientRect();
      const px = (ev.clientX - r.left) / r.width * W;
      const py = (ev.clientY - r.top) / r.height * H;
      const f = ev.deltaY < 0 ? 1.16 : 1 / 1.16;
      const k2 = Math.max(0.3, Math.min(3.5, _grafT.k * f));
      _grafT = { k: k2,
        x: px - (px - _grafT.x) * (k2 / _grafT.k),
        y: py - (py - _grafT.y) * (k2 / _grafT.k) };
      aplicar();
    }, { passive: false });

    let down = null;
    view.addEventListener('pointerdown', (ev) => {
      if (ev.button !== 0) return;
      down = { x: ev.clientX, y: ev.clientY, moved: false };
    });
    window.addEventListener('pointermove', (ev) => {
      if (!down) return;
      const r = svg.getBoundingClientRect();
      if (!down.moved && Math.hypot(ev.clientX - down.x, ev.clientY - down.y) > 4) {
        down.moved = true;
        view.classList.add('paneando');
      }
      if (down.moved) {
        ev.preventDefault();
        _grafT.x += (ev.clientX - down.x) / r.width * W / _grafT.k;
        _grafT.y += (ev.clientY - down.y) / r.height * H / _grafT.k;
        down = { x: ev.clientX, y: ev.clientY, moved: true };
        aplicar();
      }
    });
    window.addEventListener('pointerup', () => { down = null; view.classList.remove('paneando'); });

    // ── Highlight del subgrafo conectado (hover) + tooltip ─────
    // El tooltip se llena SOLO en pointerenter (no en el move) y queda
    // visible a cualquier zoom: la identidad de la memoria no se pierde
    // al alejar (las etiquetas del nodo las escala --lk).
    function tooltipMostrar(ev, n) {
      tip.innerHTML = `${_badgesHTML(n)}<b>${esc(n.titulo)}</b><i>${esc(n.autor)} · ${esc(n.creado)}${n.actualizado ? ' · act. ' + esc(n.actualizado) : ''}</i><em>${_t('Conexiones')}: ${n.grado}</em>`;
      tip.hidden = false;
      const w = tip.offsetWidth, h = tip.offsetHeight;
      // Flip: si a la derecha no entra, va a la IZQUIERDA del cursor
      const aDerecha = (ev.clientX + 14 + w) <= window.innerWidth - 6;
      tip.style.left = (aDerecha ? ev.clientX + 14 : ev.clientX - w - 14) + 'px';
      tip.style.top  = Math.min(ev.clientY + 14, window.innerHeight - h - 6) + 'px';
    }
    nodeEls.forEach(g => {
      g.addEventListener('pointerenter', (ev) => {
        const slug = g.dataset.slug;
        const conn = new Set([slug]);
        springs.forEach(([a, b]) => {
          if (a.slug === slug) conn.add(b.slug);
          if (b.slug === slug) conn.add(a.slug);
        });
        nodeEls.forEach(o => o.classList.toggle('dim', !conn.has(o.dataset.slug)));
        edges.forEach((e, i) => {
          const [a, b] = springs[i];
          const toca = a.slug === slug || b.slug === slug;
          e.classList.toggle('activo', toca);
          e.classList.toggle('atenuado', !toca);
        });
        g.classList.add('hito');
        const n = porSlug[slug];
        if (n) tooltipMostrar(ev, n);
      });
      g.addEventListener('pointerleave', () => {
        nodeEls.forEach(o => o.classList.remove('dim'));
        edges.forEach(e => e.classList.remove('activo', 'atenuado'));
        g.classList.remove('hito');
        tip.hidden = true;
      });
    });

    fit();

    // ── Disparos neuronales ─────────────────────────────────────
    // Un nodo al azar "dispara" y sus sinapsis se encienden: flash efímero
    // sin animaciones infinitas. Se limpia en el próximo render/cierre.
    // El mouse sobre el grafo pausa los disparos (el hover ya es señal).
    let _firing = true;
    view.addEventListener('pointerenter', () => { _firing = false; });
    view.addEventListener('pointerleave', () => { _firing = true; });
    _grafTimer = setInterval(() => {
      if (!_firing) return;
      const g = nodeEls[Math.floor(Math.random() * nodeEls.length)];
      if (!g) return;
      const slug = g.dataset.slug;
      g.classList.add('fire');
      edges.forEach((e, i) => {
        const [a, b] = springs[i];
        if (a.slug === slug || b.slug === slug) e.classList.add('sinapsis');
      });
      setTimeout(() => {
        g.classList.remove('fire');
        edges.forEach((e, i) => {
          const [a, b] = springs[i];
          if (a.slug === slug || b.slug === slug) e.classList.remove('sinapsis');
        });
      }, 750);
    }, 2400);
  }

  /* ── Live (agentes en vivo) ────────────────────────────────── */
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
    // re-render solo si el modal está abierto en la pestaña live
    if (_tab === 'live' && document.querySelector('.mem-overlay')) {
      const body = document.getElementById('mem-body');
      if (body) _renderLive(body, /*sinFetch*/ true);
    }
  }

  async function _renderLive(body, sinFetch) {
    if (!sinFetch) await _cargarLive();
    const L = window.JarvisLiveState;
    const st = _liveEstado || L.crearEstado();
    const ahora = Date.now();
    const flashes = new Set(L.flashesVigentes(st, ahora));

    const nAg   = (st.agentes || []).length;
    const nTrab = (st.agentes || []).filter(a => a.estado === 'trabajando').length;
    const nArch = new Set((st.agentes || []).flatMap(a => (a.archivos || []).map(f => f.path))).size;

    body.innerHTML = `
      <div class="mem-live">
        <div class="mem-live-main" id="mem-live-main">
          <div class="mem-live-toolbar">
            <div class="mem-live-vistas">
              <button class="mem-live-v ${_liveVista === 'pulso' ? 'activo' : ''}" data-v="pulso">Pulso</button>
              <button class="mem-live-v ${_liveVista === 'mapa' ? 'activo' : ''}" data-v="mapa">Mapa</button>
            </div>
            <div class="mem-live-stats">
              <span class="mem-live-stat"><i class="dot ${nTrab ? 'trab' : ''}"></i>${_t('{n} trabajando').replace('{n}', nTrab)}</span>
              <span class="mem-live-stat">${_t('{n} agente(s)').replace('{n}', nAg)}</span>
              <span class="mem-live-stat">${_t('{n} archivo(s)').replace('{n}', nArch)}</span>
            </div>
          </div>
          <div class="mem-live-cuerpo" id="mem-live-cuerpo"></div>
        </div>
        <aside class="mem-live-feed" id="mem-live-feed"></aside>
      </div>`;

    body.querySelectorAll('.mem-live-v').forEach(b =>
      b.addEventListener('click', () => { _liveVista = b.dataset.v; _renderLive(body, true); }));

    const cuerpo = body.querySelector('#mem-live-cuerpo');
    if (_liveVista === 'mapa') _renderLiveMapa(cuerpo, st, flashes);
    else _renderLivePulso(cuerpo, st, flashes);
    _renderLiveFeed(body.querySelector('#mem-live-feed'), st);

    // los flashes decaen solos: un re-render diferido los apaga
    clearTimeout(_liveTimer);
    if (flashes.size) {
      _liveTimer = setTimeout(() => {
        if (_tab === 'live' && document.getElementById('mem-live-cuerpo')) _renderLive(body, true);
      }, L.FLASH_MS + 100);
    }
  }

  function _permisosDe(st, path) {
    return (st.permisos || []).filter(p =>
      p.archivo === path || path.endsWith('/' + p.archivo) || p.archivo.endsWith('/' + path));
  }

  function _renderLivePulso(cuerpo, st, flashes) {
    const L = window.JarvisLiveState;
    const agentes = L.ordenarAgentes(st.agentes);
    if (!agentes.length) {
      cuerpo.innerHTML = '<div class="mem-vacio">Sin agentes activos en este proyecto.</div>';
      return;
    }
    const PEMOJI = { pendiente: '⏳', ok: '✅', no: '⛔', expirado: '💨' };
    cuerpo.innerHTML = `<div class="mem-live-cards">${agentes.map(a => `
      <div class="mem-live-card" data-estado="${esc(a.estado)}">
        <div class="mem-live-head">
          <span class="mem-live-anillo" data-estado="${esc(a.estado)}">${window.cliLogo(a.tipo_ia, 18)}</span>
          <span class="mem-live-nombre">${esc(a.nombre)}</span>
          <span class="mem-live-estado">${a.estado === 'trabajando' ? 'trabajando' : 'idle'}</span>
        </div>
        <div class="mem-live-chips">${(a.archivos || []).filter(f => f.writes > 0 || f.reads > 0).map(f => {
          const permisos = _permisosDe(st, f.path);
          const burbujas = permisos.map(p =>
            `<span class="mem-live-permiso" data-estado="${esc(p.estado)}"
                   title="${esc(p.pide)} → ${esc(p.dueno)}: ${esc(p.detalle || '')}${p.respuesta ? ' · ' + _t('resp: {r}').replace('{r}', esc(p.respuesta)) : ''}">${PEMOJI[p.estado] || '·'}</span>`).join('');
          return `<span class="mem-live-chip ${flashes.has(f.path) ? 'flash' : ''} ${f.writes ? 'write' : 'read'}"
                        title="${esc(f.path)} — ${f.writes}w/${f.reads}r, hace ${L.hace(f.hace_s)}">
            ${f.dueno ? '<i class="lock">🔒</i>' : ''}${esc(f.path.split('/').pop())}${f.writes > 1 ? `<i class="x">×${f.writes}</i>` : ''}${burbujas}
          </span>`;
        }).join('') || '<span class="mem-live-sinops">sin actividad de archivos</span>'}</div>
      </div>`).join('')}</div>`;
  }

  function _renderLiveFeed(aside, st) {
    aside.innerHTML = `<div class="mem-live-feed-titulo">Actividad</div>
      <div class="mem-live-feed-items">${(st.actividad || []).map((e, i) => `
        <div class="mem-live-evento ${esc(e.clase)}" style="--i:${Math.min(i, 12)}">
          <span class="mem-live-hora">${esc(e.hora)}</span><span class="tx">${esc(e.texto)}</span>
        </div>`).join('') || '<div class="mem-vacio">Todavía nada.</div>'}</div>`;
  }

  // El Mapa: constelación viva del enjambre. Mide el contenedor REAL (px) →
  // el grafo llena el espacio disponible en vez de un viewBox fijo 900×480 que
  // se achicaba (pedido del usuario: "se queda sin espacio"). Layout de fuerzas
  // + colisión + declutter de etiquetas; posiciones persistidas entre renders
  // (_mapaPos) para que un update no re-baraje todo — solo lo nuevo se acomoda.
  function _renderLiveMapa(cuerpo, st, flashes) {
    const agentes = (st.agentes || []).filter(a => (a.archivos || []).length || a.estado === 'trabajando');
    const paths = [...new Set((st.agentes || []).flatMap(a => (a.archivos || []).map(f => f.path)))];
    if (!agentes.length) {
      cuerpo.innerHTML = '<div class="mem-vacio">El mapa aparece cuando los agentes tocan archivos.</div>';
      return;
    }
    const rect = cuerpo.getBoundingClientRect();
    const W = Math.max(360, Math.round(rect.width)), H = Math.max(280, Math.round(rect.height));
    const R = Math.min(W, H);
    const SC = Math.max(0.8, Math.min(1.7, R / 470));       // escala el grafo al tamaño real (llena el espacio)
    const REST = 118 * SC;                                   // largo de reposo de los resortes agente↔archivo
    const AR = 18, FR = 6;                                   // radios de nodo
    const MXA = 76, MYT = 34, MYB = 58, MF = 34;             // márgenes (agente: lados/arriba/abajo; archivo)
    const LEG = { w: 280, h: 52 };                           // zona de la leyenda (abajo-izq): keep-out
    const seed = (id, fx, fy) => {
      const p = _mapaPos[id];
      return p ? { x: p.x, y: p.y } : { x: fx, y: fy };      // arranca de la posición previa si existe
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
      for (const n of nodos) for (const m of nodos) {              // colisión (separación mínima)
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

    // Lado del label de archivo (arriba/abajo), fijado UNA vez para no oscilar.
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
    for (let it = 0; it < 80; it++) {                             // declutter de etiquetas
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
    _mapaPos = {};                                               // persistir para el próximo render
    for (const n of nodos) _mapaPos[n.id] = { x: n.x, y: n.y };

    const stateCol = { trabajando: 'var(--ob-run)', idle: 'var(--ob-fg-4)' };
    const P = (e) => {                                           // conector curvo agente→archivo
      const ax = e.a.x, ay = e.a.y, bx = e.b.x, by = e.b.y;
      const mx = (ax + bx) / 2, my = (ay + by) / 2, dx = bx - ax, dy = by - ay, len = Math.hypot(dx, dy) || 1;
      const off = Math.min(38, len * 0.16), cx = mx - dy / len * off, cy = my + dx / len * off;
      return `M${ax.toFixed(1)} ${ay.toFixed(1)} Q${cx.toFixed(1)} ${cy.toFixed(1)} ${bx.toFixed(1)} ${by.toFixed(1)}`;
    };

    cuerpo.innerHTML = `
      <div class="mem-live-mapa">
        <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
          <defs>
            <radialGradient id="mem-amb" cx="50%" cy="40%" r="72%">
              <stop offset="0%" stop-color="var(--ob-accent)" stop-opacity="0.11"/>
              <stop offset="55%" stop-color="var(--ob-accent)" stop-opacity="0.03"/>
              <stop offset="100%" stop-color="transparent"/>
            </radialGradient>
            <filter id="mem-softglow" x="-150%" y="-150%" width="400%" height="400%">
              <feGaussianBlur stdDeviation="8"/>
            </filter>
          </defs>
          <rect x="0" y="0" width="${W}" height="${H}" fill="url(#mem-amb)"/>
          <g class="mem-live-aristas">
            ${aristas.map(e => `<path class="mem-live-arista ${e.clase} ${e.activa ? 'activa' : ''}" d="${P(e)}"
                style="--w:${Math.min(3, 1 + (e.writes || 0) * 0.5)}px"/>`).join('')}
          </g>
          <g class="mem-live-nodos">
            ${nodos.filter(n => n.tipo === 'archivo').map(n => `
              <g class="mem-live-narchivo ${flashes.has(n.path) ? 'flash' : ''}" transform="translate(${n.x.toFixed(1)},${n.y.toFixed(1)})">
                <circle class="halo" r="11"/><circle class="dot" r="4.5"/>
                <text class="lbl" y="${n.up ? -11 : 16}" dominant-baseline="${n.up ? 'auto' : 'hanging'}">${esc(n.label.slice(0, 26))}</text>
              </g>`).join('')}
            ${nodos.filter(n => n.tipo === 'agente').map(n => `
              <g class="mem-live-nagente" data-estado="${esc(n.estado)}" transform="translate(${n.x.toFixed(1)},${n.y.toFixed(1)})">
                <circle class="aura" r="${AR + 9}" style="fill:${stateCol[n.estado] || 'var(--ob-fg-4)'}" filter="url(#mem-softglow)"/>
                <circle class="disco" r="${AR}"/>
                <circle class="anillo" r="${AR}" style="stroke:${stateCol[n.estado] || 'var(--ob-line-3)'}"/>
                <foreignObject x="-12" y="-12" width="24" height="24"><div class="lg" xmlns="http://www.w3.org/1999/xhtml">${window.cliLogo(n.tipo_ia, 19)}</div></foreignObject>
                ${n.estado === 'trabajando' ? `<circle class="pip" r="3.6" cx="${(AR * 0.72).toFixed(1)}" cy="${(-AR * 0.72).toFixed(1)}"/>` : ''}
                <text class="lbl" y="${AR + 15}">${esc(n.label)}</text>
              </g>`).join('')}
          </g>
        </svg>
        <div class="mem-live-legend">
          <span><i class="ln write"></i>escribe</span>
          <span><i class="ln read"></i>lee</span>
          <span><i class="ln pendiente"></i>permiso</span>
          <span><i class="ln denegada"></i>conflicto</span>
          <span><i class="lock">🔒</i>dueño</span>
        </div>
      </div>`;
  }

  /* ── API pública ───────────────────────────────────────────── */
  window.JarvisMemory = {
    init(projectId) {
      _projectId = projectId;
      // El disparador ahora vive en JarvisSettings (sección Memoria) — el
      // botón #btn-memory del header viejo fue eliminado.
    },
    onProjectChanged(projectId) {
      _projectId = projectId;
      _memorias = []; _edges = []; _slugAbierta = null;
      _liveEstado = null; _mapaPos = {}; clearTimeout(_liveTimer);
      clearInterval(_grafTimer); _grafTimer = null; _core = null;
    },
    abrir,
    onLiveEvent,
  };
})();
