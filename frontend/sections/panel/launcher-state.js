// JARVIS — Lógica pura del modal "Nuevo proyecto" (grid de CLIs + templates).
(function (global) {
  'use strict';

  const MAX_TERMINALES = 12;  // espejo de plotspace/routers/terminals.py (MAX_TERMINALES)
  const CLI_ORDEN  = ['claude', 'codex', 'opencode', 'qwen', 'antigravity', 'grok', 'cursor', 'pi', 'manual'];
  const CLI_LABELS = { claude: 'Claude Code', codex: 'Codex',
                       opencode: 'OpenCode', qwen: 'Qwen Code',
                       antigravity: 'Antigravity', grok: 'Grok Build',
                       cursor: 'Cursor', pi: 'Pi',
                       manual: 'Shell' };

  // Templates integrados (no borrables). Los del usuario viven en localStorage.
  const PRESETS = [
    { nombre: 'Solo Claude', counts: { claude: 1 } },
    { nombre: 'Dúo Claude',  counts: { claude: 2 } },
    { nombre: 'Squad mixto', counts: { claude: 1, codex: 1 } },
  ];
  const MAX_TEMPLATES   = 12;
  const MAX_NOMBRE_TPL  = 28;

  function totalContadores(counts) {
    return CLI_ORDEN.reduce((t, k) => t + ((counts && counts[k]) | 0), 0);
  }
  function loteDesdeContadores(counts, desde = 0) {
    const lote = [];
    for (const tipo of CLI_ORDEN) {
      const n = ((counts && counts[tipo]) | 0);
      for (let i = 0; i < n; i++) {
        lote.push({ nombre: `${CLI_LABELS[tipo]} #${desde + lote.length + 1}`, tipo_ia: tipo });
      }
    }
    return lote;
  }
  function etiquetaCrear(counts) {
    const t = totalContadores(counts);
    if (t === 0) return 'Crear proyecto';
    return `Crear con ${t} terminal${t === 1 ? '' : 'es'}`;
  }
  function etiquetaAbrir(counts) {
    const t = totalContadores(counts);
    if (t === 0) return 'Abrir proyecto';
    return `Abrir con ${t} terminal${t === 1 ? '' : 'es'}`;
  }
  // Launcher initial set: ALL zero — the user picks the agents. It used to come
  // with claude=1, preselecting Claude Code even when it wasn't installed.
  // Derived from CLI_ORDEN so a new CLI never falls outside the reset.
  function countsIniciales() {
    const out = {};
    for (const t of CLI_ORDEN) out[t] = 0;
    return out;
  }
  // n deseado para `tipo`, con `usadas` terminales ya vivas y los demás contadores fijos.
  function clampContador(n, usadas, counts, tipo) {
    const otros = totalContadores(Object.assign({}, counts, { [tipo]: 0 }));
    const libres = Math.max(0, MAX_TERMINALES - usadas - otros);
    return Math.min(libres, Math.max(0, n | 0));
  }

  // "2× Claude Code · 1× Codex" — resumen humano de un set de contadores.
  function resumenCounts(counts) {
    const partes = CLI_ORDEN
      .filter(t => ((counts && counts[t]) | 0) > 0)
      .map(t => `${counts[t]}× ${CLI_LABELS[t]}`);
    return partes.length ? partes.join(' · ') : 'Sin terminales';
  }

  // Aplica los counts de un template clampeando acumulativamente contra el
  // cupo libre (MAX - usadas), en orden CLI_ORDEN. Siempre devuelve un set
  // completo (todos los tipos presentes, aunque sea con 0).
  function aplicarTemplate(tCounts, usadas) {
    const out = {};
    let resto = Math.max(0, MAX_TERMINALES - Math.max(0, usadas | 0));
    for (const tipo of CLI_ORDEN) {
      const n = Math.min(resto, Math.max(0, ((tCounts && tCounts[tipo]) | 0)));
      out[tipo] = n;
      resto -= n;
    }
    return out;
  }

  // ¿Dos sets de contadores son equivalentes? (faltantes cuentan como 0)
  function mismosCounts(a, b) {
    return CLI_ORDEN.every(t => (((a && a[t]) | 0) === ((b && b[t]) | 0)));
  }

  // Sanitiza lo que venga de localStorage: shape {nombre, counts}, nombres
  // únicos y no vacíos, counts solo de CLIs conocidas, tope MAX_TEMPLATES.
  function templatesValidos(raw) {
    if (!Array.isArray(raw)) return [];
    const vistos = new Set();
    const out = [];
    for (const t of raw) {
      if (!t || typeof t.nombre !== 'string') continue;
      const nombre = t.nombre.trim().slice(0, MAX_NOMBRE_TPL);
      if (!nombre || vistos.has(nombre)) continue;
      const counts = {};
      let total = 0;
      for (const tipo of CLI_ORDEN) {
        const n = Math.min(MAX_TERMINALES, Math.max(0, ((t.counts && t.counts[tipo]) | 0)));
        if (n > 0) { counts[tipo] = n; total += n; }
      }
      if (total === 0) continue;
      vistos.add(nombre);
      out.push({ nombre, counts });
      if (out.length >= MAX_TEMPLATES) break;
    }
    return out;
  }

  // Terminales ya vivas en el proyecto DESTINO del lanzamiento (mismo criterio
  // que _tlResolverDestino): vacía/relativa → el actual; absoluta dentro de un
  // proyecto conocido → ese; carpeta desconocida → proyecto nuevo (0).
  function usadasDestino(ruta, proyectos, idActual, usadasActual) {
    const r = (ruta || '').trim();
    if (!r || !r.startsWith('/')) return usadasActual | 0;
    const p = r.replace(/\/+$/, '');
    const m = (proyectos || []).find(x => x && x.ruta && (p === x.ruta || p.startsWith(x.ruta + '/')));
    if (!m) return 0;
    if (String(m.id) === String(idActual)) return usadasActual | 0;
    return (m.terminales_activas | 0);
  }

  // CLIs del orden que faltan instalar según GET /api/clis. Los pickers los
  // pliegan al final («+N sin instalar») para que no dominen la grilla.
  function faltantes(orden, estado) {
    const clis = Array.isArray(estado && estado.clis) ? estado.clis : [];
    return (orden || []).filter(t => t !== 'manual' && clis.some(c => c && c.id === t && !c.instalado));
  }

  // ¿Con qué selector se elige la carpeta? El del SISTEMA si el servidor puede abrirlo
  // en esta máquina (cap = GET /api/fs/nativo); si no, el explorador propio de Jarvis
  // (respaldo). Si lo que falta es una herramienta instalable (zenity en Linux) se avisa
  // UNA vez cómo instalarla.
  function planSelector(cap, yaAvisado) {
    if (cap && cap.disponible) return { usar: 'nativo', avisar: false };
    const instalable = !!cap && cap.razon === 'falta_herramienta' && !!cap.ayuda;
    return { usar: 'web', avisar: instalable && !yaAvisado };
  }

  // Dónde arranca el diálogo del sistema: la ruta que ya hay en el campo (si es
  // absoluta), si no la base de proyectos (modo "Crear") o el home de ESTA máquina.
  function inicioSelector(valor, env, target) {
    const v = String(valor || '').trim();
    if (v.startsWith('/')) return v.replace(/\/+$/, '') || '/';
    if (target === 'loc' && env && env.proyectos) return env.proyectos;
    return (env && env.home) || '';
  }

  // Qué decirle a la persona mientras el diálogo del sistema está abierto (puede
  // abrirse DETRÁS del navegador). Devuelve la clave del texto, no el texto.
  function pistaEspera(entorno) {
    return entorno === 'wsl' ? 'wsl' : entorno === 'macos' ? 'macos' : 'linux';
  }

  const pure = { planSelector, inicioSelector, pistaEspera, MAX_TERMINALES, CLI_ORDEN, CLI_LABELS, PRESETS, MAX_TEMPLATES,
                 totalContadores, loteDesdeContadores, etiquetaCrear, etiquetaAbrir,
                 countsIniciales, clampContador, usadasDestino, faltantes,
                 resumenCounts, aplicarTemplate, mismosCounts, templatesValidos };
  global.JarvisLauncherState = pure;
  if (typeof module !== 'undefined' && module.exports) module.exports = pure;
})(typeof window !== 'undefined' ? window : globalThis);
