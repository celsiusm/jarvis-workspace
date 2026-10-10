// JARVIS — Notificaciones cuando un agente termina y la pestaña está en
// background (Sprint 3). Flash de título SIEMPRE (sin permisos) + notificación
// del SO opt-in (toggle en Configuración pide el permiso con el gesto del click).
(function (global) {
  'use strict';

  // Título a mostrar: alterna entre el base y "🔔 (N) base" mientras hay
  // avisos pendientes y la pestaña sigue oculta. Puro y testeable.
  function tituloFlash(base, n, mostrarAlterno) {
    if (!n || n <= 0 || !mostrarAlterno) return base;
    return `🔔 (${n}) ${base}`;
  }

  // ¿Hacer algo? Solo si la pestaña está oculta y el usuario no silenció tareas
  // (silenciar = "no me molestes", coherente con el toggle de sonido).
  function debeAvisar({ hidden, sonidoOn }) {
    return !!hidden && sonidoOn !== false;
  }

  // ¿Disparar notificación del SO? Solo con permiso concedido y opt-in activo.
  function debeOsNotif({ permiso, osOptIn }) {
    return permiso === 'granted' && !!osOptIn;
  }

  const pure = { tituloFlash, debeAvisar, debeOsNotif };
  global.JarvisNotify = Object.assign(global.JarvisNotify || {}, { _pure: pure });
  if (typeof document === 'undefined') return;  // tests Node: solo _pure

  // El título base se lee RECIÉN al arrancar un flash: al cargar el script la
  // página todavía tiene el título genérico ("Jarvis Workspace"), y el workspace
  // lo cambia después a "JARVIS — <proyecto>". Leerlo antes lo pisaba en cada focus.
  let _base = null;
  let _pend = 0;
  let _timer = null;
  let _alterno = false;

  function _osOptIn() { return localStorage.getItem('jarvis.notif.os') === '1'; }

  function _startFlash() {
    if (_timer) return;
    _timer = setInterval(() => {
      _alterno = !_alterno;
      document.title = tituloFlash(_base, _pend, _alterno);
    }, 1000);
  }

  function _limpiar() {
    const huboFlash = !!_timer || _pend > 0;
    _pend = 0; _alterno = false;
    if (_timer) { clearInterval(_timer); _timer = null; }
    // Solo restaurar si HUBO flash: si no, cada focus/visibilitychange pisaba el
    // título vigente de la pestaña.
    if (huboFlash && _base !== null) document.title = _base;
    _base = null;
  }

  // tipo ∈ 'termino' | 'espera' · nombre = nombre de la terminal
  global.JarvisNotify.avisar = ({ tipo, nombre, sonidoOn } = {}) => {
    if (!debeAvisar({ hidden: document.hidden, sonidoOn })) return;
    if (_pend === 0 && !_timer) _base = document.title;
    _pend++;
    _startFlash();
    const permiso = (typeof Notification !== 'undefined') ? Notification.permission : 'denied';
    if (debeOsNotif({ permiso, osOptIn: _osOptIn() })) {
      try {
        // Fuera del DOM (notificación del SO): el observer de i18n no la ve → t().
        const tr = (s) => (global.JarvisI18n && global.JarvisI18n.t) ? global.JarvisI18n.t(s) : s;
        const cuerpo = tr(tipo === 'espera' ? '{n} espera respuesta' : '{n} terminó')
          .replace('{n}', nombre || tr('Un agente'));
        const n = new Notification('Jarvis', { body: cuerpo, tag: 'jarvis-agente' });
        n.onclick = () => { window.focus(); n.close(); };
      } catch (_) { /* el SO puede negar; el flash de título ya avisó */ }
    }
  };

  // Pedir permiso del SO (lo llama el toggle de Configuración con el gesto del
  // click). Devuelve true si quedó concedido.
  global.JarvisNotify.pedirPermiso = async () => {
    if (typeof Notification === 'undefined') return false;
    try {
      const res = await Notification.requestPermission();
      return res === 'granted';
    } catch (_) { return false; }
  };

  document.addEventListener('visibilitychange', () => { if (!document.hidden) _limpiar(); });
  window.addEventListener('focus', _limpiar);
})(typeof window !== 'undefined' ? window : globalThis);
