#!/usr/bin/env python3
"""
commit-propio — fearless committing in the shared tree.

In this repo N agents share a single working tree and ONE git index:
hand-picking what to stage is a source of panic ("am I taking someone
else's work?") and of the stage↔commit race (two agents staging at once).
This script solves it:

  1. Identifies the agent by its tmux session (jarvis_<id>).
  2. Asks Jarvis WHAT TEXT each agent wrote in the files you touched (real
     provenance, the one reported by the CLI hook) and picks the candidates
     by crossing that with the dirty files + what LIVE.md says + the extras
     you pass as arguments.
  3. Stages at the right granularity:
       · file only you touched → whole file.
       · file where ANOTHER agent has uncommitted work → by HUNK,
         filtering by CONTENT (never by line number: filtering by line
         already took out someone else's function living 1300 lines below).
       · hunk that cannot be attributed → NOT staged, and you are told.
  4. Takes a LOCK (flock on .git/jarvis-commit.lock) that serializes
     stage+commit, and also removes from the SHARED index whatever another
     one left staged (otherwise it would ride along in your commit).
  5. Commits with your message (hooks run as usual: secrets, ownership,
     memories).

If Jarvis does not answer, it degrades to the old path (LIVE.md + whole file)
and tells you, so you can look at the staged diff before trusting it.

Usage:  python3 scripts/commit_propio.py -m "fix(x): message" [extra path ...]

If you are not running inside a Jarvis terminal, it tells you and does
nothing (the user commits as usual). Pure stdlib.
"""
import argparse
import os
import subprocess
import sys
import time

# The lock that serializes stage+commit between agents. `fcntl` is Unix-only:
# on Windows it does not exist and importing it kept this script —and its
# test— from even loading. The mechanism is picked per system.
try:
    import fcntl
except ImportError:                       # Windows
    fcntl = None
    import msvcrt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard_propiedad as gp


