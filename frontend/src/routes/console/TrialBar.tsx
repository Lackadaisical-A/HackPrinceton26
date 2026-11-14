import { Link } from 'react-router'
import { finishTrial, markDeformation, markDrop, startTrial } from '../../app/runtime'
import { clock } from '../../lib/format'
import { useSession } from '../../state/session'
import { useTrials } from '../../state/trials'
import { Button } from '../../ui/Button'
import { Plate } from '../../ui/Plate'
import { Readout } from '../../ui/Readout'

function readElapsed(): string {
  const trial = useSession.getState().trial
  return trial ? clock((Date.now() - trial.startedAt) / 1000) : '0:00'
}

function Tally({ label, value, action, hint, onMark }: { label: string; value: number; action: string; hint: string; onMark: () => void }) {
  return (
    <div className="flex items-end gap-2.5">
      <div>
        <p className="text-xs text-ink-2">{label}</p>
        <p className="num text-2xl leading-8 font-medium text-ink" aria-live="polite">
          {value}
        </p>
      </div>
      {/* Taller on a narrow screen, where these are pressed with a thumb instead of the D and F keys. */}
      <Button size="sm" hint={hint} onClick={onMark} className="mb-1 max-md:min-h-10 max-md:px-3">
        {action}
      </Button>
    </div>
  )
}

/**
 * Runs one trial of the comparison: the same task, with or without haptic feedback.
 * The haptics setting is fixed for the length of a trial so each result belongs to one condition.
 */
export function TrialBar() {
  const trial = useSession((s) => s.trial)
  const haptics = useSession((s) => s.haptics)
  const linkState = useSession((s) => s.link.state)
  const count = useTrials((s) => s.trials.length)
  const idle = linkState === 'idle'

  return (
    <Plate
      title="Trial"
      note={trial ? (trial.haptics ? 'running with haptics' : 'running without haptics, the control') : undefined}
      aside={
        <Link to="/trials" className="text-xs font-medium text-ink underline underline-offset-2">
          {count === 0 ? 'No trials recorded yet' : count === 1 ? '1 trial recorded' : `${count} trials recorded`}
        </Link>
      }
    >
      {trial ? (
        <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
          <div>
            <p className="text-xs text-ink-2">Elapsed</p>
            <p className="text-2xl leading-8 font-medium text-ink">
              <Readout read={readElapsed} hz={4} width={4} />
            </p>
          </div>
          <Tally label="Drops" value={trial.drops} action="Mark drop" hint="D" onMark={markDrop} />
          <Tally label="Deformations" value={trial.deformations} action="Mark deformation" hint="F" onMark={markDeformation} />
          <div className="ml-auto flex items-center gap-2">
            <Button variant="quiet" onClick={() => finishTrial('aborted')}>
              Abort
            </Button>
            <Button variant="solid" hint="T" onClick={() => finishTrial('completed')}>
              Finish trial
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <Button variant="solid" hint="T" onClick={startTrial} disabled={idle}>
            Start trial
          </Button>
          <p className="min-w-0 flex-1 basis-72 text-sm text-ink-2">
            {idle ? (
              'Connect a data source to run a trial.'
            ) : (
              <>
                The next trial runs{' '}
                <strong className="font-semibold text-ink">{haptics ? 'with haptics' : 'without haptics'}</strong>. The
                setting is locked until it ends
                {linkState === 'simulated' ? ', and the result is saved as simulated.' : '.'}
              </>
            )}
          </p>
        </div>
      )}
    </Plate>
  )
}
