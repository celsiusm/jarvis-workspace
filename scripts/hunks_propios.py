#!/usr/bin/env python3
"""Filtering of a `git diff -U0` to keep ONLY your own hunks.

WHY
---
All agents work on the same branch and the same working tree, so
`git add <file>` does not mean "mine": it drags the WHOLE file with the
other's uncommitted work inside. It already happened, and it was documented
in the project MAILBOX: an agent filtered its hunks by LINE NUMBER ("mine
are above 4400") and took out someone else's function that lived at 5739,
because the zones are interleaved, not in per-agent blocks.

The conclusion they reached the hard way is the right one: filter by
CONTENT and with `-U0` (minimal hunks; with -U3 the neighbor's line gets
stuck on). What was missing was the DATA — knowing what text each one
wrote. The provenance ledger (`plotspace/core/provenance.py`) now has that,
fed by the CLI hook.

GOLDEN INVARIANT
----------------
When in doubt, the hunk is NOT mine. Leaving out a change of your own costs
a second commit; taking someone else's erases another agent's work.

Pure stdlib (like guard_propiedad.py): the hooks and the commit script have
to work even when the venv is not activated.
"""
import re

# Lines too common to attribute a hunk to anyone: they show up in anyone's
# diff. Without this, a hunk that only closes a brace gets taken by the
# first one who passes.
_TRIVIALES = {'', '}', '{', '};', ')', '(', '),', '];', '[', ']', ',', ';',
              '*/', '/*', '"""', "'''", 'return', 'pass', 'else:', 'else {',
              '});', '})', '>', '<div>', '</div>'}
_LARGO_MIN = 8          # less than this identifies no one

_RE_HUNK = re.compile(r'^@@ ', re.MULTILINE)


def _significativa(linea: str) -> bool:
    s = linea.strip()
    return len(s) >= _LARGO_MIN and s not in _TRIVIALES


def partir_diff(diff_texto):
    """(header, [hunk, ...]). The header is the `diff --git`/`---`/
    `+++`/`index` lines git needs to know which file to apply to."""
    texto = diff_texto or ''
    if not texto:
        return '', []
    pos = [m.start() for m in _RE_HUNK.finditer(texto)]
    if not pos:
        return texto, []
    cabecera = texto[:pos[0]]
    hunks = []
    for i, inicio in enumerate(pos):
        fin = pos[i + 1] if i + 1 < len(pos) else len(texto)
        hunks.append(texto[inicio:fin])
    return cabecera, hunks


def _lineas(hunk: str, signo: str):
    otro = '-' if signo == '+' else '+'
    out = []
    for linea in (hunk or '').splitlines()[1:]:      # [0] is the `@@ ... @@`
        if not linea.startswith(signo):
            continue
        if linea.startswith(signo * 3) or linea.startswith(otro * 3):
            continue                                 # '+++ b/x' / '--- a/x'
        out.append(linea[1:].strip())
    return out


def lineas_agregadas(hunk):
    return _lineas(hunk, '+')


def lineas_quitadas(hunk):
    return _lineas(hunk, '-')


def _aparece(linea: str, fragmentos) -> bool:
    """Is this line inside some fragment the agent wrote?
    It is compared WITHOUT indentation: the CLI reports the text just as it
    inserted it, and a later reformat must not break attribution."""
    objetivo = linea.strip()
    if not objetivo:
        return False
    for f in fragmentos or ():
        if not f:
            continue
        if objetivo in f:
            return True
        # line-by-line comparison, tolerant of indentation
        if any(objetivo == l.strip() for l in str(f).splitlines()):
            return True
    return False


def hunk_es_mio(hunk, fragmentos_mios, fragmentos_ajenos=()) -> bool:
    """Did I produce this hunk? It requires positive evidence and the absence
    of other people's evidence — a mixed hunk is left out on purpose."""
    agregadas = [l for l in lineas_agregadas(hunk) if _significativa(l)]
    quitadas = [l for l in lineas_quitadas(hunk) if _significativa(l)]
    candidatas = agregadas or quitadas          # a hunk that only deletes counts
    if not candidatas:
        return False
    if any(_aparece(l, fragmentos_ajenos) for l in candidatas):
        return False                            # ambiguous or outright someone else's
    return any(_aparece(l, fragmentos_mios) for l in candidatas)


def filtrar_parche(diff_texto, fragmentos_mios, fragmentos_ajenos=()):
    """Patch applicable with `git apply --cached --unidiff-zero` containing
    ONLY my hunks, or None if there are none."""
    cabecera, hunks = partir_diff(diff_texto)
    mios = [h for h in hunks if hunk_es_mio(h, fragmentos_mios, fragmentos_ajenos)]
    if not mios:
        return None
    parche = cabecera + ''.join(mios)
    return parche if parche.endswith('\n') else parche + '\n'
