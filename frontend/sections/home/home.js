// JARVIS — Atelier Console (v3 rework)
// Diseño compacto, dashboard-style. Sin watermark gigante, todo en un viewport.

// ─── Refs ──────────────────────────────────────────────────────
const elGreeting   = document.getElementById('hc-greeting');
const elStatProj   = document.getElementById('stat-projects');
const elStatAgents = document.getElementById('stat-agents');

const elGrid       = document.getElementById('hc-grid');
const elEmpty      = document.getElementById('hc-empty');
const elSearch     = document.getElementById('hc-search-input');
const elFilters    = document.getElementById('hc-filters');


// ─── State ─────────────────────────────────────────────────────
let _proyectos = [];
let _query     = '';
let _filtro    = 'all';  // 'all' | 'pinned' | 'active' | 'archived'

// ─── Utils ─────────────────────────────────────────────────────
const TONES = ['tone-violet','tone-blue','tone-teal','tone-cyan','tone-amber','tone-rose','tone-green'];
function _hash(s) { let h = 0; for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0; return Math.abs(h); }
function _toneFor(n) { return TONES[_hash(n) % TONES.length]; }
function _iniciales(n) {
  const limpio = (n || '?').replace(/[^a-zA-Z0-9]/g, '');
  if (limpio.length === 0) return '??';
  if (limpio.length === 1) return limpio[0].toUpperCase() + limpio[0].toLowerCase();
  return limpio[0].toUpperCase() + limpio[1].toLowerCase();
}
function esc(s) { const d = document.createElement('div'); d.textContent = String(s ?? ''); return d.innerHTML; }
function _t(s) { return (window.JarvisI18n && window.JarvisI18n.t(s)) || s; }
function _fechaHumana(iso) {
  if (!iso) return 'sin actividad';
  const d = new Date(iso); if (isNaN(d)) return 'sin actividad';
  const diff = Date.now() - d.getTime();
  const min = Math.floor(diff / 60_000);
  const h   = Math.floor(diff / 3_600_000);
  const dia = Math.floor(diff / 86_400_000);
  if (min < 1)   return _t('ahora');
  if (min < 60)  return _t('hace {n}m').replace('{n}', min);
  if (h   < 24)  return _t('hace {n}h').replace('{n}', h);
  if (dia < 7)   return _t('hace {n}d').replace('{n}', dia);
  return d.toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit' });
}

// Navegar con transición de salida (fade del body, luego location)
function _irA(url) {
  document.body.classList.add('saliendo');
  setTimeout(() => { location.href = url; }, 170);
}

// Count-up animado para los stats del hero (~400ms, ease-out cúbico)
function _countUp(el, target) {
  if (!el) return;
  const start = parseInt(el.textContent, 10) || 0;
  if (start === target) { el.textContent = target; return; }
  const t0 = performance.now(), dur = 400;
  function paso(t) {
    const k = Math.min((t - t0) / dur, 1);
    el.textContent = Math.round(start + (target - start) * (1 - Math.pow(1 - k, 3)));
    if (k < 1) requestAnimationFrame(paso);
  }
  requestAnimationFrame(paso);
}

// ─── Greeting time-aware ───────────────────────────────────────
function pintarSaludo() {
  const h = new Date().getHours();
  const saludo = h < 6  ? 'Madrugada.'
              : h < 12 ? 'Buenos días.'
              : h < 19 ? 'Buenas tardes.'
              : h < 23 ? 'Buenas noches.'
              :          'Tarde por aquí.';
  if (elGreeting) elGreeting.textContent = saludo;
}

// ─── Carga proyectos ───────────────────────────────────────────
let _primeraCarga = true;
let _firmaDatos   = '';
async function cargarProyectos() {
  if (_primeraCarga && elGrid) {
    // Skeletons mientras resuelve el fetch (sin salto de carga)
    elGrid.innerHTML = '<div class="hc-skel skeleton"></div>'.repeat(6);
  }
  try {
    const res = await fetch('/api/projects');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const datos = await res.json();
    // Refresh imperceptible: si nada cambió, no tocar el DOM
    const firma = JSON.stringify(datos);
    if (!_primeraCarga && firma === _firmaDatos) return;
    _firmaDatos = firma;
    _proyectos = datos;
  } catch (err) {
    console.error('Error cargando proyectos:', err);
    if (_primeraCarga) toast('No pude cargar los proyectos. Reintentá en un momento.', 'error');
    _proyectos = _proyectos || [];
  }
  // Stagger de cards SOLO en el primer paint
  elGrid?.classList.toggle('hc-anim', _primeraCarga);
  _primeraCarga = false;
  pintarStats();
  renderizar();
}

