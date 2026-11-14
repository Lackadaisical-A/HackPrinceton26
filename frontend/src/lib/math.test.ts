import { describe, expect, it } from 'vitest'
import type { Quat, Vec3 } from './math'
import { clamp, DEG, eulerFromQuat, lerp, median, quatFromEuler, ramp, rotate, smoothstep } from './math'

const expectVec = (actual: Vec3, expected: Vec3): void => {
  for (let i = 0; i < 3; i++) expect(actual[i], `component ${i}`).toBeCloseTo(expected[i], 12)
}

describe('clamp, lerp, smoothstep and ramp', () => {
  it('clamp holds a value inside its range', () => {
    expect(clamp(-2, 0, 1)).toBe(0)
    expect(clamp(0.4, 0, 1)).toBe(0.4)
    expect(clamp(7, 0, 1)).toBe(1)
  })

  it('lerp goes from the first value to the second', () => {
    expect(lerp(10, 20, 0)).toBe(10)
    expect(lerp(10, 20, 0.25)).toBe(12.5)
    expect(lerp(10, 20, 1)).toBe(20)
  })

  it('smoothstep eases from 0 to 1 and holds outside', () => {
    expect(smoothstep(-1)).toBe(0)
    expect(smoothstep(0)).toBe(0)
    expect(smoothstep(0.25)).toBe(0.15625)
    expect(smoothstep(0.5)).toBe(0.5)
    expect(smoothstep(1)).toBe(1)
    expect(smoothstep(3)).toBe(1)
  })

  it('ramp is 0 at or below the low end and 1 at or above the high end', () => {
    expect(ramp(5, 10, 20)).toBe(0)
    expect(ramp(10, 10, 20)).toBe(0)
    expect(ramp(15, 10, 20)).toBe(0.5)
    expect(ramp(20, 10, 20)).toBe(1)
    expect(ramp(99, 10, 20)).toBe(1)
  })
})

describe('quatFromEuler', () => {
  it('gives the identity for no rotation', () => {
    expect(quatFromEuler(0, 0, 0)).toEqual([1, 0, 0, 0])
  })

  it('puts a single-axis rotation on that axis', () => {
    const half = Math.SQRT1_2
    const roll = quatFromEuler(90 * DEG, 0, 0)
    const pitch = quatFromEuler(0, 90 * DEG, 0)
    const yaw = quatFromEuler(0, 0, 90 * DEG)
    ;[roll, pitch, yaw].forEach((q, axis) => {
      expect(q[0]).toBeCloseTo(half, 12)
      for (let i = 0; i < 3; i++) expect(q[1 + i]).toBeCloseTo(i === axis ? half : 0, 12)
    })
  })

  it('always returns a unit quaternion', () => {
    for (const [roll, pitch, yaw] of [
      [0.3, -0.7, 1.9],
      [-2.5, 1.2, -3],
      [3.1, -1.5, 0.01],
    ]) {
      expect(Math.hypot(...quatFromEuler(roll, pitch, yaw))).toBeCloseTo(1, 12)
    }
  })
})

describe('eulerFromQuat', () => {
  it('round-trips angles through a quaternion', () => {
    const angles = [-170, -90, -35, 0, 20, 90, 175]
    const pitches = [-80, -30, 0, 45, 85]
    for (const roll of angles) {
      for (const pitch of pitches) {
        for (const yaw of angles) {
          const back = eulerFromQuat(quatFromEuler(roll * DEG, pitch * DEG, yaw * DEG))
          const at = `roll ${roll}, pitch ${pitch}, yaw ${yaw}`
          expect(back.roll / DEG, at).toBeCloseTo(roll, 8)
          expect(back.pitch / DEG, at).toBeCloseTo(pitch, 8)
          expect(back.yaw / DEG, at).toBeCloseTo(yaw, 8)
        }
      }
    }
  })

  it('gives the same angles for an unnormalised quaternion', () => {
    const q = quatFromEuler(0.3, -0.4, 1.1)
    const scaled = q.map((v) => v * 3.7) as Quat
    const back = eulerFromQuat(scaled)
    expect(back.roll).toBeCloseTo(0.3, 12)
    expect(back.pitch).toBeCloseTo(-0.4, 12)
    expect(back.yaw).toBeCloseTo(1.1, 12)
  })

  it('gives the same angles for a quaternion and its negative', () => {
    const q = quatFromEuler(0.3, -0.4, 1.1)
    const negative = q.map((v) => -v) as Quat
    expect(eulerFromQuat(negative).yaw).toBeCloseTo(eulerFromQuat(q).yaw, 12)
    expect(eulerFromQuat(negative).roll).toBeCloseTo(eulerFromQuat(q).roll, 12)
  })

  it('does not return NaN at the pitch limit or for a zero quaternion', () => {
    const up = eulerFromQuat(quatFromEuler(0, 90 * DEG, 0))
    expect(up.pitch / DEG).toBeCloseTo(90, 5)
    expect(Number.isFinite(up.roll) && Number.isFinite(up.yaw)).toBe(true)

    const zero = eulerFromQuat([0, 0, 0, 0])
    expect([zero.roll, zero.pitch, zero.yaw].every(Number.isFinite)).toBe(true)
  })
})

