import { describe, expect, it } from 'vitest'
import type { Trial } from '../../state/trials'
import {
  canCompare,
  chartSummary,
  completedByCondition,
  countByOrigin,
  CSV_COLUMNS,
  defaultOrigin,
  describeTrial,
  dodge,
  measurePoints,
  MEASURES,
  MIN_PER_CONDITION,
  niceScale,
  originBreakdown,
  sampleTrials,
  scaleFor,
  stackStep,
  startedText,
  summarize,
  trialCount,
  trialsCsv,
  trialsOf,
} from './stats'

let nextId = 0
const trial = (patch: Partial<Trial> = {}): Trial => ({
  id: `t${nextId++}`,
  startedAt: 1_800_000_000_000 + nextId * 1000,
  durationS: 20,
  haptics: true,
  drops: 0,
  deformations: 0,
  outcome: 'completed',
  peakGrip: 0.6,
  origin: 'live',
  ...patch,
})

const measure = (key: string) => MEASURES.find((m) => m.key === key)!

describe('origins', () => {
  it('counts trials by origin', () => {
    const trials = [trial(), trial({ origin: 'sample' }), trial({ origin: 'sample' })]
    expect(countByOrigin(trials)).toEqual({ live: 1, simulated: 0, sample: 2 })
  })

  it('prefers live, then simulated, then sample', () => {
    expect(defaultOrigin({ live: 1, simulated: 4, sample: 12 })).toBe('live')
    expect(defaultOrigin({ live: 0, simulated: 4, sample: 12 })).toBe('simulated')
    expect(defaultOrigin({ live: 0, simulated: 0, sample: 12 })).toBe('sample')
    expect(defaultOrigin({ live: 0, simulated: 0, sample: 0 })).toBe('live')
  })

  it('lists one origin, newest first, without changing the input', () => {
    const a = trial({ startedAt: 100 })
    const b = trial({ startedAt: 300 })
    const c = trial({ startedAt: 200, origin: 'simulated' })
    const input = [a, b, c]
    expect(trialsOf(input, 'live')).toEqual([b, a])
    expect(input).toEqual([a, b, c])
  })
})

describe('completedByCondition', () => {
  it('splits by condition and never mixes origins', () => {
    const withLive = trial({ haptics: true })
    const withoutLive = trial({ haptics: false })
    const withSample = trial({ haptics: true, origin: 'sample' })
    const groups = completedByCondition([withLive, withoutLive, withSample], 'live')
    expect(groups.withHaptics).toEqual([withLive])
    expect(groups.withoutHaptics).toEqual([withoutLive])
  })

  it('leaves aborted trials out', () => {
    const groups = completedByCondition([trial({ outcome: 'aborted' }), trial({ haptics: false })], 'live')
    expect(groups.withHaptics).toHaveLength(0)
    expect(groups.withoutHaptics).toHaveLength(1)
  })
})

describe('canCompare', () => {
  const group = (n: number, haptics: boolean) => Array.from({ length: n }, () => trial({ haptics }))

  it('needs the minimum in both conditions', () => {
    expect(canCompare({ withHaptics: group(MIN_PER_CONDITION, true), withoutHaptics: group(MIN_PER_CONDITION, false) })).toBe(true)
    expect(canCompare({ withHaptics: group(9, true), withoutHaptics: group(MIN_PER_CONDITION - 1, false) })).toBe(false)
    expect(canCompare({ withHaptics: [], withoutHaptics: group(9, false) })).toBe(false)
  })
})

describe('measures', () => {
  it('reads each measure from a trial', () => {
    const t = trial({ durationS: 18.4, drops: 2, deformations: 1, peakGrip: 0.83 })
    expect(measurePoints([t], measure('duration'))[0].value).toBe(18.4)
    expect(measurePoints([t], measure('drops'))[0].value).toBe(2)
    expect(measurePoints([t], measure('deformations'))[0].value).toBe(1)
    expect(measurePoints([t], measure('peakGrip'))[0].value).toBe(0.83)
  })

  it('skips trials with no pressure data for peak grip only', () => {
    const trials = [trial({ peakGrip: null }), trial({ peakGrip: 0.5 })]
    expect(measurePoints(trials, measure('peakGrip'))).toHaveLength(1)
    expect(measurePoints(trials, measure('duration'))).toHaveLength(2)
  })

  it('formats whole and half counts', () => {
    expect(measure('drops').format(2)).toBe('2')
    expect(measure('drops').format(1.5)).toBe('1.5')
    expect(measure('peakGrip').format(0.9)).toBe('0.90')
  })
})

