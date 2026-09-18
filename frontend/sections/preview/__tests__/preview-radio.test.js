'use strict';
// Tests de la lógica pura de la Radio (preview-radio.js). Node puro, patrón _pure.
const assert = require('assert');
const R = require('../preview-radio.js')._pure;

let n = 0;
const test = (nombre, fn) => { fn(); n++; };

test('ESTACIONES: 6 estaciones con id/nombre/q y ids únicos', () => {
  assert.strictEqual(R.ESTACIONES.length, 6);
  const ids = R.ESTACIONES.map((e) => e.id);
  assert.strictEqual(new Set(ids).size, 6);
  for (const e of R.ESTACIONES) {
    assert.ok(e.id && e.nombre && e.q, `estación incompleta: ${JSON.stringify(e)}`);
  }
});

test('crearEstado arranca vacío, en pausa y en el segundo 0', () => {
  assert.deepStrictEqual(R.crearEstado(), { track: null, sonando: false, t: 0 });
});

test('fuenteDe: la pista manda, default youtube y def propio', () => {
  assert.strictEqual(R.fuenteDe({ id: 'x', fuente: 'local' }), 'local');
  assert.strictEqual(R.fuenteDe({ id: 'x' }), 'youtube');          // tracks viejos emitidos son youtube
  assert.strictEqual(R.fuenteDe(null), 'youtube');
  assert.strictEqual(R.fuenteDe({ id: 'x' }, 'spotify'), 'spotify');
  assert.strictEqual(R.fuenteDe({ fuente: '' }), 'youtube');       // vacío = ausente
});

test('pistaDeResultado normaliza un resultado del backend', () => {
  const r = { id: 'abc123', url: 'https://www.youtube.com/watch?v=abc123', titulo: 'lofi', canal: 'Lofi Girl', thumb: 'http://x/t.jpg', duracion: 'EN VIVO' };
  assert.deepStrictEqual(R.pistaDeResultado(r), {
    id: 'abc123', titulo: 'lofi', canal: 'Lofi Girl', thumb: 'http://x/t.jpg', dur: 'EN VIVO',
    url: 'https://www.youtube.com/watch?v=abc123',
  });
});
test('pistaDeResultado devuelve null sin id', () => {
  assert.strictEqual(R.pistaDeResultado({ titulo: 'x' }), null);
  assert.strictEqual(R.pistaDeResultado(null), null);
});

test('elegir setea la pista, arranca sonando y en el segundo 0', () => {
  const t = { id: 'v1', titulo: 'a', canal: 'b', thumb: null, dur: '', url: 'u' };
  assert.deepStrictEqual(R.elegir(R.crearEstado(), t), { track: t, sonando: true, t: 0 });
});

test('alternar togglea sonando solo si hay pista y conserva la posición', () => {
  const t = { id: 'v1', titulo: 'a', canal: 'b' };
  const e1 = R.conPosicion(R.elegir(R.crearEstado(), t), 42);  // sonando true, t=42
  const p = R.alternar(e1);
  assert.strictEqual(p.sonando, false);
  assert.strictEqual(p.t, 42);                       // pausar NO rebobina
  assert.strictEqual(R.alternar(p).sonando, true);
  assert.strictEqual(R.alternar(p).t, 42);
  // sin pista: no-op
  assert.deepStrictEqual(R.alternar(R.crearEstado()), { track: null, sonando: false, t: 0 });
});

test('conPosicion actualiza t, clampa negativos y descarta basura', () => {
  const t = { id: 'v1' };
  const e = R.elegir(R.crearEstado(), t);
  assert.strictEqual(R.conPosicion(e, 123.7).t, 123.7);
  assert.strictEqual(R.conPosicion(e, -5).t, 0);
  assert.strictEqual(R.conPosicion(e, NaN).t, 0);
  assert.strictEqual(R.conPosicion(e, Infinity).t, 0);
  // conserva pista y sonando
  const c = R.conPosicion(e, 10);
  assert.deepStrictEqual(c.track, t);
  assert.strictEqual(c.sonando, true);
});

