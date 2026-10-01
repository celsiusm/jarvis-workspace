"""Selector de carpetas nativo (plotspace/core/selector_nativo.py + /api/fs/nativo|elegir).

Los diálogos reales no se pueden abrir en CI: cada motor se prueba con SHIMS (scripts
ejecutables en un PATH de mentira que imprimen lo que imprimiría zenity / osascript /
powershell.exe / wslpath), así se ejercita el subprocess de verdad — argumentos,
códigos de salida, decodificación, traducción de rutas — sin pantalla."""
import base64
import os
import stat
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest
from fastapi.testclient import TestClient

from plotspace.main import app
import plotspace.core.selector_nativo as sn


# ── helpers ────────────────────────────────────────────────────────────────

_SLEEP = next(p for p in ("/bin/sleep", "/usr/bin/sleep") if os.path.exists(p))

@pytest.fixture
def banco(monkeypatch):
    """PATH de mentira (solo los shims) + DISPLAY + sin override de env."""
    bin_dir = tempfile.mkdtemp(prefix="jarvis_shims_")
    monkeypatch.setenv("PATH", bin_dir)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("JARVIS_SELECTOR_NATIVO", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    return bin_dir


def shim(bin_dir, nombre, stdout="", codigo=0, stderr="", dormir=0, python=None):
    """Crea un ejecutable `nombre` que guarda sus argumentos en <nombre>.args."""
    ruta = os.path.join(bin_dir, nombre)
    args_log = os.path.join(bin_dir, nombre + ".args")
    if python:
        cuerpo = f"#!{sys.executable}\nimport sys\nopen({args_log!r},'a').write('\\0'.join(sys.argv[1:])+'\\n')\n{python}\n"
    else:
        cuerpo = (f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{args_log}'\n"
                  + (f"{_SLEEP} {dormir}\n" if dormir else "")   # PATH de mentira: ruta absoluta
                  + (f"printf '%s' '{stdout}'\n" if stdout else "")
                  + (f"printf '%s' '{stderr}' >&2\n" if stderr else "")
                  + f"exit {codigo}\n")
    with open(ruta, "w") as f:
        f.write(cuerpo)
    os.chmod(ruta, os.stat(ruta).st_mode | stat.S_IXUSR)
    return args_log


def args_de(log):
    with open(log) as f:
        return f.read()


@pytest.fixture
def carpeta():
    return tempfile.mkdtemp(prefix="jarvis_elegida_")


# ── comandos puros ─────────────────────────────────────────────────────────

def test_cmd_zenity_con_y_sin_inicio():
    c = sn.cmd_zenity("/home/u/proyectos", "Elegí")
    assert c[:3] == ["zenity", "--file-selection", "--directory"]
    assert c[c.index("--title") + 1] == "Elegí"
    assert c[c.index("--filename") + 1] == "/home/u/proyectos/"
    assert "--filename" not in sn.cmd_zenity(None, "x")


def test_cmd_kdialog():
    c = sn.cmd_kdialog("/home/u", "T")
    assert c == ["kdialog", "--title", "T", "--getexistingdirectory", "/home/u"]


def test_cmd_osascript_escapa_comillas_y_barras():
    c = sn.cmd_osascript('/Users/ana/"mi" proj\\x', 'Elegí "la" carpeta')
    assert c[:2] == ["osascript", "-e"]
    script = c[2]
    assert 'prompt "Elegí \\"la\\" carpeta"' in script
    assert 'POSIX file "/Users/ana/\\"mi\\" proj\\\\x"' in script
    assert "choose folder" in script and "POSIX path of _f" in script
    assert "default location" not in sn.cmd_osascript(None, "T")[2]


def test_script_powershell_escapa_y_codifica():
    s = sn.script_powershell(r"\\wsl.localhost\Ubuntu\home\o'brien", "T'x", "Marca")
    assert "o''brien" in s and "T''x" in s          # comillas simples duplicadas
    assert "InitialDirectory" in s and "ValidateNames = $false" in s
    assert "InitialDirectory" not in sn.script_powershell(None, "T", "M")
    c = sn.cmd_powershell("powershell.exe", s)
    assert c[0] == "powershell.exe" and "-STA" in c and "-NoProfile" in c
    assert base64.b64decode(c[c.index("-EncodedCommand") + 1]).decode("utf-16-le") == s


# ── entorno y motor ────────────────────────────────────────────────────────

def test_entorno(monkeypatch):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.delenv("WSL_INTEROP", raising=False)
    monkeypatch.setattr(sn, "open", lambda *a, **k: __import__("io").StringIO("Linux version 5.15 generic"), raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    assert sn.entorno() == "linux"
    monkeypatch.setattr(sn, "open", lambda *a, **k: __import__("io").StringIO("Linux 5.15.90.1-microsoft-standard-WSL2"), raising=False)
    assert sn.entorno() == "wsl"
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setattr(sn, "open", lambda *a, **k: (_ for _ in ()).throw(OSError()), raising=False)
    assert sn.entorno() == "wsl"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert sn.entorno() == "macos"


def test_motor_linux(banco, monkeypatch):
    monkeypatch.setattr(sn, "entorno", lambda: "linux")
    assert sn.motor() == (None, "falta_herramienta")
    assert sn.ayuda_instalacion() and "zenity" in sn.ayuda_instalacion()
    shim(banco, "kdialog")
    assert sn.motor() == ("kdialog", None)
    shim(banco, "zenity")
    assert sn.motor() == ("zenity", None)           # zenity gana a kdialog
    assert sn.ayuda_instalacion() is None
    monkeypatch.delenv("DISPLAY")
    assert sn.motor() == (None, "sin_pantalla")


def test_motor_macos_wsl_y_apagado(banco, monkeypatch):
    monkeypatch.setattr(sn, "entorno", lambda: "macos")
    assert sn.motor() == (None, "falta_herramienta")
    shim(banco, "osascript")
    assert sn.motor() == ("osascript", None)
    monkeypatch.setattr(sn, "entorno", lambda: "wsl")
    monkeypatch.setattr(sn, "_RUTA_POWERSHELL", "/no/existe")
    assert sn.motor() == (None, "sin_powershell")
    shim(banco, "powershell.exe")
    assert sn.motor() == (None, "falta_herramienta")        # falta wslpath
    shim(banco, "wslpath")
    assert sn.motor() == ("powershell", None)
    monkeypatch.setenv("JARVIS_SELECTOR_NATIVO", "off")
    assert sn.motor() == (None, "desactivado")
    monkeypatch.delenv("JARVIS_SELECTOR_NATIVO")
    monkeypatch.setattr(sn, "entorno", lambda: "windows")
    assert sn.motor() == (None, "no_soportado")


def test_inicio_valido_sube_al_ancestro_que_existe(carpeta):
    assert sn._inicio_valido(carpeta) == carpeta
    assert sn._inicio_valido(os.path.join(carpeta, "no", "existe", "aun")) == carpeta
    assert sn._inicio_valido("relativa/x") is None
    assert sn._inicio_valido("") is None


# ── motores con shims ──────────────────────────────────────────────────────

def test_zenity_devuelve_la_ruta(banco, carpeta, monkeypatch):
    monkeypatch.setattr(sn, "entorno", lambda: "linux")
    log = shim(banco, "zenity", stdout=carpeta + "\n")
    r = sn.elegir_carpeta(os.path.join(carpeta, "base-nueva"), "Mi título")
    assert r == {"ruta": carpeta, "cancelado": False, "aviso": None, "motor": "zenity"}
    a = args_de(log)
    assert "--directory" in a and "Mi título" in a
    assert f"--filename {carpeta}/" in a              # arrancó en el ancestro que existe


def test_cancelar_no_es_error(banco, monkeypatch):
    monkeypatch.setattr(sn, "entorno", lambda: "linux")
    shim(banco, "zenity", codigo=1)
    r = sn.elegir_carpeta()
    assert r["ruta"] is None and r["cancelado"] is True


def test_error_del_selector(banco, monkeypatch):
    monkeypatch.setattr(sn, "entorno", lambda: "linux")
    shim(banco, "zenity", codigo=2, stderr="Gtk-WARNING: cannot open display")
    with pytest.raises(sn.ErrorSelector, match="cannot open display"):
        sn.elegir_carpeta()


def test_ruta_inexistente_es_error(banco, monkeypatch):
    monkeypatch.setattr(sn, "entorno", lambda: "linux")
    shim(banco, "zenity", stdout="/no/existe/de/verdad")
    with pytest.raises(sn.ErrorSelector):
        sn.elegir_carpeta()


def test_timeout_cuenta_como_cancelado(banco, monkeypatch):
    monkeypatch.setattr(sn, "entorno", lambda: "linux")
    monkeypatch.setattr(sn, "TIMEOUT", 0.4)
    shim(banco, "zenity", dormir=3, stdout="/tmp")
    assert sn.elegir_carpeta()["cancelado"] is True


def test_osascript_quita_la_barra_final_y_detecta_cancelacion(banco, carpeta, monkeypatch):
    monkeypatch.setattr(sn, "entorno", lambda: "macos")
    shim(banco, "osascript", stdout=carpeta + "/\n")
    assert sn.elegir_carpeta()["ruta"] == carpeta
    shim(banco, "osascript", codigo=1, stderr="execution error: User canceled. (-128)")
    assert sn.elegir_carpeta()["cancelado"] is True


def test_wsl_traduce_rutas_y_arranca_en_el_home_de_linux(banco, carpeta, monkeypatch):
    monkeypatch.setattr(sn, "entorno", lambda: "wsl")
    monkeypatch.setattr(sn, "_RUTA_POWERSHELL", "/no/existe")
    # powershell.exe: imprime la ruta UNC; wslpath: -w → UNC, -u → la carpeta real del test
    ps_log = shim(banco, "powershell.exe", stdout=r"\\wsl.localhost\Ubuntu\home\ana\proy")
    shim(banco, "wslpath", python=(
        "if sys.argv[1] == '-w':\n"
        "    print(r'\\\\wsl.localhost\\Ubuntu' + sys.argv[2].replace('/', '\\\\'))\n"
        f"else:\n    print({carpeta!r})\n"))
    r = sn.elegir_carpeta(carpeta)
    assert r["ruta"] == carpeta and r["motor"] == "powershell" and r["aviso"] is None
    # el script que viaja a PowerShell arranca en la UNC del home de Linux
    enc = args_de(ps_log).split("\0")[-1].strip().split()[-1]
    assert "wsl.localhost" in base64.b64decode(enc).decode("utf-16-le")


def test_wsl_avisa_si_la_carpeta_esta_en_windows(banco, monkeypatch):
    monkeypatch.setattr(sn, "entorno", lambda: "wsl")
    monkeypatch.setattr(sn, "_RUTA_POWERSHELL", "/no/existe")
    shim(banco, "powershell.exe", stdout=r"C:\Users\ana\proy")
    shim(banco, "wslpath", python="print('/mnt/c/Users/ana/proy' if sys.argv[1]=='-u' else r'\\\\wsl.localhost\\U\\h')")
    real = os.path.isdir
    monkeypatch.setattr(sn.os.path, "isdir", lambda p: True if p == "/mnt/c/Users/ana/proy" else real(p))
    r = sn.elegir_carpeta()
    assert r["ruta"] == "/mnt/c/Users/ana/proy" and r["aviso"] == "mnt"


def test_un_solo_selector_a_la_vez(banco, monkeypatch):
    monkeypatch.setattr(sn, "entorno", lambda: "linux")
    shim(banco, "zenity")
    assert sn._lock.acquire(blocking=False)
    try:
        with pytest.raises(sn.Ocupado):
            sn.elegir_carpeta()
    finally:
        sn._lock.release()


def test_sin_motor_lanza_no_disponible(banco, monkeypatch):
    monkeypatch.setattr(sn, "entorno", lambda: "linux")
    with pytest.raises(sn.NoDisponible) as e:
        sn.elegir_carpeta()
    assert e.value.razon == "falta_herramienta"


# ── endpoints ──────────────────────────────────────────────────────────────

@pytest.fixture
def cli():
    with TestClient(app) as c:
        yield c


REMOTO = {"host": "192.168.1.50:3000"}


def test_endpoint_nativo(cli, monkeypatch):
    monkeypatch.setattr(sn, "motor", lambda: ("zenity", None))
    d = cli.get("/api/fs/nativo").json()
    assert d["disponible"] is True and d["motor"] == "zenity" and d["razon"] is None
    monkeypatch.setattr(sn, "motor", lambda: (None, "falta_herramienta"))
    d = cli.get("/api/fs/nativo").json()
    assert d["disponible"] is False and d["razon"] == "falta_herramienta"


def test_endpoint_nativo_desde_otra_maquina_no_ofrece_el_dialogo(cli, monkeypatch):
    monkeypatch.setattr(sn, "motor", lambda: ("zenity", None))
    d = cli.get("/api/fs/nativo", headers=REMOTO).json()
    assert d["disponible"] is False and d["razon"] == "remoto"


def test_endpoint_elegir_ok_y_parametros(cli, monkeypatch):
    visto = {}
    def falso(inicio, titulo):
        visto.update(inicio=inicio, titulo=titulo)
        return {"ruta": "/tmp/x", "cancelado": False, "aviso": None, "motor": "zenity"}
    monkeypatch.setattr(sn, "elegir_carpeta", falso)
    r = cli.post("/api/fs/elegir", json={"inicio": "/home/u", "titulo": "  Hola "})
    assert r.status_code == 200 and r.json()["ruta"] == "/tmp/x"
    assert visto == {"inicio": "/home/u", "titulo": "Hola"}
    cli.post("/api/fs/elegir", json={})
    assert visto == {"inicio": None, "titulo": None}


def test_endpoint_elegir_errores(cli, monkeypatch):
    def lanza(exc):
        def f(*a):
            raise exc
        return f
    monkeypatch.setattr(sn, "elegir_carpeta", lanza(sn.NoDisponible("sin_pantalla")))
    r = cli.post("/api/fs/elegir", json={})
    assert r.status_code == 503 and r.json()["detail"] == "sin_pantalla"
    monkeypatch.setattr(sn, "elegir_carpeta", lanza(sn.Ocupado()))
    assert cli.post("/api/fs/elegir", json={}).status_code == 409
    monkeypatch.setattr(sn, "elegir_carpeta", lanza(sn.ErrorSelector("boom")))
    r = cli.post("/api/fs/elegir", json={})
    assert r.status_code == 500 and r.json()["detail"] == "boom"


def test_endpoint_elegir_desde_otra_maquina_se_rechaza(cli, monkeypatch):
    llamado = []
    monkeypatch.setattr(sn, "elegir_carpeta", lambda *a: llamado.append(1))
    r = cli.post("/api/fs/elegir", json={}, headers=REMOTO)
    assert r.status_code == 403 and not llamado