describe('summarize', () => {
  it('describes a group', () => {
    expect(summarize([3, 1, 2])).toEqual({ n: 3, median: 2, min: 1, max: 3 })
    expect(summarize([4, 1, 2, 3]).median).toBe(2.5)
  })

  it('has no median for an empty group', () => {
    expect(summarize([])).toEqual({ n: 0, median: null, min: null, max: null })
  })
})

describe('niceScale', () => {
  it('ends on a round number at or above the top value', () => {
    expect(niceScale(31.2, false)).toEqual({ max: 40, ticks: [0, 10, 20, 30, 40] })
    expect(niceScale(27.9, false)).toEqual({ max: 30, ticks: [0, 10, 20, 30] })
    expect(niceScale(1, false)).toEqual({ max: 1, ticks: [0, 0.25, 0.5, 0.75, 1] })
  })

  it('uses whole-number ticks for counts', () => {
    expect(niceScale(3, true)).toEqual({ max: 3, ticks: [0, 1, 2, 3] })
    expect(niceScale(12, true).ticks.every(Number.isInteger)).toBe(true)
    expect(niceScale(12, true).max).toBeGreaterThanOrEqual(12)
  })

  it('never returns an empty axis', () => {
    expect(niceScale(0, true).max).toBeGreaterThan(0)
    expect(niceScale(0, false).max).toBeGreaterThan(0)
  })
})

describe('scaleFor', () => {
  it('keeps a minimum reach when values are small or missing', () => {
    expect(scaleFor(measure('drops'), []).max).toBe(3)
    expect(scaleFor(measure('duration'), [2]).max).toBe(10)
  })

  it('fixes the peak grip axis at 0 to 1', () => {
    expect(scaleFor(measure('peakGrip'), [0.2, 0.4]).max).toBe(1)
  })

  it('grows to hold the largest value', () => {
    expect(scaleFor(measure('drops'), [7, 2]).max).toBeGreaterThanOrEqual(7)
  })
})

describe('dodge', () => {
  it('stacks equal positions into a column', () => {
    expect(dodge([10, 10, 10], 12)).toEqual([0, 1, 2])
  })

  it('leaves well-separated marks on the baseline', () => {
    expect(dodge([0, 20, 40], 12)).toEqual([0, 0, 0])
  })

  it('lifts only the mark that would overprint, whatever the input order', () => {
    expect(dodge([0, 5, 30], 12)).toEqual([0, 1, 0])
    expect(dodge([30, 5, 0], 12)).toEqual([0, 1, 0])
  })

  it('reuses a level once there is room again', () => {
    expect(dodge([0, 6, 12, 18], 12)).toEqual([0, 1, 0, 1])
  })

  it('handles an empty row', () => {
    expect(dodge([], 12)).toEqual([])
  })
})

describe('stackStep', () => {
  it('keeps the full spacing while the stack fits', () => {
    expect(stackStep(0, 12, 84)).toBe(12)
    expect(stackStep(7, 12, 84)).toBe(12)
  })

  it('squeezes a tall stack to the height allowed, down to a floor', () => {
    expect(stackStep(14, 12, 84)).toBe(6)
    expect(stackStep(200, 12, 84)).toBe(3)
  })
})

describe('words', () => {
  it('writes the start with its date, in local time', () => {
    expect(startedText(new Date(2026, 10, 14, 14, 3, 27).getTime())).toBe('14 Nov 14:03:27')
  })

  it('describes a trial in one sentence', () => {
    const t = trial({
      startedAt: new Date(2026, 10, 14, 9, 5, 0).getTime(),
      haptics: false,
      durationS: 21.84,
      drops: 1,
      deformations: 2,
      peakGrip: null,
    })
    expect(describeTrial(t)).toBe('Without haptics, started 14 Nov 09:05:00: 21.8 s, 1 drop, 2 deformations, no grip data')
  })

  it('counts trials in words', () => {
    expect(trialCount(1)).toBe('1 trial')
    expect(trialCount(0)).toBe('0 trials')
    expect(trialCount(12)).toBe('12 trials')
  })

  it('lists the origins that have trials', () => {
    expect(originBreakdown({ live: 3, simulated: 2, sample: 12 })).toBe('3 live, 2 simulated and 12 sample')
    expect(originBreakdown({ live: 3, simulated: 0, sample: 12 })).toBe('3 live and 12 sample')
    expect(originBreakdown({ live: 0, simulated: 0, sample: 12 })).toBe('12 sample')
    expect(originBreakdown({ live: 0, simulated: 0, sample: 0 })).toBe('')
  })
})

