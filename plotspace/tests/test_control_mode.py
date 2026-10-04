"""
Tests del parser de tmux CONTROL MODE (`core/control_mode.py`) — la base del
motor de terminales de UN emulador.

Arquitectura (validada por spike 2026-07-02, tmux 3.6): el attach -C no emula
cliente — entrega los bytes CRUDOS de la app en eventos `%output %<pane> <payload>`
con no-imprimibles escapados en octal (\\NNN). El browser (xterm.js) pasa a ser
el ÚNICO emulador; tmux queda como guardián de procesos + caño. El parser es
PURO e INCREMENTAL: recibe chunks arbitrarios (cortados donde sea) y emite
eventos completos. El payload octal se decodifica a BYTES y recién ahí a UTF-8
INCREMENTAL (un carácter multibyte partido entre chunks/escapes no debe romper
— misma clase de bug que la sexta capa de [[tmux-size-clamping]]).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plotspace.core.control_mode import ParserControlMode, decodificar_payload


def test_decodificar_payload_ascii_y_octal():
    # ASCII plano pasa tal cual; \NNN es un byte en octal; \\ es backslash literal.
    assert decodificar_payload(b'hola') == b'hola'
    assert decodificar_payload(b'\\033[31mROJO\\033[0m') == b'\x1b[31mROJO\x1b[0m'
    assert decodificar_payload(b'a\\\\b') == b'a\\b'
    assert decodificar_payload(b'') == b''


def test_decodificar_payload_utf8_crudo_y_octal():
    # REGRESIÓN REAL (2026-07-02, "las terminales muestran ? por cada byte"):
    # tmux 3.6 pasa los bytes UTF-8 >127 CRUDOS en %output (NO los escapa) —
    # el pipeline debe ser BYTES de punta a punta; una pasada por ASCII los
    # convertía en '?' (é→??, ─→???, ✅→???). El octal queda solo para los
    # bytes de control que tmux SÍ escapa (\033, \015...).
    assert decodificar_payload('mañana'.encode()) == 'mañana'.encode()
    assert decodificar_payload('✅ ─ 🚀'.encode()) == '✅ ─ 🚀'.encode()
    # Mezcla: control escapado en octal + UTF-8 crudo en el mismo payload.
    crudo = b'\\033[31m' + 'está'.encode() + b'\\015'
    assert decodificar_payload(crudo) == b'\x1b[31m' + 'está'.encode() + b'\r'
    # Y si algún tmux escapara los bytes altos en octal, también funciona.
    assert decodificar_payload(b'ma\\303\\261ana') == 'mañana'.encode('utf-8')
    assert decodificar_payload(b'\\360\\237\\232\\200') == '🚀'.encode('utf-8')


def test_parser_output_simple():
    p = ParserControlMode()
    evs = p.alimentar(b'%output %0 hola\\015\\012\n')
    assert evs == [('output', '%0', b'hola\r\n')]


def test_parser_chunks_partidos():
    # Los chunks llegan cortados en CUALQUIER lado (incluso a mitad de un escape).
    p = ParserControlMode()
    evs = []
    for pedazo in [b'%out', b'put %3 ro', b'jo=\\03', b'3[31m\n%output %3 fin\n']:
        evs += p.alimentar(pedazo)
    assert evs == [('output', '%3', b'rojo=\x1b[31m'),
                   ('output', '%3', b'fin')]
    # Y cortado A MITAD de un carácter UTF-8 crudo (bytes altos sin escapar).
    p = ParserControlMode()
    evs = []
    mitad = '%output %1 está\n'.encode()
    for pedazo in (mitad[:13], mitad[13:]):   # corta adentro de la 'á'
        evs += p.alimentar(pedazo)
    assert evs == [('output', '%1', 'está'.encode())]


def test_parser_bloques_begin_end():
    # Las respuestas a comandos van en bloques %begin/%end: se emiten como
    # evento 'respuesta' con las líneas del cuerpo (para futuros usos) y NO
    # contaminan el stream de output.
    p = ParserControlMode()
    evs = p.alimentar(b'%begin 123 45 0\nlinea1\nlinea2\n%end 123 45 0\n')
    assert evs == [('respuesta', ['linea1', 'linea2'])]

    # %error también cierra el bloque, marcado como error.
    evs = p.alimentar(b'%begin 1 2 0\nboom\n%error 1 2 0\n')
    assert evs == [('respuesta_error', ['boom'])]


def test_parser_notificaciones():
    # Notificaciones sueltas que nos importan: exit / sesión terminada.
    p = ParserControlMode()
    assert p.alimentar(b'%exit\n') == [('exit', '')]
    assert p.alimentar(b'%exit detached\n') == [('exit', 'detached')]
    # Las que no manejamos se emiten como ('notif', línea) — el caller decide.
    evs = p.alimentar(b'%window-renamed @1 claude\n')
    assert evs == [('notif', '%window-renamed @1 claude')]


def test_parser_no_confunde_output_dentro_de_bloque():
    # Una línea que ARRANCA con %output pero está DENTRO de un bloque begin/end
    # es cuerpo de respuesta (p.ej. un capture-pane de un pane que muestra
    # texto '%output ...'), no un evento de stream.
    p = ParserControlMode()
    evs = p.alimentar(b'%begin 9 9 0\n%output %0 fake\n%end 9 9 0\n')
    assert evs == [('respuesta', ['%output %0 fake'])]


def test_parser_utf8_partido_entre_eventos():
    # Un carácter multibyte cuyo primer byte cae en un %output y el resto en el
    # siguiente NO debe romper: la conversión a str es responsabilidad del
    # consumidor (decoder UTF-8 incremental) — el parser entrega BYTES.
    p = ParserControlMode()
    evs = p.alimentar(b'%output %0 ma\\303\n%output %0 \\261ana\n')
    assert evs[0] == ('output', '%0', b'ma\xc3')
    assert evs[1] == ('output', '%0', b'\xb1ana')
    # y el decoder incremental del consumidor lo re-arma:
    import codecs
    d = codecs.getincrementaldecoder('utf-8')(errors='replace')
    texto = d.decode(evs[0][2]) + d.decode(evs[1][2])
    assert texto == 'mañana'


def test_parser_linea_gigante_no_explota():
    # Un flood puede traer payloads enormes en una línea: se procesa entero.
    p = ParserControlMode()
    payload = b'x' * 300_000
    evs = p.alimentar(b'%output %7 ' + payload + b'\n')
    assert evs == [('output', '%7', payload)]


def test_comandos_send_keys_hex_y_chunks():
    from plotspace.core.control_mode import comandos_send_keys
    # Bytes arbitrarios → hex sin ambigüedad de quoting.
    cmds = comandos_send_keys('jarvis_9', b'ls\r')
    assert cmds == ['send-keys -t jarvis_9 -H 6c 73 0d']
    # Un paste grande se parte en chunks FIFO.
    cmds = comandos_send_keys('s', b'a' * 600, chunk=256)
    assert len(cmds) == 3
    assert all(c.startswith('send-keys -t s -H ') for c in cmds)
    assert sum(c.count('61') for c in cmds) == 600
    # UTF-8 multibyte viaja como sus bytes.
    cmds = comandos_send_keys('s', 'ñ'.encode())
    assert cmds == ['send-keys -t s -H c3 b1']


def test_armar_seed_reconstruye_pantalla_y_cursor():
    from plotspace.core.control_mode import armar_seed
    # \n → \r\n, sin newline final colgante, cursor CUP 1-based.
    seed = armar_seed('linea1\nlinea2\n', alt_on=False, cursor_x=7, cursor_y=1, rows=24)
    assert seed == 'linea1\r\nlinea2\x1b[2;8H'
    # App en pantalla alternativa: se antepone el switch para que xterm entre.
    seed = armar_seed('TUI\n', alt_on=True, cursor_x=0, cursor_y=0, rows=24)
    assert seed.startswith('\x1b[?1049h')
    assert seed.endswith('\x1b[1;1H')
    # El cursor jamás se va de la pantalla (clamp a rows).
    seed = armar_seed('x\n', alt_on=False, cursor_x=0, cursor_y=99, rows=24)
    assert seed.endswith('\x1b[24;1H')


def test_armar_seed_restaura_modos_de_la_app():
    """Los MODOS que la app activó ANTES del attach (mouse, cursor-keys) no
    viajan por el stream (ya pasaron): el seed los re-enuncia leyendo los
    flags que tmux trackea del pane. Sin esto, una TUI viva reconectada queda
    con el mouse/las flechas rotos hasta su próximo re-enable."""
    from plotspace.core.control_mode import armar_seed
    seed = armar_seed('x\n', alt_on=False, cursor_x=0, cursor_y=0, rows=24,
                      modos={'mouse_any': True, 'mouse_boton': True,
                             'cursor_keys_app': True})
    assert '\x1b[?1002h' in seed          # button-event tracking
    assert '\x1b[?1006h' in seed          # SGR encoding (siempre con mouse)
    assert '\x1b[?1h' in seed             # DECCKM (application cursor keys)
    # Sin modos: no se inyecta nada.
    seed = armar_seed('x\n', alt_on=False, cursor_x=0, cursor_y=0, rows=24)
    assert '\x1b[?1002h' not in seed and '\x1b[?1h' not in seed
    # mouse "all motion" pisa a button.
    seed = armar_seed('x\n', alt_on=False, cursor_x=0, cursor_y=0, rows=24,
                      modos={'mouse_any': True, 'mouse_todo': True})
    assert '\x1b[?1003h' in seed and '\x1b[?1002h' not in seed


def test_armar_seed_alt_limpia_antes_de_pintar():
    """El seed alt debe ARRANCAR de pantalla limpia (\\x1b[H\\x1b[2J tras el
    1049h): en el attach cae en un xterm virgen (no-op), pero el RE-SEED del
    watchdog post-resize (S1: la app idle no repinta tras el SIGWINCH) cae
    sobre una pantalla alt VIVA con el cursor donde lo dejó la app — sin el
    clear+home, el cuerpo pintaría desde ahí y quedaría basura wrappeada."""
    from plotspace.core.control_mode import armar_seed
    seed = armar_seed('TUI\n', alt_on=True, cursor_x=0, cursor_y=0, rows=24)
    assert seed.startswith('\x1b[?1049h\x1b[H\x1b[2J')
    # El camino normal (shell) NO limpia: el seed re-reproduce scrollback y un
    # clear ahí rompería el historial del attach.
    seed = armar_seed('x\n', alt_on=False, cursor_x=0, cursor_y=0, rows=24)
    assert '\x1b[2J' not in seed


def test_debe_resembrar():
    """Decisión del watchdog post-resize (fix S1 'negro al salir de fullscreen',
    2026-07-08): tras un resize que CAMBIÓ las medidas del pane, en el motor
    control NADIE repinta salvo la app (refresh-client -C solo hace el SIGWINCH;
    tmux no re-emite pantalla en control mode) — y claude fullscreen IDLE no
    redibuja en SIGWINCH (render state-gated, issue upstream #43273). Si el pane
    quedó MUDO, el backend re-siembra con la verdad de tmux. SOLO en alt-screen:
    el buffer normal lo refluye xterm solo, y un seed sin reset duplicaría el
    scrollback de un shell."""
    from plotspace.core.control_mode import debe_resembrar
    assert debe_resembrar(salida_desde_resize=0, alt_on=True) is True
    # Llegó output → la app repintó sola: no tocar.
    assert debe_resembrar(salida_desde_resize=3, alt_on=True) is False
    # Buffer normal (shell): jamás re-sembrar sin reset del cliente.
    assert debe_resembrar(salida_desde_resize=0, alt_on=False) is False
    assert debe_resembrar(salida_desde_resize=1, alt_on=False) is False


def test_seed_degradado():
    """¿La captura de estado del seed vino incompleta? (timeout de tmux bajo
    carga, carrera durante el arranque de la app). Un seed degradado cae a los
    defaults (alt_on=False, modos=None): xterm pierde el alt-screen y el
    mouse-tracking de la app → SCROLL MUERTO en esa terminal hasta el próximo
    redraw (el bug 'una terminal random no scrollea y revive al mandarle un
    mensaje', 2026-07-11). Detectarlo permite REINTENTAR la captura antes de
    sembrar degradado. info = los 8 campos del display-message del seed:
    [alternate_on, cursor_x, cursor_y, pane_height, mouse_any, mouse_button,
    mouse_all, keypad_cursor]."""
    from plotspace.core.control_mode import seed_degradado
    # Captura completa y sana → NO degradado (con y sin alt/mouse).
    assert seed_degradado(['0', '5', '2', '24', '0', '0', '0', '0']) is False
    assert seed_degradado(['1', '0', '0', '40', '1', '1', '0', '1']) is False
    # Timeout / fallo total → degradado.
    assert seed_degradado([]) is True
    assert seed_degradado(None) is True
    # Captura parcial (menos de 8 campos: el display-message se cortó) → degradado.
    assert seed_degradado(['1', '0', '0', '24']) is True
    # Flags que no son 0/1 (basura en el stream) → degradado.
    assert seed_degradado(['x', '0', '0', '24', '0', '0', '0', '0']) is True
    assert seed_degradado(['1', '0', '0', '24', '?', '0', '0', '0']) is True
    # Coordenadas/altura no numéricas → degradado.
    assert seed_degradado(['1', 'a', '0', '24', '0', '0', '0', '0']) is True
    assert seed_degradado(['1', '0', '0', '', '0', '0', '0', '0']) is True


def test_secuencia_seed_modos_afirma_estado_exacto():
    """La secuencia de modos privados que usan el seed Y la sincronización viva:
    deja a xterm EXACTO al estado de la app — prende lo que va y APAGA lo que
    sobra. La asimetría vieja (solo prendía) no podía corregir un xterm pegado
    en el DECCKM/mouse de una pantalla anterior."""
    from plotspace.core.control_mode import secuencia_seed_modos
    # Estado desconocido (captura degradada) → no tocar nada.
    assert secuencia_seed_modos(None) == ''
    # Flechas en modo aplicación (DECCKM) ON.
    s = secuencia_seed_modos({'cursor_keys_app': True})
    assert '\x1b[?1h' in s and '\x1b[?1l' not in s
    # Flechas OFF → se APAGA explícitamente (el fix de la asimetría).
    s = secuencia_seed_modos({'cursor_keys_app': False})
    assert '\x1b[?1l' in s and '\x1b[?1h' not in s
    # Mouse OFF → se limpian los cuatro tracking (1000/1002/1003/1006).
    s = secuencia_seed_modos({'mouse_any': False})
    for m in ('\x1b[?1000l', '\x1b[?1002l', '\x1b[?1003l', '\x1b[?1006l'):
        assert m in s
    # Mouse button ON → button-event + SGR, y NINGÚN clear de mouse.
    s = secuencia_seed_modos({'mouse_any': True, 'mouse_boton': True})
    assert '\x1b[?1002h' in s and '\x1b[?1006h' in s and '\x1b[?1000l' not in s


def test_armar_seed_apaga_los_modos_que_sobran():
    """REGRESIÓN del bug 'no me puedo mover con las flechas en el menú del
    agente' (2026-07-23): con un estado de modos CONOCIDO, el seed lo afirma
    EXACTO (prende y apaga). Antes solo re-enunciaba los prendidos, así que un
    xterm pegado en el DECCKM/mouse de una pantalla anterior quedaba
    desincronizado del pane → las flechas salían como CSI y el CLI las ignoraba.
    modos=None (captura degradada) sigue sin inyectar nada."""
    from plotspace.core.control_mode import armar_seed
    seed = armar_seed('x\n', alt_on=False, cursor_x=0, cursor_y=0, rows=24,
                      modos={'mouse_any': False, 'cursor_keys_app': False})
    assert '\x1b[?1l' in seed          # flechas normales, afirmado
    assert '\x1b[?1000l' in seed       # mouse apagado, afirmado
    seed = armar_seed('x\n', alt_on=False, cursor_x=0, cursor_y=0, rows=24)
    assert '\x1b[?1l' not in seed and '\x1b[?1h' not in seed


def test_parsear_modos_degradado_es_none():
    """info = los 8 campos del display-message. Sano → dict de modos; incompleto
    o con basura → None (no se puede confiar)."""
    from plotspace.core.control_mode import parsear_modos
    assert parsear_modos(['0', '5', '2', '24', '1', '0', '0', '1']) == {
        'mouse_any': True, 'mouse_boton': False, 'mouse_todo': False,
        'cursor_keys_app': True}
    assert parsear_modos([]) is None
    assert parsear_modos(['1', '0', '0', '24']) is None            # parcial
    assert parsear_modos(['x', '0', '0', '24', '0', '0', '0', '0']) is None


def test_resolver_modos_reusa_el_ultimo_bueno_si_degradado():
    """Un hipo de tmux (captura degradada) NO debe tirar los modos de un menú
    vivo: se REUSA el último bueno en vez de caer a None (que apagaba todo)."""
    from plotspace.core.control_mode import resolver_modos
    bueno = {'mouse_any': False, 'mouse_boton': False, 'mouse_todo': False,
             'cursor_keys_app': True}
    assert resolver_modos(['0', '0', '0', '24', '0', '0', '0', '1'], None) == bueno
    assert resolver_modos([], bueno) == bueno       # degradada + previo → el previo
    assert resolver_modos([], None) is None         # degradada sin previo → None


def test_sincronizar_modos_empuja_solo_al_cambiar():
    """Decisión pura del poller de sync vivo: (secuencia_o_None, nuevo_previo).
    Reproduce el bug: el menú prende DECCKM DESPUÉS del seed → el poller detecta
    el cambio de flag y empuja \\x1b[?1h a xterm; sin cambios no manda nada; una
    captura degradada no toca el previo."""
    from plotspace.core.control_mode import sincronizar_modos
    normal = ['0', '0', '0', '24', '0', '0', '0', '0']
    menu   = ['0', '0', '0', '24', '0', '0', '0', '1']   # keypad_cursor=1 (DECCKM)
    off    = {'mouse_any': False, 'mouse_boton': False, 'mouse_todo': False,
              'cursor_keys_app': False}
    # Primer sync desde None: hay estado conocido → empuja y fija el previo.
    seq, prev = sincronizar_modos(None, normal)
    assert seq is not None and prev == off
    # Mismo estado → nada que hacer.
    seq2, prev2 = sincronizar_modos(prev, normal)
    assert seq2 is None and prev2 == prev
    # Abre el menú (DECCKM ON) → empuja \x1b[?1h.
    seq3, prev3 = sincronizar_modos(prev, menu)
    assert seq3 is not None and '\x1b[?1h' in seq3 and prev3['cursor_keys_app'] is True
    # Captura degradada → no manda nada ni toca el previo.
    seq4, prev4 = sincronizar_modos(prev3, [])
    assert seq4 is None and prev4 == prev3


def test_cliente_control_integracion_tmux_real():
    """Integración contra tmux REAL (patrón asyncio.run de test_tmux_window_size):
    attach -C a una sesión scratch, input por send-keys -H, output crudo de
    vuelta, resize con SIGWINCH y detach limpio SIN matar la sesión."""
    import asyncio
    import shutil
    import subprocess as sp
    if not shutil.which('tmux'):
        import pytest
        pytest.skip('sin tmux en el entorno')

    from plotspace.core.control_mode import ClienteControl

    SES = 'pytest_cm'
    sp.run(['tmux', 'kill-session', '-t', SES], capture_output=True)
    r = sp.run(['tmux', 'new-session', '-d', '-s', SES, '-x', '80', '-y', '24'],
               capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    async def escenario():
        cliente = ClienteControl(SES)
        recibido = bytearray()
        vivo = asyncio.Event()
        cliente.iniciar(on_output=lambda b: (recibido.extend(b), vivo.set()),
                        on_exit=lambda: None)
        # input crudo end-to-end (echo del shell + salida del comando)
        await cliente.enviar_bytes(b'echo hola-control-$((2+2))\r')
        for _ in range(200):   # presupuesto 20s: bash -i tarda >3s en arrancar bajo carga (medido load 7 en 4 cores); corta al toque cuando llega
            if b'hola-control-4' in bytes(recibido):
                break
            await asyncio.sleep(0.1)
        assert b'hola-control-4' in bytes(recibido), bytes(recibido)[-200:]

        # UTF-8 multibyte end-to-end POR EL PIPE REAL (la regresión de los '?'):
        # tildes, cajas y emoji deben volver como SUS bytes, no como b'?'.
        marca = 'utf8-está─✅'
        await cliente.enviar_bytes(f'echo {marca}\r'.encode())
        for _ in range(200):   # presupuesto 20s: bash -i tarda >3s en arrancar bajo carga (medido load 7 en 4 cores); corta al toque cuando llega
            if recibido.count(marca.encode()) >= 1:
                break
            await asyncio.sleep(0.1)
        assert marca.encode() in bytes(recibido), bytes(recibido)[-300:]
        assert b'utf8-est?' not in bytes(recibido)

        # resize: la app ve el SIGWINCH (tput cols imprime el nuevo ancho)
        cliente.resize(66, 22)
        await asyncio.sleep(0.5)
        await cliente.enviar_bytes(b'tput cols\r')
        for _ in range(200):   # presupuesto 20s: bash -i tarda >3s en arrancar bajo carga (medido load 7 en 4 cores); corta al toque cuando llega
            if b'\r\n66' in bytes(recibido) or b'\n66' in bytes(recibido):
                break
            await asyncio.sleep(0.1)
        w = sp.run(['tmux', 'display', '-p', '-t', SES, '#{window_width}'],
                   capture_output=True, text=True).stdout.strip()
        assert w == '66', f'window_width={w}'

        await cliente.cerrar()
        # la sesión sigue viva tras el detach (el agente no muere con la vista)
        vive = sp.run(['tmux', 'has-session', '-t', SES], capture_output=True)
        assert vive.returncode == 0

    try:
        asyncio.run(escenario())
    finally:
        sp.run(['tmux', 'kill-session', '-t', SES], capture_output=True)


# ─── comando_con_respuesta: FIFO + bloque espurio del attach ──────────────────

def _cliente_sin_proc():
    """ClienteControl 'seco' para tests puros: sin proceso tmux; el stdin se
    anula y los eventos se inyectan directo por _despachar."""
    from plotspace.core.control_mode import ClienteControl
    cliente = ClienteControl('pytest_seco')
    cliente._escribir_stdin = lambda payload: None
    return cliente


def test_respuestas_fifo_y_bloque_espurio():
    """El attach -C emite UN %begin/%end propio antes de cualquier comando
    (verificado tmux 3.6): ese bloque se descarta y NO se atribuye al primer
    comando_con_respuesta. Las respuestas siguientes se resuelven FIFO."""
    import asyncio

    async def escenario():
        cliente = _cliente_sin_proc()
        cliente._loop = asyncio.get_running_loop()
        cliente._bloques_espurios = 1

        # El bloque espurio llega ANTES de que exista espera: se descarta.
        cliente._despachar(('respuesta', ['espurio-del-attach']))

        t1 = asyncio.create_task(cliente.comando_con_respuesta('display -p uno'))
        t2 = asyncio.create_task(cliente.comando_con_respuesta('display -p dos'))
        await asyncio.sleep(0)          # deja que ambos encolen su espera
        cliente._despachar(('respuesta', ['uno']))
        cliente._despachar(('respuesta_error', ['no such session: x']))
        ok1, cuerpo1 = await t1
        ok2, cuerpo2 = await t2
        assert ok1 is True and cuerpo1 == ['uno']
        assert ok2 is False and 'no such session' in cuerpo2[0]

    asyncio.run(escenario())


def test_respuesta_espuria_llegando_con_espera_activa():
    """Si el bloque espurio todavía no se consumió cuando ya hay un comando en
    vuelo, igual se descarta (contador, no heurística): el comando recibe SU
    bloque, no el del attach."""
    import asyncio

    async def escenario():
        cliente = _cliente_sin_proc()
        cliente._loop = asyncio.get_running_loop()
        cliente._bloques_espurios = 1
        t = asyncio.create_task(cliente.comando_con_respuesta('display -p real'))
        await asyncio.sleep(0)
        cliente._despachar(('respuesta', ['bloque-del-attach']))   # espurio
        cliente._despachar(('respuesta', ['real']))
        ok, cuerpo = await t
        assert ok is True and cuerpo == ['real']

    asyncio.run(escenario())


def test_terminar_cancela_esperas_pendientes():
    """Si el cliente muere (%exit/EOF) con comandos en vuelo, las esperas no
    quedan colgadas hasta el timeout: se cancelan al toque."""
    import asyncio

    async def escenario():
        cliente = _cliente_sin_proc()
        cliente._loop = asyncio.get_running_loop()
        t = asyncio.create_task(cliente.comando_con_respuesta('display -p x'))
        await asyncio.sleep(0)
        cliente._terminar()
        try:
            await t
            assert False, 'debía cancelarse/fallar'
        except (asyncio.CancelledError, RuntimeError):
            pass

    asyncio.run(escenario())


def test_comando_con_respuesta_sobre_cliente_cerrado():
    import asyncio
    from plotspace.core import control_mode as cm

    async def escenario():
        cliente = _cliente_sin_proc()
        cliente._loop = asyncio.get_running_loop()
        cliente._cerrado = True
        try:
            await cliente.comando_con_respuesta('display -p x')
            assert False, 'debía fallar sobre cliente cerrado'
        except RuntimeError:
            pass

    asyncio.run(escenario())


# ─── Tipeo: el bloque %begin/%end de CADA comando se atribuye a SU dueño ──────

def test_targets_exactos_no_resuelven_por_prefijo():
    """Sin '=' tmux resuelve `jarvis_1` por PREFIJO si esa sesión murió y existe
    `jarvis_12`: el tipeo de la 1 caería en la 12. El cliente usa SIEMPRE el
    target exacto (`=nombre` para attach, `=nombre:` para un pane)."""
    from plotspace.core.control_mode import ClienteControl
    c = ClienteControl('jarvis_1')
    assert c.target_sesion == '=jarvis_1'
    assert c.pane == '=jarvis_1:'


def test_enviar_bytes_apunta_al_pane_exacto():
    import asyncio

    async def escenario():
        c = _cliente_sin_proc()
        c._loop = asyncio.get_running_loop()
        escritos = []
        c._escribir_stdin = escritos.append
        await c.enviar_bytes(b'hola')
        assert len(escritos) == 1
        linea = escritos[0].decode()
        assert linea.startswith('send-keys -t =pytest_seco: -H 68 6f 6c 61')

    asyncio.run(escenario())


def test_fire_and_forget_consume_su_bloque_y_no_desfasa_las_respuestas():
    """CADA comando que escribimos (también send-keys y refresh-client, que no
    devuelven nada) produce un bloque %begin/%end. Si el tipeo recién escrito
    no consume SU bloque, el siguiente comando_con_respuesta recibe la
    respuesta del tipeo (vacía) en vez de la suya: el seed quedaba degradado."""
    import asyncio

    async def escenario():
        c = _cliente_sin_proc()
        c._loop = asyncio.get_running_loop()
        c._bloques_espurios = 0
        c.comando('refresh-client -C 80x24')           # fire-and-forget
        await c.enviar_bytes(b'ab')                    # un send-keys
        t = asyncio.create_task(c.comando_con_respuesta('display-message -p x'))
        await asyncio.sleep(0)
        # tmux responde EN ORDEN de escritura: resize, send-keys, display
        c._despachar(('respuesta', []))
        c._despachar(('respuesta', []))
        assert not t.done()
        c._despachar(('respuesta', ['real']))
        ok, cuerpo = await t
        assert ok is True and cuerpo == ['real']
        assert not c._esperas

    asyncio.run(escenario())


def test_bloque_tardio_de_un_timeout_no_se_atribuye_al_siguiente_comando():
    """Un comando_con_respuesta que vence su timeout deja su Future cancelado;
    su bloque llega DESPUÉS y debe consumirse (descartarse), no pasar al
    comando siguiente — saltear el Future sin consumir corría la cuenta para
    siempre."""
    import asyncio

    async def escenario():
        c = _cliente_sin_proc()
        c._loop = asyncio.get_running_loop()
        c._bloques_espurios = 0
        try:
            await c.comando_con_respuesta('display -p uno', timeout=0.01)
            assert False, 'debía vencer'
        except (asyncio.TimeoutError, TimeoutError):
            pass
        t2 = asyncio.create_task(c.comando_con_respuesta('display -p dos'))
        await asyncio.sleep(0)
        c._despachar(('respuesta', ['uno-tardio']))     # el bloque del que venció
        c._despachar(('respuesta', ['dos']))
        ok, cuerpo = await t2
        assert cuerpo == ['dos']

    asyncio.run(escenario())


def test_espera_real_ignora_el_tipeo():
    """El poller de modos cede el paso a un seed/probe REAL en vuelo, no a cada
    tecla: los fire-and-forget no cuentan como espera."""
    import asyncio

    async def escenario():
        c = _cliente_sin_proc()
        c._loop = asyncio.get_running_loop()
        await c.enviar_bytes(b'x')
        c.comando('refresh-client -C 80x24')
        assert c._esperas and not c.espera_real()
        t = asyncio.create_task(c.comando_con_respuesta('display -p y'))
        await asyncio.sleep(0)
        assert c.espera_real()
        c._terminar()
        try:
            await t
        except (asyncio.CancelledError, RuntimeError):
            pass

    asyncio.run(escenario())


def test_escritura_grande_hace_cola_y_no_se_intercala_con_otros_emisores():
    """Un paste grande sale por un thread. Mientras ese write está en vuelo, el
    poller de modos / un resize / una tecla escribían POR ENCIMA y su línea se
    colaba a mitad de un `send-keys -H …` (tmux rechaza la línea partida y el
    paste perdía un tramo). Ahora todo lo que llega hace cola y sale después,
    en orden y entero."""
    import asyncio
    import threading

    async def escenario():
        c = _cliente_sin_proc()
        c._loop = asyncio.get_running_loop()
        escritos = []
        suelta = threading.Event()

        def stub(payload):
            if len(payload) > 16384:
                suelta.wait(5)          # tmux lento consumiendo el paste
            escritos.append(payload)

        c._escribir_stdin = stub
        grande = asyncio.create_task(c.enviar_bytes(b'a' * 20000))
        await asyncio.sleep(0.05)       # el thread quedó bloqueado en el write grande
        c.comando('display-message -p intruso')     # el poller / un resize
        tecla = asyncio.create_task(c.enviar_bytes(b'z'))
        await asyncio.sleep(0.05)
        assert escritos == [], 'nada puede colarse mientras el write grande está en vuelo'
        suelta.set()
        await asyncio.wait_for(asyncio.gather(grande, tecla), 5)
        assert len(escritos) == 3
        assert len(escritos[0]) > 16384 and escritos[0].startswith(b'send-keys')
        assert escritos[1] == b'display-message -p intruso\n'
        assert escritos[2].startswith(b'send-keys') and b'7a' in escritos[2]
        # cada línea del paste salió ENTERA (termina en \n, ninguna cortada)
        assert escritos[0].endswith(b'\n')

    asyncio.run(escenario())


# ─── Registro de dueños (un cliente de control DUEÑO por terminal) ────────────

def test_registro_duenos_swap_y_liberar():
    """registrar_dueno devuelve al anterior (para desplazarlo con 4010) y
    liberar_dueno solo borra si el registrado sigue siendo ESTE cliente (una
    conexión vieja que muere tarde no des-registra al dueño nuevo)."""
    from plotspace.core import control_mode as cm

    a, b = _cliente_sin_proc(), _cliente_sin_proc()
    cm._duenos.clear()
    try:
        assert cm.registrar_dueno(7, a, lambda: None) is None
        anterior = cm.registrar_dueno(7, b, lambda: None)
        assert anterior is not None and anterior['cliente'] is a
        # la conexión vieja (a) muere después: NO debe des-registrar a b
        cm.liberar_dueno(7, a)
        assert cm._duenos[7]['cliente'] is b
        cm.liberar_dueno(7, b)
        assert 7 not in cm._duenos
    finally:
        cm._duenos.clear()


def test_cerrar_clientes_para_reexec_vacia_el_registro():
    """Pre-execv: cierra todos los dueños vivos sin explotar (clientes sin
    proceso real) y deja el registro vacío."""
    from plotspace.core import control_mode as cm

    cm._duenos.clear()
    a, b = _cliente_sin_proc(), _cliente_sin_proc()
    cm.registrar_dueno(1, a, lambda: None)
    cm.registrar_dueno(2, b, lambda: None)
    cm.cerrar_clientes_para_reexec()
    assert cm._duenos == {}
    assert a._cerrado and b._cerrado


def test_comando_con_respuesta_integracion_tmux_real():
    """Contra tmux REAL: el bloque espurio del attach no contamina, display y
    capture-pane -e responden por el MISMO stream (cuerpo con ESC y UTF-8
    CRUDOS — verificado: tmux no octal-escapa dentro de %begin/%end)."""
    import asyncio
    import shutil
    import subprocess as sp
    if not shutil.which('tmux'):
        import pytest
        pytest.skip('sin tmux en el entorno')

    from plotspace.core.control_mode import ClienteControl

    SES = 'pytest_cm_resp'
    sp.run(['tmux', 'kill-session', '-t', SES], capture_output=True)
    r = sp.run(['tmux', 'new-session', '-d', '-s', SES, '-x', '80', '-y', '24',
                "printf '\\033[31mROJO\\033[0m fin✅\\n'; sleep 120"],
               capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    async def escenario():
        # el pane ya pintó (comando directo, sin shell interactivo lento)
        for _ in range(100):
            cap = sp.run(['tmux', 'capture-pane', '-p', '-t', SES],
                         capture_output=True, text=True).stdout
            if 'ROJO' in cap:
                break
            await asyncio.sleep(0.1)

        cliente = ClienteControl(SES)
        cliente.iniciar(on_output=lambda b: None, on_exit=lambda: None)
        ok, cuerpo = await cliente.comando_con_respuesta(
            f"display-message -p -t {SES} '#{{pane_width}}x#{{pane_height}}'")
        assert ok is True and cuerpo and cuerpo[0].strip() == '80x24', cuerpo
        ok2, cuerpo2 = await cliente.comando_con_respuesta(
            f'capture-pane -p -e -t {SES} -S -50')
        texto = '\n'.join(cuerpo2)
        assert ok2 is True and '\x1b[31mROJO' in texto and '✅' in texto, texto[:200]
        # error real del server → %error → ok=False (un target inválido en
        # display-message NO alcanza: tmux 3.6 igual responde %end)
        ok3, cuerpo3 = await cliente.comando_con_respuesta('comando-inexistente-xyz')
        assert ok3 is False
        await cliente.cerrar()

    try:
        asyncio.run(escenario())
    finally:
        sp.run(['tmux', 'kill-session', '-t', SES], capture_output=True)
