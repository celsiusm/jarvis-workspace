"""
Test: auto-intervención en TASK_BLOCKED/ERROR (Etapa 5 del rework).

Antes, ante un TASK_BLOCKED el sistema broadcasteaba "¿cómo continuamos?" y
quedaba ESPERANDO al humano — la sección de manejo de errores del system
prompt nunca se ejercitaba sola. Ahora el orquestador se llama a sí mismo con
el contexto del evento y re-instruye al agente. Viable porque corre con la
SUSCRIPCIÓN (motor CLI), no con API paga. Guardas que fija este test:
una sola intervención por paso + tope por hora + flag ORQ_AUTO_INTERVENCION.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plotspace.routers.orchestrator import (
    ORQ_AUTO_INTERVENCION,
    _mensaje_auto_intervencion,
    _puede_auto_intervenir,
)


def test_flag_default_on():
    if not os.environ.get('ORQ_AUTO_INTERVENCION'):
        assert ORQ_AUTO_INTERVENCION is True


def test_interviene_en_paso_virgen():
    assert _puede_auto_intervenir({'estado': 'blocked'}, True, [], ahora=1000.0)


def test_respeta_flag_apagado():
    assert not _puede_auto_intervenir({'estado': 'blocked'}, False, [], ahora=1000.0)


def test_una_sola_vez_por_paso():
    paso = {'estado': 'blocked', 'auto_intervencion_ts': 900.0}
    assert not _puede_auto_intervenir(paso, True, [], ahora=1000.0)


def test_tope_por_hora():
    ahora = 10_000.0
    recientes = [ahora - 60 * i for i in range(6)]      # 6 en la última hora
    assert not _puede_auto_intervenir({'estado': 'blocked'}, True, recientes,
                                      ahora=ahora, tope=6)
    # las viejas (>1h) no cuentan
    viejas = [ahora - 4000 - i for i in range(6)]
    assert _puede_auto_intervenir({'estado': 'blocked'}, True, viejas,
                                  ahora=ahora, tope=6)


def test_mensaje_de_intervencion():
    m = _mensaje_auto_intervencion('TASK_BLOCKED', 2, 'Auth JWT',
                                   'no sé si la tabla users existe', 'Backend')
    assert 'TASK_BLOCKED' in m and 'paso_2' in m and 'Auth JWT' in m
    assert 'tabla users' in m and 'Backend' in m
    # instruye resolver solo, y escalar únicamente con UNA pregunta concreta
    assert 'AUTO-INTERVENCIÓN' in m
    assert 'pregunta' in m.lower()


def test_mensaje_sin_motivo():
    m = _mensaje_auto_intervencion('TASK_ERROR', 0, 'X', '', 'Front')
    assert 'sin motivo' in m


def test_lanzar_dispara_en_fondo_con_referencia_fuerte(monkeypatch):
    """Con el flag prendido, BLOCKED dispara la intervención en background y la
    tarea queda referenciada hasta terminar (sin eso el GC podía matarla)."""
    import asyncio
    import plotspace.routers.orchestrator as orch
    monkeypatch.setattr(orch, 'ORQ_AUTO_INTERVENCION', True)
    monkeypatch.setattr(orch, 'ORQUESTADOR_MOTOR', 'suscripcion')
    monkeypatch.setattr(orch, '_actualizar_workflow_db', lambda *a, **k: None)
    llamadas = []

    async def falso(*args):
        llamadas.append(args)

    monkeypatch.setattr(orch, '_auto_intervenir', falso)

    async def main():
        pasos = [{'estado': 'blocked'}]
        orch._lanzar_auto_intervencion({'id': 1, 'nombre': 'W'}, pasos, 0, 7,
                                       'TASK_BLOCKED', 'falta dato', 'Back')
        assert len(orch._tareas_fondo) == 1
        await asyncio.gather(*list(orch._tareas_fondo))
        assert pasos[0]['auto_intervencion_ts']
    asyncio.run(main())
    assert llamadas and llamadas[0][0] == 7
    assert not orch._tareas_fondo
