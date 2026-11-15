import { describe, expect, it } from 'vitest'
import { AIM_DEFAULT, AIM_LIMIT } from '../config'
import { clampAim, describeAim, isDefaultAim, sameAim } from './aim'

describe('clampAim', () => {
  it('keeps a point inside the picture as it is, to 3 decimals', () => {
    expect(clampAim({ x: 0.62, y: 0.58 })).toEqual({ x: 0.62, y: 0.58 })
    expect(clampAim({ x: 0.123456, y: 0.987654 * 0.5 })).toEqual({ x: 0.123, y: 0.494 })
  })

  it('keeps the point AIM_LIMIT away from every edge', () => {
    expect(clampAim({ x: -3, y: 0 })).toEqual({ x: AIM_LIMIT, y: AIM_LIMIT })
    expect(clampAim({ x: 1, y: 7 })).toEqual({ x: 1 - AIM_LIMIT, y: 1 - AIM_LIMIT })
  })

  it('turns anything that is not two finite numbers into the default', () => {
    for (const value of [undefined, null, 0.5, 'x', [0.5, 0.5], { x: 0.5 }, { x: Infinity, y: 0.5 }, { x: 0.5, y: '1' }]) {
      expect(clampAim(value)).toEqual(AIM_DEFAULT)
    }
  })

  it('returns a copy, never the shared default', () => {
    expect(clampAim(null)).not.toBe(AIM_DEFAULT)
  })
})

describe('sameAim and isDefaultAim', () => {
  it('ignore differences smaller than what is sent', () => {
    expect(sameAim({ x: 0.5, y: 0.5 }, { x: 0.5004, y: 0.4996 })).toBe(true)
    expect(sameAim({ x: 0.5, y: 0.5 }, { x: 0.501, y: 0.5 })).toBe(false)
    expect(isDefaultAim({ ...AIM_DEFAULT })).toBe(true)
    expect(isDefaultAim({ x: 0.5, y: 0.6 })).toBe(false)
  })
})

describe('describeAim', () => {
  it('reads as whole percentages of the picture', () => {
    expect(describeAim({ x: 0.523, y: 0.61 })).toBe('x 52 %, y 61 %')
  })
})
