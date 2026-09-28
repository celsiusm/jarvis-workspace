// JARVIS — ⚙ → Extensiones · "Estudio de extensiones" (rediseño 2026-09).
//
// Reemplaza al rack heredado (.ps-* cableado desde workspace.js). Concepto
// nuevo: la pantalla responde "¿qué va a leer CADA IA en este proyecto?".
//   · Héroe: el mapa de lectores — una ficha por IA (Claude Code, Codex,
//     Gemini, Cursor, Copilot…) con cuántos archivos suyos hay acá. Es también
//     el filtro.
//   · Tres vistas: Skills e instrucciones (GLOBAL, todas las IAs) · Plugins y
//     Marketplace (conceptos SOLO de Claude Code, marcados como tales).
//   · Un cajón lateral para ver/editar una skill, leer la regla de otra IA o
//     instalar un plugin del marketplace.
// Backend: /api/plugins/* + /api/projects/{id}/skills-md* (Claude) y
// /api/projects/{id}/skills/detectadas (core/skills_ia.py, multi-IA).
//
// Expone window.JarvisExtensiones = { montar(el, opts), desmontar(), pure }.
// La lógica pura (filtros, agrupado, validaciones) se exporta para los tests
// de Node (__tests__/extensions.test.js).
(function (global) {
  'use strict';

  /* ═══════════════════════════════════════════════════════════
     LÓGICA PURA (sin DOM)
     ═══════════════════════════════════════════════════════════ */

  const KINDS = {
    skill:        { label: 'Skill',         icon: 'sparkles',    uno: 'skill',        varios: 'skills' },
    command:      { label: 'Comando',       icon: 'terminal',    uno: 'comando',      varios: 'comandos' },
    agent:        { label: 'Agente',        icon: 'cpu',         uno: 'agente',       varios: 'agentes' },
    rules:        { label: 'Reglas',        icon: 'list-checks', uno: 'regla',        varios: 'reglas' },
    instructions: { label: 'Instrucciones', icon: 'file',        uno: 'instrucciones', varios: 'instrucciones' },
    config:       { label: 'Config',        icon: 'sliders',     uno: 'config',       varios: 'config' },
  };

  // Monograma para las IAs sin logo en shared/icons (cliLogo cae a "$").
  const MONOGRAMA = { gemini: 'G', copilot: 'Co', windsurf: 'W', cline: 'Cl', roo: 'R' };

  // Plugin (Claude Code) → familia de color. Sale del glifo de plugin-icons.js
  // y aterriza en TOKENS del tema (nada de los hex de .tone-*).
  const HUE_POR_TONO = {
    'tone-violet': 'acc', 'tone-blue': 'info', 'tone-cyan': 'info', 'tone-teal': 'run',
    'tone-green': 'run', 'tone-amber': 'work', 'tone-rose': 'err',
  };

  // Comparación sin mayúsculas ni tildes: "configuracion" encuentra "Configuración".
  const _norm = (s) => String(s ?? '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  function coincide(campos, q) {
    const n = _norm(q).trim();
    if (!n) return true;
    const heno = _norm((Array.isArray(campos) ? campos : [campos]).join(' '));
    return n.split(/\s+/).every(t => heno.includes(t));
  }

  function filtrarItems(items, { ia = 'todas', q = '' } = {}) {
    return (items || []).filter(i =>
      (ia === 'todas' || i.ia === ia) &&
      coincide([i.nombre, i.descripcion, i.path, i.ia_label, (KINDS[i.kind] || {}).label, i.detalle], q));
  }

  // [{ herr, items }] en el orden del catálogo (Claude primero). Las IAs sin
  // items no aparecen; `sinArchivos` las lista aparte (el "también buscamos").
  function agrupar(items, catalogo) {
    const por = new Map();
    for (const it of items || []) {
      if (!por.has(it.ia)) por.set(it.ia, []);
      por.get(it.ia).push(it);
    }
    const grupos = [];
    for (const h of catalogo || []) if (por.has(h.id)) grupos.push({ herr: h, items: por.get(h.id) });
    // IAs que el catálogo no conoce (no debería pasar) al final, sin perderlas.
    for (const [id, its] of por) if (!(catalogo || []).some(h => h.id === id)) {
      grupos.push({ herr: { id, label: its[0].ia_label || id, lee: [] }, items: its });
    }
    return grupos;
  }

  function sinArchivos(catalogo, herramientas) {
    const con = new Set((herramientas || []).map(h => h.id));
    return (catalogo || []).filter(h => !con.has(h.id));
  }

  function filtrarMarket(lista, { origen = 'todos', q = '' } = {}) {
    return (lista || []).filter(p =>
      (origen === 'todos' ||
       (origen === 'oficial' && p.source !== 'external') ||
       (origen === 'externo' && p.source === 'external') ||
       (origen === 'instalados' && p.instalado)) &&
      coincide([p.nombre, p.descripcion, p.full_id, p.marketplace], q));
  }

  function contarMarket(lista) {
    const c = { todos: 0, oficial: 0, externo: 0, instalados: 0 };
    for (const p of lista || []) {
      c.todos++;
      if (p.source === 'external') c.externo++; else c.oficial++;
      if (p.instalado) c.instalados++;
    }
    return c;
  }

  // "1 regla" / "3 reglas": la cuenta del encabezado de cada IA.
  const etiquetaCuenta = (kind, n) => {
    const k = KINDS[kind];
    return k ? (n === 1 ? k.uno : k.varios) : String(kind);
  };

  const cmdInstalar = (p) => `/plugin install ${p.full_id}`;
  const nombreSkillValido = (n) => /^[a-zA-Z0-9_-]+$/.test(String(n || '').trim());
  const plantillaSkill = (nombre = '') =>
    `---\nname: ${nombre}\ndescription: \n---\n\n# ${nombre}\n\n`;

  // Al tipear el nombre de una skill NUEVA, el `name:` del frontmatter lo sigue
  // mientras el usuario no lo haya tocado a mano.
  function sincronizarNombre(contenido, anterior, nuevo) {
    const re = new RegExp(`^name: ${anterior.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`, 'm');
    if (!re.test(contenido)) return contenido;
    let out = contenido.replace(re, `name: ${nuevo}`);
    const h = new RegExp(`^# ${anterior.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`, 'm');
    if (h.test(out)) out = out.replace(h, `# ${nuevo}`);
    return out;
  }

  // Nombre que espera /skills-md/{nombre}: el de la carpeta o el del .md flat.
  function nombreSkillDeItem(it) {
    if (!it || it.ia !== 'claude' || it.kind !== 'skill' || it.alcance !== 'proyecto') return null;
    return it.nombre;
  }

  function hueDePlugin(p) {
    const tono = global.JarvisPluginIcons?.tonoDePlugin?.(p.full_id, p.nombre) || 'tone-violet';
    return HUE_POR_TONO[tono] || 'acc';
  }

  const pure = {
    KINDS, MONOGRAMA, HUE_POR_TONO, coincide, filtrarItems, agrupar, sinArchivos,
    filtrarMarket, contarMarket, etiquetaCuenta, cmdInstalar, nombreSkillValido, plantillaSkill,
    sincronizarNombre, nombreSkillDeItem, hueDePlugin,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = pure;
  if (typeof document === 'undefined') return;

  /* ═══════════════════════════════════════════════════════════
     DOM
     ═══════════════════════════════════════════════════════════ */

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const _t = (s) => global.JarvisI18n?.t?.(s) ?? s;
  const ic = (n, s = 14) => (typeof global.icon === 'function' ? global.icon(n, s) : '');
  const PAG_MARKET = 60;
  // `detalle` llega del backend en español (core/skills_ia.py): 'siempre',
  // 'modo {m}', 'aplica a {carpeta}/' o un glob. 'modo X' no entra por plantilla
  // del DOM (poco texto fijo + valor sin dígitos): se traduce acá.
  const _det = (d) => {
    const s = String(d ?? '');
    const m = /^modo (.+)$/.exec(s);
    return m ? _t('modo {m}').replace('{m}', m[1]) : _t(s);
  };

  const S = {
    root: null, pid: null, hooks: {},
    vista: 'skills', ia: 'todas', q: '', origen: 'todos', pag: PAG_MARKET,
    plugins: null, activos: new Set(), det: null, market: null,
    err: { plugins: '', det: '', market: '' },
    drawer: null, gen: 0,
  };

  /* ── Marcas de cada IA ── */
  function marca(h, size = 18) {
    const id = h?.id || h;
    if (h?.logo && typeof global.cliLogo === 'function') {
      return `<span class="ex-mark" data-ia="${esc(id)}" style="--s:${size}px">${global.cliLogo(h.logo, size)}</span>`;
    }
    const mono = MONOGRAMA[id] || String(h?.label || id).slice(0, 1);
    return `<span class="ex-mark ex-mono" data-ia="${esc(id)}" style="--s:${size}px" aria-hidden="true">${esc(size < 16 ? mono.slice(0, 1) : mono)}</span>`;
  }
  const catalogo = () => S.det?.catalogo || [];
  const herrDe = (id) => catalogo().find(h => h.id === id) || { id, label: id, lee: [] };

  /* ── Montaje ── */
  function montar(el, opts = {}) {
    desmontar();
    S.root = el; S.pid = opts.projectId ?? null; S.hooks = opts;
    S.vista = opts.vista || 'skills'; S.ia = 'todas'; S.q = ''; S.origen = 'todos'; S.pag = PAG_MARKET;
    S.plugins = null; S.activos = new Set(); S.det = null; S.market = null;
    S.err = { plugins: '', det: '', market: '' };
    const gen = ++S.gen;

    el.innerHTML = `
      <div class="ex" data-vista="${S.vista}">
       <div class="ex-main">
        <section class="ex-hero" aria-label="${esc(_t('Mapa de lectores'))}"></section>
        <div class="ex-bar">
          <nav class="ex-views" role="tablist" aria-label="${esc(_t('Vistas'))}"></nav>
          <label class="ex-find">
            ${ic('search', 13)}
            <input type="search" class="ex-q" placeholder="${esc(_t('Buscar skill, regla o plugin…'))}"
                   autocomplete="off" spellcheck="false" aria-label="${esc(_t('Buscar skill, regla o plugin'))}">
          </label>
        </div>
        <div class="ex-view" role="tabpanel"></div>
       </div>
        <div class="ex-scrim" hidden></div>
        <aside class="ex-drawer" role="dialog" aria-modal="true" aria-hidden="true" tabindex="-1"></aside>
      </div>`;
    const ex = el.querySelector('.ex');
    ex.addEventListener('click', _onClick);
    ex.addEventListener('change', _onChange);
    ex.addEventListener('input', _onInput);
    ex.addEventListener('keydown', _onKeyLocal);
    document.addEventListener('keydown', _onKeyDoc, true);
    // La barra de vistas es sticky: su repisa maciza se prende SOLO pegada
    // arriba (en reposo sería una franja más clara que la losa).
    S.scroller = el.closest('.sx-scroll');
    S.scroller?.addEventListener('scroll', _marcarBarra, { passive: true });

    _pintar();
    if (!S.pid) return;
    _cargarPlugins(gen);
    _cargarDetectadas(gen);
    // El marketplace es disco puro (manifests); se pide al rato para tener el
    // conteo en la pestaña sin frenar el primer pintado.
    setTimeout(() => { if (gen === S.gen && S.market == null) _cargarMarket(gen); }, 450);
  }

  let _barraRaf = 0;
  function _marcarBarra() {
    if (_barraRaf) return;
    _barraRaf = requestAnimationFrame(() => {
      _barraRaf = 0;
      const bar = $('.ex-bar');
      if (!bar || !S.scroller) return;
      const techo = S.scroller.getBoundingClientRect().top;
      bar.classList.toggle('fija', bar.getBoundingClientRect().top <= techo + 1);
    });
  }

  function desmontar() {
    document.removeEventListener('keydown', _onKeyDoc, true);
    S.scroller?.removeEventListener('scroll', _marcarBarra);
    S.scroller = null;
    S.gen++;
    S.drawer = null;
    S.root = null;
  }

  const $ = (sel) => S.root?.querySelector(sel);

  /* ── Carga ── */
  async function _json(url, init) {
    const r = await fetch(url, init);
    if (!r.ok) {
      let det = `HTTP ${r.status}`;
      try { det = (await r.json()).detail || det; } catch {}
      throw new Error(det);
    }
    return r.status === 204 ? null : r.json();
  }

  async function _cargarPlugins(gen) {
    try {
      const [inst, act] = await Promise.all([
        _json('/api/plugins/instalados'),
        _json(`/api/projects/${S.pid}/plugins/activos`),
      ]);
      if (gen !== S.gen) return;
      S.plugins = inst || [];
      S.activos = new Set(act?.activos || []);
      S.err.plugins = '';
    } catch (e) {
      if (gen !== S.gen) return;
      S.plugins = S.plugins || []; S.err.plugins = e.message;
    }
    _avisarActivos();
    _pintar();
  }

  async function _cargarDetectadas(gen = S.gen) {
    try {
      const d = await _json(`/api/projects/${S.pid}/skills/detectadas`);
      if (gen !== S.gen) return;
      S.det = d; S.err.det = '';
    } catch (e) {
      if (gen !== S.gen) return;
      S.det = S.det || { items: [], herramientas: [], catalogo: [] }; S.err.det = e.message;
    }
    _pintar();
  }

  async function _cargarMarket(gen = S.gen) {
    try {
      const m = await _json('/api/plugins/marketplace');
      if (gen !== S.gen) return;
      S.market = m || []; S.err.market = '';
    } catch (e) {
      if (gen !== S.gen) return;
      S.market = []; S.err.market = e.message;
    }
    _pintarNav();
    if (S.vista === 'market') _pintarVista();
  }

  function _avisarActivos() {
    try { S.hooks.onActivos?.(S.activos.size); } catch {}
  }

  /* ═══ PINTADO ═══════════════════════════════════════════════ */
  function _pintar() {
    if (!S.root) return;
    _pintarHero();
    _pintarNav();
    _pintarVista();
  }

  /* ── Héroe: mapa de lectores ── */
  function _pintarHero() {
    const hero = $('.ex-hero');
    if (!hero) return;
    if (!S.pid) {
      hero.innerHTML = `<div class="ex-void"><b>${esc(_t('Sin proyecto abierto'))}</b>
        <p>${esc(_t('Abrí un proyecto para ver sus skills, reglas e instrucciones.'))}</p></div>`;
      return;
    }
    const det = S.det;
    const items = det?.items || [];
    const nSkillsClaude = items.filter(i => i.ia === 'claude' && i.alcance === 'proyecto').length;
    const otras = items.filter(i => i.ia !== 'claude').length;
    const iasCon = (det?.herramientas || []).length;
    const nPlug = S.plugins ? S.plugins.length : null;
    const stat = (v, l, sub) => `
      <div class="ex-stat">
        <b>${v == null ? '<i class="ex-dots"></i>' : esc(v)}</b>
        <span>${esc(_t(l))}${sub ? ` <em>${esc(sub)}</em>` : ''}</span>
      </div>`;

    const tiles = (det?.herramientas || []).map(h => `
      <button type="button" class="ex-tile${S.ia === h.id ? ' on' : ''}" data-act="ia" data-ia="${esc(h.id)}"
              aria-pressed="${S.ia === h.id}" title="${esc(_t('Lee') + ': ' + (h.lee || []).join(' · '))}">
        ${marca(h, 18)}
        <span class="ex-tile-t">${esc(h.label)}</span>
        <span class="ex-tile-n">${h.total}</span>
      </button>`).join('');
    const faltan = det ? sinArchivos(det.catalogo, det.herramientas) : [];

    hero.innerHTML = `
      <div class="ex-hero-top">
        <div class="ex-hero-copy">
          <span class="ex-eyebrow">${ic('plug', 12)} ${esc(_t('Estudio de extensiones'))}</span>
          <h2>${esc(_t('Lo que cada IA lee en este proyecto'))}</h2>
          <p>${esc(_t('Skills, comandos, agentes y reglas de Claude Code, Codex, Gemini, Cursor, Copilot y más — detectados en el repo y en tu usuario.'))}</p>
        </div>
        <div class="ex-stats">
          ${stat(det ? nSkillsClaude : null, 'de Claude', '')}
          ${stat(det ? otras : null, 'de otras IAs', '')}
          ${stat(nPlug == null ? null : `${S.activos.size}/${nPlug}`, 'plugins activos', '')}
        </div>
      </div>
      <div class="ex-readers" role="group" aria-label="${esc(_t('Filtrar por IA'))}">
        <button type="button" class="ex-tile ex-tile-all${S.ia === 'todas' ? ' on' : ''}" data-act="ia" data-ia="todas" aria-pressed="${S.ia === 'todas'}">
          <span class="ex-mark ex-all" style="--s:18px">${ic('sparkles', 12)}</span>
          <span class="ex-tile-t">${esc(_t('Todas'))}</span>
          <span class="ex-tile-n">${det ? items.length : '·'}</span>
        </button>
        ${det ? tiles : '<span class="ex-tile ex-ghost"></span><span class="ex-tile ex-ghost"></span><span class="ex-tile ex-ghost"></span>'}
        ${faltan.length ? `
          <span class="ex-readers-sep" aria-hidden="true"></span>
          <span class="ex-absent" title="${esc(_t('También buscamos archivos de estas IAs'))}">
            <span class="ex-absent-l">${esc(_t('sin archivos'))}</span>
            ${faltan.map(h => `<span class="ex-absent-i" title="${esc(h.label + ' — ' + _t('lee') + ': ' + (h.lee || []).join(' · '))}">${marca(h, 16)}</span>`).join('')}
          </span>` : ''}
      </div>
      ${iasCon ? '' : ''}`;
  }

  /* ── Barra de vistas ── */
  function _pintarNav() {
    const nav = $('.ex-views');
    if (!nav) return;
    const nItems = S.det ? filtrarItems(S.det.items, { ia: S.ia }).length : null;
    const nPlug = S.plugins ? S.plugins.length : null;
    const nMk = S.market ? S.market.length : null;
    const claude = typeof global.cliLogo === 'function' ? global.cliLogo('claude', 12) : '';
    const tab = (id, label, n, soloClaude, corto) => `
      <button type="button" role="tab" class="ex-view-b${S.vista === id ? ' on' : ''}" data-act="vista" data-v="${id}"
              aria-selected="${S.vista === id}" title="${esc(_t(label))}">
        <span class="ex-view-l">${esc(_t(label))}</span>${corto ? `<span class="ex-view-s">${esc(_t(corto))}</span>` : ''}
        ${soloClaude ? `<span class="ex-only" title="${esc(_t('Solo Claude Code'))}">${claude}</span>` : ''}
        <span class="ex-view-n">${n == null ? '·' : n}</span>
      </button>`;
    nav.innerHTML =
      tab('skills', S.ia === 'todas' ? 'Skills e instrucciones' : herrDe(S.ia).label, nItems, false, S.ia === 'todas' ? 'Skills' : '') +
      tab('plugins', 'Plugins', nPlug, true) +
      tab('market', 'Marketplace', nMk, true);
    S.root.querySelector('.ex').dataset.vista = S.vista;
  }

  function _pintarVista() {
    const v = $('.ex-view');
    if (!v) return;
    if (!S.pid) { v.innerHTML = ''; return; }
    ({ skills: _vistaSkills, plugins: _vistaPlugins, market: _vistaMarket }[S.vista] || _vistaSkills)(v);
  }

  const _cargando = (txt) => `<div class="ex-loading">${ic('loader', 14)} ${esc(_t(txt))}</div>`;
  const _error = (msg, reintentar) => `
    <div class="ex-void ex-void-err">
      <b>${esc(_t('No se pudo cargar'))}</b><p>${esc(msg)}</p>
      <button type="button" class="sx-btn sm" data-act="${reintentar}">${ic('refresh', 13)} ${esc(_t('Reintentar'))}</button>
    </div>`;

  /* ── Vista 1: skills e instrucciones (todas las IAs) ── */
  function _vistaSkills(v) {
    if (!S.det) { v.innerHTML = _cargando('Buscando skills de cada IA…'); return; }
    if (S.err.det && !S.det.items.length) { v.innerHTML = _error(S.err.det, 'recargar-det'); return; }
    const visibles = filtrarItems(S.det.items, { ia: S.ia, q: S.q });
    const grupos = agrupar(visibles, S.det.catalogo);
    const verClaude = S.ia === 'todas' || S.ia === 'claude';
    // Claude siempre tiene su grupo (con "Nueva skill") aunque esté vacío.
    if (verClaude && !grupos.some(g => g.herr.id === 'claude') && !S.q) {
      grupos.unshift({ herr: herrDe('claude'), items: [] });
    }
    if (!grupos.length) {
      v.innerHTML = `<div class="ex-void"><b>${esc(_t('Sin resultados'))}</b>
        <p>${S.q ? `${esc(_t('Nada coincide con'))} «${esc(S.q)}».` : esc(_t('Esta IA no tiene archivos en el proyecto.'))}</p></div>`;
      return;
    }
    v.innerHTML = grupos.map((g, gi) => _grupoHTML(g, gi)).join('');
  }

  function _grupoHTML({ herr, items }, gi) {
    const esClaude = herr.id === 'claude';
    const proy = items.filter(i => i.alcance === 'proyecto');
    const usr = items.filter(i => i.alcance === 'usuario');
    const kinds = {};
    items.forEach(i => { kinds[i.kind] = (kinds[i.kind] || 0) + 1; });
    const resumen = Object.entries(kinds).map(([k, n]) => `${n} ${esc(_t(etiquetaCuenta(k, n)))}`).join(' · ');
    const cards = (lista) => lista.map(_cardHTML).join('');
    const vacioClaude = esClaude && !proy.length ? `
      <button type="button" class="ex-card ex-card-new" data-act="nueva-skill">
        <span class="ex-new-plus">${ic('plus', 18)}</span>
        <b>${esc(_t('Creá la primera skill'))}</b>
        <p>${esc(_t('Un .md en .claude/skills/ que Claude Code carga cuando la tarea lo pide.'))}</p>
      </button>` : '';
    return `
      <section class="ex-group" data-ia="${esc(herr.id)}" style="--gi:${gi}">
        <header class="ex-group-h">
          ${marca(herr, 22)}
          <div class="ex-group-t">
            <h3>${esc(herr.label)}${esClaude ? '' : ` <span class="ex-global">${esc(_t('otra IA'))}</span>`}</h3>
            <span>${resumen || esc(_t('lee') + ' ' + (herr.lee || []).slice(0, 3).join(' · '))}</span>
          </div>
          ${esClaude ? `<button type="button" class="sx-btn sm pri ex-new" data-act="nueva-skill">${ic('plus', 13)} ${esc(_t('Nueva skill'))}</button>` : ''}
        </header>
        <div class="ex-grid">${cards(proy)}${vacioClaude}</div>
        ${usr.length ? `
          <div class="ex-sub">${ic('key', 12)} ${esc(_t('De tu usuario'))} <span>~/ · ${esc(_t('solo lectura'))}</span></div>
          <div class="ex-grid">${cards(usr)}</div>` : ''}
      </section>`;
  }

  function _cardHTML(it) {
    const k = KINDS[it.kind] || { label: it.kind, icon: 'file' };
    const tambien = (it.tambien || []).map(id => herrDe(id)).filter(h => h.label);
    return `
      <button type="button" class="ex-card" data-act="abrir" data-path="${esc(it.path)}" data-alc="${esc(it.alcance)}" data-ia="${esc(it.ia)}">
        <span class="ex-card-top">
          <span class="ex-kind k-${esc(it.kind)}">${ic(k.icon, 11)} ${esc(_t(k.label))}</span>
          ${it.formato ? `<span class="ex-fmt" title="${esc(it.formato === 'flat' ? _t('Archivo .md suelto') : _t('Carpeta con SKILL.md'))}">${esc(it.formato === 'flat' ? '.md' : 'SKILL.md')}</span>` : ''}
          ${it.editable ? `<span class="ex-edit-ic" title="${esc(_t('Editable'))}">${ic('edit', 12)}</span>` : ''}
        </span>
        <b class="ex-card-n">${esc(it.nombre)}</b>
        <span class="ex-card-d">${esc(it.descripcion || _t('(sin descripción)'))}</span>
        <span class="ex-card-f">
          <code title="${esc(it.path)}">${esc('\u200E' + it.path + '\u200E')}</code>
          ${it.detalle ? `<span class="ex-det">${esc(_det(it.detalle))}</span>` : ''}
          ${tambien.length ? `<span class="ex-also" title="${esc(_t('También lo leen') + ': ' + tambien.map(h => h.label).join(', '))}">${tambien.map(h => marca(h, 13)).join('')}</span>` : ''}
        </span>
      </button>`;
  }

  /* ── Vista 2: plugins instalados (Claude Code) ── */
  function _vistaPlugins(v) {
    if (S.plugins == null) { v.innerHTML = _cargando('Cargando plugins…'); return; }
    if (S.err.plugins && !S.plugins.length) { v.innerHTML = _error(S.err.plugins, 'recargar-plug'); return; }
    const lista = S.plugins.filter(p => coincide([p.nombre, p.descripcion, p.full_id], S.q));
    const intro = `
      <div class="ex-note">
        ${typeof global.cliLogo === 'function' ? global.cliLogo('claude', 16) : ''}
        <p><b>${esc(_t('Plugins de Claude Code.'))}</b> ${esc(_t('Se instalan una vez en tu usuario y se activan por proyecto: los activos se anotan en el CLAUDE.md del repo.'))}</p>
        <span class="ex-note-n"><b>${S.activos.size}</b> / ${S.plugins.length} ${esc(_t('activos'))}</span>
      </div>`;
    if (!S.plugins.length) {
      v.innerHTML = intro + `<div class="ex-void"><b>${esc(_t('Todavía no instalaste plugins'))}</b>
        <p>${esc(_t('Explorá el marketplace, copiá el comando de instalación y pegalo en una terminal de Claude Code.'))}</p>
        <button type="button" class="sx-btn sm pri" data-act="vista" data-v="market">${ic('search', 13)} ${esc(_t('Explorar marketplace'))}</button></div>`;
      return;
    }
    if (!lista.length) {
      v.innerHTML = intro + `<div class="ex-void"><b>${esc(_t('Sin resultados'))}</b><p>${esc(_t('Nada coincide con'))} «${esc(S.q)}».</p></div>`;
      return;
    }
    v.innerHTML = intro + `<div class="ex-plugs">${lista.map(_plugHTML).join('')}</div>`;
  }

  function _glifo(p) {
    const g = global.JarvisPluginIcons?.iconoDePlugin?.(p.full_id, p.nombre) || 'plug';
    return `<span class="ex-glyph" data-hue="${hueDePlugin(p)}">${ic(g, 17)}</span>`;
  }

  function _plugHTML(p) {
    const on = S.activos.has(p.full_id);
    return `
      <div class="ex-plug${on ? ' on' : ''}" data-full="${esc(p.full_id)}" title="${esc(p.full_id)}">
        ${_glifo(p)}
        <div class="ex-plug-b">
          <div class="ex-plug-n"><b>${esc(p.nombre)}</b>
            ${p.version && p.version !== 'unknown' ? `<span class="ex-ver">v${esc(p.version)}</span>` : ''}
</div>
          <p lang="en">${esc(p.descripcion || p.full_id)}</p>
        </div>
        <span class="ex-plug-st">${on ? esc(_t('Activo aquí')) : esc(_t('Inactivo'))}</span>
        <label class="sx-sw" title="${esc(on ? _t('Desactivar para este proyecto') : _t('Activar para este proyecto'))}">
          <input type="checkbox" data-full-id="${esc(p.full_id)}" ${on ? 'checked' : ''}
                 aria-label="${esc(_t('Activo en este proyecto') + ': ' + p.nombre)}"><i></i>
        </label>
      </div>`;
  }

  /* ── Vista 3: marketplace (Claude Code) ── */
  function _vistaMarket(v) {
    if (S.market == null) { v.innerHTML = _cargando('Cargando marketplace…'); _cargarMarket(); return; }
    if (S.err.market && !S.market.length) { v.innerHTML = _error(S.err.market, 'recargar-market'); return; }
    const c = contarMarket(S.market);
    const lista = filtrarMarket(S.market, { origen: S.origen, q: S.q });
    const chip = (id, label) => `
      <button type="button" class="ex-chip${S.origen === id ? ' on' : ''}" data-act="origen" data-o="${id}" aria-pressed="${S.origen === id}">
        ${esc(_t(label))} <span>${c[id]}</span></button>`;
    const head = `
      <div class="ex-mk-bar">
        <div class="ex-chips">${chip('todos', 'Todos')}${chip('oficial', 'Oficiales')}${chip('externo', 'Externos')}${chip('instalados', 'Instalados')}</div>
        <span class="ex-mk-hint">${typeof global.cliLogo === 'function' ? global.cliLogo('claude', 12) : ''} ${esc(_t('Se instalan desde Claude Code con /plugin install'))}</span>
      </div>`;
    if (!S.market.length) {
      v.innerHTML = `<div class="ex-void"><b>${esc(_t('No hay marketplaces descargados'))}</b>
        <p>${esc(_t('Claude Code guarda los catálogos en ~/.claude/plugins/marketplaces. Corré /plugin en una terminal de Claude para sumar uno.'))}</p></div>`;
      return;
    }
    const pagina = lista.slice(0, S.pag);
    v.innerHTML = head + (lista.length
      ? `<div class="ex-mk-grid">${pagina.map(_mkHTML).join('')}</div>
         ${lista.length > pagina.length ? `<button type="button" class="ex-more" data-act="market-mas">${esc(_t('Ver más'))} <span>${lista.length - pagina.length}</span></button>` : ''}`
      : `<div class="ex-void"><b>${esc(_t('Sin resultados'))}</b><p>${S.q ? `${esc(_t('Nada coincide con'))} «${esc(S.q)}».` : esc(_t('No hay plugins en este filtro.'))}</p></div>`);
  }

  function _mkHTML(p) {
    const on = S.activos.has(p.full_id);
    const estado = p.instalado ? (on ? 'activo' : 'instalado') : 'libre';
    const accion = {
      libre:     `<button type="button" class="sx-btn sm" data-act="instalar" data-full="${esc(p.full_id)}">${ic('download', 13)} ${esc(_t('Instalar'))}</button>`,
      instalado: `<button type="button" class="sx-btn sm pri" data-act="activar" data-full="${esc(p.full_id)}">${ic('zap', 13)} ${esc(_t('Activar aquí'))}</button>`,
      activo:    `<span class="ex-on">${ic('check', 13)} ${esc(_t('Activo'))}</span>`,
    }[estado];
    return `
      <article class="ex-mk ${estado}" data-full="${esc(p.full_id)}">
        <header>${_glifo(p)}<b>${esc(p.nombre)}</b>
          ${p.instalado ? `<span class="ex-badge">${esc(_t('Instalado'))}</span>` : ''}</header>
        <p lang="en">${esc(p.descripcion || _t('(sin descripción)'))}</p>
        <footer>
          <span class="ex-src">${p.source === 'external' ? `${ic('external-link', 11)} ${esc(_t('externo'))}` : `${ic('check', 11)} ${esc(_t('oficial'))}`}</span>
          <span class="ex-mk-n" title="${esc(p.marketplace)}">${esc(p.marketplace)}</span>
          ${accion}
        </footer>
      </article>`;
  }

  /* ═══ EVENTOS ═══════════════════════════════════════════════ */
  function _onClick(e) {
    const b = e.target.closest('[data-act]');
    if (!b || !S.root?.contains(b)) {
      if (e.target.classList?.contains('ex-scrim')) _cerrarDrawer();
      return;
    }
    const act = b.dataset.act;
    switch (act) {
      case 'ia':
        S.ia = b.dataset.ia;
        if (S.vista !== 'skills') S.vista = 'skills';
        _pintar();
        break;
      case 'vista':
        S.vista = b.dataset.v; S.pag = PAG_MARKET;
        _pintarNav(); _pintarVista();
        break;
      case 'origen': S.origen = b.dataset.o; S.pag = PAG_MARKET; _pintarVista(); break;
      case 'market-mas': S.pag += PAG_MARKET; _pintarVista(); break;
      case 'nueva-skill': _drawerSkill(null); break;
      case 'abrir': _abrirItem(b.dataset.path, b.dataset.alc); break;
      case 'instalar': _drawerInstalar(S.market?.find(p => p.full_id === b.dataset.full)); break;
      case 'activar': _toggle(b.dataset.full, true); break;
      case 'recargar-det': S.det = null; _pintarVista(); _cargarDetectadas(); break;
      case 'recargar-plug': S.plugins = null; _pintarVista(); _cargarPlugins(S.gen); break;
      case 'recargar-market': S.market = null; _pintarVista(); break;
      case 'cerrar': _cerrarDrawer(); break;
      case 'guardar': _guardarSkill(); break;
      case 'eliminar': _eliminarSkill(); break;
      case 'copiar': _copiar(b); break;
      case 'editor': _abrirEnEditor(b.dataset.path); break;
    }
  }

  function _onChange(e) {
    const cb = e.target.closest('input[data-full-id]');
    if (cb) _toggle(cb.dataset.fullId, cb.checked, cb);
  }

  let _qTimer = 0;
  function _onInput(e) {
    if (e.target.classList.contains('ex-q')) {
      clearTimeout(_qTimer);
      _qTimer = setTimeout(() => { S.q = e.target.value; S.pag = PAG_MARKET; _pintarNav(); _pintarVista(); }, 90);
      return;
    }
    const d = S.drawer;
    if (d?.modo === 'nueva' && e.target.classList.contains('ex-d-name')) {
      const ta = $('.ex-d-ta');
      const nuevo = e.target.value.trim();
      if (ta) ta.value = sincronizarNombre(ta.value, d.nombreSync || '', nuevo);
      d.nombreSync = nuevo;
      e.target.classList.toggle('bad', !!nuevo && !nombreSkillValido(nuevo));
    }
    if (e.target.classList.contains('ex-d-ta')) { d && (d.sucio = true); _contarTA(); }
  }

  function _onKeyLocal(e) {
    if (e.target.classList?.contains('ex-q') && e.key === 'Escape' && e.target.value) {
      e.preventDefault(); e.stopPropagation();
      e.target.value = ''; S.q = ''; _pintarNav(); _pintarVista();
    }
    if (S.drawer && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault(); _guardarSkill();
    }
  }

  // Esc cierra el cajón antes que la configuración (settings.js cede el Esc
  // mientras .ex-drawer.open exista). Con un confirm encima, lo cede también.
  function _onKeyDoc(e) {
    if (e.key !== 'Escape' || !S.drawer) return;
    if (document.querySelector('.ob-confirm-overlay')) return;
    e.preventDefault(); e.stopPropagation();
    _cerrarDrawer();
  }

  /* ── Plugins: toggle reconciliado con el server ── */
  async function _toggle(fullId, quiere, cb) {
    try {
      const d = await _json(`/api/projects/${S.pid}/plugins/toggle`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plugin_id: fullId, activo: quiere }),
      });
      const real = !!d.activo;                 // verdad del server, nunca asumir
      if (real) S.activos.add(fullId); else S.activos.delete(fullId);
      if (cb && cb.checked !== real) cb.checked = real;
      global.toast?.(real ? _t('Plugin activado para este proyecto') : _t('Plugin desactivado'), real ? 'success' : 'info', 2200);
    } catch (err) {
      if (cb) cb.checked = !quiere;
      global.toast?.(`${_t('Error')}: ${err.message}`, 'error');
    }
    _avisarActivos();
    _pintarHero(); _pintarNav(); _pintarVista();
  }

  /* ═══ CAJÓN ═════════════════════════════════════════════════ */
  function _abrirDrawer(html, estado) {
    const dr = $('.ex-drawer'), scrim = $('.ex-scrim');
    if (!dr) return;
    S.drawer = estado;
    dr.innerHTML = html;
    scrim.hidden = false;
    dr.classList.add('open');
    dr.setAttribute('aria-hidden', 'false');
    requestAnimationFrame(() => scrim.classList.add('on'));
    setTimeout(() => (dr.querySelector('[autofocus]') || dr).focus?.(), 30);
  }

  async function _cerrarDrawer(forzar) {
    const d = S.drawer;
    if (!d) return;
    if (!forzar && d.sucio && typeof global.confirmar === 'function') {
      const ok = await global.confirmar(_t('¿Descartar los cambios sin guardar?'), { peligro: true, confirmText: _t('Descartar') });
      if (!ok) return;
    }
    S.drawer = null;
    const dr = $('.ex-drawer'), scrim = $('.ex-scrim');
    if (!dr) return;
    dr.classList.remove('open');
    dr.setAttribute('aria-hidden', 'true');
    scrim.classList.remove('on');
    setTimeout(() => { if (!S.drawer && scrim) { scrim.hidden = true; dr.innerHTML = ''; } }, 220);
  }

  const _drawerHead = (h, kind, titulo, sub) => `
    <header class="ex-d-h">
      ${marca(h, 26)}
      <div class="ex-d-t">
        <span class="ex-d-eyebrow">${esc(h.label)}${kind ? ` · ${esc(_t((KINDS[kind] || { label: kind }).label))}` : ''}</span>
        <h3>${esc(titulo)}</h3>
        ${sub ? `<code>${esc(sub)}</code>` : ''}
      </div>
      <button type="button" class="ex-d-x" data-act="cerrar" aria-label="${esc(_t('Cerrar'))}">${ic('x', 15)}</button>
    </header>`;

  function _contarTA() {
    const ta = $('.ex-d-ta'), out = $('.ex-d-count');
    if (!ta || !out) return;
    const l = ta.value.split('\n').length;
    out.textContent = `${l} ${_t(l === 1 ? 'línea' : 'líneas')} · ${ta.value.length} ${_t('caracteres')}`;
  }

  function _abrirItem(path, alcance) {
    const it = (S.det?.items || []).find(i => i.path === path && i.alcance === alcance);
    if (!it) return;
    const nombre = nombreSkillDeItem(it);
    if (nombre) _drawerSkill(it);
    else _drawerLectura(it);
  }

  // Skill de Claude del proyecto: editor (nueva o existente).
  async function _drawerSkill(it) {
    const nueva = !it;
    const h = herrDe('claude');
    const plano = !it || it.formato === 'flat';
    _abrirDrawer(`
      ${_drawerHead(h, 'skill', nueva ? _t('Nueva skill') : it.nombre, nueva ? '.claude/skills/<nombre>.md' : it.path)}
      <div class="ex-d-body">
        ${nueva ? `
          <label class="ex-d-field">
            <span>${esc(_t('Nombre'))}</span>
            <input class="ex-d-name" type="text" autofocus spellcheck="false" autocomplete="off"
                   placeholder="${esc(_t('ej. deploy-a-prod'))}" aria-label="${esc(_t('Nombre de la skill'))}">
            <em>${esc(_t('Letras, números, guiones y _. Se guarda como .claude/skills/<nombre>.md'))}</em>
          </label>` : `
          <div class="ex-d-meta">
            <span class="ex-kind k-skill">${ic('sparkles', 11)} ${esc(_t('Skill'))}</span>
            <span class="ex-fmt">${esc(plano ? '.md' : 'SKILL.md')}</span>
            <span class="ex-d-note">${esc(_t('Claude Code la carga cuando la tarea coincide con su description.'))}</span>
          </div>`}
        <textarea class="ex-d-ta" spellcheck="false" ${nueva ? '' : 'autofocus'} aria-label="${esc(_t('Contenido de la skill'))}"
                  placeholder="${esc(_t('Cargando…'))}" ${nueva ? '' : 'disabled'}>${nueva ? esc(plantillaSkill('')) : ''}</textarea>
      </div>
      <footer class="ex-d-f">
        ${!nueva && plano ? `<button type="button" class="sx-btn sm ex-danger" data-act="eliminar">${ic('trash', 13)} ${esc(_t('Eliminar'))}</button>` : ''}
        <span class="ex-d-count"></span>
        <button type="button" class="sx-btn sm gho" data-act="cerrar">${esc(_t('Cancelar'))}</button>
        <button type="button" class="sx-btn sm pri" data-act="guardar">${ic('check', 13)} ${esc(_t('Guardar'))} <kbd>⌘S</kbd></button>
      </footer>`, { modo: nueva ? 'nueva' : 'editar', nombre: nueva ? null : it.nombre, nombreSync: '', sucio: false });
    _contarTA();
    if (nueva) return;
    try {
      const d = await _json(`/api/projects/${S.pid}/skills-md/${encodeURIComponent(it.nombre)}`);
      const ta = $('.ex-d-ta');
      if (!ta || S.drawer?.nombre !== it.nombre) return;
      ta.value = d.content || '';
      ta.disabled = false; ta.placeholder = '';
      ta.focus(); ta.setSelectionRange(0, 0); ta.scrollTop = 0;
      _contarTA();
    } catch (err) {
      global.toast?.(`${_t('Error cargando skill')}: ${err.message}`, 'error');
      _cerrarDrawer(true);
    }
  }

  async function _guardarSkill() {
    const d = S.drawer;
    if (!d || (d.modo !== 'nueva' && d.modo !== 'editar')) return;
    const nombre = (d.nombre || $('.ex-d-name')?.value || '').trim();
    const content = $('.ex-d-ta')?.value ?? '';
    if (!nombre) { global.toast?.(_t('El nombre es obligatorio.'), 'warning'); $('.ex-d-name')?.focus(); return; }
    if (!nombreSkillValido(nombre)) { global.toast?.(_t('Solo letras, números, guiones y underscores.'), 'warning'); return; }
    if (d.modo === 'nueva' && (S.det?.items || []).some(i => i.ia === 'claude' && i.kind === 'skill' && i.alcance === 'proyecto' && i.nombre === nombre)) {
      global.toast?.(_t('Ya existe una skill con ese nombre.'), 'warning'); return;
    }
    try {
      await _json(`/api/projects/${S.pid}/skills-md`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ nombre, content }),
      });
      global.toast?.(_t('Skill guardada'), 'success', 2000);
      _cerrarDrawer(true);
      _cargarDetectadas();
    } catch (err) {
      global.toast?.(`${_t('Error guardando')}: ${err.message}`, 'error');
    }
  }

  async function _eliminarSkill() {
    const d = S.drawer;
    if (!d?.nombre) return;
    const ok = typeof global.confirmar === 'function'
      ? await global.confirmar(_t('¿Eliminar la skill "{s}"?').replace('{s}', d.nombre), { peligro: true, confirmText: _t('Eliminar') })
      : true;
    if (!ok) return;
    try {
      await _json(`/api/projects/${S.pid}/skills-md/${encodeURIComponent(d.nombre)}`, { method: 'DELETE' });
      global.toast?.(_t('Skill eliminada'), 'info', 2000);
      _cerrarDrawer(true);
      _cargarDetectadas();
    } catch (err) {
      global.toast?.(`${_t('Error eliminando')}: ${err.message}`, 'error');
    }
  }

  // Cualquier otro archivo detectado: ficha + contenido en solo lectura.
  async function _drawerLectura(it) {
    const h = herrDe(it.ia);
    const tambien = (it.tambien || []).map(herrDe);
    const esUsr = it.alcance === 'usuario';
    const fila = (k, v) => v ? `<div><dt>${esc(_t(k))}</dt><dd>${v}</dd></div>` : '';
    _abrirDrawer(`
      ${_drawerHead(h, it.kind, it.nombre, it.path)}
      <div class="ex-d-body">
        <p class="ex-d-desc">${esc(it.descripcion || _t('(sin descripción)'))}</p>
        <dl class="ex-d-dl">
          ${fila('Lo lee', `${marca(h, 14)} ${esc(h.label)}`)}
          ${fila('Tipo', esc(_t((KINDS[it.kind] || { label: it.kind }).label)))}
          ${fila('Alcance', esc(esUsr ? _t('Tu usuario (~/) — aplica a todos tus proyectos') : _t('Este proyecto')))}
          ${fila('Aplica a', it.detalle ? `<code>${esc(_det(it.detalle))}</code>` : '')}
          ${fila('También lo leen', tambien.map(t => `<span class="ex-d-also">${marca(t, 14)} ${esc(t.label)}</span>`).join(''))}
          ${fila('Tamaño', esc(_fmtBytes(it.bytes)))}
        </dl>
        ${esUsr
          ? `<div class="ex-d-lock">${ic('eye-off', 14)} ${esc(_t('Archivo de tu usuario: se muestra solo el resumen, nunca el contenido.'))}</div>`
          : `<pre class="ex-d-pre" aria-label="${esc(_t('Contenido'))}">${esc(_t('Cargando…'))}</pre>`}
      </div>
      <footer class="ex-d-f">
        <span class="ex-d-count">${esc(_t('Solo lectura'))}</span>
        ${esUsr ? '' : `<button type="button" class="sx-btn sm" data-act="editor" data-path="${esc(it.path)}">${ic('edit', 13)} ${esc(_t('Abrir en el editor'))}</button>`}
        <button type="button" class="sx-btn sm gho" data-act="cerrar" autofocus>${esc(_t('Cerrar'))}</button>
      </footer>`, { modo: 'leer', path: it.path });
    if (esUsr) return;
    try {
      const d = await _json(`/api/projects/${S.pid}/skills/detectadas/contenido?path=${encodeURIComponent(it.path)}`);
      const pre = $('.ex-d-pre');
      if (!pre || S.drawer?.path !== it.path) return;
      pre.textContent = d.content + (d.truncado ? `\n\n… ${_t('(truncado)')}` : '');
    } catch (err) {
      const pre = $('.ex-d-pre');
      if (pre) pre.textContent = `${_t('No se pudo leer')}: ${err.message}`;
    }
  }

  function _fmtBytes(n) {
    if (n == null) return '';
    if (n < 1024) return `${n} B`;
    return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB`;
  }

  function _abrirEnEditor(path) {
    const ed = global.JarvisEditor;
    if (!ed?.abrirArchivo) { global.toast?.(_t('El editor no está disponible'), 'warning'); return; }
    _cerrarDrawer(true);
    global.JarvisSettings?.close?.();
    try { global.JarvisDock?.setTab?.('editor'); } catch {}
    ed.abrirArchivo(path);
  }

  // Marketplace → instalar: el comando + los tres pasos.
  function _drawerInstalar(p) {
    if (!p) return;
    const cmd = cmdInstalar(p);
    _abrirDrawer(`
      ${_drawerHead(herrDe('claude'), null, p.nombre, p.full_id)}
      <div class="ex-d-body">
        <div class="ex-d-plug">${_glifo(p)}<p lang="en">${esc(p.descripcion || _t('(sin descripción)'))}</p></div>
        <ol class="ex-steps">
          <li><b>${esc(_t('Copiá el comando'))}</b>
            <div class="ex-cmd"><code>${esc(cmd)}</code>
              <button type="button" class="sx-btn sm pri" data-act="copiar" data-cmd="${esc(cmd)}" autofocus>${ic('copy', 13)} ${esc(_t('Copiar'))}</button></div></li>
          <li><b>${esc(_t('Pegalo en una terminal de Claude Code'))}</b>
            <span>${esc(_t('Claude lo descarga e instala en tu usuario (~/.claude/plugins).'))}</span></li>
          <li><b>${esc(_t('Activalo en este proyecto'))}</b>
            <span>${esc(_t('Volvé a Plugins y prendé su interruptor: queda anotado en el CLAUDE.md.'))}</span></li>
        </ol>
      </div>
      <footer class="ex-d-f">
        <span class="ex-d-count">${esc(p.marketplace)} · ${esc(p.source === 'external' ? _t('externo') : _t('oficial'))}</span>
        <button type="button" class="sx-btn sm gho" data-act="cerrar">${esc(_t('Listo'))}</button>
      </footer>`, { modo: 'instalar' });
  }

  async function _copiar(b) {
    const cmd = b.dataset.cmd || '';
    try {
      await navigator.clipboard.writeText(cmd);
      b.innerHTML = `${ic('check', 13)} ${esc(_t('Copiado'))}`;
      setTimeout(() => { if (b.isConnected) b.innerHTML = `${ic('copy', 13)} ${esc(_t('Copiar'))}`; }, 1600);
    } catch {
      global.toast?.(_t('Copiá manualmente: {c}').replace('{c}', cmd), 'info');
    }
  }

  global.JarvisExtensiones = { montar, desmontar, pure, abierto: () => !!S.drawer };
})(typeof window !== 'undefined' ? window : globalThis);
