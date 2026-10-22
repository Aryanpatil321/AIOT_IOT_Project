# AIoT Predictive Safety System - MQTT broker launcher (local dev)
#
# Prefers Eclipse Mosquitto when installed. If Mosquitto is not available
# (e.g. no admin rights, or mosquitto.org unreachable on this network), it
# falls back to the bundled pure-Python MQTT 3.1.1 broker (amqtt) which serves
# the same listeners as mosquitto/mosquitto.conf (TCP 1883 + WS 9001).

param(
    [string]$ConfigPath = "$PSScriptRoot\..\mosquitto\mosquitto.conf",
    [switch]$ForceFallback,
    [switch]$Restart
)

$resolved = (Resolve-Path -LiteralPath $ConfigPath).Path

# Refuse to start a second broker: the TCP port is the single source of truth.
$existing = Get-NetTCPConnection -LocalPort 1883 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($existing -and -not $Restart) {
    $proc = Get-Process -Id $existing.OwningProcess -ErrorAction SilentlyContinue
    Write-Host ("An MQTT broker is already listening on port 1883 (PID {0}, {1})." -f `
        $existing.OwningProcess, $proc.ProcessName) -ForegroundColor Yellow
    Write-Host "Nothing to do - it is already running." -ForegroundColor Green
    Write-Host 'To restart it:  .\scripts\start_mosquitto.ps1 -Restart' -ForegroundColor Gray
    exit 0
}
if ($existing -and $Restart) {
    Write-Host ("Stopping broker on port 1883 (PID {0}) ..." -f $existing.OwningProcess) -ForegroundColor Yellow
    Stop-Process -Id $existing.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 800
}

if (-not $ForceFallback -and (Get-Command mosquitto -ErrorAction SilentlyContinue)) {
    Write-Host "Starting Eclipse Mosquitto with config: $resolved" -ForegroundColor Cyan
    & mosquitto -c $resolved -v
    exit $LASTEXITCODE
}

Write-Host "Eclipse Mosquitto not found - using the bundled Python broker (amqtt)." -ForegroundColor Yellow

# Pick an interpreter that already has amqtt; otherwise use the first available
# and install it there.
$pyCandidates = @(
    "$PSScriptRoot\..\ml_service\.venv\Scripts\python.exe",
    "$PSScriptRoot\..\dashboard\.venv\Scripts\python.exe"
)
$cmd = Get-Command python -ErrorAction SilentlyContinue
if ($cmd) { $pyCandidates += $cmd.Source }

$python = $null
$needInstall = $false
foreach ($candidate in $pyCandidates) {
    if (-not (Test-Path -LiteralPath $candidate)) { continue }
    & $candidate -c "import amqtt" 2>$null
    if ($LASTEXITCODE -eq 0) { $python = $candidate; $needInstall = $false; break }
    if (-not $python) { $python = $candidate; $needInstall = $true }
}
if (-not $python) {
    Write-Error "No Python interpreter found. Install Python, then: pip install amqtt"
    exit 1
}

if ($needInstall) {
    Write-Host "Installing the amqtt broker into $python ..." -ForegroundColor Cyan
    & $python -m pip install --quiet amqtt
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Could not install amqtt. Run manually: pip install amqtt"
        exit 1
    }
}

Write-Host "Starting local MQTT broker on port 1883 (config: $resolved)" -ForegroundColor Cyan
& $python "$PSScriptRoot\mqtt_broker.py"
