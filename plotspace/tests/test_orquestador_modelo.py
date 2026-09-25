"""
Test: modelo del orquestador configurable.

`ORQUESTADOR_MODEL` (env var) es la fuente única del modelo; sin override, el
default depende del motor (suscripción → sonnet, api → haiku).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plotspace.routers.orchestrator import ORQUESTADOR_MODEL


def test_default_segun_motor():
    # Sin env vars: motor suscripción (default) → sonnet (el costo por token
    # desapareció); con ORQUESTADOR_MOTOR=api el default vuelve a haiku.
    if not os.environ.get('ORQUESTADOR_MODEL') and not os.environ.get('ORQUESTADOR_MOTOR'):
        assert ORQUESTADOR_MODEL == 'sonnet'
