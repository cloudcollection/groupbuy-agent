param([switch]$WithUI)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
if ([IO.Path]::GetPathRoot($projectRoot) -ne 'D:\') { throw 'Place the project on D:.' }
if (-not $env:GB_TEMP_DIR) { $env:GB_TEMP_DIR = Join-Path $projectRoot 'runtime/tmp' }
if ([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($env:GB_TEMP_DIR)) -ne 'D:\') { throw 'GB_TEMP_DIR must be on D:.' }
New-Item -ItemType Directory -Force -Path $env:GB_TEMP_DIR | Out-Null
$env:TEMP = $env:GB_TEMP_DIR
$env:TMP = $env:GB_TEMP_DIR
$env:PIP_CACHE_DIR = Join-Path $env:GB_TEMP_DIR 'pip-cache'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
Push-Location $projectRoot
try {
    python -B -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python venv failed.' }
    if ($WithUI) {
        ./.venv/Scripts/python.exe -B -m pip install --no-cache-dir '.[windows]'
        if ($LASTEXITCODE -ne 0) { throw 'UI dependency installation failed.' }
    }
    if (Test-Path -LiteralPath (Join-Path $projectRoot '.git')) {
        git config core.hooksPath .githooks
        if ($LASTEXITCODE -ne 0) { throw 'Git hook configuration failed.' }
    } else {
        Write-Host 'Source archive ready. Initialize Git and configure hooks before publication.'
    }
} finally { Pop-Location }
