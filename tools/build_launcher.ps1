param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$project = (Resolve-Path $ProjectRoot).Path
$source = Join-Path $project "tools\DesktopAgendaLauncher.cs"
$icon = Join-Path $project "data\agenda.ico"
if (-not $OutputPath) {
    $OutputPath = Join-Path $project "DesktopAgenda.exe"
}

$compiler = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $compiler)) {
    $compiler = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
}
if (-not (Test-Path $compiler)) {
    throw "Microsoft C# compiler was not found."
}
if (-not (Test-Path $source)) {
    throw "Launcher source was not found: $source"
}
if (-not (Test-Path $icon)) {
    throw "Application icon was not found: $icon"
}

$outputDirectory = Split-Path -Parent $OutputPath
if ($outputDirectory -and -not (Test-Path $outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
}

& $compiler /nologo /target:winexe /optimize+ /platform:anycpu `
    /reference:System.dll /reference:System.Windows.Forms.dll `
    "/win32icon:$icon" "/out:$OutputPath" $source
if ($LASTEXITCODE -ne 0) {
    throw "Launcher compilation failed with exit code $LASTEXITCODE."
}

Write-Output $OutputPath
