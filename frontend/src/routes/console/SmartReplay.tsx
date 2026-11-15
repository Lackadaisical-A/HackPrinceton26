import { useEffect, useState } from 'react'
import { bus } from '../../telemetry/bus'
import type { ReplayAction, ReplayStatus } from '../../telemetry/contracts'
import { Button } from '../../ui/Button'
import { usePolled } from '../signals/usePolled'
import { confirmedBrief, taskFor } from './smartTask'

interface LogEntry { run: string | null; seq: number; action: string; why: string; ok: boolean; detail: string }

/** The store keeps the whole run; this many is enough to see what Gemini just tried. */
const SHOWN = 6

const words = (action: string | null) => (action ?? 'action').replaceAll('_', ' ')

/**
 * Smart replay: Gemini studies the recording, the operator confirms what it understood, then Gemini
 * chooses each action and the robot carries it out. Stop, the joystick button and the robot's own
 * limits work exactly as in a plain replay.
 */
export function SmartReplay({ replay, available, act, confirmed, onConfirm }: {
  replay: ReplayStatus | null | undefined
  available: boolean
  act: (action: ReplayAction) => void
  /** The recording (its `recorded_wall`) whose brief the operator confirmed. */
  confirmed: number | null
  onConfirm: (recording: number | null) => void
}) {
  const [task] = usePolled(() => bus.latest('task'), 2)
  const [thinking] = usePolled(() => bus.latest('agent'), 4)
  const [log, setLog] = useState<LogEntry[]>([])
  useEffect(() => bus.on('calibration', (update) => {
    const agent = update.replay?.agent
    if (!agent || agent.result.seq === 0) return
    const { result } = agent
    setLog((entries) => {
      const kept = entries.filter((e) => e.run === agent.run)
      if (kept.some((e) => e.seq === result.seq)) return kept.length === entries.length ? entries : kept
      return [...kept, { run: agent.run, seq: result.seq, action: words(result.action), why: agent.action?.why ?? '',
        ok: result.ok, detail: result.detail }]
    })
  }), [])

  if (!replay) return null
  const recording = replay.recorded_wall ?? null
  const current = taskFor(task, replay)
  const brief = current?.status === 'ok' ? current.brief : null
  const agent = replay.agent ?? null
  const running = replay.phase === 'smart'
  const ready = !!confirmedBrief(task, replay, confirmed)
  const gemini = running && agent?.state === 'waiting' && thinking?.run === agent.run && thinking.turn === agent.seq ? thinking : null

  return <div className="mt-3">
    <p className="mt-1 text-xs text-ink-2">Gemini studies your recording, then drives the arm itself, one action at a time, checking the camera after each and correcting when something goes wrong. Stop and the joystick button work as usual.</p>

    {recording === null && <p className="mt-2 text-sm text-ink-2">Record a motion first. Gemini studies it when the recording finishes.</p>}
    {recording !== null && !current && <p className="mt-2 text-sm text-ink-2" role="status">Waiting for Gemini to study this recording. Is the assist process running?</p>}
    {current?.status === 'working' && <p className="mt-2 text-sm text-ink-2" role="status">{current.detail ?? 'Gemini is studying the recording.'}</p>}
    {current?.status === 'error' && <p className="mt-2 text-sm text-alarm-ink" role="alert">{current.detail ?? 'Gemini could not study the recording.'}</p>}

    {brief && <div className="mt-2 rounded border border-rule p-3 text-sm">
      <p className="font-medium text-ink">{brief.summary}</p>
      {brief.rule && <p className="mt-1 text-ink-2"><span className="text-ink-3">Rule: </span>{brief.rule}</p>}
      {brief.done_when && <p className="mt-1 text-ink-2"><span className="text-ink-3">Finished when: </span>{brief.done_when}</p>}
      <ol className="mt-2 list-decimal space-y-1 pl-5 text-xs text-ink-2">
        {brief.steps.map((step, index) => <li key={index}>
          {step.text}{step.expect && <span className="text-ink-3"> · expects: {step.expect}</span>}
        </li>)}
      </ol>
      {current?.model && <p className="mt-2 text-xs text-ink-3">Understood by {current.model}. Check it before letting Gemini drive.</p>}
    </div>}

    <div className="mt-3 flex flex-wrap gap-2">
      {brief && confirmed !== recording && !running
        && <Button variant="outline" onClick={() => onConfirm(recording)}>This is the task · confirm</Button>}
      {!running && <Button variant="solid" disabled={!available || !ready} onClick={() => act('play_smart')}
        title={ready ? 'Gemini chooses each action; the robot carries it out' : 'Confirm what Gemini understood first'}>
        Start smart replay
      </Button>}
      {running && agent?.state === 'asking' && <Button variant="solid" disabled={!available} onClick={() => act('resume')}>Continue</Button>}
    </div>

    {(running || (agent && log.length > 0)) && agent && <div className="mt-3 text-sm" role="status">
      <p className={agent.state === 'asking' ? 'font-medium text-caution-ink' : 'text-ink'}>
        {agent.state === 'asking' ? `Gemini needs you: ${agent.detail}`
          : agent.state === 'waiting' ? (gemini?.status === 'error' ? `Gemini: ${gemini.detail ?? 'no answer'} Retrying.` : 'Gemini is choosing the next action…')
          : agent.state === 'acting' ? `Doing: ${words(agent.action?.action ?? null)}${agent.action?.why ? ` · ${agent.action.why}` : ''}`
          : agent.detail}
      </p>
      <p className="mt-1 text-xs text-ink-3">{agent.count} action{agent.count === 1 ? '' : 's'} so far{agent.arm?.holding ? ' · holding an object' : ''}</p>
      {log.length > 0 && <ol className="mt-2 space-y-1 text-xs">
        {log.slice(-SHOWN).map((entry) => <li key={entry.seq} className={entry.ok ? 'text-ink-2' : 'text-alarm-ink'}>
          <span className="font-medium">{entry.seq}. {entry.action}</span>{entry.why && ` · ${entry.why}`}
          <span className="block text-ink-3">{entry.ok ? '' : 'Failed: '}{entry.detail}</span>
        </li>)}
      </ol>}
    </div>}
  </div>
}
