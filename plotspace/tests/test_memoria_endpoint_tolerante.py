"""La Lista/Grafo de Memoria no deben caerse porque la carpeta del proyecto no sea
escribible: leer la memoria es lo que importa, sembrar/regenerar el INDEX es «de paso»."""
import os
import tempfile

from fastapi.testclient import TestClient

from plotspace.core.database import get_db
from plotspace.main import app


def _proyecto(ruta):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO projects(nombre, ruta, fecha_creacion, ultimo_acceso) "
                    "VALUES('tolerante', ?, '2026-01-01', '2026-01-01')", (ruta,))
        pid = cur.lastrowid
        conn.commit()
        return pid
    finally:
        conn.close()


def _borrar(pid):
    conn = get_db()
    try:
        conn.execute('DELETE FROM projects WHERE id=?', (pid,))
        conn.commit()
    finally:
        conn.close()


def test_listar_no_da_500_si_la_ruta_no_es_una_carpeta():
    f = tempfile.NamedTemporaryFile(delete=False)
    f.close()
    pid = _proyecto(f.name)       # la «carpeta» del proyecto es un archivo: makedirs falla
    try:
        cl = TestClient(app, raise_server_exceptions=False)
        r = cl.get(f'/api/projects/{pid}/memory')
        assert r.status_code == 200, r.text
        d = r.json()
        assert 'memorias' in d and 'edges' in d
        assert cl.get(f'/api/projects/{pid}/memory/salud').status_code == 200
    finally:
        _borrar(pid)
        os.unlink(f.name)


def test_listar_lee_las_memorias_aunque_no_pueda_regenerar_el_indice(monkeypatch):
    from plotspace.routers import memory as mem
    with tempfile.TemporaryDirectory() as d:
        carpeta = os.path.join(d, '.jarvis', 'memory')
        os.makedirs(carpeta)
        with open(os.path.join(carpeta, 'una.md'), 'w', encoding='utf-8') as fh:
            fh.write('---\ntitulo: Una\ntags: [x]\nresumen: r\nestado: vigente\n---\ncuerpo\n')

        def _falla(*a, **k):
            raise PermissionError('solo lectura')
        monkeypatch.setattr(mem, '_regenerar_index', _falla)
        monkeypatch.setattr(mem, 'asegurar_memoria_proyecto', _falla)
        pid = _proyecto(d)
        try:
            r = TestClient(app, raise_server_exceptions=False).get(f'/api/projects/{pid}/memory')
            assert r.status_code == 200, r.text
            assert [m['slug'] for m in r.json()['memorias']] == ['una']
        finally:
            _borrar(pid)
