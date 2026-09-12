// JARVIS — Picker de terminal rápida (Ctrl+\): "Nueva terminal".
// Armás la TANDA (cantidad por CLI, cupos = MAX_TERMINALES - vivas) y la
// DISPOSICIÓN (presets reales de TerminalLayout._pure.snapPresets para el N
// final, más "Auto" = canónico vertical). Al lanzar: onPick(counts, preset)
// → workspace.js crea el batch y aplica el preset UNA vez al final.
(function (global) {
  'use strict';

  const OPCIONES = [
    { tipo: 'claude',      label: 'Claude Code', desc: 'El agente de Anthropic', tecla: '1' },
    { tipo: 'codex',       label: 'Codex',       desc: 'El CLI de OpenAI',       tecla: '2' },
    { tipo: 'opencode',    label: 'OpenCode',    desc: 'Agente open-source',     tecla: '3' },
    { tipo: 'qwen',        label: 'Qwen Code',   desc: 'El coder de Alibaba',    tecla: '4' },
    { tipo: 'antigravity', label: 'Antigravity', desc: 'El agente de Google',    tecla: '5' },
    { tipo: 'grok',        label: 'Grok Build',  desc: 'El agente de xAI',       tecla: '6' },
    { tipo: 'cursor',      label: 'Cursor',      desc: 'El agente de Cursor',    tecla: '7' },
    { tipo: 'pi',          label: 'Pi',          desc: 'El agente de Earendil',  tecla: '8' },
    { tipo: 'manual',      label: 'Shell',       desc: 'WSL puro, sin agente',   tecla: '9' },
  ];
  function opcionPorTecla(k) { return OPCIONES.find(o => o.tecla === k) || null; }
  function moverSeleccion(idx, delta, total) { return ((idx + delta) % total + total) % total; }
  // 'Digit7' → 6 (índice en OPCIONES); null si la tecla no es un dígito.
  function indicePorDigito(codigo) {
    const m = /^Digit(\d)$/.exec(codigo || '');
    return m ? +m[1] - 1 : null;
  }

  // Cantidad acotada a [1, cupo libre] (la conserva el stepper de cada CLI).
  function clampCantidad(n, disponibles) {
    const tope = Math.max(1, disponibles | 0);
    n = n | 0;
    return Math.min(Math.max(1, n), tope);
  }
  // ←/− bajan, →/+ suben; cualquier otra tecla no toca la cantidad.
  function deltaCantidadPorTecla(key) {
    if (key === 'ArrowRight' || key === '+') return 1;
    if (key === 'ArrowLeft'  || key === '-') return -1;
    return 0;
  }

  // ¿El elemento enfocado debe bloquear el atajo Ctrl+\? (true = no abrir el picker)
  // El textarea oculto de xterm es la excepción: con foco en una terminal el
  // atajo corre igual (el listener consume la tecla antes de que llegue al PTY).
  function focoBloqueaAtajo(el) {
    if (!el) return false;
    if (el.classList?.contains('xterm-helper-textarea')) return false;
    return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || !!el.isContentEditable;
  }

  // ─── Tanda (contadores por CLI) ────────────────────────────────────────────
  function totalCounts(counts) {
    return Object.values(counts || {}).reduce((a, b) => a + Math.max(0, b | 0), 0);
  }
  // Suma/resta inmutable respetando el cupo total (disponibles). Resta a 0 = sale.
  function sumarCount(counts, tipo, delta, disponibles) {
    const c = { ...(counts || {}) };
    if ((delta | 0) > 0 && totalCounts(c) >= Math.max(0, disponibles | 0)) return c;
    const n = Math.max(0, (c[tipo] | 0) + (delta | 0));
    if (n) c[tipo] = n; else delete c[tipo];
    return c;
  }

  // ─── Disposición ────────────────────────────────────────────────────────────
  // Espejo del canónico del sistema vertical (terminal-layout.js): ≤6 → una fila
  // de N columnas; 7..12 → dos filas con la de ABAJO capada en 3 y el excedente
  // ARRIBA (7→[6,1], 8→[6,2], 9→[6,3], 10→[7,3]...). Es el arreglo al que llega
  // el alta secuencial con _dist=null; el ancho real puede ajustar columnas.
  function autoCells(n) {
    n = Math.max(1, Math.min(12, n | 0));
    const fila = (c, y, h) => Array.from({ length: c }, (_, i) => ({ x: i / c, y, w: 1 / c, h }));
    if (n <= 6) return fila(n, 0, 1);
    const abajo = Math.min(n - 6, 3);
    return [...fila(n - abajo, 0, .5), ...fila(abajo, .5, .5)];
  }
  function autoDesc(n) {
    n = Math.max(1, Math.min(12, n | 0));
    if (n <= 6) return { filas: [n] };
    const abajo = Math.min(n - 6, 3);
    return { filas: [n - abajo, abajo] };
  }
  function cellsEq(a, b) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    const E = 0.01;
    return a.every((c, i) => Math.abs(c.x - b[i].x) < E && Math.abs(c.y - b[i].y) < E &&
                             Math.abs(c.w - b[i].w) < E && Math.abs(c.h - b[i].h) < E);
  }
  // Tiles a ofrecer para el N final: "Auto" + el catálogo real (snapPresets se
  // inyecta — en el browser viene de TerminalLayout._pure). Como hace el panel
  // de disposición del producto (sinPresetActual), un preset idéntico al Auto
  // no se ofrece dos veces.
  function tilesFor(nTotal, snapPresets) {
    const auto = { key: 'auto', label: 'Auto', cells: autoCells(nTotal) };
    const cat = typeof snapPresets === 'function' ? (snapPresets(nTotal) || []) : [];
    return [auto, ...cat.filter(p => !cellsEq(p.cells, auto.cells))];
  }

  const pure = { OPCIONES, opcionPorTecla, moverSeleccion, indicePorDigito,
                 focoBloqueaAtajo,
                 clampCantidad, deltaCantidadPorTecla,
                 totalCounts, sumarCount, autoCells, autoDesc, cellsEq, tilesFor };
  global.QuickPicker = Object.assign(global.QuickPicker || {}, { _pure: pure, ...pure });

  if (typeof document !== 'undefined') {
    let _el = null, _onPick = null, _nombreProyecto = '', _prevFocus = null;
    let _counts = {}, _pdSel = 'auto', _tiles = [], _disponibles = 1, _existentes = 0;
    // Estado de instalación por CLI (GET /api/clis): null = todavía no lo sé.
    // Lo que falta instalar se pinta en negro con un aviso, pero SIGUE
    // seleccionable — informar es distinto de bloquear.
    let _clisEstado = null;

    // Bilingüe para strings COMPUESTAS (números adentro → el observer de i18n no
    // matchea por clave); las estáticas van en español y las traduce el observer.
    const _L = (es, en) => (window.JarvisI18n?.lang?.() === 'en' ? en : es);
    const _snapPresets = () => window.TerminalLayout?._pure?.snapPresets;

    function _max() { return _existentes + _disponibles; }

    function _hintDe(t) {
      if (t.key !== 'auto') return `<b>${t.label}</b> — ${_L('queda como distribución del proyecto', 'becomes the project layout')}`;
      const d = autoDesc(_existentes + totalCounts(_counts));
      const forma = d.filas.length === 1
        ? _L(d.filas[0] === 1 ? 'pantalla completa' : `${d.filas[0]} columnas`,
             d.filas[0] === 1 ? 'full screen' : `${d.filas[0]} columns`)
        : _L(`${d.filas[0]} arriba · ${d.filas[1]} abajo`, `${d.filas[0]} top · ${d.filas[1]} bottom`);
      return `<b>Auto</b> — ${forma}, ${_L('se re-acomoda al agregar', 're-arranges as you add')}`;
    }

    // Cambiar la SELECCIÓN no reconstruye los tiles: re-crearlos replay-eaba la
    // animación de entrada (qp-rise, translateY 10px) en cada click/flecha →
    // parpadeo + desborde vertical transitorio en .qp-disp (scrollbar fantasma).
    // Solo se mueve la clase .sel y se actualiza el hint. Bonus: el tile
    // clickeado sobrevive → el foco no cae a <body> (el viejo gotcha de teclado).
    function _pintarSel() {
      _el.querySelectorAll('.qp-disp .qp-pd').forEach((b, i) => {
        b.classList.toggle('sel', !!_tiles[i] && _tiles[i].key === _pdSel);
      });
      const sel = _tiles.find(x => x.key === _pdSel);
      _el.querySelector('.qp-hint').innerHTML = sel ? _hintDe(sel) : '';
    }

    function _renderDisp() {
      const disp = _el.querySelector('.qp-disp'), hint = _el.querySelector('.qp-hint');
      const nTotal = _existentes + totalCounts(_counts);
      disp.innerHTML = '';
      if (!totalCounts(_counts)) {
        _tiles = [];
        disp.innerHTML = `<div class="qp-pd-void">${_L('Sumá agentes para ver las disposiciones', 'Add agents to see the layouts')}</div>`;
        hint.textContent = '';
        return;
      }
      _tiles = tilesFor(nTotal, _snapPresets());
      if (!_tiles.some(t => t.key === _pdSel)) _pdSel = 'auto';
      _tiles.forEach((t, i) => {
        const d = document.createElement('button');
        d.type = 'button';
        d.className = 'qp-pd' + (t.key === _pdSel ? ' sel' : '');
        d.style.setProperty('--i', i);
        const th = document.createElement('span');
        th.className = 'qp-pd-thumb';
        t.cells.forEach(c => {
          const cell = document.createElement('i');
          cell.style.left = (c.x * 100) + '%';
          cell.style.top = (c.y * 100) + '%';
          cell.style.width = `calc(${c.w * 100}% - 2px)`;
          cell.style.height = `calc(${c.h * 100}% - 2px)`;
          th.appendChild(cell);
        });
        const lb = document.createElement('small');
        lb.textContent = t.label;
        d.append(th, lb);
        d.addEventListener('pointerenter', () => { hint.innerHTML = _hintDe(t); });
        d.addEventListener('pointerleave', () => {
          const sel = _tiles.find(x => x.key === _pdSel);
          hint.innerHTML = sel ? _hintDe(sel) : '';
        });
        d.addEventListener('click', () => { _pdSel = t.key; _pintarSel(); });
        disp.appendChild(d);
      });
      const sel = _tiles.find(x => x.key === _pdSel);
      hint.innerHTML = sel ? _hintDe(sel) : '';
    }

    function _render() {
      if (!_el) return;
      _el.querySelector('.qp-proyecto').textContent = _nombreProyecto;
      const n = totalCounts(_counts);
      _el.querySelectorAll('.qp-row').forEach(r => {
        const tipo = r.dataset.tipo, c = _counts[tipo] | 0;
        r.classList.toggle('has', c > 0);
        const step = r.querySelector('.qp-step'), kbd = r.querySelector('.qp-tecla');
        step.hidden = c === 0;
        kbd.hidden = c > 0;
        step.querySelector('b').textContent = c;
      });
      // cupos: celditas MAX (en uso · a crear · libres) — espejo de #tl-capacidad
      const cells = _el.querySelector('.qp-cap-cells');
      cells.innerHTML = '';
      for (let i = 0; i < _max(); i++) {
        const cell = document.createElement('i');
        if (i < _existentes) cell.className = 'uso';
        else if (i < _existentes + n) cell.className = 'on';
        cells.appendChild(cell);
      }
      const libres = _disponibles - n;
      _el.querySelector('.qp-cap-txt').textContent =
        (_existentes ? _L(`${_existentes} en uso · `, `${_existentes} in use · `) : '') +
        (n ? _L(`${n} a crear · ${libres} libres`, `${n} to create · ${libres} free`)
           : _L(`${_disponibles} cupos libres`, `${_disponibles} slots free`));
      const partes = OPCIONES.filter(o => _counts[o.tipo])
        .map(o => `<b>${_counts[o.tipo]}× ${o.label}</b>`);
      _el.querySelector('.qp-sum').innerHTML = partes.length
        ? partes.join(' · ')
        : _L('Elegí al menos un agente', 'Pick at least one agent');
      const go = _el.querySelector('.qp-go');
      go.disabled = !n;
      _el.querySelector('.qp-go-txt').textContent =
        n === 0 ? _L('Lanzar', 'Launch')
        : n === 1 ? _L('Lanzar 1 terminal', 'Launch 1 terminal')
        : _L(`Lanzar ${n} terminales`, `Launch ${n} terminals`);
      _renderDisp();
      _pintarFalta();
    }

    function _sumar(tipo, delta) {
      // CLI no instalado: no se puede sumar (el estado llega siempre ANTES de
      // abrir — el cacheo en JarvisClisEstado lo carga al arrancar la página).
      if (_faltaDe(tipo)) return;
      _counts = sumarCount(_counts, tipo, delta, _disponibles);
      _render();
    }

    // ¿Este CLI falta en el sistema? (solo con estado conocido)
    function _faltaDe(tipo) {
      const st = (_clisEstado || []).find(c => c.id === tipo);
      return !!st && !st.instalado;
    }

    // Aplica el estado de instalación (fila en negro + aviso, deshabilitada).
    function _pintarFalta() {
      if (!_el) return;
      _el.querySelectorAll('.qp-row').forEach(r => {
        const falta = _faltaDe(r.dataset.tipo);
        r.classList.toggle('off', falta);
        r.disabled = falta;                       // no seleccionable sin instalar
        r.setAttribute('aria-disabled', String(falta));
        const chip = r.querySelector('.qp-falta');
        if (chip) chip.hidden = !falta;
      });
    }

    function _build() {
      _el = document.createElement('div');
      _el.className = 'qp-overlay';
      _el.hidden = true;
      _el.innerHTML = `
        <div class="qp-panel" role="dialog" aria-modal="true" aria-label="Nueva terminal">
          <div class="qp-head">
            <span class="qp-head-ico" aria-hidden="true"><svg viewBox="0 0 17 17" fill="none"><path d="m3 5.5 3.5 3L3 11.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M9 12h5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></span>
            <div class="qp-head-txt">
              <h2 class="qp-title">Nueva terminal</h2>
              <p class="qp-sub">Armá la tanda de agentes para <b class="qp-proyecto"></b></p>
            </div>
            <button class="qp-x" type="button" title="Cerrar (Esc)"><svg viewBox="0 0 12 12" fill="none" aria-hidden="true"><path d="m2 2 8 8M10 2l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></button>
          </div>
          <div class="qp-sec">
            <b class="qp-lbl">Agentes</b>
            <span class="qp-fill"></span>
            <span class="qp-cap"><span class="qp-cap-cells"></span><span class="qp-cap-txt"></span></span>
          </div>
          <div class="qp-grid">
            ${OPCIONES.map(o => `
              <button class="qp-row" data-tipo="${o.tipo}" type="button">
                <span class="qp-logo">${window.cliLogo ? cliLogo(o.tipo, 18) : ''}</span>
                <span class="qp-txt"><b>${o.label}</b><span class="qp-desc"><small>${o.desc}</small><i class="qp-falta" hidden>Falta instalar</i></span></span>
                <span class="qp-step" hidden><span class="qp-st-b" data-d="-1">−</span><b>0</b><span class="qp-st-b" data-d="1">+</span></span>
                <kbd class="qp-tecla">${o.tecla}</kbd>
              </button>`).join('')}
          </div>
          <div class="qp-sec">
            <b class="qp-lbl">Disposición</b>
            <span class="qp-fill"></span>
            <span class="qp-hint"></span>
          </div>
          <div class="qp-disp"></div>
          <div class="qp-foot">
            <span class="qp-sum"></span>
            <button class="qp-go" type="button"><span class="qp-go-txt"></span> <kbd>⏎</kbd></button>
          </div>
        </div>`;
      document.body.appendChild(_el);
      _el.addEventListener('click', (e) => {
        if (e.target === _el) { cerrar(); return; }
        // red de seguridad: si un click interno dejó el foco fuera del overlay
        // (nodo re-renderizado), volver al panel para que el teclado siga vivo
        if (!_el.hidden && !_el.contains(document.activeElement)) _el.querySelector('.qp-panel').focus();
      });
      _el.querySelector('.qp-x').addEventListener('click', cerrar);
      _el.querySelector('.qp-go').addEventListener('click', _lanzar);
      _el.querySelectorAll('.qp-row').forEach(r => {
        // click suma 1; el − del stepper resta; click derecho resta (como el launcher)
        r.addEventListener('click', (e) => {
          const b = e.target.closest('.qp-st-b');
          _sumar(r.dataset.tipo, b ? +b.dataset.d : +1);
        });
        r.addEventListener('contextmenu', (e) => { e.preventDefault(); _sumar(r.dataset.tipo, -1); });
      });
      _el.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cerrar(); return; }
        if (e.key === 'Enter')  { e.preventDefault(); e.stopPropagation(); _lanzar(); return; }
        const di = indicePorDigito(e.code);        // 1-9 suma · Shift resta
        if (di != null && di >= 0 && di < OPCIONES.length) {
          e.preventDefault(); e.stopPropagation();
          _sumar(OPCIONES[di].tipo, e.shiftKey ? -1 : +1);
          return;
        }
        const dc = deltaCantidadPorTecla(e.key);              // ←/→ recorren disposiciones
        if (dc && _tiles.length) {
          e.preventDefault(); e.stopPropagation();
          const i = _tiles.findIndex(t => t.key === _pdSel);
          _pdSel = _tiles[moverSeleccion(i, dc, _tiles.length)].key;
          _pintarSel();
        }
      });
      // idioma: las compuestas (_L) se re-arman a mano al cambiar ES⇆EN
      window.addEventListener('jarvis:lang', () => { if (_el && !_el.hidden) _render(); });
    }

    function _lanzar() {
      if (!totalCounts(_counts)) return;
      const cb = _onPick, counts = { ..._counts };
      const t = _tiles.find(x => x.key === _pdSel);
      const preset = (t && t.key !== 'auto') ? t : null;   // Auto = no tocar la distribución
      cerrar();
      cb?.(counts, preset);
    }

    function abrir({ proyecto, onPick, disponibles, existentes } = {}) {
      if (!_el) _build();
      _prevFocus = document.activeElement;
      _nombreProyecto = proyecto || '';
      _onPick = onPick || _onPick;
      _disponibles = Math.max(1, disponibles | 0);
      _existentes = Math.max(0, existentes | 0);
      _counts = {};                // sin preselección: la tanda la arma el usuario
      _pdSel = 'auto';
      // Estado desde la cache global (la carga el shell al arrancar): las filas
      // pintan el aviso YA, sin el "1 segundo de nada". Después se refresca en
      // el fondo por si cambió mientras la app está abierta.
      _clisEstado = Array.isArray(global.JarvisClisEstado?.clis) ? global.JarvisClisEstado.clis : null;
      // Sin cache en memoria, tirar de la última detección guardada: el aviso
      // sale al instante aunque el shell todavía no haya terminado el fetch.
      if (!_clisEstado) {
        try {
          const prev = JSON.parse(localStorage.getItem('jarvis_clis_estado') || 'null');
          if (prev && Array.isArray(prev.clis)) _clisEstado = prev.clis;
        } catch { /* localStorage roto: seguimos sin cache */ }
      }
      _el.hidden = false;
      _render();
      _el.querySelector('.qp-panel').setAttribute('tabindex', '-1');
      _el.querySelector('.qp-panel').focus();
      // ¿Qué CLIs faltan instalar? Se pide por apertura (estado fresco); sin
      // red, el picker funciona igual (las filas quedan con la cache).
      fetch('/api/clis')
        .then(r => (r.ok ? r.json() : null))
        .then(d => {
          if (d && !_el.hidden) {
            _clisEstado = Array.isArray(d.clis) ? d.clis : null;
            global.JarvisClisEstado = d;
            _pintarFalta();
          }
        })
        .catch(() => {});
    }
    function cerrar() {
      if (_el) _el.hidden = true;
      const pf = _prevFocus;
      _prevFocus = null;
      pf?.focus?.();
    }
    function init(opts) { _onPick = opts?.onPick || null; }
    Object.assign(global.QuickPicker, { abrir, cerrar, init,
      estaAbierto() { return !!_el && !_el.hidden; } });
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = pure;
})(typeof window !== 'undefined' ? window : globalThis);
