# plotspace/routers/live.py
# JARVIS — Agents Live (API).
# Snapshot del estado vivo: qué agente toca qué archivo, dueños y permisos.
# El cliente pide esto UNA vez al abrir la pestaña Live; después se mantiene
# por WS (live_update / permiso_* / conflicto_archivo llevan snapshot).
#
# Acá vive además la API del ENJAMBRE, que consume el hook del CLI:
#   POST /api/swarm/op    (PostToolUse) — provenance real de cada edición
#   POST /api/swarm/check (PreToolUse)  — guarda ANTES de escribir
# Doctrina de las dos: NUNCA tirar error y NUNCA frenar de más. Un 500 acá
# ensucia el pane de todos los agentes, y una guarda con falso positivo los
# bloquea a todos — por eso `check` falla ABIERTO ante cualquier duda.

import asyncio
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from plotspace.core import agent_live, provenance
from plotspace.core.database import get_db

router = APIRouter(prefix="/api", tags=["live"])


@router.get("/projects/{project_id}/live")
def get_live(project_id: int):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM projects WHERE id = ?', (project_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Proyecto no encontrado")
        cursor.execute(
            'SELECT id AS tid, nombre AS tnombre, tipo_ia AS tipo_ia '
            'FROM terminals WHERE project_id = ? AND activa = 1', (project_id,))
        rows = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()
    return agent_live.snapshot(project_id, rows)


# ─── API del enjambre (la consume el hook del CLI) ────────────────────────────

# Ventana en la que una escritura ajena hace sospechosa una sobrescritura total.
VENTANA_SOBRESCRITURA_S = 900     # 15 min


@router.get("/swarm/grupos/{project_id}")
def swarm_grupos(project_id: int):
    """Qué terminales están trabajando sobre la MISMA superficie, ahora.

    Lo consume el ícono de cada card (¿estoy enredado con alguien?) y la lista
    del overlay. Si tres agentes convergen en un archivo, devuelve UN grupo de
    tres — no tres pares."""
    from plotspace.core import swarm_grupos
    grupos = swarm_grupos.detectar_grupos(provenance.ediciones(pid=project_id))
    con_alerta = {c['tid'] for c in provenance.colisiones(pid=project_id)} | \
                 {c['contra_tid'] for c in provenance.colisiones(pid=project_id)}
    for g in grupos:
        # 🔵 convergencia (informativo) vs 🔴 colisión (alerta real). Prender la
        # alarma por compartir archivo sería el ruido que ya sabemos que se
        # termina ignorando.
        g['estado'] = 'colision' if any(m['tid'] in con_alerta
                                        for m in g['miembros']) else 'convergencia'
    return {'grupos': grupos}


@router.get("/swarm/grupo/{project_id}/{grupo_id}")
def swarm_grupo_detalle(project_id: int, grupo_id: str):
    """Todo lo del overlay: quién es quién, la superficie compartida, la línea
    de tiempo entrelazada, la conversación entre ellos y las colisiones."""
    from plotspace.core import swarm_grupos
    d = swarm_grupos.detalle(project_id, grupo_id)
    if not d:
        raise HTTPException(status_code=404, detail="Ese grupo ya no existe")
    return d


class MensajeSwarm(BaseModel):
    terminal_id: int
    para: str
    msg: str
    espera: bool = False       # `jv ask`: el que pregunta queda bloqueado esperando


@router.post("/swarm/msg")
async def swarm_msg(body: MensajeSwarm):
    """Mandar un mensaje a otro agente, con acuse REAL de resolución.

    Antes, un destinatario ambiguo ("@Claude Code" con dos vivos) o una
    terminal muerta hacían que el mensaje se descartara con un print en el
    stdout del server: 16 de 112 mensajes (14%) murieron así, sin que el que
    escribió se enterara nunca. Ahora se contesta al instante y con
    sugerencias."""
    from plotspace.core import swarm_cli
    return await swarm_cli.enviar(body.terminal_id, body.para, body.msg,
                                  espera=body.espera)


@router.get("/swarm/inbox/{terminal_id}")
def swarm_inbox(terminal_id: int, marcar: bool = True, de: str = None):
    """SOLO mis mensajes no leídos (y los marca leídos). El reemplazo de releer
    los 46 KB del MAILBOX.md, que no tiene cursor por agente. `de` filtra por
    remitente y marca SOLO esos — es lo que usa el poll de `jv ask` para no
    consumir mensajes de terceros durante la espera."""
    from plotspace.core import swarm_cli
    return swarm_cli.inbox(terminal_id, marcar=marcar, de=de)


