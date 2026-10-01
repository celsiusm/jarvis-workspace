// JARVIS — "Instalar" un CLI de agente que falta (picker de Nueva terminal y modal
// "Agregar proyecto"). Un solo lugar decide QUÉ hace el botón y vigila cuándo termina:
//
//   · con comando + proyecto abierto → abre una TERMINAL nueva y tipea ahí la línea de
//     instalación, a la vista (POST …/terminals/batch con `instalar_cli`: el SERVER arma
//     el comando — el navegador nunca decide qué se ejecuta);
//   · con comando pero sin proyecto → instalación en segundo plano por npm (si se puede);
//   · sin comando (Antigravity: app de escritorio) → abre el sitio oficial.
//
// Mientras instala, consulta GET /api/clis?refrescar=1 hasta que el CLI aparece y avisa.
// El estado nuevo se publica en window.JarvisClisEstado + el evento `clis-estado`: los
// pickers abiertos se repintan solos.
(function (global) {
  'use strict';

  const POLL_MAX_MS = 8 * 60 * 1000;     // tras esto se deja de vigilar (la instalación pudo fallar)

  // ── Lógica PURA ────────────────────────────────────────────────────────────

  /** Qué hace "Instalar" para este CLI. `cli` = una entrada de GET /api/clis. */
  function planInstalacion(cli, o = {}) {
    if (!cli || cli.instalado) return { accion: 'ninguna', motivo: 'instalado' };
    if (o.enCurso) return { accion: 'ninguna', motivo: 'en_curso' };
    if (cli.comando) {
      if (o.hayProyecto) return { accion: 'terminal', comando: cli.comando };
      if (cli.instalable) return { accion: 'silenciosa', comando: cli.comando };
      return { accion: 'ninguna', motivo: 'sin_proyecto' };
    }
    if (cli.url) return { accion: 'sitio', url: cli.url };
    return { accion: 'ninguna', motivo: 'sin_via' };
  }

  /** Lo que la fila necesita para pintarse. */
  function estadoFila(cli, o = {}) {
    if (!cli || cli.instalado) return { falta: false, instalando: false, plan: { accion: 'ninguna', motivo: 'instalado' } };
    const instalando = !!o.enCurso;
    const plan = planInstalacion(cli, o);
    return {
      falta: true, instalando, plan,
      comando: cli.comando || null,
      conNode: !!cli.con_node,
      url: cli.url || null,
      // El botón se ofrece si hay algo que hacer, o si está en curso (para mostrar el estado).
      accionable: plan.accion === 'terminal' || plan.accion === 'silenciosa' || plan.accion === 'sitio',
    };
  }

  /** Nombre de la terminal de instalación. */
  function nombreTerminal(cli) { return `Instalar ${(cli && cli.nombre) || ''}`.trim(); }

  /** ids que estaban faltando y ahora están instalados (para avisar "listo"). */
  function recienInstalados(antes, ahora, ids) {
    const a = new Map((antes || []).map(c => [c.id, c]));
    const n = new Map((ahora || []).map(c => [c.id, c]));
    return [...ids].filter(id => n.get(id) && n.get(id).instalado && !(a.get(id) && a.get(id).instalado));
  }

  /** Cuánto esperar antes de volver a mirar: rápido al principio, después más espaciado. */
  function esperaPoll(intento) { return intento < 12 ? 3000 : 6000; }

  const pure = { planInstalacion, estadoFila, nombreTerminal, recienInstalados, esperaPoll, POLL_MAX_MS };
  global.JarvisCliInstalar = Object.assign(global.JarvisCliInstalar || {}, { _pure: pure, ...pure });
  if (typeof module !== 'undefined' && module.exports) { module.exports = pure; return; }
  if (typeof document === 'undefined') return;

  // ── Navegador ──────────────────────────────────────────────────────────────

  const _t = (s) => (global.JarvisI18n?.t ? global.JarvisI18n.t(s) : s);
  const _aviso = (m, tipo, ms) => { try { global.toast?.(m, tipo, ms); } catch (_) {} };
  let _proyectoId = () => null;
  const _enCurso = new Set();
  let _poll = null;

  function init(o) { if (o && typeof o.proyectoId === 'function') _proyectoId = o.proyectoId; }

  const _estado = () => (Array.isArray(global.JarvisClisEstado?.clis) ? global.JarvisClisEstado.clis : []);
  const cliPorId = (id) => _estado().find(c => c.id === id) || null;
  const enCurso = (id) => _enCurso.has(id);

  /** Publica un estado nuevo de /api/clis: cache global, localStorage y el evento. */
  function aplicarEstado(d) {
    if (!d || !Array.isArray(d.clis)) return;
    global.JarvisClisEstado = d;
    try { localStorage.setItem('jarvis_clis_estado', JSON.stringify(d)); } catch (_) {}
    try { global.dispatchEvent(new CustomEvent('clis-estado', { detail: d })); } catch (_) {}
  }
  const _avisarCambio = () => aplicarEstado(global.JarvisClisEstado);

  async function refrescar() {
    try {
      const r = await fetch('/api/clis?refrescar=1');
      if (!r.ok) return null;
      const d = await r.json();
      aplicarEstado(d);
      return d;
    } catch (_) { return null; }
  }

  function _vigilar(id) {
    _enCurso.add(id);
    _avisarCambio();
    if (_poll) return;
    const inicio = Date.now();
    let intento = 0;
    const tick = async () => {
      const antes = _estado();
      const d = await refrescar();
      if (d) {
        for (const listo of recienInstalados(antes, d.clis, _enCurso)) {
          _enCurso.delete(listo);
          const cli = d.clis.find(c => c.id === listo);
          _aviso(_t('{cli} quedó instalado: ya lo podés lanzar').replace('{cli}', (cli && cli.nombre) || listo), 'success', 7000);
        }
      }
      if (!_enCurso.size || Date.now() - inicio > POLL_MAX_MS) {
        _enCurso.clear(); _poll = null; _avisarCambio(); return;
      }
      _poll = setTimeout(tick, esperaPoll(++intento));
    };
    _poll = setTimeout(tick, esperaPoll(0));
  }

  async function _enTerminal(cli) {
    const pid = _proyectoId();
    let res;
    try {
      res = await fetch(`/api/projects/${pid}/terminals/batch`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ terminales: [{ nombre: nombreTerminal(cli), tipo_ia: 'manual' }], instalar_cli: cli.id }),
      });
    } catch (e) {
      _aviso(_t('No se pudo abrir la terminal de instalación: {m}').replace('{m}', e.message || ''), 'error');
      return { ok: false };
    }
    if (!res.ok) {
      let d = null; try { d = await res.json(); } catch (_) {}
      _aviso(_t('No se pudo abrir la terminal de instalación: {m}').replace('{m}', (d && d.detail) || res.status), 'error');
      return { ok: false };
    }
    const creadas = await res.json();
    (creadas || []).forEach(t => { try { global.agregarTarjetaTerminal?.(t); } catch (_) {} });
    try { global.actualizarVista?.(); } catch (_) {}
    _aviso(_t('Instalando {cli} en una terminal nueva…').replace('{cli}', cli.nombre), 'info', 6000);
    _vigilar(cli.id);
    return { ok: true, via: 'terminal' };
  }

  async function _silenciosa(cli) {
    _enCurso.add(cli.id);
    _avisarCambio();
    _aviso(_t('Instalando {cli}… puede tardar unos minutos').replace('{cli}', cli.nombre), 'info', 8000);
    let r = null;
    try {
      const res = await fetch(`/api/clis/${encodeURIComponent(cli.id)}/instalar`, { method: 'POST' });
      r = await res.json();
    } catch (e) { r = { ok: false, salida: e.message }; }
    _enCurso.delete(cli.id);
    if (r && r.ok) {
      _aviso(_t('{cli} quedó instalado: ya lo podés lanzar').replace('{cli}', cli.nombre), 'success', 7000);
    } else {
      _aviso(_t('{cli} no se pudo instalar: {m}').replace('{cli}', cli.nombre).replace('{m}', (r && (r.salida || r.detail)) || ''), 'error', 9000);
    }
    await refrescar();
    return { ok: !!(r && r.ok), via: 'silenciosa' };
  }

  /** El clic de "Instalar". */
  async function instalar(id) {
    const cli = cliPorId(id);
    const plan = planInstalacion(cli, { hayProyecto: !!_proyectoId(), enCurso: enCurso(id) });
    if (plan.accion === 'sitio') {
      try { global.open(plan.url, '_blank', 'noopener,noreferrer'); } catch (_) {}
      return { ok: true, via: 'sitio' };
    }
    if (plan.accion === 'terminal') return _enTerminal(cli);
    if (plan.accion === 'silenciosa') return _silenciosa(cli);
    if (plan.motivo === 'sin_proyecto') _aviso(_t('Abrí un proyecto para instalarlo desde una terminal'), 'info');
    return { ok: false, via: 'ninguna' };
  }

  const hayProyecto = () => !!_proyectoId();
  Object.assign(global.JarvisCliInstalar, { init, instalar, enCurso, refrescar, aplicarEstado, cliPorId, hayProyecto });
})(typeof window !== 'undefined' ? window : globalThis);
