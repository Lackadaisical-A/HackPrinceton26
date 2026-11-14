import { LIMITS } from '../../config'
import { seconds, timeOfDay } from '../../lib/format'
import { median } from '../../lib/math'
import type { Trial } from '../../state/trials'

/**
 * The arithmetic behind the Trials screen, kept free of React so it can be unit-tested.
 *
 * Nothing here tests a hypothesis. It groups trials by condition and describes each group with
 * a count and a median, which is all a handful of hackathon trials can honestly support.
 */

export type Origin = Trial['origin']

/** The order in which origins are offered, and in which the default is chosen. */
export const ORIGINS = ['live', 'simulated', 'sample'] as const satisfies readonly Origin[]

export const ORIGIN_LABEL: Record<Origin, string> = { live: 'Live', simulated: 'Simulated', sample: 'Sample' }

/** Completed trials needed in each condition before medians are shown. */
export const MIN_PER_CONDITION = 3

export function countByOrigin(trials: readonly Trial[]): Record<Origin, number> {
  const counts: Record<Origin, number> = { live: 0, simulated: 0, sample: 0 }
  for (const trial of trials) counts[trial.origin]++
  return counts
}

/** The first origin that has trials, with live data preferred. Live when there are none at all. */
export function defaultOrigin(counts: Record<Origin, number>): Origin {
  return ORIGINS.find((origin) => counts[origin] > 0) ?? 'live'
}

/** One origin's trials, newest first, for the table. */
export function trialsOf(trials: readonly Trial[], origin: Origin): Trial[] {
  return trials.filter((t) => t.origin === origin).sort((a, b) => b.startedAt - a.startedAt)
}

export interface Groups {
  withHaptics: Trial[]
  withoutHaptics: Trial[]
}

/**
 * The trials that are charted: one origin only, completed only, split by condition.
 * Origins are never mixed, so simulated or sample rows cannot leak into a live comparison.
 */
export function completedByCondition(trials: readonly Trial[], origin: Origin): Groups {
  const groups: Groups = { withHaptics: [], withoutHaptics: [] }
  for (const trial of trials) {
    if (trial.origin !== origin || trial.outcome !== 'completed') continue
    if (trial.haptics) groups.withHaptics.push(trial)
    else groups.withoutHaptics.push(trial)
  }
  return groups
}

export function canCompare(groups: Groups): boolean {
  return groups.withHaptics.length >= MIN_PER_CONDITION && groups.withoutHaptics.length >= MIN_PER_CONDITION
}

// --- Measures ----------------------------------------------------------------------------------

export type MeasureKey = 'duration' | 'drops' | 'deformations' | 'peakGrip'

export interface Measure {
  key: MeasureKey
  title: string
  /** A qualifier shown beside the title. */
  note: string | null
  /** The axis label. */
  axis: string
  /** Whole numbers, so the axis uses whole-number ticks. */
  discrete: boolean
  /** The axis always reaches at least this value. */
  minTop: number
  /** Set when the measure has a fixed upper bound. */
  fixedTop: number | null
  /** A value worth marking on the axis, with the sentence that explains it. */
  reference: { value: number; text: string } | null
  value: (trial: Trial) => number | null
  format: (value: number) => string
}

const count = (value: number): string => (Number.isInteger(value) ? String(value) : value.toFixed(1))

export const MEASURES: readonly Measure[] = [
  {
    key: 'duration',
    title: 'Completion time',
    note: null,
    axis: 'Seconds',
    discrete: false,
    minTop: 10,
    fixedTop: null,
    reference: null,
    value: (t) => t.durationS,
    format: seconds,
  },
  {
    key: 'drops',
    title: 'Drops',
    note: null,
    axis: 'Drops per trial',
    discrete: true,
    minTop: 3,
    fixedTop: null,
    reference: null,
    value: (t) => t.drops,
    format: count,
  },
  {
    key: 'deformations',
    title: 'Deformations',
    note: null,
    axis: 'Deformations per trial',
    discrete: true,
    minTop: 3,
    fixedTop: null,
    reference: null,
    value: (t) => t.deformations,
    format: count,
  },
  {
    key: 'peakGrip',
    title: 'Peak grip',
    note: 'relative, uncalibrated',
    axis: 'Highest relative grip in the trial, 0 to 1',
    discrete: false,
    minTop: 1,
    fixedTop: 1,
    reference: {
      value: LIMITS.gripCaution,
      text: `The dashed line is at ${LIMITS.gripCaution.toFixed(2)}, where the console shows a grip caution.`,
    },
    value: (t) => t.peakGrip,
    format: (v) => v.toFixed(2),
  },
]

