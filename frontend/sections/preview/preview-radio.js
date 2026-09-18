'use strict';
// ─── Web Preview: lógica pura de la Radio ────────────────────────────────────
// La Radio es un buscador de YouTube de música con estaciones curadas: buscás
// (o elegís una estación), suena en un reproductor persistente del workspace y
// no ocupa una pestaña. Esta es la máquina pura (SIN DOM): estado de la pista,
// normalización de resultados del backend, y la URL del embed. El panel + el
// iframe de audio persistente + el fetch al backend viven en preview.js.
// Patrón UMD _pure, testeable en Node.
//
// estado = { track: {id,titulo,canal,thumb,dur,url}|null, sonando: bool, t: seg }
//   t = último segundo reproducido (para reanudar exacto tras un reinicio).

(function (root) {

  // Estaciones = búsquedas de YouTube curadas para codear (un click y suena).
  const ESTACIONES = [
    { id: 'lofi',   nombre: 'Lo-fi',      q: 'lofi hip hop radio beats to relax study to' },
    { id: 'synth',  nombre: 'Synthwave',  q: 'synthwave radio' },
    { id: 'focus',  nombre: 'Deep Focus', q: 'deep focus music for coding concentration' },
    { id: 'jazz',   nombre: 'Jazz',       q: 'relaxing jazz music coffee' },
    { id: 'chill',  nombre: 'Chillhop',   q: 'chillhop radio beats to study relax' },
    { id: 'piano',  nombre: 'Piano',      q: 'relaxing piano music instrumental' },
  ];

  function crearEstado() {
    return { track: null, sonando: false, t: 0 };
  }

  // Segundo de reproducción actual → estado (para reanudar donde quedó). Clampa
  // a ≥0 y descarta basura (NaN/Infinity → 0).
  function conPosicion(estado, t) {
    const base = estado || crearEstado();
    const n = Number(t);
    return { track: base.track, sonando: base.sonando, t: (isFinite(n) && n > 0) ? n : 0 };
  }

  // Los contadores que da YouTube vienen LOCALIZADOS por el Accept-Language del
  // backend (siempre 'es'), así que "20 k vistas"/"77 k lo están viendo"
  // aparecían en español aunque la app esté en inglés. Traducimos las PALABRAS
  // (no los números: "77 k"/"1,2 M" quedan como los da YouTube).
  const _CONTEO_PALABRAS = [
    [/\blo est[áa]n viendo\b/gi, 'lo están viendo', 'watching'],
    [/\bviendo ahora\b/gi, 'viendo ahora', 'watching'],
    [/\bde espectadores\b/gi, 'de espectadores', 'viewers'],
    [/\bespectadores\b/gi, 'espectadores', 'viewers'],
    [/\bde reproducciones\b/gi, 'de reproducciones', 'plays'],
    [/\breproducciones\b/gi, 'reproducciones', 'plays'],
    [/\bde suscriptores\b/gi, 'de suscriptores', 'subscribers'],
    [/\bsuscriptores\b/gi, 'suscriptores', 'subscribers'],
    [/\bde me gusta\b/gi, 'de me gusta', 'likes'],
    [/\bme gusta\b/gi, 'me gusta', 'likes'],
    [/\bde vistas\b/gi, 'de vistas', 'views'],
    [/\bvistas\b/gi, 'vistas', 'views'],
  ];
  function localizarConteo(texto, lang) {
    let s = String(texto == null ? '' : texto);
    if (!s) return s;
    const en = lang === 'en';
    for (const [re, esTxt, enTxt] of _CONTEO_PALABRAS) s = s.replace(re, en ? enTxt : esTxt);
    if (en) s = s.replace(/\bmil millones\b/gi, 'B').replace(/\bmillones\b/gi, 'M').replace(/\bmil\b/gi, 'K');
    return s;
  }

  // Normaliza un resultado del backend (buscar_youtube: {id,url,titulo,canal,
  // duracion,thumb}) a una pista de la Radio. null si no trae id de video.
  function pistaDeResultado(r) {
    if (!r || !r.id) return null;
    return {
      id: r.id,
      titulo: r.titulo || '',
      canal: r.canal || '',
      thumb: r.thumb || null,
      dur: r.duracion || '',
      url: r.url || `https://www.youtube.com/watch?v=${r.id}`,
    };
  }

  // Extrae el videoId de una URL de YouTube (watch / embed / youtu.be) y arma
  // una pista para "mover a la Radio" una pestaña. null si no es YouTube.
  function pistaDeUrl(url, titulo) {
    let id = null;
    try {
      const u = new URL(url);
      const host = u.hostname.toLowerCase().replace(/^(www\.|m\.)/, '');
      if (host === 'youtu.be') id = u.pathname.slice(1).split('/')[0] || null;
      else if (host === 'youtube.com' || host === 'youtube-nocookie.com') {
        if (u.pathname === '/watch') id = u.searchParams.get('v');
        else if (u.pathname.startsWith('/embed/')) id = u.pathname.slice(7).split('/')[0] || null;
      }
    } catch { /* no-URL */ }
    if (!id) return null;
    return {
      id, titulo: titulo || '', canal: '', dur: '',
      thumb: `https://i.ytimg.com/vi/${id}/hqdefault.jpg`,
      url: `https://www.youtube.com/watch?v=${id}`,
    };
  }

  // Clave de CANCIÓN (no de video): la misma canción vive en YouTube bajo
  // varios ids (official video / en vivo / re-upload) y el dedupe por id no
  // los ve — la Radio volvía a sonar el mismo tema a las 2-3 pistas. Normaliza
  // el título (sin tildes/paréntesis/puntuación), le saca el artista adelante
  // (si coincide con el canal) y el ruido del final ("en vivo", "official
  // video"...). '' = no se pudo derivar clave (tratarla como única).
  const _RUIDO_FIN = [
    'official music video', 'official video', 'video oficial', 'official audio',
    'audio oficial', 'official visualizer', 'visualizer', 'lyric video',
    'video lyric', 'lyrics', 'letra', 'audio', 'en vivo', 'live', 'hd', '4k',
    'remastered', 'remasterizado', 'remaster',
  ];
  function claveCancion(titulo, canal) {
    const norm = (s) => String(s == null ? '' : s)
      .toLowerCase()
      .normalize('NFD').replace(/[\u0300-\u036f]/g, '')   // sin tildes (ñ→n incluida)
      .replace(/[([{][^)\]}]*[)\]}]/g, ' ')               // fuera (…)/[…]/{…}
      .replace(/[^a-z0-9\s]/g, ' ')                       // solo letras/números
      .replace(/\s+/g, ' ').trim();
    let t = norm(titulo);
    if (!t) return '';
    const c = norm(canal);
    if (c && t.startsWith(c + ' ')) t = t.slice(c.length + 1);
    let cambio = true;
    while (cambio) {                                      // puede haber varias frases de ruido
      cambio = false;
      for (const fin of _RUIDO_FIN) {
        if (t.length > fin.length + 2 && t.endsWith(' ' + fin)) {
          t = t.slice(0, -(fin.length + 1)).trim(); cambio = true;
        }
      }
    }
    return t;
  }

  // Fuente efectiva de una pista: la propia (los tracks de fuentes nuevas la
  // traen en `.fuente`) o 'youtube' por defecto — los tracks viejos persistidos
  // no la tenían. `def` permite otro default.
  function fuenteDe(t, def) {
    const f = t && t.fuente;
    return (typeof f === 'string' && f) ? f : (def || 'youtube');
  }

  function elegir(estado, track) {
    return { track, sonando: true, t: 0 };
  }

  // Play/pausa. Sin pista, no hace nada (no se puede sonar la nada). Conserva la
  // posición t (pausar no rebobina).
  function alternar(estado) {
    const t = (estado && estado.t) || 0;
    if (!estado || !estado.track) return { track: (estado && estado.track) || null, sonando: false, t };
    return { track: estado.track, sonando: !estado.sonando, t };
  }

  // URL del embed de YouTube para el reproductor. enablejsapi+origin SIEMPRE
  // (habilita el control por postMessage); autoplay/start opcionales.
  function urlEmbed(id, { origin, autoplay = true, start = 0 } = {}) {
    const p = new URLSearchParams();
    p.set('enablejsapi', '1');
    if (origin) p.set('origin', origin);
    if (autoplay) p.set('autoplay', '1');
    if (start > 3) p.set('start', String(Math.floor(start)));
    return `https://www.youtube.com/embed/${id}?${p.toString()}`;
  }

  // Persistencia: pista + segundo actual (t) + si estaba sonando (play), para
  // reanudar EXACTO donde quedó tras un reinicio. `deserializar` reconstruye la
  // intención (sonando = estaba sonando): preview.js reanuda con autoplay y, si
  // el navegador lo bloquea (política de autoplay), un gesto lo despausa.
  // Compat: datos viejos `{track}` (sin t/play) → cued en 0.
  function serializar(estado) {
    if (!estado || !estado.track) return { track: null };
    return { track: estado.track, t: Math.floor(estado.t || 0), play: !!estado.sonando };
  }
  function deserializar(data) {
    if (!data || !data.track || !data.track.id) return crearEstado();
    const t = Number(data.t);
    return { track: data.track, sonando: !!data.play, t: (isFinite(t) && t > 0) ? t : 0 };
  }

  // ─── Playlist: lo que se VE es lo que SIGUE ────────────────────────────────
  // Una lista = { items: [pista…], idx: índice de la que suena (-1 = ninguna) }.
  // La lista que el usuario está mirando (búsqueda, canal, estación o
  // relacionados) ES la cola: tocás una fila y siguen las de abajo, en orden.
  // Antes la cola se reconstruía con los relacionados del tema elegido y "lo
  // que seguía" no era lo que se veía. Todo acá es puro (sin DOM) e inmutable:
  // devuelve listas nuevas, nunca muta la que recibe.

  function crearLista(items, idx = 0) {
    const arr = (Array.isArray(items) ? items : []).filter((t) => t && t.id);
    if (!arr.length) return { items: [], idx: -1 };
    const i = Number(idx);
    return { items: arr.slice(), idx: (isFinite(i) && i >= 0 && i < arr.length) ? Math.floor(i) : 0 };
  }
  const _norm = (lista) => (lista && Array.isArray(lista.items)) ? lista : { items: [], idx: -1 };

  function siguienteIdx(lista) {
    const l = _norm(lista);
    return (l.idx + 1 < l.items.length) ? l.idx + 1 : -1;
  }
  function anteriorIdx(lista) {
    const l = _norm(lista);
    return (l.idx > 0) ? l.idx - 1 : -1;
  }
  function saltarA(lista, i) {
    const l = _norm(lista);
    const n = Number(i);
    if (!(isFinite(n) && n >= 0 && n < l.items.length)) return { items: l.items.slice(), idx: l.idx };
    return { items: l.items.slice(), idx: Math.floor(n) };
  }
  function pistaEn(lista, i) {
    const l = _norm(lista);
    return l.items[i] || null;
  }
  function loQueViene(lista) {
    const l = _norm(lista);
    return l.items.slice(l.idx + 1);
  }
  function porDelante(lista) { return loQueViene(lista).length; }

  // Suma pistas por ABAJO (relacionados, tanda siguiente de la búsqueda) sin
  // tocar el orden visible ni el índice. Dedupea por id de video y —si se pasa
  // `claveDe`— por CANCIÓN: la misma canción vuelve con otro id (official/en
  // vivo) y re-sonaba a las 2-3 pistas.
  function anexar(lista, nuevos, claveDe) {
    const l = _norm(lista);
    const ids = new Set(l.items.map((t) => t.id));
    const claves = new Set(claveDe ? l.items.map((t) => claveDe(t.titulo, t.canal)).filter(Boolean) : []);
    const suma = [];
    for (const t of (Array.isArray(nuevos) ? nuevos : [])) {
      if (!t || !t.id || ids.has(t.id)) continue;
      const k = claveDe ? claveDe(t.titulo, t.canal) : '';
      if (k && claves.has(k)) continue;
      ids.add(t.id); if (k) claves.add(k);
      suma.push(t);
    }
    return { items: l.items.concat(suma), idx: l.idx };
  }

  // Baraja SOLO lo que viene (Fisher-Yates): lo ya sonado y la pista actual se
  // quedan donde están — mezclar no rebobina.
  function mezclarCola(lista, rnd) {
    const l = _norm(lista);
    const r = typeof rnd === 'function' ? rnd : Math.random;
    const cabeza = l.items.slice(0, l.idx + 1);
    const cola = l.items.slice(l.idx + 1);
    for (let i = cola.length - 1; i > 0; i--) {
      const j = Math.floor(r() * (i + 1));
      [cola[i], cola[j]] = [cola[j], cola[i]];
    }
    return { items: cabeza.concat(cola), idx: l.idx };
  }

  const _pure = {
    ESTACIONES, crearEstado, conPosicion, pistaDeResultado, pistaDeUrl, claveCancion, fuenteDe, elegir, alternar, urlEmbed, serializar, deserializar,
    crearLista, siguienteIdx, anteriorIdx, saltarA, pistaEn, loQueViene, porDelante, anexar, mezclarCola, localizarConteo,
  };

  root.WebPreviewRadio = { _pure };
  if (typeof module !== 'undefined' && module.exports) module.exports = root.WebPreviewRadio;

})(typeof window !== 'undefined' ? window : globalThis);