test('pistaDeUrl saca el videoId de watch/embed/youtu.be', () => {
  assert.strictEqual(R.pistaDeUrl('https://www.youtube.com/watch?v=abc123', 'T').id, 'abc123');
  assert.strictEqual(R.pistaDeUrl('https://www.youtube.com/embed/abc123?autoplay=1').id, 'abc123');
  assert.strictEqual(R.pistaDeUrl('https://youtu.be/abc123', 'T').id, 'abc123');
  const t = R.pistaDeUrl('https://www.youtube.com/watch?v=xyz', 'Mi mix');
  assert.strictEqual(t.titulo, 'Mi mix');
  assert.ok(t.thumb.includes('/vi/xyz/'));
  assert.strictEqual(t.url, 'https://www.youtube.com/watch?v=xyz');
});
test('pistaDeUrl devuelve null si no es YouTube', () => {
  assert.strictEqual(R.pistaDeUrl('https://vimeo.com/123', 'x'), null);
  assert.strictEqual(R.pistaDeUrl('http://localhost:5173/', 'x'), null);
  assert.strictEqual(R.pistaDeUrl('no-una-url', 'x'), null);
});

test('urlEmbed arma el embed con jsapi/origin y autoplay opcional', () => {
  const u = R.urlEmbed('abc123', { origin: 'http://localhost:3000', autoplay: true });
  assert.ok(u.includes('/embed/abc123'));
  assert.ok(u.includes('enablejsapi=1'));
  assert.ok(u.includes('origin=http'));
  assert.ok(u.includes('autoplay=1'));
  const u2 = R.urlEmbed('abc123', { origin: 'http://localhost:3000', autoplay: false });
  assert.ok(!u2.includes('autoplay=1'));
});

test('serializar/deserializar hacen roundtrip de pista + posición + estado', () => {
  const t = { id: 'v1', titulo: 'a', canal: 'b', thumb: 't', dur: 'd', url: 'u' };
  // sonando en el segundo 87.6 → se persiste play:true y t=87 (floor)
  const estado = R.conPosicion(R.elegir(R.crearEstado(), t), 87.6);
  const data = R.serializar(estado);
  assert.deepStrictEqual(data, { track: t, t: 87, play: true });
  const e = R.deserializar(data);
  assert.deepStrictEqual(e.track, t);
  assert.strictEqual(e.sonando, true);   // reanuda sonando donde quedó (no más cued forzado)
  assert.strictEqual(e.t, 87);
  // pausado → play:false, y al restaurar NO reanuda
  const pausado = R.alternar(estado);    // sonando false, t=87.6
  assert.deepStrictEqual(R.serializar(pausado), { track: t, t: 87, play: false });
  assert.strictEqual(R.deserializar(R.serializar(pausado)).sonando, false);
});

test('serializar sin pista y deserializar de basura/compat', () => {
  assert.deepStrictEqual(R.serializar(null), { track: null });
  assert.deepStrictEqual(R.serializar(R.crearEstado()), { track: null });
  assert.deepStrictEqual(R.deserializar(null), { track: null, sonando: false, t: 0 });
  // compat: dato viejo con solo {track} (sin t/play) → cued en 0, sin reanudar
  const t = { id: 'v9', titulo: 'x' };
  assert.deepStrictEqual(R.deserializar({ track: t }), { track: t, sonando: false, t: 0 });
});

// claveCancion: la MISMA canción bajo videos distintos (official/en vivo/otro
// upload) tiene que dar la MISMA clave — es el anti-bucle de la Radio (el
// dedupe por id de video dejaba pasar la misma canción con otro id y volvía a
// sonar a las 2-3 pistas). Casos tomados de resultados reales de YouTube.
test('claveCancion: la misma canción en versiones distintas da la misma clave', () => {
  const c = 'Soda Stereo';
  const oficial = R.claveCancion('Soda Stereo - De Música Ligera (Official Video)', c);
  assert.ok(oficial);
  assert.strictEqual(R.claveCancion('Soda Stereo - De Musica Ligera (El Último Concierto)', c), oficial);
  assert.strictEqual(R.claveCancion('SODA STEREO - De musica ligera en vivo', c), oficial);
});

test('claveCancion: matchea aunque un título no lleve el artista adelante', () => {
  const c = 'Soda Stereo';
  assert.strictEqual(
    R.claveCancion('En la Ciudad de la Furia', c),
    R.claveCancion('Soda Stereo - En la Ciudad de la Furia (Official Video)', c));
});

