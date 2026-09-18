#!/usr/bin/env python3
"""jv — the swarm CLI. Run by AGENTS, not the user.

WHY IT EXISTS
-------------
Coordinating was extremely expensive. Measured on this project:
  · `.jarvis/MAILBOX.md` is 46 KB / 11.6K tokens and the protocol demanded
    rereading it often, because there was no per-agent read cursor.
  · 16 of 112 messages (14%) never reached anyone —ambiguous name or
    dead terminal— and were silently dropped.
  · 32% of inter-agent traffic was pure protocol (permissions, reservations,
    acknowledgements), and each delivery wakes an agent for a full turn.
  · Every session started by paying ~30K tokens of protocol in context.

`jv` changes that: you ask for what you need, when you need it.

COMANDOS
--------
  jv estado                  what others are touching, what reached you, whether tracking works
  jv inbox                   your new messages (and marks them read)
  jv msg "<agent>" "<text>"  send a message (tells you if it arrived)
  jv ask "<agent>" "<text>"  send and WAIT for the reply (without spending a turn)
  jv claim "<symbol|file|folder>"   reserve territory BEFORE touching it
  jv commit -m "<message>"   commit only yours, by hunk
  jv help

Pure stdlib. If Jarvis does not answer, each command says so and exits with code 1.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

TIMEOUT_S = 6
ESPERA_ASK_S = 240          # how long `jv ask` waits for a reply
INTERVALO_ASK_S = 3


def _base():
    return f"http://127.0.0.1:{os.environ.get('JARVIS_PORT', '3000')}"


def _tid():
    tid = os.environ.get('JARVIS_TERMINAL_ID')
    if tid:
        return tid
    # Fallback: the tmux session is named jarvis_<id>.
    import re
    import subprocess
    try:
        r = subprocess.run(['tmux', 'display-message', '-p', '#{session_name}'],
                           capture_output=True, text=True, timeout=3)
        m = re.match(r'jarvis_(\d+)', (r.stdout or '').strip())
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _pedir(ruta, cuerpo=None, timeout=TIMEOUT_S):
    req = urllib.request.Request(
        _base() + ruta,
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers={'Content-Type': 'application/json'},
        method='POST' if cuerpo is not None else 'GET')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', errors='replace'))


def _fallo(msg, code=1):
    print(f'jv: {msg}')
    return code


# ─── Commands ─────────────────────────────────────────────────────────────────

def cmd_estado(tid, args):
    d = _pedir(f'/api/swarm/estado/{tid}')
    if d.get('error'):
        return _fallo(d['error'])
    print(f"You are: {d['yo']}")
    pares = d.get('pares') or []
    otros = d.get('otros') or {}
    if pares:
        print(f'Agents in the project ({len(pares)} besides you):')
        for p in pares:
            est = p.get('estado')
            marca = ('🟢 working' if est == 'trabajando'
                     else '💀 CLI down (it closed)' if est == 'caido'
                     else '💀 down (no tmux session)' if est == 'sin_sesion'
                     else '⚪ idle')
            linea = f"  · {p['nombre']} ({p.get('tipo_ia', 'manual')}) — {marca}"
            archs = otros.get(p['nombre'])
            if archs:
                linea += ' · edits ' + ', '.join(archs[:6])
            print(linea)
    else:
        print('You are the only active agent in the project.')
    if d['mis_archivos']:
        print('Your files: ' + ', '.join(d['mis_archivos'][:12]))
    if d.get('mi_territorio'):
        print('Your territory: ' + ', '.join(d['mi_territorio'][:10]))
    if d.get('territorio_ajeno'):
        print('Territory of others (do not delete or rename it):')
        for nombre, patron in d['territorio_ajeno'][:8]:
            print(f'  · {patron} — {nombre}')
    # Inheritance: uncommitted work from agents that are gone. No one is going
    # to come looking for it — if you touch one of those files, commit it yourself.
    for h in (d.get('herencia') or [])[:4]:
        archivos = h.get('archivos') or []
        print(f"⚠ Inheritance from {h.get('nombre', '?')} (left without committing): "
              + ', '.join(archivos[:6])
              + (f' (+{len(archivos) - 6})' if len(archivos) > 6 else ''))
    print(f"Unread messages: {d['mensajes_sin_leer']}"
          + ('  → run: jv inbox' if d['mensajes_sin_leer'] else ''))
    s = d.get('salud_provenance') or {}
    if s.get('muda'):
        print('⚠ edit tracking has not recorded ANYTHING yet — if you already '
              'wrote files, tell the user (the hooks may be down and nobody '
              'is protected).')
    return 0


def cmd_inbox(tid, args):
    d = _pedir(f'/api/swarm/inbox/{tid}')
    if d.get('error'):
        return _fallo(d['error'])
    print(d.get('texto') or 'Inbox: no new messages.')
    return 0


def _enviar(tid, para, texto, espera=False):
    d = _pedir('/api/swarm/msg', {'terminal_id': int(tid), 'para': para,
                                  'msg': texto, 'espera': espera})
    if not d.get('ok'):
        print(f"jv: {d.get('error', 'could not send')}")
        sug = d.get('sugerencias') or []
        if sug:
            print('     did you mean?: ' + ' · '.join(sug[:6]))
        return None
    if d.get('destino_vivo') is False:
        print(f"jv: ⚠ {d['para']} has the CLI CLOSED — the message was written "
              f"to the MAILBOX but no one is going to read it. If their work "
              f"blocks you, check `jv estado` (their territory is now free and "
              f"their uncommitted work shows up as inheritance).")
        return d
    print(f"jv: message delivered to {d['para']}")
    return d


def cmd_msg(tid, args):
    if len(args) < 2:
        return _fallo('usage: jv msg "<agent>" "<text>"')
    return 0 if _enviar(tid, args[0], ' '.join(args[1:])) else 1


def cmd_ask(tid, args):
    """Sends and WAITS for the reply. It is what turns a negotiation of 6
    inference turns (with two agents waking each other every turn) into ONE
    blocking call that returns the text over stdout."""
    if len(args) < 2:
        return _fallo('usage: jv ask "<agent>" "<question>"')
    para = args[0]
    # espera=True → the message goes as 'ask': it is the only case, along with
    # HANDOFF, that warrants waking the recipient even if they already closed
    # their task (otherwise this ask would always expire).
    d = _enviar(tid, para, ' '.join(args[1:]), espera=True)
    if not d:
        return 1
    # Asking a dead agent is waiting 4 minutes for no one. The message is
    # already written; what makes no sense is BLOCKING.
    if d.get('destino_vivo') is False:
        print(f'jv: do not keep waiting — {para} is not around to answer. Move on.')
        return 2
    limite = time.time() + ESPERA_ASK_S
    print(f'jv: waiting for a reply from {para} (up to {ESPERA_ASK_S}s)…')
    # `de=` (server 2026-08+): the poll returns and marks ONLY the messages
    # from the one asked. Without the filter, a message from a THIRD party
    # that arrived during the wait was marked delivered here and never shown.
    import urllib.parse
    filtro = urllib.parse.quote(para)
    while time.time() < limite:
        time.sleep(INTERVALO_ASK_S)
        try:
            d = _pedir(f'/api/swarm/inbox/{tid}?de={filtro}')
        except Exception:
            continue
        for m in d.get('mensajes') or []:
            if para.strip().lower() in (m.get('de') or '').strip().lower():
                print(f"\n{m['de']} replied:\n{m['msg']}")
                return 0
    print(f'jv: {para} did not reply in {ESPERA_ASK_S}s. Move on to something '
          f'else and check later with: jv inbox')
    return 2


def cmd_claim(tid, args):
    """Claims territory by NAME: a symbol, a file or a folder.

    Never by line number — lines move, and in this repo that already cost a
    deleted function. Free territory is granted instantly; someone else's is
    reported with its owner, never stolen."""
    if not args:
        return _fallo('usage: jv claim "<symbol|file|folder>" [more…]\n'
                      '     (to release it: jv claim --soltar "<pattern>")')
    soltar = args[0] in ('--soltar', '-s')
    patrones = args[1:] if soltar else args
    if not patrones:
        return _fallo('tell me what to release')
    d = _pedir('/api/swarm/claim', {'terminal_id': int(tid),
                                    'patrones': patrones, 'soltar': soltar})
    if not d.get('ok'):
        return _fallo(d.get('error', 'could not do it'))
    if soltar:
        print('jv: released — ' + ', '.join(d.get('soltados') or []))
        return 0
    if d.get('otorgados'):
        print('jv: yours — ' + ', '.join(d['otorgados']))
    for o in d.get('ocupados') or []:
        print(f"jv: ⛔ {o['patron']} already belongs to {o['de']} — ask for it with "
              f"jv ask \"{o['de']}\" \"…\"")
    return 0 if d.get('otorgados') or not d.get('ocupados') else 2


def cmd_commit(tid, args):
    import subprocess
    aqui = os.path.dirname(os.path.abspath(__file__))
    return subprocess.run([sys.executable, os.path.join(aqui, 'commit_propio.py'),
                           *args]).returncode


def cmd_help(tid, args):
    bloque = __doc__.split('COMANDOS\n--------\n', 1)[-1]
    print('\n'.join(l[2:] if l.startswith('  ') else l
                    for l in bloque.strip().splitlines()))
    return 0


COMANDOS = {'estado': cmd_estado, 'inbox': cmd_inbox, 'msg': cmd_msg,
            'ask': cmd_ask, 'claim': cmd_claim, 'commit': cmd_commit,
            'help': cmd_help}


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        return cmd_help(None, [])
    cmd, args = argv[0], argv[1:]
    fn = COMANDOS.get(cmd)
    if not fn:
        return _fallo(f'unknown command "{cmd}". Try: jv help')
    if cmd == 'help':
        return fn(None, args)
    tid = _tid()
    if not tid:
        return _fallo('you are not in a Jarvis terminal (I cannot find '
                      'JARVIS_TERMINAL_ID or the tmux session jarvis_<id>).')
    try:
        return fn(tid, args)
    except urllib.error.HTTPError as e:
        return _fallo(f'Jarvis responded {e.code} — is the server up to date?')
    except Exception as e:
        return _fallo(f'I could not talk to Jarvis ({e}). Is it running?')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
