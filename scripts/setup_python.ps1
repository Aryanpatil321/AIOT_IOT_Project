# AIoT Predictive Safety System - Setup Python environments (Windows)

$root = Split-Path -Parent $PSScriptRoot
Set-StrictMode -Version Latest

function New-Venv([string]$Name, [string]$Requirements) {
    $venvPath = Join-Path $root $Name
    Write-Host "Creating virtualenv: $venvPath" -ForegroundColor Cyan
    python -m venv $venvPath
    $python = Join-Path $venvPath "Scripts\python.exe"
    & $python -m pip install --upgrade pip
    & $python -m pip install -r $Requirements
    Write-Host "Installed dependencies for $Name" -ForegroundColor Green
}

New-Venv "ml_service\.venv" (Join-Path $root "ml_service\requirements.txt")
New-Venv "dashboard\.venv" (Join-Path $root "dashboard\requirements.txt")

Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host "Activate ML env:        .\ml_service\.venv\Scripts\Activate.ps1"
Write-Host "Activate Dashboard env: .\dashboard\.venv\Scripts\Activate.ps1"