test('claveCancion: canciones distintas dan claves distintas', () => {
  const c = 'Soda Stereo';
  assert.notStrictEqual(R.claveCancion('Soda Stereo - Zoom (Official Video)', c),
    R.claveCancion('Soda Stereo - Signos (Gira Me Verás Volver)', c));
  assert.notStrictEqual(R.claveCancion('Cuando Pase el Temblor', c),
    R.claveCancion('Persiana Americana', c));
});

test('claveCancion: sin canal también normaliza; basura da vacío', () => {
  assert.strictEqual(R.claveCancion('Lofi Beats (24/7 radio)', ''),
    R.claveCancion('LOFI BEATS', null));
  assert.strictEqual(R.claveCancion('', 'Canal'), '');
  assert.strictEqual(R.claveCancion('(Official Video)', 'Canal'), '');
  assert.strictEqual(R.claveCancion(null, null), '');
});

test('claveCancion: no vacía un título que ES solo la frase de ruido', () => {
  assert.ok(R.claveCancion('Live', 'Canal'));   // "live" a secas es el título, no ruido
});

// ─── Playlist: lo que se VE es lo que SIGUE ──────────────────────────────────
// La lista visible (búsqueda, canal, estación o relacionados) ES la cola: al
// tocar una fila suena esa y después siguen las de abajo, EN ORDEN. Antes la
// cola se reconstruía con los relacionados del tema elegido y "lo que seguía"
// no era lo que el usuario estaba viendo (bug reportado 2026-07-20).

const _t = (id, titulo, canal) => ({ id, titulo: titulo || id, canal: canal || 'C' });
const _lista3 = () => R.crearLista([_t('a'), _t('b'), _t('c')], 0);

test('crearLista guarda los items y clampa el índice', () => {
  assert.deepStrictEqual(R.crearLista([_t('a'), _t('b')], 1).idx, 1);
  assert.strictEqual(R.crearLista([_t('a')], 9).idx, 0);
  assert.strictEqual(R.crearLista([_t('a')], -3).idx, 0);
  assert.deepStrictEqual(R.crearLista(null), { items: [], idx: -1 });
  assert.strictEqual(R.crearLista([], 0).idx, -1);
});

test('crearLista copia el array (no lo aliasa)', () => {
  const items = [_t('a')];
  const l = R.crearLista(items, 0);
  items.push(_t('b'));
  assert.strictEqual(l.items.length, 1);
});

test('siguienteIdx avanza EN ORDEN por la lista visible', () => {
  const l = _lista3();
  assert.strictEqual(R.siguienteIdx(l), 1);
  assert.strictEqual(R.siguienteIdx(R.saltarA(l, 1)), 2);
  assert.strictEqual(R.siguienteIdx(R.saltarA(l, 2)), -1);   // final: no hay más
  assert.strictEqual(R.siguienteIdx(R.crearLista([])), -1);
});

test('siguienteIdx NO saltea repetidos: la lista se respeta tal cual se ve', () => {
  const l = R.crearLista([_t('a'), _t('a'), _t('b')], 0);
  assert.strictEqual(R.siguienteIdx(l), 1);
});

test('anteriorIdx retrocede y frena en la primera', () => {
  const l = _lista3();
  assert.strictEqual(R.anteriorIdx(R.saltarA(l, 2)), 1);
  assert.strictEqual(R.anteriorIdx(l), -1);
});

test('saltarA elige una fila cualquiera y deja el resto INTACTO', () => {
  const l = R.saltarA(_lista3(), 2);
  assert.strictEqual(l.idx, 2);
  assert.deepStrictEqual(l.items.map((t) => t.id), ['a', 'b', 'c']);
  assert.strictEqual(R.saltarA(_lista3(), 99).idx, 0);   // fuera de rango: no mueve
});

test('pistaEn / loQueViene describen lo que sigue', () => {
  const l = _lista3();
  assert.strictEqual(R.pistaEn(l, 1).id, 'b');
  assert.strictEqual(R.pistaEn(l, 7), null);
  assert.deepStrictEqual(R.loQueViene(l).map((t) => t.id), ['b', 'c']);
  assert.deepStrictEqual(R.loQueViene(R.saltarA(l, 2)), []);
  assert.strictEqual(R.porDelante(l), 2);
});

