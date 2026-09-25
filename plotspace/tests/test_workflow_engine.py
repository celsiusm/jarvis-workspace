"""
Test: motor de workflows — funciones puras de gating/terminación.

Eran puras y deterministas pero NINGÚN test las cubría (gap Critical de la
review). Acá se fija su contrato: qué pasos están listos para arrancar
(incluida la compuerta del reviewer y la resolución de dependencias) y cuándo
un workflow se considera terminado.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plotspace.routers.orchestrator import (
    _dep_indice,
    _pasos_listos_para_arrancar,
    _workflow_terminado,
    _progreso_workflow,
)


# ─── _dep_indice ─────────────────────────────────────────────────────────────

def test_dep_indice_parsea_paso_n():
    assert _dep_indice('paso_0') == 0
    assert _dep_indice('paso_2') == 2
    assert _dep_indice('paso_10') == 10


def test_dep_indice_null_o_no_parseable():
    assert _dep_indice(None) is None
    assert _dep_indice('') is None
    assert _dep_indice('ninguno') is None
    assert _dep_indice('paso_x') is None


# ─── _workflow_terminado ─────────────────────────────────────────────────────

def test_terminado_todos_done():
    assert _workflow_terminado([{'estado': 'done'}, {'estado': 'done'}])


def test_terminado_cuenta_blocked_y_error_como_terminales():
    assert _workflow_terminado([{'estado': 'done'}, {'estado': 'blocked'}, {'estado': 'error'}])


def test_no_terminado_si_hay_pending_o_running():
    assert not _workflow_terminado([{'estado': 'done'}, {'estado': 'pending'}])
    assert not _workflow_terminado([{'estado': 'running'}])


def test_terminado_lista_vacia():
    assert _workflow_terminado([])  # all() de vacío = True


# ─── _progreso_workflow ──────────────────────────────────────────────────────

def test_progreso_cuenta_done():
    assert _progreso_workflow([{'estado': 'done'}, {'estado': 'pending'}, {'estado': 'done'}]) == 2
    assert _progreso_workflow([{'estado': 'pending'}]) == 0


# ─── _pasos_listos_para_arrancar ─────────────────────────────────────────────

def test_listo_pending_sin_dependencia():
    pasos = [{'estado': 'pending', 'depende_de': None}]
    assert _pasos_listos_para_arrancar(pasos) == [0]


def test_no_listo_si_ya_running_o_done():
    pasos = [{'estado': 'running', 'depende_de': None},
             {'estado': 'done', 'depende_de': None}]
    assert _pasos_listos_para_arrancar(pasos) == []


def test_dependencia_resuelta_vs_pendiente():
    # paso 1 depende del índice 0
    pendiente = [{'estado': 'running', 'depende_de': None},
                 {'estado': 'pending', 'depende_de': 'paso_0'}]
    assert _pasos_listos_para_arrancar(pendiente) == []   # dep aún no done

    resuelta = [{'estado': 'done', 'depende_de': None},
                {'estado': 'pending', 'depende_de': 'paso_0'}]
    assert _pasos_listos_para_arrancar(resuelta) == [1]   # dep done → listo


def test_reviewer_espera_a_todos_los_demas():
    # El reviewer NO arranca mientras quede trabajo a medias
    a_medias = [{'estado': 'done'},
                {'estado': 'running'},
                {'estado': 'pending', 'rol': 'reviewer'}]
    assert 2 not in _pasos_listos_para_arrancar(a_medias)

    todo_listo = [{'estado': 'done'},
                  {'estado': 'done'},
                  {'estado': 'pending', 'rol': 'reviewer'}]
    assert _pasos_listos_para_arrancar(todo_listo) == [2]


def test_varios_listos_en_paralelo():
    pasos = [{'estado': 'pending', 'depende_de': None},
             {'estado': 'pending', 'depende_de': None}]
    assert _pasos_listos_para_arrancar(pasos) == [0, 1]


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


# ─── Dependencias múltiples, saneadas y pasos varados ───────────────────────

from plotspace.routers.orchestrator import (  # noqa: E402
    _deps_indices, _pasos_varados, _sanear_dependencias, _paso_de_terminal,
)


def test_deps_indices_acepta_varias_y_listas():
    assert _deps_indices(None) == []
    assert _deps_indices('paso_2') == [2]
    assert _deps_indices('paso_0, paso_1') == [0, 1]
    assert _deps_indices(['paso_0', 'paso_3']) == [0, 3]
    assert _deps_indices(1) == [1]
    assert _deps_indices('ninguno') == []


def test_multiples_deps_esperan_a_todas():
    pasos = [{'estado': 'done'}, {'estado': 'running'},
             {'estado': 'pending', 'depende_de': 'paso_0, paso_1'}]
    assert _pasos_listos_para_arrancar(pasos) == []
    pasos[1]['estado'] = 'done'
    assert _pasos_listos_para_arrancar(pasos) == [2]


def test_sanear_descarta_autodeps_ciclos_y_fuera_de_rango():
    """Solo vale depender de un paso ANTERIOR: eso hace imposible un ciclo y
    un paso que espera algo que no existe (antes quedaban pending para siempre)."""
    pasos = [
        {'depende_de': 'paso_0'},            # a sí mismo
        {'depende_de': 'paso_2'},            # hacia adelante (ciclo potencial)
        {'depende_de': 'paso_1'},            # válido
        {'depende_de': 'paso_9, paso_0'},    # uno fuera de rango, uno válido
        {'depende_de': 'basura'},            # no parseable
    ]
    avisos = _sanear_dependencias(pasos)
    assert [p['depende_de'] for p in pasos] == [None, None, 'paso_1', 'paso_0', None]
    assert len(avisos) == 4


def test_paso_con_dep_bloqueada_queda_varado_transitivamente():
    pasos = [{'estado': 'blocked'},
             {'estado': 'pending', 'depende_de': 'paso_0'},
             {'estado': 'pending', 'depende_de': 'paso_1'},
             {'estado': 'pending', 'depende_de': None}]
    assert _pasos_varados(pasos) == {1, 2}


def test_reviewer_arranca_aunque_haya_pasos_varados():
    """Un paso que depende de uno bloqueado nunca va a arrancar: el Reviewer
    no puede esperarlo (antes el workflow quedaba colgado para siempre)."""
    pasos = [{'estado': 'error'},
             {'estado': 'pending', 'depende_de': 'paso_0'},
             {'estado': 'pending', 'rol': 'reviewer'}]
    assert _pasos_listos_para_arrancar(pasos) == [2]
    pasos[2]['estado'] = 'done'
    assert _workflow_terminado(pasos)


def test_paso_de_terminal_prioriza_running_y_no_inventa():
    pasos = [{'terminal_id': 5, 'estado': 'done'},
             {'terminal_id': 5, 'estado': 'running'},
             {'terminal_id': 6, 'estado': 'blocked'}]
    assert _paso_de_terminal(pasos, 5) == 1
    assert _paso_de_terminal(pasos, 6) == 2, 'un bloqueado que se destraba cierra su paso'
    assert _paso_de_terminal(pasos, 99) is None, 'terminal ajena: jamás paso_actual'


def test_entero_tolera_basura_del_modelo():
    from plotspace.routers.orchestrator import _entero
    assert _entero('3', 1) == 3
    assert _entero(None, 1) == 1
    assert _entero('dos', None) is None
    assert _entero(2.0, 1) == 2
