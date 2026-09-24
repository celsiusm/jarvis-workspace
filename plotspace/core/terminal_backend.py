"""Motor de terminales — la interfaz y su implementación tmux.

POR QUÉ EXISTE ESTE MÓDULO
==========================
`terminals.py` mezclaba dos cosas distintas en las mismas funciones:

  1. QUÉ lanzar — lógica de producto: qué CLI corre en la terminal, con qué
     cuenta de Codex, con qué PATH de nvm, si el arranque se ve o no. Eso es
     Jarvis y no cambia nunca según el sistema operativo.
  2. CÓMO spawnearlo — detalle del motor: `tmux new-session`, `send-keys`,
     `capture-pane`. Eso SÍ cambia: en Windows no hay tmux y las PTYs se
     crean con ConPTY.

Este módulo se queda con (2) detrás de una interfaz. `terminals.py` conserva
(1) y le pasa al backend una `EspecSesion` — "corré este comando, en este
directorio, con estas variables" — sin saber cómo se hace.

Hoy hay una sola implementación (`TmuxBackend`, comportamiento idéntico al
histórico). Los motores de la app de Windows (ConPTY/termhost/in-proc y el
motor Rust) se eliminaron el 2026-08-06 junto con la app: tmux es EL motor.
La interfaz queda porque separa el QUÉ del CÓMO y los tests inyectan dobles
por acá (`set_backend`).

REGLA HEREDADA QUE NO SE NEGOCIA
--------------------------------
`subprocess.run` síncrono para TODO comando tmux de control:
`asyncio.create_subprocess_exec` cuelga indefinidamente con `new-session -d`
(hereda FDs y `communicate()` no retorna). Lo que se hace para no trabar el
event loop es sacarlo a un thread (`asyncio.to_thread`), NUNCA cambiar de API.
Ver CLAUDE.md § "subprocess.run vs asyncio para tmux/git".
"""
import asyncio
import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

# Timeout de pared para los comandos de control. Un server tmux trabado (socket
# colgado tras un crash) sin esto congela al handler que lo llama; con esto se
# degrada a "no existe" / "no pude", que siempre es la respuesta segura.
TIMEOUT_CONTROL = 5


@dataclass
class EspecSesion:
    """Lo que `terminals.py` le pide al backend: el QUÉ, sin el CÓMO.

    `comando` None = el pane nace como shell pelado (arranque visible, el CLI
    se tipea después con `enviar_texto`). Con comando = el CLI es el programa
    del pane y no se ve tipeado nada.
    """
    terminal_id: int
    cwd: str
    comando: Optional[str] = None
    # Variables que van al ENTORNO DE LA SESIÓN (en tmux, cada una un `-e`).
    # OJO: tmux IGNORA el override de PATH por esta vía — el toolchain de nvm
    # se antepone dentro del propio `comando`. Ver [[codex-wsl-path-node]].
    env: Dict[str, str] = field(default_factory=dict)
    # Entorno del PROCESO que lanza la sesión (hoy `_env_terminal()`: sin
    # ANTHROPIC_API_KEY para que los agentes usen sus propias credenciales).
    entorno_proceso: Optional[Dict[str, str]] = None
    cols: int = 220
    rows: int = 50


# "Todo el scrollback disponible", para quien necesita el buffer ENTERO y no
# una ventana: el watchdog rescatando un TASK_* que quedó fuera de las últimas
# N líneas. El motor lo traduce a lo suyo — tmux a `-S -`. No puede ser 0 ni
# None: esos ya significan "solo la
# pantalla visible", que es exactamente lo contrario.
TODO_EL_SCROLLBACK = -1


