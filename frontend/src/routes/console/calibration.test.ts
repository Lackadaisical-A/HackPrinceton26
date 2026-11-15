import { describe, expect, it } from 'vitest'
import { calibrationAvailable, canCalibrate } from './calibration'
import { type CalibrationMessage, validateCalibration } from '../../telemetry/contracts'

const state: CalibrationMessage = {
  t: 100, session: 'controller', revision: 0, phase: 'needs_pose_a', capture_pose: null,
  remaining_s: 0, input_fresh: true, sensors: [true, true, true], can_recenter: false,
  detail: 'Capture A.', request: null,
}

describe('calibration controls', () => {
  it('requires both fresh arrival and a fresh controller timestamp', () => {
    expect(calibrationAvailable(state, 10, true, 100010)).toBe(true)
    expect(calibrationAvailable(state, 2000, true, 100010)).toBe(false)
    expect(calibrationAvailable(state, 0, true, 105000)).toBe(false)
    expect(calibrationAvailable(state, 0, true, 99000)).toBe(false)
    expect(calibrationAvailable(state, 0, false, 100010)).toBe(false)
    expect(calibrationAvailable(null, 0, true, 100010)).toBe(false)
  })

  it('requires A before B and saved mounting before recenter', () => {
    expect(canCalibrate('capture_a', state, true, false, false)).toBe(true)
    expect(canCalibrate('capture_b', state, true, false, false)).toBe(false)
    expect(canCalibrate('recenter', state, true, false, false)).toBe(false)
    expect(canCalibrate('capture_b', { ...state, phase: 'needs_pose_b' }, true, false, false)).toBe(true)
    expect(canCalibrate('recenter', { ...state, can_recenter: true, phase: 'needs_recenter' }, true, false, false)).toBe(true)
    expect(canCalibrate('capture_elbow', { ...state, range_capture: true }, true, false, false)).toBe(false)
    expect(canCalibrate('capture_shoulder', { ...state, phase: 'ready' }, true, false, false)).toBe(false)
    expect(canCalibrate('capture_elbow', { ...state, phase: 'ready', range_capture: true }, true, false, false)).toBe(true)
    expect(canCalibrate('capture_shoulder', { ...state, phase: 'ready', range_capture: true }, true, false, false)).toBe(true)
    expect(canCalibrate('capture_pan_left', { ...state, phase: 'ready', range_capture: true }, true, false, false)).toBe(false)
    expect(canCalibrate('capture_pan_left', { ...state, phase: 'ready', pan_capture: true }, true, false, false)).toBe(true)
    expect(canCalibrate('capture_pan_right', { ...state, phase: 'ready', pan_capture: true }, true, false, false)).toBe(true)
  })

  it('blocks stale sensors, trials, duplicate clicks and active captures but allows stop', () => {
    for (const [message, pending, trial] of [
      [{ ...state, input_fresh: false }, false, false],
      [{ ...state, sensors: [true, false, true] }, false, false],
      [state, true, false], [state, false, true],
      [{ ...state, phase: 'sampling', capture_pose: 'A' }, false, false],
    ] as [CalibrationMessage, boolean, boolean][]) {
      expect(canCalibrate('capture_a', message, true, pending, trial)).toBe(false)
      expect(canCalibrate('capture_pan_left', { ...message, pan_capture: true }, true, pending, trial)).toBe(false)
      expect(canCalibrate('cancel', message, true, pending, trial)).toBe(true)
    }
    expect(canCalibrate('cancel', state, false, false, false)).toBe(false)
  })

  it('rejects malformed or inconsistent status instead of enabling controls', () => {
    expect(validateCalibration(state)).toBeNull()
    for (const patch of [
      { sensors: [true] }, { phase: 'success' }, { revision: -1 }, { remaining_s: NaN },
      { phase: 'sampling' }, { capture_pose: 'A' }, { input_fresh: 1 },
      { request: { id: 'r', ok: 'yes', detail: 'ok' } }, { session: '' },
    ]) expect(validateCalibration({ ...state, ...patch })).not.toBeNull()
  })
  it('validates pan ranges and saved config status', () => {
    expect(validateCalibration({ ...state, pan_capture: true, motion_ranges: { pan_left_deg: 40, pan_right_deg: 30 },
      saved_config: { path: 'wearable_test/calibration.local.json', state: 'loaded', detail: 'Recenter in pose A.' } })).toBeNull()
    expect(validateCalibration({ ...state, phase: 'preparing', capture_pose: 'E', remaining_s: 3 })).toBeNull()
    for (const patch of [{ motion_ranges: { pan_left_deg: 14 } }, { motion_ranges: { pan_right_deg: 151 } },
      { motion_ranges: { pan_left_deg: NaN } }, { pan_capture: 1 }, { saved_config: { path: 'x', state: 'ok', detail: '' } }]) {
      expect(validateCalibration({ ...state, ...patch })).not.toBeNull()
    }
  })
})
