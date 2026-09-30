@echo off
rem Start only the desktop panel (no console), explicitly. Uses the bundled runtime.
setlocal
set ROOT=%~dp0
set PYW=%ROOT%runtime\pythonw.exe
if not exist "%PYW%" set PYW=pythonw
start "" "%PYW%" -B "%ROOT%main.py" --panel %*
endlocal
