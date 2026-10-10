# plotspace/core/herencia.py
"""Herencia del enjambre — el trabajo sin commitear del agente que ya no está.

EL CASO REAL (24% del tráfico del mailbox de este repo)
-------------------------------------------------------
Un agente edita `builder.js`, deja su parte sin commitear, y su terminal se
cierra. Lo que pasaba entonces:

  1. `agent_live._purgar_terminales` llamaba a `provenance.purgar_terminal` y
     BORRABA todo rastro de qué había escrito ese agente;
  2. sin esos registros, `/swarm/fragmentos` dejaba de reportar sus hunks como
     `ajenos`;
  3. `commit_propio.clasificar_archivos` pasaba entonces el archivo de modo
     'hunk' a modo 'archivo completo';
  4. y el próximo agente que commiteaba se llevaba el trabajo del muerto adentro
     de SU commit, bajo SU mensaje y SU trailer `Jarvis-Agent`.

De ahí salía el "tal agente tiene commits que no hizo". La pieza que faltaba no
era un canal de mensajes mejor: era que el trabajo huérfano estuviera DECLARADO,
para que el que llega sepa que eso no está en vuelo — está abandonado, y le toca
a él commitearlo (o dejarlo, pero sabiendo).

REGLAS (las tres importan)
--------------------------
  · Solo cuenta lo SUCIO. Lo que ya está en HEAD no es herencia de nadie.
  · El dueño es el ÚLTIMO que escribió el archivo. Si después del muerto pasó un
    vivo, el archivo es del vivo y no hay herencia que declarar.
  · Sin provenance del archivo NO se adivina dueño. Un huérfano mal atribuido es
    peor que uno sin atribuir: manda a alguien a commitear lo que no entiende.
"""
import os
import subprocess
import time

CACHE_TTL_S = 3            # el árbol sucio no cambia tan rápido como se consulta


def parsear_sucios(porcelain) -> set:
    """Salida de `git status --porcelain` → set de rutas sucias (modificadas,
    staged, nuevas). En un renombre se toma el DESTINO, que es el archivo que
    existe ahora."""
    out = set()
    for linea in (porcelain or '').splitlines():
        if len(linea) < 4:
            continue
        ruta = linea[3:].strip()
        if ' -> ' in ruta:                    # 'R  viejo.py -> nuevo.py'
            ruta = ruta.split(' -> ', 1)[1].strip()
        ruta = ruta.strip('"')
        if ruta:
            out.add(ruta)
    return out


def calcular(sucios, ediciones, vivos) -> list:
    """[{tid, nombre, archivos}] — lo sucio cuyo último escritor ya no está vivo.

    Pura: el caller trae el árbol sucio, el libro de provenance y quién vive.
    Ordenada por tid para que dos llamadas seguidas den lo mismo (la firma del
    briefing depende de eso)."""
    # Solo trabajo REAL: mandar a un agente a commitear la salida de un build o
    # el mockup de otro sería peor que no decirle nada (ver politica_commit).
    from plotspace.core import politica_commit
    sucios = {p for p in (sucios or ()) if politica_commit.hay_que_commitear(p)}
    vivos = set(vivos or ())
    if not sucios:
        return []

    # Último escritor por archivo. Solo writes: leer no da propiedad.
    ultimo = {}
    for e in (ediciones or ()):
        if not isinstance(e, dict) or e.get('op') != 'write':
            continue
        path, tid = e.get('path'), e.get('tid')
        if not path or tid is None or path not in sucios:
            continue
        try:
            ts = float(e.get('ts') or 0)
        except (TypeError, ValueError):
            ts = 0.0
        previo = ultimo.get(path)
        if previo is None or ts >= previo['ts']:
            ultimo[path] = {'ts': ts, 'tid': tid, 'nombre': e.get('nombre') or ''}

    por_agente = {}
    for path, d in ultimo.items():
        if d['tid'] in vivos:
            continue                          # está vivo: no es herencia, es trabajo
        entrada = por_agente.setdefault(
            d['tid'], {'tid': d['tid'],
                       'nombre': d['nombre'] or f"terminal {d['tid']}",
                       'archivos': []})
        entrada['archivos'].append(path)
    for h in por_agente.values():
        h['archivos'].sort()
    return [por_agente[k] for k in sorted(por_agente)]


# ─── Capa impura: el árbol de verdad ──────────────────────────────────────────

_cache: dict = {}          # ruta_proyecto → (ts, set de sucios)


def sucios_de(ruta_proyecto, ahora=None) -> set:
    """Árbol sucio de un proyecto (cacheado). set() si no es un repo o si falla:
    sin dato NO se declara herencia, que es el lado seguro."""
    if not ruta_proyecto or not os.path.isdir(os.path.join(ruta_proyecto, '.git')):
        return set()
    ahora = time.monotonic() if ahora is None else ahora
    hit = _cache.get(ruta_proyecto)
    if hit and (ahora - hit[0]) < CACHE_TTL_S:
        return hit[1]
    try:
        r = subprocess.run(['git', '--no-optional-locks', '-c', 'core.quotepath=false', 'status',
                            '--porcelain'], cwd=ruta_proyecto,
                           capture_output=True, text=True, timeout=15)
        sucios = parsear_sucios(r.stdout) if r.returncode == 0 else set()
    except Exception:
        sucios = set()
    _cache[ruta_proyecto] = (ahora, sucios)
    return sucios


def de_proyecto(project_id, ruta_proyecto, vivos) -> list:
    """Herencia vigente de un proyecto, con el libro de provenance real.

    Best-effort: cualquier error devuelve [] — esto cuelga de `jv estado` y del
    briefing, y jamás puede romperle el turno a un agente."""
    try:
        from plotspace.core import provenance
        return calcular(sucios_de(ruta_proyecto),
                        provenance.ediciones(pid=project_id), vivos)
    except Exception as e:
        print(f'[herencia] no pude calcular la de {project_id}: {e}')
        return []


def reset():
    """Solo para tests."""
    _cache.clear()
