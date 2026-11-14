import { useMemo } from 'react'
import { seconds } from '../../lib/format'
import { clamp } from '../../lib/math'
import { DOT_R, HIT_R, layoutChart, type ChartLayout, type PlacedDot, type PlacedRow } from './chartLayout'
import { ConditionGlyph, Mark } from './marks'
import { chartSummary, conditionText, describeTrial, peakGripText, startedText, type Groups, type Measure } from './stats'
import { useElementWidth } from './useElementWidth'

export interface StripChartProps {
  measure: Measure
  /** Completed trials of one origin, split by condition. */
  groups: Groups
  /** The trial being pointed at in any chart, so the same trial is ringed in every chart. */
  activeId: string | null
  /** True for the chart the pointer is in. Only that chart shows the trial's details. */
  showDetails: boolean
  onActive: (id: string | null) => void
}

const DETAILS_WIDTH = 216
/** Lands a one-pixel line on the pixel grid so it stays sharp. */
const crisp = (v: number): number => Math.round(v) + 0.5

/**
 * One measure as a dot plot: a row per condition on one shared axis, a mark per trial, and equal
 * values stacked so counts can be read. Position along the axis is the only thing that encodes
 * the value.
 */
export function StripChart({ measure, groups, activeId, showDetails, onActive }: StripChartProps) {
  const [ref, width] = useElementWidth<HTMLDivElement>()
  const chart = useMemo(() => (width > 0 ? layoutChart(measure, groups, width) : null), [measure, groups, width])
  const summary = useMemo(() => chartSummary(measure, groups), [measure, groups])
  const active = chart !== null && activeId !== null ? findDot(chart, activeId) : null

  return (
    <div ref={ref} className="relative">
      {chart && (
        <svg role="group" aria-label={summary} width={chart.width} height={chart.height} className="block select-none">
          {chart.rows.map((row) => (
            <Row key={row.haptics ? 'with' : 'without'} row={row} chart={chart} measure={measure} />
          ))}

          {chart.scale.ticks.map((tick) => (
            <text
              key={tick}
              className="num"
              x={chart.x(tick)}
              y={chart.height - 6}
              textAnchor="middle"
              fontSize={12}
              fill="var(--ink-3)"
            >
              {tick}
            </text>
          ))}

          {active && (
            <circle cx={active.x} cy={active.y} r={DOT_R + 3.5} fill="none" stroke="var(--ink)" strokeWidth={1.5} />
          )}

          {/* Pointer targets, larger than the marks and on top of everything. */}
          {chart.rows.flatMap((row) =>
            row.dots.map((dot) => (
              <circle
                key={dot.trial.id}
                data-hit=""
                cx={dot.x}
                cy={dot.y}
                r={HIT_R}
                fill="transparent"
                onPointerEnter={() => onActive(dot.trial.id)}
                onPointerLeave={(event) => {
                  // A touch has no hover to end. Its details stay until the next tap.
                  if (event.pointerType !== 'touch') onActive(null)
                }}
              />
            )),
          )}
        </svg>
      )}
      {chart && active && showDetails && <Details dot={active} chart={chart} />}
    </div>
  )
}

function findDot(chart: ChartLayout, id: string): PlacedDot | null {
  for (const row of chart.rows) {
    const dot = row.dots.find((d) => d.trial.id === id)
    if (dot) return dot
  }
  return null
}