// Lista de proyectos siempre fresca: polling liviano + refetch al volver
// a la pestaña. Con la firma anti-rebuild, el refresh es imperceptible.
setInterval(cargarProyectos, 12000);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) cargarProyectos();
});

// ─── Stats inline ──────────────────────────────────────────────
function pintarStats() {
  const total   = _proyectos.length;
  const agentes = _proyectos.reduce((acc, p) => acc + (p.terminales_activas || 0), 0);
  _countUp(elStatProj, total);
  _countUp(elStatAgents, agentes);
  // El dot "live" solo respira si hay agentes corriendo
  elStatAgents?.closest('.live')?.classList.toggle('on', agentes > 0);

  // Filter counts
  const counts = {
    all:      total,
    pinned:   _proyectos.filter(p => p.seccion === 'pinned').length,
    // Mismo criterio que el filtro (_aplicaFiltros): Activos = todo lo no
    // archivado, anclados incluidos. Antes el número no coincidía con la lista.
    active:   _proyectos.filter(p => p.seccion !== 'archived').length,
    archived: _proyectos.filter(p => p.seccion === 'archived').length,
  };
  elFilters?.querySelectorAll('.hc-filter-count').forEach(el => {
    const key = el.dataset.count;
    if (counts[key] !== undefined) el.textContent = counts[key];
  });
}

// ─── Filtros + search ──────────────────────────────────────────
// Etiqueta es de cada filtro: el aviso de "sin coincidencias" nombra el filtro
// (no la key cruda «pinned»), y _t() la traduce con el resto.
const FILTRO_ES = { all: 'Todos', pinned: 'Anclados', active: 'Activos', archived: 'Archivados' };

function _aplicaFiltros(p) {
  if (_filtro === 'pinned'   && p.seccion !== 'pinned')   return false;
  if (_filtro === 'archived' && p.seccion !== 'archived') return false;
  if (_filtro === 'active'   && p.seccion === 'archived') return false;
  if (_query) {
    const q = _query.toLowerCase();
    const match = (p.nombre || '').toLowerCase().includes(q)
               || (p.branch || '').toLowerCase().includes(q)
               || (p.ruta   || '').toLowerCase().includes(q);
    if (!match) return false;
  }
  return true;
}

// ─── Render ────────────────────────────────────────────────────
function renderizar() {
  const filtrados = _proyectos.filter(_aplicaFiltros);
  const hayProyectos = _proyectos.length > 0;

  elEmpty?.classList.toggle('visible', !hayProyectos);
  elGrid.style.display = hayProyectos ? '' : 'none';

  if (!hayProyectos) return;

  if (filtrados.length === 0) {
    // La card "+ Nuevo proyecto" va PRIMERO para no perder su línea (la fila de
    // arriba, como cuando hay resultados); el aviso la acompaña al lado.
    // _t(): el grid es [data-i18n-skip], el observer no lo traduce.
    const ref = _query || _t(FILTRO_ES[_filtro] || _filtro);
    elGrid.innerHTML = _cardNewHTML() + `
      <div class="hc-no-match">
        ${icon('search', 28)}
        <div>${esc(_t('Sin coincidencias para'))} <b>"${esc(ref)}"</b></div>
        <button type="button" id="hc-clear-filters">${esc(_t('Limpiar búsqueda y filtros'))}</button>
      </div>`;
    document.getElementById('hc-clear-filters')?.addEventListener('click', () => {
      _query = ''; _filtro = 'all';
      if (elSearch) elSearch.value = '';
      elFilters?.querySelectorAll('.hc-filter').forEach(b =>
        b.classList.toggle('activo', b.dataset.filter === 'all'));
      renderizar();
    });
  } else {
    elGrid.innerHTML = filtrados.map((p, i) => _cardHTML(p, i)).join('') + _cardNewHTML(filtrados.length);
  }

  // Cablear eventos (click + teclado + archivar)
  elGrid.querySelectorAll('.hc-card').forEach(card => {
    card.addEventListener('click', (e) => {
      if (e.target.closest('.hc-card-x')) return; // el × maneja lo suyo
      _irA(`/workspace?id=${card.dataset.id}`);
    });
    card.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        _irA(`/workspace?id=${card.dataset.id}`);
      }
    });
    card.querySelector('.hc-card-x')?.addEventListener('click', async (e) => {
      e.stopPropagation();
      const id = card.dataset.id;
      const archivado = card.classList.contains('archived');
      const nueva = archivado ? 'active' : 'archived';
      try {
        const res = await fetch(`/api/projects/${id}/section`, {
          method:  'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ seccion: nueva }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        toast(archivado ? 'Proyecto restaurado.' : 'Proyecto archivado.', 'success');
        cargarProyectos();
    } catch (err) {
        const frase = archivado
          ? _t('No pude restaurar el proyecto: {msg}').replace('{msg}', err.message)
          : _t('No pude archivar el proyecto: {msg}').replace('{msg}', err.message);
        toast(frase, 'error');
    }
    });
  });
  elGrid.querySelector('.hc-card-new')?.addEventListener('click', abrirModal);
}

