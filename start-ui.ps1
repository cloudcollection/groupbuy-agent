param([int]$Port = 8787, [string]$Config = 'examples/config.synthetic.json')
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
if ([IO.Path]::GetPathRoot($projectRoot) -ne 'D:\') { throw 'Place the project on D:.' }
if (-not $env:GB_TEMP_DIR) { $env:GB_TEMP_DIR = Join-Path $projectRoot 'runtime/tmp' }
if ([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($env:GB_TEMP_DIR)) -ne 'D:\') { throw 'GB_TEMP_DIR must be on D:.' }
New-Item -ItemType Directory -Force -Path $env:GB_TEMP_DIR | Out-Null
$env:TEMP = $env:GB_TEMP_DIR
$env:TMP = $env:GB_TEMP_DIR
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:PIP_CACHE_DIR = Join-Path $env:GB_TEMP_DIR 'pip-cache'
$env:XDG_CACHE_HOME = Join-Path $env:GB_TEMP_DIR 'cache'
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run ./setup.ps1 -WithUI first.' }
Push-Location $projectRoot
try {
    Write-Host "Open http://127.0.0.1:$Port in a browser. Keep this terminal running."
    & $pythonPath -B -m groupbuy --config $Config dashboard --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Dashboard stopped; check the controlled error code.' }
} finally { Pop-Location }
