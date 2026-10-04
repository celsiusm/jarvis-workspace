"""tmux CONTROL MODE — parser + cliente (motor de terminales de UN emulador).

Arquitectura (spike validado 2026-07-02, tmux 3.6): `tmux -C attach` NO emula
cliente — no manda alt-screen propio, no re-wrapea, no tiene copy-mode: entrega
los bytes CRUDOS que escribe la app del pane en eventos:

    %output %<pane-id> <payload>\\n

con los no-imprimibles escapados en octal (``\\NNN``, un byte por escape) y el
backslash como ``\\\\``. Las respuestas a comandos van en bloques
``%begin ... %end`` (o ``%error``). Con esto el browser (xterm.js) pasa a ser el
ÚNICO emulador (scroll local nativo, alt-screen de la app pasa intacto —
verificado: less emite ``\\033[?1049h`` y viaja crudo) y tmux queda solo como
guardián de procesos + caño de bytes.

Este módulo es la parte PURA (testeable sin tmux): un parser INCREMENTAL que
acepta chunks cortados en cualquier punto y emite eventos completos. El payload
se decodifica a BYTES — la conversión a str es del consumidor con un decoder
UTF-8 incremental (un multibyte partido entre eventos no debe romper; misma
lección que la sexta capa de [[tmux-size-clamping]]).
"""
import asyncio
import collections
import os
import re
import subprocess

_ESCAPE_RE = re.compile(rb'\\(\d{3}|\\)')


def decodificar_payload(payload: bytes) -> bytes:
    """Payload de %output (BYTES del pipe) → BYTES crudos de la app.

    REGRESIÓN CAZADA 2026-07-02 ("todo carácter no-ASCII sale como '?'"):
    tmux 3.6 pasa los bytes UTF-8 >127 CRUDOS en %output — NO los escapa.
    Solo escapa los bytes de CONTROL como ``\\NNN`` octal (\\033, \\015, ...)
    y el backslash literal como ``\\\\``. Por eso este pipeline es BYTES de
    punta a punta: una pasada intermedia por ASCII convertía cada byte alto
    en '?' (é→??, ─→???, ✅→???). Se soportan igual los escapes octales de
    bytes altos por si otro tmux los emitiera.
    """
    def _sub(m):
        g = m.group(1)
        if g == b'\\':
            return b'\\'
        return bytes([int(g, 8) & 0xFF])

    return _ESCAPE_RE.sub(_sub, payload)


class ParserControlMode:
    """Parser incremental del stream de control mode.

    ``alimentar(chunk)`` acepta texto cortado en CUALQUIER punto (incluso a
    mitad de un escape octal: el corte cae entre líneas completas porque el
    protocolo es por líneas — se bufferea hasta el \\n). Emite tuplas:

      ('output', '%<pane>', bytes)   — bytes crudos de la app
      ('respuesta', [líneas])        — cuerpo de un bloque %begin/%end
      ('respuesta_error', [líneas])  — cuerpo de un bloque %begin/%error
      ('exit', 'razón')              — el cliente de control terminó
      ('notif', línea)               — cualquier otra notificación %...
    """

    def __init__(self):
        self._resto = b''         # línea parcial pendiente (sin \n todavía)
        self._en_bloque = False   # dentro de %begin ... %end/%error
        self._bloque = []         # cuerpo acumulado del bloque

    def alimentar(self, chunk: bytes):
        """Chunk CRUDO del pipe (bytes — el stream lleva UTF-8 sin escapar)."""
        eventos = []
        self._resto += chunk
        while True:
            idx = self._resto.find(b'\n')
            if idx < 0:
                break
            linea = self._resto[:idx]
            self._resto = self._resto[idx + 1:]
            ev = self._procesar_linea(linea)
            if ev is not None:
                eventos.append(ev)
        return eventos

    def _procesar_linea(self, linea: bytes):
        # Dentro de un bloque de respuesta, TODO es cuerpo hasta %end/%error —
        # incluso líneas que parecen eventos (un capture-pane puede contener
        # texto '%output ...' del propio pane).
        if self._en_bloque:
            if linea.startswith(b'%end ') or linea == b'%end':
                self._en_bloque = False
                cuerpo, self._bloque = self._bloque, []
                return ('respuesta', cuerpo)
            if linea.startswith(b'%error ') or linea == b'%error':
                self._en_bloque = False
                cuerpo, self._bloque = self._bloque, []
                return ('respuesta_error', cuerpo)
            self._bloque.append(linea.decode('utf-8', errors='replace'))
            return None

        if linea.startswith(b'%output '):
            # '%output %<pane> <payload>' — el payload puede contener espacios
            # y bytes UTF-8 crudos: queda en BYTES hasta el decoder incremental.
            partes = linea.split(b' ', 2)
            pane = partes[1].decode('ascii', errors='replace') if len(partes) > 1 else ''
            payload = partes[2] if len(partes) > 2 else b''
            return ('output', pane, decodificar_payload(payload))

        if linea.startswith(b'%begin'):
            self._en_bloque = True
            self._bloque = []
            return None

        if linea == b'%exit' or linea.startswith(b'%exit '):
            razon = linea[6:].decode('utf-8', errors='replace') if len(linea) > 6 else ''
            return ('exit', razon)

        if linea.startswith(b'%'):
            return ('notif', linea.decode('utf-8', errors='replace'))

        # Línea fuera de protocolo (no debería pasar): ignorar sin romper.
        return None