test('anexar suma por ABAJO sin tocar el orden ni el índice', () => {
  const l = R.anexar(R.saltarA(_lista3(), 1), [_t('d'), _t('e')]);
  assert.deepStrictEqual(l.items.map((t) => t.id), ['a', 'b', 'c', 'd', 'e']);
  assert.strictEqual(l.idx, 1);
});

test('anexar dedupea por id de video y por CANCIÓN (misma canción, otro id)', () => {
  const l = R.crearLista([{ id: 'v1', titulo: 'Smooth Criminal (Official Video)', canal: 'MJ' }], 0);
  const dos = R.anexar(l, [
    { id: 'v1', titulo: 'Smooth Criminal', canal: 'MJ' },            // mismo id
    { id: 'v2', titulo: 'Smooth Criminal (Live)', canal: 'MJ' },     // misma canción, otro id
    { id: 'v3', titulo: 'Beat It', canal: 'MJ' },                    // nueva
    { id: 'v3', titulo: 'Beat It', canal: 'MJ' },                    // repetida dentro del lote
  ], R.claveCancion);
  assert.deepStrictEqual(dos.items.map((t) => t.id), ['v1', 'v3']);
});

test('anexar sin claveDe dedupea solo por id, y descarta basura', () => {
  const l = R.anexar(R.crearLista([_t('a')], 0), [_t('a'), _t('b'), null, { titulo: 'sin id' }]);
  assert.deepStrictEqual(l.items.map((t) => t.id), ['a', 'b']);
});

test('anexar sobre una lista vacía deja el índice listo para sonar', () => {
  const l = R.anexar(R.crearLista([]), [_t('a'), _t('b')]);
  assert.deepStrictEqual(l.items.map((t) => t.id), ['a', 'b']);
  assert.strictEqual(l.idx, -1);   // nada sonando todavía: lo elige quien reproduce
});

test('mezclarCola solo baraja lo que VIENE (lo ya sonado y la actual no se mueven)', () => {
  const l = R.crearLista([_t('a'), _t('b'), _t('c'), _t('d'), _t('e')], 1);
  const m = R.mezclarCola(l, () => 0);   // rnd determinista
  assert.deepStrictEqual(m.items.slice(0, 2).map((t) => t.id), ['a', 'b']);
  assert.strictEqual(m.idx, 1);
  assert.deepStrictEqual([...m.items.slice(2).map((t) => t.id)].sort(), ['c', 'd', 'e']);
});

test('mezclarCola con 0 o 1 pista por delante no rompe', () => {
  const l = R.saltarA(_lista3(), 2);
  assert.deepStrictEqual(R.mezclarCola(l, Math.random).items.map((t) => t.id), ['a', 'b', 'c']);
});

// ─── localizarConteo: contadores de YouTube según el idioma de la app ─────────
// El backend pide YouTube en español (Accept-Language: es), así que "vistas" y
// "lo están viendo" salían crudos aunque la app esté en inglés.
test('localizarConteo EN traduce vistas / viendo / suscriptores', () => {
  assert.strictEqual(R.localizarConteo('20 k vistas', 'en'), '20 k views');
  assert.strictEqual(R.localizarConteo('1,2 M de vistas', 'en'), '1,2 M views');
  assert.strictEqual(R.localizarConteo('77 k lo están viendo', 'en'), '77 k watching');
  assert.strictEqual(R.localizarConteo('3 suscriptores', 'en'), '3 subscribers');
  assert.strictEqual(R.localizarConteo('1,2 mil vistas', 'en'), '1,2 K views');
});

test('localizarConteo ES deja el texto de YouTube tal cual', () => {
  assert.strictEqual(R.localizarConteo('20 k vistas', 'es'), '20 k vistas');
  assert.strictEqual(R.localizarConteo('77 k lo están viendo', 'es'), '77 k lo están viendo');
});

test('localizarConteo no toca números, duraciones ni vacíos', () => {
  assert.strictEqual(R.localizarConteo('13:55', 'en'), '13:55');
  assert.strictEqual(R.localizarConteo('', 'en'), '');
  assert.strictEqual(R.localizarConteo(null, 'en'), '');
});

console.log(`preview-radio.test.js — ${n} tests OK`);
