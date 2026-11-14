import { describe, expect, it } from 'vitest'
import type { Condition } from './alerts'
import { AlertTracker } from './alerts'

const HOLD_MS = 10_000

/** A brief event: latched, so it stays on screen for a while after it ends. */
const grip = (patch: Partial<Condition> = {}): Condition => ({
  key: 'grip-high',
  level: 'caution',
  where: 'touch',
  text: 'Grip near sensor limit',
  on: true,
  latch: true,
  ...patch,
})

/** A continuing state: unlatched, so it clears the moment the state ends. */
const feed = (patch: Partial<Condition> = {}): Condition => ({
  key: 'vision-lost',
  level: 'warning',
  where: 'camera',
  text: 'Vision feed lost',
  on: true,
  latch: false,
  ...patch,
})

describe('AlertTracker', () => {
  it('shows nothing while no condition holds', () => {
    const tracker = new AlertTracker(HOLD_MS)
    expect(tracker.update([grip({ on: false }), feed({ on: false })], 1000)).toEqual({
      alerts: [],
      raised: [],
      changed: false,
    })
  })

  it('raises an alert when a condition starts', () => {
    const tracker = new AlertTracker(HOLD_MS)
    const update = tracker.update([grip()], 1000)
    const alert = {
      key: 'grip-high',
      level: 'caution',
      where: 'touch',
      text: 'Grip near sensor limit',
      active: true,
      since: 1000,
    }
    expect(update.alerts).toEqual([alert])
    expect(update.raised).toEqual([alert])
    expect(update.changed).toBe(true)
  })

  it('reports no change while a condition simply continues', () => {
    const tracker = new AlertTracker(HOLD_MS)
    tracker.update([grip()], 1000)
    const update = tracker.update([grip()], 1100)
    expect(update.changed).toBe(false)
    expect(update.raised).toEqual([])
    expect(update.alerts).toHaveLength(1)
    expect(update.alerts[0].since).toBe(1000)
  })

  it('clears an unlatched alert the moment its condition ends', () => {
    const tracker = new AlertTracker(HOLD_MS)
    tracker.update([feed()], 1000)
    const update = tracker.update([feed({ on: false })], 1016)
    expect(update.alerts).toEqual([])
    expect(update.changed).toBe(true)
    expect(tracker.update([feed({ on: false })], 1032).changed).toBe(false)
  })

  describe('a latched alert', () => {
    it('stays on screen, marked inactive, until the hold time has passed', () => {
      const tracker = new AlertTracker(HOLD_MS)
      tracker.update([grip()], 1000)

      const ended = tracker.update([grip({ on: false })], 1400)
      expect(ended.changed).toBe(true)
      expect(ended.raised).toEqual([])
      expect(ended.alerts).toHaveLength(1)
      expect(ended.alerts[0].active).toBe(false)
      expect(ended.alerts[0].since).toBe(1000)

      const held = tracker.update([grip({ on: false })], 6000)
      expect(held.changed).toBe(false)
      expect(held.alerts).toHaveLength(1)
      expect(held.alerts[0].active).toBe(false)

      const lastMoment = tracker.update([grip({ on: false })], 1000 + HOLD_MS - 1)
      expect(lastMoment.changed).toBe(false)
      expect(lastMoment.alerts).toHaveLength(1)

      const expired = tracker.update([grip({ on: false })], 1000 + HOLD_MS)
      expect(expired.alerts).toEqual([])
      expect(expired.changed).toBe(true)
    })

    it('measures the hold from when the alert was raised, not from when it ended', () => {
      const tracker = new AlertTracker(HOLD_MS)
      tracker.update([grip()], 1000)
      tracker.update([grip()], 1000 + HOLD_MS + 500)
      // It has already been visible for longer than the hold, so it can go at once.
      const update = tracker.update([grip({ on: false })], 1000 + HOLD_MS + 516)
      expect(update.alerts).toEqual([])
      expect(update.changed).toBe(true)
    })

    it('becomes active again if the condition returns while it is held', () => {
      const tracker = new AlertTracker(HOLD_MS)
      tracker.update([grip()], 1000)
      tracker.update([grip({ on: false })], 1400)

      const back = tracker.update([grip()], 3000)
      expect(back.changed).toBe(true)
      expect(back.alerts).toHaveLength(1)
      expect(back.alerts[0].active).toBe(true)
      // Same alert as before, so it is not announced a second time.
      expect(back.raised).toEqual([])
      expect(back.alerts[0].since).toBe(1000)
    })

    it('is raised afresh if the condition returns after the alert has cleared', () => {
      const tracker = new AlertTracker(HOLD_MS)
      tracker.update([grip()], 1000)
      tracker.update([grip({ on: false })], 1400)
      tracker.update([grip({ on: false })], 1000 + HOLD_MS)

      const again = tracker.update([grip()], 20_000)
      expect(again.raised).toHaveLength(1)
      expect(again.alerts[0].since).toBe(20_000)
      expect(again.alerts[0].active).toBe(true)
    })
  })

  describe('a change of wording or level', () => {
    it('updates the alert and marks a change, without raising it again', () => {
      const tracker = new AlertTracker(HOLD_MS)
      tracker.update([feed({ key: 'rate', level: 'caution', text: 'Vision rate low' })], 1000)

      const worse = tracker.update([feed({ key: 'rate', level: 'warning', text: 'Vision rate very low' })], 2000)
      expect(worse.changed).toBe(true)
      expect(worse.raised).toEqual([])
      expect(worse.alerts).toHaveLength(1)
      expect(worse.alerts[0]).toMatchObject({ level: 'warning', text: 'Vision rate very low', active: true, since: 1000 })
    })

    it('counts a change of text alone', () => {
      const tracker = new AlertTracker(HOLD_MS)
      tracker.update([feed({ text: 'No vision data' })], 1000)
      const update = tracker.update([feed({ text: 'Vision feed lost' })], 1100)
      expect(update.changed).toBe(true)
      expect(update.alerts[0].text).toBe('Vision feed lost')
    })

    it('counts a change of level alone', () => {
      const tracker = new AlertTracker(HOLD_MS)
      tracker.update([feed({ level: 'caution' })], 1000)
      const update = tracker.update([feed({ level: 'warning' })], 1100)
      expect(update.changed).toBe(true)
      expect(update.alerts[0].level).toBe('warning')
    })
  })

  it('tracks each condition on its own', () => {
    const tracker = new AlertTracker(HOLD_MS)
    const first = tracker.update([grip(), feed()], 1000)
    expect(first.raised.map((a) => a.key)).toEqual(['grip-high', 'vision-lost'])

    const second = tracker.update([grip({ on: false }), feed({ on: false })], 2000)
    expect(second.changed).toBe(true)
    // The unlatched alert has gone; the latched one is held, inactive.
    expect(second.alerts.map((a) => [a.key, a.active])).toEqual([['grip-high', false]])
  })

  it('returns a new list each time, so a stored result is never changed later', () => {
    const tracker = new AlertTracker(HOLD_MS)
    const first = tracker.update([grip()], 1000)
    tracker.update([grip({ on: false })], 1400)
    expect(first.alerts).toHaveLength(1)
    expect(first.alerts[0].active).toBe(true)
  })

  it('forgets everything on clear', () => {
    const tracker = new AlertTracker(HOLD_MS)
    tracker.update([grip(), feed()], 1000)
    tracker.clear()
    expect(tracker.update([grip({ on: false }), feed({ on: false })], 1100)).toEqual({
      alerts: [],
      raised: [],
      changed: false,
    })
    expect(tracker.update([grip()], 1200).raised).toHaveLength(1)
  })
})