export interface Point {
  trial: Trial
  value: number
}

/** A measure's value for each trial. Trials with no value (no pressure data) are left out. */
export function measurePoints(trials: readonly Trial[], measure: Measure): Point[] {
  const points: Point[] = []
  for (const trial of trials) {
    const value = measure.value(trial)
    if (value !== null && Number.isFinite(value)) points.push({ trial, value })
  }
  return points
}

export interface Summary {
  n: number
  median: number | null
  min: number | null
  max: number | null
}

export function summarize(values: readonly number[]): Summary {
  if (values.length === 0) return { n: 0, median: null, min: null, max: null }
  return { n: values.length, median: median(values), min: Math.min(...values), max: Math.max(...values) }
}

// --- Chart layout ------------------------------------------------------------------------------

export interface Scale {
  max: number
  ticks: number[]
}

/** An axis from zero to a round number at or above `top`, with about `target` evenly spaced ticks. */
export function niceScale(top: number, discrete: boolean, target = 5): Scale {
  const reach = Math.max(top, discrete ? 1 : 1e-6)
  const rough = reach / (target - 1)
  const power = 10 ** Math.floor(Math.log10(rough))
  const steps = discrete ? [1, 2, 5, 10] : [1, 2, 2.5, 5, 10]
  let step = (steps.find((s) => s * power >= rough - 1e-9) ?? 10) * power
  if (discrete) step = Math.max(1, Math.round(step))
  const intervals = Math.max(1, Math.ceil(reach / step - 1e-9))
  const round = (v: number): number => Math.round(v * 1e6) / 1e6
  return {
    max: round(intervals * step),
    ticks: Array.from({ length: intervals + 1 }, (_, i) => round(i * step)),
  }
}

/** The axis for one measure, wide enough for every value in both conditions. */
export function scaleFor(measure: Measure, values: readonly number[]): Scale {
  const top = measure.fixedTop ?? Math.max(measure.minTop, ...values)
  return niceScale(top, measure.discrete)
}

/**
 * Stack levels for marks along one row. Marks closer together than `minGap` would overprint,
 * so each is given the lowest level (0 is the baseline) where it has room. Equal values pile up
 * into a column, which is what lets counts be read off a dot plot.
 *
 * @returns one level per input position, in input order
 */
export function dodge(xs: readonly number[], minGap: number): number[] {
  const order = xs.map((_, i) => i).sort((a, b) => xs[a] - xs[b] || a - b)
  const lastAtLevel: number[] = []
  const levels = new Array<number>(xs.length).fill(0)
  for (const i of order) {
    let level = 0
    while (level < lastAtLevel.length && xs[i] - lastAtLevel[level] < minGap) level++
    lastAtLevel[level] = xs[i]
    levels[i] = level
  }
  return levels
}

/**
 * The vertical distance between stacked marks. Short stacks get the full spacing. A stack too
 * tall for `maxHeight` is squeezed so its marks overlap like coins and the row stays compact.
 */
export function stackStep(maxLevel: number, full: number, maxHeight: number, min = 3): number {
  if (maxLevel * full <= maxHeight) return full
  return Math.max(min, maxHeight / maxLevel)
}

