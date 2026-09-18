@echo off
REM ── Jarvis — THE entry door from Windows (post-native-app) ──
REM
REM Double click and done: if the server is not running it starts it inside
REM WSL (scripts/reiniciar-server.sh) and then opens the workspace as an APP
REM in Chrome (clean window, no tab bar or omnibox).
REM
REM Save this .bat on Windows (e.g. the Desktop) and double click it.
REM
REM Paths (do not hardcode someone else's machine):
REM   - Repo in WSL: $HOME/jarvis-workspace  (override: JARVIS_WSL_DIR)
REM   - Distro: the WSL default              (override: JARVIS_WSL_DISTRO)
REM
REM Details:
REM   - Health is checked via 127.0.0.1 (the NAME localhost resolves ::1
REM     first and WSL does not listen there), but the window opens via localhost:
REM     Chrome falls back to IPv4 on its own, and Radio needs that origin for YouTube.
REM   - Kiosk variant (full fullscreen, exit with Alt+F4): comment
REM     the normal start line and uncomment the --kiosk one.

setlocal EnableExtensions
set "URL=http://localhost:3000"
set "HEALTH=http://127.0.0.1:3000/api/health"
set intentos=0

if defined JARVIS_WSL_DISTRO (
  set "WSL=wsl.exe -d %JARVIS_WSL_DISTRO%"
) else (
  set "WSL=wsl.exe"
)

curl -s -o NUL --max-time 2 %HEALTH% && goto abrir

echo Starting Jarvis in WSL...
REM Warm-up first: if the distro is COLD (Windows just rebooted, or a
REM `wsl --shutdown`), the first wsl.exe is spent entirely booting it and the command
REM below would be lost. And the log goes to the repo's data/, NOT to /tmp: /tmp in WSL
REM is tmpfs and is wiped on every boot of the distro — exactly what you
REM need to read when this fails.
%WSL% -- true
if errorlevel 1 (
  echo Could not talk to WSL. Install a distro ^(wsl --install^) and reboot.
  pause
  exit /b 1
)

REM Repo = JARVIS_WSL_DIR, or $HOME/jarvis-workspace inside the distro.
REM Windows forwards JARVIS_WSL_DIR to the WSL environment if it is set.
REM (no nested quotes: cmd.exe does not escape \" like bash)
%WSL% -- bash -lc "REPO=${JARVIS_WSL_DIR:-$HOME/jarvis-workspace}; test -f $REPO/scripts/reiniciar-server.sh || exit 42; cd $REPO && mkdir -p data && setsid nohup bash scripts/reiniciar-server.sh >>data/lanzador.log 2>&1 </dev/null & exit 0"
if errorlevel 42 goto sin_repo
if errorlevel 1 goto fallo

:esperar
curl -s -o NUL --max-time 2 %HEALTH% && goto abrir
set /a intentos+=1
if %intentos% geq 90 goto fallo
timeout /t 1 /nobreak >NUL
goto esperar

:abrir
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" (
  start "" %URL%
  exit /b 0
)
start "" "%CHROME%" --app=%URL% --new-window
REM start "" "%CHROME%" --kiosk %URL% --new-window
exit /b 0

:sin_repo
echo I cannot find Jarvis Workspace inside WSL.
echo Clone it there ^(public repo name^):
echo   git clone https://github.com/celsiusm/jarvis-workspace.git ~/jarvis-workspace
echo If it is already in another path, set the JARVIS_WSL_DIR environment variable
echo to that Linux path ^(e.g. /home/you/my-apps/jarvis-workspace^).
pause
exit /b 1

:fallo
echo I could not start the server after 90s. Try manually inside WSL:
echo   bash ~/jarvis-workspace/scripts/reiniciar-server.sh
echo (attempt log: ~/jarvis-workspace/data/lanzador.log)
echo Repo in another path? set JARVIS_WSL_DIR. Another distro? set JARVIS_WSL_DISTRO.
pause
exit /b 1