# ─── Helpers PUROS del cliente (testeables sin tmux) ──────────────────────────

# Bytes por comando send-keys -H: la línea de comando de tmux es finita; 256
# bytes → ~1KB de línea. Un paste grande sale como N comandos consecutivos
# (FIFO por el mismo stdin → el orden queda garantizado).
SEND_KEYS_CHUNK = 256

# Por encima de este tamaño un payload al stdin de tmux sale por un thread (el
# pipe tiene 64KB: un paste grande puede bloquear el write). Ver
# ClienteControl._escribir.
LIMITE_ESCRITURA_INLINE = 16384


def comandos_send_keys(target: str, data: bytes, chunk: int = SEND_KEYS_CHUNK):
    """Input crudo → lista de comandos `send-keys -H` (hex, sin ambigüedad de
    quoting: CUALQUIER byte viaja igual — Enter, ESC, UTF-8, bracketed paste).
    `target` = el pane destino; pasale el EXACTO (`=jarvis_1:`), no el nombre
    pelado (que resuelve por prefijo si la sesión ya no existe)."""
    cmds = []
    for i in range(0, len(data), chunk):
        hx = ' '.join(f'{b:02x}' for b in data[i:i + chunk])
        cmds.append(f'send-keys -t {target} -H {hx}')
    return cmds


def armar_seed(captura: str, alt_on: bool, cursor_x: int, cursor_y: int,
               rows: int, modos: dict = None) -> str:
    """Replay inicial para xterm al attachear (el 'F5 restaura todo').

    captura: salida de `capture-pane -p -e` (colores incluidos, \\n como fin de
    línea). Se convierte a \\r\\n, se antepone el switch a pantalla alternativa
    si la app la está usando, y se reposiciona el cursor REAL de la app con un
    CUP relativo a la pantalla visible (la cola de la captura es la pantalla:
    sus últimas `rows` líneas). Sin el CUP, el cursor quedaría al final del
    texto y el eco del tipeo aparecería corrido hasta el próximo redraw."""
    texto = captura
    if texto.endswith('\n'):
        texto = texto[:-1]
    cuerpo = texto.replace('\n', '\r\n')
    # Alt: clear+home tras el 1049h. En el attach (xterm virgen) es no-op, pero
    # el RE-SEED del watchdog post-resize (S1 2026-07-08) cae sobre una pantalla
    # alt VIVA con el cursor donde lo dejó la app — sin esto, el cuerpo pintaría
    # desde ahí y quedaría basura wrappeada. El camino normal (shell) NO limpia:
    # su seed re-reproduce scrollback y un clear rompería el historial.
    prefijo = '\x1b[?1049h\x1b[H\x1b[2J' if alt_on else ''
    # CUP es 1-based y relativo al viewport; cursor_x/y de tmux son 0-based
    # relativos a la pantalla visible del pane.
    fila = max(0, min(int(cursor_y), rows - 1)) + 1
    col = max(0, int(cursor_x)) + 1
    # Modos privados de la app (mouse-tracking, DECCKM de flechas): tmux los
    # ABSORBE en flags de pane y NO los reenvía por el stream, así que el seed
    # los re-enuncia EXACTOS (prende lo que va, apaga lo que sobra). La misma
    # secuencia la usa la sincronización viva. Ver secuencia_seed_modos.
    sufijo = secuencia_seed_modos(modos)
    return f'{prefijo}{cuerpo}\x1b[{fila};{col}H{sufijo}'