function _cardHTML(p, idx = 0) {
  const tone   = _toneFor(p.nombre || '');
  const ini    = _iniciales(p.nombre || '');
  const status = p.status || 'idle';
  const archivado = p.seccion === 'archived';
  const pinned    = p.seccion === 'pinned';
  const agentes = p.terminales_activas || 0;
  const branch  = p.branch || 'main';
  const lastAct = _fechaHumana(p.ultimo_acceso);
  const title = `${p.nombre || ''}${branch ? ' · ' + branch : ''}\n${p.ruta || ''}`;

  return `
    <article class="hc-card ${archivado ? 'archived' : ''}"
             style="--i:${Math.min(idx, 12)}"
             data-id="${esc(p.id)}" tabindex="0" title="${esc(title)}">
      <div class="hc-card-top">
        <div class="hc-card-icon ${tone}">${esc(ini)}</div>
        <div class="hc-card-titles">
          <h3 class="hc-card-name">${esc(p.nombre)}</h3>
          <div class="hc-card-path">${esc(p.ruta)}</div>
        </div>
      </div>
      <div class="hc-card-meta">
        <span class="hc-card-branch">
          <svg width="10" height="10" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
            <path d="M11.75 2.5a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0zm.75 2.452V8.5a.75.75 0 0 1-.75.75H8.06l2.22 2.22a.749.749 0 0 1-.326 1.275.749.749 0 0 1-.734-.215L6.47 9.78a.75.75 0 0 1 0-1.06l2.75-2.75a.749.749 0 0 1 1.275.326.749.749 0 0 1-.215.734L8.06 8.25H11v-3.298a2.25 2.25 0 1 1 1.5 0zM4.25 13.5a.75.75 0 1 1 0-1.5.75.75 0 0 1 0 1.5zm0-8.5a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5zm-.75 6.298V5.202a2.25 2.25 0 1 1 1.5 0v6.096a2.25 2.25 0 1 1-1.5 0z"/>
          </svg>
          <span>${esc(branch)} · ${esc(lastAct)}</span>
        </span>
        <span class="hc-card-agents">
          <span class="hc-card-pip ${status}"></span>${agentes}
        </span>
      </div>
      ${pinned ? '<span class="hc-card-pinned">Pin</span>' : ''}
      <button class="hc-card-x" type="button" data-act="arch"
              title="${archivado ? 'Restaurar proyecto' : 'Archivar proyecto'}"
              aria-label="${archivado ? 'Restaurar proyecto' : 'Archivar proyecto'}">
        ${icon('archive', 12)}
      </button>
    </article>`;
}

function _cardNewHTML(idx = 0) {
  return `
    <button class="hc-card-new" type="button" style="--i:${Math.min(idx, 12)}" title="Crear nueva terminal">
      <div class="hc-card-new-icon">
        <svg width="15" height="15" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true">
          <line x1="7" y1="2.5" x2="7" y2="11.5"/>
          <line x1="2.5" y1="7" x2="11.5" y2="7"/>
        </svg>
      </div>
      <span>${_t('Nuevo proyecto')}</span>
    </button>`;
}

// ─── Filtros (tabs) ────────────────────────────────────────────
elFilters?.addEventListener('click', (e) => {
  const btn = e.target.closest('.hc-filter');
  if (!btn) return;
  elFilters.querySelectorAll('.hc-filter').forEach(b => b.classList.remove('activo'));
  btn.classList.add('activo');
  _filtro = btn.dataset.filter;
  renderizar();
});

// ─── Creación de terminal / proyecto ───────────────────────────
// Único punto de entrada: el LAUNCHER del workspace.
// Con proyectos → aterriza en el primero y abre el launcher.
// Sin proyectos → bootstrap sin id (workspace tolera ?launcher=nuevo sin id y
//                 crea el primer proyecto desde el launcher).
function abrirModal() {
  if (_proyectos.length > 0) {
    _irA(`/workspace?id=${_proyectos[0].id}&launcher=nuevo`);
  } else {
    _irA(`/workspace?launcher=nuevo`);
  }
}
document.getElementById('btn-new-project')?.addEventListener('click', abrirModal);
document.getElementById('btn-new-project-empty')?.addEventListener('click', abrirModal);

