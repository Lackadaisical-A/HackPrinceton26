import { describe, expect, it } from 'vitest'
import { easeGlow, GLOW_FLOOR, glowLook, glowTarget, HALO_SCALE_MAX, HALO_SCALE_MIN } from './loadGlow'

describe('joint load glow', () => {
  it('uses the magnitude of signed load, clamped, and 0 without a reading', () => {
    expect(glowTarget([0.2, -0.6, 3], 1)).toBeCloseTo(0.6)
    expect(glowTarget([0.2, -0.6, 3], 2)).toBe(1)
    expect(glowTarget(null, 0)).toBe(0)
    expect(glowTarget([Number.NaN], 0)).toBe(0)
  })

  it('eases toward the target and settles', () => {
    const step = easeGlow(0, 1, 1 / 30)
    expect(step).toBeGreaterThan(0)
    expect(step).toBeLessThan(1)
    let v = 0
    for (let i = 0; i < 6; i++) v = easeGlow(v, 1, 1 / 30)
    expect(v).toBeGreaterThan(0.95)
    for (let i = 0; i < 30; i++) v = easeGlow(v, 0, 1 / 30)
    expect(v).toBe(0)
  })

  it('hides small loads and grows the halo with load', () => {
    expect(glowLook(GLOW_FLOOR / 2).strength).toBe(0)
    expect(glowLook(1)).toEqual({ strength: 1, scale: HALO_SCALE_MAX })
    expect(glowLook(GLOW_FLOOR).scale).toBe(HALO_SCALE_MIN)
  })
})
