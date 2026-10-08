@echo off
rem Open the quick-entry window: paste QQ group notices and they join the agenda.
rem Uses the runtime bundled in this project, so it does not depend on any other
rem Python installation. ASCII-only on purpose (cmd reads .cmd as GBK).
setlocal
set ROOT=%~dp0
set PYW=%ROOT%runtime\pythonw.exe
if not exist "%PYW%" set PYW=pythonw
start "" "%PYW%" -B "%ROOT%main.py" --paste
endlocal