@router.get("/swarm/estado/{terminal_id}")
def swarm_estado(terminal_id: int):
    """Qué necesito saber AHORA: qué toqué yo, qué tocan los otros, qué me
    llegó. Bajo demanda, en vez de 30K tokens de protocolo en cada sesión."""
    from plotspace.core import swarm_cli
    return swarm_cli.estado(terminal_id)


@router.get("/swarm/fragmentos/{terminal_id}")
def swarm_fragmentos(terminal_id: int):
    """Qué texto escribió CADA agente en los archivos que tocó este agente.

    Lo consume `scripts/commit_propio.py` para stagear por HUNK en vez de por
    archivo: en este árbol compartido `git add <archivo>` arrastra el trabajo
    sin commitear del otro que vive en el mismo archivo. Con esto el filtro se
    hace con datos (quién escribió qué) y no con heurísticas de número de línea
    — que ya se llevaron puesta una función ajena."""
    import asyncio as _asyncio     # noqa: F401  (import local, patrón del módulo)
    row = agent_live._row_terminal(terminal_id)
    if not row:
        return {'mios': {}, 'ajenos': {}, 'archivos': []}
    pid = row['pid']
    mios, ajenos = {}, {}
    mis_paths = {e['path'] for e in provenance.ediciones(pid=pid, tid=terminal_id)}
    for e in provenance.ediciones(pid=pid):
        if e['path'] not in mis_paths:
            continue                       # solo los archivos que YO toqué
        destino = mios if e['tid'] == terminal_id else ajenos
        frags = destino.setdefault(e['path'], [])
        for t in (e['despues'], e['antes']):
            if t and t not in frags:
                frags.append(t)
    return {'mios': mios, 'ajenos': ajenos, 'archivos': sorted(mis_paths)}


@router.get("/swarm/briefing/{terminal_id}")
def swarm_briefing(terminal_id: int, solo_si_cambio: bool = False):
    """Lo que este agente tiene que saber ANTES de empezar: quién más está vivo,
    qué toca cada uno, qué territorio no puede pisar, qué herencia quedó de un
    agente que se fue y cuántos mensajes tiene sin leer.

    Lo pide el hook `UserPromptSubmit` en cada tarea nueva — así el contexto del
    enjambre deja de depender de que el agente se acuerde de correr `jv estado`,
    que es exactamente lo que no pasaba: los agentes se enteraban del otro
    chocándose. `solo_si_cambio=True` es el canal de piggyback (ver briefing.py):
    devuelve texto vacío si el estado es idéntico al del último briefing.

    Devuelve SIEMPRE 200 con `texto` (vacío si no hay nada que decir): este
    endpoint cuelga del prompt de un agente y un error acá le arruinaría el turno."""
    from plotspace.core import briefing
    return briefing.briefing_para(terminal_id, solo_si_cambio=solo_si_cambio)


@router.get("/swarm/salud")
def swarm_salud():
    """¿La provenance está viva? Responde sin adivinar: cuántas ediciones
    reales entraron, de cuántos agentes, y si el CLI mandó algún payload con
    formato desconocido (la señal de que hay que actualizar el normalizador).

    `hook_instalado` mira el settings.json de verdad: un hook desinstalado a
    mano deja todo el subsistema ciego y hasta ahora no había forma de saberlo."""
    import os
    from plotspace.core import hooks_cli
    s = provenance.salud()
    instalado = False
    try:
        import json as _json
        p = os.path.join(os.path.expanduser('~'), '.claude', 'settings.json')
        with open(p, encoding='utf-8') as f:
            hooks = (_json.load(f) or {}).get('hooks') or {}
        instalado = all(
            any(hooks_cli.NOMBRE_SCRIPT in (h.get('command') or '')
                for g in (hooks.get(ev) or []) for h in (g.get('hooks') or []))
            for ev, _ in hooks_cli._EVENTOS)
    except Exception:
        pass
    s['hook_instalado'] = instalado
    return s