class TerminalBackend(ABC):
    """Contrato del motor de terminales.

    Todo lo que Jarvis necesita de una sesión viva. Lo que NO está acá a
    propósito: el stream de bytes del attach, que ya vive separado en
    `core/control_mode.py` y se reemplaza por su cuenta.
    """

    @abstractmethod
    def nombre_sesion(self, terminal_id: int) -> str:
        """Identificador de la sesión de esta terminal."""

    @abstractmethod
    def existe(self, terminal_id: int) -> bool:
        """¿La sesión está viva? Un motor colgado responde False."""

    @abstractmethod
    def crear(self, espec: EspecSesion) -> bool:
        """Crea la sesión si no existe. Síncrono a propósito: el caller lo
        saca del event loop con `asyncio.to_thread`."""

    @abstractmethod
    async def matar(self, terminal_id: int) -> bool:
        """Mata la sesión y VERIFICA el kill. False = sigue viva o falló."""

    @abstractmethod
    def enviar_texto(self, terminal_id: int, texto: str) -> None:
        """Escribe texto literal en el pane (sin interpretarlo como teclas)."""

    @abstractmethod
    def enviar_tecla(self, terminal_id: int, tecla: str) -> None:
        """Manda una tecla con nombre ('Enter', 'BSpace', …)."""

    @abstractmethod
    def capturar(self, terminal_id: int, lineas: Optional[int] = None,
                 con_escapes: bool = False) -> Optional[str]:
        """Texto visible del pane. `lineas` = cuántas de scrollback hacia
        atrás; None = solo la pantalla. None de retorno = no se pudo."""

    async def capturar_async(self, terminal_id: int,
                             lineas: Optional[int] = None) -> str:
        """Igual que `capturar`, para los POLLERS (agent_watch 1s, agent_live y
        dev_detect 2s). Existe aparte porque esos corren en el event loop y no
        pueden pagar un `subprocess.run` sincrónico por terminal por tick.

        POR QUÉ ESTO ES UN MÉTODO DEL MOTOR y no un buffer alimentado por el
        stream del WS: los bytes del attach solo fluyen cuando hay un browser
        conectado. La conciencia ambiental del enjambre (los sonidos, el aura,
        la detección de dev servers) tiene que funcionar con el browser cerrado
        — así que la pantalla la sirve quien SIEMPRE lee la PTY: tmux.

        Default: envolver la versión sincrónica en un thread. Los motores que
        tienen una vía async nativa la pisan."""
        return await asyncio.to_thread(self.capturar, terminal_id, lineas) or ''

    @abstractmethod
    async def refrescar(self, terminal_id: int) -> None:
        """Repintado completo para todos los clientes de la sesión."""

    @abstractmethod
    async def redimensionar(self, terminal_id: int, cols: int, rows: int) -> None:
        """Reacomoda la sesión a un tamaño nuevo del browser."""

    @abstractmethod
    def listar_sesiones(self) -> Set[str]:
        """Nombres de todas las sesiones vivas del motor."""

    @abstractmethod
    def titulos_vivos(self) -> Dict[str, str]:
        """{nombre_sesión: título del pane} — qué está haciendo cada agente."""

    def comandos_vivos(self) -> Optional[Dict[str, str]]:
        """{nombre_sesión: comando corriendo} — con qué CLI está cada terminal.

        Lo usa el liveness para distinguir un agente vivo de uno caído. None =
        NO SE PUDO LEER, y esa distinción es crítica: `estados()` la interpreta
        como "no sé" y nadie se declara muerto. Un dict vacío significaría "no
        hay ninguna sesión viva" y marcaría caído a todo el enjambre.
        """
        return None

    def pid_de_terminal(self, terminal_id: int) -> Optional[int]:
        """PID del proceso que corre en la terminal, o None si el motor no lo
        sabe.

        Lo usa la atribución de dev servers: un puerto cuyo árbol de ancestros
        incluye este PID fue levantado EN esa terminal, y así el menú de
        localhost puede decir de quién es. Devolver None degrada esa atribución
        (el server se sigue detectando, solo que sin dueño) — nunca rompe."""
        return None

    @abstractmethod
    def estado_pane(self, terminal_id: int, formato: str) -> Optional[str]:
        """Una propiedad del pane (pid, título, flags…) según el motor."""

    def preparar_servidor(self) -> None:
        """Configuración global del motor (estilos, bindings). Opcional: los
        motores que no tienen estado global la dejan en no-op."""

    def sanear_sesion(self, nombre: str) -> None:
        """Re-aplica la configuración obligatoria a una sesión que sobrevivió
        a un reinicio de Jarvis. Opcional."""


# ══════════════════════════════════════════════════════════════════════════
#  tmux
# ══════════════════════════════════════════════════════════════════════════

