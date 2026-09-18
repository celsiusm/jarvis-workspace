#!/usr/bin/env python3
"""
Mailbox warning in the pre-commit hook — it ONLY warns, it NEVER blocks.

The commit moment is where an unread message hurts: if another agent warned
you "I changed the interface you use" and you didn't see it, your commit is
born broken. The server keeps `.jarvis/mailbox-pendientes.json` (per target
terminal); this script identifies the agent by its tmux session (same
mechanism as guard_propiedad) and prints its pending items — with emphasis if
a message mentions a file about to be committed. Exit is ALWAYS 0 (it is a
warning). Pure stdlib.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard_propiedad as gp


def avisos(msgs: list, staged: list) -> list:
    """Warning lines for the pending messages. A message that mentions
    the basename of a staged file goes first and flagged."""
    urgentes, normales = [], []
    basenames = {os.path.basename(s) for s in staged}
    for m in msgs:
        texto = m.get('msg', '')
        de = m.get('de', '?')
        toca = sorted(b for b in basenames if b and b in texto)
        if toca:
            urgentes.append(f"📬⚠ de {de} — MENCIONA {', '.join(toca)} (staged): {texto[:200]}")
        else:
            normales.append(f"📬 de {de}: {texto[:160]}")
    return urgentes + normales


def main():
    try:
        tid = gp.detectar_terminal_id()
        if tid is None:
            return 0                      # the user in their shell: silence
        raiz = (gp._git('rev-parse', '--show-toplevel') or '').strip()
        if not raiz:
            return 0
        path = os.path.join(raiz, '.jarvis', 'mailbox-pendientes.json')
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        msgs = data.get(str(tid)) or []
        if not msgs:
            return 0
        lineas = avisos(msgs, gp._staged())
        print(f"— You have {len(msgs)} unread message(s) in .jarvis/MAILBOX.md —")
        for l in lineas[:6]:
            print('  ' + l)
        print("  (warning, does not block: read them before continuing)")
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
