import type { Trial } from '../../state/trials'
import {
  dodge,
  measurePoints,
  MIN_PER_CONDITION,
  scaleFor,
  stackStep,
  summarize,
  type Groups,
  type Measure,
  type Scale,
} from './stats'

/**
 * Where everything in one strip chart goes, in CSS pixels. Kept apart from the component so the
 * geometry can be checked without a browser.
 *
 * Each chart has two rows, one per condition, that share a single horizontal axis. A row is a
 * label line, an optional line for the median's value, then the marks stacked up from a baseline.
 */

/** Radius of a trial's mark. The mark is 10 px across. */
export const DOT_R = 5
/** Radius of the invisible pointer target around each mark. */
export const HIT_R = 11

const PAD_LEFT = 12
const PAD_RIGHT = 16
/** Marks closer than this would overprint, so they are stacked instead. */
const DOT_GAP = DOT_R * 2 + 2
const LABEL_LINE = 20
const MEDIAN_LINE = 18
const STACK_MAX = 84
const ROW_GAP = 12
const AXIS_LINE = 24

export interface PlacedDot {
  trial: Trial
  value: number
  x: number
  y: number
}

export interface PlacedRow {
  haptics: boolean
  /** Completed trials in this condition, with or without a value for this measure. */
  trials: number
  /** The top of the row, where its label line starts. */
  top: number
  /** The top of the area the marks stack into. */
  stackTop: number
  baseline: number
  /** Lowest marks first, so a mark higher in a stack is painted over the one beneath it. */
  dots: PlacedDot[]
  /** Null when medians are not shown. */
  median: number | null
}

export interface ChartLayout {
  width: number
  height: number
  left: number
  right: number
  scale: Scale
  /** Medians are drawn only when both rows have enough trials to describe. */
  showMedians: boolean
  rows: PlacedRow[]
  x: (value: number) => number
}

export function layoutChart(measure: Measure, groups: Groups, width: number): ChartLayout {
  const sides = [
    { haptics: true, trials: groups.withHaptics },
    { haptics: false, trials: groups.withoutHaptics },
  ].map((side) => ({ ...side, points: measurePoints(side.trials, measure) }))

  const showMedians = sides.every((side) => side.points.length >= MIN_PER_CONDITION)
  const scale = scaleFor(
    measure,
    sides.flatMap((side) => side.points.map((p) => p.value)),
  )
  const left = PAD_LEFT
  const right = Math.max(left + 1, width - PAD_RIGHT)
  const x = (value: number): number => left + (Math.min(Math.max(value, 0), scale.max) / scale.max) * (right - left)

  const rows: PlacedRow[] = []
  let top = 0
  for (const side of sides) {
    const xs = side.points.map((p) => x(p.value))
    const levels = dodge(xs, DOT_GAP)
    const maxLevel = Math.max(0, ...levels)
    const step = stackStep(maxLevel, DOT_GAP, STACK_MAX)
    const stackTop = top + LABEL_LINE + (showMedians ? MEDIAN_LINE : 0)
    const baseline = stackTop + maxLevel * step + DOT_R * 2 + 6
    const dots = side.points
      .map((p, i) => ({ trial: p.trial, value: p.value, x: xs[i], y: baseline - DOT_R - 2 - levels[i] * step }))
      .sort((a, b) => b.y - a.y || a.x - b.x)
    rows.push({
      haptics: side.haptics,
      trials: side.trials.length,
      top,
      stackTop,
      baseline,
      dots,
      median: showMedians ? summarize(side.points.map((p) => p.value)).median : null,
    })
    top = baseline + ROW_GAP
  }

  return { width, height: rows[rows.length - 1].baseline + AXIS_LINE, left, right, scale, showMedians, rows, x }
}