// ─── Search ────────────────────────────────────────────────────
// Debounce del re-render: con muchos proyectos cada tecla reconstruía todo el
// grid (innerHTML + re-attach de listeners). _query se actualiza al toque para
// que Enter/Escape usen el valor fresco; el render pesado se difiere ~130ms.
let _searchTimer = 0;
function _renderDiferido() {
  clearTimeout(_searchTimer);
  _searchTimer = setTimeout(renderizar, 130);
}
function _renderYa() { clearTimeout(_searchTimer); renderizar(); }
elSearch?.addEventListener('input', () => { _query = elSearch.value; _renderDiferido(); });
elSearch?.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    _renderYa();  // flush para no navegar a una card stale del debounce
    const primero = elGrid.querySelector('.hc-card');
    if (primero) _irA(`/workspace?id=${primero.dataset.id}`);
  } else if (e.key === 'Escape') {
    elSearch.value = ''; _query = ''; _renderYa(); elSearch.blur();
  }
});

// ─── Shortcuts globales ────────────────────────────────────────
document.addEventListener('keydown', e => {
  const mod = e.metaKey || e.ctrlKey;
  if (mod && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault(); elSearch?.focus(); elSearch?.select(); return;
  }
  if (mod && (e.key === 'n' || e.key === 'N')) {
    e.preventDefault(); abrirModal(); return;
  }
  if (mod && e.key >= '1' && e.key <= '9') {
    const tag = document.activeElement?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;
    const cards = elGrid.querySelectorAll('.hc-card');
    const c = cards[parseInt(e.key, 10) - 1];
    if (c) { e.preventDefault(); _irA(`/workspace?id=${c.dataset.id}`); }
  }
});

// ─── Spotlight de cursor en las cards (un solo listener delegado) ──
// El rect no cambia mientras no haya scroll/resize: lo cacheo al entrar a una
// card (pointerover) en vez de leerlo por mousemove (evita reflow forzado por
// evento). Y coalezco la escritura de --mx/--my en un solo rAF por frame.
let _spotCard = null, _spotRect = null, _spotX = 0, _spotY = 0, _spotRaf = 0;
function _spotFlush() {
  _spotRaf = 0;
  if (!_spotCard || !_spotRect) return;
  _spotCard.style.setProperty('--mx', `${_spotX - _spotRect.left}px`);
  _spotCard.style.setProperty('--my', `${_spotY - _spotRect.top}px`);
}
elGrid?.addEventListener('pointerover', (e) => {
  const card = e.target.closest('.hc-card');
  if (!card || card === _spotCard) return;
  _spotCard = card;
  _spotRect = card.getBoundingClientRect();  // 1 lectura por entrada, no por movimiento
});
elGrid?.addEventListener('mousemove', (e) => {
  if (!_spotCard) return;
  if (!e.target.closest('.hc-card')) { _spotCard = null; _spotRect = null; return; }
  _spotX = e.clientX; _spotY = e.clientY;
  if (!_spotRaf) _spotRaf = requestAnimationFrame(_spotFlush);
});
// El rect cacheado se invalida si la página se mueve bajo el cursor
window.addEventListener('scroll', () => { _spotRect = _spotCard?.getBoundingClientRect() || null; }, { passive: true });
window.addEventListener('resize', () => { _spotRect = _spotCard?.getBoundingClientRect() || null; });

// ─── Strands: cintas de luz detrás del hero (shared/strands.js) ──
// Paleta = el mismo trío de la aurora estática (accent + info + magenta),
// leído del tema activo; sin WebGL2 el mount devuelve null y queda la aurora.
(function () {
  const cont = document.getElementById('atmo-strands');
  if (!cont || !window.JarvisStrands?.montar) return;
  const strands = JarvisStrands.montar(cont, {
    coloresCss: ['var(--ob-accent)', 'var(--ob-info)', 'var(--ob-magenta)'],
    count: 3, speed: 0.3, amplitude: 0.9, waviness: 1.05, thickness: 0.6,
    glow: 2.3, intensity: 0.5, saturation: 1.35, scale: 1.35,
  });
  if (strands) window.addEventListener('theme-changed', () => strands.refrescarColores());
})();

// ─── Init ──────────────────────────────────────────────────────
// El grid lleva [data-i18n-skip] (nombres y rutas de proyecto son datos), así
// que el traductor no ve la card estática "+ Nuevo proyecto": la pinta _t() y
// re-renderizamos al cambiar de idioma.
window.addEventListener('jarvis:lang', () => renderizar());
pintarSaludo();
cargarProyectos();
