// JARVIS — JarvisEditor: panel editor (file tree + Monaco), extraído de workspace.js
//
// Script CLÁSICO, cargado DESPUÉS de workspace.js. Espeja el patrón de
// OrchestratorPanel/window.jarvisPanel: posee TODO el estado del editor y
// expone una superficie pública CONGELADA en window.JarvisEditor que el resto
// del workspace consume (nunca toca los globals internos).
//
// Fase 0: extracción pura, cero cambios de comportamiento. Las funciones
// internas conservan sus nombres históricos (abrirArchivoEnEditor, setActiveTab,
// cerrarTab, guardarArchivoActivo, cargarFileTree, ...) y la API pública son
// wrappers finos sobre ellas.

(function () {
  'use strict';

  // ─── Estado interno (antes en workspace.js:32-40) ───────────────────
  let _projectId    = null;   // lo setea init()/onProjectChanged()
  let monacoLoaded  = false;
  let monacoLoading = false;
  let _monacoPromise = null;   // promesa única del cold-load (evita race: la 2da llamada AWAITea el mismo load)
  let fileTreeData  = null;
  let fileTreeTimer = null;
  const MAX_TABS    = 10;

  // Links `archivo:línea` (terminal) + sesión por proyecto.
  let _projectRoot = null;          // ruta absoluta del proyecto (cache del fetch)
  let _rootPromise = null;
  let _sesTimer    = null;
  let _restaurando = false;         // true mientras restauramos → no guardar
  let _sesProyectoRestaurado = null; // evita restaurar dos veces el mismo proyecto

  // ─── Estado del editor: modelo de grupos (split view, Fase 9) ────────
  // editorGroups[0] siempre existe (host = #monaco-editor-host).
  // editorGroups[1] se crea LAZY al primer split (toggleSplit).
  // activeGroupIdx apunta al grupo que recibe abrirArchivo/activarTab/etc.
  //
  // Modelos Monaco compartidos por URI inmemory://jarvis/<path>: ambos
  // grupos referencian el MISMO modelo/buffer (sin save-pisado). Un tab por
  // grupo: { content, language, dirty, mtime?, conflicto? }. El viewState es
  // POR GRUPO (cada panel conserva su propio scroll/cursor del mismo archivo).
  function _nuevoGrupo(host, placeholder) {
    return {
      editor:     null,         // instancia monaco.editor.create (lazy)
      tabs:       new Map(),    // path → { content, language, dirty, ... }
      activeTab:  null,         // path activo en ESTE grupo
      viewStates: new Map(),    // path → Monaco viewState (por grupo)
      host,                     // <div .monaco-editor-host>
      placeholder,              // <div .monaco-placeholder>
    };
  }

  let editorGroups     = [];          // se inicializa en _initGrupos()
  let activeGroupIdx   = 0;
  let splitActivo      = false;       // false = 1 grupo visible; true = 2 grupos
  let splitOrientacion = 'vertical';  // 'vertical' (lado a lado) | 'horizontal' (apilado)

  function _grupoActivo() { return editorGroups[activeGroupIdx] || null; }
  function _otroIdx(g)    { return editorGroups.indexOf(g) === 0 ? 1 : 0; }

  // Inicializa editorGroups[0] con el DOM existente. editorGroups[1] queda
  // null hasta el primer split (lo conecta toggleSplit).
  function _initGrupos() {
    if (editorGroups.length) return;
    const host0 = document.getElementById('monaco-editor-host');
    const ph0   = document.getElementById('monaco-placeholder');
    editorGroups = [_nuevoGrupo(host0, ph0), null];
    _cablearDropGrupos();
  }

  // Shims de compatibilidad: las funciones internas que aún razonan en
  // "instancia única" ruteando SIEMPRE al grupo activo. La fuente de verdad
  // de tabs/activeTab vive en cada grupo (g.tabs / g.activeTab).
  function _mon()  { return _grupoActivo()?.editor || null; }
  function _tabs() { return _grupoActivo()?.tabs || new Map(); }
  function _aTab() { return _grupoActivo()?.activeTab || null; }

  // Estado git por archivo: { path: 'M'|'U'|'A'|'D'|'R'|'C' }. Vacío si no es repo.
  let gitStatusMap = {};

  // ─── Contrato único del file tree (spec §4.2) ──────────────────────────────
  let ftExpandedDirs = new Set();   // paths de carpetas expandidas; preserva expansión entre renders
  let ftEditing      = false;        // true mientras hay rename/create inline abierto → pausa polling
  let ftFilter       = '';           // query de búsqueda-en-árbol activa (vacío = sin filtro)
  let _ftFlatIndex   = null;         // índice plano [{name,path,type}] | null = invalidado, hay que recomputar
  let _ftFirma       = '';           // firma del último render (tree+git): si no cambió, el polling no reconstruye el árbol
  let _ftRenderTimer = null;         // debounce de cargarFileTree
  let _openGen       = 0;            // token de generación de abrirArchivoEnEditor: descarta aperturas viejas tras un await

  // ─── Stagger del render inicial (tarea JS 5) ───────────────────────────────
  // Solo el PRIMER render del árbol tras cargar/cambiar de proyecto aplica el
  // retardo escalonado (style="--i:N", cap 20) que dispara ob-pop-in en .ft-node.
  // Los re-renders por polling NO re-staggean (parpadearía todo el árbol cada 30s)
  // y los toggles de carpeta ni siquiera re-renderizan nodos. _ftStagger se activa
  // en cada (re)carga de proyecto y se consume en el primer _cargarFileTreeAhora.
  let _ftStagger     = true;         // ¿este render debe escalonar? (one-shot)
  let _ftStaggerIdx  = 0;            // contador de nodos del render en curso (cap 20)
  const _FT_STAGGER_CAP = 20;

  // ─── Fase 4: cache de archivos + viewStates + LRU de modelos ────────
  // fileCache: path -> { content, language, size, mtime, ts }
  const fileCache  = new Map();
  // viewStates ahora viven POR GRUPO (g.viewStates). Helper para olvidar el
  // viewState de un path en TODOS los grupos (al invalidar/evictar un modelo).
  function _olvidarViewStates(path) {
    for (const g of editorGroups) g?.viewStates?.delete(path);
  }
  // TTL de frescura "optimista": dentro de este lapso abrimos sin fetch
  // y validamos mtime en background. Fuera de él, validamos sincrónicamente.
  const CACHE_TTL_MS = 5000;
  // Cantidad máxima de modelos Monaco vivos antes de disponer el más viejo.
  const MAX_LIVE_MODELS = 15;
  // Orden LRU de URIs de modelos (string), del más viejo [0] al más nuevo.
  const modelLRU = [];

  function _ftUri(path) {
    return monaco.Uri.parse('inmemory://jarvis/' + path);
  }

  // Marca un modelo como usado recientemente (lo manda al final del LRU).
  function _touchModel(path) {
    const key = path;
    const idx = modelLRU.indexOf(key);
    if (idx !== -1) modelLRU.splice(idx, 1);
    modelLRU.push(key);
    _evictModelsIfNeeded();
  }

  // ¿Algún grupo tiene `path` abierto (tab) o activo? Si sí, su modelo NO se
  // puede disponer (buffer compartido visible en al menos un panel).
  function _pathReferenciado(path) {
    return editorGroups.some(g => g && (g.activeTab === path || g.tabs.has(path)));
  }

  // Dispone modelos viejos que NO estén referenciados por NINGÚN grupo.
  function _evictModelsIfNeeded() {
    let i = 0;
    while (modelLRU.length > MAX_LIVE_MODELS && i < modelLRU.length) {
      const key = modelLRU[i];
      if (_pathReferenciado(key)) { i++; continue; }
      const model = monaco.editor.getModel(_ftUri(key));
      if (model) {
        _olvidarViewStates(key);
        model.dispose();
      }
      modelLRU.splice(i, 1);
    }
  }

  // Invalidación de cache (borrado/rename/upload). Llamar con el path afectado.
  function _invalidarCache(path) {
    fileCache.delete(path);
    _olvidarViewStates(path);
    const idx = modelLRU.indexOf(path);
    if (idx !== -1) modelLRU.splice(idx, 1);
    monaco.editor.getModel(_ftUri(path))?.dispose();
  }

  // esc() vive en workspace.js como global; lo reusamos para no duplicar.
  const esc = (s) => window.esc(s);
  // esc() escapa <>& pero NO comillas; para interpolar en atributos="..." hace
  // falta escapar también " (y ') para evitar romper el atributo (XSS).
  const escAttr = (s) => esc(s).replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  const _t = (s) => (window.JarvisI18n && window.JarvisI18n.t) ? window.JarvisI18n.t(s) : s;

  // ─── File tree ──────────────────────────────────────────────────────

  // ─── Iconos SVG inline (reemplazan FT_ICONS/emoji). Coherentes con .ic-* del header.
  //     Cada glyph hereda el color via currentColor; el tamaño lo fija .ft-icon svg.
  const _SVG_GLYPHS = {
    folder:      '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M1.5 4.5a1 1 0 0 1 1-1h3.6a1 1 0 0 1 .7.3l1 1a1 1 0 0 0 .7.3h4.8a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1z"/></svg>',
    folderOpen:  '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M1.5 4.5a1 1 0 0 1 1-1h3.6a1 1 0 0 1 .7.3l1 1a1 1 0 0 0 .7.3h4.8a1 1 0 0 1 1 1v.5h-10a1 1 0 0 0-.97.76l-1.03 4.1z"/><path d="M2.5 13.5l1.2-4.8a1 1 0 0 1 .97-.76h9.6a1 1 0 0 1 .97 1.24l-1 4a1 1 0 0 1-.97.76z"/></svg>',
    file:        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 2.5h5l4 4v7a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1z"/><path d="M8.5 2.5v4h4"/></svg>',
    code:        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 2.5h5l4 4v7a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1z"/><path d="M8.5 2.5v4h4"/><path d="M6.2 9.2L4.8 10.6l1.4 1.4M9.8 9.2l1.4 1.4-1.4 1.4"/></svg>',
    markup:      '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 2.5h5l4 4v7a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1z"/><path d="M8.5 2.5v4h4"/><path d="M6.5 9l-1.5 1.5L6.5 12M9.5 9l1.5 1.5L9.5 12"/></svg>',
    style:       '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 2.5h5l4 4v7a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1z"/><path d="M8.5 2.5v4h4"/><circle cx="6" cy="10.5" r="1"/><circle cx="9.5" cy="10.5" r="1"/></svg>',
    data:        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 2.5h5l4 4v7a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1z"/><path d="M8.5 2.5v4h4"/><path d="M6 9.5h4M6 11.5h4"/></svg>',
    doc:         '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 2.5h5l4 4v7a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1z"/><path d="M8.5 2.5v4h4"/><path d="M5.5 9h5M5.5 11h3"/></svg>',
    shell:       '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="12" height="10" rx="1"/><path d="M4.5 6.5l2 1.5-2 1.5M8 9.5h3"/></svg>',
    database:    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="8" cy="4" rx="5" ry="2"/><path d="M3 4v8c0 1.1 2.24 2 5 2s5-.9 5-2V4"/><path d="M3 8c0 1.1 2.24 2 5 2s5-.9 5-2"/></svg>',
  };

  // Mapea una extensión de archivo a un "kind" de icono.
  function _extKind(name) {
    const ext = (name.split('.').pop() || '').toLowerCase();
    if (['js','jsx','ts','tsx','mjs','cjs','py','rb','go','rs','java','c','h','cpp','php'].includes(ext)) return 'code';
    if (['html','htm','xml','vue','svelte'].includes(ext)) return 'markup';
    if (['css','scss','sass','less'].includes(ext)) return 'style';
    if (['json','yaml','yml','toml','ini','env','conf'].includes(ext)) return 'data';
    if (['md','txt','rst','adoc'].includes(ext)) return 'doc';
    if (['sh','bash','zsh','fish'].includes(ext)) return 'shell';
    if (['sql','db','sqlite'].includes(ext)) return 'database';
    return 'file';
  }

  // Devuelve el markup SVG de un kind (fallback a 'file').
  function _svgIcon(kind) {
    return _SVG_GLYPHS[kind] || _SVG_GLYPHS.file;
  }

  // Captura del DOM los paths de carpetas hoy expandidas, ANTES de re-render.
  // Así los nodos "nacen" expandidos según ftExpandedDirs y el árbol no parpadea.
  function _capturarExpansion() {
    const cont = document.getElementById('file-tree-body');
    if (!cont) return;
    ftExpandedDirs = new Set();
    cont.querySelectorAll('.ft-node.ft-dir.expanded').forEach(row => {
      if (row.dataset.path) ftExpandedDirs.add(row.dataset.path);
    });
  }

  async function cargarFileTree() {
    // Debounce: dos mutaciones rápidas no disparan renders concurrentes que se pisen.
    if (_ftRenderTimer) clearTimeout(_ftRenderTimer);
    _ftRenderTimer = setTimeout(_cargarFileTreeAhora, 60);
  }

  // Skeleton de carga del árbol: 8 filas shimmer de anchos variados. Solo se
  // muestra en el PRIMER fetch (árbol vacío); los refrescos por polling conservan
  // el árbol existente para no parpadear.
  const _FT_SKELETON_ANCHOS = [70, 52, 84, 60, 46, 76, 58, 66];
  function _ftSkeletonHTML() {
    return _FT_SKELETON_ANCHOS.map(w =>
      `<div class="skeleton" style="height:11px;width:${w}%;margin:6px 10px"></div>`
    ).join('');
  }

  async function _cargarFileTreeAhora() {
    _ftRenderTimer = null;
    const cont   = document.getElementById('file-tree-body');
    const titulo = document.getElementById('ft-project-name');
    if (!cont) return;

    _capturarExpansion();  // preservar expansión actual antes de reconstruir

    // Primera carga (árbol aún vacío) → skeleton mientras llega el fetch.
    if (!cont.querySelector('.ft-node')) cont.innerHTML = _ftSkeletonHTML();

    try {
      const [treeRes, gitData] = await Promise.all([
        fetch(`/api/projects/${_projectId}/files/tree`),
        fetch(`/api/projects/${_projectId}/files/git-status`)
          .then(r => (r.ok ? r.json() : { git: false, files: {} }))
          .catch(() => ({ git: false, files: {} })),
      ]);
      if (!treeRes.ok) throw new Error(`HTTP ${treeRes.status}`);
      const treeJson = await treeRes.json();
      const nuevoGit = (gitData && gitData.git) ? (gitData.files || {}) : {};

      // Refresh imperceptible (patrón _firmaDatos de home.js): si ni el árbol ni
      // el git-status cambiaron y ya hay nodos pintados, no reconstruir nada
      // (evita el rebuild completo cada 30s: cientos de .ft-node destruidos/recreados).
      const firma = JSON.stringify(treeJson) + '|' + JSON.stringify(nuevoGit);
      if (firma === _ftFirma && cont.querySelector('.ft-node')) return;
      _ftFirma = firma;

      fileTreeData = treeJson;
      gitStatusMap = nuevoGit;
      _ftFlatIndex = null;  // ÚNICO punto de invalidación del índice plano
      if (titulo) titulo.textContent = fileTreeData.name || 'archivos';
      cont.innerHTML = '';
      _ftStaggerIdx = 0;   // reiniciar el contador del stagger para este render
      cont.appendChild(_renderFTNodes(fileTreeData.children || [], 0));
      _ftStagger = false;  // one-shot: los próximos renders (polling) no escalonan
      _aplicarGitRollupCarpetas();
      // Re-marcar el archivo activo (el re-render borró la clase .activo)
      if (_aTab()) marcarArchivoActivoEnTree(_aTab());
      // Re-aplicar el filtro activo para que el polling no lo descarte.
      if (ftFilter) filtrarTree(ftFilter);
    } catch (err) {
      cont.innerHTML = `<div class="ft-empty">${esc(_t('Error: {msg}').replace('{msg}', err.message))}</div>`;
    }
  }

  function _renderFTNodes(nodes, depth) {
    const frag = document.createDocumentFragment();
    for (const n of nodes) frag.appendChild(_renderFTNode(n, depth));
    return frag;
  }

  // Índice plano único del árbol (spec §4.2). Lo consumen búsqueda-en-árbol (Fase 3)
  // y command palette (Fase 5). Se memoiza en _ftFlatIndex; cargarFileTree lo invalida.
  function _ftFlatten() {
    if (_ftFlatIndex) return _ftFlatIndex;
    const out = [];
    (function walk(nodes) {
      if (!nodes) return;
      for (const n of nodes) {
        out.push({ name: n.name, path: n.path, type: n.type });
        if (n.type === 'dir' && n.children) walk(n.children);
      }
    })(fileTreeData && fileTreeData.children);
    _ftFlatIndex = out;
    return _ftFlatIndex;
  }

  // ─── Command palette: estado interno (Fase 5) ──────────────────────
  let _cpOverlay    = null;  // elemento .cmd-palette-overlay montado, o null
  let _cpKeyHandler = null;  // listener capture instalado en document
  let _cpItems      = [];    // items filtrados actuales (orden = render)
  let _cpActiveIdx  = 0;     // índice resaltado en la lista

  // Fuzzy subsecuencia: ¿están todos los chars de `q` en `text` en orden?
  // Devuelve {score, positions:[idx...]} o null si no matchea.
  // Score: menor es mejor (premia matches contiguos y al inicio de palabra).
  function _cpFuzzy(q, text) {
    if (!q) return { score: 0, positions: [] };
    const lt = text.toLowerCase();
    const lq = q.toLowerCase();
    const positions = [];
    let ti = 0, score = 0, prev = -2;
    for (let qi = 0; qi < lq.length; qi++) {
      const ch = lq[qi];
      let found = -1;
      for (let i = ti; i < lt.length; i++) {
        if (lt[i] === ch) { found = i; break; }
      }
      if (found === -1) return null;
      // Penaliza gaps; premia contigüidad e inicio de palabra (tras '/' o '_' '-' '.')
      if (found === prev + 1) score += 1;
      else score += (found - prev) + 5;
      const before = found > 0 ? lt[found - 1] : '/';
      if (before === '/' || before === '_' || before === '-' || before === '.') score -= 3;
      positions.push(found);
      prev = found;
      ti = found + 1;
    }
    // Premia match temprano en el string
    score += positions[0];
    return { score, positions };
  }

  function _cpEsc(s) {
    return String(s).replace(/[&<>"']/g, ch => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
    ));
  }

  // Resalta los chars matcheados en `text` envolviéndolos en <span class="cpi-hl">.
  // `positions` son índices crecientes; escapa el resto contra XSS de paths.
  function _cpHighlight(text, positions) {
    if (!positions || !positions.length) return _cpEsc(text);
    const set = new Set(positions);
    let out = '';
    for (let i = 0; i < text.length; i++) {
      const c = _cpEsc(text[i]);
      out += set.has(i) ? `<span class="cpi-hl">${c}</span>` : c;
    }
    return out;
  }

  // ─── Command palette: registro de comandos (modo ">") ───────────────
  const CMD_REGISTRY = [
    {
      label: 'Guardar archivo',
      run() { JarvisEditor.guardarActivo(); }
    },
    {
      label: 'Nuevo archivo',
      run() {
        // Fallback a refrescar para que el usuario use el menú contextual.
        JarvisEditor.refrescarArbol();
      }
    },
    {
      label: 'Refrescar árbol de archivos',
      run() { JarvisEditor.refrescarArbol(); }
    },
    {
      label: 'Alternar panel del editor',
      run() { if (typeof window.togglePanel === 'function') window.togglePanel('editor'); }
    },
    {
      label: 'Alternar panel del orquestador',
      run() { if (typeof window.togglePanel === 'function') window.togglePanel('orquestador'); }
    },
    {
      label: 'Dividir editor: vertical (lado a lado)',
      run() { toggleSplit('vertical'); }
    },
    {
      label: 'Dividir editor: horizontal (apilado)',
      run() { toggleSplit('horizontal'); }
    },
    {
      label: 'Quitar división del editor',
      run() { unsplit(); }
    },
  ];

  // ─── Command palette: render + navegación ──────────────────────────
  function _cpCerrar() {
    if (_cpKeyHandler) {
      document.removeEventListener('keydown', _cpKeyHandler, true);
      _cpKeyHandler = null;
    }
    if (_cpOverlay) { _cpOverlay.remove(); _cpOverlay = null; }
    _cpItems = [];
    _cpActiveIdx = 0;
  }

  // Construye la lista filtrada según la query cruda del input.
  function _cpRefiltrar(raw) {
    const comando = raw.startsWith('>');
    const q = comando ? raw.slice(1).trim() : raw.trim();
    const scored = [];
    if (comando) {
      for (const cmd of CMD_REGISTRY) {
        const m = _cpFuzzy(q, cmd.label);
        if (m) scored.push({ kind: 'cmd', label: cmd.label, run: cmd.run, score: m.score, positions: m.positions });
      }
    } else {
      // _ftFlatten() devuelve [{name,path,type}] (Fase 1), memoizado. Solo archivos.
      for (const it of _ftFlatten()) {
        if (it.type !== 'file') continue;
        // Matchea contra el path completo, pero resalta sobre el path mostrado.
        const m = _cpFuzzy(q, it.path);
        if (m) scored.push({ kind: 'file', name: it.name, path: it.path, score: m.score, positions: m.positions });
      }
    }
    scored.sort((a, b) => a.score - b.score);
    _cpItems = scored.slice(0, 200);
    _cpActiveIdx = 0;
  }

  function _cpRenderLista(listEl) {
    if (!_cpItems.length) {
      listEl.innerHTML = '<div class="cmd-palette-empty">Sin resultados</div>';
      return;
    }
    listEl.innerHTML = _cpItems.map((it, i) => {
      const cls = i === _cpActiveIdx ? 'cmd-palette-item active' : 'cmd-palette-item';
      if (it.kind === 'cmd') {
        return `<div class="${cls}" data-idx="${i}"><span class="cpi-name">${_cpHighlight(it.label, it.positions)}</span></div>`;
      }
      return `<div class="${cls}" data-idx="${i}">` +
             `<span class="cpi-name">${_cpEsc(it.name)}</span>` +
             `<span class="cpi-path">${_cpHighlight(it.path, it.positions)}</span></div>`;
    }).join('');
    const act = listEl.querySelector('.cmd-palette-item.active');
    if (act) act.scrollIntoView({ block: 'nearest' });
  }

  function _cpEjecutar(item) {
    if (!item) return;
    _cpCerrar();
    if (item.kind === 'cmd') item.run();
    else JarvisEditor.abrirArchivo(item.path);
  }

  // modo: 'archivo' (default) abre vacío; 'comando' abre con ">" precargado.
  function _cpAbrir(modo) {
    _cpCerrar();  // idempotente: nunca dos overlays

    const overlay = document.createElement('div');
    overlay.className = 'cmd-palette-overlay';
    const prefill = modo === 'comando' ? '>' : '';
    overlay.innerHTML = `
      <div class="cmd-palette-modal">
        <input class="cmd-palette-input" type="text" spellcheck="false" autocomplete="off"
               placeholder="Buscá archivos…  (escribí > para comandos)">
        <div class="cmd-palette-list"></div>
      </div>`;
    document.body.appendChild(overlay);
    _cpOverlay = overlay;

    const input  = overlay.querySelector('.cmd-palette-input');
    const listEl = overlay.querySelector('.cmd-palette-list');

    const repintar = () => { _cpRefiltrar(input.value); _cpRenderLista(listEl); };
    input.value = prefill;
    repintar();

    input.addEventListener('input', repintar);

    overlay.addEventListener('click', e => { if (e.target === overlay) _cpCerrar(); });
    listEl.addEventListener('click', e => {
      const row = e.target.closest('.cmd-palette-item');
      if (!row) return;
      _cpEjecutar(_cpItems[parseInt(row.dataset.idx, 10)]);
    });

    // Navegación de teclado en CAPTURE: gana antes que cualquier otro handler
    // y nunca deja que la tecla burbujee a xterm o al handler global.
    _cpKeyHandler = function (e) {
      if (!_cpOverlay) return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (_cpItems.length) { _cpActiveIdx = (_cpActiveIdx + 1) % _cpItems.length; _cpRenderLista(listEl); }
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (_cpItems.length) { _cpActiveIdx = (_cpActiveIdx - 1 + _cpItems.length) % _cpItems.length; _cpRenderLista(listEl); }
      } else if (e.key === 'Tab') {
        e.preventDefault();
        const dir = e.shiftKey ? -1 : 1;
        if (_cpItems.length) { _cpActiveIdx = (_cpActiveIdx + dir + _cpItems.length) % _cpItems.length; _cpRenderLista(listEl); }
      } else if (e.key === 'Enter') {
        e.preventDefault();
        _cpEjecutar(_cpItems[_cpActiveIdx]);
      } else if (e.key === 'Escape') {
        e.preventDefault();
        e.stopImmediatePropagation();
        if (input.value) { input.value = ''; repintar(); }  // 1er Esc: limpia texto
        else _cpCerrar();                                     // 2do Esc: cierra
      }
    };
    document.addEventListener('keydown', _cpKeyHandler, true);

    input.focus();
  }

  // Caret en SVG inline (currentColor) — reemplaza el glifo ▸/▾ textual.
  const _FT_CARET_SVG =
    '<svg class="ft-caret-svg" width="10" height="10" viewBox="0 0 16 16" aria-hidden="true">' +
    '<path fill="currentColor" d="M6 4l4 4-4 4z"/></svg>';

  function _renderFTNode(node, depth) {
    const row = document.createElement('div');
    row.className     = 'ft-node ' + (node.type === 'dir' ? 'ft-dir' : 'ft-file');
    row.dataset.path  = node.path;
    row.dataset.type  = node.type;
    row.dataset.name  = node.name;
    row.style.paddingLeft = (8 + depth * 12) + 'px';

    // Stagger one-shot del primer render: setea --i (cap 20) → ob-pop-in escalonado.
    // El selector .ft-node[style*="--i"] (editor.css) solo anima cuando esta var
    // está presente, así que el polling (sin _ftStagger) no re-anima nada.
    if (_ftStagger) {
      row.style.setProperty('--i', Math.min(_ftStaggerIdx, _FT_STAGGER_CAP));
      _ftStaggerIdx++;
    }

    // Guía de indentación (1px por nivel); ayuda visual de jerarquía.
    let guides = '';
    for (let i = 0; i < depth; i++) {
      guides += `<span class="ft-indent-guide" style="left:${8 + i * 12 + 5}px"></span>`;
    }

    if (node.type === 'dir') {
      const expanded = ftExpandedDirs.has(node.path);
      if (expanded) row.classList.add('expanded');
      const _ic = expanded ? _svgIcon('folderOpen') : _svgIcon('folder');
      row.innerHTML = guides +
        `<span class="ft-caret">${_FT_CARET_SVG}</span>` +
        `<span class="ft-icon">${_ic}</span>` +
        `<span class="ft-name" data-i18n-skip>${esc(node.name)}</span>` +
        `<span class="ft-git-badge"></span>`;

      const childrenWrap = document.createElement('div');
      childrenWrap.className = 'ft-children' + (expanded ? '' : ' oculto');
      childrenWrap.appendChild(_renderFTNodes(node.children || [], depth + 1));

      row.addEventListener('click', e => {
        e.stopPropagation();
        const nowExpanded = row.classList.toggle('expanded');
        childrenWrap.classList.toggle('oculto', !nowExpanded);
        if (nowExpanded) ftExpandedDirs.add(node.path);
        else             ftExpandedDirs.delete(node.path);
      });

      const wrap = document.createElement('div');
      wrap.appendChild(row);
      wrap.appendChild(childrenWrap);
      return wrap;
    }

    // archivo
    row.innerHTML = guides +
      `<span class="ft-caret-spacer"></span>` +
      `<span class="ft-icon">${_svgIcon(_extKind(node.name))}</span>` +
      `<span class="ft-name" data-i18n-skip>${esc(node.name)}</span>` +
      `<span class="ft-git-badge"></span>`;
    // Badge de estado git en el slot canónico (§4.3, margin-left:auto).
    // Limpiar clases ft-git-* previas para evitar badges fantasma en re-render.
    row.classList.remove('ft-git-M', 'ft-git-U', 'ft-git-A', 'ft-git-D', 'ft-git-R', 'ft-git-C');
    const _gitSlot = row.querySelector('.ft-git-badge');
    if (_gitSlot) _gitSlot.textContent = '';
    {
      const code = gitStatusMap[node.path];
      if (code) {
        row.classList.add('ft-git-' + code);
        if (_gitSlot) {
          _gitSlot.textContent = code;
          // Leyenda descubrible por hover (M/U/A… son crípticos sin tooltip).
          _gitSlot.title = ({ M: 'Modificado', U: 'Sin trackear', A: 'Agregado',
                              D: 'Borrado', R: 'Renombrado', C: 'Copiado' })[code] || code;
        }
      }
    }
    row.addEventListener('click', e => {
      e.stopPropagation();
      abrirArchivoEnEditor(node.path);
    });
    return row;
  }

  // ─── Git rollup en carpetas (Fase 8) ────────────────────────────────
  // Prioridad del "peor" estado para propagar a carpetas: C > D > M > R > A > U
  const _GIT_PRIORIDAD = { C: 6, D: 5, M: 4, R: 3, A: 2, U: 1 };

  function _peorGit(a, b) {
    if (!a) return b;
    if (!b) return a;
    return (_GIT_PRIORIDAD[a] || 0) >= (_GIT_PRIORIDAD[b] || 0) ? a : b;
  }

  // Escapa un valor para usarlo dentro de un selector de atributo.
  function _gitCssEscape(v) {
    if (window.CSS && CSS.escape) return CSS.escape(v);
    return String(v).replace(/["\\\]]/g, '\\$&');
  }

  // Calcula recursivamente el peor estado git por carpeta y lo aplica al DOM.
  // Devuelve el peor código encontrado bajo `nodes` (o null).
  function _rollupNodos(nodes) {
    let peor = null;
    for (const n of nodes) {
      if (n.type === 'dir') {
        const sub = _rollupNodos(n.children || []);
        if (sub) {
          const fila = document.querySelector(
            `.ft-dir[data-path="${_gitCssEscape(n.path)}"]`
          );
          if (fila) {
            fila.classList.remove(
              'ft-git-M', 'ft-git-U', 'ft-git-A', 'ft-git-D', 'ft-git-R', 'ft-git-C'
            );
            fila.classList.add('ft-git-' + sub);
            const slot = fila.querySelector('.ft-git-badge');
            if (slot) slot.textContent = sub;
          }
          peor = _peorGit(peor, sub);
        }
      } else {
        peor = _peorGit(peor, gitStatusMap[n.path] || null);
      }
    }
    return peor;
  }

  function _aplicarGitRollupCarpetas() {
    if (!fileTreeData || !fileTreeData.children) return;
    _rollupNodos(fileTreeData.children);
  }

  // Refresca solo el estado git (sin reconstruir el árbol): limpia badges y re-pinta.
  async function refrescarGitStatus() {
    if (!_projectId) return;
    let gitData;
    try {
      const r = await fetch(`/api/projects/${_projectId}/files/git-status`);
      gitData = r.ok ? await r.json() : { git: false, files: {} };
    } catch (_) {
      gitData = { git: false, files: {} };
    }
    gitStatusMap = (gitData && gitData.git) ? (gitData.files || {}) : {};
    _ftFirma = '';   // git cambió fuera de _cargarFileTreeAhora → invalidar la firma para que el próximo poll reconstruya el árbol

    const cont = document.getElementById('file-tree-body');
    if (!cont) return;
    // Limpiar TODAS las clases/slots ft-git-* previas (evita badges fantasma).
    cont.querySelectorAll('.ft-node').forEach(row => {
      row.classList.remove(
        'ft-git-M', 'ft-git-U', 'ft-git-A', 'ft-git-D', 'ft-git-R', 'ft-git-C'
      );
      const slot = row.querySelector('.ft-git-badge');
      if (slot) slot.textContent = '';
    });
    // Re-pintar archivos
    cont.querySelectorAll('.ft-file').forEach(row => {
      const code = gitStatusMap[row.dataset.path];
      if (code) {
        row.classList.add('ft-git-' + code);
        const slot = row.querySelector('.ft-git-badge');
        if (slot) slot.textContent = code;
      }
    });
    // Re-aplicar rollup a carpetas
    _aplicarGitRollupCarpetas();
  }

  // ─── Filtro/búsqueda en el árbol (cliente) ──────────────────────────
  // Nota: `ftFilter` ya está declarado arriba (estado del contrato §4.2). Acá
  // solo agregamos el timer de debounce y la lógica de filtrado.
  let _ftFilterTimer = null;

  function filtrarTree(termRaw) {
    ftFilter = (termRaw || '').trim().toLowerCase();
    const body = document.getElementById('file-tree-body');
    if (!body) return;

    if (!ftFilter) {
      // limpiar: quitar clases y restaurar expansión normal
      body.querySelectorAll('.ft-node').forEach(n => {
        n.classList.remove('ft-match', 'ft-hidden');
      });
      body.querySelectorAll('.ft-children').forEach(c => c.classList.remove('ft-filter-open'));
      return;
    }

    // 1) Calcular set de paths que matchean + sus ancestros (para auto-expand)
    const flat = _ftFlatten();
    const visibles = new Set();      // paths a mostrar
    const matches  = new Set();      // paths que matchean directamente
    for (const item of flat) {
      if (item.name.toLowerCase().includes(ftFilter)) {
        matches.add(item.path);
        visibles.add(item.path);
        // sumar todos los ancestros
        const parts = item.path.split('/');
        for (let i = 1; i < parts.length; i++) {
          visibles.add(parts.slice(0, i).join('/'));
        }
      }
    }

    // 2) Aplicar al DOM: ocultar lo no visible, marcar matches, auto-expandir ancestros
    body.querySelectorAll('.ft-node').forEach(n => {
      const p = n.dataset.path;
      const visible = visibles.has(p);
      n.classList.toggle('ft-hidden', !visible);
      n.classList.toggle('ft-match', matches.has(p));
      if (visible && n.dataset.type === 'dir') {
        // expandir contenedor de hijos asociado
        const wrap = n.parentElement;
        const children = wrap?.querySelector(':scope > .ft-children');
        if (children) children.classList.add('ft-filter-open');
      }
    });
  }

  const _ftSearchInput = document.getElementById('ft-search-input');
  _ftSearchInput?.addEventListener('input', (e) => {
    const val = e.target.value;
    clearTimeout(_ftFilterTimer);
    _ftFilterTimer = setTimeout(() => filtrarTree(val), 120);
  });
  _ftSearchInput?.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      e.target.value = '';
      filtrarTree('');
      e.target.blur();
    }
  });

  // ─── Menú contextual del árbol (delegado) ──────────────────────────
  let _ftMenuEl = null;

  function _ftCerrarMenu() {
    _ftMenuEl?.remove();
    _ftMenuEl = null;
    document.removeEventListener('click', _ftCerrarMenu, true);
    document.removeEventListener('contextmenu', _ftCerrarMenu, true);
  }

  function _ftMostrarMenu(x, y, node) {
    _ftCerrarMenu();
    const path = node.dataset.path;
    const type = node.dataset.type;   // 'dir' | 'file'

    // contenedor "padre" donde crear cosas nuevas:
    // si es carpeta → dentro de ella; si es archivo → su carpeta
    const parentDir = type === 'dir'
      ? path
      : (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '');

    const items = [];
    if (type === 'dir') {
      items.push({ label: 'Nuevo archivo',  fn: () => _ftCrearInline(parentDir, 'file') });
      items.push({ label: 'Nueva carpeta',  fn: () => _ftCrearInline(parentDir, 'dir') });
      items.push({ sep: true });
    }
    items.push({ label: 'Renombrar',  fn: () => _ftRenombrarInline(node) });
    items.push({ label: 'Borrar',     danger: true, fn: () => _ftBorrar(path, type) });

    const menu = document.createElement('div');
    menu.className = 'ft-ctx-menu';
    for (const it of items) {
      if (it.sep) {
        const s = document.createElement('div');
        s.className = 'ft-ctx-sep';
        menu.appendChild(s);
        continue;
      }
      const el = document.createElement('div');
      el.className = 'ft-ctx-item' + (it.danger ? ' danger' : '');
      el.textContent = it.label;
      el.addEventListener('click', () => { _ftCerrarMenu(); it.fn(); });
      menu.appendChild(el);
    }

    // x/y vienen del evento (píxeles de PANTALLA) y left/top se escriben en píxeles
    // CSS, que la Escala de la app vuelve a multiplicar: sin traducir, el menú
    // aparece corrido respecto del click. Ver shared/escala.js.
    const zx = window.JarvisEscala?.aCss?.(x) ?? x;
    const zy = window.JarvisEscala?.aCss?.(y) ?? y;
    menu.style.left = zx + 'px';
    menu.style.top  = zy + 'px';
    document.body.appendChild(menu);
    _ftMenuEl = menu;

    // reposicionar si se sale de la ventana (el rect ya viene en px de pantalla)
    const r = menu.getBoundingClientRect();
    if (r.right > window.innerWidth)  menu.style.left = (zx - (window.JarvisEscala?.aCss?.(r.width) ?? r.width))  + 'px';
    if (r.bottom > window.innerHeight) menu.style.top = (zy - (window.JarvisEscala?.aCss?.(r.height) ?? r.height)) + 'px';

    // cerrar al click/contextmenu fuera (capture para ganar al delegado)
    setTimeout(() => {
      document.addEventListener('click', _ftCerrarMenu, true);
      document.addEventListener('contextmenu', _ftCerrarMenu, true);
    }, 0);
  }

  document.getElementById('file-tree-body')?.addEventListener('contextmenu', (e) => {
    const node = e.target.closest('.ft-node');
    if (!node) return;
    e.preventDefault();
    e.stopPropagation();
    _ftMostrarMenu(e.clientX, e.clientY, node);
  });

  // ─── Create / Rename inline ─────────────────────────────────────────

  // Helper genérico: pinta un <input> en el árbol, resuelve con el valor o null (cancel).
  function _ftInputInline(host, valorInicial) {
    return new Promise((resolve) => {
      ftEditing = true;
      const input = document.createElement('input');
      input.className = 'ft-inline';
      input.type = 'text';
      input.value = valorInicial || '';
      input.spellcheck = false;
      input.autocomplete = 'off';
      host.appendChild(input);
      input.focus();
      // seleccionar el nombre sin la extensión si es archivo con punto
      const dot = input.value.lastIndexOf('.');
      if (dot > 0) input.setSelectionRange(0, dot);
      else input.select();

      let cerrado = false;
      const cerrar = (valor) => {
        if (cerrado) return;
        cerrado = true;
        ftEditing = false;
        input.remove();
        resolve(valor);
      };
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter')  { e.preventDefault(); cerrar(input.value.trim()); }
        if (e.key === 'Escape') { e.preventDefault(); cerrar(null); }
      });
      input.addEventListener('blur', () => cerrar(input.value.trim() || null));
    });
  }

  // Crear archivo o carpeta dentro de parentDir ('' = raíz)
  async function _ftCrearInline(parentDir, tipo) {
    const body = document.getElementById('file-tree-body');
    if (!body) return;
    const nombre = await _ftInputInline(body, '');
    if (!nombre) return;
    const path = parentDir ? parentDir + '/' + nombre : nombre;

    try {
      let res;
      if (tipo === 'dir') {
        res = await fetch(`/api/projects/${_projectId}/files/mkdir`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path }),
        });
      } else {
        // archivo nuevo = guardar contenido vacío (reusa /files/save, que crea padres)
        res = await fetch(`/api/projects/${_projectId}/files/save`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path, content: '' }),
        });
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      await JarvisEditor.refrescarArbol();
      if (tipo === 'file') JarvisEditor.abrirArchivo(path);
    } catch (err) {
      toast(`Error creando: ${err.message}`, 'error');
    }
  }

  // Re-mapea los tabs (en TODOS los grupos) + recrea el modelo Monaco compartido
  // cuando se renombra un archivo ABIERTO. El modelo es único por URI: se dispone
  // una sola vez y se recrea con la URI nueva, conservando contenido.
  function _ftRemapearTab(viejo, nuevo) {
    if (!_pathReferenciado(viejo)) return;

    // Tomar contenido/lenguaje de cualquier grupo que lo tenga (mismo buffer).
    let muestra = null;
    for (const g of editorGroups) {
      const t = g?.tabs?.get(viejo);
      if (t) { muestra = t; break; }
    }
    if (!muestra) return;

    // disponer el modelo viejo y crear el nuevo con la URI correcta
    const uriVieja = monaco.Uri.parse('inmemory://jarvis/' + viejo);
    monaco.editor.getModel(uriVieja)?.dispose();
    const uriNueva = monaco.Uri.parse('inmemory://jarvis/' + nuevo);
    if (!monaco.editor.getModel(uriNueva)) {
      monaco.editor.createModel(muestra.content, muestra.language, uriNueva);
    }
    // LRU: el path viejo deja de existir, el nuevo entra.
    const idxLRU = modelLRU.indexOf(viejo);
    if (idxLRU !== -1) modelLRU.splice(idxLRU, 1);
    _touchModel(nuevo);

    // Re-mapear el tab en cada grupo que lo tenga (preserva orden por re-set).
    for (const g of editorGroups) {
      if (!g || !g.tabs.has(viejo)) continue;
      const tab = g.tabs.get(viejo);
      g.tabs.delete(viejo);
      g.tabs.set(nuevo, tab);
      const vs = g.viewStates.get(viejo);
      if (vs) { g.viewStates.delete(viejo); g.viewStates.set(nuevo, vs); }
      if (g.activeTab === viejo) {
        g.activeTab = null;          // evitar guardar viewState bajo el path viejo
        setActiveTab(nuevo, g);      // re-engancha el modelo nuevo en este grupo
      }
    }
    renderTabs();
  }

  // Renombrar (sirve también para carpetas)
  async function _ftRenombrarInline(node) {
    const body = document.getElementById('file-tree-body');
    if (!body) return;
    const src = node.dataset.path;
    const tipo = node.dataset.type;
    const nameSpan = node.querySelector('.ft-name');
    const valorInicial = node.dataset.name || src.split('/').pop();

    // input inline reemplazando el nombre dentro de la fila
    const original = nameSpan ? nameSpan.textContent : valorInicial;
    if (nameSpan) nameSpan.textContent = '';
    const host = nameSpan || body;
    const nuevoNombre = await _ftInputInline(host, valorInicial);
    if (nameSpan && !nuevoNombre) nameSpan.textContent = original;
    if (!nuevoNombre || nuevoNombre === valorInicial) return;

    const parentDir = src.includes('/') ? src.slice(0, src.lastIndexOf('/')) : '';
    const dst = parentDir ? parentDir + '/' + nuevoNombre : nuevoNombre;

    try {
      const res = await fetch(`/api/projects/${_projectId}/files/rename`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ src, dst }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      if (tipo === 'dir') {
        // Rename de carpeta: re-mapear TODOS los tabs descendientes abiertos en
        // CUALQUIER grupo + mover sus entradas de fileCache. Prefijo exacto
        // (src + '/') para no afectar carpetas hermanas (p.ej. "src2"). SNAPSHOT
        // de los paths antes de mutar (no iterar y mutar a la vez).
        const prefijo = src + '/';
        const descendientes = new Set();
        for (const g of editorGroups) {
          for (const p of (g?.tabs?.keys() || [])) {
            if (p === src || p.startsWith(prefijo)) descendientes.add(p);
          }
        }
        for (const p of (fileCache.keys ? fileCache.keys() : [])) {
          if (p === src || p.startsWith(prefijo)) descendientes.add(p);
        }
        for (const p of descendientes) {
          const nuevo = dst + p.slice(src.length);   // src.length cubre src y src/...
          // Mover la cache del descendiente (rename = mismo contenido) ANTES de
          // remapear, sin disponer el modelo (lo hace _ftRemapearTab).
          const _cd = fileCache.get(p);
          if (_cd) { fileCache.set(nuevo, { ..._cd, ts: Date.now() }); }
          fileCache.delete(p);
          if (_pathReferenciado(p)) {
            // Abierto en algún grupo → re-mapea tab + recrea modelo + viewState + LRU.
            _ftRemapearTab(p, nuevo);
          } else {
            // Cerrado pero cacheado: disponer el modelo viejo y limpiar su LRU.
            monaco.editor.getModel(_ftUri(p))?.dispose();
            const idxLRU = modelLRU.indexOf(p);
            if (idxLRU !== -1) modelLRU.splice(idxLRU, 1);
            _olvidarViewStates(p);
          }
        }
      } else {
        // Fase 4: mover la cache del path viejo al nuevo (rename = mismo contenido)
        const _cv = fileCache.get(src);
        if (_cv) { fileCache.set(dst, { ..._cv, ts: Date.now() }); }
        _invalidarCache(src);
        // Must-fix: archivo abierto → re-mapear tab + modelo
        if (JarvisEditor.estaAbierto(src)) {
          _ftRemapearTab(src, dst);
        }
      }
      await JarvisEditor.refrescarArbol();
    } catch (err) {
      toast(`Error renombrando: ${err.message}`, 'error');
      await JarvisEditor.refrescarArbol();
    }
  }

  // ─── Borrar archivo o carpeta (desde menú contextual) ──────────────
  async function _ftBorrar(path, tipo) {
    const etiqueta = tipo === 'dir' ? 'la carpeta' : 'el archivo';
    if (!(await confirmar(
      _t('¿Borrar {el archivo|la carpeta} "{path}"? Esta acción no se puede deshacer.')
        .replace('{el archivo|la carpeta}', etiqueta)
        .replace('{the file|the folder}', tipo === 'dir' ? 'the folder' : 'the file')
        .replace('{path}', path),
      { peligro: true, confirmText: 'Eliminar' }))) return;

    try {
      let res;
      if (tipo === 'dir') {
        res = await fetch(`/api/projects/${_projectId}/files/dir?path=${encodeURIComponent(path)}`, {
          method: 'DELETE',
        });
      } else {
        res = await fetch(`/api/projects/${_projectId}/files?path=${encodeURIComponent(path)}`, {
          method: 'DELETE',
        });
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }

      if (tipo === 'dir') {
        // Must-fix: cerrar TODOS los tabs cuyo path cuelga de la carpeta borrada,
        // en CUALQUIER grupo (split). El archivo ya no existe → cerrar sin prompt.
        const prefijo = path.endsWith('/') ? path : path + '/';
        const descendientes = new Set();
        for (const g of editorGroups) {
          for (const p of (g?.tabs?.keys() || [])) {
            if (p === path || p.startsWith(prefijo)) descendientes.add(p);
          }
        }
        for (const p of descendientes) {
          for (const g of editorGroups) {
            const tab = g?.tabs?.get(p);
            if (tab) { tab.dirty = false; cerrarTab(p, g); }  // evitar el confirm
          }
          _ftInvalidarCaches(p);        // helper de Fase 4 (no-op si aún no existe)
        }
      } else {
        // archivo: cerrar su tab en cada grupo donde esté abierto
        for (const g of editorGroups) {
          const tab = g?.tabs?.get(path);
          if (tab) { tab.dirty = false; cerrarTab(path, g); }
        }
        _ftInvalidarCaches(path);
      }

      await JarvisEditor.refrescarArbol();
    } catch (err) {
      toast(`Error borrando: ${err.message}`, 'error');
    }
  }

  // Invalidación de caches del editor (fileCache/viewStates/modelo/LRU de Fase 4).
  // Delega en _invalidarCache (invalidación completa). Defensivo por si se llama
  // antes de que Monaco esté cargado.
  function _ftInvalidarCaches(path) {
    try { _invalidarCache(path); } catch (_) {}
  }

  function iniciarPollingFileTree() {
    detenerPollingFileTree();
    fileTreeTimer = setInterval(() => {
      // Gate (spec §4.2): no refrescar si el panel está oculto, si hay edición
      // inline abierta, o si hay un filtro de búsqueda activo (evita pisar el estado).
      if (window.editorVisible && !ftEditing && !ftFilter) cargarFileTree();
    }, 30_000);
  }

  function detenerPollingFileTree() {
    if (fileTreeTimer) clearInterval(fileTreeTimer);
    fileTreeTimer = null;
  }

  document.getElementById('ft-refresh-btn')?.addEventListener('click', cargarFileTree);

  // ─── Subir archivos / carpetas / zip ───────────────────────────────────────
  const FT_IGNORE_DIRS = new Set([
    '.git', '.worktrees', '__pycache__', 'node_modules',
    'venv', '.venv', '.workspace', '.idea', '.vscode',
    'dist', 'build', '.next', '.cache',
  ]);

  // true si algún segmento de la ruta relativa está en IGNORE_DIRS
  function _ftRutaIgnorada(rel) {
    return rel.split('/').some(seg => FT_IGNORE_DIRS.has(seg));
  }

  async function subirArchivosAlProyecto(archivos) {
    if (!archivos || !archivos.length) return;

    // Filtrar carpetas ignoradas en cliente (no mandar miles de parts de .git/node_modules/venv…)
    const items = [];
    for (const f of archivos) {
      const rel = (f.webkitRelativePath || f.name || '').replace(/\\/g, '/');
      if (rel && _ftRutaIgnorada(rel)) continue;
      items.push({ file: f, rel });
    }
    if (!items.length) return;

    // Batching cliente: tandas de 200 para no armar FormData gigantes
    const BATCH = 200;
    let subidosTotal = 0;
    const rechazadosTotal = [];
    try {
      for (let i = 0; i < items.length; i += BATCH) {
        const tanda = items.slice(i, i + BATCH);
        const fd = new FormData();
        for (const it of tanda) {
          fd.append('files', it.file);          // mismo bucle → orden alineado
          fd.append('rel_paths', it.rel || it.file.name);
        }
        const res = await fetch(`/api/projects/${_projectId}/files/upload`, {
          method: 'POST',
          body:   fd,
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || `HTTP ${res.status}`);
        }
        const data = await res.json();
        // Fase 4: invalidar cache de cada archivo subido (su contenido cambió en disco)
        for (const p of (data.subidos || [])) _invalidarCache(p);
        subidosTotal += (data.subidos?.length || 0);
        if (data.rechazados?.length) rechazadosTotal.push(...data.rechazados);
      }
      await cargarFileTree();
      if (rechazadosTotal.length) {
        const motivos = rechazadosTotal.map(r => `${r.archivo}: ${r.motivo}`).join(', ');
        toast(_t('Subidos: {n}. Algunos no se subieron — {m}').replace('{n}', subidosTotal).replace('{m}', motivos), 'warning');
      }
    } catch (err) {
      toast(`Error subiendo archivos: ${err.message}`, 'error');
    }
  }

  // Sube un único .zip al endpoint de extracción server-side
  async function subirZipAlProyecto(file) {
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    try {
      const res = await fetch(`/api/projects/${_projectId}/files/upload-zip`, {
        method: 'POST',
        body:   fd,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      for (const p of (data.subidos || [])) _invalidarCache(p);
      await cargarFileTree();
      if (data.rechazados?.length) {
        const motivos = data.rechazados.map(r => `${r.archivo}: ${r.motivo}`).join(', ');
        toast(_t('Extraídos: {n}. Algunos se omitieron — {m}').replace('{n}', data.subidos?.length || 0).replace('{m}', motivos), 'warning');
      }
    } catch (err) {
      toast(`Error extrayendo zip: ${err.message}`, 'error');
    }
  }

  // Mini-menú del botón + : archivos / carpeta / zip
  (() => {
    const btn  = document.getElementById('ft-upload-btn');
    const menu = document.getElementById('ft-upload-menu');
    if (!btn || !menu) return;

    const fileInput = document.getElementById('ft-upload-input');
    const dirInput  = document.getElementById('ft-upload-dir-input');
    const zipInput  = document.getElementById('ft-upload-zip-input');

    const cerrar = () => { menu.hidden = true; };

    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
    });
    document.addEventListener('click', (e) => {
      if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) cerrar();
    });

    menu.addEventListener('click', (e) => {
      const act = e.target?.dataset?.act;
      if (!act) return;
      cerrar();
      if (act === 'files') fileInput?.click();
      else if (act === 'dir') dirInput?.click();
      else if (act === 'zip') zipInput?.click();
    });

    fileInput?.addEventListener('change', async (e) => {
      await subirArchivosAlProyecto([...e.target.files]);
      e.target.value = '';
    });
    dirInput?.addEventListener('change', async (e) => {
      await subirArchivosAlProyecto([...e.target.files]); // traen webkitRelativePath
      e.target.value = '';
    });
    zipInput?.addEventListener('change', async (e) => {
      const f = e.target.files?.[0];
      if (f) await subirZipAlProyecto(f);
      e.target.value = '';
    });
  })();

  // Recorre un FileSystemEntry (archivo o carpeta) y acumula File con webkitRelativePath sintético.
  // Drena el DirectoryReader hasta vacío (readEntries devuelve tandas de ~100).
  function _ftLeerEntry(entry, prefijo, acc) {
    return new Promise((resolve) => {
      if (entry.isFile) {
        entry.file((file) => {
          const rel = prefijo ? `${prefijo}/${file.name}` : file.name;
          if (!_ftRutaIgnorada(rel)) {
            try { Object.defineProperty(file, 'webkitRelativePath', { value: rel }); } catch (_) {}
            acc.push(file);
          }
          resolve();
        }, () => resolve());
      } else if (entry.isDirectory) {
        const nombreDir = prefijo ? `${prefijo}/${entry.name}` : entry.name;
        if (_ftRutaIgnorada(nombreDir)) { resolve(); return; }
        const reader = entry.createReader();
        const hijos = [];
        const drenar = () => {
          reader.readEntries(async (tanda) => {
            if (!tanda.length) {
              await Promise.all(hijos.map(h => _ftLeerEntry(h, nombreDir, acc)));
              resolve();
              return;
            }
            hijos.push(...tanda);
            drenar(); // seguir leyendo: una sola llamada NO devuelve todo
          }, () => resolve());
        };
        drenar();
      } else {
        resolve();
      }
    });
  }

  // Drag&drop sobre todo el panel del editor (file tree + Monaco)
  (() => {
    const panel = document.getElementById('panel-editor');
    if (!panel) return;
    panel.addEventListener('dragover', (e) => {
      if (e.dataTransfer.types?.includes('Files')) {
        e.preventDefault();
        panel.classList.add('drag-over');
        panel.querySelectorAll('.monaco-placeholder').forEach(ph => { ph.dataset.dropHint = _t(' · soltá para subir archivo'); });
      }
    });
    panel.addEventListener('dragleave', (e) => {
      if (!panel.contains(e.relatedTarget)) panel.classList.remove('drag-over');
    });
    panel.addEventListener('drop', async (e) => {
      const dtItems = e.dataTransfer.items;
      if (!dtItems?.length && !e.dataTransfer.files?.length) return;
      e.preventDefault();
      panel.classList.remove('drag-over');

      // webkitGetAsEntry conserva la estructura de carpetas arrastradas.
      const entries = [];
      if (dtItems?.length) {
        for (const it of dtItems) {
          const entry = it.webkitGetAsEntry?.();
          if (entry) entries.push(entry);
        }
      }
      if (entries.length) {
        const acc = [];
        for (const entry of entries) await _ftLeerEntry(entry, '', acc);
        await subirArchivosAlProyecto(acc);
      } else if (e.dataTransfer.files?.length) {
        // Fallback: navegador sin webkitGetAsEntry → solo archivos sueltos
        await subirArchivosAlProyecto([...e.dataTransfer.files]);
      }
    });
  })();

  window.addEventListener('jarvis:lang', () => {
    document.querySelectorAll('.monaco-placeholder').forEach(ph => { ph.dataset.dropHint = _t(' · soltá para subir archivo'); });
  });

  // ─── Borrar archivo con tecla Supr (Delete) ────────────────────────────────
  // Solo dispara cuando hay un archivo seleccionado en el tree Y el foco NO
  // está en Monaco, ni en un input/textarea — para no romper la edición.
  document.addEventListener('keydown', async (e) => {
    if (e.key !== 'Delete') return;
    if (!window.editorVisible) return;
    if (editorGroups.some(g => g?.editor?.hasTextFocus?.())) return;
    const tag = e.target?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || e.target?.isContentEditable) return;

    const nodo = document.querySelector('#file-tree-body .ft-file.activo');
    if (!nodo) return;
    const path = nodo.dataset.path;
    if (!path) return;

    e.preventDefault();
    if (!(await confirmar(
      _t('¿Borrar el archivo "{path}"? Esta acción no se puede deshacer.').replace('{path}', path),
      { peligro: true, confirmText: 'Eliminar' }))) return;

    try {
      const res = await fetch(`/api/projects/${_projectId}/files?path=${encodeURIComponent(path)}`, {
        method: 'DELETE',
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      // Cerrar el tab en cada grupo donde esté abierto e invalidar su cache
      for (const g of editorGroups) {
        if (g?.tabs?.has(path)) { g.tabs.get(path).dirty = false; cerrarTab(path, g); }
      }
      _invalidarCache(path);
      await cargarFileTree();
    } catch (err) {
      toast(_t('Error: {msg}').replace('{msg}', err.message), 'error');
    }
  });

  // ─── Monaco lazy load ───────────────────────────────────────────────

  // Crea (si no existe) la instancia Monaco de un grupo y cablea sus listeners.
  // Idempotente: si g.editor ya existe, no hace nada. Asume Monaco ya cargado.
  function crearEditorEnGrupo(g) {
    if (!g || g.editor) return g?.editor || null;
    g.editor = monaco.editor.create(g.host, {
      value:         '',
      language:      'plaintext',
      theme:         'jarvis-dark',
      automaticLayout: true,
      fontSize:      13,
      fontFamily:    "'Cascadia Code', 'JetBrains Mono', Consolas, monospace",
      minimap:       { enabled: false },
      scrollBeyondLastLine: false,
      tabSize:       2,
    });

    // Foco → este grupo pasa a ser el activo.
    g.editor.onDidFocusEditorWidget(() => {
      const idx = editorGroups.indexOf(g);
      if (idx >= 0 && idx !== activeGroupIdx) setActiveGroup(idx);
    });

    // Posición del cursor → footer compartido (solo si es el grupo activo).
    g.editor.onDidChangeCursorPosition(e => {
      if (g !== _grupoActivo()) return;
      const elPos = document.getElementById('mf-pos');
      if (elPos) elPos.textContent = `Ln ${e.position.lineNumber}, Col ${e.position.column}`;
    });

    // Edición → marca dirty el tab activo de ESTE grupo. El modelo es compartido
    // por URI, así que el contenido es el mismo buffer en ambos grupos; sincronizar
    // el flag dirty/content del tab homónimo en el otro grupo.
    g.editor.onDidChangeModelContent(() => {
      const at = g.activeTab;
      if (!at) return;
      const tab = g.tabs.get(at);
      if (!tab) return;
      tab.dirty   = true;
      tab.content = g.editor.getValue();
      const otro = editorGroups[_otroIdx(g)];
      const tabOtro = otro?.tabs?.get(at);
      if (tabOtro) { tabOtro.dirty = true; tabOtro.content = tab.content; }
      renderTabs();
    });

    // Ctrl/Cmd+S → guardar el activo del grupo activo.
    g.editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, guardarArchivoActivo);

    return g.editor;
  }

  // ─── Tema de Monaco derivado de los tokens --ob-* (Obsidian Glass) ──────
  // Monaco exige colores en hex estricto (#RRGGBB / #RRGGBBAA). getComputedStyle
  // resuelve los tokens oklch a oklch(...)/rgb(...) según el browser, y Monaco
  // rechaza oklch. Normalizamos CUALQUIER color CSS a hex usando el canvas 2D
  // (su fillStyle convierte el color computado a #rrggbb o rgba()).
  const _canvasCtx = (() => {
    try { return document.createElement('canvas').getContext('2d'); }
    catch (_) { return null; }
  })();

  // rgb(a) → #rrggbb[aa]. Acepta enteros y porcentajes.
  function _rgbAHex(rgb) {
    const m = rgb.match(/rgba?\(([^)]+)\)/i);
    if (!m) return null;
    const parts = m[1].split(/[,\/]/).map(s => s.trim()).filter(Boolean);
    const toByte = (v) => {
      if (v.endsWith('%')) return Math.round(parseFloat(v) * 2.55);
      return Math.round(parseFloat(v));
    };
    const r = toByte(parts[0]), g = toByte(parts[1]), b = toByte(parts[2]);
    const h = (n) => Math.max(0, Math.min(255, n)).toString(16).padStart(2, '0');
    let out = '#' + h(r) + h(g) + h(b);
    if (parts[3] != null) {
      const a = parts[3].endsWith('%') ? parseFloat(parts[3]) / 100 : parseFloat(parts[3]);
      out += Math.round(Math.max(0, Math.min(1, a)) * 255).toString(16).padStart(2, '0');
    }
    return out;
  }

  // Convierte cualquier color CSS (token oklch resuelto, color-mix, hex, rgb) a
  // hex que Monaco acepte. Si no se puede resolver, devuelve el fallback.
  function _aHexMonaco(color, fallback) {
    if (!color) return fallback;
    color = color.trim();
    if (/^#[0-9a-f]{3,8}$/i.test(color)) return color;
    if (_canvasCtx) {
      try {
        _canvasCtx.fillStyle = '#000';          // reset
        _canvasCtx.fillStyle = color;            // el browser normaliza
        const norm = _canvasCtx.fillStyle;       // '#rrggbb' o 'rgba(...)'
        if (/^#[0-9a-f]{3,8}$/i.test(norm)) return norm;
        const hex = _rgbAHex(norm);
        if (hex) return hex;
      } catch (_) { /* color inválido para el canvas */ }
    }
    return fallback;
  }

  function _temaMonacoDesdeTokens() {
    const css = getComputedStyle(document.documentElement);
    const tok = (n, fb) => (css.getPropertyValue(n) || '').trim() || fb;
    // Resolver tokens → hex. Solo el "chrome" del editor; la SINTAXIS (rules) se
    // hereda intacta de vs-dark (rules: []).
    const bg     = _aHexMonaco(tok('--ob-bg-void', '#0b0b0b'), '#0b0b0b');
    const fg     = _aHexMonaco(tok('--ob-fg-1', '#e2e2e2'), '#e2e2e2');
    const line   = _aHexMonaco(tok('--ob-fg-3', '#3a3a3a'), '#3a3a3a');
    const accent = _aHexMonaco(tok('--ob-accent', '#7c3aed'), '#7c3aed');
    const cursor = _aHexMonaco(tok('--ob-accent-fg', '#a78bfa'), '#a78bfa');
    // Selección: acento al ~27% sobre el fondo (color-mix resuelto a hex).
    const seleccion = _aHexMonaco(
      tok('--ob-accent-24', '') ||
        `color-mix(in oklch, ${tok('--ob-accent', '#7c3aed')} 27%, transparent)`,
      '#7c3aed44'
    );
    // Línea activa: un escalón por encima del fondo (bg-1).
    const lineaActiva = _aHexMonaco(tok('--ob-bg-1', '#141414'), '#141414');
    return {
      base:    'vs-dark',
      inherit: true,
      rules:   [],   // sintaxis intacta (keywords/strings/etc. de vs-dark)
      colors:  {
        'editor.background':                 bg,
        'editor.foreground':                 fg,
        'editorLineNumber.foreground':       line,
        'editorLineNumber.activeForeground': accent,
        'editor.selectionBackground':        seleccion,
        'editor.lineHighlightBackground':    lineaActiva,
        'editorCursor.foreground':           cursor,
      },
    };
  }

  // Overlay de carga de Monaco: spinner cónico centrado sobre el contenedor del
  // editor mientras descarga el bundle desde el CDN. Idempotente; se retira en
  // _ocultarMonacoLoading().
  function _mostrarMonacoLoading() {
    const cont = document.getElementById('monaco-container') ||
                 editorGroups[0]?.host?.closest('.monaco-container');
    if (!cont || document.getElementById('monaco-loading-overlay')) return;
    const ov = document.createElement('div');
    ov.id = 'monaco-loading-overlay';
    ov.style.cssText =
      'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;' +
      'background:var(--ob-bg-void);z-index:3';
    ov.innerHTML = '<div class="ob-spinner lg"></div>';
    if (getComputedStyle(cont).position === 'static') cont.style.position = 'relative';
    cont.appendChild(ov);
  }
  function _ocultarMonacoLoading() {
    document.getElementById('monaco-loading-overlay')?.remove();
  }

  function cargarMonacoLazy() {
    _initGrupos();
    if (monacoLoaded) return Promise.resolve();
    // Promesa única: si el load ya está en vuelo, devolvemos la MISMA para que
    // todos los callers AWAITeen el mismo cold-load (sin tocar monaco a medias).
    if (_monacoPromise) return _monacoPromise;
    monacoLoading = true;
    _mostrarMonacoLoading();
    _monacoPromise = (async () => {
      try {
        await _cargarScript('https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs/loader.min.js');
        require.config({ paths: { 'vs': 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs' }});
        await new Promise((resolve, reject) => {
          require(['vs/editor/editor.main'], () => resolve(), reject);
        });

        monaco.editor.defineTheme('jarvis-dark', _temaMonacoDesdeTokens());

        crearEditorEnGrupo(editorGroups[0]);

        monacoLoaded = true;
      } catch (err) {
        console.error('Error cargando Monaco:', err);
        _monacoPromise = null;   // permitir reintentar el load si falló
      } finally {
        monacoLoading = false;
        _ocultarMonacoLoading();
      }
    })();
    return _monacoPromise;
  }

  // ─── Split view: grupo activo, toggle, unsplit ──────────────────────
  function _relayout() { editorGroups.forEach(g => g?.editor?.layout?.()); }

  function setActiveGroup(idx) {
    if (idx !== 0 && idx !== 1) return;
    if (!editorGroups[idx]) return;
    activeGroupIdx = idx;
    document.querySelectorAll('.monaco-group').forEach((el) => {
      el.classList.toggle('group-active', Number(el.dataset.group) === activeGroupIdx);
    });
    // El footer/breadcrumb son únicos y reflejan el grupo activo.
    const g = _grupoActivo();
    renderTabs();
    renderBreadcrumb(g?.activeTab || null);
    marcarArchivoActivoEnTree(g?.activeTab || null);
    const elLang = document.getElementById('mf-lang');
    if (elLang) {
      const t = g?.activeTab ? g.tabs.get(g.activeTab) : null;
      elLang.textContent = t ? (t.language || 'plaintext') : '—';
    }
  }

  // Activa el split en la orientación dada ('vertical' = lado a lado,
  // 'horizontal' = apilado). Si ya está activo en esa orientación → unsplit.
  async function toggleSplit(orientation = 'vertical') {
    await cargarMonacoLazy();
    _initGrupos();

    if (splitActivo && splitOrientacion === orientation) { unsplit(); return; }

    splitOrientacion = orientation;
    const split = document.getElementById('monaco-split');
    const sep   = document.getElementById('monaco-splitter');
    const dom1  = document.getElementById('monaco-group-1');
    if (!split || !dom1) return;

    if (!editorGroups[1]) {
      editorGroups[1] = _nuevoGrupo(
        document.getElementById('monaco-editor-host-1'),
        document.getElementById('monaco-placeholder-1'),
      );
      _cablearDropGrupos();
    }

    dom1.hidden = false;
    if (sep) sep.hidden = false;
    split.classList.add('is-split');
    split.classList.toggle('split-horizontal', orientation === 'horizontal');
    splitActivo = true;

    crearEditorEnGrupo(editorGroups[1]);
    const seed = editorGroups[0].activeTab;
    if (seed) await abrirArchivoEnEditor(seed, editorGroups[1]);

    setActiveGroup(1);
    _relayout();
    _sesGuardar();
  }

  // Colapsa el split: vuelca todo al grupo 0 y oculta el grupo 1.
  function unsplit() {
    _initGrupos();
    const split = document.getElementById('monaco-split');
    const sep   = document.getElementById('monaco-splitter');
    const dom1  = document.getElementById('monaco-group-1');

    const g1 = editorGroups[1];
    if (g1) {
      for (const path of [...g1.tabs.keys()]) {
        g1.tabs.delete(path);
        g1.viewStates.delete(path);
      }
      g1.activeTab = null;
      g1.editor?.setModel(null);
    }
    // Tras quitar las refs del grupo 1, disponer modelos que ya nadie use.
    _evictModelsIfNeeded();

    if (dom1) dom1.hidden = true;
    if (sep) sep.hidden = true;
    split?.classList.remove('is-split', 'split-horizontal');
    splitActivo = false;

    setActiveGroup(0);
    _relayout();
    _sesGuardar();
  }

  // Reset COMPLETO del estado del editor al cambiar de proyecto. Limpia tabs/
  // viewStates de TODOS los grupos, vacía fileCache, dispone TODOS los modelos
  // Monaco inmemory://jarvis/* y vacía el LRU. Robusto si Monaco aún no cargó
  // (editores null) y si monaco/getModels no están disponibles todavía.
  function _resetEditorState() {
    // Colapsar a un solo grupo (vacía y oculta el grupo 1) antes de limpiar g0.
    if (splitActivo) unsplit();
    _initGrupos();

    // Re-armar el stagger: el primer render del árbol del nuevo proyecto escalona.
    _ftStagger = true;

    // Vaciar tabs/viewStates y resetear la UI de cada grupo.
    for (const g of editorGroups) {
      if (!g) continue;
      g.tabs.clear();
      g.activeTab = null;
      g.viewStates.clear();
      // Soltar el modelo del editor (si Monaco ya cargó y el editor existe).
      g.editor?.setModel?.(null);
      // Placeholder visible, host de Monaco oculto, vista binaria oculta.
      g.placeholder?.classList.remove('oculto');
      g.host?.classList.remove('visible');
      const bv = _binaryViewDe(g);
      if (bv) { bv.hidden = true; bv.innerHTML = ''; }
    }

    // Vaciar la cache de contenido.
    fileCache.clear();

    // Disponer TODOS los modelos inmemory://jarvis/* (sobreviven entre proyectos).
    try {
      if (window.monaco?.editor?.getModels) {
        for (const m of monaco.editor.getModels()) {
          const uri = m.uri?.toString?.() || '';
          if (uri.startsWith('inmemory://jarvis/')) m.dispose();
        }
      }
    } catch (_) { /* Monaco aún no cargó: no hay modelos que disponer */ }
    modelLRU.length = 0;

    // UI única (footer/breadcrumb/árbol) a estado vacío.
    renderTabs();
    renderBreadcrumb(null);
    marcarArchivoActivoEnTree(null);
    const elLang = document.getElementById('mf-lang');
    if (elLang) elLang.textContent = '—';
  }

  // ─── Links `archivo:línea` (terminal) + sesión por proyecto ─────────────────

  // Ruta absoluta del proyecto (para convertir links absolutos de la terminal a
  // relativos al root). Se cachea; se invalida al cambiar de proyecto.
  function _rootProyecto() {
    if (_projectRoot != null) return Promise.resolve(_projectRoot);
    if (!_rootPromise) {
      _rootPromise = fetch(`/api/workspace/${_projectId}/state`)
        .then(r => (r.ok ? r.json() : null))
        .then(st => { _projectRoot = (st && st.project && st.project.ruta) || ''; return _projectRoot; })
        .catch(() => { _projectRoot = ''; return ''; });
    }
    return _rootPromise;
  }

  async function _aRelativoProyecto(path) {
    let p = String(path || '').trim().replace(/^file:\/\//, '');
    if (!p) return null;
    const root = await _rootProyecto();
    const base = root ? root.replace(/\/+$/, '') : '';
    if (p.startsWith('/')) {
      if (base && (p === base || p.startsWith(base + '/'))) p = p.slice(base.length + 1);
      else return null;   // absoluto fuera del proyecto: no lo podemos abrir
    }
    p = p.replace(/^\.\//, '').replace(/^\/+/, '');
    return p || null;
  }

  // Abre `path` (relativo al proyecto, o absoluto dentro de él) y salta a la
  // línea. La usa la terminal para los `archivo:línea` clickeables.
  async function _abrirArchivoEnLinea(path, linea, col) {
    const rel = await _aRelativoProyecto(path);
    if (!rel) return false;
    try { window.JarvisDock?.open?.('editor'); } catch (_) {}
    const g = _grupoActivo() || (_initGrupos(), _grupoActivo());
    if (!g) return false;
    await abrirArchivoEnEditor(rel, g);
    if (!g.editor || !g.tabs.has(rel)) return false;
    const model = g.editor.getModel();
    const total = model ? model.getLineCount() : 1;
    const ln = Math.min(Math.max(1, parseInt(linea, 10) || 1), total);
    const c  = Math.max(1, parseInt(col, 10) || 1);
    try {
      g.editor.setPosition({ lineNumber: ln, column: c });
      g.editor.revealLineInCenter(ln);
      g.editor.focus();
    } catch (_) {}
    return true;
  }

  // ─── Sesión del editor por proyecto (tabs/cursor/split en localStorage) ──────
  function _sesClave() { return _projectId ? `jarvis.editor.sesion.${_projectId}` : null; }

  function _sesGuardar() {
    if (_restaurando) return;
    const clave = _sesClave();
    if (!clave) return;
    clearTimeout(_sesTimer);
    _sesTimer = setTimeout(() => {
      try {
        const idxs = splitActivo ? [0, 1] : [0];
        const grupos = idxs.map(i => {
          const g = editorGroups[i];
          if (!g) return null;
          return { activo: g.activeTab || null, tabs: [...g.tabs.keys()] };
        });
        const vs = idxs.map(i => {
          const g = editorGroups[i];
          if (!g?.editor || !g.activeTab) return null;
          try { return { i, path: g.activeTab, vs: g.editor.saveViewState() }; } catch (_) { return null; }
        }).filter(Boolean);
        localStorage.setItem(clave, JSON.stringify({
          v: 1, split: { activo: splitActivo, orientacion: splitOrientacion }, grupos, vs, ts: Date.now(),
        }));
      } catch (_) { /* cuota / modo privado: ignorar */ }
    }, 250);
  }

  async function _sesRestaurar() {
    const clave = _sesClave();
    if (!clave) return;
    let data = null;
    try { data = JSON.parse(localStorage.getItem(clave) || 'null'); } catch (_) { data = null; }
    if (!data || !Array.isArray(data.grupos) || !data.grupos.length) return;
    _restaurando = true;
    try {
      await cargarMonacoLazy();
      if (data.split?.activo) await toggleSplit(data.split.orientacion || 'vertical');
      const abrirGrupo = async (i, info) => {
        const g = editorGroups[i];
        if (!g || !info || !Array.isArray(info.tabs)) return;
        for (const p of info.tabs) {
          if (!g.tabs.has(p) && g.tabs.size < MAX_TABS) await abrirArchivoEnEditor(p, g);
        }
        if (info.activo && g.tabs.has(info.activo)) setActiveTab(info.activo, g);
      };
      await abrirGrupo(0, data.grupos[0]);
      if (data.split?.activo) await abrirGrupo(1, data.grupos[1]);
      if (Array.isArray(data.vs)) {
        for (const v of data.vs) {
          const g = editorGroups[v.i];
          if (g?.editor && v.path && v.vs && g.tabs.has(v.path)) {
            try { g.editor.restoreViewState(v.vs); } catch (_) {}
          }
        }
      }
    } catch (_) { /* restauración best-effort */ }
    finally {
      _restaurando = false;
      _sesGuardar();
    }
  }

  // Arranca la restauración UNA vez por proyecto (cuando el editor es usable).
  function _sesArrancar() {
    // El onTabShown del dock puede correr ANTES de init(): sin projectId no
    // podemos leer la sesión, y no hay que marcarla como restaurada.
    if (!_projectId) return;
    if (_sesProyectoRestaurado === _projectId) return;
    _sesProyectoRestaurado = _projectId;
    _sesRestaurar();
  }

  function _cargarScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error('No se pudo cargar ' + src));
      document.head.appendChild(s);
    });
  }

  // ─── Tabs y apertura de archivos ────────────────────────────────────

  // Getter interno del editor Monaco activo: la instancia del grupo activo.
  function _editorActivo() { return _mon(); }

  // Abre (o reusa si ya está abierto) un archivo y salta a line/col, seleccionando
  // el match. col/length vienen en unidades UTF-16 (alineadas con Monaco, ver backend).
  async function abrirYSaltarA(path, line, col, length) {
    await abrirArchivoEnEditor(path);   // no recarga si ya está abierto (estaAbierto)
    const ed = _editorActivo();          // instancia Monaco del grupo activo
    if (!ed) return;
    const startCol = col;
    const endCol   = col + (length || 0);
    ed.revealLineInCenter(line);
    ed.setSelection({
      startLineNumber: line,
      startColumn:     startCol,
      endLineNumber:   line,
      endColumn:       endCol,
    });
    ed.setPosition({ lineNumber: line, column: startCol });
    ed.focus();
  }

  // ─── Vista no-Monaco (binarios / imágenes / archivos grandes), spec §7 ──
  // El host es POR GRUPO: la vista binaria es el hermano .editor-binary-view
  // dentro del mismo .monaco-editor-wrap que el host del grupo.
  function _binaryViewDe(g) {
    const host = g?.host;
    if (!host) return null;
    return host.parentElement?.querySelector('.editor-binary-view') || null;
  }

  function _ocultarBinaryView(g = _grupoActivo()) {
    const v = _binaryViewDe(g);
    if (v) { v.hidden = true; v.innerHTML = ''; }
    g?.host?.classList.add('visible');
  }

  function _mostrarBinaryView(html, g = _grupoActivo()) {
    const v = _binaryViewDe(g);
    g?.placeholder?.classList.add('oculto');
    g?.host?.classList.remove('visible');   // oculta Monaco sin destruir su modelo
    if (v) { v.innerHTML = html; v.hidden = false; }
  }

  // Abre `path` en el grupo `g` (default: grupo activo). Modelo compartido por URI.
  // `force` reintenta saltando el límite de 1MB (hasta el tope duro del backend).
  async function abrirArchivoEnEditor(path, g = _grupoActivo(), force = false) {
    if (!g) { _initGrupos(); g = _grupoActivo(); }

    // Si ya está abierto en ESTE grupo, solo activarlo (revalidar en background).
    if (g.tabs.has(path)) {
      setActiveTab(path, g);
      _revalidarMtime(path);
      return;
    }
    if (g.tabs.size >= MAX_TABS) {
      toast(_t('Máximo {n} tabs abiertos en este panel. Cerrá alguno antes.').replace('{n}', MAX_TABS), 'warning');
      return;
    }

    // Guard de generación: abrir A y luego B rápido NO debe dejar A en foco por
    // resolver último. Capturamos el proyecto para descartar aperturas tras un
    // cambio de proyecto durante el await (mismo espíritu que v.gen en preview.js).
    const gen        = ++_openGen;
    const projAlAbrir = _projectId;

    await cargarMonacoLazy();
    if (gen !== _openGen || projAlAbrir !== _projectId) return;
    if (!g.editor) crearEditorEnGrupo(g);

    // Si el modelo ya existe (abierto en el otro grupo), reutilizar su contenido
    // sin re-fetch (mismo buffer compartido). Heredar dirty/mtime del otro tab.
    const modeloExistente = monaco.editor.getModel(_ftUri(path));
    if (modeloExistente) {
      const otro    = editorGroups[_otroIdx(g)];
      const tabOtro = otro?.tabs?.get(path);
      g.tabs.set(path, {
        content:  modeloExistente.getValue(),
        language: modeloExistente.getLanguageId(),
        dirty:    tabOtro?.dirty || false,
        mtime:    tabOtro?.mtime,
        conflicto: tabOtro?.conflicto || false,
      });
      setActiveTab(path, g);
      // Reuso de modelo por URI: revalidar mtime en background por si cambió en
      // disco (mismo patrón que la rama de cache fresca). No rompe el split: el
      // modelo es compartido y _revalidarMtime actualiza el buffer una sola vez.
      _revalidarMtime(path);
      return;
    }

    // Camino rápido: cache fresca → abrir SIN fetch y validar mtime en background.
    const cached = fileCache.get(path);
    if (cached && (Date.now() - cached.ts) < CACHE_TTL_MS) {
      g.tabs.set(path, { content: cached.content, language: cached.language, dirty: false, mtime: cached.mtime });
      setActiveTab(path, g);
      _revalidarMtime(path);
      return;
    }

    // Camino lento: fetch, poblar cache y abrir.
    try {
      const qs = `path=${encodeURIComponent(path)}${force ? '&force=true' : ''}`;
      const res = await fetch(`/api/projects/${_projectId}/files/read?${qs}`);
      // Apertura superada (el usuario pidió otro archivo) o cambió de proyecto: descartar.
      if (gen !== _openGen || projAlAbrir !== _projectId) return;

      // Archivo demasiado grande: vista no-Monaco con botón de reintento (sin alert).
      if (res.status === 413) {
        const err = await res.json().catch(() => ({}));
        const kb  = (err.detail || '').match(/(\d+)\s*KB/);
        const aprox = kb ? `${kb[1]} KB` : 'grande';
        _mostrarBinaryView(
          `<div class="ebv-msg">Archivo grande (${aprox}).<br>` +
          `<span class="ebv-sub">${esc(path)}</span><br>` +
          `<button class="ebv-force-btn" type="button">Abrir de todos modos</button></div>`,
          g
        );
        _binaryViewDe(g)
          ?.querySelector('.ebv-force-btn')
          ?.addEventListener('click', () => abrirArchivoEnEditor(path, g, true));
        return;
      }

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      if (gen !== _openGen || projAlAbrir !== _projectId) return;

      // Binario/imagen: vista no-Monaco, sin crear tab.
      if (data.binary) {
        const url = `/api/projects/${_projectId}/files/raw?path=${encodeURIComponent(path)}`;
        if (data.kind === 'image') {
          _mostrarBinaryView(`<img class="ebv-img" src="${url}" alt="${escAttr(path)}">`, g);
        } else {
          _mostrarBinaryView(
            `<div class="ebv-msg">Archivo binario — no editable<br><span class="ebv-sub">${esc(path)}</span></div>`,
            g
          );
        }
        return;   // no se crea tab Monaco para binarios
      }

      fileCache.set(path, {
        content: data.content, language: data.language,
        size: data.size, mtime: data.mtime, ts: Date.now(),
      });
      g.tabs.set(path, { content: data.content, language: data.language, dirty: false, mtime: data.mtime });
      setActiveTab(path, g);
    } catch (err) {
      if (!_restaurando) toast(`Error abriendo archivo: ${err.message}`, 'error');
    }
  }

  // Política mtime (§4.5): valida el mtime cacheado contra GET /files/stat sin
  // transferir contenido. Si cambió en disco y el tab NO está dirty → recarga
  // silenciosa. Si está dirty → avisa conflicto, no pisa en silencio.
  async function _revalidarMtime(path) {
    // Tomar una muestra del tab desde cualquier grupo que lo tenga.
    const _tabsConPath = editorGroups.filter(g => g?.tabs?.has(path));
    if (!_tabsConPath.length) return;
    const tab = _tabsConPath[0].tabs.get(path);
    try {
      const res = await fetch(`/api/projects/${_projectId}/files/stat?path=${encodeURIComponent(path)}`);
      if (!res.ok) return; // archivo borrado/movido: lo maneja el árbol, no acá
      const st = await res.json();
      if (tab.mtime != null && st.mtime != null && Math.abs(st.mtime - tab.mtime) < 0.001) return; // sin cambios
      const algunoDirty = _tabsConPath.some(g => g.tabs.get(path).dirty);
      if (algunoDirty) {
        console.warn(`[editor] conflicto: "${path}" cambió en disco y tiene cambios sin guardar`);
        for (const g of _tabsConPath) g.tabs.get(path).conflicto = true;
        renderTabs();
        return;
      }
      // No dirty → recargar silenciosamente el contenido fresco.
      const r2 = await fetch(`/api/projects/${_projectId}/files/read?path=${encodeURIComponent(path)}`);
      if (!r2.ok) return;
      const data = await r2.json();
      // Actualizar el tab homónimo en TODOS los grupos (buffer compartido).
      for (const g of _tabsConPath) {
        const t = g.tabs.get(path);
        t.content = data.content; t.language = data.language;
        t.mtime = data.mtime; t.conflicto = false;
      }
      fileCache.set(path, {
        content: data.content, language: data.language,
        size: data.size, mtime: data.mtime, ts: Date.now(),
      });
      const model = monaco.editor.getModel(_ftUri(path));
      if (model && model.getValue() !== data.content) model.setValue(data.content);
      renderTabs();   // limpiar la clase .conflicto si estaba puesta
    } catch (_) { /* red caída: el polling/refresh lo retoma luego */ }
  }

  // Activa `path` en el grupo `g` (default: grupo activo). Comparte el modelo por
  // URI: si ya existe, NO lo recrea ni lo pisa (mismo buffer en ambos grupos).
  // viewState es POR GRUPO. Footer/breadcrumb/árbol solo se actualizan si g es activo.
  function setActiveTab(path, g = _grupoActivo()) {
    if (!g || !g.editor) return;
    const tab = g.tabs.get(path);
    if (!tab) return;

    // Guardar viewState del tab SALIENTE de ESTE grupo antes de cambiar de modelo.
    if (g.activeTab && g.activeTab !== path) {
      const vs = g.editor.saveViewState();
      if (vs) g.viewStates.set(g.activeTab, vs);
    }

    g.activeTab = path;

    // Cambiar modelo Monaco (reuso por URI; LRU lo mantiene vivo). El modelo es
    // la fuente de verdad compartida: solo lo creamos si no existe, NUNCA setValue
    // (eso pisaría ediciones no guardadas que viven en el buffer compartido).
    const uri   = _ftUri(path);
    let model   = monaco.editor.getModel(uri);
    if (!model) {
      model = monaco.editor.createModel(tab.content, tab.language, uri);
    }
    g.editor.setModel(model);
    monaco.editor.setModelLanguage(model, tab.language || 'plaintext');
    _touchModel(path);

    // Restaurar viewState del tab ENTRANTE en ESTE grupo (scroll/cursor/folding).
    const vsIn = g.viewStates.get(path);
    if (vsIn) g.editor.restoreViewState(vsIn);
    g.editor.focus();

    // UI del grupo (placeholder/host). Cerrar la vista binaria si estaba abierta.
    _ocultarBinaryView(g);
    g.placeholder?.classList.add('oculto');
    g.host?.classList.add('visible');

    // Footer/breadcrumb/árbol son únicos → solo si este es el grupo activo.
    if (g === _grupoActivo()) {
      const elLang = document.getElementById('mf-lang');
      if (elLang) elLang.textContent = tab.language || 'plaintext';
      renderTabs();
      renderBreadcrumb(path);
      marcarArchivoActivoEnTree(path);
    } else {
      renderTabs();   // por si cambió el dirty del otro grupo (no debería)
    }
    _sesGuardar();    // persiste tabs/activo/split de este proyecto
  }

  // Cierra `path` en el grupo `g` (default: grupo activo). Solo pregunta por dirty
  // si el archivo NO queda referenciado en el otro grupo. NO dispone el modelo aquí:
  // queda vivo en el LRU para reabrir instantáneo (Fase 4); _evictModelsIfNeeded lo
  // dispone cuando NINGÚN grupo lo referencia y se exceden MAX_LIVE_MODELS.
  async function cerrarTab(path, g = _grupoActivo()) {
    if (!g) return;
    const tab = g.tabs.get(path);
    if (!tab) return;

    const otro   = editorGroups[_otroIdx(g)];
    const enOtro = !!otro?.tabs?.has(path);
    // Solo los cierres interactivos llegan al prompt: los cierres en masa
    // (borrado de carpeta/archivo, navegación) ponen dirty=false antes de llamar,
    // así que no esperan este await. Por eso es seguro hacer la función async.
    if (tab.dirty && !enOtro &&
        !(await confirmar('Hay cambios sin guardar. ¿Cerrar igual?',
          { peligro: true, confirmText: 'Cerrar igual' }))) return;

    // Guardar el viewState de ESTE grupo por si se reabre el tab más tarde.
    if (g.activeTab === path && g.editor) {
      const vs = g.editor.saveViewState();
      if (vs) g.viewStates.set(path, vs);
    }

    g.tabs.delete(path);
    _evictModelsIfNeeded();

    if (g.activeTab === path) {
      const siguiente = g.tabs.size > 0 ? [...g.tabs.keys()].pop() : null;
      if (siguiente) {
        g.activeTab = null;       // que setActiveTab no auto-guarde viewState del cerrado
        setActiveTab(siguiente, g);
      } else {
        g.activeTab = null;
        g.placeholder?.classList.remove('oculto');
        g.host?.classList.remove('visible');
        g.editor?.setModel(null);
        if (g === _grupoActivo()) {
          marcarArchivoActivoEnTree(null);
          renderBreadcrumb(null);
          const elLang = document.getElementById('mf-lang');
          if (elLang) elLang.textContent = '—';
        }
      }
    }
    renderTabs();
    _sesGuardar();
  }

  function renderBreadcrumb(path) {
    const bc = document.getElementById('editor-breadcrumb');
    if (!bc) return;
    if (!path) { bc.hidden = true; bc.innerHTML = ''; return; }
    const parts = path.split('/').filter(Boolean);
    bc.innerHTML = parts.map((p, i) => {
      const isLast = i === parts.length - 1;
      const icon = isLast ? `<span class="bc-icon">${_svgIcon(_extKind(p))}</span>` : '';
      return `<span class="bc-seg${isLast ? ' bc-current' : ''}">${icon}${esc(p)}</span>`;
    }).join('<span class="bc-sep" aria-hidden="true">›</span>');
    bc.hidden = false;
  }

  // Renderiza la barra de tabs única, mostrando los tabs del GRUPO ACTIVO.
  function renderTabs() {
    const cont = document.getElementById('monaco-tabs');
    if (!cont) return;
    const g = _grupoActivo();
    cont.innerHTML = '';
    if (!g) return;
    for (const [path, tab] of g.tabs) {
      const el = document.createElement('div');
      el.className = 'mt-tab' + (path === g.activeTab ? ' activo' : '') + (tab.dirty ? ' dirty' : '') + (tab.conflicto ? ' conflicto' : '');
      el.draggable = true;
      el.dataset.path = path;
      const fname = path.split('/').pop();
      const titulo = tab.conflicto ? 'Cambió en disco — guardá para resolver' : escAttr(path);
      el.innerHTML =
        `<span class="mt-icon">${_svgIcon(_extKind(fname))}</span>` +
        `<span class="mt-name" title="${titulo}" data-i18n-skip>${esc(fname)}</span>` +
        `<span class="mt-conflict-mark" aria-hidden="true" title="Cambió en disco — guardá para resolver">${icon('alert', 12)}</span>` +
        `<span class="mt-dirty-dot" aria-hidden="true"></span>` +
        `<button class="mt-close" title="Cerrar" aria-label="Cerrar">` +
        `<svg viewBox="0 0 12 12" width="10" height="10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="3" y1="3" x2="9" y2="9"/><line x1="9" y1="3" x2="3" y2="9"/></svg>` +
        `</button>`;
      el.addEventListener('click', e => {
        if (!e.target.closest('.mt-close')) setActiveTab(path, g);
      });
      el.querySelector('.mt-close').addEventListener('click', e => {
        e.stopPropagation();
        cerrarTab(path, g);
      });
      _cablearDragTab(el, path);
      cont.appendChild(el);
    }
  }

  // ─── Drag & drop de tabs entre grupos (Fase 9) ──────────────────────
  let _tabDrag = null;  // { path } siendo arrastrado

  function _cablearDragTab(el, path) {
    el.addEventListener('dragstart', (e) => {
      _tabDrag = { path };
      el.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      try { e.dataTransfer.setData('text/plain', path); } catch (_) {}
    });
    el.addEventListener('dragend', () => {
      el.classList.remove('dragging');
      _tabDrag = null;
      document.querySelectorAll('.monaco-group.drop-target')
        .forEach(gr => gr.classList.remove('drop-target'));
    });
  }

  function _cablearDropGrupos() {
    [0, 1].forEach(idx => {
      const dom = document.getElementById('monaco-group-' + idx);
      if (!dom || dom._dropCableado) return;
      dom._dropCableado = true;

      dom.addEventListener('dragover', (e) => {
        if (!_tabDrag) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        dom.classList.add('drop-target');
      });
      dom.addEventListener('dragleave', (e) => {
        if (!dom.contains(e.relatedTarget)) dom.classList.remove('drop-target');
      });
      dom.addEventListener('drop', async (e) => {
        if (!_tabDrag) return;
        e.preventDefault();
        dom.classList.remove('drop-target');
        const path = _tabDrag.path;

        // Si el destino es el grupo 1 y aún no hay split, abrirlo.
        if (idx === 1 && !splitActivo) await toggleSplit(splitOrientacion);
        const destino = editorGroups[idx];
        if (!destino) return;

        // Soltar donde ya está activo: no-op.
        if (destino.activeTab === path) { setActiveGroup(idx); return; }

        await abrirArchivoEnEditor(path, destino);  // copia el tab al destino (modelo compartido)
        setActiveGroup(idx);
      });
    });
  }

  function marcarArchivoActivoEnTree(path) {
    document.querySelectorAll('#file-tree-body .ft-file.activo').forEach(el => el.classList.remove('activo'));
    if (!path) return;
    const node = document.querySelector(`#file-tree-body .ft-file[data-path="${CSS.escape(path)}"]`);
    node?.classList.add('activo');
  }

  // ─── Indicador de guardado en la status-bar (spec §7) ───────────────
  let _saveStatusTimer = null;

  // `texto` se inserta como textContent (sin emojis); `iconName` opcional
  // antepone un glyph SVG (icon()) heredando el color del estado vía currentColor.
  function _setSaveStatus(texto, clase, iconName) {
    const el = document.getElementById('editor-save-status');
    if (!el) return;
    if (_saveStatusTimer) { clearTimeout(_saveStatusTimer); _saveStatusTimer = null; }
    el.className = 'mf-save-status' + (clase ? ' ' + clase : '');
    if (iconName) {
      el.innerHTML = icon(iconName, 12) + '<span></span>';
      el.querySelector('span').textContent = texto;
    } else {
      el.textContent = texto;
    }
    if (clase === 'ok') {
      _saveStatusTimer = setTimeout(() => {
        el.classList.add('fade');
        _saveStatusTimer = setTimeout(() => { el.textContent = ''; el.className = 'mf-save-status'; }, 300);
      }, 2000);
    }
  }

  async function guardarArchivoActivo() {
    const g = _grupoActivo();
    if (!g || !g.activeTab || !g.editor) return;
    const path = g.activeTab;
    const tab  = g.tabs.get(path);
    if (!tab) return;
    const content = g.editor.getValue();
    _setSaveStatus('Guardando…', 'saving');
    try {
      const res = await fetch(`/api/projects/${_projectId}/files/save`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ path, content }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      // Modelo compartido → limpiar dirty/conflicto + actualizar mtime/content del
      // tab homónimo en AMBOS grupos (sin save-pisado).
      for (const gr of editorGroups) {
        const t = gr?.tabs?.get(path);
        if (t) { t.dirty = false; t.conflicto = false; t.content = content; t.mtime = data.mtime; }
      }
      // Actualizar la cache con lo recién guardado para que reabrir sea instantáneo
      // y la revalidación de mtime no dispare recargas falsas.
      fileCache.set(path, {
        content,
        language: tab.language,
        size: data.size,
        mtime: data.mtime,
        ts: Date.now(),
      });
      renderTabs();
      // Repintar badges git sin reconstruir el árbol (el archivo ahora es 'M').
      refrescarGitStatus();
      _setSaveStatus('Guardado', 'ok', 'check');
    } catch (err) {
      _setSaveStatus('Error al guardar', 'err');
    }
  }

  document.getElementById('mf-save-btn')?.addEventListener('click', guardarArchivoActivo);

  // ─── Aviso de cambios sin guardar (spec §7) ─────────────────────────
  // Recorre TODOS los grupos: true si algún tab está dirty en cualquier panel.
  function hayTabsSinGuardar() {
    for (const g of editorGroups) {
      if (!g) continue;
      for (const tab of g.tabs.values()) {
        if (tab && tab.dirty) return true;
      }
    }
    return false;
  }

  // true si se puede continuar (no hay dirty, o el usuario confirmó el descarte).
  // Migrada de window.confirm() nativo a confirmar() del sistema (ui.js).
  // Es async: sus callers en workspace.js (cambiarProyecto, popstate) ya son async
  // y la consumen con `const ok = await …; if (ok === false) return;`.
  async function confirmarDescarteSiSucio() {
    if (!hayTabsSinGuardar()) return true;
    return confirmar('Hay archivos sin guardar. ¿Descartar los cambios y continuar?', {
      titulo:      'Cambios sin guardar',
      confirmText: 'Descartar y salir',
      cancelText:  'Seguir editando',
      peligro:     true,
    });
  }

  // Aviso nativo al cerrar/recargar la pestaña del browser. Registrado UNA sola
  // vez (no por proyecto) para no acumular listeners en cambiarProyecto.
  window.addEventListener('beforeunload', e => {
    if (hayTabsSinGuardar()) {
      e.preventDefault();
      e.returnValue = '';   // requerido por Chrome para disparar el diálogo nativo
    }
  });

  // ─── Búsqueda en contenido (⌘⇧F) ───────────────────────────────────
  const _searchFlags = { case_sensitive: false, whole_word: false, regex: false };
  let _searchTimer = null;
  let _searchWired = false;

  // Escapa texto para inyección segura en innerHTML del panel de resultados.
  // Reusa el esc() global (mismo helper que el file tree).
  function _searchEsc(s) { return esc(String(s)); }

  function _cerrarSearch() {
    document.getElementById('search-panel')?.classList.add('oculto');
  }

  function _wireSearch() {
    if (_searchWired) return;
    _searchWired = true;
    const input = document.getElementById('search-input');
    if (!input) { _searchWired = false; return; }
    input.addEventListener('input', () => {
      clearTimeout(_searchTimer);
      _searchTimer = setTimeout(() => _ejecutarBusqueda(), 200);
    });
    const toggle = (id, flag) => {
      const btn = document.getElementById(id);
      if (!btn) return;
      btn.addEventListener('click', () => {
        _searchFlags[flag] = !_searchFlags[flag];
        btn.classList.toggle('active', _searchFlags[flag]);
        _ejecutarBusqueda();
      });
    };
    toggle('search-case', 'case_sensitive');
    toggle('search-word', 'whole_word');
    toggle('search-regex', 'regex');
    // Click delegado en resultados
    document.getElementById('search-results')?.addEventListener('click', (e) => {
      const m = e.target.closest('.sr-match');
      if (m) {
        abrirYSaltarA(
          m.dataset.path,
          parseInt(m.dataset.line, 10),
          parseInt(m.dataset.col, 10),
          parseInt(m.dataset.length, 10)
        );
        return;
      }
      const f = e.target.closest('.sr-file');
      if (f) {
        const primer = f.nextElementSibling;
        if (primer && primer.classList.contains('sr-match')) {
          abrirYSaltarA(
            primer.dataset.path,
            parseInt(primer.dataset.line, 10),
            parseInt(primer.dataset.col, 10),
            parseInt(primer.dataset.length, 10)
          );
        }
      }
    });
    // Escape cierra el panel
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') _cerrarSearch();
    });
  }

  async function _ejecutarBusqueda() {
    const input = document.getElementById('search-input');
    const meta  = document.getElementById('search-meta');
    const cont  = document.getElementById('search-results');
    if (!input || !meta || !cont) return;
    const q = (input.value || '').trim();
    if (!q) { meta.textContent = ''; meta.classList.remove('error'); cont.innerHTML = ''; return; }

    const params = new URLSearchParams({
      q,
      case_sensitive: _searchFlags.case_sensitive,
      regex:          _searchFlags.regex,
      whole_word:     _searchFlags.whole_word,
    });
    let data;
    try {
      const res = await fetch(`/api/projects/${_projectId}/files/search?${params}`);
      data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    } catch (err) {
      meta.classList.add('error');
      meta.textContent = _t('Error: {msg}').replace('{msg}', err.message);
      cont.innerHTML = '';
      return;
    }

    if (data.error) {
      meta.classList.add('error');
      meta.textContent = data.error;
      cont.innerHTML = '';
      return;
    }
    meta.classList.remove('error');
    const trunc = data.truncated ? ' (parcial)' : '';
    meta.textContent = _t('{n} resultado(s) en {m} archivo(s)').replace('{n}', data.total).replace('{m}', data.results.length) + trunc;

    if (data.results.length === 0) {
      cont.innerHTML = '<div class="sr-empty">Sin coincidencias.</div>';
      return;
    }

    let html = '';
    for (const r of data.results) {
      html += `<div class="sr-file" data-path="${escAttr(r.file)}">` +
              `<span data-i18n-skip>${_searchEsc(r.file)}</span>` +
              `<span class="sr-file-count">${r.matches.length}</span></div>`;
      for (const m of r.matches) {
        html += `<div class="sr-match" data-i18n-skip data-path="${escAttr(r.file)}" ` +
                `data-line="${m.line}" data-col="${m.col}" data-length="${m.length}">` +
                `<span class="sr-line-no">${m.line}</span>${_searchEsc(m.text)}</div>`;
      }
    }
    cont.innerHTML = html;
  }

  // Superficie pública congelada (§3.3): abre el panel de búsqueda en contenido.
  function openSearch() {
    const panel = document.getElementById('search-panel');
    if (!panel) return;
    _wireSearch();
    panel.classList.remove('oculto');
    const input = document.getElementById('search-input');
    if (input) { input.focus(); input.select(); }
  }

  // Limpia el estado visible del panel (al navegar de proyecto).
  function _limpiarSearch() {
    const cont = document.getElementById('search-results');
    const meta = document.getElementById('search-meta');
    if (cont) cont.innerHTML = '';
    if (meta) { meta.textContent = ''; meta.classList.remove('error'); }
    _cerrarSearch();
  }

  // Atajo ⌘⇧F / Ctrl+Shift+F → búsqueda en contenido. Registrado en CAPTURE
  // para ganarle al handler global de workspace.js y al diálogo nativo.
  // Idempotente vía flag por si el módulo se evaluara más de una vez.
  let _searchKeyWired = false;
  function _wireSearchShortcut() {
    if (_searchKeyWired) return;
    _searchKeyWired = true;
    document.addEventListener('keydown', (e) => {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.shiftKey && (e.code === 'KeyF' || e.key === 'F' || e.key === 'f')) {
        e.preventDefault();
        e.stopImmediatePropagation();
        openSearch();
      }
    }, true);
  }
  _wireSearchShortcut();

  // ─── Ciclo de vida del editor (lazy: solo arranca si el panel está visible) ──
  function _arrancarSiVisible() {
    if (window.editorVisible) {
      cargarFileTree();
      iniciarPollingFileTree();
      cargarMonacoLazy();
      _sesArrancar();
    }
  }

  // ═══════════════════════════════════════════════════════════════════
  //  Superficie pública CONGELADA — window.JarvisEditor
  //  El resto del workspace consume SOLO esto, nunca los globals internos.
  // ═══════════════════════════════════════════════════════════════════
  window.JarvisEditor = {
    // Ciclo de vida
    init(projectId) {
      _projectId = String(projectId);
      _projectRoot = null; _rootPromise = null; _sesProyectoRestaurado = null;
      _arrancarSiVisible();
      _sesArrancar();   // barato sin sesión; restaura aunque el panel esté oculto
    },
    onProjectChanged(projectId) {
      _cpCerrar(); _limpiarSearch();
      _restaurando = true;          // que el reset no pise la sesión guardada
      _resetEditorState();
      _restaurando = false;
      _projectId = String(projectId);
      _projectRoot = null; _rootPromise = null; _sesProyectoRestaurado = null;
      _arrancarSiVisible();
      _sesArrancar();
    },

    // Tabs / archivos (superficie congelada → funciones internas históricas).
    // Rutean SIEMPRE al grupo activo (default param de las internas).
    abrirArchivo(path)  { return abrirArchivoEnEditor(path); },
    abrirArchivoEnLinea(path, linea, col) { return _abrirArchivoEnLinea(path, linea, col); },
    cerrarTab(path)     { return cerrarTab(path); },
    guardarActivo()     { return guardarArchivoActivo(); },
    estaAbierto(path)   { return editorGroups.some(g => g?.tabs?.has(path)); },

    // Cambios sin guardar (aditivos a la superficie §3.3, spec §7).
    hayTabsSinGuardar()       { return hayTabsSinGuardar(); },
    confirmarDescarteSiSucio(){ return confirmarDescarteSiSucio(); },

    // Split view (Fase 9)
    toggleSplit(orientation) { return toggleSplit(orientation); },
    unsplit()                { return unsplit(); },

    // Árbol
    refrescarArbol()    { return cargarFileTree(); },

    // Toggle del panel (lo invoca el shim window.togglePanel → JarvisDock)
    iniciarPolling()    { return iniciarPollingFileTree(); },
    detenerPolling()    { return detenerPollingFileTree(); },
    cargarMonaco()      { cargarMonacoLazy(); _sesArrancar(); },

    // Relayout (lo invoca JarvisDock al redimensionar/maximizar): ambos grupos.
    relayout()          { _relayout(); },

    // Command palette (Fase 5) + búsqueda en contenido (Fase 6)
    openPalette(modo)   { return _cpAbrir(modo); },
    openSearch()        { return openSearch(); },
  };
})();