class HookOp(BaseModel):
    """Payload del hook. Todo opcional salvo terminal_id: el contrato del CLI
    cambia entre versiones y el endpoint tiene que sobrevivir a eso."""
    terminal_id: int
    tool_name: Optional[str] = None
    tool_input: Optional[dict] = None
    cwd: Optional[str] = None
    session_id: Optional[str] = None


class HookFin(BaseModel):
    terminal_id: int


def _fila_terminal(tid: int):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute('SELECT id, project_id, nombre FROM terminals WHERE id = ? AND activa = 1', (tid,))
        r = c.fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


@router.post("/swarm/fin")
async def swarm_fin(body: HookFin):
    """Hook `Stop` del CLI: el agente ACABA de terminar de responder.

    Es la señal exacta (el heurístico de quietud del pane tarda ~16 s): se emite
    `agente_termino` YA — el frontend suena la campanita — y se le avisa a
    agent_watch para que no repita ese mismo final. Nunca tira error (un 500 acá
    ensuciaría el pane del agente)."""
    try:
        fila = await asyncio.to_thread(_fila_terminal, body.terminal_id)
        if not fila:
            return {'ok': False}
        from plotspace.core import agent_watch
        from plotspace.core.events import broadcaster
        agent_watch.registrar_fin_hook(fila['id'])
        await broadcaster.broadcast(fila['project_id'], {
            'type': 'agente_termino',
            'terminal_id': fila['id'],
            'terminal_nombre': fila['nombre'],
        })
        return {'ok': True}
    except Exception:
        return {'ok': False}


@router.post("/swarm/op")
async def swarm_op(body: HookOp):
    """PostToolUse: el agente YA escribió. Registra la provenance real.

    Reemplaza al parseo de panes de agent_live, que quedó ciego cuando el CLI
    pasó a resúmenes colapsados sin nombre de archivo. De acá salen la
    propiedad, las alertas de conflicto y el libro que habilita el commit por
    hunk."""
    # Canario de contrato: si la herramienta era de escritura y el payload no
    # entregó lo que debería, el CLI cambió el formato. Se canta en la PRIMERA
    # edición — la vez pasada estuvimos ciegos horas sin enterarnos.
    sospecha = provenance.formato_sospechoso(body.tool_name, body.tool_input)
    if sospecha:
        provenance.contar_formato_raro(body.tool_name, sospecha)
        print(f'[swarm] ⚠ formato de payload desconocido — {sospecha}')
        try:
            from plotspace.core import logs
            logs.evento('provenance_formato_desconocido', nivel='warn',
                        terminal_id=body.terminal_id, motivo=sospecha[:300])
        except Exception:
            pass

    ops = provenance.normalizar_payload(body.tool_name, body.tool_input)
    if not ops:
        return {'ops': []}
    sobre = provenance.es_sobrescritura_total(body.tool_name, body.tool_input)
    resultados = []
    for i, o in enumerate(ops):
        try:
            r = await agent_live.registrar_op_externa(
                body.terminal_id, o['op'], o['path'],
                antes=o['antes'], despues=o['despues'],
                sobrescritura=sobre and o['op'] == 'write',
                # Publicar UNA vez por request: cada publicación reescribe
                # LIVE.md y hace broadcast, y un Edit múltiple traería N.
                publicar=(i == len(ops) - 1))
        except Exception as e:                      # nunca romper el pane
            print(f'[swarm] op de terminal {body.terminal_id} falló: {e}')
            r = {'estado': 'error'}
        resultados.append(r)

    # Ampliación automática del territorio: el agente se queda con los símbolos
    # que acaba de declarar y que no tengan dueño. Silencioso — es lo que hace
    # que la prevención no se vuelva una cárcel de permisos.
    try:
        from plotspace.core import territorio
        for r, o in zip(resultados, ops):
            if o['op'] == 'write' and r.get('pid') and r.get('path'):
                territorio.registrar_escritura(r['pid'], body.terminal_id,
                                               r.get('nombre') or '', r['path'],
                                               o['antes'], o['despues'])
    except Exception as e:
        print(f'[swarm] auto-reclamo falló: {e}')

    # Colisión por FUNCIONALIDAD: ¿esta edición borró algo que otro agente usa?
    # Se responde en el acto y el hook se lo inyecta al agente junto al
    # resultado de su propia herramienta — cero ida y vuelta por el mailbox.
    avisos = []
    pid = None
    try:
        pid = next((r.get('pid') for r in resultados if r.get('pid')), None)
        if pid is not None:
            for o in ops:
                if o['op'] != 'write':
                    continue
                perdidos = provenance.simbolos_perdidos(o['antes'], o['despues'])
                if not perdidos:
                    continue
                avisos += provenance.colisiones_por_simbolos(
                    perdidos, provenance.ediciones(pid=pid),
                    tid_propio=body.terminal_id)
        if avisos:
            provenance.contar_aviso_colision()
            await _avisar_colision(pid, body.terminal_id, avisos)
    except Exception as e:
        print(f'[swarm] chequeo de colisión falló: {e}')

    # Grupos en vivo: si esta edición cambió quién está enredado con quién, el
    # ícono de las cards tiene que reaccionar YA, no en el próximo poll.
    if pid is not None:
        await _publicar_grupos(pid)

    # Briefing de PIGGYBACK: los CLIs sin hook de prompt (Antigravity, opencode)
    # no tienen otro canal para enterarse del enjambre. Viaja acá, pegado al
    # resultado de su propia herramienta, y SOLO cuando el estado cambió — el
    # dedup por firma vive en briefing.py, así que no repite lo mismo en cada
    # edición. Nunca puede romper el registro de la op: va en su propio try.
    brief = ''
    try:
        from plotspace.core import briefing
        # A un THREAD sí o sí: armar el briefing forkea tmux (liveness) y corre
        # `git status` (herencia), y este endpoint está en el camino caliente de
        # CADA edición de CADA agente. Bloquear el loop acá se ve como cortes en
        # el eco del tipeo de todas las terminales.
        brief = (await asyncio.to_thread(
            briefing.briefing_para, body.terminal_id,
            solo_si_cambio=True)).get('texto') or ''
    except Exception as e:
        print(f'[swarm] briefing de piggyback falló: {e}')

    return {'ops': resultados, 'avisos': avisos,
            'aviso_texto': provenance.texto_aviso_colision(avisos),
            'briefing': brief}


