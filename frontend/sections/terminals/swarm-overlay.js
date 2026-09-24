// JARVIS — Overlay del grupo del enjambre.
//
// QUÉ MUESTRA
// Los agentes que convergieron sobre la misma superficie, como lo que son:
// presencias CONECTADAS. No un gráfico de telemetría — un escenario vivo.
//
// LA METÁFORA
// Cada agente es un nodo. Entre los que comparten superficie hay un ENLACE que
// respira todo el tiempo: eso es lo que se ve de un vistazo, que están atados.
// Cuando uno le manda un mensaje a otro, un PULSO recorre el enlace del que
// habla al que escucha y florece al llegar. La conversación deja de ser una
// lista y pasa a ser algo que ocurre delante tuyo.
//
// El color no decora: info = enlace y mensajes · warning = sobrescritura ·
// error = colisión · acento = actividad reciente. Todo por tokens (24 temas).
(function (global) {
  'use strict';

  // ─── Lógica pura (testeable en Node, sin DOM) ──────────────────────────────

  const GEO = { ancho: 920, alto: 400, radio: 122, nodo: 34 };

  /**
   * Ubica a los agentes en el escenario. Dos, enfrentados; tres o más, en
   * círculo — la forma dice "esto es un grupo", no "esto es una lista".
   * Puro.
   */
  function posicionarNodos(miembros, opts) {
    const g = Object.assign({}, GEO, opts || {});
    const ms = (miembros || []).filter(m => m && m.tid != null);
    const n = ms.length;
    if (!n) return [];
    const cx = g.ancho / 2;
    if (n === 1) return [{ ...ms[0], x: cx, y: g.alto / 2, ang: 0,
                           etiquetaArriba: false }];
    // n=2 → 180°/0° (enfrentados). n>=3 → arranca arriba y reparte el círculo.
    const inicio = n === 2 ? Math.PI : -Math.PI / 2;
    const rx = n === 2 ? g.radio * 1.7 : g.radio * 1.5;
    const ry = n === 2 ? 0 : g.radio;
    // Con tres, el vértice de arriba está a `ry` del centro y los de abajo a
    // `ry/2`: centrar por el centro geométrico deja el conjunto pegado arriba.
    // Se compensa bajando el centro (incluye el alto de las etiquetas).
    const cy = g.alto / 2 + (n === 2 ? 0 : ry / 4);
    return ms.map((m, i) => {
      const ang = inicio + (i * 2 * Math.PI) / n;
      const y = cy + Math.sin(ang) * ry;
      // La etiqueta va del lado LIBRE: arriba si el nodo está en la mitad de
      // arriba, abajo si no. Si no, en un triángulo el nombre del nodo de
      // arriba cae justo encima de sus dos enlaces.
      return { ...m, ang, x: cx + Math.cos(ang) * rx, y, etiquetaArriba: y < cy - 1 };
    });
  }

  /**
   * Los enlaces entre nodos. Curva hacia afuera del centro para que con tres
   * agentes los tres enlaces se distingan en vez de superponerse.
   * Puro.
   */
  function enlacesDe(nodos, opts) {
    const g = Object.assign({}, GEO, opts || {});
    const out = [];
    for (let i = 0; i < (nodos || []).length; i++) {
      for (let j = i + 1; j < nodos.length; j++) {
        const a = nodos[i], b = nodos[j];
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        const dx = b.x - a.x, dy = b.y - a.y;
        const largo = Math.hypot(dx, dy) || 1;
        // normal, empujada hacia afuera del centro del escenario
        const cxs = g.ancho / 2, cys = g.alto / 2;
        let nx = -dy / largo, ny = dx / largo;
        if ((mx - cxs) * nx + (my - cys) * ny < 0) { nx = -nx; ny = -ny; }
        const comba = nodos.length === 2 ? 0 : largo * 0.12;
        const cx = mx + nx * comba, cy = my + ny * comba;
        // El enlace TOCA el anillo, no atraviesa el disco: se recorta en cada
        // punta hacia su punto de control. Es el detalle que separa "líneas
        // cruzando círculos" de "nodos atados".
        const borde = g.nodo * 0.62 + 7;
        const rec = (p, hacia) => {
          const dx2 = hacia.x - p.x, dy2 = hacia.y - p.y;
          const d = Math.hypot(dx2, dy2) || 1;
          return { x: p.x + (dx2 / d) * borde, y: p.y + (dy2 / d) * borde };
        };
        const pa = rec(a, { x: cx, y: cy }), pb = rec(b, { x: cx, y: cy });
        out.push({ a: a.tid, b: b.tid, x1: pa.x, y1: pa.y, x2: pb.x, y2: pb.y,
                   cx, cy, largo });
      }
    }
    return out;
  }

  /** El enlace que une a dos tids (en cualquier orden), o null. */
  function enlaceEntre(enlaces, t1, t2) {
    return (enlaces || []).find(e => (e.a === t1 && e.b === t2)
                                  || (e.a === t2 && e.b === t1)) || null;
  }

  /**
   * Los mensajes que se van a animar como pulsos: los últimos `tope`, ya
   * resueltos a tids y con su enlace. Se acotan a propósito — treinta pulsos
   * simultáneos no son información, son ruido.
   * Puro.
   */
  function pulsosDeMensajes(timeline, nodos, enlaces, tope) {
    const porNombre = new Map((nodos || []).map(
      n => [String(n.nombre || '').trim().toLowerCase(), n.tid]));
    const out = [];
    for (const e of timeline || []) {
      if (e.tipo !== 'mensaje') continue;
      const de = porNombre.get(String(e.de || '').trim().toLowerCase());
      const para = porNombre.get(String(e.para || '').trim().toLowerCase());
      if (de == null || para == null || de === para) continue;
      const enlace = enlaceEntre(enlaces, de, para);
      if (!enlace) continue;
      // El pulso viaja del que habla al que escucha: si el enlace está guardado
      // al revés, se recorre invertido.
      out.push({ de, para, ts: e.ts, texto: e.texto || '',
                 enlace, invertido: enlace.a !== de });
    }
    return out.slice(-(tope || 6));
  }

  /** Actividad por agente para el halo del nodo: cuántas escrituras y cuándo. */
  function pulsoDeNodo(timeline, tid, ahora) {
    let n = 0, ultima = 0;
    for (const e of timeline || []) {
      if (e.tipo === 'edicion' && e.tid === tid) { n++; ultima = Math.max(ultima, e.ts); }
    }
    const hace = ultima ? (ahora || Date.now() / 1000) - ultima : Infinity;
    return { escrituras: n, ultima, activo: hace < 90 };
  }

  /** "hace 3 min" — el tiempo relativo es lo legible en un panel en vivo. */
  function hace(ts, ahora) {
    const s = Math.max(0, Math.round(((ahora || Date.now() / 1000) - ts)));
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m`;
    return `${Math.floor(s / 3600)}h`;
  }

  /**
   * Encabezado: por qué están enredados. Los símbolos se NOMBRAN (son cortos y
   * son lo interesante); "3 símbolos en común" esconde el dato con el que se
   * decide si te importa.
   */
  function resumenSuperficie(grupo, t) {
    const tr = t || (s => s);
    const arch = grupo?.archivos || [];
    const simb = grupo?.simbolos || [];
    const partes = [];
    if (arch.length) {
      partes.push(arch.length <= 2 ? arch.join(' · ') : `${arch[0]} +${arch.length - 1}`);
    }
    if (simb.length) {
      const muestra = simb.slice(0, 3).map(s => '`' + s + '`').join(' ');
      partes.push(simb.length > 3 ? `${muestra} +${simb.length - 3}` : muestra);
    }
    return partes.join('  ·  ');
  }

  /** Una línea legible por evento, para la lectura cronológica. */
  function lineaEvento(e, t) {
    const tr = t || (s => s);
    if (e.tipo === 'mensaje') return { icono: 'mensaje', quien: e.de,
      texto: e.texto, extra: `→ ${e.para}` };
    if (e.tipo === 'colision') return { icono: 'colision', quien: e.nombre,
      texto: `${tr('borró')} \`${e.simbolo}\` — ${tr('lo usa')} ${e.contra_nombre}`,
      extra: e.contra_path || '' };
    return { icono: e.sobrescritura ? 'sobrescritura' : 'edicion', quien: e.nombre,
      texto: e.path, extra: (e.simbolos || []).slice(0, 3).join(' · ') };
  }

  /**
   * De qué proyecto es este grupo. Manda el que abre el overlay (SwarmLink ya
   * sabe con qué proyecto pidió los grupos); si no lo dice, sale de la URL, que
   * es de donde lo saca el workspace entero y se mantiene al día con pushState
   * al cambiar de proyecto.
   *
   * Existe como función y con test porque acá vivía el bug: se resolvía por una
   * global (`proyectoActual` / `PROJECT_ID`) que NO EXISTE en ningún lado del
   * frontend, así que siempre daba null, el detalle nunca se pedía y el panel se
   * cerraba en el mismo frame en que se abría — un botón que no hace nada.
   * Puro.
   */
  function pidDe(explicito, search) {
    if (explicito != null && explicito !== '' && String(explicito) !== '0') {
      return String(explicito);
    }
    const id = new URLSearchParams(search || '').get('id');
    return id || null;
  }

  const pure = { posicionarNodos, enlacesDe, enlaceEntre, pulsosDeMensajes,
                 pulsoDeNodo, hace, resumenSuperficie, lineaEvento, pidDe, GEO };
  global.SwarmOverlay = Object.assign(global.SwarmOverlay || {}, { _pure: pure });

  if (typeof document === 'undefined') return;   // tests Node: solo _pure

  // ─── Capa DOM ──────────────────────────────────────────────────────────────

  const NS = 'http://www.w3.org/2000/svg';
  const t = (s) => (global.JarvisI18n?.t ? global.JarvisI18n.t(s) : s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  let _abierto = null;       // {grupoId, tid, pid, detalle}
  let _nodo = null;
  let _timer = null;
  let _raf = null;
  let _pulsos = [];          // estado vivo de la animación
  let _t0 = 0;

  const _reduce = () => global.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

  // null = el grupo no existe (4xx); undefined = falla de red/transitoria (NO
  // es "el vínculo se deshizo": antes rechazaba y el intervalo de 4s llenaba
  // la consola de promesas sin manejar).
  async function _traer(grupoId, pid) {
    if (pid == null) return null;
    try {
      const r = await fetch(`/api/swarm/grupo/${pid}/${encodeURIComponent(grupoId)}`,
                            { credentials: 'same-origin' });
      if (r.status >= 500) return undefined;
      return r.ok ? await r.json() : null;
    } catch (_) { return undefined; }
  }

  function _d(e) {
    return e.cx == null || (e.cx === (e.x1 + e.x2) / 2 && e.cy === (e.y1 + e.y2) / 2)
      ? `M ${e.x1} ${e.y1} L ${e.x2} ${e.y2}`
      : `M ${e.x1} ${e.y1} Q ${e.cx} ${e.cy} ${e.x2} ${e.y2}`;
  }

  /** El escenario: nodos, enlaces vivos y los pulsos de los mensajes. */
  function _escenario(d) {
    const nodos = posicionarNodos(d.miembros, {});
    const enlaces = enlacesDe(nodos, {});
    if (!nodos.length) return '';
    const ahora = Date.now() / 1000;
    const colisionados = new Set();
    for (const c of d.colisiones || []) { colisionados.add(c.tid); colisionados.add(c.contra_tid); }

    const gEnlaces = enlaces.map((e, i) => {
      const roto = colisionados.has(e.a) && colisionados.has(e.b);
      return `<g class="sw-enlace ${roto ? 'es-roto' : ''}" data-i="${i}">
        <path class="sw-enlace-base" d="${_d(e)}"/>
        <path class="sw-enlace-flujo" d="${_d(e)}"/>
      </g>`;
    }).join('');

    const gNodos = nodos.map(n => {
      const p = pulsoDeNodo(d.timeline, n.tid, ahora);
      const roto = colisionados.has(n.tid);
      return `<g class="sw-nodo ${p.activo ? 'es-activo' : ''} ${roto ? 'es-roto' : ''}"
                 transform="translate(${n.x},${n.y})">
        <circle class="sw-nodo-halo" r="${GEO.nodo * 0.92}"/>
        <circle class="sw-nodo-anillo" r="${GEO.nodo * 0.62}"/>
        <circle class="sw-nodo-cuerpo" r="${GEO.nodo * 0.46}"/>
        <text class="sw-nodo-ini" y="4" text-anchor="middle">${
          esc((n.nombre || '?').replace(/[^0-9]/g, '').slice(-2) || '·')}</text>
        <text class="sw-nodo-nom" y="${n.etiquetaArriba ? -GEO.nodo * 1.66 : GEO.nodo * 1.34}"
              text-anchor="middle">${esc(n.nombre)}</text>
        <text class="sw-nodo-sub"
              y="${n.etiquetaArriba ? -GEO.nodo * 1.66 + 15 : GEO.nodo * 1.34 + 15}"
              text-anchor="middle">${
          p.escrituras} ${esc(p.escrituras === 1 ? t('escritura') : t('escrituras'))}</text>
      </g>`;
    }).join('');

    return `<svg class="sw-escena" viewBox="0 0 ${GEO.ancho} ${GEO.alto}"
                 preserveAspectRatio="xMidYMid meet" role="img"
                 aria-label="${esc(t('Agentes conectados y sus mensajes'))}">
      <defs>
        <radialGradient id="sw-vineta" cx="50%" cy="50%" r="62%">
          <stop offset="0%"  class="sw-vin-0"/>
          <stop offset="100%" class="sw-vin-1"/>
        </radialGradient>
      </defs>
      <rect class="sw-fondo-escena" width="${GEO.ancho}" height="${GEO.alto}"/>
      <g class="sw-enlaces">${gEnlaces}</g>
      <g class="sw-pulsos"></g>
      <g class="sw-nodos">${gNodos}</g>
    </svg>`;
  }

  /**
   * Anima los mensajes viajando por sus enlaces. Cada pulso recorre el camino
   * del que habla al que escucha y florece al llegar; después espera su turno y
   * vuelve — así el panel muestra que estos dos se están hablando, aunque no
   * llegue nada nuevo mientras lo mirás.
   */
  function _armarPulsos(d) {
    cancelAnimationFrame(_raf); _raf = null;
    _pulsos = [];
    const capa = _nodo?.querySelector('.sw-pulsos');
    const svg = _nodo?.querySelector('.sw-escena');
    if (!capa || !svg) return;
    capa.innerHTML = '';
    if (_reduce()) return;                      // sin movimiento: los enlaces bastan

    const nodos = posicionarNodos(d.miembros, {});
    const enlaces = enlacesDe(nodos, {});
    const msgs = pulsosDeMensajes(d.timeline, nodos, enlaces, 6);
    if (!msgs.length) return;

    msgs.forEach((m, i) => {
      const camino = document.createElementNS(NS, 'path');
      camino.setAttribute('d', _d(m.enlace));
      camino.setAttribute('fill', 'none');
      camino.setAttribute('stroke', 'none');
      capa.appendChild(camino);
      const estela = document.createElementNS(NS, 'circle');
      estela.setAttribute('class', 'sw-pulso-estela');
      estela.setAttribute('r', '9');
      const bola = document.createElementNS(NS, 'circle');
      bola.setAttribute('class', 'sw-pulso');
      bola.setAttribute('r', '4.2');
      capa.appendChild(estela); capa.appendChild(bola);
      const flor = document.createElementNS(NS, 'circle');
      flor.setAttribute('class', 'sw-pulso-flor');
      flor.setAttribute('r', '4');
      capa.appendChild(flor);
      _pulsos.push({ camino, bola, estela, flor, m,
                     largo: camino.getTotalLength() || 1,
                     retraso: i * 900 });        // escalonados: se leen de a uno
    });

    _t0 = performance.now();
    const DUR = 1500, PAUSA = 2600;
    const ciclo = DUR + PAUSA + _pulsos.length * 900;

    function paso(ahora) {
      const tGlobal = ahora - _t0;
      for (const p of _pulsos) {
        const local = ((tGlobal - p.retraso) % ciclo + ciclo) % ciclo;
        const dentro = local >= 0 && local <= DUR;
        const k = dentro ? local / DUR : -1;
        if (k < 0) {
          p.bola.style.opacity = p.estela.style.opacity = p.flor.style.opacity = '0';
          continue;
        }
        // ease-out: sale rápido y se posa. El viaje se siente intencional.
        const e = 1 - Math.pow(1 - k, 3);
        const rec = p.m.invertido ? (1 - e) : e;
        const pt = p.camino.getPointAtLength(rec * p.largo);
        for (const el of [p.bola, p.estela]) {
          el.setAttribute('cx', pt.x); el.setAttribute('cy', pt.y);
        }
        const entra = Math.min(1, k / 0.12), sale = Math.min(1, (1 - k) / 0.18);
        const vis = Math.min(entra, sale);
        p.bola.style.opacity = String(vis);
        p.estela.style.opacity = String(vis * 0.5);
        // Al llegar: florece en el que escucha.
        const llega = k > 0.86 ? (k - 0.86) / 0.14 : 0;
        p.flor.style.opacity = String(llega ? (1 - llega) * 0.9 : 0);
        if (llega) {
          const fin = p.camino.getPointAtLength(p.m.invertido ? 0 : p.largo);
          p.flor.setAttribute('cx', fin.x); p.flor.setAttribute('cy', fin.y);
          p.flor.setAttribute('r', String(4 + llega * 22));
        }
      }
      _raf = requestAnimationFrame(paso);
    }
    _raf = requestAnimationFrame(paso);
  }

  function _lectura(d) {
    if (!(d.timeline || []).length) {
      return `<p class="sw-vacio">${esc(t('Todavía no hay actividad compartida.'))}</p>`;
    }
    const ahora = Date.now() / 1000;
    return [...d.timeline].reverse().map(e => {
      const l = lineaEvento(e, t);
      return `<li class="sw-ev sw-ev-${l.icono}">
        <span class="sw-ev-t">${esc(hace(e.ts, ahora))}</span>
        <span class="sw-ev-quien">${esc(l.quien)}</span>
        <span class="sw-ev-txt">${esc(l.texto)}</span>
        ${l.extra ? `<span class="sw-ev-extra">${esc(l.extra)}</span>` : ''}
      </li>`;
    }).join('');
  }

  function _render(d) {
    // El panel se refresca solo cada 4s: si el re-render tirara el scroll, leer
    // un mensaje largo sería imposible. Se preserva la posición de lectura.
    const scroll = _nodo.querySelector('.sw-lectura')?.scrollTop || 0;
    const nombres = (d.miembros || []).map(m => esc(m.nombre))
      .join('<span class="sw-sep">·</span>');
    const colis = (d.colisiones || []).length;
    _nodo.querySelector('.sw-panel').innerHTML = `
      <header class="sw-cab">
        <div class="sw-quienes">
          <h2 id="sw-tit">${nombres}</h2>
          <p class="sw-donde">${esc(resumenSuperficie(d, t))}</p>
        </div>
        ${colis ? `<span class="sw-chip sw-chip-err">${colis} ${
          esc(colis === 1 ? t('colisión') : t('colisiones'))}</span>` : ''}
        <button class="sw-cerrar" aria-label="${esc(t('Cerrar'))}">${
          global.icon ? global.icon('x', 16) : '✕'}</button>
      </header>
      <section class="sw-teatro">${_escenario(d)}</section>
      <section class="sw-lectura">
        <ol class="sw-eventos">${_lectura(d)}</ol>
      </section>`;
    _nodo.querySelector('.sw-cerrar')?.addEventListener('click', cerrar);
    const lect = _nodo.querySelector('.sw-lectura');
    if (lect && scroll) lect.scrollTop = scroll;
    _armarPulsos(d);
  }

  function _montar() {
    if (_nodo) return;
    _nodo = document.createElement('div');
    _nodo.className = 'sw-overlay';
    _nodo.setAttribute('role', 'dialog');
    _nodo.setAttribute('aria-modal', 'true');
    _nodo.setAttribute('aria-labelledby', 'sw-tit');
    _nodo.innerHTML = `<div class="sw-fondo"></div><div class="sw-panel"></div>`;
    _nodo.querySelector('.sw-fondo').addEventListener('click', cerrar);
    document.body.appendChild(_nodo);
  }

  function _onEsc(e) { if (e.key === 'Escape') { e.stopPropagation(); cerrar(); } }

  /**
   * El grupo ya no existe: se deshizo entre que se pintó el ícono y el click, o
   * mientras lo mirabas (la convergencia se vence sola a los 30 min, y el libro
   * de ediciones también se vacía cuando el server se reinicia).
   *
   * Cerrar el panel de golpe acá era indistinguible de un botón roto — es
   * literalmente lo que se veía. Se dice qué pasó y se deja cerrar a mano.
   */
  function _sinGrupo() {
    clearInterval(_timer); _timer = null;
    cancelAnimationFrame(_raf); _raf = null; _pulsos = [];
    _nodo.querySelector('.sw-panel').innerHTML = `
      <header class="sw-cab">
        <div class="sw-quienes">
          <h2 id="sw-tit">${esc(t('El vínculo ya no existe'))}</h2>
          <p class="sw-donde">${esc(t('Estos agentes dejaron de trabajar sobre la misma superficie.'))}</p>
        </div>
        <button class="sw-cerrar" aria-label="${esc(t('Cerrar'))}">${
          global.icon ? global.icon('x', 16) : '✕'}</button>
      </header>
      <section class="sw-lectura"><p class="sw-vacio">${
        esc(t('El vínculo se deshace solo cuando pasan 30 minutos sin que ninguno vuelva a tocar la zona compartida.'))}</p></section>`;
    const b = _nodo.querySelector('.sw-cerrar');
    b?.addEventListener('click', cerrar);
    b?.focus();
  }

  async function abrir(grupoId, tid, pid) {
    _montar();
    const sesion = { grupoId, tid, pid: pidDe(pid, global.location?.search),
                     detalle: null };
    _abierto = sesion;
    _nodo.querySelector('.sw-panel').innerHTML = '';
    _nodo.classList.add('visible');
    document.addEventListener('keydown', _onEsc, true);
    const d = await _traer(grupoId, sesion.pid);
    // Se cerró — o se cerró y se abrió OTRO grupo — mientras cargaba: la
    // respuesta de este grupo no debe pintarse en el panel del otro.
    if (_abierto !== sesion) return;
    if (d === undefined) { cerrar(); return; }   // sin red: no mentir "el vínculo ya no existe"
    if (!d) { _sinGrupo(); return; }
    _abierto.detalle = d;
    _render(d);
    _nodo.querySelector('.sw-cerrar')?.focus();
    clearInterval(_timer);
    _timer = setInterval(() => refrescar(), 4000);   // el panel es un instrumento vivo
  }

  async function refrescar() {
    const sesion = _abierto;
    if (!sesion) return;
    const d = await _traer(sesion.grupoId, sesion.pid);
    if (_abierto !== sesion) return;
    if (d === undefined) return;                 // falla transitoria: el próximo tick reintenta
    if (!d) { _sinGrupo(); return; }             // el grupo se deshizo mientras mirabas
    _abierto.detalle = d;
    _render(d);
  }

  function refrescarSiAbierto() { if (_abierto) refrescar(); }

  function cerrar() {
    clearInterval(_timer); _timer = null;
    cancelAnimationFrame(_raf); _raf = null; _pulsos = [];
    _abierto = null;
    document.removeEventListener('keydown', _onEsc, true);
    _nodo?.classList.remove('visible');
  }

  global.SwarmOverlay = Object.assign(global.SwarmOverlay, {
    _pure: pure, abrir, cerrar, refrescar, refrescarSiAbierto,
    abierto: () => !!_abierto,
  });
})(typeof window !== 'undefined' ? window : globalThis);
