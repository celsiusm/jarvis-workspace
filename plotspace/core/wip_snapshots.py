"""
wip_snapshots — el paracaídas del árbol compartido.

En un working tree que comparten N agentes, el trabajo SIN commitear es
tierra de nadie: un `git add -A` ajeno se lo lleva, un stash ajeno lo pisa,
un checkout lo borra. Este módulo saca una foto periódica del árbol sucio a
refs internas (`refs/jarvis/wip/<ts>`) usando un ÍNDICE TEMPORAL — jamás
toca el índice real (que es de los agentes) ni crea commits en ninguna rama.

Recuperar un archivo barrido:
    git for-each-ref refs/jarvis/wip            # listar fotos
    git show <ref>:<ruta/del/archivo>           # ver/restaurar contenido

Lo dispara el janitor (30 min) para cada proyecto con git y árbol sucio.
`subprocess.run` síncrono a propósito (regla del repo para git/tmux).
"""
import os
import subprocess
from datetime import datetime

KEEP = 12          # ~6 h de fotos con el janitor de 30 min
TIMEOUT_ADD_S = 300  # el `add -A` hashea todo el árbol sucio: es el paso caro


def _git(repo, *args, env=None, timeout=60):
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(['git', '--no-optional-locks', *args], cwd=repo, env=e,
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip()


# Último motivo por el que una foto NO salió. Existe porque este módulo estuvo
# CINCO DÍAS sin sacar una sola foto y nadie se enteró: cada excepción se tragaba
# con `except Exception: return None` y el janitor solo imprimía cuando había
# fotos, así que "no había nada sucio" y "se rompe siempre" se veían igual.
_fallo: dict = {'motivo': ''}


def ultimo_fallo() -> str:
    """Motivo del último intento fallido ('' si la última foto salió bien)."""
    return _fallo['motivo']


def _fallar(motivo: str):
    _fallo['motivo'] = motivo
    print(f'[wip] snapshot NO sacado: {motivo}')
    return None


def _limpiar_lock(idx: str):
    """Borra el .lock de NUESTRO índice temporal si quedó de una corrida muerta.

    Esto es lo que tuvo el paracaídas cinco días fuera de servicio: una corrida
    se interrumpió a mitad del `add -A` (el updater reemplaza el proceso con
    os.execv, y ese paso tardaba ~60s con 927 MB de artefactos sin ignorar),
    dejó `jarvis-wip-index.lock`, y desde entonces TODOS los intentos morían en
    el `read-tree` con "Another git process seems to be running".

    Es seguro borrarlo sin mirar el reloj porque el archivo es NUESTRO y de un
    solo uso: nada más en el repo toca `jarvis-wip-index`. Ojo: jamás tocar
    `.git/index.lock`, que sí es el índice compartido de los agentes."""
    try:
        os.remove(idx + '.lock')
        print('[wip] había un lock huérfano de una corrida anterior: borrado')
    except OSError:
        pass


def snapshot_wip(repo: str, keep: int = KEEP):
    """Foto del árbol sucio a refs/jarvis/wip/<ts>. None si el árbol está limpio
    (caso normal, sin ruido) o si algo falló — y en ese caso el motivo queda en
    `ultimo_fallo()` y en el stdout del server, nunca en el vacío."""
    try:
        rc, sucio = _git(repo, 'status', '--porcelain')
        if rc != 0:
            return _fallar(f'git status falló en {repo}')
        if not sucio.strip():
            _fallo['motivo'] = ''         # árbol limpio no es un fallo
            return None
        idx = os.path.join(repo, '.git', 'jarvis-wip-index')
        env = {'GIT_INDEX_FILE': idx}
        _limpiar_lock(idx)
        try:
            # índice TEMPORAL sembrado desde HEAD + todo el árbol encima
            if _git(repo, 'read-tree', 'HEAD', env=env)[0] != 0:
                return _fallar(f'read-tree falló en {repo}')
            # El timeout es generoso a propósito: el `add -A` es lo único caro
            # acá (hashea todo el árbol sucio) y es exactamente lo que reventó la
            # vez pasada. Lo que evita que vuelva a pasar no es el timeout sino
            # tener los artefactos en .gitignore — esto es la red de abajo.
            if _git(repo, 'add', '-A', env=env, timeout=TIMEOUT_ADD_S)[0] != 0:
                return _fallar(f'add -A falló en {repo}')
            rc, tree = _git(repo, 'write-tree', env=env)
            if rc != 0:
                return _fallar(f'write-tree falló en {repo}')
            rc, head = _git(repo, 'rev-parse', 'HEAD')
            if rc != 0:
                return _fallar(f'{repo} no tiene HEAD todavía')
            rc, commit = _git(repo, 'commit-tree', tree, '-p', head,
                              '-m', 'wip snapshot (paracaídas de Jarvis)')
            if rc != 0:
                return _fallar(f'commit-tree falló en {repo}')
            ts = datetime.now().strftime('%Y%m%d-%H%M%S')
            ref = f'refs/jarvis/wip/{ts}'
            if _git(repo, 'update-ref', ref, commit)[0] != 0:
                return _fallar(f'update-ref falló en {repo}')
        finally:
            try:
                os.remove(idx)
            except OSError:
                pass
            _limpiar_lock(idx)
        _podar(repo, keep)
        _fallo['motivo'] = ''
        return ref
    except Exception as e:
        return _fallar(f'{type(e).__name__} en {repo}: {e}')


def _podar(repo: str, keep: int):
    """Deja solo las últimas `keep` fotos (el nombre YYYYmmdd-HHMMSS ordena)."""
    rc, out = _git(repo, 'for-each-ref', '--format=%(refname)', 'refs/jarvis/wip')
    if rc != 0:
        return
    refs = sorted(l for l in out.splitlines() if l.strip())
    for ref in refs[:-keep] if keep else refs:
        _git(repo, 'update-ref', '-d', ref)


def snapshots_todos() -> dict:
    """Pasada del janitor: una foto por proyecto con git y árbol sucio.

    Devuelve {fotos, fallos, motivos}. Los `fallos` importan tanto como las
    fotos: antes esto devolvía solo {'fotos': N} y con N=0 era imposible
    distinguir "no había nada sucio" de "se rompe en todos los proyectos" — que
    es exactamente cómo el paracaídas estuvo cinco días muerto sin que nadie lo
    notara."""
    resumen = {'fotos': 0, 'fallos': 0, 'motivos': []}
    try:
        from plotspace.core.database import get_db
        conn = get_db()
        try:
            rutas = [r['ruta'] for r in conn.execute('SELECT ruta FROM projects').fetchall()]
        finally:
            conn.close()
    except Exception as e:
        resumen['fallos'] += 1
        resumen['motivos'].append(f'no pude listar los proyectos: {e}')
        return resumen
    for ruta in rutas:
        if not ruta or not os.path.isdir(os.path.join(ruta, '.git')):
            continue
        try:
            if snapshot_wip(ruta):
                resumen['fotos'] += 1
            elif ultimo_fallo():
                resumen['fallos'] += 1
                resumen['motivos'].append(ultimo_fallo())
        except Exception as e:
            resumen['fallos'] += 1
            resumen['motivos'].append(f'{ruta}: {e}')
    return resumen