def secuencia_seed_modos(modos) -> str:
    """Secuencias de modo privado que dejan a xterm EXACTO al estado `modos` de
    la app: prende lo que va y APAGA lo que sobra. PURA — la comparten el seed
    (armar_seed) y la SINCRONIZACIÓN VIVA (sincronizar_modos).

    `modos` = dict {mouse_any, mouse_boton, mouse_todo, cursor_keys_app} o None.
    None = estado DESCONOCIDO (captura degradada) → '' (no tocar: apagar a ciegas
    rompería una TUI viva). Un dict SÍ es autoritativo: la asimetría vieja (solo
    prender) dejaba un xterm pegado en el DECCKM/mouse de una pantalla anterior
    sin forma de corregirse — origen del bug 'no me puedo mover con las flechas
    en el menú del agente' (2026-07-23, [[modos-privados-sync-vivo]])."""
    if modos is None:
        return ''
    s = ''
    if modos.get('mouse_any'):
        if modos.get('mouse_todo'):
            s += '\x1b[?1003h'           # any-motion tracking
        elif modos.get('mouse_boton'):
            s += '\x1b[?1002h'           # button-event tracking
        else:
            s += '\x1b[?1000h'           # click tracking básico
        s += '\x1b[?1006h'               # SGR (coords sanas en panes anchos)
    else:
        # Mouse APAGADO: limpiar los cuatro tracking por si xterm quedó con uno
        # prendido de una pantalla anterior (si ya estaban off, es no-op).
        s += '\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l'
    # DECCKM: afirmar el estado exacto de las flechas (aplicación vs normal).
    s += '\x1b[?1h' if modos.get('cursor_keys_app') else '\x1b[?1l'
    return s


def parsear_modos(info):
    """info = los 8 campos del display-message del seed ([alternate_on, cursor_x,
    cursor_y, pane_height, mouse_any, mouse_button, mouse_all, keypad_cursor]) →
    dict de modos, o None si la captura vino degradada (no confiable). PURA."""
    if seed_degradado(info):
        return None
    try:
        return {'mouse_any': info[4] == '1', 'mouse_boton': info[5] == '1',
                'mouse_todo': info[6] == '1', 'cursor_keys_app': info[7] == '1'}
    except (IndexError, ValueError):
        return None


def resolver_modos(info, previos=None):
    """Modos a sembrar: los recién capturados si son fiables; si la captura vino
    DEGRADADA (timeout de tmux bajo carga, carrera con el arranque de la app), se
    REUSA el último bueno — un hipo no debe tirar el DECCKM/mouse de un menú vivo
    y dejar las flechas/el clic muertos. None solo si nunca hubo un bueno. PURA."""
    m = parsear_modos(info)
    return m if m is not None else previos


def sincronizar_modos(previos, info):
    """Decisión PURA del poller de SINCRONIZACIÓN VIVA de modos. Dado el último
    estado empujado a xterm (`previos`) y una captura fresca de flags (`info`),
    devuelve (secuencia_a_enviar | None, nuevo_previos).

    Los modos privados que la app prende/apaga DESPUÉS del attach (p.ej. un menú
    que entra en DECCKM) NO viajan por %output (tmux los absorbe): sin re-enunciar
    el cambio, xterm manda las flechas como CSI \\x1b[B y el CLI las ignora
    (espera SS3 \\x1bOB) — flechas muertas, mientras 'n' (byte literal) anda. El
    poller re-sincroniza SOLO al detectar un cambio de flag; captura degradada o
    sin cambios → (None, previos)."""
    nuevos = parsear_modos(info)
    if nuevos is None or nuevos == previos:
        return None, previos
    return secuencia_seed_modos(nuevos), nuevos


