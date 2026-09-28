# plotspace/core/orq_contexto.py
"""Motor de contexto del orquestador: la foto COMPLETA del proyecto y del
enjambre que recibe `claude -p` en cada turno.

El gap que cierra: Jarvis ya sabía casi todo (fase y pregunta pendiente de
cada agente, si su CLI se cayó, qué archivos tocó, qué muestra su pane, qué
se le pidió, qué eventos cerró, git, dev servers, conflictos) pero el
orquestador recibía una línea por terminal. Orquestaba a ciegas: mandaba
trabajo a una terminal con una pregunta en pantalla o con el CLI muerto,
repetía trabajo ya commiteado y no veía por qué un agente se había bloqueado.

Diseño:
  · Lógica PURA de formato (testeable sin tmux, git ni DB) + recolección
    impura best-effort: cada fuente que falla degrada a "sin ese dato",
    jamás rompe el chat.
  · Presupuesto por bloque (caracteres): el contexto crece con el enjambre y
    el prompt viaja entero en cada turno. Lo más accionable va primero.
  · `es_libre` es la definición ÚNICA de "terminal libre": la usan el
    contexto y la guarda de enviar_prompt (antes era solo texto en
    el prompt y el código miraba otra cosa).
"""
import asyncio
import os
import re
import subprocess
import time

# ─── Presupuestos (caracteres) ────────────────────────────────────────────────
TOPE_TERMINALES = 9000
TOPE_COLA_PANE = 5000       # suma de las colas de pane de todas las terminales
LINEAS_COLA = 6
ANCHO_LINEA = 150
TOPE_GIT = 1800
TOPE_GUIA = 1800
TOPE_EVENTOS = 1500
TOPE_COORD = 1200
TOPE_DEV = 600

_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[@-Z\\-_]')
# Líneas que son solo marco/decoración de TUI (bordes de cajas, separadores).
_SOLO_MARCO_RE = re.compile(r'^[\s─━│┃╭╮╰╯┌┐└┘├┤┬┴┼═║╔╗╚╝\-_|>~·•.*]*$')

# ─── Qué se le mandó a cada terminal ──────────────────────────────────────────
# tid → (epoch, texto). Lo alimenta send_to_agent (tareas de spawn_terminal y
# enviar_prompt): sin esto una terminal con una tarea directa figuraba "libre".
_envios: dict = {}


def registrar_envio(terminal_id: int, texto: str) -> None:
    _envios[terminal_id] = (time.time(), (texto or '').strip())
    if len(_envios) > 512:
        for tid in sorted(_envios, key=lambda k: _envios[k][0])[:len(_envios) - 512]:
            _envios.pop(tid, None)


def ultimo_envio(terminal_id: int):
    return _envios.get(terminal_id)


# ─── Lógica pura ──────────────────────────────────────────────────────────────

def recortar(texto: str, tope: int) -> str:
    texto = texto or ''
    if len(texto) <= tope:
        return texto
    return texto[:max(0, tope - 20)].rstrip() + '\n  … (recortado)'


def hace(segundos) -> str:
    if segundos is None:
        return ''
    s = max(0, int(segundos))
    if s < 60:
        return f'{s}s'
    if s < 3600:
        return f'{s // 60} min'
    if s < 86400:
        return f'{s // 3600} h'
    return f'{s // 86400} d'


def una_linea(texto: str, ancho: int = 160) -> str:
    t = ' '.join((texto or '').split())
    return t if len(t) <= ancho else t[:ancho - 1] + '…'


def cola_pane(texto: str, lineas: int = LINEAS_COLA, ancho: int = ANCHO_LINEA) -> list:
    """Últimas líneas CON CONTENIDO del pane: sin ANSI, sin marcos de TUI."""
    limpio = _ANSI_RE.sub('', texto or '')
    utiles = [l.rstrip() for l in limpio.splitlines()
              if l.strip() and not _SOLO_MARCO_RE.match(l)]
    return [una_linea(l, ancho) for l in utiles[-lineas:]]


