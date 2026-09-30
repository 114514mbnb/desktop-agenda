@echo off
rem Desktop shortcut entry (the name is kept for compatibility).
rem
rem Rule set by the user: the FIRST launch opens ONLY the desktop panel.
rem Launching it again while the panel is already running brings up the console.
rem The console's other two doors are the panel's gear button and the tray icon.
rem
rem Uses the runtime bundled in this project, so it does not depend on any other
rem tool's Python installation. ASCII-only on purpose (cmd reads .cmd as GBK).
setlocal
set ROOT=%~dp0
set PYW=%ROOT%runtime\pythonw.exe
if not exist "%PYW%" set PYW=pythonw
start "" "%PYW%" -B "%ROOT%main.py" --shortcut %*
endlocal
