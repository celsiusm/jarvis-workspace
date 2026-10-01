'use strict';
/* JARVIS — Fondo del modo Glass: el bloque de ⚙ → Apariencia.
   El motor vive en shared/fondo.js (window.JarvisFondo); acá solo se arma el
   markup y se cablean los controles. La parte pura (qué filas, qué texto,
   qué estado) se exporta para los tests de Node. */
(function (global) {
  const _t = (s) => (global.JarvisI18n?.t ? global.JarvisI18n.t(s) : s);
  const _esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  // [clave de JarvisFondo, etiqueta, ayuda]
  const SLIDERS = [
    ['blur',  'Desenfoque',           'Cuánto se difumina la imagen detrás del vidrio.'],
    ['dim',   'Velo',                 'Oscurece el fondo en los temas oscuros y lo aclara en los claros: más velo = texto más legible.'],
    ['sat',   'Saturación',           'Intensidad de los colores de la imagen.'],
    ['term',  'Opacidad terminales',  '100% = fondo sólido con los colores de la apariencia; menos lo vuelve vidrio y deja ver la imagen.'],
    ['panel', 'Opacidad paneles',     'Lo mismo para la franja de proyectos, la barra superior y el panel derecho: al 100% usan los colores de la apariencia.'],
  ];

  function etiquetaValor(clave, v, rangos) {
    const r = rangos[clave];
    return `${v}${r ? r.unidad : ''}`;
  }

  /** Qué avisos mostrar según el entorno. */
  function avisos({ glass, reducir }) {
    const a = [];
    if (glass === false) a.push('Activá Liquid Glass (arriba) para usar un fondo.');
    if (reducir === true) a.push('Tu sistema pide reducir transparencias: el fondo está en pausa.');
    return a;
  }

  function html(cfg, o = {}) {
    const F = o.F;
    const glass = o.glass !== false;
    const bloqueado = !glass || o.reducir === true;
    const dis = bloqueado ? ' disabled' : '';
    const tiles = F.PRESETS.map(p => `
      <button type="button" class="fd-t${cfg.fuente === p.id ? ' on' : ''}" role="radio"
              aria-checked="${cfg.fuente === p.id}" data-fuente="${p.id}"${dis}
              style="--fd-bg:${p.css}" title="${_esc(_t(p.nombre))}">
        <span class="fd-t-p" aria-hidden="true"></span>
        <span class="fd-t-n">${_esc(_t(p.nombre))}</span>
      </button>`).join('');
    const hayPropia = !!o.existeImagen;
    const propia = `
      <button type="button" class="fd-t fd-custom${cfg.fuente === 'custom' ? ' on' : ''}${hayPropia ? '' : ' vacio'}" role="radio"
              aria-checked="${cfg.fuente === 'custom'}" data-fuente="custom"${dis}
              title="${_esc(_t(hayPropia ? 'Tu imagen' : 'Subir una imagen'))}">
        <span class="fd-t-p" aria-hidden="true">${hayPropia
          ? `<img src="${_esc(F.URL_IMAGEN)}?v=${Number(cfg.version) || 0}" alt="">`
          : (o.icon ? o.icon('image', 18) : '+')}</span>
        <span class="fd-t-n">${_esc(_t(hayPropia ? 'Tu imagen' : 'Subir imagen'))}</span>
      </button>`;
    const sl = SLIDERS.map(([k, l, ayuda]) => {
      const r = F.RANGOS[k];
      return `
      <label class="fd-sl" title="${_esc(_t(ayuda))}">
        <span class="fd-sl-l">${_esc(_t(l))}</span>
        <span class="sx-slider">
          <input type="range" data-k="${k}" min="${r.min}" max="${r.max}" step="1" value="${cfg[k]}"${dis}
                 aria-label="${_esc(_t(l))}">
          <output data-o="${k}">${etiquetaValor(k, cfg[k], F.RANGOS)}</output>
        </span>
      </label>`;
    }).join('');
    const av = avisos({ glass, reducir: o.reducir }).map(m => `<p class="fd-aviso">${_esc(_t(m))}</p>`).join('');
    return `
      <div class="fd">
        <div class="fd-top">
          <div class="fd-top-t"><b>${_esc(_t('Fondo personalizado'))}</b>
            <span>${_esc(_t('Una imagen detrás de la franja, la barra, el panel y las terminales.'))}</span></div>
          <label class="sx-sw" title="${_esc(_t('Fondo personalizado'))}">
            <input type="checkbox" id="fd-on" ${cfg.on ? 'checked' : ''}${dis} aria-label="${_esc(_t('Fondo personalizado'))}"><i></i>
          </label>
        </div>
        ${av}
        <div class="fd-fuentes" role="radiogroup" aria-label="${_esc(_t('Imagen de fondo'))}">${tiles}${propia}</div>
        <div class="fd-acc">
          <button class="sx-btn sm" id="fd-subir" type="button"${dis}>${o.icon ? o.icon('upload', 13) : ''} ${_esc(_t(hayPropia ? 'Cambiar imagen…' : 'Subir imagen…'))}</button>
          <button class="sx-btn gho sm" id="fd-quitar" type="button"${hayPropia ? '' : ' hidden'}>${_esc(_t('Quitar imagen'))}</button>
          <button class="sx-btn gho sm" id="fd-reset" type="button"${dis}>${_esc(_t('Restablecer'))}</button>
          <input type="file" id="fd-file" accept="image/*" hidden>
        </div>
        <div class="fd-sls">${sl}</div>
        <p class="fd-nota">${_esc(_t('Con un fondo activo, el texto de las terminales usa suavizado en escala de grises (se ve un poco más fino).'))}</p>
      </div>`;
  }

  /** Monta el bloque en `raiz` y lo mantiene al día. */
  function montar(raiz, o = {}) {
    const F = global.JarvisFondo;
    if (!raiz || !F) return;
    const icon = o.icon || global.icon;
    const toast = o.toast || ((m, t) => global.toast?.(m, t));
    let existe = F.actual().version > 0;
    let ocupado = false;

    const reducir = () => { try { return !!global.matchMedia?.('(prefers-reduced-transparency: reduce)').matches; } catch { return false; } };
    const glass = () => (global.JarvisGlass?.actual?.() ?? 'on') === 'on';

    function pintar() {
      if (!raiz.isConnected) { global.removeEventListener('glass-changed', pintar); return; }
      raiz.innerHTML = html(F.actual(), { F, glass: glass(), reducir: reducir(), existeImagen: existe, icon });
      cablear();
    }

    async function elegirArchivo(file) {
      if (!file || ocupado) return;
      ocupado = true;
      const btn = raiz.querySelector('#fd-subir');
      if (btn) btn.disabled = true;
      try {
        const d = await F.subir(file);
        existe = !!d.existe;
        toast(_t('Fondo actualizado'), 'info');
      } catch (e) {
        toast(e?.message || _t('No se pudo guardar la imagen'), 'error');
      } finally {
        ocupado = false;
        pintar();
      }
    }

    function cablear() {
      const file = raiz.querySelector('#fd-file');
      raiz.querySelector('#fd-on')?.addEventListener('change', (e) => { F.set({ on: e.target.checked }); });
      raiz.querySelectorAll('[data-fuente]').forEach(btn => btn.addEventListener('click', () => {
        const f = btn.dataset.fuente;
        if (f === 'custom' && !existe) { file?.click(); return; }
        F.set({ fuente: f, on: true });
        pintar();
      }));
      raiz.querySelector('#fd-subir')?.addEventListener('click', () => file?.click());
      file?.addEventListener('change', () => { const f = file.files?.[0]; file.value = ''; elegirArchivo(f); });
      raiz.querySelector('#fd-quitar')?.addEventListener('click', async () => {
        await F.quitarImagen();
        existe = false;
        pintar();
      });
      raiz.querySelector('#fd-reset')?.addEventListener('click', () => { F.restablecer(); pintar(); });
      raiz.querySelectorAll('input[type=range][data-k]').forEach(inp => inp.addEventListener('input', () => {
        const k = inp.dataset.k;
        F.set({ [k]: Number(inp.value) });
        const out = raiz.querySelector(`output[data-o="${k}"]`);
        if (out) out.textContent = etiquetaValor(k, Number(inp.value), F.RANGOS);
      }));
    }

    pintar();
    global.addEventListener('glass-changed', pintar);
    // Alinear con el servidor (otra pestaña pudo subir/quitar la imagen).
    F.reconciliar().then(d => { if (d) { existe = !!d.existe; if (raiz.isConnected) pintar(); } });
  }

  const api = { SLIDERS, etiquetaValor, avisos, html, montar };
  global.JarvisFondoUI = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