# Guards de proceso: lo global del server tmux se aplica una vez, no una por
# terminal (las ~190 bindings cuestan ~1.1s y se veían como demora del primer
# pintado; los 11 `set -g` del estilo, repetidos por terminal, eran ruido).
_COPY_MODE_BINDINGS_INSTALADOS = False
_ESTILO_OBSIDIAN_APLICADO = False


class TmuxBackend(TerminalBackend):
    """El motor histórico: una sesión tmux detached por terminal.

    tmux acá NO dibuja nada — con el motor control-mode es guardián de
    procesos y caño de bytes, y xterm.js es el único emulador. Ver
    [[motor-un-emulador]].
    """

    PREFIJO = 'jarvis_'

    # ── identidad ────────────────────────────────────────────────────────
    def nombre_sesion(self, terminal_id: int) -> str:
        return f'{self.PREFIJO}{terminal_id}'

    def _target_exacto(self, terminal_id: int) -> str:
        """Target con '=' — sin él tmux resuelve por PREFIJO cuando no hay
        match exacto, y matar una `jarvis_1` ya muerta se llevaba puesta a
        `jarvis_12` viva."""
        return f'={self.nombre_sesion(terminal_id)}'

    def _pane_exacto(self, terminal_id: int) -> str:
        """Target de PANE exacto para send-keys/capture-pane: `=nombre` solo
        vale como target de sesión; para un pane tmux exige `=nombre:`. Sin el
        '=', con `jarvis_1` muerta y `jarvis_12` viva, el texto/captura de la 1
        iba a parar a la 12."""
        return f'{self._target_exacto(terminal_id)}:'

    # ── ciclo de vida ────────────────────────────────────────────────────
    def existe(self, terminal_id: int) -> bool:
        try:
            r = subprocess.run(
                ['tmux', 'has-session', '-t', self._target_exacto(terminal_id)],
                capture_output=True, timeout=TIMEOUT_CONTROL,
            )
        except subprocess.TimeoutExpired:
            return False       # tmux colgado = degradación segura
        return r.returncode == 0

    def crear(self, espec: EspecSesion) -> bool:
        nombre = self.nombre_sesion(espec.terminal_id)
        if self.existe(espec.terminal_id):
            return True

        argv = ['tmux', 'new-session', '-d', '-s', nombre,
                '-x', str(espec.cols), '-y', str(espec.rows)]
        for clave, valor in espec.env.items():
            argv += ['-e', f'{clave}={valor}']
        if espec.comando:
            argv.append(espec.comando)

        r = subprocess.run(
            argv, capture_output=True, text=True,
            cwd=espec.cwd, env=espec.entorno_proceso,
        )
        if r.returncode != 0:
            print(f'[tmux] Error creando sesión {nombre}: {(r.stderr or "").strip()}')
            return False

        self.sanear_sesion(nombre)
        self.preparar_servidor()
        print(f'[tmux] Sesión {nombre} creada en {espec.cwd}')
        return True

    async def matar(self, terminal_id: int) -> bool:
        session = self.nombre_sesion(terminal_id)
        target = self._target_exacto(terminal_id)
        try:
            r = await self._async('tmux', 'kill-session', '-t', target,
                                  text=True, timeout=TIMEOUT_CONTROL)
        except Exception as e:
            print(f'[tmux] kill-session {session}: excepción {e}')
            return False
        if r.returncode != 0 and 'find' not in (r.stderr or ''):
            print(f'[tmux] kill-session {session} rc={r.returncode}: {(r.stderr or "").strip()}')
        # Verificar el kill: si "no falló" pero la sesión sigue viva (tmux
        # degradado), el agente fantasma deja de pasar mudo.
        try:
            chk = await self._async('tmux', 'has-session', '-t', target,
                                    text=True, timeout=TIMEOUT_CONTROL)
            if chk.returncode == 0:
                print(f'[tmux] ALERTA: {session} sigue VIVA tras kill-session (¿tmux trabado?)')
                return False
        except Exception:
            pass
        return True

    # ── entrada ──────────────────────────────────────────────────────────
    def enviar_texto(self, terminal_id: int, texto: str) -> None:
        # `-l --` literal: el texto NUNCA se interpreta como nombre de tecla ni
        # como flag. Mismo patrón anti-inyección que el batch y send_to_agent.
        try:
            subprocess.run(
                ['tmux', 'send-keys', '-t', self._pane_exacto(terminal_id), '-l', '--', texto],
                capture_output=True, timeout=TIMEOUT_CONTROL,
            )
        except subprocess.TimeoutExpired:
            print(f'[tmux] send-keys {self.nombre_sesion(terminal_id)}: timeout (¿tmux trabado?)')

    def enviar_tecla(self, terminal_id: int, tecla: str) -> None:
        try:
            subprocess.run(
                ['tmux', 'send-keys', '-t', self._pane_exacto(terminal_id), tecla],
                capture_output=True, timeout=TIMEOUT_CONTROL,
            )
        except subprocess.TimeoutExpired:
            print(f'[tmux] send-keys {self.nombre_sesion(terminal_id)}: timeout (¿tmux trabado?)')

    # ── lectura ──────────────────────────────────────────────────────────
    def capturar(self, terminal_id: int, lineas: Optional[int] = None,
                 con_escapes: bool = False) -> Optional[str]:
        argv = ['tmux', 'capture-pane', '-t', self._pane_exacto(terminal_id), '-p']
        if con_escapes:
            argv.append('-e')          # conserva los colores del pane
        if lineas == TODO_EL_SCROLLBACK:
            argv += ['-S', '-']        # el buffer entero
        elif lineas:
            argv += ['-S', f'-{lineas}']
        try:
            r = subprocess.run(argv, capture_output=True, text=True,
                               timeout=TIMEOUT_CONTROL)
        except Exception:
            return None
        return r.stdout if r.returncode == 0 else None

    async def capturar_async(self, terminal_id: int,
                             lineas: Optional[int] = None) -> str:
        """Vía async nativa para los pollers. Es la ÚNICA excepción a la regla
        de `subprocess.run` para tmux (ver CLAUDE.md): `capture-pane` produce
        salida y termina, así que no cuelga como los comandos de control — y
        acá el beneficio es real, son ~3N capturas por segundo que no deben
        pasar por un thread cada una."""
        argv = ['tmux', 'capture-pane', '-t', self._pane_exacto(terminal_id), '-p']
        if lineas == TODO_EL_SCROLLBACK:
            argv += ['-S', '-']
        elif lineas:
            argv += ['-S', f'-{lineas}']
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()   # sin el wait quedaba un zombie por kill (144 medidos)
            except Exception:
                pass
            return ''               # tmux colgado: no acumular procesos/FDs en el poller
        return out.decode(errors='replace') if proc.returncode == 0 else ''

    def listar_sesiones(self) -> Set[str]:
        try:
            r = subprocess.run(
                ['tmux', 'list-sessions', '-F', '#{session_name}'],
                capture_output=True, text=True, timeout=TIMEOUT_CONTROL,
            )
        except Exception:
            return set()
        return set(r.stdout.splitlines()) if r.returncode == 0 else set()

    def comandos_vivos(self) -> Optional[Dict[str, str]]:
        try:
            r = subprocess.run(
                ['tmux', 'list-panes', '-a', '-F',
                 '#{session_name}\t#{pane_current_command}'],
                capture_output=True, text=True, timeout=TIMEOUT_CONTROL)
        except Exception:
            return None
        if r.returncode != 0:
            return None
        salida = {}
        for linea in r.stdout.splitlines():
            if '\t' in linea:
                sesion, cmd = linea.split('\t', 1)
                salida.setdefault(sesion.strip(), cmd.strip())
        return salida

    def titulos_vivos(self) -> Dict[str, str]:
        try:
            r = subprocess.run(
                ['tmux', 'list-panes', '-a', '-F', '#{session_name}\t#{pane_title}'],
                capture_output=True, text=True, timeout=TIMEOUT_CONTROL,
            )
        except Exception:
            return {}
        if r.returncode != 0:
            return {}
        titulos = {}
        for linea in r.stdout.splitlines():
            if '\t' not in linea:
                continue
            sesion, titulo = linea.split('\t', 1)
            titulos[sesion] = titulo
        return titulos

    def estado_pane(self, terminal_id: int, formato: str) -> Optional[str]:
        try:
            r = subprocess.run(
                ['tmux', 'display-message', '-p', '-t',
                 self.nombre_sesion(terminal_id), formato],
                capture_output=True, text=True, timeout=TIMEOUT_CONTROL,
            )
        except Exception:
            return None
        return r.stdout.strip() if r.returncode == 0 else None

    # ── repintado ────────────────────────────────────────────────────────
    async def refrescar(self, terminal_id: int) -> None:
        await self._refrescar_clientes(self.nombre_sesion(terminal_id))

    async def redimensionar(self, terminal_id: int, cols: int, rows: int) -> None:
        # Tamaños degenerados (container colapsado en el browser) harían que
        # tmux reformatee el output como una letra por línea.
        if cols < 20 or rows < 5:
            return
        # NO se usa `resize-window -x -y`: deja la ventana en `window-size
        # manual`, que la CLAVA y anula el `latest`. Con manual + un cliente
        # más chico (pestaña vieja, attach huérfano) tmux rellena el sobrante
        # con '·' — el bug del "rectángulo con puntitos". El tamaño real ya lo
        # propaga el SIGWINCH del PTY. Ver [[tmux-size-clamping]].
        await self._refrescar_clientes(self.nombre_sesion(terminal_id))

    async def _refrescar_clientes(self, session: str) -> None:
        """`refresh-client -t <target>` toma un CLIENTE (el tty), NO una
        sesión: en tmux 3.6 pasarle el nombre de sesión falla con 'can't find
        client' y se tragaba en silencio por capture_output → el auto-sanado
        del garble nunca repintaba y había que apretar F5. Acá se enumeran los
        clientes reales y se refresca cada uno."""
        try:
            r = await asyncio.to_thread(
                subprocess.run,
                ['tmux', 'list-clients', '-t', session, '-F', '#{client_tty}'],
                capture_output=True, text=True, timeout=TIMEOUT_CONTROL,
            )
        except Exception:
            return
        for tty in [t.strip() for t in (r.stdout or '').splitlines() if t.strip()]:
            await self._async('tmux', 'refresh-client', '-t', tty)

    # ── configuración del server ─────────────────────────────────────────
    def sanear_sesion(self, nombre: str) -> None:
        """Opciones OBLIGATORIAS por sesión. Se re-aplican también a las que
        sobrevivieron a un reinicio de Jarvis: si el server tmux se reinició,
        los globales volvieron al default y el guard de proceso impide
        re-aplicarlos — por-sesión es barato y a prueba de eso."""
        opciones = [
            ('mouse', 'on'),
            ('focus-events', 'off'),
            # window-size latest: la ventana sigue al ÚLTIMO cliente activo,
            # nunca al menor. Sin esto, dos clientes de distinto tamaño clavan
            # la ventana al más chico y aparecen los puntitos.
            ('window-size', 'latest'),
            # status off: la barra de abajo era ruido (el título vivo ya está
            # en el chrome de la card) y se comía una fila del agente.
            ('status', 'off'),
        ]
        for opcion, valor in opciones:
            subprocess.run(
                ['tmux', 'set-option', '-t', nombre, opcion, valor],
                capture_output=True, timeout=TIMEOUT_CONTROL,
            )

    def preparar_servidor(self) -> None:
        """Estilo global del server tmux (bordes y copy-mode al design system
        Obsidian Glass del frontend). Guard de proceso: una vez alcanza."""
        global _ESTILO_OBSIDIAN_APLICADO
        if _ESTILO_OBSIDIAN_APLICADO:
            return
        _ESTILO_OBSIDIAN_APLICADO = True
        estilos = [
            ('status',                      'off'),
            ('status-style',                'bg=#15151d,fg=#75768a'),
            ('status-left-style',           'fg=#a78bfa,bold'),
            ('window-status-style',         'fg=#75768a'),
            ('window-status-current-style', 'fg=#a78bfa,bold'),
            ('message-style',               'bg=#15151d,fg=#e6e6f0'),
            ('message-command-style',       'bg=#15151d,fg=#e6e6f0'),
            ('mode-style',                  'bg=#3b2d66,fg=#e6e6f0'),
            ('pane-border-style',           'fg=#2a2a36'),
            ('pane-active-border-style',    'fg=#a78bfa'),
            ('clock-mode-colour',           '#a78bfa'),
        ]
        for opcion, valor in estilos:
            subprocess.run(['tmux', 'set', '-g', opcion, valor], capture_output=True)

    def instalar_bindings_copy_mode(self) -> None:
        """Toda tecla imprimible en copy-mode cancela el modo y pasa al shell,
        así se puede tipear normal cuando el scroll del mouse metió a tmux en
        copy-mode. SOLO aplica al motor classic: en control-mode el scroll es
        local de xterm, las bindings son inertes y encima —por globales— se
        filtraban a las sesiones tmux PERSONALES del usuario."""
        global _COPY_MODE_BINDINGS_INSTALADOS
        if _COPY_MODE_BINDINGS_INSTALADOS:
            return
        chars = list('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')
        chars += list('.,;:/?!@#$%&*()-_=+[]{}|\\\'\"<>~`^')
        especiales = [('Space', 'Space'), ('Enter', 'Enter'),
                      ('BSpace', 'BSpace'), ('Tab', 'Tab')]
        for tabla in ('copy-mode', 'copy-mode-vi'):
            for k in chars:
                # La acción entera va como UN string: si ';' viaja como argv
                # aparte, tmux lo toma como separador de su propio comando y
                # ejecuta el send-keys sobre la pane activa.
                subprocess.run(
                    ['tmux', 'bind-key', '-T', tabla, k,
                     f'send-keys -X cancel ; send-keys {k!r}'],
                    capture_output=True,
                )
            for tecla, envio in especiales:
                subprocess.run(
                    ['tmux', 'bind-key', '-T', tabla, tecla,
                     f'send-keys -X cancel ; send-keys {envio}'],
                    capture_output=True,
                )
        _COPY_MODE_BINDINGS_INSTALADOS = True
        print('[tmux] Bindings copy-mode → passthrough instaladas')

    def matar_sesion_por_nombre(self, nombre: str) -> None:
        """Kill de una sesión que no cuelga de un terminal_id (zombis del
        reconcile). Target exacto, mismo motivo que `_target_exacto`."""
        subprocess.run(['tmux', 'kill-session', '-t', f'={nombre}'],
                       capture_output=True, timeout=TIMEOUT_CONTROL)

    # ── plomería ─────────────────────────────────────────────────────────
    @staticmethod
    async def _async(*args, **kw):
        """Comando de control FUERA del event loop. `subprocess.run` sigue
        siendo obligatorio (create_subprocess_exec cuelga); `to_thread` solo
        lo saca del loop para que no trabe el eco de las otras terminales.
        Es el blindaje contra el "freeze por mil cortes"."""
        kw.setdefault('capture_output', True)
        return await asyncio.to_thread(subprocess.run, list(args), **kw)


