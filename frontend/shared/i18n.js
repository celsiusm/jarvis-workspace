/* ════════════════════════════════════════════════════════════════
   Jarvis i18n — traductor de interfaz español ⇆ inglés.

   La app está escrita en español (en el HTML y en los strings de JS). En vez de
   etiquetar cientos de strings en cada archivo, este motor traduce el DOM en
   runtime contra un diccionario es→en y observa los cambios (las secciones que
   re-renderizan se traducen solas). Cambiar de idioma NO recarga la página.

   - lang 'es' = original (no se toca nada). 'en' = traduce contra DICT.
   - Se respeta el contenido del USUARIO / dinámico: subárboles con
     [data-i18n-skip], terminales (.xterm), editor de código (.monaco-editor),
     <textarea>, <canvas>, <script>, <style> y los iframes (no se entra) quedan
     intactos.
   - Persistencia: localStorage 'jarvis.lang' (default 'en').
═══════════════════════════════════════════════════════════════ */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.JarvisI18n = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // Diccionario es→en. Se completa con las frases de toda la UI (ver final del
  // archivo, sección DICT). Clave = frase en español NORMALIZADA (espacios
  // colapsados, sin espacios al borde).
  var DICT = {};

  // Atributos de texto a traducir además del contenido.
  var ATTRS = ['placeholder', 'title', 'aria-label'];

  function normalizar(s) { return (s == null ? '' : String(s)).replace(/\s+/g, ' ').trim(); }

  // PURA: traducción de una frase a `lang` contra `dict`, o null si no aplica
  // (idioma es, frase vacía, sin entrada, o traducción == original).
  function traducir(texto, lang, dict) {
    if (lang !== 'en') return null;
    var k = normalizar(texto);
    if (!k) return null;
    var v = dict[k];
    return (v && v !== k) ? v : null;
  }

  // PURA: parte un valor de texto en (pre-espacios, núcleo, post-espacios) para
  // poder traducir el núcleo conservando el whitespace que lo rodea.
  function partes(valor) {
    var m = String(valor == null ? '' : valor).match(/^(\s*)([\s\S]*?)(\s*)$/);
    return { pre: m[1], core: m[2], post: m[3] };
  }

  // PURA: qué valor ORIGINAL (en español) usar para un atributo. `guardado`
  // es el original que se recordó al traducirlo; `actual`, lo que tiene ahora.
  // Si `actual` no es ni el original ni su traducción, lo cambió la APP (p.ej.
  // el botón Enviar que pasa a Detener): ese es el nuevo original. Antes se
  // restauraba siempre el guardado y ningún aria-label/title dinámico
  // sobrevivía en inglés.
  function origenAtributo(guardado, actual, lang, dict) {
    if (guardado == null) return actual;
    if (actual === guardado || actual === traducir(guardado, lang, dict)) return guardado;
    return actual;
  }

  // ── Motor DOM (solo navegador) ───────────────────────────────────────────
  if (typeof document !== 'undefined') {
    var _lang = (function () {
      var v = null;
      try { v = localStorage.getItem('jarvis.lang'); } catch (e) {}
      if (v === 'en' || v === 'es') return v;
      // Sin preferencia guardada: INGLÉS por defecto — el idioma main del
      // producto es el inglés (la mayoría de los usuarios vienen en inglés).
      // Si el sistema es español no tuvimos hasta hoy forma de distinguir
      // "usuario nuevo hispano" y esto ya se definió así. El selector de
      // ⚙→Apariencia persiste la elección y desde entonces manda él.
      return 'en';
    })();
    var WM_TXT = new WeakMap();   // textNode -> nodeValue original (es)
    var WM_ATTR = new WeakMap();  // element  -> { attr: valorOriginal }
    var _obs = null;
    var _SKIP_TAGS = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, CANVAS: 1, NOSCRIPT: 1 };
    // Para ATRIBUTOS (placeholder/title/aria-label) NO excluimos <textarea>: su
    // CONTENIDO es dato del usuario (y eso sí lo salta el walker de texto), pero
    // su placeholder/aria-label son chrome de UI y deben traducirse.
    var _SKIP_TAGS_ATTR = { SCRIPT: 1, STYLE: 1, CANVAS: 1, NOSCRIPT: 1 };

    function _enZonaExcluidaPor(el, tags) {
      for (; el && el.nodeType === 1; el = el.parentElement) {
        if (tags[el.tagName]) return true;
        if (el.hasAttribute('data-i18n-skip')) return true;
        var c = el.classList;
        if (c && (c.contains('xterm') || c.contains('monaco-editor'))) return true;
      }
      return false;
    }
    function _enZonaExcluida(el) { return _enZonaExcluidaPor(el, _SKIP_TAGS); }
    function _enZonaExcluidaAttrs(el) { return _enZonaExcluidaPor(el, _SKIP_TAGS_ATTR); }

    function _traducirTexto(node) {
      if (_enZonaExcluida(node.parentElement)) return;
      var orig = WM_TXT.has(node) ? WM_TXT.get(node) : node.nodeValue;
      if (_lang === 'en') {
        var p = partes(orig);
        var tr = traducir(p.core, 'en', DICT);
        if (tr != null) {
          if (!WM_TXT.has(node)) WM_TXT.set(node, orig);
          var nuevo = p.pre + tr + p.post;
          if (node.nodeValue !== nuevo) node.nodeValue = nuevo;
        }
      } else if (WM_TXT.has(node)) {            // volver a es
        if (node.nodeValue !== orig) node.nodeValue = orig;
        WM_TXT.delete(node);
      }
    }

    function _traducirAttrs(el) {
      if (_enZonaExcluidaAttrs(el)) return;
      for (var i = 0; i < ATTRS.length; i++) {
        var a = ATTRS[i];
        if (!el.hasAttribute(a)) continue;
        var guard = WM_ATTR.get(el) || {};
        var actual = el.getAttribute(a);
        var orig = origenAtributo((a in guard) ? guard[a] : null, actual, 'en', DICT);
        if ((a in guard) && orig !== guard[a]) delete guard[a];   // lo cambió la app
        if (_lang === 'en') {
          var tr = traducir(orig, 'en', DICT);
          if (tr != null) {
            if (!(a in guard)) { guard[a] = orig; WM_ATTR.set(el, guard); }
            if (actual !== tr) el.setAttribute(a, tr);
          }
        } else if (a in guard) {
          if (el.getAttribute(a) !== orig) el.setAttribute(a, orig);
          delete guard[a];
        }
      }
    }

    function _recorrer(root) {
      if (!root) return;
      // Atributos: el root y sus descendientes elementos.
      if (root.nodeType === 1) {
        _traducirAttrs(root);
        var els = root.querySelectorAll('[placeholder],[title],[aria-label]');
        for (var i = 0; i < els.length; i++) _traducirAttrs(els[i]);
      }
      // Texto: TreeWalker de nodos de texto.
      var base = root.nodeType === 1 ? root : root.parentElement;
      if (!base) { if (root.nodeType === 3) _traducirTexto(root); return; }
      var tw = document.createTreeWalker(base, NodeFilter.SHOW_TEXT, null);
      var n; var lista = [];
      while ((n = tw.nextNode())) lista.push(n);
      for (var j = 0; j < lista.length; j++) _traducirTexto(lista[j]);
    }

    function aplicar(root) { _recorrer(root || document.body); }

    function _arrancarObserver() {
      if (_obs || !document.body) return;
      _obs = new MutationObserver(function (muts) {
        if (_lang !== 'en') return;        // en es no hay nada que traducir
        for (var i = 0; i < muts.length; i++) {
          var m = muts[i];
          if (m.type === 'attributes' && m.target.nodeType === 1) { _traducirAttrs(m.target); continue; }
          for (var k = 0; k < m.addedNodes.length; k++) {
            var nd = m.addedNodes[k];
            if (nd.nodeType === 1) _recorrer(nd);
            else if (nd.nodeType === 3) _traducirTexto(nd);
          }
        }
      });
      _obs.observe(document.body, {
        childList: true, subtree: true,
        attributes: true, attributeFilter: ATTRS,
      });
    }

    function setLang(lang) {
      lang = (lang === 'en') ? 'en' : 'es';
      if (lang === _lang) return;
      _lang = lang;
      try { localStorage.setItem('jarvis.lang', lang); } catch (e) {}
      document.documentElement.setAttribute('lang', lang);
      aplicar(document.body);
      try { window.dispatchEvent(new CustomEvent('jarvis:lang', { detail: { lang: lang } })); } catch (e) {}
    }

    function t(esFrase) {
      var tr = traducir(esFrase, _lang, DICT);
      return tr != null ? tr : esFrase;
    }

    function init() {
      document.documentElement.setAttribute('lang', _lang);
      if (_lang === 'en') aplicar(document.body);
      _arrancarObserver();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();

    var api = {
      _pure: { traducir: traducir, normalizar: normalizar, partes: partes, origenAtributo: origenAtributo },
      t: t, setLang: setLang, aplicar: aplicar,
      lang: function () { return _lang; },
      DICT: DICT,
      agregar: function (mas) { for (var k in mas) if (mas.hasOwnProperty(k)) DICT[normalizar(k)] = mas[k]; },
    };
    // El IIFE externo asigna root.JarvisI18n = factory(); acá solo retornamos
    // (la factory no tiene `root` en su scope — referirlo tiraba ReferenceError).
    return api;
  }

  // ── Export para Node (tests de la lógica pura) ─────────────────────────────
  return {
    _pure: { traducir: traducir, normalizar: normalizar, partes: partes, origenAtributo: origenAtributo },
    DICT: DICT,
  };
}));