describe('chartSummary', () => {
  const three = (haptics: boolean, drops: number[]) => drops.map((n) => trial({ haptics, drops: n }))

  it('gives the count and median of each condition', () => {
    const groups = { withHaptics: three(true, [0, 0, 1]), withoutHaptics: three(false, [1, 2, 2]) }
    expect(chartSummary(measure('drops'), groups)).toBe(
      'Drops, drops per trial. With haptics: 3 trials, median 0. Without haptics: 3 trials, median 2.',
    )
  })

  it('leaves the medians out when either condition is short of trials', () => {
    const groups = { withHaptics: three(true, [0, 0, 1]), withoutHaptics: three(false, [1]) }
    expect(chartSummary(measure('drops'), groups)).toBe(
      'Drops, drops per trial. With haptics: 3 trials. Without haptics: 1 trial.',
    )
  })

  it('counts only the trials that have a value', () => {
    const groups = {
      withHaptics: [trial({ peakGrip: null }), trial({ peakGrip: 0.5 }), trial({ peakGrip: 0.7 })],
      withoutHaptics: [trial({ haptics: false }), trial({ haptics: false }), trial({ haptics: false })],
    }
    expect(chartSummary(measure('peakGrip'), groups)).toContain('With haptics: 2 trials. Without haptics: 3 trials.')
  })
})

describe('trialsCsv', () => {
  it('writes a header and one row per trial, oldest first', () => {
    const later = trial({ id: 'b', startedAt: Date.UTC(2026, 10, 14, 12, 0, 30), haptics: false, peakGrip: null })
    const earlier = trial({ id: 'a', startedAt: Date.UTC(2026, 10, 14, 12, 0, 0), durationS: 18.456, drops: 1, peakGrip: 0.8125 })
    const lines = trialsCsv([later, earlier]).split('\r\n')
    expect(lines[0]).toBe(CSV_COLUMNS.join(','))
    expect(lines[1]).toBe('a,2026-11-14T12:00:00.000Z,live,with_haptics,18.46,1,0,0.813,completed')
    expect(lines[2]).toBe('b,2026-11-14T12:00:30.000Z,live,without_haptics,20.00,0,0,,completed')
    expect(lines[3]).toBe('')
    expect(lines).toHaveLength(4)
  })

  it('quotes cells that contain commas or quotes', () => {
    const csv = trialsCsv([trial({ id: 'odd,"id"' })])
    expect(csv.split('\r\n')[1].startsWith('"odd,""id""",')).toBe(true)
  })

  it('is just the header when there are no trials', () => {
    expect(trialsCsv([])).toBe(`${CSV_COLUMNS.join(',')}\r\n`)
  })
})

describe('sampleTrials', () => {
  const now = 1_800_000_000_000
  const sample = sampleTrials(now)

  it('is the same every time for the same clock', () => {
    expect(sampleTrials(now)).toEqual(sample)
  })

  it('is about a dozen rows, all tagged as sample data, with unique ids', () => {
    expect(sample).toHaveLength(12)
    expect(sample.every((t) => t.origin === 'sample')).toBe(true)
    expect(new Set(sample.map((t) => t.id)).size).toBe(sample.length)
  })

  it('starts in the past, in order', () => {
    expect(sample.every((t) => t.startedAt < now)).toBe(true)
    expect(sample.every((t, i) => i === 0 || t.startedAt > sample[i - 1].startedAt)).toBe(true)
  })

  it('has both conditions, a few aborted, and enough completed trials to compare', () => {
    const aborted = sample.filter((t) => t.outcome === 'aborted').length
    expect(aborted).toBeGreaterThanOrEqual(1)
    expect(aborted).toBeLessThanOrEqual(2)
    expect(sample.filter((t) => t.haptics)).toHaveLength(6)
    expect(canCompare(completedByCondition(sample, 'sample'))).toBe(true)
  })

  it('keeps values in range', () => {
    for (const t of sample) {
      expect(t.durationS).toBeGreaterThan(0)
      expect(Number.isInteger(t.drops) && t.drops >= 0).toBe(true)
      expect(Number.isInteger(t.deformations) && t.deformations >= 0).toBe(true)
      expect(t.peakGrip).toBeGreaterThanOrEqual(0)
      expect(t.peakGrip).toBeLessThanOrEqual(1)
    }
  })
})