_grupos_previos: dict = {}      # pid → firma del último set publicado


async def _publicar_grupos(pid):
    """Emite `swarm_grupos` solo cuando el set de grupos CAMBIÓ (alta, baja o
    cambio de estado). Sin el dedup, cada edición sería un broadcast."""
    try:
        from plotspace.core import swarm_grupos as sg
        from plotspace.core.events import broadcaster
        grupos = sg.detectar_grupos(provenance.ediciones(pid=pid))
        con_alerta = set()
        for c in provenance.colisiones(pid=pid):
            con_alerta.update({c['tid'], c['contra_tid']})
        for g in grupos:
            g['estado'] = 'colision' if any(m['tid'] in con_alerta
                                            for m in g['miembros']) else 'convergencia'
        firma = tuple(sorted((g['id'], g['estado']) for g in grupos))
        if _grupos_previos.get(pid) == firma:
            return
        _grupos_previos[pid] = firma
        await broadcaster.broadcast(pid, {'type': 'swarm_grupos', 'grupos': grupos})
    except Exception as e:
        print(f'[swarm] no pude publicar grupos: {e}')


async def _avisar_colision(pid, tid, avisos):
    """Deja constancia de la colisión para el humano: actividad de Agents Live
    + evento WS. El agente ya se entera por la respuesta del hook."""
    from plotspace.core.events import broadcaster
    for c in avisos:
        agent_live._registrar_actividad(
            pid, f"⚠ colisión: `{c['simbolo']}` lo usa {c['nombre']} "
                 f"en {c['path']}", 'alerta')
    # Nombre del ACTOR (el que borró): lo lleva el evento para que la animación
    # del Swarm le diga a la víctima QUIÉN le rompió el símbolo. Defensivo: si no
    # se puede resolver, queda '' (el front cae a "Otro agente").
    try:
        nombre = (agent_live._row_terminal(tid) or {}).get('tnombre', '')
    except Exception:
        nombre = ''
    # Constancia para el panel: antes la colisión era un aviso efímero al agente
    # y una línea de log — el usuario no la veía nunca.
    try:
        provenance.registrar_colision(pid, tid, nombre,
                                      avisos[0].get('path', ''), avisos)
    except Exception:
        pass
    try:
        await broadcaster.broadcast(pid, {
            'type': 'colision_funcional', 'terminal_id': tid,
            'terminal_nombre': nombre, 'colisiones': avisos})
    except Exception:
        pass
    try:
        from plotspace.core import logs
        logs.evento('colision_funcional', nivel='warn', terminal_id=tid,
                    project_id=pid,
                    simbolos=[c['simbolo'] for c in avisos][:10])
    except Exception:
        pass


