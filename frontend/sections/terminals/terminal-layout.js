// JARVIS — TerminalLayout: mosaico de terminales que SIEMPRE llena el espacio.
// Modelo: grilla por fracciones. Al agregar/quitar se re-acomoda en una grilla
// balanceada (cols=ceil(√N), última fila estira). Resize = divisor de paneles:
// la vecina se achica/crece en su lugar (no se mueve). Drag = intercambia celdas.
// La lógica pura (sin DOM) es testeable en Node; el motor DOM solo se instala en
// el navegador. Espeja el patrón window.JarvisEditor.
(function (global) {
  'use strict';

  // Zoom de la Escala de la app: los eventos del mouse llegan en píxeles de
  // PANTALLA y este módulo mide el grid en píxeles CSS (clientWidth). Sin
  // dividir, con la app al 125% el divisor se corría 25% más que el cursor.
  const _zoomApp = () => (global.JarvisEscala && global.JarvisEscala.zoom) ? global.JarvisEscala.zoom() : 1;

  // ── Constantes (en píxeles) ──────────────────────────────────────
  const MIN_W = 160, MIN_H = 90;   // tamaño mínimo usable de una celda
  // Gutter entre celdas: lee el token --terminal-gap (shared/tokens.css);
  // fallback 10 para Node (tests) o si el token no existe.
  const GAP = (typeof document !== 'undefined' && typeof getComputedStyle === 'function')
    ? (parseInt(getComputedStyle(document.documentElement).getPropertyValue('--terminal-gap'), 10) || 10)
    : 10;

  // ── Lógica pura (sin DOM) ────────────────────────────────────────
  // Estructura: { rows: [ { hFrac, cells: [ {id, wFrac} ] } ] }
  //   Σ rows[].hFrac == 1 ; por fila Σ cells[].wFrac == 1.

  // ¿Se solapan dos rects? (bordes que se tocan NO cuentan como solape)
  function overlaps(a, b) {
    return a.x < b.x + b.w && b.x < a.x + a.w &&
           a.y < b.y + b.h && b.y < a.y + a.h;
  }

  // Grilla balanceada para una lista de ids (orden = lectura izq→der, arriba→abajo).
  // cols = ceil(√N); las filas se llenan con `cols` celdas y la ÚLTIMA reparte el
  // resto (estira). Fracciones parejas dentro de cada fila y entre filas.
  function balancedGrid(ids) {
    const n = ids.length;
    if (n === 0) return { rows: [] };
    const cols = Math.ceil(Math.sqrt(n));
    const nRows = Math.ceil(n / cols);
    const rows = [];
    let i = 0;
    for (let r = 0; r < nRows; r++) {
      const remaining = n - i;
      const count = (r === nRows - 1) ? remaining : Math.min(cols, remaining);
      const cells = [];
      for (let c = 0; c < count; c++) cells.push({ id: String(ids[i++]), wFrac: 1 / count });
      rows.push({ hFrac: 1 / nRows, cells });
    }
    return { rows };
  }

  // Lista de ids en orden de lectura.
  function gridIds(grid) {
    const out = [];
    for (const row of grid.rows) for (const c of row.cells) out.push(c.id);
    return out;
  }

  // Ubica (rowIdx, cellIdx) de un id, o null si no está.
  function findCell(grid, id) {
    id = String(id);
    for (let r = 0; r < grid.rows.length; r++) {
      const ci = grid.rows[r].cells.findIndex(c => c.id === id);
      if (ci !== -1) return { rowIdx: r, cellIdx: ci };
    }
    return null;
  }

  // Deriva rects {x,y,w,h} en px que llenan W×H con GAP como gutter y margen.
  function deriveRects(grid, W, H, GAP_) {
    const g = (GAP_ == null) ? GAP : GAP_;
    const out = {};
    const nRows = grid.rows.length;
    if (!nRows) return out;
    const innerH = H - (nRows + 1) * g;
    let y = g;
    for (let r = 0; r < nRows; r++) {
      const row = grid.rows[r];
      const rowH = Math.round(row.hFrac * innerH);
      const nCells = row.cells.length;
      const innerW = W - (nCells + 1) * g;
      let x = g;
      for (let c = 0; c < nCells; c++) {
        const cell = row.cells[c];
        const cellW = Math.round(cell.wFrac * innerW);
        out[cell.id] = { x, y, w: cellW, h: rowH };
        x += cellW + g;
      }
      y += rowH + g;
    }
    return out;
  }

  // Divisores invisibles en los gutters: uno VERTICAL entre cada par de celdas
  // adyacentes de una fila (alimenta resizeVertical) y uno HORIZONTAL entre
  // cada par de filas, a lo ancho de todo el panel (resizeHorizontal mueve
  // filas enteras). Misma aritmética de redondeo que deriveRects para que cada
  // divisor calce exacto en el gap entre rects.
  function deriveDividers(grid, W, H, GAP_) {
    const g = (GAP_ == null) ? GAP : GAP_;
    const out = [];
    const nRows = grid.rows.length;
    if (!nRows) return out;
    const innerH = H - (nRows + 1) * g;
    let y = g;
    for (let r = 0; r < nRows; r++) {
      const row = grid.rows[r];
      const rowH = Math.round(row.hFrac * innerH);
      const nCells = row.cells.length;
      const innerW = W - (nCells + 1) * g;
      let x = g;
      for (let c = 0; c < nCells; c++) {
        x += Math.round(row.cells[c].wFrac * innerW);
        if (c < nCells - 1) out.push({ type: 'v', rowIdx: r, cellIdx: c, x, y, w: g, h: rowH });
        x += g;
      }
      y += rowH;
      if (r < nRows - 1) out.push({ type: 'h', rowIdx: r, x: 0, y, w: W, h: g });
      y += g;
    }
    return out;
  }

  function _clone(grid) {
    return { rows: grid.rows.map(r => ({ hFrac: r.hFrac, cells: r.cells.map(c => ({ id: c.id, wFrac: c.wFrac })) })) };
  }

  // Divisor vertical entre la celda cellIdx y su vecina cellIdx+1 de la misma fila:
  // transfiere deltaFrac de la vecina a la celda (clamp para que ambos >= minFrac).
  // Si no hay vecina a la derecha → no-op (devuelve el mismo grid).
  function resizeVertical(grid, rowIdx, cellIdx, deltaFrac, minFrac) {
    const row = grid.rows[rowIdx];
    if (!row || cellIdx < 0 || cellIdx >= row.cells.length - 1) return grid;
    const a = row.cells[cellIdx].wFrac, b = row.cells[cellIdx + 1].wFrac;
    const t = Math.max(minFrac - a, Math.min(b - minFrac, deltaFrac));
    if (t === 0) return grid;
    const g = _clone(grid);
    g.rows[rowIdx].cells[cellIdx].wFrac = a + t;
    g.rows[rowIdx].cells[cellIdx + 1].wFrac = b - t;
    return g;
  }

  // Divisor horizontal entre la fila rowIdx y la fila rowIdx+1 (afecta filas enteras).
  function resizeHorizontal(grid, rowIdx, deltaFrac, minFrac) {
    if (rowIdx < 0 || rowIdx >= grid.rows.length - 1) return grid;
    const a = grid.rows[rowIdx].hFrac, b = grid.rows[rowIdx + 1].hFrac;
    const t = Math.max(minFrac - a, Math.min(b - minFrac, deltaFrac));
    if (t === 0) return grid;
    const g = _clone(grid);
    g.rows[rowIdx].hFrac = a + t;
    g.rows[rowIdx + 1].hFrac = b - t;
    return g;
  }

  // Intercambia las posiciones de idA e idB (cada card adopta el tamaño/celda destino;
  // se intercambian solo los id, los wFrac del slot se conservan).
  function swapCells(grid, idA, idB) {
    idA = String(idA); idB = String(idB);
    if (idA === idB) return grid;
    const A = findCell(grid, idA), B = findCell(grid, idB);
    if (!A || !B) return grid;
    const g = _clone(grid);
    g.rows[A.rowIdx].cells[A.cellIdx].id = idB;
    g.rows[B.rowIdx].cells[B.cellIdx].id = idA;
    return g;
  }

  // ══ Modo Libre (vertical): cards en fracciones del contenedor (0-1) ═════════
  // La fracción es el storage (escala con el panel). Desde 2026-07-02 el modo ya
  // NO es "ventana": achicar/mover libre se eliminó — el tiling siempre cubre
  // todo (expandirF) y la única redimensión es la pared acoplada (deriveWallsF/
  // moverParedF). ajustarF/topeF quedan en _pure como legacy (sin uso en el DOM);
  // findGapF sigue como red de seguridad de _spotLibre.
  function _overlapF(a, b, eps) {
    eps = eps || 0;
    return a.x < b.x + b.w - eps && b.x < a.x + a.w - eps && a.y < b.y + b.h - eps && b.y < a.y + a.h - eps;
  }
  function clampF(it, minW, minH) {
    const w = Math.max(minW, Math.min(1, it.w));
    const h = Math.max(minH, Math.min(1, it.h));
    return { x: Math.max(0, Math.min(1 - w, it.x)), y: Math.max(0, Math.min(1 - h, it.y)), w, h };
  }
  // Valida un `free` PERSISTIDO antes de usarlo (localStorage es input no confiable:
  // versiones viejas, escrituras a medias, otra resolución). Descarta entradas
  // no-numéricas o degeneradas y clampa el resto al contenedor. Jamás revienta.
  function sanearFreeF(free, minW, minH) {
    const out = {};
    if (!free || typeof free !== 'object') return out;
    const num = v => typeof v === 'number' && isFinite(v);
    for (const id of Object.keys(free)) {
      const f = free[id];
      if (!f || typeof f !== 'object') continue;
      if (!num(f.x) || !num(f.y) || !num(f.w) || !num(f.h)) continue;
      if (f.w <= 0 || f.h <= 0) continue;
      out[id] = clampF({ x: f.x, y: f.y, w: f.w, h: f.h }, minW, minH);
    }
    return out;
  }
  // Achica la activa (borde der, luego inf) hasta NO solapar; null si ni al mínimo
  // entra. Las demás NO se tocan.
  function ajustarF(free, id, minW, minH) {
    id = String(id);
    const otros = Object.keys(free).filter(k => k !== id).map(k => free[k]);
    const choca = (r) => otros.some(o => _overlapF(r, o, 0.003));
    let r = { ...free[id] };
    if (!choca(r)) return r;
    let g = 0; while (r.w > minW && choca(r) && g++ < 400) r.w = Math.max(minW, r.w - 0.008);
    if (!choca(r)) return r;
    r = { ...free[id] }; g = 0; while (r.h > minH && choca(r) && g++ < 400) r.h = Math.max(minH, r.h - 0.008);
    return choca(r) ? null : r;
  }
  // Tope de resize: clampa w,h (ancla x,y) para no invadir vecinas. Las demás quietas.
  function topeF(free, id, w, h, minW, minH) {
    id = String(id);
    const it = free[id];
    const otros = Object.keys(free).filter(k => k !== id).map(k => free[k]);
    let r = { x: it.x, y: it.y, w: Math.max(minW, Math.min(1 - it.x, w)), h: Math.max(minH, Math.min(1 - it.y, h)) };
    let g = 0; while (r.w > minW && otros.some(o => _overlapF(r, o, 0.003)) && g++ < 500) r.w = Math.max(minW, r.w - 0.005);
    g = 0;     while (r.h > minH && otros.some(o => _overlapF(r, o, 0.003)) && g++ < 500) r.h = Math.max(minH, r.h - 0.005);
    return { w: r.w, h: r.h };
  }
  // Hueco libre para una card nueva: primera posición (grilla fina de 0.04) donde una
  // card de tamaño def NO solape; si no entra, achica; último recurso, esquina inf-der.
  function findGapF(free, defW, defH, minW, minH) {
    const otros = Object.values(free);
    const libre = (r) => !otros.some(o => _overlapF(r, o, 0.003));
    for (const [w, h] of [[defW, defH], [defW * 0.6, defH * 0.6], [minW, minH]]) {
      for (let y = 0; y + h <= 1.001; y += 0.04)
        for (let x = 0; x + w <= 1.001; x += 0.04) {
          const r = clampF({ x: +x.toFixed(3), y: +y.toFixed(3), w, h }, minW, minH);
          if (libre(r)) return r;
        }
    }
    return clampF({ x: 1 - defW, y: 1 - defH, w: defW, h: defH }, minW, minH);
  }
  // Tile balanceado en fracciones (para "Reordenar" y el arreglo inicial). Suma 1
  // (el gap lo mete el render en px). Reusa balancedGrid (cols=ceil(√N)).
  function tileF(ids) {
    const grid = balancedGrid(ids);
    const f = {}; let y = 0;
    for (const row of grid.rows) {
      let x = 0;
      for (const c of row.cells) { f[c.id] = { x, y, w: c.wFrac, h: row.hFrac }; x += c.wFrac; }
      y += row.hFrac;
    }
    return f;
  }

  // Sistema VERTICAL (pedido del usuario): N columnas verticales lado a lado en
  // MÁXIMO 2 FILAS (pedido 2026-07-04: "dos filas y no tres"). 1 sola = ancho
  // completo, 2 = lado a lado, … hasta perRow en UNA fila; recién ahí se abre la
  // SEGUNDA fila. La de ABAJO se capa en 3 (MAX_ABAJO) y TODO el excedente — el 10º,
  // 11º, 12º — va ARRIBA, en la MISMA fila superior ("sale arriba"): jamás se abre
  // una 3ª fila (antes 10→6+1+3, 11→6+2+3, 12→6+3+3). El ANCHO de columna cede antes
  // que sumar una fila. perRow (6 por defecto; baja en grid angosto vía perRowMax) es
  // el umbral para pasar a 2 filas, NO un techo de columnas de la fila de arriba.
  function tile2col(ids, perRowMax) {
    const n = ids.length, f = {};
    if (!n) return f;
    const MAX_ABAJO = 3;
    const perRow = perRowMax == null ? 6 : Math.max(1, Math.min(6, Math.floor(perRowMax)));
    let counts;
    if (n <= perRow) {
      counts = [n];                                     // entra en 1 fila
    } else {
      const abajo = Math.min(MAX_ABAJO, n - perRow);    // 7→1, 8→2, 9→3, 10..12→3
      counts = [n - abajo, abajo];                      // 2 filas: el excedente SUBE (top-fill)
    }
    const h = 1 / counts.length;
    let idx = 0;
    counts.forEach((cnt, r) => {
      const w = 1 / cnt;
      for (let c = 0; c < cnt; c++) f[String(ids[idx++])] = { x: c * w, y: r * h, w, h };
    });
    return f;
  }

  // Cuenta las FILAS (bandas horizontales) de un layout `free`: agrupa las cards
  // por `y` con tolerancia. Sirve para MIGRAR layouts persistidos viejos que podían
  // tener 3 filas (10→6+1+3, 12→6+3+3) al nuevo tope de 2 (pedido 2026-07-04): si
  // devuelve >2, el caller re-siembra con tile2col. Un layout de 1-2 filas devuelve
  // 1-2 → no se toca (se conservan los anchos que el usuario haya ajustado).
  function contarFilasF(free) {
    const ys = Object.values(free || {}).map(f => f.y).sort((a, b) => a - b);
    let filas = 0, ref = -1;
    for (const y of ys) { if (ref < 0 || y - ref > 0.06) { filas++; ref = y; } }
    return filas;
  }

  // Alta DETERMINISTA para el modo VERTICAL (pedido 2026-07-04): la terminal nueva
  // entra a la fila que le toca SIN reshuffle entre filas y la OTRA fila queda
  // CONGELADA. El usuario: "cuando ya hay tres abajo, la nueva que se cree ARRIBA y
  // las de abajo que queden intactas, sin moverse". Espeja el reparto de tile2col,
  // pero incremental y sin bail a re-tile (el bail era lo que reordenaba: la nueva
  // caía abajo y una de abajo subía). Reglas:
  //   · 1 fila con < perRow cards → crece esa fila (sigue 1 sola).      [ …→[6] ]
  //   · 1 fila llena (perRow)      → se abre la fila de ABAJO con la nueva; las de
  //     arriba conservan su x/w, solo su alto baja a la mitad.            [6→[6,1]]
  //   · 2 filas, ABAJO con < maxAbajo → la nueva entra ABAJO (reparte abajo, ARRIBA
  //     intacta).                                          [ [6,1]→[6,2]→[6,3] ]
  //   · 2 filas, ABAJO llena (≥maxAbajo) → la nueva entra ARRIBA (reparte arriba,
  //     ABAJO CONGELADA — el pedido puntual).          [ [6,3]→[7,3]→…→[9,3] ]
  // Nunca 3 filas; jamás mueve la fila que no recibe a la nueva. Devuelve SIEMPRE un
  // layout válido. La fila que recibe se reparte pareja ("todas la misma medida").
  function agregarVerticalF(free, nuevoId, perRow, maxAbajo) {
    nuevoId = String(nuevoId);
    const ids = Object.keys(free || {});
    if (!ids.length) return { [nuevoId]: { x: 0, y: 0, w: 1, h: 1 } };
    perRow = Math.max(1, Math.min(6, Math.floor(perRow || 6)));
    maxAbajo = maxAbajo || 3;
    const EPSB = 1e-4;
    const out = {};
    for (const id of ids) out[id] = { ...free[id] };
    const yMin = Math.min(...ids.map(id => out[id].y));
    const yMax = Math.max(...ids.map(id => out[id].y));
    const fila = (y) => ids.filter(id => Math.abs(out[id].y - y) < EPSB).sort((a, b) => out[a].x - out[b].x);
    const dosFilas = (yMax - yMin) > EPSB;
    // Reparte `cards` + la nueva (al final) a lo ancho de su rango x, a la altura (y,h) dada.
    const repartir = (cards, y, h) => {
      const x0 = Math.min(...cards.map(id => out[id].x));
      const x1 = Math.max(...cards.map(id => out[id].x + out[id].w));
      const span = (x1 - x0) || 1;
      const orden = cards.concat(nuevoId);
      const w = span / orden.length;
      orden.forEach((id, i) => { out[id] = { x: x0 + i * w, y, w, h }; });
    };
    const arriba = fila(yMin);
    if (!dosFilas) {
      const h = out[arriba[0]].h, y = out[arriba[0]].y;
      if (arriba.length < perRow) {
        repartir(arriba, y, h);                        // crece la única fila
      } else {
        const h2 = h / 2;                              // se abre la fila de ABAJO
        for (const id of arriba) out[id] = { ...out[id], h: h2 };
        out[nuevoId] = { x: 0, y: y + h2, w: 1, h: h2 };
      }
      return out;
    }
    const abajo = fila(yMax);
    if (abajo.length < maxAbajo) repartir(abajo, yMax, out[abajo[0]].h);   // crece ABAJO, arriba intacta
    else                         repartir(arriba, yMin, out[arriba[0]].h); // crece ARRIBA, ABAJO congelada
    return out;
  }

  // ── Alta/baja SIN molestar a las OTRAS bandas (2026-07-02/03) ──
  // Antes cada add/remove re-tileaba TODO (tile2col): TODAS las cards cambiaban
  // de tamaño y TODAS las CLIs recibían un resize (el "se van achicando las
  // otras"). Ahora la terminal nueva entra a UNA banda y solo esa banda se
  // reparte en partes IGUALES (agregarEnBandaF); las demás quedan BYTE-idénticas
  // (ni un pixel, ni un SIGWINCH). Al cerrar, la banda de la quitada se
  // re-reparte en partes IGUALES (quitarDeBandaF, espejo del alta), y lo que
  // quede libre lo reclama expandirF (estira, nunca achica). tile2col queda
  // como fallback y para el botón "Reordenar".

  // Alta por BANDA (pedido 2026-07-03: "todas la misma medida"): la terminal
  // nueva entra a la banda de la card más ancha y la banda ENTERA se reparte en
  // partes IGUALES (partir a la mitad dejaba "una más grande que las otras dos":
  // ½+¼+¼). Solo esa banda se toca/refitea; las demás quedan byte-idénticas.
  // Devuelve { free, banda } (banda = ids que había, para el refit) o null si ni
  // repartiendo se alcanza minW (el caller re-tilea con tile2col).
  function agregarEnBandaF(free, nuevoId, minW, maxAbajo) {
    const ids = Object.keys(free || {});
    if (!ids.length) {
      return { free: { [String(nuevoId)]: { x: 0, y: 0, w: 1, h: 1 } }, banda: [] };
    }
    const EPS = 1e-9, EPSB = 1e-4;
    // Tope ABAJO: si la banda de MÁS ABAJO ya tiene maxAbajo cards, ahí no entra
    // nadie — la nueva va ARRIBA (se excluyen esas candidatas). Con una sola
    // banda (todas y=0) no existe "abajo": sin restricción.
    let candidatas = ids;
    if (maxAbajo != null) {
      const maxY = Math.max(...ids.map(id => free[id].y));
      if (maxY > EPSB) {
        const abajo = ids.filter(id => Math.abs(free[id].y - maxY) < EPSB);
        if (abajo.length >= maxAbajo) candidatas = ids.filter(id => !abajo.includes(id));
      }
    }
    if (!candidatas.length) return null;
    let mejor = null;
    for (const id of candidatas) {
      const f = free[id], m = mejor && free[mejor];
      if (!mejor || f.w > m.w + EPS ||
          (Math.abs(f.w - m.w) <= EPS && (f.y < m.y - EPS ||
           (Math.abs(f.y - m.y) <= EPS && f.x < m.x)))) mejor = id;
    }
    const ref = free[mejor];
    const banda = ids.filter(id => Math.abs(free[id].y - ref.y) < EPSB &&
                                   Math.abs(free[id].h - ref.h) < EPSB)
                     .sort((a, b) => free[a].x - free[b].x);
    const x0 = Math.min(...banda.map(id => free[id].x));
    const x1 = Math.max(...banda.map(id => free[id].x + free[id].w));
    const w = (x1 - x0) / (banda.length + 1);
    if (w < minW) return null;                 // ni repartiendo entra
    const out = { ...free };
    banda.forEach((id, i) => { out[id] = { ...free[id], x: x0 + i * w, w }; });
    out[String(nuevoId)] = { x: x0 + banda.length * w, y: ref.y, w, h: ref.h };
    // Alturas mixtas en la fila: la banda excluye a la card de altura distinta
    // pero el rango x0..x1 puede contenerla → el reparto la pisaría. Si el
    // resultado solapa una ajena → null (el caller re-tilea, que no solapa).
    const miembros = banda.concat(String(nuevoId));
    const ajenas = ids.filter(id => !banda.includes(id));
    if (miembros.some(id => ajenas.some(o => _overlapF(out[id], out[o], 0.003)))) return null;
    return { free: out, banda };
  }

  // Baja por BANDA (pedido 2026-07-03, espejo de agregarEnBandaF): al cerrar
  // una terminal su banda ENTERA se re-reparte en partes IGUALES. Reemplaza a
  // absorberEspacioF (una sola vecina se tragaba todo el espacio y quedaba el
  // doble de ancha que las demás — "no se sincroniza la anchura"). Las otras
  // bandas quedan byte-idénticas. Devuelve { free, banda } (banda = ids que
  // quedan, para el refit) o null si era la única de su banda (el caller deja
  // que expandirF reclame el hueco).
  function quitarDeBandaF(free, idQuitado) {
    idQuitado = String(idQuitado);
    const f = (free || {})[idQuitado];
    if (!f) return null;
    const EPSB = 1e-4;
    const resto = { ...free };
    delete resto[idQuitado];
    const banda = Object.keys(resto).filter(id => Math.abs(resto[id].y - f.y) < EPSB &&
                                                  Math.abs(resto[id].h - f.h) < EPSB)
                        .sort((a, b) => resto[a].x - resto[b].x);
    if (!banda.length) return null;                // era la única de su banda: queda el hueco
    const x0 = Math.min(f.x, ...banda.map(id => resto[id].x));
    const x1 = Math.max(f.x + f.w, ...banda.map(id => resto[id].x + resto[id].w));
    const w = (x1 - x0) / banda.length;
    banda.forEach((id, i) => { resto[id] = { ...resto[id], x: x0 + i * w, w }; });
    // Mismo guard que el alta: el rango de la quitada puede contener una card
    // de altura distinta (fuera de la banda) → si el reparto la pisa → null
    // (queda el hueco y expandirF lo reclama sin solapar jamás).
    const ajenas = Object.keys(resto).filter(id => !banda.includes(id));
    if (banda.some(id => ajenas.some(o => _overlapF(resto[id], resto[o], 0.003)))) return null;
    return { free: resto, banda };
  }

  // Estira las cards hacia el ESPACIO LIBRE (pedido 2026-07-02: en modo vertical
  // las columnas ocupan SIEMPRE toda la altura; al cerrar/cargar no quedan bandas
  // muertas — p.ej. 6 columnas a media altura con la banda de abajo vacía, herencia
  // de un layout de 2 filas). Por eje: primero SNAP al borde del obstáculo previo
  // (izq/arriba, procesando en orden para que los ya corridos sean firmes), después
  // CRECER hasta el próximo obstáculo (der/abajo). Solo crece/corre hacia el hueco —
  // JAMÁS achica a nadie. Pura: no muta la entrada.
  function expandirF(free) {
    const EPS = 1e-4;
    const out = {};
    for (const id of Object.keys(free || {})) out[id] = { ...free[id] };
    const ids = Object.keys(out);
    const cruzaY = (a, b) => a.y < b.y + b.h - EPS && b.y < a.y + a.h - EPS;
    const cruzaX = (a, b) => a.x < b.x + b.w - EPS && b.x < a.x + a.w - EPS;
    const pasada = (cruza, pos, dim) => {
      ids.slice().sort((p, q) => out[p][pos] - out[q][pos]).forEach(id => {
        const f = out[id];
        let borde = 0;
        for (const o of ids) {
          if (o === id) continue;
          const g = out[o];
          if (cruza(f, g) && g[pos] + g[dim] <= f[pos] + EPS) borde = Math.max(borde, g[pos] + g[dim]);
        }
        f[pos] = borde;
      });
      for (const id of ids) {
        const f = out[id];
        let borde = 1;
        for (const o of ids) {
          if (o === id) continue;
          const g = out[o];
          if (cruza(f, g) && g[pos] >= f[pos] + f[dim] - EPS) borde = Math.min(borde, g[pos]);
        }
        f[dim] = borde - f[pos];
      }
    };
    pasada(cruzaY, 'x', 'w');   // ancho: reclama huecos horizontales (columna cerrada)
    pasada(cruzaX, 'y', 'h');   // alto: reclama bandas muertas (columnas a alto completo)
    return out;
  }

  // ── Paredes ACOPLADAS (pedido 2026-07-02) ────────────────────────
  // El resize libre por esquinas se ELIMINÓ: agrandar una terminal se hace
  // arrastrando la PARED que comparte con su vecina — una crece y la otra se
  // achica EXACTAMENTE lo mismo, el patrón nunca se rompe y no quedan huecos.
  // deriveWallsF encuentra las paredes: cada borde compartido entre dos cards
  // es un tramo; los tramos sobre la misma línea se FUSIONAN SOLO si comparten
  // una card (su borde es UNA línea: si una card abarca varias vecinas, mover
  // solo un tramo rompería el tiling). Tramos que apenas se TOCAN en la juntura
  // de dos bandas (sin card compartida) quedan SEPARADOS — pedido 2026-07-03:
  // agrandar una terminal de abajo no mueve las de arriba aunque sus bordes
  // caigan en la misma vertical. moverParedF desplaza la pared con clamp al
  // mínimo por card (una card ya bajo el mínimo no cede más, pero sí crece).
  function deriveWallsF(free) {
    const EPS = 2e-3;
    const ids = Object.keys(free || {});
    const tramos = { v: [], h: [] };
    for (const i of ids) for (const j of ids) {
      if (i === j) continue;
      const a = free[i], b = free[j];
      if (Math.abs(a.x + a.w - b.x) < EPS && a.y < b.y + b.h - EPS && b.y < a.y + a.h - EPS)
        tramos.v.push({ pos: b.x, d0: Math.max(a.y, b.y), d1: Math.min(a.y + a.h, b.y + b.h), antes: i, despues: j });
      if (Math.abs(a.y + a.h - b.y) < EPS && a.x < b.x + b.w - EPS && b.x < a.x + a.w - EPS)
        tramos.h.push({ pos: b.y, d0: Math.max(a.x, b.x), d1: Math.min(a.x + a.w, b.x + b.w), antes: i, despues: j });
    }
    const walls = [];
    for (const type of ['v', 'h']) {
      const resto = tramos[type].sort((p, q) => (p.pos - q.pos) || (p.d0 - q.d0));
      while (resto.length) {
        const t = resto.shift();
        const w = { type, pos: t.pos, d0: t.d0, d1: t.d1, antes: new Set([t.antes]), despues: new Set([t.despues]) };
        for (let creció = true; creció;) {
          creció = false;
          for (let k = 0; k < resto.length; k++) {
            const p = resto[k];
            if (Math.abs(p.pos - w.pos) < EPS && (w.antes.has(p.antes) || w.despues.has(p.despues))) {
              w.d0 = Math.min(w.d0, p.d0); w.d1 = Math.max(w.d1, p.d1);
              w.antes.add(p.antes); w.despues.add(p.despues);
              resto.splice(k--, 1); creció = true;
            }
          }
        }
        walls.push({ type, pos: w.pos, d0: w.d0, d1: w.d1, antes: [...w.antes], despues: [...w.despues] });
      }
    }
    return walls;
  }

  // Poda posiciones de terminales que YA NO existen, SIN tocar las protegidas
  // (guardadas esperando su add() durante la carga de un proyecto). El bug de
  // "cambio de posición, navego a otro proyecto, vuelvo y se perdió" salía de
  // podar el free guardado ANTES de que las terminales se re-agregaran.
  function podarFreeF(free, vivos, protegidos) {
    const viv = new Set((vivos || []).map(String));
    const out = {};
    for (const id of Object.keys(free || {}))
      if (viv.has(id) || (protegidos && protegidos.has(id))) out[id] = free[id];
    return out;
  }

  // Qué borde/esquina de qué card controla qué pared (las agarraderas viven EN
  // la terminal — pedido 2026-07-03: sin barritas en el gutter). Para cada pared,
  // las cards del lado "antes" la agarran por su borde der/aba y las del lado
  // "despues" por izq/arr. Esquinas inferiores (sw/se) donde confluyen una pared
  // v y una h en la misma card (arrastran las dos a la vez, tipo ventana).
  function bordesF(walls) {
    const bordes = [];
    (walls || []).forEach((w, i) => {
      for (const id of w.antes) bordes.push({ id: String(id), lado: w.type === 'v' ? 'der' : 'aba', wall: i });
      for (const id of w.despues) bordes.push({ id: String(id), lado: w.type === 'v' ? 'izq' : 'arr', wall: i });
    });
    const por = {};
    for (const b of bordes) (por[b.id] = por[b.id] || {})[b.lado] = b.wall;
    const esquinas = [];
    for (const id of Object.keys(por)) {
      const p = por[id];
      if (p.izq != null && p.aba != null) esquinas.push({ id, esquina: 'sw', wallV: p.izq, wallH: p.aba });
      if (p.der != null && p.aba != null) esquinas.push({ id, esquina: 'se', wallV: p.der, wallH: p.aba });
    }
    return { bordes, esquinas };
  }

  function moverParedF(free, wall, delta, minW, minH) {
    const pos = wall.type === 'v' ? 'x' : 'y';
    const dim = wall.type === 'v' ? 'w' : 'h';
    const min = wall.type === 'v' ? minW : minH;
    const margen = id => Math.max(0, free[id][dim] - Math.min(min, free[id][dim]));
    const t = Math.max(-Math.min(...wall.antes.map(margen)),
                       Math.min(Math.min(...wall.despues.map(margen)), delta));
    if (!t) return free;
    const out = {};
    for (const id of Object.keys(free)) out[id] = { ...free[id] };
    for (const id of wall.antes) out[id][dim] += t;
    for (const id of wall.despues) { out[id][pos] += t; out[id][dim] -= t; }
    return out;
  }

  // ── Selector de DISPOSICIÓN ("snap", estilo Windows-11 / FancyZones) ──────────
  // Al arrastrar una card aparece un panel arriba con presets de arreglo para el
  // CONTEO actual N: N=4 → presets de 4, N=6 → presets de 6. Cada preset trae sus
  // `cells` en FRACCIONES (0-1), en ORDEN de lectura (izq→der, arriba→abajo) y con
  // cells.length === N. Al soltar en una zona, la arrastrada ocupa esa celda y el
  // resto rellena las demás en su orden actual (applyPreset). Todo esto es PURO y
  // vive en `_free` (modo Vertical/libre) — coexiste con el drag=intercambio: el
  // intercambio actúa al soltar sobre OTRA card; el snap, al soltar sobre el panel.
  function _cols(n) {                          // n columnas, alto completo
    const out = [];
    for (let i = 0; i < n; i++) out.push({ x: i / n, y: 0, w: 1 / n, h: 1 });
    return out;
  }
  function _rowsOf(counts) {                    // filas de igual alto; counts[r] columnas
    const R = counts.length, out = [];
    for (let r = 0; r < R; r++) {
      const c = counts[r];
      for (let i = 0; i < c; i++) out.push({ x: i / c, y: r / R, w: 1 / c, h: 1 / R });
    }
    return out;
  }
  function _bigLeft(n) {                        // 1 principal (mitad izq) + (n-1) apiladas a la der
    const out = [{ x: 0, y: 0, w: 0.5, h: 1 }];
    const m = n - 1;
    for (let i = 0; i < m; i++) out.push({ x: 0.5, y: i / m, w: 0.5, h: 1 / m });
    return out;
  }
  // Catálogo curado por N (1..12). Cada entrada: { key, label(ES), cells }.
  function snapPresets(n) {
    n = Math.max(1, Math.min(12, n | 0));
    const P = (key, label, cells) => ({ key, label, cells });
    switch (n) {
      case 1:  return [P('full', 'Pantalla completa', _cols(1))];
      case 2:  return [P('cols', 'Lado a lado', _cols(2)),
                       P('rows', 'Arriba y abajo', _rowsOf([1, 1]))];
      case 3:  return [P('cols', '3 columnas', _cols(3)),
                       P('main', 'Principal + 2', _bigLeft(3)),
                       P('2-1', '2 arriba · 1 abajo', _rowsOf([2, 1])),
                       P('1-2', '1 arriba · 2 abajo', _rowsOf([1, 2]))];
      case 4:  return [P('grid', 'Cuadrícula 2×2', _rowsOf([2, 2])),
                       P('cols', '4 columnas', _cols(4)),
                       P('main', 'Principal + 3', _bigLeft(4)),
                       P('3-1', '3 arriba · 1 abajo', _rowsOf([3, 1]))];
      case 5:  return [P('3-2', '3 · 2', _rowsOf([3, 2])),
                       P('2-3', '2 · 3', _rowsOf([2, 3])),
                       P('main', 'Principal + 4', _bigLeft(5)),
                       P('cols', '5 columnas', _cols(5))];
      // De N≥6 en adelante NO hay "Principal + N" (_bigLeft): la terminal grande
      // ocupa media pantalla y las 5+ restantes quedan apiladas tan finitas que "ya
      // no se ven" (pedido del usuario). El main vive solo de +2 a +4 (N=3,4,5).
      // Los conteos altos ofrecen ≥3 opciones REALMENTE distintas (espejos
      // bottom-heavy incluidos) — pedido 2026-07-08: "que haya otros, pero no tan
      // iguales al mismo" (con 2 opciones, elegida una quedaba UNA alternativa).
      case 6:  return [P('grid', 'Cuadrícula 3×2', _rowsOf([3, 3])),
                       P('4-2', '4 · 2', _rowsOf([4, 2])),
                       P('2-4', '2 · 4', _rowsOf([2, 4])),
                       P('cols', '6 columnas', _cols(6))];
      case 7:  return [P('4-3', '4 · 3', _rowsOf([4, 3])),
                       P('3-4', '3 · 4', _rowsOf([3, 4])),
                       P('5-2', '5 · 2', _rowsOf([5, 2])),
                       P('3-2-2', '3 · 2 · 2', _rowsOf([3, 2, 2]))];
      case 8:  return [P('grid', 'Cuadrícula 4×2', _rowsOf([4, 4])),
                       P('5-3', '5 · 3', _rowsOf([5, 3])),
                       P('3-3-2', '3 · 3 · 2', _rowsOf([3, 3, 2])),
                       P('2-3-3', '2 · 3 · 3', _rowsOf([2, 3, 3]))];
      case 9:  return [P('grid', 'Cuadrícula 3×3', _rowsOf([3, 3, 3])),
                       P('5-4', '5 · 4', _rowsOf([5, 4])),
                       P('4-5', '4 · 5', _rowsOf([4, 5])),
                       P('6-3', '6 · 3', _rowsOf([6, 3]))];
      case 10: return [P('grid', 'Cuadrícula 5×2', _rowsOf([5, 5])),
                       P('6-4', '6 · 4', _rowsOf([6, 4])),
                       P('4-6', '4 · 6', _rowsOf([4, 6])),
                       P('4-3-3', '4 · 3 · 3', _rowsOf([4, 3, 3]))];
      case 11: return [P('6-5', '6 · 5', _rowsOf([6, 5])),
                       P('5-6', '5 · 6', _rowsOf([5, 6])),
                       P('4-4-3', '4 · 4 · 3', _rowsOf([4, 4, 3])),
                       P('3-4-4', '3 · 4 · 4', _rowsOf([3, 4, 4]))];
      default: return [P('grid', 'Cuadrícula 6×2', _rowsOf([6, 6])),
                       P('4x3', 'Cuadrícula 4×3', _rowsOf([4, 4, 4])),
                       P('6-3-3', '6 · 3 · 3', _rowsOf([6, 3, 3]))];
    }
  }
  // Coloca la arrastrada en la zona elegida; el resto (orden actual, sin la
  // arrastrada) rellena las celdas libres en orden. Devuelve un `_free` nuevo.
  function applyPreset(cells, orderedIds, draggedId, zoneIndex) {
    const dragged = String(draggedId);
    const n = cells.length;
    let zi = zoneIndex | 0;
    if (zi < 0 || zi >= n) zi = 0;
    const rest = orderedIds.map(String).filter(id => id !== dragged);
    const free = {};
    let ri = 0;
    for (let i = 0; i < n; i++) {
      const id = (i === zi) ? dragged : rest[ri++];
      if (id == null) continue;              // menos ids que celdas: no forzar
      const c = cells[i];
      free[id] = { x: c.x, y: c.y, w: c.w, h: c.h };
    }
    return free;
  }
  // Índice de la celda que contiene (fx,fy) en [0,1]; fuera de rango → la más
  // cercana por centro (robustez ante hit-tests en el borde).
  function hitZone(cells, fx, fy) {
    for (let i = 0; i < cells.length; i++) {
      const c = cells[i];
      if (fx >= c.x && fx < c.x + c.w && fy >= c.y && fy < c.y + c.h) return i;
    }
    let best = -1, bd = Infinity;
    for (let i = 0; i < cells.length; i++) {
      const c = cells[i], cx = c.x + c.w / 2, cy = c.y + c.h / 2;
      const d = (fx - cx) * (fx - cx) + (fy - cy) * (fy - cy);
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  }

  // ── MEMORIA DE DISTRIBUCIÓN (pedido del usuario 2026-07-05) ──────────────────
  // El usuario elige una disposición del panel (columnas, cuadrícula, principal+N,
  // filas…) y esa FAMILIA se recuerda: al agregar/quitar terminales, el arreglo se
  // re-acomoda SEGÚN esa distribución para el nuevo N (no vuelve al default). El
  // descriptor generaliza a cualquier N: `{ k:'rows', r, heavy? }` (r filas
  // balanceadas; r=1 = columnas; heavy:'bottom' = el excedente va ABAJO) o
  // `{ k:'main' }` (principal + resto). `null` = 'auto' (la lógica vertical de
  // columnas/2-filas de siempre — el default de proyecto nuevo).
  //
  // Reparte n celdas en R filas balanceadas. Por defecto TOP-HEAVY (el excedente va
  // a las filas de ARRIBA: 3-2, 4-3, 4-2, 3-2-2…); heavy:'bottom' lo manda ABAJO
  // (2-3, 3-4, 2-3-3…). Sin la orientación, elegir "2·3" se generalizaba a [3,2] al
  // agregar/quitar — "no te podés salir del mismo diseño" (fix 2026-07-08).
  // Devuelve cells en orden de lectura (fila por fila, izq→der). r=1 ⇒ n columnas.
  function balancedRowsCells(n, R, heavy) {
    n = Math.max(1, n | 0);
    R = Math.max(1, Math.min(R | 0, n));       // no más filas que terminales
    const base = Math.floor(n / R), extra = n % R;
    const counts = [];
    for (let r = 0; r < R; r++)
      counts.push(base + ((heavy === 'bottom' ? r >= R - extra : r < extra) ? 1 : 0));
    return _rowsOf(counts);
  }
  // Nº de filas distintas de un arreglo de celdas (por su `y`, redondeado — evita
  // que el error de float parta una fila en dos).
  function distinctYCount(cells) {
    const ys = new Set();
    for (const c of cells) ys.add(Math.round(c.y * 1000));
    return Math.max(1, ys.size);
  }
  // Columnas por fila de un arreglo de celdas en orden de lectura: [4,3] = 4 arriba
  // y 3 abajo. Base de la orientación (heavy) y de los tests del catálogo.
  function rowCountsOf(cells) {
    const counts = [];
    let refY = null;
    for (const c of cells || []) {
      const y = Math.round(c.y * 1000);
      if (refY === null || y !== refY) { counts.push(0); refY = y; }
      counts[counts.length - 1]++;
    }
    return counts.length ? counts : [0];
  }
  // Descriptor de familia a partir del preset elegido. Todo el catálogo mapea:
  // main → principal+resto; cols/full → 1 fila; el resto → su nº de filas + la
  // ORIENTACIÓN del peso (bottom si la última fila es más densa que la primera).
  // El top queda implícito (sin campo) para que los descriptores v4 ya persistidos
  // sigan siendo válidos e iguales.
  function distFromPreset(key, cells) {
    if (key === 'main') return { k: 'main' };
    if (key === 'cols' || key === 'full') return { k: 'rows', r: 1 };
    const counts = rowCountsOf(cells || []);
    const d = { k: 'rows', r: Math.max(1, counts.length) };
    if (counts.length > 1 && counts[0] < counts[counts.length - 1]) d.heavy = 'bottom';
    return d;
  }
  // Celdas de una distribución a un conteo N. `main` degrada a 2 filas balanceadas
  // fuera de su rango legible (2..5) — misma regla que el catálogo, que saca "main"
  // con N≥6. `null` (auto) devuelve null → el caller usa la lógica vertical.
  function distCells(dist, n) {
    n = Math.max(1, n | 0);
    if (!dist) return null;
    if (dist.k === 'main') return (n >= 2 && n <= 5) ? _bigLeft(n) : balancedRowsCells(n, 2);
    return balancedRowsCells(n, dist.r || 1, dist.heavy);
  }
  // ids (en orden de lectura) → celdas EXACTAS, una por id. Es lo que hace que
  // "lo que el tile muestra sea lo que queda": el click del panel aplica las
  // celdas del preset tal cual, sin re-balancear (fix 2026-07-08).
  function cellsToFree(cells, orderedIds) {
    const list = (orderedIds || []).map(String);
    const free = {};
    for (let i = 0; i < (cells || []).length && i < list.length; i++) {
      const c = cells[i];
      free[list[i]] = { x: c.x, y: c.y, w: c.w, h: c.h };
    }
    return free;
  }
  // ¿El layout real (`free`) ES este preset? Compara rect a rect en orden de
  // lectura con tolerancia. Para marcar `.sel` en el panel: el activo es el que
  // MATCHEA lo que se ve — nunca dos a la vez, y ninguno si el usuario movió
  // paredes a mano (layout custom: honesto).
  function presetMatchesFree(cells, free, eps) {
    eps = eps || 0.01;
    const ids = Object.keys(free || {});
    if (!cells || ids.length !== cells.length || !ids.length) return false;
    const orden = ids.sort((a, b) =>
      (Math.round(free[a].y * 1000) - Math.round(free[b].y * 1000)) || (free[a].x - free[b].x));
    for (let i = 0; i < orden.length; i++) {
      const f = free[orden[i]], c = cells[i];
      if (Math.abs(f.x - c.x) > eps || Math.abs(f.y - c.y) > eps ||
          Math.abs(f.w - c.w) > eps || Math.abs(f.h - c.h) > eps) return false;
    }
    return true;
  }
  // Filtra el preset en el que el layout YA está (pedido 2026-07-08: "que
  // detecte en qué tipo de layout estás y no salga el mismo"): ofrecer lo que
  // ya tenés es ruido — el panel muestra solo alternativas REALES. Un layout
  // custom (paredes movidas) no matchea ninguno → se ofrecen todos, incluido
  // el "limpio" del que partiste (sirve para volver a él).
  function sinPresetActual(presets, free) {
    return (presets || []).filter(p => !presetMatchesFree(p.cells, free));
  }
  // Aplica una distribución a un conjunto de ids (orden de lectura → celdas en
  // orden). `_free` nuevo, o null si dist=auto (que decida el caller).
  function applyDistribution(dist, orderedIds, n) {
    const cells = distCells(dist, n != null ? n : orderedIds.length);
    if (!cells) return null;
    return cellsToFree(cells, orderedIds);
  }

  const pure = {
    overlaps, balancedGrid, gridIds, findCell, deriveRects, deriveDividers,
    resizeVertical, resizeHorizontal, swapCells,
    snapPresets, applyPreset, hitZone,
    balancedRowsCells, distinctYCount, rowCountsOf, distFromPreset, distCells,
    cellsToFree, presetMatchesFree, sinPresetActual, applyDistribution,
    clampF, sanearFreeF, ajustarF, topeF, findGapF, tileF, tile2col, contarFilasF, agregarVerticalF, _overlapF,
    agregarEnBandaF, quitarDeBandaF, expandirF, deriveWallsF, moverParedF, bordesF, podarFreeF,
    MIN_W, MIN_H, GAP,
  };

  // ── Motor DOM (solo navegador) ───────────────────────────────────
  if (typeof document !== 'undefined') {
    let _gridEl = null;          // contenedor .terminals-grid (positioned)
    let _layout = { rows: [] };  // estructura VIVA del mosaico
    let _projectId = null;
    let _refitCb = null;
    let _maximizedId = null;
    let _interacting = false;
    let _gridObs = null;         // ResizeObserver del contenedor (re-render al medir/resize)
    // ── Disposición: UN solo motor (Vertical/libre). El viejo modo "Mosaico"
    // (cuadrícula auto-acomodada) se RETIRÓ (pedido del usuario 2026-07-05): sus
    // cuadrículas ya las da el panel de disposición como presets, así que el toggle
    // era redundante. `_mode` queda clavado en 'libre' (las ramas `!== 'libre'` son
    // código muerto guardado — no se ejecutan; ver [[terminales-snap-disposicion]]).
    // `_free` en FRACCIONES (0-1). `_dist` = distribución elegida (memoria): al
    // agregar/quitar terminales, el arreglo se re-acomoda según ella (null = auto).
    let _mode = 'libre';
    let _free = {};
    let _dist = null;                     // { k:'rows', r } | { k:'main' } | null (auto)
    const DEF_W = 0.49, DEF_H = 0.49;     // tamaño de una card nueva (libre)
    // Tamaño mínimo REAL de card (px). Política nativa (2026-07-02): la fuente de la
    // terminal es FIJA — una card angosta tiene menos columnas, jamás letra más chica.
    // Este piso garantiza que "menos columnas" siga siendo usable (~34 cols × ~6 filas
    // a fuente 13). Criterio: columnas parejas y legibles, nunca miniaturas.
    const MIN_PX_W = 280, MIN_PX_H = 160;
    // Ancho mínimo de COLUMNA para el tiling vertical: por debajo, la fila envuelve
    // (la siguiente terminal va ABAJO en vez de angostar a todas).
    const MIN_COL_PX = 260;
    // Tope de la banda de ABAJO (pedido 2026-07-03): máximo 3 terminales abajo;
    // el excedente va ARRIBA (agregarEnBandaF lo excluye, tile2col lo capa).
    const MAX_ABAJO = 3;
    // Posiciones GUARDADAS esperando su add() (carga/cambio de proyecto): la poda
    // de _renderLibre NO las toca — sin esto, el primer render tras init() (layout
    // aún vacío, grid ya medido) borraba el arreglo guardado y el usuario perdía
    // sus posiciones cada vez que navegaba a otro proyecto y volvía.
    let _restaurando = new Set();
    let _settleTimer = 0;
    function _armarSettle() {
      clearTimeout(_settleTimer);
      _settleTimer = setTimeout(_asentar, 1500);
    }
    // Al asentarse la carga (1.5s sin add nuevos): las guardadas que nunca llegaron
    // son de terminales muertas → podarlas y que las vivas reclamen su espacio.
    function _asentar() {
      if (_interacting) { _armarSettle(); return; }
      _restaurando.clear();
      const antes = Object.keys(_free).length;
      _free = podarFreeF(_free, gridIds(_layout), null);
      if (Object.keys(_free).length === antes) return;   // nada muerto → no tocar nada
      if (_mode === 'libre') _free = expandirF(_free);
      render(); _refit(null, true); _persistLibre();
    }
    function _perRowMax() {
      const W = _gridW();
      if (!W) return 6;                   // grid sin medir (init): techo histórico
      return Math.max(1, Math.min(6, Math.floor(W / MIN_COL_PX)));
    }

    function _persistKey() { return `jarvis.terminals.layout.${_projectId}`; }
    function _persistLibre() {
      try { localStorage.setItem(_persistKey(), JSON.stringify({ v: 4, dist: _dist, free: _free })); } catch (_) {}
    }
    function _cargarLibre() {
      try {
        const o = JSON.parse(localStorage.getItem(_persistKey()) || 'null');
        if (o && o.v === 4) return o;                 // { dist, free } — formato actual
        if (o && o.v === 3) return { dist: null, free: o.free };   // v3 (mode+free) → migra a auto
      } catch (_) {}
      return null;
    }
    // localStorage es input NO confiable: validar el descriptor de distribución.
    function _distSano(d) {
      if (!d || typeof d !== 'object') return null;
      if (d.k === 'main') return { k: 'main' };
      if (d.k === 'rows') {
        const r = d.r | 0;
        if (r >= 1 && r <= 12) {
          const out = { k: 'rows', r };
          if (d.heavy === 'bottom') out.heavy = 'bottom';   // orientación (fix 2026-07-08)
          return out;
        }
      }
      return null;
    }
    // Aplica las CELDAS EXACTAS del preset elegido y recuerda su familia. Es el
    // camino del CLICK en el panel: lo que el tile muestra es lo que queda.
    // (Antes el click colapsaba a la familia balanceada top-heavy: elegir "2·3"
    // aplicaba [3,2] — el mismo diseño del que querías salir. Fix 2026-07-08.)
    function _aplicarPreset(p) {
      const nuevo = cellsToFree(p.cells, _ordenLibre());
      if (Object.keys(nuevo).length) _free = nuevo;
      _dist = distFromPreset(p.key, p.cells);
      _syncModeBtn();
      render(); _refit(null, true); _persistLibre();
    }
    // Aplica una distribución al conjunto ACTUAL de terminales y la RECUERDA (memoria):
    // desde ahí, agregar/quitar la re-aplica para el nuevo N. `null` = volver a 'auto'.
    function _aplicarDist(dist) {
      _dist = _distSano(dist);
      const ids = _ordenLibre();
      if (_dist && ids.length) {
        const nuevo = applyDistribution(_dist, ids, ids.length);
        if (nuevo) _free = nuevo;
      } else if (!_dist) {
        // Auto: re-tilear al canónico vertical (columnas / 2 filas).
        _free = tile2col(gridIds(_layout), _perRowMax());
      }
      _syncModeBtn();
      render(); _refit(null, true); _persistLibre();
    }
    // Mínimos en FRACCIÓN del contenedor actual (clamp para grids chicos).
    function _minF() {
      const W = _gridW() || 1, H = _gridH() || 1;
      return { minW: Math.min(0.9, MIN_PX_W / W), minH: Math.min(0.9, MIN_PX_H / H) };
    }
    // FRACCIONES → rects px (con gutter GAP). Es lo que posiciona las cards.
    function _freeRectsPx(W, H) {
      const out = {};
      const g = GAP / 2;
      for (const id in _free) {
        const f = _free[id];
        out[id] = { x: Math.round(f.x * W + g), y: Math.round(f.y * H + g),
                    w: Math.round(f.w * W - GAP), h: Math.round(f.h * H - GAP) };
      }
      return out;
    }

    const _cardEl = (id) => document.getElementById(`terminal-card-${id}`);
    // Marca cards angostas para que el CSS compacte su chrome (cerrar/maximizar
    // SIEMPRE visibles; ver base.css .t-narrow/.t-xnarrow).
    function _narrow(el, w) {
      el.classList.toggle('t-narrow', w < 250);
      el.classList.toggle('t-xnarrow', w < 150);
    }
    const _gridW = () => (_gridEl ? _gridEl.clientWidth : 0);
    const _gridH = () => (_gridEl ? _gridEl.clientHeight : 0);
    // El grid arranca con .oculto (display:none → 0×0). Sin medida no se puede
    // derivar el mosaico; el observer re-llama a render() al volverse medible.
    const _ready = () => _gridW() >= MIN_W && _gridH() >= MIN_H;

    function _ensureGridObserver() {
      if (_gridObs || !_gridEl || typeof ResizeObserver === 'undefined') return;
      // SIEMPRE reposicionar al cambiar el tamaño del contenedor (ventana, dock,
      // franja). Antes esto estaba gateado por `!_interacting`: si el flag quedaba
      // clavado en true (p.ej. un pointercancel sin handler), el observer dejaba de
      // reposicionar → las cards quedaban con px viejos = ASIMÉTRICAS / "movidas",
      // y solo se acomodaban al maximizar/restaurar (que llama render() directo).
      // render() solo reescribe left/top/width/height (barato) y saltea la card que
      // se arrastra (usa transform); el refit caro a tmux sigue gateado por su lado
      // (per-terminal ResizeObserver + _refit chequean isInteracting). Sin loop: mover
      // cards no cambia el tamaño del grid. Ver [[bug-terminales-letras-rotas]] tema E.
      _gridObs = new ResizeObserver(() => render());
      _gridObs.observe(_gridEl);
    }

    // Posiciona cada card en absoluto desde el mosaico derivado (px). Maximizado =
    // una card llena todo y el resto oculto.
    function render() {
      if (!_gridEl) return;
      _ensureGridObserver();
      if (!_ready()) { return; }
      const W = _gridW(), H = _gridH();

      if (_maximizedId !== null) {
        _renderDividers();  // sin divisores en maximizado
        for (const id of gridIds(_layout)) {
          const el = _cardEl(id);
          if (!el) continue;
          const esMax = (String(id) === String(_maximizedId));
          el.style.display = esMax ? '' : 'none';
          if (esMax) {
            el.style.position = 'absolute';
            el.style.left = '0px'; el.style.top = '0px';
            el.style.width = '100%'; el.style.height = '100%';
          }
        }
        return;
      }

      if (_mode === 'libre') { _renderLibre(W, H); return; }

      _renderDividers();
      const rects = deriveRects(_layout, W, H, GAP);
      for (const id of gridIds(_layout)) {
        const el = _cardEl(id);
        if (!el) continue;
        el.style.display = '';
        const r = rects[id];
        if (!r) continue;
        // La card que se arrastra NO se reposiciona: la mueve el transform.
        if (_drag && String(_drag.id) === String(id)) continue;
        el.style.position = 'absolute';
        el.style.left   = r.x + 'px';
        el.style.top    = r.y + 'px';
        el.style.width  = r.w + 'px';
        el.style.height = r.h + 'px';
        _narrow(el, r.w);
      }
    }

    // Spot para una card nueva: el HUECO LIBRE (no pisa a nadie; achica si hace falta).
    function _spotLibre() {
      const { minW, minH } = _minF();
      return findGapF(_free, DEF_W, DEF_H, minW, minH);
    }

    function _renderLibre(W, H) {
      _renderDividers();   // en libre los dividers son las paredes acopladas
      const ids = gridIds(_layout);
      for (const id of ids) if (!_free[id]) _free[id] = _spotLibre();
      // Poda PROTEGIDA: las posiciones guardadas que esperan su add() no se tocan.
      _free = podarFreeF(_free, ids, _restaurando);
      const rects = _freeRectsPx(W, H);
      for (const id of ids) {
        const el = _cardEl(id);
        if (!el) continue;
        el.style.display = '';
        const r = rects[id];
        if (!r) continue;
        if (_drag && String(_drag.id) === String(id)) continue;          // la arrastrada va por transform
        el.style.position = 'absolute';
        el.style.left = r.x + 'px'; el.style.top = r.y + 'px';
        el.style.width = r.w + 'px'; el.style.height = r.h + 'px';
        _narrow(el, r.w);
      }
    }

    // El resize por ESQUINAS (ventana libre) se ELIMINÓ (pedido 2026-07-02):
    // achicar/agrandar una card suelta rompía el patrón y dejaba huecos. La única
    // redimensión ahora es la PARED acoplada de los gutters (ver _renderDividers).

    function _persist() { /* manual = temporal: no se persiste el mosaico */ }

    // Refit (xterm). force (auto-tile/maximizar): DOS pasadas (rAF + 240ms) para
    // medir el tamaño FINAL aunque el layout/animación se asienten con delay, y
    // fitea aunque la celda sea chica. Sin force (resize/swap manual): una pasada
    // a 60ms, respetando el gate "modo chico" (no reformatear output al achicar).
    function _refit(ids, force) {
      // Interacción activa (drag de divisor del grid o del splitter del dock):
      // NO refitear. Cada fit manda un resize a tmux y el TUI redibuja entero;
      // refitear por frame durante un drag = tormenta de redraws que llena el
      // scrollback de basura y lo mutila al re-wrapearlo (ver
      // [[tmux-size-clamping]]). El refit único sale al soltar (pointerup).
      if (!_refitCb) return;
      // force (maximizar/restaurar/auto-tile) NO se traga por interacción: es
      // una acción discreta, no un refit por-frame de un drag. Tragarla dejaba
      // xterm+tmux clavados en medidas de fullscreen si restaurabas (Esc)
      // durante un drag del splitter, sin cura hasta el próximo pointerup
      // (deep work 2026-07-08, hueco #3). El no-force sigue gateado igual.
      if (_interacting && !force) return;
      const list = ids || gridIds(_layout);
      const run = (f) => list.forEach(id => { try { _refitCb(Number(id), f); } catch (_) {} });
      if (force) {
        requestAnimationFrame(() => run(true));
        setTimeout(() => run(true), 240);
        return;
      }
      setTimeout(() => run(false), 60);
    }

    // ── Agarraderas de resize ─
    // MOSAICO: divisores en los gutters (deriveDividers, como siempre).
    // LIBRE (vertical, pedido 2026-07-03): las barritas del gutter SE FUERON —
    // el agarre vive EN la terminal: bordes izq/der/abajo + esquinas sw/se
    // (arriba no: ahí vive el chrome con sus botones; esa pared se agarra desde
    // el borde de abajo de la card superior). Arrastrar mueve la PARED acoplada
    // (una card crece, la vecina cede lo mismo); la esquina mueve las DOS
    // paredes a la vez, tipo ventana. Pool de nodos reusados por índice: NO se
    // recrean en cada render porque durante el drag el pointer capture vive en
    // el nodo y recrearlo cortaría el arrastre.
    const EDGE_PX = 8, ESQ_PX = 16, CHROME_PX = 38;
    let _divEls = [];
    let _walls = [];
    function _renderDividers() {
      if (!_gridEl) return;
      let divs = [];
      _walls = [];
      if (_maximizedId === null && _mode === 'libre') {
        const W = _gridW(), H = _gridH();
        _walls = deriveWallsF(_free);
        const rects = _freeRectsPx(W, H);
        const { bordes, esquinas } = bordesF(_walls);
        for (const b of bordes) {
          const r = rects[b.id];
          if (!r || b.lado === 'arr') continue;
          if (b.lado === 'aba') {
            divs.push({ cls: 't-edge t-edge-h', wallIdx: b.wall,
                        x: r.x, y: r.y + r.h - EDGE_PX, w: r.w, h: EDGE_PX });
          } else {
            divs.push({ cls: 't-edge t-edge-v', wallIdx: b.wall,
                        x: b.lado === 'izq' ? r.x : r.x + r.w - EDGE_PX,
                        y: r.y + CHROME_PX, w: EDGE_PX, h: Math.max(0, r.h - CHROME_PX) });
          }
        }
        // Esquinas al FINAL: pintan encima de los bordes → ganan el pointer.
        for (const q of esquinas) {
          const r = rects[q.id];
          if (!r) continue;
          divs.push({ cls: 't-edge ' + (q.esquina === 'se' ? 't-edge-nwse' : 't-edge-nesw'),
                      wallIdx: q.wallV, wallIdx2: q.wallH,
                      x: q.esquina === 'sw' ? r.x : r.x + r.w - ESQ_PX,
                      y: r.y + r.h - ESQ_PX, w: ESQ_PX, h: ESQ_PX });
        }
      } else if (_maximizedId === null) {
        divs = deriveDividers(_layout, _gridW(), _gridH(), GAP)
          .map(d => ({ ...d, cls: `t-divider t-divider-${d.type}` }));
      }
      while (_divEls.length < divs.length) {
        const el = document.createElement('div');
        el.addEventListener('pointerdown', _onDividerDown);
        _gridEl.appendChild(el);
        _divEls.push(el);
      }
      while (_divEls.length > divs.length) _divEls.pop().remove();
      divs.forEach((d, i) => {
        const el = _divEls[i];
        el.className = d.cls;
        el.dataset.type = (d.type == null) ? '' : d.type;
        el.dataset.rowIdx = (d.rowIdx == null) ? '' : d.rowIdx;
        el.dataset.cellIdx = (d.cellIdx == null) ? '' : d.cellIdx;
        el.dataset.wallIdx = (d.wallIdx == null) ? '' : d.wallIdx;
        el.dataset.wallIdx2 = (d.wallIdx2 == null) ? '' : d.wallIdx2;
        el.style.left   = d.x + 'px';
        el.style.top    = d.y + 'px';
        el.style.width  = d.w + 'px';
        el.style.height = d.h + 'px';
      });
    }

    let _rz = null;
    function _onDividerDown(e) {
      if (_maximizedId !== null) return;
      const el = e.currentTarget;
      e.preventDefault(); e.stopPropagation();
      _interacting = true;
      if (typeof document !== 'undefined') document.body.classList.add('tl-resizing');
      el.setPointerCapture?.(e.pointerId);
      // LIBRE: pared(es) acoplada(s) — un borde trae una, una esquina trae las dos.
      const w1 = el.dataset.wallIdx === '' ? null : _walls[Number(el.dataset.wallIdx)];
      const w2 = el.dataset.wallIdx2 === '' ? null : _walls[Number(el.dataset.wallIdx2)];
      // IDs afectadas por ESTE drag — para la adaptación EN VIVO del contenido.
      let liveIds = [];
      if (w1 || w2) {
        liveIds = [...new Set([w1, w2].filter(Boolean).flatMap(w => [...w.antes, ...w.despues]))];
      } else if (el.dataset.type === 'v') {
        const row = _layout.rows[Number(el.dataset.rowIdx)];
        const ci = Number(el.dataset.cellIdx);
        liveIds = row ? [row.cells[ci], row.cells[ci + 1]].filter(Boolean).map(c => c.id) : [];
      } else if (el.dataset.type === 'h') {
        const ri = Number(el.dataset.rowIdx);
        liveIds = [...(_layout.rows[ri]?.cells || []), ...(_layout.rows[ri + 1]?.cells || [])].map(c => c.id);
      }
      _rz = {
        type: el.dataset.type,
        rowIdx: el.dataset.rowIdx === '' ? null : Number(el.dataset.rowIdx),
        cellIdx: el.dataset.cellIdx === '' ? null : Number(el.dataset.cellIdx),
        wallV: (w1 && w1.type === 'v') ? w1 : ((w2 && w2.type === 'v') ? w2 : null),
        wallH: (w1 && w1.type === 'h') ? w1 : ((w2 && w2.type === 'h') ? w2 : null),
        liveIds, liveT: 0,
        baseFree: { ..._free },   // snapshot al agarrar: el drag es delta sobre la base
        startX: e.clientX, startY: e.clientY, base: _clone(_layout), raf: 0,
        W: _gridW(), H: _gridH(),   // tamaño del grid CAPTURADO al agarrar: no cambia durante el arrastre del divisor
      };
      _gridEl.addEventListener('pointermove', _onResizeMove);
      _gridEl.addEventListener('pointerup', _onResizeUp, { once: true });
    }
    function _onResizeMove(e) {
      if (!_rz) return;
      const W = _rz.W, H = _rz.H;   // capturados en _onDividerDown → cero layout reads por pointermove
      // Deltas del cursor traducidos a píxeles CSS (W/H se midieron con clientWidth).
      const z = _zoomApp();
      const dxCss = (e.clientX - _rz.startX) / z, dyCss = (e.clientY - _rz.startY) / z;
      if (_rz.wallV || _rz.wallH) {
        // Pared(es) acoplada(s) (libre): un lado crece, el otro cede lo mismo.
        // La esquina arrastra ambas a la vez (v con dx, h con dy). Mínimo
        // MIN_W/MIN_H px (como los divisores del mosaico) para que con columnas
        // ya angostas igual se pueda agrandar la elegida.
        let f = _rz.baseFree;
        if (_rz.wallV) f = moverParedF(f, _rz.wallV, dxCss / W, MIN_W / W, MIN_H / H);
        if (_rz.wallH) f = moverParedF(f, _rz.wallH, dyCss / H, MIN_W / W, MIN_H / H);
        _free = f;
      } else if (_rz.type === 'v') {
        // Divisor vertical: reparte ancho entre la celda cellIdx y su vecina.
        const row = _rz.base.rows[_rz.rowIdx];
        const innerW = W - (row.cells.length + 1) * GAP;
        _layout = resizeVertical(_rz.base, _rz.rowIdx, _rz.cellIdx, dxCss / innerW, MIN_W / innerW);
      } else {
        // Divisor horizontal: reparte alto entre la fila rowIdx y la siguiente.
        const innerH = H - (_rz.base.rows.length + 1) * GAP;
        _layout = resizeHorizontal(_rz.base, _rz.rowIdx, dyCss / innerH, MIN_H / innerH);
      }
      if (_rz.raf) cancelAnimationFrame(_rz.raf);
      _rz.raf = requestAnimationFrame(() => {
        if (!_rz) return;
        _rz.raf = 0;
        render();
        // Adaptación EN VIVO (pedido 2026-07-03): el CONTENIDO de las terminales
        // afectadas acompaña el nuevo tamaño DURANTE el arrastre — throttle de
        // ~180ms y solo las tocadas. Llamada directa al callback (el gate
        // _interacting de _refit es para el resto del mundo); es seguro porque
        // refitTerminal es idempotente hacia tmux (solo manda resize si cambian
        // cols/rows) y en el motor de UN emulador ese resize es UNA orden barata.
        const ahora = (typeof performance !== 'undefined' ? performance.now() : 0);
        if (_refitCb && _rz.liveIds && _rz.liveIds.length && ahora - _rz.liveT >= 180) {
          _rz.liveT = ahora;
          for (const id of _rz.liveIds) { try { _refitCb(Number(id), true); } catch (_) {} }
        }
      });
    }
    function _onResizeUp() {
      if (!_rz) return;
      if (_rz.raf) cancelAnimationFrame(_rz.raf);
      const esLibre = !!(_rz.wallV || _rz.wallH);
      const liveIds = _rz.liveIds || [];
      _interacting = false;
      if (typeof document !== 'undefined') document.body.classList.remove('tl-resizing');
      _gridEl.removeEventListener('pointermove', _onResizeMove);
      _rz = null;
      render();
      // Refit final FORZADO de las afectadas (doble pasada: asegura que el
      // contenido quede perfectamente asentado al tamaño final).
      if (liveIds.length) _refit(liveIds, true);
      else _refit();
      if (esLibre) _persistLibre();
    }

    // ── Drag (mover) = INTERCAMBIAR celdas ─────────────────────────
    const DRAG_THRESHOLD = 5;   // px: por debajo es un CLICK, no un arrastre → la card NO se mueve
    // Banda superior que revela el panel de disposición por CERCANÍA (reveal-on-approach).
    const SNAP_BAND = 108;      // px desde el top del grid: "zona de invocación" del panel
    const SNAP_HYST = 28;       // px de histéresis: se oculta recién pasada la banda (sin titileo)
    let _drag = null;
    function _onPointerDown(e) {
      if (_maximizedId !== null) return;
      const chrome = e.target.closest('.terminal-chrome');
      if (!chrome) return;
      if (e.target.closest('select, button, [contenteditable], input')) return;
      const card = chrome.closest('.terminal-card');
      if (!card) return;
      const id = card.id.replace('terminal-card-', '');
      if (!findCell(_layout, id)) return;
      e.preventDefault();
      card.setPointerCapture?.(e.pointerId);
      // NO marcamos interacción ni movemos todavía: un click normal sobre el chrome
      // (con el mínimo temblor de mano) NO debe mover la card. El drag REAL arranca en
      // _onPointerMove al superar DRAG_THRESHOLD. (bug "las terminales se mueven solas".)
      _drag = { id, startX: e.clientX, startY: e.clientY, card, target: null, started: false,
                // Geometría del grid CACHEADA al agarrar: durante el drag el grid no cambia
                // de tamaño → _hitTest la reusa sin getBoundingClientRect/deriveRects por evento.
                gridRect: _gridEl.getBoundingClientRect(),
                cellRects: _mode === 'libre' ? _freeRectsPx(_gridW(), _gridH())
                                             : deriveRects(_layout, _gridW(), _gridH(), GAP) };
      _gridEl.addEventListener('pointermove', _onPointerMove);
      _gridEl.addEventListener('pointerup', _onPointerUp, { once: true });
    }
    function _hitTest(clientX, clientY) {
      // Geometría cacheada en _onPointerDown (fija durante el drag): 0 reflows por pointermove.
      const rect = _drag.gridRect;
      const x = clientX - rect.left + _gridEl.scrollLeft;
      const y = clientY - rect.top + _gridEl.scrollTop;
      const rects = _drag.cellRects;
      for (const id of gridIds(_layout)) {
        if (String(id) === String(_drag.id)) continue;
        const r = rects[id];
        if (r && x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h) return id;
      }
      return null;
    }
    function _onPointerMove(e) {
      if (!_drag) return;
      // dx/dy en píxeles CSS: el transform de la card se interpreta en esa unidad
      // (el umbral click↔drag también, para que no dependa de la Escala).
      const _z = _zoomApp();
      const dx = (e.clientX - _drag.startX) / _z, dy = (e.clientY - _drag.startY) / _z;
      if (!_drag.started) {
        // Umbral click↔drag: hasta superar DRAG_THRESHOLD es un CLICK → no mover nada.
        if (Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) return;
        _drag.started = true;
        _interacting = true;
        if (typeof document !== 'undefined') document.body.classList.add('tl-resizing');
        _drag.card.classList.add('dragging');
        _drag.card.style.willChange = 'transform';
        _drag.card.style.zIndex = '5';
        // Reveal-on-approach (estilo Windows/Google): el panel NO sale al agarrar.
        // Si AGARRÉ dentro de la banda superior (una terminal de arriba) NO se arma
        // → no salta "de una"; se arma al salir de la banda y aparece al VOLVER arriba.
        // Si agarré abajo, arranca armado y aparece al subir a la banda.
        _drag.snapArmed = (_drag.startY > _drag.gridRect.top + SNAP_BAND * _zoomApp());
      }
      _drag.card.style.transform = `translate(${dx}px, ${dy}px)`;
      // Revelar/ocultar el panel según la cercanía al top; después, si el cursor está
      // sobre él, elegir zona y SUPRIMIR el intercambio; fuera → swap.
      _updateSnapReveal(e.clientY);
      if (_updateSnapHover(e.clientX, e.clientY)) {
        if (_drag.target) { _cardEl(_drag.target)?.classList.remove('swap-target'); _drag.target = null; }
        return;
      }
      const target = _hitTest(e.clientX, e.clientY);
      if (target !== _drag.target) {
        if (_drag.target) _cardEl(_drag.target)?.classList.remove('swap-target');
        _drag.target = target;
        if (target) _cardEl(target)?.classList.add('swap-target');
      }
    }
    function _onPointerUp() {
      if (!_drag) return;
      _gridEl.removeEventListener('pointermove', _onPointerMove);
      if (!_drag.started) { _drag = null; return; }   // fue un CLICK (no superó el umbral) → NO mover ni commitear
      _interacting = false;
      if (typeof document !== 'undefined') document.body.classList.remove('tl-resizing');
      const { id, card, target, snap } = _drag;
      _hideSnapBar();
      // Snap: se soltó sobre una zona del panel → aplicar ese preset (la arrastrada
      // ocupa la zona; el resto rellena en orden de lectura). Gana sobre el swap.
      if (snap && _mode === 'libre') {
        _free = applyPreset(snap.cells, _ordenLibre(), id, snap.zone);
        // RECORDAR la distribución elegida: desde acá, agregar/quitar la re-aplica.
        _dist = distFromPreset(snap.key, snap.cells);
        _stampGhost(snap.cells, snap.zone);   // pulso de "sellado" del blueprint
        _syncModeBtn();
        card.style.transform = ''; card.style.willChange = ''; card.style.zIndex = '';
        card.classList.remove('dragging');
        if (target) _cardEl(target)?.classList.remove('swap-target');
        _drag = null;
        render(); _refit(null, true); _persistLibre();
        return;
      }
      if (target) {
        _cardEl(target)?.classList.remove('swap-target');
        if (_mode === 'libre') {
          // INTERCAMBIO también en libre (pedido 2026-07-02): cada card adopta la
          // celda de la otra — el patrón queda intacto, imposible dejar huecos.
          // (El reposicionamiento libre con achique ajustarF se eliminó.)
          const a = _free[id], b = _free[target];
          if (a && b) { _free[id] = b; _free[target] = a; }
        } else {
          _layout = swapCells(_layout, id, target);
        }
      }
      card.style.transform = '';
      card.style.willChange = '';
      card.style.zIndex = '';
      card.classList.remove('dragging');
      _drag = null;
      render();
      if (_mode === 'libre') {
        if (target) _refit([id, target], true);   // solo las dos que intercambiaron
        _persistLibre();
      } else {
        _refit();  // mosaico: como siempre
      }
    }

    // ── Selector de DISPOSICIÓN flotante ("snap", estilo Windows-11) ────────────
    // Aparece ARRIBA del grid al empezar a arrastrar una card (modo Vertical) con
    // los presets del CONTEO actual. Convive con el intercambio: sobre el panel →
    // snap; fuera del panel → intercambio (como siempre). Se aplica al soltar en
    // una zona (la arrastrada la ocupa; el resto rellena en orden de lectura).
    let _snapBar = null;
    let _dispPresets = null;   // presets del panel abierto "en frío" (modo pick por ícono)
    let _previewN = 2;         // preview de crecimiento con 1 sola terminal (cuántas se verán)
    const _SNAP_HINT = '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="2.5" width="12" height="11" rx="1.6"/><path d="M8 2.5v11M2 8h12"/></svg>';
    function _ensureSnapBar() {
      if (_snapBar) return _snapBar;
      const bar = document.createElement('div');
      bar.className = 'tsnap-bar';
      bar.hidden = true;
      bar.setAttribute('aria-hidden', 'true');
      // Click en un tile (SOLO en modo pick, abierto por el ícono): aplicar las
      // celdas EXACTAS de ese preset + recordar su familia. En drag el pointer está
      // capturado por la card → este click nunca corre (lo gatea _dispOpen).
      bar.addEventListener('click', (e) => {
        if (!_dispOpen) return;
        const cntBtn = e.target.closest('.tsnap-count-b');
        if (cntBtn) {
          _previewN = Math.min(6, Math.max(2, _previewN + Number(cntBtn.dataset.cnt)));
          _hideGhost();                        // la proyección vieja no corresponde al nuevo conteo
          _openDispPanel(document.getElementById('terminals-mode-btn'));
          return;
        }
        const tileEl = e.target.closest('.tsnap-tile');
        if (!tileEl || !_dispPresets) return;
        const p = _dispPresets[Number(tileEl.dataset.tile)];
        if (p) {
          const previo = _snapBar.dataset.previo === '1';
          _aplicarPreset(p); _stampGhost(p.cells, -1);
          if (previo) {
            // Preview con 1 sola terminal: la card NO se achica a la primera
            // celda del preset (con N=6 quedaría en 1/6 de pantalla). El patrón
            // queda guardado (_dist) y el "cómo se verá" lo muestran los tiles
            // y el fantasma sellado; los add() futuros sí re-acomodan todo.
            const ids = gridIds(_layout);
            if (ids.length === 1) {
              const solo = _free[ids[0]];
              if (solo && (solo.w < 1 || solo.h < 1)) {
                _free[ids[0]] = { x: 0, y: 0, w: 1, h: 1 };
                render(); _refit(null, true); _persistLibre();
              }
            }
            window.toast?.('Patrón guardado — las terminales nuevas seguirán este arreglo', 'success');
          }
        }
        _closeDispPanel();
      });
      // Modo pick: pasar el mouse por un tile PROYECTA ese arreglo sobre el grid
      // real (blueprint fantasma) — ves cómo quedaría ANTES de hacer click.
      bar.addEventListener('pointerover', (e) => {
        if (!_dispOpen || !_dispPresets) return;
        const tileEl = e.target.closest('.tsnap-tile');
        if (!tileEl) return;
        const p = _dispPresets[Number(tileEl.dataset.tile)];
        if (p) { _showGhost(p.cells, -1); _setHint(p.label); }
      });
      bar.addEventListener('pointerleave', () => {
        if (_dispOpen) { _hideGhost(); _setHint(bar.dataset.hintDef || ''); }
      });
      document.body.appendChild(bar);
      _snapBar = bar;
      return bar;
    }

    // ── Proyección FANTASMA sobre el grid (blueprint del arreglo futuro) ────────
    // Mientras el cursor está sobre una zona del panel (o sobre un tile en modo
    // pick), el grid REAL muestra el arreglo que va a quedar: celdas en trazo
    // sutil + la zona destino encendida con el acento. Al soltar/click, el
    // blueprint hace un pulso de "sellado" y se desvanece. pointer-events:none →
    // invisible para elementFromPoint/hitTest; ≤12 nodos, solo opacity (barato).
    let _ghostEl = null, _ghostHideT = 0;
    function _ensureGhost() {
      if (_ghostEl && _ghostEl.parentNode === _gridEl) return _ghostEl;
      if (_ghostEl) _ghostEl.remove();
      const g = document.createElement('div');
      g.className = 'tsnap-ghost';
      g.hidden = true;
      g.setAttribute('aria-hidden', 'true');
      _gridEl.appendChild(g);
      _ghostEl = g;
      return g;
    }
    function _showGhost(cells, zone) {
      if (!_gridEl || !cells) return;
      const g = _ensureGhost();
      if (_ghostHideT) { clearTimeout(_ghostHideT); _ghostHideT = 0; }
      let h = '';
      for (let i = 0; i < cells.length; i++) {
        const c = cells[i];
        h += `<i class="${i === zone ? 'tg-dest' : ''}" style="left:${(c.x * 100).toFixed(3)}%;top:${(c.y * 100).toFixed(3)}%;width:${(c.w * 100).toFixed(3)}%;height:${(c.h * 100).toFixed(3)}%"></i>`;
      }
      g.innerHTML = h;
      g.classList.remove('tsnap-stamp');
      if (g.hidden) { g.hidden = false; void g.offsetWidth; }   // reflow → fade desde 0
      g.classList.add('tsnap-ghost-on');
    }
    function _hideGhost(immediate) {
      const g = _ghostEl;
      if (!g) return;
      g.classList.remove('tsnap-ghost-on', 'tsnap-stamp');
      if (g.hidden) return;
      if (_ghostHideT) { clearTimeout(_ghostHideT); _ghostHideT = 0; }
      const reduce = (typeof matchMedia === 'function') && matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (immediate || reduce) { g.hidden = true; return; }
      // Mismo esquema que el panel: sacar del DOM al terminar el fade (timeout, no
      // transitionend) — un fantasma a opacity:0 pero visible no molesta al hit-test
      // (pointer-events:none), pero sí ensucia el DOM del grid.
      _ghostHideT = setTimeout(() => {
        _ghostHideT = 0;
        if (!g.classList.contains('tsnap-ghost-on')) g.hidden = true;
      }, 240);
    }
    // Pulso de confirmación: el blueprint "sella" el arreglo elegido y se va.
    function _stampGhost(cells, zone) {
      if (!_gridEl || !cells) return;
      const reduce = (typeof matchMedia === 'function') && matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (reduce) { _hideGhost(true); return; }
      _showGhost(cells, zone);
      _ghostEl.classList.add('tsnap-stamp');
      if (_ghostHideT) clearTimeout(_ghostHideT);
      _ghostHideT = setTimeout(() => { _ghostHideT = 0; _hideGhost(); }, 430);
    }
    // Hint dinámico: en reposo el microcopy del modo; sobre una zona/tile, el
    // NOMBRE del preset (sabés qué vas a aplicar sin mirar la etiqueta chica).
    function _setHint(txt) {
      const b = _snapBar && _snapBar.querySelector('.tsnap-hint b');
      if (b && txt && b.textContent !== txt) b.textContent = txt;
    }
    // CLAVA el ancho del hint al texto MÁS LARGO que puede mostrar (default +
    // todos los labels), medido UNA vez por apertura. Sin esto, el swap del hint
    // cambiaba el ancho del bar y — centrado con translateX(-50%) — el panel se
    // RE-CENTRABA: los tiles se corrían bajo el cursor, la zona hover cambiaba,
    // el hint cambiaba de nuevo… lazo de "baila y hace múltiples selecciones"
    // (video del usuario 2026-07-08). El bar tiene que estar EN el DOM y visible
    // (hidden=false) para que offsetWidth mida de verdad.
    function _lockHintWidth(bar, presets) {
      const b = bar.querySelector('.tsnap-hint b');
      if (!b) return;
      const def = b.textContent;
      b.style.width = '';
      let w = b.offsetWidth;
      for (const p of presets) { b.textContent = p.label; w = Math.max(w, b.offsetWidth); }
      b.textContent = def;
      b.style.width = Math.ceil(w) + 'px';
    }
    function _snapZonesHTML(cells) {
      let h = '';
      for (let i = 0; i < cells.length; i++) {
        const c = cells[i];
        h += `<span class="tsnap-zone" data-zone="${i}" style="left:${(c.x * 100).toFixed(3)}%;top:${(c.y * 100).toFixed(3)}%;width:${(c.w * 100).toFixed(3)}%;height:${(c.h * 100).toFixed(3)}%"><i></i></span>`;
      }
      return h;
    }
    // Orden de lectura del arreglo ACTUAL (arriba→abajo, izq→der): así "el resto
    // rellena en orden" respeta lo que el usuario ve.
    function _ordenLibre() {
      return gridIds(_layout).slice().sort((a, b) => {
        const A = _free[a] || { x: 0, y: 0 }, B = _free[b] || { x: 0, y: 0 };
        return (Math.round(A.y * 1000) - Math.round(B.y * 1000)) || (A.x - B.x);
      });
    }
    // Visible = en el DOM Y abierto (no a mitad de la transición de salida).
    function _snapShown() { return !!_snapBar && !_snapBar.hidden && _snapBar.classList.contains('tsnap-open'); }
    function _showSnapBar() {
      if (_mode !== 'libre' || !_drag) return;     // el snap vive en el arreglo por fracciones
      const n = gridIds(_layout).length;
      if (n < 2) return;                           // con 1 sola no hay nada que acomodar
      const bar = _ensureSnapBar();
      if (bar._tsnapHideT) { clearTimeout(bar._tsnapHideT); bar._tsnapHideT = 0; }
      // Reconstruir SOLO en una apertura fresca (oculto, o drag nuevo sin presets).
      // Re-entrada del MISMO drag (venía saliendo) → no rebuildea: revierte la salida.
      if (bar.hidden || !_drag.presets) {
        bar.classList.remove('tsnap-pick');          // drag = zonas (no pick por click)
        // El layout ACTUAL no se ofrece (ya lo tenés; reposicionar dentro de él
        // es el swap de siempre). Solo alternativas reales.
        const presets = sinPresetActual(snapPresets(n), _free);
        if (!presets.length) return;
        _drag.presets = presets;
        bar.dataset.hintDef = 'Soltá en una zona';
        let html = `<span class="tsnap-aura" aria-hidden="true"></span><span class="tsnap-hint">${_SNAP_HINT}<b>Soltá en una zona</b><span class="tsnap-count">${n}</span></span><span class="tsnap-rail">`;
        presets.forEach((p, ti) => {
          html += `<span class="tsnap-tile" data-tile="${ti}" style="--i:${ti}"><span class="tsnap-frame">${_snapZonesHTML(p.cells)}</span><span class="tsnap-label">${p.label}</span></span>`;
        });
        html += '</span>';
        bar.innerHTML = html;
        // Anclado ARRIBA del grid, centrado (reveal-on-approach: solo al subir a la banda).
        const g = _drag.gridRect;                  // cacheado al agarrar (fixed → coords de viewport)
        bar.style.left = Math.round(g.left + g.width / 2) + 'px';
        bar.style.bottom = 'auto';
        bar.style.top = Math.round(g.top + 12) + 'px';
        if (bar.hidden) { bar.hidden = false; void bar.offsetWidth; }   // reflow → la transición corre desde "cerrado"
        _lockHintWidth(bar, presets);   // el hint dinámico no debe mover el panel
      }
      if (!bar.classList.contains('tsnap-open')) {
        bar.classList.remove('tsnap-open'); void bar.offsetWidth;       // re-dispara el stagger en cada apertura
        bar.classList.add('tsnap-open');
      }
    }
    // Revela/oculta el panel según la cercanía del cursor a la banda superior del
    // grid. `_drag.snapArmed` implementa el "no salga al agarrar de arriba": mientras
    // esté sin armar, no aparece; sale de la banda → arma; entra a la banda → aparece.
    function _updateSnapReveal(clientY) {
      if (_mode !== 'libre' || !_drag || gridIds(_layout).length < 2) return;
      const top = _drag.gridRect.top;
      // SNAP_BAND/SNAP_HYST son px CSS; clientY viene en px de pantalla.
      const zz = _zoomApp(), banda = SNAP_BAND * zz, hist = SNAP_HYST * zz;
      if (clientY > top + banda + hist) {            // bien lejos del top
        _drag.snapArmed = true;                      // rearmar para la próxima subida
        if (_snapShown()) _hideSnapBar();            // se alejó → ocultar
      } else if (clientY <= top + banda && _drag.snapArmed) {
        _showSnapBar();                              // cerca del top y armado → aparecer
      }
    }
    function _clearSnapHover() {
      if (!_snapBar) return;
      _snapBar.querySelector('.tsnap-tile.hot')?.classList.remove('hot');
      _snapBar.querySelector('.tsnap-zone.on')?.classList.remove('on');
      if (_drag && _drag.snap) { _hideGhost(); _setHint(_snapBar.dataset.hintDef || ''); }
      if (_drag) _drag.snap = null;
    }
    // Devuelve true si el cursor está DENTRO del panel (→ suprimir intercambio).
    function _updateSnapHover(clientX, clientY) {
      if (!_snapBar || _snapBar.hidden) return false;
      const b = _snapBar.getBoundingClientRect();
      if (clientX < b.left || clientX > b.right || clientY < b.top || clientY > b.bottom) {
        _clearSnapHover(); return false;
      }
      const el = document.elementFromPoint(clientX, clientY);
      const zoneEl = el?.closest?.('.tsnap-zone');
      const tileEl = el?.closest?.('.tsnap-tile');
      if (zoneEl && tileEl && _drag.presets) {
        const ti = Number(tileEl.dataset.tile), zi = Number(zoneEl.dataset.zone);
        if (!_drag.snap || _drag.snap.tile !== ti || _drag.snap.zone !== zi) {
          _clearSnapHover();
          _drag.snap = { tile: ti, zone: zi, cells: _drag.presets[ti].cells, key: _drag.presets[ti].key };
          tileEl.classList.add('hot');
          zoneEl.classList.add('on');
          // Proyección en vivo: el grid muestra el arreglo que va a quedar, con
          // la zona destino (donde cae la arrastrada) encendida.
          _showGhost(_drag.snap.cells, zi);
          _setHint(_drag.presets[ti].label);
        }
      } else {
        _clearSnapHover();                         // sobre el panel pero no sobre una zona
      }
      return true;
    }
    function _hideSnapBar(immediate) {
      if (!_snapBar) return;
      _clearSnapHover();
      const bar = _snapBar;
      bar.classList.remove('tsnap-open', 'tsnap-pick');   // dispara la transición de SALIDA
      if (bar.hidden) return;
      if (bar._tsnapHideT) { clearTimeout(bar._tsnapHideT); bar._tsnapHideT = 0; }
      const reduce = (typeof matchMedia === 'function') && matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (immediate || reduce) { bar.hidden = true; return; }
      // Sacar del DOM al terminar la salida (timeout ≈ duración de la transición). Si
      // se reabrió antes, no ocultar → un panel invisible nunca captura elementFromPoint.
      bar._tsnapHideT = setTimeout(() => {
        bar._tsnapHideT = 0;
        if (!bar.classList.contains('tsnap-open')) bar.hidden = true;
      }, 260);
    }

    // ── Ícono de DISPOSICIÓN en la barra (.jw-bar-right) — se AUTO-INYECTA (como el
    // banner del updater) para no editar workspace.html/js. Es una CUADRÍCULA 2×2 que
    // abre el PANEL VISUAL de disposiciones (mini-mockups animados). Reemplaza al viejo
    // toggle Vertical/Ordenada: el modo Mosaico se RETIRÓ (sus cuadrículas ya las da el
    // panel como presets → el toggle era redundante). Click en un preset = aplicar esa
    // distribución a TODAS + recordarla (_dist). Es el MISMO panel que aparece al
    // arrastrar, abierto "en frío" con los tiles clickeables. Pedido del usuario
    // 2026-07-05 · ver [[terminales-snap-disposicion]]. ──
    const _SVG = {
      // Cuadrícula 2×2 ("un cuadrado en cuatro") — comunica "disposición".
      grid: '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="2" width="5.3" height="5.3" rx="1.3"/><rect x="8.7" y="2" width="5.3" height="5.3" rx="1.3"/><rect x="2" y="8.7" width="5.3" height="5.3" rx="1.3"/><rect x="8.7" y="8.7" width="5.3" height="5.3" rx="1.3"/></svg>',
    };
    let _dispOpen = false;
    function _onDocDisp(e) {
      if (!_snapBar) return;
      if (_snapBar.contains(e.target) || e.target.closest('#terminals-mode-btn')) return;
      _closeDispPanel();
    }
    function _onKeyDisp(e) { if (e.key === 'Escape') _closeDispPanel(); }
    function _closeDispPanel() {
      if (!_dispOpen) return;
      _dispOpen = false;
      _previewN = 2;                                  // el preview de crecimiento se resetea
      if (_snapBar) _snapBar.dataset.previo = '';
      document.removeEventListener('pointerdown', _onDocDisp, true);
      document.removeEventListener('keydown', _onKeyDisp, true);
      _hideSnapBar();
      // El fantasma del hover se va con el panel — pero NO el pulso de sellado
      // (si acaban de elegir un preset, el stamp confirma y se desvanece solo).
      if (!(_ghostEl && _ghostEl.classList.contains('tsnap-stamp'))) _hideGhost();
      _syncModeBtn();
    }
    // Abre el panel "en frío" (sin drag): tiles clickeables, marca el activo, cierra
    // al elegir / click afuera / Esc.
    function _openDispPanel(btn) {
      if (typeof document === 'undefined' || !_gridEl) return;
      const n = gridIds(_layout).length;
      // Con UNA sola terminal el panel igual abre, en modo PREVIEW de cómo
      // crecerá: muestra el arreglo para N futuras (2..6). Elegir un tile deja
      // la familia guardada (_dist) → los próximos add() la re-aplican.
      const previo = n < 2;                       // 1 sola terminal: preview de cómo crecerá
      const N = previo ? _previewN : n;           // _previewN default 2
      const bar = _ensureSnapBar();
      if (bar._tsnapHideT) { clearTimeout(bar._tsnapHideT); bar._tsnapHideT = 0; }
      // El layout ACTUAL no se ofrece (pedido 2026-07-08): clickearlo era un
      // no-op — el panel muestra solo alternativas. (Antes se marcaba `.sel`;
      // ya no hay nada que marcar: lo que ves es siempre "otra cosa".)
      const presets = sinPresetActual(snapPresets(N), _free);
      if (!presets.length) return;
      _dispPresets = presets;
      bar.dataset.previo = previo ? '1' : '';
      const hintDef = previo ? 'Así se verá al agregar terminales' : 'Elegí una disposición';
      bar.dataset.hintDef = hintDef;
      const cntHtml = previo
        ? `<span class="tsnap-count"><button type="button" class="tsnap-count-b" data-cnt="-1">−</button><em>${N}</em><button type="button" class="tsnap-count-b" data-cnt="1">+</button></span>`
        : `<span class="tsnap-count">${n}</span>`;
      let html = `<span class="tsnap-aura" aria-hidden="true"></span><span class="tsnap-hint">${_SNAP_HINT}<b>${hintDef}</b>${cntHtml}</span><span class="tsnap-rail">`;
      presets.forEach((p, ti) => {
        html += `<button type="button" class="tsnap-tile" data-tile="${ti}" style="--i:${ti}"><span class="tsnap-frame">${_snapZonesHTML(p.cells)}</span><span class="tsnap-label">${p.label}</span></button>`;
      });
      html += '</span>';
      bar.innerHTML = html;
      bar.classList.add('tsnap-pick');                    // habilita el modo click + su estética
      // Anclaje: el grid; si está OCULTO (workspace sin terminales → .oculto) su
      // rect es 0×0 en (0,0) y el panel, centrado con translateX(-50%), se iba
      // fuera de pantalla a la izquierda. Caemos al contenedor del mosaico para
      // mostrarlo igual que cuando hay terminales.
      let g = _gridEl.getBoundingClientRect();
      if (g.width < 4 || g.height < 4) {
        const cont = _gridEl.parentElement;
        if (cont) g = cont.getBoundingClientRect();
      }
      bar.style.bottom = 'auto';
      bar.style.top = Math.round(g.top + 12) + 'px';
      if (bar.hidden) { bar.hidden = false; void bar.offsetWidth; }
      _lockHintWidth(bar, presets);   // fija el ancho (el hint dinámico no debe moverlo)
      // Clamp horizontal: `left` es el CENTRO del panel, así que media barra
      // tiene que quedar dentro del viewport en los dos bordes. Se mide DESPUÉS
      // de _lockHintWidth para usar el ancho final.
      const mitad = (bar.offsetWidth || 0) / 2;
      let left = g.left + g.width / 2;
      const minL = mitad + 8, maxL = window.innerWidth - mitad - 8;
      if (mitad && maxL > minL) left = Math.max(minL, Math.min(maxL, left));
      bar.style.left = Math.round(left) + 'px';
      bar.classList.remove('tsnap-open'); void bar.offsetWidth;   // re-dispara el stagger
      bar.classList.add('tsnap-open');
      _dispOpen = true;
      _syncModeBtn();
      setTimeout(() => {
        document.addEventListener('pointerdown', _onDocDisp, true);
        document.addEventListener('keydown', _onKeyDisp, true);
      }, 0);
    }
    function _ensureModeBtn() {
      if (typeof document === 'undefined') return;
      const bar = document.querySelector('.jw-bar-right');
      if (!bar) return;
      // El botón "Reordenar" suelto sigue oculto: su acción vive dentro del panel.
      const reset = document.getElementById('terminals-reset-btn');
      if (reset) reset.style.display = 'none';
      if (document.getElementById('terminals-mode-btn')) { _syncModeBtn(); return; }
      const b = document.createElement('button');
      b.id = 'terminals-mode-btn'; b.type = 'button'; b.className = 'jw-icon-btn';
      b.setAttribute('aria-label', 'Disposición de terminales');
      b.addEventListener('click', (e) => {
        e.stopPropagation();
        _dispOpen ? _closeDispPanel() : _openDispPanel(b);
      });
      if (reset && reset.parentNode === bar) reset.insertAdjacentElement('afterend', b);
      else bar.insertBefore(b, bar.firstChild);
      _syncModeBtn();
    }
    function _syncModeBtn() {
      if (typeof document === 'undefined') return;
      const b = document.getElementById('terminals-mode-btn');
      if (!b) return;
      b.innerHTML = _SVG.grid;
      b.title = 'Disposición de terminales';
      b.classList.toggle('activo', _dispOpen);
    }

    Object.assign(global.TerminalLayout = global.TerminalLayout || {}, {
      _pure: pure,
      enableInteractions() {
        if (!_gridEl || _gridEl._tlWired) return;
        _gridEl._tlWired = true;
        _gridEl.classList.toggle('tl-libre', _mode === 'libre');
        _gridEl.addEventListener('pointerdown', _onPointerDown);
        // Backstop anti-flag-clavado: un `pointercancel` (gesto, cambio de ventana,
        // WSL) NO tiene handler propio → dejaría un drag/resize interno colgado:
        // _interacting clavado en true (gatea el refit) + transform pegado en la card.
        // Al terminar CUALQUIER puntero a nivel ventana, si quedó algo interno sin
        // cerrar, lo cerramos y reacomodamos. `pointerup` corre en bubble DESPUÉS de
        // los handlers de _gridEl → en el caso normal _drag/_rz ya son null y es
        // no-op. Ver [[bug-terminales-letras-rotas]] tema E.
        const _backstop = () => {
          if (!_drag && !_rz) return;                   // nada interno colgado → no-op
          _hideSnapBar(true);   // teardown → ocultar al instante (sin animación)
          _hideGhost(true);
          if (_drag?.card) { const c = _drag.card; c.style.transform = ''; c.style.willChange = ''; c.style.zIndex = ''; c.classList.remove('dragging'); }
          _gridEl.removeEventListener('pointermove', _onPointerMove);
          _gridEl.removeEventListener('pointermove', _onResizeMove);
          _interacting = false;
          if (typeof document !== 'undefined') document.body.classList.remove('tl-resizing');
          _drag = null; _rz = null;
          render(); _refit();
        };
        window.addEventListener('pointerup', _backstop);
        window.addEventListener('pointercancel', _backstop);
        _ensureModeBtn();
      },
      setRefitCallback(fn) { _refitCb = fn; },
      isInteracting() { return _interacting; },
      // Drags EXTERNOS (splitter del dock): marcan interacción al agarrar y la
      // liberan al soltar; con el flag puesto, relayoutAll() solo renderiza.
      setInteracting(on) { _interacting = !!on; },
      setContainer(el) { _gridEl = el; },
      getLayout() { return _clone(_layout); },
      render,
      mode() { return _mode; },
      // El modo Mosaico se retiró (2026-07-05): hay UN solo motor (libre). Se conserva
      // el método por estabilidad de API, pero SIEMPRE queda en libre (nunca re-entra a
      // mosaico). La disposición ahora se elige por el panel visual (`setDist`/panel).
      setMode() {
        if (_mode === 'libre') return;
        _mode = 'libre';
        _gridEl?.classList.toggle('tl-libre', true);
        _free = Object.keys(_free).length ? expandirF(_free) : tile2col(gridIds(_layout), _perRowMax());
        _persistLibre(); _syncModeBtn(); render(); _refit(null, true);
      },
      // API pública para aplicar/recordar una distribución (por si alguna otra UI
      // quiere gatillarla). `null` = volver a auto.
      setDist(dist) { _aplicarDist(dist); },
      // Aplica un preset EXACTO del catálogo (`{key,label,cells}` como los devuelve
      // _pure.snapPresets): celdas tal cual + memoria de familia + persistencia.
      // Lo usa el picker de terminal rápida al lanzar con disposición elegida.
      aplicarPreset(p) { if (p && Array.isArray(p.cells) && p.cells.length) _aplicarPreset(p); },
      dist() { return _dist ? { ...(_dist) } : null; },
      init(projectId, gridEl) {
        _projectId = projectId; if (gridEl) _gridEl = gridEl;
        _maximizedId = null;
        // Sanear el estado de interacción: si un drag/resize quedó a medias al navegar
        // (sin su pointerup), _interacting clavado en true GATEA el refit para siempre →
        // las terminales no re-sincronizan tamaño con tmux → garble. Reset duro al cargar.
        _interacting = false;
        if (typeof document !== 'undefined') document.body.classList.remove('tl-resizing');
        _drag = null; _rz = null;
        _hideSnapBar(true);   // un panel de snap colgado de otro proyecto no debe sobrevivir
        _hideGhost(true);
        _layout = { rows: [] };
        _mode = 'libre';
        const saved = _cargarLibre();
        _dist = _distSano(saved && saved.dist);   // distribución elegida recordada (o auto)
        // localStorage es input NO confiable (versiones viejas, escrituras a medias):
        // se valida SIEMPRE con sanearFreeF — basura descartada, resto clampeado.
        // Mins fraccionales chicos a propósito: acá el grid puede no estar medido
        // (los mins en px reales los aplican las interacciones vía _minF()).
        _free = sanearFreeF(saved && saved.free, 0.02, 0.02);
        // Vertical llena TODO al cargar: layouts persistidos con bandas muertas
        // (p.ej. columnas a media altura de un layout viejo de 2 filas) estiran
        // hasta ocupar el espacio completo (pedido 2026-07-02).
        _free = expandirF(_free);
        // Migración "máx 2 filas" (pedido 2026-07-04) SOLO en modo auto: un layout
        // viejo podía tener 3 filas espurias. PERO si el usuario ELIGIÓ una distribución
        // de 3 filas, 3 filas es legítimo → no la clobbereamos (se restaura tal cual).
        if (!_dist && contarFilasF(_free) > 2) _free = tile2col(Object.keys(_free), _perRowMax());
        // Proteger lo guardado hasta que cada terminal haga su add() — y armar el
        // asentamiento que poda lo que nunca llegue (terminales muertas).
        _restaurando = new Set(Object.keys(_free));
        _armarSettle();
        _gridEl?.classList.toggle('tl-libre', true);
        _syncModeBtn();
        render();
      },
      onProjectChanged(projectId) {
        global.TerminalLayout.init(projectId, _gridEl);
      },
      // Alta SIN molestar a las demás (2026-07-02): en LIBRE, la terminal nueva
      // parte SOLO la card más ancha; el resto no cambia ni un pixel (ni recibe
      // resize). Una terminal con posición GUARDADA (recarga/navegación) la
      // conserva tal cual. Fallback tile2col solo si no entra ni partiendo.
      add(id) {
        id = String(id);
        _restaurando.delete(id);   // llegó: su posición guardada ya no necesita protección
        _armarSettle();            // re-armar: la poda de muertas corre tras el ÚLTIMO add
        if (!findCell(_layout, id)) {
          const ids = gridIds(_layout); ids.push(id);
          _layout = balancedGrid(ids);
        }
        if (_mode === 'libre') {
          if (_free[id]) {
            // posición persistida (carga de proyecto): restaurar sin tocar a nadie
            render(); _refit([id], true); _persistLibre();
            return;
          }
          const antes = _free;
          if (_dist) {
            // DISTRIBUCIÓN ELEGIDA activa (pedido 2026-07-05): la nueva entra al FINAL
            // del orden de lectura y TODO se re-acomoda según la distribución para el
            // nuevo conteo (columnas siguen columnas, cuadrícula sigue cuadrícula…).
            const orden = Object.keys(_free).filter(k => k !== id)
              .sort((a, b) => (_free[a].y - _free[b].y) || (_free[a].x - _free[b].x));
            orden.push(id);
            const nuevo = applyDistribution(_dist, orden, orden.length);
            if (nuevo) _free = nuevo;
          } else {
            // Auto (default): alta DETERMINISTA (pedido 2026-07-04) — la nueva entra a
            // la fila que le toca y la OTRA fila queda CONGELADA; con la de abajo llena
            // (3), la nueva "se crea arriba" y las 3 de abajo NO se mueven.
            _free = (contarFilasF(_free) > 2)                   // layout viejo raro (>2 filas)
              ? tile2col(gridIds(_layout), _perRowMax())        //   → re-tile limpio a 2 filas
              : agregarVerticalF(_free, id, _perRowMax(), MAX_ABAJO);
          }
          const cambio = (a, b) => !a || Math.abs(a.x - b.x) > 1e-6 || Math.abs(a.y - b.y) > 1e-6 ||
                                   Math.abs(a.w - b.w) > 1e-6 || Math.abs(a.h - b.h) > 1e-6;
          const tocadas = Object.keys(_free).filter(k => cambio(antes[k], _free[k]));
          render();
          _refit(tocadas.length ? tocadas : [id], true);   // solo lo que cambió de rect
          _persistLibre();
          return;
        }
        render(); _refit(null, true); _persistLibre();
      },
      // Baja (pedido 2026-07-04): al eliminar, las que quedan se RE-AGRUPAN al layout
      // canónico del modo vertical — "tal cual el mecanismo": ≤6 → UNA sola fila; >6 →
      // 2 filas (abajo capada en 3, top-fill). Se re-tilea con tile2col respetando el
      // ORDEN DE LECTURA (arriba izq→der, luego abajo) para mover lo mínimo. Reemplaza
      // al quitarDeBandaF+expandirF, que dejaba estados no-canónicos (p.ej. [5,1] con 6
      // terminales, sin colapsar a [6]). Solo refitean las cards cuyo rect cambió.
      remove(id) {
        id = String(id);
        _restaurando.delete(id);
        if (_maximizedId == id) _maximizedId = null;
        _layout = balancedGrid(gridIds(_layout).filter(x => x !== id));
        if (_mode === 'libre') {
          const antes = _free;
          const orden = Object.keys(_free)
            .filter(k => k !== id)
            .sort((a, b) => (_free[a].y - _free[b].y) || (_free[a].x - _free[b].x));
          // Con distribución elegida: re-aplicarla al conteo restante. Auto: re-agrupa
          // al canónico vertical (≤6 colapsa a 1 fila).
          const nuevo = _dist ? applyDistribution(_dist, orden, orden.length) : null;
          _free = nuevo || tile2col(orden, _perRowMax());
          const cambio = (a, b) => !a || Math.abs(a.x - b.x) > 1e-6 || Math.abs(a.y - b.y) > 1e-6 ||
                                   Math.abs(a.w - b.w) > 1e-6 || Math.abs(a.h - b.h) > 1e-6;
          const tocadas = Object.keys(_free).filter(k => cambio(antes[k], _free[k]));
          render();
          if (tocadas.length) _refit(tocadas, true);
          _persistLibre();
          return;
        }
        render(); _refit(null, true); _persistLibre();
      },
      // Reordenar (acción del panel): re-acomoda TODO prolijo (anima). Respeta la
      // distribución elegida si hay una; si no, columnas con ancho mínimo.
      reset() {
        _maximizedId = null;
        _restaurando.clear();   // re-tile explícito: lo guardado no restaurado muere acá
        _layout = balancedGrid(gridIds(_layout));
        const ids = _ordenLibre();
        const nuevo = _dist ? applyDistribution(_dist, ids, ids.length) : null;
        _free = nuevo || tile2col(gridIds(_layout), _perRowMax());
        _persistLibre();
        render(); _refit(null, true);
      },
      relayoutAll() { render(); _refit(); },
      maximize(id) { _maximizedId = String(id); render(); _refit([id], true); },
      restore(id) { if (_maximizedId == id) _maximizedId = null; render(); _refit([id], true); },
    });
  }

  // ── Export para Node (tests) ─────────────────────────────────────
  if (typeof module !== 'undefined' && module.exports) module.exports = pure;
})(typeof window !== 'undefined' ? window : globalThis);
