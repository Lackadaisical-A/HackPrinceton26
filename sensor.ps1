param(
    [ValidateSet('help', 'test', 'live', 'record', 'viewer', 'sessions', 'upload', 'ports', 'haptic-test', 'motion-test', 'motor', 'dashboard', 'wearable-test', 'wearable-robot', 'sim', 'robot-check', 'robot')]
    [string]$Command = 'help',
    [string]$Session,
    [string]$Port = 'COM7',
    [string]$Sensor = 'arm',
    [ValidateRange(1, 2)]
    [int]$Expected = 1,
    [double]$Threshold = 5,
    [int]$PulseMs = 200,
    [int]$Strength = 64,
    [ValidateSet(1, 2)]
    [int]$Motor = 2,
    [ValidateRange(0.1, 3600)]
    [double]$Seconds = 2,
    [double]$FullScale = 45,
    [int]$MinStrength = 30,
    [ValidateSet('neutral', 'gesture')]
    [string]$Mode = 'neutral',
    [string]$RobotPort,
    [ValidateSet('shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper')]
    [string]$Joint = 'shoulder_pan',
    [switch]$ReverseLoad,
    [ValidateSet('x', 'y')]
    [string]$JoystickAxis = 'y',
    [switch]$ReverseClaw,
    [string]$S3Port = 'COM8',
    [switch]$Demo
)

$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    switch ($Command) {
        { $_ -in 'robot', 'robot-check' } {
            $robotArgs = @('--joint', $Joint)
            if ($RobotPort) { $robotArgs += @('--port', $RobotPort) }
            if ($ReverseLoad) { $robotArgs += '--reverse-load' }
            $robotArgs += @('--joystick-axis', $JoystickAxis)
            if ($ReverseClaw) { $robotArgs += '--reverse-claw' }
            if ($Command -eq 'robot-check') { $robotArgs += '--check' }
            uv run --python 3.12 robot_control/run.py @robotArgs
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        }
        'sim' { uv run --python 3.12 so101_sim/sim.py }
        'dashboard' { uv run bno085_reader/dashboard.py }
        { $_ -in 'wearable-test', 'wearable-robot' } {
            $wearableArgs = @('--wroom', $Port, '--s3', $S3Port)
            if ($Demo) { $wearableArgs += '--demo' }
            if ($Command -eq 'wearable-robot' -and -not $RobotPort) { $RobotPort = 'COM6' }
            if ($RobotPort) { $wearableArgs += @('--robot-port', $RobotPort) }
            uv run wearable_test/run.py @wearableArgs
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        }
        'motion-test' { uv run bno085_reader/motion_haptics.py --port $Port --mode $Mode --threshold-deg $Threshold --pulse-ms $PulseMs --strength $Strength --full-scale-deg $FullScale --min-strength $MinStrength }
        'motor' { uv run bno085_reader/motor_control.py --port $Port --motor $Motor --strength $Strength --seconds $Seconds }
        'haptic-test' { uv run bno085_reader/haptic_smoke.py --port $Port --expected $Expected }
        'ports' { uv run --with pyserial python -m serial.tools.list_ports -v }
        'test' { uv run bno085_reader/bridge.py --test }
        'live' { uv run bno085_reader/bridge.py --port $Port --sensor $Sensor }
        'record' { uv run bno085_reader/bridge.py --port $Port --sensor $Sensor --record }
        'viewer' { uv run bno085_reader/viewer.py }
        'sessions' {
            if (Test-Path -LiteralPath recordings) {
                Get-ChildItem -LiteralPath recordings -Directory | Sort-Object Name -Descending | ForEach-Object {
                    $outcomeFile = Join-Path $_.FullName 'outcome.json'
                    $count = if (Test-Path -LiteralPath $outcomeFile) {
                        (Get-Content -LiteralPath $outcomeFile -Raw | ConvertFrom-Json).samples
                    } else { 'unfinished' }
                    [PSCustomObject]@{ Session = $_.Name; Samples = $count }
                } | Format-Table -AutoSize
            } else { Write-Output 'No recordings yet.' }
        }
        'upload' {
            if (-not $Session) { throw 'Specify a session: .\sensor.ps1 upload SESSION_NAME' }
            uv run bno085_reader/upload_recording.py $Session
        }
        'help' {
            @'
.\sensor.ps1 test                    Test Firebase write/read
.\sensor.ps1 dashboard               View all 3 IMUs and publish COM7 + COM8 to Firebase
.\sensor.ps1 wearable-test           Local 3D arm + all four coins + hand joystick
                                    Close dashboard/robot/Serial Monitor first.
                                    Capture straight-forward pose A, then arm-down pose B.
                                    After calibration: Enable SO-101 URDF preview.
                                    Joystick Y: 0=max/open, 1810=half, 4095=min/closed.
                                    Optional: -Port COM7 -S3Port COM8; -Demo for synthetic data.
.\sensor.ps1 wearable-robot -RobotPort COM6
                                    Same calibrated Wearable Lab mapping -> physical SO-101.
                                    Connects read-only. Recenter, then enable arm/claw separately.
                                    Esc / Stop holds robot and stops wearable motors.
.\sensor.ps1 sim                     SO-101 URDF simulation (keep dashboard running)
.\sensor.ps1 robot-check             Detect arm + read six motors (no robot writes)
.\sensor.ps1 robot                   All 3 IMUs + motor-load vibration + optional arm control
                                    Optional: -RobotPort COM6 -Joint shoulder_pan -ReverseLoad
                                    Close the old dashboard first; robot includes its own.
                                    Joystick claw: release stick, enable position control (neutral=half open).
                                    Optional: -JoystickAxis x -ReverseClaw
.\sensor.ps1 ports                   List connected COM ports
.\sensor.ps1 haptic-test -Port COM7  Pulse driver and check IMU stream
.\sensor.ps1 motion-test -Port COM7 Continuous vibration proportional to tilt from neutral
.\sensor.ps1 motor -Motor 2 -Strength 128 -Seconds 3  Second coin: GPIO15 PWM, 0..255
.\sensor.ps1 motor -Motor 1 -Strength 64 -Seconds 3   First coin: DRV2605L, 0..127
                                    Add -Port COM8 for either motor on the ESP32-S3.
                                    Close dashboard/robot/Serial Monitor first; Ctrl+C stops.
.\sensor.ps1 live                    Stream latest pose to Firebase
.\sensor.ps1 record                  Stream AND record locally
.\sensor.ps1 sessions                List local recordings
.\sensor.ps1 upload SESSION_NAME     Upload a completed recording
.\sensor.ps1 viewer                  Open USB 3D viewer

Options for live/record: -Port COM7 -Sensor arm
Options for motion-test: -Threshold 5 -FullScale 45 -Strength 64 -MinStrength 30
Previous gesture pulses: -Mode gesture -Threshold 8 -PulseMs 200
Ctrl+C stops streaming/recording. Disconnect the USB viewer before streaming.
Live pose: /live/sensors/<sensor>
Uploaded sessions: /recordings/<session>
The viewer reads USB directly; it does not read Firebase or saved sessions.
'@ | Write-Output
        }
    }
} finally {
    Pop-Location
}
