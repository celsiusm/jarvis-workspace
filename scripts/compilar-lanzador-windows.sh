#!/usr/bin/env bash
# Compiles Jarvis.exe — the thin desktop SHELL (scripts/jarvis-shell.cs) —
# and leaves it on the real Windows user's Desktop.
#
# App with its own window (WebView2)
# that shows the workspace living on Linux, with its "Open
# workspace" screen. NOTHING native inside (no motor, no Rust): the lesson of
# 2026-08-06 is that the mess started when the app wanted to be more than a
# window.
#
# The exe resolves the repo in WSL at runtime — it does not bake in paths from this machine:
#   $HOME/jarvis-workspace   (override: JARVIS_WSL_DIR)
#   default WSL distro       (override: JARVIS_WSL_DISTRO)
#
# Toolchain: ONLY the csc.exe that Windows ships (.NET Framework 4)
# + the 3 DLLs from the WebView2 SDK, which are downloaded from NuGet to data/ (gitignored)
# the first time and stay EMBEDDED in the exe — a single file, without an
# installer. The WebView2 runtime already comes with Windows 10/11.
set -euo pipefail
cd "$(dirname "$0")"

CSC='/mnt/c/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe'
[ -x "$CSC" ] || { echo "cannot find csc.exe (Windows without .NET Framework 4?)" >&2; exit 1; }

# ── WebView2 SDK (cached in data/, gitignored) ──
SDK="../data/webview2-sdk"
WV2_VERSION='1.0.2210.55'
if [ ! -f "$SDK/WebView2Loader.dll" ]; then
  echo "downloading the WebView2 SDK ${WV2_VERSION} from NuGet…"
  mkdir -p "$SDK"
  curl -sL -o "$SDK/wv2.nupkg" \
    "https://www.nuget.org/api/v2/package/Microsoft.Web.WebView2/${WV2_VERSION}"
  python3 - "$SDK" <<'PY'
import sys, zipfile
sdk = sys.argv[1]
z = zipfile.ZipFile(sdk + '/wv2.nupkg')
for n in ('lib/net45/Microsoft.Web.WebView2.Core.dll',
          'lib/net45/Microsoft.Web.WebView2.WinForms.dll',
          'runtimes/win-x64/native/WebView2Loader.dll'):
    open(sdk + '/' + n.split('/')[-1], 'wb').write(z.read(n))
PY
fi

WINUSER=$(/mnt/c/Windows/System32/cmd.exe /c "echo %USERNAME%" 2>/dev/null | tr -d '\r\n')
# The REAL Desktop comes from the registry (it may be redirected to D:/OneDrive —
# on this machine it lives in D:\Users\USER\Desktop, not in C:).
DESKTOP_WIN=$(/mnt/c/Windows/System32/reg.exe query \
    'HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders' \
    /v Desktop 2>/dev/null | grep -o '[A-Z]:\\.*' | tr -d '\r')
DESTINO=$(wslpath "${DESKTOP_WIN:-C:\\Users\\${WINUSER}\\Desktop}")
TMP="/mnt/c/Users/${WINUSER}/AppData/Local/Temp/jarvis-shell-build"

mkdir -p "$TMP"
cp jarvis-shell.cs jarvis.ico "$TMP/"
cp jarvis-shell-ui.html "$TMP/ui.html"      # the usual splash (text resource)
cp "$SDK/Microsoft.Web.WebView2.Core.dll" \
   "$SDK/Microsoft.Web.WebView2.WinForms.dll" \
   "$SDK/WebView2Loader.dll" "$TMP/"

# System.Web.Extensions = JavaScriptSerializer (comes with the Framework, goes to the
# GAC — there is no DLL to ship): used by the Discord Rich Presence to
# parse/build the IPC JSON.
( cd "$TMP" && "$CSC" /nologo /target:winexe /win32icon:jarvis.ico \
    /r:System.Windows.Forms.dll /r:System.Drawing.dll \
    /r:System.Web.Extensions.dll \
    /r:Microsoft.Web.WebView2.Core.dll /r:Microsoft.Web.WebView2.WinForms.dll \
    /resource:jarvis.ico /resource:ui.html \
    /out:Jarvis.exe jarvis-shell.cs )

