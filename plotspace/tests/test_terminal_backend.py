"""Tests de la interfaz del motor de terminales (`core/terminal_backend.py`).

POR QUÉ ESTOS TESTS EXISTEN
===========================
Este módulo nació de sacar la lógica de tmux de `terminals.py` para que un
segundo motor (ConPTY, vía el termhost, en Windows) pueda enchufarse sin tocar
al resto. Un refactor así es peligroso justo por lo que NO se ve: cada flag de
estos comandos está puesto por una regresión que ya pasó una vez.

Por eso los tests de acá no verifican "que ande" — verifican el ARGV EXACTO.
Si alguien más adelante saca el `=` del kill, el `-l --` del send-keys o el
`window-size latest`, no se entera por un síntoma raro tres semanas después:
se entera acá.

Cada aserción tiene al lado el bug que evita.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plotspace.core import terminal_backend as tb
from plotspace.core.terminal_backend import EspecSesion, TerminalBackend, TmuxBackend

# Estos tests verifican el argv EXACTO de tmux: desde F5·2 el default es el
# termhost, así que hay que pedir ese motor explícitamente (ver conftest).
import pytest

pytestmark = pytest.mark.usefixtures('motor_tmux')



class FakeRun:
    """Sustituto de subprocess.run que anota cada llamada y devuelve lo que se
    le programe. `respuestas` mapea un fragmento del comando → (rc, stdout)."""

    def __init__(self, respuestas=None, default=(0, '')):
        self.llamadas = []
        self.respuestas = respuestas or {}
        self.default = default

    def __call__(self, argv, **kw):
        self.llamadas.append((list(argv), kw))
        linea = ' '.join(str(a) for a in argv)
        for fragmento, (rc, out) in self.respuestas.items():
            if fragmento in linea:
                return subprocess.CompletedProcess(argv, rc, out, '')
        rc, out = self.default
        return subprocess.CompletedProcess(argv, rc, out, '')

    def argv_con(self, *fragmentos):
        """Primer argv que contiene TODOS los fragmentos dados."""
        for argv, _ in self.llamadas:
            if all(f in argv for f in fragmentos):
                return argv
        return None

    def hubo(self, *fragmentos):
        return self.argv_con(*fragmentos) is not None


def _limpiar_guards():
    """Los guards globales ('una vez por proceso') harían que el segundo test
    no vea los comandos del primero."""
    tb._ESTILO_OBSIDIAN_APLICADO = False
    tb._COPY_MODE_BINDINGS_INSTALADOS = False


def _fake(monkeypatch, respuestas=None, default=(0, '')):
    _limpiar_guards()
    f = FakeRun(respuestas, default)
    monkeypatch.setattr(tb.subprocess, 'run', f)
    return f


# ── identidad ────────────────────────────────────────────────────────────

def test_nombre_de_sesion_es_el_historico():
    # El nombre NO es cosmético: es la identidad del agente para los hooks, el
    # guard de propiedad, el sentinel y el CLI `jv`. Cambiarlo rompe el enjambre.
    assert TmuxBackend().nombre_sesion(441) == 'jarvis_441'


def test_targets_tmux_son_exactos_no_por_prefijo(monkeypatch):
    # Sin '=', tmux resuelve por PREFIJO: con `jarvis_1` muerta y `jarvis_12`
    # viva, has-session/send-keys/capture-pane de la 1 caían en la 12.
    f = _fake(monkeypatch, {'has-session': (0, '')})
    b = TmuxBackend()
    b.existe(1)
    assert f.argv_con('has-session') == ['tmux', 'has-session', '-t', '=jarvis_1']
    b.enviar_tecla(1, 'Enter')
    assert f.argv_con('send-keys')[3] == '=jarvis_1:'


# ── creación ─────────────────────────────────────────────────────────────

def test_crear_arma_el_argv_completo(monkeypatch, tmp_path):
    f = _fake(monkeypatch, {'has-session': (1, '')})   # no existe todavía
    ok = TmuxBackend().crear(EspecSesion(
        terminal_id=7, cwd=str(tmp_path), comando='claude; exec bash -l',
        env={'JARVIS_TERMINAL_ID': '7', 'CODEX_HOME': '/tmp/codex'},
        cols=220, rows=50,
    ))
    assert ok is True
    argv = f.argv_con('new-session')
    assert argv is not None, 'no se llamó a new-session'
    # -d: detached. Sin esto tmux intenta tomar la terminal del server.
    assert argv[:5] == ['tmux', 'new-session', '-d', '-s', 'jarvis_7']
    # Tamaño explícito: sin -x/-y tmux hereda 80x24 y el primer pintado sale
    # recortado hasta que el browser manda su tamaño real.
    assert '-x' in argv and '220' in argv and '-y' in argv and '50' in argv
    # Cada variable va como su propio -e (entorno de SESIÓN, no del proceso).
    assert argv[argv.index('-e') + 1] == 'JARVIS_TERMINAL_ID=7'
    assert 'CODEX_HOME=/tmp/codex' in argv
    # El comando va ÚLTIMO y entero: partirlo hace que tmux tome los pedazos
    # como argumentos suyos.
    assert argv[-1] == 'claude; exec bash -l'


def test_crear_sin_comando_deja_shell_pelado(monkeypatch, tmp_path):
    # Arranque VISIBLE: el pane nace como shell de login (se ve el prompt de
    # WSL) y el CLI se tipea corto después. Si acá se colara un comando vacío,
    # tmux lo tomaría como el programa del pane y no nacería el shell.
    f = _fake(monkeypatch, {'has-session': (1, '')})
    TmuxBackend().crear(EspecSesion(terminal_id=8, cwd=str(tmp_path), comando=None))
    argv = f.argv_con('new-session')
    assert argv[-1] == '50', f'no debería haber comando al final: {argv[-1]!r}'


def test_crear_no_recrea_una_sesion_viva(monkeypatch, tmp_path):
    # Idempotencia: el reconcile del boot y el attach pueden pedir la creación
    # de una sesión que ya existe. Recrearla mataría al agente que trabaja.
    f = _fake(monkeypatch, {'has-session': (0, '')})   # ya existe
    assert TmuxBackend().crear(EspecSesion(terminal_id=9, cwd=str(tmp_path))) is True
    assert not f.hubo('new-session'), 'recreó una sesión viva'


def test_crear_aplica_las_opciones_obligatorias(monkeypatch, tmp_path):
    f = _fake(monkeypatch, {'has-session': (1, '')})
    TmuxBackend().crear(EspecSesion(terminal_id=10, cwd=str(tmp_path)))
    # window-size latest: sin esto, dos clientes de distinto tamaño clavan la
    # ventana al más chico y el sobrante se rellena con '·'.
    assert f.hubo('set-option', 'window-size', 'latest')
    # status off: la barra de tmux comía una fila del agente y era ruido.
    assert f.hubo('set-option', 'status', 'off')
    assert f.hubo('set-option', 'mouse', 'on')
    # focus-events off: el pane no debe reaccionar al foco del cliente.
    assert f.hubo('set-option', 'focus-events', 'off')


def test_crear_devuelve_false_si_tmux_falla(monkeypatch, tmp_path):
    # Un fallo de creación tiene que propagarse: si se traga, la card aparece
    # vacía y el usuario cree que el agente arrancó.
    f = _fake(monkeypatch, {'has-session': (1, ''), 'new-session': (1, '')})
    assert TmuxBackend().crear(EspecSesion(terminal_id=11, cwd=str(tmp_path))) is False


# ── muerte ───────────────────────────────────────────────────────────────

def test_matar_usa_target_exacto(monkeypatch):
    # SIN el '=', tmux resuelve por PREFIJO cuando no hay match exacto: matar
    # una jarvis_1 ya muerta se llevaba puesta a jarvis_12, viva y trabajando.
    import asyncio
    f = _fake(monkeypatch, {'has-session': (1, '')})   # tras el kill, no existe
    monkeypatch.setattr(tb.asyncio, 'to_thread',
                        lambda fn, *a, **kw: _corutina(fn(*a, **kw)))
    assert asyncio.run(TmuxBackend().matar(1)) is True
    argv = f.argv_con('kill-session')
    assert '=jarvis_1' in argv, f'target sin "=": {argv}'


def test_matar_devuelve_false_si_la_sesion_sobrevive(monkeypatch):
    # El "agente fantasma": tmux degradado devuelve rc=0 al kill pero la sesión
    # sigue viva, editando el repo sin card visible. Tiene que dar False.
    import asyncio
    _fake(monkeypatch, {'has-session': (0, '')})       # sigue viva tras el kill
    monkeypatch.setattr(tb.asyncio, 'to_thread',
                        lambda fn, *a, **kw: _corutina(fn(*a, **kw)))
    assert asyncio.run(TmuxBackend().matar(2)) is False


async def _corutina(valor):
    return valor


# ── entrada ──────────────────────────────────────────────────────────────

def test_enviar_texto_es_literal(monkeypatch):
    # '-l --' literal: sin eso, un texto que contenga "Enter", "C-c" o algo que
    # empiece con '-' se interpreta como tecla o como flag de tmux. Es el
    # patrón anti-inyección del pegado de tareas a los agentes.
    f = _fake(monkeypatch)
    TmuxBackend().enviar_texto(3, '--peligroso Enter')
    argv = f.argv_con('send-keys')
    assert argv == ['tmux', 'send-keys', '-t', '=jarvis_3:', '-l', '--', '--peligroso Enter']


def test_enviar_tecla_no_es_literal(monkeypatch):
    # Enter SÍ tiene que interpretarse como tecla: va por otro camino a propósito.
    f = _fake(monkeypatch)
    TmuxBackend().enviar_tecla(3, 'Enter')
    assert f.argv_con('send-keys') == ['tmux', 'send-keys', '-t', '=jarvis_3:', 'Enter']


# ── lectura ──────────────────────────────────────────────────────────────

def test_capturar_pantalla_y_scrollback(monkeypatch):
    f = _fake(monkeypatch, default=(0, 'contenido'))
    b = TmuxBackend()

    assert b.capturar(4) == 'contenido'
    assert f.argv_con('capture-pane') == ['tmux', 'capture-pane', '-t', '=jarvis_4:', '-p']

    f.llamadas.clear()
    b.capturar(4, lineas=100)
    argv = f.argv_con('capture-pane')
    assert '-S' in argv and '-100' in argv, f'sin scrollback: {argv}'

    f.llamadas.clear()
    b.capturar(4, con_escapes=True)
    assert '-e' in f.argv_con('capture-pane'), 'perdió los colores del pane'


def test_capturar_todo_el_scrollback(monkeypatch):
    # El watchdog rescata un TASK_* perdido releyendo el buffer ENTERO, no una
    # ventana: si el agente escribió mucho después de terminar, el cierre queda
    # fuera de las últimas N líneas y el paso se da por colgado para siempre.
    # Antes lo hacía con su propio `tmux capture-pane -S -`, que en Windows no
    # existe; ahora lo pide por el motor y cada uno lo traduce a lo suyo.
    f = _fake(monkeypatch, default=(0, 'todo'))
    b = TmuxBackend()

    assert b.capturar(4, lineas=tb.TODO_EL_SCROLLBACK) == 'todo'
    argv = f.argv_con('capture-pane')
    i = argv.index('-S')
    assert argv[i + 1] == '-', f'no pidió el buffer entero: {argv}'


def test_todo_el_scrollback_no_se_confunde_con_un_numero():
    # El centinela tiene que ser inconfundible: si fuera 0 o None chocaría con
    # "solo la pantalla", que es justo lo contrario.
    assert tb.TODO_EL_SCROLLBACK not in (0, None)
    assert isinstance(tb.TODO_EL_SCROLLBACK, int)


def test_capturar_devuelve_none_si_falla(monkeypatch):
    # Degradación segura: quien lee la pantalla (agent_watch, el sentinel)
    # tiene que poder distinguir "vacío" de "no pude leer".
    _fake(monkeypatch, default=(1, ''))
    assert TmuxBackend().capturar(5) is None


def test_titulos_vivos_mapea_sesion_a_titulo(monkeypatch):
    # El título del pane es lo que la card muestra como "qué está haciendo el
    # agente". Un título con tabs adentro no debe partir el parseo.
    _fake(monkeypatch, default=(0, 'jarvis_1\tescribiendo tests\njarvis_2\tidle\nbasura\n'))
    titulos = TmuxBackend().titulos_vivos()
    assert titulos == {'jarvis_1': 'escribiendo tests', 'jarvis_2': 'idle'}


def test_listar_sesiones(monkeypatch):
    _fake(monkeypatch, default=(0, 'jarvis_1\njarvis_2\notra\n'))
    assert TmuxBackend().listar_sesiones() == {'jarvis_1', 'jarvis_2', 'otra'}


def test_listar_sesiones_vacio_si_no_hay_server(monkeypatch):
    # tmux sin server devuelve rc≠0: el reconcile del boot no debe explotar.
    _fake(monkeypatch, default=(1, ''))
    assert TmuxBackend().listar_sesiones() == set()


# ── repintado ────────────────────────────────────────────────────────────

def test_redimensionar_ignora_tamanos_degenerados(monkeypatch):
    # Un container colapsado en el browser manda 1x1: si eso llega a tmux,
    # reformatea el output a una letra por línea y la terminal queda ilegible.
    import asyncio
    f = _fake(monkeypatch)
    asyncio.run(TmuxBackend().redimensionar(6, 1, 1))
    assert not f.llamadas, 'un tamaño degenerado llegó a tmux'


def test_redimensionar_no_usa_resize_window(monkeypatch):
    # resize-window deja la ventana en 'window-size manual', que la CLAVA y
    # anula el 'latest' → vuelve el bug del rectángulo con puntitos. El tamaño
    # real ya lo propaga el SIGWINCH del PTY; acá solo se repinta.
    import asyncio
    f = _fake(monkeypatch, default=(0, '/dev/pts/3\n'))
    monkeypatch.setattr(tb.asyncio, 'to_thread',
                        lambda fn, *a, **kw: _corutina(fn(*a, **kw)))
    asyncio.run(TmuxBackend().redimensionar(6, 120, 40))
    assert not f.hubo('resize-window'), 'volvió el resize-window que clava el tamaño'


def test_refrescar_apunta_al_tty_no_a_la_sesion(monkeypatch):
    # 'refresh-client -t <sesión>' falla con "can't find client" en tmux 3.6 y
    # el error se tragaba por capture_output → el garble no se auto-sanaba y
    # había que apretar F5. Hay que enumerar los clientes y refrescar cada tty.
    import asyncio
    f = _fake(monkeypatch, default=(0, '/dev/pts/3\n/dev/pts/7\n'))
    monkeypatch.setattr(tb.asyncio, 'to_thread',
                        lambda fn, *a, **kw: _corutina(fn(*a, **kw)))
    asyncio.run(TmuxBackend().refrescar(6))
    assert f.hubo('refresh-client', '/dev/pts/3')
    assert f.hubo('refresh-client', '/dev/pts/7')
    assert not f.hubo('refresh-client', 'jarvis_6'), 'refrescó la sesión en vez del tty'


# ── selección del motor ──────────────────────────────────────────────────

def test_tmux_sigue_siendo_alcanzable(monkeypatch):
    """Desde F5·2 el default es el termhost, en los tres sistemas.

    Este test antes afirmaba lo contrario (`el default es tmux`). Lo que
    importa ahora es que tmux siga siendo ALCANZABLE: es la vía de escape del
    cambio, y borrarla dejaría sin salida a quien dependa de algo que solo tmux
    hace. Qué motor sale por defecto lo cubre `test_motor_default.py`.
    """
    tb.set_backend(None)
    monkeypatch.setenv('TERMINALES_MOTOR', 'tmux')
    assert isinstance(tb.backend(), TmuxBackend)
    tb.set_backend(None)


def test_backend_es_inyectable():
    # El punto de toda esta refactorización: que un segundo motor entre sin
    # tocar a nadie más.
    class Falso(TerminalBackend):
        def nombre_sesion(self, tid): return f'falso_{tid}'
        def existe(self, tid): return True
        def crear(self, espec): return True
        async def matar(self, tid): return True
        def enviar_texto(self, tid, texto): pass
        def enviar_tecla(self, tid, tecla): pass
        def capturar(self, tid, lineas=None, con_escapes=False): return ''
        async def refrescar(self, tid): pass
        async def redimensionar(self, tid, cols, rows): pass
        def listar_sesiones(self): return set()
        def titulos_vivos(self): return {}
        def estado_pane(self, tid, formato): return None

    try:
        tb.set_backend(Falso())
        assert tb.backend().nombre_sesion(1) == 'falso_1'
        # Los opcionales tienen default: un motor sin estado global no
        # necesita implementarlos.
        tb.backend().preparar_servidor()
        tb.backend().sanear_sesion('falso_1')
    finally:
        tb.set_backend(None)


# ── captura para los pollers ─────────────────────────────────────────────

def test_capturar_async_default_envuelve_la_sincronica():
    """Un motor que no tenga vía async nativa no queda sin servir a los
    pollers: el default los atiende metiendo la sincrónica en un thread."""
    import asyncio

    class SoloSync(TerminalBackend):
        def nombre_sesion(self, tid): return f's{tid}'
        def existe(self, tid): return True
        def crear(self, espec): return True
        async def matar(self, tid): return True
        def enviar_texto(self, tid, texto): pass
        def enviar_tecla(self, tid, tecla): pass
        def capturar(self, tid, lineas=None, con_escapes=False): return f'pantalla {lineas}'
        async def refrescar(self, tid): pass
        async def redimensionar(self, tid, cols, rows): pass
        def listar_sesiones(self): return set()
        def titulos_vivos(self): return {}
        def estado_pane(self, tid, formato): return None

    assert asyncio.run(SoloSync().capturar_async(1, 120)) == 'pantalla 120'


def test_capturar_async_nunca_devuelve_none():
    """Los pollers hacen .splitlines() sobre esto: un None los rompería a
    todos (sonidos, aura, detección de dev servers) ante un motor caído."""
    import asyncio

    class Caido(TerminalBackend):
        def nombre_sesion(self, tid): return f's{tid}'
        def existe(self, tid): return False
        def crear(self, espec): return False
        async def matar(self, tid): return True
        def enviar_texto(self, tid, texto): pass
        def enviar_tecla(self, tid, tecla): pass
        def capturar(self, tid, lineas=None, con_escapes=False): return None
        async def refrescar(self, tid): pass
        async def redimensionar(self, tid, cols, rows): pass
        def listar_sesiones(self): return set()
        def titulos_vivos(self): return {}
        def estado_pane(self, tid, formato): return None

    assert asyncio.run(Caido().capturar_async(1)) == ''


if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main([__file__, '-q']))
