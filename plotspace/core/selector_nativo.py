"""Selector de carpetas NATIVO del sistema operativo.

Jarvis corre en TU máquina (un servidor local + el navegador). Un navegador no puede
entregar la ruta real de una carpeta, pero el servidor SÍ puede abrir el diálogo
del sistema en tu escritorio y devolver la ruta que elegiste. Por motor:

- **macOS** → `osascript` (`choose folder`, el selector de Finder).
- **Linux** → `zenity` (GTK / GNOME) o `kdialog` (KDE). Necesita sesión gráfica.
- **Windows (WSL2)** → `powershell.exe` abre el diálogo de Windows; la ruta que vuelve
  (`C:\\…` o `\\\\wsl.localhost\\Ubuntu\\home\\…`) se traduce con `wslpath -u` a la ruta
  Linux que el motor entiende. El diálogo ARRANCA en tu home de Linux
  (`wslpath -w ~`), que es donde deben vivir los proyectos.

Si no hay motor (servidor sin pantalla, falta zenity, acceso remoto…) el front cae al
explorador propio de Jarvis (`routers/fs.py`). Nada de acá toca el contenido de las
carpetas: solo devuelve LA RUTA elegida.

Un solo diálogo a la vez (lock): abrir dos selectores encima no tiene sentido.
`JARVIS_SELECTOR_NATIVO=off` lo apaga.
"""
import base64
import os
import shutil
import subprocess
import sys
import threading
from typing import Optional

from plotspace.core import idioma_ui

TIMEOUT = 600          # s que se espera a que la persona elija (después = cancelado)
_lock = threading.Lock()

_RUTA_POWERSHELL = '/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'


class NoDisponible(Exception):
    """No hay selector nativo en este entorno. `razon` lo explica."""
    def __init__(self, razon: str):
        super().__init__(razon)
        self.razon = razon


class Ocupado(Exception):
    """Ya hay un selector abierto."""


class ErrorSelector(Exception):
    """El diálogo falló (no es lo mismo que cancelarlo)."""


# ── entorno y motor ────────────────────────────────────────────────────────

def entorno() -> str:
    """'wsl' | 'macos' | 'linux' | 'windows' | 'otro'."""
    if sys.platform == 'darwin':
        return 'macos'
    if os.name == 'nt':
        return 'windows'
    if sys.platform.startswith('linux'):
        if os.environ.get('WSL_DISTRO_NAME') or os.environ.get('WSL_INTEROP'):
            return 'wsl'
        try:
            with open('/proc/version', encoding='utf-8', errors='replace') as f:
                if 'microsoft' in f.read().lower():
                    return 'wsl'
        except OSError:
            pass
        return 'linux'
    return 'otro'


def _powershell() -> Optional[str]:
    return shutil.which('powershell.exe') or (_RUTA_POWERSHELL if os.path.exists(_RUTA_POWERSHELL) else None)


def _hay_pantalla() -> bool:
    return bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))


def motor():
    """(nombre, razon): el motor que se usaría, o (None, por qué no hay)."""
    if os.environ.get('JARVIS_SELECTOR_NATIVO', '').strip().lower() in ('off', '0', 'false', 'no'):
        return None, 'desactivado'
    ent = entorno()
    if ent == 'macos':
        return ('osascript', None) if shutil.which('osascript') else (None, 'falta_herramienta')
    if ent == 'wsl':
        if not _powershell():
            return None, 'sin_powershell'
        if not shutil.which('wslpath'):
            return None, 'falta_herramienta'
        return 'powershell', None
    if ent == 'linux':
        if not _hay_pantalla():
            return None, 'sin_pantalla'
        for nombre in ('zenity', 'kdialog'):
            if shutil.which(nombre):
                return nombre, None
        return None, 'falta_herramienta'
    return None, 'no_soportado'


def ayuda_instalacion() -> Optional[str]:
    """Qué instalar cuando falta la herramienta (texto para la persona)."""
    if entorno() == 'linux' and _hay_pantalla() and not (shutil.which('zenity') or shutil.which('kdialog')):
        return idioma_ui.L(
            'Para elegir carpetas con el selector de tu sistema instalá zenity: sudo apt install zenity',
            "To pick folders with your system's dialog install zenity: sudo apt install zenity")
    return None


# ── construcción de comandos (PURA: se prueba sin abrir nada) ──────────────

def _as(s: str) -> str:
    """Escapa un texto para meterlo entre comillas en AppleScript."""
    return str(s).replace('\\', '\\\\').replace('"', '\\"')


def _ps(s: str) -> str:
    """Escapa un texto para meterlo entre comillas simples en PowerShell."""
    return str(s).replace("'", "''")


def cmd_zenity(inicio: Optional[str], titulo: str) -> list:
    cmd = ['zenity', '--file-selection', '--directory', '--title', titulo]
    if inicio:
        cmd += ['--filename', inicio.rstrip('/') + '/']
    return cmd


def cmd_kdialog(inicio: Optional[str], titulo: str) -> list:
    return ['kdialog', '--title', titulo, '--getexistingdirectory', inicio or os.path.expanduser('~')]


def cmd_osascript(inicio: Optional[str], titulo: str) -> list:
    por_defecto = f' default location (POSIX file "{_as(inicio)}")' if inicio else ''
    # `activate` suelto trae el diálogo al frente SIN pasar por "System Events" (que
    # pediría el permiso de Automatización de macOS la primera vez).
    script = (
        'activate\n'
        f'set _f to choose folder with prompt "{_as(titulo)}"{por_defecto}\n'
        'return POSIX path of _f'
    )
    return ['osascript', '-e', script]


