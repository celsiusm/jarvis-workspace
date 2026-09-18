#!/usr/bin/env python3
"""
Ownership LOCK between agents (user request, 2026-06-16).

The CLAUDE.md rule "commit ONLY your files" is soft discipline: an agent may
not read it, may be another CLI (Codex/Gemini don't read CLAUDE.md) or, like
any model, may drift now and then. This is the HARD enforcement, twin of the
secrets scanner:

  - The .githooks/pre-commit hook runs this script BEFORE every commit.
  - The script identifies the committing agent by its tmux session
    (`jarvis_<terminal_id>`) and reads `.jarvis/LIVE.md`.
  - If among the staged files there is one whose 🔒 owner is ANOTHER agent (and
    there is no "→ OK" permission from that owner to me over that file) → it
    BLOCKS the commit.

Fails OPEN on purpose: without tmux (the user commits in their shell), without
LIVE.md, or on any error, it ALLOWS. A bug in the guard must never brick
everyone's commits; the worst that happens is going back to the previous state
(soft discipline). The escape hatch for legitimate false positives:
`git commit --no-verify`. Pure stdlib (like scan_secretos.py).
"""
import os
import re
import subprocess
import sys


# ── Parsing of LIVE.md (format from plotspace/core/agent_live.py:generar_live_md) ──
# Agent header:  «## <name> (<type>, terminal <id>) — <state>»
_RE_AGENTE = re.compile(r'^##\s+(.+?)\s+\([^)]*terminal\s+(\d+)\)')
# File line:      «- `<path>` — write ×N (hace Xm)[ 🔒 dueño]»
_RE_PATH = re.compile(r'^-\s+`([^`]+)`')
# Permission line:      «- <emoji> <asker> pidió PERMISO sobre `<archivo>` ... [→ OK/NO]»
_RE_PERMISO = re.compile(r'^-\s+\S+\s+(.+?)\s+pidió PERMISO sobre `([^`]+)`')
# Reservation line:      «- 🔖 `<path>` — <name> (hace Xm)»
_RE_RESERVA = re.compile(r'^-\s+🔖\s+`([^`]+)`\s+—\s+(.+?)\s+\(hace')


def parsear_reservas(texto):
    """[(nombre, path)] from the «## Reservas» section of LIVE.md — the lease
    "I'm going to touch X" of mailbox v2. Only the current ones appear (agent_live
    prunes by TTL), so there are no times to compute here."""
    reservas = []
    en_reservas = False
    for linea in (texto or '').splitlines():
        if linea.startswith('## '):
            en_reservas = linea.startswith('## Reservas')
            continue
        if en_reservas:
            m = _RE_RESERVA.match(linea)
            if m:
                reservas.append((m.group(2).strip(), m.group(1).strip()))
    return reservas


def parsear_live(texto):
    """(agentes, permisos).
      agentes  = [{'tid': int, 'nombre': str, 'owned': [path, ...],
                   'muerto': bool}]   (owned = only the 🔒 ones)
      permisos = [(pide_nombre, archivo, estado)]  estado ∈ {ok, no, pendiente}

    `muerto` comes from the 💀 in the header (agent_live._EMOJI_ESTADO): an agent
    whose CLI was closed or whose tmux session no longer exists. An old LIVE.md
    without that mark leaves everyone alive — the previous behavior."""
    agentes, permisos = [], []
    actual = None
    en_permisos = False
    for linea in (texto or '').splitlines():
        m = _RE_AGENTE.match(linea)
        if m:
            actual = {'nombre': m.group(1).strip(), 'tid': int(m.group(2)),
                      'owned': [], 'muerto': '💀' in linea}
            agentes.append(actual)
            en_permisos = False
            continue
        if linea.startswith('## '):                 # another section (e.g. Permisos)
            en_permisos = linea.startswith('## Permisos')
            actual = None
            continue
        if en_permisos:
            mp = _RE_PERMISO.match(linea)
            if mp:
                if '→ OK' in linea:
                    estado = 'ok'
                elif '→ NO' in linea:
                    estado = 'no'
                else:
                    estado = 'pendiente'
                permisos.append((mp.group(1).strip(), mp.group(2).strip(), estado))
            continue
        if actual is not None and '🔒' in linea:
            mpath = _RE_PATH.match(linea)
            if mpath:
                actual['owned'].append(mpath.group(1))
    return agentes, permisos


