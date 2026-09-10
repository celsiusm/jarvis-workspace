// JARVIS — OrchestratorPanel (panel izquierdo premium)
// Componente vanilla JS: header + esfera + waveform + chat + composer

/* ── SVG icons (stroke 2px, currentColor) ─────────────────────── */
const ORCH_SVG = {
  history: `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
  newThread: `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`,
  more: `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="5" cy="12" r="1.5" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.5" fill="currentColor" stroke="none"/></svg>`,
  // Iconos del composer — SET SIMÉTRICO (grid óptico único: viewBox 24, trazo 1.9,
  // round, sin rellenos que desbalanceen → los 3 leen como un juego coherente).
  mention: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-3.92 7.94"/></svg>`,
  slash:   `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18 15 6"/></svg>`,
  attach:  `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>`,
  // Enviar = flecha ↑ (estándar de los composers de chat modernos).
  // Minimalista, se lee "enviar" al instante y equilibra el círculo del botón.
  send: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5"/><path d="m5 12 7-7 7 7"/></svg>`,
  // Mini-iconos contextuales para quick replies (12px, stroke 1.7px)
  chip_status:   `<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="3" y1="13" x2="3" y2="9.5"/><line x1="7" y1="13" x2="7" y2="6.5"/><line x1="11" y1="13" x2="11" y2="3.5"/></svg>`,
  chip_claude:   `<svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-opacity="0.5"/><circle cx="8" cy="8" r="2.6" fill="currentColor"/></svg>`,
  chip_terminal: `<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3.5,5 6,8 3.5,11"/><line x1="8" y1="11" x2="12.5" y2="11"/></svg>`,
  chip_plus:     `<svg width="12" height="12" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><line x1="7" y1="2.5" x2="7" y2="11.5"/><line x1="2.5" y1="7" x2="11.5" y2="7"/></svg>`,
  chip_spark:    `<svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 1l1.4 4.6L14 7l-4.6 1.4L8 13l-1.4-4.6L2 7l4.6-1.4z"/></svg>`,
};

// Mapeo heurístico de label → key del icono. No requiere cambios en el caller.
function orchChipIcon(label) {
  const l = (label || '').toLowerCase();
  if (l.includes('estado') || l.includes('status'))      return ORCH_SVG.chip_status;
  if (l.includes('claude'))                              return ORCH_SVG.chip_claude;
  if (l.includes('terminal'))                            return ORCH_SVG.chip_terminal;
  if (l.startsWith('+') || l.includes('nueva') || l.includes('nuevo')) return ORCH_SVG.chip_plus;
  return ORCH_SVG.chip_spark;
}

/* ── Escape HTML ──────────────────────────────────────────────── */
function orchEsc(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

const _orchT = (s) => (window.JarvisI18n && window.JarvisI18n.t) ? window.JarvisI18n.t(s) : s;

/* ── Inline markdown parser (code, em) ───────────────────────── */
function orchParseContent(text) {
  return orchEsc(text)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*([^*\n]+)\*/g, '<em>$1</em>')
    .replace(/\n/g, '<br>');
}

/* ── Iniciales del avatar (autor → 1-2 letras uppercase) ─────── */
function orchInitials(author) {
  const palabras = String(author || '').trim().split(/\s+/).filter(Boolean);
  if (palabras.length === 0) return 'T';                  // fallback vacío
  const iniciales = palabras.slice(0, 2).map(p => p[0]).join('').toUpperCase();
  // 'Tú' → 'T' (una sola palabra acentuada): tomamos solo la primera letra
  return iniciales || 'T';
}

/* ── Format HH:MM ────────────────────────────────────────────── */
function orchFmtTime(date) {
  if (!date) return '';
  const d = (date instanceof Date) ? date : new Date(date);
  if (isNaN(d)) return '';
  return d.toLocaleTimeString('es-PY', { hour: '2-digit', minute: '2-digit', hour12: false });
}

/* ══════════════════════════════════════════════════════════════
   OrchestratorPanel
   options: { messages, sphereState, model, onSend, onQuickReply,
              onHeaderAction }
══════════════════════════════════════════════════════════════ */
class OrchestratorPanel {
  constructor(el, opts = {}) {
    this.el              = el;
    this.messages        = opts.messages        || [];
    this.sphereState     = opts.sphereState     || 'idle';
    this.onSend          = opts.onSend          || (() => {});
    this.onQuickReply    = opts.onQuickReply    || (() => {});
    this.onHeaderAction  = opts.onHeaderAction  || (() => {});

    this._userScrolled   = false;
    this._unreadCount    = 0;        // J2: cuántos mensajes nuevos no leídos

    this._build();
    this._refs();
    this._bindEvents();
    this._initConstellation();     // canvas vivo detrás de todo
    this._renderMessages();
    this._updateSphere(this.sphereState);
    this._updateRunningIndicator();
  }

