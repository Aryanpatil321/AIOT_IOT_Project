# AIoT Predictive Safety System - Mosquitto broker launcher (local dev)

param(
    [string]$ConfigPath = "$PSScriptRoot\..\mosquitto\mosquitto.conf"
)

$resolved = (Resolve-Path -LiteralPath $ConfigPath).Path

if (-not (Get-Command mosquitto -ErrorAction SilentlyContinue)) {
    Write-Error "mosquitto not found in PATH. Install Eclipse Mosquitto first."
    exit 1
}

Write-Host "Starting Mosquitto broker with config: $resolved" -ForegroundColor Cyan
& mosquitto -c $resolved -v