def _quitar_prefijo_rel(p):
    return p[2:] if p.startswith('./') else p


def _match_archivo(a, b):
    """Mirror of agent_live._match_archivo: the owner usually replies with the
    basename, so 'frontend/shared/ui.js' matches 'ui.js'."""
    a, b = _quitar_prefijo_rel(a), _quitar_prefijo_rel(b)
    return a == b or a.endswith('/' + b) or b.endswith('/' + a)


def _clave(p):
    r"""How two paths are compared to decide OWNERSHIP.

    On Windows the filesystem is case-insensitive: `Src\Index.js`
    and `src/index.js` are THE SAME file. Comparing them as raw strings,
    the same file ends up with two owners and the lock does not see the collision.
    On Linux they are different files and merging them would be the opposite bug —
    that is why the rule follows the system, not taste. Twin of
    `plotspace/core/rutas.clave_propiedad` (not imported here: this script runs
    as a git hook, without the backend package on the path).
    """
    p = (p or '').replace('\\', '/')
    return p.lower() if os.name == 'nt' else p


def violaciones(staged, mi_tid, agentes, permisos, reservas=None):
    """Staged files whose owner is ANOTHER agent — or with a current foreign
    RESERVATION — and for which I do NOT have an ok permission.
    → [{'path', 'dueno_tid', 'dueno_nombre'}] (dueno_tid None if it is a reservation)."""
    dueno = {}                       # _clave(path) -> (tid, name)
    mi_nombre = None
    for a in agentes:
        if a['tid'] == mi_tid:
            mi_nombre = a['nombre']
        # A DEAD agent defends nothing: its CLI was closed and the commits
        # everyone expected from it will never arrive. Letting it block was
        # turning its death into a permanent lock over live files.
        if a.get('muerto'):
            continue
        for p in a['owned']:
            dueno[_clave(p)] = (a['tid'], a['nombre'])
    mis_oks = [arch for (pide, arch, est) in permisos
               if est == 'ok' and mi_nombre is not None and pide == mi_nombre]
    out = []
    for f in staged:
        d = dueno.get(_clave(f))
        if d is None:
            # current reservation by ANOTHER? ("I'm going to touch X" of mailbox v2)
            r = next(((nom, path) for nom, path in (reservas or [])
                      if _match_archivo(path, f)), None)
            if r and (mi_nombre is None
                      or r[0].strip().lower() != mi_nombre.strip().lower()):
                d = (None, f'{r[0]} (reserva)')
        if not d or d[0] == mi_tid:
            continue
        if any(_match_archivo(arch, f) for arch in mis_oks):
            continue                 # the owner gave me permission over this file
        out.append({'path': f, 'dueno_tid': d[0], 'dueno_nombre': d[1]})
    return out


def filtrar_guard_ok(viol, ok_env):
    """SCOPED bypass: GUARD_OK="path1,path2" exempts ONLY those files (with a
    record). Replaces the total --no-verify, which also turned off the secrets
    scanner. Returns (current_violations, exempted)."""
    if not ok_env:
        return viol, []
    eximidas = [v for v in viol
                if any(_match_archivo(e, v['path']) for e in ok_env)]
    return [v for v in viol if v not in eximidas], eximidas


