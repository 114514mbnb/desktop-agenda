# Install: autostart shortcut + daily merge task (per-user, no admin required).
# Kept ASCII-only on purpose: Windows PowerShell 5.1 reads .ps1 as ANSI/GBK,
# and non-ASCII text inside string literals can break parsing.

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot          # tools\.. -> project root
$vbs = Join-Path $PSScriptRoot 'launch-panel.vbs'
$mainPy = Join-Path $root 'main.py'
$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup 'Desktop-Agenda.lnk'
$mergeCmd = Join-Path $root 'run-daily.cmd'
$taskName = 'DesktopAgenda-DailyMerge'

Write-Host '[1/3] Checking files...'
if (-not (Test-Path $vbs)) { throw "missing $vbs" }
if (-not (Test-Path $mainPy)) { throw "missing $mainPy" }

Write-Host '[2/3] Creating autostart shortcut...'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnk)
$shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
$shortcut.Arguments = '"' + $vbs + '"'
$shortcut.WorkingDirectory = $root
$shortcut.Description = 'Desktop Agenda panel (QQ group notices + timetable)'
$shortcut.Save()
if (-not (Test-Path $lnk)) { throw 'shortcut creation failed' }
Write-Host "    created: $lnk"

Write-Host '[3/3] Registering daily merge task (07:30)...'
try {
    $action = New-ScheduledTaskAction -Execute $mergeCmd -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Daily -At '07:30'
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
        -Description 'Merge QQ group notices from data\inbox into the desktop agenda' -Force | Out-Null
    Write-Host "    registered: $taskName"
} catch {
    Write-Host "    task registration failed (policy?): $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host '    run manually later:'
    Write-Host "      schtasks /create /f /tn `"$taskName`" /tr `"`"$mergeCmd`"`" /sc daily /st 07:30"
}

Write-Host ''
Write-Host 'Install done.'
Write-Host "  * the client starts automatically at logon (Startup: $startup)"
Write-Host '  * the panel also scans data\inbox every 15 minutes'
Write-Host '  * to undo: uninstall.cmd'
