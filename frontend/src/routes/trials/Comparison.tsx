import { useEffect, useState } from 'react'
import { Chip } from '../../ui/Chip'
import { Plate } from '../../ui/Plate'
import { StripChart } from './StripChart'
import { canCompare, MEASURES, MIN_PER_CONDITION, ORIGIN_LABEL, type Groups, type MeasureKey, type Origin } from './stats'

/**
 * The two conditions side by side: four small charts, one measure each, every one on its own
 * axis. The page describes what was recorded and stops there. It makes no claim of a difference.
 */
export function Comparison({ groups, aborted, origin }: { groups: Groups; aborted: number; origin: Origin }) {
  const [active, setActive] = useState<{ id: string; chart: MeasureKey } | null>(null)
  const pointing = active !== null

  // A tap leaves a trial's details open, so a tap anywhere else, or Escape, closes them.
  useEffect(() => {
    if (!pointing) return
    const onPointerDown = (event: PointerEvent): void => {
      if (!(event.target instanceof Element && event.target.hasAttribute('data-hit'))) setActive(null)
    }
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') setActive(null)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [pointing])

  const withCount = groups.withHaptics.length
  const withoutCount = groups.withoutHaptics.length

  return (
    <Plate
      title="With and without haptics"
      note="Completed trials only"
      aside={origin !== 'live' && <Chip tone="sim">{ORIGIN_LABEL[origin]}</Chip>}
    >
      {!canCompare(groups) && (
        <p className="mb-4 max-w-[80ch] rounded-md bg-well px-3 py-2 text-sm text-ink">
          <span className="font-semibold">Run at least {MIN_PER_CONDITION} trials in each condition to compare.</span> So
          far <span className="num">{withCount}</span> {withCount === 1 ? 'is' : 'are'} completed with haptics and{' '}
          <span className="num">{withoutCount}</span> without. Until then the charts show each trial and no medians.
        </p>
      )}

      <div className="grid grid-cols-1 gap-x-8 gap-y-6 md:grid-cols-2 2xl:grid-cols-4">
        {MEASURES.map((measure) => (
          <figure key={measure.key} className="min-w-0">
            <figcaption className="mb-2 flex flex-wrap items-baseline gap-x-2 border-b border-rule pb-1.5">
              <h3 className="text-sm font-semibold text-ink">{measure.title}</h3>
              {measure.note && <span className="text-xs text-ink-3">{measure.note}</span>}
            </figcaption>
            <StripChart
              measure={measure}
              groups={groups}
              activeId={active?.id ?? null}
              showDetails={active?.chart === measure.key}
              onActive={(id) => setActive(id === null ? null : { id, chart: measure.key })}
            />
            <p className="mt-1 text-xs text-ink-3">{measure.axis}</p>
            {measure.reference && <p className="text-xs text-ink-3">{measure.reference.text}</p>}
          </figure>
        ))}
      </div>

      <div className="mt-5 max-w-[80ch] space-y-1 border-t border-rule pt-3 text-xs text-ink-3">
        <p>Each mark is one trial. Point at a mark, or tap it, to see that trial and to find it in the other charts.</p>
        <p>Medians of a small sample describe these trials. They are not a statistical test.</p>
        <p>
          {aborted === 0
            ? 'Aborted trials are listed in the table and left out of the charts.'
            : aborted === 1
              ? '1 aborted trial is listed in the table and left out of the charts.'
              : `${aborted} aborted trials are listed in the table and left out of the charts.`}
        </p>
      </div>
    </Plate>
  )
}