def registrar_bloqueo(raiz, mi_tid, viol):
    """Leaves the block in data/jarvis.log (same JSON-lines format as
    plotspace/core/logs.py) so the friction signal does not evaporate with the
    commit output. Best-effort and pure stdlib: NEVER breaks the hook."""
    try:
        import json
        from datetime import datetime
        data_dir = os.environ.get('JARVIS_DATA_DIR', '').strip() or os.path.join(raiz, 'data')
        os.makedirs(data_dir, exist_ok=True)
        rec = {
            'ts': datetime.now().isoformat(timespec='seconds'),
            'nivel': 'warn',
            'evento': 'guard_propiedad_bloqueo',
            'terminal_id': mi_tid,
            'archivos': [v['path'] for v in viol],
            'duenos': sorted({v['dueno_nombre'] for v in viol}),
        }
        with open(os.path.join(data_dir, 'jarvis.log'), 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    except Exception:
        pass


# ── Wiring with the environment (git + tmux) ─────────────────────────────────

def detectar_terminal_id():
    """The terminal_id of the committing agent. None if it does not run inside
    a Jarvis terminal (e.g. the user committing from their own shell).

    FIRST the environment variable, which Jarvis injects when creating the
    terminal and works with ANY engine. The tmux session name is the fallback
    for terminals created before the variable existed: when the engine is
    ConPTY (Windows) there will be no tmux session to query, and this guard
    —which is the swarm's ownership lock— cannot go blind.
    Same order as `scripts/jv.py`."""
    tid = (os.environ.get('JARVIS_TERMINAL_ID') or '').strip()
    if tid.isdigit():
        return int(tid)
    try:
        r = subprocess.run(['tmux', 'display-message', '-p', '#{session_name}'],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            m = re.match(r'jarvis_(\d+)', r.stdout.strip())
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return None


def _git(*args):
    try:
        r = subprocess.run(['git', *args], capture_output=True, text=True, timeout=10)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def _staged():
    out = _git('diff', '--cached', '--name-only', '--diff-filter=ACMR') or ''
    return [l.strip() for l in out.splitlines() if l.strip()]


def main():
    try:
        mi_tid = detectar_terminal_id()
        if mi_tid is None:
            return 0                 # not an identifiable agent → allow
        raiz = (_git('rev-parse', '--show-toplevel') or '').strip() or os.getcwd()
        try:
            with open(os.path.join(raiz, '.jarvis', 'LIVE.md'), encoding='utf-8') as f:
                texto = f.read()
        except OSError:
            return 0                 # without LIVE.md → nothing to protect
        agentes, permisos = parsear_live(texto)
        viol = violaciones(_staged(), mi_tid, agentes, permisos,
                           reservas=parsear_reservas(texto))
        ok_env = [p.strip() for p in os.environ.get('GUARD_OK', '').split(',') if p.strip()]
        viol, eximidas = filtrar_guard_ok(viol, ok_env)
        for v in eximidas:
            print(f"[guard_propiedad] {v['path']} exempted by GUARD_OK "
                  f"(owner: {v['dueno_nombre']}) — record")
        if not viol:
            return 0
        registrar_bloqueo(raiz, mi_tid, viol)
        print('✖ BLOCKED: you are about to commit files owned by ANOTHER agent (per .jarvis/LIVE.md):')
        for v in viol:
            term = f"terminal {v['dueno_tid']}" if v['dueno_tid'] is not None else 'current reservation'
            print(f"   • {v['path']} — 🔒 owner: {v['dueno_nombre']} ({term})")
        print('')
        print('Commit ONLY your files: `git add <your paths>` (NOT `git add -A` / `git commit -am`).')
        print('If the owner gave you permission and it still blocks, exempt ONLY that file:')
        print('  GUARD_OK="path/to/file.py" git commit -m "..."   (do NOT use --no-verify:')
        print('  it also turns off the secrets scanner)')
        return 1
    except Exception as e:
        # Fails open: the guard must never brick commits because of its own bug.
        print(f'[guard_propiedad] warning: check skipped due to internal error ({e})', file=sys.stderr)
        return 0


if __name__ == '__main__':
    sys.exit(main())
