import { describe, expect, it } from 'vitest'
import { LABEL_LIMITS } from '../config'
import { boxFromDrag, nameProblem, normalizeName } from './labels'

describe('normalizeName', () => {
  it('lower-cases and keeps single spaces, as vision does', () => {
    expect(normalizeName('  My   Flask ')).toBe('my flask')
    expect(normalizeName('Cup\tB')).toBe('cup b')
    expect(normalizeName('   ')).toBe('')
  })
})

describe('nameProblem', () => {
  it('accepts an ordinary name', () => {
    expect(nameProblem('my flask')).toBeNull()
    expect(nameProblem('a'.repeat(LABEL_LIMITS.nameChars))).toBeNull()
  })

  it('refuses what vision would refuse', () => {
    expect(nameProblem('')).toBe('Type a name.')
    expect(nameProblem('a'.repeat(LABEL_LIMITS.nameChars + 1))).toBe(`Use at most ${LABEL_LIMITS.nameChars} characters.`)
    expect(nameProblem('cup/2')).toBe('Names cannot contain a slash or a hash sign.')
    expect(nameProblem('cup#2')).toBe('Names cannot contain a slash or a hash sign.')
    expect(nameProblem('drop station')).toBe('That name belongs to a station.')
  })
})

describe('boxFromDrag', () => {
  it('orders the corners whichever way the drag went', () => {
    expect(boxFromDrag(0.6, 0.7, 0.3, 0.2)).toEqual([0.3, 0.2, 0.6, 0.7])
    expect(boxFromDrag(0.3, 0.7, 0.6, 0.2)).toEqual([0.3, 0.2, 0.6, 0.7])
  })

  it('keeps the box inside the picture and rounds to four decimals, as vision publishes boxes', () => {
    expect(boxFromDrag(-0.2, 0.123456, 0.5, 1.4)).toEqual([0, 0.1235, 0.5, 1])
  })

  it('treats a click or a thin slip as no box', () => {
    expect(boxFromDrag(0.5, 0.5, 0.5, 0.5)).toBeNull()
    expect(boxFromDrag(0.2, 0.2, 0.6, 0.2 + LABEL_LIMITS.minBoxSide / 2)).toBeNull()
    expect(boxFromDrag(0.2, 0.2, 0.2 + LABEL_LIMITS.minBoxSide + 0.001, 0.6)).not.toBeNull()
  })
})
