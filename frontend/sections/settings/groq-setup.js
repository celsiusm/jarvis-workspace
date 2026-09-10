'use strict';
(function (global) {
  const CLAVE_URL = 'https://console.groq.com/keys';
  const LS_TECLA = 'jarvis.groq.setup.tecla';
  // "Ya vio el aviso": al cargar la modal sale SOLO la primera vez. Cerrarla
  // (X) o completar el setup la marca, y no vuelve a molestar al cargar.
  const LS_INTRO = 'jarvis.groq.intro.visto';
  // Gaming order: 1=left, 2=right, 3=middle, 4=back (e.button 0, 2, 1, 3).
  const MOUSE_BOTONES = [
    { n: 1, button: 0, label: 'Mouse 1' },
    { n: 2, button: 2, label: 'Mouse 2' },
    { n: 3, button: 1, label: 'Mouse 3' },
    { n: 4, button: 3, label: 'Mouse 4' },
  ];

  function clavePareceGroq(s) {
    return /^gsk_[A-Za-z0-9]{20,}$/.test(String(s || '').trim());
  }

  function siguientePaso({ groq, teclaLista }) {
    if (!groq) return 'clave';
    if (!teclaLista) return 'tecla';
    return 'listo';
  }

  function chipSeleccionado(binding, button) {
    return !!binding && binding.type === 'mouse' && Number(binding.value) === Number(button);
  }

  const api = { CLAVE_URL, clavePareceGroq, siguientePaso, LS_TECLA, MOUSE_BOTONES, chipSeleccionado };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
    return;
  }

  const _t = (s) => (global.JarvisI18n && global.JarvisI18n.t) ? global.JarvisI18n.t(s) : s;
  let _groq = false;
  let _root = null;
  let _prevOnCambio = null;
  let _hookedOnCambio = false;

  function _esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function haceFaltaClave() { return !_groq; }

  function _cerrar() {
    global.JarvisControls?.cancelarCaptura?.();
    if (global.JarvisControls && _hookedOnCambio) {
      global.JarvisControls.onCambio = _prevOnCambio;
      _prevOnCambio = null;
      _hookedOnCambio = false;
    }
    _root?.remove();
    _root = null;
  }

  // Cerrar con la X: no vuelve a molestar al cargar (se reabre solo si el
  // usuario usa la voz o toca el control de voz en Configuración).
  function _cerrarIntro() {
    try { localStorage.setItem(LS_INTRO, '1'); } catch (_) {}
    _cerrar();
  }

  function _btnX() {
    const ic = global.icon ? global.icon('x', 15) : '×';
    return `<button type="button" class="gq-x" aria-label="${_esc(_t('Cerrar'))}">${ic}</button>`;
  }

  function _pintar(paso) {
    if (!_root) {
      _root = document.createElement('div');
      _root.className = 'gq-ov';
      _root.setAttribute('role', 'dialog');
      _root.setAttribute('aria-modal', 'true');
      document.body.appendChild(_root);
    }
    const tecla = global.JarvisControls?.label?.('mic-ptt') || 'Alt';
    if (paso === 'clave') {
      _root.innerHTML = `
        <div class="gq-card">
          ${_btnX()}
          <p class="gq-kicker">${_esc(_t('Dictado'))}</p>
          <h2>${_esc(_t('Para hablar hace falta una clave de Groq'))}</h2>
          <p class="gq-msg">${_esc(_t('El dictado usa Whisper gratis en Groq. Creá una clave (sin tarjeta) y pegala acá — después podés hablarle a los agentes.'))}</p>
          <a class="gq-link" href="${CLAVE_URL}" target="_blank" rel="noopener">${_esc(_t('Sacá una clave gratis de Groq'))} →</a>
          <label class="gq-lab" for="gq-key">${_esc(_t('Tu clave de Groq'))}</label>
          <input id="gq-key" class="gq-in" type="password" autocomplete="off" spellcheck="false"
                 placeholder="gsk_…" />
          <p class="gq-err" hidden></p>
          <div class="gq-act">
            <button type="button" class="gq-btn gq-prim" id="gq-save">${_esc(_t('Guardar y seguir'))}</button>
          </div>
        </div>`;
      const inp = _root.querySelector('#gq-key');
      const err = _root.querySelector('.gq-err');
      const go = async () => {
        const key = inp.value.trim();
        if (!clavePareceGroq(key)) {
          err.hidden = false;
          err.textContent = _t('Eso no parece una clave de Groq (empieza con gsk_).');
          return;
        }
        err.hidden = true;
        const r = await fetch('/api/voice/groq-key', {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key }),
        });
        if (!r.ok) {
          err.hidden = false;
          err.textContent = _t('No se pudo guardar. Revisá la clave e intentá de nuevo.');
          return;
        }
        _groq = true;
        inp.value = '';
        _pintar('tecla');
      };
      _root.querySelector('#gq-save').addEventListener('click', go);
      _root.querySelector('.gq-x')?.addEventListener('click', _cerrarIntro);
      inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
      inp.focus();
      return;
    }
    _root.innerHTML = `
      <div class="gq-card">
        ${_btnX()}
        <p class="gq-kicker">${_esc(_t('Dictado'))}</p>
        <h2>${_esc(_t('Elegí la tecla para dictar'))}</h2>
        <p class="gq-msg">${_esc(_t('Apretá una tecla o un botón del mouse. También podés elegir Mouse 1–4 acá.'))}</p>
        <button class="gq-keycap settings-keybind set-keybind" data-id="mic-ptt" type="button"
                aria-label="${_esc(_t('Reasignar la tecla de voz'))}">
          <span class="settings-kbd">${_esc(_t(tecla))}</span>
        </button>
        <div class="gq-mice" role="group" aria-label="${_esc(_t('Botones del mouse'))}">
          ${MOUSE_BOTONES.map(b => `
            <button type="button" class="gq-mouse" data-mouse="${b.button}" aria-pressed="false">${_esc(b.label)}</button>
          `).join('')}
        </div>
        <p class="gq-hint">${_esc(_t('Mouse 1 es el izquierdo, 2 el derecho, 3 el del medio, 4 el de atrás. Por defecto es Alt.'))}</p>
        <div class="gq-act">
          <button type="button" class="gq-btn gq-prim" id="gq-done">${_esc(_t('Listo'))}</button>
        </div>
      </div>`;
    const _marcarMice = () => {
      const b = global.JarvisControls?.binding?.('mic-ptt');
      _root?.querySelectorAll('.gq-mouse').forEach(btn => {
        const on = chipSeleccionado(b, btn.getAttribute('data-mouse'));
        btn.classList.toggle('on', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
    };
    const _refrescarTecla = () => {
      const k = global.JarvisControls?.label?.('mic-ptt') || 'Alt';
      const cap = _root?.querySelector('.set-keybind');
      if (cap) {
        cap.classList.remove('capturando');
        cap.innerHTML = `<span class="settings-kbd">${_esc(_t(k))}</span>`;
      }
      _marcarMice();
    };
    _root.addEventListener('contextmenu', (e) => e.preventDefault());
    _root.querySelector('.set-keybind')?.addEventListener('click', (e) =>
      global.JarvisControls?.capturar?.(e.currentTarget.dataset.id));
    _root.querySelectorAll('.gq-mouse').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        const n = Number(btn.getAttribute('data-mouse'));
        global.JarvisControls?.setBinding?.('mic-ptt', { type: 'mouse', value: n });
      });
    });
    if (global.JarvisControls) {
      if (!_hookedOnCambio) {
        _prevOnCambio = global.JarvisControls.onCambio || null;
        _hookedOnCambio = true;
      }
      global.JarvisControls.onCambio = _refrescarTecla;
    }
    _marcarMice();
    // Escucha ya: un botón del mouse (1–4) se toma al apretarlo, sin otro click.
    global.JarvisControls?.capturar?.('mic-ptt');
    _root.querySelector('.gq-x')?.addEventListener('click', _cerrarIntro);
    _root.querySelector('#gq-done').addEventListener('click', () => {
      try { localStorage.setItem(LS_TECLA, '1'); localStorage.setItem(LS_INTRO, '1'); } catch (_) {}
      _cerrar();
    });
  }

  async function init() {
    try {
      const r = await fetch('/api/voice/setup', { credentials: 'include' });
      if (!r.ok) return;
      const d = await r.json();
      _groq = !!d.groq;
    } catch (_) { return; }
    // Solo la PRIMERA vez (nunca vista/cerrada) aparece sola al cargar el
    // workspace; despues se reabre solo bajo demanda (voz o editar el control).
    let introVisto = false;
    try { introVisto = localStorage.getItem(LS_INTRO) === '1'; } catch (_) {}
    if (introVisto) return;
    let teclaLista = false;
    try { teclaLista = localStorage.getItem(LS_TECLA) === '1'; } catch (_) {}
    const paso = siguientePaso({ groq: _groq, teclaLista });
    if (paso === 'listo') return;
    _pintar(paso);
  }

  function abrir() {
    // Abrir bajo demanda SIEMPRE: sirve tanto para configurar como para EDITAR
    // el control de voz. Si ya esta todo listo, muestra el paso de la tecla
    // (para reasignarla) en vez de cortar.
    const teclaLista = (() => { try { return localStorage.getItem(LS_TECLA) === '1'; } catch (_) { return false; } })();
    let paso = siguientePaso({ groq: _groq, teclaLista });
    if (paso === 'listo') paso = 'tecla';
    _pintar(paso);
  }

  global.JarvisGroqSetup = { init, abrir, haceFaltaClave, CLAVE_URL };
})(typeof window !== 'undefined' ? window : globalThis);
