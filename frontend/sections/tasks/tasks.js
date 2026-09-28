// JARVIS — «Tareas»: monitor EN VIVO de los agentes del proyecto.
//
// Cada terminal activa (la abra el usuario o la lance Jarvis como enjambre)
// con su nombre, CLI, qué está haciendo (título vivo del pane + tarea) y UN
// estado: esperando tu respuesta · trabajando · arrancando · terminó · quieto ·
// caído. Fuente única: GET /api/projects/{id}/agentes (plotspace/routers/tasks.py).
//
//  · Pollea cada 2s SOLO con la pestaña a la vista; además refresca (debounced)
//    ante los WS de agentes (onAgentEvent), también con la pestaña oculta, para
//    levantar el badge del dock cuando un agente pasa a «esperando».
//  · Render por CLAVE (grupos y filas persistentes): el poll no re-crea el DOM
//    → las animaciones de estado no se reinician y el foco del teclado se queda.
//  · Click / Enter en un agente → enfoca su card de terminal en el workspace.
//
// Expone window.JarvisTasks = { init, show, onProjectChanged, onAgentEvent, _pure }

(function (root) {
  'use strict';

  /* ══ Lógica pura (testeada en __tests__/tasks.test.js) ═══════════ */

  const ESTADOS = ['esperando', 'trabajando', 'arrancando', 'termino', 'quieto', 'caido'];

  // Texto de la píldora de estado (español canónico; el i18n lo traduce).
  const ETIQUETA = {
    esperando:  'Te necesita',
    trabajando: 'Trabajando',
    arrancando: 'Arrancando',
    termino:    'Terminó',
    quieto:     'Quieto',
    caido:      'Caído',
  };

  // Conteos: «{n} …» con singular/plural en español.
  const CONTEO = {
    esperando:  ['esperando', 'esperando'],
    trabajando: ['trabajando', 'trabajando'],
    arrancando: ['arrancando', 'arrancando'],
    termino:    ['terminó', 'terminaron'],
    quieto:     ['quieto', 'quietos'],
    caido:      ['caído', 'caídos'],
  };

  const EVENTO = {
    TASK_DONE:    'Cerró como hecha',
    TASK_BLOCKED: 'Bloqueado',
    TASK_ERROR:   'Error',
  };

  function normEstado(e) { return ESTADOS.includes(e) ? e : 'quieto'; }

  function claseEstado(e) { return 'tk-st-' + normEstado(e); }

  function textoConteo(estado, n) {
    const par = CONTEO[normEstado(estado)];
    return `${n} ${n === 1 ? par[0] : par[1]}`;
  }

  // [{estado, n, texto}] en el orden canónico, sin ceros.
  function partesResumen(resumen) {
    const r = resumen || {};
    return ESTADOS.filter(e => (r[e] | 0) > 0)
      .map(e => ({ estado: e, n: r[e] | 0, texto: textoConteo(e, r[e] | 0) }));
  }

  function contar(agentes) {
    const out = {};
    for (const a of agentes || []) { const e = normEstado(a.estado); out[e] = (out[e] | 0) + 1; }
    return out;
  }

  // Duración compacta y neutra de idioma: 8s · 12 min · 3 h · 2 d.
  function fmtDur(seg) {
    if (seg == null || !isFinite(seg)) return '';
    const s = Math.max(0, Math.floor(seg));
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)} min`;
    if (s < 86400) return `${Math.floor(s / 3600)} h`;
    return `${Math.floor(s / 86400)} d`;
  }

  // Esperando arriba (es lo que pide acción); el resto en orden de creación
  // (estable: las filas no bailan con cada cambio de estado).
  function ordenar(agentes) {
    return (agentes || []).slice().sort((a, b) => {
      const wa = normEstado(a.estado) === 'esperando' ? 0 : 1;
      const wb = normEstado(b.estado) === 'esperando' ? 0 : 1;
      return wa - wb || (a.id - b.id);
    });
  }

  // {grupos:[{clave, tipo:'jarvis'|'usuario', titulo, resumen, agentes}]}
  // Jarvis primero (una tarjeta por objetivo, en el orden del backend = más
  // reciente primero), después «Tus terminales».
  function agrupar(data) {
    const agentes = (data && data.agentes) || [];
    const porId = new Map(agentes.map(a => [a.id, a]));
    const grupos = [];
    const usados = new Set();
    for (const o of (data && data.orquestaciones) || []) {
      const lista = (o.agentes || []).map(id => porId.get(id)).filter(Boolean);
      if (!lista.length) continue;
      lista.forEach(a => usados.add(a.id));
      grupos.push({
        clave: 'j:' + (o.objetivo || ''),
        tipo: 'jarvis',
        titulo: o.objetivo || '',
        resumen: contar(lista),
        agentes: ordenar(lista),
      });
    }
    // Agentes de origen jarvis que el backend no agrupó (defensivo).
    const sueltosJ = agentes.filter(a => !usados.has(a.id) && a.origen === 'jarvis');
    if (sueltosJ.length) {
      sueltosJ.forEach(a => usados.add(a.id));
      grupos.push({ clave: 'j:', tipo: 'jarvis', titulo: '', resumen: contar(sueltosJ), agentes: ordenar(sueltosJ) });
    }
    const tuyos = agentes.filter(a => !usados.has(a.id));
    if (tuyos.length) {
      grupos.push({ clave: 'u', tipo: 'usuario', titulo: 'Tus terminales', resumen: contar(tuyos), agentes: ordenar(tuyos) });
    }
    return { grupos, resumen: contar(agentes), total: agentes.length };
  }

  // IDs que ENTRARON a «esperando» respecto del snapshot previo (Map id→estado).
  function nuevosEsperando(prev, agentes) {
    const out = [];
    for (const a of agentes || []) {
      if (normEstado(a.estado) === 'esperando' && (!prev || prev.get(a.id) !== 'esperando')) out.push(a.id);
    }
    return out;
  }

  function basename(p) { const s = String(p || ''); const i = s.lastIndexOf('/'); return i >= 0 ? s.slice(i + 1) : s; }

  function hostDe(url) {
    try { const u = new URL(url); return u.host || url; } catch (_) { return String(url || ''); }
  }

  // Firma de lo que se ve de una fila EXCEPTO el tiempo (que se parchea aparte).
  function firmaAgente(a) {
    return JSON.stringify([a.nombre, a.tipo_ia, normEstado(a.estado), a.titulo || '',
      a.tarea ? a.tarea.texto : '', (a.archivos || []).map(f => f.path + ':' + f.writes),
      a.dev_servers || [], a.ultimo_evento ? [a.ultimo_evento.event, a.ultimo_evento.motivo] : null]);
  }

  const _pure = {
    ESTADOS, ETIQUETA, normEstado, claseEstado, textoConteo, partesResumen, contar,
    fmtDur, ordenar, agrupar, nuevosEsperando, basename, hostDe, firmaAgente,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = _pure;
  if (typeof document === 'undefined') return;   // Node: solo la lógica pura

  /* ══ DOM ══════════════════════════════════════════════════════════ */

  const POLL_MS = 2000;
  let _projectId = null;
  let _montado = false;
  let _timer = null;
  let _deb = null;
  let _gen = 0;                      // invalida fetch de un proyecto anterior
  let _enVuelo = false;
  let _prevEstados = null;           // Map id → estado del último snapshot
  let _data = null;
  let _sigResumen = '';
  const _grupos = new Map();         // clave → { el, sig }
  const _filas = new Map();          // id → { el, sig }

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const _t = (s) => (root.JarvisI18n && root.JarvisI18n.t) ? root.JarvisI18n.t(s) : s;
  const ic = (n, s) => (typeof root.icon === 'function' ? root.icon(n, s) : '');
  const logo = (tipo, s) => (typeof root.cliLogo === 'function' ? root.cliLogo(tipo, s) : '');
  const tituloVisible = (t) => (root.JarvisTitulosI18n && root.JarvisTitulosI18n.mostrar)
    ? root.JarvisTitulosI18n.mostrar(t) : t;

  function _visible() {
    if (document.hidden) return false;
    const dock = root.JarvisDock;
    if (dock && typeof dock.activeTab === 'function') return dock.activeTab() === 'tasks';
    const pane = document.querySelector('.jw-pane[data-pane="tasks"]');
    return !!(pane && !pane.hidden);
  }

  /* ── Montaje ─────────────────────────────────────────────────── */
  function _montar() {
    const panel = $('tasks-panel');
    if (!panel || _montado) return;
    _montado = true;
    panel.innerHTML = `
      <div class="tk-root">
        <header class="tk-head">
          <div class="tk-head-row">
            <span class="tk-live" aria-hidden="true"></span>
            <h2 class="tk-title">Agentes</h2>
            <span class="tk-sub">monitor en vivo</span>
            <span class="tk-total" id="tk-total" data-i18n-skip></span>
          </div>
          <div class="tk-chips" id="tk-chips" role="status" aria-live="polite"></div>
        </header>
        <div class="tk-scroll" id="tk-scroll">
          <div class="tk-grupos" id="tk-grupos"></div>
          <div class="tk-vacio" id="tk-vacio" hidden>
            <div class="tk-vacio-orb" aria-hidden="true">${ic('terminal', 22)}</div>
            <div class="tk-vacio-tit">Sin agentes en este proyecto</div>
            <div class="tk-vacio-txt">Abrí una terminal con Ctrl+\\ o pedile a Jarvis que arme un equipo: acá vas a ver en vivo qué hace cada uno.</div>
          </div>
        </div>
      </div>`;

    const grupos = $('tk-grupos');
    grupos.addEventListener('click', (e) => {
      const link = e.target.closest('[data-dev]');
      if (link) { e.preventDefault(); e.stopPropagation(); _abrirDev(link.dataset.dev); return; }
      const fila = e.target.closest('.tk-agente');
      if (fila) _enfocarTerminal(parseInt(fila.dataset.id, 10));
    });
    grupos.addEventListener('keydown', (e) => {
      const fila = e.target.closest('.tk-agente');
      if (!fila || e.target !== fila) return;
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        _enfocarTerminal(parseInt(fila.dataset.id, 10));
      } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const todas = Array.from(grupos.querySelectorAll('.tk-agente'));
        const i = todas.indexOf(fila) + (e.key === 'ArrowDown' ? 1 : -1);
        if (todas[i]) todas[i].focus();
      }
    });
  }

  /* ── Datos ───────────────────────────────────────────────────── */
  async function refrescar() {
    if (_projectId == null || _enVuelo) return;
    _enVuelo = true;
    const gen = _gen;
    const pid = _projectId;
    try {
      const r = await fetch(`/api/projects/${pid}/agentes`);
      if (!r.ok) return;
      const data = await r.json();
      if (gen !== _gen) return;                 // cambió el proyecto mientras viajaba
      _aplicar(data);
    } catch (_) { /* server reiniciando: el próximo tick reintenta */ }
    finally { _enVuelo = false; }
  }

  function _aplicar(data) {
    const agentes = (data && data.agentes) || [];
    const nuevos = nuevosEsperando(_prevEstados, agentes);
    _prevEstados = new Map(agentes.map(a => [a.id, normEstado(a.estado)]));
    _data = data;
    if (nuevos.length && !_visible()) root.JarvisDock?.notify?.('tasks', nuevos.length);
    if (_montado) _render();
  }

  function _programar(ms) {
    clearTimeout(_deb);
    _deb = setTimeout(refrescar, ms);
  }

  function _arrancarPoll() {
    if (_timer) return;
    _timer = setInterval(() => {
      if (!_visible()) { _pararPoll(); return; }
      refrescar();
    }, POLL_MS);
  }
  function _pararPoll() { clearInterval(_timer); _timer = null; }

  /* ── Render por clave ────────────────────────────────────────── */
  function _render() {
    const vista = agrupar(_data);
    const cont = $('tk-grupos');
    $('tk-vacio').hidden = vista.total > 0;
    $('tk-total').textContent = vista.total ? String(vista.total) : '';

    // Chips del encabezado (solo si cambiaron los conteos)
    const partes = partesResumen(vista.resumen);
    const sigR = JSON.stringify(partes);
    if (sigR !== _sigResumen) {
      _sigResumen = sigR;
      $('tk-chips').innerHTML = partes.map(p =>
        `<span class="tk-chip ${claseEstado(p.estado)}"><i class="tk-dot" aria-hidden="true"></i>${esc(p.texto)}</span>`).join('');
    }

    const vivosG = new Set();
    const vivosF = new Set();
    vista.grupos.forEach((g, gi) => {
      vivosG.add(g.clave);
      let rg = _grupos.get(g.clave);
      if (!rg) {
        const el = document.createElement('section');
        el.className = 'tk-grupo tk-grupo-' + g.tipo;
        el.innerHTML = '<header class="tk-grupo-head"></header><div class="tk-lista" role="list"></div>';
        rg = { el, sig: '' };
        _grupos.set(g.clave, rg);
      }
      if (cont.children[gi] !== rg.el) cont.insertBefore(rg.el, cont.children[gi] || null);
      const sigG = JSON.stringify([g.titulo, partesResumen(g.resumen), g.agentes.length]);
      if (sigG !== rg.sig) { rg.sig = sigG; rg.el.firstElementChild.innerHTML = _headGrupoHTML(g); }

      const lista = rg.el.lastElementChild;
      g.agentes.forEach((a, ai) => {
        vivosF.add(a.id);
        let rf = _filas.get(a.id);
        if (!rf) {
          const el = document.createElement('article');
          el.className = 'tk-agente';
          el.tabIndex = 0;
          el.setAttribute('role', 'listitem');
          el.dataset.id = a.id;
          rf = { el, sig: '' };
          _filas.set(a.id, rf);
        }
        if (lista.children[ai] !== rf.el) {
          const teniaFoco = rf.el.contains(document.activeElement);
          lista.insertBefore(rf.el, lista.children[ai] || null);
          if (teniaFoco) rf.el.focus({ preventScroll: true });
        }
        const sig = firmaAgente(a);
        if (sig !== rf.sig) {
          rf.sig = sig;
          const est = normEstado(a.estado);
          rf.el.className = `tk-agente ${claseEstado(est)}`;
          rf.el.innerHTML = _agenteHTML(a, est);
          const etiqueta = _t('Ir a la terminal {nombre}').replace('{nombre}', a.nombre);
          rf.el.setAttribute('aria-label', `${etiqueta} — ${_t(ETIQUETA[est])}`);
        }
        const dur = rf.el.querySelector('[data-dur]');
        const txt = fmtDur(a.estado_hace_s);
        if (dur && dur.textContent !== txt) dur.textContent = txt;
      });
      // filas que se fueron de ESTE grupo (a otro grupo o eliminadas)
      Array.from(lista.children).forEach(ch => {
        const id = parseInt(ch.dataset.id, 10);
        if (!g.agentes.some(a => a.id === id)) ch.remove();
      });
    });
    for (const [k, rg] of _grupos) if (!vivosG.has(k)) { rg.el.remove(); _grupos.delete(k); }
    for (const [id, rf] of _filas) if (!vivosF.has(id)) { rf.el.remove(); _filas.delete(id); }
  }

  function _headGrupoHTML(g) {
    const partes = partesResumen(g.resumen);
    const total = g.agentes.length || 1;
    const barra = partes.map(p =>
      `<i class="tk-seg ${claseEstado(p.estado)}" style="flex-grow:${p.n}"></i>`).join('');
    const resumen = partes.map(p =>
      `<span class="tk-gp ${claseEstado(p.estado)}">${esc(p.texto)}</span>`).join('<span class="tk-sep" aria-hidden="true">·</span>');
    if (g.tipo === 'jarvis') {
      return `
        <div class="tk-grupo-kicker">${ic('sparkles', 11)}<span>Orquestado por Jarvis</span></div>
        ${g.titulo
          ? `<div class="tk-grupo-obj" data-i18n-skip title="${esc(g.titulo)}">${esc(g.titulo)}</div>`
          : '<div class="tk-grupo-obj tk-grupo-obj-vacio">Enjambre de Jarvis</div>'}
        <div class="tk-grupo-prog">
          <div class="tk-barra" aria-hidden="true" data-n="${total}">${barra}</div>
          <div class="tk-grupo-res">${resumen}</div>
        </div>`;
    }
    return `
      <div class="tk-grupo-kicker">${ic('terminal', 11)}<span>Tus terminales</span></div>
      <div class="tk-grupo-res">${resumen}</div>`;
  }

  function _agenteHTML(a, est) {
    const titulo = a.titulo ? tituloVisible(a.titulo) : '';
    const icono = est === 'termino' ? ic('check', 10)
      : est === 'esperando' ? ic('message', 10)
      : est === 'caido' ? ic('alert', 10)
      : est === 'arrancando' ? ic('loader', 10) : '<i class="tk-dot" aria-hidden="true"></i>';

    const lineas = [];
    if (titulo) {
      lineas.push(`<div class="tk-hace" title="${esc(titulo)}">${esc(titulo)}</div>`);
    }
    if (a.tarea && a.tarea.texto) {
      lineas.push(`<div class="tk-tarea"><span class="tk-lbl">Tarea</span><span class="tk-tarea-txt" data-i18n-skip title="${esc(a.tarea.texto)}">${esc(a.tarea.texto)}</span></div>`);
    }
    if (!titulo && !(a.tarea && a.tarea.texto)) {
      lineas.push(`<div class="tk-hace tk-hace-vacio">${est === 'caido' ? 'Su CLI no está corriendo' : 'Sin actividad reportada'}</div>`);
    }

    const meta = [];
    const ev = a.ultimo_evento;
    if (ev && EVENTO[ev.event]) {
      const tipoEv = ev.event === 'TASK_DONE' ? 'ok' : 'mal';
      meta.push(`<span class="tk-ev tk-ev-${tipoEv}">${ic(tipoEv === 'ok' ? 'check' : 'alert', 10)}<span>${EVENTO[ev.event]}</span>${
        ev.motivo ? `<span class="tk-ev-mot" data-i18n-skip title="${esc(ev.motivo)}">${esc(ev.motivo)}</span>` : ''}</span>`);
    }
    const archs = a.archivos || [];
    if (archs.length) {
      const vis = archs.slice(0, 3).map(f =>
        `<span class="tk-file" data-i18n-skip title="${esc(f.path)}">${esc(basename(f.path))}${f.writes > 1 ? `<b>×${f.writes}</b>` : ''}</span>`).join('');
      const mas = archs.length > 3 ? `<span class="tk-mas">+${archs.length - 3} más</span>` : '';
      meta.push(`<span class="tk-files">${ic('file', 10)}${vis}${mas}</span>`);
    }
    for (const url of (a.dev_servers || []).slice(0, 3)) {
      meta.push(`<a class="tk-dev" href="${esc(url)}" data-dev="${esc(url)}" data-i18n-skip title="${esc(url)}">${ic('globe', 10)}${esc(hostDe(url))}</a>`);
    }

    return `
      <div class="tk-logo" aria-hidden="true">${logo(a.tipo_ia, 18)}</div>
      <div class="tk-cuerpo">
        <div class="tk-fila1">
          <span class="tk-nombre" data-i18n-skip title="${esc(a.nombre)}">${esc(a.nombre)}</span>
          <span class="tk-pill">${icono}<span class="tk-pill-txt">${ETIQUETA[est]}</span><span class="tk-pill-dur" data-dur data-i18n-skip></span></span>
        </div>
        ${lineas.join('')}
        ${meta.length ? `<div class="tk-meta">${meta.join('')}</div>` : ''}
      </div>`;
  }

  /* ── Acciones ────────────────────────────────────────────────── */
  function _enfocarTerminal(id) {
    if (!id) return;
    const card = document.getElementById(`terminal-card-${id}`);
    if (!card) { root.toast?.(_t('Esa terminal no está en pantalla'), 'info'); return; }
    try { card.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' }); } catch (_) { card.scrollIntoView(); }
    card.classList.remove('tk-foco-card');
    void card.offsetWidth;               // re-dispara la animación si ya estaba
    card.classList.add('tk-foco-card');
    clearTimeout(card._tkFoco);
    card._tkFoco = setTimeout(() => card.classList.remove('tk-foco-card'), 1700);
    // Foco de teclado a la terminal: si está esperando, respondés sin clickear.
    const ta = card.querySelector('.xterm-helper-textarea');
    if (ta) { try { ta.focus({ preventScroll: true }); } catch (_) { ta.focus(); } }
    root.TerminalAura?.apagar?.(id);
  }

  function _abrirDev(url) {
    if (!url) return;
    if (root.WebPreview && root.JarvisDock) {
      root.JarvisDock.setTab('preview');
      root.WebPreview.abrirLink?.(url);
    } else {
      window.open(url, '_blank', 'noopener');
    }
  }

  function _reset() {
    _gen++;
    _data = null;
    _prevEstados = null;
    _sigResumen = '';
    for (const rg of _grupos.values()) rg.el.remove();
    for (const rf of _filas.values()) rf.el.remove();
    _grupos.clear();
    _filas.clear();
    if (_montado) { $('tk-chips').innerHTML = ''; $('tk-total').textContent = ''; $('tk-vacio').hidden = true; }
  }

  /* ── API pública ─────────────────────────────────────────────── */
  function onAgentEvent() {
    if (_projectId == null) return;
    _programar(350);
  }

  root.JarvisTasks = {
    init(projectId) {
      _projectId = projectId;
      _montar();
    },
    show() {
      _montar();
      refrescar();
      _arrancarPoll();
    },
    onProjectChanged(projectId) {
      _projectId = projectId;
      _reset();
      if (_visible()) { refrescar(); _arrancarPoll(); }
    },
    onAgentEvent,
    _pure,
  };

  // Volver a la pestaña del navegador con el dock en Tareas → retomar el poll.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && _visible()) { refrescar(); _arrancarPoll(); }
  });
})(typeof window !== 'undefined' ? window : globalThis);
