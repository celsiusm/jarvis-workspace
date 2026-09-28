"""
Test: detección de keywords de control (_linea_es_keyword en terminals.py).

Es el mecanismo anti-falso-positivo central del monitor de cierres: tiene
que distinguir el TASK_DONE REAL que escribe el agente del TASK_DONE que aparece
DENTRO de la instrucción ("Cuando termines escribí TASK_DONE"). Tres capas:
limpieza ANSI + regex "solo no-letras alrededor" + (baseline, no testeable acá).
La review lo marcó como gap Critical: solo había una copia testeada en agent_watch.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plotspace.routers.terminals import _linea_es_keyword


def test_keyword_solo_en_la_linea():
    assert _linea_es_keyword('TASK_DONE', 'TASK_DONE')
    assert _linea_es_keyword('TASK_BLOCKED', 'TASK_BLOCKED')
    assert _linea_es_keyword('TASK_ERROR', 'TASK_ERROR')


def test_keyword_con_simbolos_alrededor_ok():
    # Output real del agente suele venir con glyphs/puntuación, no letras
    assert _linea_es_keyword('✅ TASK_DONE', 'TASK_DONE')
    assert _linea_es_keyword('>>> TASK_DONE <<<', 'TASK_DONE')
    assert _linea_es_keyword('  TASK_DONE  ', 'TASK_DONE')


def test_instruccion_con_letras_alrededor_no_dispara():
    # El caso clásico de falso positivo: la propia instrucción de Jarvis
    assert not _linea_es_keyword('Cuando termines escribí TASK_DONE', 'TASK_DONE')
    assert not _linea_es_keyword('TASK_DONE ya quedó', 'TASK_DONE')
    assert not _linea_es_keyword('miTASK_DONE', 'TASK_DONE')


def test_limpia_codigos_ansi():
    # El verde de "TASK_DONE" no debe impedir el match
    assert _linea_es_keyword('\x1b[32mTASK_DONE\x1b[0m', 'TASK_DONE')


def test_keyword_distinto_no_matchea():
    assert not _linea_es_keyword('TASK_BLOCKED', 'TASK_DONE')
    assert not _linea_es_keyword('TASK_DONE', 'TASK_ERROR')


if __name__ == '__main__':
    import traceback
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith('test_') and callable(fn):
            try:
                fn(); print(f'ok  {nombre}')
            except Exception:
                fallos += 1; print(f'FAIL {nombre}'); traceback.print_exc()
    sys.exit(1 if fallos else 0)
