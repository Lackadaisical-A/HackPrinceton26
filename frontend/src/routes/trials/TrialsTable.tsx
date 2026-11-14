import { seconds } from '../../lib/format'
import type { Trial } from '../../state/trials'
import { Button } from '../../ui/Button'
import { IconTrash } from '../../ui/icons'
import { ConditionGlyph } from './marks'
import { conditionText, peakGripText, startedText } from './stats'

const HEAD = 'py-2 pr-4 font-medium whitespace-nowrap'
const CELL = 'py-1.5 pr-4 whitespace-nowrap'

/**
 * Every trial of the origin on show, as a table: the same values the charts draw, plus the
 * aborted trials the charts leave out. On a narrow screen the table scrolls sideways inside its
 * plate, so the page itself never does.
 */
export function TrialsTable({
  trials,
  caption,
  onDelete,
}: {
  trials: readonly Trial[]
  caption: string
  onDelete: (trial: Trial) => void
}) {
  return (
    // `relative` keeps the visually hidden labels inside this scroller. Without it they are placed
    // against the page, far to the right, and the whole page scrolls sideways on a phone.
    <div role="region" aria-label={caption} tabIndex={0} className="relative overflow-x-auto">
      <table className="w-full min-w-[46rem] border-collapse text-sm text-ink">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-rule-strong text-xs text-ink-2">
            <th scope="col" className={`${HEAD} text-left`}>
              Started
            </th>
            <th scope="col" className={`${HEAD} text-left`}>
              Condition
            </th>
            <th scope="col" className={`${HEAD} text-right`}>
              Duration
            </th>
            <th scope="col" className={`${HEAD} text-right`}>
              Drops
            </th>
            <th scope="col" className={`${HEAD} text-right`}>
              Deformations
            </th>
            <th scope="col" className={`${HEAD} text-right`}>
              Peak grip
            </th>
            <th scope="col" className={`${HEAD} pl-4 text-left`}>
              Outcome
            </th>
            <th scope="col" className="py-2">
              <span className="sr-only">Delete</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {trials.map((trial) => {
            const started = startedText(trial.startedAt)
            return (
              <tr key={trial.id} className="border-b border-rule last:border-b-0">
                <th scope="row" className={`${CELL} num text-left font-normal`}>
                  {started}
                </th>
                <td className={CELL}>
                  <span className="inline-flex items-center gap-2">
                    <ConditionGlyph haptics={trial.haptics} />
                    {conditionText(trial.haptics)}
                  </span>
                </td>
                <td className={`${CELL} num text-right`}>{seconds(trial.durationS)}</td>
                <td className={`${CELL} num text-right`}>{trial.drops}</td>
                <td className={`${CELL} num text-right`}>{trial.deformations}</td>
                <td className={`${CELL} text-right ${trial.peakGrip === null ? 'text-ink-2' : 'num'}`}>
                  {peakGripText(trial.peakGrip)}
                </td>
                <td className={`${CELL} pl-4`}>{trial.outcome === 'completed' ? 'Completed' : 'Aborted, not charted'}</td>
                <td className="py-1 text-right">
                  <Button
                    variant="quiet"
                    size="sm"
                    icon={<IconTrash size={14} />}
                    aria-label={`Delete the trial started ${started}`}
                    onClick={() => onDelete(trial)}
                  >
                    Delete
                  </Button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
