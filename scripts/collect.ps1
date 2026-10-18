# AIoT Predictive Safety System - Data collection helper (Windows)
param(
    [Parameter(Mandatory = $true)][string]$Label,
    [string]$Duration = ""
)

# Parse the duration with invariant culture so "2.5" works regardless of the
# OS locale (comma-decimal locales would otherwise fail to cast the param).
$durationSec = 0.0
if (-not [string]::IsNullOrWhiteSpace($Duration)) {
    if (-not [double]::TryParse($Duration, [Globalization.CultureInfo]::InvariantCulture, [ref]$durationSec)) {
        Write-Error "Invalid -Duration '$Duration'. Use a number like 30 or 2.5."; exit 1
    }
}

$root = Split-Path -Parent $PSScriptRoot
$mlDir = Join-Path $root "ml_service"
$py = Join-Path $mlDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $py)) {
    Write-Error "ML venv missing. Run scripts\setup_python.ps1 first."; exit 1
}

Push-Location $mlDir
if ($durationSec -gt 0) {
    & $py collect_data.py --label $Label --duration $durationSec.ToString([Globalization.CultureInfo]::InvariantCulture)
} else {
    & $py collect_data.py --label $Label
}
Pop-Location