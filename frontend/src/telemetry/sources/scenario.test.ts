import { describe, expect, it } from 'vitest'
import { DETECTOR_CLASSES, JOINTS, LIMITS } from '../../config'
import { PHASES } from '../contracts'
import type { Phase } from '../contracts'
import { CYCLE_S, PHASE_STARTS, scenarioAt, SQUEEZE_AT_S, summaryFor } from './scenario'

/**
 * A squeeze has to clear the limit by more than the 0.012 of noise the simulator adds to each
 * pad, or the console's "Grip near sensor limit" would appear on some squeezes and not others.
 */
const CLEARS_LIMIT = LIMITS.gripCaution + 0.02

/** Every instant of the given cycle, at the pressure stream's 50 Hz. */
function* instants(cycle: number, stepS = 0.02): Generator<number> {
  const steps = Math.round(CYCLE_S / stepS)
  for (let i = 0; i < steps; i++) yield cycle * CYCLE_S + i * stepS
}

function peakGrip(cycle: number): number {
  let peak = 0
  for (const time of instants(cycle)) peak = Math.max(peak, scenarioAt(time).grip)
  return peak
}

describe('scenarioAt', () => {
  it('returns the same frame for the same instant', () => {
    for (const time of [0, 2.4, 7.31, 18.6, 54.6, 1234.567]) {
      expect(scenarioAt(time)).toEqual(scenarioAt(time))
    }
    const options = { preferred: 'box', sinceSqueezeS: 0.4 }
    expect(scenarioAt(56.2, options)).toEqual(scenarioAt(56.2, options))
  })

  it('numbers the cycles from zero', () => {
    expect(scenarioAt(0).cycle).toBe(0)
    expect(scenarioAt(CYCLE_S - 0.01).cycle).toBe(0)
    expect(scenarioAt(CYCLE_S).cycle).toBe(1)
    expect(scenarioAt(CYCLE_S * 5 + 3).cycle).toBe(5)
  })

  it('is in the expected phase at known times', () => {
    const expected: [number, Phase][] = [
      [0, 'search'],
      [2.79, 'search'],
      [2.8, 'approach'],
      [10.99, 'approach'],
      [11, 'grasp'],
      [13.99, 'grasp'],
      [14, 'carry'],
      [22.99, 'carry'],
      [23, 'place'],
      [26.99, 'place'],
      [27, 'release'],
      [31.19, 'release'],
      [31.2, 'search'],
      [35.99, 'search'],
    ]
    for (const [time, phase] of expected) expect(scenarioAt(time).phase, `t = ${time}`).toBe(phase)
  })

  it('starts each phase where PHASE_STARTS says', () => {
    for (const phase of PHASES) expect(scenarioAt(PHASE_STARTS[phase]).phase).toBe(phase)
  })

  it('goes through the same phases in every cycle', () => {
    const mid: [number, Phase][] = [
      [1, 'search'],
      [7, 'approach'],
      [12.5, 'grasp'],
      [18, 'carry'],
      [25, 'place'],
      [29, 'release'],
      [34, 'search'],
    ]
    for (const cycle of [1, 2, 7]) {
      for (const [t, phase] of mid) expect(scenarioAt(cycle * CYCLE_S + t).phase, `cycle ${cycle}, t = ${t}`).toBe(phase)
    }
  })

  it('has no target, then an unsure sighting, then a lock early in a cycle', () => {
    const hidden = scenarioAt(1)
    expect(hidden.status).toBe('no_target')
    expect(hidden.box).toBeNull()

    const unsure = scenarioAt(2.4)
    expect(unsure.status).toBe('low_confidence')
    expect(unsure.phase).toBe('search')
    expect(unsure.box).not.toBeNull()
    expect(unsure.confidence).toBeLessThan(0.5)

    const locked = scenarioAt(3)
    expect(locked.status).toBe('ok')
    expect(locked.phase).toBe('approach')
    expect(locked.confidence).toBeGreaterThan(0.5)

    expect(scenarioAt(33).status).toBe('no_target')
  })

  it('never reports a status other than no_target without an object in view', () => {
    for (const cycle of [0, 1]) {
      for (const time of instants(cycle)) {
        const frame = scenarioAt(time)
        expect(frame.status === 'no_target', `time = ${time}`).toBe(frame.box === null)
      }
    }
  })

  it('cycles through the detector classes unless one is preferred', () => {
    DETECTOR_CLASSES.forEach((label, cycle) => {
      expect(scenarioAt(cycle * CYCLE_S + 5).label).toBe(label)
    })
    expect(scenarioAt(DETECTOR_CLASSES.length * CYCLE_S + 5).label).toBe(DETECTOR_CLASSES[0])
    expect(scenarioAt(5, { preferred: 'box' }).label).toBe('box')
    expect(scenarioAt(CYCLE_S + 5, { preferred: 'box' }).label).toBe('box')
    expect(scenarioAt(CYCLE_S + 5, { preferred: null }).label).toBe(DETECTOR_CLASSES[1])
  })

  it('moves from far to in reach during the approach', () => {
    expect(scenarioAt(3).distance).toBe('far')
    expect(scenarioAt(10.9).distance).toBe('in_reach')
    expect(scenarioAt(18).distance).toBe('in_reach')
  })

  it('holds the object from the grasp until the release', () => {
    expect(scenarioAt(5).held).toBe(false)
    expect(scenarioAt(5).grip).toBe(0)
    expect(scenarioAt(12.6).held).toBe(true)
    expect(scenarioAt(20).held).toBe(true)
    expect(scenarioAt(20).grip).toBeGreaterThan(0.4)
    expect(scenarioAt(27.6).held).toBe(false)
    expect(scenarioAt(30).grip).toBe(0)
  })

  it('squeezes past the grip limit on every odd cycle', () => {
    // Cycles 9, 13 and 29 are ones where the hand's wobble is at its lowest during the squeeze.
    for (let cycle = 1; cycle < 80; cycle += 2) {
      expect(peakGrip(cycle), `cycle ${cycle}`).toBeGreaterThanOrEqual(CLEARS_LIMIT)
    }
  })

  it('reaches the limit only around the scripted squeeze', () => {
    for (const cycle of [1, 3, 9]) {
      for (const time of instants(cycle)) {
        if (scenarioAt(time).grip < LIMITS.gripCaution) continue
        expect(Math.abs(time - cycle * CYCLE_S - SQUEEZE_AT_S), `time = ${time}`).toBeLessThan(1)
      }
    }
  })

  it('stays under the grip limit on even cycles', () => {
    for (const cycle of [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20]) {
      expect(peakGrip(cycle), `cycle ${cycle}`).toBeLessThan(LIMITS.gripCaution - 0.1)
    }
  })

  it('squeezes past the limit on request, but only while something is held', () => {
    // The simulator samples the squeeze as it rises, so look at the peak over its three seconds.
    const peakOf = (time: number): number => {
      let peak = 0
      for (let s = 0; s <= 3; s += 0.02) peak = Math.max(peak, scenarioAt(time + s, { sinceSqueezeS: s }).grip)
      return peak
    }
    // Asked for at any point from the grasp to just before the release, on cycles with no
    // scripted squeeze.
    for (let cycle = 0; cycle < 60; cycle += 2) {
      for (const t of [12.6, 13.5, 15, 17, 19, 21, 23, 25, 26.5]) {
        expect(peakOf(cycle * CYCLE_S + t), `cycle ${cycle}, t = ${t}`).toBeGreaterThanOrEqual(CLEARS_LIMIT)
      }
    }
    // Nothing in the gripper: no grip at all.
    expect(peakOf(4)).toBe(0)
    expect(peakOf(32)).toBe(0)
    // No request: the option changes nothing.
    expect(scenarioAt(18, { sinceSqueezeS: null })).toEqual(scenarioAt(18))
  })

  it('never reports a grip above 1, even when both squeezes coincide', () => {
    const at = CYCLE_S + SQUEEZE_AT_S
    for (let s = 0; s <= 1.2; s += 0.02) {
      expect(scenarioAt(at - 0.6 + s, { sinceSqueezeS: s }).grip).toBeLessThanOrEqual(1)
    }
  })

  it('keeps every value in range over whole cycles', () => {
    for (const cycle of [0, 1, 2, 3]) {
      for (const time of instants(cycle)) {
        const frame = scenarioAt(time)
        const at = `time = ${time}`

        if (frame.box) {
          const [x1, y1, x2, y2] = frame.box
          expect(x1, at).toBeGreaterThanOrEqual(0)
          expect(y1, at).toBeGreaterThanOrEqual(0)
          expect(x2, at).toBeLessThanOrEqual(1)
          expect(y2, at).toBeLessThanOrEqual(1)
          expect(x1, at).toBeLessThan(x2)
          expect(y1, at).toBeLessThan(y2)
        }
        expect(frame.confidence, at).toBeGreaterThanOrEqual(0)
        expect(frame.confidence, at).toBeLessThanOrEqual(1)
        expect(Math.abs(frame.dx), at).toBeLessThanOrEqual(1)
        expect(Math.abs(frame.dy), at).toBeLessThanOrEqual(1)

        expect(frame.grip, at).toBeGreaterThanOrEqual(0)
        expect(frame.grip, at).toBeLessThanOrEqual(1)

        expect(frame.joints, at).toHaveLength(JOINTS.length)
        expect(frame.loads, at).toHaveLength(JOINTS.length)
        expect(frame.joints.every(Number.isFinite), at).toBe(true)
        for (const load of frame.loads) expect(Math.abs(load), at).toBeLessThanOrEqual(1)
        // The gripper is reported as 0 (closed) to 100 (open).
        expect(frame.joints[5], at).toBeGreaterThanOrEqual(0)
        expect(frame.joints[5], at).toBeLessThanOrEqual(100)

        for (const angle of [frame.roll, frame.pitch, frame.yaw]) {
          expect(Math.abs(angle), at).toBeLessThan(Math.PI / 2)
        }
      }
    }
    // Tens of thousands of checks: 1.6 s on an idle computer, over the default 5 s while vision is
    // running on it.
  }, 30_000)
})

