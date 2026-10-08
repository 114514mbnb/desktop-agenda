@echo off
rem Remove autostart + daily task and stop the panel.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
echo.
pause
