import pytest

from plotspace.routers.review import _parse_diff_hunks, _parse_blame


DIFF = """diff --git a/x.py b/x.py
index 1111111..2222222 100644
--- a/x.py
+++ b/x.py
@@ -1 +1,3 @@
-old
+new1
+new2
@@ -10,0 +12 @@
+added
@@ -20 +21,0 @@
-removed
"""


def test_hunks_mixtos():
    assert _parse_diff_hunks(DIFF) == [
        {'start': 1, 'end': 3, 'type': 'mod'},
        {'start': 12, 'end': 12, 'type': 'add'},
        {'start': 21, 'end': 21, 'type': 'del'},
    ]


def test_hunks_vacio():
    assert _parse_diff_hunks('') == []
    assert _parse_diff_hunks('no hay hunks acá\n') == []


def test_hunk_solo_agrega():
    d = '@@ -0,0 +1,2 @@\n+a\n+b\n'
    assert _parse_diff_hunks(d) == [{'start': 1, 'end': 2, 'type': 'add'}]


BLAME = """abcd1234abcd1234abcd1234abcd1234abcd1234 1 1 1
author Juan Perez
author-mail <juan@example.com>
author-time 1700000000
author-tz -0300
committer Juan Perez
summary Arregla el bug de login
filename x.py
\tprint(1)
"""


def test_parse_blame():
    b = _parse_blame(BLAME)
    assert b['hash'] == 'abcd1234'
    assert b['autor'] == 'Juan Perez'
    assert b['tiempo'] == 1700000000
    assert b['resumen'] == 'Arregla el bug de login'


def test_parse_blame_vacio():
    assert _parse_blame('') == {}