function Row({ row, chart, measure }: { row: PlacedRow; chart: ChartLayout; measure: Measure }) {
  const count = row.dots.length
  const partial = count < row.trials
  const labelY = row.top + 14

  return (
    <g>
      <Mark x={DOT_R + 1} y={row.top + 10} haptics={row.haptics} halo={false} />
      <text x={DOT_R * 2 + 8} y={labelY} fontSize={13} fontWeight={500} fill="var(--ink)">
        {conditionText(row.haptics)}
      </text>
      {/* A trial with no value for this measure is not drawn, so the count says how many are. */}
      <text x={chart.width - 1} y={labelY} textAnchor="end" fontSize={12} fill="var(--ink-2)">
        <tspan className="num">{count}</tspan>
        {partial && ' of '}
        {partial && <tspan className="num">{row.trials}</tspan>}
        {(partial ? row.trials : count) === 1 ? ' trial' : ' trials'}
      </text>

      {count > 0 &&
        chart.scale.ticks.map((tick) => (
          <line
            key={tick}
            x1={crisp(chart.x(tick))}
            x2={crisp(chart.x(tick))}
            y1={row.stackTop}
            y2={row.baseline}
            stroke="var(--rule)"
          />
        ))}
      <line x1={chart.left} x2={chart.right} y1={row.baseline + 0.5} y2={row.baseline + 0.5} stroke="var(--rule)" />

      {count > 0 && measure.reference && (
        <line
          x1={crisp(chart.x(measure.reference.value))}
          x2={crisp(chart.x(measure.reference.value))}
          y1={row.stackTop}
          y2={row.baseline}
          stroke="var(--rule-strong)"
          strokeDasharray="3 3"
        />
      )}

      {count === 0 && (
        <text x={chart.left} y={row.baseline - 6} fontSize={12} fill="var(--ink-3)">
          {row.trials === 0 ? 'No completed trials' : 'No pressure data in these trials'}
        </text>
      )}

      {row.median !== null && <Median value={row.median} row={row} chart={chart} measure={measure} />}

      {row.dots.map((dot) => (
        <g key={dot.trial.id} role="img" pointerEvents="none">
          <title>{describeTrial(dot.trial)}</title>
          <Mark x={dot.x} y={dot.y} haptics={row.haptics} />
        </g>
      ))}
    </g>
  )
}

/** The row's median: a tick across the row with its value written above it, both in ink. */
function Median({ value, row, chart, measure }: { value: number; row: PlacedRow; chart: ChartLayout; measure: Measure }) {
  const x = chart.x(value)
  // The label is centred on the tick, except near an edge, where it is tucked inward.
  const label =
    x < 52
      ? ({ x: Math.max(0, x - 1), textAnchor: 'start' } as const)
      : x > chart.width - 52
        ? ({ x: Math.min(chart.width, x + 1), textAnchor: 'end' } as const)
        : ({ x, textAnchor: 'middle' } as const)

  return (
    <>
      {/* Drawn behind the marks and past both ends of the row, so a stack sitting on the median cannot hide it. */}
      <line x1={x} x2={x} y1={row.stackTop - 4} y2={row.baseline + 6} stroke="var(--ink)" strokeWidth={2} />
      <text {...label} y={row.stackTop - 8} fontSize={12} fill="var(--ink)">
        {'Median '}
        <tspan className="num">{measure.format(value)}</tspan>
      </text>
    </>
  )
}

/** Everything recorded about the trial under the pointer. Hidden from assistive technology: the table has it all. */
function Details({ dot, chart }: { dot: PlacedDot; chart: ChartLayout }) {
  const { trial } = dot
  const left = clamp(dot.x - DETAILS_WIDTH / 2, 0, Math.max(0, chart.width - DETAILS_WIDTH))

  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute z-10 rounded-md border border-rule-strong bg-plate px-2.5 py-2 text-xs text-ink"
      style={{ left, bottom: chart.height - dot.y + HIT_R + 1, width: DETAILS_WIDTH }}
    >
      <p className="flex items-center gap-1.5 font-semibold">
        <ConditionGlyph haptics={trial.haptics} />
        {conditionText(trial.haptics)}
      </p>
      <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
        <dt className="text-ink-2">Started</dt>
        <dd className="num text-right">{startedText(trial.startedAt)}</dd>
        <dt className="text-ink-2">Duration</dt>
        <dd className="num text-right">{seconds(trial.durationS)}</dd>
        <dt className="text-ink-2">Drops</dt>
        <dd className="num text-right">{trial.drops}</dd>
        <dt className="text-ink-2">Deformations</dt>
        <dd className="num text-right">{trial.deformations}</dd>
        <dt className="text-ink-2">Peak grip</dt>
        <dd className={trial.peakGrip === null ? 'text-right' : 'num text-right'}>{peakGripText(trial.peakGrip)}</dd>
      </dl>
    </div>
  )
}