@router.post("/swarm/check")
async def swarm_check(body: HookOp):
    """PreToolUse: el agente está por escribir. Devuelve {permitir, motivo}.

    Hoy frena UNA cosa, la única que destruye trabajo en silencio: la
    SOBRESCRITURA TOTAL de un archivo que otro agente tocó recién. Editar por
    zona un archivo ajeno NO se frena — está medido que dos ediciones en zonas
    distintas conviven, y frenar eso es exactamente el ruido que hizo que el
    32% del tráfico entre agentes fuera negociación de permisos."""
    try:
        ops = provenance.normalizar_payload(body.tool_name, body.tool_input)
        escrituras = [o for o in ops if o['op'] == 'write']
        if not escrituras:
            return {'permitir': True}
        row = await asyncio.to_thread(agent_live._row_terminal, body.terminal_id)
        if not row:
            return {'permitir': True}          # falla ABIERTO: no sé quién sos
        pid, tid = row['pid'], body.terminal_id
        sobre = provenance.es_sobrescritura_total(body.tool_name, body.tool_input)

        from plotspace.core import territorio
        for o in escrituras:
            path = agent_live.relativo_al_proyecto(o['path'], row['ruta'])

            # 1. Sobrescritura total de un archivo que otro tocó recién: es la
            #    ÚNICA operación que destruye trabajo ajeno en silencio.
            if sobre:
                otro = provenance.ultimo_escritor(
                    pid, path, excluir_tid=tid, ventana_s=VENTANA_SOBRESCRITURA_S)
                if otro:
                    hace_min = max(1, int((time.time() - otro['ts']) // 60))
                    return {'permitir': False, 'dueno': otro['nombre'], 'motivo': (
                        f"Vas a REESCRIBIR ENTERO `{path}`, y {otro['nombre']} lo "
                        f"editó hace ~{hace_min} min. Si tu copia es anterior a ese "
                        f"cambio, se pierde sin dejar rastro. Volvé a leer el archivo "
                        f"y editá SOLO tu zona con Edit; si de verdad necesitás "
                        f"reescribirlo entero, avisale a {otro['nombre']} por "
                        f".jarvis/MAILBOX.md primero.")}

            # 2. Territorio ajeno: borrar/renombrar un símbolo de otro, o
            #    escribir dentro de una ruta que otro reclamó explícitamente.
            v = territorio.chequear(pid, tid, path, o['antes'], o['despues'])
            if v:
                return {'permitir': False, 'motivo': v['motivo'],
                        'dueno': v['dueno'], 'patron': v.get('patron')}
        return {'permitir': True}
    except Exception as e:
        print(f'[swarm] check de terminal {body.terminal_id} falló: {e}')
        return {'permitir': True}              # ante cualquier error, dejar pasar


class ReclamoSwarm(BaseModel):
    terminal_id: int
    patrones: list = []
    soltar: bool = False


@router.post("/swarm/claim")
async def swarm_claim(body: ReclamoSwarm):
    """Reclamar territorio por NOMBRE (símbolo, archivo o carpeta).

    Nunca por número de línea: las líneas se mueven y en este repo eso ya costó
    una función borrada. Lo libre se concede al instante; lo ajeno se informa
    con su dueño, jamás se roba."""
    from plotspace.core import territorio
    row = await asyncio.to_thread(agent_live._row_terminal, body.terminal_id)
    if not row:
        return {'ok': False, 'error': 'no encuentro tu terminal'}
    if body.soltar:
        territorio.soltar(row['pid'], body.terminal_id, body.patrones)
        return {'ok': True, 'soltados': body.patrones}
    r = territorio.reclamar(row['pid'], body.terminal_id, row['tnombre'],
                            body.patrones)
    return {'ok': True, **r}


@router.get("/swarm/territorio/{project_id}")
def swarm_territorio(project_id: int):
    """Quién tiene reclamado qué, ahora. Lo consume el panel y `jv estado`."""
    from plotspace.core import territorio
    return {'reclamos': territorio.reclamos(project_id)}
