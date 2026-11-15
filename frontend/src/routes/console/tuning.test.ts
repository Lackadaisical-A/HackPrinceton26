import { describe, expect, it } from 'vitest'
import { validateCalibration } from '../../telemetry/contracts'

const message = () => ({
  t: 123, session: 'test', revision: 0, phase: 'ready', capture_pose: null, remaining_s: 0,
  input_fresh: true, sensors: [true, true, true], can_recenter: true, detail: 'Ready', request: null,
  tuning: { values: { speed_deg_s: 240, claw_speed_per_s: 2, max_load: 650, gripper_max_load: 350, load_stops_enabled: false },
    revision: 1, applied_revision: 0, connected: true, fresh: true, enabled: false, request: null },
  slider: { referenced: false, enabled: false, pos: null, target: null, detail: 'Reference slider', request: null },
})

describe('confirmed robot tuning and slider state', () => {
  it('accepts queued settings and an unreferenced slider', () => {
    expect(validateCalibration(message())).toBeNull()
  })
  it.each([
    ['speed_deg_s', 361], ['claw_speed_per_s', 0], ['max_load', 1024], ['gripper_max_load', -1],
    ['load_stops_enabled', 0], ['max_load', NaN],
  ])('rejects invalid %s values', (key, value) => {
    const m = message()
    Object.assign(m.tuning.values, { [key]: value })
    expect(validateCalibration(m)).not.toBeNull()
  })
  it('rejects impossible acknowledgment ordering and out-of-range slider positions', () => {
    const m = message()
    m.tuning.applied_revision = 2
    expect(validateCalibration(m)).not.toBeNull()
    const s = message()
    Object.assign(s.slider, { pos: 101 })
    expect(validateCalibration(s)).not.toBeNull()
  })
  it('accepts replay progress and rejects malformed recording metadata', () => {
    const replay = { phase: 'returning', revision: 3, duration_s: 2, elapsed_s: 0, samples: 101,
      includes_slider: true, detail: 'Returning to the first pose', request: null }
    expect(validateCalibration({ ...message(), replay })).toBeNull()
    for (const changes of [{ phase: 'unknown' }, { duration_s: 121 }, { elapsed_s: -1 },
      { samples: 1.5 }, { includes_slider: 1 }, { revision: -1 }, { request: { id: 'a', ok: true } }]) {
      expect(validateCalibration({ ...message(), replay: { ...replay, ...changes } })).not.toBeNull()
    }
  })
  it('validates grasp settings and status while retaining older controller support', () => {
    const m = message()
    expect(validateCalibration(m)).toBeNull()
    Object.assign(m.tuning.values, { gripper_contact_load: 180, gripper_hold_load: 120 })
    Object.assign(m.tuning, { grasp: { holding: true, load: 120, detail: 'Holding object' } })
    expect(validateCalibration(m)).toBeNull()
    Object.assign(m.tuning.values, { gripper_hold_load: 200 })
    expect(validateCalibration(m)).not.toBeNull()
    Object.assign(m.tuning.values, { gripper_hold_load: 120 })
    for (const grasp of [{ holding: 'yes', load: 120, detail: '' }, { holding: true, load: NaN, detail: '' }]) {
      Object.assign(m.tuning, { grasp })
      expect(validateCalibration(m)).not.toBeNull()
    }
  })
})
