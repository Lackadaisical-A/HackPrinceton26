import type { CalibrationAction, CalibrationMessage } from '../../telemetry/contracts'

export const CALIBRATION_FRESH_MS = 1500

export function calibrationAvailable(message: CalibrationMessage | null, ageMs: number, connected: boolean, wallMs = Date.now()): boolean {
  if (!message || !connected) return false
  const age = wallMs - message.t * 1000
  return ageMs >= 0 && ageMs < CALIBRATION_FRESH_MS && age >= -250 && age < CALIBRATION_FRESH_MS
}

export function canCalibrate(action: CalibrationAction, message: CalibrationMessage | null, available: boolean, pending: boolean, trial: boolean): boolean {
  if (!available || !message) return false
  // Stop remains available during a trial, a capture or stale sensor input.
  if (action === 'cancel') return true
  if (pending || trial || !message.input_fresh || !message.sensors.every(Boolean)) return false
  if (message.phase === 'preparing' || message.phase === 'sampling') return false
  if (action === 'capture_b') return message.phase === 'needs_pose_b'
  if (action === 'recenter') return message.can_recenter
  if (action === 'capture_pan_left' || action === 'capture_pan_right') return message.phase === 'ready' && message.pan_capture === true
  if (action === 'capture_elbow' || action === 'capture_shoulder') return message.phase === 'ready' && message.range_capture === true
  return true
}
