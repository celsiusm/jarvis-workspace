'use strict';
// ─── Memoria: lógica pura de metadatos ───────────────────────────────────────
// SIN DOM (patrón UMD _pure, como live-state.js): badges de estado
// (lápida/obsoleta/lección), sublínea de la lista y resumen de salud del
// linter. memory.js la consume; los tests corren en Node.

(function (root) {

  const _t = (s) => (root.JarvisI18n && root.JarvisI18n.t) ? root.JarvisI18n.t(s) : s;

  // Badges visuales de una memoria: estado no-vigente primero, después la
  // marca de lección (tags [leccion] = regla de prevención del enjambre).
  function badges(m) {
    const out = [];
    const estado = ((m && m.estado) || 'vigente').toLowerCase();
    if (estado === 'lapida')   out.push({ k: 'lapida',   label: 'lápida' });
    if (estado === 'obsoleta') out.push({ k: 'obsoleta', label: 'obsoleta' });
    if (((m && m.tags) || []).some(t => String(t).toLowerCase() === 'leccion')) {
      out.push({ k: 'leccion', label: 'lección' });
    }
    return out;
  }

  // "autor · fecha · N links" — la fecha viva es actualizado (si existe) con
  // el prefijo "act." para distinguir frescura de creación.
  function subLinea(m) {
    m = m || {};
    const partes = [m.autor || '—'];
    if (m.actualizado) partes.push(_t('act. {f}').replace('{f}', m.actualizado));
    else if (m.creado) partes.push(m.creado);
    const n = (m.links || []).length;
    if (n) partes.push(n + ' link' + (n !== 1 ? 's' : ''));
    return partes.join(' · ');
  }

  // Contadores no-cero del endpoint /memory/salud → [{k, n}] para el strip.
  // Incluye los chequeos de las capas de coherencia y endurecimiento.
  function problemasSalud(salud) {
    if (!salud) return [];
    const out = [];
    const push = (k, n) => { if (n) out.push({ k: k, n: n }); };
    push('rotos', (salud.rotos || []).length);
    push('citas', (salud.citas_muertas || []).length);
    push('huerfanas', (salud.huerfanas || []).length);
    push('contrato', (salud.contrato || []).length);
    push('choques', (salud.choques || []).length);
    push('cuarentena', (salud.cuarentena || []).length);
    push('guard', (salud.candidatas_guard || []).length);
    push('duplicados', (salud.duplicados || []).length);
    push('global', (salud.candidatas_global || []).length);
    return out;
  }

  // Línea compacta del loop de lecciones (o null si el backend no lo trae):
  // cuántas reglas están siempre-cargadas y si el destilador API está trabado
  // (señales sobre el umbral sin API key = alerta — nada de degradar en silencio).
  function estadoLecciones(salud) {
    const l = (salud && salud.lecciones) || null;
    if (!l || !Object.keys(l).length) return null;
    const partes = [];
    partes.push(_t('{n} lecciones cargadas').replace('{n}', l.lecciones_memoria || 0));
    const sen = l.senales_pendientes || 0;
    const umb = l.umbral || 0;
    let alerta = false;
    if (!l.activo) {
      partes.push(_t('destilador OFF'));
    } else if (!l.api_ok && sen >= umb && umb > 0) {
      partes.push(_t('destilador trabado: {n}/{m} señales, sin API key').replace('{n}', sen).replace('{m}', umb));
      alerta = true;
    } else {
      partes.push(_t('destilador: {n}/{m} señales').replace('{n}', sen).replace('{m}', umb));
    }
    return { texto: partes.join(' · '), alerta: alerta };
  }

  // Salud desglosada por categoría → filas SOLO de las que tienen problemas,
  // ordenadas por cantidad de problemas desc (el cuadro más sucio arriba).
  function categoriasSalud(salud) {
    const por = (salud && salud.por_categoria) || {};
    const filas = [];
    for (const cid of Object.keys(por)) {
      const c = por[cid];
      const problemas = (c.rotos || 0) + (c.citas_muertas || 0) +
                        (c.huerfanas || 0) + (c.contrato || 0);
      if (problemas) filas.push({ id: cid, nombre: c.nombre || cid, total: c.total || 0, problemas: problemas });
    }
    filas.sort((a, b) => b.problemas - a.problemas || a.nombre.localeCompare(b.nombre));
    return filas;
  }

  // Altímetro (7 días): ¿el recall rinde? inyectadas vs leídas de verdad
  // (según el cierre de los agentes) y cuántas lecturas fueron en pasos OK.
  // null mientras no hay datos (rodaje): una línea de ceros solo mete ruido.
  function altimetro(salud) {
    const a = (salud && salud.altimetro) || null;
    if (!a || !Object.keys(a).length) return null;
    const iny = a.inyecciones || 0;
    const lec = a.lecturas || 0;
    if (!iny && !lec) return null;
    const partes = [_t('{d}d: {n} inyectadas').replace('{d}', a.dias || 7).replace('{n}', iny)];
    let leidas = (a.tasa_lectura !== null && a.tasa_lectura !== undefined)
      ? _t('{n} leídas ({p}%)').replace('{n}', lec).replace('{p}', Math.round(a.tasa_lectura * 100))
      : _t('{n} leídas').replace('{n}', lec);
    partes.push(leidas);
    partes.push(_t('{n} en pasos OK').replace('{n}', a.lecturas_en_done || 0));
    return { texto: _t('Altímetro') + ' ' + partes.join(' · ') };
  }

  // ── Atlas (rediseño 2026-09): categorías como constelaciones ──────────────
  // La taxonomía canónica vive en plotspace/protocolos/categorias.json; acá
  // solo el matiz (hue OKLCH) con que se pinta cada una. El CSS arma el color
  // con oklch(var(--mc-l) var(--mc-c) var(--h)) → L/C siguen al tema (claros
  // más profundos), el matiz identifica la categoría. Sin hex en ningún lado.
  const CATEGORIAS = [
    { id: 'terminales', nombre: 'Terminales & tmux',   hue: 220 },
    { id: 'ui',         nombre: 'UI · Workspace',      hue: 290 },
    { id: 'swarm',      nombre: 'Backend & Swarm',     hue: 155 },
    { id: 'diseno',     nombre: 'Diseño & Craft',      hue: 330 },
    { id: 'preview',    nombre: 'Web Preview & Radio', hue: 255 },
    { id: 'cuentas',    nombre: 'Cuentas & CLIs',      hue: 88 },
    { id: 'voz',        nombre: 'Voz & Audio',         hue: 22 },
    { id: 'desktop',    nombre: 'Desktop',             hue: 122 },
    { id: 'entorno',    nombre: 'Entorno · WSL & Git', hue: 55 },
    { id: 'producto',   nombre: 'Producto & Roadmap',  hue: 190 },
  ];
  const SIN_CAT = { id: 'sin-clasificar', nombre: 'Sin clasificar', hue: 270, neutra: true };

  function categoria(id) {
    return CATEGORIAS.find(c => c.id === id) || (id && id !== SIN_CAT.id
      ? { id: id, nombre: id, hue: 270, neutra: true } : SIN_CAT);
  }

  // Categorías presentes con su cantidad, en el orden canónico (las
  // desconocidas y sin-clasificar al final).
  function contarCategorias(memorias) {
    const n = {};
    for (const m of memorias || []) {
      const id = (m && m.categoria) || SIN_CAT.id;
      n[id] = (n[id] || 0) + 1;
    }
    const orden = CATEGORIAS.map(c => c.id);
    return Object.keys(n)
      .sort((a, b) => {
        const ia = orden.indexOf(a), ib = orden.indexOf(b);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
      })
      .map(id => Object.assign({}, categoria(id), { n: n[id] }));
  }

  // Estado normalizado (lápida con tilde también vale).
  function estadoDe(m) {
    const e = String((m && m.estado) || 'vigente').toLowerCase().replace('á', 'a');
    return e || 'vigente';
  }
  const esLeccion = (m) => ((m && m.tags) || []).some(t => String(t).toLowerCase() === 'leccion');

  // Filtro único que comparten la Lista y el Grafo.
  // f = { q, cats: [ids] (vacío = todas), estado: 'todas'|'vigente'|'obsoleta'
  //       |'lapida'|'archivo'|'leccion', slugs: [slugs] | null }
  function filtrar(memorias, f) {
    f = f || {};
    const q = String(f.q || '').trim().toLowerCase();
    const cats = f.cats && f.cats.length ? new Set(f.cats) : null;
    const slugs = f.slugs ? new Set(f.slugs) : null;
    const estado = f.estado || 'todas';
    return (memorias || []).filter(m => {
      if (cats && !cats.has(m.categoria || SIN_CAT.id)) return false;
      if (slugs && !slugs.has(m.slug)) return false;
      if (estado === 'leccion' && !esLeccion(m)) return false;
      if (estado !== 'todas' && estado !== 'leccion' && estadoDe(m) !== estado) return false;
      if (!q) return true;
      return [m.titulo, m.resumen, m.slug, m.autor].some(s => String(s || '').toLowerCase().includes(q))
        || (m.tags || []).some(t => String(t).toLowerCase().includes(q));
    });
  }

  // Cuántas memorias hay por estado (para los chips del filtro).
  function contarEstados(memorias) {
    const out = { todas: 0, vigente: 0, obsoleta: 0, lapida: 0, archivo: 0, leccion: 0 };
    for (const m of memorias || []) {
      out.todas++;
      const e = estadoDe(m);
      if (e in out) out[e]++;
      if (esLeccion(m)) out.leccion++;
    }
    return out;
  }

  // Vecindario de una memoria: a quién enlaza, quién la cita y qué links
  // apuntan a memorias que no existen (todavía).
  function conexiones(slug, edges, memorias) {
    const salientes = [], entrantes = [];
    for (const e of edges || []) {
      if (e.from === slug && !salientes.includes(e.to)) salientes.push(e.to);
      if (e.to === slug && !entrantes.includes(e.from)) entrantes.push(e.from);
    }
    const existe = new Set((memorias || []).map(m => m.slug));
    const propia = (memorias || []).find(m => m.slug === slug);
    const rotos = ((propia && propia.links) || []).filter(l => l !== slug && !existe.has(l));
    return { salientes, entrantes, rotos };
  }

  // Grado (conexiones únicas, en cualquier dirección) por slug.
  function grados(edges) {
    const vec = {};
    for (const e of edges || []) {
      (vec[e.from] = vec[e.from] || new Set()).add(e.to);
      (vec[e.to] = vec[e.to] || new Set()).add(e.from);
    }
    const out = {};
    for (const k in vec) out[k] = vec[k].size;
    return out;
  }

  // slug → [tipos de problema] desde /memory/salud (para marcar las cards).
  function marcasSalud(salud) {
    const out = {};
    if (!salud) return out;
    const add = (slug, k) => {
      if (!slug) return;
      const arr = out[slug] || (out[slug] = []);
      if (!arr.includes(k)) arr.push(k);
    };
    for (const x of salud.rotos || []) add(x.slug, 'rotos');
    for (const x of salud.citas_muertas || []) add(x.slug, 'citas');
    for (const s of salud.huerfanas || []) add(typeof s === 'string' ? s : s.slug, 'huerfanas');
    for (const x of salud.contrato || []) add(x.slug, 'contrato');
    for (const x of salud.choques || []) { add(x.a, 'choques'); add(x.b, 'choques'); }
    for (const s of salud.cuarentena || []) add(typeof s === 'string' ? s : s.slug, 'cuarentena');
    for (const x of salud.duplicados || []) { add(x.a, 'duplicados'); add(x.b, 'duplicados'); }
    return out;
  }

  // Slugs afectados por UN tipo de problema (el chip de salud filtra la lista).
  function slugsDe(salud, k) {
    const m = marcasSalud(salud);
    return Object.keys(m).filter(s => m[s].includes(k)).sort();
  }

  // Pulso de salud: % de memorias sin ningún problema del linter.
  function puntajeSalud(salud, total) {
    if (!total) return null;
    const conProblemas = Object.keys(marcasSalud(salud)).length;
    const sanas = Math.max(0, total - conProblemas);
    return { pct: Math.round(sanas / total * 100), sanas: sanas, conProblemas: conProblemas };
  }

  // "2026-07-09" (o ISO) → "hoy" / "ayer" / "hace 3 d" / "hace 2 sem" / …
  function fechaRelativa(fecha, ahora) {
    if (!fecha) return '';
    const d = new Date(String(fecha).length === 10 ? fecha + 'T12:00:00' : fecha);
    if (isNaN(d)) return String(fecha);
    const hoy = ahora ? new Date(ahora) : new Date();
    const a = Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
    const b = Date.UTC(hoy.getFullYear(), hoy.getMonth(), hoy.getDate());
    const dias = Math.round((b - a) / 86400000);
    if (dias <= 0) return _t('hoy');
    if (dias === 1) return _t('ayer');
    if (dias < 14) return _t('hace {n} d').replace('{n}', dias);
    if (dias < 60) return _t('hace {n} sem').replace('{n}', Math.round(dias / 7));
    if (dias < 365) return _t('hace {n} mes').replace('{n}', Math.round(dias / 30));
    return _t('hace {n} a').replace('{n}', Math.round(dias / 365));
  }

  // Timestamp ISO de un evento → "12s" / "4m" / "3h" / "2d" (relojito del feed).
  function haceCorto(iso, ahora) {
    const t = new Date(iso).getTime();
    if (isNaN(t)) return '';
    const s = Math.max(0, Math.round(((ahora || Date.now()) - t) / 1000));
    if (s < 60) return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm';
    if (s < 86400) return Math.floor(s / 3600) + 'h';
    return Math.floor(s / 86400) + 'd';
  }

  // Resultado de un evento de memoria_uso → etiqueta + clase visual.
  function resultadoUso(r) {
    switch (r) {
      case 'inyectada': return { k: 'inyectada', label: 'sugerida' };
      case 'done':      return { k: 'done',      label: 'leída · OK' };
      case 'blocked':   return { k: 'blocked',   label: 'leída · bloqueado' };
      case 'error':     return { k: 'error',     label: 'leída · error' };
      default:          return { k: 'otro',      label: String(r || '—') };
    }
  }

  // ── Markdown mini (puro): escapa PRIMERO, después marca. ──────────────────
  // Soporta: #/##/###, listas (-, *, 1.), > cita, ``` bloques, `código`,
  // **negrita**, *itálica*, [texto](http…), --- y [[wikilinks]] (botón con
  // data-link; opts.wikiIcon = HTML del ícono que lo acompaña).
  function escHTML(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function _inline(txt, opts) {
    const code = [];
    let h = escHTML(txt).replace(/`([^`\n]+)`/g, (_, c) => { code.push(c); return '\u0000' + (code.length - 1) + '\u0000'; });
    h = h.replace(/\[\[([^\]\n]+)\]\]/g, (_, t) =>
      `<button type="button" class="mem-wikilink" data-link="${t}">${opts.wikiIcon || ''}<span>${t}</span></button>`);
    h = h.replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    h = h.replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>');
    h = h.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, '$1<i>$2</i>');
    h = h.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${code[+i]}</code>`);
    return h;
  }

  function markdown(src, opts) {
    opts = opts || {};
    const lineas = String(src == null ? '' : src).replace(/\r\n?/g, '\n')
      .replace(/^---\n[\s\S]*?\n---\s*/, '').split('\n');
    const out = [];
    let para = [], lista = null, cita = [];
    const cerrarPara = () => { if (para.length) { out.push(`<p>${para.map(l => _inline(l, opts)).join('<br>')}</p>`); para = []; } };
    const cerrarLista = () => { if (lista) { out.push(`<${lista.tag}>${lista.items.map(i => `<li>${_inline(i, opts)}</li>`).join('')}</${lista.tag}>`); lista = null; } };
    const cerrarCita = () => { if (cita.length) { out.push(`<blockquote>${cita.map(l => _inline(l, opts)).join('<br>')}</blockquote>`); cita = []; } };
    const cerrar = () => { cerrarPara(); cerrarLista(); cerrarCita(); };
    for (let i = 0; i < lineas.length; i++) {
      const l = lineas[i];
      const fence = l.match(/^\s*```\s*([\w+-]*)\s*$/);
      if (fence) {
        cerrar();
        const buf = [];
        i++;
        while (i < lineas.length && !/^\s*```\s*$/.test(lineas[i])) buf.push(lineas[i++]);
        out.push(`<pre${fence[1] ? ` data-lang="${escHTML(fence[1])}"` : ''}><code>${escHTML(buf.join('\n'))}</code></pre>`);
        continue;
      }
      let m;
      if (!l.trim()) { cerrar(); continue; }
      if ((m = l.match(/^(#{1,4})\s+(.+)$/))) {
        cerrar();
        const n = Math.min(m[1].length, 3);
        out.push(`<h${n}>${_inline(m[2], opts)}</h${n}>`);
        continue;
      }
      if (/^\s*(---|\*\*\*)\s*$/.test(l)) { cerrar(); out.push('<hr>'); continue; }
      if ((m = l.match(/^\s*>\s?(.*)$/))) { cerrarPara(); cerrarLista(); cita.push(m[1]); continue; }
      if ((m = l.match(/^\s*([-*]|\d+[.)])\s+(.+)$/))) {
        cerrarPara(); cerrarCita();
        const tag = /\d/.test(m[1]) ? 'ol' : 'ul';
        if (lista && lista.tag !== tag) cerrarLista();
        if (!lista) lista = { tag: tag, items: [] };
        lista.items.push(m[2]);
        continue;
      }
      if (lista && /^\s{2,}\S/.test(l)) { lista.items[lista.items.length - 1] += ' ' + l.trim(); continue; }
      cerrarLista(); cerrarCita();
      para.push(l);
    }
    cerrar();
    return out.join('');
  }

  // Resumen de una card: markdown → texto plano (sin **, `, [[ ]], #, >).
  function textoPlano(s) {
    return String(s == null ? '' : s)
      .replace(/\[\[([^\]\n]+)\]\]/g, '$1')
      .replace(/\[([^\]\n]+)\]\([^)\s]+\)/g, '$1')
      .replace(/\*\*|__|`/g, '')
      .replace(/^\s*(#{1,6}|>|[-*]|\d+[.)])\s+/, '')
      .replace(/\s+/g, ' ').trim();
  }

  // Texto de un [[wikilink]] → slug (mismo criterio que _slugify del router:
  // sin acentos, minúsculas, todo lo no alfanumérico = guión).
  function slugDeLink(t) {
    const s = String(t || '').normalize('NFKD').replace(/[̀-ͯ]/g, '')
      .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
    return s.slice(0, 64) || 'memoria';
  }

  const api = { badges, subLinea, problemasSalud, categoriasSalud, estadoLecciones, altimetro,
    slugDeLink, textoPlano, CATEGORIAS, categoria, contarCategorias, estadoDe, esLeccion, filtrar, contarEstados,
    conexiones, grados, marcasSalud, slugsDe, puntajeSalud, fechaRelativa, haceCorto,
    resultadoUso, escHTML, markdown };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.JarvisMemoryMeta = api;

}(typeof self !== 'undefined' ? self : this));
