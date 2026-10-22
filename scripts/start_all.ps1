# AIoT Predictive Safety System - Run ML inference + Dashboard (Windows)
# Start the broker first: scripts\start_mosquitto.ps1 (Mosquitto or bundled fallback).
# Assumes a trained model exists (see ml_service/train_model.py).

$root = Split-Path -Parent $PSScriptRoot
$mlDir = Join-Path $root "ml_service"
$dashDir = Join-Path $root "dashboard"

$pyMl = Join-Path $mlDir ".venv\Scripts\python.exe"
$pyDash = Join-Path $dashDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pyMl)) {
    Write-Error "ML venv missing. Run scripts\setup_python.ps1 first."; exit 1
}
if (-not (Test-Path -LiteralPath $pyDash)) {
    Write-Error "Dashboard venv missing. Run scripts\setup_python.ps1 first."; exit 1
}

Write-Host "Launching ML inference service..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$mlDir'; & '$pyMl' infer_realtime.py"

Write-Host "Starting dashboard on http://localhost:5000 ..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$dashDir'; & '$pyDash' app.py"

Write-Host "Done. Open http://localhost:5000" -ForegroundColor Green