  /* ── HTML skeleton ─────────────────────────────────────────── */
  _build() {
    this.el.className = 'orch-panel';
    this.el.setAttribute('role', 'complementary');
    this.el.setAttribute('aria-label', 'Panel Jarvis');
    this.el.dataset.state = this.sphereState;

    this.el.innerHTML = `
      <!-- CONSTELACIÓN (fondo vivo — Jarvis es el nodo central) -->
      <canvas class="orch-net" id="orch-net" aria-hidden="true"></canvas>
      <div class="orch-halo" aria-hidden="true"></div>
      <div class="orch-vignette" aria-hidden="true"></div>
      <div class="orch-grain" aria-hidden="true"></div>

      <!-- HEADER: la marca Jarvis, prominente y arriba -->
      <header class="orch-header">
        <div class="orch-brand">
          <span class="orch-orb" id="orch-orb" aria-hidden="true"></span>
          <span class="orch-brand-name">Jarvis</span>
        </div>
        <div class="orch-more-wrap">
          <button class="orch-icon-btn" id="orch-btn-more" title="Más opciones" aria-label="Más opciones" aria-haspopup="menu" aria-expanded="false">
            ${ORCH_SVG.more}
          </button>
          <div class="orch-more-menu" id="orch-more-menu" role="menu">
            <button class="orch-more-item" data-action="new-thread" role="menuitem">
              <span>Nueva conversación</span>
            </button>
            <button class="orch-more-item" data-action="history" role="menuitem">
              <span>Historial de conversaciones</span>
            </button>
            <button class="orch-more-item" data-action="export" role="menuitem">
              <span>Exportar conversación</span>
            </button>
            <button class="orch-more-item" data-action="workflows" role="menuitem">
              <span>Ver workflows</span>
            </button>
            <button class="orch-more-item" data-action="clear-history" role="menuitem">
              <span>Limpiar historial</span>
            </button>
          </div>
        </div>
      </header>

      <!-- MESSAGES -->
      <div class="orch-messages-wrap">
        <main class="orch-messages" id="orch-messages" role="log" aria-label="Conversación" aria-live="polite" tabindex="0">
        </main>
        <!-- J2: pill flotante que aparece cuando hay mensajes nuevos y user no
             está al fondo del scroll. Click → scroll al final. -->
        <button class="orch-scroll-down" id="orch-scroll-down" type="button" hidden aria-label="Ir al último mensaje">
          <svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <polyline points="3,5 6,8 9,5"/>
          </svg>
          <span class="orch-scroll-down-count">0 nuevos</span>
        </button>
      </div>

      <!-- COMPOSER -->
      <footer class="orch-composer">

        <!-- Float menus (positioned relative to composer) -->
        <div class="orch-float-menu" id="orch-mention-menu" role="listbox" aria-label="Archivos del proyecto">
        </div>
        <div class="orch-float-menu" id="orch-slash-menu" role="listbox" aria-label="Comandos disponibles">
          <div class="orch-float-menu-item" role="option" tabindex="0" data-val="clear">
            <span>Limpiar chat actual</span><strong>/clear</strong>
          </div>
          <div class="orch-float-menu-item" role="option" tabindex="0" data-val="status">
            <span>Ver estado del workspace</span><strong>/status</strong>
          </div>
        </div>

        <!-- Image preview (shown when image is attached) -->
        <div class="orch-image-preview" id="orch-image-preview">
          <img id="orch-preview-img" alt="Imagen adjunta">
          <button class="orch-preview-remove" id="orch-preview-remove" aria-label="Quitar imagen"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button>
        </div>

        <!-- Composer: barra ÚNICA tipo pill (concepto d4): iconos · textarea · enviar -->
        <div class="orch-input-box">
          <div class="orch-input-left">
            <button class="orch-input-icon-btn" id="orch-icon-attach" title="Adjuntar imagen" aria-label="Adjuntar imagen">${ORCH_SVG.attach}</button>
            <input type="file" id="orch-file-input" accept="image/*" hidden aria-hidden="true">
            <button class="orch-input-icon-btn" id="orch-icon-mention" title="Buscar archivo (@)" aria-label="Buscar archivo">
              ${ORCH_SVG.mention}
            </button>
            <button class="orch-input-icon-btn" id="orch-icon-slash" title="Comandos" aria-label="Comandos de slash">
              ${ORCH_SVG.slash}
            </button>
          </div>
          <textarea
            id="orch-textarea"
            class="orch-textarea"
            placeholder="Escribí o grabá…"
            rows="1"
            aria-label="Mensaje para Jarvis"
            autocomplete="off"
            spellcheck="true"
          ></textarea>
          <button class="orch-send-btn" id="orch-send-btn" disabled aria-label="Enviar mensaje (⌘↵)" title="Enviar (⌘↵)">
            ${ORCH_SVG.send}
          </button>
        </div>

        <p class="orch-hints" aria-hidden="true">↵ enviar · ⇧↵ línea nueva · mantené tu tecla de voz para hablar</p>
      </footer>

      <!-- TELEMETRÍA (abajo): red · agentes · costo -->
      <div class="orch-telemetry" id="orch-telemetry" aria-hidden="true">
        <span class="orch-tl-item"><span class="orch-tl-net-dot" aria-hidden="true"></span><span class="orch-tl-k">RED</span><b id="orch-tl-net">—</b></span>
        <span class="orch-tl-sep"></span>
        <span class="orch-tl-item"><span class="orch-tl-k">AGENTES</span><b id="orch-tl-agents">0</b></span>
        <span class="orch-tl-sep"></span>
        <span class="orch-tl-item"><span class="orch-tl-k">COSTO</span><b id="orch-tl-cost">$0.00</b></span>
      </div>
    `;
  }

  /* ── Cache refs ────────────────────────────────────────────── */
  _refs() {
    const q = id => this.el.querySelector(id);
    this.$net            = q('#orch-net');            // canvas de la constelación
    this.$messages       = q('#orch-messages');
    this.$textarea       = q('#orch-textarea');
    this._wfCards        = new Map();
    this.$sendBtn        = q('#orch-send-btn');
    this.$mentionMenu    = q('#orch-mention-menu');
    this.$slashMenu      = q('#orch-slash-menu');
    this.$btnMore        = q('#orch-btn-more');
    this.$moreMenu       = q('#orch-more-menu');
    // Telemetría (abajo)
    this.$tlNet          = q('#orch-tl-net');
    this.$tlAgents       = q('#orch-tl-agents');
    this.$tlCost         = q('#orch-tl-cost');
    // Image attach
    this.$iconAttach     = q('#orch-icon-attach');
    this.$fileInput      = q('#orch-file-input');
    this.$imgPreview     = q('#orch-image-preview');
    this.$previewImg     = q('#orch-preview-img');
    this.$previewRm      = q('#orch-preview-remove');
    this._pendingImg     = null;
  }

