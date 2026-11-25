# Builds dist\NeoTerm.exe (single file, no console window) with the license texts bundled inside.
# Usage:  .\build.ps1            (uses the Python on PATH, or set $env:PYTHON to another python.exe)

# Success is judged by exit codes: Windows PowerShell 5.1 would treat harmless stderr output
# (e.g. pip's update notice) as a terminating error under $ErrorActionPreference = "Stop".
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$py = if ($env:PYTHON) { $env:PYTHON } else { "python" }

function Invoke-Python($what) {
    $global:LASTEXITCODE = 1                       # stays non-zero if python could not even start
    & $py @args
    if (-not $? -or $LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

$exe = Join-Path $PSScriptRoot "dist\NeoTerm.exe"
if (Test-Path $exe) { Remove-Item $exe -ErrorAction Stop }   # never report an old exe as built

Invoke-Python "pip install" -m pip install -r requirements.txt
Invoke-Python "PyInstaller" -m PyInstaller --noconfirm --onefile --windowed --name NeoTerm `
    --icon neoterm.ico --version-file version_info.txt --collect-data wcwidth --collect-all winpty `
    --add-data "LICENSE;." --add-data "THIRD_PARTY_NOTICES.md;." --add-data "licenses;licenses" `
    neoterm.py

if (-not (Test-Path $exe)) { throw "Build finished but $exe was not created" }
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash
Write-Host "Built: $exe"
Write-Host "SHA-256: $hash"
