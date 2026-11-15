import { expect, it } from 'vitest'
import { hapticDirection } from './hapticDirection'

it('matches the physical palm-bottom / forearm-top wiring for every direction', () => {
  expect(hapticDirection(-0.5, 0, 1)).toEqual([1, 0, 1, 0])
  expect(hapticDirection(0.5, 0, 1)).toEqual([0, 1, 0, 1])
  expect(hapticDirection(0, -0.5, 1)).toEqual([0, 0, 1, 1])
  expect(hapticDirection(0, 0.5, 1)).toEqual([1, 1, 0, 0])
  expect(hapticDirection(-0.5, -0.5, 1)).toEqual([0, 0, 1, 0])
  expect(hapticDirection(0.5, 0.5, 1)).toEqual([0, 1, 0, 0])
  expect(hapticDirection(0.08, -0.1, 1)).toEqual([0, 0, 0, 0])
})
