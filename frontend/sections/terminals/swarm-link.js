// JARVIS — Vínculo del enjambre: cuándo dos o más agentes están trabajando
// sobre la MISMA superficie, y cómo se ve eso.
//
// POR QUÉ EXISTE
// El usuario reparte el trabajo entre varios agentes, así que el que necesita
// ver los choques venir es ÉL. Hasta ahora no veía nada: ni quién converge con
// quién, ni los mensajes que se mandan entre ellos, ni las colisiones.
//
// DOS ESTADOS, Y LA DIFERENCIA IMPORTA
//   convergencia → informativo. Están trabajando juntos acá. NO es un problema:
//                  está medido que dos ediciones en zonas distintas del mismo
//                  archivo conviven perfecto.
//   colision     → alerta real. Uno borró algo que el otro usa, o se frenó una
//                  sobrescritura.
// Si el ícono se prendiera fuerte cada vez que comparten archivo, en dos días
// se ignora. Callado donde es inofensivo, fuerte donde es fatal.
(function (global) {
  'use strict';

  // ─── Lógica pura (testeable en Node, sin DOM) ──────────────────────────────

  /** El grupo al que pertenece una terminal, o null. */
  function grupoDe(grupos, tid) {
    for (const g of grupos || []) {
      if ((g.miembros || []).some(m => m.tid === tid)) return g;
    }
    return null;
  }

  /** Estado visual de la card: null (sin vínculo) | 'convergencia' | 'colision'. */
  function estadoDeCard(grupos, tid) {
    const g = grupoDe(grupos, tid);
    return g ? (g.estado === 'colision' ? 'colision' : 'convergencia') : null;
  }

  /** Los OTROS del grupo (no vos): es lo que dice el tooltip. */
  function companeros(grupo, tid) {
    return (grupo?.miembros || []).filter(m => m.tid !== tid).map(m => m.nombre);
  }

  /**
   * Texto del tooltip. Dice QUIÉN y POR QUÉ, no solo que "algo pasa" — un
   * indicador que no explica es ruido.
   */
  function tituloVinculo(grupo, tid, t) {
    const tr = t || (s => s);
    if (!grupo) return '';
    const otros = companeros(grupo, tid);
    const quienes = otros.length === 1 ? otros[0]
      : otros.slice(0, -1).join(', ') + ' ' + tr('y') + ' ' + otros[otros.length - 1];
    const donde = (grupo.archivos || [])[0]
      || ((grupo.simbolos || [])[0] ? '`' + grupo.simbolos[0] + '`' : '');
    const base = grupo.estado === 'colision'
      ? tr('Colisión con') + ' ' + quienes
      : tr('Trabajando con') + ' ' + quienes;
    return donde ? `${base} — ${donde}` : base;
  }

  /** Cuántos vínculos hay que dibujar y cuáles cambiaron (evita repintar todo). */
  function diffEstados(previo, grupos, tids) {
    const nuevo = new Map();
    for (const tid of tids || []) {
      const e = estadoDeCard(grupos, tid);
      if (e) nuevo.set(tid, e);
    }
    const cambios = [];
    for (const [tid, est] of nuevo) if (previo.get(tid) !== est) cambios.push([tid, est]);
    for (const tid of previo.keys()) if (!nuevo.has(tid)) cambios.push([tid, null]);
    return { estados: nuevo, cambios };
  }

  /**
   * Las VÍCTIMAS de una colisión que están en pantalla: agrupa el evento
   * `colision_funcional` por terminal afectada, junta sus símbolos rotos y
   * descarta al propio actor y a las que no se ven (no se puede pulsar una card
   * ausente). → [{tid, nombre, simbolos:[...], path}].
   */
  function victimasDeColision(event, tidsEnPantalla) {
    const enPantalla = new Set(tidsEnPantalla || []);
    const actor = event && event.terminal_id;
    const porTid = new Map();
    for (const c of (event && event.colisiones) || []) {
      if (c.tid == null || c.tid === actor || !enPantalla.has(c.tid)) continue;
      if (!porTid.has(c.tid)) {
        porTid.set(c.tid, { tid: c.tid, nombre: c.nombre || '', simbolos: [], path: c.path || '' });
      }
      const v = porTid.get(c.tid);
      if (c.simbolo && !v.simbolos.includes(c.simbolo)) v.simbolos.push(c.simbolo);
    }
    return [...porTid.values()];
  }

  /** El aviso DESDE EL LADO de la víctima: quién le rompió qué, y dónde lo usa. */
  function mensajeColisionVictima(actorNombre, simbolos, path, t) {
    const tr = t || (s => s);
    const quien = actorNombre || tr('Otro agente');
    const simbs = (simbolos || []).map(s => '`' + s + '`').join(', ');
    const base = `${quien} ${tr('borró')} ${simbs} ${tr('que usás')}`;
    return path ? `${base} — ${path}` : base;
  }

  const pure = { grupoDe, estadoDeCard, companeros, tituloVinculo, diffEstados,
                 victimasDeColision, mensajeColisionVictima };
  global.SwarmLink = Object.assign(global.SwarmLink || {}, { _pure: pure });

  if (typeof document === 'undefined') return;   // tests Node: solo _pure

  // ─── Capa DOM ──────────────────────────────────────────────────────────────

  let _grupos = [];
  let _estados = new Map();
  let _pid = null;           // el proyecto de estos grupos: se lo pasamos al overlay

  const t = (s) => (global.JarvisI18n?.t ? global.JarvisI18n.t(s) : s);

  function _tidsEnPantalla() {
    return [...document.querySelectorAll('.terminal-card')]
      .map(c => parseInt(c.id.replace('terminal-card-', ''), 10))
      .filter(n => !Number.isNaN(n));
  }

  function _boton(tid) {
    const card = document.getElementById(`terminal-card-${tid}`);
    if (!card) return null;
    let b = card.querySelector('.t-btn-vinculo');
    if (b) return b;
    const chrome = card.querySelector('.terminal-chrome');
    const max = chrome?.querySelector('.t-btn-max');
    if (!chrome || !max) return null;
    b = document.createElement('button');
    b.className = 't-btn t-btn-vinculo';
    b.dataset.id = String(tid);
    b.setAttribute('aria-label', t('Ver el vínculo con los otros agentes'));
    // Dos nodos unidos por un hilo, con un pulso que lo recorre: es una
    // miniatura exacta del overlay que se abre al tocarlo. Ícono propio
    // (stroke 1.5, como el resto del set) para no mezclar familias.
    b.innerHTML = `
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="1.5" stroke-linecap="round" aria-hidden="true">
        <line class="v-hilo" x1="8.4" y1="12" x2="15.6" y2="12"/>
        <circle class="v-nodo" cx="5"  cy="12" r="2.6"/>
        <circle class="v-nodo" cx="19" cy="12" r="2.6"/>
        <circle class="v-pulso" cx="12" cy="12" r="1.7"/>
      </svg>`;
    b.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const g = grupoDe(_grupos, tid);
      if (g) global.SwarmOverlay?.abrir(g.id, tid, _pid);
    });
    chrome.insertBefore(b, max);
    return b;
  }

  function _pintar(tid, estado) {
    const card = document.getElementById(`terminal-card-${tid}`);
    if (!card) return;
    if (!estado) {
      card.querySelector('.t-btn-vinculo')?.remove();
      card.classList.remove('con-vinculo', 'con-vinculo-colision');
      return;
    }
    const b = _boton(tid);
    if (!b) return;
    const g = grupoDe(_grupos, tid);
    b.classList.toggle('es-colision', estado === 'colision');
    b.title = tituloVinculo(g, tid, t);
    card.classList.add('con-vinculo');
    card.classList.toggle('con-vinculo-colision', estado === 'colision');
  }

  function aplicar(grupos) {
    _grupos = Array.isArray(grupos) ? grupos : [];
    const { estados, cambios } = diffEstados(_estados, _grupos, _tidsEnPantalla());
    _estados = estados;
    for (const [tid, est] of cambios) _pintar(tid, est);
    global.SwarmOverlay?.refrescarSiAbierto?.(_grupos);
  }

  const HIT_MS = 3600;   // dura el pulso "te golpearon" (3 ciclos de sw-hit-victima)

  /**
   * Una colisión ACABA de romper algo que usan otras cards en pantalla → a cada
   * VÍCTIMA se le da un pulso de atención (una vez) en su card y un aviso desde
   * SU lado. Es la única señal del enjambre pensada para la víctima, y va por la
   * animación — NUNCA por su pane (decisión del usuario: no interrumpirla).
   */
  function alertarColision(event) {
    const actor = (event && event.terminal_nombre) || '';
    for (const v of victimasDeColision(event, _tidsEnPantalla())) {
      const card = document.getElementById(`terminal-card-${v.tid}`);
      if (card) {
        card.classList.remove('t-colision-hit');
        void card.offsetWidth;              // reinicia la animación si ya estaba corriendo
        card.classList.add('t-colision-hit');
        setTimeout(() => card.classList.remove('t-colision-hit'), HIT_MS);
      }
      global.toast?.(`⚠ ${mensajeColisionVictima(actor, v.simbolos, v.path, t)}`, 'info');
    }
  }

  async function recargar(projectId) {
    if (projectId == null) return;
    _pid = projectId;
    try {
      const r = await fetch(`/api/swarm/grupos/${projectId}`, { credentials: 'same-origin' });
      if (!r.ok) return;
      const d = await r.json();
      // Respuesta de un proyecto que ya dejamos (cambio rápido A→B): aplicarla
      // diffeaba contra las cards de B y les borraba los íconos de vínculo.
      if (String(_pid) !== String(projectId)) return;
      aplicar(d.grupos);
    } catch { /* sin red: se reintenta en el próximo evento */ }
  }

  function onProjectChanged(projectId) {
    for (const tid of _estados.keys()) _pintar(tid, null);
    _estados = new Map();
    _grupos = [];
    _pid = projectId;
    recargar(projectId);
  }

  global.SwarmLink = Object.assign(global.SwarmLink, {
    _pure: pure, aplicar, alertarColision, recargar, onProjectChanged,
    grupos: () => _grupos,
  });
})(typeof window !== 'undefined' ? window : globalThis);
