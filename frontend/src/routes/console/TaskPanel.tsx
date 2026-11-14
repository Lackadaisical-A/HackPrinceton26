import { selectTarget } from '../../app/runtime'
import { DETECTOR_CLASSES } from '../../config'
import { sentenceCase } from '../../lib/format'
import { useSession } from '../../state/session'
import { PHASES } from '../../telemetry/contracts'
import { cx } from '../../ui/cx'
import { Plate } from '../../ui/Plate'
import { Segmented } from '../../ui/Segmented'

const ANY = 'any'
const TARGETS: { value: string; label: string }[] = [
  { value: ANY, label: 'Any object' },
  ...DETECTOR_CLASSES.map((name) => ({ value: name, label: sentenceCase(name) })),
]

/** The six phases of a pick and place, as a line the task moves along. */
function PhaseTrack() {
  const phase = useSession((s) => s.vision.phase)
  const current = PHASES.indexOf(phase)

  return (
    <ol className="grid grid-cols-6" aria-label="Task phases">
      {PHASES.map((name, i) => {
        const state = i < current ? 'done' : i === current ? 'now' : 'todo'
        return (
          <li
            key={name}
            aria-current={state === 'now' ? 'step' : undefined}
            className="relative flex min-w-0 flex-col items-center gap-1.5 text-center"
          >
            {i > 0 && (
              <span
                aria-hidden="true"
                className={cx('absolute top-[7px] right-1/2 h-px w-full', i <= current ? 'bg-ink-2' : 'bg-rule-strong')}
              />
            )}
            <span aria-hidden="true" className="relative grid size-[15px] place-items-center">
              {state === 'now' ? (
                <span className="grid size-[15px] place-items-center rounded-full bg-ink">
                  <span className="size-[5px] rounded-full bg-plate" />
                </span>
              ) : state === 'done' ? (
                <span className="size-[9px] rounded-full bg-ink-2" />
              ) : (
                <span className="size-[11px] rounded-full border border-rule-strong bg-plate" />
              )}
            </span>
            <span
              className={cx(
                'max-w-full truncate text-xs',
                state === 'now' ? 'font-semibold text-ink' : state === 'done' ? 'text-ink-2' : 'text-ink-3',
              )}
            >
              {sentenceCase(name)}
              <span className="sr-only">{state === 'done' ? ', done' : state === 'now' ? ', current' : ''}</span>
            </span>
          </li>
        )
      })}
    </ol>
  )
}

export function TaskPanel() {
  const target = useSession((s) => s.targetLabel)
  const idle = useSession((s) => s.link.state === 'idle')
  const stale = useSession((s) => ['degraded', 'silent', 'late'].includes(s.vision.state))

  return (
    <Plate title="Task" note={stale ? 'last known phase, vision is lost' : undefined}>
      <PhaseTrack />
      <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-rule pt-3">
        <span className="text-sm font-medium text-ink">Target</span>
        <Segmented
          label="Target object"
          value={target ?? ANY}
          options={TARGETS}
          onChange={(value) => selectTarget(value === ANY ? null : value)}
          disabled={idle}
        />
        <span className="text-xs text-ink-2">
          {idle ? 'Connect a data source to choose a target.' : 'The object vision looks for and guides the gripper to.'}
        </span>
      </div>
    </Plate>
  )
}