def seed_degradado(info) -> bool:
    """¿La captura de estado del seed vino incompleta? PURA. info = los 8 campos
    del display-message del attach ([alternate_on, cursor_x, cursor_y,
    pane_height, mouse_any, mouse_button, mouse_all, keypad_cursor]); vacía o
    parcial cuando tmux no respondió (timeout bajo carga) o la carrera cortó el
    bloque. Un seed degradado cae a los defaults (alt_on=False, modos=None):
    xterm pierde el alt-screen y el mouse-tracking de la app → SCROLL MUERTO en
    esa terminal hasta el próximo redraw ('una terminal random no scrollea y
    revive al mandarle un mensaje', 2026-07-11). El caller reintenta la captura
    antes de sembrar degradado (nunca queda mudo: al final siembra lo que haya)."""
    if not info or len(info) < 8:
        return True
    flags_ok = all(str(info[i]).strip() in ('0', '1') for i in (0, 4, 5, 6, 7))
    numeros_ok = all(str(info[i]).strip().lstrip('-').isdigit() for i in (1, 2, 3))
    return not (flags_ok and numeros_ok)


_SGR_RE = re.compile(r'\x1b\[[0-9;:?]*[a-zA-Z]')


def masa_capture(captura: str) -> int:
    """Líneas con CONTENIDO real de un `capture-pane -p -e`. PURA.

    El `-e` mete secuencias SGR: sin limpiarlas, una línea vacía pero coloreada
    contaría como texto. Es la métrica con la que `reseed_seguro` compara."""
    if not captura:
        return 0
    return sum(1 for l in captura.split('\n') if _SGR_RE.sub('', l).strip())


def reseed_seguro(captura: str, masa_previa) -> bool:
    """¿Pintar este capture como RE-seed NO destruye lo que ya se ve? PURA.

    BUG 2026-07-19 ("las terminales se cortan"): tras un resize que ACHICA las
    filas, tmux recorta su copia del alt-screen y **no la restaura** al volver
    a crecer (medido 44→30→44: quedan 12 filas y el resto vacío). xterm.js SÍ
    restaura. Entonces el re-seed tomaba la copia mutilada de tmux y la pintaba
    sobre la pantalla sana del browser: la "reparación" era la que rompía.

    El invariante que lo cierra: el watchdog SOLO corre con el pane MUDO desde
    el resize (`debe_resembrar`), y una app muda no puede haber cambiado su
    pantalla. Así que todo contenido que el capture perdió respecto del último
    seed es recorte de tmux, no salida de la app → no se pinta.

    Medido además: mientras el pane está encogido, tmux y xterm tienen
    EXACTAMENTE el mismo contenido, así que frenar el seed nunca deja al
    usuario peor que antes (en ese estado el seed ya era un no-op)."""
    masa = masa_capture(captura)
    if masa == 0:
        return False            # pintar vacío sobre contenido vivo solo destruye
    if masa_previa is not None and masa < masa_previa:
        return False            # el capture perdió contenido: recorte de tmux
    return True


def debe_resembrar(salida_desde_resize: int, alt_on: bool) -> bool:
    """Decisión del watchdog post-resize (fix S1 "negro al salir de fullscreen",
    2026-07-08). Tras un resize que CAMBIÓ las medidas del pane, en el motor
    control la ÚNICA fuente de repintado es la app (refresh-client -C solo hace
    el SIGWINCH; en control mode tmux jamás re-emite pantalla) — y claude
    fullscreen IDLE no redibuja en SIGWINCH (render state-gated, issue upstream
    #43273): la card queda con el crop/pad del alt-buffer (mayormente negra)
    hasta el próximo output. Re-sembrar SOLO si el pane quedó MUDO tras el
    SIGWINCH y está en alt-screen: el buffer normal lo refluye xterm solo, y un
    seed sin reset del cliente duplicaría el scrollback de un shell."""
    return salida_desde_resize == 0 and bool(alt_on)


# ─── Cliente de control (impuro: subprocess + event loop) ─────────────────────