// --- Words -------------------------------------------------------------------------------------

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "14 Nov 14:03:27", in local time. Trials are kept across days, so the date matters. */
export function startedText(epochMs: number): string {
  const d = new Date(epochMs)
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${timeOfDay(epochMs)}`
}

export const conditionText = (haptics: boolean): string => (haptics ? 'With haptics' : 'Without haptics')

export const peakGripText = (peakGrip: number | null): string => (peakGrip === null ? 'No data' : peakGrip.toFixed(2))

const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`

/** "1 trial", "12 trials". */
export const trialCount = (n: number): string => plural(n, 'trial')

/** One trial as a sentence, for a chart mark's title. */
export function describeTrial(trial: Trial): string {
  const grip = trial.peakGrip === null ? 'no grip data' : `peak grip ${trial.peakGrip.toFixed(2)}`
  return (
    `${conditionText(trial.haptics)}, started ${startedText(trial.startedAt)}: ${seconds(trial.durationS)}, ` +
    `${plural(trial.drops, 'drop')}, ${plural(trial.deformations, 'deformation')}, ${grip}`
  )
}

/** The origins that have trials, as a phrase: "3 live, 2 simulated and 12 sample". */
export function originBreakdown(counts: Record<Origin, number>): string {
  const parts = ORIGINS.filter((origin) => counts[origin] > 0).map((origin) => `${counts[origin]} ${origin}`)
  if (parts.length <= 1) return parts.join('')
  return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`
}

/**
 * One chart in words, for people who cannot see it. Medians are included only when the chart
 * draws them.
 */
export function chartSummary(measure: Measure, groups: Groups): string {
  const sides = [
    { haptics: true, values: measurePoints(groups.withHaptics, measure).map((p) => p.value) },
    { haptics: false, values: measurePoints(groups.withoutHaptics, measure).map((p) => p.value) },
  ]
  const withMedians = sides.every((side) => side.values.length >= MIN_PER_CONDITION)
  const parts = sides.map((side) => {
    const { n, median: mid } = summarize(side.values)
    const middle = withMedians && mid !== null ? `, median ${measure.format(mid)}` : ''
    return `${conditionText(side.haptics)}: ${trialCount(n)}${middle}.`
  })
  return `${measure.title}, ${measure.axis.toLowerCase()}. ${parts.join(' ')}`
}

// --- CSV ---------------------------------------------------------------------------------------

export const CSV_COLUMNS = [
  'id',
  'started_iso',
  'origin',
  'condition',
  'duration_s',
  'drops',
  'deformations',
  'peak_grip_relative',
  'outcome',
] as const

const csvCell = (value: string | number): string => {
  const text = String(value)
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
}

/**
 * Every trial as CSV, oldest first. The origin column travels with each row, so simulated and
 * sample rows stay identifiable after the file leaves the app.
 */
export function trialsCsv(trials: readonly Trial[]): string {
  const rows = [...trials]
    .sort((a, b) => a.startedAt - b.startedAt)
    .map((t) =>
      [
        t.id,
        new Date(t.startedAt).toISOString(),
        t.origin,
        t.haptics ? 'with_haptics' : 'without_haptics',
        t.durationS.toFixed(2),
        t.drops,
        t.deformations,
        t.peakGrip === null ? '' : t.peakGrip.toFixed(3),
        t.outcome,
      ]
        .map(csvCell)
        .join(','),
    )
  return [CSV_COLUMNS.join(','), ...rows].join('\r\n') + '\r\n'
}

// --- Sample set --------------------------------------------------------------------------------

type SampleRow = [haptics: boolean, durationS: number, drops: number, deformations: number, peakGrip: number, completed: boolean]

/**
 * Made-up rows for trying out the page. The conditions alternate, as they would in a real
 * session, and the two overlap so the charts show an ordinary, untidy comparison.
 */
const SAMPLE_ROWS: readonly SampleRow[] = [
  [true, 24.6, 0, 1, 0.71, true],
  [false, 27.9, 1, 2, 0.93, true],
  [true, 21.8, 0, 0, 0.64, true],
  [false, 25.3, 0, 1, 0.88, true],
  [true, 23.1, 1, 0, 0.69, true],
  [false, 31.2, 1, 1, 0.95, true],
  [true, 19.4, 0, 0, 0.62, true],
  [false, 12.6, 2, 0, 0.97, false],
  [true, 20.7, 0, 1, 0.74, true],
  [false, 22.4, 0, 2, 0.91, true],
  [true, 8.3, 1, 0, 0.58, false],
  [false, 24.8, 1, 1, 0.84, true],
]

const SAMPLE_SPACING_MS = 90_000

/** The sample set, timed as if the last trial started 90 seconds before `nowMs`. */
export function sampleTrials(nowMs: number): Trial[] {
  const first = nowMs - SAMPLE_ROWS.length * SAMPLE_SPACING_MS
  return SAMPLE_ROWS.map(([haptics, durationS, drops, deformations, peakGrip, completed], i) => ({
    id: `sample-${String(i + 1).padStart(2, '0')}`,
    startedAt: first + i * SAMPLE_SPACING_MS,
    durationS,
    haptics,
    drops,
    deformations,
    outcome: completed ? 'completed' : 'aborted',
    peakGrip,
    origin: 'sample',
  }))
}