  /* ── Events ────────────────────────────────────────────────── */
  _bindEvents() {
    const ta = this.$textarea;

    // Textarea: autosize + send-state + menu triggers
    ta.addEventListener('input', () => {
      this._autosize();
      this._updateSendBtn();
      this._checkMenuTriggers();
    });

    // Keyboard shortcuts
    ta.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this._send(); return; }
      if (e.key === 'Escape')               { this._closeMenus(); return; }
      if (e.key === 'Tab' && (this.$mentionMenu.classList.contains('open') || this.$slashMenu.classList.contains('open'))) {
        e.preventDefault();
        const firstItem = this.el.querySelector('.orch-float-menu.open .orch-float-menu-item');
        firstItem?.focus();
      }
    });

    // Push-to-talk: el listener global vive en workspace.js (instalarPushToTalk).
    // workspace.js dispara window._orchOnMicHold / window._orchOnMicRelease
    // según el binding configurado (default Alt). El panel solo reacciona
    // a esos callbacks vía start/stopListening (que ya están cableados).

    // Send button
    this.$sendBtn.addEventListener('click', () => this._send());

    // Mic: ya no hay botón en el composer. La voz se activa SIEMPRE desde la
    // tecla de PTT global (configurable en Controles del sidebar).

    // Scroll detection
    this.$messages.addEventListener('scroll', () => {
      const c = this.$messages;
      // Si el pane está OCULTO (dock en otra pestaña → display:none) las dims son
      // 0 y el cálculo daría siempre "al fondo" (false), corrompiendo el flag y
      // haciendo que al volver el chat salte abajo. Ignoramos esos eventos para
      // preservar la posición real del usuario. (bug "se va todo para abajo".)
      if (c.clientHeight === 0) return;
      this._userScrolled = (c.scrollTop + c.clientHeight) < (c.scrollHeight - 80);
      // Si el user vuelve al fondo, resetear contador de no leídos
      if (!this._userScrolled) this._resetUnreadIndicator();
    }, { passive: true });

    // J2: scroll-down button → bajar al final y resetear contador
    const scrollDownBtn = this.el.querySelector('#orch-scroll-down');
    scrollDownBtn?.addEventListener('click', () => {
      this.$messages.scrollTop = this.$messages.scrollHeight;
      this._resetUnreadIndicator();
    });

    // Mention menu items are populated dynamically in _loadFileMentions()

    // Slash menu items
    this.$slashMenu.querySelectorAll('.orch-float-menu-item').forEach(item => {
      item.addEventListener('click',   () => this._pickSlash(item.dataset.val));
      item.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); this._pickSlash(item.dataset.val); }
        if (e.key === 'Escape') { this._closeMenus(); ta.focus(); }
      });
    });

    // Composer chips
    this.el.querySelectorAll('.orch-composer-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const a = chip.dataset.chip;
        if (a === 'mention') { ta.value += '@'; ta.focus(); this._checkMenuTriggers(); }
      });
    });

    // Image attach
    this.$iconAttach?.addEventListener('click', () => this.$fileInput?.click());
    this.$fileInput?.addEventListener('change', e => {
      this._handleImageFile(e.target.files[0]);
      e.target.value = ''; // reset so same file can be re-selected
    });
    this.$previewRm?.addEventListener('click', () => this._clearImage());

    // Drag & drop de imágenes en todo el panel → reusa _handleImageFile
    this.el.addEventListener('dragover', e => {
      // Solo activar feedback si hay archivos siendo arrastrados (no texto del propio textarea)
      if (e.dataTransfer.types?.includes('Files')) {
        e.preventDefault();
        this.el.classList.add('drag-over');
      }
    });
    this.el.addEventListener('dragleave', e => {
      // Solo quitar si salimos del panel (no de un hijo interno)
      if (!this.el.contains(e.relatedTarget)) this.el.classList.remove('drag-over');
    });
    this.el.addEventListener('drop', e => {
      e.preventDefault();
      this.el.classList.remove('drag-over');
      const file = [...(e.dataTransfer.files || [])].find(f => f.type.startsWith('image/'));
      if (file) this._handleImageFile(file);
    });

    // Icon buttons del input bar
    this.el.querySelector('#orch-icon-mention')?.addEventListener('click', () => {
      ta.value += '@'; ta.focus(); this._checkMenuTriggers();
    });
    this.el.querySelector('#orch-icon-slash')?.addEventListener('click', () => {
      if (!ta.value.trim()) { ta.value = '/'; ta.focus(); this._checkMenuTriggers(); }
    });

    // Header: solo el menú "⋯" (historial/nueva sesión viven adentro, ya no
    // como botones sueltos del header — pedido del usuario 2026-07-04).
    this.$btnMore?.addEventListener('click', e => {
      e.stopPropagation();
      this._toggleMoreMenu();
    });
    this.$moreMenu?.querySelectorAll('.orch-more-item').forEach(item => {
      item.addEventListener('click', () => {
        const action = item.dataset.action;
        this._closeMoreMenu();
        this.onHeaderAction(action);
      });
    });

    // Close menus on outside click
    document.addEventListener('click', e => {
      if (!this.el.contains(e.target)) {
        this._closeMenus();
        this._closeMoreMenu();
      }
    }, true);
  }

  _toggleMoreMenu() {
    const open = this.$moreMenu.classList.toggle('open');
    this.$btnMore.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  _closeMoreMenu() {
    this.$moreMenu?.classList.remove('open');
    this.$btnMore?.setAttribute('aria-expanded', 'false');
  }

  /* ── Textarea autosize ─────────────────────────────────────── */
  _autosize() {
    const ta = this.$textarea;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 128) + 'px';
  }

  _updateSendBtn() {
    this.$sendBtn.disabled = !this.$textarea.value.trim() && !this._pendingImg;
  }

  /* ── Menu triggers ─────────────────────────────────────────── */
  _checkMenuTriggers() {
    const val    = this.$textarea.value;
    const pos    = this.$textarea.selectionStart;
    const before = val.slice(0, pos);

    const atMatch = /@(\w*)$/.exec(before);
    if (atMatch) {
      this._closeMenu(this.$slashMenu);
      this._loadFileMentions(atMatch[1]); // async, fire-and-forget
    } else if (/^\/\w*$/.test(before.trimStart())) {
      this._openMenu(this.$slashMenu);
      this._closeMenu(this.$mentionMenu);
    } else {
      this._closeMenus();
    }
  }

  async _loadFileMentions(query) {
    // Skeleton: 3 filas shimmer mientras esperamos la respuesta de _orchGetFiles.
    this.$mentionMenu.innerHTML = `
      <div class="orch-float-menu-item orch-mention-skeleton" aria-hidden="true">
        <div class="skeleton" style="height:12px;width:55%"></div>
        <div class="skeleton" style="height:10px;width:25%"></div>
      </div>
      <div class="orch-float-menu-item orch-mention-skeleton" aria-hidden="true">
        <div class="skeleton" style="height:12px;width:42%"></div>
        <div class="skeleton" style="height:10px;width:30%"></div>
      </div>
      <div class="orch-float-menu-item orch-mention-skeleton" aria-hidden="true">
        <div class="skeleton" style="height:12px;width:60%"></div>
        <div class="skeleton" style="height:10px;width:20%"></div>
      </div>`;
    this._openMenu(this.$mentionMenu);

    const archivos = await window._orchGetFiles?.(query) ?? [];
    this.$mentionMenu.innerHTML = '';
    if (archivos.length === 0) { this._closeMenu(this.$mentionMenu); return; }

    for (const f of archivos) {
      const item = document.createElement('div');
      item.className = 'orch-float-menu-item';
      item.setAttribute('role', 'option');
      item.setAttribute('tabindex', '0');
      const name = f.path.split('/').pop();
      item.innerHTML = `<span>${orchEsc(name)}</span><strong>${orchEsc(f.path)}</strong>`;
      const pick = () => this._pickFile(f.path);
      item.addEventListener('click', pick);
      item.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); }
        if (e.key === 'Escape') { this._closeMenus(); this.$textarea.focus(); }
      });
      this.$mentionMenu.appendChild(item);
    }
    this._openMenu(this.$mentionMenu);
  }

  _pickFile(path) {
    const ta  = this.$textarea;
    const pos = ta.selectionStart;
    const rep = ta.value.slice(0, pos).replace(/@\w*$/, path + ' ');
    ta.value  = rep + ta.value.slice(pos);
    ta.selectionStart = ta.selectionEnd = rep.length;
    ta.focus();
    this._autosize();
    this._updateSendBtn();
    this._closeMenus();
  }

  _openMenu(menu)  { menu.classList.add('open'); }
  _closeMenu(menu) { menu.classList.remove('open'); }
  _closeMenus()    { this._closeMenu(this.$mentionMenu); this._closeMenu(this.$slashMenu); }

  _pickSlash(val) {
    this._closeMenus();
    // Comandos con efecto inmediato:
    if (val === 'clear') {
      this.$textarea.value = '';
      this._autosize();
      this._updateSendBtn();
      this.onHeaderAction('new-thread');
      return;
    }
    // El resto se inserta como texto y se manda al backend
    this.$textarea.value = `/${val} `;
    this._autosize();
    this._updateSendBtn();
    this.$textarea.focus();
  }

  /* ── Send ──────────────────────────────────────────────────── */
  _send() {
    const text = this.$textarea.value.trim();
    if (!text && !this._pendingImg) return;
    const img = this._pendingImg;
    this._clearImage();
    this.$textarea.value = '';
    this._autosize();
    this._updateSendBtn();
    this._closeMenus();
    this.onSend(text, img?.base64, img?.mediaType);
  }

  /* ── Image attach ──────────────────────────────────────────── */
  _handleImageFile(file) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = e => {
      const dataUrl  = e.target.result;
      const [header, data] = dataUrl.split(',');
      const mediaType = header.match(/data:([^;]+)/)?.[1] || 'image/jpeg';
      this._pendingImg = { base64: data, mediaType };
      if (this.$previewImg) this.$previewImg.src = dataUrl;
      if (this.$imgPreview) this.$imgPreview.style.display = 'flex';
      // H2: marcar el composer para que el CSS active el glow extra en send
      this.el.querySelector('.orch-composer')?.setAttribute('data-has-image', 'true');
      this._updateSendBtn();
      this.$textarea.focus();
    };
    reader.readAsDataURL(file);
  }

  _clearImage() {
    this._pendingImg = null;
    if (this.$previewImg) this.$previewImg.src = '';
    if (this.$imgPreview) this.$imgPreview.style.display = 'none';
    this.el.querySelector('.orch-composer')?.removeAttribute('data-has-image');
    this._updateSendBtn();
  }

  /* ══════════════════════════════════════════════════════════
     PUBLIC API
  ══════════════════════════════════════════════════════════ */

  setSphereState(state) {
    this.sphereState = state;
    this._updateSphere(state);   // la constelación (rAF) lee this.sphereState
    // E2: typing indicator visible solo cuando Jarvis está "produciendo" output
    // Y ya hay conversación. Con el HERO a la vista (primer contacto), NO metemos
    // la burbuja suelta en la esquina: el hero mismo pasa a "Pensando" (ver
    // _updateHeroVoice) para no romper el momento editorial de la constelación.
    const heroUp = !!this.$messages?.querySelector('.orch-empty');
    if ((state === 'processing' || state === 'responding') && !heroUp) {
      this._showTyping();
    } else {
      this._hideTyping();
    }
    // Estado de voz en el hero: "Te estoy escuchando…" + transcript en vivo
    // (listening) / "Pensando" (processing) sobre la constelación — el momento
    // que pedía el concepto d4. Autocontenido: reacciona al estado que ya llega.
    this._updateHeroVoice(state);
    // H3: composer marca data-recording cuando estamos en listening — CSS
    // muestra un pulso violeta en el border del input box (no movemos la
    // waveform DOM porque eso requiere reparentar; basta con el feedback
    // visual en el lugar donde el usuario está mirando).
    const composer = this.el.querySelector('.orch-composer');
    if (composer) {
      if (state === 'listening') composer.setAttribute('data-recording', 'true');
      else composer.removeAttribute('data-recording');
    }
  }

  // Hero editorial ⇄ estado de voz. Con el hero a la vista, mientras Jarvis
  // escucha reemplazamos el saludo por "Te estoy escuchando…" + el transcript
  // en vivo (lo lee del textarea, que es donde workspace.js va volcando el STT
  // parcial — así no tocamos el pipeline de voz). Al procesar → "Pensando".
  _updateHeroVoice(state) {
    const empty = this.$messages?.querySelector('.orch-empty');
    if (!empty) { this._stopTranscriptEcho(); return; }
    const label = empty.querySelector('.orch-voice-label');
    const tr    = empty.querySelector('.orch-voice-transcript');
    if (state === 'listening') {
      empty.dataset.voice = 'listening';
      if (label) label.textContent = 'Te estoy escuchando…';
      this._startTranscriptEcho(tr);
    } else if (state === 'processing' || state === 'responding') {
      empty.dataset.voice = 'thinking';
      if (label) label.textContent = 'Pensando';
      this._stopTranscriptEcho();
      if (tr) tr.textContent = (this.$textarea?.value || '').trim();
    } else {                                  // idle → vuelve el saludo
      delete empty.dataset.voice;
      this._stopTranscriptEcho();
      if (tr) tr.textContent = '';
    }
  }

  _startTranscriptEcho(tr) {
    this._stopTranscriptEcho();
    if (!tr) return;
    const tick = () => {
      if (!tr.isConnected) { this._stopTranscriptEcho(); return; }   // hero reemplazado por el chat
      tr.textContent = (this.$textarea?.value || '').trim();
      this._echoRaf = requestAnimationFrame(tick);
    };
    tick();
  }
  _stopTranscriptEcho() {
    if (this._echoRaf) cancelAnimationFrame(this._echoRaf);
    this._echoRaf = null;
  }

  // E2: insertar/quitar burbuja con 3 dots animados al final del log.
  // Se llama desde setSphereState. Es idempotente.
  _showTyping() {
    if (!this.$messages) return;
    if (this.$messages.querySelector('.orch-typing')) return; // ya está
    this.$messages.querySelector('.orch-empty')?.remove();    // salir del hero
    const el = document.createElement('article');
    el.className = 'orch-msg orch-typing';
    el.dataset.role = 'jarvis';
    el.setAttribute('aria-label', 'Jarvis está escribiendo');
    el.innerHTML = `
      <div class="orch-msg-head">
        <div class="orch-avatar orch-avatar-jarvis" aria-hidden="true">J</div>
        <div class="orch-msg-meta">
          <span class="orch-msg-author">Jarvis</span>
          <span class="orch-msg-badge">orchestrator</span>
        </div>
      </div>
      <div class="orch-typing-bubble" role="status" aria-live="polite">
        <span class="orch-typing-dot"></span>
        <span class="orch-typing-dot"></span>
        <span class="orch-typing-dot"></span>
      </div>`;
    this.$messages.appendChild(el);
    this._syncConv();
    if (!this._userScrolled) {
      requestAnimationFrame(() => { this.$messages.scrollTop = this.$messages.scrollHeight; });
    }
  }
  _hideTyping() {
    this.$messages?.querySelector('.orch-typing')?.remove();
    this._syncConv();
  }

  addMessage(msg) {
    // Si Jarvis manda un mensaje real, el typing indicator deja de tener sentido
    if (msg.role === 'jarvis') { this._hideTyping(); this._refrescarUso(); }
    // Transición hero → chat: si estábamos en el hero, reconstruir (el hero se
    // reemplaza por el day-divider + el primer mensaje).
    const enHero = !!this.$messages.querySelector('.orch-empty');
    this.messages.push(msg);
    if (enHero) this._renderMessages();
    else this._appendMessage(msg);
    this._updateRunningIndicator();
    this._syncConv();
    if (!this._userScrolled) {
      requestAnimationFrame(() => {
        this.$messages.scrollTop = this.$messages.scrollHeight;
      });
    } else {
      // J2: el user no está al fondo — incrementar contador de no leídos
      this._unreadCount++;
      this._renderUnreadIndicator();
    }
  }

  // Actualiza el contenido de un mensaje YA renderizado (por id) — lo usa el
  // streaming del orquestador para revelar la respuesta token a token sin
  // re-crear la burbuja. Solo re-renderiza el cuerpo (no toca avatar/head).
  updateMessage(id, content) {
    const m = this.messages.find(x => x.id === id);
    if (m) m.content = content;
    const art = this.$messages?.querySelector(`article[data-id="${id}"]`);
    if (art) {
      const body = art.querySelector('.orch-msg-body');
      if (body) body.innerHTML = orchParseContent(content);
    }
    if (!this._userScrolled) {
      requestAnimationFrame(() => { this.$messages.scrollTop = this.$messages.scrollHeight; });
    }
  }

  _renderUnreadIndicator() {
    const btn = this.el.querySelector('#orch-scroll-down');
    if (!btn) return;
    if (this._unreadCount > 0) {
      const lbl = btn.querySelector('.orch-scroll-down-count');
      if (lbl) lbl.textContent = `${this._unreadCount} ${this._unreadCount === 1 ? 'nuevo' : 'nuevos'}`;
      btn.hidden = false;
    } else {
      btn.hidden = true;
    }
  }
  _resetUnreadIndicator() {
    this._unreadCount = 0;
    this._renderUnreadIndicator();
  }

  setMessages(msgs) {
    this.messages = [...msgs];
    this._renderMessages();
    this._updateRunningIndicator();
    this._refrescarUso();   // cambió de proyecto/thread → refrescar costo
  }

  // ── Uso/costo del orquestador (Sprint 3) ───────────────────────
  // Lee el projectId de la URL (igual que workspace.js) — así esta sección
  // es autosuficiente y no necesita un hook en el shell.
  _fmtTokens(n) {
    n = n || 0;
    if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'k';
    return String(n);
  }

  async _refrescarUso() {
    const pid = new URLSearchParams(location.search).get('id');
    const el = this.$tlCost;                             // COSTO en la telemetría de abajo
    if (!pid || !el) return;
    try {
      const url = `/api/orchestrator/uso/${pid}`;
      const r = window.apiFetch ? await window.apiFetch(url) : await fetch(url);
      if (!r.ok) return;
      const u = await r.json();
      const tot = (u.input_tokens || 0) + (u.output_tokens || 0);
      const c = u.costo_usd ?? 0;
      el.textContent = `$${c.toFixed(2)}`;
      const tel = this.el.querySelector('#orch-telemetry');
      if (tel) tel.title = u.llamadas
        ? _orchT('Uso del orquestador en este proyecto: {c} · {t} tokens · {n} llamada(s)')
            .replace('{c}', '$' + c.toFixed(4))
            .replace('{t}', this._fmtTokens(tot))
            .replace('{n}', u.llamadas)
        : '';
    } catch (_) { /* silencioso: el costo es informativo */ }
  }

  // Limpia mensajes y workflow cards. Útil al iniciar un thread nuevo.
  clearMessages() {
    this.messages = [];
    this._wfCards.forEach(card => card.remove());
    this._wfCards.clear();
    this._renderMessages();
    this._updateRunningIndicator();
  }

  // Devuelve copia plana de los mensajes (para guardar/exportar).
  getMessages() {
    return this.messages.map(m => ({
      role:      m.role,
      author:    m.author,
      content:   m.content,
      timestamp: m.timestamp,
    }));
  }

  // Registra un elemento DOM como workflow card y lo adjunta a $messages.
  // La card persiste en cambios de proyecto y llamadas a setMessages().
  addWorkflowCard(id, el) {
    this.$messages.querySelector('.orch-empty')?.remove();    // salir del hero
    this._wfCards.set(String(id), el);
    this.$messages.appendChild(el);
    this._syncConv();
    if (!this._userScrolled) {
      requestAnimationFrame(() => {
        this.$messages.scrollTop = this.$messages.scrollHeight;
      });
    }
  }

  // Devuelve el elemento DOM de una workflow card por su ID, o null.
  findWorkflowCard(id) {
    return this._wfCards.get(String(id)) ?? null;
  }

  // Elimina una workflow card del registro y del DOM.
  removeWorkflowCard(id) {
    const card = this._wfCards.get(String(id));
    if (card) {
      this._wfCards.delete(String(id));
      card.remove();
    }
    // Si se vació todo, volver al hero.
    if (this.messages.length === 0 && this._wfCards.size === 0
        && !this.$messages.querySelector('.orch-empty')) this._renderMessages();
    this._syncConv();
  }

  /* ══════════════════════════════════════════════════════════
     PRIVATE RENDER
  ══════════════════════════════════════════════════════════ */

  _updateSphere(state) {
    // El estado ahora se comunica por la CONSTELACIÓN (color + ondas) + el orbe
    // de la marca. Sin chip "EN REPOSO" (pedido del usuario 2026-07-04).
    this.el.dataset.state = state;                       // .orch-panel[data-state]
    const orb = this.el.querySelector('#orch-orb');
    if (orb) orb.dataset.state = state;
    // Al entrar a un estado activo, disparamos una onda que recorre la red.
    if (state === 'listening' || state === 'processing' || state === 'responding') {
      this._spawnWave(state === 'processing' ? 0.55 : state === 'responding' ? 0.9 : 0.75);
    }
  }

  // AGENTES en la telemetría = pasos de workflow corriendo/pendientes (agentes
  // realmente trabajando). Se llama en cada cambio de mensajes.
  _updateRunningIndicator() {
    let n = 0;
    this.messages.forEach(m => {
      (m.actionPlan?.steps || []).forEach(s => {
        if (s && (s.status === 'pending' || s.status === 'running')) n++;
      });
    });
    if (this.$tlAgents) this.$tlAgents.textContent = String(n);
  }

  // data-conv sobre .orch-panel: '1' cuando hay conversación (la red se atenúa
  // para dar contraste al chat) · '0' en el hero (la red brilla plena).
  _syncConv() {
    const activo = !this._isHeroState()
                 || !!this.$messages?.querySelector('.orch-typing');
    this.el.dataset.conv = activo ? '1' : '0';
  }

  // El saludo de bienvenida ("¿Qué hacemos, señor?") lo inyecta workspace.js como
  // un MENSAJE (setMessages / nuevoThread) — pero en el diseño d4 ese saludo ES el
  // HERO editorial gigante sobre la constelación, no una burbuja de chat. Detectamos
  // ese caso para mostrar el hero (y mantener la red brillante) hasta que haya un
  // turno REAL del usuario. Sin esto el hero no se veía NUNCA en la app real.
  _isWelcomeMsg(m) {
    if (!m || m.role !== 'jarvis') return false;
    if (String(m.id || '').startsWith('welcome')) return true;
    const c = (m.content || '').trim().replace(/\s+/g, ' ');
    return c === '¿Qué hacemos, señor?' || c === 'What shall we build, sir?';
  }
  _isHeroState() {
    if (this._wfCards.size > 0) return false;
    if (this.messages.length === 0) return true;
    return this.messages.length === 1 && this._isWelcomeMsg(this.messages[0]);
  }

  /* ══════════════════════════════════════════════════════════
     CONSTELACIÓN — Jarvis es el nodo central de una red viva.
     Al hablar, ondas recorren la red y encienden los nodos; la
     red vibra con tu voz real (window._orchVoiceLevel/_orchVoiceBins,
     publicados por el analyser del PTT en workspace.js).
     Portado del concepto D4 (preview-orquestador/d4.html).
  ══════════════════════════════════════════════════════════ */
  _initConstellation() {
    const cv = this.$net;
    if (!cv || !cv.getContext) return;
    const ctx = cv.getContext('2d');
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
    const DPR = Math.min(window.devicePixelRatio || 1, 2);
    const EMPTY_BINS = new Array(64).fill(0);

    // Colores oklch que ESPEJAN los tokens (el canvas no lee var(--ob-*)).
    const COL = {
      accent: a => `oklch(61% 0.22 293 / ${a})`,
      accFg:  a => `oklch(80% 0.14 293 / ${a})`,
      info:   a => `oklch(78% 0.15 230 / ${a})`,
      fg:     a => `oklch(97% 0.006 300 / ${a})`,
      line:   a => `oklch(44% 0.026 300 / ${a})`,
    };

    // PRNG determinista → misma red en cada carga
    const mulberry = seed => () => {
      seed |= 0; seed = seed + 0x6D2B79F5 | 0;
      let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
    const rng = mulberry(20260704);

    const RINGS = [
      { r: 0.17, n: 5 }, { r: 0.30, n: 7 }, { r: 0.44, n: 9 }, { r: 0.58, n: 10 },
      { r: 0.72, n: 8 }, { r: 0.86, n: 7 }, { r: 1.00, n: 6 },
    ];
    const nodes = [];
    nodes.push({ nx: 0, ny: 0, r: 0, central: true, size: 1, bin: 0, tw: 0, da: 0, dsx: 0.11, dsy: 0.09, dpx: 0, dpy: 0, lit: 0, x: 0, y: 0 });
    const addNode = (nx, ny, r) => nodes.push({
      nx, ny, r, central: false,
      size: 0.32 + (1 - Math.min(r, 1)) * 0.5 + rng() * 0.22,
      bin: Math.min(63, Math.floor(r * 30) + (nodes.length * 7) % 22),
      tw: rng() * Math.PI * 2,
      da: 0.008 + rng() * 0.014, dsx: 0.05 + rng() * 0.12, dsy: 0.05 + rng() * 0.12,
      dpx: rng() * Math.PI * 2, dpy: rng() * Math.PI * 2, lit: 0, x: 0, y: 0,
    });
    RINGS.forEach(ring => {
      const off = rng() * Math.PI * 2;
      for (let i = 0; i < ring.n; i++) {
        const a = off + (i / ring.n) * Math.PI * 2 + (rng() - 0.5) * 0.34;
        const rr = ring.r + (rng() - 0.5) * 0.055;
        addNode(Math.cos(a) * rr, Math.sin(a) * rr, rr);
      }
    });
    for (let s = 0; s < 9; s++) {
      const a2 = rng() * Math.PI * 2, rr2 = 1.12 + rng() * 0.30;
      addNode(Math.cos(a2) * rr2, Math.sin(a2) * rr2, rr2);
    }
    let maxR = 0; nodes.forEach(n => { if (n.r > maxR) maxR = n.r; });

    const edges = [], seen = {};
    for (let i = 0; i < nodes.length; i++) {
      const ds = [];
      for (let j = 0; j < nodes.length; j++) {
        if (j === i) continue;
        const dx = nodes[i].nx - nodes[j].nx, dy = nodes[i].ny - nodes[j].ny;
        ds.push([dx * dx + dy * dy, j]);
      }
      ds.sort((a, b) => a[0] - b[0]);
      const k = nodes[i].central ? 6 : 3;
      for (let m = 0; m < k && m < ds.length; m++) {
        const d = Math.sqrt(ds[m][0]), jj = ds[m][1];
        if (m > 0 && d > 0.44) break;
        const key = Math.min(i, jj) + '-' + Math.max(i, jj);
        if (seen[key]) continue; seen[key] = 1;
        edges.push({ a: i, b: jj, base: Math.max(0.08, 0.2 - d * 0.22) });
      }
    }

    // Telemetría RED = cantidad de nodos
    if (this.$tlNet) this.$tlNet.textContent = String(nodes.length);

    this._nodes = nodes; this._edges = edges; this._maxR = maxR;
    this._waves = [];
    this._cyanEnv = 0; this._lastWave = -9999;
    this._netCtx = ctx; this._netReduce = reduce;

    let W = 0, H = 0, cx = 0, cy = 0, scale = 0;
    const sizeCanvas = () => {
      const w = this.el.clientWidth || 320, h = this.el.clientHeight || 480;
      W = cv.width = Math.round(w * DPR); H = cv.height = Math.round(h * DPR);
      cv.style.width = w + 'px'; cv.style.height = h + 'px';
      cx = W * 0.5; cy = H * 0.42;
      scale = Math.min(W, H) * 0.62;
    };
    sizeCanvas();
    if ('ResizeObserver' in window) {
      this._netRO = new ResizeObserver(() => sizeCanvas());
      this._netRO.observe(this.el);
    } else {
      window.addEventListener('resize', sizeCanvas);
    }

    const WAVE_TRAVEL = 1500;
    const draw = () => {
      this._netRaf = requestAnimationFrame(draw);
      // Panel oculto (dock en otra pestaña) → no dibujar (ahorra CPU)
      if (this.el.clientWidth === 0) return;
      if (cv.width !== Math.round(this.el.clientWidth * DPR)) sizeCanvas();

      const now = performance.now();
      const state = this.sphereState;
      const level = Math.max(0, Math.min(1, window._orchVoiceLevel || 0));
      const bins = window._orchVoiceBins || EMPTY_BINS;
      ctx.clearRect(0, 0, W, H);

      const listening = state === 'listening';
      this._cyanEnv += ((listening ? 1 : 0) - this._cyanEnv) * 0.08;
      const cyanEnv = this._cyanEnv;

      if (!reduce) {
        const interval = state === 'listening' ? 820 : state === 'responding' ? 1150
                       : state === 'processing' ? 640 : 0;
        if (interval && now - this._lastWave > interval) {
          this._lastWave = now;
          this._spawnWave(state === 'processing' ? 0.5 : state === 'responding' ? 0.85 : 0.7);
        }
      }

      const t = now / 1000;
      for (let a = 0; a < nodes.length; a++) {
        const n = nodes[a], wob = reduce ? 0 : n.da;
        n.x = cx + (n.nx + Math.sin(t * n.dsx + n.dpx) * wob) * scale;
        n.y = cy + (n.ny + Math.cos(t * n.dsy * 0.9 + n.dpy) * wob) * scale;
      }
      const waves = this._waves;
      for (let w = 0; w < waves.length; w++) {
        waves[w].wr = ((now - waves[w].t0) / WAVE_TRAVEL) * maxR;
        if (waves[w].wr > maxR * 1.18) { waves.splice(w, 1); w--; }
      }

      const sig = 0.055;
      for (let b2 = 0; b2 < nodes.length; b2++) {
        const nd = nodes[b2];
        nd.lit *= 0.92;
        let ig = 0;
        for (let wv = 0; wv < waves.length; wv++) {
          const dr = Math.abs(nd.r - waves[wv].wr);
          let g = Math.exp(-(dr * dr) / (2 * sig * sig)) * waves[wv].strength;
          g *= (1 - Math.min((now - waves[wv].t0) / WAVE_TRAVEL, 1) * 0.35);
          if (g > ig) ig = g;
        }
        if (ig > nd.lit) nd.lit = ig;
      }

      ctx.globalCompositeOperation = 'lighter';
      const glow = 0.5 + level * 0.9;
      const strokeSeg = (u, v) => { ctx.beginPath(); ctx.moveTo(u.x, u.y); ctx.lineTo(v.x, v.y); ctx.stroke(); };
      for (let e = 0; e < edges.length; e++) {
        const na = nodes[edges[e].a], nb = nodes[edges[e].b];
        const litL = (na.lit + nb.lit) * 0.5;
        let alpha = reduce ? edges[e].base * 0.9 + litL * 0.4 : edges[e].base * (0.55 + glow * 0.5) + litL * 0.5;
        if (alpha < 0.015) continue;
        if (alpha > 0.9) alpha = 0.9;
        ctx.lineWidth = (0.8 + litL * 1.6) * DPR;
        if (cyanEnv < 0.98) { ctx.strokeStyle = COL.line(alpha * (1 - cyanEnv) * 0.8 + litL * 0.3 * (1 - cyanEnv)); strokeSeg(na, nb); }
        if (litL > 0.04 || cyanEnv > 0.02) {
          ctx.strokeStyle = cyanEnv > 0.02 ? COL.info(alpha * cyanEnv * 0.7 + litL * 0.3) : COL.accent(litL * 0.45);
          strokeSeg(na, nb);
        }
      }

      for (let r2 = 0; r2 < waves.length; r2++) {
        const rr3 = waves[r2].wr * scale;
        if (rr3 <= 2) continue;
        const wage2 = (now - waves[r2].t0) / WAVE_TRAVEL;
        const ra = (1 - Math.min(wage2, 1)) * 0.24 * waves[r2].strength;
        ctx.lineWidth = 1.6 * DPR;
        ctx.strokeStyle = cyanEnv > 0.4 ? COL.info(ra) : COL.accFg(ra);
        ctx.beginPath(); ctx.arc(cx, cy, rr3, 0, Math.PI * 2); ctx.stroke();
      }

      const drawGlow = (gx, gy, gr, bri) => {
        const aV = Math.min(bri, 1.4);
        if (cyanEnv < 0.98) {
          const g = ctx.createRadialGradient(gx, gy, 0, gx, gy, gr);
          g.addColorStop(0, COL.accFg(0.5 * aV * (1 - cyanEnv)));
          g.addColorStop(0.4, COL.accent(0.18 * aV * (1 - cyanEnv)));
          g.addColorStop(1, COL.accent(0));
          ctx.fillStyle = g; ctx.beginPath(); ctx.arc(gx, gy, gr, 0, Math.PI * 2); ctx.fill();
        }
        if (cyanEnv > 0.02) {
          const g2 = ctx.createRadialGradient(gx, gy, 0, gx, gy, gr);
          g2.addColorStop(0, COL.info(0.5 * aV * cyanEnv));
          g2.addColorStop(0.4, COL.info(0.18 * aV * cyanEnv));
          g2.addColorStop(1, COL.info(0));
          ctx.fillStyle = g2; ctx.beginPath(); ctx.arc(gx, gy, gr, 0, Math.PI * 2); ctx.fill();
        }
      };
      for (let p = 0; p < nodes.length; p++) {
        const no = nodes[p]; if (no.central) continue;
        const rNorm = Math.min(no.r / maxR, 1);
        const twk = reduce ? 0.5 : (0.5 + 0.5 * Math.sin(t * 0.6 + no.tw)) * 0.9;
        const binB = (bins[no.bin] || 0) * Math.pow(1 - rNorm, 1.6) * 0.7;
        let B = 0.14 + twk * 0.16 + no.lit * 1.0 + binB + level * 0.12;
        if (B > 1.5) B = 1.5;
        drawGlow(no.x, no.y, (2.4 + no.size * 5.0 + B * 7.5) * DPR, B);
      }

      const cn = nodes[0];
      const beat = 1 + level * 0.55 + Math.sin(t * 2.2) * 0.04 + cn.lit * 0.4;
      drawGlow(cn.x, cn.y, (10 + level * 10) * DPR * beat * 2.6, 1.4);

      ctx.globalCompositeOperation = 'source-over';
      const dotColor = (al, lit) => lit > 0.35
        ? COL.fg(Math.min(0.6 + lit * 0.5, 1))
        : (cyanEnv > 0.4 ? COL.info(al) : COL.accFg(al));
      for (let q = 0; q < nodes.length; q++) {
        const nq = nodes[q]; if (nq.central) continue;
        const rN2 = Math.min(nq.r / maxR, 1);
        const twk2 = reduce ? 0.5 : (0.5 + 0.5 * Math.sin(t * 0.6 + nq.tw)) * 0.9;
        let B2 = 0.16 + twk2 * 0.18 + nq.lit * 1.0 + (bins[nq.bin] || 0) * Math.pow(1 - rN2, 1.6) * 0.6 + level * 0.12;
        if (B2 > 1) B2 = 1;
        const dot = (0.7 + nq.size * 1.5 + B2 * 1.4) * DPR;
        ctx.beginPath(); ctx.arc(nq.x, nq.y, dot, 0, Math.PI * 2);
        ctx.fillStyle = dotColor(0.35 + B2 * 0.65, nq.lit); ctx.fill();
      }

      ctx.beginPath(); ctx.arc(cn.x, cn.y, (5 + level * 4) * DPR * beat, 0, Math.PI * 2);
      const cg = ctx.createRadialGradient(cn.x, cn.y, 0, cn.x, cn.y, (6 + level * 5) * DPR * beat);
      cg.addColorStop(0, COL.fg(0.98));
      cg.addColorStop(0.5, cyanEnv > 0.4 ? COL.info(0.85) : COL.accFg(0.9));
      cg.addColorStop(1, cyanEnv > 0.4 ? COL.info(0) : COL.accent(0));
      ctx.fillStyle = cg; ctx.fill();
      ctx.beginPath(); ctx.arc(cn.x, cn.y, (9 + level * 8) * DPR * beat, 0, Math.PI * 2);
      ctx.lineWidth = 1.4 * DPR;
      ctx.strokeStyle = cyanEnv > 0.4 ? COL.info(0.4 + level * 0.4) : COL.accFg(0.35 + level * 0.4);
      ctx.stroke();
    };
    this._netRaf = requestAnimationFrame(draw);
  }

  _spawnWave(strength) {
    if (this._netReduce || !this._waves) return;
    this._waves.push({ t0: performance.now(), strength, wr: 0 });
    if (this._waves.length > 6) this._waves.shift();
  }

  _renderMessages() {
    this.$messages.innerHTML = '';
    this._syncConv();

    // I1: empty state editorial cuando no hay mensajes y no hay workflow cards.
    // Muestra la mark "J" grande + una frase editorial — coherente con el home/sidebar.
    if (this._isHeroState()) {
      // Hero de la constelación: el saludo gigante pisa la red (el nodo central
      // = Jarvis late detrás). Quick actions cablean a onQuickReply.
      const empty = document.createElement('div');
      empty.className = 'orch-empty';
      empty.innerHTML = `
        <span class="orch-eyebrow">Red de agentes</span>
        <h1 class="orch-greet">¿Qué hacemos, <span class="k">señor</span>?</h1>
        <p class="orch-greet-sub">Sostené tu tecla de voz y hablá, o escribí abajo. Coordino agentes y ejecuto workflows por vos.</p>
        <!-- Estado de voz: reemplaza al saludo mientras Jarvis escucha/piensa (concepto d4) -->
        <div class="orch-voice" aria-hidden="true">
          <span class="orch-voice-orbit"><i></i><i></i><i></i></span>
          <span class="orch-voice-label">Te estoy escuchando…</span>
          <p class="orch-voice-transcript" data-i18n-skip></p>
        </div>
        <div class="orch-hero-qa" role="group" aria-label="Acciones rápidas">
          <button class="orch-hero-chip" type="button" data-q="Ver estado">
            <span class="orch-chip-icon" aria-hidden="true">${ORCH_SVG.chip_status}</span><span>Ver estado</span>
          </button>
          <button class="orch-hero-chip" type="button" data-q="Lanzar Claude Code">
            <span class="orch-chip-icon" aria-hidden="true">${ORCH_SVG.chip_claude}</span><span>Lanzar Claude Code</span>
          </button>
          <button class="orch-hero-chip" type="button" data-q="Nueva terminal">
            <span class="orch-chip-icon" aria-hidden="true">${ORCH_SVG.chip_terminal}</span><span>Nueva terminal</span>
          </button>
        </div>`;
      empty.querySelectorAll('.orch-hero-chip').forEach(chip => {
        chip.addEventListener('click', () => this.onQuickReply(chip.dataset.q));
      });
      this.$messages.appendChild(empty);
      return;
    }

    const div = document.createElement('div');
    div.className = 'orch-day-divider';
    div.setAttribute('aria-hidden', 'true');
    const now = new Date();
    div.textContent = `TODAY · ${now.toLocaleTimeString('es-PY', { hour: '2-digit', minute: '2-digit', hour12: false })}`;
    this.$messages.appendChild(div);

    this.messages.forEach((m, idx) => this._appendMessage(m, idx));

    // Re-adjuntar workflow cards que deben sobrevivir a limpiezas del chat
    this._wfCards.forEach(card => this.$messages.appendChild(card));

    requestAnimationFrame(() => {
      this.$messages.scrollTop = this.$messages.scrollHeight;
    });
  }

  // idx: índice del mensaje en el render inicial del thread → setea --i para
  // el stagger de la animación de entrada. Mensajes agregados de a uno (sin
  // idx) usan --i:0 (sin delay).
  _appendMessage(msg, idx = 0) {
    const art = document.createElement('article');
    art.className = 'orch-msg';
    art.dataset.id   = msg.id ?? '';
    art.dataset.role = msg.role === 'jarvis' ? 'jarvis' : 'user';
    art.style.setProperty('--i', Math.min(idx, 12));
    art.setAttribute('aria-label', `${orchEsc(msg.author)}: ${orchEsc(msg.content)}`);
    art.innerHTML  = this._buildMsgHTML(msg);
    this.$messages.appendChild(art);

    art.querySelectorAll('.orch-chip').forEach(chip => {
      // El texto del chip puede llevar icono al inicio — extraer solo el label limpio
      const label = (chip.dataset.label || chip.textContent).trim();
      chip.addEventListener('click', () => this.onQuickReply(label));
    });
  }

  _buildMsgHTML(msg) {
    const time    = orchFmtTime(msg.timestamp);
    const isJarv  = msg.role === 'jarvis';
    const avClass = isJarv ? 'orch-avatar-jarvis' : 'orch-avatar-user';
    const badge   = msg.badge
      ? `<span class="orch-msg-badge">${orchEsc(msg.badge)}</span>`
      : '';
    const body    = orchParseContent(msg.content);
    const card    = msg.actionPlan ? this._buildActionCard(msg.actionPlan) : '';
    const replies = (msg.quickReplies?.length)
      ? `<div class="orch-quick-replies" role="group" aria-label="Respuestas rápidas">
           ${msg.quickReplies.map(r => `
             <button class="orch-chip" type="button" data-label="${orchEsc(r)}">
               <span class="orch-chip-icon" aria-hidden="true">${orchChipIcon(r)}</span>
               <span>${orchEsc(r)}</span>
             </button>`).join('')}
         </div>`
      : '';

    // Avatar:
    //   - Jarvis → mark "J" Instrument Serif italic (coherente con sidebar/home brand)
    //   - User   → iniciales derivadas de msg.author (primeras letras de las
    //     primeras 2 palabras, uppercase). Fallback 'TÚ' → 'T'.
    const avInner = isJarv ? 'J' : orchInitials(msg.author);
    return `
      <div class="orch-msg-head">
        <div class="orch-avatar ${avClass}" aria-hidden="true">${avInner}</div>
        <div class="orch-msg-meta">
          <span class="orch-msg-author">${orchEsc(msg.author)}</span>
          ${badge}
          <span class="orch-msg-time">${time}</span>
        </div>
      </div>
      <div class="orch-msg-body" data-i18n-skip>${body}</div>
      ${card}
      ${replies}
    `;
  }

  _buildActionCard(plan) {
    // Check con SVG stroke-draw (polyline animada de izquierda a derecha)
    const checkDrawSVG = `
      <svg class="orch-step-check" width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <polyline points="2.5,6 5,8.5 9.5,3.5"
                  stroke-dasharray="14" stroke-dashoffset="0"/>
      </svg>`;
    const alertSVG = `
      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M12 3.5L2.5 20h19L12 3.5zM12 10v4.5M12 17.5v.01"/>
      </svg>`;
    const xSVG = `
      <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true">
        <path d="M6 6l12 12M18 6L6 18"/>
      </svg>`;

    // Estados soportados: done | running | pending (legacy = en curso) |
    // blocked | error | idle. El pip/ícono comunica por color+forma.
    // Guard: el LLM podría mandar un actionPlan sin 'steps' válido → no romper el render.
    const steps = Array.isArray(plan.steps) ? plan.steps : [];
    const rows = steps.map(step => {
      const status = step.status || 'idle';
      let iconCls = 'orch-step-idle';
      let iconInner = '';
      if (status === 'done') {
        iconCls   = 'orch-step-done';
        iconInner = `<span style="color:var(--green)">${checkDrawSVG}</span>`;
      } else if (status === 'running' || status === 'pending') {
        iconCls = 'orch-step-running';
      } else if (status === 'blocked') {
        iconCls   = 'orch-step-blocked';
        iconInner = `<span style="color:var(--amber)">${alertSVG}</span>`;
      } else if (status === 'error') {
        iconCls   = 'orch-step-error';
        iconInner = `<span style="color:var(--rose)">${xSVG}</span>`;
      }
      const target = step.target
        ? `<span class="orch-step-target"><mark>${orchEsc(step.target)}</mark></span>`
        : '';
      const rolPill = (step.rol && step.rol !== 'builder')
        ? `<span class="orch-step-rol ${orchEsc(step.rol)}">${orchEsc(step.rol)}</span>` : '';
      return `
        <div class="orch-action-row" data-status="${orchEsc(status)}">
          <div class="orch-step-icon ${iconCls}" aria-hidden="true">${iconInner}</div>
          <span class="orch-step-label">${orchEsc(step.label)}</span>
          ${rolPill}
          ${target}
        </div>`;
    }).join('');

    // Progress bar slim (2px) arriba del card: % de pasos done / total.
    const totalSteps = steps.length || 1;
    const doneSteps  = plan.steps.filter(s => s.status === 'done').length;
    const progressPct = Math.round((doneSteps / totalSteps) * 100);

    // Ícono contextual del header (SVG stroke vía ui.js, heurística por título)
    const iconCtx = (() => {
      const t = (plan.title || '').toLowerCase();
      const nombre = t.includes('test')                            ? 'check'
                   : (t.includes('deploy') || t.includes('publish')) ? 'external-link'
                   : (t.includes('search') || t.includes('busc'))    ? 'search'
                   : 'zap';
      return window.icon ? window.icon(nombre, 11) : '';
    })();

    // Barra final de completado (reemplaza el viejo '✅ completado' de la .ep-*)
    const doneBar = plan.done
      ? `<div class="orch-action-done"><span style="color:var(--green)">${checkDrawSVG}</span> completado</div>`
      : '';

    return `
      <div class="orch-action-card" role="list" aria-label="${orchEsc(plan.title)}" data-progress="${progressPct}">
        <div class="orch-action-progress" aria-hidden="true">
          <div class="orch-action-progress-fill" style="width: ${progressPct}%"></div>
        </div>
        <div class="orch-action-head">
          <span class="orch-action-icon" aria-hidden="true">${iconCtx}</span>
          <span class="orch-action-title">${orchEsc(plan.title)}</span>
          <span class="orch-action-pill">${doneSteps}/${plan.steps.length} STEPS</span>
        </div>
        <div class="orch-action-rows">${rows}</div>
        ${doneBar}
      </div>`;
  }
}

/* ══════════════════════════════════════════════════════════════
   Integración con workspace.js
   Reemplaza el panel-left y expone window.jarvisPanel
══════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  const container = document.getElementById('orch-panel');
  if (!container) return;

  // Arranca en el HERO de la constelación (saludo gigante + quick actions).
  // El historial real lo carga workspace.js con setMessages() al conectar.
  const panel = new OrchestratorPanel(container, {
    messages:    [],
    sphereState: 'idle',

    onSend(text, imagenBase64, mediaType) {
      // Bridge → workspace.js
      window._orchOnSend?.(text, imagenBase64, mediaType);
    },
    onQuickReply(text) {
      window._orchOnSend?.(text);
    },
    onHeaderAction(action) {
      // Bridge → workspace.js: 'history' | 'new-thread' | 'export' | 'workflows' | 'clear-history'
      window._orchOnHeaderAction?.(action);
    },
  });

  // Exponer globalmente para workspace.js
  window.jarvisPanel = panel;
  panel._refrescarUso();   // costo acumulado al montar
});