describe('rotate', () => {
  it('leaves a vector alone under the identity', () => {
    expectVec(rotate([1, 0, 0, 0], [0.3, -2, 5], [0, 0, 0]), [0.3, -2, 5])
  })

  it('turns x to y under a quarter turn of yaw', () => {
    const q = quatFromEuler(0, 0, 90 * DEG)
    expectVec(rotate(q, [1, 0, 0], [0, 0, 0]), [0, 1, 0])
    expectVec(rotate(q, [0, 1, 0], [0, 0, 0]), [-1, 0, 0])
    expectVec(rotate(q, [0, 0, 1], [0, 0, 0]), [0, 0, 1])
  })

  it('turns y to z under a quarter turn of roll, and z to x under pitch', () => {
    expectVec(rotate(quatFromEuler(90 * DEG, 0, 0), [0, 1, 0], [0, 0, 0]), [0, 0, 1])
    expectVec(rotate(quatFromEuler(0, 90 * DEG, 0), [0, 0, 1], [0, 0, 0]), [1, 0, 0])
  })

  it('applies roll first, then pitch, then yaw', () => {
    // Roll a quarter turn (y to z), then yaw a quarter turn (which leaves z alone).
    expectVec(rotate(quatFromEuler(90 * DEG, 0, 90 * DEG), [0, 1, 0], [0, 0, 0]), [0, 0, 1])
    // The other order would have sent y to -x first, and roll leaves x alone.
    expectVec(rotate(quatFromEuler(90 * DEG, 0, 90 * DEG), [1, 0, 0], [0, 0, 0]), [0, 1, 0])
  })

  it('keeps the length of the vector', () => {
    const out = rotate(quatFromEuler(0.7, -0.4, 2.2), [1, 2, 3], [0, 0, 0])
    expect(Math.hypot(...out)).toBeCloseTo(Math.hypot(1, 2, 3), 12)
  })

  it('writes into the vector it is given and returns it', () => {
    const out: Vec3 = [9, 9, 9]
    const returned = rotate(quatFromEuler(0, 0, 90 * DEG), [1, 0, 0], out)
    expect(returned).toBe(out)
    expectVec(out, [0, 1, 0])
  })

  it('may write over its own input', () => {
    const v: Vec3 = [1, 0, 0]
    rotate(quatFromEuler(0, 0, 90 * DEG), v, v)
    expectVec(v, [0, 1, 0])
  })
})

describe('median', () => {
  it('is the middle value of an odd count', () => {
    expect(median([3, 1, 2])).toBe(2)
    expect(median([5])).toBe(5)
  })

  it('is the mean of the two middle values of an even count', () => {
    expect(median([4, 1, 3, 2])).toBe(2.5)
    expect(median([1, 2])).toBe(1.5)
  })

  it('sorts as numbers, not as text', () => {
    expect(median([10, 9, 100])).toBe(10)
    expect(median([-1, -10, 2])).toBe(-1)
  })

  it('does not reorder the list it is given', () => {
    const values = [3, 1, 2]
    median(values)
    expect(values).toEqual([3, 1, 2])
  })

  it('is NaN for an empty list', () => {
    expect(Number.isNaN(median([]))).toBe(true)
  })
})