def script_powershell(inicio_win: Optional[str], titulo: str, marcador: str) -> str:
    """OpenFileDialog "de carpeta": sin validar nombres ni existencia, la persona
    navega a la carpeta y confirma con el nombre-marcador; se devuelve el padre.
    (A diferencia de FolderBrowserDialog, este diálogo SÍ puede arrancar y navegar
    por rutas UNC como \\\\wsl.localhost\\…, que es donde viven los proyectos.)"""
    inicial = f"$d.InitialDirectory = '{_ps(inicio_win)}'\n" if inicio_win else ''
    return (
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)\n"
        "Add-Type -AssemblyName System.Windows.Forms\n"
        "$f = New-Object System.Windows.Forms.Form\n"
        "$f.TopMost = $true\n"
        "$d = New-Object System.Windows.Forms.OpenFileDialog\n"
        f"$d.Title = '{_ps(titulo)}'\n"
        "$d.ValidateNames = $false\n"
        "$d.CheckFileExists = $false\n"
        "$d.CheckPathExists = $true\n"
        f"$d.FileName = '{_ps(marcador)}'\n"
        f"{inicial}"
        "if ($d.ShowDialog($f) -eq [System.Windows.Forms.DialogResult]::OK) {\n"
        "  [Console]::Out.Write((Split-Path -Parent $d.FileName))\n"
        "} else { exit 1 }\n"
    )


def cmd_powershell(exe: str, script: str) -> list:
    codificado = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    return [exe, '-NoProfile', '-NonInteractive', '-STA', '-EncodedCommand', codificado]


# ── ejecución ──────────────────────────────────────────────────────────────

def _inicio_valido(inicio: Optional[str]) -> Optional[str]:
    """La carpeta donde arranca el diálogo: la pedida si existe; si no, el ancestro
    más cercano que exista (la base de 'proyectos nuevos' puede no existir todavía)."""
    if not inicio or not str(inicio).startswith('/'):
        return None
    p = os.path.abspath(os.path.expanduser(str(inicio)))
    while p and not os.path.isdir(p):
        padre = os.path.dirname(p)
        if padre == p:
            return None
        p = padre
    return p or None


def _correr(cmd: list):
    return subprocess.run(cmd, capture_output=True, timeout=TIMEOUT)


def _texto(b: bytes) -> str:
    return (b or b'').decode('utf-8', errors='replace').replace('\r', '').strip('\n').strip()


def _wslpath(flag: str, ruta: str) -> Optional[str]:
    try:
        r = subprocess.run(['wslpath', flag, ruta], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    out = _texto(r.stdout)
    return out if r.returncode == 0 and out else None


def _es_cancelacion(nombre: str, r) -> bool:
    if r.returncode == 0:
        return False
    if nombre == 'osascript':
        return '-128' in _texto(r.stderr) or 'cancel' in _texto(r.stderr).lower()
    # zenity / kdialog / powershell: salida 1 sin error = canceló
    return r.returncode == 1 and not _texto(r.stderr)


def elegir_carpeta(inicio: Optional[str] = None, titulo: Optional[str] = None) -> dict:
    """Abre el selector del sistema y espera. Bloqueante (llamar en un thread).
    → {ruta | None, cancelado, aviso | None, motor}"""
    nombre, razon = motor()
    if not nombre:
        raise NoDisponible(razon or 'no_soportado')
    if not _lock.acquire(blocking=False):
        raise Ocupado()
    try:
        titulo = titulo or idioma_ui.L('Elegí la carpeta del proyecto', 'Choose the project folder')
        inicio = _inicio_valido(inicio)
        if nombre == 'zenity':
            cmd = cmd_zenity(inicio, titulo)
        elif nombre == 'kdialog':
            cmd = cmd_kdialog(inicio, titulo)
        elif nombre == 'osascript':
            cmd = cmd_osascript(inicio, titulo)
        else:  # powershell (WSL)
            inicio_win = _wslpath('-w', inicio or os.path.expanduser('~'))
            marcador = idioma_ui.L('Seleccionar esta carpeta', 'Select this folder')
            cmd = cmd_powershell(_powershell(), script_powershell(inicio_win, titulo, marcador))
        try:
            r = _correr(cmd)
        except subprocess.TimeoutExpired:
            return {'ruta': None, 'cancelado': True, 'aviso': None, 'motor': nombre}
        except OSError as e:
            raise ErrorSelector(str(e))
        if _es_cancelacion(nombre, r):
            return {'ruta': None, 'cancelado': True, 'aviso': None, 'motor': nombre}
        if r.returncode != 0:
            raise ErrorSelector(_texto(r.stderr) or f'código {r.returncode}')
        ruta = _texto(r.stdout)
        if not ruta:
            return {'ruta': None, 'cancelado': True, 'aviso': None, 'motor': nombre}
        if nombre == 'powershell':
            ruta = _wslpath('-u', ruta) or ''
        if len(ruta) > 1:
            ruta = ruta.rstrip('/')
        if not ruta or not os.path.isdir(ruta):
            raise ErrorSelector(idioma_ui.L('La ruta elegida no existe', "The chosen path doesn't exist"))
        aviso = 'mnt' if (nombre == 'powershell' and ruta.startswith('/mnt/')) else None
        return {'ruta': ruta, 'cancelado': False, 'aviso': aviso, 'motor': nombre}
    finally:
        _lock.release()
