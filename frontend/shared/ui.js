/* ═══════════════════════════════════════════════════════════════
   JARVIS — Obsidian Glass · Utilidades de UI compartidas
   icon()      → SVG stroke 1.5px (mata los emojis-como-ícono)
   toast()     → notificación glass (reemplaza alert())
   confirmar() → Promise<bool> con modal glass (reemplaza confirm())
   Cargado por index.html y workspace.html ANTES de los JS de sección.
═══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  /* ── Íconos (paths 24x24, estilo Lucide: stroke 1.5, round) ── */
  const ICONS = {
    'x':             '<path d="M6 6l12 12M18 6L6 18"/>',
    'check':         '<path d="M4 12.5l5 5L20 6.5"/>',
    'plus':          '<path d="M12 5v14M5 12h14"/>',
    'trash':         '<path d="M3.5 6.5h17M9.5 6V4.5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1V6M6 6.5l1 13a1 1 0 0 0 1 .9h8a1 1 0 0 0 1-.9l1-13M10 10.5v6M14 10.5v6"/>',
    'terminal':      '<path d="M4 17l6-5-6-5M12 19h8"/>',
    'alert':         '<path d="M12 3.5L2.5 20h19L12 3.5zM12 10v4.5M12 17.5v.01"/>',
    'info':          '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.01"/>',
    'refresh':       '<path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6"/>',
    'external-link': '<path d="M14 4.5h5.5V10M19.5 4.5l-9 9M19.5 13.5v6h-15v-15h6"/>',
    'camera':        '<path d="M3 8a1.5 1.5 0 0 1 1.5-1.5H8l1.5-2h5L16 6.5h3.5A1.5 1.5 0 0 1 21 8v10a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18V8z"/><circle cx="12" cy="13" r="3.5"/>',
    'zap':           '<path d="M13 2.5L4.5 14H11l-1 7.5L18.5 10H12l1-7.5z"/>',
    'plug':          '<path d="M9 2.5V8M15 2.5V8M6.5 8h11v3.5a5.5 5.5 0 0 1-11 0V8zM12 17v4.5"/>',
    'clock':         '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
    'settings':      '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    'chart':         '<path d="M4 20V10M10 20V4M16 20v-7M21 20H3"/>',
    'play':          '<path d="M7 4.5l12 7.5-12 7.5v-15z"/>',
    'square':        '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
    'edit':          '<path d="M16.5 3.5l4 4L8 20l-5 1 1-5L16.5 3.5z"/>',
    'undo':          '<path d="M3.5 7.5v6h6M3.5 13.5a9 9 0 1 1 2.6 5"/>',
    'redo':          '<path d="M20.5 7.5v6h-6M20.5 13.5a9 9 0 1 0-2.6 5"/>',
    'download':      '<path d="M12 3.5V15M6.5 9.5l5.5 5.5 5.5-5.5M4.5 20.5h15"/>',
    'sparkle':       '<path d="M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2 2-6z"/>',
    'qr':            '<rect x="3.5" y="3.5" width="7" height="7" rx="1"/><rect x="13.5" y="3.5" width="7" height="7" rx="1"/><rect x="3.5" y="13.5" width="7" height="7" rx="1"/><path d="M13.5 13.5h3v3h-3zM17.5 17.5h3v3h-3z"/>',
    'phone':         '<rect x="7" y="2.5" width="10" height="19" rx="2"/><path d="M11 18.5h2"/>',
    'folder':        '<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>',
    'archive':       '<path d="M3.5 4.5h17v3.5h-17v-3.5zM5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8M10 12h4"/>',
    'pin':           '<path d="M9 4.5h6l-1 6 3 2.5v2H7v-2l3-2.5-1-6zM12 15v6"/>',
    'search':        '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.5-4.5"/>',
    'mic':           '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5v3.5"/>',
    'file':          '<path d="M6 2.5h8l4 4V21a.5.5 0 0 1-.5.5h-11A.5.5 0 0 1 6 21V2.5zM14 2.5v4h4"/>',
    'git-branch':    '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="9" r="2.5"/><path d="M6 8.5v7M18 11.5a7 7 0 0 1-7 6.5h-2.5"/>',
    'send':          '<path d="M21 3L10.5 13.5M21 3l-7 18-3.5-7.5L3 10l18-7z"/>',
    'maximize':      '<path d="M8 3.5H3.5V8M16 3.5h4.5V8M8 20.5H3.5V16M16 20.5h4.5V16"/>',
    'minimize':      '<path d="M3.5 8H8V3.5M20.5 8H16V3.5M3.5 16H8v4.5M20.5 16H16v4.5"/>',
    'history':       '<path d="M3.5 7.5v5h5"/><path d="M3.8 12.5a9 9 0 1 0 2-6.5"/><path d="M12 8v4.5l3 2"/>',
    'cpu':           '<rect x="6" y="6" width="12" height="12" rx="1.5"/><rect x="10" y="10" width="4" height="4"/><path d="M9 2.5v3M15 2.5v3M9 18.5v3M15 18.5v3M2.5 9h3M2.5 15h3M18.5 9h3M18.5 15h3"/>',
    'eye':           '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
    'eye-off':       '<path d="M9.9 5.8A9.6 9.6 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5a17 17 0 0 1-3.2 4M6.3 7.8A17 17 0 0 0 2.5 12S6 18.5 12 18.5c1.5 0 2.8-.4 4-1M10 10a2.8 2.8 0 0 0 4 4M3 3l18 18"/>',
    'loader':        '<path d="M12 3v4M12 17v4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M3 12h4M17 12h4M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"/>',
    'keyboard':  '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
    'sliders':   '<path d="M4 8h9M17 8h3M4 16h3M11 16h9"/><circle cx="15" cy="8" r="2"/><circle cx="9" cy="16" r="2"/>',
    'brain':     '<circle cx="6" cy="7" r="2.6"/><circle cx="17" cy="5.5" r="2.2"/><circle cx="14" cy="17" r="2.8"/><path d="M8.2 8.5l4.3 6M15.9 7.5l-1.3 6.7M8.6 6.6l6.2-.8"/>',
    'workflow':  '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="17" cy="12" r="2.5"/><path d="M6 8.5v7M8.5 6h4a3 3 0 0 1 3 3v.5"/>',
    'dot':       '<circle cx="12" cy="12" r="4" fill="currentColor" stroke="none"/>',
    'panel-right':   '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M15 3v18"/>',
    'dots-h':        '<circle cx="5" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.4" fill="currentColor" stroke="none"/>',
    'copy':          '<rect x="8" y="8" width="14" height="14" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    'globe':         '<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20"/>',
    'monitor':       '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
    'sparkles':      '<path d="M12 3l1.9 5.7a2 2 0 0 0 1.4 1.4L21 12l-5.7 1.9a2 2 0 0 0-1.4 1.4L12 21l-1.9-5.7a2 2 0 0 0-1.4-1.4L3 12l5.7-1.9a2 2 0 0 0 1.4-1.4Z"/>',
    'list-checks':   '<path d="M3 6h.01M8 6h13M3 12h.01M8 12h13M3 18h.01M8 18h13"/>',
    'unplug':        '<path d="m19 5 3-3M2 22l3-3M6.3 20.3a2.4 2.4 0 0 0 3.4 0L12 18l-6-6-2.3 2.3a2.4 2.4 0 0 0 0 3.4ZM7.5 13.5l-2 2M10.5 16.5l-2 2M12 6l6 6 2.3-2.3a2.4 2.4 0 0 0 0-3.4l-2.6-2.6a2.4 2.4 0 0 0-3.4 0Z"/>',
    'message':       '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22z"/>',
    'key':           '<circle cx="8" cy="16" r="4"/><path d="M10.85 13.15 21 3M17 5l2 2M14.5 7.5l2 2"/>',
  };

  /**
   * icon('trash')          → string SVG 16px
   * icon('trash', 20)      → string SVG 20px
   * Hereda color del contexto (stroke: currentColor).
   */
  function icon(nombre, size = 16) {
    const paths = ICONS[nombre];
    if (!paths) { console.warn(`[ui] icono desconocido: ${nombre}`); return ''; }
    return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" ` +
           `stroke="currentColor" stroke-width="1.5" stroke-linecap="round" ` +
           `stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
  }

  /* ── Toasts ──────────────────────────────────────────────────── */
  const TOAST_ICONS = { success: 'check', error: 'alert', warning: 'alert', info: 'info' };

  function _contenedorToasts() {
    let cont = document.getElementById('ob-toasts');
    if (!cont) {
      cont = document.createElement('div');
      cont.id = 'ob-toasts';
      cont.setAttribute('aria-live', 'polite');
      document.body.appendChild(cont);
    }
    return cont;
  }

  /**
   * toast('Proyecto creado', 'success')
   * tipos: 'info' (default) | 'success' | 'error' | 'warning'
   */
  function toast(mensaje, tipo = 'info', duracion = 4000) {
    // Alias que usan varias secciones ('ok' / 'warn'): sin normalizar caían al
    // estilo neutro (sin borde de color y con el ícono de info).
    tipo = ({ ok: 'success', warn: 'warning' })[tipo] || tipo;
    const cont = _contenedorToasts();
    const el = document.createElement('div');
    el.className = `ob-toast ${tipo}`;
    el.style.setProperty('--ob-toast-dur', `${duracion}ms`);
    el.innerHTML = `${icon(TOAST_ICONS[tipo] || 'info')}<span></span>`;
    el.querySelector('span').textContent = mensaje;
    cont.appendChild(el);

    const cerrar = () => {
      el.classList.add('saliendo');
      setTimeout(() => el.remove(), 200);
    };
    el.addEventListener('click', cerrar);
    setTimeout(cerrar, duracion);
    return el;
  }

  /* ── Confirm (Promise) ───────────────────────────────────────── */
  /**
   * const ok = await confirmar('¿Eliminar la terminal?', { peligro: true });
   * opts: { titulo = '¿Confirmás?', confirmText = 'Confirmar',
   *         cancelText = 'Cancelar', peligro = false }
   */
  function confirmar(mensaje, opts = {}) {
    const {
      titulo      = '¿Confirmás?',
      confirmText = 'Confirmar',
      cancelText  = 'Cancelar',
      peligro     = false,
    } = opts;

    return new Promise((resolve) => {
      const overlay = document.createElement('div');
      overlay.className = 'ob-confirm-overlay';
      overlay.innerHTML = `
        <div class="ob-confirm" role="dialog" aria-modal="true">
          <h3 class="ob-confirm-title"></h3>
          <p class="ob-confirm-msg"></p>
          <div class="ob-confirm-actions">
            <button type="button" class="ob-confirm-btn" data-act="cancel"></button>
            <button type="button" class="ob-confirm-btn ${peligro ? 'peligro' : 'primario'}" data-act="ok"></button>
          </div>
        </div>`;
      overlay.querySelector('.ob-confirm-title').textContent = titulo;
      overlay.querySelector('.ob-confirm-msg').textContent = mensaje;
      overlay.querySelector('[data-act="cancel"]').textContent = cancelText;
      overlay.querySelector('[data-act="ok"]').textContent = confirmText;

      const terminar = (resultado) => {
        document.removeEventListener('keydown', onKey);
        overlay.remove();
        resolve(resultado);
      };
      const onKey = (e) => {
        if (e.key === 'Escape') terminar(false);
        if (e.key === 'Enter')  terminar(true);
      };

      overlay.addEventListener('click', (e) => { if (e.target === overlay) terminar(false); });
      overlay.querySelector('[data-act="cancel"]').addEventListener('click', () => terminar(false));
      overlay.querySelector('[data-act="ok"]').addEventListener('click', () => terminar(true));
      document.addEventListener('keydown', onKey);

      document.body.appendChild(overlay);
      overlay.querySelector('[data-act="ok"]').focus();
    });
  }

  /* ── Prompt de texto (Promise) ───────────────────────────────── */
  /**
   * const nombre = await pedirTexto('Nombre nuevo:', { valor: 'actual' });
   * Devuelve el string ingresado o null si canceló.
   * opts: { titulo = 'Ingresá un valor', valor = '', placeholder = '',
   *         confirmText = 'Aceptar', cancelText = 'Cancelar' }
   */
  function pedirTexto(mensaje, opts = {}) {
    const {
      titulo      = 'Ingresá un valor',
      valor       = '',
      placeholder = '',
      confirmText = 'Aceptar',
      cancelText  = 'Cancelar',
    } = opts;

    return new Promise((resolve) => {
      const overlay = document.createElement('div');
      overlay.className = 'ob-confirm-overlay';
      overlay.innerHTML = `
        <div class="ob-confirm" role="dialog" aria-modal="true">
          <h3 class="ob-confirm-title"></h3>
          <p class="ob-confirm-msg"></p>
          <input type="text" class="ob-confirm-input" spellcheck="false" autocomplete="off">
          <div class="ob-confirm-actions">
            <button type="button" class="ob-confirm-btn" data-act="cancel"></button>
            <button type="button" class="ob-confirm-btn primario" data-act="ok"></button>
          </div>
        </div>`;
      overlay.querySelector('.ob-confirm-title').textContent = titulo;
      overlay.querySelector('.ob-confirm-msg').textContent = mensaje;
      const input = overlay.querySelector('.ob-confirm-input');
      input.value = valor;
      input.placeholder = placeholder;
      overlay.querySelector('[data-act="cancel"]').textContent = cancelText;
      overlay.querySelector('[data-act="ok"]').textContent = confirmText;

      const terminar = (resultado) => {
        document.removeEventListener('keydown', onKey);
        overlay.remove();
        resolve(resultado);
      };
      const onKey = (e) => {
        if (e.key === 'Escape') terminar(null);
        if (e.key === 'Enter')  terminar(input.value.trim() || null);
      };

      overlay.addEventListener('click', (e) => { if (e.target === overlay) terminar(null); });
      overlay.querySelector('[data-act="cancel"]').addEventListener('click', () => terminar(null));
      overlay.querySelector('[data-act="ok"]').addEventListener('click', () => terminar(input.value.trim() || null));
      document.addEventListener('keydown', onKey);

      document.body.appendChild(overlay);
      input.focus();
      input.select();
    });
  }

  function apiFetch(url, opts) {
    return fetch(url, opts);
  }

  /* ── Logos de CLIs (SVGs vendoreados de @lobehub/icons-static-svg@1.91.0) ── */
  // 'manual' (shell) no tiene logo: devuelve un $ tipográfico.
  const CLI_LOGOS = { claude: 'claude-color', codex: 'codex-color', opencode: 'opencode', qwen: 'qwen-color', antigravity: 'antigravity', grok: 'grok', cursor: 'cursor', pi: 'pi' };
  function cliLogo(tipo, size = 16) {
    const f = CLI_LOGOS[tipo];
    if (!f) return `<span class="cli-logo cli-logo-shell" style="width:${size}px;height:${size}px" aria-hidden="true">$</span>`;
    const inv = ['opencode', 'antigravity', 'grok', 'cursor', 'pi'].includes(tipo) ? ' cli-logo-inv' : '';
    return `<img class="cli-logo${inv}" src="/static/shared/icons/${f}.svg?v=1" width="${size}" height="${size}" alt="" aria-hidden="true">`;
  }

  /* ── Export global ───────────────────────────────────────────── */
  window.icon = icon;
  window.toast = toast;
  window.confirmar = confirmar;
  window.pedirTexto = pedirTexto;
  window.apiFetch = apiFetch;
  window.cliLogo = cliLogo;
})();
