@echo off
rem Merge QQ notices from data\inbox into the agenda, then exit.
rem Used by the daily scheduled task (DesktopAgenda-DailyMerge).
setlocal
set ROOT=%~dp0
set PYTHON=%ROOT%runtime\python.exe
if not exist "%PYTHON%" set PYTHON=python
"%PYTHON%" -B "%ROOT%main.py" --once %*
endlocal