# ── Installation: the exe and its DLLs together, like any app (and like the
# old app was). The DLLs are NOT embedded: an unsigned exe that writes them to
# disk at startup triggers Defender's dropper heuristic
# (Trojan:Win32/Sabsik.FL.A!ml — happened on 2026-08-06 with the "single
# file" version). On the Desktop goes a shortcut with the icon, which is what
# the user opens.
APP="/mnt/c/Users/${WINUSER}/AppData/Local/Jarvis"
mkdir -p "$APP"

# Installation tolerant of "the app is OPEN". Windows does not let you overwrite an
# exe/DLL in use (cp → "Permission denied"), but it DOES let you rename it. So:
# if the copy clashes, we rename the old one and copy over it. The open window
# keeps working against the renamed file and the next double click already
# picks up the new one. Without this, compiling with Jarvis open aborted midway
# through the installation and left the app mixed (new exe with old DLLs, or nothing).
rm -f "$APP"/*.viejo 2>/dev/null || true      # leftovers from a previous installation

# The source is validated BEFORE touching anything in the destination. Learned the hard way
# (2026-08-07): if the freshly compiled exe turned out ILLEGIBLE —Defender flags it
# as Trojan:Win32/Sabsik.FL.A!ml, a classic false positive against new unsigned .NET
# binaries— and one has already renamed the old one, the user is left WITHOUT an
# app and with a broken shortcut. First we verify, then we swap.
legible() { head -c 2 "$1" >/dev/null 2>&1; }

instalar() {
  local origen="$1" destino="$2/$(basename "$1")"
  if ! legible "$origen"; then
    echo "ABORT: '$origen' cannot be read (did Defender flag it?). I will not touch the current installation." >&2
    return 1
  fi
  cp "$origen" "$destino" 2>/dev/null && return 0
  # Windows does not let you OVERWRITE what is in use, but it does let you rename it.
  mv -f "$destino" "${destino}.viejo" 2>/dev/null || true
  if ! cp "$origen" "$destino" 2>/dev/null; then
    mv -f "${destino}.viejo" "$destino" 2>/dev/null || true   # leave it as it was
    echo "ABORT: could not copy '$origen'. Restored the previous installation." >&2
    return 1
  fi
}
for f in "$TMP/Jarvis.exe" "$TMP/jarvis.ico" \
         "$SDK/Microsoft.Web.WebView2.Core.dll" \
         "$SDK/Microsoft.Web.WebView2.WinForms.dll" \
         "$SDK/WebView2Loader.dll"; do
  instalar "$f" "$APP"
done
rm -rf "$TMP"

# Desktop shortcut (app name + icon).
APP_WIN="C:\\Users\\${WINUSER}\\AppData\\Local\\Jarvis"
DESTINO_WIN=$(wslpath -w "$DESTINO")
PS1="$APP/crear-acceso.ps1"
cat > "$PS1" <<PSEOF
\$s = (New-Object -ComObject WScript.Shell).CreateShortcut('${DESTINO_WIN}\\Jarvis.lnk')
\$s.TargetPath = '${APP_WIN}\\Jarvis.exe'
\$s.IconLocation = '${APP_WIN}\\jarvis.ico,0'
\$s.WorkingDirectory = '${APP_WIN}'
\$s.Description = 'Jarvis Workspace'
\$s.Save()
PSEOF
/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe \
    -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w "$PS1")" >/dev/null 2>&1 || true
rm -f "$PS1"

echo "done: ${APP}/Jarvis.exe"
echo "       shortcut → ${DESTINO}/Jarvis.lnk"
