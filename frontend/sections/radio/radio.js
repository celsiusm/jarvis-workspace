'use strict';
// ═══════════════════════════════════════════════════════════════════════════
// JARVIS · Radio GLOBAL (window.JarvisRadio)
// Mini-player en el header (#jw-bar-right, a la izquierda de los íconos) que
// despliega un popover (Órbita) con lo que suena, buscador de YouTube,
// relacionados, cola, estaciones y ENTRAR A UN CANAL (ver todos sus videos).
//
// La música vive en un iframe de audio colgado de <body> → suena en TODO el
// workspace, sobrevive cambios de proyecto/pestaña y se REANUDA sola tras un
// reinicio, lo más rápido posible, desde el segundo donde quedó (boot temprano).
//
// MULTI-FUENTE (V1): el buscador del popover tiene un pill de FUENTE (YouTube,
// Local, y las que registren otros agentes (spotify). Cada fuente
// registra SU player y SUS endpoints vía window.JarvisRadio.registrarFuente()
// (firma documentada abajo). `_cmd` despacha comandos al player de la pista
// que está sonando: el resto del código no sabe de dónde sale el audio.
//
// El Web Preview YA NO tiene Radio: cuando reproducís un video de YouTube ahí,
// se HANDOFF a esta Radio global (adopta la pista + arma la cola de relacionados
// y sigue). La única vez que la Radio se para es mientras se ve Twitch.
//
// Motor puro reutilizado: window.WebPreviewRadio._pure (preview-radio.js).
// ═══════════════════════════════════════════════════════════════════════════
(function (root) {
  if (root.JarvisRadio) return;

  const KEY = 'jarvis.preview.radio';           // estado de la pista (reanudación)
  const SRCKEY = 'jarvis.preview.radio.src';    // fuente activa del buscador (persistida)
  const YT_ORIGINS = ['https://www.youtube.com', 'https://www.youtube-nocookie.com'];
  const BUSCAR = '/api/orchestrator/preview/buscar?modo=yt&q=';
  const REL = '/api/orchestrator/preview/buscar?modo=ytrel&q=';   // relacionados REALES (q = id de video)
  const MAS = '/api/orchestrator/preview/buscar?modo=ytmas&token=';   // tanda SIGUIENTE de una búsqueda
  // Fuente LOCAL (backend en paralelo): búsqueda/biblioteca/upload de audio.
  const BUSCAR_LOCAL = '/api/orchestrator/preview/buscar?modo=local&q=';
  const LISTAR_LOCAL = '/api/radio/local/listar?carpeta=';
  const SUBIR_LOCAL = '/api/radio/local/subir';

  const RY = () => (root.WebPreviewRadio && root.WebPreviewRadio._pure) || null;

  // ── Íconos (inline) ──
  const I = {
    play:  '<path d="M7 4.5v15l13-7.5z" fill="currentColor" stroke="none"/>',
    pause: '<rect x="6.5" y="5" width="4" height="14" rx="1.2" fill="currentColor" stroke="none"/><rect x="13.5" y="5" width="4" height="14" rx="1.2" fill="currentColor" stroke="none"/>',
    prev:  '<path d="M18 5.5v13L9 12z" fill="currentColor" stroke="none"/><rect x="5" y="5.5" width="2.4" height="13" rx="1" fill="currentColor" stroke="none"/>',
    next:  '<path d="M6 5.5v13L15 12z" fill="currentColor" stroke="none"/><rect x="16.6" y="5.5" width="2.4" height="13" rx="1" fill="currentColor" stroke="none"/>',
    shuffle:'<path d="M16 4h4v4M20 4l-6.5 6.5M16 20h4v-4M20 20l-5-5M4 6l4 4M4 18l8.5-8.5"/>',
    repeat:'<path d="M17 2l3 3-3 3M20 5H8a4 4 0 0 0-4 4v1M7 22l-3-3 3-3M4 19h12a4 4 0 0 0 4-4v-1"/>',
    search:'<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    chevdown:'<path d="M6 9l6 6 6-6"/>', chevup:'<path d="M6 15l6-6 6 6"/>',
    radio: '<circle cx="5" cy="17" r="2.6"/><path d="M8 17V6l11-2.4V14"/><circle cx="17.5" cy="14.5" r="2.6"/>',
    eye:   '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="2.6"/>',
    heart: '<path d="M12 20s-7-4.4-9.3-8.2C1 8.6 2.6 5 6 5c2 0 3.2 1.2 4 2.3C10.8 6.2 12 5 14 5c3.4 0 5 3.6 3.3 6.8C19 15.6 12 20 12 20z"/>',
    clock: '<circle cx="12" cy="12" r="8.4"/><path d="M12 7.5V12l3 2"/>',
    users: '<circle cx="9" cy="8" r="3.4"/><path d="M3.5 20a5.5 5.5 0 0 1 11 0M16 5.2a3.4 3.4 0 0 1 0 6.4M20.5 20a5.5 5.5 0 0 0-4-5.3"/>',
    back:  '<path d="M15 5l-7 7 7 7"/>', list: '<path d="M8 6h12M8 12h12M8 18h12M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>',
    note:  '<path d="M9 18V5l10-2v11"/><circle cx="6.5" cy="18" r="2.6"/><circle cx="16.5" cy="14" r="2.6"/>',
    addq:  '<path d="M4 7h11M4 12h11M4 17h7M18 13v7M14.5 16.5h7"/>',
    volume:'<path d="M4 9h3l5-4v14l-5-4H4z" fill="currentColor" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12"/>',
    mute:  '<path d="M4 9h3l5-4v14l-5-4H4z" fill="currentColor" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M16 9.5l5 5M21 9.5l-5 5"/>',
    upload:'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M17 8l-5-5-5 5M12 3v12"/>',
  };
  const svg = (n, cls) => `<svg class="jr-ico ${cls || ''}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${I[n]}</svg>`;
  const viz = (on) => `<span class="jr-viz${on ? ' on' : ''}"><i></i><i></i><i></i><i></i></span>`;
  // Ícono de upload: window.icon('upload', 18) si la UI global ya lo tiene;
  // si no (cargado 'new' devuelve ''), cae al svg local del set I.
  const _upIco = () => (typeof root.icon === 'function' ? (root.icon('upload', 18) || svg('upload')) : svg('upload'));

  const ST_META = { lofi: 'estudiar', synth: 'nocturno', focus: 'flow', jazz: 'café', chill: 'relax', piano: 'calma' };

  const _esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const _t = (s) => (root.JarvisI18n && root.JarvisI18n.t) ? root.JarvisI18n.t(s) : s;
  const _lang = () => (root.JarvisI18n && root.JarvisI18n.lang && root.JarvisI18n.lang()) || 'es';
  // Los contadores de YouTube llegan en el idioma del backend (es) — ver
  // RY().localizarConteo. Sin esto "vistas"/"lo están viendo" salían crudos en
  // inglés. La etiqueta de fuente usa su variante EN cuando corresponde.
  const _vistas = (t) => {
    const raw = t && t.vistas;
    if (!raw) return '';
    const r = RY();
    return (r && r.localizarConteo) ? r.localizarConteo(raw, _lang()) : raw;
  };
  const _etiquetaFuente = (fu) => fu ? (_lang() === 'en' ? fu.etiqueta_en : fu.etiqueta_es) : 'Local';
  const $ = (sel, ctx) => (ctx || document).querySelector(sel);

  // ── Estado ──
  let _state = null;          // RY estado {track, sonando, t}. track lleva {id,titulo,canal,canalId,thumb,dur,vistas,url}
  let _audio = null, _booted = false, _msgOn = false, _intent = false, _gestoOn = false;
  let _saveTs = 0, _timer = null;
  // PLAYLIST: lo que se VE es lo que SIGUE. `_pl` = {items, idx} (motor puro
  // preview-radio.js): la lista que el usuario mira —búsqueda, canal, estación
  // o relacionados— ES la cola. Tocás una fila y siguen las de abajo, EN ORDEN;
  // nada la reordena por atrás. `_plFuente` sabe de dónde traer MÁS (token de
  // continuación de la búsqueda, o relacionados de la última pista).
  let _pl = { items: [], idx: -1 };
  let _plFuente = null;             // {tipo:'busqueda'|'canal'|'estacion'|'rel', q, token}
  const _played = new Set();        // ids de video ya reproducidos
  const _playedClaves = new Set();  // claves de CANCIÓN ya reproducidas (la misma canción
                                    // vuelve con OTRO id — official/en vivo — y re-sonaba a las 2-3 pistas)
  let _twitch = 0;            // >0 = pausada por Twitch (contador de fuentes)
  let _mounted = false, _open = false, _pane = 'rel', _inChannel = false, _buscando = false;
  let _vol = 80, _muted = false, _lastVol = 80;   // volumen (0-100) + mute, persistidos
  let _repeat = false;                            // repetir el track actual al terminar (persistido)
  let _cur = 0, _dur = 0, _seeking = false;       // progreso: segundo actual / duración / arrastrando
  let _lastPct = 0;                               // último % pintado del seek (detecta saltos hacia atrás)
  let _errSeguidos = 0;      // racha de tracks NO reproducibles (freno anti-tormenta de saltos)
  let _errEnEsteLoad = false; // ya se manejó UN error de esta carga (de-dupe del doble handshake)

  // Memoria POR FUENTE de lo que se ve en #jr-pane-rel: al cambiar la pill se
  // congela {items, fuente, esPl, buscando, q, hint, scrollTop} y al volver se
  // restaura desde el snapshot (sin re-fetch) — cada fuente conserva su vista.
  const _panes = {};   // fuente id → snapshot del pane

  // ── Fuentes de música (registro multi-fuente) ──────────────────────────────
  // Cada fuente aporta su buscador y su player; el resto de la Radio es
  // agnóstico. Registrar la tuya con window.JarvisRadio.registrarFuente({...})
  // (firma EXACTA documentada en la parte pública, abajo). `_intFuente()` es la
  // fuente ACTUAL del buscador; `_fuenteDe(t)` la fuente efectiva de una pista
  // (el track trae `.fuente`; tracks viejos persistidos = youtube).
  const _fuentes = {};          // id → fuente {id, etiqueta_es, etiqueta_en, buscar, mas, relacionados, player}
  const _ordenFuentes = [];     // orden de registro (el de las pills)
  let _fuenteActiva = 'youtube';
  const _intFuente = () => _fuentes[_fuenteActiva] || _fuentes.youtube || null;
  const _fuenteDe = (t, def) => { const r = RY(); return (r && r.fuenteDe) ? r.fuenteDe(t, def) : (((t && t.fuente) || def || 'youtube')); };
  const _filas = (data, fid) => ((data && data.resultados) || []).map((r) => _track(r, fid)).filter(Boolean);

  function _leer() { try { return JSON.parse(localStorage.getItem(KEY) || 'null'); } catch { return null; } }
  function _guardar() { try { const r = RY(); if (r) localStorage.setItem(KEY, JSON.stringify(r.serializar(_state))); } catch {} }
  // Normaliza una fila del backend de la fuente `fuente` (o la fuente de la fila)
  // a una pista de la Radio. Defaults de thumb/url SOLO para youtube: una pista
  // local trae su `url` (archivo) y su `thumb` si los hay; nunca se asume
  // i.ytimg. El `.fuente` queda grabado en el track (viaja con él a la persistencia).
  function _track(r, fuente) {
    if (!r || !r.id) return null;
    const f = fuente || r.fuente || _fuenteActiva || 'youtube';
    const yt = f === 'youtube';
    return { id: r.id, titulo: r.titulo || '', canal: r.canal || '', canalId: r.canalId || null,
      thumb: r.thumb || (yt ? `https://i.ytimg.com/vi/${r.id}/hqdefault.jpg` : ''),
      dur: r.duracion || r.dur || '', vistas: r.vistas || '',
      url: r.url || (yt ? `https://www.youtube.com/watch?v=${r.id}` : ''), fuente: f };
  }

  // ── Motor de audio: un player por fuente ────────────────────────────────────
  // `_cmd(func, args)` es el ROUTER: despacha al player de la pista que está
  // sonando (_state.track.fuente), con el vocabulario de YouTube
  // (playVideo/pauseVideo/seekTo/setVolume/mute/unMute). Los players conocen
  // ese vocabulario y lo traducen a su motor (postMessage iframe / <audio>).
  function _cmd(func, args) {
    const t = _state && _state.track;
    const p = _playerDe(t);
    if (p && p.cmd) p.cmd(func, args || []);
  }
  function _playerDe(t) {
    const fid = _fuenteDe(t);
    return _players[fid] || _players.youtube || null;
  }
  // Adaptador del player de una fuente al vocabulario de la Radio. Los players
  // de V2 (spotify) trae SU propia API (play/pause/seek(ms)/
  // volumen/onEvento) y pueden NO cumplir el contrato mínimo de player acá:
  // este bridge traduce `cmd` (playVideo/pauseVideo/seekTo/setVolume/mute/
  // unMute) a su vocab y suscribe `onEvento` a los canales compartidos
  // (duracion/posicion/play/pausa/fin/error) para que seek, repeat y
  // auto-avance funcionen igual que con el iframe de YouTube.
  function _normalizarPlayer(p) {
    if (!p) return null;
    const q = Object.assign({}, p);
    if (typeof q.cargar !== 'function') q.cargar = () => {};
    if (typeof q.pause !== 'function') q.pause = () => {};
    if (typeof q.cmd !== 'function') {
      let ultVol = _vol;
      q.cmd = (func, args) => {
        const n = Number(args && args[0]);
        if (func === 'playVideo') { if (typeof q.play === 'function') q.play(); }
        else if (func === 'pauseVideo') q.pause();
        else if (func === 'seekTo') { if (typeof q.seek === 'function' && isFinite(n)) q.seek(n * 1000); }
        else if (func === 'setVolume') { ultVol = n; if (typeof q.volumen === 'function') q.volumen(n); }
        else if (func === 'mute') { if (typeof q.volumen === 'function') q.volumen(0); }
        else if (func === 'unMute') { if (typeof q.volumen === 'function') q.volumen(ultVol); }
      };
    }
    if (typeof q.onMensaje !== 'function') q.onMensaje = () => {};
    if (typeof q.destroy !== 'function') q.destroy = () => { try { q.pause(); } catch {} };
    if (typeof q.onEvento === 'function') { try { q.onEvento((ev) => _eventoFuente(q, ev)); } catch {} }
    return q;
  }
  // Eventos del player de una fuente (onEvento) → canales compartidos de la
  // Radio. Solo valen si la pista sonando ES de esta fuente: un evento tardío
  // de un audio ya dejado atrás no pisa el estado actual.
  function _eventoFuente(p, ev) {
    if (!ev) return;
    if (!_state || !_state.track) return;
    if (_playerDe(_state.track) !== p) return;
    const t = ev.tipo;
    if (t === 'play') return _handleInfo({ playerState: 1 });
    if (t === 'pausa') return _handleInfo({ playerState: 2 });
    if (t === 'fin') return _handleInfo({ playerState: 0 });
    if (t === 'duracion' || t === 'posicion') {
      const seg = Number(ev.seg);
      if (isFinite(seg)) return _handleInfo(t === 'duracion' ? { duration: seg } : { currentTime: seg });
    }
    if (t === 'error') {
      if (ev.motivo === 'blocked' || ev.motivo === 'autoplay') { _armarGesto(); return; }
      _handleError(150);   // fatal genérico: saltar (como el local)
    }
  }
  const _players = {};   // fuente id → player (adaptador al vocabulario común)

  // Player YouTube: iframe persistente colgado de <body> (el de SIEMPRE — no se
  // toca su comportamiento, solo se lo envuelve). El iframe lo crea
  // `_asegurarAudio` y el src lo arma `_src` (urlEmbed). El double-handshake
  // del postMessage y onError siguen iguales.
  function _asegurarAudio() {
    if (_audio && _audio.isConnected) return _audio;
    const f = document.createElement('iframe');
    f.className = 'jr-audio'; f.id = 'jarvis-radio-audio'; f.title = 'Radio (audio)';
    f.setAttribute('allow', 'autoplay; encrypted-media'); f.setAttribute('referrerpolicy', 'origin');
    document.body.appendChild(f);
    f.addEventListener('load', () => { _errEnEsteLoad = false; _saludar(); setTimeout(_saludar, 1200); setTimeout(_volApply, 1000); });
    _audio = f; _asegurarListeners();
    return f;
  }
  // Handshake con el player embebido. Además de 'listening' (infoDelivery),
  // hay que SUSCRIBIRSE a onError explícitamente: sin eso YouTube no reenvía
  // los errores de "no reproducible" y un tema bloqueado dejaba la Radio muda.
  function _saludar() {
    try {
      _audio?.contentWindow?.postMessage(JSON.stringify({ event: 'listening', id: 'jarvis-radio', channel: 'widget' }), 'https://www.youtube.com');
      _ytCmd('addEventListener', ['onError']);
    } catch {}
  }
  function _ytCmd(func, args) { try { _audio?.contentWindow?.postMessage(JSON.stringify({ event: 'command', func, args: args || [] }), 'https://www.youtube.com'); } catch {} }
  function _asegurarListeners() { if (_msgOn) return; _msgOn = true; window.addEventListener('message', _onMensaje); }

  // Player LOCAL: <audio> invisible colgado de <body> (misma idea que el
  // iframe). Los events del media element mapean a los MISMOS canales que usa
  // _onMensaje: timeupdate→currentTime, durationchange→duración, play/pause →
  // playerState 1/2, ended→0 (fin), error→onError fatal.
  let _audioLocal = null;
  function _asegurarAudioLocal() {
    if (_audioLocal && _audioLocal.isConnected) return _audioLocal;
    const a = document.createElement('audio');
    a.id = 'jarvis-radio-audio-local'; a.preload = 'metadata';
    a.playsInline = true; a.setAttribute('playsinline', ''); a.hidden = true;
    for (const ev of ['timeupdate', 'durationchange', 'play', 'pause', 'ended', 'error']) a.addEventListener(ev, _evLocal);
    document.body.appendChild(a);
    _audioLocal = a;
    return a;
  }
  function _evLocal(e) {
    const t = _state && _state.track;
    if (_fuenteDe(t) !== 'local') return;   // el audio anterior quedó atrás: sus events son basura
    const a = e.currentTarget;
    if (e.type === 'error') { _handleError(150); return; }
    const info = {};
    if (isFinite(a.duration) && a.duration > 0) info.duration = a.duration;
    if (isFinite(a.currentTime) && a.currentTime > 0) info.currentTime = a.currentTime;
    if (e.type === 'play') info.playerState = 1;
    else if (e.type === 'pause') info.playerState = 2;
    else if (e.type === 'ended') info.playerState = 0;
    if (info.playerState != null || info.duration != null || info.currentTime != null) _handleInfo(info);
  }
  const _playerLocal = {
    cargar(track, opts) {
      if (!track.url) { setTimeout(() => _handleError(150), 0); return; }
      const a = _asegurarAudioLocal();
      const start = Number((opts && opts.start) || 0);
      if (a.getAttribute('src') !== track.url) {
        a.pause(); a.src = track.url;   // cambiar src destronca la anterior
        if (start > 1) a.addEventListener('loadedmetadata', () => { try { a.currentTime = start; } catch {} }, { once: true });
      }
      a.muted = _muted; a.volume = Math.max(0, Math.min(1, _vol / 100));
      if (opts && opts.autoplay === false) { a.pause(); return; }
      if (_twitch) { a.pause(); return; }
      const pr = a.play(); if (pr && pr.catch) pr.catch(() => _armarGesto());
    },
    cmd(func, args) {
      const a = _audioLocal; if (!a || !a.isConnected) return;
      const n = Number(args && args[0]);
      if (func === 'playVideo') { const pr = a.play(); if (pr && pr.catch) pr.catch(() => {}); }
      else if (func === 'pauseVideo') a.pause();
      else if (func === 'seekTo') { if (isFinite(n)) { try { a.currentTime = n; } catch {} } }
      else if (func === 'setVolume') { a.volume = Math.max(0, Math.min(1, (isFinite(n) ? n : _vol) / 100)); }
      else if (func === 'mute') a.muted = true;
      else if (func === 'unMute') a.muted = false;
    },
    onMensaje() {},
    destroy() { const a = _audioLocal; if (!a) return; a.pause(); a.removeAttribute('src'); try { a.load(); } catch {} },
  };

  // ── Estado del player: mensajes/errores compartidos YouTube + Local ─────────
  // (el iframe manda por window 'message'; el <audio> por events) — misma
  // máquina de estado: duración, posición, fin/repeat/auto-avance y la racha de
  // reprodudores NO reproducibles (_errSeguidos). El error de youtube dice la
  // verdad sin maquillaje (101/150 = el dueño no lo deja sonar fuera); el local
  // no tiene ese problema: mensaje genérico.
  function _handleInfo(info) {
    const r = RY(); if (!r) return;
    if (typeof info.duration === 'number' && info.duration > 0) _dur = info.duration;
    if (typeof info.currentTime === 'number' && info.currentTime > 0) {
      _state = r.conPosicion(_state, info.currentTime);
      if (!_seeking) { _cur = info.currentTime; _renderSeek(); }   // barra de progreso viva
    }
    const ps = info.playerState;
    if (ps === 0) {                              // FIN del track → repeat o auto-avance
      _intent = false;
      if (_repeat && _state && _state.track) { _cmd('seekTo', [0, true]); _cmd('playVideo'); return; }
      if (_next()) return;                       // arrancó el siguiente
      if (_state && (_state.sonando || _state.t)) { _state = { track: _state.track, sonando: false, t: 0 }; _renderNow(); _renderMini(); }
    } else if (ps === 1 || ps === 2) {
      const son = ps === 1;
      if (son) _errSeguidos = 0;   // algo sonó de verdad → racha de errores reseteada
      if (_state && son !== _state.sonando) { _state = { track: _state.track, sonando: son, t: _state.t || 0 }; _renderNow(); _renderMini(); }
    }
    const now = Date.now(); if (now - _saveTs > 3000) { _saveTs = now; _guardar(); }
  }
  // Track NO reproducible. YouTube: 101/150 = el dueño no permite reproducirlo
  // fuera de YouTube (VEVO/sellos), 100 = borrado/privado; SOLO esos códigos son
  // fatales (2/5 pueden ser transitorios y saltaban de más). El handshake se
  // manda DOS veces a propósito → el error puede llegar duplicado: _errEnEsteLoad
  // de-dupea a UN error por carga (el duplicado tardío saltaba temas BUENOS).
  // Local: cualquier error es fatal (archivo quebrado / faltante / sin soporte).
  function _handleError(code) {
    if (![100, 101, 150].includes(code)) return;
    if (_errEnEsteLoad) return;
    _errEnEsteLoad = true;
    _errSeguidos += 1;
    const fid = _fuenteDe(_state && _state.track);
    const esLocal = fid === 'local', esYT = fid === 'youtube';
    const titulo = (_state && _state.track && _state.track.titulo) || 'Ese tema';
    if (typeof toast === 'function') {
      toast(_errSeguidos <= 5
        ? (esLocal || !esYT
            ? _t('No se pudo reproducir — saltando ▸')
            : _t('«{t}» no se deja reproducir fuera de YouTube — saltando ▸').replace('{t}', titulo.slice(0, 44)))
        : _t('Varios temas seguidos no se dejan reproducir — elegí otro de la lista'));
    }
    if (_state) { _state = { track: _state.track, sonando: false, t: 0 }; _renderNow(); _renderMini(); }
    if (_errSeguidos <= 5) _next();
  }
  // Mensajes del iframe YouTube (único que usa window 'message'). Solo cuando
  // la pista sonando es youtube: si tocó Local quedan fuera (no romper nada).
  function _onMensaje(e) {
    if (!YT_ORIGINS.includes(e.origin)) return;
    if (!_audio || e.source !== _audio.contentWindow) return;
    if (_fuenteDe(_state && _state.track) !== 'youtube') return;
    let d = e.data; if (typeof d === 'string') { try { d = JSON.parse(d); } catch { return; } }
    if (!d) return;
    if (d.event === 'onError') { _handleError(Number(d.info)); return; }
    if (d.event !== 'infoDelivery' || !d.info) return;
    _handleInfo(d.info);
  }

  function _armarGesto() {
    if (_gestoOn) return; _gestoOn = true;
    const g = () => {
      document.removeEventListener('pointerdown', g, true); document.removeEventListener('keydown', g, true); _gestoOn = false;
      if (!_intent || !_state || !_state.track) return;   // NO depender del iframe: vale para cualquier fuente
      _intent = false; _cmd('playVideo'); if (!_muted) { _cmd('unMute'); _cmd('setVolume', [_vol]); }
    };
    document.addEventListener('pointerdown', g, true); document.addEventListener('keydown', g, true);
  }
  function _guardadoPeriodico() {
    if (_timer) return;
    _timer = setInterval(() => { if (_state && _state.track) _guardar(); }, 5000);
    window.addEventListener('pagehide', () => { if (_state && _state.track) _guardar(); });
  }

  // ── Reproducción ──
  function _src(track, opts) { const r = RY(); return r ? r.urlEmbed(track.id, Object.assign({ origin: location.origin }, opts)) : ''; }

  // Arranca `track`. NO decide qué sigue: eso lo dice la playlist (`_pl`), que
  // arma quien reproduce (_reproducirDeLista / _reproducirSuelto). Lo único que
  // hace acá es RELLENAR por abajo si quedan pocas por delante, para que la
  // música nunca se corte al llegar al final de lo que se ve.
  function play(track, { auto = false } = {}) {
    const r = RY(); if (!r || !track || !track.id) return;
    const prevP = _playerDe(_state && _state.track);
    const p = _playerDe(track);
    _state = r.elegir(_state, track); _booted = true;
    _played.add(track.id);   // marcá como reproducida (para no volver a encolarla)
    const kc = _clave(track); if (kc) _playedClaves.add(kc);   // ...ni a la misma canción con otro id
    _cur = 0; _dur = _parseDur(track.dur); _seeking = false; _errEnEsteLoad = false; _renderSeek();   // reset del progreso
    if (prevP && prevP !== p && prevP.destroy) prevP.destroy();   // otra fuente: apagá la anterior
    if (p && p.cargar) p.cargar(track, { autoplay: !_twitch, start: 0 });
    _guardadoPeriodico(); _guardar(); _renderNow(); _renderMini(); _marcarActiva(!!auto);
    if (r.porDelante(_pl) < 4) _rellenarCola();
  }

  // Reproducir la fila `i` de una LISTA visible: esa lista pasa a ser la cola
  // (lo que sigue es lo que se ve, abajo, en orden).
  function _reproducirDeLista(items, i, fuente) {
    const r = RY(); if (!r) return;
    _pl = r.crearLista(items, i);
    const t = r.pistaEn(_pl, _pl.idx);
    // `fuente` es el box._fuente de donde vino la lista; su `.fuente` (la mesa
    // en la que vive esa lista) viaja en `_plFuente` para continuarla después.
    _plFuente = fuente ? Object.assign({}, fuente) : { tipo: 'rel', fuente: (t && t.fuente) || 'youtube' };
    if (!_plFuente.fuente && t) _plFuente.fuente = t.fuente || 'youtube';
    if (t) play(t);
  }
  // Reproducir algo SUELTO (handoff del preview, reanudación tras reinicio):
  // no hay lista todavía → la playlist nace con esa pista y se llena con sus
  // relacionados reales.
  function _reproducirSuelto(track) {
    const r = RY(); if (!r || !track) return;
    _pl = r.crearLista([track], 0);
    _plFuente = { tipo: 'rel', fuente: track.fuente || 'youtube' };
    play(track);
  }
  // Siembra la playlist con una pista que YA está sonando (reanudación tras un
  // reinicio, cued desde storage): sin tocar el player, la cola nace con ella y
  // se llena con sus relacionados.
  function _sembrarPlaylist(track) {
    const r = RY(); if (!r || !track || !track.id) return;
    _pl = r.crearLista([track], 0);
    _plFuente = { tipo: 'rel', fuente: track.fuente || 'youtube' };
    _rellenarCola();
  }
  // Salta a un índice de la playlist actual (avance, retroceso, click en la cola).
  function _irA(i, auto) {
    const r = RY(); if (!r) return false;
    const t = r.pistaEn(_pl, i); if (!t) return false;
    _pl = r.saltarA(_pl, i);
    play(t, { auto: !!auto });
    return true;
  }

  // Handoff desde el Web Preview: adopta lo que se puso a sonar ahí (SIEMPRE
  // YouTube — el preview no tiene fuente local). NO abre el popover (el usuario
  // lo abre cuando quiere) — solo pasa a sonar en la Radio.
  function adopt(raw) { const t = _track(raw, 'youtube'); if (t) { if (!_mounted) _montar(); _reproducirSuelto(t); } }

  function _alternar() {
    const r = RY(); if (!r || !_state || !_state.track) return;
    _intent = false; _state = r.alternar(_state);
    _cmd(_state.sonando ? 'playVideo' : 'pauseVideo');
    _guardadoPeriodico(); _guardar(); _renderNow(); _renderMini();
  }
  // Siguiente = la de ABAJO en la lista que se ve. Si se acabó, pide
  // continuaciones y arranca la primera que llegue (devuelve true: hay algo en
  // camino, no hay que dar la música por terminada).
  function _next() {
    const r = RY(); if (!r) return false;
    const i = r.siguienteIdx(_pl);
    if (i >= 0) return _irA(i, true);
    _rellenarCola().then(() => {
      const j = RY() && RY().siguienteIdx(_pl);
      if (j >= 0) _irA(j, true);
      else if (_state && (_state.sonando || _state.t)) {
        _state = { track: _state.track, sonando: false, t: 0 }; _renderNow(); _renderMini();
      }
    });
    return true;
  }
  // Anterior REAL: vuelve a la de arriba en la lista. Con el tema ya empezado
  // (>5s) el ⏮ rebobina primero, como cualquier reproductor.
  function _prev() {
    const r = RY(); if (!r || !_state || !_state.track) return;
    const i = r.anteriorIdx(_pl);
    if (_cur > 5 || i < 0) { _cmd('seekTo', [0, true]); _cmd('playVideo'); _cur = 0; _renderSeek(); return; }
    _irA(i, false);
  }

  // ── Twitch: la única cosa que para la Radio ──
  function pauseForTwitch() { _twitch++; if (_twitch === 1 && _state && _state.track && _state.sonando) { _cmd('pauseVideo'); const m = $('#jarvis-radio-mini'); if (m) m.classList.add('twitch'); } }
  function resumeAfterTwitch() { if (_twitch > 0) _twitch--; if (_twitch === 0) { const m = $('#jarvis-radio-mini'); if (m) m.classList.remove('twitch'); if (_state && _state.track && _state.sonando) _cmd('playVideo'); } }

  // ── Fetch al backend ──
  const _clave = (t) => { const r = RY(); return (r && r.claveCancion && t) ? r.claveCancion(t.titulo, t.canal) : ''; };

  // Una BÚSQUEDA (o el listado de un canal/estación) es una lista con fuente:
  // guarda la consulta y el `token` de continuación (de la fuente: para YouTube
  // es el de la tanda siguiente; la fuente local no tiene continuación) para
  // poder traer la tanda siguiente cuando el usuario toca los dots (o cuando la
  // música llega al final de lo que se ve). Usa SIEMPRE la fuente activa del
  // buscador (_intFuente): buscar/continuar le pertenecen a esa mesa.
  async function _buscar(q, into) {
    q = (q || '').trim();
    const box = $(into); if (!box) return;
    const f = _intFuente(); if (!f) return;
    const fid = f.id;
    const input = $('#jr-q');   // para no pintar resultados de una búsqueda que el usuario dejó atrás
    if (!q) {   // input vacío → volver a mostrar la playlist que está sonando
      _buscando = false;
      if (_pl.items.length) _renderPlaylistEn(box);
      else if (fid === 'local') { _listarLocal(box); return; }
      else if (f.catalogo) {
        // Fuente con catálogo propio: mostrar el catálogo entero
        // (su buscar('') es un filtro local, sin red).
        try {
          const data = await f.buscar('');
          if (box && box.isConnected && !input.value.trim()) _renderFilas(box, _filas(data, fid), data.error || 'No se pudo cargar el catálogo',
            { tipo: 'catalogo', q: '', token: data.token || null, fuente: fid });
        } catch { if (box && box.isConnected && !input.value.trim()) _renderFilas(box, [], 'No se pudo cargar el catálogo'); }
        return;
      }
      else { box.innerHTML = '<div class="jr-hint">Buscá música o elegí una estación.</div>'; box._items = []; box._fuente = null; }
      return;
    }
    _buscando = true;   // el pane muestra resultados de búsqueda (no la playlist)
    box.innerHTML = '<div class="jr-hint">Buscando…</div>';
    const q0 = q;
    try {
      const data = await f.buscar(q0);
      if (input && input.value.trim() !== q0) return;   // el usuario siguió tipeando
      _renderFilas(box, _filas(data, fid), data.error,
        { tipo: 'busqueda', q: q0, token: data.token || null, fuente: fid });
    } catch { _renderFilas(box, [], 'No se pudo buscar', { tipo: 'busqueda', q: q0, token: null, fuente: fid }); }
  }

  // ── Continuaciones: de dónde salen las pistas que se suman por ABAJO ────────
  // Dos vías, según de dónde vino la lista:
  //   · búsqueda/canal/estación → la tanda SIGUIENTE de esa misma consulta
  //     (fuente.mas + token de continuación). Más de lo que buscaste, sin
  //     repetir lo que ya se ve.
  //   · relacionados (o búsqueda agotada) → los relacionados REALES de la última
  //     pista de la lista (fuente.relacionados), con fallback a buscar por
  //     canal/título EN LA MISMA FUENTE.
  // Devuelve pistas ya normalizadas; el dedupe fino lo hace `anexar` (por id y
  // por CANCIÓN) contra la lista destino.
  async function _traerMas(fuente, ultima) {
    const nuevas = [];
    const fid = (fuente && fuente.fuente) || _fuenteDe(ultima);
    const f = _fuentes[fid] || _intFuente();
    if (f && fuente && fuente.token) {
      try {
        const data = await f.mas(fuente.token);
        fuente.token = data.token || null;   // token de la tanda que sigue (o fin)
        for (const r of (data.resultados || [])) { const t = _track(r, fid); if (t) nuevas.push(t); }
      } catch { fuente.token = null; }
      if (nuevas.length) return nuevas;
    }
    if (!f || !ultima || !ultima.id) return nuevas;
    let data = null;
    try { data = await f.relacionados(ultima.id); } catch { data = null; }
    for (const r of (data && data.resultados) || []) {
      const t = _track(r, fid); if (!t || _played.has(t.id)) continue;
      const k = _clave(t); if (k && _playedClaves.has(k)) continue;
      nuevas.push(t);
    }
    if (nuevas.length) return nuevas;
    const q = (ultima.canal || ultima.titulo || '').trim(); if (!q) return nuevas;
    try {
      const data2 = await f.buscar(q);
      for (const r of (data2.resultados || [])) {
        const t = _track(r, fid); if (!t || _played.has(t.id)) continue;
        const k = _clave(t); if (k && _playedClaves.has(k)) continue;
        nuevas.push(t);
      }
    } catch {}
    return nuevas;
  }

  // Rellena la PLAYLIST por abajo (sin tocar el orden ni el índice). Devuelve
  // cuántas entraron. Una sola en vuelo: dos finales seguidos duplicaban filas.
  let _rellenando = null;
  function _rellenarCola() {
    if (_rellenando) return _rellenando;
    const r = RY(); if (!r || !_pl.items.length) return Promise.resolve(0);
    _rellenando = (async () => {
      const antes = _pl.items.length;
      try {
        if (!_plFuente) _plFuente = { tipo: 'rel' };
        const nuevas = await _traerMas(_plFuente, _pl.items[_pl.items.length - 1]);
        if (nuevas.length) {
          _pl = r.anexar(_pl, nuevas, r.claveCancion);
          _repintarPlaylist();
        }
      } catch {}
      const sumadas = _pl.items.length - antes;
      _rellenando = null;
      return sumadas;
    })();
    return _rellenando;
  }

  // Canal: sus videos vía búsqueda por el nombre del canal (mismo endpoint) —
  // esa lista también es una cola con continuación (token). Usa la MESA de
  // donde vino el track: si el buscador está en otra fuente, el canal se
  // pregunta igual (el pane de canal es de esa lista, no del buscador).
  async function _cargarCanal(track) {
    const list = $('#jr-cv-list'); if (!list) return;
    list.innerHTML = '<div class="jr-hint">Cargando el canal…</div>'; list._items = []; list._fuente = null;
    // La MESA de donde vino el track, no la que esté activa en el buscador:
    // si el usuario cambió la pill, el canal se sigue preguntando igual.
    const fid = _fuenteDe(track);
    const f = _fuentes[fid] || _fuentes.youtube;
    const q = (track.canal || track.titulo || '').trim();
    try {
      const data = await f.buscar(q);
      _renderFilas(list, _filas(data, fid), data.error || 'No se pudieron traer los videos del canal',
        { tipo: 'canal', q, token: data.token || null, fuente: fid });
    } catch { _renderFilas(list, [], 'No se pudo cargar el canal'); }
  }

  // ── Volumen: control por la API del iframe de YouTube (setVolume/mute), con
  // persistencia y reaplicación al reanudar (load del iframe + gesto de autoplay).
  const VOLKEY = 'jarvis.preview.radio.vol';
  function _volLoad() {
    try { const d = JSON.parse(localStorage.getItem(VOLKEY) || 'null');
      if (d) { _vol = Math.max(0, Math.min(100, +d.v || 0)); _muted = !!d.m; _lastVol = _vol || 60; _repeat = !!d.r; } } catch {}
  }
  function _volSave() { try { localStorage.setItem(VOLKEY, JSON.stringify({ v: _vol, m: _muted, r: _repeat })); } catch {} }
  function _volIcon() { return (_muted || _vol === 0) ? 'mute' : 'volume'; }
  function _volApply() { _cmd('setVolume', [_vol]); _muted ? _cmd('mute') : _cmd('unMute'); }
  function _volIconUpd() {
    const b = $('#jr-vol-btn'); if (b) { b.innerHTML = svg(_volIcon()); b.title = (_muted || _vol === 0) ? 'Activar sonido' : 'Silenciar'; }
  }
  function _volSliderUpd() {
    const v = _muted ? 0 : _vol;
    const fill = $('#jr-vol-fill'); if (fill) fill.style.width = v + '%';
    const el = $('#jr-vol-el'); if (el) el.setAttribute('aria-valuenow', String(v));
    const pct = $('#jr-vol-pct'); if (pct) pct.textContent = v;
  }
  function _volToggleMute() {
    if (_muted || _vol === 0) { _muted = false; if (_vol === 0) _vol = _lastVol || 60; }
    else { _muted = true; _lastVol = _vol; }
    _volApply(); _volSave(); _volIconUpd(); _volSliderUpd();
  }

  // ── Slider elástico (port vanilla de ElasticSlider/React Bits, sin libs) ──
  // Arrastrar más allá de un borde estira la pista como goma: el exceso pasa por
  // una sigmoide (rinde cada vez menos, tope VOL_OV px) y empuja el ícono/% de
  // ese lado; al soltar, un resorte subamortiguado (rAF) la devuelve con una
  // oscilación corta. Con prefers-reduced-motion la goma se apaga entera.
  const VOL_OV = 26;                 // estiramiento máximo en px (sigmoide)
  let _ov = 0, _ovSide = 0, _ovRaf = 0;   // overflow actual (px, con signo) / lado / rAF del resorte
  const _sinMotion = () => !!(root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)').matches);
  function _ovDecay(v) { return (2 * (1 / (1 + Math.exp(-(v / VOL_OV))) - 0.5)) * VOL_OV; }   // sigmoide impar
  function _ovPaint() {
    const el = $('#jr-vol-el'), st = $('#jr-vol-stretch'); if (!el || !st) return;
    const btn = $('#jr-vol-btn'), pct = $('#jr-vol-pct');
    const a = Math.abs(_ov);
    if (a < 0.15) { st.style.transform = ''; if (btn) btn.style.transform = ''; if (pct) pct.style.transform = ''; return; }
    const w = el.clientWidth || 1;
    st.style.transformOrigin = _ovSide < 0 ? '100% 50%' : '0% 50%';
    st.style.transform = `scaleX(${(1 + a / w).toFixed(4)}) scaleY(${Math.max(0.78, 1 - (a / VOL_OV) * 0.2).toFixed(3)})`;
    if (btn) btn.style.transform = _ovSide < 0 ? `translateX(${(_ov * 0.6).toFixed(1)}px) scale(${(1 + (a / VOL_OV) * 0.12).toFixed(3)})` : '';
    if (pct) pct.style.transform = _ovSide > 0 ? `translateX(${(_ov * 0.6).toFixed(1)}px)` : '';
  }
  function _ovSpring() {
    cancelAnimationFrame(_ovRaf);
    if (_sinMotion() || Math.abs(_ov) < 0.15) { _ov = 0; _ovSide = 0; _ovPaint(); return; }
    let x = _ov, v = 0, last = performance.now();
    const paso = (now) => {
      const dt = Math.min(32, now - last) / 1000; last = now;
      v += (-320 * x - 17 * v) * dt; x += v * dt;   // resorte k=320 c=17 → ζ≈.48, 1-2 rebotes cortos
      if (Math.abs(x) < 0.15 && Math.abs(v) < 2) { _ov = 0; _ovSide = 0; _ovPaint(); return; }
      _ov = x; _ovPaint(); _ovRaf = requestAnimationFrame(paso);
    };
    _ovRaf = requestAnimationFrame(paso);
  }
  function _volSet(v) {
    _vol = Math.max(0, Math.min(100, Math.round(v))); if (_vol > 0) _muted = false;
    _volApply(); _volSave(); _volIconUpd(); _volSliderUpd();
  }
  function _volInitUI() {
    const el = $('#jr-vol-el'); if (!el) return;
    _volSliderUpd(); _volIconUpd();
    let drag = false;
    const mover = (e) => {
      const r = el.getBoundingClientRect(); if (!r.width) return;
      _volSet(((e.clientX - r.left) / r.width) * 100);
      let raw = 0;
      if (e.clientX < r.left) raw = e.clientX - r.left;
      else if (e.clientX > r.right) raw = e.clientX - r.right;
      _ovSide = raw < 0 ? -1 : (raw > 0 ? 1 : _ovSide);
      _ov = _sinMotion() ? 0 : _ovDecay(raw);
      _ovPaint();
    };
    el.addEventListener('pointerdown', (e) => {
      drag = true; el.classList.add('drag'); cancelAnimationFrame(_ovRaf);
      try { el.setPointerCapture(e.pointerId); } catch {}
      mover(e); e.preventDefault();
    });
    el.addEventListener('pointermove', (e) => { if (drag) mover(e); });
    const soltar = () => { if (!drag) return; drag = false; el.classList.remove('drag'); _ovSpring(); };
    el.addEventListener('pointerup', soltar);
    el.addEventListener('pointercancel', soltar);
    el.addEventListener('lostpointercapture', soltar);
    el.addEventListener('keydown', (e) => {
      let v = null;
      if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') v = _vol - 5;
      else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') v = _vol + 5;
      else if (e.key === 'Home') v = 0; else if (e.key === 'End') v = 100;
      if (v === null) return;
      e.preventDefault(); _volSet(v);
    });
    const b = $('#jr-vol-btn'); if (b) b.addEventListener('click', _volToggleMute);
  }

  // ── Barra de progreso (seek): tiempo transcurrido/total + adelantar en videos largos ──
  function _fmt(s) {
    s = Math.max(0, Math.floor(s || 0));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
    const p = (n) => (n < 10 ? '0' + n : '' + n);
    return h > 0 ? h + ':' + p(m) + ':' + p(ss) : m + ':' + p(ss);
  }
  function _parseDur(str) {
    if (!str || /live|vivo/i.test(str)) return 0;
    const parts = String(str).split(':').map((x) => parseInt(x, 10));
    if (!parts.length || parts.some((n) => isNaN(n))) return 0;
    return parts.reduce((a, n) => a * 60 + n, 0);
  }
  function _renderSeek() {
    const fill = $('#jr-seek-fill'); if (!fill) return;
    const knob = $('#jr-seek-knob'), cur = $('#jr-seek-cur'), tot = $('#jr-seek-tot'), bar = $('#jr-seek-bar');
    const live = !(_dur > 0);                                   // live / duración desconocida
    const pct = live ? 100 : Math.max(0, Math.min(100, (_cur / _dur) * 100));
    // El fill se desliza (transition linear .5s) entre updates del player; un
    // salto hacia ATRÁS (track nuevo, seek) se pinta instantáneo con .snap para
    // que la barra no "rebobine" animada.
    if (bar && !_seeking && pct < _lastPct - 2) {
      bar.classList.add('snap');
      requestAnimationFrame(() => requestAnimationFrame(() => bar.classList.remove('snap')));
    }
    _lastPct = pct;
    fill.style.width = pct + '%';
    if (knob) knob.style.left = pct + '%';
    if (cur) cur.textContent = live ? 'EN VIVO' : _fmt(_cur);
    if (tot) tot.textContent = live ? '' : _fmt(_dur);
    if (bar) bar.classList.toggle('live', live);
  }
  function _seekAt(clientX) {
    const bar = $('#jr-seek-bar'); if (!bar || !(_dur > 0)) return null;
    const r = bar.getBoundingClientRect(); if (!r.width) return null;
    return Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * _dur;
  }
  function _seekInitUI() {
    const bar = $('#jr-seek-bar'); if (!bar) return;
    const bub = $('#jr-seek-bub');
    const burbuja = (e) => {                       // burbuja de tiempo bajo el cursor (hover Y arrastre)
      if (!bub) return;
      const t = _seekAt(e.clientX); if (t == null) { bub.classList.remove('on'); return; }
      const r = bar.getBoundingClientRect();
      bub.style.left = (Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * 100) + '%';
      bub.textContent = _fmt(t); bub.classList.add('on');
    };
    const preview = (e) => { const t = _seekAt(e.clientX); if (t == null) return; _cur = t; _renderSeek(); };
    bar.addEventListener('pointerdown', (e) => {
      if (!(_dur > 0)) return;
      _seeking = true; bar.classList.add('seeking');
      try { bar.setPointerCapture(e.pointerId); } catch {} preview(e); burbuja(e); e.preventDefault();
    });
    bar.addEventListener('pointermove', (e) => { if (_seeking) preview(e); burbuja(e); });
    bar.addEventListener('pointerleave', () => { if (!_seeking && bub) bub.classList.remove('on'); });
    const soltar = (e) => {
      if (!_seeking) return; _seeking = false; bar.classList.remove('seeking');
      const t = _seekAt(e.clientX); if (t != null) { _cmd('seekTo', [Math.floor(t), true]); _cur = t; _renderSeek(); }
      if (bub && !bar.matches(':hover')) bub.classList.remove('on');
    };
    bar.addEventListener('pointerup', soltar);
    bar.addEventListener('pointercancel', () => {   // cancel NO commitea (clientX puede ser basura)
      _seeking = false; bar.classList.remove('seeking'); if (bub) bub.classList.remove('on');
    });
  }

  // ── i18n (ES⇆EN): registrá las frases ANTES de montar el UI para que el
  // observer de JarvisI18n las traduzca solo al re-render y al cambiar de idioma.
  let _i18nDone = false;
  function _i18n() {
    if (_i18nDone || !(root.JarvisI18n && root.JarvisI18n.agregar)) return;
    _i18nDone = true;
    root.JarvisI18n.agregar({
      'EN VIVO': 'LIVE',
      'Reproduciendo': 'Now playing',
      'Ver canal': 'View channel', 'Ver el canal': 'View channel',
      'Relacionados': 'Related', 'Cola': 'Queue', 'Estaciones': 'Stations',
      'Reproducir': 'Play', 'Reproducir / pausar': 'Play / pause',
      'Minimizar': 'Minimize', 'Cerrar': 'Close', 'Aleatorio': 'Shuffle', 'Mezclar la cola': 'Shuffle the queue',
      'Reiniciar': 'Restart', 'Siguiente': 'Next', 'Repetir': 'Repeat',
      'Volver': 'Back', 'Agregar a la cola': 'Add to queue',
      'Silenciar': 'Mute', 'Activar sonido': 'Unmute', 'Volumen': 'Volume', 'Progreso': 'Progress',
      'Buscá música o pegá un link de YouTube…': 'Search for music or paste a YouTube link…',
      'Buscá música o pegá un link de Spotify…': 'Search for music or paste a Spotify link…',
      'Buscá en data/music…': 'Search in data/music…',
      'Elegí un stream en vivo…': 'Pick a live stream…',
      'No se pudo cargar el catálogo': "Couldn't load the catalog",
      'Buscá música o elegí una estación.': 'Search for music or pick a station.',
      'Lista para sonar': 'Ready to play',
      'Nada sonando todavía.': 'Nothing playing yet.',
      'Poné algo a sonar y te muestro relacionados.': "Play something and I'll show you related tracks.",
      'Poné algo a sonar': 'Play something', 'en el workspace': 'in the workspace',
      'La cola está vacía. Se llena sola con los relacionados de lo que suena.': "The queue is empty — it fills up with what's related to what's playing.",
      'Buscando…': 'Searching…', 'Sin resultados': 'No results', 'No se pudo buscar': "Couldn't search",
      'Cargando el canal…': 'Loading channel…',
      'No se pudieron traer los videos del canal': "Couldn't load the channel's videos",
      'No se pudo cargar el canal': "Couldn't load the channel",
      'Sintonizando…': 'Tuning in…', 'No se pudo sintonizar': "Couldn't tune in",
      'Más música relacionada': 'More related music', 'Más del canal': 'More from this channel',
      'Más como': 'More like',
      'No hay más por ahora': 'Nothing more for now',
      'Fuente': 'Source',
      'YouTube': 'YouTube', 'Local': 'Local', 'Spotify': 'Spotify',
      'Subir música': 'Upload music',
      'Los archivos van a data/music': 'Files go to data/music',
      'No se pudo subir la música': "Couldn't upload the music",
      'Se subieron {n} archivos a data/music': 'Uploaded {n} files to data/music',
      'No se subió ningún archivo': 'No files were uploaded',
      'No se pudo cargar la música local': "Couldn't load local music",
      'Nada en data/music todavía': 'Nothing in data/music yet',
      'No se pudo reproducir — saltando ▸': "Couldn't play — skipping",
      'Sesión de Spotify no iniciada': 'Spotify session not started',
      'YouTube · videos del canal': 'YouTube · channel videos', 'Canal': 'Channel', 'vistas': 'views',
      'estudiar': 'study', 'nocturno': 'night', 'café': 'coffee', 'calma': 'calm',
      // Errores del backend (web_search / musica_local / spotify_api): llegan en
      // español y se muestran como hint de la lista o toast. El observer de
      // JarvisI18n los traduce al insertarlos, pero necesita la entrada acá.
      'YouTube no respondió': 'YouTube did not respond',
      'YouTube no devolvió resultados (challenge o formato nuevo)': 'YouTube returned no results (challenge or new format)',
      'no se pudo leer la respuesta de YouTube': "couldn't read YouTube's response",
      'consulta vacía': 'empty query',
      'token de continuación inválido': 'invalid continuation token',
      'id de video inválido': 'invalid video id',
      'ruta vacía': 'empty path',
      'ruta no permitida': 'path not allowed',
      'ruta fuera de la biblioteca': 'path outside the library',
      'archivo inexistente': 'file not found',
      'portada no soportada': 'unsupported cover',
      'portada inexistente': 'cover not found',
      'carpeta inexistente': 'folder not found',
      'Spotify no está configurado (SPOTIFY_CLIENT_ID)': 'Spotify is not configured (SPOTIFY_CLIENT_ID)',
      'Spotify no respondió': 'Spotify did not respond',
      'No se pudo validar la sesión de Spotify': "Couldn't validate the Spotify session",
      'Sin sesión de Spotify — iniciá sesión en ⚙ → Radio': 'No Spotify session — sign in at ⚙ → Radio',
      'Spotify está saturado — intentá de nuevo en un momento': 'Spotify is busy — try again in a moment',
      'no se pudo leer la respuesta de Spotify': "couldn't read Spotify's response",
    });
  }

  // ═══════════════ UI ═══════════════
  function _montar() {
    if (_mounted) return;
    const barRight = $('#jw-bar .jw-bar-right') || $('.jw-bar-right');
    if (!barRight) return;   // el header todavía no está; se reintenta
    _mounted = true;
    _i18n(); _volLoad();

    // Mini-player (a la IZQUIERDA de los íconos → primer hijo de .jw-bar-right)
    const mini = document.createElement('div');
    mini.className = 'jr-mini'; mini.id = 'jarvis-radio-mini'; mini.title = 'Radio';
    mini.setAttribute('role', 'button'); mini.tabIndex = 0;
    barRight.insertBefore(mini, barRight.firstChild);

    // Popover + catcher (colgados de <body>)
    const pop = document.createElement('div'); pop.className = 'jr-pop'; pop.id = 'jarvis-radio-pop';
    pop.innerHTML =
      `<div class="jr-card"><div class="jr-inner">
        <div class="jr-head"><span class="jr-mark">${svg('radio')}</span>
          <span class="jr-title">Radio <span class="jr-livewrap" id="jr-live" hidden><span class="jr-eq on"><i></i><i></i><i></i></span> EN VIVO</span></span>
          <button class="jr-ibtn" id="jr-min" title="Minimizar">${svg('chevup')}</button>
          <button class="jr-ibtn" id="jr-close" title="Cerrar">${svg('close')}</button></div>
        <div class="jr-src" id="jr-src"></div>
        <div class="jr-src-hints" id="jr-src-hints" hidden></div>
        <!-- Subida de música (card SOLO con fuente local activa): entre los
             hints y la lista. El input file es ÚNICO (vive acá desde el montaje;
             la fila de pills NO lo duplica). -->
        <div class="jr-upload" id="jr-upload" hidden>
          <button type="button" id="jr-upload-btn">${_upIco()} ${_t('Subir música')}</button>
          <span class="jr-upload-note">${_t('Los archivos van a data/music')}</span>
          <input type="file" multiple accept="audio/*" id="jr-src-file" hidden>
        </div>
        <label class="jr-search"><span>${svg('search')}</span><input id="jr-q" placeholder="Buscá música o pegá un link de YouTube…" spellcheck="false" autocomplete="off"></label>
        <div class="jr-now" id="jr-now"></div>
        <div class="jr-transport" id="jr-transport"></div>
        <div class="jr-seek" id="jr-seek">
          <span class="jr-seek-t" id="jr-seek-cur">0:00</span>
          <div class="jr-seek-bar" id="jr-seek-bar" role="slider" aria-label="Progreso" tabindex="0"><div class="jr-seek-fill" id="jr-seek-fill"></div><div class="jr-seek-knob" id="jr-seek-knob"></div><span class="jr-seek-bub" id="jr-seek-bub">0:00</span></div>
          <span class="jr-seek-t r" id="jr-seek-tot">0:00</span>
        </div>
        <div class="jr-vol" id="jr-vol">
          <span class="jr-vol-l"><button class="jr-vol-btn" id="jr-vol-btn" type="button" title="Silenciar" aria-label="Silenciar">${svg('volume')}</button></span>
          <div class="jr-vol-el" id="jr-vol-el" role="slider" aria-label="Volumen" aria-valuemin="0" aria-valuemax="100" aria-valuenow="80" tabindex="0">
            <div class="jr-vol-stretch" id="jr-vol-stretch"><div class="jr-vol-track"><div class="jr-vol-fill" id="jr-vol-fill"></div></div></div>
          </div>
          <span class="jr-vol-pct" id="jr-vol-pct">80</span>
        </div>
        <div id="jr-browse">
          <div class="jr-seg" id="jr-seg"><button class="on" data-p="rel">Relacionados</button><button data-p="q">Cola</button><button data-p="st">Estaciones</button></div>
          <div class="jr-scroll">
            <div class="jr-pane" id="jr-pane-rel"><div class="jr-hint">Poné algo a sonar y te muestro relacionados.</div></div>
            <div class="jr-pane" id="jr-pane-q" hidden></div>
            <div class="jr-pane jr-stations" id="jr-pane-st" hidden></div>
          </div>
        </div>
        <div class="jr-channel" id="jr-channel" hidden></div>
      </div></div>`;
    document.body.appendChild(pop);
    const catcher = document.createElement('div'); catcher.className = 'jr-catch'; catcher.id = 'jarvis-radio-catch';
    document.body.appendChild(catcher);

    // Listeners
    mini.addEventListener('click', (e) => { if (e.target.closest('#jr-mini-play')) return; _toggle(); });
    mini.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _toggle(); } });
    $('#jr-min').addEventListener('click', () => _toggle(false));
    $('#jr-close').addEventListener('click', () => _toggle(false));
    catcher.addEventListener('click', () => _toggle(false));
    $('#jr-src').addEventListener('click', (e) => { const b = e.target.closest('[data-src]'); if (b) _setFuente(b.dataset.src); });
    const upBtn = $('#jr-upload-btn');
    if (upBtn) upBtn.addEventListener('click', () => { const fi = $('#jr-src-file'); if (fi) fi.click(); });
    const upIn = $('#jr-src-file');
    if (upIn) upIn.addEventListener('change', (ev) => { _subirLocal(ev.target && ev.target.files); if (ev.target) ev.target.value = ''; });
    const q = $('#jr-q'); let deb = null;
    q.addEventListener('input', () => { clearTimeout(deb); deb = setTimeout(() => _buscar(q.value, '#jr-pane-rel'), 350); if (q.value.trim()) _setPane('rel'); });
    q.addEventListener('keydown', (e) => { if (e.key === 'Enter') { clearTimeout(deb); _buscar(q.value, '#jr-pane-rel'); _setPane('rel'); } });
    $('#jr-seg').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) _setPane(b.dataset.p); });
    $('#jr-pane-rel').addEventListener('click', (e) => {
      const rel = $('#jr-pane-rel'); _clicFila(e, () => (rel._items || []), rel);
    });
    $('#jr-pane-q').addEventListener('click', _clicQueue);
    $('#jr-pane-st').addEventListener('click', (e) => { const s = e.target.closest('.jr-station'); if (s) _estacion(s.dataset.q); });
    $('#jr-now').addEventListener('click', (e) => { if (e.target.closest('#jr-chanlink')) { if (_state && _state.track) _abrirCanal(_state.track); } });
    $('#jr-transport').addEventListener('click', (e) => {
      const b = e.target.closest('[data-a]'); if (!b) return;
      const a = b.dataset.a;
      if (a === 'play') _alternar(); else if (a === 'next') { if (!_next()) _prev0(); } else if (a === 'prev') _prev();
      else if (a === 'shuffle') _shuffle(b);
      else if (a === 'repeat') { _repeat = !_repeat; _volSave(); b.classList.toggle('on', _repeat); }
    });

    _volInitUI(); _seekInitUI();
    _renderFuentes();
    _renderEstaciones();
    _renderNow(); _renderMini(); _renderPaneChrome();
    // El placeholder y los hints los setea el código (no el HTML): al cambiar
    // de idioma hay que re-sincronizarlos con la fuente activa. Además los
    // contadores de YouTube (vistas/viendo) se localizan al render → re-pintamos
    // el now-playing, la cola y las listas visibles para que cambien YA.
    window.addEventListener('jarvis:lang', () => {
      _syncPlaceholder(); _renderHints(); _renderFuentes(); _renderNow(); _renderMini();
      if (_pane === 'q') _renderQueue();
      _repintarListas();
    });

    // Estado inicial: si el boot no lo levantó, restaurar cued desde storage.
    if (!_state) {
      const r = RY(); _state = r ? r.deserializar(_leer()) : { track: null, sonando: false, t: 0 };
      if (_state.track) {
        const p = _playerDe(_state.track);
        if (p && p.cargar) p.cargar(_state.track, { autoplay: false, start: _state.t });
        _guardadoPeriodico();
        _sembrarPlaylist(_state.track);   // la cola arranca con lo que quedó cued
      }
      _renderNow(); _renderMini();
    }
  }
  function _prev0() { /* next sin cola: nada */ }

  function _toggle(force) {
    _open = typeof force === 'boolean' ? force : !_open;
    $('#jarvis-radio-pop').classList.toggle('on', _open);
    $('#jarvis-radio-catch').classList.toggle('on', _open);
    $('#jarvis-radio-mini').classList.toggle('abierto', _open);
    $('#jr-mini-chev') && ($('#jr-mini-chev').innerHTML = svg(_open ? 'chevup' : 'chevdown'));
    if (_open) {
      _alinear(); requestAnimationFrame(_segThumb); _renderFuentes();   // re-fresca pills (registros tardíos)
      const rel = $('#jr-pane-rel');   // al abrir, Relacionados muestra lo que viene
      if (rel && _pane === 'rel' && !_buscando && !rel._esPl && _pl.items.length) _renderPlaylistEn(rel);
      const q = $('#jr-q'); if (q) setTimeout(() => q.focus(), 30);
    }
  }
  function open() { if (!_mounted) _montar(); _toggle(true); }
  function close() { _toggle(false); }
  function _alinear() {
    const m = $('#jarvis-radio-mini'), pop = $('#jarvis-radio-pop'); if (!m || !pop) return;
    const r = m.getBoundingClientRect(), vw = document.documentElement.clientWidth;
    const cardRight = Math.max(10, vw - r.right);
    pop.style.right = cardRight + 'px';
    const caret = Math.max(16, Math.min((vw - (r.left + r.width / 2)) - cardRight, 360));
    const card = pop.querySelector('.jr-card'); if (card) card.style.setProperty('--jr-caret', caret + 'px');
  }
  window.addEventListener('resize', () => { if (_open) _alinear(); });

  function _setPane(p) {
    _pane = p; _inChannel = false;
    $('#jr-channel').hidden = true; $('#jr-browse').hidden = false;
    for (const b of $('#jr-seg').querySelectorAll('button')) b.classList.toggle('on', b.dataset.p === p);
    _segThumb();
    $('#jr-pane-rel').hidden = p !== 'rel'; $('#jr-pane-q').hidden = p !== 'q'; $('#jr-pane-st').hidden = p !== 'st';
    if (p === 'q') _renderQueue();
    // Relacionados = la playlist que está sonando (salvo que el pane esté
    // mostrando una búsqueda: esa lista se respeta hasta que la vacíen).
    const rel = $('#jr-pane-rel');
    if (p === 'rel' && _pl.items.length && !_buscando && !(rel && rel._esPl)) _renderPlaylistEn(rel);
  }

  // Thumb deslizante del segmented: mide la pestaña activa y publica --segx/--segw.
  // Solo mide con el popover visible (cerrado, offsetWidth da 0 y el thumb se esconde).
  function _segThumb() {
    const seg = $('#jr-seg'); if (!seg) return;
    const b = seg.querySelector('button.on');
    if (!b || !b.offsetWidth) { seg.classList.remove('thumbed'); return; }
    seg.style.setProperty('--segx', b.offsetLeft + 'px');
    seg.style.setProperty('--segw', b.offsetWidth + 'px');
    seg.classList.add('thumbed');
  }

  // Ícono de música del mini-player (ondas de radio, como el mockup). Reemplaza
  // a la carátula: el mini muestra SIEMPRE el ícono; al sonar, las ondas hacen
  // el fade escalonado + el core "respira" (animación del mockup). La carátula
  // del track sigue viva en el popover grande (_renderNow).
  const _rdIco = () =>
    '<svg class="jr-rd" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" aria-hidden="true">'
    + '<circle class="jr-rd-core" cx="12" cy="12" r="2.1" fill="currentColor" stroke="none"/>'
    + '<path class="jr-rd-wv jr-rd-w1" d="M8.6 8.6a4.8 4.8 0 0 0 0 6.8M15.4 8.6a4.8 4.8 0 0 1 0 6.8"/>'
    + '<path class="jr-rd-wv jr-rd-w2" d="M6 6a8.5 8.5 0 0 0 0 12M18 6a8.5 8.5 0 0 1 0 12"/>'
    + '</svg>';

  // ── Render: mini ──
  // Solo un ÍCONO en la barra (como el mockup): las ondas de radio, que animan
  // al sonar. Sin cuadro/título/play inline. Click en el ícono → abre la radio
  // (el popover con todo: now-playing, transport, búsqueda, cola). El título del
  // track vive en el tooltip para el vistazo rápido.
  function _renderMini() {
    const m = $('#jarvis-radio-mini'); if (!m) return;
    const t = _state && _state.track, son = _state && _state.sonando;
    m.classList.toggle('playing', !!son);
    m.title = t ? `${t.titulo}${t.canal ? ' — ' + t.canal : ''}` : 'Radio';
    m.setAttribute('aria-label', t ? `Radio: ${t.titulo}` : 'Radio');
    m.innerHTML = _rdIco();
  }

  // ── Render: now-playing + transport ──
  function _renderNow() {
    const now = $('#jr-now'); if (!now) return;
    const t = _state && _state.track, son = _state && _state.sonando;
    const live = $('#jr-live'); if (live) live.hidden = !(t && son);
    if (!t) { now.classList.remove('playing'); now.innerHTML = `<span class="jr-art ghost"></span><span class="jr-ninfo"><span class="jr-eyebrow">Lista para sonar</span><span class="jr-ntitle">Nada sonando todavía.</span></span>`; _renderTransport(); return; }
    const fid = _fuenteDe(t);
    const fu = _fuentes[fid];
    const nomFuente = _etiquetaFuente(fu);
    // Canal (botón "Ver canal") es solo YouTube: el <audio> local no tiene canal.
    const chanlink = fid === 'youtube'
      ? `<button class="jr-chanlink" id="jr-chanlink" title="Ver el canal"><span class="jr-av"></span><span>${_esc(t.canal || 'YouTube')}</span><span class="jr-vercanal">Ver canal</span></button>`
      : `<span class="jr-chanlink" title=""><span class="jr-av"></span><span>${_esc(nomFuente)}</span></span>`;
    const v = _vistas(t);
    const chipFuente = fid === 'youtube'
      ? (v ? `<span class="jr-chip">${svg('eye')}<b>${_esc(v)}</b></span>` : `<span class="jr-chip">${svg('eye')}<b>YouTube</b></span>`)
      : `<span class="jr-chip">${svg('note')}<b>${_esc(nomFuente)}</b></span>`;
    now.innerHTML =
      `<span class="jr-art">${t.thumb ? `<img src="${_esc(t.thumb)}" alt="" onerror="this.remove()">` : ''}${viz(!!son)}</span>`
      + `<span class="jr-ninfo"><span class="jr-eyebrow">Reproduciendo</span>`
      + `<span class="jr-ntitle">${_esc(t.titulo)}</span>`
      + chanlink
      + `<span class="jr-chips">`
      + chipFuente
      + (t.dur ? `<span class="jr-chip">${svg('clock')}<b>${_esc(t.dur)}</b></span>` : '')
      + `</span></span>`;
    now.classList.toggle('playing', !!son);   // pulso del glow de la carátula al ritmo
    _renderTransport();
  }
  function chip(ic, val) { return `<span class="jr-chip">${svg(ic)}<b>${_esc(val)}</b></span>`; }

  // Waveform de música: 28 barras que "bailan" con fase orgánica (senoidal, sin
  // random → estable) mientras suena; en pausa se aquietan a una línea baja.
  // Simulada: el iframe de YouTube es cross-origin y no da acceso a Web Audio.
  function _waveHTML(on) {
    let bars = '';
    for (let i = 0; i < 28; i++) {
      const delay = -((i * 0.055) + (Math.sin(i * 0.65) * 0.14 + 0.16));
      const dur = 1.0 + (i % 6) * 0.14;
      bars += `<i style="animation-delay:${delay.toFixed(2)}s;animation-duration:${dur.toFixed(2)}s"></i>`;
    }
    return `<div class="jr-wave${on ? ' on' : ''}" aria-hidden="true">${bars}</div>`;
  }

  // Mezcla la cola (Fisher-Yates) — acción real del botón shuffle: el orden
  // nuevo se VE al instante en Relacionados/Cola, más un pop en el botón.
  function _shuffle(btn) {
    const r = RY();
    if (r && r.porDelante(_pl) > 1) {
      _pl = r.mezclarCola(_pl);
      // La lista visible ES la cola: re-pintarla entera para que se VEA el
      // orden nuevo (lo ya sonado y la pista actual se quedan donde están).
      for (const box of [$('#jr-pane-rel'), $('#jr-cv-list')]) if (box && box._esPl) _renderPlaylistEn(box);
      if (_pane === 'q') _renderQueue();
    }
    if (btn) { btn.classList.remove('pop'); void btn.offsetWidth; btn.classList.add('pop'); }
  }

  function _renderTransport() {
    const tp = $('#jr-transport'); if (!tp) return;
    const son = _state && _state.sonando;
    tp.innerHTML =
      _waveHTML(!!son)
      + `<div class="jr-tp"><button class="jr-tp-btn sm" data-a="shuffle" title="Mezclar la cola">${svg('shuffle')}</button>`
      + `<button class="jr-tp-btn" data-a="prev" title="Reiniciar">${svg('prev')}</button>`
      + `<button class="jr-tp-play" data-a="play" title="Reproducir / pausar">${svg(son ? 'pause' : 'play')}</button>`
      + `<button class="jr-tp-btn" data-a="next" title="Siguiente">${svg('next')}</button>`
      + `<button class="jr-tp-btn sm${_repeat ? ' on' : ''}" data-a="repeat" title="Repetir" aria-pressed="${_repeat ? 'true' : 'false'}">${svg('repeat')}</button></div>`;
  }
  function _renderPaneChrome() { /* placeholder para futuros badges de cola */ }

  // ── Render: filas / cola / estaciones ──
  function _filaHTML(t, i) {
    return `<button class="jr-row${_state && _state.track && _state.track.id === t.id ? ' activa' : ''}" data-i="${i}" data-vid="${_esc(t.id)}">`
      + `<span class="jr-thumb">${t.thumb ? `<img src="${_esc(t.thumb)}" alt="" onerror="this.remove()">` : ''}${t.dur ? `<span class="jr-dur${/live|vivo/i.test(t.dur) ? ' live' : ''}">${_esc(t.dur)}</span>` : ''}</span>`
      + `<span class="jr-rmeta"><b>${_esc(t.titulo)}</b><span class="s">${_esc(t.canal)}${_vistas(t) ? ' · ' + _esc(_vistas(t)) : ''}</span></span>`
      + `<span class="jr-add" title="Agregar a la cola" data-add="${i}">${svg('addq')}</span></button>`;
  }

  // DOTS DE CONTINUACIÓN: el pie de toda lista. Tres puntos que dicen "hay más
  // de esto" — click y se suman por abajo (la tanda siguiente de la búsqueda o
  // los relacionados de la última pista). Nunca reordena lo que ya se ve.
  // El texto va en DOS spans a propósito: la frase fija se traduce sola por el
  // i18n del workspace y la consulta del usuario queda aparte, sin tocar.
  function _dotsHTML(fuente) {
    const q = (fuente && fuente.q || '').trim();
    const busqueda = fuente && fuente.tipo === 'busqueda' && q;
    const txt = busqueda ? 'Más como' : (fuente && fuente.tipo === 'canal') ? 'Más del canal' : 'Más música relacionada';
    const corta = q.length > 24 ? q.slice(0, 24) + '…' : q;
    return `<button class="jr-more" type="button" data-mas="1" aria-label="${_esc(txt + (busqueda ? ' «' + corta + '»' : ''))}">`
      + `<span class="jr-dots" aria-hidden="true"><i></i><i></i><i></i></span>`
      + `<span class="jr-more-txt">${_esc(txt)}</span>`
      + (busqueda ? `<span class="jr-more-q">«${_esc(corta)}»</span>` : '') + `</button>`;
  }
  function _pintarDots(box) {
    if (!box) return;
    const viejo = box.querySelector(':scope > .jr-more'); if (viejo) viejo.remove();
    if (!(box._items && box._items.length)) return;
    box.insertAdjacentHTML('beforeend', _dotsHTML(box._fuente));
  }

  // Suma filas al final de una lista (arriba de los dots) con entrada suave —
  // sin re-render: el scroll y las filas de arriba no se mueven.
  function _insertarFilas(box, items, desde) {
    if (!box || !items || !items.length) return;
    const html = items.map((t, k) => _filaHTML(t, desde + k)).join('');
    const dots = box.querySelector(':scope > .jr-more');
    if (dots) dots.insertAdjacentHTML('beforebegin', html); else box.insertAdjacentHTML('beforeend', html);
    const filas = box.querySelectorAll('.jr-row');
    for (let i = desde; i < filas.length; i++) filas[i].classList.add('jr-nueva');
  }

  function _renderFilas(box, items, error, fuente) {
    if (!box) return;
    box._items = items || []; box._fuente = fuente || null; box._esPl = false; box._error = error || null;
    box.innerHTML = (items && items.length) ? items.map((t, i) => _filaHTML(t, i)).join('')
      : `<div class="jr-hint">${_esc(error || 'Sin resultados')}</div>`;
    _pintarDots(box);
  }
  // Re-pinta las listas VISIBLES con el idioma actual (los contadores de YouTube
  // y los hints del backend se localizan al render; el observer de i18n no puede
  // "des-traducir" un texto que ya se insertó en inglés).
  function _repintarListas() {
    for (const box of [$('#jr-pane-rel'), $('#jr-cv-list'), $('#jr-pane-st')]) {
      if (!box || !box._items) continue;
      const eraPl = box._esPl;
      _renderFilas(box, box._items, box._error, box._fuente);
      if (eraPl) box._esPl = true;
    }
  }
  // Pinta la PLAYLIST (lo que suena + lo que sigue) en un pane y lo marca como
  // "es la playlist": los dots de ahí rellenan la cola de verdad.
  function _renderPlaylistEn(box) {
    if (!box) return;
    _renderFilas(box, _pl.items, 'Poné algo a sonar y te muestro relacionados.', _plFuente);
    box._esPl = true;
    _marcarActiva(false);
  }
  // Re-pinta las listas después de sumar pistas por abajo, conservando el
  // scroll (se agregan filas nuevas, no se re-renderiza todo).
  function _repintarPlaylist() {
    const rel = $('#jr-pane-rel'), cv = $('#jr-cv-list');
    // Nadie está mostrando la playlist (handoff del preview, reanudación): que
    // Relacionados la muestre — es "lo que viene" y tiene que verse.
    if (!(rel && rel._esPl) && !(cv && cv._esPl)) {
      if (rel && !_buscando && !_inChannel && _pl.items.length) { _renderPlaylistEn(rel); }
      if (_pane === 'q') _renderQueue();
      return;
    }
    for (const box of [rel, cv]) {
      if (!box || !box._esPl) continue;
      const desde = box._items.length;
      const nuevas = _pl.items.slice(desde);
      if (!nuevas.length) continue;
      box._items = _pl.items;
      _insertarFilas(box, nuevas, desde);
      _pintarDots(box);
    }
    if (_pane === 'q') _renderQueue();
  }
  // La fila que suena se resalta en TODAS las listas visibles (la playlist
  // avanza a la vista, sin re-render). `traer` = acompañar el auto-avance
  // dejando la fila a la vista.
  function _marcarActiva(traer) {
    const vid = _state && _state.track && _state.track.id;
    for (const box of [$('#jr-pane-rel'), $('#jr-cv-list')]) {
      if (!box) continue;
      let activa = null;
      for (const row of box.querySelectorAll('.jr-row')) {
        const on = !!vid && row.dataset.vid === vid;
        row.classList.toggle('activa', on);
        if (on && !activa) activa = row;
      }
      if (traer && activa && box._esPl && !box.hidden) {
        try { activa.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); } catch { activa.scrollIntoView(); }
      }
    }
    if (_pane === 'q') _renderQueue();
  }

  function _clicFila(e, getItems, box) {
    const items = getItems();
    if (e.target.closest('[data-mas]')) { _masEnLista(box || e.currentTarget); return; }
    const add = e.target.closest('[data-add]');
    if (add) {   // agregar a la cola = va al FINAL de lo que sigue
      const r = RY(), t = items[+add.dataset.add];
      if (r && t) { _pl = r.anexar(_pl, [t]); _repintarPlaylist(); if (_pane === 'q') _renderQueue(); }
      return;
    }
    const row = e.target.closest('.jr-row'); if (!row) return;
    const i = +row.dataset.i;
    if (!items[i]) return;
    // ESTA lista pasa a ser la cola: suena la elegida y siguen las de abajo.
    _reproducirDeLista(items, i, (box || e.currentTarget)._fuente);
    const b = box || e.currentTarget;
    b._items = _pl.items; b._esPl = true;
    for (const otro of [$('#jr-pane-rel'), $('#jr-cv-list')]) if (otro && otro !== b) otro._esPl = false;
    _marcarActiva(false);
  }

  // Los dots: traer MÁS para esta lista. Si la lista es la playlist, lo nuevo
  // entra en la cola real; si es una lista que solo se está mirando, se suma
  // ahí nomás (y seguirá siendo cola en cuanto toques una fila).
  const _masEnCurso = new WeakSet();
  async function _masEnLista(box) {
    if (!box || _masEnCurso.has(box)) return;
    const r = RY(); if (!r) return;
    const btn = box.querySelector(':scope > .jr-more');
    _masEnCurso.add(box);
    if (btn) btn.classList.add('cargando');
    try {
      if (box._esPl) {
        _plFuente = box._fuente || _plFuente;
        const n = await _rellenarCola();
        box._fuente = _plFuente;
        if (!n && btn) _dotsSinMas(box);
      } else {
        if (!box._fuente) box._fuente = { tipo: 'rel' };
        const nuevas = await _traerMas(box._fuente, box._items[box._items.length - 1]);
        const lista = r.anexar(r.crearLista(box._items, 0), nuevas, r.claveCancion);
        const suma = lista.items.slice(box._items.length);
        if (!suma.length) { _dotsSinMas(box); return; }
        const desde = box._items.length;
        box._items = lista.items;
        _insertarFilas(box, suma, desde);
        _pintarDots(box); _marcarActiva(false);
      }
    } catch {} finally {
      _masEnCurso.delete(box);
      const b2 = box.querySelector(':scope > .jr-more'); if (b2) b2.classList.remove('cargando');
    }
  }
  function _dotsSinMas(box) {
    const btn = box && box.querySelector(':scope > .jr-more'); if (!btn) return;
    btn.classList.remove('cargando'); btn.classList.add('fin'); btn.disabled = true;
    const q = btn.querySelector('.jr-more-q'); if (q) q.remove();
    const txt = btn.querySelector('.jr-more-txt'); if (txt) txt.textContent = 'No hay más por ahora';
  }

  function _renderQueue() {
    const box = $('#jr-pane-q'); if (!box) return;
    const r = RY(); const viene = r ? r.loQueViene(_pl) : [];
    if (!viene.length) { box.innerHTML = '<div class="jr-hint">La cola está vacía. Se llena sola con los relacionados de lo que suena.</div>'; return; }
    box.innerHTML = viene.map((t, i) =>
      `<div class="jr-qrow" data-i="${i}"><span class="jr-art jr-qart">${t.thumb ? `<img src="${_esc(t.thumb)}" alt="" onerror="this.remove()">` : ''}</span>`
      + `<span class="jr-rmeta"><b>${_esc(t.titulo)}</b><span class="s">${_esc(t.canal)}</span></span>`
      + `<span class="jr-qdur">${_esc(t.dur || '')}</span></div>`).join('');
  }
  // Click en la Cola: salta a esa pista de la playlist; lo de abajo sigue igual.
  function _clicQueue(e) {
    const row = e.target.closest('.jr-qrow'); if (!row) return;
    _irA(_pl.idx + 1 + (+row.dataset.i), false);
  }
  function _renderEstaciones() {
    const box = $('#jr-pane-st'); if (!box) return; const r = RY(); if (!r) return;
    box.innerHTML = r.ESTACIONES.map((e) =>
      `<button class="jr-station jr-st-${e.id}" data-q="${_esc(e.q)}"><span class="jr-st-name">${_esc(e.nombre)}</span><span class="jr-st-tag">${_esc(ST_META[e.id] || '')}</span></button>`).join('');
  }
  async function _estacion(q) {
    _setPane('rel');
    const box = $('#jr-pane-rel'); if (box) box.innerHTML = '<div class="jr-hint">Sintonizando…</div>';
    // Las estaciones son consultas curadas de YOUTUBE: van SIEMPRE por esa
    // mesa, aunque la pill del buscador esté en otra fuente.
    const f = _fuentes.youtube || _intFuente();
    const fid = 'youtube';
    try {
      const data = await f.buscar(q);
      const items = _filas(data, fid);
      const fuente = { tipo: 'estacion', q, token: data.token || null, fuente: fid };
      _renderFilas(box, items, data.error, fuente);
      // La estación ES la cola: suena la primera y siguen las de abajo, en orden.
      if (items[0]) { _buscando = false; _reproducirDeLista(items, 0, fuente); if (box) { box._items = _pl.items; box._esPl = true; } _marcarActiva(false); }
    } catch { _renderFilas(box, [], 'No se pudo sintonizar'); }
  }

  // ── Fuente LOCAL: biblioteca + subida de archivos ───────────────────────────
  // `_listarLocal` muestra los archivos de audio de data/music (listado viejo
  // de la mesa local, sin búsqueda). `_subirLocal` manda archivos al backend
  // (multipart) y re-pinta la lista actual.
  async function _listarLocal(box) {
    if (!box) return;
    _buscando = true;
    box.innerHTML = '<div class="jr-hint">Buscando…</div>';
    try {
      const res = await fetch(LISTAR_LOCAL);
      const data = await res.json();
      _renderFilas(box, _filas(data, 'local'), data.error || 'Nada en data/music todavía',
        { tipo: 'listar', fuente: 'local' });
    } catch { _renderFilas(box, [], 'No se pudo cargar la música local'); }
  }
  async function _subirLocal(files) {
    const arr = files ? Array.from(files) : [];
    if (!arr.length) return;
    const btn = $('#jr-upload-btn'); if (btn) btn.disabled = true;   // la card queda; el CSS pinta el estado
    try {
      const fd = new FormData();
      for (const file of arr) fd.append('archivos', file, file.name);
      const res = await fetch(SUBIR_LOCAL, { method: 'POST', body: fd });
      const data = await res.json();
      if (typeof toast !== 'function') return;
      if (res.ok && !data.error) {
        const n = (data.archivos || []).length;
        toast(n ? _t('Se subieron {n} archivos a data/music').replace('{n}', String(n)) : _t('No se subió ningún archivo'));
      } else toast(data.error || _t('No se pudo subir la música'));
    } catch { if (typeof toast === 'function') toast(_t('No se pudo subir la música')); }
    finally { if (btn) btn.disabled = false; }
    // Re-pinta lo que haya en el pane (búsqueda o biblioteca) para que se vea lo nuevo.
    const q = $('#jr-q'); const txt = (q && q.value.trim()) || '';
    const rel = $('#jr-pane-rel');
    if (txt && rel) _buscar(txt, '#jr-pane-rel');
    else if (rel && _fuenteActiva === 'local') { _buscando = false; _listarLocal(rel); }
  }

  // ── Selección de fuente (pills sobre el buscador) ───────────────────────────
  // La fuente activa mueve SOLO el buscador (y por ahí las continuaciones de
  // las listas que se vean desde ahí). Lo que ya suena sigue hasta que el
  // usuario toque otra pista: cada track lleva su `.fuente`.
  let _fuenteElegida = false;   // el usuario (o el storage) ya definió la fuente:
                                // una restauración tardía no la pisa
  function _leerFuentePersistida() {
    try {
      const s = localStorage.getItem(SRCKEY);
      if (s && _fuentes[s] && !_fuenteElegida) { _fuenteActiva = s; _fuenteElegida = true; }
    } catch {}
  }
  async function _setFuente(id) {
    if (!_fuentes[id]) return;
    _fuenteElegida = true;
    if (id === _fuenteActiva) return;
    _guardarPane(_fuenteActiva);   // memoria por fuente: congelá lo que ésta veía
    _fuenteActiva = id;
    try { localStorage.setItem(SRCKEY, id); } catch {}
    _renderFuentes();
    const rel = $('#jr-pane-rel');
    if (rel && _panes[id]) { _restaurarPane(id, rel); return; }   // snapshot → re-render inmediato
    if (rel) { rel.innerHTML = ''; rel._items = []; rel._fuente = null; rel._esPl = false; }
    const q = $('#jr-q'); const txt = q ? q.value.trim() : '';
    if (txt) { _buscar(txt, '#jr-pane-rel'); return; }
    const f = _intFuente() || _fuentes.youtube;
    if (!rel || !f) return;
    _buscando = false;
    if (id === 'local') { _listarLocal(rel); return; }
    if (f.catalogo) {
      // Fuente con catálogo propio: su buscar('') es un filtro local.
      try {
        const data = await f.buscar('');
        if (_fuenteActiva !== id) return;   // el usuario re-cambó la fuente en el medio
        _renderFilas(rel, _filas(data, id), data.error || 'No se pudo cargar el catálogo',
          { tipo: 'catalogo', q: '', token: data.token || null, fuente: id });
      } catch { if (_fuenteActiva === id) _renderFilas(rel, [], 'No se pudo cargar el catálogo'); }
      return;
    }
    if (_pl.items.length) _renderPlaylistEn(rel);
    else rel.innerHTML = '<div class="jr-hint">Buscá música o elegí una estación.</div>';
  }
  // Transcribe EN→ES si el hint capturado ya pasó por el observer de i18n
  // (inverse del DICT, mejor esfuerzo: la vista restaurada queda en la frase
  // canónica y el observer se encarga de re-traducirla si el idioma es EN).
  const _hintEs = (t) => {
    const I = root.JarvisI18n;
    if (I && I.DICT && t) {
      for (const k of Object.keys(I.DICT)) if (I.DICT[k] === t) return k;
    }
    return t || '';
  };
  function _guardarPane(fid) {
    const rel = $('#jr-pane-rel'); if (!rel) return;
    const sc = rel.closest('.jr-scroll');
    _panes[fid] = {
      items: rel._items || [],
      fuente: rel._fuente || null,
      esPl: !!rel._esPl,
      buscando: _buscando,
      q: ($('#jr-q') && $('#jr-q').value) || '',
      hint: _hintEs((rel.querySelector(':scope > .jr-hint') || {}).textContent || ''),
      scrollTop: sc ? sc.scrollTop : 0,
    };
  }
  function _restaurarPane(fid, box) {
    const snap = _panes[fid];
    if (!snap || !box) return false;
    const q = $('#jr-q'); if (q) q.value = snap.q || '';
    _buscando = !!snap.buscando;
    if (snap.esPl) {
      // La lista visible era la playlist: renderizarla FRESCA (la real, que
      // siguió creciendo mientras se veía otra fuente) y conservar la mesa de
      // continuación que la parió (snap.fuente) para los dots.
      _renderPlaylistEn(box);
      if (snap.fuente && snap.fuente !== _plFuente) box._fuente = Object.assign({}, snap.fuente);
      _marcarActiva(false);
    } else {
      _renderFilas(box, snap.items || [], snap.hint || null, snap.fuente);
    }
    const sc = box.closest('.jr-scroll');
    if (sc) requestAnimationFrame(() => { sc.scrollTop = snap.scrollTop; });
    return true;
  }
  function _renderFuentes() {
    _syncPlaceholder();
    const row = $('#jr-src'); if (!row) return;
    let html = _ordenFuentes.map((id) => {
      const fs = _fuentes[id];
      return `<button type="button" class="jr-src-pill${id === _fuenteActiva ? ' on' : ''}" data-src="${id}" aria-pressed="${id === _fuenteActiva}">${_esc(_etiquetaFuente(fs))}</button>`;
    }).join('');
    row.innerHTML = html;
    // La subida de música NO vive en la fila de pills: es una card propia que
    // se muestra SOLO cuando la fuente activa es 'local' (entre hints y lista).
    const up = $('#jr-upload'); if (up) up.hidden = _fuenteActiva !== 'local';
    _renderHints();
    // Hook opcional de la fuente activa: al ENTRAR (o re-pintar) la fuente se
    // le da la palabra (spotify: verificar sesión → hint de login si no hay).
    const fa = _intFuente() || _fuentes.youtube;
    if (fa && typeof fa.alActivar === 'function') { try { fa.alActivar(); } catch {} }
  }

  // ── Placeholder del buscador por fuente + hints de la fuente (spotify) ──────
  // El placeholder cambia con la fuente activa; los hints (Premium, "no
  // configurado", sesión) los empuja la fuente vía JarvisRadio.hintDeFuente()
  // y SOLO se muestran cuando esa fuente es la activa (si no, molestar).
  const _PH = {
    youtube: 'Buscá música o pegá un link de YouTube…',
    local: 'Buscá en data/music…',
    spotify: 'Buscá música o pegá un link de Spotify…',
  };
  function _syncPlaceholder() {
    const q = $('#jr-q'); if (!q) return;
    const f = _intFuente(); if (!f) return;
    q.placeholder = _t(_PH[f.id] || 'Buscá música o pegá un link de YouTube…');
  }
  const _hintsFuente = {};
  // `extra` opcional: {boton: 'Conectá Spotify'} → cada línea del hint se
  // acompaña de un botón que llama al `login` de la fuente activa;
  // {boton, url} → el "botón" es un LINK (target _blank) a esa url (ej:
  // "Abrir Spotify" de la cuenta Premium — ahí NO hay nada que loguear).
  // REPLACE, no append: una fuente solo puede tener UNA línea activa — si
  // re-emite el MISMO contenido, no se pisa nada (evita parpadeos y
  // duplicados por re-emisión tardía).
  function hintDeFuente(fid, texto, extra) {
    if (!fid) return;
    const txt = (typeof texto === 'string' && texto.trim()) ? texto : null;
    const boton = (extra && extra.boton) ? extra.boton : null;
    const url = (extra && extra.url) ? extra.url : null;
    const prev = _hintsFuente[fid];
    if (prev && prev.txt === txt && prev.boton === boton && prev.url === url) return;
    if (txt) _hintsFuente[fid] = { txt: txt, boton: boton, url: url };
    else delete _hintsFuente[fid];
    _renderHints();
  }
  function _renderHints() {
    const cont = $('#jr-src-hints'); if (!cont) return;
    const h = _hintsFuente[_fuenteActiva];
    const txt = h && h.txt;
    if (!txt) { cont.hidden = true; cont.innerHTML = ''; return; }
    cont.hidden = false; cont.innerHTML = '';
    const vistas = [];   // dedupe: la MISMA línea emitida dos veces → una sola
    for (const linea of String(txt).split('\n')) {
      const l = linea.trim();
      if (!l || vistas.includes(l)) continue;
      vistas.push(l);
      const b = document.createElement('div'); b.className = 'jr-src-hint';
      const span = document.createElement('span'); span.textContent = l;
      b.appendChild(span);
      if (h && h.boton) {
        let bt;
        if (h.url) {
          bt = document.createElement('a'); bt.href = h.url; bt.target = '_blank'; bt.rel = 'noopener noreferrer';
        } else if (!l.includes('Premium')) {
          bt = document.createElement('button'); bt.type = 'button';
          bt.addEventListener('click', () => {
            const f = _fuentes[_fuenteActiva];
            if (f && typeof f.login === 'function') { try { f.login(); } catch {} }
          });
        }
        if (bt) { bt.className = 'jr-src-hint-btn'; bt.textContent = h.boton; b.appendChild(bt); }
      }
      cont.appendChild(b);
    }
    if (root.JarvisI18n && typeof root.JarvisI18n.aplicar === 'function') {
      try { root.JarvisI18n.aplicar(cont); } catch {}
    }
  }

  // ── Vista de canal ──
  function _abrirCanal(track) {
    if (_fuenteDe(track) !== 'youtube') return;   // solo YouTube tiene canal; Local no
    _inChannel = true; $('#jr-browse').hidden = true;
    const ch = $('#jr-channel'); ch.hidden = false;
    ch.innerHTML =
      `<div class="jr-cv-head"><button class="jr-cv-back" id="jr-cv-back" title="Volver">${svg('back')}</button>`
      + `<span class="jr-cv-av">${track.thumb ? '' : ''}</span>`
      + `<span class="jr-cv-meta"><b>${_esc(track.canal || 'Canal')}</b><span>${svg('users')} YouTube · videos del canal</span></span>`
      + `<button class="jr-cv-play" id="jr-cv-play" title="Reproducir">${svg('play')} Reproducir</button></div>`
      + `<div class="jr-scroll"><div class="jr-pane" id="jr-cv-list"></div></div>`;
    ch.querySelector('#jr-cv-back').addEventListener('click', () => { _inChannel = false; ch.hidden = true; $('#jr-browse').hidden = false; });
    ch.querySelector('#jr-cv-list').addEventListener('click', (e) => {
      const list = $('#jr-cv-list'); _clicFila(e, () => (list._items || []), list);
    });
    ch.querySelector('#jr-cv-play').addEventListener('click', () => {   // el canal entero como cola
      const list = $('#jr-cv-list'), items = (list && list._items) || [];
      if (!items.length) return;
      _reproducirDeLista(items, 0, list._fuente);
      list._items = _pl.items; list._esPl = true;
      const rel = $('#jr-pane-rel'); if (rel) rel._esPl = false;
      _marcarActiva(true);
    });
    _cargarCanal(track);
  }

  // ── Boot temprano: reanuda la música ASAP tras un reinicio ──
  function _boot() {
    if (_booted) return;
    const r = RY(); if (!r) return;
    const st = r.deserializar(_leer());
    if (!st || !st.track || !st.sonando) return;
    _booted = true; _state = st; _intent = true; _played.add(st.track.id);
    const kb = _clave(st.track); if (kb) _playedClaves.add(kb);
    // El track guardado conoce su fuente: se reanuda con SU player (iframe o
    // <audio>), nunca a ciegas con YouTube.
    const p = _playerDe(st.track);
    if (p && p.cargar) p.cargar(st.track, { autoplay: true, start: st.t });
    _armarGesto(); _guardadoPeriodico();
    _sembrarPlaylist(st.track);
    _renderNow(); _renderMini();
  }

  // ── Init ──
  let _tries = 0;
  function init() {
    _montar();
    if (!_mounted && _tries < 40) { _tries++; setTimeout(init, 120); }   // el header puede tardar
  }

  // ── Registro de fuentes + API pública ───────────────────────────────────────
  // registrarFuente() es la superficie ESTABLE para los demás agentes (spotify/
  // ). Cada fuente aporta SU buscador (buscar/mas/relacionados) y SU
  // player. Las etiquetas son las de las pills del popover. No romper la firma:
  // otros agentes ya la usan.
  //
  // ─────────────────────── FIRMA ESTABLE DEL API ───────────────────────────────
  // window.JarvisRadio.registrarFuente({
  //   id, etiqueta_es, etiqueta_en, buscar, mas, relacionados, player
  // })
  //
  //   id: string úNICO de la fuente ('youtube' | 'local' | 'spotify' | ...).
  //       Idempotente: re-registrar un id ya visto NO pisa (devolver el ya
  //       registrado). El id viaja en cada track como `track.fuente` y se
  //       persiste: es la clave del player correcto al reanudar.
  //   etiqueta_es / etiqueta_en: strings de la pill del popover (fallback al
  //       id). La i18n las traduce con las claves 'YouTube'/'Local'/'Spotify'/
  //       'Streams' — registrá la tuya también en radio.js _i18n() o quedará
  //       en ES.
  //   buscar(q): Promise<{resultados, token?, error?}> — búsqueda de texto
  //       libre. `resultados` = filas {id, url, titulo, canal, duracion, thumb}
  //       ya normalizables por la Radio; `token` = marca de continuación (o
  //       null/undefined = no hay más).
  //   mas(token): Promise<{resultados, token?, error?}> — la tanda SIGUIENTE
  //       de la búsqueda anterior (recibe el `token` que devolvió buscar()).
  //       Sin continuación: Promise.resolve({resultados: []}).
  //   relacionados(id): Promise<{resultados, error?}> — relacionados de una
  //       pista (id = el id de la fila). Sin relacionados o la fuente de V1
  //       no los tiene: Promise.resolve({resultados: []}).
  //   player: { cargar(track, {autoplay, start}), cmd(func, args),
  //             onMensaje(e), destroy() } — el adaptador del reproductor:
  //     · cargar(track, {autoplay, start}): empieza a sonar `track` (o lo
  //       deja cueado si autoplay=false; start>1 reanuda en ese segundo).
  //     · cmd(func, args): vocabulario fijo de YouTube —
  //       'playVideo'|'pauseVideo'|'seekTo'([sec])|'setVolume'([0-100])|
  //       'mute'|'unMute'. Traducí a tu motor.
  //     · onMensaje(e): recibí los eventos del player como infoDelivery
  //       {duration, currentTime, playerState(0=fin,1=play,2=pausa)} y
  //       onError {info: código}: son los canales que usa la Radio para el
  //       seek, repeat, auto-avance y la racha de errores.
  //     · destroy(): pausá y liberá TU player cuando la Radio salta a otra
  //       fuente (p.ej. <audio>.pause() + src=""; el iframe de youtube usa
  //       pauseVideo y se queda montado).
  // ─────────────────────────────────────────────────────────────────────────────
  function registrarFuente(esp) {
    if (!esp || typeof esp.id !== 'string' || !esp.id) return null;
    if (_fuentes[esp.id]) return _fuentes[esp.id];   // idempotente: re-registrar no pisa
    // Object.assign(esp, ...): conserva extras públicos (catalogo,
    // login/hints de spotify) sin tocar el contrato de la firma.
    const f = Object.assign({}, esp, {
      id: esp.id,
      etiqueta_es: (esp.etiqueta_es || esp.id),
      etiqueta_en: (esp.etiqueta_en || esp.etiqueta_es || esp.id),
      buscar: (typeof esp.buscar === 'function') ? esp.buscar : async () => ({ resultados: [] }),
      mas: (typeof esp.mas === 'function') ? esp.mas : async () => ({ resultados: [] }),
      relacionados: (typeof esp.relacionados === 'function') ? esp.relacionados : async () => ({ resultados: [] }),
      player: esp.player || null,
    });
    _fuentes[f.id] = f; _ordenFuentes.push(f.id);
    _players[f.id] = _normalizarPlayer(f.player);
    _leerFuentePersistida();
    _renderFuentes();
    return f;
  }

  // Fuente 'youtube' (defecto): busca/mas/rel + player iframe postMessage. Su
  // comportamiento es EXACTAMENTE el de siempre — solo quedó envuelto en el
  // adaptador de player.
  registrarFuente({
    id: 'youtube', etiqueta_es: 'YouTube', etiqueta_en: 'YouTube',
    buscar: async (q) => { try { return await (await fetch(BUSCAR + encodeURIComponent(q))).json(); } catch { return { resultados: [], error: 'No se pudo buscar' }; } },
    mas: async (token) => { try { return await (await fetch(MAS + encodeURIComponent(token))).json(); } catch { return { resultados: [], token: null }; } },
    relacionados: async (id) => { try { return await (await fetch(REL + encodeURIComponent(id))).json(); } catch { return { resultados: [], error: 'No se pudo buscar' }; } },
    player: {
      cargar(track, opts) {
        _asegurarAudio().src = _src(track, Object.assign({ autoplay: !_twitch, start: 0 }, opts || {}));
        if (_twitch) _ytCmd('pauseVideo');
      },
      cmd: (func, args) => _ytCmd(func, args),
      onMensaje: (e) => _onMensaje(e),
      destroy: () => { _ytCmd('pauseVideo', []); },
    },
  });

  // Fuente 'local': archivos de audio del workspace (data/music). Busca con
  // modo=local (backend en paralelo); la subida es UI directa a /api/radio/
  // local/subir. MAS/REL de V1 no tienen continuación real: la cola sigue por
  // lo que haya abajo de la lista y, si se agota, el fallback de `_traerMas`
  // busca por título en la misma fuente (dedupe por id/canción).
  registrarFuente({
    id: 'local', etiqueta_es: 'Local', etiqueta_en: 'Local',
    buscar: async (q) => { try { return await (await fetch(BUSCAR_LOCAL + encodeURIComponent(q))).json(); } catch { return { resultados: [], error: 'No se pudo buscar' }; } },
    mas: async () => ({ resultados: [], token: null }),
    relacionados: async () => ({ resultados: [] }),
    player: _playerLocal,
  });

  // Fuente activa persistida (respetala al abrir; si no está registrada,
  // queda el default youtube). Se re-lee en cada registro: spotify
  // llegan DESPUÉS del init y la preferencia debe aplicar igual.
  _leerFuentePersistida();

  // `play` público = poner algo a sonar desde afuera (mismo camino que el
  // handoff del preview: nace una playlist con esa pista y sigue por relacionados).
  root.JarvisRadio = { init, open, close, play: adopt, adopt, pauseForTwitch, resumeAfterTwitch, estado: () => _state, registrarFuente, fuentes: () => _ordenFuentes.slice(), hintDeFuente, fuenteHint: hintDeFuente };

  // Boot ASAP (resume) + montaje de UI cuando el DOM esté listo.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { _boot(); init(); }, { once: true });
  } else { _boot(); init(); }

})(typeof window !== 'undefined' ? window : globalThis);
