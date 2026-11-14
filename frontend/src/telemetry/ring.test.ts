import { describe, expect, it } from 'vitest'
import { Ring } from './ring'

describe('Ring', () => {
  it('starts empty and reports its shape', () => {
    const ring = new Ring(8, 3)
    expect(ring.length).toBe(0)
    expect(ring.capacity).toBe(8)
    expect(ring.stride).toBe(3)
  })

  it('reads rows back oldest first', () => {
    const ring = new Ring(4, 1)
    ring.push(10)
    ring.push(20)
    ring.push(30)
    expect(ring.length).toBe(3)
    expect([ring.at(0), ring.at(1), ring.at(2)]).toEqual([10, 20, 30])
  })

  it('drops the oldest rows once it is full', () => {
    const ring = new Ring(3, 1)
    for (const v of [1, 2, 3, 4, 5]) ring.push(v)
    expect(ring.length).toBe(3)
    expect([ring.at(0), ring.at(1), ring.at(2)]).toEqual([3, 4, 5])
  })

  it('stays in order after wrapping many times', () => {
    const ring = new Ring(5, 1)
    for (let v = 0; v < 1003; v++) ring.push(v)
    expect(ring.length).toBe(5)
    for (let i = 0; i < 5; i++) expect(ring.at(i)).toBe(998 + i)
  })

  it('holds exactly its capacity when filled to the brim', () => {
    const ring = new Ring(3, 1)
    for (const v of [7, 8, 9]) ring.push(v)
    expect(ring.length).toBe(3)
    expect([ring.at(0), ring.at(1), ring.at(2)]).toEqual([7, 8, 9])
  })

  describe('last', () => {
    it('returns the newest row', () => {
      const ring = new Ring(3, 2)
      ring.push(1, 100)
      ring.push(2, 200)
      expect(ring.last()).toBe(2)
      expect(ring.last(1)).toBe(200)
    })

    it('returns the newest row after wrapping', () => {
      const ring = new Ring(2, 2)
      ring.push(1, 100)
      ring.push(2, 200)
      ring.push(3, 300)
      expect(ring.last(0)).toBe(3)
      expect(ring.last(1)).toBe(300)
    })

    it('returns the fallback when empty', () => {
      const ring = new Ring(3, 2)
      expect(ring.last()).toBe(0)
      expect(ring.last(1, -1)).toBe(-1)
      expect(Number.isNaN(ring.last(0, Number.NaN))).toBe(true)
    })
  })

  describe('stride', () => {
    it('keeps the fields of each row together', () => {
      const ring = new Ring(4, 3)
      ring.push(1, 2, 3)
      ring.push(4, 5, 6)
      expect([ring.at(0, 0), ring.at(0, 1), ring.at(0, 2)]).toEqual([1, 2, 3])
      expect([ring.at(1, 0), ring.at(1, 1), ring.at(1, 2)]).toEqual([4, 5, 6])
    })

    it('keeps rows intact across the wrap', () => {
      const ring = new Ring(2, 3)
      ring.push(1, 2, 3)
      ring.push(4, 5, 6)
      ring.push(7, 8, 9)
      expect([ring.at(0, 0), ring.at(0, 1), ring.at(0, 2)]).toEqual([4, 5, 6])
      expect([ring.at(1, 0), ring.at(1, 1), ring.at(1, 2)]).toEqual([7, 8, 9])
    })

    it('stores a missing field as zero, even over an old value', () => {
      const ring = new Ring(1, 4)
      ring.push(1, 2, 3, 4)
      ring.push(5)
      expect([ring.at(0, 0), ring.at(0, 1), ring.at(0, 2), ring.at(0, 3)]).toEqual([5, 0, 0, 0])
    })

    it('ignores fields beyond the stride instead of writing into the next row', () => {
      const narrow = new Ring(2, 2)
      narrow.push(3, 4) // the row a stray third field would land in
      narrow.push(1, 2, 99, 98)
      expect([narrow.at(0, 0), narrow.at(0, 1)]).toEqual([3, 4])
      expect([narrow.at(1, 0), narrow.at(1, 1)]).toEqual([1, 2])

      const single = new Ring(2, 1)
      single.push(1, 99)
      single.push(2, 98)
      expect([single.at(0), single.at(1)]).toEqual([1, 2])
    })

    it('supports four fields', () => {
      const ring = new Ring(2, 4)
      ring.push(1, 2, 3, 4)
      expect([ring.at(0, 0), ring.at(0, 1), ring.at(0, 2), ring.at(0, 3)]).toEqual([1, 2, 3, 4])
    })
  })

  describe('clear', () => {
    it('empties the buffer', () => {
      const ring = new Ring(3, 2)
      ring.push(1, 10)
      ring.push(2, 20)
      ring.clear()
      expect(ring.length).toBe(0)
      expect(ring.last(0, -1)).toBe(-1)
    })

    it('starts again from the first row', () => {
      const ring = new Ring(3, 1)
      for (const v of [1, 2, 3, 4]) ring.push(v)
      ring.clear()
      ring.push(50)
      ring.push(60)
      expect(ring.length).toBe(2)
      expect([ring.at(0), ring.at(1)]).toEqual([50, 60])
      expect(ring.last()).toBe(60)
    })
  })
})
