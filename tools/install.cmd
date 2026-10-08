@echo off
rem Install autostart + daily merge task (per-user, no admin needed).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
