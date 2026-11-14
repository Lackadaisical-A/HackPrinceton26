import { useState } from 'react'
import { selectTarget, sendCommand } from '../../app/runtime'
import { DETECTOR_CLASSES } from '../../config'
import { sentenceCase, timeOfDay } from '../../lib/format'
import { useSession } from '../../state/session'
import type { Command, Phase } from '../../telemetry/contracts'
import { COMMAND_TOPIC, encodeFrame, PHASES } from '../../telemetry/contracts'
import { AlertTag } from '../../ui/AlertTag'
import { Button } from '../../ui/Button'
import { Plate } from '../../ui/Plate'
import { Segmented } from '../../ui/Segmented'

const ANY = 'any'

const TARGET_OPTIONS: readonly { value: string; label: string }[] = [
  { value: ANY, label: 'Any object' },
  ...DETECTOR_CLASSES.map((label) => ({ value: label, label: sentenceCase(label) })),
]

const PHASE_OPTIONS = PHASES.map((phase) => ({ value: phase, label: sentenceCase(phase) }))

interface Outcome {
  frame: string
  sent: boolean
  /** True when the simulator took the command, so no frame went over a wire. */
  simulated: boolean
  at: number
}

function Result({ outcome }: { outcome: Outcome | null }) {
  if (!outcome) {
    return <p className="text-sm text-ink-2">The frame for the last command you send appears here.</p>
  }
  return (
    <div className="flex flex-col items-start gap-2 text-sm text-ink">
      {!outcome.sent && <AlertTag level="caution" text="Not sent" />}
      <p>
        {!outcome.sent
          ? 'There is no connection. Connect to the bridge or use simulated data, then send the command again. The frame would have been:'
          : outcome.simulated
            ? `The simulator applied this command at ${timeOfDay(outcome.at)}. Over the bridge, the frame would be:`
            : `Sent at ${timeOfDay(outcome.at)} as this frame:`}
      </p>
      <pre className="num w-full rounded-md bg-well p-3 text-xs break-all whitespace-pre-wrap text-ink">{outcome.frame}</pre>
    </div>
  )
}

export function CommandPlate() {
  const [target, setTarget] = useState<string>(ANY)
  const [phase, setPhase] = useState<Phase>('search')
  const [outcome, setOutcome] = useState<Outcome | null>(null)

  const report = (command: Command, sent: boolean, simulated: boolean): void => {
    const at = Date.now()
    setOutcome({ frame: encodeFrame(COMMAND_TOPIC, { t: at / 1000, ...command }), sent, simulated, at })
  }

  const sendTarget = (): void => {
    const label = target === ANY ? null : target
    const session = useSession.getState()
    const simulated = session.link.state === 'simulated'
    // selectTarget logs exactly one event: with a caution level when the command could not be sent.
    const before = session.events[0]?.id
    selectTarget(label)
    const logged = useSession.getState().events[0]
    const sent = logged !== undefined && logged.id !== before && logged.level === undefined
    report(label ? { type: 'select_target', label } : { type: 'clear_target' }, sent, simulated)
  }

  const sendPhase = (): void => {
    const session = useSession.getState()
    const simulated = session.link.state === 'simulated'
    const command: Command = { type: 'set_phase', phase }
    const sent = sendCommand(command)
    if (sent) session.log('command', `Phase set to ${phase}`)
    else session.log('command', 'Phase not changed: no connection', 'caution')
    report(command, sent, simulated)
  }

  return (
    <Plate title="Send a command" note="to the vision module">
      <div className="flex flex-col gap-4">
        <div className="flex flex-col items-start gap-2">
          <span className="text-sm font-medium text-ink" aria-hidden="true">
            Target
          </span>
          <Segmented label="Target" value={target} options={TARGET_OPTIONS} onChange={setTarget} />
          <Button onClick={sendTarget}>Send target</Button>
        </div>

        <div className="flex flex-col items-start gap-2">
          <span className="text-sm font-medium text-ink" aria-hidden="true">
            Phase
          </span>
          <Segmented label="Phase" value={phase} options={PHASE_OPTIONS} onChange={setPhase} />
          <Button onClick={sendPhase}>Send phase</Button>
        </div>

        <div role="status" className="border-t border-rule pt-3">
          <Result outcome={outcome} />
        </div>

        <p className="max-w-[62ch] text-xs text-ink-3">
          Target and phase commands are part of the vision contract. The haptics switch on the console sends{' '}
          <span className="num">set_haptics</span>, which is a proposed command: nothing on the Python side handles it
          yet.
        </p>
      </div>
    </Plate>
  )
}
