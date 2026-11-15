import { useSession } from '../../state/session'
import { bus } from '../../telemetry/bus'
import { Plate } from '../../ui/Plate'
import { usePolled } from '../signals/usePolled'
import { calibrationAvailable } from './calibration'
import { ReplayControls } from './ReplayControls'

/** Record a motion, then replay it exactly or let Gemini repeat the task (smart replay). */
export function ReplayPanel() {
  const [message] = usePolled(() => bus.latest('calibration'), 5)
  const [now] = usePolled(() => Date.now(), 5)
  const link = useSession((s) => s.link.state)
  const trial = useSession((s) => s.trial !== null)
  const live = !!calibrationAvailable(message, bus.ageMs('calibration'), link === 'open', now)
  const phase = live ? message?.replay?.phase : undefined
  const state = phase === 'smart' ? 'Gemini driving' : phase === 'recording' ? 'Recording'
    : phase === 'returning' || phase === 'playing' || phase === 'finishing' ? 'Replaying'
    : phase === 'ready' ? 'Recording ready' : phase === 'empty' ? 'Nothing recorded' : 'Unavailable'
  // A controller restart discards the old instance's pending request and confirmation.
  return (
    <Plate title="Replay" note="exact or smart (Gemini)" aside={<span className="text-xs text-ink-2">{state}</span>}>
      <ReplayControls key={message?.session ?? 'waiting'} message={message} live={live} trial={trial} />
    </Plate>
  )
}
