# Uninstall: remove the autostart shortcut, the daily task, and stop the panel.
# ASCII-only on purpose (see install.ps1).
$ErrorActionPreference = 'Continue'

$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup 'Desktop-Agenda.lnk'
$taskName = 'DesktopAgenda-DailyMerge'

Write-Host '[1/3] Removing autostart shortcut...'
if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host '    removed' } else { Write-Host '    not present' }

Write-Host '[2/3] Removing scheduled task...'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $task) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host '    removed'
} else {
    Write-Host '    not present'
}

Write-Host '[3/3] Stopping the running panel...'
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*desktop-agenda*main.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "    stopped $($_.ProcessId)" }

Write-Host ''
Write-Host 'Uninstall done (agenda data remains in the data directory).'
