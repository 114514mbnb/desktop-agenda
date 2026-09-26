@echo off
rem Launch the desktop-agenda client: control console + desktop panel.
rem Uses the runtime bundled in this project, so it does not depend on any other
rem tool's Python installation. ASCII-only on purpose (cmd reads .cmd as GBK).
setlocal
set ROOT=%~dp0
set PYW=%ROOT%runtime\pythonw.exe
if not exist "%PYW%" set PYW=pythonw
start "" "%PYW%" -B "%ROOT%main.py" --client --start-panel %*
endlocal
