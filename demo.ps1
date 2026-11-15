param(
    [ValidateSet('pots', 'leslider', 'imu')][string]$Controller = 'leslider',
    [string]$WroomPort = 'COM7',
    [string]$S3Port = 'COM8',
    [string]$LeaderPort = 'COM9',
    [string]$RobotPort = 'COM6',
    [string]$CameraSource = 'wrist',
    [string]$VisionConfig,
    [switch]$DryRun,
    [switch]$Check,
    [switch]$ServicesOnly,
    [switch]$NoBrowser,
    [string[]]$ControllerOptions = @()
)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    $demoArgs = @('demo', '--controller', $Controller, '--leader-port', $LeaderPort,
                  '--robot-port', $RobotPort, '--source', $CameraSource)
    if ($VisionConfig) { $demoArgs += @('--vision-config', $VisionConfig) }
    if ($Controller -eq 'imu') { $demoArgs += @('--wroom-port', $WroomPort, '--s3-port', $S3Port) }
    if ($DryRun) { $demoArgs += '--dry-run' }
    if ($Check) { $demoArgs += '--check' }
    if ($ServicesOnly) { $demoArgs += '--services-only' }
    if ($NoBrowser) { $demoArgs += '--no-browser' }
    if ($ControllerOptions.Count) { $demoArgs += @('--') + $ControllerOptions }
    & uv run --python 3.12 leslider_control/run.py @demoArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
