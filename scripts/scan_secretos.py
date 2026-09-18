#!/usr/bin/env python3
"""
Secret scanner — the repo's anti-leak lock (requested 2026-06-12).

An API key (Anthropic, MCP, whatever), the Jarvis token, or credentials of any
provider must NEVER leave for the remote: they belong to the person and cost
money. This script is the pure piece; the .githooks/ hooks run it (pre-commit
on what is staged, pre-push on the ENTIRE range of outgoing commits) and BLOCK
the operation if they find anything.

It detects two things:
1. Provider credential FORMATS + generic assignments of a long literal to a
   secret-like variable.
2. The REAL VALUES of the local secrets (data/jarvis_token.txt,
   plotspace/.env, data/telegram.json) — read at runtime, never stored here.

Usage: <text on stdin> | python3 scripts/scan_secretos.py [--origen label]
Exit 0 = clean · exit 1 = secrets found (the output shows them MASKED).

Tests: plotspace/tests/test_scan_secretos.py. No dependencies (pure stdlib):
the hooks must work even when the venv is not activated.
"""
import json
import os
import re
import sys

# The char classes in these regexes keep the FILE itself from
# self-detecting when committed (after each prefix comes '[').
PATRONES = [
    ('api-anthropic',  re.compile(r'sk-ant-[A-Za-z0-9_-]{20,}')),
    ('api-openai',     re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9]{32,}')),
    ('aws-access-key', re.compile(r'AKIA[0-9A-Z]{16}')),
    ('github-token',   re.compile(r'gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{22,}')),
    ('slack-token',    re.compile(r'xox[baprs]-[A-Za-z0-9-]{10,}')),
    ('google-api-key', re.compile(r'AIza[0-9A-Za-z_-]{35}')),
    ('telegram-bot',   re.compile(r'\b\d{8,10}:AA[A-Za-z0-9_-]{32,}')),
    ('private-key',    re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY')),
    ('jwt',            re.compile(r'eyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}')),
    ('asignacion-secreto', re.compile(
        r'(?i)(?:api[_-]?key|secret|token|passwd|password)["\']?\s*[:=]\s*'
        r'["\'][A-Za-z0-9+/=_-]{20,}["\']')),
]

# .env keys whose value is NOT a secret (model names, flags).
_ENV_NO_SECRETAS = re.compile(r'(?i)(_MODEL|_MOTOR|_ENABLED|_DEBUG|_LEVEL)$')


def valores_locales(raiz):
    """[(name, value)] of the real secrets in the local environment. Any
    literal occurrence of one of these in what leaves the repo is a certain
    leak, whatever format it has."""
    vals = []
    try:
        tok = open(os.path.join(raiz, 'data', 'jarvis_token.txt'),
                   encoding='utf-8').read().strip()
        if len(tok) > 12:
            vals.append(('token-jarvis', tok))
    except OSError:
        pass
    try:
        for linea in open(os.path.join(raiz, 'plotspace', '.env'),
                          encoding='utf-8'):
            linea = linea.strip()
            if not linea or linea.startswith('#') or '=' not in linea:
                continue
            clave, valor = linea.split('=', 1)
            valor = valor.strip().strip('"\'')
            if len(valor) > 12 and not _ENV_NO_SECRETAS.search(clave.strip()):
                vals.append((f'env-{clave.strip()}', valor))
    except OSError:
        pass
    try:
        cfg = json.load(open(os.path.join(raiz, 'data', 'telegram.json'),
                             encoding='utf-8'))
        if isinstance(cfg.get('token'), str) and len(cfg['token']) > 12:
            vals.append(('telegram-token', cfg['token']))
    except (OSError, ValueError):
        pass
    # Snapshots of CLI accounts (data/cli-accounts/<id>/*.json): the OAuth
    # tokens of claude/codex/gemini/etc. Defense in depth — if someone pastes
    # them by mistake into a TRACKED file, the hook catches them by literal value.
    try:
        base = os.path.join(raiz, 'data', 'cli-accounts')
        for root, _dirs, files in os.walk(base):
            for fn in files:
                if not fn.endswith('.json'):
                    continue
                try:
                    data = json.load(open(os.path.join(root, fn), encoding='utf-8'))
                except (OSError, ValueError):
                    continue
                hojas = []
                _hojas_str(data, hojas)
                for h in hojas:
                    # ~/.claude.json (snapshotted) brings leaves that are NOT
                    # secrets and also live in the repo (vendor URLs,
                    # the owner's email, skill slugs, human names)
                    # → they blocked the push as false positives.
                    if _hoja_inocua(h):
                        continue
                    vals.append(('cli-account', h))
    except OSError:
        pass
    return vals


def _url_inocua(s):
    """Simple vendor URL (domain + ≤2 path segments, no query or
    fragment): metadata, not a secret. A webhook with a token in the path
    (Slack style: /services/T…/B…/xxx, 3+ segments) or any URL with a
    query string is NOT harmless and is still caught by literal value."""
    if not s.startswith(('http://', 'https://')):
        return False
    if '?' in s or '#' in s:
        return False
    resto = s.split('://', 1)[1]
    segmentos = [p for p in resto.split('/')[1:] if p]
    return len(segmentos) <= 2


_EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')

# A normal path segment: folder/file names. Short and without the long
# uppercase+digit mix that gives away a token.
_SEGMENTO_RUTA = re.compile(r'^[A-Za-z0-9._+-]{1,64}$')
_ARRANQUE_RUTA = re.compile(r'^(/|~/|\./|\.\./|[A-Za-z]:[\\/])')


def _parece_token(seg):
    """Segment that looks like a credential: long and with a mix of uppercase,
    lowercase and digits. No real folder is named like that."""
    if len(seg) < 20:
        return False
    return (any(c.isupper() for c in seg) and any(c.islower() for c in seg)
            and any(c.isdigit() for c in seg))


def _ruta_inocua(s):
    """Filesystem path: it is metadata, not a credential.

    Codex stores in its snapshot the directories where you worked. Without this,
    those paths come in as "secrets" and block ANY commit in the repo that
    mentions them — a test, a script, a comment — with a message that talks
    about API keys and does not help to understand anything.

    Two guards against the obvious hole (hiding the token in the path):
    it requires 2+ segments, and none of them may look like a credential. And
    starting with '/' is not enough: the base64 alphabet includes the slash, so
    a real token can start the same way — that is why each segment has to be tame.
    """
    if not _ARRANQUE_RUTA.match(s):
        return False
    # The drive letter ("C:") is not a segment: remove it before splitting, or
    # the colon makes the pattern fail and every Windows path is left out.
    resto = s[2:] if re.match(r'^[A-Za-z]:[\\/]', s) else s
    segmentos = [p for p in re.split(r'[\\/]+', resto) if p and p not in ('~', '.', '..')]
    if len(segmentos) < 2:
        return False
    return all(_SEGMENTO_RUTA.match(p) and not _parece_token(p) for p in segmentos)


def _hoja_inocua(s):
    """Snapshot leaves that are NOT tokens: simple vendor URLs, human
    text with spaces ("…'s Organization"), emails, and kebab-case slugs
    without entropy (subagent-driven-development). A real token (mix of
    uppercase and digits, no spaces) never falls into these categories."""
    if _url_inocua(s):
        return True
    if _ruta_inocua(s):                  # where you worked, not what you log in with
        return True
    if any(c.isspace() for c in s):      # tokens have no spaces
        return True
    if _EMAIL_RE.match(s):               # account owner's email
        return True
    # Skill/plugin slug: lowercase words with 2+ hyphens and no digits
    # (subagent-driven-development). Looser is NOT allowed: "snap-zzz…" (1
    # hyphen) must still be caught — the snapshot test pins it.
    if re.fullmatch(r'[a-z]+(-[a-z]+){2,}', s):
        return True
    return False


def _hojas_str(obj, out, prof=0):
    """Accumulates into `out` the leaf strings >= 20 chars of a nested JSON
    (the token candidates). Bounded in depth so it does not hang."""
    if prof > 8 or len(out) > 500:
        return
    if isinstance(obj, str):
        if len(obj) >= 20:
            out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _hojas_str(v, out, prof + 1)
    elif isinstance(obj, list):
        for v in obj:
            _hojas_str(v, out, prof + 1)


def encontrar_secretos(texto, valores=()):
    """Findings in `text`: [{'patron', 'linea', 'valor'}]. `valores` are extra
    (name, real_value) pairs to search for literally."""
    hallazgos = []
    for num, linea in enumerate(texto.splitlines(), 1):
        for nombre, pat in PATRONES:
            m = pat.search(linea)
            if m:
                hallazgos.append(
                    {'patron': nombre, 'linea': num, 'valor': m.group(0)})
        for nombre, valor in valores:
            if valor and valor in linea:
                hallazgos.append(
                    {'patron': nombre, 'linea': num, 'valor': valor})
    return hallazgos


def _mascara(valor):
    return valor[:8] + '…' + f'({len(valor)} chars)'


def formatear(hallazgos):
    """Readable report. The full value is NEVER printed."""
    lineas = []
    for h in hallazgos:
        lineas.append(
            f"  line {h['linea']}: [{h['patron']}] {_mascara(h['valor'])}")
    return '\n'.join(lineas)


def main():
    origen = ''
    if '--origen' in sys.argv:
        origen = sys.argv[sys.argv.index('--origen') + 1]
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    texto = sys.stdin.read()
    hallazgos = encontrar_secretos(texto, valores=valores_locales(raiz))
    if not hallazgos:
        return 0
    print(f'\n🛑 SECRETS DETECTED{f" in {origen}" if origen else ""} '
          f'— operation BLOCKED:\n', file=sys.stderr)
    print(formatear(hallazgos), file=sys.stderr)
    print('\nAPI keys / tokens are personal and cost money: they NEVER '
          'go to the repo.\nRemove the secret from the change (use plotspace/.env or '
          'data/, which are gitignored).\nIf it is a real false positive, '
          'adjust the pattern in scripts/scan_secretos.py\n(with its test in '
          'plotspace/tests/test_scan_secretos.py).', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