def es_libre(info: dict) -> bool:
    """Una terminal a la que se le puede dar trabajo nuevo: viva, quieta y sin
    una pregunta en pantalla."""
    if info.get('estado') in ('caido', 'sin_sesion'):
        return False
    if info.get('fase') in ('trabajando', 'arrancando'):
        return False
    return not info.get('esperando')


def motivo_no_libre(info: dict) -> str:
    """'' si está libre; si no, POR QUÉ (para el aviso de enviar_prompt)."""
    if info.get('estado') in ('caido', 'sin_sesion'):
        return 'su CLI está caído (no hay nadie que lea el mensaje)'
    if info.get('fase') == 'trabajando':
        return 'está trabajando ahora mismo'
    if info.get('fase') == 'arrancando':
        return 'todavía está arrancando'
    if info.get('esperando'):
        return 'tiene una pregunta en pantalla esperando respuesta'
    return ''


_ICONO_FASE = {'trabajando': '🟢 trabajando', 'idle': '⚪ quieta',
               'arrancando': '⏳ arrancando'}


def formatear_terminal(t: dict, info: dict) -> str:
    """Una terminal: cabecera con estado + detalle + cola del pane."""
    cab = [f"ID {t['id']}: {t['nombre']} ({t.get('tipo_ia') or 'manual'})"]
    if info.get('estado') in ('caido', 'sin_sesion'):
        cab.append('💀 CAÍDA — su CLI no está corriendo, NO le mandes nada')
    else:
        fase = _ICONO_FASE.get(info.get('fase') or '', '')
        if fase:
            dur = hace(info.get('fase_hace_s'))
            cab.append(f'{fase}' + (f' hace {dur}' if dur else ''))
        if info.get('esperando'):
            cab.append('❓ ESPERANDO RESPUESTA (hay una pregunta en su pantalla)')
    cab.append('LIBRE' if es_libre(info) else 'ocupada')
    lineas = ['- ' + ' — '.join(cab)]

    origen = info.get('origen')
    if origen and origen.get('orquestacion'):
        lineas.append(f"    lanzada por Jarvis para: {una_linea(origen['orquestacion'], 140)}")
    tarea = info.get('tarea')
    if tarea:
        lineas.append(f"    tarea: {una_linea(tarea['texto'], 220)}"
                      + (f" (hace {hace(tarea['hace_s'])})" if tarea.get('hace_s') is not None else ''))
    archivos = info.get('archivos') or []
    if archivos:
        vista = ', '.join(
            f"{a['path']}{' 🔒' if a.get('dueno') else ''}" for a in archivos[:6])
        if len(archivos) > 6:
            vista += f' (+{len(archivos) - 6} más)'
        lineas.append(f'    editó: {vista}')
    ev = info.get('ultimo_evento')
    if ev:
        det = f": {una_linea(ev['motivo'], 160)}" if ev.get('motivo') else ''
        lineas.append(f"    último cierre: {ev['event']} hace {hace(ev.get('hace_s'))}{det}")
    for d in info.get('dev_servers') or []:
        lineas.append(f'    dev server: {d}')
    cola = info.get('cola') or []
    if cola:
        lineas.append('    pantalla (últimas líneas):')
        lineas.extend(f'      | {l}' for l in cola)
    return '\n'.join(lineas)


def formatear_bloque_terminales(terminales: list, infos: dict,
                                tope: int = TOPE_TERMINALES) -> str:
    if not terminales:
        return 'No hay terminales activas.'
    libres = [t for t in terminales if es_libre(infos.get(t['id']) or {})]
    cab = f"Terminales activas: {len(terminales)} · libres: " + (
        ', '.join(f"#{t['id']} {t['nombre']}" for t in libres) if libres else 'ninguna')
    partes = [cab]
    usado = len(cab)
    for t in terminales:
        bloque = formatear_terminal(t, infos.get(t['id']) or {})
        if usado + len(bloque) > tope:
            partes.append(f'… (+{len(terminales) - len(partes) + 1} terminales sin detalle '
                          'por presupuesto)')
            break
        partes.append(bloque)
        usado += len(bloque)
    return '\n'.join(partes)


