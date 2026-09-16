// JARVIS — Task Board (kanban conectado a los workflows del orquestador).
// Dos fuentes de cards:
//  · Tareas MANUALES (tabla tasks): CRUD + drag entre columnas + "asignar a
//    agente" → la tarea viaja por tmux y el monitor de TASK_DONE la resuelve.
//  · Pasos de WORKFLOWS del orquestador (read-only): proyectados en vivo
//    desde los workflow_update del WebSocket — el board ES la vista kanban
//    de lo que JARVIS está orquestando.
// Expone window.JarvisTasks = { init, show, onProjectChanged,
//                               onTasksUpdate, onWorkflowUpdate }.

(() => {
  let _projectId = null;
  let _tasks     = [];        // tareas manuales (API)
  let _workflows = new Map(); // workflow_id → { nombre, pasos[] } (en vivo)
  let _visible   = false;
  let _montado   = false;
  let _picker    = null;      // { el, taskId } del picker de asignar abierto

  const $ = (id) => document.getElementById(id);
  const esc = (s) => { const d = document.createElement('div'); d.textContent = String(s ?? ''); return d.innerHTML; };

  const _t = (s) => (window.JarvisI18n && window.JarvisI18n.t) ? window.JarvisI18n.t(s) : s;

  const COLS = [
    { id: 'backlog', nombre: 'Backlog' },
    { id: 'running', nombre: 'En curso' },
    { id: 'blocked', nombre: 'Bloqueado' },
    { id: 'done',    nombre: 'Hecho' },
  ];
  // estado de paso de workflow → columna
  const COL_DE_PASO = { pending: 'backlog', running: 'running', blocked: 'blocked', error: 'blocked', done: 'done' };

  /* ── Montaje ───────────────────────────────────────────────── */
  function _montar() {
    const panel = $('tasks-panel');
    if (!panel || _montado) return;
    _montado = true;
    panel.innerHTML = `
      <div class="tk-root">
        <header class="tk-topbar">
          <span class="tk-title">Tareas.</span>
          <span class="tk-sub">kanban del workspace</span>
          <span class="tk-spacer"></span>
          <button class="tk-nueva" id="tk-nueva" type="button">
            ${icon('plus', 12)} <span>Nueva tarea</span>
          </button>
        </header>
        <div class="tk-board" id="tk-board">
          ${COLS.map(c => `
            <div class="tk-col" data-col="${c.id}">
              <div class="tk-col-head">
                <span class="tk-col-dot"></span>
                <span class="tk-col-nombre">${c.nombre}</span>
                <span class="tk-col-count" id="tk-count-${c.id}">0</span>
              </div>
              <div class="tk-col-body" id="tk-col-${c.id}"></div>
            </div>`).join('')}
        </div>
      </div>`;

    $('tk-nueva').addEventListener('click', _crearTarea);
    _cablearDnD();
  }

  /* ── Datos ─────────────────────────────────────────────────── */
  async function _cargarTasks() {
    try {
      const r = await fetch(`/api/projects/${_projectId}/tasks`);
      if (!r.ok) return;
      _tasks = await r.json();
    } catch { /* red */ }
  }

  async function _cargarWorkflows() {
    try {
      const r = await fetch(`/api/orchestrator/workflows/${_projectId}`);
      if (!r.ok) return;
      const lista = await r.json();
      _workflows = new Map();
      // Proyectar solo los últimos 12 workflows (vienen DESC por fecha):
      // el historial completo vive en el modal de Workflows, acá el board
      // muestra lo reciente sin que "Hecho" crezca infinito.
      for (const w of (Array.isArray(lista) ? lista : []).slice(0, 12)) {
        let pasos = w.pasos;
        if (typeof pasos === 'string') { try { pasos = JSON.parse(pasos); } catch { pasos = []; } }
        if (Array.isArray(pasos) && pasos.length) {
          _workflows.set(String(w.id), { nombre: w.nombre || 'Workflow', pasos });
        }
      }
    } catch { /* red */ }
  }

  /* ── Render ────────────────────────────────────────────────── */
  function _render() {
    if (!_montado) return;
    const porCol = { backlog: [], running: [], blocked: [], done: [] };

    // Tareas manuales
    for (const t of _tasks) {
      (porCol[t.estado] || porCol.backlog).push({ tipo: 'task', t });
    }
    // Pasos de workflows (en vivo)
    for (const [wfId, wf] of _workflows) {
      wf.pasos.forEach((paso, i) => {
        const col = COL_DE_PASO[paso.estado || 'pending'] || 'backlog';
        porCol[col].push({ tipo: 'wf', wfId, wfNombre: wf.nombre, paso, idx: i });
      });
    }

    for (const c of COLS) {
      const cont = $(`tk-col-${c.id}`);
      const items = porCol[c.id];
      $(`tk-count-${c.id}`).textContent = items.length;
      if (!items.length) {
        cont.innerHTML = `<div class="tk-col-empty">${c.id === 'backlog' ? 'Creá una tarea o pedile un workflow a JARVIS' : '—'}</div>`;
        continue;
      }
      cont.innerHTML = items.map((it, i) => it.tipo === 'task'
        ? _cardTaskHTML(it.t, i)
        : _cardWfHTML(it, i)).join('');
      _cablearCards(cont);
    }
  }

  function _cardTaskHTML(t, i) {
    const acciones = [];
    if (t.estado === 'backlog') {
      acciones.push(`<button class="asignar" data-act="asignar" title="Asignar a un agente" aria-label="Asignar a un agente">${icon('play', 11)}</button>`);
    }
    if (t.estado === 'done' || t.estado === 'blocked') {
      acciones.push(`<button data-act="reabrir" title="Volver al backlog" aria-label="Volver al backlog">${icon('undo', 11)}</button>`);
    }
    acciones.push(`<button data-act="editar" title="Editar" aria-label="Editar">${icon('edit', 11)}</button>`);
    acciones.push(`<button class="peligro" data-act="borrar" title="Borrar" aria-label="Borrar">${icon('trash', 11)}</button>`);

    const agente = t.terminal_id && t.estado === 'running'
      ? `<span class="tk-badge agente">${icon('terminal', 9)} T${esc(t.terminal_id)}</span>` : '';

    return `
      <article class="tk-card" draggable="true" data-task="${t.id}" style="--i:${Math.min(i, 10)}">
        <div class="tk-card-head"><span class="tk-card-titulo">${esc(t.titulo)}</span></div>
        ${t.descripcion ? `<div class="tk-card-desc">${esc(t.descripcion)}</div>` : ''}
        <div class="tk-card-meta">${agente}</div>
        <span class="tk-card-acciones">${acciones.join('')}</span>
      </article>`;
  }

  function _cardWfHTML(it, i) {
    const tarea = (it.paso.tarea || '').replace(/Cuando termines.*$/i, '').trim();
    return `
      <article class="tk-card" data-wf="${esc(it.wfId)}" style="--i:${Math.min(i, 10)}">
        <div class="tk-card-head"><span class="tk-card-titulo">${esc(it.paso.agente || _t('Paso {n}').replace('{n}', it.idx + 1))}</span></div>
        ${tarea ? `<div class="tk-card-desc">${esc(tarea.slice(0, 140))}</div>` : ''}
        <div class="tk-card-meta">
          <span class="tk-badge wf">${icon('zap', 9)} ${esc(it.wfNombre.slice(0, 24))}</span>
          ${it.paso.rol && it.paso.rol !== 'builder' ? `<span class="tk-badge rol-${esc(it.paso.rol)}">${esc(it.paso.rol)}</span>` : ''}
          ${it.paso.terminal_id ? `<span class="tk-badge agente">${icon('terminal', 9)} T${esc(it.paso.terminal_id)}</span>` : ''}
        </div>
      </article>`;
  }

  /* ── Interacción de cards ──────────────────────────────────── */
  function _cablearCards(cont) {
    cont.querySelectorAll('.tk-card[data-task]').forEach(card => {
      const id = parseInt(card.dataset.task, 10);

      card.addEventListener('dragstart', (e) => {
        card.classList.add('dragging');
        e.dataTransfer.setData('text/plain', String(id));
        e.dataTransfer.effectAllowed = 'move';
      });
      card.addEventListener('dragend', () => card.classList.remove('dragging'));

      card.querySelector('[data-act="asignar"]')?.addEventListener('click', (e) => {
        e.stopPropagation(); _abrirPicker(e.currentTarget, id);
      });
      card.querySelector('[data-act="reabrir"]')?.addEventListener('click', async (e) => {
        e.stopPropagation(); await _patch(id, { estado: 'backlog' });
      });
      card.querySelector('[data-act="editar"]')?.addEventListener('click', async (e) => {
        e.stopPropagation();
        const t = _tasks.find(x => x.id === id);
        const titulo = await pedirTexto('Título de la tarea:', { titulo: 'Editar tarea', valor: t?.titulo || '', confirmText: 'Guardar' });
        if (!titulo) return;
        const desc = await pedirTexto('Descripción (opcional — el agente la recibe junto al título):', {
          titulo: 'Detalle', valor: t?.descripcion || '', confirmText: 'Guardar', cancelText: 'Sin detalle',
        });
        await _patch(id, { titulo, descripcion: desc || '' });
      });
      card.querySelector('[data-act="borrar"]')?.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!(await confirmar('¿Borrar esta tarea?', { peligro: true, confirmText: 'Borrar' }))) return;
        await fetch(`/api/tasks/${id}`, { method: 'DELETE' });
        await refrescar();
      });
    });
  }

  function _cablearDnD() {
    document.querySelectorAll('#tasks-panel .tk-col').forEach(col => {
      col.addEventListener('dragover', (e) => { e.preventDefault(); col.classList.add('drag-over'); });
      col.addEventListener('dragleave', () => col.classList.remove('drag-over'));
      col.addEventListener('drop', async (e) => {
        e.preventDefault();
        col.classList.remove('drag-over');
        const id = parseInt(e.dataTransfer.getData('text/plain'), 10);
        if (!id) return;
        const destino = col.dataset.col;
        if (destino === 'running') {
          // "En curso" de verdad = un agente la agarra: pedir a cuál
          _abrirPicker(null, id, e.clientX, e.clientY);
          return;
        }
        await _patch(id, { estado: destino });
      });
    });
  }

  async function _patch(id, body) {
    await fetch(`/api/tasks/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    await refrescar();
  }

  async function _crearTarea() {
    const titulo = await pedirTexto('¿Qué hay que hacer?', {
      titulo: 'Nueva tarea', placeholder: 'ej. Agregar tests al router de pagos', confirmText: 'Crear',
    });
    if (!titulo) return;
    await fetch(`/api/projects/${_projectId}/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ titulo }),
    });
    await refrescar();
  }

  /* ── Picker de agente (asignar) ────────────────────────────── */
  function _cerrarPicker() {
    if (_picker) { try { _picker.el.remove(); } catch { /* ya estaba */ } _picker = null; }
    document.querySelectorAll('.tk-picker').forEach(p => p.remove());
  }

  // `anchor` = elemento disparador (se abre DEBAJO, así no lo tapa y el segundo
  // click puede togglear); sin anchor (drop en "En curso") usa x/y del mouse.
  async function _abrirPicker(anchor, taskId, x, y) {
    // Toggle: si el picker YA está abierto para esta misma tarea, el segundo
    // click lo cierra. Antes el mousedown global lo borraba y el click del
    // mismo botón lo volvía a crear → quedaba pegado y no se podía ocultar.
    if (_picker && _picker.taskId === String(taskId)) { _cerrarPicker(); return; }
    _cerrarPicker();

    let terminales = [];
    try {
      const r = await fetch(`/api/workspace/${_projectId}/state`);
      if (r.ok) terminales = (await r.json()).terminals || [];
    } catch { /* red */ }

    const el = document.createElement('div');
    el.className = 'tk-picker';
    el.innerHTML = `
      <div class="tk-picker-titulo">Asignar a un agente</div>
      ${terminales.length
        ? terminales.map(t => `<button data-tid="${t.id}">${icon('terminal', 12)} ${esc(t.nombre)}</button>`).join('')
        : '<div class="tk-picker-vacio">No hay terminales — creá una con + Terminal</div>'}`;
    document.body.appendChild(el);

    const { offsetWidth: w, offsetHeight: h } = el;
    let px = x, py = y;
    if (anchor && anchor.getBoundingClientRect) {
      const r = anchor.getBoundingClientRect();
      px = r.left; py = r.bottom + 6;
    }
    el.style.left = `${Math.min(px, window.innerWidth - w - 8)}px`;
    el.style.top  = `${Math.min(py, window.innerHeight - h - 8)}px`;

    _picker = { el, taskId: String(taskId) };

    el.querySelectorAll('[data-tid]').forEach(b =>
      b.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        _cerrarPicker();
        const r = await fetch(`/api/tasks/${taskId}/asignar`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ terminal_id: parseInt(b.dataset.tid, 10) }),
        });
        if (r.ok) toast('Tarea enviada al agente — el board se mueve solo con TASK_DONE.', 'success');
        else toast('No se pudo asignar la tarea.', 'error');
        await refrescar();
      }));
  }

  // Un solo listener global: cierra al click afuera, pero IGNORA el botón que
  // abre (así el click del botón alcanza a hacer su toggle en vez de que el
  // mousedown lo anule) y el propio picker.
  document.addEventListener('mousedown', (ev) => {
    if (!_picker) return;
    if (ev.target.closest('.tk-picker') || ev.target.closest('[data-act="asignar"]')) return;
    _cerrarPicker();
  });

  /* ── API pública ───────────────────────────────────────────── */
  async function refrescar() {
    // Generation-guard: si cambian de proyecto durante los fetch, no pintar lo viejo.
    const pid = _projectId;
    await Promise.all([_cargarTasks(), _cargarWorkflows()]);
    if (pid !== _projectId) return;
    _render();
  }

  window.JarvisTasks = {
    init(projectId) {
      _projectId = projectId;
      _montar();
    },
    async show() {
      _visible = true;
      _montar();
      await refrescar();
    },
    onProjectChanged(projectId) {
      _projectId = projectId;
      _workflows = new Map();
      _tasks = [];
      if (_visible) refrescar();
    },
    // WS: el board cambió en el server (crear/asignar/TASK_DONE)
    onTasksUpdate() { if (_visible) refrescar(); },
    // WS: progreso de workflow del orquestador → proyectar en vivo
    onWorkflowUpdate(data) {
      if (!data?.workflow_id || !Array.isArray(data.pasos)) return;
      const previo = _workflows.get(String(data.workflow_id));
      _workflows.set(String(data.workflow_id), {
        nombre: previo?.nombre || data.nombre || 'Workflow',
        pasos:  data.pasos,
      });
      if (_visible) _render();
    },
  };
})();