def _tomar_lock(archivo, espera=30.0):
    """Exclusive lock on the open file, in whichever way the system supports.

    Serializes stage+commit between agents: without it, two committing at the
    same time carry each other's files into their commit (the git index is
    shared). It is what protects other people's work, so if it cannot be
    acquired you must know about it, not silently move on.
    """
    limite = time.monotonic() + espera
    while True:
        try:
            if fcntl is not None:
                fcntl.flock(archivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                # Windows: locks one byte of the file. Same effect, another API.
                msvcrt.locking(archivo.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            if time.monotonic() >= limite:
                raise RuntimeError(
                    'otro agente lleva más de 30s commiteando — reintentá en un momento')
            time.sleep(0.1)


def archivos_de_mi_seccion(texto_live, mi_tid):
    """ALL the paths listed under MY section of LIVE.md (with or without 🔒):
    they are the files live tracking saw me write."""
    mios, actual = [], None
    for linea in (texto_live or '').splitlines():
        m = gp._RE_AGENTE.match(linea)
        if m:
            actual = int(m.group(2))
            continue
        if linea.startswith('## '):
            actual = None
            continue
        if actual == mi_tid:
            mp = gp._RE_PATH.match(linea)
            if mp:
                mios.append(mp.group(1))
    return mios


def nombre_de_agente(texto_live, mi_tid):
    """Name of agent `mi_tid` according to its heading in LIVE.md, or None. It
    is the identity stamped on the commit: the git author is the same for ALL
    agents (same git user.name), so without this `git log` cannot tell whose
    commit each one is."""
    for linea in (texto_live or '').splitlines():
        m = gp._RE_AGENTE.match(linea)
        if m and int(m.group(2)) == mi_tid:
            return m.group(1).strip()
    return None


def trailer_atribucion(nombre, mi_tid):
    """`Jarvis-Agent: <name> (tid N)` trailer that makes it recoverable which
    agent a commit came from, WITHOUT touching the git author (which the CI and
    the changelog popup expect to stay stable). A single line: newlines are
    flattened so they do not break git's trailer block."""
    quien = ' '.join(str(nombre).split()) if nombre else f'terminal {mi_tid}'
    return f'Jarvis-Agent: {quien} (tid {mi_tid})'


def elegir_staged(dirty, mios, extras):
    """What to stage: dirty ∩ mine (basename-tolerant match) + extras."""
    out = []
    for f in dirty:
        if any(gp._match_archivo(m, f) for m in mios) and f not in out:
            out.append(f)
    for e in extras:
        if e not in out:
            out.append(e)
    return out


def _dirty(raiz):
    r = subprocess.run(['git', 'status', '--porcelain'], capture_output=True,
                       text=True, cwd=raiz, timeout=15)
    out = []
    for linea in r.stdout.splitlines():
        if len(linea) > 3:
            out.append(linea[3:].strip().strip('"'))
    return out


def _untracked(raiz):
    r = subprocess.run(['git', 'ls-files', '--others', '--exclude-standard'],
                       capture_output=True, text=True, cwd=raiz, timeout=15)
    return {l.strip() for l in r.stdout.splitlines() if l.strip()}


# ─── Stage by HUNK (real provenance, no line heuristic) ───────────────────────

def elegir_candidatos(dirty, archivos_prov, mios_live, extras):
    """Which files go into the commit: the DIRTY ones that provenance or LIVE.md
    say I wrote, plus the explicit extras. Never other people's."""
    mios = list(archivos_prov) + list(mios_live)
    out = []
    for f in dirty:
        if any(gp._match_archivo(m, f) for m in mios) and f not in out:
            out.append(f)
    for e in extras:
        if e not in out:
            out.append(e)
    return out


def clasificar_archivos(candidatos, untracked, ajenos, extras):
    """How to stage each file:
      'archivo' → whole file: no one else wrote there, or it is new, or you asked.
      'hunk'    → by hunks: another agent also has uncommitted text there,
                  and staging the whole file would drag it along."""
    modo = {}
    for f in candidatos:
        comparte = any(gp._match_archivo(k, f) and v for k, v in (ajenos or {}).items())
        if comparte and f not in untracked and f not in extras:
            modo[f] = 'hunk'
        else:
            modo[f] = 'archivo'
    return modo


def _fragmentos_de_jarvis(mi_tid):
    """{mios, ajenos, archivos} from the server. None if Jarvis does not answer
    (then it falls back to the old path: LIVE.md + whole file)."""
    import json
    import urllib.request
    puerto = os.environ.get('JARVIS_PORT', '3000')
    try:
        req = urllib.request.Request(
            f'http://127.0.0.1:{puerto}/api/swarm/fragmentos/{mi_tid}')
        with urllib.request.urlopen(req, timeout=4) as r:
            return json.loads(r.read().decode('utf-8', errors='replace'))
    except Exception:
        return None


def _stage_por_hunk(raiz, archivo, mios, ajenos):
    """Applies to the index ONLY my hunks of `archivo`.
    Returns (ok, reason_if_not)."""
    import hunks_propios as hp
    d = subprocess.run(['git', 'diff', '-U0', '--', archivo], capture_output=True,
                       text=True, cwd=raiz, timeout=30)
    if not d.stdout.strip():
        return False, 'no unstaged changes'
    parche = hp.filtrar_parche(d.stdout, mios, ajenos)
    if not parche:
        return False, ('could not attribute any hunk to you (did you edit lines '
                       'adjacent to those of the other agent?)')
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix='.patch', dir=os.path.join(raiz, '.git'))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(parche)
        r = subprocess.run(['git', 'apply', '--cached', '--unidiff-zero', tmp],
                           capture_output=True, text=True, cwd=raiz, timeout=30)
        if r.returncode != 0:
            return False, f'git apply failed: {r.stderr.strip()[:200]}'
        return True, ''
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser(description='Safe commit in the shared tree')
    ap.add_argument('-m', '--mensaje', required=True)
    ap.add_argument('extras', nargs='*', help='extra paths to include')
    args = ap.parse_args()

    mi_tid = gp.detectar_terminal_id()
    if mi_tid is None:
        print('commit-propio: you are not in a Jarvis terminal (jarvis_<id>) — '
              'commit normally with git add <paths> && git commit.')
        return 1
    raiz = (gp._git('rev-parse', '--show-toplevel') or '').strip()
    if not raiz:
        print('commit-propio: there is no git repo here.')
        return 1
    try:
        with open(os.path.join(raiz, '.jarvis', 'LIVE.md'), encoding='utf-8') as f:
            live = f.read()
    except OSError:
        live = ''
    mios_live = archivos_de_mi_seccion(live, mi_tid)
    trailer = trailer_atribucion(nombre_de_agente(live, mi_tid), mi_tid)

    # Real provenance (CLI hook). If Jarvis does not answer, it continues with
    # LIVE.md and whole files — the old path, degraded but alive.
    prov = _fragmentos_de_jarvis(mi_tid)
    if prov is None:
        print('commit-propio: Jarvis is not responding — going with LIVE.md and '
              'whole files (no hunk filtering). Check the staged diff.')
        prov = {'mios': {}, 'ajenos': {}, 'archivos': []}

    dirty = _dirty(raiz)
    extras = set(args.extras)
    candidatos = elegir_candidatos(dirty, prov.get('archivos') or [],
                                   mios_live, args.extras)
    if not candidatos:
        print('commit-propio: found no dirty files of yours. If tracking did '
              'not see you, pass the explicit paths as arguments.')
        return 1

    modo = clasificar_archivos(candidatos, _untracked(raiz),
                               prov.get('ajenos') or {}, extras)

    lock_path = os.path.join(raiz, '.git', 'jarvis-commit.lock')
    staged, saltados = [], []
    with open(lock_path, 'w') as lock:
        try:
            _tomar_lock(lock)                     # serializes stage+commit

            # The index is SHARED: whatever another one left staged would ride
            # in MY commit. It is removed before starting (it stays in their tree).
            ya = subprocess.run(['git', 'diff', '--cached', '--name-only'],
                                capture_output=True, text=True, cwd=raiz, timeout=15)
            ajeno_staged = [f for f in ya.stdout.split()
                            if f and not any(gp._match_archivo(c, f) for c in candidatos)]
            if ajeno_staged:
                subprocess.run(['git', 'reset', '-q', '--'] + ajeno_staged,
                               cwd=raiz, timeout=30)
                print('commit-propio: removed from the index what is not yours: '
                      + ', '.join(ajeno_staged[:6]))

            for f in candidatos:
                if modo[f] == 'archivo':
                    r = subprocess.run(['git', 'add', '--', f], cwd=raiz,
                                       capture_output=True, text=True, timeout=30)
                    (staged if r.returncode == 0 else saltados).append(
                        f if r.returncode == 0 else (f, r.stderr.strip()[:120]))
                    continue
                ok, motivo = _stage_por_hunk(
                    raiz, f, (prov['mios'] or {}).get(f, []),
                    (prov['ajenos'] or {}).get(f, []))
                (staged if ok else saltados).append(f if ok else (f, motivo))

            if not staged:
                for f, m in saltados:
                    print(f'commit-propio: ⚠ {f} — {m}')
                print('commit-propio: nothing of yours was left to commit.')
                return 1

            r = subprocess.run(['git', 'commit', '-m', args.mensaje, '-m', trailer],
                               cwd=raiz, timeout=120)
            if r.returncode != 0:
                # the hook blocked (ownership/secrets/memory): undo the stage
                # so the index is not left half-done for the next one
                subprocess.run(['git', 'reset', '-q', '--'] + staged,
                               cwd=raiz, timeout=30)
                return r.returncode
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)

    por_hunk = [f for f in staged if modo.get(f) == 'hunk']
    print(f'commit-propio: committed ({len(staged)} file(s)): '
          + ', '.join(staged[:8]) + ('…' if len(staged) > 8 else ''))
    if por_hunk:
        print('  · by HUNK (shared with another agent): ' + ', '.join(por_hunk))
    for f, m in saltados:
        print(f'  ⚠ left out: {f} — {m}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
