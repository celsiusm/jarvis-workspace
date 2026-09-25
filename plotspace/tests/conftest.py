"""Aislamiento global de los tests.

dev_detect persiste detectados/descartadas a data/dev_servers.json para
sobrevivir reinicios; los tests NO deben escribir ni leer el archivo real del
workspace (contaminarían el estado del server vivo con entradas fake).

Lo mismo, y peor, con la DB: un test que llame a `swarm_cli.enviar` escribe
mensajes de mentira en `data/jarvis.db` — y esos mensajes le aparecen DESPUÉS a
un agente de verdad en su `jv inbox`. Pasó (2026-07-25: cinco filas 'hola' de
Frontend a Backend en la DB del workspace vivo). Por eso la DB también se aísla
para TODA la suite."""
import pytest


@pytest.fixture(scope='session')
def _db_plantilla(tmp_path_factory):
    """Una DB con el esquema creado, UNA sola vez por corrida. Los tests reciben
    una copia (copiar el archivo sale más barato que correr init_db 1000 veces)."""
    import plotspace.core.database as db
    ruta = tmp_path_factory.mktemp('db-plantilla') / 'plantilla.db'
    original = db.DB_PATH
    db.DB_PATH = str(ruta)
    try:
        db.init_db()
    finally:
        db.DB_PATH = original
    return str(ruta)


@pytest.fixture(autouse=True)
def _db_aislada(tmp_path, monkeypatch, _db_plantilla):
    """Ningún test escribe en la DB del workspace vivo.

    Cada test arranca con una DB VACÍA pero con el esquema completo, así que el
    código bajo test encuentra sus tablas y ninguna fila de mentira sobrevive al
    test. Un fixture propio del test que pise `DB_PATH` sigue funcionando (corre
    después de este)."""
    import shutil
    import plotspace.core.database as db
    destino = tmp_path / 'jarvis-test.db'
    shutil.copy(_db_plantilla, destino)
    monkeypatch.setattr(db, 'DB_PATH', str(destino))


@pytest.fixture(autouse=True)
def _dev_detect_persistencia_aislada(tmp_path, monkeypatch):
    import plotspace.core.dev_detect as dd
    monkeypatch.setattr(dd, '_PERSIST_PATH', str(tmp_path / 'dev_servers.json'))
    monkeypatch.setattr(dd, '_persist_cargado', True)   # no cargar el archivo real
    monkeypatch.setattr(dd, '_persist_ultimo', None)


@pytest.fixture(autouse=True)
def _stt_env_aislado(monkeypatch):
    """La suite no debe depender del plotspace/.env del box: cualquier import de
    backend.main corre load_dotenv y, con STT_MOTOR=groq + GROQ_API_KEY reales
    en ese .env, prewarm/transcribe tomarían el camino remoto y los tests del
    motor local fallarían según la máquina. Se neutraliza acá; los tests de
    Groq setean su entorno explícito con monkeypatch (pisa a este fixture)."""
    monkeypatch.delenv('GROQ_API_KEY', raising=False)
    monkeypatch.delenv('STT_MOTOR', raising=False)


@pytest.fixture(autouse=True)
def _sin_auto_intervencion(monkeypatch):
    """La auto-intervención lanza el `claude -p` REAL en background: dentro de
    un test, un TASK_BLOCKED terminaba spawneando el CLI de verdad (y si la
    llamada completaba, un test unitario podía crear terminales o workflows
    reales). Además `_auto_intervenciones` es global al proceso y el tope por
    hora hacía que el resultado dependiera del orden de los tests. Apagada para
    toda la suite; el test que la ejercita la prende con monkeypatch."""
    import plotspace.routers.orchestrator as orch
    monkeypatch.setattr(orch, 'ORQ_AUTO_INTERVENCION', False)
    monkeypatch.setattr(orch, '_auto_intervenciones', [])


@pytest.fixture
def motor_tmux():
    """Fuerza el motor tmux para este test.

    tmux volvió a ser el ÚNICO motor (2026-08-06, se fue con la app de
    Windows), pero declararlo sigue siendo lo correcto: un test que asume en
    silencio cuál es el default deja de probar lo que cree si el default
    cambia algún día.
    """
    from plotspace.core import terminal_backend as _tb
    _tb.set_backend(_tb.TmuxBackend())
    yield
    _tb.set_backend(None)
