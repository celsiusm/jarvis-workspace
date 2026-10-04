// JARVIS — Sonidos del aviso "un agente terminó / necesita respuesta".
//
// Lógica PURA (testeada en Node): los tonos disponibles, el volumen y las
// preferencias. El motor WebAudio (contexto, osciladores, dedup por terminal) vive
// en workspace.js, que le pide a plan() QUÉ notas tocar. Configuración → Voz →
// Avisos elige el tono y el volumen y los prueba.
//
// Compatibilidad: el default (tono 'acorde', volumen 40 → ganancia 0.16) es EXACTO
// el chime que sonaba antes, así que quien no toque nada oye lo mismo.
(function (global) {
  'use strict';

  const K_PERFIL = 'jarvis.sonidoTareas.perfil';
  const K_VOL = 'jarvis.sonidoTareas.vol';

  const PERFIL_DEFAULT = 'acorde';
  const VOL_DEFAULT = 40;       // % → ganancia pico 0.16 (el volumen histórico)
  const VOL_MIN = 5;
  const VOL_MAX = 100;
  const GANANCIA_MAX = 0.4;     // 100% → -8 dBFS: margen para notas que se solapan
  // "Necesito atención" sonaba un poco más fuerte que el "terminé" (0.18 vs 0.16).
  const FACTOR_ESPERA = 1.125;

  // `g` = peso de la nota dentro del tono (0-1): permite armar un timbre con varios
  // parciales simultáneos sin que su suma pase de la ganancia pico (clip).
  const PERFILES = {
    acorde: {
      label: 'Acorde', tipo: 'sine',
      notas: [   // C5 → E5 → G5 ascendente: "terminé bien"
        { freq: 523.25, start: 0.00, dur: 0.12 },
        { freq: 659.25, start: 0.10, dur: 0.12 },
        { freq: 783.99, start: 0.20, dur: 0.18 },
      ],
    },
    campana: {
      label: 'Campana', tipo: 'sine',
      notas: [   // fundamental + parciales: una campanita con cola larga
        { freq: 880.00, start: 0, dur: 0.90, g: 0.55 },
        { freq: 1760.0, start: 0, dur: 0.55, g: 0.25 },
        { freq: 2637.0, start: 0, dur: 0.30, g: 0.12 },
      ],
    },
    ding: {
      label: 'Ding', tipo: 'sine',
      notas: [   // dos notas altas, tipo timbre de mostrador
        { freq: 1318.5, start: 0.00, dur: 0.40, g: 0.8 },
        { freq: 1975.5, start: 0.11, dur: 0.55, g: 0.7 },
      ],
    },
    digital: {
      label: 'Digital', tipo: 'triangle',
      notas: [   // tres bips cortos, el último más agudo
        { freq: 988.0,  start: 0.00, dur: 0.09 },
        { freq: 988.0,  start: 0.13, dur: 0.09 },
        { freq: 1319.0, start: 0.26, dur: 0.18 },
      ],
    },
    suave: {
      label: 'Suave', tipo: 'sine',
      notas: [   // una sola nota larga y baja, para no sobresaltar
        { freq: 659.25, start: 0, dur: 0.60, g: 0.7 },
        { freq: 987.77, start: 0, dur: 0.45, g: 0.3 },
      ],
    },
  };
  const ORDEN = ['acorde', 'campana', 'ding', 'digital', 'suave'];

  // Aviso de ATENCIÓN (espera respuesta / bloqueado / error): igual en todos los
  // tonos — es otra señal ("mirame"), no el "terminé" que se personaliza.
  const ATENCION = {
    tipo: 'triangle',
    notas: [
      { freq: 392.00, start: 0.00, dur: 0.16 },
      { freq: 311.13, start: 0.14, dur: 0.22 },
    ],
  };

  function perfilValido(p) { return ORDEN.indexOf(p) >= 0 ? p : PERFIL_DEFAULT; }

  function volValido(v) {
    const n = Math.round(Number(v));
    if (!Number.isFinite(n)) return VOL_DEFAULT;
    return Math.min(VOL_MAX, Math.max(VOL_MIN, n));
  }

  // % de volumen → ganancia pico del oscilador.
  function gananciaDe(vol) { return GANANCIA_MAX * volValido(vol) / 100; }

  // Qué tocar para un evento: {notas, tipo, gain} | null (evento sin sonido).
  // 'TASK_DONE' = terminó · 'TASK_BLOCKED' / 'TASK_ERROR' = necesita atención.
  function plan(evento, prefs) {
    const p = prefs || {};
    const gain = gananciaDe(p.vol);
    if (evento === 'TASK_DONE') {
      const perfil = PERFILES[perfilValido(p.perfil)];
      return { notas: perfil.notas, tipo: perfil.tipo, gain };
    }
    if (evento === 'TASK_BLOCKED' || evento === 'TASK_ERROR') {
      return { notas: ATENCION.notas, tipo: ATENCION.tipo, gain: gain * FACTOR_ESPERA };
    }
    return null;
  }

  // Preferencias: `storage` = localStorage (puede tirar con el storage bloqueado).
  function leerPrefs(storage) {
    let perfil = PERFIL_DEFAULT, vol = VOL_DEFAULT;
    try {
      perfil = perfilValido(storage.getItem(K_PERFIL));
      const v = storage.getItem(K_VOL);
      vol = v == null ? VOL_DEFAULT : volValido(v);
    } catch (_) { /* sin storage: defaults */ }
    return { perfil, vol };
  }

  function guardarPrefs(storage, prefs) {
    const p = prefs || {};
    try {
      if (p.perfil !== undefined) storage.setItem(K_PERFIL, perfilValido(p.perfil));
      if (p.vol !== undefined) storage.setItem(K_VOL, String(volValido(p.vol)));
    } catch (_) { /* el storage puede estar bloqueado: queda solo en memoria de la sesión */ }
  }

  const api = {
    PERFILES, ORDEN, PERFIL_DEFAULT, VOL_DEFAULT, VOL_MIN, VOL_MAX,
    perfilValido, volValido, gananciaDe, plan, leerPrefs, guardarPrefs,
  };
  global.JarvisSonidoFin = Object.assign(global.JarvisSonidoFin || {}, api);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