class ClienteControl:
    """UN cliente `tmux -C attach` por conexión WS de terminal.

    Integración asyncio SIN threads: el stdout del subprocess es un pipe y se
    lee con loop.add_reader (no-bloqueante). El callback corre EN el loop, así
    que puede tocar colas/estado sin locks. La lectura se puede pausar/reanudar
    (backpressure hacia tmux: con el pipe lleno, tmux bufferea de SU lado y el
    resto de las sesiones no se entera — aislamiento por cliente).

    NO usar asyncio.create_subprocess_exec acá: el gotcha documentado de tmux
    (CLAUDE.md) aplica a communicate(); este es un proceso de VIDA LARGA con
    pipes crudos — Popen + add_reader es el patrón correcto y probado (mismo
    espíritu que leer()/add_reader del PTY clásico)."""

    def __init__(self, session: str):
        self.session = session
        # Targets EXACTOS (`=nombre`): sin el '=' tmux resuelve por PREFIJO cuando
        # no hay match exacto, así que con `jarvis_1` muerta y `jarvis_12` viva el
        # tipeo/captura de la 1 caía en la 12 (mismo gotcha que terminal_backend
        # `_pane_exacto`). Un pane exige `=nombre:`; attach-session, `=nombre`.
        self.target_sesion = f'={session}'
        self.pane = f'={session}:'
        self._proc = None
        self._fd = None
        self._loop = None
        self._parser = ParserControlMode()
        self._on_output = None      # callback(bytes) — corre en el loop
        self._on_exit = None        # callback() — corre en el loop, una vez
        self._leyendo = False
        self._cerrado = False
        # TODO comando que escribimos al stdin de tmux produce UN bloque
        # %begin/%end en el stream de lectura (también `send-keys` y
        # `refresh-client`, aunque vengan vacíos). `_esperas` lleva UNA entrada
        # por comando en el ORDEN de escritura: un Future si alguien espera la
        # respuesta (comando_con_respuesta) o None si es fire-and-forget
        # (tipeo, resize). Sin los None, el bloque vacío de un send-keys recién
        # escrito se le atribuía al siguiente comando_con_respuesta: el seed
        # recibía la respuesta del tipeo en vez de la captura (seed degradado
        # → reintento de 0.3s, o display-message/capture-pane cruzados).
        self._esperas = collections.deque()
        # El attach -C emite UN %begin/%end propio (vacío) antes de cualquier
        # comando nuestro (verificado tmux 3.6): se descarta por contador para
        # que no se atribuya al primer comando en vuelo.
        self._bloques_espurios = 0
        # Escritura ORDENADA al stdin de tmux (ver _escribir): los payloads
        # grandes salen por un thread y, mientras uno está en vuelo, TODO lo
        # demás hace cola detrás en vez de intercalarse a mitad de línea.
        self._cola_w = collections.deque()
        self._drenando = None

    def iniciar(self, on_output, on_exit):
        """Lanza el attach -C y arranca la lectura. Corre en el event loop."""
        self._loop = asyncio.get_running_loop()
        self._on_output = on_output
        self._on_exit = on_exit
        self._bloques_espurios = 1
        self._proc = subprocess.Popen(
            ['tmux', '-C', 'attach-session', '-t', self.target_sesion],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0,
        )
        self._fd = self._proc.stdout.fileno()
        os.set_blocking(self._fd, False)
        self.reanudar_lectura()

    # ── lectura (pipe → parser → callbacks) ──
    def _leer(self):
        try:
            data = os.read(self._fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b''
        if not data:
            self._terminar()
            return
        # BYTES de punta a punta: el stream lleva UTF-8 crudo (tmux NO escapa
        # los bytes altos) — cualquier pasada por str acá rompería los acentos.
        for ev in self._parser.alimentar(data):
            self._despachar(ev)

    def _despachar(self, ev):
        if ev[0] == 'output':
            if self._on_output:
                self._on_output(ev[2])
        elif ev[0] in ('respuesta', 'respuesta_error'):
            self._resolver_respuesta(ev)
        elif ev[0] == 'exit':
            self._terminar()

    def _resolver_respuesta(self, ev):
        if self._bloques_espurios > 0:
            self._bloques_espurios -= 1
            return
        if not self._esperas:
            return          # bloque sin dueño: descartar
        # UN bloque ↔ UNA entrada, siempre. Un None (fire-and-forget: tipeo,
        # resize) y un Future ya cancelado (comando_con_respuesta que venció su
        # timeout) también CONSUMEN su bloque cuando llega: saltearlos sin
        # consumir corría la cuenta y el próximo comando_con_respuesta recibía
        # la respuesta TARDÍA del anterior.
        fut = self._esperas.popleft()
        if fut is not None and not fut.done():
            fut.set_result(ev)

    def espera_real(self) -> bool:
        """¿Hay algún comando_con_respuesta en vuelo? (los fire-and-forget del
        tipeo no cuentan: el poller de modos solo debe cederle el paso a un
        seed/probe real, no a cada tecla.)"""
        return any(f is not None and not f.done() for f in self._esperas)

    async def comando_con_respuesta(self, cmd: str, timeout: float = 5.0):
        """Comando por el stdin del cliente -C esperando SU bloque %begin/%end.

        La gracia es la SERIALIZACIÓN: la respuesta viaja por el mismo stream
        que los %output, así que todo output emitido antes del %end ya fue
        entregado cuando esto resuelve — un capture-pane por acá no puede
        perderse bytes "en vuelo" ni re-aplicarlos (la race del seed duplicado).
        Devuelve (ok, [líneas]) — el cuerpo llega con ESC/UTF-8 CRUDOS (tmux no
        octal-escapa dentro de bloques; verificado 3.6)."""
        if self._cerrado:
            raise RuntimeError(f'cliente de control cerrado ({self.session})')
        fut = self._loop.create_future()
        self._esperas.append(fut)
        self._escribir((cmd + '\n').encode())
        ev = await asyncio.wait_for(fut, timeout)
        return ev[0] == 'respuesta', ev[1]

    def _terminar(self):
        if self._cerrado:
            return
        self._cerrado = True
        self.pausar_lectura()
        # Esperas en vuelo: cancelarlas ya (que no cuelguen hasta su timeout).
        while self._esperas:
            fut = self._esperas.popleft()
            if fut is not None and not fut.done():
                fut.cancel()
        self._cola_w.clear()
        cb, self._on_exit = self._on_exit, None
        if cb:
            cb()

    def pausar_lectura(self):
        if self._leyendo and self._fd is not None and self._loop is not None:
            try:
                self._loop.remove_reader(self._fd)
            except Exception:
                pass
            self._leyendo = False

    def reanudar_lectura(self):
        if not self._leyendo and not self._cerrado and self._fd is not None:
            self._loop.add_reader(self._fd, self._leer)
            self._leyendo = True

    # ── comandos (stdin del cliente de control) ──
    def _escribir_stdin(self, payload: bytes):
        try:
            if self._proc and self._proc.stdin:
                self._proc.stdin.write(payload)
                self._proc.stdin.flush()
        except Exception:
            pass   # el %exit / EOF del lado de lectura reporta la muerte real

    def _escribir(self, payload: bytes):
        """Punto ÚNICO de escritura al stdin de tmux (solo desde el event loop).

        Un payload chico (≤ LIMITE_ESCRITURA_INLINE) entra de una al pipe. Uno
        grande (un paste de decenas de KB, x3 por el hex) puede llenar el pipe
        y bloquear el write → sale por un thread. MIENTRAS ese thread escribe, el
        loop sigue vivo y otros emisores (el poller de modos cada 1s, un resize,
        una tecla) escribían POR ENCIMA: el kernel no garantiza atomicidad para
        writes de más de un pipe-buffer, así que su línea se colaba a MITAD de
        un `send-keys -H …` → tmux rechazaba la línea partida y el paste perdía
        un tramo (y el comando intruso volvía como %error). Ahora, con una
        escritura grande en vuelo, todo lo que llega hace cola y sale DESPUÉS,
        en orden."""
        if self._cola_w or self._drenando is not None:
            self._cola_w.append(payload)
            return
        if len(payload) <= LIMITE_ESCRITURA_INLINE:
            self._escribir_stdin(payload)
            return
        self._cola_w.append(payload)
        self._drenando = asyncio.ensure_future(self._drenar_cola_w())

    async def _drenar_cola_w(self):
        try:
            while self._cola_w and not self._cerrado:
                await asyncio.to_thread(self._escribir_stdin, self._cola_w.popleft())
        finally:
            self._drenando = None

    def comando(self, cmd: str):
        if self._cerrado:
            return
        self._esperas.append(None)      # su bloque %begin/%end llega y se descarta
        self._escribir((cmd + '\n').encode())

    async def enviar_bytes(self, data: bytes):
        """Input del usuario → send-keys -H. Los pastes grandes se escriben en
        un thread (el pipe de stdin puede llenarse y bloquear el write); el
        await vuelve cuando TODO lo encolado hasta acá ya salió, así el loop de
        recepción del WS procesa los mensajes siguientes en orden."""
        if self._cerrado or not data:
            return
        cmds = comandos_send_keys(self.pane, data)
        self._esperas.extend([None] * len(cmds))     # un bloque por send-keys
        self._escribir(('\n'.join(cmds) + '\n').encode())
        tarea = self._drenando
        if tarea is not None:
            try:
                await asyncio.shield(tarea)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

    def resize(self, cols: int, rows: int):
        """El resize del browser: UNA orden; tmux hace SIGWINCH a la app.
        (refresh-client -C fija el tamaño de ESTE cliente; con window-size
        latest la ventana lo sigue — validado en el spike.)"""
        self.comando(f'refresh-client -C {int(cols)}x{int(rows)}')

    async def cerrar(self):
        """Detach limpio; la sesión tmux (y el agente) siguen vivos."""
        self._cerrado = True
        self.pausar_lectura()
        self._escribir_stdin(b'detach-client\n')
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                await asyncio.to_thread(proc.wait, 5)
            except Exception:
                pass


# ─── Registro de DUEÑOS (un cliente de control con derecho a tamaño por
#     terminal) ──────────────────────────────────────────────────────────────
# En el motor classic el `attach -d` desplazaba al cliente anterior; en control
# mode los clientes CONVIVEN — dos pestañas dueñas se pelean el tamaño
# (`window-size latest` sigue al último) y la perdedora queda wrapeada a un
# ancho ajeno SIN cura (su gate idempotente nunca re-manda). Este registro
# restituye la semántica: al conectar un dueño nuevo, el anterior es
# desplazado (su WS cierra con 4010 y NO se auto-reintenta). Los observers
# (?qa=1) no participan. Vive acá (y no en terminals.py) para que el cierre
# pre-execv no dependa del router.

_duenos: dict = {}   # terminal_id -> {'cliente': ClienteControl, 'desplazar': callable}


def registrar_dueno(terminal_id: int, cliente: 'ClienteControl', desplazar):
    """Registra al dueño nuevo; devuelve el registro del ANTERIOR (o None).
    El caller decide cómo desplazarlo (cerrar su WS con 4010)."""
    anterior = _duenos.get(terminal_id)
    _duenos[terminal_id] = {'cliente': cliente, 'desplazar': desplazar}
    return anterior


def liberar_dueno(terminal_id: int, cliente: 'ClienteControl'):
    """Des-registra SOLO si el dueño vigente es este cliente: una conexión
    vieja que muere tarde no debe des-registrar al dueño nuevo."""
    reg = _duenos.get(terminal_id)
    if reg is not None and reg['cliente'] is cliente:
        del _duenos[terminal_id]


def cerrar_clientes_para_reexec():
    """Cierre ordenado de TODOS los clientes de control vivos, para llamar
    justo ANTES del os.execv del updater (SÍNCRONO a propósito: el loop está
    por morir). Sin esto, cada `tmux -C attach` quedaba huérfano/zombi colgado
    del nuevo proceso (que jamás lo reapea) — un zombi por terminal conectada
    por cada restart. detach + terminate + wait corto; las sesiones tmux (y
    los agentes) siguen vivas."""
    regs = list(_duenos.values())
    _duenos.clear()
    for reg in regs:
        c = reg['cliente']
        try:
            c._cerrado = True
            c.pausar_lectura()
            c._escribir_stdin(b'detach-client\n')
            if c._proc is not None:
                c._proc.terminate()
        except Exception:
            pass
    for reg in regs:
        proc = reg['cliente']._proc
        if proc is None:
            continue
        try:
            proc.wait(timeout=1)
        except Exception:
            pass