def repartir_colas(infos: dict, tope: int = TOPE_COLA_PANE) -> None:
    """Recorta las colas de pane para que la suma no pase `tope`. Prioriza a
    quien más importa mirar: esperando > trabajando > resto."""
    def prioridad(tid):
        i = infos[tid]
        if i.get('esperando'):
            return 0
        if i.get('fase') == 'trabajando':
            return 1
        return 2
    usado = 0
    for tid in sorted(infos, key=prioridad):
        cola = infos[tid].get('cola') or []
        peso = sum(len(l) + 9 for l in cola)
        if usado + peso > tope:
            infos[tid]['cola'] = cola[-2:] if usado + 2 * (ANCHO_LINEA + 9) <= tope else []
            peso = sum(len(l) + 9 for l in infos[tid]['cola'])
        usado += peso


def formatear_git(info: dict) -> str:
    if not info:
        return ''
    lineas = [f"rama: {info.get('rama') or '?'}"]
    cambios = info.get('cambios') or []
    if cambios:
        lineas.append(f'sin commitear ({len(cambios)}): ' + ', '.join(cambios[:20])
                      + (' …' if len(cambios) > 20 else ''))
    else:
        lineas.append('árbol limpio (todo commiteado)')
    commits = info.get('commits') or []
    if commits:
        lineas.append('últimos commits:')
        lineas.extend(f'  {c}' for c in commits)
    return recortar('\n'.join(lineas), TOPE_GIT)