# ══════════════════════════════════════════════════════════════════════════
#  Selección del motor
# ══════════════════════════════════════════════════════════════════════════

_BACKEND: Optional[TerminalBackend] = None
# En el boot varios hilos piden backend() a la vez (reconcile, pollers): sin
# lock, cada uno fabricaba SU instancia del motor — con motores con estado
# propio eso duplicaba mundos de terminales (cards negras, 2026-08-02).
_BACKEND_LOCK = threading.Lock()


def backend() -> TerminalBackend:
    """El motor de terminales activo. ÚNICO punto de selección: el resto del
    código pide `backend()` y no sabe con qué está hablando.

    Desde 2026-08-06 el motor es UNO: tmux. Los motores de la app nativa de
    Windows (ConPTY in-proc, termhost, motor Rust) se fueron con la app; la
    indirección queda porque es el punto de inyección de los tests
    (`set_backend`) y la costura por si algún día vuelve a haber otro motor."""
    global _BACKEND
    if _BACKEND is None:
        with _BACKEND_LOCK:
            if _BACKEND is None:
                _BACKEND = TmuxBackend()
    return _BACKEND


def set_backend(nuevo: Optional[TerminalBackend]) -> None:
    """Inyección para los tests. None restaura el default."""
    global _BACKEND
    _BACKEND = nuevo
