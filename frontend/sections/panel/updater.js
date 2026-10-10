// JARVIS — In-app updater (Sprint 3+): banner "nueva versión" abajo de la franja
// + modal con las novedades + reinicio del server con animación.
// Autosuficiente: crea su propio DOM y se auto-inicia. Solo necesita el <script>.
(function (global) {
  'use strict';

  // ── Lógica pura (testeable en Node) ──
  const CTA = 'Actualizar ahora';
  function debeMostrar(info) { return !!(info && info.hay_update); }
  function etiquetaBanner() { return CTA; }   // texto fijo y simple, no depende del nº de versión
  // Versión "linda" para MOSTRAR: una minor redonda (patch y hotfix en cero,
  // 1.7.00 o 1.7.00.0) se muestra como 1.7; apenas sube cualquier segmento
  // (1.7.01, 1.7.00.1, 1.6.97.3) se muestra completa como viene. Solo afecta lo
  // que se ve — el número interno de VERSION no cambia.
  function mostrarVersion(v) {
    if (!v || typeof v !== 'string') return v || '';
    const seg = v.split('.');
    if (seg.length < 3) return v;
    const patch = Number(seg[2]);
    const hotfix = seg.length >= 4 ? Number(seg[3]) : 0;
    return (patch === 0 && hotfix === 0) ? `${seg[0]}.${seg[1]}` : v;
  }
  // Línea sutil bajo el CTA: el salto de versión "v1.5.0 → v1.5.1" cuando se
  // conoce (el server bumpea VERSION al aplicar); si no, "Jarvis vX.Y.Z".
  function lineaVersion(info) {
    if (!info) return '';
    if (info.corriendo && info.proxima && info.proxima !== info.corriendo)
      return `v${mostrarVersion(info.corriendo)} → v${mostrarVersion(info.proxima)}`;
    const v = info.proxima || info.disponible;
    return v ? `Jarvis v${mostrarVersion(v)}` : '';
  }
  // Estado del banner: 'update' = burbuja con glow + CTA · 'aldia' = píldora
  // quieta con la versión corriente · 'oculto' = sin info (server viejo que no
  // manda 'corriendo'). El banner ya NO mira agentes_trabajando: el gate vive en
  // el backend, que prende hay_update SOLO cuando hay un COMMIT NUEVO (= una
  // tarea TERMINADA y commiteada). Así "Actualizar ahora" aparece apenas una
  // terminal commitea lo suyo y se aplica lo terminado, aunque otra terminal de
  // Jarvis siga trabajando; las ediciones sin commitear no lo prenden (pedido del
  // usuario 2026-06-18).
  function estadoBanner(info) {
    if (info && info.hay_update) return 'update';
    return (info && info.corriendo) ? 'aldia' : 'oculto';
  }
  // Qué hace falta para aplicar el update pendiente. PURA.
  // server = hace falta POST /restart (hay código del motor nuevo): siempre que
  // haya update — un restart de más es inofensivo.
  function planAplicar(info) {
    return { server: !!(info && info.hay_update) };
  }
  // "Jarvis v1.5.1" — la versión en la que estás (estado al día).
  function lineaAlDia(info) {
    return (info && info.corriendo) ? `Jarvis v${mostrarVersion(info.corriendo)}` : '';
  }
  // 4º segmento (1.5.1.1) = hotfix de esa versión; el badge del modal lo anuncia.
  function esHotfix(v) { return ((v || '').split('.').length >= 4); }
  function etiquetaVersionNueva(v) { return v ? (esHotfix(v) ? `v${mostrarVersion(v)} · hotfix` : `v${mostrarVersion(v)}`) : ''; }

  // Aviso de caída: el WS de eventos se cerró y /api/health no responde →
  // el server murió de verdad (reinicio del usuario u otro cliente).
  // Solo se avisa si no hay otro overlay encima (el flujo de Actualizar manda).
  function debeAvisarCaida(overlayVisible, healthOk) { return !overlayVisible && !healthOk; }
  function textosCaida() {
    return {
      titulo: 'El servidor se está reiniciando',
      sub:    'La página se recarga sola cuando vuelva.',
    };
  }

  // ¿Avisar que el server quedó en uvloop? uvloop traba el event loop ~0.4-1s de
  // forma periódica en este entorno → corta el eco de TODAS las terminales a la
  // vez (el "corte de un segundo" del tipeo). Reiniciar lo pasa a asyncio (sin
  // cortes). NO se avisa si ya hay un update pendiente: ese mismo botón
  // "Actualizar ahora" reinicia con --loop asyncio y lo cura — no duplicar.
  function debeAvisarLoop(info) {
    return !!(info && info.loop_degradado && !info.hay_update);
  }

  // ── Novedades en inglés ──
  // Los ítems del modal son texto LIBRE de los mensajes de commit (español): el
  // i18n de diccionario no los cubre, así que se traducen vía red (Google, gratis)
  // y se cachean por texto normalizado para no re-pedirlos al re-abrir el modal.
  function normNov(s) { return (s == null ? '' : String(s)).replace(/\s+/g, ' ').trim(); }
  // PURA: de una lista de novedades ES, las ÚNICAS que faltan traducir (no están
  // en la cache) — es lo único que se manda a la red.
  function novedadesFaltantes(items, cache) {
    const out = [], visto = {};
    (items || []).forEach(it => {
      const k = normNov(it);
      if (!k || (cache && k in cache) || visto[k]) return;
      visto[k] = 1; out.push(it);
    });
    return out;
  }
  // PURA: mapea cada novedad ES a su traducción cacheada, o el ORIGINAL si falta
  // (degradación elegante: sin traducción, la novedad queda en español).
  function novedadesTraducidas(items, cache) {
    return (items || []).map(it => {
      const k = normNov(it);
      return (cache && cache[k]) ? cache[k] : it;
    });
  }
  // PURA: el texto a MOSTRAR en el PRIMER render de cada novedad. En inglés usa la
  // traducción cacheada (o el original si todavía no está); en español, el
  // original. Es lo que MATA el flash: el <li> nace ya en el idioma correcto en
  // vez de pintarse en español y recién después cambiarse por red.
  function novedadesRender(items, cache, lang) {
    return (lang === 'en') ? novedadesTraducidas(items, cache) : (items || []).slice();
  }

  // Gate del reload post-update. PURA. El frontend NO recarga apenas el server
  // acepta conexiones (health=200 / boot_id nuevo) sino cuando terminó de
  // reconciliar las sesiones tmux (/api/system/ready → {ready}). Recargar antes =
  // la página fresca re-attachea las N terminales contra un tmux todavía
  // arrancando → seeds vacíos/tardíos → cards negras + scroll muerto (el bug del
  // update). `resp` = el JSON de /ready (o null si el fetch falló: server todavía
  // re-execando). Solo ready===true recarga; pasado el tope SIN estar listo se
  // rinde (fail-open: el usuario recarga a mano, nunca un loop infinito).
  //
  // `saludOk` (2026-08-08) = pings CONSECUTIVOS sanos a /api/health. El gate
  // era fail-CERRADO: si `ready` no viraba nunca (reconcile colgado, semáforo
  // inaccesible, skew tras el re-exec) el usuario se comía DOS MINUTOS detrás
  // del overlay "Actualizando…" con el server ya de vuelta y andando — el "no
  // puedo usar el workspace" del reporte. Con el server contestando de forma
  // SOSTENIDA se recarga igual: entrar un toque antes de que la reconciliación
  // termine es un mal MENOR (y el overlay post-reload igual espera a que la
  // cola de attaches drene) al lado de una pantalla secuestrada.
  // null/undefined = sin medición (wiring viejo) → como antes.
  const SALUD_PARA_RECARGAR = 30;   // ~30s seguidos de server sano
  function decidirRecargaReady(resp, intento, cap, saludOk) {
    if (resp && resp.ready === true) return 'recargar';
    if (saludOk != null && saludOk >= SALUD_PARA_RECARGAR) return 'recargar';
    if (intento > cap) return 'rendirse';
    return 'esperar';
  }

  // Overlay post-reload. PURA. Tras el reload del update, el overlay
  // "Actualizando…" SIGUE tapando el workspace hasta que la cola de attaches
  // (TerminalAttach) drenó — el trabajo pesado (seeds de N terminales) pasa
  // DENTRO del overlay y al levantarse todo responde. Antes el overlay moría
  // con el reload y el usuario veía el workspace congelado 3-5s mientras los
  // seeds se parseaban. estado = TerminalAttach.estado() o null (módulo no
  // cargado). drenadaMs = hace cuánto la cola está drenada de forma SOSTENIDA
  // (lo trackea el wiring): el drenado marca "llegaron los seeds", pero el
  // parseo + el asentamiento del server recién reiniciado siguen unos
  // instantes — levantar el overlay en el drenado pelado dejaba el primer
  // scroll cayendo en la tormenta (se sentía muerto ~5s, reporte 2026-07-11).
  // pingsRapidos (2026-07-11, 2ª vuelta del mismo reporte): el settle FIJO de
  // 1.2s también se levantaba en plena tormenta — la rueda es un ida-y-vuelta
  // por el server recién re-ejecutado, y "1.2s drenada" no dice nada de si ESE
  // server ya respira. El wiring pinguea /api/health y cuenta las respuestas
  // rápidas CONSECUTIVAS (la misma cola de eventos que relaya la rueda; el
  // fetch además se retrasa si el main thread del browser sigue parseando
  // seeds — mide las dos puntas). Se levanta con ≥3 seguidas; null = sin
  // medición (compat) → como antes. Tope de gracia: drenada ≥5s se levanta
  // igual (caja bajo carga crónica del enjambre: taparla más no ayuda).
  // Settle 1.2s + piso de vida 800ms (sin parpadeo); sin cola 2.5s; failsafe
  // duro 12s (jamás secuestrar la página).
  function decidirOverlayPostReload(estado, elapsedMs, drenadaMs, pingsRapidos) {
    if (elapsedMs >= 12000) return 'ocultar';
    const st = estado || { pendientes: 0, activos: 0, usada: false };
    if (st.usada) {
      const drenada = st.pendientes === 0 && st.activos === 0;
      if (!drenada) return 'mantener';
      if ((drenadaMs || 0) >= 5000) return 'ocultar';
      const servidorRespira = (pingsRapidos == null) || pingsRapidos >= 3;
      return ((drenadaMs || 0) >= 1200 && elapsedMs >= 800 && servidorRespira)
        ? 'ocultar' : 'mantener';
    }
    return (elapsedMs >= 2500) ? 'ocultar' : 'mantener';
  }

  // Tiles del overlay post-reload. PURA. Un mini-terminal por attach, en orden
  // listas ('on') → conectando ('run') → esperando (''), + contador
  // "hechos/total" (numerales puros: no pasa por el diccionario i18n). Cap
  // defensivo en 12 tiles (MAX_TERMINALES) — el contador sigue diciendo la verdad.
  function tilesOverlay(estado) {
    const st = estado || {};
    const hechos = st.hechos || 0, activos = st.activos || 0, pendientes = st.pendientes || 0;
    const total = hechos + activos + pendientes;
    if (!st.usada || total <= 0) return { tiles: [], texto: '' };
    const tiles = [];
    for (let i = 0; i < total && tiles.length < 12; i++) {
      tiles.push(i < hechos ? 'on' : (i < hechos + activos ? 'run' : ''));
    }
    return { tiles, texto: `${hechos}/${total}` };
  }

  const pure = { debeMostrar, etiquetaBanner, lineaVersion, esHotfix, etiquetaVersionNueva,
                 mostrarVersion, debeAvisarCaida, textosCaida, estadoBanner, lineaAlDia,
                 debeAvisarLoop, novedadesFaltantes, novedadesTraducidas, novedadesRender,
                 planAplicar, decidirRecargaReady, decidirOverlayPostReload, tilesOverlay };
  global.JarvisUpdater = Object.assign(global.JarvisUpdater || {}, { _pure: pure });
  if (typeof document === 'undefined') return;   // tests Node: solo _pure

  let _info = null, _banner = null, _overlay = null, _loopBanner = null;
  let _abriendo = false;          // reentrancy: _abrirModal espera la traducción antes de pintar
  let _prewarmPromise = null;     // traducción de novedades disparada en background (pre-warm)

  const _tradCache = {};   // { novedadES normalizada -> inglés } — traducciones ya pedidas
  const _fetch = (url, opts) => (global.apiFetch ? global.apiFetch(url, opts) : fetch(url, opts));
  const _esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));   // incluye comillas
  const _escAttr = _esc;
  // Idioma actual de la UI (lo maneja el i18n). El modal se crea por innerHTML y
  // el observer traduce el chrome fijo; los ítems dinámicos los traducimos acá.
  const _lang = () => { try { return (global.JarvisI18n && global.JarvisI18n.lang) ? global.JarvisI18n.lang() : 'es'; } catch (_) { return 'es'; } };
  // Textos con interpolación (nº de versión) que el diccionario no puede cubrir.
  const _t = (es, en) => (_lang() === 'en' ? en : es);

  function _ensureBanner() {
    if (_banner) return _banner;
    const strip = document.getElementById('jw-strip');
    if (!strip) return null;
    _banner = document.createElement('button');
    _banner.id = 'jw-update-banner';
    _banner.type = 'button';
    _banner.hidden = true;
    _banner.addEventListener('click', _abrirModal);
    strip.appendChild(_banner);   // abajo de todo (después de #sidebar-nav)
    return _banner;
  }

  // Banner de RENDIMIENTO: el server quedó en uvloop (corta el tipeo). Vive en la
  // franja como el de update; al click abre un modal que ofrece reiniciar a
  // asyncio. Theme-aware (tokens --ob-*); se oculta solo cuando el loop está sano.
  function _ensureLoopBanner() {
    if (_loopBanner) return _loopBanner;
    const strip = document.getElementById('jw-strip');
    if (!strip) return null;
    _loopBanner = document.createElement('button');
    _loopBanner.id = 'jw-loop-banner';
    _loopBanner.type = 'button';
    _loopBanner.hidden = true;
    _loopBanner.title = 'El motor del server (uvloop) corta el tipeo cada tanto — reiniciá para optimizar';
    _loopBanner.innerHTML =
      `<span class="jw-lb-in">` +
        `<span class="jw-lb-ic" aria-hidden="true">⚡</span>` +
        `<span class="jw-lb-tx">Optimizar tipeo</span>` +
      `</span>`;
    _loopBanner.addEventListener('click', _abrirModalLoop);
    strip.appendChild(_loopBanner);
    return _loopBanner;
  }

  // Chip de versión en la BARRA (arriba, al lado de WORKSPACES). Muestra SIEMPRE
  // la versión corriente con un orbe que respira (la "señal de vida" de Jarvis);
  // con update pendiente vira al acento e insinúa la versión nueva (→ v1.5.x).
  // Es informativo: la ACCIÓN de actualizar vive en el banner de la franja.
  function _renderChip(info) {
    const chip = document.getElementById('jw-version-chip');
    if (!chip) return;
    if (!info || !info.corriendo) { chip.hidden = true; return; }
    const upd = !!info.hay_update;
    const next = (info.proxima && info.proxima !== info.corriendo) ? info.proxima
               : (upd ? (info.disponible || '') : '');
    chip.classList.toggle('jw-vc-upd', upd);
    // La identidad (marca Jarvis + wordmark "Jarvis") vive al lado en el
    // header; el chip queda SOLO con la versión (como el mockup).
    chip.innerHTML =
      `<span class="jw-vc-inner">` +
        `<span class="jw-vc-ver">v${_esc(mostrarVersion(info.corriendo))}</span>` +
      `</span>`;
    const nextFmt = mostrarVersion(next);
    chip.title = (upd && next)
      ? _t(`Versión nueva disponible: v${nextFmt} — actualizá desde la franja`,
           `New version available: v${nextFmt} — update from the sidebar`)
      : _t('Estás en la última versión', "You're on the latest version");
    chip.hidden = false;
  }

  async function _chequear() {
    try {
      const r = await _fetch('/api/system/version');
      if (!r.ok) return;
      _info = await r.json();
      // 1) Chip de versión arriba (siempre que el server informe la corriente)
      _renderChip(_info);
      // 2) Banner de la franja: SOLO el botón "Actualizar ahora" cuando hay update
      //    (la versión "al día" ya la muestra el chip de la barra).
      const b = _ensureBanner();
      if (!b) return;
      if (estadoBanner(_info) === 'update') {
        // Barra con flecha: badge ↑ que late + "Actualizar ahora" + salto de versión.
        // (Sin agentes de Jarvis trabajando — si los hubiera, el backend no prende
        // hay_update y estadoBanner devuelve 'aldia'/'oculto'.)
        const ver = lineaVersion(_info);
        // Cara hover como el mock: v_vieja tachada → v_nueva.
        const vOld = mostrarVersion(_info.corriendo);
        const vNew = mostrarVersion((_info.proxima && _info.proxima !== _info.corriendo) ? _info.proxima : (_info.disponible || _info.corriendo));
        const verHTML = (vOld !== vNew)
          ? `<s>v${_esc(vOld)}</s> → v${_esc(vNew)}`
          : `${_esc(ver)}`;
        b.disabled = false;
        b.removeAttribute('title');
        // Cara idle: marca Jarvis ploteándose + CTA (bilingüe). Cara hover:
        // el salto de versión (aparece SOLO al pasar el mouse). Ver panel.css.
        b.innerHTML =
          `<span class="jw-up-in">` +
            `<span class="jw-up-face jw-up-idle">` +
              `<svg class="jw-up-mark" viewBox="0 0 64 64" fill="none" aria-hidden="true">` +
                `<path class="jw-up-trazo" d="M12 46 L24 35 L32 41 L50 15" pathLength="100" stroke="currentColor" stroke-width="7" stroke-linecap="round" stroke-linejoin="miter" stroke-miterlimit="5"/>` +
                `<g class="jw-up-chispa">` +
                  `<circle cx="50" cy="15" r="7.5" fill="currentColor" opacity=".35"/>` +
                  `<circle cx="50" cy="15" r="3.4" fill="#fff"/>` +
                  `<g stroke="#fff" stroke-width="1.6" stroke-linecap="round">` +
                    `<path d="M50 5.5 V9.5"/><path d="M50 20.5 V24.5"/><path d="M40.5 15 H44.5"/><path d="M55.5 15 H59.5"/>` +
                  `</g>` +
                `</g>` +
              `</svg>` +
              `<b>${_esc(_t('Actualizar ahora', 'Update now'))}</b>` +
            `</span>` +
            (ver ? `<span class="jw-up-face jw-up-hover" aria-hidden="true"><b>${verHTML}</b></span>` : ``) +
          `</span>`;
        b.hidden = false;
        // Pre-calentar la traducción de las novedades YA (si la UI está en inglés)
        // para que el modal, al abrirse, aparezca directo en inglés — sin flash.
        _prewarmNovedades();
      } else {
        b.hidden = true;   // al día / oculto → la versión vive en el chip de la barra
      }
      // 3) Banner de rendimiento: si el server quedó en uvloop (cortes de tipeo)
      //    y NO hay update pendiente, ofrecer reiniciar para optimizar.
      const lb = _ensureLoopBanner();
      if (lb) lb.hidden = !debeAvisarLoop(_info);
    } catch (_) { /* silencioso: el aviso es informativo */ }
  }

  // Todas las novedades (ítems) aplanadas de los grupos — lo que se traduce.
  function _novedadesItems() {
    return ((_info && _info.novedades) || []).reduce((a, g) => a.concat(g.items || []), []);
  }

  // Novedades agrupadas por área (auto-detectadas server-side). Si no hay, un
  // mensaje genérico lindo. Todo escapado (_esc) — el texto viene de commits.
  // Los <li> son texto libre de los commits (español): se marcan con data-es
  // (el original) dentro de un <ul data-i18n-skip> para que el observer del i18n
  // NO los toque. Con la UI en inglés el <li> NACE traducido (novedadesRender lee
  // la cache ya pre-calentada) → sin flash español→inglés; si algún ítem todavía
  // no está cacheado, _localizarNovedades() lo completa por red. El nombre de área
  // (jw-up-cat-name) SÍ lo cubre el diccionario.
  function _novedadesHTML() {
    const grupos = (_info && _info.novedades) || [];
    if (!grupos.length) {
      return `<p class="jw-up-empty">Mejoras y arreglos varios. El servidor se reinicia unos segundos y la página se recarga sola — los agentes siguen trabajando.</p>`;
    }
    const lang = _lang();
    return `<div class="jw-up-cats">` + grupos.map(g => {
      const items = g.items || [];
      const disp = novedadesRender(items, _tradCache, lang);
      return `<div class="jw-up-cat">` +
        `<div class="jw-up-cat-ic" aria-hidden="true">${_esc(g.icon || '✨')}</div>` +
        `<div>` +
          `<div class="jw-up-cat-name">${_esc(g.area || '')}</div>` +
          `<ul data-i18n-skip>` + items.map((it, i) =>
            `<li data-es="${_escAttr(it)}">${_esc(disp[i])}</li>`).join('') + `</ul>` +
        `</div>` +
      `</div>`;
    }).join('') + `</div>`;
  }

  // Pide al backend (lote, gratis vía Google) la traducción de los ítems que
  // FALTAN y los guarda en _tradCache. Resuelve cuando terminó (o falló: los que
  // no se pudieron quedan sin cachear → se muestran en español). Es el único punto
  // que toca la red; lo comparten el pre-warm y la localización del modal.
  async function _traducirNovedades(items) {
    const faltan = novedadesFaltantes(items, _tradCache);
    if (!faltan.length) return;
    try {
      const r = await _fetch('/api/voice/translate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ texts: faltan }),
      });
      if (r.ok) {
        const outs = ((await r.json()) || {}).texts || [];
        faltan.forEach((es, i) => { if (outs[i]) _tradCache[normNov(es)] = outs[i]; });
      }
    } catch (_) { /* sin red: las novedades quedan en español */ }
  }

  // Pre-calienta la cache en background apenas se detecta el update (o al cambiar a
  // inglés): así, cuando el usuario abre el modal, la traducción YA está y el <li>
  // nace en inglés. Guarda la promesa para que _abrirModal la espere si hace falta
  // (sin disparar un segundo pedido). En español no hace nada.
  function _prewarmNovedades() {
    if (_lang() !== 'en') return null;
    _prewarmPromise = _traducirNovedades(_novedadesItems());
    return _prewarmPromise;
  }

  // Traduce las novedades del modal ABIERTO al idioma actual. En español restaura
  // el original; en inglés completa por red lo que falte (normalmente ya está
  // cacheado por el pre-warm) y lo pinta. Sin red → quedan en español.
  async function _localizarNovedades(root) {
    if (!root) return;
    const lis = root.querySelectorAll('li[data-es]');
    if (!lis.length) return;
    if (_lang() !== 'en') {
      lis.forEach(li => { if (li.textContent !== li.dataset.es) li.textContent = li.dataset.es; });
      return;
    }
    const originales = Array.from(lis, li => li.dataset.es);
    await _traducirNovedades(originales);
    if (_lang() !== 'en') return;   // el usuario cambió de idioma mientras esperábamos
    const trads = novedadesTraducidas(originales, _tradCache);
    lis.forEach((li, i) => { if (li.textContent !== trads[i]) li.textContent = trads[i]; });
  }

  async function _abrirModal() {
    if (!_info || _overlay || _abriendo) return;
    // GARANTÍA anti-flash: en inglés, asegurar la traducción ANTES de pintar. El
    // pre-warm de _chequear suele dejarla lista (esto no espera nada); si la cache
    // sigue fría, espera la MISMA traducción en vuelo (o la arranca) — sin pedido
    // duplicado — para que el modal abra ya en inglés en vez de flashear español.
    if (_lang() === 'en' && novedadesFaltantes(_novedadesItems(), _tradCache).length) {
      _abriendo = true;
      try { await (_prewarmPromise || _prewarmNovedades()); } finally { _abriendo = false; }
      if (_overlay || !_info) return;   // se abrió/cerró algo mientras esperábamos
    }
    const badge = _esc(etiquetaVersionNueva(_info.proxima || _info.disponible));
    _overlay = document.createElement('div');
    _overlay.className = 'jw-up-overlay';
    _overlay._esNovedades = true;   // el listener de idioma re-traduce sus ítems
    _overlay.innerHTML =
      `<div class="jw-up-card" role="dialog" aria-modal="true" aria-label="Novedades de la nueva versión">
         <div class="jw-up-head-row">
           <div class="jw-up-medal" aria-hidden="true">✨</div>
           <div class="jw-up-meta">
             <div class="jw-up-eyebrow">Actualización disponible</div>
             <div class="jw-up-title">Novedades</div>
           </div>
           ${badge ? `<span class="jw-up-badge">${badge}</span>` : ``}
         </div>
         <p class="jw-up-sub">Vas a cambiar a la nueva versión. El servidor se reinicia unos segundos y la página se recarga sola — los agentes siguen trabajando.</p>
         ${_novedadesHTML()}
         <div class="jw-up-actions">
           <button class="jw-up-btn-ghost" id="jw-up-cancel" type="button">Cancelar</button>
           <button class="jw-up-btn-go" id="jw-up-go" type="button">Actualizar y reiniciar</button>
         </div>
       </div>`;
    document.body.appendChild(_overlay);
    _localizarNovedades(_overlay);   // ítems de commits → inglés si corresponde
    _overlay.querySelector('#jw-up-cancel').addEventListener('click', _cerrar);
    _overlay.querySelector('#jw-up-go').addEventListener('click', _aplicar);
    _overlay.addEventListener('click', e => { if (e.target === _overlay) _cerrar(); });
  }

  // Modal del banner de rendimiento: explica el uvloop y ofrece reiniciar. Reusa
  // _actualizar (POST /api/system/restart → re-exec con --loop asyncio → recarga):
  // sin update pendiente el restart NO bumpea versión, solo cambia el loop.
  function _abrirModalLoop() {
    if (_overlay) return;
    _overlay = document.createElement('div');
    _overlay.className = 'jw-up-overlay';
    _overlay.innerHTML =
      `<div class="jw-up-card" role="dialog" aria-modal="true" aria-label="Optimizar el tipeo">
         <div class="jw-up-head-row">
           <div class="jw-up-medal" aria-hidden="true">⚡</div>
           <div class="jw-up-meta">
             <div class="jw-up-eyebrow">Rendimiento</div>
             <div class="jw-up-title">Optimizá el tipeo de las terminales</div>
           </div>
         </div>
         <p class="jw-up-sub">El servidor está usando <b>uvloop</b> como motor, que en este entorno traba el procesamiento cada tanto y hace que el tipeo se corte un instante en todas las terminales a la vez. Reiniciar lo cambia a <b>asyncio</b> (sin cortes). El servidor vuelve en unos segundos y la página se recarga sola — los agentes siguen trabajando.</p>
         <div class="jw-up-actions">
           <button class="jw-up-btn-ghost" id="jw-up-cancel" type="button">Ahora no</button>
           <button class="jw-up-btn-go" id="jw-up-go" type="button">Reiniciar y optimizar</button>
         </div>
       </div>`;
    document.body.appendChild(_overlay);
    _overlay.querySelector('#jw-up-cancel').addEventListener('click', _cerrar);
    _overlay.querySelector('#jw-up-go').addEventListener('click', _reiniciarMotorSolo);
    _overlay.addEventListener('click', e => { if (e.target === _overlay) _cerrar(); });
  }

  function _cerrar() { _overlay?.remove(); _overlay = null; }

  // Suelta el overlay que esté en pantalla SIN cancelar la espera: el waiter de
  // recargarCuandoListo sigue vivo y recarga solo cuando el server vuelva. Se
  // baja `recargando` para que las terminales retomen su auto-reintento. Lo usa
  // el camino 'rendirse' del gate (fail-open a los ~2 min). El botón "Seguir
  // usando Jarvis" que llamaba acá se ELIMINÓ (pedido del usuario 2026-08-08):
  // la salida correcta no es un botón de escape sino que el cartel de caída no
  // bloquee nada (es un aviso flotante, ver _mostrarCaida).
  function _soltarOverlay() {
    _cerrar();
    global.JarvisUpdater.recargando = false;
  }

  // Aplica el update pendiente. Es lo que engancha el botón del modal de
  // NOVEDADES. El modal de RENDIMIENTO (uvloop) usa _reiniciarMotor directo
  // (siempre un restart pelado).
  async function _aplicar() {
    const card = _overlay.querySelector('.jw-up-card');
    const plan = planAplicar(_info);
    if (plan.server) {
      await _reiniciarMotor(card);
    } else {
      _cerrar();   // nada que aplicar (no debería pasar con el banner visible)
    }
  }

  // POST /restart con el canary + espera-y-recarga.
  async function _reiniciarMotor(card) {
    card.classList.add('actualizando');
    card.innerHTML =
      `<div class="jw-up-spinner" aria-hidden="true"></div>
       <div class="jw-up-head">Actualizando…</div>
       <p class="jw-up-sub" id="jw-up-status">Verificando que el código nuevo arranque…</p>`;
    // Si el reinicio no cierra, el gate se resuelve solo: fail-open por salud
    // sostenida o 'rendirse' a los ~2 min (decidirRecargaReady) — sin botones.
    // El server corre un canary de arranque antes del re-exec: si el código
    // nuevo no importa, contesta 409 y NO se reinicia — mostrar el porqué.
    let fallo = null;
    try {
      const r = await _fetch('/api/system/restart', { method: 'POST' });
      if (!r.ok) {
        const d = await r.json().catch(() => null);
        fallo = (d && d.error) ? d : { error: 'El servidor rechazó la actualización.' };
      }
    } catch (_) { /* la conexión se corta cuando el re-exec mata el proceso: camino feliz */ }
    if (fallo) { _mostrarFalloCanary(card, fallo); return; }
    recargarCuandoListo();
  }

  // Modal de rendimiento (uvloop): reinicio pelado del motor.
  function _reiniciarMotorSolo() {
    _reiniciarMotor(_overlay.querySelector('.jw-up-card'));
  }

  function _mostrarFalloCanary(card, fallo) {
    card.classList.remove('actualizando');
    card.innerHTML =
      `<div class="jw-up-head-row">
         <div class="jw-up-medal" aria-hidden="true">🛡️</div>
         <div class="jw-up-meta">
           <div class="jw-up-eyebrow">No se aplicó</div>
           <div class="jw-up-title">La versión nueva no arranca</div>
         </div>
       </div>
       <p class="jw-up-sub">${_esc(fallo.error || '')} El servidor actual sigue corriendo normalmente.</p>
       ${fallo.detalle ? `<pre class="jw-up-err">${_esc(fallo.detalle)}</pre>` : ``}
       <div class="jw-up-actions">
         <button class="jw-up-btn-ghost" id="jw-up-cerrar" type="button">Entendido</button>
       </div>`;
    card.querySelector('#jw-up-cerrar').addEventListener('click', _cerrar);
  }

  // El server tarda unos segundos en volver Y en reconciliar las sesiones tmux.
  // Pollea /api/system/ready (abierto, como /health) y recarga recién cuando el
  // server volvió Y terminó de reconciliar — NO apenas acepta conexiones.
  // Recargar antes = la página fresca re-attachea las N terminales contra un tmux
  // todavía arrancando → seeds vacíos/tardíos → cards negras + scroll muerto (el
  // bug del update). La decisión es PURA (decidirRecargaReady). Reentrante: el
  // botón y el reload por boot_id (workspace.js) pueden llamarlo a la vez — corre
  // un solo waiter. Antes polleaba /api/health, que da 200 apenas el proceso
  // acepta conexiones (sin esperar el reconcile) — esa era la raíz del bug.
  let _esperandoReady = false;
  function recargarCuandoListo() {
    if (_esperandoReady) return;
    _esperandoReady = true;
    // Señal para el resto del frontend (auditoría 2026-07-02): la página VA a
    // recargarse — el onclose de cada terminal (terminal.js) no debe auto-
    // reintentar su attach en paralelo (era el "doble re-attach por reinicio":
    // N terminales attacheaban por el retry Y de nuevo por el reload).
    global.JarvisUpdater.recargando = true;
    let intentos = 0;
    let saludOk = 0;    // pings sanos CONSECUTIVOS: el fail-open del gate
    const tick = async () => {
      intentos++;
      let resp = null;
      try {
        const r = await fetch('/api/system/ready', { cache: 'no-store' });
        if (r.ok) resp = await r.json().catch(() => null);
      } catch (_) { /* server todavía cayéndose/levantando */ }
      // El /ready que CONTESTA (aunque diga ready:false) ya prueba que el server
      // está de pie: es la señal de salud, sin un segundo pedido por vuelta.
      saludOk = resp ? saludOk + 1 : 0;
      _pintarEspera(intentos, saludOk);
      const d = decidirRecargaReady(resp, intentos, 120, saludOk);   // ~2 min de gracia
      if (d === 'recargar') {
        // Continuidad del overlay a través del reload: la página fresca lo
        // re-muestra hasta que los attaches de las terminales drenaron (los
        // seeds ya se aplicaron) — el trabajo pesado pasa DENTRO del
        // "Actualizando…" y al levantarse todo responde (sin freeze visible).
        try { sessionStorage.setItem(_REANUDAR_KEY, '1'); } catch (_) { /* sin storage */ }
        location.reload(); return;
      }
      if (d === 'rendirse') {
        // Fail-open (reconcile colgado > 2min): NO recargar a ciegas. Soltar los
        // guardas para que las terminales se recuperen solas (auto-reintento) y
        // que un boot_id posterior pueda reintentar el gate. Y LEVANTAR el
        // cartel: antes solo se le cambiaba el texto, así que el overlay seguía
        // tapando el workspace para siempre — rendirse dejaba al usuario igual
        // de encerrado que esperar.
        _esperandoReady = false;
        _soltarOverlay();
        try {
          global.toast?.(_t('El servidor tardó en volver — si algo quedó raro, recargá la página.',
                            'The server took too long — reload the page if anything looks off.'), 'warn', 7000);
        } catch (_) { /* sin toast: el overlay ya se fue, que es lo que importa */ }
        return;
      }
      setTimeout(tick, 1000);
    };
    setTimeout(tick, 2000);   // darle tiempo a morir antes de pollear
  }

  // Feedback de la espera: sin esto el cartel se ve CONGELADO (el texto era fijo
  // y no cambiaba nunca) y no hay forma de saber si el server está volviendo o
  // si la app se colgó. Los segundos y el estado se pintan en vivo; el texto va
  // por _t (lleva número interpolado, el diccionario no lo cubre).
  function _pintarEspera(intentos, saludOk) {
    const s = document.getElementById('jw-up-status');
    if (!s || intentos < 5) return;   // los primeros segundos, el texto original
    const seg = intentos + 2;         // el primer tick sale a los 2s
    s.textContent = saludOk
      ? _t(`El servidor volvió — terminando de acomodar las terminales… (${seg}s)`,
           `Server is back — settling the terminals… (${seg}s)`)
      : _t(`Esperando que el servidor vuelva… (${seg}s)`,
           `Waiting for the server to come back… (${seg}s)`);
  }

  // ── Aviso de reinicio por agente ──
  // workspace.js llama acá cuando el WS de eventos se cierra. Un probe a
  // /api/health distingue el blip de WS (server vivo → nada) del server caído
  // de verdad (un agente lo reinició → overlay + esperar y recargar, el mismo
  // flujo del botón Actualizar pero sin acción del usuario).
  let _verificandoCaida = false;
  let _unloading = false;   // la página se está yendo (reload/cierre): no avisar
  window.addEventListener('pagehide', () => { _unloading = true; });

  async function _avisarPosibleCaida() {
    if (_overlay || _verificandoCaida || _unloading) return;
    _verificandoCaida = true;
    let healthOk = false;
    try { healthOk = (await fetch('/api/health', { cache: 'no-store' })).ok; } catch (_) { /* caído */ }
    _verificandoCaida = false;
    if (_unloading || !debeAvisarCaida(!!_overlay, healthOk)) return;
    _mostrarCaida();
  }

  function _mostrarCaida() {
    // Este cartel lo levanta el frontend SOLO (nadie lo pidió), así que NO
    // bloquea: es un aviso flotante (jw-up-caida, pointer-events:none en el
    // fondo) — el workspace queda a la vista y usable debajo mientras el gate
    // espera al server y recarga cuando vuelve. Antes era un modal full-screen
    // con botón de escape a los 15s; el usuario pidió sacarlo (2026-08-08):
    // sin secuestro no hace falta salida.
    const t = textosCaida();
    _overlay = document.createElement('div');
    _overlay.className = 'jw-up-overlay jw-up-caida';
    _overlay.innerHTML =
      `<div class="jw-up-card actualizando" role="alert" aria-label="${_esc(t.titulo)}">
         <div class="jw-up-spinner" aria-hidden="true"></div>
         <div class="jw-up-head">${_esc(t.titulo)}</div>
         <p class="jw-up-sub" id="jw-up-status">${_esc(t.sub)}</p>
       </div>`;
    document.body.appendChild(_overlay);
    recargarCuandoListo();
  }

  // ── Overlay post-reload: el "Actualizando…" sobrevive al reload ──
  // recargarCuandoListo deja esta marca en sessionStorage (misma pestaña) justo
  // antes del location.reload(); acá la página fresca la levanta y mantiene el
  // overlay tapando el workspace mientras las terminales se re-attachean
  // (escalonadas por attach-queue.js). La decisión de cuándo levantar es PURA
  // (decidirOverlayPostReload) y se pollea cada 250ms contra el estado real de
  // la cola. Sin la marca (F5 normal, primera carga): no hace nada.
  const _REANUDAR_KEY = 'jw_up_reanudar';

  function _overlayPostReload() {
    let marca = null;
    try {
      marca = sessionStorage.getItem(_REANUDAR_KEY);
      sessionStorage.removeItem(_REANUDAR_KEY);
    } catch (_) { /* sin storage */ }
    if (!marca) return;
    // Diagnóstico del freeze post-update (deep work 2026-07-11): muestrea ~30s
    // main-thread/ping/rx/rueda y lo persiste en el server. Solo con la marca.
    try { window.JarvisDiagUpdate?.arrancar?.('post-update'); } catch (_) {}
    if (_overlay) return;   // otro overlay del updater ya está en pantalla (p.ej. app pendiente)
    _overlay = document.createElement('div');
    _overlay.className = 'jw-up-overlay';
    // Los mini-terminales (.jw-up-terms) se van ENCENDIENDO a medida que cada
    // attach entrega su seed (tilesOverlay + estado de la cola): pendiente
    // (tenue) → conectando (pulso de acento) → lista (encendida). El contador
    // es "n/N" en tabular-nums (numerales puros); la frase la traduce el
    // observer del i18n vía diccionario (ES⇆EN).
    _overlay.innerHTML =
      `<div class="jw-up-card actualizando" role="alertdialog" aria-modal="true" aria-label="Actualizando">
         <div class="jw-up-spinner" aria-hidden="true"></div>
         <div class="jw-up-head">Actualizando…</div>
         <div class="jw-up-terms" id="jw-up-terms" aria-hidden="true"></div>
         <p class="jw-up-sub" id="jw-up-status"><span>Reconectando las terminales…</span><span class="jw-up-terms-n" id="jw-up-terms-n" data-i18n-skip></span></p>
       </div>`;
    document.body.appendChild(_overlay);
    // Sin botón de escape: el failsafe duro de 12s (decidirOverlayPostReload)
    // garantiza que este overlay jamás viva más que eso.
    const ov = _overlay;
    const t0 = performance.now();
    let _drenadaDesde = 0;   // instante en que la cola quedó drenada (0 = no drenada)
    let _firmaTiles = '';    // re-render de tiles SOLO cuando el estado cambió
    // Pings de respiración del server: /api/health cada 300ms; una respuesta en
    // <250ms suma al contador de rápidas CONSECUTIVAS, una lenta (o error) lo
    // resetea. Es la medición REAL de que la tormenta pasó (la rueda va y
    // vuelve por esa misma cola de eventos) — el settle fijo mentía en cajas
    // cargadas. Serializado: un ping a la vez (no apilar fetches si el server
    // tarda). Termina cuando el overlay se levanta.
    let _pingsRapidos = 0;
    let _pingVivo = true;
    const _ping = async () => {
      if (!_pingVivo || _overlay !== ov) return;
      const ini = performance.now();
      let rapida = false;
      try {
        const r = await fetch('/api/health', { cache: 'no-store' });
        rapida = r.ok && (performance.now() - ini) < 250;
      } catch (_) { /* server ahogado/re-execando: cuenta como lenta */ }
      _pingsRapidos = rapida ? _pingsRapidos + 1 : 0;
      if (_pingVivo && _overlay === ov) setTimeout(_ping, 300);
    };
    _ping();
    const _pintarTiles = (st) => {
      const { tiles, texto } = tilesOverlay(st);
      const firma = tiles.join(',') + '|' + texto;
      if (firma === _firmaTiles) return;
      _firmaTiles = firma;
      const cont = ov.querySelector('#jw-up-terms');
      const n = ov.querySelector('#jw-up-terms-n');
      if (cont) cont.innerHTML = tiles.map(s => `<i class="jw-t${s ? ' ' + s : ''}"></i>`).join('');
      if (n) n.textContent = texto ? ' · ' + texto : '';
    };
    const tick = () => {
      if (_overlay !== ov) return;   // otro flujo lo reemplazó/cerró: soltar
      const st = (window.TerminalAttach && window.TerminalAttach.estado)
        ? window.TerminalAttach.estado() : null;
      _pintarTiles(st);
      const ahora = performance.now();
      // Trackear el drenado SOSTENIDO: si la cola vuelve a tener trabajo
      // (un attach tardío), el reloj de settle arranca de cero.
      const drenada = !!st && st.usada && st.pendientes === 0 && st.activos === 0;
      if (drenada && !_drenadaDesde) _drenadaDesde = ahora;
      if (!drenada) _drenadaDesde = 0;
      const drenadaMs = _drenadaDesde ? (ahora - _drenadaDesde) : 0;
      if (decidirOverlayPostReload(st, ahora - t0, drenadaMs, _pingsRapidos) === 'mantener') {
        setTimeout(tick, 250);
        return;
      }
      // Levantar con un fade corto: el workspace ya responde debajo.
      _pingVivo = false;
      ov.style.transition = 'opacity .25s ease';
      ov.style.opacity = '0';
      setTimeout(() => { ov.remove(); if (_overlay === ov) _overlay = null; }, 260);
    };
    setTimeout(tick, 250);
  }

  global.JarvisUpdater.chequear = _chequear;
  global.JarvisUpdater.avisarPosibleCaida = _avisarPosibleCaida;
  // El reload por boot_id (workspace.js, server reiniciado) lo llama para esperar
  // el reconcile de tmux antes de recargar (no re-attachear contra un tmux crudo).
  global.JarvisUpdater.recargarCuandoListo = recargarCuandoListo;

  // Al cambiar de idioma (⚙→Apariencia): re-armar el chip (su tooltip lleva el
  // nº de versión, que el diccionario no interpola) y re-traducir las novedades
  // si el modal está abierto (el chrome fijo lo maneja el observer del i18n).
  function _onLang() {
    if (_info) _renderChip(_info);
    // Al pasar a inglés, pre-calentar las novedades aunque el modal esté cerrado:
    // si después lo abren, ya estará en inglés sin esperar (ni flashear).
    if (_lang() === 'en' && estadoBanner(_info) === 'update') _prewarmNovedades();
    if (_overlay && _overlay._esNovedades) _localizarNovedades(_overlay);
  }

  function _init() {
    window.addEventListener('jarvis:lang', _onLang);
    _overlayPostReload();      // continuidad del "Actualizando…" hasta terminales listas
    _chequear();
    setInterval(_chequear, 120000);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _init);
  else _init();
})(typeof window !== 'undefined' ? window : globalThis);