def guia_desde_markdown(texto: str, tope: int = TOPE_GUIA) -> str:
    """Intro + índice de secciones de un CLAUDE.md: lo justo para respetar sus
    reglas al planear, sin pegar el archivo entero (se lee con Read si hace
    falta el detalle)."""
    if not texto:
        return ''
    lineas = texto.splitlines()
    intro, titulos = [], []
    for l in lineas:
        if l.startswith('## '):
            titulos.append(l[3:].strip())
        elif not titulos and l.strip() and not l.startswith('# '):
            intro.append(l.strip())
    salida = ' '.join(intro)
    salida = una_linea(salida, max(200, tope // 2))
    if titulos:
        salida += '\nsecciones: ' + ' · '.join(titulos[:30])
    return recortar(salida, tope)


def formatear_eventos(eventos: list) -> str:
    lineas = []
    for e in eventos:
        det = f": {una_linea(e['motivo'], 140)}" if e.get('motivo') else ''
        lineas.append(f"- hace {hace(e.get('hace_s'))} · {e.get('nombre') or '#' + str(e['terminal_id'])}"
                      f" · {e['event']}{det}")
    return recortar('\n'.join(lineas), TOPE_EVENTOS)


def formatear_coordinacion(permisos: list, reservas: list, actividad: list) -> str:
    lineas = []
    for p in permisos:
        if p.get('estado') == 'pendiente':
            lineas.append(f"- ⏳ {p.get('pide')} pidió permiso sobre {p.get('archivo')} "
                          f"a {p.get('a') or p.get('dueno') or '?'}")
    for r in reservas:
        lineas.append(f"- 🔖 {r['nombre']} reservó {r['path']} (hace {hace(r.get('hace_s'))})")
    for a in actividad:
        if a.get('clase') in ('conflicto', 'permiso'):
            lineas.append(f"- {a.get('hora', '')} {una_linea(a.get('texto', ''), 160)}")
    return recortar('\n'.join(lineas[:14]), TOPE_COORD)


# ─── Recolección impura (best-effort) ─────────────────────────────────────────

def _git(ruta: str, *args) -> str:
    try:
        r = subprocess.run(['git', *args], cwd=ruta, capture_output=True,
                           text=True, timeout=3)
        return r.stdout if r.returncode == 0 else ''
    except Exception:
        return ''


def info_git(ruta: str) -> dict:
    if not ruta or not os.path.isdir(os.path.join(ruta, '.git')):
        return {}
    rama = _git(ruta, 'rev-parse', '--abbrev-ref', 'HEAD').strip()
    cambios = [l[3:].strip() for l in _git(ruta, 'status', '--porcelain').splitlines()
               if len(l) > 3]
    commits = [l.strip() for l in _git(
        ruta, 'log', '-8', '--format=%h %ar · %s').splitlines() if l.strip()]
    return {'rama': rama, 'cambios': cambios, 'commits': commits}


def guia_proyecto(ruta: str) -> str:
    for nombre in ('CLAUDE.md', 'AGENTS.md'):
        p = os.path.join(ruta or '', nombre)
        try:
            with open(p, encoding='utf-8', errors='replace') as f:
                texto = f.read(200_000)
        except OSError:
            continue
        g = guia_desde_markdown(texto)
        if g:
            return f'({nombre} — leelo con Read si necesitás el detalle)\n{g}'
    return ''


def archivos_raiz(ruta: str, limite: int = 30) -> list:
    try:
        return sorted(n for n in os.listdir(ruta)
                      if not n.startswith('.') and os.path.isfile(os.path.join(ruta, n)))[:limite]
    except OSError:
        return []


def _db():
    from plotspace.core.database import get_db
    return get_db()


def _segundos_desde_iso(ts: str):
    from datetime import datetime
    try:
        return (datetime.now() - datetime.fromisoformat(ts)).total_seconds()
    except (TypeError, ValueError):
        return None


def eventos_recientes(project_id: int, limite: int = 10) -> list:
    conn = _db()
    try:
        filas = conn.execute(
            "SELECT e.terminal_id, e.event, e.timestamp, e.motivo, t.nombre "
            "FROM task_events e LEFT JOIN terminals t ON t.id = e.terminal_id "
            "WHERE e.project_id = ? AND e.event IN ('TASK_DONE','TASK_BLOCKED','TASK_ERROR') "
            "ORDER BY e.id DESC LIMIT ?", (project_id, limite)).fetchall()
    finally:
        conn.close()
    out = []
    for f in filas:
        d = dict(f)
        d['hace_s'] = _segundos_desde_iso(d.get('timestamp'))
        out.append(d)
    return out


def _estado_agent_watch(tid: int) -> dict:
    try:
        from plotspace.core import agent_watch
        st = agent_watch._estados.get(tid) or {}
    except Exception:
        return {}
    desde = st.get('fase_desde')
    return {'fase': st.get('fase') or '',
            'esperando': bool(st.get('esperando')),
            'fase_hace_s': (time.monotonic() - desde) if desde else None}


async def _colas(tids: list) -> dict:
    from plotspace.core import pane_capture

    async def una(tid):
        try:
            return tid, await asyncio.wait_for(pane_capture.capturar(tid, ttl=2.0), 2.5)
        except Exception:
            return tid, ''
    res = await asyncio.gather(*[una(t) for t in tids])
    return {tid: cola_pane(texto) for tid, texto in res}


def infos_terminales(project_id: int, terminales: list,
                     eventos: list = None, con_live: bool = True) -> dict:
    """Todo lo que se sabe de cada terminal, SIN la cola del pane (async).

    `terminales` son filas de la tabla terminals (dicts con id, nombre,
    tipo_ia y —si vienen— las columnas de origen: origen/tarea/orquestacion/
    orquestacion_ts). Claves por terminal: fase, esperando, fase_hace_s,
    estado, archivos, tarea, ultimo_evento, dev_servers (+ origen)."""
    ultimo_ev = {}
    for e in (eventos if eventos is not None else eventos_recientes(project_id, 40)):
        ultimo_ev.setdefault(e['terminal_id'], e)

    live = {}
    if con_live and terminales:
        try:
            from plotspace.core import agent_live
            rows = [{'tid': t['id'], 'tnombre': t['nombre'], 'tipo_ia': t.get('tipo_ia')}
                    for t in terminales]
            for a in agent_live.snapshot(project_id, rows).get('agentes', []):
                live[a['terminal_id']] = a
        except Exception:
            pass

    devs = {}
    try:
        from plotspace.core import dev_detect
        for s in dev_detect.servers_detectados(project_id):
            if s.get('terminal_id'):
                devs.setdefault(s['terminal_id'], []).append(s['url'])
    except Exception:
        pass

    infos = {}
    ahora = time.time()
    for t in terminales:
        tid = t['id']
        info = _estado_agent_watch(tid)
        a = live.get(tid) or {}
        info['estado'] = a.get('estado') or ''
        info['archivos'] = [f for f in (a.get('archivos') or []) if f.get('writes')]
        env = ultimo_envio(tid)
        if env:
            info['tarea'] = {'texto': env[1], 'hace_s': ahora - env[0]}
        elif t.get('tarea'):
            # Tras un reinicio el registro en memoria se pierde: la tarea que
            # Jarvis le entregó sigue persistida en la fila de la terminal.
            info['tarea'] = {'texto': t['tarea'],
                             'hace_s': _segundos_desde_iso(t.get('orquestacion_ts'))}
        if t.get('origen'):
            info['origen'] = {'origen': t['origen'],
                              'orquestacion': t.get('orquestacion') or '',
                              'ts': t.get('orquestacion_ts')}
        info['ultimo_evento'] = ultimo_ev.get(tid)
        info['dev_servers'] = devs.get(tid, [])
        infos[tid] = info
    return infos


def info_terminal(project_id: int, terminal: dict) -> dict:
    """Info de UNA terminal (para la guarda de enviar_prompt)."""
    return infos_terminales(project_id, [terminal]).get(terminal['id']) or {}


async def construir_bloques(project: dict, terminales: list) -> list:
    """Los bloques de contexto (sin [Orden] ni memorias), ya formateados y
    acotados. Cada fuente degrada a nada si falla."""
    pid = project['id']
    ruta = project.get('ruta') or ''

    def _recolectar():
        evs = []
        try:
            evs = eventos_recientes(pid, 40)
        except Exception as e:
            print(f'[orq-contexto] eventos: {e}')
        infos = infos_terminales(pid, terminales, evs)
        datos = {'evs': evs, 'infos': infos}
        for clave, fn in (('git', lambda: info_git(ruta)),
                          ('guia', lambda: guia_proyecto(ruta)),
                          ('raiz', lambda: archivos_raiz(ruta))):
            try:
                datos[clave] = fn()
            except Exception as e:
                print(f'[orq-contexto] {clave}: {e}')
                datos[clave] = None
        return datos

    datos = await asyncio.to_thread(_recolectar)
    infos = datos['infos']
    try:
        colas = await _colas([t['id'] for t in terminales])
        for tid, cola in colas.items():
            if tid in infos:
                infos[tid]['cola'] = cola
        repartir_colas(infos)
    except Exception as e:
        print(f'[orq-contexto] colas de pane: {e}')

    bloques = [f"[Estado actual]\n{formatear_bloque_terminales(terminales, infos)}"]

    proyecto = [f"nombre: {project.get('nombre') or '?'} · ruta: {ruta}"]
    if datos.get('raiz'):
        proyecto.append('archivos en la raíz: ' + ', '.join(datos['raiz']))
    git_txt = formatear_git(datos.get('git') or {})
    if git_txt:
        proyecto.append(git_txt)
    bloques.append('[Proyecto]\n' + '\n'.join(proyecto))

    if datos.get('guia'):
        bloques.append(f"[Guía del proyecto]\n{datos['guia']}")

    if datos['evs']:
        bloques.append(f"[Eventos]\n{formatear_eventos(datos['evs'][:10])}")

    try:
        from plotspace.core import agent_live
        permisos = agent_live._permisos.get(pid, [])
        reservas = [{'path': path, 'nombre': r['nombre'],
                     'hace_s': time.monotonic() - r['ts']}
                    for (rpid, path), r in agent_live._reservas.items()
                    if rpid == pid
                    and time.monotonic() - r['ts'] < agent_live.RESERVA_TTL_S]
        actividad = list(agent_live._actividad.get(pid, []))[:20]
        coord = formatear_coordinacion(permisos, reservas, actividad)
        if coord:
            bloques.append(f"[Coordinación]\n{coord}")
    except Exception as e:
        print(f'[orq-contexto] coordinación: {e}')

    return bloques
