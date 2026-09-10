// JARVIS — Configuración · rediseño "Liquid Glass · Datasheet" (2026-07-19).
// Reemplaza al rediseño "Observatorio", que había cambiado el TEMA sobre la
// misma estructura genérica (nav lateral + filas idénticas con toggle). Acá
// cambia la ESTRUCTURA: el layout lateral se queda (pedido del usuario) y todo
// lo demás es nuevo — una sola losa de vidrio, cuerpo de ficha técnica (canaleta
// de etiqueta mono + contenido, líneas de pelo, cero tarjetas anidadas), valores
// vivos en el rail y CADA DOMINIO CON LA FORMA DE SU DATO: la tecla de voz como
// objeto físico, un mapa de teclado real, los 24 temas como espectro con vista
// previa, un conmutador de cuentas, un rack de extensiones, una consola de
// memoria y una línea de tiempo de workflows.
// El prototipo aprobado y las decisiones (qué NO reintroducir) viven en
// frontend/preview-settings/AGENTS.md.
// Expone window.JarvisSettings = { init, onProjectChanged, open, close, isOpen,
// refrescar, onCuentaAgregada }.
(() => {
  'use strict';

  let _projectId    = null;
  let _abierta      = false;
  let _seccion      = 'voz';
  let _prevFocus    = null;
  let _altaCerrar   = null;   // cierre del modal de alta de cuenta (si está abierto)
  let _altaResolver = null;   // handler de "cuenta detectada" para ese modal
  let _resSel       = -1;     // índice seleccionado en los resultados de búsqueda

  // Resumen para los valores vivos del rail (se refresca al abrir).
  const _res = { cuentas: null, activos: null, memorias: null, workflows: null };

  const SECCIONES = [
    { grupo: 'general', items: [
      { id: 'voz',        label: 'Voz',         icon: 'mic' },
      { id: 'atajos',     label: 'Teclado',     icon: 'keyboard' },
      { id: 'apariencia', label: 'Apariencia',  icon: 'sparkles' },
      { id: 'cuentas',    label: 'Cuentas',     icon: 'key' },
    ]},
    { grupo: 'proyecto', items: [
      { id: 'skills',    label: 'Extensiones', icon: 'plug' },
      { id: 'memoria',   label: 'Memoria',     icon: 'brain' },
      { id: 'workflows', label: 'Workflows',   icon: 'workflow' },
    ]},
  ];

  const SEC_META = {
    voz:        { t: 'Voz',         sub: 'Tu tecla para hablar, el dictado y los avisos.' },
    atajos:     { t: 'Teclado',     sub: 'Todo el workspace en un teclado. Tocá una tecla para reasignarla.' },
    apariencia: { t: 'Apariencia',  sub: '24 temas, tonalidad fina e idioma. Se aplica al instante.' },
    cuentas:    { t: 'Cuentas',     sub: 'Varias cuentas por CLI, cambio sin re-loguear.' },
    skills:     { t: 'Extensiones', sub: 'Plugins activos en este proyecto y skills del repo.' },
    memoria:    { t: 'Memoria',     sub: 'El conocimiento que comparten los agentes: estado y salud.' },
    workflows:  { t: 'Workflows',   sub: 'Historial de orquestaciones multi-agente.' },
  };

  const esc = (s) => { const d = document.createElement('div'); d.textContent = String(s ?? ''); return d.innerHTML; };
  const _t = (s) => window.JarvisI18n?.t?.(s) ?? s;
  const num = (n) => Number(n || 0).toLocaleString('es-AR');
  const _root = () => document.getElementById('jw-settings');
  const $ = (s) => _root()?.querySelector(s);
  const _itemDe = (id) => SECCIONES.flatMap(g => g.items).find(i => i.id === id);
  // Los resúmenes de memoria vienen en markdown crudo: se leen como texto.
  const limpio = (s) => String(s || '')
    .replace(/^[>\s]*\[[^\]]*\]\s*/, '').replace(/[*_`#>]+/g, '').replace(/\s+/g, ' ').trim();

  /* ═══════════════════════════════════════════════════════════
     ARMAZÓN
     ═══════════════════════════════════════════════════════════ */
  function _render() {
    const root = _root();
    if (!root) return;
    root.innerHTML = `
      <section class="sx-slab" id="sx-slab">
        <aside class="sx-rail">
          <div class="sx-brand"><span class="sx-brand-t">${esc(_t('Configuración'))}</span></div>
          <label class="sx-find">
            ${icon('search', 13)}
            <input id="sx-q" type="text" placeholder="${esc(_t('Buscar ajuste'))}" autocomplete="off"
                   spellcheck="false" aria-label="${esc(_t('Buscar ajuste'))}">
            <kbd>/</kbd>
          </label>
          <nav class="sx-nav" id="sx-nav" aria-label="${esc(_t('Secciones'))}">
            <span class="sx-thumb" id="sx-thumb" aria-hidden="true"></span>
            ${SECCIONES.map(g => `
              <span class="sx-nav-lbl">${esc(_t(g.grupo))}</span>
              ${g.items.map(i => `
                <button class="sx-item${i.id === _seccion ? ' on' : ''}" type="button"
                        data-sec="${i.id}" aria-current="${i.id === _seccion}">
                  ${icon(i.icon, 15)}
                  <span class="sx-item-t">${esc(_t(i.label))}</span>
                  <span class="sx-item-v" data-v="${i.id}"></span>
                </button>`).join('')}`).join('')}
          </nav>
          <div class="sx-res" id="sx-res" hidden></div>
        </aside>

        <main class="sx-body">
          <header class="sx-sill">
            <h1 id="sx-t"></h1>
            <p class="sx-sill-sub" id="sx-sub"></p>
            <button class="sx-x" id="sx-close" type="button" aria-label="${esc(_t('Cerrar'))}">${icon('x', 15)}</button>
          </header>
          <div class="sx-scroll" id="sx-scroll">
            <div class="sx-sheet sx-in" id="sx-sheet"></div>
          </div>
        </main>
      </section>`;

    $('#sx-close').addEventListener('click', close);
    root.addEventListener('mousedown', (e) => { if (e.target === root) close(); });
    root.querySelectorAll('.sx-item').forEach(b =>
      b.addEventListener('click', () => setSeccion(b.dataset.sec)));

    _especular();
    _wireBusqueda();
    _pintarValores();
    _renderSeccion();
    requestAnimationFrame(() => _moverThumb(true));
    window.addEventListener('resize', _resizeThumb);
  }

  const _resizeThumb = () => _moverThumb(true);

  // Especular del vidrio: sigue al puntero (una sola custom property por frame).
  function _especular() {
    const slab = $('#sx-slab');
    if (!slab || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    let raf = 0;
    slab.addEventListener('pointermove', (e) => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        const r = slab.getBoundingClientRect();
        slab.style.setProperty('--sx-mx', `${((e.clientX - r.left) / r.width) * 100}%`);
        slab.style.setProperty('--sx-my', `${((e.clientY - r.top) / r.height) * 100}%`);
      });
    });
    slab.addEventListener('pointerenter', () => slab.classList.add('sx-lit'));
    slab.addEventListener('pointerleave', () => slab.classList.remove('sx-lit'));
  }

  // Indicador líquido: viaja estirándose y se asienta al llegar.
  function _moverThumb(instant) {
    const thumb = $('#sx-thumb');
    const activo = _root()?.querySelector('.sx-item.on');
    if (!thumb) return;
    if (!activo) { thumb.style.opacity = '0'; return; }
    const nav = $('#sx-nav');
    const top = activo.offsetTop - (nav?.scrollTop || 0);
    const prev = parseFloat(thumb.dataset.top || top);
    const sy = instant ? 1 : 1 + Math.min(0.3, Math.abs(top - prev) / 780);
    thumb.style.opacity = '1';
    thumb.style.height = `${activo.offsetHeight}px`;
    thumb.dataset.top = top;
    if (instant || matchMedia('(prefers-reduced-motion: reduce)').matches) {
      thumb.style.transform = `translateY(${top}px)`;
      return;
    }
    thumb.style.transform = `translateY(${top}px) scaleY(${sy})`;
    clearTimeout(thumb._t);
    thumb._t = setTimeout(() => { thumb.style.transform = `translateY(${top}px) scaleY(1)`; }, 190);
  }

  // Valores vivos del rail: la config se ve sin entrar a cada sección.
  function _pintarValores() {
    const tema = window.JarvisThemes?.actual?.() || 'violeta';
    const v = {
      voz: '',
      atajos: String(Object.keys(_atajosVivos()).length),
      apariencia: (TEMA_META[tema] || [tema])[0],
      cuentas: _res.cuentas == null ? '' : String(_res.cuentas),
      skills: _res.activos == null ? '' : String(_res.activos),
      memoria: _res.memorias == null ? '' : String(_res.memorias),
      workflows: _res.workflows == null ? '' : String(_res.workflows),
    };
    _root()?.querySelectorAll('[data-v]').forEach(el => { el.textContent = v[el.dataset.v] ?? ''; });
  }

  // Los cuatro números del rail que dependen del server. Livianos y en paralelo.
  async function _cargarResumen() {
    const j = async (url) => { const r = await fetch(url); if (!r.ok) throw 0; return r.json(); };
    const pid = _projectId;
    const tareas = [
      j('/api/cuentas').then(d => { _res.cuentas = (d.clis || []).reduce((a, c) => a + (c.cuentas || []).length, 0); }),
    ];
    if (pid) {
      tareas.push(
        j(`/api/projects/${pid}/plugins/activos`).then(d => { _res.activos = (d.activos || []).length; }),
        j(`/api/projects/${pid}/memory/salud`).then(d => { _res.memorias = d.total || 0; }),
        j(`/api/orchestrator/workflows/${pid}`).then(d => { _res.workflows = (d || []).length; }),
      );
    }
    await Promise.allSettled(tareas);
    if (_abierta) _pintarValores();
  }

  function setSeccion(sec) {
    if (!sec || sec === _seccion) return;
    _seccion = sec;
    _root()?.querySelectorAll('.sx-item').forEach(b => {
      const on = b.dataset.sec === sec;
      b.classList.toggle('on', on);
      b.setAttribute('aria-current', on);
    });
    _moverThumb(false);
    _renderSeccion();
  }

  function _renderSeccion() {
    const meta = SEC_META[_seccion] || {};
    const t = $('#sx-t'), sub = $('#sx-sub'), sheet = $('#sx-sheet');
    if (!sheet) return;
    t.textContent = _t(meta.t || '');
    sub.textContent = _t(meta.sub || '');
    sheet.innerHTML = '';
    ({
      voz: _renderVoz, atajos: _renderAtajos, apariencia: _renderApariencia,
      cuentas: _renderCuentas, skills: _renderSkills, memoria: _renderMemoria,
      workflows: _renderWorkflows,
    }[_seccion] || (() => {}))(sheet);
    sheet.classList.remove('sx-in'); void sheet.offsetWidth; sheet.classList.add('sx-in');
    $('#sx-scroll').scrollTop = 0;
    _vigilarBandas(sheet);
  }

  /* ── Bandas de bloque: marcar la que quedó pegada arriba ──
     La banda pegada es el "estás acá" de la sección, así que tiene que
     acertar SIEMPRE. Se mide contra el techo del scrollport en un rAF por
     frame de scroll (son ≤7 bandas: el costo es ruido).
     Con IntersectionObserver no alcanzaba: un salto de scroll (el de la
     búsqueda, una rueda rápida) puede llevar la banda de "asomando abajo" a
     "pegada arriba" sin cruzar ningún umbral, y el estado se perdía.
     El MutationObserver re-marca lo que llega tarde: cuentas, memoria,
     extensiones y workflows pintan su cuerpo async. */
  let _obsSheet = null, _obsSheetNodo = null, _bandaRaf = 0;

  function _marcarBandas() {
    _bandaRaf = 0;
    const scroll = $('#sx-scroll');
    if (!scroll) return;
    const techo = scroll.getBoundingClientRect().top;
    scroll.querySelectorAll('.sx-blk-l').forEach(b => {
      const r = b.getBoundingClientRect();
      b.classList.toggle('fija', r.top <= techo + 1 && r.bottom > techo);
    });
  }
  const _pedirMarca = () => { if (!_bandaRaf) _bandaRaf = requestAnimationFrame(_marcarBandas); };

  function _vigilarBandas(sheet) {
    const scroll = $('#sx-scroll');
    if (!scroll) return;
    if (!scroll.dataset.bandas) {              // _render() rehace el DOM: nodo nuevo, cable nuevo
      scroll.dataset.bandas = '1';
      scroll.addEventListener('scroll', _pedirMarca, { passive: true });
    }
    if (_obsSheetNodo !== sheet && typeof MutationObserver === 'function') {
      _obsSheet?.disconnect();
      _obsSheetNodo = sheet;
      _obsSheet = new MutationObserver(_pedirMarca);
      _obsSheet.observe(sheet, { childList: true, subtree: true });
    }
    _pedirMarca();
  }

  /* ── Búsqueda profunda: cada AJUSTE, no cada sección ── */
  function _indexAjustes() {
    const idx = [];
    const push = (sec, label, extra, key) => idx.push({ sec, label, extra: extra || '', key: key === undefined ? label : key });
    SECCIONES.forEach(g => g.items.forEach(it => push(it.id, it.label, g.grupo, null)));
    (window.JarvisControls?.list?.() || []).forEach(c =>
      push(c.mode === 'press' ? 'atajos' : 'voz', c.label, 'tecla atajo binding'));
    _DICTADO_OPTS.forEach(o => push('voz', o.label, o.desc));
    push('voz', 'Notificaciones del navegador', 'sistema permiso avisos push');
    push('voz', 'Sonido al terminar tareas', 'acorde agente espera aviso');
    push('atajos', 'Atajos del workspace', 'teclado ctrl esc mapa teclas');
    push('apariencia', 'Tema de color', 'paleta oscuro claro acento espectro');
    Object.entries(TEMA_META).forEach(([id, [n]]) => push('apariencia', n, `tema ${id}`, 'Tema de color'));
    push('apariencia', 'Tonalidad', 'matiz saturación profundidad tinte');
    push('apariencia', 'Escala de la app', 'zoom tamaño agrandar achicar interfaz letra grande');
    push('apariencia', 'Idioma de la interfaz', 'español english lang');
    push('apariencia', 'Auto-iniciar el preview móvil', 'expo metro mobile');
    push('cuentas', 'Conectar cuenta nueva', 'login oauth vincular cli', null);
    push('skills', 'Plugins instalados', 'marketplace extensiones', null);
    push('memoria', 'Explorar memoria', 'wikilinks grafo notas salud');
    push('workflows', 'Historial de workflows', 'orquestación pasos agentes', null);
    return idx;
  }

  function _wireBusqueda() {
    const input = $('#sx-q'), nav = $('#sx-nav'), res = $('#sx-res');
    const idx = _indexAjustes();
    const pintar = () => {
      const q = input.value.trim().toLowerCase();
      if (!q) { res.hidden = true; res.innerHTML = ''; nav.hidden = false; _resSel = -1; _moverThumb(true); return; }
      const hits = idx.filter(a =>
        a.label.toLowerCase().includes(q) || a.extra.toLowerCase().includes(q)).slice(0, 10);
      nav.hidden = true; res.hidden = false;
      _resSel = hits.length ? 0 : -1;
      res.innerHTML = hits.length ? hits.map((h, i) => {
        const it = _itemDe(h.sec);
        return `<button class="sx-res-i${i === 0 ? ' sel' : ''}" type="button" data-sec="${h.sec}" ${h.key ? `data-key="${esc(h.key)}"` : ''}>
          ${icon(it ? it.icon : 'settings', 13)}
          <span class="sx-res-tx"><span class="sx-res-a">${esc(h.label)}</span><span class="sx-res-b">${esc(it ? it.label : h.sec)}</span></span>
        </button>`;
      }).join('') : `<div class="sx-res-none">${esc(_t('Nada coincide con'))} «${esc(input.value.trim())}».</div>`;
      res.querySelectorAll('.sx-res-i').forEach(b => b.addEventListener('click', () => {
        _saltarA(b.dataset.sec, b.dataset.key || null);
        input.value = ''; pintar();
      }));
    };
    input.addEventListener('input', pintar);
    input.addEventListener('keydown', (e) => {
      const items = [...res.querySelectorAll('.sx-res-i')];
      if (e.key === 'Escape' && input.value) { e.preventDefault(); e.stopPropagation(); input.value = ''; pintar(); return; }
      if (!items.length) return;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        _resSel = (_resSel + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        items.forEach((el, i) => el.classList.toggle('sel', i === _resSel));
        items[_resSel].scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter') { e.preventDefault(); items[Math.max(0, _resSel)].click(); }
    });
  }

  function _saltarA(sec, key) {
    setSeccion(sec);
    if (!key) return;
    requestAnimationFrame(() => {
      const el = _root()?.querySelector(`#sx-sheet [data-key="${CSS.escape(key)}"]`);
      if (!el) return;
      const quieto = matchMedia('(prefers-reduced-motion: reduce)').matches;
      el.scrollIntoView({ block: 'center', behavior: quieto ? 'auto' : 'smooth' });
      el.classList.add('sx-hit');
      setTimeout(() => el.classList.remove('sx-hit'), 1500);
    });
  }

  /* ── Abrir / cerrar ── */
  function open(seccion) {
    if (seccion) _seccion = seccion;
    _prevFocus = document.activeElement;
    _abierta = true;
    const root = _root();
    if (!root) return;
    root.hidden = false;
    _render();
    _cargarResumen();
    document.addEventListener('keydown', _onKey, true);
  }

  function close() {
    _abierta = false;
    const root = _root();
    if (root) { root.hidden = true; root.innerHTML = ''; }
    // El DOM de Voz/Atajos se detacha: soltar el callback para no pintar sobre
    // nodos removidos ni mantener vivo el subtree detached.
    if (window.JarvisControls) window.JarvisControls.onCambio = null;
    document.removeEventListener('keydown', _onKey, true);
    window.removeEventListener('resize', _resizeThumb);
    _prevFocus?.focus?.();
  }

  function _onKey(e) {
    if (!_abierta) return;
    // No robar Esc si hay un sub-modal encima (editor de skill, alta de cuenta,
    // confirm/prompt). El buscador con texto también se queda su Esc.
    if (e.key === 'Escape'
        && !document.querySelector('#modal-skill-md[style*="flex"]')
        && !document.querySelector('.cta-alta-overlay')
        && !document.querySelector('.ob-confirm-overlay')) {
      e.preventDefault(); e.stopPropagation(); close();
      return;
    }
    if (e.key === '/' && !/^(INPUT|TEXTAREA)$/.test(document.activeElement?.tagName || '')) {
      e.preventDefault(); $('#sx-q')?.focus();
    }
  }

  /* ═══════════════════════════════════════════════════════════
     PRIMITIVAS DE FICHA TÉCNICA (reemplazan a la tarjeta)
     Un bloque = banda (rótulo + pista, pegajosa) + contenido a TODO el ancho.
     `o.wide` ya no cambia nada: quedó de cuando el rótulo vivía en una
     canaleta lateral y un bloque podía romperla. Se deja porque lo pasan
     una docena de llamadas; en las nuevas, no hace falta.
     ═══════════════════════════════════════════════════════════ */
  const blk = (label, hint, inner, o = {}) => `
    <section class="sx-blk${o.wide ? ' wide' : ''}"${o.key ? ` data-key="${esc(o.key)}"` : ''}>
      <div class="sx-blk-l"><b>${esc(_t(label))}</b>${hint ? `<span>${esc(_t(hint))}</span>` : ''}</div>
      <div class="sx-blk-c">${inner}</div>
    </section>`;

  const sw = (id, on, label) => `
    <label class="sx-sw" title="${esc(label || '')}">
      <input type="checkbox" id="${id}" ${on ? 'checked' : ''} aria-label="${esc(label || '')}">
      <i></i>
    </label>`;

  const setRow = (label, desc, control, key) => `
    <div class="sx-set"${key ? ` data-key="${esc(key)}"` : ''}>
      <div class="sx-set-t"><b>${esc(_t(label))}</b>${desc ? `<span>${esc(_t(desc))}</span>` : ''}</div>
      ${control}
    </div>`;

  /* ═══════════════════════════════════════════════════════════
     1 · VOZ — la tecla como objeto físico + la ruta de la señal
     ═══════════════════════════════════════════════════════════ */
  const _DICTADO_OPTS = [
    { ls: 'jarvis.voz.traducir',   on: '1',  off: '0',   def: false, label: 'Traducir a inglés',   desc: 'Lo que dictás se transcribe y se pasa a inglés.' },
    { ls: 'jarvis.voz.sonido',     on: 'on', off: 'off', def: true,  label: 'Bip al dictar',       desc: 'Un tono corto al empezar y al terminar de escuchar.' },
    { ls: 'jarvis.voz.autoenviar', on: '1',  off: '0',   def: false, label: 'Auto-enviar',         desc: 'Manda el mensaje al soltar la tecla, sin apretar ↵.' },
    { ls: 'jarvis.voz.pill',       on: 'on', off: 'off', def: true,  label: 'Píldora de voz',      desc: 'La barra compacta que aparece mientras hablás.' },
  ];
  const _dictOn = (o) => { const raw = localStorage.getItem(o.ls); return raw === null ? o.def : raw === o.on; };

  function _renderVoz(b) {
    const ptt = (window.JarvisControls?.list?.() || []).find(c => c.mode !== 'press');
    const lbl = () => window.JarvisControls?.label?.(ptt?.id || 'mic-ptt') || 'Alt';

    const pintarTecla = () => {
      // El label pasa por _t acá: interpolado dentro de una frase ("Mantené X
      // para hablar") el scanner del i18n ya no lo alcanza y quedaba mezclado
      // ("Hold Mouse · adelante to talk").
      const k = _t(lbl());
      // El botón ES .set-keybind: el motor de captura de workspace.js lo busca por
      // esa clase y le reescribe el innerHTML mientras captura. No renombrar.
      return `
        <div class="vz">
          <div class="vz-col">
            <button class="vz-key settings-keybind set-keybind${k.length > 4 ? ' largo' : ''}"
                    data-id="${esc(ptt?.id || 'mic-ptt')}" type="button"
                    aria-label="${esc(_t('Reasignar la tecla de voz'))}">
              <span class="settings-kbd">${esc(k)}</span>
            </button>
            <span class="vz-key-h">${esc(_t('tocá para reasignar'))}</span>
          </div>
          <div class="vz-txt">
            <p><b>${esc(_t('Mantené'))} ${esc(k)}</b> ${esc(_t('para hablar. El audio va a donde hiciste click por última vez: una terminal o el chat de Jarvis.'))}</p>
            <p class="vz-sec">${esc(_t('Doble toque: dictado fijado — grabás sin mantener nada y te movés por el workspace. La misma tecla envía; Esc cancela.'))}</p>
            <div class="vz-ruta" aria-label="${esc(_t('Ruta de la señal'))}">
              <span class="vz-nodo on">${esc(k)}</span>
              <span class="vz-flecha"></span><span class="vz-nodo">${esc(_t('escuchar'))}</span>
              <span class="vz-flecha"></span><span class="vz-nodo">${esc(_t('transcribir'))}</span>
              <span class="vz-flecha"></span><span class="vz-nodo">${esc(_t('terminal activa'))}</span>
            </div>
          </div>
        </div>`;
    };

    const pintar = () => {
      b.innerHTML =
        blk('tecla', 'push-to-talk', pintarTecla(), { key: ptt?.label || 'Hablar' }) +
        blk('dictado', 'sobre lo que dictás',
          _DICTADO_OPTS.map((o, i) => setRow(o.label, o.desc,
            sw(`sx-dic-${i}`, _dictOn(o), _t(o.label)), o.label)).join(''),
          { key: 'Dictado' }) +
        blk('avisos', 'cuando no mirás',
          setRow('Notificaciones del navegador',
            'Apagadas — al activarlas el navegador va a pedir permiso.',
            sw('sx-notif', false, _t('Notificaciones del navegador')),
            'Notificaciones del navegador') +
          setRow('Sonido al terminar una tarea',
            'Un acorde corto cuando un agente termina o queda esperando respuesta.',
            sw('sx-snd', window.JarvisSonido ? window.JarvisSonido.get()
                        : localStorage.getItem('jarvis.sonidoTareas') !== 'off',
               _t('Sonido al terminar tareas')),
            'Sonido al terminar tareas'),
          { key: 'Avisos' });

      // Dictado
      _DICTADO_OPTS.forEach((o, i) => {
        const el = b.querySelector(`#sx-dic-${i}`);
        el?.addEventListener('change', () => localStorage.setItem(o.ls, el.checked ? o.on : o.off));
      });
      // Notificaciones del SO: el click ES el gesto que el browser exige.
      const notif = b.querySelector('#sx-notif');
      const notifDesc = notif?.closest('.sx-set')?.querySelector('.sx-set-t span');
      const concedido = typeof Notification !== 'undefined' && Notification.permission === 'granted';
      const onNotif = localStorage.getItem('jarvis.notif.os') === '1' && concedido;
      if (notif) notif.checked = onNotif;
      if (notifDesc && onNotif) notifDesc.textContent = _t('Activadas');
      notif?.addEventListener('change', async () => {
        if (notif.checked) {
          const ok = await (window.JarvisNotify?.pedirPermiso?.() ?? Promise.resolve(false));
          if (ok) { localStorage.setItem('jarvis.notif.os', '1'); if (notifDesc) notifDesc.textContent = _t('Activadas'); }
          else {
            notif.checked = false;
            localStorage.removeItem('jarvis.notif.os');
            if (notifDesc) notifDesc.textContent = _t('El navegador no dio permiso');
            window.toast?.(_t('El navegador bloqueó las notificaciones'), 'error');
          }
        } else {
          localStorage.removeItem('jarvis.notif.os');
          if (notifDesc) notifDesc.textContent = _t('Apagadas — al activarlas el navegador va a pedir permiso.');
        }
      });
      // Sonido de eventos de agente (fuente de verdad: window.JarvisSonido)
      const snd = b.querySelector('#sx-snd');
      snd?.addEventListener('change', () => {
        if (window.JarvisSonido) window.JarvisSonido.set(snd.checked);
        else localStorage.setItem('jarvis.sonidoTareas', snd.checked ? 'on' : 'off');
      });
      // The voice control opens the voice setup dialog (Groq key + key) instead
      // of capturing inline: it's the only place where it's edited.
      b.querySelector('.set-keybind')?.addEventListener('click', (e) => {
        if (e.currentTarget.dataset.id === 'mic-ptt' && window.JarvisGroqSetup?.abrir) {
          window.JarvisGroqSetup.abrir();
          return;
        }
        window.JarvisControls?.capturar?.(e.currentTarget.dataset.id);
      });
    };

    window.JarvisControls && (window.JarvisControls.onCambio = () => { pintar(); _pintarValores(); });
    pintar();
  }

  /* ═══════════════════════════════════════════════════════════
     2 · TECLADO — un mapa de teclas, no una tabla
     ═══════════════════════════════════════════════════════════ */
  const FILAS = [
    [['Escape', 'esc', 2], ['Digit1', '1', 2], ['Digit2', '2', 2], ['Digit3', '3', 2], ['Digit4', '4', 2],
     ['Digit5', '5', 2], ['Digit6', '6', 2], ['Digit7', '7', 2], ['Digit8', '8', 2], ['Digit9', '9', 2],
     ['Digit0', '0', 2], ['Minus', '-', 2], ['Equal', '=', 2], ['Backspace', '⌫', 4]],
    [['Tab', 'tab', 3], ['KeyQ', 'Q', 2], ['KeyW', 'W', 2], ['KeyE', 'E', 2], ['KeyR', 'R', 2], ['KeyT', 'T', 2],
     ['KeyY', 'Y', 2], ['KeyU', 'U', 2], ['KeyI', 'I', 2], ['KeyO', 'O', 2], ['KeyP', 'P', 2],
     ['BracketLeft', '[', 2], ['BracketRight', ']', 2], ['Backslash', '\\', 3]],
    [['CapsLock', 'caps', 4], ['KeyA', 'A', 2], ['KeyS', 'S', 2], ['KeyD', 'D', 2], ['KeyF', 'F', 2], ['KeyG', 'G', 2],
     ['KeyH', 'H', 2], ['KeyJ', 'J', 2], ['KeyK', 'K', 2], ['KeyL', 'L', 2], ['Semicolon', ';', 2],
     ['Quote', "'", 2], ['Enter', '↵', 4]],
    [['ShiftLeft', 'shift', 5], ['KeyZ', 'Z', 2], ['KeyX', 'X', 2], ['KeyC', 'C', 2], ['KeyV', 'V', 2],
     ['KeyB', 'B', 2], ['KeyN', 'N', 2], ['KeyM', 'M', 2], ['Comma', ',', 2], ['Period', '.', 2],
     ['Slash', '/', 2], ['ShiftRight', 'shift', 5]],
    [['ControlLeft', 'ctrl', 3], ['MetaLeft', '⌘', 3], ['AltLeft', 'alt', 3], ['Space', '', 15],
     ['AltRight', 'alt', 3], ['ControlRight', 'ctrl', 3]],
  ];

  const _ATAJOS_FIJOS = {
    KeyK: { mod: 'Ctrl', que: 'Buscar proyecto' },
    KeyT: { mod: 'Ctrl', que: 'Nuevo workspace' },
    KeyB: { mod: 'Ctrl', que: 'Ocultar / mostrar la franja' },
    KeyP: { mod: 'Ctrl', que: 'Abrir / cerrar el panel' },
    KeyE: { mod: 'Ctrl', que: 'Panel → Editor' },
    KeyJ: { mod: 'Ctrl', que: 'Panel → Jarvis' },
    Escape: { mod: null, que: 'Cerrar / salir de pantalla completa' },
  };
  for (let i = 1; i <= 9; i++) _ATAJOS_FIJOS[`Digit${i}`] = { mod: 'Ctrl', que: _t('Saltar al proyecto {i}').replace('{i}', i) };

  // El code de teclado del binding, o null si está en un botón del mouse
  // (entonces no hay tecla que iluminar en el mapa).
  function _bindingCode(id) {
    try {
      const raw = localStorage.getItem(`jarvis.control.${id}`);
      if (raw === null) return id === 'mic-ptt' ? 'AltLeft' : 'Backslash';
      const b = JSON.parse(raw);
      if (!b || b.value === undefined || b.value === null) return null;
      return b.type === 'mouse' ? null : String(b.value);
    } catch { return null; }
  }

  function _atajosVivos() {
    const m = Object.assign({}, _ATAJOS_FIJOS);
    (window.JarvisControls?.list?.() || []).forEach(c => {
      const code = _bindingCode(c.id);
      if (!code) return;
      m[code] = c.mode === 'press'
        ? { mod: 'Ctrl', que: c.label, cfg: c.id }
        : { mod: null, que: `${c.label} (mantener)`, cfg: c.id };
    });
    return m;
  }

  function _renderAtajos(b) {
    const pintar = () => {
      const A = _atajosVivos();
      const ctrls = window.JarvisControls?.list?.() || [];
      const teclado = `
        <div class="kb">
          ${FILAS.map(f => `<div class="kb-f">${f.map(([code, cap, w]) => {
            const a = A[code];
            return `<button class="kb-k${a ? ' bound' : ''}${a && a.cfg ? ' cfg' : ''}" type="button"
                      style="grid-column: span ${w}" data-code="${code}"
                      ${a ? `data-que="${esc((a.mod ? a.mod + ' + ' : '') + (cap || 'espacio'))} — ${esc(_t(a.que))}"` : 'tabindex="-1" aria-hidden="true"'}>
                      <span>${esc(cap)}</span>${a && a.mod ? '<i class="kb-mod" aria-hidden="true"></i>' : ''}
                    </button>`;
          }).join('')}</div>`).join('')}
        </div>
        <p class="kb-cap" id="kb-cap"><span class="sx-dim">${esc(_t('Pasá por una tecla iluminada.'))}</span></p>`;

      const reasignar = ctrls.map(c => `
        <div class="sx-set" data-key="${esc(c.label)}">
          <div class="sx-set-t"><b>${esc(c.label)}</b><span>${esc(c.mode === 'press'
            ? _t('Abre el selector de terminal rápida.') : _t('Se mantiene apretada para dictar.'))}</span></div>
          <button class="kb-bind settings-keybind set-keybind" data-id="${esc(c.id)}" type="button">
            <span class="sx-dim">${c.mode === 'press' ? 'Ctrl +' : esc(_t('mantené'))}</span>
            <span class="settings-kbd">${esc(window.JarvisControls.label(c.id))}</span>
          </button>
          <button class="kb-reset set-keybind-reset" data-id="${esc(c.id)}" type="button"
                  title="${esc(_t('Restaurar al default'))}" aria-label="${esc(_t('Restaurar'))}">${icon('refresh', 13)}</button>
        </div>`).join('');

      const mouseSolo = ctrls.filter(c => !_bindingCode(c.id))
        .map(c => `<div><span class="kb-chip">${esc(window.JarvisControls.label(c.id))}</span> ${esc(c.label)} — ${esc(_t('es un botón del mouse, por eso no está en el mapa'))}</div>`).join('');

      b.innerHTML =
        blk('mapa', 'lo iluminado está ocupado', teclado, { wide: true, key: 'Atajos del workspace' }) +
        (reasignar ? blk('reasignar', 'los que cambiás', reasignar, { key: 'Atajos configurables' }) : '') +
        blk('sin tecla', 'mouse y combinaciones', `
          <div class="kb-otros">
            ${mouseSolo}
            <div><span class="kb-chip">${esc(_t('click derecho'))}</span> ${esc(_t('acciones del proyecto'))}</div>
            <div><span class="kb-chip">${esc(_t('arrastrar'))}</span> ${esc(_t('reordenar proyectos y terminales'))}</div>
            <div><span class="kb-chip">Ctrl + Shift + P</span> ${esc(_t('paleta de comandos'))}</div>
          </div>`);

      const cap = b.querySelector('#kb-cap');
      b.querySelectorAll('.kb-k.bound').forEach(k => {
        const mostrar = () => {
          const [tecla, que] = k.dataset.que.split(' — ');
          cap.innerHTML = `<span class="kb-cap-k">${esc(tecla)}</span> ${esc(que || '')}`;
        };
        k.addEventListener('pointerenter', mostrar);
        k.addEventListener('focus', mostrar);
        k.addEventListener('click', () => {
          const id = A[k.dataset.code]?.cfg;
          if (id) window.JarvisControls?.capturar?.(id);
        });
      });
      b.querySelectorAll('.kb-bind').forEach(x =>
        x.addEventListener('click', () => window.JarvisControls?.capturar?.(x.dataset.id)));
      b.querySelectorAll('.kb-reset').forEach(x =>
        x.addEventListener('click', () => { window.JarvisControls?.reset?.(x.dataset.id); pintar(); _pintarValores(); }));
    };
    // Asignación plana: cada visita reemplaza el callback anterior.
    window.JarvisControls && (window.JarvisControls.onCambio = () => { pintar(); _pintarValores(); });
    pintar();
  }

  /* ═══════════════════════════════════════════════════════════
     3 · APARIENCIA — banco de pruebas vivo + espectro de 24 temas
     ═══════════════════════════════════════════════════════════ */
  // [nombre visible, bg-0, bg-1, line-2, accent] — espeja los bloques
  // html[data-theme] de tokens.css, ORDENADO por rueda de color.
  const TEMA_META = {
    violeta:    ['Violeta',       'oklch(13% 0.018 300)',   'oklch(17% 0.02 300)',   'oklch(34% 0.024 300)', 'oklch(61% 0.22 293)'],
    medianoche: ['Medianoche',    'oklch(10% 0.016 290)',   'oklch(13.5% 0.018 290)','oklch(30% 0.022 290)', 'oklch(58% 0.21 288)'],
    crepusculo: ['Crepúsculo',    'oklch(12.5% 0.018 285)', 'oklch(16.5% 0.020 285)','oklch(33% 0.024 285)', 'oklch(74% 0.14 55)'],
    orquidea:   ['Orquídea',      'oklch(13% 0.018 320)',   'oklch(17% 0.020 320)',  'oklch(34% 0.024 320)', 'oklch(66% 0.21 320)'],
    sakura:     ['Sakura',        'oklch(12.5% 0.016 345)', 'oklch(16.5% 0.018 345)','oklch(33% 0.022 345)', 'oklch(83% 0.09 350)'],
    rosa:       ['Rosa neón',     'oklch(13% 0.018 330)',   'oklch(17% 0.020 330)',  'oklch(34% 0.024 330)', 'oklch(70% 0.19 350)'],
    rojo:       ['Rojo carmesí',  'oklch(13% 0.012 20)',    'oklch(17% 0.014 20)',   'oklch(34% 0.018 20)',  'oklch(65% 0.19 25)'],
    naranja:    ['Naranja',       'oklch(13% 0.014 50)',    'oklch(17% 0.016 50)',   'oklch(34% 0.020 50)',  'oklch(72% 0.17 50)'],
    oro:        ['Oro pálido',    'oklch(13% 0.011 90)',    'oklch(17% 0.013 90)',   'oklch(34% 0.017 90)',  'oklch(78% 0.09 90)'],
    lima:       ['Lima',          'oklch(13% 0.013 122)',   'oklch(17% 0.015 122)',  'oklch(34% 0.019 122)', 'oklch(82% 0.19 125)'],
    verde:      ['Verde carbón',  'oklch(12.5% 0.012 145)', 'oklch(16.5% 0.014 145)','oklch(33% 0.018 145)', 'oklch(78% 0.16 150)'],
    salvia:     ['Salvia',        'oklch(13% 0.008 155)',   'oklch(17% 0.010 155)',  'oklch(34% 0.013 155)', 'oklch(84% 0.07 160)'],
    bosque:     ['Bosque',        'oklch(12.5% 0.016 160)', 'oklch(16.5% 0.018 160)','oklch(33% 0.022 160)', 'oklch(78% 0.12 90)'],
    esmeralda:  ['Esmeralda',     'oklch(12.5% 0.013 168)', 'oklch(16.5% 0.015 168)','oklch(33% 0.019 168)', 'oklch(72% 0.15 168)'],
    aurora:     ['Aurora',        'oklch(12.5% 0.012 250)', 'oklch(16.5% 0.014 250)','oklch(33% 0.018 250)', 'oklch(80% 0.13 165)'],
    petroleo:   ['Petróleo',      'oklch(12.5% 0.016 195)', 'oklch(16.5% 0.018 195)','oklch(33% 0.022 195)', 'oklch(72% 0.15 35)'],
    oceano:     ['Océano',        'oklch(13% 0.014 210)',   'oklch(17% 0.016 210)',  'oklch(34% 0.020 210)', 'oklch(74% 0.13 200)'],
    neon:       ['Neón',          'oklch(9% 0.006 230)',    'oklch(12.5% 0.008 230)','oklch(30% 0.014 230)', 'oklch(80% 0.15 215)'],
    hielo:      ['Hielo',         'oklch(13% 0.008 240)',   'oklch(17% 0.009 240)',  'oklch(34% 0.011 240)', 'oklch(85% 0.06 240)'],
    azul:       ['Azul lineal',   'oklch(13.5% 0.012 250)', 'oklch(17.5% 0.014 250)','oklch(34% 0.018 250)', 'oklch(65% 0.16 255)'],
    tinta:      ['Tinta',         'oklch(11% 0.005 270)',   'oklch(15% 0.006 270)',  'oklch(32% 0.009 270)', 'oklch(90% 0.04 85)'],
    grafito:    ['Grafito',       'oklch(13% 0 0)',         'oklch(17% 0 0)',        'oklch(34% 0 0)',       'oklch(83% 0 0)'],
    papel:      ['Papel',         'oklch(94.5% 0.014 92)',  'oklch(97% 0.008 94)',   'oklch(82% 0.015 90)',  'oklch(46% 0.09 55)'],
    alba:       ['Alba',          'oklch(94.5% 0.010 245)', 'oklch(97% 0.006 248)',  'oklch(82% 0.013 248)', 'oklch(50% 0.19 268)'],
  };
  const ORDEN_TEMAS = Object.keys(TEMA_META);
  const _esClaro = (t) => !!window.JarvisThemes?.esTemaClaro?.(t);
  const _temaActual = () => window.JarvisThemes?.actual?.() || 'violeta';

  // Banco de pruebas: mini-workspace pintado con SUS variables --bk-*, que por
  // defecto son los tokens reales. La vista previa las pisa inline.
  const _BANCO = `
    <div class="ap-banco" aria-hidden="true">
      <span class="ap-strip"><i class="on"></i><i></i><i></i><i></i></span>
      <span class="ap-main">
        <span class="ap-bar"><em></em><em class="w"></em></span>
        <span class="ap-grid">
          <span class="ap-term on"><b></b><s></s><s class="w"></s><s></s></span>
          <span class="ap-term"><b></b><s></s><s class="w"></s></span>
        </span>
      </span>
      <span class="ap-dock"><i></i><i></i><i></i></span>
    </div>`;

  function _renderApariencia(b) {
    const act = _temaActual();
    const temas = ORDEN_TEMAS.filter(t => (window.JarvisThemes?.TEMAS || ORDEN_TEMAS).includes(t));
    const tinte = window.JarvisThemes?.tinte?.() || { matiz: 0, saturacion: 100, profundidad: 0 };
    const idioma = window.JarvisI18n?.lang?.() || 'es';

    const espectro = `
      <div class="ap-spec" id="ap-spec" role="radiogroup" aria-label="${esc(_t('Tema de color'))}">
        ${temas.map(t => {
          const [n, bg0, bg1, ln, ac] = TEMA_META[t];
          return `<button class="ap-t${t === act ? ' on' : ''}${_esClaro(t) ? ' claro' : ''}" type="button"
                    role="radio" aria-checked="${t === act}" data-tema="${t}" title="${esc(n)}"
                    style="--t-bg:${bg0};--t-pn:${bg1};--t-ln:${ln};--t-ac:${ac}">
                    <span class="ap-t-p" aria-hidden="true"></span>
                    <span class="ap-t-n">${esc(n)}</span>
                  </button>`;
        }).join('')}
      </div>
      <p class="ap-spec-cap" id="ap-cap"></p>`;

    const sliders = `
      <div class="ap-ton" data-key="Tonalidad">
        ${[['h', 'matiz', -40, 40, 1, tinte.matiz, '°'],
           ['s', 'saturación', 50, 150, 5, tinte.saturacion, '%'],
           ['p', 'profundidad', -3, 3, 1, tinte.profundidad, '']].map(([k, l, mn, mx, st, v, u]) => `
          <label class="ap-ton-r">
            <span class="ap-ton-l">${esc(_t(l))}</span>
            <span class="sx-slider">
              <input type="range" id="ap-${k}" min="${mn}" max="${mx}" step="${st}" value="${v}" aria-label="${esc(_t(l))}">
              <output id="ap-${k}-o"></output>
            </span>
          </label>`).join('')}
        <button class="sx-btn gho sm" id="ap-reset" type="button" hidden>${esc(_t('Restablecer'))}</button>
      </div>`;

    // Escala de la app: un solo número (%) que agranda o achica TODA la
    // interfaz. El motor vive en shared/escala.js (zoom en <html>); acá está
    // la regla graduada con el detente en 100%.
    const escAct = window.JarvisEscala?.actual?.() ?? 100;
    const escMin = window.JarvisEscala?.MIN ?? 70;
    const escMax = window.JarvisEscala?.MAX ?? 150;
    const escDef = window.JarvisEscala?.DEF ?? 100;
    // Detente del 100% sobre la pista: el centro del pulgar (15px) recorre de
    // 7.5px a W−7.5px, así que la marca va en fracción·W + (7.5 − fracción·15).
    const fDef = (escDef - escMin) / (escMax - escMin);
    const detente = `calc(${(fDef * 100).toFixed(2)}% + ${(7.5 - fDef * 15).toFixed(2)}px)`;
    const zoom = `
      <div class="ap-esc" data-key="Escala de la app">
        <label class="ap-ton-r ap-esc-r">
          <span class="ap-ton-l">${esc(_t('tamaño'))}</span>
          <span class="sx-slider">
            <span class="ap-esc-track" style="--esc-det:${detente}">
              <input type="range" id="ap-esc" min="${escMin}" max="${escMax}"
                     step="${window.JarvisEscala?.PASO ?? 5}" value="${escAct}"
                     aria-label="${esc(_t('Escala de la app'))}">
              <span class="ap-esc-ends" aria-hidden="true"><i>${escMin}%</i><i>${escDef}%</i><i>${escMax}%</i></span>
            </span>
            <output id="ap-esc-o"></output>
          </span>
        </label>
        <button class="sx-btn gho sm" id="ap-esc-reset" type="button" hidden>${esc(_t('Restablecer'))}</button>
        <p class="ap-esc-cap">${esc(_t('Agranda o achica el workspace entero: franja, barra, cards, panel y el texto de las terminales. Es aparte del zoom del navegador y queda guardado.'))}</p>
      </div>`;

    b.innerHTML =
      blk('banco', 'el workspace, en vivo', _BANCO, { wide: true }) +
      blk('temas', `${temas.length} ${_t('· ordenados por rueda de color')}`, espectro, { wide: true, key: 'Tema de color' }) +
      blk('tonalidad', 'ajuste fino del tema', sliders, { key: 'Tonalidad' }) +
      blk('escala', 'qué tan grande se ve todo', zoom, { key: 'Escala de la app' }) +
      blk('interfaz', 'idioma y modo',
        setRow('Idioma', 'Toda la interfaz, al instante.',
          `<div class="sx-seg" id="ap-lang" role="radiogroup" aria-label="${esc(_t('Idioma'))}">
             <button type="button" data-l="es" class="${idioma === 'es' ? 'on' : ''}">Español</button>
             <button type="button" data-l="en" class="${idioma === 'en' ? 'on' : ''}">English</button>
           </div>`, 'Idioma de la interfaz') +
        setRow('Auto-iniciar el preview móvil',
          'En proyectos Expo, abre la pestaña móvil cuando detecta el Metro que levantó el agente.',
          sw('ap-mob', localStorage.getItem('jarvis.autoMobilePreview') !== '0', _t('Auto-iniciar el preview móvil')),
          'Auto-iniciar el preview móvil'),
        { key: 'Interfaz' });

    // ── Espectro: hover = previsualizar en el banco, click = elegir (y ahí se
    //    despliega). Previsualizar NO toca el data-theme del documento: pisa las
    //    variables --bk-* del banco, así el tema real no cambia hasta el click.
    const cap = b.querySelector('#ap-cap');
    const banco = b.querySelector('.ap-banco');
    let tPrevia = 0;
    const pintarCap = (t, previa) => {
      const [n] = TEMA_META[t] || [t];
      cap.innerHTML = `<b>${esc(n)}</b> <span class="sx-dim">${_esClaro(t) ? _t('claro') : _t('oscuro')} · ${esc(t)}</span>`
        + (previa ? ` <span class="ap-previa">${esc(_t('vista previa — click para aplicarlo'))}</span>` : '');
    };
    const verPrevia = (t) => {
      const m = TEMA_META[t]; if (!m || !banco) return;
      banco.style.setProperty('--bk-bg', m[1]);
      banco.style.setProperty('--bk-pn', m[2]);
      banco.style.setProperty('--bk-ln', m[3]);
      banco.style.setProperty('--bk-ac', m[4]);
      banco.classList.toggle('previa', t !== _temaActual());
      pintarCap(t, t !== _temaActual());
    };
    const soltarPrevia = () => {
      clearTimeout(tPrevia);
      ['--bk-bg', '--bk-pn', '--bk-ln', '--bk-ac'].forEach(p => banco?.style.removeProperty(p));
      banco?.classList.remove('previa');
      pintarCap(_temaActual(), false);
    };
    pintarCap(act, false);
    b.querySelectorAll('.ap-t').forEach(x => {
      x.addEventListener('pointerenter', () => {
        clearTimeout(tPrevia);
        tPrevia = setTimeout(() => verPrevia(x.dataset.tema), 70);
      });
      x.addEventListener('focus', () => verPrevia(x.dataset.tema));
      x.addEventListener('click', () => {
        clearTimeout(tPrevia);
        window.JarvisThemes?.aplicar?.(x.dataset.tema);
        b.querySelectorAll('.ap-t').forEach(y => {
          const on = y === x;
          y.classList.toggle('on', on);
          y.setAttribute('aria-checked', on);
        });
        soltarPrevia();
        _pintarValores();
      });
    });
    b.querySelector('#ap-spec').addEventListener('pointerleave', soltarPrevia);

    // ── Tonalidad ──
    const h = b.querySelector('#ap-h'), s = b.querySelector('#ap-s'), p = b.querySelector('#ap-p');
    const reset = b.querySelector('#ap-reset');
    const leer = () => ({ matiz: +h.value, saturacion: +s.value, profundidad: +p.value });
    const pintarTon = () => {
      const t = leer();
      b.querySelector('#ap-h-o').textContent = `${t.matiz > 0 ? '+' : ''}${t.matiz}°`;
      b.querySelector('#ap-s-o').textContent = `${t.saturacion}%`;
      b.querySelector('#ap-p-o').textContent = `${t.profundidad > 0 ? '+' : ''}${t.profundidad}`;
      reset.hidden = t.matiz === 0 && t.saturacion === 100 && t.profundidad === 0;
    };
    [h, s, p].forEach(el => {
      // input = aplicación viva silenciosa · change = commit + theme-changed
      el.addEventListener('input', () => { pintarTon(); window.JarvisThemes?.setTinte?.(leer(), { silencioso: true }); });
      el.addEventListener('change', () => window.JarvisThemes?.setTinte?.(leer()));
    });
    reset.addEventListener('click', () => {
      h.value = 0; s.value = 100; p.value = 0;
      pintarTon(); window.JarvisThemes?.setTinte?.(leer());
    });
    pintarTon();

    // ── Escala de la app ──
    // El slider se aplica al SOLTAR (change), no en cada input: el zoom cambia
    // el tamaño físico del propio slider bajo el dedo, y aplicarlo por frame
    // hace que el control se escape del cursor (arrastre pegajoso, valores que
    // rebotan). Con las flechas del teclado, change dispara en cada tecla, así
    // que ahí el ajuste igual se ve en vivo.
    const esl = b.querySelector('#ap-esc'), eslOut = b.querySelector('#ap-esc-o');
    const eslReset = b.querySelector('#ap-esc-reset');
    const pintarEsc = () => {
      eslOut.textContent = window.JarvisEscala?.etiqueta?.(esl.value) ?? `${esl.value}%`;
      eslReset.hidden = Number(esl.value) === (window.JarvisEscala?.DEF ?? 100);
    };
    esl.addEventListener('input', pintarEsc);
    esl.addEventListener('change', () => { pintarEsc(); window.JarvisEscala?.aplicar?.(esl.value); });
    eslReset.addEventListener('click', () => {
      esl.value = String(window.JarvisEscala?.DEF ?? 100);
      pintarEsc(); window.JarvisEscala?.aplicar?.(esl.value);
    });
    pintarEsc();

    b.querySelector('#ap-lang').addEventListener('click', (e) => {
      const btn = e.target.closest('button'); if (!btn) return;
      b.querySelectorAll('#ap-lang button').forEach(x => x.classList.toggle('on', x === btn));
      window.JarvisI18n?.setLang?.(btn.dataset.l);
    });
    b.querySelector('#ap-mob').addEventListener('change', (e) =>
      localStorage.setItem('jarvis.autoMobilePreview', e.target.checked ? '1' : '0'));
  }

  /* ═══════════════════════════════════════════════════════════
     4 · CUENTAS — conmutador (un CLI por fila, sus cuentas como
     botones de un selector). La acción real acá es CAMBIAR de cuenta.
     El motor (fetch, alta OAuth con sus 4 flujos, renombrar, quitar)
     es el mismo de siempre; lo que cambió es la forma.
     ═══════════════════════════════════════════════════════════ */
  async function _ctaFetch(url, method = 'GET', body) {
    const opts = { method, headers: {} };
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    const res = await fetch(url, opts);
    let data = null;
    try { data = await res.json(); } catch {}
    if (!res.ok) throw new Error((data && data.detail) || _t('Error del servidor'));
    return data;
  }

  function _renderCuentas(b) {
    let _abiertaId = null;   // detalle desplegado (cli:id)
    let _verMail = false;    // el correo arranca enmascarado; el ojo lo revela
    let _clis = [];

    b.innerHTML = blk('conmutador', 'cargando…', `<div class="cta-cuerpo" id="cta-cuerpo">
        <div class="cta-cargando">${icon('loader', 15)} ${esc(_t('Cargando cuentas…'))}</div></div>`,
      { wide: true, key: 'Conectar cuenta nueva' });
    const cuerpo = b.querySelector('#cta-cuerpo');

    const _mask = (e) => {
      if (!e) return '';
      const at = e.indexOf('@');
      return at > 0 ? `${e[0]}•••${e.slice(at)}` : '•••••••••';
    };
    const _rel = (iso) => {
      if (!iso) return '';
      const d = new Date(iso); if (isNaN(d.getTime())) return '';
      const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
      if (s < 60) return _t('recién');
      if (s < 3600) return _t('hace {n} min').replace('{n}', Math.floor(s / 60));
      if (s < 86400) return _t('hace {n} h').replace('{n}', Math.floor(s / 3600));
      const dd = Math.floor(s / 86400);
      return dd < 30 ? _t('hace {n} d').replace('{n}', dd) : d.toLocaleDateString();
    };
    const _buscar = (id) => {
      for (const cli of _clis) {
        const f = (cli.cuentas || []).find(x => String(x.id) === String(id));
        if (f) return f;
      }
      return null;
    };
    const _tipoLabel = (t) => (_clis.find(c => c.tipo === t)?.label) || t || _t('ese CLI');

    async function _conectarNueva(cli) {
      if (!cli) return;
      window.toast?.(_t('Abriendo el login de {cli}…').replace('{cli}', cli.label), 'info');
      let r;
      try { r = await _ctaFetch('/api/cuentas/login/iniciar', 'POST', { tipo: cli.tipo }); }
      catch (e) { window.toast?.(e?.message || _t('No se pudo abrir el login'), 'error'); return; }
      _altaModal(cli, r || {});
    }

    // Modal de alta: cuatro modos (manual, device-code, pegar código, callback).
    // Markup y flujo intactos respecto del diseño anterior — solo cambia dónde
    // se dispara desde la UI nueva.
    function _altaModal(cli, r) {
      if (typeof _altaCerrar === 'function') _altaCerrar();
      const paste = !!r.paste;
      const manual = !!r.manual;
      const codigo = r.codigo || null;
      const ov = document.createElement('div');
      ov.className = 'cta-alta-overlay';
      const introHTML = manual
        ? `<span>El login de</span> <b>${esc(cli.label)}</b> <span>se elige en su propia pantalla (proveedor, región, plan…): abrí la terminal acá abajo (o corré el comando en cualquier otra) y completá los pasos. Apenas termines, detecto la cuenta acá y la guardo sola.</span>`
        : codigo
        ? `Para conectar <b>${esc(cli.label)}</b>: iniciá sesión en la página de login con la cuenta que querés y confirmá que te muestre este código. La página se abre sola; si no, tocá el botón de abajo.`
        : `Se abrió la página de login de <b>${esc(cli.label)}</b> en otra pestaña. Entrá con la cuenta nueva.${r.url ? ` ¿No se abrió? <a href="${esc(r.url)}" target="_blank" rel="noopener">Abrir de nuevo</a>.` : ''}`;
      const cuerpoHTML = manual ? `
            <button type="button" class="cta-alta-link cta-alta-term" data-term>${icon('terminal', 14)} Abrir una terminal con el login</button>
            <div class="cta-alta-codigo">
              <span class="cta-alta-codigo-lbl">O corrélo vos en cualquier terminal</span>
              <button type="button" class="cta-alta-codigo-val cta-alta-cmd" data-copy-cmd title="Copiar comando">${esc(r.comando || '')} ${icon('copy', 13)}</button>
            </div>
            <div class="cta-alta-wait">${icon('loader', 15)} Esperando que completes el login…</div>`
        : codigo ? `
            ${r.url ? `<a class="cta-alta-link" href="${esc(r.url)}" target="_blank" rel="noopener">${icon('external-link', 14)} Abrir la página de login</a>` : ''}
            <div class="cta-alta-codigo">
              <span class="cta-alta-codigo-lbl">Código de un solo uso</span>
              <button type="button" class="cta-alta-codigo-val" data-copy-codigo title="Copiar código">${esc(codigo)} ${icon('copy', 13)}</button>
            </div>
            <div class="cta-alta-wait">${icon('loader', 15)} Esperando que completes el login…</div>`
        : paste ? `
            <label class="cta-alta-lbl">${esc(_t('Pegá el código que te da {cli}:').replace('{cli}', cli.label))}</label>
            <div class="cta-alta-row">
              <input type="text" class="cta-alta-input" id="cta-alta-codigo" placeholder="código de autorización" spellcheck="false" autocomplete="off">
              <button class="sx-btn pri" data-codigo type="button">Conectar</button>
            </div>`
        : `
            <div class="cta-alta-wait">${icon('loader', 15)} Esperando que completes el login…</div>`;
      ov.innerHTML = `
        <div class="cta-alta" role="dialog" aria-modal="true" aria-label="Conectar cuenta">
          <div class="cta-alta-head">
            <span class="cta-cli-logo">${window.cliLogo ? window.cliLogo(cli.tipo, 20) : ''}</span>
            <h3>${esc(_t('Conectando {cli}').replace('{cli}', cli.label))}</h3>
            <button class="cta-alta-x" data-x type="button" aria-label="Cerrar">${icon('x', 16)}</button>
          </div>
          <p class="cta-alta-intro">${introHTML}</p>
          ${cuerpoHTML}
          <div class="cta-alta-estado" id="cta-alta-estado"></div>
        </div>`;
      document.body.appendChild(ov);
      ov.querySelector('[data-copy-codigo]')?.addEventListener('click', (e) => {
        navigator.clipboard?.writeText(codigo).then(() => {
          e.currentTarget.classList.add('copiado');
          window.toast?.('Código copiado', 'info');
        }).catch(() => {});
      });
      ov.querySelector('[data-copy-cmd]')?.addEventListener('click', (e) => {
        navigator.clipboard?.writeText(r.comando || '').then(() => {
          e.currentTarget.classList.add('copiado');
          window.toast?.('Comando copiado', 'info');
        }).catch(() => {});
      });
      const estado = ov.querySelector('#cta-alta-estado');
      let exito = false;
      const cerrar = () => {
        ov.remove(); document.removeEventListener('keydown', onKey, true);
        _altaCerrar = null; _altaResolver = null;
        // Cerrar aborta el login en curso (corta watcher + PTY oculto). El modo
        // manual NO cancela: el usuario puede estar a mitad del wizard.
        if (!manual && !exito) {
          fetch('/api/cuentas/login/cancelar', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tipo: cli.tipo }),
          }).catch(() => {});
        }
      };
      const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cerrar(); } };
      document.addEventListener('keydown', onKey, true);
      ov.addEventListener('mousedown', (e) => { if (e.target === ov) cerrar(); });
      ov.querySelector('[data-x]')?.addEventListener('click', cerrar);

      ov.querySelector('[data-term]')?.addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        if (!_projectId) { window.toast?.('Abrí un proyecto para lanzar la terminal', 'error'); return; }
        btn.disabled = true;
        try {
          const creadas = await _ctaFetch(`/api/projects/${_projectId}/terminals/batch`, 'POST', {
            terminales: [{ nombre: `Login ${cli.label}`, tipo_ia: 'manual' }],
            comando: r.comando || '',
          });
          (creadas || []).forEach(t => window.agregarTarjetaTerminal?.(t));
          window.actualizarVista?.();
          window.toast?.('Completá el login en la terminal nueva — la cuenta se detecta y se guarda sola', 'info', 7000);
          cerrar();
          window.JarvisSettings?.close?.();
        } catch (err) {
          btn.disabled = false;
          window.toast?.(err?.message || 'No se pudo abrir la terminal', 'error');
        }
      });

      const enviarCodigo = async () => {
        const inp = ov.querySelector('#cta-alta-codigo');
        const cod = (inp?.value || '').trim();
        if (!cod) { inp?.focus(); return; }
        estado.textContent = 'Verificando el código…';
        try { await _ctaFetch('/api/cuentas/login/codigo', 'POST', { tipo: cli.tipo, codigo: cod }); }
        catch (e) { estado.textContent = e?.message || 'No se pudo enviar el código'; return; }
        estado.innerHTML = `${icon('loader', 13)} Conectando… detecto la cuenta y la guardo sola.`;
      };
      ov.querySelector('[data-codigo]')?.addEventListener('click', enviarCodigo);
      const inp = ov.querySelector('#cta-alta-codigo');
      inp?.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); enviarCodigo(); } });
      inp?.focus();

      _altaCerrar = cerrar;
      _altaResolver = (data) => {
        if (data && data.tipo && data.tipo !== cli.tipo) return;
        exito = true;
        if (estado) estado.innerHTML = `<span class="cta-alta-ok">${icon('check', 14)} ¡Cuenta agregada!</span>`;
        setTimeout(cerrar, 1300);
      };
    }

    const _logo = (t) => (window.cliLogo ? window.cliLogo(t, 18) : '');

    function _filaCli(c) {
      const cs = c.cuentas || [];
      const abierta = cs.find(a => `${c.tipo}:${a.id}` === _abiertaId);
      return `
      <section class="ct-cli${abierta ? ' abierta' : ''}" data-tipo="${esc(c.tipo)}">
        <div class="ct-fila">
          <span class="ct-logo">${_logo(c.tipo)}</span>
          <span class="ct-cli-n">
            <b>${esc(c.label)}</b>
            <span class="ct-cli-s sx-mono">${c.logueado
              ? (cs.length ? `${cs.length} ${cs.length === 1 ? _t('cuenta') : _t('cuentas')}` : _t('sesión sin guardar'))
              : _t('sin sesión')}</span>
          </span>
          <span class="ct-sw">
            ${cs.map(a => `
              <button class="ct-chip${a.activa ? ' on' : ''}" type="button"
                      data-act="${a.activa ? 'detalle' : 'usar'}" data-id="${a.id}" data-tipo="${esc(c.tipo)}"
                      aria-pressed="${!!a.activa}"
                      title="${a.activa ? esc(_t('En uso — ver detalle')) : esc(_t('Cambiar a esta cuenta'))}">
                ${a.activa ? '<i class="ct-dot" aria-hidden="true"></i>' : ''}
                <span>${esc(a.label || _t('Sin nombre'))}</span>
              </button>`).join('')}
            ${c.home_sin_guardar
              ? `<button class="ct-chip guardar" type="button" data-act="vincular" data-tipo="${esc(c.tipo)}"
                         title="${esc(_t('Hay una sesión activa sin guardar'))}">${icon('alert', 13)}<span>${esc(_t('Guardar sesión'))}</span></button>`
              : ''}
            <button class="ct-chip mas" type="button" data-act="conectar" data-tipo="${esc(c.tipo)}"
                    title="${esc(c.como_loguear || _t('Conectar una cuenta nueva'))}">
              ${icon('plus', 13)}<span>${esc(cs.length ? _t('Conectar') : (c.logueado ? _t('Guardar esta sesión') : _t('Conectar')))}</span>
            </button>
          </span>
        </div>
        ${abierta ? `
          <div class="ct-det">
            <dl>
              <dt>${esc(_t('correo'))}</dt>
              <dd class="sx-mono ct-mail-dd">${abierta.email ? `
                <button class="ct-copy" data-act="copy" data-id="${abierta.id}" type="button"
                        title="${esc(_t('Copiar correo'))}">${esc(_verMail ? abierta.email : _mask(abierta.email))}</button>
                <button class="ct-ojo" data-act="vermail" type="button"
                        aria-pressed="${_verMail}"
                        aria-label="${esc(_verMail ? _t('Ocultar el correo') : _t('Mostrar el correo'))}"
                        title="${esc(_verMail ? _t('Ocultar el correo') : _t('Mostrar el correo'))}">${icon(_verMail ? 'eye-off' : 'eye', 14)}</button>`
                : esc(_t('sin correo detectado'))}</dd>
              <dt>${esc(_t('vinculada'))}</dt><dd class="sx-mono">${esc(_rel(abierta.created_at) || '—')}</dd>
              <dt>login</dt><dd class="sx-mono">${esc(c.como_loguear || '—')}</dd>
            </dl>
            <div class="ct-acc">
              <button class="sx-btn sm" data-act="renombrar" data-id="${abierta.id}" type="button">${esc(_t('Renombrar'))}</button>
              <button class="sx-btn sm" data-act="recapturar" data-id="${abierta.id}" type="button"
                      title="${esc(_t('Guardar la sesión actual del CLI en esta cuenta'))}">${esc(_t('Actualizar sesión'))}</button>
              <button class="sx-btn sm ct-del" data-act="eliminar" data-id="${abierta.id}" type="button">${esc(_t('Quitar'))}</button>
            </div>
          </div>` : ''}
      </section>`;
    }

    function pintar() {
      const conCuentas = _clis.filter(c => (c.cuentas || []).length);
      const sinCuentas = _clis.filter(c => !(c.cuentas || []).length);
      const total = _clis.reduce((a, c) => a + (c.cuentas || []).length, 0);

      const dormidos = sinCuentas.length ? `
        <section class="ct-cli ct-off">
          <div class="ct-fila">
            <span class="ct-logo">${icon('unplug', 16)}</span>
            <span class="ct-cli-n">
              <b>${esc(_t('Sin cuentas guardadas'))}</b>
              <span class="ct-cli-s sx-mono">${esc(_t('sin volver a loguearte'))}</span>
            </span>
            <span class="ct-sw">
              ${sinCuentas.map(c => `
                <button class="ct-chip mas" type="button" data-act="conectar" data-tipo="${esc(c.tipo)}"
                        title="${esc(c.como_loguear || '')}">
                  <span class="ct-cli-logo">${_logo(c.tipo)}</span><span>${esc(c.label)}</span>
                </button>`).join('')}
            </span>
          </div>
        </section>` : '';

      const etiqueta = b.querySelector('.sx-blk-l span');
      if (etiqueta) etiqueta.textContent = `${total} ${total === 1 ? _t('cuenta') : _t('cuentas')} · ${conCuentas.length}/${_clis.length} CLIs`;

      cuerpo.innerHTML = `<div class="ct-wrap">${conCuentas.map(_filaCli).join('')}${dormidos}</div>
        <p class="ct-pie">${esc(_t('Tocá una cuenta para pasarte a ella al instante — no hay que volver a loguearse. La que está en uso abre su detalle.'))}</p>`;
      cuerpo.querySelectorAll('[data-act]').forEach(el =>
        el.addEventListener('click', () => accion(el.dataset)));
    }

    async function recargar() {
      try { const data = await _ctaFetch('/api/cuentas'); _clis = data.clis || []; }
      catch { cuerpo.innerHTML = `<p class="cta-err">${esc(_t('No se pudieron cargar las cuentas.'))}</p>`; return; }
      _res.cuentas = _clis.reduce((a, c) => a + (c.cuentas || []).length, 0);
      _pintarValores();
      pintar();
    }

    async function accion(ds) {
      const id = ds.id;
      if (ds.act === 'detalle') {
        const clave = `${ds.tipo}:${id}`;
        _abiertaId = _abiertaId === clave ? null : clave;
        _verMail = false;          // cada detalle se abre con el correo tapado
        pintar(); return;
      }
      if (ds.act === 'vermail') { _verMail = !_verMail; pintar(); return; }
      if (ds.act === 'copy') {
        try { await navigator.clipboard.writeText(_buscar(id)?.email || ''); window.toast?.(_t('Correo copiado')); }
        catch { window.toast?.(_t('No se pudo copiar'), 'error'); }
        return;
      }
      if (ds.act === 'conectar') { await _conectarNueva(_clis.find(c => c.tipo === ds.tipo)); return; }
      try {
        if (ds.act === 'vincular') {
          const label = await window.pedirTexto?.('Ponele un nombre para reconocerla (ej. «Personal», «Trabajo»).',
            { titulo: 'Vincular cuenta actual', placeholder: 'Personal', confirmText: 'Vincular' });
          if (!label) return;
          await _ctaFetch('/api/cuentas', 'POST', { tipo: ds.tipo, label });
          window.toast?.('Cuenta vinculada');
        } else if (ds.act === 'usar') {
          const r = await _ctaFetch(`/api/cuentas/${id}/usar`, 'POST');
          window.toast?.(_t('Activá: {label} — tus terminales de {cli} la usan en el próximo mensaje')
            .replace('{label}', r?.label || 'lista').replace('{cli}', _tipoLabel(r?.tipo)));
          _abiertaId = null;
        } else if (ds.act === 'recapturar') {
          await _ctaFetch(`/api/cuentas/${id}/recapturar`, 'POST');
          window.toast?.('Sesión actual guardada en esta cuenta');
        } else if (ds.act === 'renombrar') {
          const label = await window.pedirTexto?.('Nuevo nombre para la cuenta:',
            { titulo: 'Renombrar cuenta', valor: _buscar(id)?.label || '', confirmText: 'Guardar' });
          if (!label) return;
          await _ctaFetch(`/api/cuentas/${id}`, 'PUT', { label });
        } else if (ds.act === 'eliminar') {
          const ok = await window.confirmar?.('Se borra el perfil y su snapshot guardado (no desloguea la cuenta del CLI). ¿Quitar?',
            { titulo: 'Quitar cuenta', confirmText: 'Quitar' });
          if (!ok) return;
          await _ctaFetch(`/api/cuentas/${id}`, 'DELETE');
          _abiertaId = null;
        }
      } catch (e) {
        window.toast?.(e?.message || 'No se pudo completar la acción', 'error');
      }
      recargar();
    }

    recargar();
  }

  /* ═══════════════════════════════════════════════════════════
     5 · EXTENSIONES — el markup .ps-* lo cablea JarvisSkills.montar()
     (workspace.js): se re-viste, no se reescribe.
     ═══════════════════════════════════════════════════════════ */
  function _renderSkills(b) {
    b.innerHTML = blk('rack', 'plugins del proyecto y skills del repo', `
      <div class="ps-toolbar">
        <div class="ps-tabs">
          <button class="ps-tab activo" data-tab="instalados">${esc(_t('Mis Plugins'))}</button>
          <button class="ps-tab" data-tab="marketplace">${esc(_t('Marketplace'))}</button>
        </div>
        <label class="ps-buscar">
          ${icon('search', 13)}
          <input id="ps-buscar" type="text" placeholder="${esc(_t('Buscar plugin o skill…'))}" autocomplete="off" spellcheck="false" aria-label="${esc(_t('Buscar plugin o skill'))}">
        </label>
      </div>
      <div class="modal-body-ps">
        <div class="ps-panel activo" data-panel="instalados">
          <section class="ps-section">
            <header class="ps-section-head">
              <span class="ps-section-icon ps-icon-plugin">${icon('plug', 14)}</span>
              <h3>${esc(_t('Plugins instalados'))}</h3>
              <span class="ps-section-sub" id="ps-plugins-count">0</span>
            </header>
            <div class="ps-list" id="ps-plugins-instalados"><div class="ps-empty">${esc(_t('Cargando…'))}</div></div>
          </section>
          <section class="ps-section">
            <header class="ps-section-head">
              <span class="ps-section-icon ps-icon-skill">${icon('file', 14)}</span>
              <h3>${esc(_t('Skills del proyecto'))}</h3>
              <span class="ps-section-sub" id="ps-skills-count">0</span>
              <span class="ps-spacer"></span>
              <button class="sx-btn sm ps-mini-btn" id="ps-btn-new-skill">+ ${esc(_t('Nueva'))}</button>
            </header>
            <div class="ps-skill-path" id="ps-skill-path">.claude/skills/</div>
            <div class="ps-list" id="ps-skills-md"><div class="ps-empty">${esc(_t('Cargando…'))}</div></div>
          </section>
        </div>
        <div class="ps-panel" data-panel="marketplace">
          <section class="ps-section">
            <header class="ps-section-head">
              <span class="ps-section-icon ps-icon-plugin">${icon('plug', 14)}</span>
              <h3>${esc(_t('Plugins disponibles'))}</h3>
              <span class="ps-section-sub" id="ps-marketplace-count">0</span>
            </header>
            <div class="ps-grid" id="ps-marketplace"><div class="ps-empty">${esc(_t('Cargando marketplace…'))}</div></div>
          </section>
        </div>
      </div>`, { wide: true, key: 'Plugins instalados' });
    window.JarvisSkills?.montar?.();   // bridge expuesto desde workspace.js
    _wireSkillsBuscar(b);
  }

  // Filtra por texto las TRES listas. Las renderiza workspace.js DESPUÉS y las
  // re-renderiza al togglear/instalar, así que un observer re-aplica el filtro.
  function _wireSkillsBuscar(b) {
    const input = b.querySelector('#ps-buscar');
    if (!input) return;
    const zonas = [
      { cont: b.querySelector('#ps-plugins-instalados'), sel: '.ps-item' },
      { cont: b.querySelector('#ps-skills-md'),          sel: '.ps-item' },
      { cont: b.querySelector('#ps-marketplace'),        sel: '.ps-card' },
    ].filter(z => z.cont);
    const aplicar = () => {
      const q = input.value.trim().toLowerCase();
      for (const z of zonas) {
        const items = z.cont.querySelectorAll(z.sel);
        let visibles = 0;
        items.forEach(el => {
          const hit = !q || el.textContent.toLowerCase().includes(q);
          el.classList.toggle('ps-filtrado', !hit);
          if (hit) visibles++;
        });
        let aviso = z.cont.querySelector('.ps-filtro-vacio');
        if (q && items.length && !visibles) {
          if (!aviso) {
            aviso = document.createElement('div');
            aviso.className = 'ps-filtro-vacio';
            z.cont.appendChild(aviso);
          }
          aviso.innerHTML = `<span>${esc(_t('Sin resultados para'))}</span> <b>«${esc(input.value.trim())}»</b>`;
        } else { aviso?.remove(); }
      }
    };
    input.addEventListener('input', aplicar);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && input.value) { e.preventDefault(); e.stopPropagation(); input.value = ''; aplicar(); }
    });
    const mo = new MutationObserver((muts) => {
      if (!input.value.trim()) return;
      const ajeno = muts.some(m =>
        [...m.addedNodes, ...m.removedNodes].some(n => !(n.nodeType === 1 && n.classList?.contains('ps-filtro-vacio'))));
      if (ajeno) aplicar();
    });
    zonas.forEach(z => mo.observe(z.cont, { childList: true }));
  }

  /* ═══════════════════════════════════════════════════════════
     6 · MEMORIA — consola (antes: una fila con un botón «Abrir»)
     ═══════════════════════════════════════════════════════════ */
  function _renderMemoria(b) {
    b.innerHTML = blk('pulso', 'de un vistazo',
      `<div class="cta-cargando">${icon('loader', 15)} ${esc(_t('Leyendo la memoria del proyecto…'))}</div>`,
      { wide: true, key: 'Explorar memoria' });
    if (!_projectId) { b.innerHTML = blk('memoria', '', `<div class="sx-empty"><b>${esc(_t('Sin proyecto abierto'))}</b></div>`, { wide: true }); return; }

    const pid = _projectId;
    Promise.allSettled([
      fetch(`/api/projects/${pid}/memory/salud`).then(r => r.json()),
      fetch(`/api/projects/${pid}/memory`).then(r => r.json()),
    ]).then(([s, m]) => {
      if (!_abierta || _seccion !== 'memoria' || _projectId !== pid) return;
      const sal = s.status === 'fulfilled' ? (s.value || {}) : {};
      const mem = (m.status === 'fulfilled' ? (m.value?.memorias || []) : []);
      _pintarMemoria(b, sal, mem);
    });
  }

  function _pintarMemoria(b, sal, mem) {
    const alt = sal.altimetro || {};
    const lec = sal.lecciones || {};
    const hayTel = (alt.inyecciones || 0) > 0;
    const tasa = hayTel ? Math.round((alt.tasa_lectura || 0) * ((alt.tasa_lectura || 0) > 1 ? 1 : 100)) : null;

    const problemas = [
      ['enlaces rotos', (sal.rotos || []).length, 'er'],
      ['citas muertas', (sal.citas_muertas || []).length, 'er'],
      ['contrato incompleto', (sal.contrato || []).length, 'wk'],
      ['huérfanas', (sal.huerfanas || []).length, 'wk'],
      ['en cuarentena', (sal.cuarentena || []).length, 'wk'],
      ['choques lápida/vigente', (sal.choques || []).length, 'er'],
      ['duplicados', (sal.duplicados || []).length, 'wk'],
    ].filter(p => p[1] > 0);

    const pulso = `
      <div class="mm-pulso">
        <div class="mm-big"><b>${num(sal.total ?? mem.length)}</b><span>${esc(_t('memorias vigentes'))}</span></div>
        <div class="mm-alt">
          <div class="mm-alt-h">
            <span>${esc(_t('de lo inyectado, cuánto se leyó'))} · ${num(alt.dias || 7)} ${esc(_t('días'))}</span>
            ${hayTel ? `<b class="sx-mono">${tasa}%</b>` : `<b class="sx-mono sx-dim">${esc(_t('sin datos'))}</b>`}
          </div>
          ${hayTel ? `<div class="mm-bar"><i style="width:${Math.min(100, tasa)}%"></i></div>` : ''}
          <div class="mm-alt-f sx-mono sx-dim">${hayTel
            ? `${num(alt.inyecciones)} ${esc(_t('inyectadas'))} · ${num(alt.lecturas)} ${esc(_t('leídas'))} · ${num(alt.lecturas_en_done)} ${esc(_t('en tareas cerradas'))}`
            : esc(_t('todavía no se registraron inyecciones en esta ventana'))}</div>
        </div>
        <div class="mm-sig">${problemas.length
          ? problemas.map(([n, v, k]) => `<span class="sx-pill ${k}">${esc(_t(n))} ${v}</span>`).join('')
          : `<span class="sx-pill ok">${esc(_t('sin problemas de salud'))}</span>`}</div>
      </div>`;

    const cats = Object.entries(sal.por_categoria || {});
    const maxCat = Math.max(1, ...cats.map(([, c]) => c.total || 0));
    const cuadros = cats.length ? `<div class="mm-cats">${cats.map(([id, c]) => {
      const mal = (c.rotos || 0) + (c.citas_muertas || 0) + (c.huerfanas || 0) + (c.contrato || 0);
      return `<div class="mm-cat"${mal ? ` title="${mal} ${esc(_t('con problemas de salud'))}"` : ''}>
        <span class="mm-cat-n">${esc(c.nombre || id)}</span>
        <span class="mm-cat-b"><i style="width:${Math.round((c.total || 0) / maxCat * 100)}%"></i></span>
        <span class="mm-cat-v sx-mono">${mal ? `<em>${mal}</em>` : ''}${num(c.total)}</span>
      </div>`;
    }).join('')}</div>` : '';

    const recientes = mem.slice()
      .sort((a, b2) => String(b2.actualizado || '').localeCompare(String(a.actualizado || '')))
      .slice(0, 7);
    const lista = recientes.length ? `<div class="mm-list">${recientes.map(m => `
      <button class="mm-i" type="button" data-slug="${esc(m.slug)}">
        <span class="mm-i-t"><b>${esc(limpio(m.titulo) || m.slug)}</b><span>${esc(limpio(m.resumen))}</span></span>
        <span class="mm-i-m">
          ${m.estado && m.estado !== 'vigente' ? `<span class="sx-pill ${m.estado === 'lapida' ? 'er' : 'wk'}">${esc(m.estado)}</span>` : ''}
          <span class="sx-mono sx-dim">${esc(m.actualizado || '')}</span>
        </span>
      </button>`).join('')}</div>`
      : `<div class="sx-empty"><b>${esc(_t('Todavía no hay memorias'))}</b>
          <p>${esc(_t('Cuando un agente descubre algo que el resto debería saber, lo deja acá y el recall se lo inyecta al siguiente. Se guardan en .jarvis/memory/.'))}</p></div>`;

    b.innerHTML =
      blk('pulso', 'de un vistazo', pulso, { wide: true, key: 'Explorar memoria' }) +
      (cuadros ? blk('cuadros', 'por categoría', cuadros, { wide: true }) : '') +
      blk('recientes', 'últimas tocadas', lista, { wide: true }) +
      blk('lecciones', 'lo aprendido', `
        <div class="sx-set">
          <div class="sx-set-t"><b>${esc(_t('Destilador de lecciones'))}</b>
            <span>${esc(_t('Junta los motivos de los pasos trabados y los convierte en reglas cortas que se inyectan a TODOS los agentes.'))}</span></div>
          <span class="sx-pill ${lec.activo ? 'ok' : 'mute'}">${lec.activo ? esc(_t('activo')) : esc(_t('apagado'))}</span>
        </div>
        <div class="mm-lec-n sx-mono sx-dim">${num(lec.lecciones_memoria)} ${esc(_t('lecciones'))} ·
          ${num(lec.senales_pendientes)} ${esc(_t('señales pendientes'))} (${esc(_t('destila a las'))} ${num(lec.umbral || 6)})</div>`,
        { key: 'Lecciones' }) +
      blk('archivo', 'la carpeta', `
        <div class="sx-set">
          <div class="sx-set-t"><b>${esc(_t('Explorador de memoria'))}</b>
            <span>${esc(_t('Lista, grafo de [[wikilinks]] y panel Live de quién toca qué.'))}</span></div>
          <button class="sx-btn" id="sx-abrir-memoria" type="button">${icon('external-link', 13)} ${esc(_t('Abrir'))}</button>
        </div>`);

    const abrir = () => window.JarvisMemory?.abrir?.();
    b.querySelector('#sx-abrir-memoria')?.addEventListener('click', abrir);
    b.querySelectorAll('.mm-i').forEach(x => x.addEventListener('click', abrir));
  }

  /* ═══════════════════════════════════════════════════════════
     7 · WORKFLOWS — línea de tiempo con el track de pasos
     ═══════════════════════════════════════════════════════════ */
  const _WF_K = { running: 'wk', done: 'ok', failed: 'er', pending: 'mute' };

  function _renderWorkflows(b) {
    b.innerHTML = blk('historial', 'orquestaciones del proyecto',
      `<div class="cta-cargando">${icon('loader', 15)} ${esc(_t('Cargando…'))}</div>`,
      { wide: true, key: 'Historial de workflows' });
    if (!_projectId) return;
    const pid = _projectId;
    fetch(`/api/orchestrator/workflows/${pid}`)
      .then(r => r.json())
      .then(wfs => {
        if (!_abierta || _seccion !== 'workflows' || _projectId !== pid) return;
        _res.workflows = (wfs || []).length;
        _pintarValores();
        _pintarWorkflows(b, wfs || []);
      })
      .catch(() => _pintarWorkflows(b, []));
  }

  function _pintarWorkflows(b, wfs) {
    if (!wfs.length) {
      b.innerHTML = blk('historial', 'orquestaciones del proyecto', `
        <div class="sx-empty">
          <b>${esc(_t('Todavía no corrió ningún workflow'))}</b>
          <p>${esc(_t('Cuando le pedís algo grande al orquestador, arma un plan de pasos, levanta una terminal por agente y los coordina. Acá vas a ver cada corrida con su objetivo y en qué paso quedó.'))}</p>
        </div>`, { wide: true, key: 'Historial de workflows' }) +
        blk('así se ve', 'ejemplo de una corrida', `
          <article class="wf-i wf-demo" aria-hidden="true">
            <header class="wf-h">
              <span class="sx-pill wk">running</span>
              <b>${esc(_t('Ejemplo: rediseño de una sección'))}</b>
            </header>
            <div class="wf-track"><i class="ok"></i><i class="ok"></i><i class="now"></i><i></i><i></i>
              <span class="wf-n sx-mono sx-dim">2/5</span></div>
          </article>
          <div class="wf-leg">
            <span><i class="ok"></i> ${esc(_t('paso cerrado'))}</span>
            <span><i class="now"></i> ${esc(_t('en curso'))}</span>
            <span><i></i> ${esc(_t('pendiente'))}</span>
            <span class="sx-dim">${esc(_t('el último paso siempre es el Reviewer'))}</span>
          </div>`, { wide: true });
      return;
    }
    const filas = wfs.map(w => {
      const total = w.total_pasos || (w.pasos || []).length || 0;
      const hecho = Math.max(0, w.paso_actual || 0);
      const k = _WF_K[w.estado] || 'mute';
      const f = w.created_at ? new Date(w.created_at) : null;
      return `<article class="wf-i">
        <header class="wf-h">
          <span class="sx-pill ${k}">${esc(w.estado || '')}</span>
          <b>${esc(w.nombre || _t('Sin nombre'))}</b>
          <span class="wf-d sx-mono sx-dim">${f && !isNaN(f)
            ? f.toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit' }) + ' ' + f.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' })
            : ''}</span>
        </header>
        ${w.objetivo ? `<p class="wf-o">${esc(w.objetivo)}</p>` : ''}
        <div class="wf-track" aria-label="${hecho} / ${total}">
          ${Array.from({ length: total }, (_, i) =>
            `<i class="${i < hecho ? 'ok' : ''}${i === hecho && w.estado === 'running' ? ' now' : ''}"></i>`).join('')}
          <span class="wf-n sx-mono sx-dim">${hecho}/${total}</span>
        </div>
      </article>`;
    }).join('');
    b.innerHTML = blk('historial', `${wfs.length} ${wfs.length === 1 ? _t('corrida') : _t('corridas')}`,
      `<div class="wf">${filas}</div>`, { wide: true, key: 'Historial de workflows' });
  }

  /* ═══════════════════════════════════════════════════════════ */
  window.JarvisSettings = {
    init(projectId) { _projectId = projectId; },
    onProjectChanged(projectId) {
      _projectId = projectId;
      _res.activos = _res.memorias = _res.workflows = null;
      if (_abierta) { _renderSeccion(); _cargarResumen(); }
    },
    open, close,
    isOpen() { return _abierta; },
    // Re-renderiza la sección abierta (la usa el WS cuenta_agregada/cuentas_update).
    refrescar(seccion) { if (_abierta && (!seccion || seccion === _seccion)) _renderSeccion(); },
    // El watcher detectó la cuenta nueva: cierra el modal de alta con éxito.
    onCuentaAgregada(data) { try { _altaResolver?.(data); } catch {} },
  };
})();
