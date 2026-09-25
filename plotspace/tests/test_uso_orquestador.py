"""
Test: tracking de uso del orquestador.

El UPSERT acumulativo en la tabla orquestador_uso (antes response.usage se
tiraba).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plotspace.tests._harness import fresh_db


def test_upsert_acumula():
    fresh_db()
    from plotspace.core.database import registrar_uso_orquestador, obtener_uso_orquestador
    registrar_uso_orquestador(7, 100, 50)
    registrar_uso_orquestador(7, 200, 30)
    uso = obtener_uso_orquestador(7)
    assert uso == {'input_tokens': 300, 'output_tokens': 80, 'llamadas': 2}


def test_obtener_sin_datos_da_ceros():
    fresh_db()
    from plotspace.core.database import obtener_uso_orquestador
    assert obtener_uso_orquestador(999) == {'input_tokens': 0, 'output_tokens': 0, 'llamadas': 0}


def test_uso_por_proyecto_separado():
    fresh_db()
    from plotspace.core.database import registrar_uso_orquestador, obtener_uso_orquestador
    registrar_uso_orquestador(1, 10, 5)
    registrar_uso_orquestador(2, 99, 99)
    assert obtener_uso_orquestador(1)['input_tokens'] == 10
    assert obtener_uso_orquestador(2)['input_tokens'] == 99


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
