'use strict';
/* JARVIS — Uso de la suscripción por cuenta (⚙ → Cuentas).
   Pinta cuánto le queda a cada cuenta de Claude/Codex con los datos de
   GET /api/cuentas/uso (plotspace/core/uso_suscripcion.py). Lógica pura
   exportada para los tests de Node; el DOM lo arma settings.js. */
(function (global) {
  const _t = (s) => (global.JarvisI18n?.t ? global.JarvisI18n.t(s) : s);
  const _esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /* Nivel de la barra según cuánto queda: ok · medio · critico. */
  function nivel(usado) {
    const u = Number(usado) || 0;
    if (u >= 90) return 'critico';
    if (u >= 70) return 'medio';
    return 'ok';
  }

  function restante(usado) {
    return Math.max(0, Math.round(100 - (Number(usado) || 0)));
  }

  /* "2 h 10 min" / "3 d 4 h" / "12 min" hasta `iso`. null si no hay fecha o ya pasó. */
  function faltaPara(iso, ahora = Date.now()) {
    if (!iso) return null;
    const t = new Date(iso).getTime();
    if (isNaN(t)) return null;
    let s = Math.round((t - ahora) / 1000);
    if (s <= 0) return null;
    const d = Math.floor(s / 86400); s -= d * 86400;
    const h = Math.floor(s / 3600); s -= h * 3600;
    const m = Math.max(1, Math.floor(s / 60));
    if (d > 0) return h ? `${d} d ${h} h` : `${d} d`;
    if (h > 0) return m && m < 60 ? `${h} h ${m} min` : `${h} h`;
    return `${m} min`;
  }

  function etiqueta(v) {
    const base = v.clave === 'sesion' ? _t('Sesión (5 h)') : _t('Semana');
    return v.modelo ? `${base} · ${v.modelo}` : base;
  }

  /* La ventana que más aprieta (la de menos restante): la del resumen del chip. */
  function masApretada(ventanas) {
    if (!Array.isArray(ventanas) || !ventanas.length) return null;
    return ventanas.reduce((a, b) => (Number(b.usado) > Number(a.usado) ? b : a));
  }

  function textoEstado(uso) {
    switch (uso && uso.estado) {
      case 'token_vencido': return _t('Sesión vencida: se renueva sola la próxima vez que uses este CLI.');
      case 'sin_credencial': return _t('No encontré la credencial de esta cuenta.');
      case 'sin_datos': return _t('El proveedor no devolvió datos de uso para este plan.');
      case 'error': return _t('No se pudo consultar el uso ahora. Probá actualizar en un rato.');
      default: return '';
    }
  }

  /* Bloque de barras para la cuenta en uso. */
  function htmlBloque(uso, opts = {}) {
    const icon = opts.icon || (() => '');
    const ahora = opts.ahora || Date.now();
    if (!uso) {
      return `<div class="ct-uso cargando">${icon('loader', 13)} <span>${_esc(_t('Consultando el uso…'))}</span></div>`;
    }
    const plan = uso.plan ? `<span class="ct-uso-plan sx-mono">${_esc(String(uso.plan).toUpperCase())}</span>` : '';
    const refrescar = `<button class="ct-uso-ref" type="button" data-act="uso-refrescar"
        title="${_esc(_t('Actualizar el uso'))}" aria-label="${_esc(_t('Actualizar el uso'))}">${icon('refresh', 13)}</button>`;
    if (uso.estado !== 'ok' || !(uso.ventanas || []).length) {
      return `<div class="ct-uso vacio">${plan}<span class="ct-uso-msg">${_esc(textoEstado(uso))}</span>${refrescar}</div>`;
    }
    const filas = uso.ventanas.map((v) => {
      const niv = nivel(v.usado);
      const queda = restante(v.usado);
      const falta = faltaPara(v.reinicia, ahora);
      const renueva = falta ? _t('se renueva en {t}').replace('{t}', falta) : '';
      return `
        <div class="ct-uso-f ${niv}">
          <span class="ct-uso-l">${_esc(etiqueta(v))}</span>
          <span class="ct-uso-bar" role="meter" aria-valuemin="0" aria-valuemax="100"
                aria-valuenow="${queda}" aria-label="${_esc(etiqueta(v))}">
            <i style="width:${Math.min(100, Math.max(0, Number(v.usado) || 0))}%"></i></span>
          <span class="ct-uso-v"><b>${_esc(_t('quedan {n}%').replace('{n}', queda))}</b>${renueva ? ` · ${_esc(renueva)}` : ''}</span>
        </div>`;
    }).join('');
    const pie = uso.fuente === 'sesion'
      ? `<span class="ct-uso-nota">${_esc(_t('Dato de la última sesión del CLI'))}</span>` : '';
    return `<div class="ct-uso">${plan}${refrescar}${filas}${pie}</div>`;
  }

  /* Mini-resumen para el chip de una cuenta que NO está en uso. */
  function htmlChip(uso) {
    if (!uso || uso.estado !== 'ok') return '';
    const v = masApretada(uso.ventanas);
    if (!v) return '';
    return `<em class="ct-chip-uso ${nivel(v.usado)}" title="${_esc(etiqueta(v))}">${restante(v.usado)}%</em>`;
  }

  const api = { nivel, restante, faltaPara, etiqueta, masApretada, textoEstado, htmlBloque, htmlChip };
  global.JarvisUsoCuentas = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
