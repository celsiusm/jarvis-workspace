// JARVIS — Workspace: terminales + orquestador + sidebar de proyectos

// projectId es mutable: cambia al navegar entre proyectos sin recargar
let projectId = new URLSearchParams(location.search).get('id');
const _bootstrapNuevo = new URLSearchParams(location.search).get('launcher') === 'nuevo';
if (!projectId && !_bootstrapNuevo) location.href = '/';

// ─── Referencias DOM ──────────────────────────────────────────────
const elTitulo         = document.getElementById('project-title');
const elRuta           = document.getElementById('project-path');
const elGrid           = document.getElementById('terminals-grid');
const elVacio          = document.getElementById('terminals-empty');
const modalTerminal    = document.getElementById('modal-new-terminal');
const elSidebarNav     = document.getElementById('sidebar-nav');

// CTA del empty state de terminales ("+ Nueva terminal") → abre el PICKER de
// terminal rápida (crear una terminal EN el proyecto actual), NO el launcher de
// Nuevo proyecto (ese pide carpeta/ubicación = flujo de "workspaces"). Pedido del
// usuario 2026-07-06: estando en un proyecto sin terminales, el botón tiene que
// dejarte crear una terminal, no mandarte a crear otro proyecto.
// _abrirQuickPicker es function declaration (hoisted) → disponible al click.
document.getElementById('terminals-empty-cta')?.addEventListener('click', () => {
  _abrirQuickPicker();
});
// Las otras cards del "Arranque" (rediseño 2026-07-10): Jarvis / Editor.
document.getElementById('tea-card-jarvis')?.addEventListener('click', () => window.JarvisDock?.setTab?.('jarvis'));
document.getElementById('tea-card-editor')?.addEventListener('click', () => _abrirEditorWorkspace());
// Spotlight de las cards (el radial de ::after sigue al cursor vía --mx/--my).
document.querySelectorAll('.tea-card').forEach((el) => {
  el.addEventListener('pointermove', (e) => {
    const r = el.getBoundingClientRect();
    el.style.setProperty('--mx', ((e.clientX - r.left) / r.width * 100).toFixed(1) + '%');
    el.style.setProperty('--my', ((e.clientY - r.top) / r.height * 100).toFixed(1) + '%');
  });
});
// Keycaps vivas del Arranque: reflejan la tecla CONFIGURADA (no la de fábrica).
// _prettyKeyLabel es function declaration (hoisted). Bindings en localStorage
// jarvis.control.<id> con shape {type:'key'|'mouse', value}.
(function _teaKeycaps() {
  const label = (id, fallback) => {
    try {
      const b = JSON.parse(localStorage.getItem(`jarvis.control.${id}`) || 'null');
      if (b?.type === 'key' && b.value) return _prettyKeyLabel(b.value);
      if (b?.type === 'mouse') return `Mouse ${b.value}`;
    } catch { /* binding corrupto → fábrica */ }
    return fallback;
  };
  const kt = document.getElementById('tea-kbd-term');
  if (kt) kt.textContent = `Ctrl ${label('quick-terminal', '\\')}`;
  const pk = document.getElementById('tea-ptt-key');
  if (pk) pk.textContent = label('mic-ptt', 'Alt');
})();
// "Editor" tiene que MOSTRAR el editor del workspace actual, siempre. toggle()
// sin pestañas abiertas era un no-op (el deslizante solo abría al clickear un
// archivo del árbol) → acá: abierto = cerrar (toggle de siempre); con pestañas
// previas = reabrirlas; sin nada = abrir el primer archivo con sentido del
// proyecto (README/CLAUDE/package.json…, si no el primero del árbol).
async function _abrirEditorWorkspace() {
  const ed = window.JarvisSlideEditor;
  if (!ed) return;
  if (ed.isOpen?.()) { ed.toggle?.(); return; }
  setSidebarView('editor');               // la franja DIBUJA el árbol de archivos del workspace
  ed.toggle?.();                          // con pestañas previas esto ya lo abre
  if (ed.isOpen?.()) return;
  const data = await _sbFetchTree(projectId);
  const pick = _primerArchivoDelArbol(data?.children || []);
  if (!pick) { toast(window.JarvisI18n?.t?.('El proyecto no tiene archivos todavía') ?? 'El proyecto no tiene archivos todavía', 'info'); return; }
  _sbAbrirArchivo(projectId, pick);
}
function _primerArchivoDelArbol(nodes) {
  const files = nodes.filter(n => n.type === 'file');
  // primero los "portada" del proyecto, en este orden
  const pref = ['readme.md', 'claude.md', 'agents.md', 'package.json', 'index.html', 'main.py'];
  for (const nombre of pref) {
    const hit = files.find(f => f.name.toLowerCase() === nombre);
    if (hit) return hit.path;
  }
  if (files[0]) return files[0].path;
  for (const d of nodes.filter(n => n.type === 'dir')) {   // root sin archivos → primera hoja
    const sub = _primerArchivoDelArbol(d.children || []);
    if (sub) return sub;
  }
  return null;
}

// Helpers para acceder al nuevo OrchestratorPanel (disponible tras DOMContentLoaded)
const _panel  = () => window.jarvisPanel;
const _panelTA = () => _panel()?.$textarea;

// True si el usuario tiene el foco en otro campo editable o dentro de un
// iframe (la app del mobile preview / web builder): no hay que robárselo.
function _focoOcupadoEnOtroLado() {
  const a = document.activeElement;
  if (!a || a === document.body) return false;
  if (a === _panelTA()) return false;        // ya está en el composer de Jarvis
  if (a.tagName === 'IFRAME') return true;   // interactuando con un preview
  return _esTargetEditable(a);               // tipeando en otro campo
}

let sphereState   = 'idle';
let mediaRecorder = null;
let audioChunks   = [];
// Generación DUEÑA de audioChunks (la sella _armarRecorder al resetear el
// array). Evita que un dictado cuyo recorder nunca llegó a armarse procese
// chunks STALE del dictado anterior (re-transcribiría el mensaje viejo).
let _chunksGen    = -1;
// Segmentos de audio de recorders MUERTOS mid-hold (el revive los snapshotea
// antes de re-armar): al commitear se transcribe cada uno y se unen los
// textos — un mic que se cortó a mitad del dictado ya no pierde el arranque.
let _segmentosPrevios = [];
// true desde que el usuario SOLTÓ el PTT: si getUserMedia todavía estaba
// inicializando el mic, iniciarGrabacion aborta al resolver (sin esto la
// grabación arrancaba huérfana y el Voice HUD quedaba en "Te escucho" eterno).
let _micSoltado   = false;
// Generación de grabación: cada hold la incrementa. Invalida los flujos de
// ciclos viejos en holds rápidos encadenados — el onstop de un recorder
// stale no procesa, y un Whisper lento de un ciclo anterior no pisa el
// dictado del ciclo actual.
let _micGen       = 0;
// Guard de idempotencia del cierre del dictado (release y onstop del recorder
// pueden llamarlo los dos; solo el primero procesa). Mientras se procesa, el
// orbe del HUD muestra un spinner de "cargando" (modo proc).
// (La "ventana de gracia" post-soltar murió 2026-07-17 con el SpeechRecognition:
// existía solo para que el SR volcara su última palabra rezagada. Sin SR, el
// cierre es inmediato → ~650ms menos de latencia por dictado.)
let _finalizandoGen = -1;
// Cola de dictado al soltar el PTT (pedido 2026-07-09): el usuario suele soltar
// ANTES de terminar de decir la última palabra y se cortaba. Tras soltar se
// sigue CAPTURANDO (recorder vivo, la píldora sigue en "rec") _TAIL_MS más, y
// recién ahí se frena y se procesa. Un hold nuevo (o el discard del tap corto)
// fast-forwardea vía _tailCerrar: commitea, no pierde.
const _TAIL_MS = 2000;          // TOPE de la cola (seguís hablando al soltar → te espera hasta acá; 1500→2000 pedido 2026-07-12 "que agarre bien el último mensaje")
// Cola INTELIGENTE (pedido 2026-07-10 "que no tarde >4s"): si al soltar ya
// terminaste de hablar, la cola cierra sola al detectar _TAIL_SILENCIO_MS de
// silencio (el tick del waveform sella _micVozTs en cada frame con voz) en vez
// de esperar siempre el tope. _TAIL_MIN_MS evita cerrar antes de que el último
// fonema llegue al recorder. Sin waveform (reduced-motion / sin WebAudio)
// _micVozTs queda en 0 y la cola espera el tope completo — nunca corta a ciegas.
const _TAIL_MIN_MS = 400;
const _TAIL_SILENCIO_MS = 700;
let _micVozTs = 0;              // performance.now() del último frame CON voz (lo sella el waveform)
let _tailT = null;              // timer único de la cola post-soltar (el tope)
let _tailIntervalo = null;      // checker del cierre temprano por silencio
let _tailCerrar = null;         // frena la cola YA (la usan un hold nuevo / el discard)
// Timing del dictado (release → texto listo): va al dictado-log para medir la
// latencia REAL con el mic del usuario.
let _dictadoT0 = 0;
// Transcripción TEMPRANA: el fetch a /transcribe arranca apenas cierra la cola
// (audio completo), antes de que procesarAudio lo espere. {gen, promesa} o null.
let _precisoFetch = null;
// Supervivencia de captura mid-hold (bug 2026-07-17 "se envió solo sin soltar"):
// el track del mic moría espontáneamente (device flap de Windows) y el onstop
// del recorder lo trataba como release → commiteaba y ENVIABA a mitad del
// dictado. Ahora un stop sin release REVIVE la captura (PttFijado.alPararRecorder)
// y el commit corre recién al soltar de verdad. El audio del recorder revivido
// queda incompleto (perdió el tramo pre-muerte): se transcribe igual lo que
// haya — un dictado parcial con el toast de "se cortó el mic" avisando.
let _recRevividas = 0;          // resurrecciones del recorder en el dictado actual
const _MAX_REVIVIDAS_REC = 3;   // mic muerto de verdad: no loopear re-abriéndolo
// El stream del micrófono se mantiene CALIENTE entre dictados: getUserMedia en
// frío re-abre el dispositivo y en Windows/WSL tarda ~1-3s — con la ventana en
// 2500ms TODO dictado real pagaba ese arranque (nadie re-dicta en <2,5s; el
// usuario lo sentía como "el mic tarda ~1s en escucharme" en Jarvis).
// HISTORIA: 60s monopolizaba el mic… pero eso era el pipeline "communications"
// de Windows que activaba el echoCancellation — causa raíz ELIMINADA cuando
// _obtenerMicStream pasó a AEC/NS/AGC off. Con esa causa afuera, volvemos a
// 60s (ritmo real de conversación). Si reaparece degradación del mic en otras
// apps con Jarvis abierto: bajar de nuevo y buscar otra vía (este comentario
// es la memoria de esa deuda). El guard de visibilitychange sigue soltándolo
// AL INSTANTE al saltar a otra app — la ventana caliente solo corre en foco.
let _micStream    = null;
let _micWarmTimer = 0;
let _micReleaseDeadline = 0;   // Date.now() en que vence la ventana caliente vigente
const _MIC_WARM_MS = 60_000;
// Mic pre-calentado (abierto sin dictar aún): se suelta antes que la ventana
// post-dictado, para no dejar el indicador "grabando" del SO prendido de gusto.
// Cubre el "apreté y ya me escucha" del primer PTT sin retener el dispositivo.
// 8s→15s (pedido 2026-07-10: "que escuche desde el primer momento") + re-armado
// por ACTIVIDAD (ver _instalarLiberacionMicCicloVida): mientras usás el
// workspace el mic queda listo, y se suelta ~15s después de la última actividad.
const _MIC_PRECALENTADO_MS = 15_000;
let eventsWs      = null;
let ttsActivo     = false;

// ─── Estado de los paneles: ahora vive en window.JarvisDock ──────────
// editor.js lee window.editorVisible como getter en vivo (editor.js:2282).
// Lo derivamos del dock: la pestaña 'editor' activa Y el dock abierto.
Object.defineProperty(window, 'editorVisible', {
  get: () => window.JarvisDock?.activeTab?.() === 'editor',
  configurable: true,
});

// ─── Estado del editor Monaco ───────────────────────────────────────
// MOVIDO a frontend/js/editor.js (window.JarvisEditor). workspace.js ya no
// posee monacoEditor/openTabs/activeTab/etc. Se accede vía la superficie pública.

const chatSesiones = {};

// mapeo recording→listening para el panel que maneja voz distinto
const _SPHERE_MAP = { wake: 'idle', idle: 'idle', recording: 'listening', processing: 'processing', responding: 'responding' };

// ─── Target de la voz: a dónde mandar la transcripción ───────────────
// _voiceTarget = último destino APUNTADO (click, o hover que se quedó quieto
// sobre una card ≥ HOVER_DWELL_MS). _hoverVoz = qué hay bajo el cursor AHORA:
// tener el mouse encima de una terminal alcanza para hablarle, clickeada o no
// (pedido 2026-07-23). _activeVoiceSession es el snapshot del destino al
// EMPEZAR a grabar: mover el mouse mientras dictás no desvía el mensaje en curso.
// La decisión pura vive en shell/voice-target.js (testeada en Node).
let _voiceTarget       = { type: 'jarvis' };
let _activeVoiceSession = null;
let _hoverVoz          = null;   // {type:'terminal',id} | {type:'jarvis'} | null

function _resolveVoiceTarget() {
  const a = document.activeElement;
  return window.VoiceTarget.resolverDestinoVoz({
    proyectoAbierto: !!projectId,
    // Jarvis "visible" = dock abierto en la pestaña 'jarvis' (con Jarvis oculto
    // la voz se la queda una terminal — bug histórico "Jarvis oculto seguía
    // recibiendo").
    jarvisVisible: window.JarvisDock?.activeTab?.() === 'jarvis',
    fijado:        _voiceTarget,
    hover:         _hoverVoz,
    terminales:    [...terminales.keys()],
    activaId:      _idCardActiva(),
    // Foco del teclado en el composer de Jarvis: estás escribiendo ahí, el
    // dictado anexa a ese texto aunque el mouse descanse sobre una terminal.
    focoJarvis:    !!a && (a === _panelTA() || !!a.closest?.('#orch-panel')),
  });
}

// Superficie de diagnóstico: leer a quién le iría la voz AHORA sin tocar el
// mic (QA en browser y debug de "¿por qué se fue a esa terminal?").
window.JarvisVoz = {
  destino: () => _resolveVoiceTarget(),
  hover:   () => _hoverVoz,
  fijado:  () => _voiceTarget,
  sesion:  () => _activeVoiceSession,
};

// id de la terminal marcada `.activa` (la última clickeada), o null.
function _idCardActiva() {
  const activa = document.querySelector('.terminal-card.activa');
  if (!activa) return null;
  const id = parseInt(activa.id.replace('terminal-card-', ''), 10);
  return isNaN(id) ? null : id;
}

// id de terminal de una card del DOM (o null si el elemento no es una card).
function _idDeCard(el) {
  const card = el?.closest?.('.terminal-card');
  if (!card || !card.id.startsWith('terminal-card-')) return null;
  const id = parseInt(card.id.replace('terminal-card-', ''), 10);
  return isNaN(id) ? null : id;
}

// Proyecto en la pantalla de arranque: ninguna terminal creada. Es la condición
// de TODO lo de manos libres (destino + campo de escucha).
function _sinTerminales() {
  return terminales.size === 0;
}

// Click en una terminal → target = esa terminal.
// Click en el panel del orquestador → target = jarvis.
// Capture phase para llegar primero, y también para que xterm no se coma el evento.
document.addEventListener('click', (e) => {
  const card = e.target.closest('.terminal-card');
  if (card && card.id.startsWith('terminal-card-')) {
    const id = parseInt(card.id.replace('terminal-card-', ''), 10);
    if (!isNaN(id)) {
      _voiceTarget = { type: 'terminal', id };
      window.TerminalAura?.apagar?.(id);  // ya la viste: chau aura
    }
    return;
  }
  if (e.target.closest('#orch-panel')) {
    _voiceTarget = { type: 'jarvis' };
  }
}, true);

// Hover → target, SIN clickear: pasás el mouse por una terminal y ya podés
// hablarle. Dos tiempos:
//   · _hoverVoz  = lo que hay bajo el cursor AHORA. Vale al instante (apuntar y
//     hablar tiene que ser inmediato) y gana sobre todo lo demás.
//   · el dwell (quedarse quieto encima) lo APUNTA de verdad (_voiceTarget), así
//     el destino sobrevive a que el mouse se vaya al vacío. Cruzar una card de
//     paso camino a otro lado no deja residuo.
let _hoverDwellTimer = null;
function _setHoverVoz(nuevo) {
  const igual = (a, b) => a?.type === b?.type && a?.id === b?.id;
  if (igual(nuevo, _hoverVoz)) return;
  _hoverVoz = nuevo;
  if (_hoverDwellTimer) { clearTimeout(_hoverDwellTimer); _hoverDwellTimer = null; }
  if (!nuevo) return;
  _hoverDwellTimer = setTimeout(() => {
    _hoverDwellTimer = null;
    // Re-chequeo: el cursor sigue ahí (el timer pudo sobrevivir a un cambio).
    if (!igual(_hoverVoz, nuevo)) return;
    _voiceTarget = { ...nuevo };
    if (nuevo.type === 'terminal') _enfocarPorHover(nuevo.id);
  }, window.VoiceTarget.HOVER_DWELL_MS);
}
document.addEventListener('pointerover', (e) => {
  const id = _idDeCard(e.target);
  if (id != null)                           _setHoverVoz({ type: 'terminal', id });
  else if (e.target.closest?.('#orch-panel')) _setHoverVoz({ type: 'jarvis' });
  else                                      _setHoverVoz(null);
}, { capture: true, passive: true });
// El cursor se fue de la ventana (relatedTarget null): nada bajo el mouse.
document.addEventListener('pointerout', (e) => {
  if (!e.relatedTarget) _setHoverVoz(null);
}, { capture: true, passive: true });

// ─── El TECLADO también sigue al mouse ───────────────────────────────
// Dejás un mensaje escrito en una terminal (dictado o pegado), ponés el cursor
// encima y Enter lo manda: sin clickear la card primero. Mismo modelo que la
// voz (dwell del hover), con dos guardas — decisión pura en shell/foco-hover.js.
let _ultimaTeclaTermTs = 0;   // última tecla tipeada CON el foco en una terminal
document.addEventListener('keydown', () => {
  if (document.activeElement?.closest?.('.terminal-card')) _ultimaTeclaTermTs = performance.now();
}, { capture: true, passive: true });

// Dónde está el teclado ahora mismo: 'terminal' | 'editable' | 'libre'.
// 'editable' (composer de Jarvis, editor, inputs, iframes) es intocable: lo que
// estás escribiendo ahí no puede irse a un PTY por apoyar el mouse en otro lado.
// Ojo con el orden: el helper textarea de xterm ES un <textarea>, así que la
// terminal se chequea ANTES que lo editable.
function _dondeEstaElFoco() {
  const a = document.activeElement;
  if (!a || a === document.body) return 'libre';
  if (a.closest?.('.terminal-card')) return 'terminal';
  if (a.tagName === 'IFRAME' || _esTargetEditable(a)) return 'editable';
  return 'libre';
}

let _focoRetryTimer = null;
function _enfocarPorHover(id) {
  if (_focoRetryTimer) { clearTimeout(_focoRetryTimer); _focoRetryTimer = null; }
  const foco = _dondeEstaElFoco();
  const desde = _ultimaTeclaTermTs ? performance.now() - _ultimaTeclaTermTs : Infinity;
  const destino = window.FocoHover.decidirFocoPorHover({
    hoverTermId: id,
    foco,
    focoTermId: foco === 'terminal' ? _idDeCard(document.activeElement) : null,
    desdeUltimaTeclaMs: desde,
  });
  if (destino == null) {
    // Si lo único que frenó la mudanza fue el tipeo reciente en OTRA terminal,
    // reintentar al vencer la gracia: dejás de tipear con el cursor ya puesto en
    // la otra card y el teclado se muda solo, sin tener que menear el mouse.
    if (foco === 'terminal' && desde < window.FocoHover.GRACIA_TIPEO_MS) {
      _focoRetryTimer = setTimeout(() => {
        _focoRetryTimer = null;
        if (_hoverVoz?.type === 'terminal' && _hoverVoz.id === id) _enfocarPorHover(id);
      }, window.FocoHover.GRACIA_TIPEO_MS - desde + 20);
    }
    return;
  }
  const inst = terminales.get(destino);
  if (!inst?.term) return;
  // SOLO el foco: `.activa` (el aura violeta de "card seleccionada") sigue
  // siendo del CLICK, a pedido del usuario 2026-07-23. Pasar el mouse no es
  // seleccionar — que el hover encendiera toda la card se sentía como un click
  // que nadie dio. La señal de dónde van las teclas es la nativa de la
  // terminal: xterm marca su cursor como enfocado (`.xterm.focus`).
  inst.term.focus();
}

// Nombre lindo del target para mostrar en el toast PTT
function _voiceTargetLabel(session) {
  if (!session) return 'Jarvis';
  if (session.type === 'jarvis') return 'Jarvis';
  if (session.type === 'terminal') {
    // El NOMBRE de la terminal ("Claude Code #2"), no el título vivo del pane:
    // el destino de la voz tiene que ser reconocible de un vistazo, y el título
    // vivo cambia con lo que el agente esté haciendo (`.t-name` lo pisa y deja
    // el nombre real en dataset.nombre — ver _pintarTituloVivo).
    const el = document.querySelector(`#terminal-card-${session.id} .t-name`);
    const name = (el?.dataset.nombre || el?.textContent || '').trim();
    return name || `Terminal ${session.id}`;
  }
  return '—';
}

function setEstado(estado) {
  sphereState = estado;
  _panel()?.setSphereState(_SPHERE_MAP[estado] ?? estado);
}

// ─── Chat — delega al OrchestratorPanel ──────────────────────────

function agregarMensajeChat(rol, texto, extras) {
  if (!chatSesiones[projectId]) chatSesiones[projectId] = [];
  chatSesiones[projectId].push({ rol, texto });
  _renderMensaje(rol, texto, extras);
}

function _renderMensaje(rol, texto, extras) {
  _panel()?.addMessage({
    id:        crypto.randomUUID?.() ?? String(Date.now() + Math.random()),
    role:      rol === 'jarvis' ? 'jarvis' : 'user',
    author:    rol === 'jarvis' ? 'Jarvis' : 'Tú',
    badge:     rol === 'jarvis' ? 'orchestrator' : undefined,
    timestamp: new Date(),
    content:   texto,
    ...(extras || {}),
  });
}

// ─── WebSocket de eventos ─────────────────────────────────────────────────────

let _wsEstuvoCaido = false;  // hubo una desconexión → resync al reconectar
let _wsIntentos    = 0;      // # de reintentos (backoff exponencial del WS)
let _bootIdServer = null;    // boot_id del server al cargar la página (FE Watch)
// Última fase conocida de cada terminal según agent_watch (WS agente_*): el ✕
// de la card la consulta para pedir confirmación SOLO si el agente está
// trabajando (auditoría 2026-07-02: un misclick sobre una terminal ocupada
// mataba horas de trabajo sin preguntar; en idle sigue sin fricción). Best
// effort: si la página cargó después del último evento, no hay fase → sin confirm.
const _faseTerminales = {};

let _wsAvisoTope = false;   // ya avisamos del tope global (1013) en esta ráfaga
function conectarEventosWs() {
  if (eventsWs) {
    try { eventsWs.close(); } catch (_) {}
    eventsWs = null;
  }

  const url = `ws://${location.host}/ws/events/${projectId}`;
  eventsWs  = new WebSocket(url);

  eventsWs.onopen = () => {
    _wsIntentos = 0;
    window.WsStatus?.set?.('conectado');
    // Tras una reconexión (server reiniciado / WS caído) los broadcasts del
    // medio se perdieron: resincronizar el estado del mobile preview.
    if (_wsEstuvoCaido) {
      _wsEstuvoCaido = false;
      consultarMobilePreview();
    }
  };

  eventsWs.onmessage = e => {
    let data;
    try { data = JSON.parse(e.data); } catch { return; }

    // FE Watch — UN solo camino de reload autorizado (pedido del usuario 2026-07-03):
    // - 'hola' trae el boot_id del server; si cambió desde que cargó la página,
    //   el server se reinició (camino autorizado: el botón Actualizar) → recargar.
    // - Las ediciones de frontend SIN commit NO recargan la vista (antes lo hacían
    //   vía 'frontend_actualizado' y parpadeaban el workspace mientras el enjambre
    //   iteraba). El único disparador de reload por CÓDIGO es el banner "Actualizar
    //   ahora", que se basa en COMMITS. Ver 'frontend_actualizado' más abajo.
    if (data.type === 'hola') {
      if (_bootIdServer && data.boot_id !== _bootIdServer) {
        // El server se reinició. NO recargar de una: esperar a que reconcilie las
        // sesiones tmux (/api/system/ready) ANTES de recargar. Si no, la página
        // fresca re-attachea las N terminales contra un tmux todavía arrancando →
        // seeds vacíos/tardíos → cards negras + scroll muerto (bug del update).
        // recargarCuandoListo pone recargando=true (que ninguna terminal auto-
        // reintente en paralelo con el reload — doble re-attach, auditoría
        // 2026-07-02). Sin updater (edge): reload directo con el guard, como antes.
        if (window.JarvisUpdater?.recargarCuandoListo) {
          window.JarvisUpdater.recargarCuandoListo();
        } else {
          if (window.JarvisUpdater) window.JarvisUpdater.recargando = true;
          location.reload();
        }
        return;
      }
      _bootIdServer = data.boot_id;
      return;
    }
    // 'frontend_actualizado' = un archivo de frontend cambió en disco SIN commit
    // (un agente iterando/probando su UI). Deliberadamente NO hacemos NADA: recargar
    // acá parpadeaba el workspace (reload → re-attach de TODAS las terminales, ~5s)
    // cada vez que el enjambre editaba y quedaba quieto un instante. Pedido del usuario
    // (2026-07-03): los cambios sin commit no tocan la vista; el agente prueba en su
    // propio browser QA (&qa=1), aparte. El reload por código se autoriza SOLO por el
    // banner "Actualizar ahora" (COMMITS). El backend ya no emite esta señal; el guard
    // queda como defensa por si un server viejo la manda.
    if (data.type === 'frontend_actualizado') { return; }
    // 'codigo_commiteado' = fe_watch detectó un commit nuevo. Un commit NO es
    // "tarea terminada": los agentes commitean MUCHAS veces mientras trabajan (uno
    // por archivo/subtarea), así que sonar la campanita acá la disparaba una y otra
    // vez a mitad de tarea. Pedido del usuario (2026-07-04): que suene SOLO cuando el
    // agente terminó DE VERDAD todo. Por eso acá NO suena — la campanita "terminé"
    // queda a cargo de `agente_termino` (quietud real del pane, agent_watch) y del
    // TASK_DONE del protocolo. Este evento solo re-chequea el banner "Actualizar
    // ahora" (que SÍ se basa en commits).
    if (data.type === 'codigo_commiteado') {
      window.JarvisUpdater?.chequear?.();
      return;
    }
    // Actividad de agentes → panel del preview móvil (solo visual, no altera
    // el comportamiento existente de estas ramas).
    if (data.type === 'task_event' || data.type === 'workflow_update' || data.type === 'workflow_done') {
      window.MobilePreview?.onActividad?.(data);
    }

    if (data.type === 'task_event') {
      // Silenciado en el chat — el progreso se refleja solo en el execution plan card.
      // Sonido de notificación: el agente avisa que terminó (o que necesita atención).
      sonarEventoTarea(data.event);
      // Pip de estado de la card: keyword real del agente
      if (data.terminal_id != null) {
        if (data.event === 'TASK_DONE') {
          setTerminalStatus(data.terminal_id, 'watching');
          window.JarvisDock?.notify?.('review', 1);
        } else if (data.event === 'TASK_BLOCKED' || data.event === 'TASK_ERROR') {
          setTerminalStatus(data.terminal_id, 'error');
        }
        // Aura de notificación (si la card no es la activa)
        window.TerminalAura?.notificar?.(data.event, data.terminal_id);
      }
    } else if (data.type === 'projects_update') {
      // La lista de proyectos cambió en el server (creado por CLI/API/otro
      // cliente/el orquestador): refrescar el sidebar en vivo, sin F5.
      cargarSidebar();
    } else if (data.type === 'tasks_update') {
      // El task board cambió en el server (crear/asignar/TASK_DONE)
      window.JarvisTasks?.onTasksUpdate?.();
      window.JarvisDock?.notify?.('tasks', 1);
    } else if (data.type === 'workflow_update') {
      // Actualiza la tarjeta — sin TTS
      _actualizarWorkflowCard(data.workflow_id, data.pasos, data.paso_actual, data.estado);
      // Proyección en vivo en el task board
      window.JarvisTasks?.onWorkflowUpdate?.(data);
      window.JarvisDock?.notify?.('tasks', 1);
      // Pips de las terminales según el estado de cada paso del workflow
      for (const paso of (data.pasos || [])) {
        if (paso.terminal_id == null) continue;
        const map = { running: 'thinking', done: 'watching', blocked: 'error', error: 'error' };
        setTerminalStatus(paso.terminal_id, map[paso.estado] || 'idle');
        // El paso arrancó → el agente trabaja: apagar el aura de esa card
        if (map[paso.estado] === 'thinking') window.TerminalAura?.apagar?.(paso.terminal_id);
      }
    } else if (data.type === 'orquestador_mensaje') {
      // Mensajes intermedios del orquestador — solo chat, sin TTS
      agregarMensajeChat('jarvis', data.message);
      window.JarvisDock?.notify?.('jarvis', 1);
    } else if (data.type === 'workflow_done') {
      // Finalización del workflow — chat + TTS
      // Si hay preview_url, avisar en el chat + abrir pestaña (los localhost
      // vivos quedan listados en el menú #jw-localhosts-btn de la barra).
      if (data.preview_url) {
        agregarMensajeChat('jarvis', `${data.message}\n\n[abrir preview](${data.preview_url})`);
        try { window.open(data.preview_url, '_blank', 'noopener'); } catch {}
      } else {
        agregarMensajeChat('jarvis', data.message);
      }
      window.JarvisDock?.notify?.('jarvis', 1);
      reproducirVoz(data.message).catch(() => {});
      // El workflow terminó por completo: si había update esperando agentes,
      // el banner "Actualizar ahora" puede salir ya (sin esperar el poll).
      window.JarvisUpdater?.chequear?.();
    } else if (data.type === 'dev_server_detectado') {
      // "Menú-primero" (pedido del usuario 2026-07-12): un dev server / demo
      // detectado NO abre NUNCA una pestaña sola. Antes cada detección (este
      // "callback") amontonaba pestañas de puertos sin sentido — el http.server
      // de unos backups, cada mockup que un agente imprimía, el MISMO server por
      // 127.0.0.1 y por localhost — y con 8 iframes compitiendo algunos daban
      // error. Ahora el localhost vive en el menú #jw-localhosts-btn (con
      // contador) y lo abre el usuario desde ahí, o maximizando/seleccionando la
      // card del agente (_saltarLocalhostDeTerminal). Acá solo:
      //   1) si el usuario YA tenía abierta la pestaña de ese server, la
      //      recargamos (un reinicio por un cambio de diseño muestra lo fresco);
      //   2) si no, un badge discreto en la pestaña Preview del dock (sin
      //      abrirlo) — señal de que hay algo nuevo en el menú;
      //   3) refrescar el menú de localhost.
      window.WebPreview?.init?.(document.getElementById('jw-pane-preview'));
      const refrescada = window.WebPreview?.refrescarSiExiste?.(data.url);
      if (!refrescada) window.JarvisDock?.notify?.('preview', 1);
      window.JarvisDevServers?.refrescar?.();
    } else if (data.type === 'dev_server_caido') {
      // El server murió (o lo reinició para un cambio): resincronizar el menú.
      // La pestaña, si el usuario la tenía abierta, queda como esté (la recarga
      // la trae de vuelta al reconectarse).
      window.JarvisDevServers?.refrescar?.();
    } else if (data.type === 'agente_termino') {
      // agent_watch (heurística server-side, ~4s): el agente terminó DE VERDAD su
      // trabajo — venía cambiando el pane y quedó quieto, sin prompt de pregunta
      // (mientras trabaja el spinner del CLI redibuja cada ~1s, así que esto NO
      // salta entre commits ni en pausas de pensamiento). Esta es LA señal de
      // "terminó todo", no el commit. Acorde ascendente.
      _faseTerminales[data.terminal_id] = 'quieto';
      document.getElementById(`terminal-card-${data.terminal_id}`)?.classList.remove('t-trabajando');
      _sonarFinDedup();
      window.TerminalAura?.notificar?.('agente_termino', data.terminal_id);
      window.JarvisNotify?.avisar?.({ tipo: 'termino', nombre: _nombreTerm(data.terminal_id), sonidoOn: sonidoTareas });
      // ¿Era el último agente ocupado? El banner del updater puede aparecer.
      window.JarvisUpdater?.chequear?.();
      _pingTitulos();   // el título cambió → refrescar YA, sin esperar el poll
    } else if (data.type === 'agente_espera') {
      // El agente quedó esperando una respuesta del usuario (prompt y/n,
      // permiso) — mismas notas graves de atención que TASK_BLOCKED.
      _faseTerminales[data.terminal_id] = 'quieto';
      document.getElementById(`terminal-card-${data.terminal_id}`)?.classList.remove('t-trabajando');
      sonarEventoTarea('TASK_BLOCKED');
      window.TerminalAura?.notificar?.('agente_espera', data.terminal_id);
      window.JarvisNotify?.avisar?.({ tipo: 'espera', nombre: _nombreTerm(data.terminal_id), sonidoOn: sonidoTareas });
      _pingTitulos();
    } else if (data.type === 'agente_trabajando') {
      // El agente retomó actividad: el aura vieja ya no informa nada.
      _faseTerminales[data.terminal_id] = 'trabajando';
      // Marca la card como "trabajando" → el brillo líquido del chrome se anima
      // SOLO mientras el agente trabaja (pedido del usuario 2026-07-06).
      document.getElementById(`terminal-card-${data.terminal_id}`)?.classList.add('t-trabajando');
      window.TerminalAura?.apagar?.(data.terminal_id);
      // Y si el banner de update estaba visible, se esconde al toque.
      window.JarvisUpdater?.chequear?.();
      _pingTitulos();
    } else if (data.type === 'proyecto_trabajo') {
      // Anillo VERDE del orbe en la franja: ON mientras el workspace tenga
      // agentes trabajando, OFF al terminar. Señal GLOBAL (agent_watch) → aplica
      // a CUALQUIER proyecto, no solo al activo.
      _sbAplicarTrabajo(data.project_id, data.trabajando);
    } else if (data.type === 'mailbox_aviso') {
      // Otro agente le dejó un mensaje a ESTA terminal (mailbox 1-a-1) → aura
      // sutil en su card, sin sonido (es un "tenés mensaje", no una alarma).
      window.TerminalAura?.notificar?.('mailbox_aviso', data.terminal_id);
    } else if (data.type === 'swarm_grupos') {
      // Dos o tres agentes convergieron sobre la misma superficie (o dejaron de
      // hacerlo): el ícono de vínculo de sus cards reacciona al instante.
      window.SwarmLink?.aplicar?.(data.grupos);
    } else if (data.type === 'colision_funcional') {
      // Uno borró algo que otras cards usan → aviso a la VÍCTIMA por la animación
      // del Swarm (pulso en su card + mensaje desde su lado), nunca por su pane.
      window.SwarmLink?.alertarColision?.(data);
    } else if (data.type === 'live_update' || data.type === 'permiso_pedido'
               || data.type === 'permiso_resuelto' || data.type === 'conflicto_archivo') {
      // Agents Live → pestaña Live del modal de Memoria (si está abierto)
      window.JarvisMemory?.onLiveEvent?.(data);
      if (data.type === 'conflicto_archivo') {
        // dos agentes sobre el mismo archivo: mismas notas graves de atención
        sonarEventoTarea('TASK_BLOCKED');
        toast(_sbT('⚠ {a} y {b} sobre {archivo}').replace('{a}', data.intruso_nombre).replace('{b}', data.dueno_nombre).replace('{archivo}', data.archivo), 'info');
      }
    } else if (data.type === 'sistema_huerfanos') {
      // Janitor (post-mortem segfault tmux 2026-07-02): procesos huérfanos
      // pesados de los proyectos quemando CPU sin terminal. No se matan solos
      // (pueden ser legítimos): se avisa para que el usuario decida.
      const n = (data.procesos || []).length;
      toast(_sbT('⚠ {n} proceso(s) huérfano(s) comiendo CPU en tus proyectos (sin terminal). Corré ~/limpiar.sh o revisalos con ps.').replace('{n}', n),
            'warning', 9000);
    } else if (data.type === 'cuenta_agregada') {
      // Configuración → Cuentas: el watcher detectó la cuenta nueva tras el login.
      toast(_sbT('✓ Cuenta agregada: {label}').replace('{label}', data.label || data.email || data.tipo), 'success');
      window.JarvisSettings?.onCuentaAgregada?.(data);
      window.JarvisSettings?.refrescar?.('cuentas');
    } else if (data.type === 'cuenta_watch_timeout') {
      // Venció la ventana de login sin detectar cuenta nueva (el usuario abandonó).
      window.JarvisSettings?.refrescar?.('cuentas');
    } else if (data.type === 'cuentas_update') {
      // Cambió el estado de cuentas en otro cliente/acción: refrescar si está abierto.
      window.JarvisSettings?.refrescar?.('cuentas');
    }
  };

  eventsWs.onerror  = () => { window.WsStatus?.set?.('caido'); };
  eventsWs.onclose  = (event) => {
    _wsEstuvoCaido = true;
    window.WsStatus?.set?.('reconectando');
    // ¿El server murió de verdad? (p.ej. un agente lo reinició) → el updater
    // prueba /api/health y, si no responde, muestra el aviso "Un agente está
    // actualizando algo" y recarga sola cuando el server vuelve.
    window.JarvisUpdater?.avisarPosibleCaida?.();
    // 1013 = el server llegó al tope global de WS (demasiadas conexiones): no es
    // una caída, es saturación → reintentar al toque no libera presión. Esperamos
    // largo (30s) y avisamos al usuario que cierre pestañas, una sola vez por ráfaga.
    if (event && event.code === 1013) {
      if (!_wsAvisoTope) {
        _wsAvisoTope = true;
        toast(_sbT('Demasiadas conexiones abiertas: cerrá alguna pestaña.'), 'info');
      }
      setTimeout(() => { if (eventsWs && eventsWs.readyState !== WebSocket.OPEN) conectarEventosWs(); }, 30000);
      return;
    }
    _wsAvisoTope = false;
    // Backoff exponencial (3→6→12→24s, cap 30) en vez de 3s fijo.
    const delay = window.WsStatus?._pure?.proximoDelay?.(_wsIntentos++) ?? 3000;
    setTimeout(() => { if (eventsWs && eventsWs.readyState !== WebSocket.OPEN) conectarEventosWs(); }, delay);
  };
}

// Al VOLVER a visible: si el WS de eventos quedó CERRADO durante el idle, se
// reconecta AL TOQUE. Sin esto, el reintento vive en un setTimeout de backoff
// que Chrome throttlea a 1/min en pestañas ocultas — al volver podías esperar
// hasta 30s sin eventos globales (auras, live, títulos) con la app "muda".
// CONNECTING/OPEN/CLOSING no se tocan: churn cero en el caso sano.
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return;
    if (eventsWs && eventsWs.readyState === WebSocket.CLOSED) {
      _wsIntentos = 0;
      conectarEventosWs();
    }
  });
}

// ─── Mobile Preview: mostrar la pestaña Móvil del dock solo en proyectos Expo ─

async function consultarMobilePreview(autoAbrir = false) {
  const pid = projectId;   // proyecto al que pertenece ESTA consulta (projectId es global mutable)
  try {
    const r = await fetch(`/api/mobile-preview/${pid}/estado`);
    const d = await r.json();
    // Si cambiaste de proyecto mientras el fetch estaba en vuelo, esta respuesta
    // es de un proyecto VIEJO: descartarla (el proyecto nuevo dispara su propia
    // consulta). Sin este guard, el es_expo de A pisaría el estado de B y —con el
    // restore pendiente del dock— podría flashear la pestaña Móvil en un proyecto
    // no-Expo (y abrir() correría apuntando al proyecto equivocado).
    if (String(projectId) !== String(pid)) return;
    if (!d.es_expo) {
      // Proyecto no-Expo: ocultar la pestaña y apagar el polling/iframe si estaba
      // activo (Metro queda vivo). La pestaña Móvil solo existe en Expo (§2.4).
      window.JarvisDock?.setTabVisible?.('mobile', false);
      window.MobilePreview?.cerrar?.();
      return;
    }
    // init ANTES de setTabVisible: hacer visible la pestaña Móvil puede re-activar
    // un restore pendiente (volviste con el dock en Móvil) → dispara onTabShown
    // ('mobile') → MobilePreview.abrir(), que necesita el panel ya montado y
    // apuntando a ESTE proyecto. Sin esto, abrir() correría pre-init (proyecto
    // viejo / panel sin montar).
    window.MobilePreview?.init(pid);
    window.JarvisDock?.setTabVisible?.('mobile', true);

    // El panel NO arranca Metro: solo detecta el Expo que ya corre en una
    // terminal (sincronizar → detección) y lo muestra. Si no hay, espera.
    window.MobilePreview?.sincronizar?.(d);

    // Al entrar al proyecto, mostrar la pestaña Móvil si el auto-preview está ON.
    // Anti-molestia v2 (pedido 2026-07-07): el dock ya NO se DESPLIEGA solo —
    // el auto-open persistía open=1 y el panel quedaba "desplegado para
    // siempre" en proyectos Expo que el usuario nunca usó (parecía contagio
    // entre proyectos; QA lo confirmó). Ahora la primera vez avisa con un
    // BADGE en la pestaña Móvil y el usuario decide si abrir. El estado
    // abierto/cerrado del dock queda 100% en manos del usuario, por proyecto.
    const autoMostrar = localStorage.getItem('jarvis.autoMobilePreview') !== '0';
    const enMobile = window.JarvisDock?.isOpen?.() && window.JarvisDock?.activeTab?.() === 'mobile';
    const vistoKey = `jarvis.mobilePreview.visto.${pid}`;
    const yaVisto = localStorage.getItem(vistoKey) === '1';
    const dec = window.MobilePreviewPure?.debeAutoAbrir?.(autoAbrir, autoMostrar, enMobile, yaVisto)
      || { abrir: autoAbrir && autoMostrar && !enMobile && !yaVisto, visto: yaVisto };
    if (dec.visto && !yaVisto) { try { localStorage.setItem(vistoKey, '1'); } catch (_) {} }
    if (dec.abrir) window.JarvisDock?.notify?.('mobile');
  } catch {
    // Mismo guard de proyecto en el camino de error: no pisar el dock del nuevo.
    if (String(projectId) !== String(pid)) return;
    window.JarvisDock?.setTabVisible?.('mobile', false);
    window.MobilePreview?.cerrar?.();
  }
}

// ─── Sonido de notificación al terminar/atascarse una tarea ───────────────────
// Chime generado con WebAudio (sin assets ni red). TASK_DONE = acorde ascendente
// alegre; TASK_BLOCKED/ERROR = tono grave de atención. Toggle persistido + mute.

let sonidoTareas = localStorage.getItem('jarvis.sonidoTareas') !== 'off';  // default ON
let _audioCtx = null;

function _ctx() {
  if (!_audioCtx) {
    try { _audioCtx = new (window.AudioContext || window.webkitAudioContext)(); }
    catch { return null; }
  }
  if (_audioCtx.state === 'suspended') _audioCtx.resume().catch(() => {});
  return _audioCtx;
}

// Reproduce una secuencia de notas. notas = [{freq, start, dur}], gain pico suave.
function _tocarNotas(notas, tipo = 'sine', vol = 0.16) {
  const ctx = _ctx();
  if (!ctx) return;
  const t0 = ctx.currentTime;
  for (const n of notas) {
    const osc = ctx.createOscillator();
    const g   = ctx.createGain();
    osc.type = tipo;
    osc.frequency.value = n.freq;
    const start = t0 + n.start;
    const end   = start + n.dur;
    // envolvente ataque/decay suave para que no "clickee"
    g.gain.setValueAtTime(0.0001, start);
    g.gain.exponentialRampToValueAtTime(vol, start + 0.015);
    g.gain.exponentialRampToValueAtTime(0.0001, end);
    osc.connect(g); g.connect(ctx.destination);
    osc.start(start); osc.stop(end + 0.02);
  }
}

function sonarEventoTarea(event) {
  if (!sonidoTareas) return;
  if (event === 'TASK_DONE') {
    // acorde ascendente (C5 → E5 → G5): "terminé bien"
    _tocarNotas([
      { freq: 523.25, start: 0.00, dur: 0.12 },
      { freq: 659.25, start: 0.10, dur: 0.12 },
      { freq: 783.99, start: 0.20, dur: 0.18 },
    ], 'sine', 0.16);
  } else if (event === 'TASK_BLOCKED' || event === 'TASK_ERROR') {
    // dos notas graves descendentes: "necesito atención"
    _tocarNotas([
      { freq: 392.00, start: 0.00, dur: 0.16 },
      { freq: 311.13, start: 0.14, dur: 0.22 },
    ], 'triangle', 0.18);
  }
}

// El sonido "terminé" sale cuando el agente terminó DE VERDAD todo su trabajo: la
// QUIETUD real del pane (agent_watch → agente_termino, ~4s tras el último output, el
// agente asentado en su prompt idle) o el TASK_DONE del protocolo. NO suena en cada
// commit: los agentes commitean muchas veces a mitad de tarea y eso disparaba falsos
// "terminé" (pedido del usuario 2026-07-04). El de-dup deja una sola campanita por
// ventana, por si la quietud y un TASK_DONE cercano coincidieran.
let _ultimoSonidoFin = 0;
const _VENTANA_SONIDO_FIN = 8000;
function _sonarFinDedup() {
  const ahora = Date.now();
  if (ahora - _ultimoSonidoFin < _VENTANA_SONIDO_FIN) return false;
  _ultimoSonidoFin = ahora;
  sonarEventoTarea('TASK_DONE');
  return true;
}

function _sincronizarBotonSonido() {
  const btn = document.getElementById('terminals-sound-btn');
  if (!btn) return;
  btn.classList.toggle('muteado', !sonidoTareas);
  btn.setAttribute('aria-pressed', sonidoTareas ? 'true' : 'false');
  btn.title = sonidoTareas ? 'Sonido al terminar tareas: activado' : 'Sonido al terminar tareas: silenciado';
}

document.getElementById('terminals-sound-btn')?.addEventListener('click', () => {
  sonidoTareas = !sonidoTareas;
  localStorage.setItem('jarvis.sonidoTareas', sonidoTareas ? 'on' : 'off');
  _sincronizarBotonSonido();
  if (sonidoTareas) sonarEventoTarea('TASK_DONE');  // feedback inmediato al activar
});
_sincronizarBotonSonido();

// Fuente de verdad del toggle de sonido, para que Configuración → Notificaciones
// lo controle (el botón de la barra se movió allí, pedido UI/UX 2026-06-28).
window.JarvisSonido = {
  get: () => sonidoTareas,
  set: (v) => {
    sonidoTareas = !!v;
    localStorage.setItem('jarvis.sonidoTareas', sonidoTareas ? 'on' : 'off');
    _sincronizarBotonSonido();
    if (sonidoTareas) sonarEventoTarea('TASK_DONE');  // feedback al activar
  },
};

// ─── Workflow card en el chat ─────────────────────────────────────────────────

function _renderWorkflowCard(wf) {
  const p = _panel();
  if (!p || !wf) return;

  // Si ya existe una card para este workflow (ej. reconexión), actualizarla
  const existing = p.findWorkflowCard(wf.id);
  if (existing) {
    _llenarWorkflowCard(existing, wf);
    return;
  }

  const card = document.createElement('div');
  card.className = 'execution-plan';
  card.dataset.workflowId = wf.id;
  _llenarWorkflowCard(card, wf);

  // addWorkflowCard registra la card en panel._wfCards y la adjunta a $messages.
  // La card sobrevive a setMessages() y cambios de proyecto.
  p.addWorkflowCard(wf.id, card);
}

// Renderiza la workflow card usando el builder premium del OrchestratorPanel
// (.orch-action-card: progress 2px violeta→cian, checks stroke-draw, spinner).
// Antes acá vivía la card legacy .ep-* con emojis y hex inline — eliminada.
function _llenarWorkflowCard(card, wf) {
  const p = _panel();
  if (!p) return;
  const mapEstado = { done: 'done', running: 'running', blocked: 'blocked', error: 'error' };
  const plan = {
    title: wf.nombre || card.dataset.nombre || 'Workflow',
    done:  wf.estado === 'done',
    steps: (wf.pasos || []).map((paso, i) => ({
      label:  paso.agente || `Paso ${i + 1}`,
      status: mapEstado[paso.estado] || 'idle',
      rol:    paso.rol || null,
      target: (paso.tarea || '').replace(/Cuando termines.*$/i, '').trim().slice(0, 60),
    })),
  };
  card.dataset.nombre = plan.title;
  card.innerHTML = p._buildActionCard(plan);
}

function _actualizarWorkflowCard(workflowId, pasos, pasoActual, estado) {
  const p = _panel();
  // Buscar primero en el registro del panel (sobrevive a setMessages())
  const card = p?.findWorkflowCard(workflowId);
  if (!card) return;

  const nombre = card.dataset.nombre || '';
  _llenarWorkflowCard(card, { id: workflowId, nombre, pasos, paso_actual: pasoActual, estado });

  if (estado === 'done') {
    setTimeout(() => {
      card.style.transition = 'opacity 0.8s ease, transform 0.8s ease';
      card.style.opacity    = '0';
      card.style.transform  = 'translateY(-4px)';
      setTimeout(() => {
        p.removeWorkflowCard(workflowId);
      }, 850);
    }, 3000);
  }

  if (p?.$messages && !p._userScrolled) p.$messages.scrollTop = p.$messages.scrollHeight;
}

// Limpia el chat del panel y restaura el historial del proyecto activo.
function _restaurarChat() {
  const p = _panel();
  if (!p) return;
  const sesion = chatSesiones[projectId];
  if (sesion?.length > 0) {
    p.setMessages(sesion.map((m, i) => ({
      id:        `restore-${projectId}-${i}`,
      role:      m.rol === 'jarvis' ? 'jarvis' : 'user',
      author:    m.rol === 'jarvis' ? 'Jarvis' : 'Tú',
      badge:     m.rol === 'jarvis' ? 'orchestrator' : undefined,
      timestamp: new Date(),
      content:   m.texto,
    })));
  } else {
    p.setMessages([{
      id: `welcome-${projectId}`,
      role: 'jarvis', author: 'Jarvis', badge: 'orchestrator',
      timestamp: new Date(),
      content: '¿Qué hacemos, señor?',
      quickReplies: ['Ver estado', 'Lanzar Claude Code', 'Nueva terminal'],
    }]);
  }
}

// ─── TTS ──────────────────────────────────────────────────────────

async function _reproducirBase64(audio_b64) {
  if (!audio_b64 || ttsActivo) return;
  ttsActivo = true;
  return new Promise(resolve => {
    const audio = new Audio('data:audio/mp3;base64,' + audio_b64);
    setEstado('responding');
    audio.onended = () => { ttsActivo = false; setEstado('idle'); resolve(); };
    audio.onerror = () => { ttsActivo = false; resolve(); };
    audio.play().catch(() => { ttsActivo = false; resolve(); });
  });
}

// Detecta el idioma de un texto del USUARIO (es|en) para que la VOZ de Jarvis
// dependa de en qué idioma ESCRIBE, NO del toggle de la UI: si le escribís en
// español (aunque la UI esté en inglés), te responde con voz en español.
// Heurística liviana con sesgo a español (usuario rioplatense).
function _detectarIdiomaTexto(texto) {
  const raw = (texto || '').trim();
  if (!raw) return null;
  const t = ' ' + raw.toLowerCase() + ' ';
  if (/[áéíóúñ¿¡]/.test(t)) return 'es';                 // señal definitiva de español
  const en = (t.match(/ (the|is|are|do|does|did|make|build|create|please|want|need|show|you|your|this|that|these|with|for|and|what|how|hey|hello|hi|let|can|will|would|should|add|fix|change|remove|delete|open|close|run|new|button|page|website|landing) /g) || []).length;
  const es = (t.match(/ (que|el|la|los|las|un|una|unos|unas|por|para|con|sin|pero|como|donde|hacer|hace|hacemos|hacé|quiero|necesito|dale|senor|mas|esta|estan|estoy|mostrame|arma|armar|armá|crear|crea|creá|pagina|página|sitio|yo|vos|tu|mi|se|le|del|al|una) /g) || []).length;
  if (en > 0 && en >= es) return 'en';
  return 'es';                                           // default rioplatense
}

async function reproducirVoz(texto) {
  try {
    // La voz DEPENDE del idioma en que escribió el usuario (no del toggle de UI):
    // lo detectamos al enviar (window._orchLangConversacion). En 'en' el backend
    // traduce el texto es→en y usa voz inglesa (el orquestador responde en español).
    const lang = window._orchLangConversacion || window.JarvisI18n?.lang?.() || 'es';
    const res = await fetch('/api/voice/speak', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ text: texto, lang }),
    });
    if (!res.ok) return;
    const data = await res.json();
    await _reproducirBase64(data.audio_base64);
  } catch { /* TTS no rompe el flujo */ }
}

async function reproducirBienvenida() {
  try {
    // El saludo HABLADO sigue el idioma de la UI (voz + texto en/es).
    const lang = window.JarvisI18n?.lang?.() || 'es';
    const res = await fetch('/api/voice/welcome?lang=' + lang);
    if (!res.ok) return;
    const data = await res.json();
    await _reproducirBase64(data.audio_base64);
  } catch { /* silencioso */ }
}

// ─── Inicialización ───────────────────────────────────────────────

// Bootstrap: sin proyectos todavía. No cargamos workspace; abrimos el launcher
// en modo "crear proyecto". Al lanzar, _tlResolverDestino crea el proyecto y
// cambiarProyecto navega a /workspace?id=<nuevo>.
if (_bootstrapNuevo && !projectId) {
  window.addEventListener('DOMContentLoaded', () => {
    // Bienvenida de fondo (antes quedaba el empty de terminales + "cargando"):
    // si el usuario cierra el launcher sin crear nada, ve un estado con sentido
    // y un CTA que lo reabre.
    document.getElementById('terminals-empty')?.classList.add('oculto');
    document.getElementById('jw-welcome')?.classList.remove('oculto');
    document.getElementById('jw-welcome-cta')?.addEventListener('click', () => abrirLauncher());
    const _saludo = () => { if (elTitulo) elTitulo.textContent = window.JarvisI18n?.t?.('Bienvenido') ?? 'Bienvenido'; };
    _saludo();
    window.addEventListener('jarvis:lang', _saludo);  // #project-title es zona i18n-skip
    setTimeout(() => abrirLauncher(), 150);
  });
} else {
  inicializar();
}

async function inicializar() {
  try {
    // Inicializar el motor de layout de terminales (window.TerminalLayout) ANTES
    // de cargarProyecto(): esa carga crea las cards y llama TerminalLayout.add(id),
    // que necesita _grid seteado para aplicar grid-area. Si init() corriera después,
    // _grid sería null en add() (cards sin tamaño = cajitas diminutas) y además
    // init() hace _layout={} borrando lo que add() acababa de registrar.
    window.TerminalLayout?.setRefitCallback(window.refitTerminal);
    window.TerminalLayout?.setContainer(elGrid);
    window.TerminalLayout?.init(projectId, elGrid);
    window.TerminalLayout?.enableInteractions();
    document.getElementById('terminals-reset-btn')?.addEventListener('click', () => {
      // Reset REAL (auditoría 2026-07-02): antes solo re-acomodaba el layout —
      // el usuario lo apretaba para "arreglar" una terminal rota y no hacía nada
      // de conexión. Ahora además SANA cada terminal viva: term.reset() + refresh
      // → el backend re-captura el pane y re-siembra pantalla+scrollback (el "F5
      // sin F5"). El delay deja asentar los resizes del re-tile antes del re-seed.
      window.TerminalLayout?.reset();
      setTimeout(() => window.sanearTerminales?.(), 200);
    });

    // El preview necesita conocer el proyecto ANTES de que el dock restaure
    // su pestaña activa: si es preview, su init() restaura las pestañas
    // persistidas de este proyecto desde localStorage.
    window.WebPreview?.onProjectChanged?.(projectId);

    // Panel Único (dock): cablea su DOM, restaura open/tab del proyecto.
    window.JarvisDock?.init({
      onTabShown: (tab) => _onDockTabShown(tab),
      onResized: () => {
        if (typeof terminales !== 'undefined') terminales.forEach((_, id) => refitTerminal?.(id));
        if (window.JarvisDock?.activeTab?.() === 'editor') window.JarvisEditor?.relayout?.();
      },
    });
    window.JarvisDock?.setProject(projectId);

    await Promise.all([cargarProyecto(), cargarSidebar()]);

    window.JarvisTasks?.init(projectId);
    window.JarvisMemory?.init(projectId);
    window.JarvisReview?.init(projectId);
    window.JarvisSettings?.init(projectId);
    document.getElementById('jw-gear')?.addEventListener('click', () => window.JarvisSettings?.open());
    window.JarvisGroqSetup?.init?.();
    // Adopta logins nativos (Grok, etc.) aunque nunca se tocó ⚙ → Cuentas.
    fetch('/api/cuentas', { credentials: 'same-origin' }).catch(() => {});

    // ?launcher=nuevo (desde la home): abrir el launcher en modo workspace
    if (new URLSearchParams(location.search).get('launcher') === 'nuevo') {
      history.replaceState({ projectId }, '', `/workspace?id=${projectId}`);
      setTimeout(() => abrirLauncher(), 150);
    }

    // Conectar canal de eventos en tiempo real
    conectarEventosWs();

    // Registrar bridges para el OrchestratorPanel
    window._orchOnSend       = (texto, imagenBase64, mediaType) => enviarMensaje(texto, imagenBase64, mediaType);
    window._orchOnMicHold    = iniciarGrabacion;
    window._orchOnMicRelease = () => {
      _micSoltado = true;
      const recVivo = mediaRecorder && mediaRecorder.state !== 'inactive';
      const gen = _micGen;
      _dictadoT0 = performance.now();   // arranca el reloj release→texto (dictado-log)
      // Sin recorder vivo (mic caído sin revivir / hold abortado a medio init):
      // no hay cola posible. Si quedó audio capturado DE ESTA generación
      // (segmentos pre-corte incluidos), se procesa igual — mejor un dictado
      // parcial que perderlo; sin nada, no-op.
      if (!recVivo) {
        const armo = _lanzarTranscripcion(gen);
        if (armo || (_chunksGen === gen && audioChunks.length)) _finalizarDictado(gen);
        return;
      }
      // Feedback INMEDIATO al soltar (pedido 2026-07-17): para el usuario el
      // dictado terminó ACÁ — la esfera de Jarvis pasa a "cargando" y la
      // píldora a spinner YA, sin esperar los ~0.5-2s de la cola. La cola
      // sigue capturando por abajo igual que siempre (el recorder no se frena
      // en esta línea, y su cierre por silencio lee _micVozTs del tick del
      // waveform, que corre aparte del modo visual de la píldora).
      _pttProcesando();
      if (_activeVoiceSession?.type === 'jarvis') setEstado('processing');
      // Cola de dictado: NO frenamos al instante — el recorder sigue capturando
      // _TAIL_MS más (la píldora sigue en "rec": todavía te escucha) para no
      // comerse la última palabra dicha al soltar. Al vencer se frena y se
      // procesa (_finalizarDictado → spinner).
      const parar = () => {
        if (_tailT) { clearTimeout(_tailT); _tailT = null; }
        if (_tailIntervalo) { clearInterval(_tailIntervalo); _tailIntervalo = null; }
        if (_tailCerrar === parar) _tailCerrar = null;
        if (gen !== _micGen) return;   // lo pisó un hold nuevo: ese ciclo manda
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
          // El cierre lo hace el ONSTOP del recorder, NO acá: recién en el
          // onstop existen los chunks y arranca el fetch a /transcribe.
          // Finalizar acá mismo procesaba ANTES de que hubiera audio → todo
          // dictado salía "vacio" en ~500ms (bug 2026-07-17 post-SR; antes lo
          // tapaba la ventana de gracia de 650ms del SR). El timeout es solo
          // red de seguridad por si el onstop se pierde (_finalizarDictado es
          // idempotente por gen y un hold nuevo lo invalida por _micGen).
          try { mediaRecorder.stop(); } catch { _finalizarDictado(gen); return; }
          setTimeout(() => { if (gen === _micGen) _finalizarDictado(gen); }, 1500);
        } else {
          _finalizarDictado(gen);
        }
      };
      _tailCerrar = parar;
      _tailT = setTimeout(parar, _TAIL_MS);
      // Cierre TEMPRANO por silencio: si ya no hay voz hace _TAIL_SILENCIO_MS,
      // no tiene sentido seguir esperando el tope. Solo con waveform vivo
      // (_micVozTs > 0); respeta el mínimo para no cortar el último fonema.
      const t0 = performance.now();
      _tailIntervalo = setInterval(() => {
        if (gen !== _micGen) { clearInterval(_tailIntervalo); _tailIntervalo = null; return; }
        const ahora = performance.now();
        if (ahora - t0 < _TAIL_MIN_MS) return;
        if (_micVozTs > 0 && ahora - _micVozTs >= _TAIL_SILENCIO_MS) parar();
      }, 120);
    };
    // Descartar la captura optimista (arrancó en el keydown para no comerse la
    // primera palabra) cuando el hold NO se confirmó: combo de tipeo, tap corto o
    // blur dentro de la ventana de 220ms. Frena el mic SIN transcribir ni enviar.
    window._orchOnMicDiscard = () => {
      _micSoltado = true;
      // Cola post-soltar pendiente: matarla sin commitear (esto es un descarte).
      if (_tailT) { clearTimeout(_tailT); _tailT = null; }
      if (_tailIntervalo) { clearInterval(_tailIntervalo); _tailIntervalo = null; }
      _tailCerrar = null;
      // Bumpear la generación invalida el onstop del recorder en curso (su gen
      // capturada ya no es la vigente) → NO procesa. Doble candado con _procesadoGen.
      const gen = ++_micGen;
      _procesadoGen = gen;
      _segmentosPrevios = [];   // descarte: los segmentos pre-corte mueren acá también
      if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        try { mediaRecorder.stop(); } catch { /* ya parado */ }
      }
      _detenerWaveform();
      window.JarvisVoiceField?.apagar?.();
      _activeVoiceSession = null;
      if (window.JarvisDock?.activeTab?.() === 'jarvis') setEstado('idle');
      _programarLiberacionMic();
    };

    // Push-to-talk global con tecla/botón configurable (default: Alt)
    instalarPushToTalk();
    // Si "Traducir a inglés" ya está activado, pre-descargar el modelo on-device.
    if (_VOZ.traducir()) _precargarTraductorChrome();

    // Picker rápido de terminal (Ctrl+\ — configurable)
    window.QuickPicker?.init({ onPick: _quickCrearTerminal });
    document.getElementById('btn-quick-terminal')?.addEventListener('click', _abrirQuickPicker);

    // Estado de instalación de CLIs AL ARRANCAR: queda cacheado en
    // window.JarvisClisEstado para que picker/launcher pinten el aviso
    // "Falta instalar" al instante cuando se abran (sin el "1 segundo de nada").
    _tlEstadoFaltantes();

    // Menú de localhost activos (botón #jw-localhosts-btn con contador + popover
    // para ver los dev servers vivos del proyecto, abrirlos en el preview y
    // cerrarlos). Se nutre de GET /api/orchestrator/preview/{id}/servers.
    window.JarvisDevServers?.init();
    window.JarvisDevServers?.cargar?.(projectId);

    // Inicializar el editor (window.JarvisEditor) con el proyecto actual.
    // editor.js es un script clásico cargado después; init() le pasa el id y
    // arranca el file tree + Monaco si el panel editor está visible.
    window.JarvisEditor?.init(projectId);

    // Bridge: OrchestratorPanel llama esto para buscar archivos del proyecto por @query
    window._orchGetFiles = async (query) => {
      try {
        const res = await fetch(`/api/projects/${projectId}/files/tree`);
        if (!res.ok) return [];
        const tree = await res.json();
        const flat = [];
        (function flatten(nodes) {
          for (const n of nodes) {
            if (n.type === 'file') flat.push({ name: n.name, path: n.path });
            if (n.children) flatten(n.children);
          }
        })(tree.children || []);
        if (!query) return flat.slice(0, 12);
        const q = query.toLowerCase();
        return flat.filter(f =>
          f.name.toLowerCase().includes(q) || f.path.toLowerCase().includes(q)
        ).slice(0, 12);
      } catch { return []; }
    };

    setTimeout(async () => {
      setEstado('idle');
      _restaurarChat();
      await reproducirBienvenida();
      setEstado('idle');
      // No robar el foco si el usuario ya se puso a escribir en otro campo
      // o está interactuando con un iframe (p.ej. la app del mobile preview).
      if (!_focoOcupadoEnOtroLado()) _panelTA()?.focus();
    }, 1400);

  } catch (err) {
    console.error('Error inicializando workspace:', err);
    toast('No se pudo cargar el proyecto.', 'error');
    location.href = '/';
  }
}

// Carga el estado del proyecto activo y renderiza sus terminales.
// Reutilizable al cambiar de proyecto.
async function cargarProyecto() {
  const res = await fetch(`/api/workspace/${projectId}/state`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const estado = await res.json();

  // Breadcrumb: nombre del proyecto › ruta abreviada (como el mockup)
  if (elTitulo) elTitulo.textContent = estado.project.nombre;
  const _elPath = document.getElementById('project-path');
  const _elSep  = document.querySelector('.gh-crumb-sep');
  const _ruta = (estado.project.ruta || '').replace(/^\/home\/[^/]+/, '~');
  if (_elPath) _elPath.textContent = _ruta;
  if (_elSep) _elSep.style.display = _ruta ? '' : 'none';
  document.title = `JARVIS — ${estado.project.nombre}`;

  // Entering a project invalidates the "no workspace" welcome (bootstrap
  // ?launcher=nuevo): hide it or it sits ON TOP of #terminals-empty (same box)
  // and swallows its buttons' clicks (overlap bug 2026-09-10).
  document.getElementById('jw-welcome')?.classList.add('oculto');

  for (const t of estado.terminals) {
    agregarTarjetaTerminal(t);
  }
  actualizarVista();
  consultarMobilePreview(true);  // al entrar: auto-abre el panel si es Expo
  // Vínculos del enjambre: las cards recién creadas tienen que nacer con su
  // ícono si ya venían enredadas (el WS solo avisa los CAMBIOS).
  window.SwarmLink?.recargar?.(projectId);
}

// ─── Sidebar: lista de proyectos (V4) ────────

// Orden pensado para que tonos ADYACENTES difieran de matiz (los verdosos
// teal/cyan/green quedan separados): violet→amber→cyan→rose→green→blue→teal.
const _SB_TONES = ['tone-violet','tone-amber','tone-cyan','tone-rose','tone-green','tone-blue','tone-teal'];
let _sbProyectos = [];        // cache local de todos los proyectos
let _sbQuery     = '';        // texto de búsqueda actual
let _sbFirma     = '';        // firma del último render (skip si nada cambió)

function _sbHash(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

// Tono por POSICIÓN en la lista canónica → cada proyecto un color distinto y
// los adyacentes nunca coinciden (el usuario los diferencia de un vistazo).
// Acepta el proyecto o su nombre; con nombre suelto cae al hash (estable).
function _sbToneFor(p) {
  if (p && typeof p === 'object') {
    const i = _sbProyectos.findIndex(x => String(x.id) === String(p.id));
    if (i >= 0) return _SB_TONES[i % _SB_TONES.length];
    return _SB_TONES[_sbHash(p.nombre || '') % _SB_TONES.length];
  }
  return _SB_TONES[_sbHash(p || '') % _SB_TONES.length];
}

// AURA + figura de grilla = "este workspace tiene agentes trabajando" (la fila
// NO cambia de tamaño). La prende/apaga el evento global `proyecto_trabajo`
// (agent_watch) + el reconciliador de 3s.
// HISTÉRESIS del repliegue (pedido 2026-07-12): agent_watch apaga a los ~16s de
// panes quietos, pero una tarea real tiene pausas más largas (test silencioso,
// prompt de permiso, agente pensando) → el aura "parpadeaba" en medio del
// trabajo. Encendido INSTANTÁNEO; apagado recién tras 60s de señal idle
// SOSTENIDA — cualquier retome dentro de la ventana cancela la cuenta regresiva.
var _SB_REPLIEGUE_GRACIA_MS = 60000;   // var a propósito: overrideable en QA
const _sbRepliegueTimers = new Map();  // pid(String) → timeout id (sobrevive rebuilds)

// La gracia debe sobrevivir a los RELOADS: al aplicar un update el server
// re-ejecuta, agent_watch arranca frío unos segundos y la página recargada
// pintaba la ficha CHICA en pleno trabajo (el flap que la histéresis evita,
// pero en el borde del reinicio). Se persiste qué proyectos estaban grandes
// (localStorage, ventana 90s): el primer paint los respeta y les arma la
// gracia normal — si la señal se re-arma, quedan; si no, se repliegan a los 60s.
const _SB_FICHAS_LS = 'jarvis.fichasGrandes';
function _sbPersistirFichas(ids) {
  try { localStorage.setItem(_SB_FICHAS_LS, JSON.stringify({ ts: Date.now(), ids: [...ids] })); } catch {}
}
function _sbFichasPersistidas() {
  try {
    const d = JSON.parse(localStorage.getItem(_SB_FICHAS_LS) || 'null');
    if (!d || Date.now() - d.ts > 90000) return new Set();
    return new Set((d.ids || []).map(String));
  } catch { return new Set(); }
}

function _sbAplicarTrabajo(pid, trabajando) {
  const key = String(pid);
  if (trabajando) {
    const t = _sbRepliegueTimers.get(key);
    if (t) { clearTimeout(t); _sbRepliegueTimers.delete(key); }
    _sbAplicarTrabajoAhora(pid, true);
    return;
  }
  if (_sbRepliegueTimers.has(key)) return;   // cuenta regresiva ya corriendo
  const p = _sbProyectos.find(x => String(x.id) === key);
  const fila = elSidebarNav?.querySelector(`.sb-row[data-id="${CSS.escape(key)}"]`);
  const grande = (p?.status === 'run') || !!fila?.classList.contains('status-run');
  if (!grande) return;                        // ya está replegada: nada que demorar
  _sbRepliegueTimers.set(key, setTimeout(() => {
    _sbRepliegueTimers.delete(key);
    _sbAplicarTrabajoAhora(pid, false);
  }, _SB_REPLIEGUE_GRACIA_MS));
}

// Aplica el estado YA, sin gracia (la capa de arriba decide el cuándo).
// Actualiza el cache y la fila en el DOM sin re-render (para que el re-render
// también quede correcto).
function _sbAplicarTrabajoAhora(pid, trabajando) {
  const p = _sbProyectos.find(x => String(x.id) === String(pid));
  const archived = p ? p.seccion === 'archived' : false;
  if (p) p.status = (archived ? 'idle' : (trabajando ? 'run' : 'idle'));
  const row = elSidebarNav?.querySelector(`.sb-row[data-id="${CSS.escape(String(pid))}"]`);
  if (!row) return;
  const esArch = row.dataset.section === 'archived';
  const activo = trabajando && !esArch;
  const yaTrabaja = row.classList.contains('status-run');
  if (activo === yaTrabaja) return;   // sin cambio real → NO re-animar (el poller de 3s lo llama seguido)
  if (row._sbTransTO) { clearTimeout(row._sbTransTO); row._sbTransTO = null; }
  row.classList.remove('waking', 'sleeping', 'status-work', 'status-err');
  if (activo) {
    row.classList.add('status-run');   // ficha grande + haz del borde (CSS)
    if (!row.querySelector('.sb-snake')) row.querySelector('.sb-icon')?.insertAdjacentHTML('afterend', _sbSnakeSVG());
    if (!row.querySelector('.sb-glow')) row.insertAdjacentHTML('afterbegin', '<span class="sb-glow" aria-hidden="true"></span>');
    row.classList.add('waking');   // transición Gira: la veta gira/sale, la figura entra girando
    row._sbTransTO = setTimeout(() => { row.classList.remove('waking'); row._sbTransTO = null; }, 1300);
    _sbSubirPorTrabajo(pid);       // orden vivo: el que trabaja sube sobre los quietos
  } else {
    row.classList.remove('status-run');   // vuelve a fila normal (la veta reaparece)
    const snake = row.querySelector('.sb-snake');
    const glow = row.querySelector('.sb-glow');
    row.classList.add('sleeping');         // la figura sale girando; recién al terminar se quita
    row._sbTransTO = setTimeout(() => { row.classList.remove('sleeping'); snake?.remove(); glow?.remove(); row._sbTransTO = null; }, 1000);
  }
}

// ─── Orden vivo (pedido 2026-07-12): el que TRABAJA sube sobre los quietos ───
// Al ponerse a trabajar, el proyecto pasa por encima del bloque de proyectos
// QUIETOS contiguos arriba suyo (misma sección). El ascenso es PERSISTENTE
// (queda aunque termine — regla 3). Cada destronado guarda un RECLAMO: cuando
// ÉL vuelva a trabajar recupera su posición (vuelve arriba del que lo pasó,
// aunque el otro siga trabajando — regla 2). Si nunca trabaja, el reclamo no se
// ejerce y el ascenso queda (regla 1+3). Reclamos en localStorage (sobreviven
// reloads); el orden va por POST /reorder → projects_update → el FLIP del
// re-render hace visible el deslizamiento en todos los clientes.
const _SB_RECLAMOS_LS = 'jarvis.reclamos';
function _sbReclamos() { try { return JSON.parse(localStorage.getItem(_SB_RECLAMOS_LS) || '{}'); } catch { return {}; } }
function _sbGuardarReclamos(r) { try { localStorage.setItem(_SB_RECLAMOS_LS, JSON.stringify(r)); } catch {} }

let _sbSubirCola = Promise.resolve();   // serializa reorders (los edges vienen en ráfaga)
function _sbSubirPorTrabajo(pid) {
  _sbSubirCola = _sbSubirCola.then(() => _sbSubirAhora(String(pid))).catch(() => {});
}
async function _sbSubirAhora(pid) {
  const p = _sbProyectos.find(x => String(x.id) === pid);
  // SOLO la sección "active": los FIJADOS van siempre primeros y su orden es
  // sagrado (nunca los pasa nadie; solo el drag manual del usuario los mueve) —
  // pedido 2026-07-12. Los archivados no trabajan.
  if (!p || (p.seccion || 'active') !== 'active') return;
  const seccion = 'active';
  const lista = _sbProyectos.filter(x => (x.seccion || 'active') === seccion).map(x => String(x.id));
  const i = lista.indexOf(pid);
  if (i <= 0) return;   // ya está arriba de todo (o no está)
  const trabaja = (id) => {
    const q = _sbProyectos.find(x => String(x.id) === id);
    return !!q && q.status !== 'idle';
  };
  const reclamos = _sbReclamos();

  // 1) RECLAMO: si me pasaron por encima mientras estaba quieto, recupero mi
  //    posición (arriba del más alto de mis usurpadores), trabajen o no.
  let destino = i;
  const usurpadores = (reclamos[pid] || []).map(id => lista.indexOf(id)).filter(k => k >= 0 && k < i);
  if (usurpadores.length) destino = Math.min(...usurpadores);

  // 2) BURBUJA: sigo subiendo por encima del bloque contiguo de QUIETOS.
  while (destino > 0 && !trabaja(lista[destino - 1])) destino--;

  if (destino === i) { delete reclamos[pid]; _sbGuardarReclamos(reclamos); return; }
  // Cada QUIETO que paso gana su reclamo contra mí (recupera el lugar al trabajar).
  for (let k = destino; k < i; k++) {
    const victima = lista[k];
    if (trabaja(victima)) continue;
    reclamos[victima] = [...new Set([...(reclamos[victima] || []), pid])];
  }
  delete reclamos[pid];   // mi reclamo se consume al ejercerlo
  _sbGuardarReclamos(reclamos);

  lista.splice(i, 1); lista.splice(destino, 0, pid);
  const res = await fetch('/api/projects/reorder', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ seccion, ids: lista.map(Number) }),
  });
  if (!res.ok) return;
  // Reflejar el orden nuevo en el cache local YA (los edges encolados lo usan;
  // el projects_update del server confirma canónicamente y dispara el FLIP).
  const pos = new Map(lista.map((id, k) => [id, k]));
  _sbProyectos.sort((a, b) => {
    const sa = a.seccion || 'active', sb = b.seccion || 'active';
    if (sa !== sb) return 0;                       // el orden entre secciones lo da el server
    if (sa !== seccion) return 0;
    return (pos.get(String(a.id)) ?? 0) - (pos.get(String(b.id)) ?? 0);
  });
}

function _sbIniciales(nombre) {
  const limpio = (nombre || '?').replace(/[^a-zA-Z0-9]/g, '');
  if (limpio.length === 0) return '??';
  if (limpio.length === 1) return (limpio[0].toUpperCase() + limpio[0].toLowerCase());
  return limpio[0].toUpperCase() + limpio[1].toLowerCase();
}

function _sbMatch(proyecto, q) {
  if (!q) return true;
  const ql = q.toLowerCase();
  return (proyecto.nombre || '').toLowerCase().includes(ql)
      || (proyecto.branch || '').toLowerCase().includes(ql)
      || (proyecto.ruta   || '').toLowerCase().includes(ql);
}

// ─── Árbol de archivos inline por proyecto (dot desplegable) ──────
// El dot de cada fila abre/cierra el árbol de /files/tree DENTRO de la franja.
// Estado y cache sobreviven a los re-render del sidebar (poller de datos).
const _sbExpandido = new Set();   // ids (String) con el árbol abierto
const _sbTreeCache = {};          // id → JSON del árbol (lazy fetch)
const _SB_CHEV   = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';
const _SB_FOLDER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 6h6l2 2h10v10a2 2 0 0 1-2 2H3z"/></svg>';
const _SB_FILE   = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M14 3v5h5"/><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/></svg>';
const _SB_NEWFILE   = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 3v5h5M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h4"/><path d="M18 14v6M15 17h6"/></svg>';
const _SB_NEWFOLDER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h6l2 2h10v5M3 6v13a2 2 0 0 0 2 2h8"/><path d="M18 14v6M15 17h6"/></svg>';
const _SB_TRASH  = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 6.5h17M9.5 6V4.5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1V6M6 6.5l1 13a1 1 0 0 0 1 .9h8a1 1 0 0 0 1-.9l1-13"/></svg>';
const _SB_REFRESH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6"/></svg>';
const _SB_UPLOAD = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 15V4M8 8l4-4 4 4M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/></svg>';
const _SB_ZIP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 3v5h5M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M10.5 7h1.5M10.5 10h1.5M10.5 13h1.5"/></svg>';
// Traductor para los textos de la franja (zona i18n-skip: el observer NO la toca,
// así que hay que traducir en el render con t()). Se re-renderiza al cambiar idioma.
const _sbT = (s) => window.JarvisI18n?.t?.(s) ?? s;

async function cargarSidebar() {
  if (!elSidebarNav) return;
  try {
    const res = await fetch('/api/projects');
    if (!res.ok) return;
    const nuevos = await res.json();
    // Refresh imperceptible: si nada cambió, no reconstruir el DOM
    const firma = JSON.stringify(nuevos);
    if (firma === _sbFirma) return;
    _sbFirma = firma;
    _sbProyectos = nuevos;
    // Proyectos en gracia de repliegue: el server ya dice idle pero la ficha
    // sigue grande a propósito (histéresis) — mantener el rebuild consistente.
    for (const p of _sbProyectos)
      if (p.status === 'idle' && _sbRepliegueTimers.has(String(p.id))) p.status = 'run';
    // Primer fetch de la sesión: si un proyecto estaba GRANDE hace <90s (página
    // anterior / update del server), pintarlo grande y armarle la gracia normal —
    // cubre la señal fría de agent_watch tras un re-exec sin que la ficha parpadee.
    if (!cargarSidebar._overlayHecho) {
      cargarSidebar._overlayHecho = true;
      const previas = _sbFichasPersistidas();
      const resucitar = _sbProyectos.filter(p =>
        p.status === 'idle' && p.seccion !== 'archived' && previas.has(String(p.id)));
      for (const p of resucitar) p.status = 'run';
      if (resucitar.length) setTimeout(() => {
        for (const p of resucitar) _sbAplicarTrabajo(p.id, false);   // arma la cuenta regresiva de 60s
      }, 0);
    }
    renderSidebar();
  } catch { /* silencioso */ }
}

function renderSidebar() {
  if (!elSidebarNav) return;
  // FLIP: posiciones previas por id — la fila que se muda en el rebuild
  // (fijar/desfijar/archivar reordenan la lista) DESLIZA a su nuevo lugar en
  // vez de teletransportarse. Solo transform (compositado, one-shot).
  const _prevTop = new Map();
  elSidebarNav.querySelectorAll('.sb-row').forEach(r =>
    _prevTop.set(r.dataset.id, r.getBoundingClientRect().top));
  // Stagger de entrada SOLO en el primer paint de la sesión: los re-renders
  // (badge, pin, rename, cambio de proyecto) no deben re-animar las rows
  // (era parte del "triple parpadeo").
  elSidebarNav.classList.toggle('sb-anim', !elSidebarNav.dataset.animado);
  elSidebarNav.dataset.animado = '1';
  const filtrados = _sbProyectos.filter(p => _sbMatch(p, _sbQuery));

  // #sidebar-nav es zona i18n-skip (protege los nombres de proyecto), así que
  // su chrome propio (headers de sección, empty state) se traduce ACÁ con t().
  const _t = window.JarvisI18n?.t || ((s) => s);

  if (filtrados.length === 0) {
    elSidebarNav.innerHTML = `<div class="sb-empty">${_t('Sin resultados')}</div>`;
    return;
  }

  // Agrupar por sección
  const grupos = { pinned: [], active: [], archived: [] };
  for (const p of filtrados) {
    const s = p.seccion || 'active';
    if (grupos[s]) grupos[s].push(p);
    else grupos.active.push(p);
  }

  // Construir HTML — SIN headers de sección (mock): la jerarquía por tamaño
  // (fijados hero / activos / archivados mini) marca las secciones. Divisor
  // mudo (caja) antes de archivados; fila fantasma "+ Nuevo workspace" tras activos.
  let visibleIdx = 0; // para asignar shortcuts ⌘1-9
  const html = [];
  for (const seccion of ['pinned','active','archived']) {
    const items = grupos[seccion];
    if (seccion === 'archived' && items.length) {
      html.push(`<div class="sb-arch-div" aria-hidden="true"><i></i>` +
        `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M21 8v13H3V8M1 3h22v5H1zM10 12h4"/></svg>` +
        `<i></i></div>`);
    }
    for (const p of items) {
      visibleIdx++;
      html.push(_sbRowHTML(p, visibleIdx));
    }
    if (seccion === 'active') {
      html.push(
        `<button class="sb-new-ghost" type="button">` +
          `<span class="sb-gplus"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M5 12h14M12 5v14"/></svg></span>` +
          `${_t('Nuevo workspace')}` +
        `</button>`
      );
    }
  }
  elSidebarNav.innerHTML = html.join('');
  // FLIP (2ª mitad): las filas que cambiaron de lugar deslizan desde su posición
  // previa. Corre antes del paint (mismo task que el innerHTML) → sin flash.
  if (_prevTop.size && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    elSidebarNav.querySelectorAll('.sb-row').forEach(r => {
      const antes = _prevTop.get(r.dataset.id);
      if (antes == null) return;
      const d = antes - r.getBoundingClientRect().top;
      if (Math.abs(d) < 2) return;
      r.animate([{ transform: `translateY(${d}px)` }, { transform: 'translateY(0)' }],
                { duration: 260, easing: 'cubic-bezier(.22,1,.36,1)' });
    });
  }
  elSidebarNav.querySelector('.sb-new-ghost')?.addEventListener('click', () => abrirLauncher());

  // Cablear eventos (click, contextmenu, drag&drop)
  elSidebarNav.querySelectorAll('.sb-row').forEach(row => {
    const id = row.dataset.id;
    row.addEventListener('click', (e) => { if (e.target.isContentEditable) return; cambiarProyecto(id); });
    row.addEventListener('contextmenu', e => {
      e.preventDefault();
      _sbAbrirContextMenu(e.clientX, e.clientY, id);
    });
    row.querySelector('.sb-disc')?.addEventListener('click', (e) => {
      e.stopPropagation();
      _sbToggleFiles(row.dataset.id);
    });
    row.querySelector('.sb-row-x')?.addEventListener('click', (e) => {
      e.stopPropagation();
      _sbEjecutarAccion('remove', row.dataset.id);
    });
    _sbCablearDragDrop(row);
  });
  _sbSyncTrees();
}

// ─── Conmutador Workspaces ⇄ Editor + pane Editor (acordeón) ───────
// El pane Editor lista TODOS los proyectos; el activo se abre mostrando su
// árbol (reusa _sbFetchTree/_sbTreeChildrenHTML/_sbWireTree). Es la "pestaña
// Editor" del rediseño; el árbol inline de la lista Workspaces se conserva.
let _sbView = 'spaces';   // 'spaces' | 'editor'

function setSidebarView(v) {
  _sbView = (v === 'editor') ? 'editor' : 'spaces';
  const seg = document.getElementById('jw-seg');
  const panes = document.getElementById('jw-panes');
  if (panes) panes.dataset.view = _sbView;
  if (seg) seg.dataset.view = _sbView;
  document.getElementById('jw-seg-spaces')?.classList.toggle('on', _sbView === 'spaces');
  document.getElementById('jw-seg-editor')?.classList.toggle('on', _sbView === 'editor');
  document.getElementById('jw-seg-spaces')?.setAttribute('aria-selected', String(_sbView === 'spaces'));
  document.getElementById('jw-seg-editor')?.setAttribute('aria-selected', String(_sbView === 'editor'));
  _sbLayoutPill();   // desliza la gota de vidrio al segmento activo
  if (_sbView === 'editor') renderEditorPane();
}

// Posiciona la gota de vidrio (.jw-seg-thumb) sobre el segmento activo de la píldora.
// Mide la geometría FINAL en un clon invisible (transiciones off) para que la gota
// vaya directo al destino real, sin "pasar por el otro" a mitad del morph del rótulo.
function _sbLayoutPill() {
  const box = document.getElementById('jw-seg'); if (!box) return;
  let thumb = box.querySelector('.jw-seg-thumb');
  if (!thumb) { thumb = document.createElement('span'); thumb.className = 'jw-seg-thumb'; thumb.setAttribute('aria-hidden', 'true'); box.insertBefore(thumb, box.firstChild); }
  const clone = box.cloneNode(true);
  Object.assign(clone.style, { position: 'absolute', left: '-9999px', top: '0', visibility: 'hidden' });
  clone.querySelectorAll('*').forEach(x => { x.style.transition = 'none'; x.style.animation = 'none'; });
  box.parentNode.appendChild(clone);
  const act = clone.querySelector('button.on');
  const r = act ? { left: act.offsetLeft, width: act.offsetWidth } : null;
  clone.remove();
  if (!r) return;
  thumb.style.width = r.width + 'px';
  thumb.style.transform = `translateX(${r.left}px)`;
}

function renderEditorPane() {
  const cont = document.getElementById('fx-list');
  if (!cont) return;
  const _t = window.JarvisI18n?.t || ((s) => s);
  if (!_sbProyectos.length) {
    cont.innerHTML = `<div class="sb-empty">${_t('Sin proyectos')}</div>`;
    return;
  }
  const chv = `<svg class="chv" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="4,6 8,10 12,6"/></svg>`;
  cont.innerHTML = _sbProyectos.map(p => {
    const activo = String(p.id) === String(projectId);
    const tone   = _sbToneFor(p);
    const status = p.status || 'idle';
    return `<section class="fx-ws ${tone}${activo ? ' on' : ''}${status !== 'idle' ? ' working' : ''}" data-id="${esc(p.id)}">
        <button class="fx-ws-head" type="button">
          <span class="emark" aria-hidden="true"><i class="emk-veta"></i>${status !== 'idle' ? _sbSnakeSVG() : ''}</span>
          <b>${esc(p.nombre)}</b>${chv}
        </button>
        <div class="fx-body"><div class="fx-clip" data-clip="${esc(p.id)}"></div></div>
      </section>`;
  }).join('');
  cont.querySelectorAll('.fx-ws-head').forEach(h => h.addEventListener('click', () => {
    const sec = h.closest('.fx-ws');
    const id  = sec.dataset.id;
    if (sec.classList.contains('on')) {           // re-click en el activo → plegar/desplegar
      sec.classList.toggle('plegado');
      if (!sec.classList.contains('plegado')) _sbEditorLoadTree(sec, id);
      return;
    }
    cambiarProyecto(id);   // cambiar de proyecto (re-renderiza el pane con el nuevo activo)
  }));
  const act = cont.querySelector('.fx-ws.on');
  if (act) _sbEditorLoadTree(act, act.dataset.id);
}

async function _sbEditorLoadTree(sec, id) {
  const clip = sec.querySelector('.fx-clip');
  if (!clip || clip.dataset.loaded) return;
  const data = await _sbFetchTree(id);
  const treeHTML = (data && Array.isArray(data.children) && data.children.length)
    ? _sbTreeChildrenHTML(data.children, 0, data && data.protegido, new Set((data && data.creados) || []))
    : `<div class="sb-ftree-empty">${_sbT('Sin archivos visibles')}</div>`;
  const tools = `<div class="fx-tools">
      <button class="fx-act" data-act="refresh" title="${_sbT('Refrescar')}" aria-label="${_sbT('Refrescar')}">${_SB_REFRESH}</button>
      <button class="fx-act" data-act="upload" title="${_sbT('Subir archivos')}" aria-label="${_sbT('Subir archivos')}">${_SB_UPLOAD}</button>
      <button class="fx-act" data-new="file" title="${_sbT('Nuevo archivo')}" aria-label="${_sbT('Nuevo archivo')}">${_SB_NEWFILE}</button>
      <button class="fx-act" data-new="folder" title="${_sbT('Nueva carpeta')}" aria-label="${_sbT('Nueva carpeta')}">${_SB_NEWFOLDER}</button>
    </div>`;
  clip.innerHTML = `${tools}<div class="sb-files-inner">${treeHTML}</div>`;
  clip.dataset.loaded = '1';
  _sbWireTree(clip, id);
  _sbCablearDropSubida(clip, id);   // drop de archivos/carpetas/zip del escritorio
  clip.querySelectorAll('.fx-act').forEach(btn => btn.addEventListener('click', e => {
    e.stopPropagation();
    if (btn.dataset.act === 'refresh') { clip.dataset.loaded = ''; _sbTreeCache[id] && delete _sbTreeCache[id]; _sbEditorLoadTree(sec, id); }
    else if (btn.dataset.act === 'upload') _sbMenuSubir(btn, id);   // menú: archivos / carpeta / zip
    else _sbNuevoInput(clip, id, btn.dataset.new);
  }));
}

// ─── Drag & drop para reordenar dentro y entre secciones ──────────

let _sbDragged = null;  // row siendo arrastrada

function _sbCablearDragDrop(row) {
  row.addEventListener('dragstart', (e) => {
    _sbDragged = row;
    row.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    try { e.dataTransfer.setData('text/plain', row.dataset.id); } catch {}
  });

  row.addEventListener('dragend', () => {
    row.classList.remove('dragging');
    _sbLimpiarIndicadores();
    _sbDragged = null;
  });

  row.addEventListener('dragover', (e) => {
    if (!_sbDragged || _sbDragged === row) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const rect = row.getBoundingClientRect();
    const above = e.clientY < rect.top + rect.height / 2;
    _sbLimpiarIndicadores();
    row.classList.add(above ? 'drop-above' : 'drop-below');
  });

  row.addEventListener('dragleave', () => {
    row.classList.remove('drop-above', 'drop-below');
  });

  row.addEventListener('drop', async (e) => {
    if (!_sbDragged || _sbDragged === row) return;
    e.preventDefault();
    const rect = row.getBoundingClientRect();
    const above = e.clientY < rect.top + rect.height / 2;
    const seccionDestino = row.dataset.section;
    _sbLimpiarIndicadores();

    // Insertar en el DOM en la nueva posición
    if (above) row.parentNode.insertBefore(_sbDragged, row);
    else       row.parentNode.insertBefore(_sbDragged, row.nextSibling);

    // Marcar la dragged con la nueva sección (si cambió)
    _sbDragged.dataset.section = seccionDestino;

    // Recolectar el orden final de la sección destino y persistir
    const idsEnSeccion = [...elSidebarNav.querySelectorAll(
      `.sb-row[data-section="${seccionDestino}"]`
    )].map(r => parseInt(r.dataset.id, 10)).filter(Number.isFinite);

    try {
      const res = await fetch('/api/projects/reorder', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ seccion: seccionDestino, ids: idsEnSeccion }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await cargarSidebar();  // refrescar con datos canónicos del server
    } catch (err) {
      toast(`Error reordenando: ${err.message}`, 'error');
      await cargarSidebar();  // revertir UI al estado real
    }
  });
}

function _sbLimpiarIndicadores() {
  elSidebarNav?.querySelectorAll('.drop-above, .drop-below').forEach(el => {
    el.classList.remove('drop-above', 'drop-below');
  });
}

function _sbRowHTML(p, idx) {
  const esActivo  = String(p.id) === String(projectId);
  const tone      = _sbToneFor(p);
  const status    = p.status || 'idle';
  const archivado = (p.seccion === 'archived');
  const trabajando = status !== 'idle' && !archivado;   // muestra la figura de grilla
  const count     = p.terminales_activas || 0;
  const countHtml = count > 0
    ? `<span class="sb-row-count" title="${count} terminal${count !== 1 ? 'es' : ''} activa${count !== 1 ? 's' : ''}">${count}</span>`
    : '';
  const title = `${p.nombre}${p.branch ? ' · ' + p.branch : ''}${p.ruta ? '\n' + p.ruta : ''}`;
  // Marcador "El Roster": VETA de color (.sb-icon, tono en la fila) que al trabajar
  // se oculta y aparece la figura de grilla (.sb-snake, la inyecta _sbSnakeSVG /
  // _sbAplicarTrabajo) + el aura (.sb-glow). La fila NO cambia de tamaño (pedido
  // 2026-07-12). Inicial en mayúscula la fuerza el CSS (::first-letter).
  const nombre = p.nombre;
  return `
    <div class="sb-row ${tone}${esActivo ? ' activo' : ''}${archivado ? ' archived' : ''}${status !== 'idle' ? ' status-' + status : ''}"
         data-id="${esc(p.id)}" data-idx="${idx}" data-section="${esc(p.seccion || 'active')}"
         style="--i:${Math.min(idx, 14)}"
         draggable="true" title="${esc(title)}" role="button" tabindex="0">
      ${trabajando ? '<span class="sb-glow" aria-hidden="true"></span>' : ''}<span class="sb-icon"></span>${trabajando ? _sbSnakeSVG() : ''}
      <span class="sb-row-name">${esc(nombre)}</span>
      ${countHtml}
      <button class="sb-row-x" data-id="${esc(p.id)}" title="Quitar de la lista" aria-label="Quitar de la lista" tabindex="-1">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </button>
    </div>`;
}
// Figura de grilla 3×3 "Constante" (default): cada celda con su delay para la ola
// diagonal (r+c). El color (fill) sale de --ic-fg (tono de la fila). Ver base.css.
function _sbSnakeSVG() {
  let cells = '';
  for (let i = 0; i < 9; i++) {
    const r = Math.floor(i / 3), c = i % 3;
    const delay = ((r + c) * 0.42).toFixed(2);
    cells += `<rect x="${(6 + c * 2.8).toFixed(2)}" y="${(6 + r * 2.8).toFixed(2)}" width="2.4" height="2.4" rx="0.6" style="animation-delay:${delay}s"/>`;   // origen 6: span pintado [6,14] = centrado exacto en el viewBox 20
  }
  return `<svg class="sb-snake" viewBox="0 0 20 20" aria-hidden="true">${cells}</svg>`;
}

// ─── Motor del árbol de archivos inline ───────────────────────────
function _sbRowById(id) {
  return elSidebarNav?.querySelector(`.sb-row[data-id="${CSS.escape(String(id))}"]`);
}
async function _sbFetchTree(id) {
  if (_sbTreeCache[id]) return _sbTreeCache[id];
  try {
    const res = await fetch(`/api/projects/${encodeURIComponent(id)}/files/tree`);
    if (!res.ok) return null;
    const data = await res.json();
    _sbTreeCache[id] = data;
    return data;
  } catch { return null; }
}
async function _sbToggleFiles(id) {
  id = String(id);
  const row = _sbRowById(id);
  if (!row) return;
  if (_sbExpandido.has(id)) {
    _sbExpandido.delete(id);
    row.classList.remove('sb-open');
    _sbRemoveTree(id, true);
    return;
  }
  _sbExpandido.add(id);
  row.classList.add('sb-open');
  const data = await _sbFetchTree(id);
  if (!_sbExpandido.has(id)) return;          // se cerró mientras cargaba
  _sbInsertTree(id, _sbRowById(id) || row, data, false);
}
// Colapsa TODOS los árboles de archivos abiertos en la franja. Lo llama el editor
// deslizante al cerrarse (window.JarvisStripFiles.colapsar), así el "cajón" de
// carpetas se vuelve a esconder cuando salís del editor.
function _sbColapsarArboles() {
  for (const id of [..._sbExpandido]) {
    _sbExpandido.delete(id);
    _sbRowById(id)?.classList.remove('sb-open');
    _sbRemoveTree(id, true);
  }
}
window.JarvisStripFiles = { colapsar: _sbColapsarArboles };
function _sbInsertTree(id, row, data, instant) {
  if (!row) return;
  _sbRemoveTree(id, false);
  const wrap = document.createElement('div');
  wrap.className = 'sb-files';
  wrap.dataset.for = String(id);
  const hijos = (data && Array.isArray(data.children) && data.children.length)
    ? `<div class="sb-files-inner">${_sbTreeChildrenHTML(data.children, 0, data && data.protegido, new Set((data && data.creados) || []))}</div>`
    : `<div class="sb-files-inner"><div class="sb-ftree-empty">${_sbT('Sin archivos visibles')}</div></div>`;
  // Barra de acciones ARRIBA DE TODO: crear archivo / carpeta nuevos en el proyecto.
  const barra = `<div class="sb-files-bar">
      <span class="sb-files-title">${_sbT('Archivos')}</span>
      <button class="sb-fnew" data-act="refresh" title="${_sbT('Refrescar')}" aria-label="${_sbT('Refrescar')}">${_SB_REFRESH}</button>
      <button class="sb-fnew" data-act="upload" title="${_sbT('Subir archivos')}" aria-label="${_sbT('Subir archivos')}">${_SB_UPLOAD}</button>
      <button class="sb-fnew" data-new="file" title="${_sbT('Nuevo archivo')}" aria-label="${_sbT('Nuevo archivo')}">${_SB_NEWFILE}</button>
      <button class="sb-fnew" data-new="folder" title="${_sbT('Nueva carpeta')}" aria-label="${_sbT('Nueva carpeta')}">${_SB_NEWFOLDER}</button>
    </div>`;
  wrap.innerHTML = `<div class="sb-files-clip">${barra}${hijos}</div>`;
  row.after(wrap);
  _sbWireTree(wrap, id);
  _sbCablearDropSubida(wrap, id);   // drop de archivos/carpetas/zip del escritorio
  wrap.querySelectorAll('.sb-fnew').forEach(btn => btn.addEventListener('click', e => {
    e.stopPropagation();
    if (btn.dataset.act === 'refresh') _sbRefrescarArbol(id);                 // recarga el árbol
    else if (btn.dataset.act === 'upload') _sbMenuSubir(btn, id);             // menú: archivos / carpeta / zip
    else _sbNuevoInput(wrap, id, btn.dataset.new);
  }));
  if (instant) wrap.classList.add('open');
  else requestAnimationFrame(() => wrap.classList.add('open'));
}
// Input inline para nombrar el archivo/carpeta nuevo (aparece arriba del árbol).
function _sbNuevoInput(wrap, id, tipo) {
  const inner = wrap.querySelector('.sb-files-inner');
  if (!inner) return;
  inner.querySelector('.sb-fnew-row')?.remove();          // un solo input a la vez
  const empty = inner.querySelector('.sb-ftree-empty'); if (empty) empty.style.display = 'none';
  const row = document.createElement('div');
  row.className = 'sb-fnew-row';
  row.innerHTML = `<span class="sb-fico">${tipo === 'folder' ? _SB_FOLDER : _SB_FILE}</span>` +
    `<input class="sb-fnew-input" spellcheck="false" autocomplete="off" placeholder="${_sbT(tipo === 'folder' ? 'nombre-carpeta' : 'nombre.ext')}">`;
  inner.prepend(row);
  const input = row.querySelector('input');
  input.focus();
  let cerrado = false;
  const cancel = () => { if (cerrado) return; cerrado = true; row.remove(); if (empty) empty.style.display = ''; };
  const commit = () => {
    if (cerrado) return;
    const name = input.value.trim();
    if (!name) { cancel(); return; }
    cerrado = true; input.disabled = true;
    _sbCrear(id, name, tipo);   // crea + refresca el árbol (el re-render se lleva el input)
  };
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    else if (e.key === 'Escape') { e.preventDefault(); cancel(); }
  });
  input.addEventListener('blur', () => setTimeout(cancel, 120));   // clic afuera cancela
}
async function _sbCrear(id, name, tipo) {
  try {
    if (tipo === 'folder') {
      const res = await fetch(`/api/projects/${encodeURIComponent(id)}/files/mkdir`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: name }),
      });
      if (!res.ok) { const e = await res.json().catch(() => ({})); window.toast?.(e.detail || _sbT('No se pudo crear la carpeta'), 'error'); }
      else window.toast?.(_sbT('Carpeta creada'), 'ok');
    } else {
      // archivo: colisión chequeada contra el árbol CACHEADO (sin pegarle al backend → sin 404 en consola).
      // Si ya existe a nivel raíz NO lo pisamos → lo abrimos; si no, lo creamos vacío con save.
      const yaExiste = (_sbTreeCache[id]?.children || []).some(n => n.path === name || n.name === name);
      if (yaExiste) { window.toast?.(_sbT('Ese archivo ya existe'), 'info'); }
      else {
        const res = await fetch(`/api/projects/${encodeURIComponent(id)}/files/save`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: name, content: '' }),
        });
        if (!res.ok) { const e = await res.json().catch(() => ({})); window.toast?.(e.detail || _sbT('No se pudo crear el archivo'), 'error'); return _sbRefrescarArbol(id); }
        window.toast?.(_sbT('Archivo creado'), 'ok');
      }
      _sbAbrirArchivo(id, name);   // abrir el archivo nuevo (o el existente) en el editor
    }
  } catch (_) { window.toast?.(_sbT('No se pudo crear'), 'error'); }
  _sbRefrescarArbol(id);
}
function _sbRefrescarArbol(id) {
  _sbTreeCache[id] = null;                                // invalidar cache
  _sbFetchTree(id).then(data => {
    if (_sbExpandido.has(String(id))) _sbInsertTree(id, _sbRowById(id), data, true);
    // Si el pane Editor (fx) tiene el árbol de este proyecto cargado, recargarlo también
    const sec = document.querySelector(`.fx-ws[data-id="${CSS.escape(String(id))}"]`);
    const clip = sec?.querySelector('.fx-clip');
    if (clip?.dataset.loaded) { clip.dataset.loaded = ''; _sbEditorLoadTree(sec, id); }
  });
}
// ─── Subida al proyecto: archivos sueltos / carpeta entera / ZIP extraído ───
// Carpetas ruidosas que NO se suben (espejo de IGNORE_DIRS del backend: no
// mandar miles de parts de .git/node_modules que igual serían rechazados).
const _SB_UP_IGNORE = new Set([
  '.git', '.worktrees', '__pycache__', 'node_modules',
  'venv', '.venv', '.workspace', '.idea', '.vscode',
  'dist', 'build', '.next', '.cache',
]);
const _sbRutaIgnorada = (rel) => rel.split('/').some(s => _SB_UP_IGNORE.has(s));

// Subir archivos REALES de la PC (cualquier tipo: foto, pdf, código, lo que sea).
// Preserva estructura de carpetas vía rel_paths (webkitRelativePath del picker
// de carpeta o rel sintético del drag&drop). Batching de a 200 como el editor.
async function _sbSubir(id, fileList) {
  const items = [];
  for (const f of [...fileList]) {
    const rel = (f.webkitRelativePath || f._sbRel || f.name || '').replace(/\\/g, '/');
    if (!rel || _sbRutaIgnorada(rel)) continue;
    items.push({ file: f, rel });
  }
  if (!items.length) return;
  const BATCH = 200;
  let subidos = 0, rechazados = 0, primero = null;
  try {
    for (let i = 0; i < items.length; i += BATCH) {
      const fd = new FormData();
      for (const it of items.slice(i, i + BATCH)) {
        fd.append('files', it.file);        // mismo bucle → orden alineado con rel_paths
        fd.append('rel_paths', it.rel);
      }
      const res = await fetch(`/api/projects/${encodeURIComponent(id)}/files/upload`, { method: 'POST', body: fd });
      if (!res.ok) { const e = await res.json().catch(() => ({})); window.toast?.(e.detail || _sbT('No se pudo subir'), 'error'); return; }
      const d = await res.json();
      subidos += (d.subidos || []).length;
      rechazados += (d.rechazados || []).length;
      if (!primero && d.subidos?.[0]) primero = d.subidos[0];
    }
    if (subidos) window.toast?.(`${subidos} ${_sbT('archivo(s) subido(s)')}`, 'ok');
    if (rechazados) window.toast?.(`${rechazados} ${_sbT('rechazado(s)')}`, 'info');
    _sbRefrescarArbol(id);
    // abrir en el editor SOLO si fue un archivo único (con una carpeta sería ruido)
    if (subidos === 1 && primero) _sbAbrirArchivo(id, primero);
  } catch (_) { window.toast?.(_sbT('No se pudo subir'), 'error'); }
}

// Sube un .zip y el backend lo extrae EN MODO CARPETA (envuelto en <nombre-del-zip>/
// si el zip no trae una única carpeta raíz — nunca desparramado en la raíz).
async function _sbSubirZip(id, file) {
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const res = await fetch(`/api/projects/${encodeURIComponent(id)}/files/upload-zip`, { method: 'POST', body: fd });
    if (!res.ok) { const e = await res.json().catch(() => ({})); window.toast?.(e.detail || _sbT('No se pudo extraer el ZIP'), 'error'); return; }
    const d = await res.json();
    const n = (d.subidos || []).length;
    if (n) window.toast?.(`${n} ${_sbT('archivo(s) extraído(s) en')} ${d.carpeta ? d.carpeta + '/' : '/'}`, 'ok');
    else window.toast?.(_sbT('El ZIP no tenía archivos extraíbles'), 'info');
    if ((d.rechazados || []).length) window.toast?.(`${d.rechazados.length} ${_sbT('rechazado(s)')}`, 'info');
    _sbRefrescarArbol(id);
  } catch (_) { window.toast?.(_sbT('No se pudo extraer el ZIP'), 'error'); }
}

// Mini-menú del botón subir: archivos / carpeta / ZIP. Reusa el elemento y las
// clases del context-menu de la franja (cierre por click-afuera/Esc ya cablado).
function _sbMenuSubir(anchor, id) {
  if (!_sbCtxMenu || !anchor) return;
  const items = [
    { ic: _SB_FILE,   tone: 'violet', lbl: 'Subir archivos', act: 'files' },
    { ic: _SB_FOLDER, tone: 'amber',  lbl: 'Subir carpeta',  act: 'dir' },
    { ic: _SB_ZIP,    tone: 'teal',   lbl: 'Subir ZIP (se extrae como carpeta)', act: 'zip' },
  ];
  _sbCtxMenu.innerHTML = items.map(it =>
    `<button class="sb-ctx-item" data-up="${it.act}">` +
    `<span class="sb-ctx-ic tone-${it.tone}">${it.ic.replace('<svg ', '<svg width="13" height="13" ')}</span>` +
    `<span class="sb-ctx-lbl">${_sbT(it.lbl)}</span></button>`).join('');
  _sbCtxMenu.hidden = false;
  const a = anchor.getBoundingClientRect();
  const rect = _sbCtxMenu.getBoundingClientRect();
  _sbCtxMenu.style.left = `${Math.min(a.left, window.innerWidth - rect.width - 8)}px`;
  _sbCtxMenu.style.top  = `${Math.min(a.bottom + 4, window.innerHeight - rect.height - 8)}px`;
  _sbCtxMenu.querySelectorAll('.sb-ctx-item').forEach(b => b.addEventListener('click', () => {
    _sbCerrarContextMenu();
    _sbElegirYSubir(b.dataset.up, id);
  }));
}

// Abre el picker que corresponde y sube al proyecto `id` (onchange por invocación:
// los inputs #fx-* son globales y el proyecto destino cambia según quién los abre).
function _sbElegirYSubir(tipo, id) {
  const inp = document.getElementById(tipo === 'dir' ? 'fx-dir' : tipo === 'zip' ? 'fx-zip' : 'fx-file');
  if (!inp) return;
  inp.onchange = async () => {
    const files = [...(inp.files || [])];
    inp.value = '';   // permite volver a subir lo mismo
    if (!files.length) return;
    if (tipo === 'zip') { for (const f of files) await _sbSubirZip(id, f); }
    else await _sbSubir(id, files);
  };
  inp.click();
}

// Click derecho sobre un .zip del árbol → menú "Extraer en «nombre»/".
// El zip se extrae al lado (carpeta con su nombre) y el .zip original QUEDA
// (pedido del usuario 2026-07-16). Reusa el elemento del context-menu.
function _sbMenuZip(x, y, projId, path, nombre) {
  if (!_sbCtxMenu) return;
  const stem = nombre.replace(/\.zip$/i, '').trim() || 'zip-extraido';
  _sbCtxMenu.innerHTML =
    `<button class="sb-ctx-item" data-xzip="1">` +
    `<span class="sb-ctx-ic tone-teal">${_SB_ZIP.replace('<svg ', '<svg width="13" height="13" ')}</span>` +
    `<span class="sb-ctx-lbl">${_sbT('Extraer en')} «${esc(stem)}/»</span></button>`;
  _sbCtxMenu.hidden = false;
  const rect = _sbCtxMenu.getBoundingClientRect();
  _sbCtxMenu.style.left = `${Math.min(x, window.innerWidth - rect.width - 8)}px`;
  _sbCtxMenu.style.top  = `${Math.min(y, window.innerHeight - rect.height - 8)}px`;
  _sbCtxMenu.querySelector('[data-xzip]')?.addEventListener('click', () => {
    _sbCerrarContextMenu();
    _sbExtraerZip(projId, path);
  });
}

async function _sbExtraerZip(id, path) {
  try {
    const res = await fetch(`/api/projects/${encodeURIComponent(id)}/files/extract-zip`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path }),
    });
    if (!res.ok) { const e = await res.json().catch(() => ({})); window.toast?.(e.detail || _sbT('No se pudo extraer el ZIP'), 'error'); return; }
    const d = await res.json();
    const n = (d.subidos || []).length;
    if (n) window.toast?.(`${n} ${_sbT('archivo(s) extraído(s) en')} ${d.carpeta ? d.carpeta + '/' : '/'}`, 'ok');
    else window.toast?.(_sbT('El ZIP no tenía archivos extraíbles'), 'info');
    if ((d.rechazados || []).length) window.toast?.(`${d.rechazados.length} ${_sbT('rechazado(s)')}`, 'info');
    _sbRefrescarArbol(id);
  } catch (_) { window.toast?.(_sbT('No se pudo extraer el ZIP'), 'error'); }
}

// Recorre un FileSystemEntry (archivo o carpeta arrastrada del escritorio) y
// acumula Files con ruta relativa sintética. Drena el DirectoryReader hasta
// vacío (readEntries devuelve tandas de ~100). Mismo patrón que el editor.
function _sbLeerEntry(entry, prefijo, acc) {
  return new Promise((resolve) => {
    if (entry.isFile) {
      entry.file((file) => {
        const rel = prefijo ? `${prefijo}/${file.name}` : file.name;
        if (!_sbRutaIgnorada(rel)) {
          try { Object.defineProperty(file, 'webkitRelativePath', { value: rel }); } catch (_) {}
          try { file._sbRel = rel; } catch (_) {}
          acc.push(file);
        }
        resolve();
      }, () => resolve());
    } else if (entry.isDirectory) {
      const nombreDir = prefijo ? `${prefijo}/${entry.name}` : entry.name;
      if (_sbRutaIgnorada(nombreDir)) { resolve(); return; }
      const reader = entry.createReader();
      const hijos = [];
      const drenar = () => {
        reader.readEntries(async (tanda) => {
          if (!tanda.length) {
            await Promise.all(hijos.map(h => _sbLeerEntry(h, nombreDir, acc)));
            resolve();
            return;
          }
          hijos.push(...tanda);
          drenar();   // una sola llamada NO devuelve todo
        }, () => resolve());
      };
      drenar();
    } else {
      resolve();
    }
  });
}

// Drop de archivos/carpetas del ESCRITORIO sobre el árbol del proyecto.
// Un .zip suelto arriba de todo se extrae como carpeta; zips dentro de una
// carpeta arrastrada se suben como archivos (son parte del contenido).
function _sbCablearDropSubida(zona, id) {
  const esDeArchivos = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
  zona.addEventListener('dragover', (e) => {
    if (!esDeArchivos(e)) return;
    e.preventDefault(); e.stopPropagation();
    zona.classList.add('sb-drop-over');
  });
  zona.addEventListener('dragleave', (e) => {
    if (!zona.contains(e.relatedTarget)) zona.classList.remove('sb-drop-over');
  });
  zona.addEventListener('drop', async (e) => {
    if (!esDeArchivos(e)) return;
    e.preventDefault(); e.stopPropagation();
    zona.classList.remove('sb-drop-over');
    const entries = [];
    for (const it of (e.dataTransfer.items || [])) {
      const en = it.webkitGetAsEntry?.();
      if (en) entries.push(en);
    }
    const acc = [];
    if (entries.length) { for (const en of entries) await _sbLeerEntry(en, '', acc); }
    else acc.push(...(e.dataTransfer.files || []));   // fallback sin webkitGetAsEntry
    const esZipSuelto = (f) => /\.zip$/i.test(f.name) && (f.webkitRelativePath || f.name) === f.name;
    const zips  = acc.filter(esZipSuelto);
    const resto = acc.filter(f => !esZipSuelto(f));
    for (const z of zips) await _sbSubirZip(id, z);
    if (resto.length) await _sbSubir(id, resto);
  });
}
// Eliminar archivo/carpeta CON confirmación (modal glass). tipo: 'file' | 'dir'.
async function _sbEliminar(id, path, tipo, name) {
  const esDir = tipo === 'dir';
  const cuerpo = esDir ? _sbT('Se eliminará esta carpeta y todo su contenido. Esta acción no se puede deshacer.')
                       : _sbT('Se eliminará este archivo. Esta acción no se puede deshacer.');
  const ok = await window.confirmar?.(
    `"${name}"\n${cuerpo}`,
    { titulo: _sbT(esDir ? 'Eliminar carpeta' : 'Eliminar archivo'), confirmText: _sbT('Eliminar'), cancelText: _sbT('Cancelar'), peligro: true }
  );
  if (!ok) return;
  try {
    const url = esDir
      ? `/api/projects/${encodeURIComponent(id)}/files/dir?path=${encodeURIComponent(path)}`
      : `/api/projects/${encodeURIComponent(id)}/files?path=${encodeURIComponent(path)}`;
    const res = await fetch(url, { method: 'DELETE' });
    if (!res.ok) { const e = await res.json().catch(() => ({})); window.toast?.(e.detail || _sbT('No se pudo eliminar'), 'error'); return; }
    if (!esDir) window.JarvisSlideEditor?.cerrarArchivo?.(path);   // si estaba abierto en el editor, cerrar su pestaña
    window.toast?.(_sbT(esDir ? 'Carpeta eliminada' : 'Archivo eliminado'), 'ok');
    _sbRefrescarArbol(id);
  } catch (_) { window.toast?.(_sbT('No se pudo eliminar'), 'error'); }
}
function _sbRemoveTree(id, animate) {
  const w = elSidebarNav?.querySelector(`.sb-files[data-for="${CSS.escape(String(id))}"]`);
  if (!w) return;
  if (animate) {
    w.classList.remove('open');
    let quitado = false;
    const quitar = () => { if (!quitado) { quitado = true; w.remove(); } };
    w.addEventListener('transitionend', quitar, { once: true });
    setTimeout(quitar, 400);
  } else {
    w.remove();
  }
}
function _sbSyncTrees() {
  // tras un re-render del sidebar, re-insertar los árboles que estaban abiertos
  for (const id of _sbExpandido) {
    const row = _sbRowById(id);
    if (!row) continue;
    row.classList.add('sb-open');
    _sbInsertTree(id, row, _sbTreeCache[id], true);
  }
}
function _sbTreeChildrenHTML(nodes, depth, protegido, creados) {
  const pad = 8 + depth * 12;
  // ícono del archivo alineado con el de las carpetas: pad + chevron(11) + gap(6) = pad+17
  // En un proyecto protegido (Jarvis) solo se puede borrar lo creado desde el editor (set `creados`).
  const puedeBorrar = (p) => !protegido || (creados && creados.has(p));
  const del = (tipo, p) => puedeBorrar(p)
    ? `<button class="sb-fdel" data-del="${tipo}" title="${_sbT(tipo === 'dir' ? 'Eliminar carpeta' : 'Eliminar archivo')}" aria-label="${_sbT(tipo === 'dir' ? 'Eliminar carpeta' : 'Eliminar archivo')}" tabindex="-1">${_SB_TRASH}</button>`
    : '';
  return nodes.map(n => {
    if (n.type === 'dir') {
      return `<div class="sb-fold" data-path="${esc(n.path)}">
        <div class="sb-fnode sb-fdir" style="padding-left:${pad}px" role="button" tabindex="0" data-path="${esc(n.path)}" data-name="${esc(n.name)}">
          <span class="sb-fchev">${_SB_CHEV}</span><span class="sb-fico">${_SB_FOLDER}</span><span class="sb-fnm">${esc(n.name)}</span>${del('dir', n.path)}
        </div>
        <div class="sb-fsub"><div class="sb-fsub-clip"></div></div>
      </div>`;
    }
    // Color por tipo de archivo (extensión): el ícono toma el tono del lenguaje.
    const ext = (n.name.split('.').pop() || '').toLowerCase();
    const tipoCls = { js:'t-js', mjs:'t-js', jsx:'t-js', ts:'t-js', tsx:'t-js',
      css:'t-css', scss:'t-css', html:'t-html', htm:'t-html', py:'t-py',
      json:'t-json', md:'t-md', markdown:'t-md' }[ext] || '';
    return `<div class="sb-fnode sb-ffile ${tipoCls}" style="padding-left:${pad + 17}px" role="button" tabindex="0" data-path="${esc(n.path)}" data-name="${esc(n.name)}">
      <span class="sb-fico sb-fico-file">${_SB_FILE}</span><span class="sb-fnm">${esc(n.name)}</span>${del('file', n.path)}
    </div>`;
  }).join('');
}
function _sbFindNode(data, path) {
  if (!data || !path) return null;
  let nodes = data.children || [];
  let node = null;
  for (const part of path.split('/')) {
    node = nodes.find(x => x.name === part);
    if (!node) return null;
    nodes = node.children || [];
  }
  return node;
}
function _sbWireTree(scope, projId) {
  scope.querySelectorAll('.sb-fdir').forEach(el => {
    if (el.dataset.wired) return; el.dataset.wired = '1';
    const toggle = (e) => { e.stopPropagation(); _sbToggleFolder(el, projId); };
    el.addEventListener('click', toggle);
    el.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(e); } });
  });
  scope.querySelectorAll('.sb-fdel').forEach(btn => {
    if (btn.dataset.wired) return; btn.dataset.wired = '1';
    btn.addEventListener('click', e => {
      e.stopPropagation(); e.preventDefault();          // no abrir/togglear el nodo
      const node = btn.closest('.sb-fnode');
      _sbEliminar(projId, node.dataset.path, btn.dataset.del, node.dataset.name || (node.dataset.path || '').split('/').pop());
    });
  });
  scope.querySelectorAll('.sb-ffile').forEach(el => {
    // re-pintar el dot de "sin guardar" al re-expandir el árbol (el editor conserva el buffer sucio)
    if (window.JarvisSlideEditor?.isDirty?.(el.dataset.path)) el.classList.add('jw-dirty');
    if (el.dataset.wired) return; el.dataset.wired = '1';
    const abrir = (e) => { e.stopPropagation(); _sbSelArchivo(el); _sbAbrirArchivo(projId, el.dataset.path); };
    el.addEventListener('click', abrir);
    el.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); abrir(e); } });
    // Click derecho sobre un .zip → "Extraer en «nombre»/" (los demás conservan el menú nativo)
    el.addEventListener('contextmenu', (e) => {
      const nombre = el.dataset.name || (el.dataset.path || '').split('/').pop() || '';
      if (!/\.zip$/i.test(nombre)) return;
      e.preventDefault(); e.stopPropagation();
      _sbMenuZip(e.clientX, e.clientY, projId, el.dataset.path, nombre);
    });
  });
}
function _sbToggleFolder(dirEl, projId) {
  const fold = dirEl.closest('.sb-fold');
  const sub  = fold.querySelector('.sb-fsub');
  const clip = sub.querySelector('.sb-fsub-clip');
  if (fold.classList.contains('open')) { fold.classList.remove('open'); return; }
  if (!clip.dataset.loaded) {
    const node  = _sbFindNode(_sbTreeCache[projId], dirEl.dataset.path);
    const depth = dirEl.dataset.path.split('/').length;   // los hijos van un nivel más adentro
    clip.innerHTML = _sbTreeChildrenHTML(node?.children || [], depth, _sbTreeCache[projId]?.protegido, new Set(_sbTreeCache[projId]?.creados || []));
    clip.dataset.loaded = '1';
    _sbWireTree(sub, projId);
  }
  fold.classList.add('open');
}
function _sbSelArchivo(el) {
  elSidebarNav?.querySelectorAll('.sb-ffile.sel').forEach(x => x.classList.remove('sel'));
  el.classList.add('sel');
}
async function _sbAbrirArchivo(projId, path) {
  try {
    if (String(projId) !== String(projectId)) await cambiarProyecto(projId);
    // Editor deslizante (reemplaza al Monaco del dock): sale por la izquierda.
    window.JarvisSlideEditor?.abrirArchivo?.(projId, path);
  } catch {}
}

function actualizarSidebarActivo() {
  document.querySelectorAll('.sb-row').forEach(el => {
    el.classList.toggle('activo', String(el.dataset.id) === String(projectId));
  });
  if (_sbView === 'editor') renderEditorPane();   // el pane Editor sigue al proyecto activo
}

// Actualiza SOLO el contador de terminales del proyecto activo, sin
// reconstruir el sidebar. FIX del "triple parpadeo": antes esto llamaba a
// cargarSidebar() (innerHTML completo) en cada cambio de vista, re-disparando
// todas las animaciones de entrada de las rows en cascada.
function actualizarSidebarBadge(count) {
  const row = document.querySelector(`.sb-row[data-id="${projectId}"]`);
  _sbPintarBadge(row, count);
}

// Pinta/quita la píldora del contador en UNA fila cualquiera (el activo la
// actualiza local con `terminales.size`; los demás, con el conteo del server
// que llega en el poll de /api/projects/working).
function _sbPintarBadge(row, count) {
  if (!row) return;
  let badge = row.querySelector('.sb-row-count');
  if (count > 0) {
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'sb-row-count';
      // Antes del ✕ (última celda de la grilla) — el viejo ancla .sb-pip ya no
      // existe (roster 2026-07): con ?. la píldora nunca aterrizaba y el
      // contador desaparecía del proyecto activo.
      const x = row.querySelector('.sb-row-x');
      if (x) row.insertBefore(badge, x); else row.appendChild(badge);
    }
    if (badge.textContent === String(count)) return;   // el poll de 3s no debe tocar el DOM de gusto
    badge.textContent = count;   // solo el número (formato roster, sin la "T")
    badge.title = `${count} terminal${count !== 1 ? 'es' : ''} activa${count !== 1 ? 's' : ''}`;
  } else {
    badge?.remove();
  }
}

// ─── Context menu (right-click sobre un proyecto) ─────────────────

const _sbCtxMenu = document.getElementById('sb-context-menu');

function _sbAbrirContextMenu(x, y, projId) {
  if (!_sbCtxMenu) return;
  const p = _sbProyectos.find(pr => String(pr.id) === String(projId));
  if (!p) return;

  // Iconos en chips coloreados (tones de .sb-icon) — lenguaje Obsidian Glass
  const items = [];
  if (p.seccion !== 'pinned') items.push({ ic: 'pin', tone: 'violet', label: 'Anclar (Pin)', action: 'pin' });
  else                        items.push({ ic: 'pin', tone: 'violet', label: 'Desanclar',    action: 'unpin' });

  if (p.seccion !== 'archived') items.push({ ic: 'archive', tone: 'amber', label: 'Archivar',    action: 'archive' });
  else                          items.push({ ic: 'archive', tone: 'amber', label: 'Desarchivar', action: 'unarchive' });

  items.push({ sep: true });
  items.push({ ic: 'edit', tone: 'cyan', label: 'Renombrar',   action: 'rename' });
  items.push({ ic: 'copy', tone: 'teal', label: 'Copiar ruta', action: 'copy-path' });
  items.push({ sep: true });
  items.push({ ic: 'x', tone: 'rose', label: 'Quitar del workspace', action: 'remove', danger: true });

  _sbCtxMenu.innerHTML = items.map(it => {
    if (it.sep) return '<div class="sb-ctx-sep"></div>';
    const cls = 'sb-ctx-item' + (it.danger ? ' danger' : '');
    return `<button class="${cls}" data-action="${it.action}" data-id="${esc(projId)}">` +
           `<span class="sb-ctx-ic tone-${it.tone}">${icon(it.ic, 13)}</span>` +
           `<span class="sb-ctx-lbl">${it.label}</span></button>`;
  }).join('');

  // Posicionar (clamp para no salir del viewport)
  _sbCtxMenu.hidden = false;
  const rect = _sbCtxMenu.getBoundingClientRect();
  const xx = Math.min(x, window.innerWidth  - rect.width  - 8);
  const yy = Math.min(y, window.innerHeight - rect.height - 8);
  _sbCtxMenu.style.left = `${xx}px`;
  _sbCtxMenu.style.top  = `${yy}px`;

  _sbCtxMenu.querySelectorAll('.sb-ctx-item').forEach(b => {
    b.addEventListener('click', () => {
      _sbCerrarContextMenu();
      _sbEjecutarAccion(b.dataset.action, b.dataset.id);
    });
  });
}

function _sbCerrarContextMenu() { if (_sbCtxMenu) _sbCtxMenu.hidden = true; }

document.addEventListener('click',   (e) => { if (!_sbCtxMenu?.contains(e.target)) _sbCerrarContextMenu(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') _sbCerrarContextMenu(); });


async function _sbEjecutarAccion(accion, projId) {
  const p = _sbProyectos.find(pr => String(pr.id) === String(projId));
  if (!p) return;
  try {
    if (accion === 'pin' || accion === 'unpin' || accion === 'archive' || accion === 'unarchive') {
      const nuevaSec = (accion === 'pin')       ? 'pinned'
                    : (accion === 'unpin')      ? 'active'
                    : (accion === 'archive')    ? 'archived'
                    :                              'active';
      const res = await fetch(`/api/projects/${projId}/section`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ seccion: nuevaSec }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await cargarSidebar();
    } else if (accion === 'rename') {
      const fila = elSidebarNav?.querySelector(`.sb-row[data-id="${projId}"]`);
      const nameEl = fila?.querySelector('.sb-row-name');
      if (!nameEl) return;
      const original = nameEl.textContent;
      nameEl.contentEditable = 'true';
      nameEl.focus();
      document.getSelection()?.selectAllChildren(nameEl);
      let cerrado = false;
      const onKeydown = (e) => {
        if (e.key === 'Enter')  { e.preventDefault(); terminar(true); }
        if (e.key === 'Escape') { e.preventDefault(); terminar(false); }
      };
      const terminar = async (confirmar_) => {
        if (cerrado) return;          // guard: blur tras Enter / listeners apilados
        cerrado = true;
        nameEl.removeEventListener('keydown', onKeydown);
        nameEl.contentEditable = 'false';
        const nuevo = nameEl.textContent.trim();
        if (!confirmar_ || !nuevo || nuevo === original) { nameEl.textContent = original; return; }
        try {
          const res = await fetch(`/api/projects/${projId}/rename`, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ nombre: nuevo }),
          });
          if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
          if (String(projId) === String(projectId)) {
            if (elTitulo) elTitulo.textContent = nuevo;
            document.title = `JARVIS — ${nuevo}`;
          }
          await cargarSidebar();
        } catch (err) { nameEl.textContent = original; toast(`No se pudo renombrar: ${err.message}`, 'error'); }
      };
      nameEl.addEventListener('keydown', onKeydown);
      nameEl.addEventListener('blur', () => terminar(true), { once: true });
    } else if (accion === 'copy-path') {
      try {
        await navigator.clipboard.writeText(p.ruta);
        toast('Ruta copiada al portapapeles', 'success');
      } catch { toast('Ruta: ' + p.ruta, 'info'); }
    } else if (accion === 'remove') {
      // Quitar del workspace SIN tocar la carpeta del disco
      const mensaje = _sbT('Quitar «{nombre}» del workspace.').replace('{nombre}', p.nombre) + '\n\n' +
                      _sbT('La carpeta {ruta} y todo su contenido quedan intactos en el disco.').replace('{ruta}', p.ruta) + '\n' +
                      _sbT('Podés volver a agregar el proyecto después si querés.');
      if (!(await confirmar(mensaje, { titulo: 'Quitar del workspace', confirmText: 'Quitar' }))) return;
      await _sbBorrarProyecto(projId, { keepFolder: true });
    }
  } catch (err) {
    toast(`Error: ${err.message}`, 'error');
  }
}

async function _sbBorrarProyecto(projId, opts = {}) {
  const keepFolder = opts.keepFolder === true;
  const url = `/api/projects/${projId}` + (keepFolder ? '?keep_folder=true' : '');
  const res = await fetch(url, { method: 'DELETE' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `HTTP ${res.status}`);
  }
  // Solo avisar de errores de carpeta cuando SÍ querías borrarla.
  if (!keepFolder && !data.folder_deleted && data.folder_error) {
    toast(_sbT('Proyecto quitado del workspace, pero hubo un problema con la carpeta: {err}. Podés borrarla manualmente desde WSL si querés.').replace('{err}', data.folder_error), 'warning', 7000);
  }
  // Si era el proyecto activo, mandar al primero disponible (o a home)
  if (String(projId) === String(projectId)) {
    const otros = _sbProyectos.filter(p => String(p.id) !== String(projId));
    if (otros.length > 0) {
      await cambiarProyecto(otros[0].id);
    } else {
      location.href = '/';
      return;
    }
  }
  await cargarSidebar();
}

// ─── Búsqueda en el sidebar (input + shortcuts) ───────────────────

const _sbSearchInput = document.getElementById('sb-search-input');
// Debounce del filtrado: renderSidebar() rehace innerHTML y re-cablea
// click/contextmenu/drag&drop por fila, caro de hacer en cada tecla.
let _sbSearchDeb = 0;
_sbSearchInput?.addEventListener('input', (e) => {
  _sbQuery = e.target.value;
  clearTimeout(_sbSearchDeb);
  _sbSearchDeb = setTimeout(renderSidebar, 120);
});
_sbSearchInput?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    clearTimeout(_sbSearchDeb); renderSidebar();   // forzar el filtrado pendiente: Enter actúa sobre la lista YA filtrada
    const primero = elSidebarNav?.querySelector('.sb-row');
    if (primero) cambiarProyecto(primero.dataset.id);
  } else if (e.key === 'Escape') {
    clearTimeout(_sbSearchDeb);  // cancelar render pendiente: limpiamos ya
    _sbSearchInput.value = '';
    _sbQuery = '';
    renderSidebar();
    _sbSearchInput.blur();
  }
});

// Cambio de idioma: el sidebar vive en zona i18n-skip y sus headers se traducen
// en el render (t()), así que hay que re-renderizar para que tomen el idioma nuevo.
window.addEventListener('jarvis:lang', () => renderSidebar());

// (El Historial de terminal —Ctrl+Shift+H / botón 📜 / abrirSeleccionTerminal /
//  abrirHistorialTerminal + el endpoint GET /api/terminals/{id}/history— se
//  REMOVIÓ por completo el 2026-07-05: ya se copia y scrollea directo en la
//  terminal, el modal era redundante.)


// Shortcuts globales: ⌘P palette · ⌘K / Ctrl+K = focus search · ⌘1-9 = jump al N-ésimo
// Registrado en CAPTURE para que ⌘P le gane al diálogo de impresión nativo.
document.addEventListener('keydown', (e) => {
  const mod = e.metaKey || e.ctrlKey;
  if (!mod) return;
  // ⌘P: palette de archivo SOLO si el editor está a la vista; si no, toggle del dock.
  // ⌘⇧P: palette de comandos (siempre, si el editor ya cargó).
  if ((e.key === 'p' || e.key === 'P') && !e.altKey) {
    e.preventDefault();
    e.stopImmediatePropagation();
    if (e.shiftKey && window.JarvisEditor?.openPalette) {
      window.JarvisEditor.openPalette('comando');
    } else if (window.JarvisDock?.isOpen() && window.JarvisDock.activeTab() === 'editor'
               && window.JarvisEditor?.openPalette) {
      window.JarvisEditor.openPalette('archivo');
    } else {
      window.JarvisDock?.toggle();
    }
    return;
  }
  // Atajos del Panel Único (contrato). No interferir si se tipea en un campo.
  const _tagAtajo = document.activeElement?.tagName;
  const _editando = _tagAtajo === 'INPUT' || _tagAtajo === 'TEXTAREA'
    || document.activeElement?.isContentEditable;
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey) {
    const k = e.key.toLowerCase();
    if (k === 'e' && !_editando) { e.preventDefault(); _abrirEditorWorkspace(); return; }
    if (k === 'j' && !_editando) { e.preventDefault(); window.JarvisDock?.setTab?.('jarvis'); return; }
    if (k === 't' && !_editando) { e.preventDefault(); abrirLauncher(); return; }
  }
  if (e.key === 'k' || e.key === 'K') {
    e.preventDefault();
    _sbSearchInput?.focus();
    _sbSearchInput?.select();
    return;
  }
  if (e.key >= '1' && e.key <= '9') {
    // No interferir si el foco está en un input/textarea (escribiendo)
    const tag = document.activeElement?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;
    const n = parseInt(e.key, 10);
    const fila = elSidebarNav?.querySelector(`.sb-row[data-idx="${n}"]`);
    if (fila) {
      e.preventDefault();
      cambiarProyecto(fila.dataset.id);
    }
  }
}, true);

// Esc: salir de maximizado del dock (contrato §2.4). Listener aparte porque el
// handler de ⌘P/⌘K de arriba descarta los eventos sin modificador (if (!mod) return).
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (window.JarvisDock?.isMaximized?.()) { window.JarvisDock.setMaximized(false); return; }
  }
});

// ─── Navegación entre proyectos sin recargar ──────────────────────

// Serialización de cambios de proyecto: dos cambiarProyecto SOLAPADOS (clicks
// rápidos por la franja) corrían teardown/attach de xterm a la vez → carrera
// ("dimensions" undefined) y movimiento tosco. Con la cola, el click más nuevo
// GANA: si hay un cambio en vuelo se anota como pendiente y corre al terminar.
let _cambioEnCurso  = false;
let _cambioPendiente = null;

function _sbMarcarActivo(id) {
  document.querySelectorAll('.sb-row').forEach(el =>
    el.classList.toggle('activo', String(el.dataset.id) === String(id)));
}

async function cambiarProyecto(nuevoId) {
  if (String(nuevoId) === String(projectId) && !_cambioEnCurso) return;
  if (_cambioEnCurso) {
    _cambioPendiente = nuevoId;
    _sbMarcarActivo(nuevoId);   // feedback inmediato aunque el viaje esté encolado
    return;
  }
  _cambioEnCurso = true;
  try {

  // 0. Avisar si hay tabs sin guardar (spec §7). Si el usuario cancela, abortar.
  // `?.()` devuelve undefined si el editor no está cargado → undefined !== false → se continúa.
  const _descartarOk = await window.JarvisEditor?.confirmarDescarteSiSucio?.();
  if (_descartarOk === false) { _cambioPendiente = null; _sbMarcarActivo(projectId); return; }

  // 0.5 Respuesta visual INMEDIATA (mock "El Roster"): la selección se pinta en
  // ESTE frame, antes del trabajo pesado (teardown de xterm + fetches). Sin esto
  // el .activo aterrizaba recién en el paso 5, 300-1000ms después del click →
  // el movimiento de la card se sentía tosco. El yield deja salir el paint
  // (fallback por timeout: rAF no corre en tabs ocultas); la reentrancia que
  // abre este gap la maneja la cola de arriba.
  _sbMarcarActivo(nuevoId);
  await new Promise(r => { requestAnimationFrame(() => setTimeout(r, 0)); setTimeout(r, 80); });
  if (_cambioPendiente != null) return;   // llegó un click más nuevo: este viaje se cancela

  // 1. Desconectar terminales del proyecto actual (tmux sigue vivo)
  desconectarTodasLasTerminales();

  // El preview móvil NO se cierra al cambiar de proyecto: MobilePreview.init
  // (más abajo) ESTACIONA sus teléfonos/cards con los iframes VIVOS (pool) y al
  // volver reaparecen al instante, sin reconectar (pedido 2026-07-07).

  // 2. Actualizar ID y URL sin recargar
  projectId = String(nuevoId);
  history.pushState({ projectId }, '', `/workspace?id=${projectId}`);

  // Notificar al editor del cambio de proyecto (re-apunta su _projectId y,
  // si el panel está visible, refresca el árbol del nuevo proyecto).
  window.JarvisEditor?.onProjectChanged(projectId);
  window.JarvisSlideEditor?.onProjectChanged?.(projectId);
  window.TerminalLayout?.onProjectChanged(projectId);
  window.JarvisTasks?.onProjectChanged(projectId);
  window.JarvisMemory?.onProjectChanged(projectId);
  window.JarvisReview?.onProjectChanged(projectId);
  window.JarvisSettings?.onProjectChanged(projectId);
  window.WebPreview?.onProjectChanged?.(projectId);
  // Re-apuntar el mobile preview ANTES de que el dock restaure: si restaura
  // DIRECTO a Móvil (sin flash), dispara onTabShown('mobile') → abrir(), que
  // necesita el panel apuntando a ESTE proyecto (su init real corre después en
  // consultarMobilePreview). init es idempotente: solo re-apunta + monta una vez.
  window.MobilePreview?.init?.(projectId);
  window.JarvisDock?.onProjectChanged(projectId);
  window.TerminalAura?.reset?.();  // las auras son del proyecto que dejamos
  window.AgentSemaphore?.reset?.();   // el semáforo también es del proyecto viejo
  window.SwarmOverlay?.cerrar?.();    // el grupo abierto era del proyecto viejo
  window.SwarmLink?.onProjectChanged?.(projectId);  // vínculos del proyecto nuevo

  // Re-detectar el preview del proyecto recién cargado si la pestaña está visible.
  if (window.JarvisDock && window.JarvisDock.isOpen?.() && window.JarvisDock.activeTab?.() === 'preview') {
    window.WebPreview?.init?.(document.getElementById('jw-pane-preview'));
    window.WebPreview?.detectar?.(projectId);
  }

  // 3. Restaurar historial de chat del nuevo proyecto
  _restaurarChat();

  // Reconectar canal de eventos al nuevo proyecto
  conectarEventosWs();

  // 4. Cargar el nuevo proyecto
  try {
    await cargarProyecto();
  } catch (err) {
    console.error('Error cargando proyecto:', err);
    agregarMensajeChat('jarvis', `No pude cargar el proyecto (${err.message})`);
  }

  // 5. Resaltar el nuevo activo en el sidebar y refrescar el menú de localhost
  actualizarSidebarActivo();
  actualizarSidebarBadge(terminales.size);
  window.JarvisDevServers?.cargar?.(projectId);
  consultarMobilePreview();

  } finally {
    _cambioEnCurso = false;
    const pend = _cambioPendiente; _cambioPendiente = null;
    if (pend != null && String(pend) !== String(projectId)) cambiarProyecto(pend);
  }
}

// Manejo del botón "Atrás" del browser
window.addEventListener('popstate', async e => {
  const id = new URLSearchParams(location.search).get('id');
  if (id && id !== String(projectId)) {
    // `?.()` → undefined si el editor no está cargado; undefined !== false → se continúa.
    const _popDescartarOk = await window.JarvisEditor?.confirmarDescarteSiSucio?.();
    if (_popDescartarOk === false) {
      // El usuario canceló: re-empujar la URL del proyecto actual para no dejar
      // la barra de direcciones desincronizada del estado real.
      history.pushState({ projectId }, '', `/workspace?id=${projectId}`);
      return;
    }
    // Mismo criterio que cambiarProyecto: NO cerrar el preview móvil — init
    // estaciona sus teléfonos/cards vivos (pool) y volver es instantáneo.
    desconectarTodasLasTerminales();
    projectId = id;
    window.JarvisEditor?.onProjectChanged(projectId);
    window.JarvisSlideEditor?.onProjectChanged?.(projectId);
    window.TerminalLayout?.onProjectChanged(projectId);
    window.JarvisTasks?.onProjectChanged(projectId);
    window.JarvisMemory?.onProjectChanged(projectId);
    window.JarvisReview?.onProjectChanged(projectId);
    window.JarvisSettings?.onProjectChanged(projectId);
    window.WebPreview?.onProjectChanged?.(projectId);
    window.MobilePreview?.init?.(projectId);   // re-apuntar antes del restore directo a Móvil (ver cambiarProyecto)
    window.JarvisDock?.onProjectChanged(projectId);
    window.SwarmOverlay?.cerrar?.();                  // el grupo era del proyecto viejo
    window.SwarmLink?.onProjectChanged?.(projectId);  // vínculos del proyecto nuevo
    _restaurarChat();
    await cargarProyecto();
    actualizarSidebarActivo();
    actualizarSidebarBadge(terminales.size);
  }
});

// ─── Micrófono (controlado por OrchestratorPanel vía bridges) ────

// Mantener el mic caliente una ventana corta: reusar el stream vivo evita el arranque
// en frío de getUserMedia (~1-3s en Windows/WSL) en re-holds rápidos. El primer hold lo
// abre; los siguientes (dentro de _MIC_WARM_MS) lo reusan al instante.
async function _obtenerMicStream() {
  if (_micWarmTimer) { clearTimeout(_micWarmTimer); _micWarmTimer = 0; }
  if (_micStream && _micStream.getAudioTracks().some(t => t.readyState === 'live')) {
    return _micStream;
  }
  // Constraints EXPLÍCITOS (no el {audio:true} por defecto): con echoCancellation ON,
  // Chrome enruta la captura por el pipeline "communications" de Windows (stream de
  // referencia del AEC + ducking) que monopoliza/degrada el mic para las demás apps.
  // Apagando AEC/NS/AGC la captura sale de ese modo y, de paso, no se bombea la ganancia
  // ni se recorta el arranque de las palabras (mejor dictado en WSL, mic con auriculares).
  // OJO: AGC en true SE PROBÓ el 2026-07-09 (los papers dicen que ayuda) y el usuario
  // reportó la detección "pésima" — esta nota local le gana a los estudios genéricos:
  // NO volver a prenderlo sin un A/B con su mic real.
  _micStream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
  });
  return _micStream;
}

function _liberarMicStream(forzar = false) {
  // JAMÁS matar el stream con un dictado EN CURSO (hold activo o recorder
  // grabando): el timer de la ventana caliente venciendo a mitad de un dictado
  // largo (>15s sin tocar mouse/teclado = hablar despacio) cortaba el propio
  // mic del usuario — era el "el micrófono se cortó" fantasma (2026-07-17).
  // forzar=true = ciclo de vida (pestaña oculta / pagehide / revive del mic
  // muerto): ahí sí se suelta siempre — jamás mic tomado en background.
  if (!forzar && (_controlActive['mic-ptt'] ||
                  (mediaRecorder && mediaRecorder.state === 'recording'))) {
    _programarLiberacionMic();   // reintentar después de la ventana normal
    return;
  }
  if (_micWarmTimer) { clearTimeout(_micWarmTimer); _micWarmTimer = 0; }
  if (_micStream) {
    _micStream.getTracks().forEach(t => { try { t.stop(); } catch {} });
    _micStream = null;
  }
}

// Soltar el mic tras un rato sin usarlo, así el indicador de "grabando" del
// browser/OS no queda prendido para siempre. Se rearma en cada dictado.
function _programarLiberacionMic() {
  if (_micWarmTimer) clearTimeout(_micWarmTimer);
  _micReleaseDeadline = Date.now() + _MIC_WARM_MS;
  _micWarmTimer = setTimeout(_liberarMicStream, _MIC_WARM_MS);
}

// Liberar el mic AL INSTANTE cuando el usuario salta a otra app (la pestaña se oculta)
// o descarga la página. Sin esto, la ventana caliente dejaba el dispositivo tomado hasta
// que venciera el timer aunque el foco ya estuviera en otra app — un PTT jamás debe
// retener el mic del sistema estando en segundo plano.
// (Antes acá vivía un "pre-calentamiento" que abría el mic en el 1er gesto del usuario;
// se eliminó: con la ventana caliente corta no aportaba y abría el dispositivo sin dictar.)
// Pre-calentar el mic: abrir el stream ANTES del primer PTT para que apretar y
// hablar sea instantáneo (el usuario sentía ~1s de arranque frío en el primer
// hold). Se dispara al estar el workspace visible+enfocado, y se re-arma la
// ventana caliente para no dejar el dispositivo tomado si al final no dicta.
// Best-effort: si el permiso de mic aún no se otorgó, falla silencioso y el
// primer PTT lo pide como siempre.
let _precalentarPendiente = false;
async function _precalentarMic() {
  if (_micStream || _precalentarPendiente) return;
  if (document.visibilityState !== 'visible' || !document.hasFocus()) return;
  _precalentarPendiente = true;
  try {
    await _obtenerMicStream();      // abre el dispositivo (paga el arranque acá, no en el PTT)
    // Pre-calentado sin dictar: soltarlo relativamente pronto para no dejar el
    // indicador "grabando" del sistema prendido de gusto. Un dictado real re-arma
    // la ventana completa. Si al re-precalentar sigue vivo, este timer se pisa.
    if (_micWarmTimer) clearTimeout(_micWarmTimer);
    _micReleaseDeadline = Date.now() + _MIC_PRECALENTADO_MS;
    _micWarmTimer = setTimeout(_liberarMicStream, _MIC_PRECALENTADO_MS);
  } catch { /* sin permiso todavía: el 1er PTT lo pedirá */ }
  finally { _precalentarPendiente = false; }
}

function _instalarLiberacionMicCicloVida() {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      // Dictado FIJADO con la pestaña ocultándose: commitear (release normal:
      // cola + gracia + envío) ANTES de soltar el stream — matarlo primero
      // abortaría el recorder sin procesar lo ya dictado.
      if (window.PttFijado?.alOcultarPestana({
        fijado: _pttFijado, activo: !!_controlActive['mic-ptt'],
      }) === 'enviar') {
        const c = CONTROLS.find((x) => x.id === 'mic-ptt');
        if (c) _triggerRelease(c);
      }
      _liberarMicStream(true);
    }
    else _precalentarMic();          // volvió el foco → dejar el mic listo
  });
  window.addEventListener('focus', _precalentarMic);
  window.addEventListener('pagehide', () => _liberarMicStream(true));
  // Re-armado por ACTIVIDAD (pedido 2026-07-10): cualquier interacción re-abre /
  // re-arma el mic pre-calentado, así el PTT captura desde el instante CERO
  // (getUserMedia en frío tarda ~1-3s en WSL y el audio del recorder — el que
  // usa "Dictado preciso" — perdía el arranque). Throttle de 5s: _precalentarMic
  // ya es no-op con el stream vivo, esto solo evita spamear timers. El guard de
  // visibilitychange sigue soltando el mic al saltar a otra app.
  let _ultimaActividad = 0;
  let _ultimoPrewarmModelo = 0;
  const _actividad = () => {
    const ahora = Date.now();
    if (ahora - _ultimaActividad < 5_000) return;
    _ultimaActividad = ahora;
    // El modelo del server (parakeet, camino fiel de TODO dictado ahora) se
    // mantiene tibio por actividad (throttle 60s): la carga fría era el grueso
    // de los >10s del primer dictado, y es justo lo que hace que el fiel NO
    // llegue dentro del tope de 3s. El prewarm renueva la ventana de ocio del
    // server (600s), así se descarga solo ~10 min después de que dejás de usar
    // el workspace — nunca residente de gusto.
    if (ahora - _ultimoPrewarmModelo >= 60_000) {
      _ultimoPrewarmModelo = ahora;
      try { fetch('/api/voice/prewarm', { method: 'POST' }).catch(() => {}); } catch {}
    }
    if (_micStream) {
      // Stream vivo: empujar la liberación SOLO si eso la aleja (nunca acortar
      // la ventana post-dictado de 60s a 15s por mover el mouse).
      if (ahora + _MIC_PRECALENTADO_MS > _micReleaseDeadline) {
        if (_micWarmTimer) clearTimeout(_micWarmTimer);
        _micReleaseDeadline = ahora + _MIC_PRECALENTADO_MS;
        _micWarmTimer = setTimeout(_liberarMicStream, _MIC_PRECALENTADO_MS);
      }
    } else {
      _precalentarMic();
    }
  };
  document.addEventListener('pointerdown', _actividad, { passive: true });
  document.addEventListener('keydown', _actividad, { passive: true });
  document.addEventListener('pointermove', _actividad, { passive: true });
  _precalentarMic();                 // arranque: dejar el mic listo desde el vamos
}

async function iniciarGrabacion() {
  if (window.JarvisGroqSetup?.haceFaltaClave?.()) {
    window.JarvisGroqSetup.abrir();
    return;
  }
  _micSoltado = false;
  _dictadoT0 = 0;        // reloj release→texto fresco: un commit sin release loguea ms=null, no horas
  _recRevividas = 0;
  _segmentosPrevios = [];   // segmentos de un dictado anterior no viajan a éste
  // Si la COLA del dictado ANTERIOR sigue abierta, cerrala YA (commitea ese
  // dictado antes de arrancar el nuevo) para no perderlo en un re-hold rápido.
  // ANTES de incrementar _micGen para que su commit sea válido.
  if (_tailCerrar) { try { _tailCerrar(); } catch {} }
  const gen = ++_micGen;
  // Si quedó un recorder vivo de un ciclo anterior (hold rápido encadenado),
  // frenarlo: su onstop ve la generación vieja y NO procesa.
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    try { mediaRecorder.stop(); } catch { /* ya parado */ }
  }
  // 1. Resolver target ANTES de pedir mic. Si no hay destino válido, abortar
  //    sin tocar nada (esto arregla el bug de "Jarvis oculto seguía recibiendo").
  const target = _resolveVoiceTarget();
  if (!target) {
    _cancelarHoldsActivos();
    // Dos motivos distintos: sin workspace abierto (el chat necesita
    // project_id) o con VARIAS terminales y el cursor en el vacío (con el mouse
    // encima de una, o con una sola, ya hay destino: no se llega acá).
    _toastWarn(projectId ? 'Poné el mouse sobre una terminal para hablarle'
                         : 'Abrí un workspace para hablarle a Jarvis');
    return;
  }
  _activeVoiceSession = target;
  // El campo de escucha SOLO vive en la pantalla de arranque (sin terminales):
  // con el mosaico lleno el usuario está leyendo código y la señal es la
  // píldora del PTT, no la ventana entera.
  if (_sinTerminales()) window.JarvisVoiceField?.escuchar?.();
  // Prewarm del STT (fire-and-forget): con Groq activo es un no-op instantáneo
  // (la inferencia corre en la nube); con motor local dispara la carga del
  // modelo en paralelo a la grabación, así /transcribe lo encuentra listo.
  try { fetch('/api/voice/prewarm', { method: 'POST' }).catch(() => {}); } catch {}
  // Snapshot de lo que el usuario ya escribió en el textarea de Jarvis: el
  // dictado se ANEXA a este prefijo en vez de pisarlo.
  if (target.type === 'jarvis' && !target.manosLibres) {
    _activeVoiceSession.prefijo = _panelTA()?.value || '';
  }
  _actualizarPttIndicatorParaSesion();

  let stream;
  try {
    stream = await _obtenerMicStream();
  } catch {
    if (target.type === 'jarvis' && window.JarvisDock?.activeTab?.() === 'jarvis') {
      agregarMensajeChat('jarvis', 'Sin acceso al micrófono. Revisá los permisos del browser.');
    } else {
      _toastWarn('Sin acceso al micrófono');
    }
    _activeVoiceSession = null;
    _cancelarHoldsActivos();
    return;
  }

  // El usuario soltó el PTT mientras el mic inicializaba (o ya arrancó otro
  // hold más nuevo): el recorder nunca llegó a armarse, así que no hay audio de
  // esta generación. Cerrar el ciclo igual (procesarAudio degrada al aviso de
  // "no te entendí") en vez de dejar la píldora colgada.
  if (_micSoltado || gen !== _micGen) {
    // No frenamos el mic: queda caliente para el próximo PTT. Solo la generación
    // vigente programa su liberación por inactividad; si la pisó un hold más
    // nuevo, ese hold se hace cargo del stream.
    if (gen === _micGen) {
      _programarLiberacionMic();
      _finalizarDictado(gen);
    }
    return;
  }

  _armarRecorder(stream, gen);
  // Solo cambiamos la esfera si la voz va a Jarvis. Si va a una terminal,
  // el toast de PTT es feedback suficiente y la esfera se mantiene neutral.
  if (target.type === 'jarvis') setEstado('recording');
}

// Crea y arranca el MediaRecorder + waveform sobre `stream` para la generación
// `gen`. Lo comparten el arranque normal (iniciarGrabacion) y la resurrección
// mid-hold (_revivirCapturaMidHold): mismo cableado en ambos caminos.
function _armarRecorder(stream, gen) {
  audioChunks   = [];
  _chunksGen    = gen;
  mediaRecorder = new MediaRecorder(stream);

  mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
  // Solo el recorder de la generación VIGENTE procesa; uno stale (pisado por
  // un hold más nuevo) libera el mic y muere en silencio.
  mediaRecorder.onstop = () => {
    _detenerWaveform();
    // ¿Este stop es un release real o el recorder murió SOLO con la tecla
    // apretada (track del mic caído)? Un stop espontáneo NO debe commitear ni
    // enviar (era el bug "se envió solo sin soltar", 2026-07-17): se revive la
    // captura y el commit corre recién al soltar de verdad.
    const veredicto = window.PttFijado?.alPararRecorder?.({
      genRecorder: gen, genVigente: _micGen, soltado: _micSoltado,
    }) ?? (gen === _micGen ? (_micSoltado ? 'commitear' : 'revivir') : 'nada');
    if (veredicto === 'revivir') { _revivirCapturaMidHold(gen); return; }
    // Mantener el mic caliente para el próximo PTT; soltarlo recién tras la
    // inactividad (no en cada dictado). Solo la generación vigente programa el
    // release y finaliza; un recorder stale deja el stream al hold nuevo.
    // _finalizarDictado es idempotente por gen: si el release ya abrió la ventana
    // de gracia, este onstop es no-op (no re-procesa).
    if (veredicto === 'commitear') {
      // El audio está completo → arrancar /transcribe AHORA (antes de que
      // procesarAudio lo espere). Ver _lanzarTranscripcion: junta también los
      // segmentos pre-corte si el mic murió y revivió a mitad del dictado.
      _lanzarTranscripcion(gen);
      _programarLiberacionMic();
      _finalizarDictado(gen);
    }
  };

  mediaRecorder.start();
  // Waveform reactivo al micrófono REAL (mueve el ecualizador del HUD del PTT).
  _iniciarWaveform(stream);
}

// Arma _precisoFetch para `gen` juntando los segmentos previos (recorders
// muertos mid-hold, snapshoteados por el revive) + el audio del recorder
// actual. Cada segmento va en SU PROPIO /transcribe (dos webm no se pueden
// concatenar crudos: ffmpeg solo leería el primero) y los textos se unen en
// orden — el server transcribe con Groq (~1s por parte, en paralelo) y cae a
// parakeet si falla. El AbortController es un tope anti-cuelgue generoso:
// preferimos entregar tarde antes que perder el dictado. La promesa jamás
// rechaza (siempre resuelve a un objeto) para no colgar procesarAudio.
// Devuelve true si armó fetch (hay audio usable).
function _lanzarTranscripcion(gen) {
  const partes = _segmentosPrevios.filter(b => b.size >= 1024);
  _segmentosPrevios = [];
  if (_chunksGen === gen) {
    const blob = new Blob(audioChunks, { type: 'audio/webm' });
    if (blob.size >= 1024) partes.push(blob);
  }
  if (!partes.length) return false;
  const ctrl = new AbortController();
  const abortTimer = setTimeout(() => { try { ctrl.abort(); } catch {} }, _TRANSCRIBE_TIMEOUT_MS);
  const una = (blob) => {
    const fd = new FormData();
    fd.append('audio', blob, 'audio.webm');
    return fetch('/api/voice/transcribe', { method: 'POST', body: fd, signal: ctrl.signal })
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(d => ({ texto: (d.text || '').trim(), motor: d.motor || null, llego: true }))
      .catch(() => ({ texto: '', motor: null, llego: false }));   // tope o red caída
  };
  _precisoFetch = {
    gen,
    promesa: Promise.all(partes.map(una))
      .then(rs => ({
        texto: rs.map(r => r.texto).filter(Boolean).join(' ').trim(),
        motor: rs.find(r => r.motor)?.motor || null,
        llego: rs.some(r => r.llego),
      }))
      .finally(() => clearTimeout(abortTimer)),
  };
  return true;
}

// El recorder se detuvo SOLO con la tecla aún apretada (track del mic caído:
// flapping del dispositivo en Windows, BT cambiando de perfil…). NO es un
// release: re-armar la captura sin commitear — el commit corre recién al soltar
// de verdad. El audio re-armado pierde el tramo pre-muerte: se transcribe igual
// lo capturado (dictado parcial, con el toast de "se cortó el mic" avisando).
async function _revivirCapturaMidHold(gen) {
  if (gen !== _micGen) return;
  if (_micSoltado) return;                         // carrera: el release ya corre el commit
  if (document.visibilityState === 'hidden') {
    // La página se ocultó con la tecla apretada y el blur no llegó a soltar el
    // hold: commitear como release (jamás captura viva en background). Si el
    // blur ya lo soltó, _controlActive está en false y esto es no-op.
    const c = CONTROLS.find((x) => x.id === 'mic-ptt');
    if (c && _controlActive[c.id]) _triggerRelease(c);
    return;
  }
  // Conservar el tramo YA capturado antes de la muerte (el ondataavailable
  // del recorder muerto corrió antes que este punto): al commitear, cada
  // segmento se transcribe y los textos se unen — el dictado no pierde nada.
  if (_chunksGen === gen && audioChunks.length) {
    const seg = new Blob(audioChunks, { type: 'audio/webm' });
    if (seg.size >= 1024) _segmentosPrevios.push(seg);
    audioChunks = [];
  }
  if (_recRevividas >= _MAX_REVIVIDAS_REC) { _avisoMicIrrecuperable(); return; }
  _recRevividas++;
  // Revive SILENCIOSO (pedido 2026-07-17: el toast "se cortó el mic" con la
  // reconexión andando era puro ruido — queda solo la consola para diagnóstico).
  console.warn(`[voz] el mic se cortó a mitad del dictado; reconectando (${_recRevividas}/${_MAX_REVIVIDAS_REC})`);
  try { _liberarMicStream(true); } catch {}        // soltar el stream medio muerto
  let stream = null;
  // Sin mic: el release procesará lo ya capturado (dictado parcial) — avisar.
  try { stream = await _obtenerMicStream(); } catch { _avisoMicIrrecuperable(); return; }
  // Mientras el mic re-abría pudo pasar de todo: un hold nuevo (gen vieja se
  // borra del mapa) o el release real (el commit ya corre por cola/gracia).
  if (gen !== _micGen) return;
  if (_micSoltado) { _programarLiberacionMic(); return; }
  _armarRecorder(stream, gen);
}

// El mic murió y NO se pudo reconectar: lo dictado hasta el corte se envía
// igual al soltar (segmentos), pero el resto se pierde — esto SÍ merece aviso.
// (El revive EXITOSO es silencioso desde 2026-07-17: era puro ruido.)
function _avisoMicIrrecuperable() {
  _toastWarn('El micrófono se cortó y no pude reconectarlo — lo dictado hasta el corte se envía igual. Revisá el dispositivo de entrada.');
}

// ─── Post-proceso del dictado ────────────────────────────────────
// (El SpeechRecognition del browser —preview en vivo + carrera con el server—
// se removió 2026-07-17: el dictado es 100% server (Groq + fallback local),
// mejor y más privado que el SR cloud de Google. Ver memoria stt-groq-motor.)

// Post-corrección de jerga del workspace sobre el texto dictado ("que mid" →
// commit, "yarvis" → Jarvis…); vive en shared/stt-jerga.js. Fallback inocuo
// si el módulo no cargó.
const _corregirJerga = t => window.JarvisSTT?.corregirJerga?.(t) ?? t;

// Mensaje de aviso según el diagnóstico de captura (null = sin problema).
function _avisoMic(diag) {
  if (diag === 'bajo')      return 'El micrófono se escuchó muy bajo: acercate o subí el nivel de entrada';
  if (diag === 'saturado')  return 'El micrófono satura: bajá el nivel de entrada (o el boost de Windows)';
  if (diag === 'bluetooth') return 'Auricular Bluetooth en modo llamada: el mic pierde calidad — mejor cable o USB';
  return null;
}


// ─── Cierre del dictado ──────────────────────────────────────────
// Soltaste el PTT (y la cola ya frenó la captura): procesar en el acto.
// Idempotente por gen: si lo llaman release y onstop, solo el primero procesa.
// (Antes acá vivía la "ventana de gracia" de ~650ms esperando al SR — murió
// con el SR: cierre inmediato.)
function _finalizarDictado(gen) {
  if (gen !== _micGen) return;          // pisado por un hold más nuevo
  if (_finalizandoGen === gen) return;  // ya estamos finalizando este dictado
  _finalizandoGen = gen;

  _pttProcesando();                     // orbe del HUD → spinner de "cargando"
  if (_activeVoiceSession?.type === 'jarvis') setEstado('processing');

  // (procesarAudio es idempotente por gen y re-chequea _micGen tras sus awaits,
  // así que un hold nuevo no se pisa.)
  const proc = procesarAudio(gen);
  Promise.resolve(proc).finally(() => {
    if (_finalizandoGen === gen) _finalizandoGen = -1;
    if (gen === _micGen) _lingerVoicePill();   // la píldora vuelve a idle clickeable
  });
}

// Idempotente por generación: el cierre de la ventana de gracia commitea el
// dictado y el onstop posterior del recorder ve la misma gen y NO re-procesa.
let _procesadoGen = -1;
async function procesarAudio(gen = _micGen) {
  if (_procesadoGen === gen) return;
  _procesadoGen = gen;
  // Diagnóstico de captura del dictado que terminó (métricas del waveform +
  // etiqueta del dispositivo): causas FÍSICAS de dictado malo que ningún
  // modelo arregla — mic bajo, clipping, auricular BT en modo llamada.
  const diagMic = window.JarvisSTT?.diagnosticoMic?.({
    picoDb: _micPico > 0 ? 20 * Math.log10(_micPico) : null,
    clips: _micClips,
    etiqueta: _micStream?.getAudioTracks?.()[0]?.label || '',
  }) ?? null;

  // Capturamos el target en el momento del release. Si por alguna razón se
  // perdió (race), default a jarvis SOLO si está visible.
  const session = _activeVoiceSession || (window.JarvisDock?.activeTab?.() === 'jarvis' ? { type: 'jarvis' } : null);
  _activeVoiceSession = null;

  if (!session) { _cancelarHoldsActivos(); return; }

  if (session.type === 'jarvis') setEstado('processing');

  try {
    // 1. Transcripción del server (única fuente): Groq (~1s) con fallback a
    //    parakeet local si Groq falla — todo del lado del server. El fetch ya
    //    arrancó en el onstop del recorder; acá solo esperamos su resultado
    //    {texto, motor, llego} (nunca rechaza).
    let serverTexto = '', serverMotor = null;
    if (_precisoFetch?.gen === gen) {
      const pf = _precisoFetch; _precisoFetch = null;
      const r = await pf.promesa;
      serverTexto = r?.texto || '';
      serverMotor = r?.motor || null;
    } else if (_chunksGen === gen &&
               new Blob(audioChunks, { type: 'audio/webm' }).size >= 1024) {
      // Raro: el fetch temprano no arrancó (p.ej. release sin recorder vivo).
      // Mandar ahora, con el mismo tope anti-cuelgue. Solo chunks de ESTA
      // generación: los de un dictado anterior re-transcribirían ese mensaje.
      try {
        const fd = new FormData();
        fd.append('audio', new Blob(audioChunks, { type: 'audio/webm' }), 'audio.webm');
        const ctrl = new AbortController();
        const t = setTimeout(() => { try { ctrl.abort(); } catch {} }, _TRANSCRIBE_TIMEOUT_MS);
        const res = await fetch('/api/voice/transcribe', { method: 'POST', body: fd, signal: ctrl.signal });
        clearTimeout(t);
        if (res.ok) {
          const d = await res.json();
          serverTexto = (d.text || '').trim();
          serverMotor = d.motor || null;
        }
      } catch { /* tope o red caída: queda vacío → aviso de mic */ }
    }

    const fuente = serverTexto ? (serverMotor || 'servidor') : 'vacio';
    // Post-corrección de jerga sobre el texto final del server.
    let text = _corregirJerga(serverTexto);

    // Registro LOCAL del dictado (data/dictados.log, gitignored): qué salió,
    // por qué motor y CUÁNTO tardó (release→texto, ms) — la materia prima para
    // afinar el corrector y la latencia con datos REALES.
    try {
      const ms = _dictadoT0 ? Math.round(performance.now() - _dictadoT0) : null;
      fetch('/api/voice/dictado-log', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ texto: text, fuente, conf: null, diag: diagMic, ms }),
      }).catch(() => {});
    } catch {}

    if (!text) {
      // Si la captura estuvo mal, el aviso útil es el del MIC, no "no te entendí".
      // Manos libres = el chat no está a la vista: el aviso va por toast o no lo ve nadie.
      if (session.type === 'jarvis' && !session.manosLibres) agregarMensajeChat('jarvis', _avisoMic(diagMic) || '(No te entendí, intentá de nuevo)');
      else _toastWarn(_avisoMic(diagMic) || 'No te entendí, intentá de nuevo');
      return;
    }

    // Con texto OK igual avisamos la captura degradada que recorta palabras:
    // saturación siempre; auricular BT en modo llamada una vez por sesión.
    if (diagMic === 'saturado') _toastWarn(_avisoMic('saturado'));
    else if (diagMic === 'bluetooth' && !_avisoBtMostrado) {
      _avisoBtMostrado = true;
      _toastWarn(_avisoMic('bluetooth'));
    }

    // 2. "Traducir a inglés": traducimos el TEXTO (rápido, ~0.5s) en vez de re-correr
    //    Whisper en task=translate (que en CPU tardaba 15-20s).
    if (_VOZ.traducir()) text = await _traducirAIngles(text);

    // Si durante los await (transcribe/translate) arrancó un dictado MÁS NUEVO,
    // descartá éste: no pisar el textarea ni mandar un mensaje viejo (race del
    // PTT encadenado rápido). _micGen se incrementa en cada hold nuevo.
    if (gen !== _micGen) return;

    if (session.type === 'terminal') {
      // Mandar el texto transcrito al PTY de la terminal.
      const inst = terminales.get(session.id);
      if (!inst) { _toastWarn('La terminal ya no existe'); return; }
      if (inst.ws?.readyState === WebSocket.OPEN) {
        inst.ws.send(JSON.stringify({ type: 'input', data: text }));
        inst.term?.focus();
        inst.term?.scrollToBottom();
      } else {
        _toastWarn('Terminal desconectada');
      }
      return;
    }

    // jarvis MANOS LIBRES: hablaste al aire (sin el chat a la vista). El dictado
    // NO pasa por el textarea — se manda derecho al orquestador y el dock se
    // abre en Jarvis para que veas la respuesta y lo que va haciendo. Tocar el
    // textarea acá pisaría un borrador que el usuario ni está viendo, y lo
    // auto-enviaría de arrastre.
    if (session.manosLibres) {
      window.JarvisDock?.open?.('jarvis');
      _voiceTarget = { type: 'jarvis' };   // el chat ya está a la vista: el próximo dictado sigue siendo suyo
      // Diferido a un macrotask: enviarMensaje ignora todo mientras sphereState
      // sea 'processing', y recién el finally de acá lo devuelve a 'idle'.
      setTimeout(() => enviarMensaje(text), 0);
      return;
    }

    // jarvis — anexar el dictado a lo que el usuario ya había escrito (prefijo)
    // capturado al iniciar la grabación, en vez de pisarlo.
    const ta = _panelTA();
    if (ta) {
      const pref = session.prefijo || '';
      const finalVal = pref ? pref.replace(/\s+$/, '') + ' ' + text : text;
      ta.value = finalVal;
      ta.focus();
      ta.setSelectionRange(finalVal.length, finalVal.length);
      _panel()?._autosize?.();
      _panel()?._updateSendBtn?.();
      // "Auto-enviar a Jarvis": manda el mensaje al toque, sin tener que apretar ↵.
      // Se difiere a un macrotask para que corra DESPUÉS del finally de procesarAudio
      // (que resetea sphereState a 'idle'): si no, enviarMensaje ve sphereState
      // 'processing', el guard lo descarta y _send ya vació el textarea → dictado perdido.
      if (_VOZ.autoenviar()) setTimeout(() => _panel()?._send?.(), 0);
    }
  } catch (err) {
    if (session.type === 'jarvis') agregarMensajeChat('jarvis', `Error transcribiendo: ${err.message}`);
    else _toastWarn(`Error: ${err.message}`);
  } finally {
    // Solo resetear el orbe si seguimos siendo el dictado vigente: una corrida
    // vieja (gen != _micGen) no debe pisar el estado del dictado nuevo en curso.
    if (session.type === 'jarvis' && gen === _micGen) setEstado('idle');
  }
}

// ── Traducción a inglés del dictado ───────────────────────────────
// Doble vía para que funcione SIEMPRE:
//   1. On-device (Chrome Translator API): instantáneo, sin red, sin backend nuevo
//      → anda aunque el server no se haya reiniciado todavía.
//   2. Backend /api/voice/translate (Google, ~0.5s): para browsers sin la API.
// Si las dos fallan, devuelve el español + un aviso (nunca te quedás sin texto).
let _chromeTr = null, _chromeTrFail = false, _chromeTrP = null;

function _apiTraductor() {
  // API estable (Chrome 138+): global Translator. Fallback a la experimental vieja.
  try { if (typeof Translator !== 'undefined' && Translator?.create) return { tipo: 'std', api: Translator }; } catch {}
  try { if (self.Translator?.create) return { tipo: 'std', api: self.Translator }; } catch {}
  try { if (self.translation?.createTranslator) return { tipo: 'legacy', api: self.translation }; } catch {}
  return null;
}
async function _crearTraductorChrome() {
  const t = _apiTraductor();
  if (!t) return null;
  const opts = { sourceLanguage: 'es', targetLanguage: 'en' };
  if (t.tipo === 'std') {
    const avail = await t.api.availability?.(opts);
    if (avail === 'unavailable') return null;
    return await t.api.create(opts);   // descarga el pack si hace falta
  }
  return await t.api.createTranslator(opts);
}
// Una sola promesa de creación, compartida por el preload y el primer dictado
// (evita descargar el pack dos veces si coinciden). Cachea el resultado.
function _obtenerTraductorChrome() {
  if (_chromeTrFail) return Promise.resolve(null);
  if (_chromeTr) return Promise.resolve(_chromeTr);
  if (!_chromeTrP) {
    _chromeTrP = _crearTraductorChrome()
      .then(tr => { if (tr) { _chromeTr = tr; return tr; } _chromeTrFail = true; return null; })
      .catch(() => { _chromeTrFail = true; return null; });
  }
  return _chromeTrP;
}
// Pre-descarga el modelo on-device al activar "Traducir a inglés", así el primer
// dictado ya es instantáneo (la descarga del pack corre en segundo plano).
function _precargarTraductorChrome() { _obtenerTraductorChrome(); }
async function _traducirChrome(texto) {
  try {
    const tr = await _obtenerTraductorChrome();
    if (!tr) return null;
    const out = await tr.translate(texto);
    return (out || '').trim() || null;
  } catch { _chromeTrFail = true; _chromeTr = null; return null; }
}

async function _traducirAIngles(texto) {
  // 1. On-device primero (instantáneo, sin depender del backend reiniciado).
  const local = await _traducirChrome(texto);
  if (local) return local;
  // 2. Backend (Google). Requiere el server actualizado/reiniciado.
  try {
    const res = await fetch('/api/voice/translate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: texto }),
    });
    if (res.ok) {
      const d = await res.json();
      if (d && d.text) return d.text.trim();
    }
  } catch { /* red caída */ }
  _toastWarn(_sbT('No pude traducir a inglés; va en español'));
  return texto;
}

// ─── Helpers: detección y lectura de archivos mencionados ────────

function _detectarRutasEnTexto(texto) {
  const re = /(?:^|[\s"'`(,])([./][a-zA-Z0-9_./-]{2,}|src\/[a-zA-Z0-9_./-]+|[a-zA-Z0-9_-]+\/[a-zA-Z0-9_.-]+\.[a-z]{1,6})/gm;
  const rutas = new Set();
  let m;
  while ((m = re.exec(texto)) !== null) {
    const p = m[1].trim();
    if (p.length > 3 && !p.endsWith('/')) rutas.add(p);
  }
  return [...rutas].slice(0, 5);
}

async function _leerArchivosParaContexto(rutas) {
  const partes = [];
  for (const ruta of rutas) {
    try {
      const res = await fetch(`/api/projects/${projectId}/files/read?path=${encodeURIComponent(ruta)}`);
      if (!res.ok) continue;
      const data = await res.json();
      if (data.content) {
        const ext = ruta.split('.').pop() || '';
        partes.push(`Archivo \`${ruta}\`:\n\`\`\`${ext}\n${data.content.slice(0, 3000)}\n\`\`\``);
      }
    } catch { continue; }
  }
  return partes.join('\n\n');
}

// ─── Enviar mensaje al orquestador ───────────────────────────────
// texto + opcional imagenBase64/mediaType vienen del OrchestratorPanel

async function enviarMensaje(texto, imagenBase64 = null, mediaType = null) {
  if (!texto && !imagenBase64) return;
  if (sphereState === 'recording' || sphereState === 'processing') return;

  // Recordá en qué idioma ESCRIBE el usuario → la VOZ de Jarvis le responde en
  // ESE idioma, independiente del toggle de la UI (ver _detectarIdiomaTexto).
  if (texto) window._orchLangConversacion = _detectarIdiomaTexto(texto) || window._orchLangConversacion;

  // Capturar el proyecto AL ENVIAR: la respuesta tarda y el usuario puede navegar a otro
  // proyecto mientras tanto. Todo (request, historial, render) va a ESTE pid, no al actual.
  const pid = projectId;

  // Turnos PREVIOS del thread (capturados ANTES de pushear el mensaje actual):
  // le dan memoria conversacional al orquestador. El server sanea y capea igual.
  const historial = (chatSesiones[pid] || []).slice(-12).map(m => ({
    role: m.rol === 'jarvis' ? 'assistant' : 'user',
    content: String(m.texto || '').slice(0, 4000),
  }));

  const textoMostrar = texto + (imagenBase64 ? (texto ? ' [imagen adjunta]' : '[imagen adjunta]') : '');
  agregarMensajeChat('user', textoMostrar);
  setEstado('processing');

  try {
    // Detectar rutas de archivos en el mensaje e inyectarlas como contexto
    let textoFinal = texto || '';
    if (texto) {
      const rutas = _detectarRutasEnTexto(texto);
      if (rutas.length > 0) {
        const ctx = await _leerArchivosParaContexto(rutas);
        if (ctx) textoFinal = ctx + '\n\nMensaje: ' + texto;
      }
    }

    const body = { project_id: parseInt(pid), message: textoFinal };
    if (historial.length) body.historial = historial;
    if (imagenBase64) {
      body.image_base64 = imagenBase64;
      body.media_type   = mediaType || 'image/jpeg';
    }

    // Intentar STREAMING (la respuesta aparece en vivo, token a token). Si falla
    // ANTES de empezar, fallback automático al /chat clásico → red de seguridad.
    let data;
    try {
      data = await _chatStream(body, pid);     // crea/actualiza la burbuja en vivo; devuelve el 'done'
    } catch (errStream) {
      console.warn('[orch] streaming no disponible → fallback /chat:', errStream?.message || errStream);
      data = await _chatNoStream(body);
      if (pid === projectId) agregarMensajeChat('jarvis', data.response);
      else { (chatSesiones[pid] ||= []).push({ rol: 'jarvis', texto: data.response }); }
    }

    // Acciones/terminales/workflow — SOLO si seguimos en el MISMO proyecto (si el usuario
    // navegó, el trabajo ya quedó hecho server-side para `pid` y se verá al volver allí).
    if (pid === projectId) {
      if (data.closed_all) cerrarTodasLasTerminales();
      if (data.created_terminals?.length) {
        for (const t of data.created_terminals) agregarTarjetaTerminal(t);
        actualizarVista();
      }
      if (data.workflow_card) {
        _renderWorkflowCard(data.workflow_card);
        await reproducirVoz('De acuerdo señor, me pongo a trabajar. Te aviso cuando esté listo.');
      }
    }

  } catch (err) {
    if (pid === projectId) agregarMensajeChat('jarvis', `Error: ${err.message}`);
  } finally {
    setEstado('idle');
    // Solo recuperar el foco si el usuario no está escribiendo en otro campo
    // ni metido en un iframe (la respuesta tarda segundos; robarle el foco
    // le corta el tipeo o le deselecciona el input de la app del preview).
    if (!_focoOcupadoEnOtroLado()) _panelTA()?.focus();
  }
}

// POST /chat-stream + revela el 'message' token a token. Devuelve el evento 'done'
// (response + actions + created_terminals + workflow_card), igual que /chat. Solo
// LANZA (→ fallback) si falla ANTES de emitir tokens; si ya empezó a streamear,
// finaliza con lo que haya (no duplica la burbuja).
async function _chatStream(body, pid) {
  const res = await fetch('/api/orchestrator/chat-stream', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

  const msgId = crypto.randomUUID?.() ?? String(Date.now() + Math.random());
  let burbuja = false, acumulado = '', done = null;
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = '';

  // Render solo si seguimos en el proyecto que envió (si navegó, el panel es de otro
  // proyecto → no pintar ahí; el texto se preserva en chatSesiones[pid] al cerrar).
  const mostrar = (t) => {
    if (pid !== projectId) return;
    if (!burbuja) { _renderMensaje('jarvis', t, { id: msgId }); burbuja = true; }
    else _panel()?.updateMessage(msgId, t);
  };

  for (;;) {
    let chunk;
    try { chunk = await reader.read(); }
    catch (e) { if (!burbuja) throw e; break; }   // error antes de tokens → fallback; después → cortar
    if (chunk.done) break;
    buf += dec.decode(chunk.value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const linea = buf.slice(0, i).trim();
      buf = buf.slice(i + 2);
      if (!linea.startsWith('data:')) continue;
      let ev;
      try { ev = JSON.parse(linea.slice(5).trim()); } catch { continue; }
      if (ev.type === 'token') {
        acumulado += ev.chunk;
        mostrar(acumulado);
      } else if (ev.type === 'done') {
        done = ev;
      } else if (ev.type === 'error') {
        if (!burbuja) throw new Error(ev.detail || 'stream error');   // → fallback
        done = { response: acumulado || ('⚠ ' + (ev.detail || 'Error')), actions: [],
                 created_terminals: [], closed_all: false, workflow_card: null };
      }
    }
  }

  if (!done) {
    if (!burbuja) throw new Error('stream vacío');   // → fallback
    done = { response: acumulado, actions: [], created_terminals: [], closed_all: false, workflow_card: null };
  }
  mostrar(done.response || acumulado);   // texto final autoritativo
  (chatSesiones[pid] ||= []).push({ rol: 'jarvis', texto: done.response || acumulado });
  return done;
}

async function _chatNoStream(body) {
  const res = await fetch('/api/orchestrator/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

// ─── Grid de terminales ───────────────────────────────────────────

function actualizarLayoutGrid() {
  // El template del grid lo controla el motor (window.TerminalLayout); este
  // helper queda como puente que pide un relayout/refit al motor.
  window.TerminalLayout?.relayoutAll();
}

function actualizarVista() {
  const n = terminales.size;
  if (n === 0) {
    elVacio?.classList.remove('oculto');
    elGrid?.classList.add('oculto');
  } else {
    elVacio?.classList.add('oculto');
    elGrid?.classList.remove('oculto');
  }
  actualizarLayoutGrid();
  actualizarSidebarBadge(n);
  // rAF en vez de setTimeout(50): el refit corre tras el layout real (el grid recién
  // mostrado ya está medido) ~16ms en vez de 50ms → más snappy y igual de correcto.
  requestAnimationFrame(() => terminales.forEach((_, id) => refitTerminal(id)));
}

// ─── Tarjeta de terminal ──────────────────────────────────────────

function agregarTarjetaTerminal(terminal) {
  const { id, nombre, tipo_ia } = terminal;

  const card = document.createElement('div');
  card.className = 'terminal-card';
  card.id        = `terminal-card-${id}`;
  card.innerHTML = `
    <div class="t-nebula" aria-hidden="true"><b></b><b></b><b></b></div>
    <div class="terminal-chrome">
      <span class="t-logo" id="ia-logo-${id}" data-tipo="${esc(tipo_ia)}" title="${esc(nombre)}" data-i18n-skip>
        ${window.cliLogo ? cliLogo(tipo_ia, 15) : tipo_ia}
      </span>
      <span class="t-name" id="name-${id}" data-nombre="${esc(nombre)}" title="${esc(nombre)}">${esc(nombre)}</span>
      <span class="t-drag" aria-hidden="true" title="Arrastrá para mover"></span>
      <span class="t-status t-status-idle" id="status-${id}" aria-hidden="true">
        <span class="t-status-pip"></span>
      </span>
      <button class="t-btn t-btn-max" data-id="${id}" title="Maximizar" aria-label="Maximizar">
        <svg class="t-icon-max" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M4 9V5.5A1.5 1.5 0 0 1 5.5 4H9"/>
          <path d="M15 4h3.5A1.5 1.5 0 0 1 20 5.5V9"/>
          <path d="M20 15v3.5a1.5 1.5 0 0 1-1.5 1.5H15"/>
          <path d="M9 20H5.5A1.5 1.5 0 0 1 4 18.5V15"/>
        </svg>
        <svg class="t-icon-restore" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M7.5 4.5V7.5H4.5"/>
          <path d="M16.5 4.5V7.5H19.5"/>
          <path d="M7.5 19.5V16.5H4.5"/>
          <path d="M16.5 19.5V16.5H19.5"/>
        </svg>
      </button>
      <button class="t-btn t-btn-close" data-id="${id}" title="Eliminar terminal (mata el agente)" aria-label="Eliminar terminal">
        ${icon('x', 14)}
      </button>
    </div>
    <div class="terminal-body" id="term-body-${id}"></div>
  `;

  if (!elGrid) return;
  elGrid.appendChild(card);
  crearTerminal(`term-body-${id}`, id, tipo_ia);
  window.TerminalLayout?.add(id);

  // El nombre de la card es de SOLO LECTURA: al hacer hover, el atributo `title`
  // muestra el nombre completo (útil cuando la card angosta lo trunca con "…").
  // Se quitó la edición inline a pedido del usuario — antes el hover mostraba
  // "Click para editar nombre" en vez del nombre real. Además, los nombres de
  // terminal son la identidad de coordinación del swarm (mailbox / Agents Live),
  // así que renombrarlos al vuelo era riesgoso.

  // Botones del chrome: max → toggle; close → eliminar. (El botón Historial se
  // removió el 2026-07-05: se copia y scrollea directo en la terminal. El menú
  // ⋯ de ajustes se fue con el subsistema de shells de la era Windows: con
  // tmux el shell es el del login y no había nada que configurar.)
  card.querySelector('.t-btn-max')?.addEventListener('click', () => toggleMaximizarTerminal(id));
  card.querySelector('.t-btn-close')?.addEventListener('click', async () => {
    // ✕ = ELIMINAR de verdad: DELETE → mata la sesión tmux/agente, marca activa=0
    // en la DB y libera el cupo. Antes solo DESCONECTABA (la card desaparecía pero
    // la terminal seguía activa=1) → seguía contando para el máximo y reaparecía
    // en F5: el usuario "sacaba" terminales que volvían solas. eliminarTerminal()
    // ya hace desconectar + DELETE + quitar card + relayout + actualizarVista.
    // Guard anti-misclick (2026-07-02): SOLO si agent_watch la vio trabajando
    // hace un momento se pide confirmación — un click errado sobre un agente a
    // mitad de tarea perdía todo sin preguntar. En idle: cero fricción, como siempre.
    if (_faseTerminales[id] === 'trabajando') {
      const nom = document.getElementById(`name-${id}`)?.dataset.nombre || `Terminal ${id}`;
      const ok = await confirmar(
        _sbT('{nombre} está trabajando ahora mismo. Eliminarla mata al agente y su trabajo sin commitear.').replace('{nombre}', nom),
        { titulo: _sbT('¿Eliminar la terminal?'), confirmText: 'Eliminar', peligro: true },
      );
      if (!ok) return;
    }
    eliminarTerminal(id);
  });
}

// (El modal de Historial de terminal —abrirHistorialTerminal, capturaba el pane
//  vía GET /api/terminals/{id}/history y lo mostraba en un textarea seleccionable—
//  se removió el 2026-07-05. Junto con él ya se había ido el overlay de
//  conversación de claude (abrirConversacionSeleccionable, 2026-07-03).)

// Maximización de una terminal: oculta las demás y la card elegida ocupa todo el grid.
// Estado a nivel módulo: solo una terminal puede estar maximizada a la vez.
let terminalMaximizadaId = null;

function toggleMaximizarTerminal(id) {
  const card = document.getElementById(`terminal-card-${id}`);
  if (!card) return;

  if (terminalMaximizadaId === id) {
    window.TerminalLayout?.restore(id);
    terminalMaximizadaId = null;
    // Salir de pantalla completa: el dock vuelve a su ancho normal de antes.
    window.JarvisDock?.exitAgentFullscreen?.();
  } else {
    window.TerminalLayout?.maximize(id);
    terminalMaximizadaId = id;
    // Pantalla completa de este agente: el dock toma el ancho que dejaste la
    // última vez para ÉL (si lo ensanchaste ahí), o queda como estaba.
    window.JarvisDock?.enterAgentFullscreen?.(id);
    // Si ESTE agente tiene un localhost vivo (dev server o demo/mockup), salta
    // al Web Preview con esa URL — contraparte del "panel cerrado se queda
    // cerrado" ante dev_server_detectado (pedido 2026-07-11). Al restaurar la
    // card, exitAgentFullscreen devuelve el dock a como estaba.
    _saltarLocalhostDeTerminal(id, () => terminalMaximizadaId === id);
  }

  _sincronizarBotonesMax();
  // El fit lo hace el motor (maximize/restore → _refit forzado e inmediato).
  // No agregamos otro setTimeout acá: evitar fits compitiendo = sin glitch.
}

// El preview SIGUE al agente: si ESA terminal tiene un localhost vivo (el más
// reciente que levantó — dev server o demo/mockup), abrir el Web Preview sobre
// esa URL. Lo llaman maximizar la card Y seleccionarla en la vista global con
// el panel abierto. La fuente es el endpoint POR TERMINAL (busca en el
// snapshot vivo y, si no está — reinicio del server o URL que scrolleó —, en
// el scrollback completo del pane con chequeo de liveness). revealTab abre el
// dock SIN crear override por-agente ni tocar la base (salto programático, no
// gesto del usuario); abrirLink reusa la pestaña de esa dirección y la
// RECARGA (pedido 2026-07-11: redirigir + refresh para ver los cambios del
// agente). `vigente` deja al llamador invalidar el salto si su condición
// caducó durante el fetch.
async function _saltarLocalhostDeTerminal(id, vigente) {
  try {
    const r = await fetch(`/api/orchestrator/preview/${projectId}/terminal/${id}/localhost`);
    if (!r.ok) return;
    const s = await r.json();
    if (!s?.url || (vigente && !vigente())) return;
    window.JarvisDock?.revealTab?.('preview');
    const pane = document.getElementById('jw-pane-preview');
    window.WebPreview?.init?.(pane);
    window.WebPreview?.abrirLink?.(s.url);
  } catch { /* server reiniciando: la selección/maximizar sigue sin el salto */ }
}

// Selección de terminal en la vista global (mosaico): con el panel ABIERTO, el
// preview sigue al agente seleccionado — clickear la terminal de un agente que
// tiene localhost vivo lo muestra en el panel (pedido 2026-07-11). Con el
// panel CERRADO no se abre nada (regla "cerrado se queda cerrado"); en
// pantalla completa el salto ya lo hace toggleMaximizarTerminal. Listener en
// document: corre DESPUÉS del click-handler de terminal.js (que marca la card
// `.activa`), así la selección ya está aplicada al decidir.
document.addEventListener('click', (e) => {
  if (e.target.closest?.('.t-btn, button, a')) return;  // botones de la card no son "selección"
  const card = e.target.closest?.('.terminal-card');
  if (!card || !card.classList.contains('activa')) return;
  if (terminalMaximizadaId != null) return;
  if (!window.JarvisDock?.isOpen?.()) return;
  const id = parseInt(card.id.replace('terminal-card-', ''), 10);
  if (!Number.isFinite(id)) return;
  _saltarLocalhostDeTerminal(id, () =>
    terminalMaximizadaId == null
    && window.JarvisDock?.isOpen?.()
    && document.querySelector('.terminal-card.activa')?.id === `terminal-card-${id}`);
});

// Sincroniza tooltip/aria/clase de todos los botones de maximizar con
// terminalMaximizadaId. Se llama desde toggle y también al eliminar/desconectar
// terminales, para que un botón no quede con aria-label='Restaurar tamaño'
// sobre una card que ya no está maximizada.
function _sincronizarBotonesMax() {
  document.querySelectorAll('.t-btn-max').forEach(btn => {
    const btnId = parseInt(btn.dataset.id, 10);
    const activa = btnId === terminalMaximizadaId;
    btn.classList.toggle('activa', activa);
    btn.title = activa ? 'Restaurar tamaño' : 'Maximizar';
    btn.setAttribute('aria-label', activa ? 'Restaurar tamaño' : 'Maximizar');
  });
}

// ─── Gestión de terminales ────────────────────────────────────────

// Mata tmux completamente (los archivos del proyecto en main se mantienen)
// Pip de estado de la card de terminal (CSS en base.css: .t-status-*).
// Estados: 'idle' | 'thinking' (violeta respira) | 'watching' (verde) | 'error' (rojo)
// Nombre visible de una terminal (para notificaciones del SO).
function _nombreTerm(id) {
  return document.getElementById(`name-${id}`)?.textContent?.trim() || `Terminal ${id}`;
}

function setTerminalStatus(terminalId, estado) {
  window.AgentSemaphore?.set?.(terminalId, estado);   // semáforo de la barra
  const wrap = document.getElementById(`status-${terminalId}`);
  if (!wrap) return;
  wrap.classList.remove('t-status-idle', 't-status-thinking', 't-status-watching', 't-status-error');
  wrap.classList.add(`t-status-${estado}`);
  wrap.setAttribute('aria-label', estado);
}
window.setTerminalStatus = setTerminalStatus;

async function eliminarTerminal(id) {
  if (terminalMaximizadaId === id) { terminalMaximizadaId = null; _sincronizarBotonesMax(); }
  // Olvidar el ancho de dock recordado para este agente (y salir de su contexto
  // de pantalla completa si estaba activo).
  window.JarvisDock?.forgetAgentFullscreen?.(id);
  window.TerminalAura?.apagar?.(id);
  window.AgentSemaphore?.quitar?.(id);
  desconectarTerminal(id);
  try { await fetch(`/api/terminals/${id}`, { method: 'DELETE' }); }
  catch (err) { console.error('Error eliminando terminal:', err); }
  document.getElementById(`terminal-card-${id}`)?.remove();
  window.TerminalLayout?.remove(id);
  actualizarVista();
}

// Desconecta todos los WS sin matar tmux — para cambio de proyecto
function desconectarTodasLasTerminales() {
  terminalMaximizadaId = null;
  // Salir del contexto de pantalla completa (sin olvidar los anchos: las
  // terminales siguen vivas al cambiar de proyecto y podés volver a maximizarlas).
  window.JarvisDock?.exitAgentFullscreen?.();
  _sincronizarBotonesMax();  // botones que quedaran 'activa' antes del remove
  [...terminales.keys()].forEach(id => {
    desconectarTerminal(id);
    document.getElementById(`terminal-card-${id}`)?.remove();
  });
  actualizarVista();
}

// Elimina todas las terminales (kill tmux) — para "cerrar todo" del orquestador
function cerrarTodasLasTerminales() {
  [...terminales.keys()].forEach(id => eliminarTerminal(id));
}

async function patchTerminal(id, datos) {
  try {
    await fetch(`/api/terminals/${id}`, {
      method:  'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(datos),
    });
  } catch (err) { console.error('Error actualizando terminal:', err); }
}

// ─── Títulos vivos: la card dice QUÉ está haciendo el agente ──────
// Claude Code (y toda CLI que publique OSC title) escribe en el título del
// pane de tmux un resumen corto de su tarea actual. El backend lo sirve ya
// limpio y en batch (GET /api/projects/{id}/terminal-titles, una pasada de
// tmux). Mientras hay título, .t-name lo muestra tal cual; cuando la CLI no
// publica nada (shell pelado) vuelve el nombre de la DB. En ambos casos es de
// SOLO LECTURA y el hover (title) muestra el texto completo. Sin IA, sin "…".
// Con la UI en inglés el título se muestra TRADUCIDO (JarvisTitulosI18n:
// Google gratis + cache; el pane sigue intacto y en es no se toca nada).
async function _actualizarTitulosVivos() {
  // Sin proyecto, pestaña oculta o sin terminales: no gastar un fetch que no
  // tiene nada que actualizar (el camino rápido lo cubre _pingTitulos por WS).
  if (!projectId || document.hidden || terminales.size === 0) return;
  let data;
  try {
    const r = await fetch(`/api/projects/${projectId}/terminal-titles`);
    if (!r.ok) return;
    data = await r.json();
  } catch (_) { return; }  // server reiniciando: el próximo tick reintenta
  for (const [tid, titulo] of Object.entries(data.titles || {})) {
    const el = document.getElementById(`name-${tid}`);
    if (!el || document.activeElement === el) continue;  // no pisar edición en curso
    if (titulo) {
      const visible = window.JarvisTitulosI18n ? JarvisTitulosI18n.mostrar(titulo) : titulo;
      if (el.textContent !== visible) el.textContent = visible;
      el.title = visible;                // hover = título vivo completo
      el.classList.add('t-name-live');
    } else if (el.classList.contains('t-name-live')) {
      // El agente dejó de publicar título → restaurar el nombre real de la DB.
      el.classList.remove('t-name-live');
      el.textContent = el.dataset.nombre || el.textContent;
      el.title = el.textContent;         // hover = nombre completo
    }
  }
  // Con lang=en, traducir en background lo que falte (gratis + cacheado;
  // single-flight) y repintar vía el ping debounced. En es: no-op total.
  window.JarvisTitulosI18n?.pedir(Object.values(data.titles || {}), _pingTitulos);
  // Estado 'trabajando' EN VIVO (nivel): el brillo Liquid Glass del chrome se
  // anima SOLO en las que trabajan. Este poll repara lo que el WS
  // agente_trabajando (edge-triggered) no cubre — p.ej. las que YA venían
  // trabajando cuando cargó/recargó la página (nunca reciben la transición).
  const _trab = new Set((data.trabajando || []).map(String));
  for (const tid of terminales.keys()) {
    document.getElementById(`terminal-card-${tid}`)?.classList.toggle('t-trabajando', _trab.has(String(tid)));
  }
}
// Poll de red de seguridad (cross-evento), pero el feedback rápido lo da _pingTitulos()
// disparado por los eventos WS de actividad del agente (abajo). Sin esperar hasta 5s.
setInterval(_actualizarTitulosVivos, 3000);
// Cambio de idioma en ⚙ → repintar los títulos YA en el idioma nuevo (sin
// esperar al poll); el pedir() del update traduce lo que falte si pasó a en.
window.addEventListener('jarvis:lang', () => _pingTitulos());
let _titDeb = 0;
// Cada actividad de agente (los 3 call sites son los handlers agente_*) refresca
// títulos Y reconcilia el anillo del orbe → reparación rápida del proyecto activo
// además del poll de 3s y del edge `proyecto_trabajo`.
function _pingTitulos() { clearTimeout(_titDeb); _titDeb = setTimeout(_actualizarTitulosVivos, 200); _pingAnillos(); }

// ─── Anillo verde del orbe: reconciliación por NIVEL (self-healing) ───
// El anillo (= "este workspace tiene agentes trabajando") se maneja por NIVEL,
// igual que el brillo Liquid Glass de las cards: cada 3s (+ debounced tras
// eventos de agente) se consulta el estado REAL de TODOS los proyectos y se
// re-aplica a cada fila. Así un evento `proyecto_trabajo` perdido (hiccup/
// reconexión de WS) o un re-render que resetee la clase se AUTO-REPARAN en ≤3s
// — el anillo NUNCA se queda trabado en OFF mientras el agente sigue trabajando.
// El evento `proyecto_trabajo` (edge) sigue dando el encendido/apagado instantáneo;
// este poll es la red de seguridad que lo hace confiable al pie de la letra.
async function _reconciliarAnillos() {
  if (document.hidden) return;
  let ids, counts;
  try {
    const r = await fetch('/api/projects/working');
    if (!r.ok) return;   // backend viejo (404) / server reiniciando → reintenta luego
    const data = await r.json();
    ids = new Set((data.ids || []).map(String));
    counts = data.counts || null;   // null = backend viejo: no tocar los contadores
  } catch { return; }
  for (const p of _sbProyectos) _sbAplicarTrabajo(p.id, ids.has(String(p.id)));
  // Contador de terminales de los OTROS workspaces (mismo NIVEL, mismo poll):
  // sin esto se congelaba en el valor del último GET /api/projects — nada avisa
  // cuando otro workspace abre o cierra terminales, así que quedaba invisible
  // (o mentiroso) mientras el usuario no recargara. El proyecto ACTIVO queda
  // afuera a propósito: ahí manda `terminales.size` (verdad local, instantánea).
  if (counts) for (const p of _sbProyectos) {
    if (String(p.id) === String(projectId)) continue;
    const n = counts[String(p.id)] || 0;
    p.terminales_activas = n;    // el cache también, para que un re-render no revierta
    _sbPintarBadge(_sbRowById(p.id), n);
  }
  // Persistir qué fichas están grandes AHORA (señal + gracias en curso): el
  // próximo page load (reload / update del server) arranca de acá, no de cero.
  // Recién DESPUÉS del overlay del primer fetch — si no, un tick temprano con la
  // señal fría pisaría la memoria de la página anterior antes de usarla.
  if (cargarSidebar._overlayHecho) {
    const grandes = new Set([...ids]);
    for (const k of _sbRepliegueTimers.keys()) grandes.add(k);
    _sbPersistirFichas(grandes);
  }
}
setInterval(_reconciliarAnillos, 3000);
let _anillosDeb = 0;
function _pingAnillos() { clearTimeout(_anillosDeb); _anillosDeb = setTimeout(_reconciliarAnillos, 250); }

// ─── Modal: nuevas terminales (creación en lote) ──────────────────

// ═══ LAUNCHER — modal "Agregar proyecto": modos Crear/Abrir + grid de CLIs ═══
// Lógica pura en launcher-state.js (JarvisLauncherState); render aquí.
// Persistencia: jarvis.launcher.rutas (últimas rutas lanzadas) en localStorage.
// (La UI de templates del launcher viejo se retiró con el rediseño 2026-07-10.)

// The full shape is defined by CLI_ORDEN (countsIniciales); this literal only
// exists for the first parse (launcher-state.js loads later). All zero: the
// launcher does NOT preselect agents, the user picks them.
const _tlCounts = { claude: 0, codex: 0, opencode: 0, qwen: 0, antigravity: 0, grok: 0, cursor: 0, pi: 0, manual: 0 };
const _TL_RUTAS_KEY = 'jarvis.launcher.rutas';

function _tlLeerRutas() {
  let raw = null;
  try { raw = JSON.parse(localStorage.getItem(_TL_RUTAS_KEY) || '[]'); } catch {}
  return Array.isArray(raw) ? raw.filter(r => typeof r === 'string' && r).slice(0, 4) : [];
}
function _tlRecordarRuta(ruta) {
  if (!ruta) return;
  const rutas = [ruta, ..._tlLeerRutas().filter(r => r !== ruta)].slice(0, 4);
  try { localStorage.setItem(_TL_RUTAS_KEY, JSON.stringify(rutas)); } catch {}
}

// Cambia un contador y sincroniza la UI en el lugar (sin re-render del grid,
// para no reiniciar la animación de entrada de las cards).
function _tlSetCount(tipo, n) {
  // CLI sin instalar no se puede sumar (el estado viene cacheado del arranque).
  const st = (_tlEstadoClis?.clis || []).find(c => c.id === tipo);
  if (st && !st.instalado) return;
  _tlCounts[tipo] = window.JarvisLauncherState.clampContador(n, terminales.size, _tlCounts, tipo);
  _tlSync();
}

// ── Grid COMPACTO de cards por CLI (estilo prototipo) ──
const _TL_CLI_SUB = { claude: 'anthropic', codex: 'openai', opencode: 'sst', qwen: 'alibaba', antigravity: 'google', grok: 'xai', cursor: 'anysphere', pi: 'earendil', manual: 'bash' };
let _tlEstadoClis = null;   // GET /api/clis → qué CLIs faltan instalar (null = no lo sé aún)

function _tlPintarFalta() {
  document.querySelectorAll('#tl-grid .tl2-cli-card').forEach(card => {
    const tipo = card.dataset.tipo;
    if (tipo === 'manual') return;               // el shell no se instala
    const st = (_tlEstadoClis?.clis || []).find(c => c.id === tipo);
    const falta = !!st && !st.instalado;
    card.classList.toggle('off', falta);
    const chip = card.querySelector('.tl2-cli-falta');
    if (chip) chip.hidden = !falta;
    // sin instalar no se puede sumar: los botones mueren
    card.querySelectorAll('.inc').forEach(b => { b.disabled = falta; });
  });
}

function _tlEstadoFaltantes() {
  // Hidratación inmediata desde la última detección guardada: el aviso "Falta
  // instalar" sale YA al abrir el picker/launcher, sin esperar a la red (la
  // detección mira el PATH y en WSL tarda segundos). Después se refresca.
  if (!_tlEstadoClis) {
    try {
      const prev = JSON.parse(localStorage.getItem('jarvis_clis_estado') || 'null');
      if (prev && Array.isArray(prev.clis)) {
        _tlEstadoClis = prev;
        window.JarvisClisEstado = prev;        // cache compartida con QuickPicker
        _tlPintarFalta();
      }
    } catch { /* localStorage roto o JSON inválido: seguimos por red */ }
  }
  fetch('/api/clis')
    .then(r => (r.ok ? r.json() : null))
    .then(d => {
      if (!d) return;
      _tlEstadoClis = d;
      window.JarvisClisEstado = d;             // cache compartida con QuickPicker
      try { localStorage.setItem('jarvis_clis_estado', JSON.stringify(d)); } catch { /* cuota/privacidad */ }
      _tlPintarFalta();
    })
    .catch(() => {});
}

function _tlRenderGrid() {
  const L = window.JarvisLauncherState;
  const cont = document.getElementById('tl-grid');
  if (!cont || !L) return;
  cont.innerHTML = L.CLI_ORDEN.map(tipo => `
    <div class="tl2-cli-card" data-tipo="${tipo}">
      <span class="tl2-cli-badge">${window.cliLogo ? cliLogo(tipo, 18) : tipo}</span>
      <span class="tl2-cli-info"><span class="tl2-cli-name">${L.CLI_LABELS[tipo]}</span><span class="tl2-cli-row"><span class="tl2-cli-sub">${_TL_CLI_SUB[tipo] || ''}</span>${tipo !== 'manual' ? '<i class="tl2-cli-falta" hidden>Falta instalar</i>' : ''}</span></span>
      <span class="tl2-cli-step">
        <button type="button" class="dec" aria-label="Menos ${L.CLI_LABELS[tipo]}">−</button>
        <span class="cnt">0</span>
        <button type="button" class="inc" aria-label="Más ${L.CLI_LABELS[tipo]}">+</button>
      </span>
    </div>`).join('');
  cont.querySelectorAll('.tl2-cli-card').forEach(card => {
    const tipo = card.dataset.tipo;
    card.querySelector('.inc').addEventListener('click', () => _tlSetCount(tipo, _tlCounts[tipo] + 1));
    card.querySelector('.dec').addEventListener('click', () => _tlSetCount(tipo, _tlCounts[tipo] - 1));
  });
  _tlPintarFalta();
}

// ── Sincroniza contadores, badges, capacidad y CTA (en el lugar) ──
function _tlSync() {
  const L = window.JarvisLauncherState;
  const total = L.totalContadores(_tlCounts);
  const lleno = (terminales.size + total) >= L.MAX_TERMINALES;
  document.querySelectorAll('#tl-grid .tl2-cli-card').forEach(card => {
    const n = _tlCounts[card.dataset.tipo] | 0;
    card.classList.toggle('active', n > 0);
    card.querySelector('.cnt').textContent = n;
    card.querySelector('.dec').disabled = n === 0;
    card.querySelector('.inc').disabled = lleno || card.classList.contains('off');
  });
  _tlSyncCTA();

  // Capacidad: MAX_TERMINALES segmentos — ocupadas + seleccionadas + libres.
  // Al llegar al tope aparece el porqué de los + apagados ("límite alcanzado").
  const cap = document.getElementById('tl-capacidad');
  if (cap) {
    const usadas = Math.min(terminales.size, L.MAX_TERMINALES);
    const sel = Math.min(L.totalContadores(_tlCounts), L.MAX_TERMINALES - usadas);
    const segs = Array.from({ length: L.MAX_TERMINALES }, (_, i) =>
      `<i class="${i < usadas ? 'u' : (i < usadas + sel ? 's' : '')}"></i>`).join('');
    cap.innerHTML = `<span class="tl-cap-segs">${segs}</span><span class="tl-cap-n">${usadas + sel}/${L.MAX_TERMINALES}</span>`
      + (lleno ? '<span class="tl-cap-lleno">límite alcanzado</span>' : '');
    cap.title = _sbT('{n} en uso · {m} a crear · {k} libres').replace('{n}', usadas).replace('{m}', sel).replace('{k}', L.MAX_TERMINALES - usadas - sel);
  }
  _tlRenderDist();
  _tlSyncScrollHint();
}

// CTA del footer según modo: "Crear…" o "Abrir…", y en Abrir se deshabilita
// sin carpeta elegida (antes actuaba silencioso sobre el proyecto ACTUAL —
// modelo mental roto: el modal se llama "Agregar proyecto").
let _tlLanzando = false;
function _tlSyncCTA() {
  const L = window.JarvisLauncherState;
  const btn = document.getElementById('btn-create-terminal');
  const lbl = document.getElementById('tl-launch-label');
  if (!btn || !lbl || !L) return;
  if (_tlMode === 'open') {
    lbl.textContent = L.etiquetaAbrir(_tlCounts);
    btn.disabled = _tlLanzando || !(document.getElementById('tl-folder-input')?.value || '').trim();
  } else {
    lbl.textContent = L.etiquetaCrear(_tlCounts);
    btn.disabled = _tlLanzando;
  }
}

// Affordance de scroll del body (viewport bajo): fade abajo mientras quede
// contenido por scrollear (clase .tl2-more en launcher-plus.css).
function _tlSyncScrollHint() {
  const b = document.querySelector('#modal-new-terminal .tl2-body');
  if (b) b.classList.toggle('tl2-more', b.scrollHeight - b.clientHeight - b.scrollTop > 4);
}
document.querySelector('#modal-new-terminal .tl2-body')?.addEventListener('scroll', _tlSyncScrollHint, { passive: true });
window.addEventListener('resize', () => {
  if (modalTerminal && modalTerminal.style.display !== 'none') _tlSyncScrollHint();
});

// ── Rutas recientes (chips bajo el input) ──
function _tlRenderRutas() {
  const cont = document.getElementById('tl-rutas-recientes');
  const input = document.getElementById('tl-folder-input');
  if (!cont) return;
  // Recientes = rutas que YA usaste (localStorage), EXCLUYENDO las que hoy son un
  // proyecto agregado: nunca re-ofrecer algo que ya está en el workspace. Al quitar
  // el proyecto, su ruta vuelve a aparecer acá para poder agregarlo de nuevo.
  const yaProyecto = new Set((_sbProyectos || []).map(p => (p.ruta || '').replace(/\/+$/, '')).filter(Boolean));
  const rutas = _tlLeerRutas().filter(r => r && !yaProyecto.has(r.replace(/\/+$/, '')));
  cont.hidden = rutas.length === 0;
  // Sin rutas se oculta el BLOQUE entero: el label "Recientes" suelto confundía.
  const wrap = cont.closest('.tl2-recents'); if (wrap) wrap.hidden = rutas.length === 0;
  cont.innerHTML = rutas.map(r => {
    const base = r.split('/').filter(Boolean).pop() || r;
    return `<button type="button" class="tl-ruta-chip" data-ruta="${esc(r)}" title="${esc(r)}">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 6h6l2 2h10v10a2 2 0 0 1-2 2H3z"/></svg>${esc(base)}</button>`;
  }).join('');
  cont.querySelectorAll('.tl-ruta-chip').forEach(ch => ch.addEventListener('click', () => {
    if (input) { input.value = ch.dataset.ruta; input.focus(); }
    _tlSyncPick();
  }));
}

// ── Distribución (mosaico/vertical): preview animado + setDist post-batch ──
// Replica los layouts reales de terminal-layout.js: MOSAICO = balancedGrid
// (cols=ceil(√N), última fila estira); VERTICAL = tile2col (columnas, máx 2 filas).
let _tlDist = 'mosaico';
let _tlDistCells = [];
function _tlLayoutRects(mode, n) {
  const out = [];
  if (mode === 'vertical') {
    const perRow = 6, MAXA = 3;
    const abajo = n <= perRow ? 0 : Math.min(MAXA, n - perRow);
    const counts = abajo ? [n - abajo, abajo] : [n];
    const h = 1 / counts.length;
    counts.forEach((cnt, r) => { const w = 1 / cnt; for (let c = 0; c < cnt; c++) out.push({ x: c * w, y: r * h, w, h }); });
  } else {
    const cols = Math.ceil(Math.sqrt(n)), nRows = Math.ceil(n / cols); let i = 0;
    for (let r = 0; r < nRows; r++) {
      const remaining = n - i, count = (r === nRows - 1) ? remaining : Math.min(cols, remaining);
      const h = 1 / nRows, y = r / nRows;
      for (let c = 0; c < count; c++) { out.push({ x: c * (1 / count), y, w: 1 / count, h }); i++; }
    }
  }
  return out;
}
function _tlRenderDist() {
  const cont = document.getElementById('tl-dist');
  if (!cont) return;
  const L = window.JarvisLauncherState;
  const list = L.loteDesdeContadores(_tlCounts, 0).map(x => x.tipo_ia);   // las que se crearán
  const n = list.length;
  const nEl = document.getElementById('tl-dist-n'); if (nEl) nEl.textContent = n;
  cont.querySelector('.tl2-dist-empty')?.remove();
  if (!n) { _tlDistCells.forEach(e => e.remove()); _tlDistCells = []; cont.innerHTML = '<div class="tl2-dist-empty">Elegí al menos una terminal</div>'; return; }
  const rects = _tlLayoutRects(_tlDist, n);
  while (_tlDistCells.length < n) {
    const el = document.createElement('div'); el.className = 'tl2-cell'; el.innerHTML = '<div class="p"></div>';
    cont.appendChild(el); _tlDistCells.push(el);
    const cell = el; requestAnimationFrame(() => requestAnimationFrame(() => cell.classList.add('in')));
  }
  while (_tlDistCells.length > n) _tlDistCells.pop().remove();
  _tlDistCells.forEach((el, i) => {
    const r = rects[i];
    el.style.left = (r.x * 100) + '%'; el.style.top = (r.y * 100) + '%';
    el.style.width = (r.w * 100) + '%'; el.style.height = (r.h * 100) + '%';
    const p = el.firstElementChild;
    if (p.dataset.cli !== list[i]) { p.innerHTML = window.cliLogo ? cliLogo(list[i], 16) : list[i]; p.dataset.cli = list[i]; }
  });
}
function _tlSetDist(mode) {
  _tlDist = mode;
  document.querySelectorAll('#tl-dist-modes button').forEach(b => b.setAttribute('aria-checked', String(b.dataset.dist === mode)));
  _tlRenderDist();
}
function _tlAplicarDist() {
  try {
    const total = terminales.size;
    if (!total) return;
    // vertical = auto (tile2col: columnas / máx 2 filas) · mosaico = r filas balanceadas
    const r = Math.max(1, Math.ceil(total / Math.ceil(Math.sqrt(total))));
    window.TerminalLayout?.setDist?.(_tlDist === 'vertical' ? null : { k: 'rows', r });
  } catch {}
}
document.querySelectorAll('#tl-dist-modes button').forEach(b => b.addEventListener('click', () => _tlSetDist(b.dataset.dist)));

// ── Explorador de carpetas de la PC (GET /api/fs/list) ──
let _tlFsPath = '/home/user', _tlFsParent = null, _tlFsTarget = 'path';
// ── Rutas base de ESTA máquina (hogar del usuario, no el de Jarvis) ──
// El front NO hardcodea /home/user de la máquina del autor: se lo pide al
// backend (expanduser) y siembra con eso. Fallback final: solo antes de
// que la API responda.
const _TL_LOC_DEF = '/home/user/proyectos';
let _tlEnvP = null;
function _tlEnviron() {
  _tlEnvP ||= (async () => {
    try {
      const d = await (await fetch('/api/fs/hogar')).json();
      return d && d.home ? d : null;
    } catch { return null; }
  })();
  return _tlEnvP;
}
async function _tlOpenFS(target) {
  _tlFsTarget = target || 'path';
  const src = _tlFsTarget === 'loc' ? 'tl-new-loc' : 'tl-folder-input';
  const seed = (document.getElementById(src)?.value || '').trim();
  const env = (await _tlEnviron()) || {};
  _tlFsPath = seed.startsWith('/') ? seed.replace(/\/+$/, '') : (env.home || '/home/user');
  document.getElementById('tl-fs')?.removeAttribute('hidden');
  _tlRenderFS();
}
async function _tlRenderFS() {
  const list = document.getElementById('tl-fs-list');
  if (!list) return;
  const crumb = document.getElementById('tl-fs-crumb');
  if (crumb) crumb.textContent = _tlFsPath;
  list.innerHTML = '<div class="tl2-fs-load">Cargando…</div>';
  let data = null;
  try { const res = await fetch('/api/fs/list?path=' + encodeURIComponent(_tlFsPath)); if (res.ok) data = await res.json(); } catch {}
  if (!data) {
    list.innerHTML = '<div class="tl2-fs-empty">No se pudo leer la carpeta<br><button type="button" class="tl2-browse tl2-fs-retry">Reintentar</button></div>';
    list.querySelector('.tl2-fs-retry')?.addEventListener('click', () => _tlRenderFS());
    return;
  }
  _tlFsPath = data.path; _tlFsParent = data.parent;
  if (crumb) crumb.textContent = data.path;
  // El prefijo se traduce; la RUTA jamás (data-i18n-skip — el i18n renombraba
  // carpetas reales del filesystem, ej. proyectos→projects).
  const here = document.getElementById('tl-fs-here');
  if (here) here.innerHTML = `Elegir: <span data-i18n-skip>${esc(data.path)}</span>`;
  const up = document.getElementById('tl-fs-up'); if (up) up.disabled = !data.parent;
  const chev = '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';
  const folder = '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 6h6l2 2h10v10a2 2 0 0 1-2 2H3z"/></svg>';
  list.innerHTML = data.dirs.length
    ? data.dirs.map(d => `<button type="button" class="tl2-fs-row" data-path="${esc(d.path)}">${folder}<span class="nm" data-i18n-skip>${esc(d.name)}</span>${d.git ? '<span class="git">git</span>' : ''}${chev}</button>`).join('')
    : '<div class="tl2-fs-empty">Carpeta vacía — igual podés elegirla</div>';
  list.querySelectorAll('.tl2-fs-row').forEach(r => r.addEventListener('click', () => { _tlFsPath = r.dataset.path; _tlRenderFS(); }));
}
document.querySelectorAll('#modal-new-terminal [data-browse]').forEach(b => b.addEventListener('click', () => _tlOpenFS(b.dataset.browse)));
document.getElementById('tl-fs-up')?.addEventListener('click', () => { _tlFsPath = _tlFsParent || _tlFsPath; _tlRenderFS(); });
// Cierra SOLO el explorador (Volver / Esc): el modal y su config quedan; el
// foco vuelve al campo visible del modo que lo abrió.
function _tlCerrarFS() {
  document.getElementById('tl-fs')?.setAttribute('hidden', '');
  const foco = _tlFsTarget === 'loc' ? 'tl-new-name' : 'tl-folder-input';
  setTimeout(() => document.getElementById(foco)?.focus(), 20);
}
document.getElementById('tl-fs-cancel')?.addEventListener('click', _tlCerrarFS);
document.getElementById('tl-fs-pick')?.addEventListener('click', () => {
  const esLoc = _tlFsTarget === 'loc';
  const input = document.getElementById(esLoc ? 'tl-new-loc' : 'tl-folder-input');
  if (input) input.value = _tlFsPath;
  document.getElementById('tl-fs')?.setAttribute('hidden', '');
  if (esLoc) _tlUpdatePrev();     // 'loc' → base oculta: refresca botón + preview
  else _tlSyncPick();             // 'path' → refleja la carpeta en la tarjeta
  // El foco va al campo VISIBLE (en 'loc' el input es oculto → al nombre).
  setTimeout(() => document.getElementById(esLoc ? 'tl-new-name' : 'tl-folder-input')?.focus(), 20);
});

// ── Modos Abrir/Crear + preview de creación + ubicaciones recientes ──
let _tlMode = 'open';
function _tlSlug(s) { return (s || '').trim().toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9._-]/g, ''); }
async function _tlUpdatePrev() {
  const env = (await _tlEnviron()) || {};
  const loc = ((document.getElementById('tl-new-loc')?.value || '').trim() || env.proyectos || _TL_LOC_DEF).replace(/\/+$/, '');
  const nm = _tlSlug(document.getElementById('tl-new-name')?.value) || 'mi-app';
  const el = document.getElementById('tl-new-prev'); if (el) el.textContent = loc + '/' + nm;
  // Botón de base (.tl2-path): muestra la ubicación acortada (~) y su ruta completa
  // en el tooltip. La fuente sigue siendo el input oculto #tl-new-loc.
  // El acortamiento usa el HOGAR REAL (Linux /home/x, macOS /Users/x), no un
  // regex de /home/ embebido.
  const baseB = document.querySelector('#tl-base-new b');
  if (baseB) {
    const home = env.home;
    baseB.textContent = home && loc.startsWith(home + '/') ? '~' + loc.slice(home.length)
      : home && loc === home ? '~' : (loc || '/');
  }
  document.getElementById('tl-base-new')?.setAttribute('title', loc);
}
// Sincroniza la tarjeta de "Abrir de la PC" desde #tl-folder-input (fuente): con
// ruta muestra la selección (nombre + ruta), vacía muestra el estado inicial.
function _tlSyncPick() {
  const val = (document.getElementById('tl-folder-input')?.value || '').trim();
  const empty = document.getElementById('tl-pick-empty');
  const sel = document.getElementById('tl-pick-sel');
  if (!empty || !sel) return;
  if (val) {
    const base = val.replace(/\/+$/, '').split('/').filter(Boolean).pop() || val;
    const nEl = document.getElementById('tl-pick-name'); if (nEl) nEl.textContent = base;
    const pEl = document.getElementById('tl-pick-path'); if (pEl) pEl.textContent = val;
    empty.hidden = true; sel.hidden = false;
  } else {
    empty.hidden = false; sel.hidden = true;
  }
  _tlSyncCTA();
}
function _tlSetMode(m) {
  _tlMode = m;
  document.querySelectorAll('#tl-modes button').forEach(b => b.setAttribute('aria-selected', String(b.dataset.mode === m)));
  document.querySelectorAll('#modal-new-terminal .tl2-mode').forEach(el => el.classList.toggle('on', el.dataset.mode === m));
  const focoId = m === 'new' ? 'tl-new-name' : 'tl-folder-input';
  setTimeout(() => document.getElementById(focoId)?.focus(), 40);
  _tlSyncCTA();
}
document.querySelectorAll('#tl-modes button').forEach(b => b.addEventListener('click', () => _tlSetMode(b.dataset.mode)));
document.getElementById('tl-new-name')?.addEventListener('input', _tlUpdatePrev);
document.getElementById('tl-new-loc')?.addEventListener('input', _tlUpdatePrev);
// Pegar ruta directa (modo Abrir) → reflejar en la tarjeta de selección.
document.getElementById('tl-folder-input')?.addEventListener('input', _tlSyncPick);
document.getElementById('modal-terminal-cancel')?.addEventListener('click', () => cerrarModalTerminal());

function abrirLauncher(opts = {}) {
  // Resetear contadores — shape completo desde CLI_ORDEN (enumerar a mano
  // dejaba a grok pegado entre aperturas).
  Object.assign(_tlCounts, window.JarvisLauncherState.countsIniciales());

  const input = document.getElementById('tl-folder-input');
  if (input) {
    input.value = opts.ruta || '';
  }

  const nombreEl = document.getElementById('tl-new-name'); if (nombreEl) nombreEl.value = '';
  const locEl = document.getElementById('tl-new-loc'); if (locEl) locEl.value = '';
  // Default = carpeta de proyectos del usuario de ESTA máquina (/api/fs/hogar).
  // Si la API todavía no respondió, el preview corrige al cargarla.
  _tlEnviron().then(env => {
    const el = document.getElementById('tl-new-loc');
    if (el && !el.value) el.value = (env?.proyectos) || _TL_LOC_DEF;
    _tlUpdatePrev();
  }).catch(() => _tlUpdatePrev());
  _tlUpdatePrev();
  _tlDist = 'mosaico';
  document.getElementById('tl-fs')?.setAttribute('hidden', '');
  document.querySelectorAll('#tl-dist-modes button').forEach(b => b.setAttribute('aria-checked', String(b.dataset.dist === 'mosaico')));

  modalTerminal.style.display = 'flex';
  // Default: "Crear nuevo" (como el mockup). Si viene con una ruta pre-elegida
  // (opts.ruta), arranca en "Abrir de la PC". El foco lo pone _tlSetMode.
  _tlSetMode(opts.ruta ? 'open' : 'new');
  _tlRenderGrid();
  _tlEstadoFaltantes();
  _tlRenderRutas();
  _tlUpdatePrev();
  _tlSyncPick();            // refleja opts.ruta (o vacío) en la tarjeta de Abrir
  _tlSync();                // _tlSync() llama _tlRenderDist() al final
  requestAnimationFrame(_tlSyncScrollHint);   // el layout recién queda tras el 1er frame
}
window.abrirLauncher = abrirLauncher;

// Atajos del launcher: Enter lanza (también DESDE los inputs — tipear nombre →
// Enter es EL gesto del caso feliz, y el hint del footer lo promete), Escape
// cierra POR NIVELES (explorador abierto → solo el explorador; la config del
// modal queda) y Tab queda atrapado en el diálogo (aria-modal real: antes el
// foco escapaba al fondo). Esc cede a un .ob-confirm-overlay abierto — mismo
// patrón que el Web Builder.
document.addEventListener('keydown', (e) => {
  if (!modalTerminal || modalTerminal.style.display === 'none') return;
  if (document.querySelector('.ob-confirm-overlay')) return;
  const fs = document.getElementById('tl-fs');
  const fsAbierto = !!fs && !fs.hidden;
  if (e.key === 'Escape') {
    e.preventDefault();
    if (fsAbierto) { _tlCerrarFS(); return; }
    cerrarModalTerminal();
    return;
  }
  if (e.key === 'Tab') {
    const scope = fsAbierto ? fs : modalTerminal;
    const focos = [...scope.querySelectorAll('button, input:not([type="hidden"]), [tabindex]:not([tabindex="-1"])')]
      .filter(el => !el.disabled && el.offsetParent !== null);
    if (!focos.length) return;
    e.preventDefault();
    const dir = e.shiftKey ? -1 : 1;
    const i = focos.indexOf(document.activeElement);
    (focos[(i + dir + focos.length) % focos.length] || focos[0]).focus();
    return;
  }
  if (e.key !== 'Enter') return;
  if (document.activeElement?.tagName === 'BUTTON') return;   // Enter activa ESE botón (nativo)
  e.preventDefault();
  if (fsAbierto) { document.getElementById('tl-fs-pick')?.click(); return; }   // Enter = Elegir carpeta
  _tlLanzar();
});

document.getElementById('modal-terminal-close')?.addEventListener('click', cerrarModalTerminal);
modalTerminal?.addEventListener('click', e => { if (e.target === modalTerminal) cerrarModalTerminal(); });

function cerrarModalTerminal() {
  if (modalTerminal) modalTerminal.style.display = 'none';
}

// ─── Picker rápido de terminal (Ctrl+\) ──────────────────────────
function _quickCrearTerminal(counts, preset) {
  const lote = window.JarvisLauncherState.loteDesdeContadores(counts || {}, terminales.size);
  if (!lote.length) return;
  // Batch primero (cada add() entra barato en auto) y el preset UNA sola vez al
  // final — mismo patrón que _tlLanzar/_tlAplicarDist. "Auto" llega como null:
  // no tocamos la distribución recordada del proyecto.
  _tlEjecutarBatch(lote, null)
    .then(() => { if (preset) window.TerminalLayout?.aplicarPreset?.(preset); })
    .catch(err => toast(`No se pudo crear la terminal: ${err.message}`, 'error'));
}


function _abrirQuickPicker() {
  const max = window.JarvisLauncherState?.MAX_TERMINALES ?? 12;
  // El tope de aquí es solo UX (evita abrir el picker cuando ya estás lleno);
  // el backend reimpone MAX_TERMINALES en el batch → 400 → el frontend muestra un toast.
  if (terminales.size >= max) { toast(_sbT('Máximo {n} terminales').replace('{n}', max), 'warning'); return; }
  window.QuickPicker?.abrir({
    proyecto: document.getElementById('project-title')?.textContent || '',
    onPick: _quickCrearTerminal,
    disponibles: max - terminales.size,  // los contadores no ofrecen más que el cupo
    existentes: terminales.size,         // para que los presets tejan el N FINAL
  });
}

// Listener independiente del engine hold — Ctrl+\ (configurable) en capture phase.
// Invoca ctrl.onPress() en vez de hardcodear _abrirQuickPicker(), para que el campo
// onPress de CONTROLS sea real y no engañe a futuros mantenedores.
document.addEventListener('keydown', (e) => {
  if (!(e.ctrlKey || e.metaKey) || e.shiftKey || e.altKey) return;
  const binding = _controlBindings['quick-terminal'];
  if (!binding || binding.type !== 'key' || e.code !== binding.value) return;
  // Guard puro (testeado en Node): bloquea en campos de texto reales pero NO en
  // el textarea oculto de xterm — con foco en una terminal el atajo corre igual.
  if (window.QuickPicker?.focoBloqueaAtajo(document.activeElement)) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  const ctrl = CONTROLS.find(c => c.id === 'quick-terminal');
  ctrl?.onPress?.();
}, true);

// Ejecuta el batch contra el proyecto ACTUAL y agrega las cards.
async function _tlEjecutarBatch(lote, carpeta) {
  const res = await fetch(`/api/projects/${projectId}/terminals/batch`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ terminales: lote, carpeta: carpeta || null,
                              comando: null }),
  });
  if (!res.ok) { const e = await res.json(); throw new Error(e.detail || `HTTP ${res.status}`); }
  const creadas = await res.json();
  for (const t of creadas) agregarTarjetaTerminal(t);
  actualizarVista();
}

// Resuelve el destino del lanzamiento según la carpeta de trabajo:
//  - vacía o relativa        → proyecto actual (carpeta = subcarpeta)
//  - absoluta de un proyecto → ESE proyecto (cambia de workspace y lanza)
//  - absoluta desconocida    → CREA el proyecto ahí y lanza (workspace nuevo)
async function _tlResolverDestino(raw) {
  if (!raw)                  return { targetId: projectId, carpeta: null };
  if (!raw.startsWith('/'))  return { targetId: projectId, carpeta: raw };

  const p = raw.replace(/\/+$/, '');
  const match = _sbProyectos.find(x => p === x.ruta || p.startsWith(x.ruta + '/'));
  if (match) {
    return {
      targetId: match.id,
      carpeta:  p === match.ruta ? null : p.slice(match.ruta.length + 1),
    };
  }
  // Proyecto nuevo: nombre = nombre de la carpeta
  const nombre = p.split('/').filter(Boolean).pop() || 'Proyecto';
  const res = await fetch('/api/projects', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ nombre, ruta: p }),
  });
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${res.status}`); }
  const proyecto = await res.json();
  toast(_sbT('Workspace «{nombre}» creado.').replace('{nombre}', nombre), 'success');
  cargarSidebar();
  return { targetId: proyecto.id, carpeta: null };
}

async function _tlLanzar() {
  let raw = '';
  if (_tlMode === 'new') {
    const nm = document.getElementById('tl-new-name')?.value.trim() || '';
    if (!nm) { toast('Poné un nombre para el proyecto', 'warning'); document.getElementById('tl-new-name')?.focus(); return; }
    const env = await _tlEnviron();
    const loc = ((document.getElementById('tl-new-loc')?.value || '').trim() || env?.proyectos || _TL_LOC_DEF).replace(/\/+$/, '');
    raw = loc + '/' + _tlSlug(nm);
  } else {
    raw = document.getElementById('tl-folder-input')?.value.trim() || '';
    // Sin carpeta elegida NO se lanza (antes caía silencioso al proyecto actual).
    if (!raw) { toast('Elegí una carpeta para abrir', 'warning'); document.getElementById('tl-folder-input')?.focus(); return; }
  }

  const btn = document.getElementById('btn-create-terminal');
  _tlLanzando = true;
  btn.disabled = true;
  try {
    const { targetId, carpeta } = await _tlResolverDestino(raw);
    const esOtro = String(targetId) !== String(projectId);
    if (raw.startsWith('/')) _tlRecordarRuta(raw.replace(/\/+$/, ''));

    cerrarModalTerminal();
    if (esOtro) {
      await cambiarProyecto(targetId);
    }
    const lote = window.JarvisLauncherState.loteDesdeContadores(_tlCounts, esOtro ? 0 : terminales.size);
    if (lote.length > 0) {
      await _tlEjecutarBatch(lote, carpeta);
      _tlAplicarDist();   // aplica mosaico/vertical elegido al proyecto ya activo
    }
  } catch (err) {
    toast(`Error: ${err.message}`, 'error');
  } finally {
    _tlLanzando = false;
    _tlSyncCTA();   // re-habilita según modo/estado (no un disabled=false ciego)
  }
}

document.getElementById('btn-create-terminal')?.addEventListener('click', _tlLanzar);

// ─── Label "PROYECTOS" de la franja → home (reemplaza al gh-back) ──
document.getElementById('jw-strip-home')?.addEventListener('click', () => {
  // Volver al home — la pantalla de bienvenida que aparece al arrancar el server
  location.href = '/';
});

// ─── Franja de proyectos: ocultar/mostrar (Ctrl+B) ───────────────
const _STRIP_KEY = 'jarvis.strip.hidden';   // valores: '1' (oculta) / '0' (visible)

function _aplicarEstadoStrip(oculta) {
  // FLIP de las cards: el ancho del grid cambia de un saque (regla xterm: jamás
  // animar width — reflow continuo del mosaico), pero la POSICIÓN de cada card
  // desliza por transform (compositor puro, xterm ni se entera). El contenido
  // re-wrapea UNA vez (el fit de siempre); el movimiento deja de ser teleport.
  const cards = [...document.querySelectorAll('.terminal-card')].filter(el => el.offsetParent !== null);
  const antes = new Map(cards.map(el => [el, el.getBoundingClientRect()]));
  // Fuente de verdad única: body.jw-rail. El CSS colapsa la franja a 58px
  // (cuadraditos) y corre el margin del .jw-main. NO es display:none: el
  // riel sigue mostrando los proyectos como tiles (como el mockup).
  document.body.classList.toggle('jw-rail', oculta);
  // Ancho instantáneo → UN solo relayout del mosaico, sin animación de width
  // (evita el reflow continuo de xterm que causaba lag/parpadeos).
  window.TerminalLayout?.relayoutAll?.();   // render() sincrónico: px nuevos ya puestos
  if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
    for (const el of cards) {
      const a = antes.get(el), b = el.getBoundingClientRect();
      const dx = a.left - b.left, dy = a.top - b.top;
      if (Math.abs(dx) < 2 && Math.abs(dy) < 2) continue;
      el.getAnimations().filter(x => x.id === 'jw-rail-flip').forEach(x => x.cancel());
      const an = el.animate(
        [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'translate(0, 0)' }],
        { duration: 260, easing: 'cubic-bezier(.22,1,.36,1)' });
      an.id = 'jw-rail-flip';
    }
  }
}

function toggleStrip(action = 'toggle') {
  // try/catch: localStorage puede lanzar en modo privado/cookies bloqueadas
  // (mismo patrón defensivo que los bindings PTT).
  let actual = false;
  try { actual = localStorage.getItem(_STRIP_KEY) === '1'; } catch {}
  const oculta = window.JarvisStrip.nextStripHidden(actual, action);
  try { localStorage.setItem(_STRIP_KEY, oculta ? '1' : '0'); } catch {}
  _aplicarEstadoStrip(oculta);
}
window.toggleStrip = toggleStrip;

// La puerta del header togglea el modo riel (224px ⇄ 58px).
document.getElementById('jw-strip-hide')?.addEventListener('click', () => toggleStrip('toggle'));
// Conmutador Workspaces ⇄ Editor
document.getElementById('jw-seg-spaces')?.addEventListener('click', () => setSidebarView('spaces'));
document.getElementById('jw-seg-editor')?.addEventListener('click', () => setSidebarView('editor'));
// Coloca la gota de vidrio al arrancar (y de nuevo cuando la webfont Inter cargó,
// para que el ancho del segmento activo se mida correcto).
_sbLayoutPill();
document.fonts?.ready?.then(_sbLayoutPill);

// ─── Tooltip flotante del riel: al hover sobre un tile, el nombre del
// proyecto aparece fijo al lado (fixed → no lo recorta la franja). Solo
// activo en modo riel; delegado en #sidebar-nav. ───────────────────
(function wireRailTip() {
  const nav = document.getElementById('sidebar-nav');
  if (!nav) return;
  let tip = null;
  const ocultar = () => { if (tip) tip.classList.remove('on'); };
  nav.addEventListener('mouseover', (e) => {
    if (!document.body.classList.contains('jw-rail')) return;
    const row = e.target.closest('.sb-row');
    if (!row) return;
    const nombre = row.querySelector('.sb-row-name')?.textContent?.trim()
                || row.getAttribute('aria-label') || '';
    if (!nombre) return;
    if (!tip) { tip = document.createElement('div'); tip.className = 'jw-railtip'; document.body.appendChild(tip); }
    tip.textContent = nombre;
    const r = row.getBoundingClientRect();
    tip.style.left = (r.right + 10) + 'px';
    tip.style.top = (r.top + r.height / 2) + 'px';
    tip.classList.add('on');
  });
  nav.addEventListener('mouseout', (e) => {
    if (!e.relatedTarget || !e.target.closest('.sb-row')?.contains(e.relatedTarget)) ocultar();
  });
  nav.addEventListener('click', ocultar);
})();

// Restaurar estado persistido al cargar.
window.addEventListener('DOMContentLoaded', () => {
  let oculta = false;
  try { oculta = localStorage.getItem(_STRIP_KEY) === '1'; } catch {}
  _aplicarEstadoStrip(oculta);
});

// Ctrl+B / ⌘B → toggle de la franja (no interferir si se escribe en input).
document.addEventListener('keydown', (e) => {
  if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
  if (!(e.key === 'b' || e.key === 'B' || e.code === 'KeyB')) return;
  // TEXTAREA incluye el xterm-helper-textarea: con foco en una terminal el
  // atajo NO togglea (el usuario puede estar en nano/htop usando Ctrl+B).
  const tag = document.activeElement?.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return;
  e.preventDefault();
  toggleStrip('toggle');
});

// ─── Helpers ──────────────────────────────────────────────────────

function esc(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}
// Exponer para editor.js (window.JarvisEditor lo reusa sin duplicar)
window.esc = esc;

// ═══════════════════════════════════════════════════════════════════
//  PANEL ÚNICO (DOCK) — reemplaza los paneles toggleables viejos
// ═══════════════════════════════════════════════════════════════════

// onTabShown: lo invoca JarvisDock cuando una pestaña se vuelve visible.
// Hace el init/refit lazy de cada sección.
function _onDockTabShown(tab) {
  // Al cambiar de pestaña, el editor deja de pollear si ya no es la activa.
  if (tab !== 'editor') window.JarvisEditor?.detenerPolling?.();
  switch (tab) {
    case 'editor':
      // Arranca el editor si es la primera vez (lee window.editorVisible=true ahora).
      window.JarvisEditor?.refrescarArbol();
      window.JarvisEditor?.iniciarPolling();
      window.JarvisEditor?.cargarMonaco();
      window.JarvisEditor?.relayout?.();
      break;
    case 'jarvis': {
      // Mostrar el chat NO debe arrastrarlo al fondo si el usuario venía leyendo
      // más arriba (bug: al volver de "ver los agentes runeando" / cambiar de
      // pestaña del dock, el chat saltaba todo abajo). Solo auto-bajamos si YA
      // estaba pegado al fondo (_userScrolled=false); si scrolleó arriba,
      // respetamos su posición. El contenedor real es panel.$messages.
      const p = window.jarvisPanel;
      if (p?.$messages && !p._userScrolled) p.$messages.scrollTop = p.$messages.scrollHeight;
      break;
    }
    case 'tasks':
      window.JarvisTasks?.show?.();
      break;
    case 'review':
      window.JarvisReview?.mostrarEnPane?.();   // Task 26 lo define
      break;
    case 'preview': {
      // Lazy-init: monta el browser embebido la primera vez (idempotente),
      // luego autollena con el localhost del proyecto si lo hay.
      const panePreview = document.getElementById('jw-pane-preview');
      window.WebPreview?.init?.(panePreview);
      window.WebPreview?.detectar?.(projectId);  // Fase 3
      break;
    }
    case 'mobile':
      window.MobilePreview?.abrir?.();
      break;
  }
}
window._onDockTabShown = _onDockTabShown;

// Shim de compatibilidad: editor.js (command palette) llama window.togglePanel
// con un guard `typeof window.togglePanel === 'function'`. Lo mapeamos al dock
// para no romper esas dos entradas de la paleta ("Alternar panel del editor/
// orquestador"). Semántica de toggle: si la pestaña destino ya es la activa,
// cierra el dock; si no, lo abre en esa pestaña.
window.togglePanel = (panel) => {
  const MAP = { editor: 'editor', orquestador: 'jarvis',
                tasks: 'tasks', mobile: 'mobile' };
  const tab = MAP[panel];
  if (!tab || !window.JarvisDock) return;
  if (window.JarvisDock.activeTab?.() === tab) window.JarvisDock.close();
  else window.JarvisDock.setTab(tab);
};

// ═══════════════════════════════════════════════════════════════════
//  FEATURE 3 — PLUGINS & SKILLS (modal con tabs)
// ═══════════════════════════════════════════════════════════════════

const modalSkillMd     = document.getElementById('modal-skill-md');
const modalInstallCmd  = document.getElementById('modal-install-cmd');

let psPluginsInstalados = [];
let psPluginsActivos    = new Set();   // full_ids activos para el proyecto actual
let psMarketplace       = [];
let psSkillsMd          = [];
let psEditingSkillName  = null;  // null = nueva | string = editando existente

// ─── Tabs (el markup ps-* ahora vive dentro de #jw-settings) ────────

function activarTab(name) {
  document.querySelectorAll('#jw-settings .ps-tab').forEach(t => {
    t.classList.toggle('activo', t.dataset.tab === name);
  });
  document.querySelectorAll('#jw-settings .ps-panel').forEach(p => {
    p.classList.toggle('activo', p.dataset.panel === name);
  });
  if (name === 'marketplace' && psMarketplace.length === 0) cargarMarketplace();
}

// Glifo de cada plugin (sections/settings/plugin-icons.js). Antes TODAS las
// filas del rack —y las 174 cards del marketplace— mostraban el mismo enchufe;
// un icono repetido no informa nada. Si el módulo no cargó, cae al enchufe.
const icoPlugin = (p) =>
  window.JarvisPluginIcons?.iconoDePlugin?.(p.full_id, p.nombre) || 'plug';

// Bridge: settings.js inyecta el markup ps-* en #jw-settings y llama montar().
window.JarvisSkills = {
  montar() {
    document.querySelectorAll('#jw-settings .ps-tab').forEach(tab =>
      tab.addEventListener('click', () => activarTab(tab.dataset.tab)));
    document.getElementById('ps-btn-new-skill')?.addEventListener('click', () => abrirEditorSkillMd(null));
    activarTab('instalados');
    Promise.all([cargarPluginsInstalados(), cargarSkillsMd()]);
  },
};

// ─── TAB 1: Plugins instalados ──────────────────────────────────────

async function cargarPluginsInstalados() {
  const cont = document.getElementById('ps-plugins-instalados');
  const cnt  = document.getElementById('ps-plugins-count');
  if (!cont) return;
  cont.innerHTML = '<div class="ps-empty">Cargando plugins…</div>';
  try {
    // 1. Pedir las dos listas en paralelo: instalados (a nivel sistema) + activos (de este proyecto)
    const [resInst, resAct] = await Promise.all([
      fetch('/api/plugins/instalados'),
      fetch(`/api/projects/${projectId}/plugins/activos`),
    ]);
    if (!resInst.ok) throw new Error(`/instalados HTTP ${resInst.status}`);
    if (!resAct.ok)  throw new Error(`/activos HTTP ${resAct.status}`);

    psPluginsInstalados = await resInst.json();
    const activos       = await resAct.json();          // { activos: [...full_ids] }
    psPluginsActivos    = new Set(activos.activos || []);

    if (cnt) cnt.textContent = psPluginsInstalados.length;
    if (psPluginsInstalados.length === 0) {
      cont.innerHTML = '<div class="ps-empty">No tenés plugins instalados. Vení al tab Marketplace.</div>';
      return;
    }

    // 2. Para cada plugin: checked = exactamente lo que dijo el servidor
    cont.innerHTML = '';
    for (const p of psPluginsInstalados) {
      const activo = psPluginsActivos.has(p.full_id);
      const el = document.createElement('div');
      el.className = 'ps-item';
      el.innerHTML = `
        <span class="ps-item-badge ps-badge-plugin" title="Plugin — herramienta externa que extiende Claude Code">${icon(icoPlugin(p), 15)} PLUGIN</span>
        <div class="ps-item-body">
          <div class="ps-item-name">${esc(p.nombre)}${p.version && p.version !== 'unknown' ? ` <span class="ps-item-version">v${esc(p.version)}</span>` : ''}</div>
          <div class="ps-item-desc" lang="en">${esc(p.descripcion || p.full_id)}</div>
        </div>
        <label class="ps-switch" title="${activo ? 'Desactivar para este proyecto' : 'Activar para este proyecto'}">
          <input type="checkbox" ${activo ? 'checked' : ''} data-full-id="${esc(p.full_id)}">
          <span class="ps-switch-slider"></span>
        </label>
      `;
      el.querySelector('input[type="checkbox"]').addEventListener('change', e => {
        togglePluginEnProyecto(p.full_id, e.target.checked, e.target);
      });
      cont.appendChild(el);
    }
  } catch (err) {
    cont.innerHTML = `<div class="ps-empty">Error: ${esc(err.message)}</div>`;
  }
}

// Cambia el estado en el servidor y RECONCILIA el checkbox con la respuesta real.
// Nunca asume — siempre verifica el { activo } que devuelve el server.
async function togglePluginEnProyecto(fullId, activoDeseado, checkbox) {
  // Guardar el estado anterior del checkbox para poder revertir si falla
  const estadoPrevio = !activoDeseado;
  try {
    const res = await fetch(`/api/projects/${projectId}/plugins/toggle`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ plugin_id: fullId, activo: activoDeseado }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    const real = !!data.activo;   // ← fuente de verdad: lo que dice el servidor

    // Reconciliar el Set en memoria con la respuesta del servidor
    if (real) psPluginsActivos.add(fullId);
    else      psPluginsActivos.delete(fullId);

    // Si el checkbox no coincide con la verdad del server, corregirlo
    if (checkbox && checkbox.checked !== real) {
      checkbox.checked = real;
    }
  } catch (err) {
    // Revertir solo este checkbox (no recargar toda la lista)
    if (checkbox) checkbox.checked = estadoPrevio;
    toast(`Error: ${err.message}`, 'error');
  }
}

// ─── TAB 1: Skills .md del proyecto ─────────────────────────────────

async function cargarSkillsMd() {
  const cont = document.getElementById('ps-skills-md');
  const cnt  = document.getElementById('ps-skills-count');
  const pathLabel = document.getElementById('ps-skill-path');
  if (!cont) return;
  cont.innerHTML = '<div class="ps-empty">Cargando skills…</div>';
  try {
    const res = await fetch(`/api/projects/${projectId}/skills-md`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    psSkillsMd = data.skills || [];
    if (pathLabel) pathLabel.textContent = `${data.dir}/`;
    if (cnt) cnt.textContent = psSkillsMd.length;

    if (psSkillsMd.length === 0) {
      cont.innerHTML = '<div class="ps-empty">Sin skills .md todavía. Click en "+ Nueva".</div>';
      return;
    }

    cont.innerHTML = '';
    for (const s of psSkillsMd) {
      const el = document.createElement('div');
      el.className = 'ps-item ps-item-clickable';
      el.innerHTML = `
        <span class="ps-item-badge ps-badge-skill" title="Skill — conocimiento del proyecto que guía al agente">${icon('file', 15)} SKILL</span>
        <div class="ps-item-body">
          <div class="ps-item-name">${esc(s.nombre)} <span class="ps-item-version">${esc(s.tipo)}</span></div>
          <div class="ps-item-desc">${esc(s.preview || '(sin descripción)')}</div>
        </div>
        <button class="ps-item-edit" title="Editar">${icon('edit', 14)}</button>
      `;
      const editar = () => abrirEditorSkillMd(s.nombre);
      el.querySelector('.ps-item-edit').addEventListener('click', editar);
      el.addEventListener('click', e => {
        if (!e.target.closest('.ps-item-edit')) editar();
      });
      cont.appendChild(el);
    }
  } catch (err) {
    cont.innerHTML = `<div class="ps-empty">Error: ${esc(err.message)}</div>`;
  }
}

// El listener de #ps-btn-new-skill se cablea en JarvisSkills.montar()
// (el markup ahora se inyecta dinámicamente en #jw-settings).

async function abrirEditorSkillMd(nombre) {
  if (!modalSkillMd) return;
  psEditingSkillName = nombre;

  const tituloEl   = document.getElementById('skill-md-title');
  const inputNom   = document.getElementById('skill-md-nombre');
  const taContent  = document.getElementById('skill-md-content');
  const btnDel     = document.getElementById('ps-btn-delete-skill');

  if (nombre) {
    tituloEl.textContent = nombre;
    inputNom.value = nombre;
    inputNom.disabled = true;
    btnDel.style.display = '';
    try {
      const res = await fetch(`/api/projects/${projectId}/skills-md/${encodeURIComponent(nombre)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      taContent.value = data.content || '';
    } catch (err) {
      taContent.value = '';
      toast(`Error cargando skill: ${err.message}`, 'error');
    }
  } else {
    tituloEl.textContent = 'Nueva skill';
    inputNom.value = '';
    inputNom.disabled = false;
    taContent.value = '---\nname: \ndescription: \n---\n\n# ';
    btnDel.style.display = 'none';
  }

  modalSkillMd.style.display = 'flex';
  setTimeout(() => (nombre ? taContent : inputNom).focus(), 50);
}

function cerrarEditorSkillMd() {
  if (modalSkillMd) modalSkillMd.style.display = 'none';
  psEditingSkillName = null;
}
document.getElementById('modal-skill-md-close')?.addEventListener('click', cerrarEditorSkillMd);
document.getElementById('ps-btn-cancel-skill')?.addEventListener('click', cerrarEditorSkillMd);
modalSkillMd?.addEventListener('click', e => {
  if (e.target === modalSkillMd) cerrarEditorSkillMd();
});

document.getElementById('ps-btn-save-skill')?.addEventListener('click', async () => {
  const nombre  = (psEditingSkillName || document.getElementById('skill-md-nombre').value).trim();
  const content = document.getElementById('skill-md-content').value;
  if (!nombre) { toast('El nombre es obligatorio.', 'warning'); return; }
  if (!/^[a-zA-Z0-9_-]+$/.test(nombre)) {
    toast('Solo letras, números, guiones y underscores.', 'warning');
    return;
  }
  try {
    const res = await fetch(`/api/projects/${projectId}/skills-md`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ nombre, content }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    cerrarEditorSkillMd();
    await cargarSkillsMd();
  } catch (err) {
    toast(`Error guardando: ${err.message}`, 'error');
  }
});

document.getElementById('ps-btn-delete-skill')?.addEventListener('click', async () => {
  if (!psEditingSkillName) return;
  if (!(await confirmar(`¿Eliminar la skill "${psEditingSkillName}"?`, { peligro: true, confirmText: 'Eliminar' }))) return;
  try {
    const res = await fetch(`/api/projects/${projectId}/skills-md/${encodeURIComponent(psEditingSkillName)}`, { method: 'DELETE' });
    if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`);
    cerrarEditorSkillMd();
    await cargarSkillsMd();
  } catch (err) {
    toast(`Error eliminando: ${err.message}`, 'error');
  }
});

// ─── TAB 2: Marketplace ─────────────────────────────────────────────

async function cargarMarketplace() {
  const cont = document.getElementById('ps-marketplace');
  const cnt  = document.getElementById('ps-marketplace-count');
  if (!cont) return;
  cont.innerHTML = '<div class="ps-empty">Cargando marketplace…</div>';
  try {
    const res = await fetch('/api/plugins/marketplace');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    psMarketplace = await res.json();
    if (cnt) cnt.textContent = psMarketplace.length;

    if (psMarketplace.length === 0) {
      cont.innerHTML = '<div class="ps-empty">No hay plugins en el marketplace.</div>';
      return;
    }

    cont.innerHTML = '';
    for (const p of psMarketplace) {
      const el = document.createElement('div');
      el.className = 'ps-card' + (p.instalado ? ' ps-card-instalado' : '');
      el.innerHTML = `
        <div class="ps-card-top">
          <span class="ps-item-badge ps-badge-plugin" title="Plugin — herramienta externa que extiende Claude Code">${icon(icoPlugin(p), 15)}</span>
          <div class="ps-card-name">${esc(p.nombre)}</div>
          ${p.instalado ? '<span class="ps-card-installed-badge">Instalado</span>' : ''}
        </div>
        <div class="ps-card-desc">${esc(p.descripcion || '(sin descripción)')}</div>
        <div class="ps-card-meta">
          <span class="ps-card-source">${p.source === 'external' ? `${icon('external-link', 12)} external` : `${icon('check', 12)} official`}</span>
          <span class="ps-card-mk">${esc(p.marketplace)}</span>
        </div>
        <div class="ps-card-actions">
          ${p.instalado
            ? '<button class="btn-conectar" data-action="activate">Activar para este proyecto</button>'
            : '<button class="btn-conectar" data-action="install">Instalar</button>'}
        </div>
      `;
      el.querySelector('button')?.addEventListener('click', () => {
        if (p.instalado) {
          // Switch to tab 1 and highlight
          activarTab('instalados');
          setTimeout(() => {
            const cb = document.querySelector(`#ps-plugins-instalados input[data-full-id="${CSS.escape(p.full_id)}"]`);
            if (cb && !cb.checked) {
              cb.checked = true;
              togglePluginEnProyecto(p.full_id, true);
            }
          }, 100);
        } else {
          mostrarComandoInstalacion(p);
        }
      });
      cont.appendChild(el);
    }
  } catch (err) {
    cont.innerHTML = `<div class="ps-empty">Error: ${esc(err.message)}</div>`;
  }
}

function mostrarComandoInstalacion(plugin) {
  if (!modalInstallCmd) return;
  const cmd = `/plugin install ${plugin.full_id}`;
  document.getElementById('install-cmd-title').textContent = `Instalar ${plugin.nombre}`;
  document.getElementById('install-cmd-code').textContent  = cmd;
  modalInstallCmd.style.display = 'flex';
  document.getElementById('btn-copy-install-cmd').onclick = async () => {
    try {
      await navigator.clipboard.writeText(cmd);
      const btn = document.getElementById('btn-copy-install-cmd');
      const orig = btn.textContent;
      btn.textContent = 'Copiado';
      setTimeout(() => { btn.textContent = orig; }, 1500);
    } catch {
      toast(`Copiá manualmente: ${cmd}`, 'info');
    }
  };
}

document.getElementById('modal-install-cmd-close')?.addEventListener('click', () => {
  if (modalInstallCmd) modalInstallCmd.style.display = 'none';
});
modalInstallCmd?.addEventListener('click', e => {
  if (e.target === modalInstallCmd) modalInstallCmd.style.display = 'none';
});


// ═══════════════════════════════════════════════════════════════════
//  ORQUESTADOR — Historial, nuevo thread, export, workflows
// ═══════════════════════════════════════════════════════════════════

// thread_id del thread actual (se genera al cargar el proyecto)
let currentThreadId = (crypto.randomUUID?.() ?? `t-${Date.now()}`);

// Bridge del header del panel orquestador
window._orchOnHeaderAction = async (action) => {
  switch (action) {
    case 'history':       return abrirModalHistorial();
    case 'new-thread':    return nuevoThread();
    case 'export':        return exportarConversacion();
    case 'workflows':     return window.JarvisSettings?.open('workflows');
    case 'clear-history': return limpiarHistorialCompleto();
  }
};

// ─── Nuevo thread: guarda el actual y limpia el chat ────────────────

async function guardarThreadActual() {
  const mensajes = window.jarvisPanel?.getMessages?.() || [];
  // No guardar threads vacíos o que solo tengan el saludo inicial
  const userMsgs = mensajes.filter(m => m.role === 'user');
  if (userMsgs.length === 0) return;

  try {
    await fetch(`/api/orchestrator/historial/${projectId}`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        thread_id: currentThreadId,
        mensajes:  mensajes.map(m => ({
          rol:   m.role === 'jarvis' ? 'jarvis' : 'user',
          texto: m.content,
          ts:    m.timestamp ? new Date(m.timestamp).toISOString() : null,
        })),
      }),
    });
  } catch (err) {
    console.warn('No se pudo guardar el thread:', err);
  }
}

async function nuevoThread() {
  await guardarThreadActual();
  window.jarvisPanel?.clearMessages?.();
  if (chatSesiones[projectId]) chatSesiones[projectId] = [];
  currentThreadId = (crypto.randomUUID?.() ?? `t-${Date.now()}`);
  agregarMensajeChat('jarvis', '¿Qué hacemos, señor?');
}

// ─── Modal: historial de threads ────────────────────────────────────

const modalHistorial = document.getElementById('modal-historial');

async function abrirModalHistorial() {
  if (!modalHistorial) return;
  modalHistorial.style.display = 'flex';
  const cont = document.getElementById('historial-lista');
  if (!cont) return;
  cont.innerHTML = '<div class="hist-empty" aria-hidden="true"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>';

  try {
    const res = await fetch(`/api/orchestrator/historial/${projectId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const threads = await res.json();

    if (threads.length === 0) {
      cont.innerHTML = '<div class="hist-empty">No hay conversaciones guardadas todavía.</div>';
      return;
    }

    cont.innerHTML = '';
    for (const t of threads) {
      const fecha = t.updated_at ? new Date(t.updated_at).toLocaleString('es-AR', {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
      }) : '—';
      const el = document.createElement('div');
      el.className = 'hist-item';
      el.innerHTML = `
        <div class="hist-item-text">
          <div class="hist-item-preview">${esc(t.preview)}</div>
          <div class="hist-item-meta">${fecha} · ${t.count} mensaje${t.count !== 1 ? 's' : ''}</div>
        </div>
        <div class="hist-item-actions">
          <button class="hist-load-btn" data-id="${esc(t.thread_id)}" title="Cargar este thread">Cargar</button>
          <button class="hist-del-btn"  data-id="${esc(t.thread_id)}" title="Eliminar">${icon('trash')}</button>
        </div>
      `;
      el.querySelector('.hist-load-btn').addEventListener('click', () => cargarThread(t.thread_id));
      el.querySelector('.hist-del-btn').addEventListener('click', () => eliminarThread(t.thread_id));
      cont.appendChild(el);
    }
  } catch (err) {
    cont.innerHTML = `<div class="hist-empty">Error: ${esc(err.message)}</div>`;
  }
}

function cerrarModalHistorial() {
  if (modalHistorial) modalHistorial.style.display = 'none';
}
document.getElementById('modal-historial-close')?.addEventListener('click', cerrarModalHistorial);
modalHistorial?.addEventListener('click', e => {
  if (e.target === modalHistorial) cerrarModalHistorial();
});

async function cargarThread(threadId) {
  try {
    await guardarThreadActual();
    const res = await fetch(`/api/orchestrator/historial/${projectId}/${encodeURIComponent(threadId)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    window.jarvisPanel?.clearMessages?.();
    chatSesiones[projectId] = [];
    currentThreadId = threadId;

    for (const m of (data.mensajes || [])) {
      const rol = m.rol === 'jarvis' ? 'jarvis' : 'user';
      agregarMensajeChat(rol, m.texto || '');
    }
    cerrarModalHistorial();
  } catch (err) {
    toast(`Error cargando thread: ${err.message}`, 'error');
  }
}

async function eliminarThread(threadId) {
  if (!(await confirmar('¿Eliminar esta conversación?', { peligro: true, confirmText: 'Eliminar' }))) return;
  try {
    const res = await fetch(`/api/orchestrator/historial/${projectId}/${encodeURIComponent(threadId)}`, { method: 'DELETE' });
    if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`);
    abrirModalHistorial();
  } catch (err) {
    toast(`Error: ${err.message}`, 'error');
  }
}

async function limpiarHistorialCompleto() {
  if (!(await confirmar('¿Borrar TODO el historial de conversaciones de este proyecto? Esta acción no se puede deshacer.', { titulo: 'Borrar historial', peligro: true, confirmText: 'Borrar todo' }))) return;
  try {
    const res = await fetch(`/api/orchestrator/historial/${projectId}`, { method: 'DELETE' });
    if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`);
    toast('Historial limpio.', 'success');
  } catch (err) {
    toast(`Error: ${err.message}`, 'error');
  }
}

// ─── Export conversación a .md ──────────────────────────────────────

function exportarConversacion() {
  const mensajes = window.jarvisPanel?.getMessages?.() || [];
  if (mensajes.length === 0) {
    toast('No hay mensajes para exportar.', 'info');
    return;
  }

  const proyectoNombre = elTitulo?.textContent?.trim() || 'proyecto';
  const fecha          = new Date();
  const fechaISO       = fecha.toISOString().slice(0, 10);
  const fechaHumana    = fecha.toLocaleString('es-AR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });

  const lineas = [`# Conversación JARVIS — ${proyectoNombre} — ${fechaHumana}`, ''];
  for (const m of mensajes) {
    const autor = m.role === 'jarvis' ? 'JARVIS' : 'Usuario';
    lineas.push(`**${autor}:** ${m.content || ''}`, '');
  }
  const md = lineas.join('\n');

  const slug = proyectoNombre.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'proyecto';
  const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = `jarvis-${slug}-${fechaISO}.md`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ─── Bridge: workflows del proyecto (reutilizable desde JarvisSettings) ─

window.JarvisWorkflows = {
  async render(cont) {
    if (!cont) return;
    cont.innerHTML = '<div class="hist-empty" aria-hidden="true"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>';
    try {
      const res = await fetch(`/api/orchestrator/workflows/${projectId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const wfs = await res.json();

      if (wfs.length === 0) {
        cont.innerHTML = '<div class="hist-empty">Sin workflows ejecutados todavía.</div>';
        return;
      }

      cont.innerHTML = '';
      for (const w of wfs) {
        const fecha = w.created_at ? new Date(w.created_at).toLocaleString('es-AR', {
          day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
        }) : '—';
        const estadoColor = {
          running: 'var(--ob-work)', done: 'var(--ob-run)', failed: 'var(--ob-err)', pending: 'var(--ob-fg-3)',
        }[w.estado] || 'var(--ob-fg-3)';
        const el = document.createElement('div');
        el.className = 'hist-item';
        el.innerHTML = `
          <div class="hist-item-text">
            <div class="hist-item-preview">${esc(w.nombre)}</div>
            <div class="hist-item-meta">
              <span style="color:${estadoColor}">${icon('dot', 10)} ${esc(w.estado)}</span>
              · ${w.paso_actual}/${w.total_pasos} pasos
              · ${fecha}
            </div>
          </div>
        `;
        cont.appendChild(el);
      }
    } catch (err) {
      cont.innerHTML = `<div class="hist-empty">Error: ${esc(err.message)}</div>`;
    }
  },
};

// El viejo "preview badge/pill" de la barra (● preview · :PUERTO) se eliminó:
// los localhost vivos se ven y se cierran desde el menú #jw-localhosts-btn
// (sections/preview/dev-servers.js). El endpoint GET /preview/{id} sigue vivo
// porque lo usa el Web Preview para autodetectar (WebPreview.detectar).


// ═══════════════════════════════════════════════════════════════════
//  CONTROLES (atajos globales) + SETTINGS MODAL
// ═══════════════════════════════════════════════════════════════════
//
// Sistema de "controles": cada control es una acción con un keybind opcional
// que se ejecuta cuando el usuario aprieta/mantiene la tecla o botón del mouse.
// El registry es extensible — para agregar un nuevo atajo basta sumar una
// entrada acá, y aparece automáticamente en el modal Controles.

// Shape del binding (en localStorage por id de control):
//   { type: 'key' | 'mouse', value: string | number }
//     - key:   value = e.code (ej "AltLeft"). Robusto al layout.
//     - mouse: value = e.button (0=izq, 1=medio, 2=der, 3=back, 4=fwd).

function _prettyKeyLabel(code) {
  if (!code) return '—';
  const map = {
    AltLeft: 'Alt', AltRight: 'Alt der',
    ControlLeft: 'Ctrl', ControlRight: 'Ctrl der',
    ShiftLeft: 'Shift', ShiftRight: 'Shift der',
    MetaLeft: 'Cmd', MetaRight: 'Cmd der',
    Space: 'Espacio', Enter: 'Enter', Escape: 'Esc', Tab: 'Tab',
    CapsLock: 'CapsLock', Backquote: '`', Backslash: '\\',
  };
  if (map[code]) return map[code];
  if (code.startsWith('Key'))    return code.slice(3);
  if (code.startsWith('Digit'))  return code.slice(5);
  if (code === 'ArrowLeft')      return '←';
  if (code === 'ArrowRight')     return '→';
  if (code === 'ArrowUp')        return '↑';
  if (code === 'ArrowDown')      return '↓';
  return code;
}

function _prettyMouseLabel(button) {
  const map = {
    0: 'Click izquierdo',
    1: 'Click medio',
    2: 'Click derecho',
    3: 'Mouse · atrás',
    4: 'Mouse · adelante',
  };
  return map[button] ?? `Mouse · botón ${button}`;
}

function _renderBindingLabel(b) {
  if (!b) return 'Sin asignar';
  return b.type === 'mouse' ? _prettyMouseLabel(b.value) : _prettyKeyLabel(b.value);
}

function _esTargetEditable(target) {
  if (!target) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (target.isContentEditable) return true;
  return false;
}

function _esTeclaModificadora(code) {
  return /^(Alt|Control|Shift|Meta)(Left|Right)?$/.test(code);
}

// Si el binding es un modificador (AltLeft, etc.), matchea ambos lados.
// Sino, match exacto por code.
function _codeMatchea(bindingCode, eventCode) {
  if (bindingCode === eventCode) return true;
  if (_esTeclaModificadora(bindingCode) && _esTeclaModificadora(eventCode)) {
    // AltGr (AltRight) es tecla de ESCRITURA en teclados latinos (en Windows
    // dispara ControlLeft+AltRight). No debe matchear por familia un binding
    // AltLeft, sino interrumpe el tipeo de símbolos como @ } { etc. El usuario
    // que bindee AltRight explícito sigue funcionando por el match exacto de arriba.
    if (eventCode === 'AltRight' && bindingCode !== 'AltRight') return false;
    const fam = (c) => c.replace(/(Left|Right)$/, '');
    return fam(bindingCode) === fam(eventCode);
  }
  return false;
}

// ─── Registry de controles ────────────────────────────────────────

const CONTROLS = [
  {
    id: 'mic-ptt',
    label: 'Hablar',
    desc: 'Mantené esta tecla o botón del mouse para dictar. El audio va a donde hiciste click por última vez: una terminal o el chat de Jarvis. Doble-tap de la tecla o doble-click del botón: dictado fijado — grabás sin sostener nada y podés moverte por el workspace; la misma tecla o botón envía, Esc cancela.',
    iconSVG: `<svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.45" stroke-linecap="round" stroke-linejoin="round"><rect x="6.5" y="2.5" width="5" height="8.5" rx="2.4"/><path d="M3.7 9.1a5.3 5.3 0 0010.6 0"/><line x1="9" y1="14.5" x2="9" y2="16.2"/></svg>`,
    default: { type: 'key', value: 'AltLeft' },
    mode: 'hold',
    onHold:    () => window._orchOnMicHold?.(),
    onRelease: () => window._orchOnMicRelease?.(),
    onDiscard: () => window._orchOnMicDiscard?.(),   // arranque optimista que resultó combo/tap: descartar sin procesar
  },
  {
    id: 'quick-terminal',
    label: 'Nueva terminal rápida',
    desc: 'Con Ctrl + esta tecla se abre el picker y cada toque crea una terminal.',
    iconSVG: icon('terminal', 15),
    default: { type: 'key', value: 'Backslash' },
    mode: 'press',
    onPress: () => _abrirQuickPicker(),
  },
];

const _controlBindings = {};   // id → binding | null
const _controlActive   = {};   // id → bool (está siendo mantenido ahora)

function _storageKey(id) { return `jarvis.control.${id}`; }

function _cargarBinding(ctrl) {
  try {
    const raw = localStorage.getItem(_storageKey(ctrl.id));
    if (!raw) return ctrl.default ? { ...ctrl.default } : null;
    const parsed = JSON.parse(raw);
    if (parsed === null) return null;   // explícitamente sin atajo
    if (!parsed?.type || parsed.value === undefined) return ctrl.default ? { ...ctrl.default } : null;
    return parsed;
  } catch {
    return ctrl.default ? { ...ctrl.default } : null;
  }
}

function _guardarBinding(id, binding) {
  try { localStorage.setItem(_storageKey(id), JSON.stringify(binding)); }
  catch (e) { console.warn('No se pudo guardar binding', id, e); }
}

function _resetBinding(ctrl) {
  try { localStorage.removeItem(_storageKey(ctrl.id)); } catch {}
  _controlBindings[ctrl.id] = ctrl.default ? { ...ctrl.default } : null;
}

// Init bindings de cada control desde storage
function _initControlBindings() {
  for (const c of CONTROLS) {
    _controlBindings[c.id] = _cargarBinding(c);
    _controlActive[c.id]   = false;
  }
}

// ─── Listeners globales ───────────────────────────────────────────

let _capturando = false;       // true mientras el modal está esperando una tecla

// Hold de una tecla MODIFICADORA con ventana de confirmación de 220ms. ARRANQUE
// OPTIMISTA: la captura del mic empieza YA en el keydown para no comerse la
// primera palabra (antes esperaba los 220ms → el primer fonema se perdía).
// El umbral pasa a decidir CONFIRMAR vs DESCARTAR: si en ese lapso llega otra tecla
// (combo de tipeo) o se suelta (tap), se descarta sin procesar; si el modificador se
// mantuvo solo ≥220ms, el hold queda confirmado (ya venía grabando desde t=0).
let _pttPendiente = null;      // {ctrl, timer} | null — provisional (aún descartdable)

const _PTT_UMBRAL_MS = 220;

// Arma un hold con ventana de confirmación para bindings de tecla modificadora.
// Arranca la captura al instante; el timer solo CONFIRMA (la captura ya corría).
function _armarHoldConUmbral(ctrl) {
  if (_controlActive[ctrl.id] || _pttPendiente) return true;  // ya activo o provisional
  _triggerHold(ctrl);   // captura optimista: la primera palabra entra desde t=0
  _pttPendiente = {
    ctrl,
    timer: setTimeout(() => { _pttPendiente = null; }, _PTT_UMBRAL_MS),  // confirma: nada más que hacer
  };
  return true;
}

// El hold provisional no se confirmó (combo de tipeo, tap, o blur dentro de la
// ventana): descartar la captura optimista SIN procesar.
function _cancelarPttPendiente() {
  if (!_pttPendiente) return;
  const ctrl = _pttPendiente.ctrl;
  clearTimeout(_pttPendiente.timer);
  _pttPendiente = null;
  if (_controlActive[ctrl.id]) {
    _controlActive[ctrl.id] = false;
    _cancelOnSound();          // fue un tap/combo: la grabación no llegó a sonar
    _ocultarPttIndicator(ctrl);
    ctrl.onDiscard?.();
  }
}

// ─── Dictado FIJADO (doble-tap de la tecla de voz) ────────────────
// Dos taps rápidos (PttFijado.esDobleTap) arrancan una grabación SIN sostener
// la tecla: mouse y teclado quedan libres para pasear por el workspace mientras
// dictás (el destino quedó congelado en _activeVoiceSession al arrancar, como
// siempre). La misma tecla (tap) corta y ENVÍA; Esc cancela sin enviar. Bonus:
// con la tecla suelta no aplican los efectos de Alt sostenido sobre xterm
// (fastScrollModifier / alt-click). Decisiones puras en shell/ptt-fijado.js.
let _pttFijado      = false;
let _pttUltimoTapTs = 0;   // fin del último tap limpio del mic — tecla O mouse (detección del doble-tap)
let _mouseHoldT0    = 0;   // performance.now() del mousedown que arrancó el hold de MOUSE del mic

// Núcleo del fijado (lo comparten tecla y mouse): el hold que venía grabando
// desde el press queda confirmado SIN nada sostenido. El release que nos trajo
// acá NO libera: _controlActive queda true y el próximo tap/click del binding
// cae en el camino normal de release (corta y envía).
function _fijarHold(ctrl) {
  _pttUltimoTapTs = 0;
  if (!_controlActive[ctrl.id]) return;   // el hold abortó (sin target válido): nada que fijar
  _pttFijado = true;
  _pttMarcarFijado(ctrl);
}

// Variante TECLA (segundo tap del doble-tap): además resuelve el hold provisional
// del umbral de 220ms, que el mouse no tiene.
function _fijarDictado() {
  if (!_pttPendiente) return;
  const ctrl = _pttPendiente.ctrl;
  clearTimeout(_pttPendiente.timer);
  _pttPendiente = null;
  _fijarHold(ctrl);
}

// Tap de MOUSE (click más corto que el umbral): descartar la captura optimista
// SIN procesar — espejo exacto del tap de tecla (_cancelarPttPendiente), pero
// para holds de mouse, que no pasan por el hold provisional.
function _descartarHoldMouseCorto(ctrl) {
  if (!_controlActive[ctrl.id]) return;
  _controlActive[ctrl.id] = false;
  _cancelOnSound();          // fue un click corto: la grabación no llegó a sonar
  _ocultarPttIndicator(ctrl);
  ctrl.onDiscard?.();
}

// La píldora en modo rec pasa a avisar cómo se cierra el dictado fijado.
function _pttMarcarFijado(ctrl) {
  const hint = document.getElementById('ptt-indicator')?.querySelector('.vp-hint');
  if (!hint) return;
  hint.textContent = window.PttFijado?.hintFijado(
    _renderBindingLabel(_controlBindings[ctrl.id]), window.JarvisI18n?.t) ?? '';
}

function _triggerHold(ctrl) {
  if (_controlActive[ctrl.id]) return;
  _controlActive[ctrl.id] = true;
  ctrl.onHold?.();
  _mostrarPttIndicator(ctrl);
}

function _triggerRelease(ctrl) {
  if (!_controlActive[ctrl.id]) return;
  _controlActive[ctrl.id] = false;
  if (ctrl.id === 'mic-ptt') _pttFijado = false;   // el release cierra también un dictado fijado
  ctrl.onRelease?.();
  if (ctrl.id === 'mic-ptt') {
    // Soltó: si el "on" todavía no sonó la grabación fue más corta que el umbral
    // (tap de mouse/tecla no-modificadora) → sin "off" tampoco. Si ya sonó, "off".
    if (_onSoundTimer) _cancelOnSound(); else _sonarVoz('off');
    // Si onRelease dejó la píldora en "proc" (Transcribiendo… — el cierre corre
    // recién en el onstop del recorder, tras la cola), NO pisarla con idle: el
    // finally de _finalizarDictado la lleva a idle cuando el dictado termina.
    // (El chequeo viejo por _finalizandoGen ya no alcanza: desde 2026-07-17 el
    // finalize es asíncrono al release y acá todavía no corrió.)
    const enProc = document.getElementById('ptt-indicator')?.dataset.mode === 'proc';
    if (!enProc && _finalizandoGen !== _micGen) _lingerVoicePill(ctrl);
  } else {
    _ocultarPttIndicator(ctrl);
  }
}

// Botones laterales del mouse (3=back, 4=forward) → el browser los usa para
// navegar. Hay que cancelar SIEMPRE su acción default, sino el usuario se va de
// la página antes de que nuestro handler haga algo útil. Esto se hace en fase
// de captura para llegar primero que cualquier otro listener.
function _esBotonLateral(button) { return button === 3 || button === 4; }

function instalarControles() {
  _initControlBindings();
  _instalarLiberacionMicCicloVida();   // soltar el mic al ocultar/descargar la página (no retenerlo en background)

  // ─── KILLSWITCH global para botones laterales del mouse ─────────
  // SIEMPRE preventDefault para mouse3/mouse4 (back/forward) mientras la app
  // está cargada — no queremos navegación accidental nunca. Esto también
  // garantiza que el evento llegue a nuestros handlers en vez de irse al
  // browser. Si querés volver a tener back/forward del mouse, comentás esto.
  const _killLateralNav = (e) => {
    if (!_esBotonLateral(e.button)) return;
    e.preventDefault();
    e.stopPropagation();
  };
  // Capture-phase: llegamos antes que cualquier otro listener del documento
  document.addEventListener('mousedown', _killLateralNav, { capture: true });
  document.addEventListener('mouseup',   _killLateralNav, { capture: true });
  document.addEventListener('auxclick',  _killLateralNav, { capture: true });
  // Algunos browsers también disparan 'click' para mouse4/5
  document.addEventListener('click', (e) => {
    if (_esBotonLateral(e.button)) { e.preventDefault(); e.stopPropagation(); }
  }, { capture: true });

  // ─── Teclado ────────────────────────────────────────────────────
  document.addEventListener('keydown', (e) => {
    if (_capturando) return;

    // Si hay un hold de modificador pendiente y llega una tecla que NO es el
    // propio modificador bindeado, el usuario está tipeando (Alt+combo, AltGr
    // para un símbolo, etc.): cancelamos el pendiente y dejamos pasar la tecla
    // SIN preventDefault para no romper el tipeo.
    if (_pttPendiente) {
      const bp = _controlBindings[_pttPendiente.ctrl.id];
      if (!(bp && bp.type === 'key' && _codeMatchea(bp.value, e.code))) {
        _cancelarPttPendiente();
        // no preventDefault: que el símbolo/atajo del usuario funcione
      }
    }

    for (const c of CONTROLS) {
      if (c.mode !== 'hold') continue;   // mode:'press' se maneja en su listener propio
      const b = _controlBindings[c.id];
      if (!b || b.type !== 'key') continue;
      if (!_codeMatchea(b.value, e.code)) continue;
      if (_esTargetEditable(e.target) && !_esTeclaModificadora(e.code)) continue;
      if (_controlActive[c.id] || _pttPendiente) { e.preventDefault(); return; }  // ignorar repeats / re-armar
      if (_esTeclaModificadora(e.code)) {
        // Bindings de modificador: armar hold con umbral (no grabar al toque).
        e.preventDefault();
        _armarHoldConUmbral(c);
        return;
      }
      // Bindings de tecla no-modificadora: hold inmediato (comportamiento actual).
      e.preventDefault();
      _triggerHold(c);
      return;
    }
  });

  document.addEventListener('keyup', (e) => {
    // Tap del modificador bindeado más corto que el umbral → cancelar pendiente
    // en silencio (sin grabar, sin toast). preventDefault: sin él, soltar Alt
    // "suelto" activa el menú del browser (Chrome/Edge en Windows) y el foco
    // del teclado se va de la página — el próximo Ctrl+A ya no le pega al
    // composer. El preventDefault del keydown solo NO alcanza: Chrome decide
    // abrir el menú en el keyup.
    if (_pttPendiente && _codeMatchea(_controlBindings[_pttPendiente.ctrl.id]?.value, e.code)) {
      e.preventDefault();
      const eraMic = _pttPendiente.ctrl.id === 'mic-ptt';
      // Doble-tap del mic → dictado FIJADO: confirmar el hold provisional (que
      // ya grababa desde el keydown) y soltar la tecla sin cortar la grabación.
      if (eraMic && window.PttFijado?.esDobleTap(performance.now(), _pttUltimoTapTs)) {
        _fijarDictado();
        return;
      }
      if (eraMic) _pttUltimoTapTs = performance.now();
      _cancelarPttPendiente();
      if (eraMic) _summonVoicePill();   // tap limpio de la tecla de voz → píldora idle con opciones
      return;
    }
    for (const c of CONTROLS) {
      if (c.mode !== 'hold') continue;   // mode:'press' no tiene hold/release
      if (!_controlActive[c.id]) continue;
      const b = _controlBindings[c.id];
      if (!b || b.type !== 'key') continue;
      if (!_codeMatchea(b.value, e.code)) continue;
      // Mismo motivo: que el Alt-keyup que termina la grabación no abra el
      // menú del browser y robe el foco que ta.focus() acaba de poner en el
      // chat (procesarAudio). AltGr no entra acá: _codeMatchea no matchea
      // AltRight contra un binding AltLeft, así que los símbolos @{} siguen.
      e.preventDefault();
      _triggerRelease(c);
    }
  });

  // ─── Mouse ──────────────────────────────────────────────────────
  // Usamos capture:true así nos enteramos ANTES que cualquier handler de la
  // app y el browser pierda la chance de hacer back/forward.
  document.addEventListener('mousedown', (e) => {
    if (_capturando) return;
    for (const c of CONTROLS) {
      if (c.mode !== 'hold') continue;   // mode:'press' no usa hold de mouse
      const b = _controlBindings[c.id];
      if (!b || b.type !== 'mouse') continue;
      if (e.button !== b.value) continue;
      // Para click izq/medio/der: respetar elementos interactivos. Para
      // mouse4/5: siempre actuar (nadie los usa para UI normal).
      if (!_esBotonLateral(e.button) &&
          e.target.closest('button, a, input, textarea, select')) continue;
      if (_controlActive[c.id]) { e.preventDefault(); return; }
      e.preventDefault();
      if (c.id === 'mic-ptt') _mouseHoldT0 = performance.now();
      _triggerHold(c);
      return;
    }
  }, { capture: true });

  document.addEventListener('mouseup', (e) => {
    for (const c of CONTROLS) {
      if (c.mode !== 'hold') continue;   // mode:'press' no tiene mouseup hold
      if (!_controlActive[c.id]) continue;
      const b = _controlBindings[c.id];
      if (!b || b.type !== 'mouse') continue;
      if (e.button !== b.value) continue;
      // Botón de voz: un click corto es TAP (no dictado) y dos taps seguidos
      // FIJAN la grabación — espejo del doble-tap de tecla. El click que corta
      // un dictado ya fijado NO entra acá (guard _pttFijado): va directo al
      // release (corta y envía), igual que el hold sostenido de siempre.
      if (c.id === 'mic-ptt' && !_pttFijado) {
        const ahora = performance.now();
        const accion = window.PttFijado?.alMouseUp?.({
          durMs: ahora - _mouseHoldT0, ahoraMs: ahora,
          ultimoTapMs: _pttUltimoTapTs, umbralTapMs: _PTT_UMBRAL_MS,
        }) ?? 'soltar';
        if (accion === 'fijar') { _fijarHold(c); return; }
        if (accion === 'tap') {
          _pttUltimoTapTs = ahora;
          _descartarHoldMouseCorto(c);
          _summonVoicePill();   // click limpio del botón de voz → píldora idle con opciones
          return;
        }
      }
      _triggerRelease(c);
    }
  }, { capture: true });

  // Safety: si la ventana pierde foco mientras un hold está activo, liberar.
  // Alt suele abrir el menú del browser / perder foco: también cancelamos
  // cualquier hold pendiente de umbral para no quedar grabando "fantasma".
  // EXCEPCIÓN dictado FIJADO: si el "blur" es porque el foco entró a un iframe
  // de ESTA página (Web Preview / browser remoto), seguimos grabando — pasear
  // por el workspace es el punto del modo. Blur real (otra app) corta y envía:
  // jamás mic caliente fuera de la página.
  window.addEventListener('blur', () => {
    _cancelarPttPendiente();
    for (const c of CONTROLS) {
      if (c.mode !== 'hold') continue;   // mode:'press' no tiene estado activo que liberar
      if (!_controlActive[c.id]) continue;
      if (c.id === 'mic-ptt' && window.PttFijado?.alBlur({
        fijado: _pttFijado, tagActivo: document.activeElement?.tagName,
      }) === 'mantener') continue;
      _triggerRelease(c);
    }
  });

  // Esc durante un dictado FIJADO: cancelar SIN enviar. Capture-phase para
  // tragarnos el Esc antes de que des-maximice cards, cierre modales o llegue
  // al PTY de una terminal enfocada.
  document.addEventListener('keydown', (e) => {
    if (e.code !== 'Escape' || !_pttFijado) return;
    e.preventDefault();
    e.stopPropagation();
    _pttFijado = false;
    _controlActive['mic-ptt'] = false;
    _sonarVoz('off');
    window._orchOnMicDiscard?.();   // frena mic + SR sin transcribir ni enviar
    _ocultarPttIndicator();
  }, { capture: true });
}

// Alias para back-compat (lo llaman desde inicializar)
function instalarPushToTalk() { instalarControles(); }

// Bridge para xterm.js: terminal.js lo enchufa vía attachCustomKeyEventHandler
// para que las teclas bindeadas a controles funcionen aunque tengas foco en
// una terminal. Devuelve true si JARVIS "consume" la tecla (xterm debería
// ignorarla y no mandarla al PTY).
window._jarvisHandleControlKey = function (e) {
  if (_capturando) return false;
  // Atajos del editor cuando una terminal tiene foco (xterm captura el teclado):
  // delegar en JarvisEditor. Devolver true → xterm no manda la tecla al PTY.
  if (e.type === 'keydown' && (e.metaKey || e.ctrlKey) && e.shiftKey &&
      (e.code === 'KeyF' || e.key === 'F' || e.key === 'f')) {
    if (window.JarvisEditor) { window.JarvisEditor.openSearch(); return true; }
  }
  // ⌘P / Ctrl+P dentro de una terminal: abrir command palette en vez de mandar al PTY.
  if (e.type === 'keydown' && (e.metaKey || e.ctrlKey) && !e.altKey &&
      (e.key === 'p' || e.key === 'P' || e.code === 'KeyP')) {
    if (window.JarvisEditor?.openPalette) {
      window.JarvisEditor.openPalette(e.shiftKey ? 'comando' : 'archivo');
      return true;   // consumir: xterm no manda la tecla al PTY
    }
  }
  let matched = null;
  for (const c of CONTROLS) {
    if (c.mode !== 'hold') continue;   // mode:'press' usa listener propio en capture phase
    const b = _controlBindings[c.id];
    if (!b || b.type !== 'key') continue;
    if (!_codeMatchea(b.value, e.code)) continue;
    matched = c;
    break;
  }
  if (!matched) {
    // Tecla que NO matchea ningún binding mientras hay un hold pendiente: el
    // usuario está tipeando un combo (Alt+algo) en la terminal. Cancelamos el
    // pendiente como hace el handler global, pero NO consumimos la tecla
    // (return false → xterm la procesa normal).
    if (e.type === 'keydown' && _pttPendiente) _cancelarPttPendiente();
    return false;
  }

  if (e.type === 'keydown') {
    if (_controlActive[matched.id] || _pttPendiente) return true;  // repeat / ya armando
    if (_esTeclaModificadora(e.code)) {
      // Mismo umbral que el handler global: mantener el modificador solo ≥220ms
      // dispara el hold. Si en el ínterin llega otra tecla a xterm (combo del
      // usuario), el keydown de esa tecla pasa por acá de nuevo SIN matchear el
      // binding (matched=null arriba → return false), pero no cancela el
      // pendiente; lo cancela el keyup del modificador o el blur. Aceptable: el
      // umbral evita la grabación accidental al tipear rápido un Alt+combo.
      _armarHoldConUmbral(matched);
      return true;   // bloquear xterm — no mandar el modificador solo al PTY
    }
    _triggerHold(matched);
    return true;   // bloquear xterm — no mandar al PTY
  }
  if (e.type === 'keyup') {
    if (_pttPendiente && _pttPendiente.ctrl.id === matched.id) {
      const eraMic = matched.id === 'mic-ptt';
      // Mismo doble-tap → dictado FIJADO que el handler global (foco en xterm).
      if (eraMic && window.PttFijado?.esDobleTap(performance.now(), _pttUltimoTapTs)) {
        _fijarDictado();
        return true;
      }
      if (eraMic) _pttUltimoTapTs = performance.now();
      _cancelarPttPendiente();   // tap corto → no grabar
      if (eraMic) _summonVoicePill();   // tap limpio → píldora idle con opciones
      return true;
    }
    if (_controlActive[matched.id]) _triggerRelease(matched);
    return true;
  }
  return false;
};

// ─── Píldora de voz (HUD del dictado PTT) — estilo "bridgevoice" ──────────
// Pastilla compacta (#ptt-indicator, clase .voice-pill) con 3 estados por
// data-mode: rec (grabando, orbe + waveform + transcript), idle (resting/armed
// clickeable → popover de opciones) y warn (aviso efímero). Aparece al apretar
// la tecla de voz; un tap (sin grabar) la deja en idle para tocar las opciones.

// Preferencias de voz (localStorage). Las edita el popover y Configuración→Voz.
const _VOZ = {
  traducir:   () => localStorage.getItem('jarvis.voz.traducir')   === '1',     // dictado → inglés (Whisper translate)
  autoenviar: () => localStorage.getItem('jarvis.voz.autoenviar') === '1',     // al soltar, enviar el mensaje a Jarvis
  sonido:     () => localStorage.getItem('jarvis.voz.sonido')     !== 'off',   // bip al empezar/terminar de escuchar
  pill:       () => localStorage.getItem('jarvis.voz.pill')       !== 'off',   // mostrar la píldora idle (tap/linger)
};
// Dictado 100% server (2026-07-17): /transcribe resuelve con Groq
// (whisper-large-v3-turbo, ~1s) y cae solo a parakeet local si Groq falla.
// El SR cloud del browser (Google) se removió junto con la carrera SR-vs-server
// y su presupuesto de ~3s — ver memoria stt-groq-motor. El tope de acá es solo
// ANTI-CUELGUE, no de UX: en la doble degradación rara (sin Groq + parakeet
// frío en CPU ahogada) preferimos esperar y entregar antes que perder el
// dictado; pasado el tope se avisa "no te entendí".
const _TRANSCRIBE_TIMEOUT_MS = 30000;

// Bip de "escucha on/off" con WebAudio (reusa _tocarNotas). Distinto del acorde
// de TASK_DONE: dos notas, suave. Gateado por _VOZ.sonido().
function _sonarVoz(estado) {
  if (!_VOZ.sonido()) return;
  if (estado === 'on') _tocarNotas([{ freq: 587.33, start: 0, dur: 0.07 }, { freq: 880.00, start: 0.06, dur: 0.11 }], 'sine', 0.10);
  else                 _tocarNotas([{ freq: 784.00, start: 0, dur: 0.07 }, { freq: 523.25, start: 0.06, dur: 0.12 }], 'sine', 0.09);
}
// El "on" se programa con delay > umbral de tap así un toque corto (que no graba)
// no suena; el release/discard lo cancela si la grabación fue más corta que eso.
let _onSoundTimer = null;
function _scheduleOnSound() { _cancelOnSound(); _onSoundTimer = setTimeout(() => { _onSoundTimer = null; _sonarVoz('on'); }, 240); }
function _cancelOnSound()   { if (_onSoundTimer) { clearTimeout(_onSoundTimer); _onSoundTimer = null; } }

// SVGs inline (sin assets). Mic = mismo glifo que el control de voz.
const _VP_MIC   = `<svg viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="6.5" y="2.5" width="5" height="8.5" rx="2.4"/><path d="M3.7 9.1a5.3 5.3 0 0010.6 0"/><line x1="9" y1="14.5" x2="9" y2="16.2"/></svg>`;
const _VP_GEAR  = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`;
const _VP_TRANS = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="m5 8 6 6"/><path d="m4 14 6-6 2-3"/><path d="M2 5h12"/><path d="M7 2h1"/><path d="m22 22-5-10-5 10"/><path d="M14 18h6"/></svg>`;
const _VP_SND   = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6 9H2v6h4l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M19 5a9 9 0 0 1 0 14"/></svg>`;
const _VP_SEND  = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4Z"/></svg>`;
const _VP_GRIP  = `<svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="5" cy="4" r="1.3"/><circle cx="11" cy="4" r="1.3"/><circle cx="5" cy="8" r="1.3"/><circle cx="11" cy="8" r="1.3"/><circle cx="5" cy="12" r="1.3"/><circle cx="11" cy="12" r="1.3"/></svg>`;

const _VP_BARS = 15;   // barras del waveform "flow" (simétrico, reactivo al volumen)

function _pttIndicatorEl() {
  let el = document.getElementById('ptt-indicator');
  if (!el) {
    el = document.createElement('div');
    el.id = 'ptt-indicator';
    el.className = 'voice-pill';
    el.dataset.mode = 'rec';
    const barras = Array.from({ length: _VP_BARS }, (_, i) => `<i style="--i:${i}"></i>`).join('');
    el.innerHTML = `
      <span class="vp-orb" aria-hidden="true">${_VP_MIC}<span class="vp-rec-dot"></span></span>
      <span class="vp-wave" aria-hidden="true">${barras}</span>
      <span class="vp-text">
        <span class="vp-status"><span class="vp-status-word">Grabando</span><span class="vp-status-dest"></span><span class="vp-en" hidden>EN</span></span>
        <span class="vp-transrow"><span class="vp-trans"></span><span class="vp-cursor" aria-hidden="true">▍</span></span>
      </span>
      <span class="vp-right">
        <span class="vp-kbd"></span>
        <span class="vp-hint">soltá para enviar</span>
      </span>
      <button type="button" class="vp-gear" aria-label="Opciones de voz" title="Opciones de voz">${_VP_GEAR}</button>`;
    document.body.appendChild(el);
    _aplicarPosPill(el);   // restaurar posición si el usuario la movió antes
    // Pointer: distingue arrastrar (mover la píldora) de click (abrir opciones).
    el.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      _cancelarAutoHide();   // mientras se presiona, ningún timer viejo la esconde
      _iniciarDragPill(e, el, () => { if (el.dataset.mode !== 'rec') _toggleVoicePop(); });
    });
    el.addEventListener('mouseenter', () => { _pillHover = true; _cancelarAutoHide(); });
    el.addEventListener('mouseleave', () => {
      _pillHover = false;
      if (el.dataset.mode === 'idle' && !_voicePopAbierto()) _programarAutoHide(1600);
    });
    window.addEventListener('resize', () => {
      if (el.classList.contains('moved')) _aplicarPosPill(el);
      if (_voicePopAbierto()) _posicionarVoicePop(document.getElementById('voice-pop'));
    });
  }
  return el;
}

// ── Arrastre de la píldora de voz (mover el menú) ─────────────────
const _PILL_POS_KEY = 'jarvis.voz.pos';
let _pillHover = false;   // cursor sobre la píldora: bloquea el auto-ocultado
function _leerPosPill() {
  try { const r = JSON.parse(localStorage.getItem(_PILL_POS_KEY)); if (r && Number.isFinite(r.left) && Number.isFinite(r.top)) return r; } catch {}
  return null;
}
function _clampPos(left, top, w, h) {
  const m = 8;
  return {
    left: Math.max(m, Math.min(left, window.innerWidth  - w - m)),
    top:  Math.max(m, Math.min(top,  window.innerHeight - h - m)),
  };
}
function _aplicarPosPill(el) {
  const pos = _leerPosPill();
  if (!pos) { el.classList.remove('moved'); el.style.left = el.style.top = el.style.bottom = ''; return; }
  el.classList.add('moved');
  const c = _clampPos(pos.left, pos.top, el.offsetWidth || 170, el.offsetHeight || 46);
  el.style.left = c.left + 'px'; el.style.top = c.top + 'px'; el.style.bottom = 'auto';
}
// Re-clampea una píldora movida cuando un cambio de modo la ensancha (ej. warn con
// texto largo): sin esto, parkeada en el borde derecho se salía de pantalla.
function _reanchorPill() {
  const el = document.getElementById('ptt-indicator');
  if (el && el.classList.contains('moved')) _aplicarPosPill(el);
}
// Arranca un arrastre. `pillEl` = elemento a mover (#ptt-indicator). `onClick` =
// fallback si NO hubo arrastre (umbral 5px) — null para handles que solo mueven (grip).
function _iniciarDragPill(e, pillEl, onClick) {
  if (!pillEl) return;
  const r0 = pillEl.getBoundingClientRect();
  const sx = e.clientX, sy = e.clientY, baseL = r0.left, baseT = r0.top;
  let moved = false;
  try { e.target.setPointerCapture?.(e.pointerId); } catch {}
  const onMove = (ev) => {
    const dx = ev.clientX - sx, dy = ev.clientY - sy;
    if (!moved && Math.hypot(dx, dy) < 5) return;
    if (!moved) { moved = true; pillEl.classList.add('moved', 'dragging'); }
    _cancelarAutoHide();   // cada frame: que un mouseleave durante el arrastre no la esconda
    const c = _clampPos(baseL + dx, baseT + dy, pillEl.offsetWidth, pillEl.offsetHeight);
    pillEl.style.left = c.left + 'px'; pillEl.style.top = c.top + 'px'; pillEl.style.bottom = 'auto';
    if (_voicePopAbierto()) _posicionarVoicePop(document.getElementById('voice-pop'));
  };
  const _FIN = ['pointerup', 'pointercancel', 'lostpointercapture'];
  const onUp = () => {
    document.removeEventListener('pointermove', onMove, true);
    _FIN.forEach(t => document.removeEventListener(t, onUp, true));
    pillEl.classList.remove('dragging');
    if (moved) {
      const r = pillEl.getBoundingClientRect();
      localStorage.setItem(_PILL_POS_KEY, JSON.stringify({ left: Math.round(r.left), top: Math.round(r.top) }));
      // re-arma el auto-ocultado; su callback igual aborta si seguís encima (_pillHover).
      if (pillEl.dataset.mode === 'idle' && !_voicePopAbierto()) _programarAutoHide();
    } else if (onClick) {
      onClick();
    }
  };
  document.addEventListener('pointermove', onMove, true);
  // pointercancel / lostpointercapture además de pointerup: sin esto un pointer
  // cancelado (touch/pen, blur de ventana, chord de mouse) dejaba onMove colgado
  // y la píldora perseguía al cursor (mismo guard que panel.js/terminal-layout.js).
  _FIN.forEach(t => document.addEventListener(t, onUp, true));
}

let _warnTimer = null;

function _mostrarPttIndicator(ctrl) {
  // onHold pudo abortar (target inválido → _cancelarHoldsActivos): no pisar el warn.
  if (!_controlActive[ctrl.id]) return;
  const el = _pttIndicatorEl();
  _cancelarAutoHide();
  _cerrarVoicePop();   // si había un popover de opciones abierto, cerralo al grabar
  el.classList.remove('warn');
  if (_warnTimer) { clearTimeout(_warnTimer); _warnTimer = null; }

  const b = _controlBindings[ctrl.id];
  const dest = _voiceTargetLabel(_activeVoiceSession || _resolveVoiceTarget());

  el.dataset.mode = 'rec';
  el.querySelector('.vp-status-dest').textContent = ' · ' + dest;
  el.querySelector('.vp-trans').textContent = '';
  el.querySelector('.vp-kbd').textContent   = _renderBindingLabel(b);
  el.querySelector('.vp-en').hidden = !_VOZ.traducir();
  const hint = el.querySelector('.vp-hint'); if (hint) hint.textContent = 'soltá para enviar';
  el.dataset.ctrl = ctrl.id;
  el.classList.add('visible');
  _reanchorPill();
  _scheduleOnSound();
  // (El campo de escucha NO se enciende acá: lo prende iniciarGrabacion recién
  // con el destino resuelto y solo en la pantalla de arranque — encenderlo por
  // el hold daba un flash cuando el dictado se abortaba por falta de destino.)
}

// Oculta del todo (discard/tap-combo/cancelarHolds o si la píldora idle está off).
function _ocultarPttIndicator(_ctrl) {
  _detenerWaveform();
  window.JarvisVoiceField?.apagar?.();
  _cancelOnSound();
  _cancelarAutoHide();
  _cerrarVoicePop();
  const el = document.getElementById('ptt-indicator');
  if (el) {
    el.classList.remove('visible', 'warn');
    delete el.dataset.ctrl;
    const txt = el.querySelector('.vp-trans');
    if (txt) txt.textContent = '';   // limpiar para el próximo dictado
  }
}

// Pone la píldora en estado idle (orbe + waveform tenue + chip + engranaje),
// lista para clickear sus opciones. Reutiliza el mismo elemento.
function _refrescarPillIdle(el) {
  if (_warnTimer) { clearTimeout(_warnTimer); _warnTimer = null; }   // matar un warn pendiente que escondería la píldora idle antes de tiempo
  el.dataset.mode = 'idle';
  el.classList.remove('warn');
  const b = _controlBindings['mic-ptt'];
  el.querySelector('.vp-kbd').textContent = b ? _renderBindingLabel(b) : '';
  el.querySelector('.vp-status-dest').textContent = '';
  el.querySelector('.vp-trans').textContent = '';
  el.querySelector('.vp-en').hidden = !_VOZ.traducir();
  _reanchorPill();
}

// Tap deliberado de la tecla de voz (sin llegar a grabar): muestra la píldora
// idle para que puedas tocar sus opciones. "Apretás una tecla → aparece el cuadro".
function _summonVoicePill() {
  if (!_VOZ.pill()) return;
  const el = _pttIndicatorEl();
  _detenerWaveform();
  _cancelOnSound();
  _refrescarPillIdle(el);
  el.classList.add('visible');
  _programarAutoHide();
}

// Modo "procesando" (data-mode=proc): tras soltar, mientras la ventana de gracia
// espera que el SR termine de volcar y se procesa el dictado, el orbe del HUD
// muestra un spinner de "cargando" (sin texto). El CSS lo dibuja por data-mode.
function _pttProcesando() {
  const el = document.getElementById('ptt-indicator');
  if (!el) return;
  if (_warnTimer) { clearTimeout(_warnTimer); _warnTimer = null; }
  el.classList.remove('warn');
  el.dataset.mode = 'proc';
  // "Cargando" LEGIBLE (pedido 2026-07-17, ronda 2): el anillo solo sobre el
  // orbe no se leía como carga — la píldora ahora dice qué está haciendo.
  const tr = el.querySelector('.vp-trans');
  if (tr) tr.textContent = window.JarvisI18n?.t?.('Transcribiendo…') ?? 'Transcribiendo…';
  el.classList.add('visible');
  _reanchorPill();
  window.JarvisVoiceField?.procesando?.();   // el campo queda encendido, ya sin seguir la voz
}

// Al soltar tras una grabación real: en vez de desaparecer, la píldora queda en
// idle un rato (clickeable) y después se auto-oculta.
function _lingerVoicePill(ctrl) {
  _detenerWaveform();
  window.JarvisVoiceField?.apagar?.();   // el dictado terminó: la ventana se apaga
  if (!_VOZ.pill()) { _ocultarPttIndicator(ctrl); return; }
  const el = document.getElementById('ptt-indicator');
  if (!el) return;
  _refrescarPillIdle(el);
  el.classList.add('visible');
  _programarAutoHide();
}

// Auto-ocultado de la píldora idle (no aplica en rec ni con el popover abierto).
let _pillHideTimer = null;
function _programarAutoHide(ms = 4200) {
  _cancelarAutoHide();
  _pillHideTimer = setTimeout(() => {
    _pillHideTimer = null;
    if (_voicePopAbierto() || _pillHover) return;   // popover abierto o cursor encima: no esconder
    const el = document.getElementById('ptt-indicator');
    if (el && el.dataset.mode === 'idle') el.classList.remove('visible');
  }, ms);
}
function _cancelarAutoHide() { if (_pillHideTimer) { clearTimeout(_pillHideTimer); _pillHideTimer = null; } }

// ── Popover de opciones de voz ────────────────────────────────────
function _voicePopEl() {
  let pop = document.getElementById('voice-pop');
  if (!pop) {
    pop = document.createElement('div');
    pop.id = 'voice-pop';
    pop.className = 'vp-pop';
    document.body.appendChild(pop);
  }
  return pop;
}
function _voicePopAbierto() { const p = document.getElementById('voice-pop'); return !!(p && p.classList.contains('visible')); }

const _VOZ_OPTS = [
  { key: 'traducir',   icon: _VP_TRANS, tit: 'Traducir a inglés',   sub: 'Transcribe tu voz y la pasa a inglés',     ls: 'jarvis.voz.traducir',   on: '1',  off: '0' },
  { key: 'sonido',     icon: _VP_SND,   tit: 'Sonido al dictar',    sub: 'Un bip al empezar y al terminar de escuchar', ls: 'jarvis.voz.sonido',  on: 'on', off: 'off' },
  { key: 'autoenviar', icon: _VP_SEND,  tit: 'Auto-enviar a Jarvis', sub: 'Manda el mensaje al soltar la tecla',      ls: 'jarvis.voz.autoenviar', on: '1',  off: '0' },
];

function _renderVoicePop(pop) {
  const b = _controlBindings['mic-ptt'];
  const kbd = b ? _renderBindingLabel(b) : '—';
  const filas = _VOZ_OPTS.map(o => `
    <div class="vp-opt" data-key="${o.key}" data-on="${_VOZ[o.key]() ? '1' : '0'}" role="switch" aria-checked="${_VOZ[o.key]()}" tabindex="0">
      <span class="vp-opt-ic">${o.icon}</span>
      <span class="vp-opt-tx"><span class="vp-opt-tit">${o.tit}</span><span class="vp-opt-sub">${o.sub}</span></span>
      <span class="vp-sw" aria-hidden="true"></span>
    </div>`).join('');
  pop.innerHTML = `
    <div class="vp-pop-head">
      <span class="vp-grip" aria-label="Mover" title="Mover">${_VP_GRIP}</span>
      <span class="vp-orb" aria-hidden="true">${_VP_MIC}</span>
      <span class="vp-pop-title">Voz</span>
    </div>
    ${filas}
    <div class="vp-pop-div"></div>
    <div class="vp-pop-foot">
      <span class="vp-foot-hint"><span class="vp-kbd">${kbd}</span> para hablar</span>
      <button type="button" class="vp-foot-link">Cambiar atajo</button>
    </div>`;
  pop.querySelectorAll('.vp-opt').forEach(fila => {
    const toggle = () => {
      const o = _VOZ_OPTS.find(x => x.key === fila.dataset.key);
      const nuevo = !_VOZ[o.key]();
      localStorage.setItem(o.ls, nuevo ? o.on : o.off);
      fila.dataset.on = nuevo ? '1' : '0';
      fila.setAttribute('aria-checked', String(nuevo));
      // Reflejar al instante en la píldora (badge EN) y dar feedback de sonido.
      const pill = document.getElementById('ptt-indicator');
      if (pill) pill.querySelector('.vp-en').hidden = !_VOZ.traducir();
      if (o.key === 'sonido' && nuevo) _sonarVoz('on');
      // Al activar traducir: pre-descargar el modelo on-device (gesto del usuario →
      // Chrome permite la descarga) para que el primer dictado ya sea instantáneo.
      if (o.key === 'traducir' && nuevo) _precargarTraductorChrome();
    };
    fila.addEventListener('click', toggle);
    // Operable por teclado (role=switch): Space/Enter alternan.
    fila.addEventListener('keydown', (e) => {
      if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); toggle(); }
    });
  });
  // Grip → arrastra la píldora (y el popover la sigue). Mueve el menú de voz.
  pop.querySelector('.vp-grip')?.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    _iniciarDragPill(e, document.getElementById('ptt-indicator'), null);
  });
  pop.querySelector('.vp-foot-link')?.addEventListener('click', () => {
    _cerrarVoicePop();
    window.JarvisSettings?.open?.('voz');
  });
}

function _posicionarVoicePop(pop) {
  const pill = document.getElementById('ptt-indicator');
  if (!pill) { pop.style.left = '50%'; pop.style.bottom = '80px'; pop.style.top = 'auto'; return; }
  const r = pill.getBoundingClientRect();
  const W = pop.offsetWidth || 270, H = pop.offsetHeight || 240;
  // Centro horizontal clampeado al viewport (la píldora puede estar en un borde).
  const half = W / 2 + 8;
  pop.style.left = Math.max(half, Math.min(r.left + r.width / 2, window.innerWidth - half)) + 'px';
  // Arriba de la píldora si hay lugar; si está muy arriba, debajo.
  if (r.top - H - 10 > 8) {
    pop.style.bottom = (window.innerHeight - r.top + 10) + 'px';
    pop.style.top = 'auto';
  } else {
    pop.style.top = (r.bottom + 10) + 'px';
    pop.style.bottom = 'auto';
  }
}

function _abrirVoicePop() {
  const pop = _voicePopEl();
  _renderVoicePop(pop);
  _posicionarVoicePop(pop);
  pop.classList.add('visible');
  _cancelarAutoHide();
  setTimeout(() => {
    document.addEventListener('click', _onOutsideVoicePop, true);
    document.addEventListener('keydown', _onEscVoicePop, true);
  }, 0);
}
function _cerrarVoicePop() {
  const pop = document.getElementById('voice-pop');
  if (!pop || !pop.classList.contains('visible')) return;
  pop.classList.remove('visible');
  document.removeEventListener('click', _onOutsideVoicePop, true);
  document.removeEventListener('keydown', _onEscVoicePop, true);
  const el = document.getElementById('ptt-indicator');
  if (el && el.dataset.mode === 'idle' && el.classList.contains('visible')) _programarAutoHide(2200);
}
function _toggleVoicePop() { _voicePopAbierto() ? _cerrarVoicePop() : _abrirVoicePop(); }
function _onOutsideVoicePop(e) {
  const pop = document.getElementById('voice-pop');
  const pill = document.getElementById('ptt-indicator');
  if (pop && pop.contains(e.target)) return;
  if (pill && pill.contains(e.target)) return;
  _cerrarVoicePop();
}
function _onEscVoicePop(e) { if (e.key === 'Escape') { e.stopPropagation(); _cerrarVoicePop(); } }

// ── Waveform reactivo al MICRÓFONO REAL ───────────────────────────
// Cuelga un AnalyserNode del stream de getUserMedia y, por rAF, mueve las barras
// del ecualizador (.vp-wave i, por scaleY) con el audio real. Matemática en
// window.JarvisAudioMeter (shared/audio-meter.js, testeada en Node).
let _wfCtx = null, _wfAnalyser = null, _wfRaf = 0;
// Métricas de captura del dictado en curso (pico |x| 0..1 y muestras clipeadas):
// las escribe el tick del waveform y las lee procesarAudio para diagnosticar
// mic bajo / saturado (diagnosticoMic, shared/stt-jerga.js).
let _micPico = 0, _micClips = 0;
let _avisoBtMostrado = false;   // el aviso de auricular BT va UNA vez por sesión

function _iniciarWaveform(stream) {
  const M = window.JarvisAudioMeter;
  if (!M || !stream) return;
  // prefers-reduced-motion: no arrancamos el rAF (ni escribimos transform inline);
  // el CSS deja las barras estáticas (.vp-wave i { animation:none; transform:scaleY(.55) }).
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches) return;
  try {
    _wfCtx = new (window.AudioContext || window.webkitAudioContext)();
    const src = _wfCtx.createMediaStreamSource(stream);
    _wfAnalyser = _wfCtx.createAnalyser();
    _wfAnalyser.fftSize = 256;
    _wfAnalyser.smoothingTimeConstant = 0.55;
    src.connect(_wfAnalyser);
  } catch { _wfCtx = null; _wfAnalyser = null; return; }   // sin Web Audio: HUD anda igual, sin waveform

  const freq = new Uint8Array(_wfAnalyser.frequencyBinCount);
  const td   = new Float32Array(_wfAnalyser.fftSize);
  _micPico = 0; _micClips = 0;   // métricas frescas por dictado
  _micVozTs = 0;                 // sello de voz fresco (un TS viejo cerraría la cola de una)
  const bars = Array.from(document.querySelectorAll('#ptt-indicator .vp-wave i'));
  const sm   = bars.map(() => 0.12);
  const tick = () => {
    if (!_wfAnalyser) return;
    _wfAnalyser.getByteFrequencyData(freq);
    // Pico y clipping del mismo AnalyserNode (cuesta ~nada): alimentan el
    // diagnóstico de "mic muy bajo / saturado" al soltar el PTT.
    _wfAnalyser.getFloatTimeDomainData(td);
    let frameMax = 0;
    for (let i = 0; i < td.length; i++) {
      const a = Math.abs(td[i]);
      if (a > frameMax) frameMax = a;
      if (a > _micPico) _micPico = a;
      if (a >= 0.99) _micClips++;
    }
    // ¿Este frame tiene VOZ? Umbral RELATIVO al pico del dictado (25%) con piso
    // absoluto: funciona igual con el mic bajo del usuario (picos ~0.02) que con
    // uno sano. Alimenta el cierre temprano de la cola post-soltar (_micVozTs).
    // Piso 0.008 → 0.004 (≈-48dBFS, pedido 2026-07-17 "que me escuche más de
    // lejos"): hablando a distancia los frames con voz quedaban bajo el piso,
    // la cola los leía como silencio y cortaba el final del dictado. Alineado
    // con el trim del server (-50dB, voice.py _RECORTE).
    if (frameMax > Math.max(0.004, _micPico * 0.25)) _micVozTs = performance.now();
    // flujo() reparte la energía simétrica + reactiva al volumen → TODAS las barras
    // se mueven (no solo la de los graves, como pasaba con barras()).
    const hs = M.flujo(freq, bars.length);
    for (let i = 0; i < bars.length; i++) {
      sm[i] = M.suavizar(sm[i], hs[i]);
      bars[i].style.transform = `scaleY(${(0.12 + sm[i] * 0.88).toFixed(3)})`;
    }
    // Publicar nivel + espectro para la CONSTELACIÓN del orquestador (reacciona
    // a tu voz real). Cuesta ~nada: reusamos el mismo AnalyserNode del PTT.
    let sum = 0; for (let i = 0; i < freq.length; i++) sum += freq[i];
    window._orchVoiceLevel = Math.min(1, (sum / freq.length / 255) * 2.4);
    let vb = window._orchVoiceBins;
    if (!vb || vb.length !== 64) vb = window._orchVoiceBins = new Array(64).fill(0);
    for (let i = 0; i < 64; i++) {
      const idx = Math.min(freq.length - 1, Math.floor(Math.pow(i / 64, 1.6) * freq.length));
      vb[i] = freq[idx] / 255;
    }
    _wfRaf = requestAnimationFrame(tick);
  };
  _wfRaf = requestAnimationFrame(tick);
}

function _detenerWaveform() {
  if (_wfRaf) { cancelAnimationFrame(_wfRaf); _wfRaf = 0; }
  _wfAnalyser = null;
  if (_wfCtx) { try { _wfCtx.close(); } catch {} _wfCtx = null; }
  document.querySelectorAll('#ptt-indicator .vp-wave i').forEach(b => { b.style.transform = ''; });
  // La constelación del orquestador vuelve a reposo (deja de reaccionar a la voz).
  window._orchVoiceLevel = 0;
  if (window._orchVoiceBins) window._orchVoiceBins.fill(0);
}

// Llamado desde iniciarGrabacion una vez que tenemos sesión confirmada:
// actualiza el destino mostrado con el target real.
function _actualizarPttIndicatorParaSesion() {
  const el = document.getElementById('ptt-indicator');
  if (!el || !el.classList.contains('visible') || el.dataset.mode !== 'rec') return;
  const dest = _voiceTargetLabel(_activeVoiceSession);
  const d = el.querySelector('.vp-status-dest');
  if (d) d.textContent = ' · ' + dest;
}

// Aviso efímero (estado warn de la píldora). Se auto-oculta a los 2.4s.
function _toastWarn(mensaje) {
  const el = _pttIndicatorEl();
  _detenerWaveform();
  _cancelOnSound();
  _cancelarAutoHide();
  _cerrarVoicePop();
  el.dataset.mode = 'warn';
  el.classList.add('visible');
  el.querySelector('.vp-status-dest').textContent = '';
  el.querySelector('.vp-trans').textContent = mensaje;
  _reanchorPill();   // el texto del warn la ensancha: re-clampear si está movida
  if (_warnTimer) clearTimeout(_warnTimer);
  _warnTimer = setTimeout(() => {
    _warnTimer = null;
    // No escondas si ya se pasó a otro estado (rec/idle) o hay un popover abierto.
    if (_voicePopAbierto() || el.dataset.mode !== 'warn') return;
    el.classList.remove('visible');
    el.dataset.mode = 'idle';
  }, 2400);
}

// Marca todos los controls hold como inactivos y oculta el indicador.
// Se usa cuando abortamos una grabación que no debería haber empezado
// (ej. Jarvis oculto sin terminal activa).
function _cancelarHoldsActivos() {
  for (const c of CONTROLS) {
    _controlActive[c.id] = false;
  }
  _pttFijado = false;
  _cancelOnSound();
  _ocultarPttIndicator();
}

// ─── Motor PTT (captura de teclas/mouse para bindings configurables) ──
// El modal Controles legacy se eliminó (Task 69); la UI vive en JarvisSettings (Voz).
// _refrescarControlesModal es un no-op que notifica a settings para refrescar su fila.

function _refrescarControlesModal() { window.JarvisControls?.onCambio?.(); }

// Selectores de la UI de captura: el botón que la abre y los controles que
// tienen que seguir respondiendo al click izquierdo mientras esperamos el
// binding. Cualquier OTRO botón del mouse se captura igual encima de ellos.
const _SEL_BOTON_BIND = '.set-keybind, .control-keybind, .kb-k';
const _SEL_UI_CAPTURA = '.set-keybind-reset, .control-clear, .settings-close, .gq-btn, .gq-mouse';

// cleanup() de la captura en curso, para no apilar listeners si se reabre.
let _capturaCleanup = null;

function _iniciarCaptura(id) {
  const ctrl = CONTROLS.find(c => c.id === id);
  if (!ctrl) return;
  // Reabrir la captura (segundo click en el botón) desarma la anterior: sin esto
  // quedaban dos juegos de listeners escuchando el mismo apretón.
  if (_capturaCleanup) _capturaCleanup();
  // El botón puede vivir en JarvisSettings (sección Voz). Si no hay botón visible
  // la captura igual procede: el binding se guarda y onCambio() repinta la fila.
  const btn = document.querySelector(`.set-keybind[data-id="${id}"], .control-keybind[data-id="${id}"]`);

  _capturando = true;
  if (btn) {
    const _t = window.JarvisI18n?.t || ((s) => s);
    btn.classList.add('capturando');
    btn.innerHTML = `<span class="settings-keybind-listening">${esc(_t('Apretá tecla o botón del mouse…'))}</span>`
                  + `<span class="settings-keybind-hint">${esc(_t('Esc cancela'))}</span>`;
  }

  const onKey = (e) => {
    if (e.key === 'Escape') { _cancelarCaptura(); cleanup(); return; }
    e.preventDefault();
    e.stopPropagation();
    if (!e.code || e.code === 'Unidentified') return;
    _controlBindings[id] = { type: 'key', value: e.code };
    _guardarBinding(id, _controlBindings[id]);
    _detenerCaptura();
    cleanup();
  };

  // Para controles mode:'press' (ej. quick-terminal) solo capturamos teclado.
  // El trigger exige type==='key', así que aceptar mouse guardaría un binding
  // inútil que haría morir el atajo silenciosamente.
  const aceptaMouse = ctrl.mode !== 'press';

  // El apretón se toma DONDE SEA, también encima del botón de reasignar: quien
  // quiere Mouse·adelante lo aprieta mirando el botón, que es lo natural. El
  // criterio (y por qué el click izquierdo sobre esa UI no cuenta) vive en
  // PttCaptura.decisionMouse.
  let _tomado = false;

  const onMouse = (e) => {
    const d = window.PttCaptura?.decisionMouse({
      button: e.button,
      enBotonBind: !!e.target?.closest?.(_SEL_BOTON_BIND),
      enUiCaptura: !!e.target?.closest?.(_SEL_UI_CAPTURA),
    }) ?? 'bindear';
    if (d !== 'bindear') return;
    e.preventDefault();
    e.stopPropagation();
    _tomado = true;
    _controlBindings[id] = { type: 'mouse', value: e.button };
    _guardarBinding(id, _controlBindings[id]);
    _detenerCaptura();
    cleanup();
  };

  // Después del mousedown, el browser dispara mouseup + auxclick + click.
  // Para mouse3/mouse4 esos eventos disparan back/forward; y el click del
  // apretón que ACABAMOS de tomar volvería a pegarle al botón de reasignar,
  // reabriendo la captura. Silenciarlos hasta que cleanup() los desinstale.
  const silenciarPostMouse = (e) => {
    if (window.PttCaptura?.debeTragar({ button: e.button, capturado: _tomado })
        ?? _esBotonLateral(e.button)) {
      e.preventDefault();
      e.stopPropagation();
    }
  };

  const desarmarPostMouse = () => {
    document.removeEventListener('mouseup',     silenciarPostMouse, true);
    document.removeEventListener('auxclick',    silenciarPostMouse, true);
    document.removeEventListener('click',       silenciarPostMouse, true);
    document.removeEventListener('contextmenu', silenciarPostMouse, true);
  };

  const cleanup = () => {
    if (_capturaCleanup === cleanup) _capturaCleanup = null;
    document.removeEventListener('keydown',   onKey,   true);
    if (aceptaMouse) {
      document.removeEventListener('mousedown', onMouse, true);
      // Si acabamos de tomar un botón, su mouseup/click siguen en camino:
      // soltamos los silenciadores recién en el próximo tick, ya pasados.
      if (_tomado) setTimeout(desarmarPostMouse, 0);
      else desarmarPostMouse();
    }
  };
  _capturaCleanup = cleanup;

  document.addEventListener('keydown',   onKey,            true);
  if (aceptaMouse) {
    document.addEventListener('mousedown',  onMouse,           true);
    document.addEventListener('mouseup',    silenciarPostMouse, true);
    document.addEventListener('auxclick',   silenciarPostMouse, true);
    document.addEventListener('click',      silenciarPostMouse, true);
    document.addEventListener('contextmenu', silenciarPostMouse, true);
  }

  // Timeout de la captura. Solo aplica si ESTA sigue siendo la captura viva:
  // si el usuario reabrió, la nueva tiene su propio reloj.
  setTimeout(() => {
    if (_capturaCleanup !== cleanup) return;
    if (_capturando) { _detenerCaptura(); cleanup(); }
  }, 15000);
}

function _cancelarCaptura() {
  _capturando = false;
  _refrescarControlesModal();
}

function _detenerCaptura() {
  _capturando = false;
  _refrescarControlesModal();
}

// Bridge para la pantalla de Configuración (sección Voz): editar/leer el PTT
// sin abrir el modal Controles legacy. Reusa el motor de captura existente.
window.JarvisControls = {
  list:  () => CONTROLS.map(c => ({ id: c.id, label: c.label, desc: c.desc, icon: c.iconSVG, mode: c.mode })),
  label: (id) => _renderBindingLabel(_controlBindings[id]),
  binding: (id) => {
    const b = _controlBindings[id];
    return b ? { type: b.type, value: b.value } : null;
  },
  capturar: (id) => _iniciarCaptura(id),
  setBinding: (id, binding) => {
    if (!CONTROLS.find(x => x.id === id) || !binding || !binding.type) return;
    if (binding.type === 'mouse' && !Number.isFinite(Number(binding.value))) return;
    const next = binding.type === 'mouse'
      ? { type: 'mouse', value: Number(binding.value) }
      : { type: binding.type, value: binding.value };
    _controlBindings[id] = next;
    _guardarBinding(id, next);
    // Un chip del picker elige el botón sin pasar por el mousedown de captura:
    // hay que cortar la escucha para que el keycap deje de decir "Apretá…".
    if (_capturaCleanup) { _capturando = false; _capturaCleanup(); }
    _refrescarControlesModal();
  },
  cancelarCaptura: () => {
    if (!_capturaCleanup) return;
    _cancelarCaptura();
    _capturaCleanup();
  },
  reset: (id) => { const c = CONTROLS.find(x => x.id === id); if (c) _resetBinding(c); },
  onCambio: null,   // settings.js asigna un callback para re-renderizar la fila
};

