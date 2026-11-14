import { describe, expect, it } from 'vitest'
import type { Trial } from '../../state/trials'
import { DOT_R, layoutChart } from './chartLayout'
import { completedByCondition, MEASURES, sampleTrials, type Groups } from './stats'

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
const sample: Groups = completedByCondition(sampleTrials(1_800_000_000_000), 'sample')

describe('layoutChart', () => {
  it('keeps every mark inside the chart, at the narrowest width the app supports', () => {
    for (const m of MEASURES) {
      const chart = layoutChart(m, sample, 304)
      for (const row of chart.rows) {
        for (const dot of row.dots) {
          expect(dot.x - DOT_R).toBeGreaterThanOrEqual(0)
          expect(dot.x + DOT_R).toBeLessThanOrEqual(chart.width)
          expect(dot.y - DOT_R).toBeGreaterThanOrEqual(row.stackTop)
          expect(dot.y + DOT_R).toBeLessThanOrEqual(row.baseline)
        }
      }
    }
  })

  it('places a mark by its value along the shared axis', () => {
    const groups = { withHaptics: [trial({ durationS: 0 })], withoutHaptics: [trial({ haptics: false, durationS: 10 })] }
    const chart = layoutChart(measure('duration'), groups, 400)
    expect(chart.scale.max).toBe(10)
    expect(chart.rows[0].dots[0].x).toBe(chart.left)
    expect(chart.rows[1].dots[0].x).toBe(chart.right)
    expect(chart.x(5)).toBe((chart.left + chart.right) / 2)
  })

  it('stacks equal values instead of overprinting them', () => {
    const groups = { withHaptics: [trial(), trial(), trial()], withoutHaptics: [] }
    const [row] = layoutChart(measure('drops'), groups, 400).rows
    expect(new Set(row.dots.map((d) => d.x)).size).toBe(1)
    const ys = row.dots.map((d) => d.y)
    expect(new Set(ys).size).toBe(3)
    expect(Math.abs(ys[0] - ys[1])).toBeGreaterThanOrEqual(DOT_R * 2)
  })

  it('puts the second row below the first, and the axis below both', () => {
    const chart = layoutChart(measure('drops'), sample, 400)
    expect(chart.rows[1].top).toBeGreaterThan(chart.rows[0].baseline)
    expect(chart.height).toBeGreaterThan(chart.rows[1].baseline)
  })

  it('gives medians only when both rows have at least three trials', () => {
    const three = (haptics: boolean) => [10, 20, 40].map((durationS) => trial({ haptics, durationS }))
    const full = layoutChart(measure('duration'), { withHaptics: three(true), withoutHaptics: three(false) }, 400)
    expect(full.showMedians).toBe(true)
    expect(full.rows.map((r) => r.median)).toEqual([20, 20])

    const short = layoutChart(measure('duration'), { withHaptics: three(true), withoutHaptics: three(false).slice(1) }, 400)
    expect(short.showMedians).toBe(false)
    expect(short.rows.map((r) => r.median)).toEqual([null, null])
    expect(short.rows[0].dots).toHaveLength(3)
  })

  it('withholds the peak grip median when too few trials have pressure data', () => {
    const groups = {
      withHaptics: [trial({ peakGrip: null }), trial({ peakGrip: null }), trial({ peakGrip: 0.5 })],
      withoutHaptics: [trial({ haptics: false }), trial({ haptics: false }), trial({ haptics: false })],
    }
    const grip = layoutChart(measure('peakGrip'), groups, 400)
    expect(grip.showMedians).toBe(false)
    expect(grip.rows[0].trials).toBe(3)
    expect(grip.rows[0].dots).toHaveLength(1)
    expect(layoutChart(measure('duration'), groups, 400).showMedians).toBe(true)
  })

  it('draws an empty row without failing', () => {
    const chart = layoutChart(measure('duration'), { withHaptics: [], withoutHaptics: [] }, 304)
    expect(chart.rows.every((r) => r.dots.length === 0)).toBe(true)
    expect(chart.height).toBeGreaterThan(0)
  })
})
