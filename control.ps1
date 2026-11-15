param(
    [ValidateSet('help', 'ports', 'pots', 'leslider', 'slider', 'calibrate-slider', 'test')]
    [string]$Command = 'help',
    [string]$LeaderPort,
    [string]$RobotPort = 'COM6',
    [switch]$DryRun,
    [switch]$Recalibrate,
    [switch]$Options
)

$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    if ($Command -eq 'help') {
        @'
.\control.ps1 ports
.\control.ps1 pots -LeaderPort COM9 -DryRun      Six-pot input check; no robot connection
.\control.ps1 pots -LeaderPort COM9             Six pots -> SO-101 on COM6
.\control.ps1 leslider -LeaderPort COM9 -DryRun  Seven-pot input check; no robot connection
.\control.ps1 leslider -LeaderPort COM9         Seven pots -> SO-101 + LeSlider ID 7
.\control.ps1 slider                           ID-7-only position test window
.\control.ps1 calibrate-slider                 Manually capture slider endpoints
.\control.ps1 test                             Copied hardware-free regression tests

COM9 is an example: use the actual port of the ESP32 with the pot sketch.
Options: -RobotPort COM6, -Recalibrate (pots), -Options (full controller help).
Close other programs using the leader or robot port before controlling hardware.
Sketches and wiring: leslider_control\README.md
'@ | Write-Output
        return
    }
    $controllerArgs = @($Command)
    if ($Options) {
        $controllerArgs += '--help'
    } elseif ($Command -in 'pots', 'leslider') {
        if (-not $LeaderPort) { throw 'Specify -LeaderPort for the ESP32 running the potentiometer sketch.' }
        if ($LeaderPort -eq $RobotPort) { throw 'LeaderPort and RobotPort must be different.' }
        $controllerArgs += @('--leader-port', $LeaderPort, '--robot-port', $RobotPort)
        if ($DryRun) { $controllerArgs += '--dry-run' }
        if ($Recalibrate) { $controllerArgs += '--recalibrate' }
    } elseif ($Command -in 'slider', 'calibrate-slider') {
        if ($DryRun -or $Recalibrate) { throw 'DryRun and Recalibrate apply to pots/leslider only.' }
        $controllerArgs += @('--port', $RobotPort)
    }
    $uvArgs = @('run', '--python', '3.12')
    if ($Command -eq 'test') { $uvArgs += @('--with', 'pytest') }
    & uv @uvArgs leslider_control/run.py @controllerArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
