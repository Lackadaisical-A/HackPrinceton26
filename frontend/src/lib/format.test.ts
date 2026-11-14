import { describe, expect, it } from 'vitest'
import { clock, fileStamp, fixed, seconds, sentenceCase, signed, timeOfDay } from './format'

/** A real minus sign (U+2212), not a hyphen. */
const MINUS = '−'

describe('signed', () => {
  it('always shows a sign on a non-zero value', () => {
    expect(signed(0.12)).toBe('+0.12')
    expect(signed(-0.05)).toBe(`${MINUS}0.05`)
    expect(signed(1)).toBe('+1.00')
  })

  it('uses a space in place of the sign for zero, so the column keeps its width', () => {
    expect(signed(0)).toBe(' 0.00')
    expect(signed(-0)).toBe(' 0.00')
  })

  it('treats a value that rounds to zero as zero, never as −0.00', () => {
    expect(signed(-0.004)).toBe(' 0.00')
    expect(signed(0.004)).toBe(' 0.00')
    expect(signed(-0.006)).toBe(`${MINUS}0.01`)
  })

  it('uses the number of decimals asked for', () => {
    expect(signed(0.126, 1)).toBe('+0.1')
    expect(signed(-12.345, 0)).toBe(`${MINUS}12`)
    expect(signed(0.04, 1)).toBe(' 0.0')
    expect(signed(-0.5, 3)).toBe(`${MINUS}0.500`)
  })

  it('gives every value the same width at the same magnitude', () => {
    const widths = [0.5, -0.5, 0, 0.004].map((v) => signed(v).length)
    expect(new Set(widths).size).toBe(1)
  })
})

describe('fixed', () => {
  it('uses a real minus sign and no plus', () => {
    expect(fixed(0.5)).toBe('0.50')
    expect(fixed(-0.5)).toBe(`${MINUS}0.50`)
    expect(fixed(3.14159, 3)).toBe('3.142')
  })
})

describe('clock', () => {
  it('shows minutes and seconds', () => {
    expect(clock(0)).toBe('0:00')
    expect(clock(7)).toBe('0:07')
    expect(clock(42)).toBe('0:42')
    expect(clock(60)).toBe('1:00')
    expect(clock(725)).toBe('12:05')
    expect(clock(3599)).toBe('59:59')
  })

  it('adds hours once there is one', () => {
    expect(clock(3600)).toBe('1:00:00')
    expect(clock(3723)).toBe('1:02:03')
    expect(clock(36_000 + 59)).toBe('10:00:59')
  })

  it('counts whole seconds only', () => {
    expect(clock(42.9)).toBe('0:42')
    expect(clock(59.999)).toBe('0:59')
  })

  it('shows zero for a negative time', () => {
    expect(clock(-5)).toBe('0:00')
  })
})

describe('timeOfDay and fileStamp', () => {
  // Built from local-time parts, so the expected text does not depend on the machine's time zone.
  const at = new Date(2026, 10, 14, 9, 3, 7).getTime()

  it('timeOfDay pads each part to two digits', () => {
    expect(timeOfDay(at)).toBe('09:03:07')
    expect(timeOfDay(new Date(2026, 10, 14, 14, 3, 27).getTime())).toBe('14:03:27')
  })

  it('fileStamp is a date and a time that sort in order', () => {
    expect(fileStamp(at)).toBe('20261114-090307')
    expect(fileStamp(new Date(2026, 0, 2, 23, 59, 59).getTime())).toBe('20260102-235959')
  })
})

describe('sentenceCase', () => {
  it('capitalises the first letter only', () => {
    expect(sentenceCase('pill bottle')).toBe('Pill bottle')
    expect(sentenceCase('Cup')).toBe('Cup')
    expect(sentenceCase('')).toBe('')
  })
})

describe('seconds', () => {
  it('shows one decimal and keeps the unit on the same line', () => {
    expect(seconds(12.44)).toBe('12.4 s')
    expect(seconds(0)).toBe('0.0 s')
  })
})