describe('summaryFor', () => {
  it('says where the target is, in the vision module’s wording', () => {
    expect(summaryFor('cup', 'near', -0.12, 0.05)).toBe('Cup near, slightly left.')
    expect(summaryFor('bottle', 'far', 0.5, 0)).toBe('Bottle ahead, right.')
    expect(summaryFor('box', 'in_reach', 0, 0)).toBe('Box in reach, centered.')
  })

  it('names both directions when both are off centre', () => {
    expect(summaryFor('cup', 'near', -0.5, 0.2)).toBe('Cup near, left and slightly down.')
    expect(summaryFor('cup', 'far', 0.3, -0.6)).toBe('Cup ahead, slightly right and up.')
  })

  it('treats 0.1 as centred and 0.35 as slight', () => {
    expect(summaryFor('cup', 'near', 0.1, -0.1)).toBe('Cup near, centered.')
    expect(summaryFor('cup', 'near', 0.11, 0)).toBe('Cup near, slightly right.')
    expect(summaryFor('cup', 'near', 0.35, 0)).toBe('Cup near, slightly right.')
    expect(summaryFor('cup', 'near', 0.36, 0)).toBe('Cup near, right.')
  })

  it('capitalises only the first letter of the label', () => {
    expect(summaryFor('pill bottle', 'near', 0, 0.2)).toBe('Pill bottle near, slightly down.')
  })
})
