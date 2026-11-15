import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { JOINTS } from '../../config'
import { useSession } from '../../state/session'
import { bus } from '../../telemetry/bus'
import { BODY_JOINTS, type RobotChanges } from '../../telemetry/contracts'
import { Button } from '../../ui/Button'
import { Plate } from '../../ui/Plate'
import { Segmented } from '../../ui/Segmented'
import { usePolled } from '../signals/usePolled'
import { mergeChanges, REQUEST_TIMEOUT_MS } from './requests'
import { checkedValue, nudgedProfile, responseRange, responseValue, shownProfile, shownResponse, type ResponseKey } from './robotControl'
import { useControllerRequest } from './useControllerRequest'
import { labUnavailable, useLabStatus } from './useLabStatus'

const FIELDS: { key: ResponseKey; label: string; unit: string; step: number }[] = [
  { key: 'speed_deg_s', label: 'Arm speed', unit: '°/s', step: 5 },
  // Steps count from each range's minimum: 1 ms steps keep 5, 10, 20 ms reachable from a 1 ms minimum.
  { key: 'response_ms', label: 'Smoothing', unit: 'ms', step: 1 },
  { key: 'control_hz', label: 'Control rate', unit: 'Hz', step: 5 },
  ...BODY_JOINTS.map((name, i) => ({ key: `lead:${name}` as ResponseKey, label: `${JOINTS[i].label} lead`, unit: '°', step: 0.5 })),
]

/** Local slider positions win briefly, so a drag is not pulled back by the status in between sends. */
const LOCAL_HOLD_MS = 1500

function changesFor(key: ResponseKey, value: number): RobotChanges {
  return key.startsWith('lead:') ? { joint_lead_deg: { [key.slice(5)]: value } } : { [key]: value }
}

/** A number box that commits on Enter or leaving the field, like the desktop spinbox. */
function NumberBox({ id, value, range, step, disabled, onCommit }: {
  id: string; value: number; range: readonly [number, number]; step: number; disabled: boolean; onCommit: (value: number) => void
}) {
  const [text, setText] = useState<string | null>(null)
  const commit = () => {
    if (text === null) return
    const checked = checkedValue(text, range)
    if (checked !== null && checked !== value) onCommit(checked)
    setText(null)
  }
  return (
    <input id={id} type="number" inputMode="decimal" min={range[0]} max={range[1]} step={step} disabled={disabled}
      value={text ?? String(value)} onChange={(event) => setText(event.target.value)} onBlur={commit}
      onKeyDown={(event) => { if (event.key === 'Enter') commit() }}
      aria-invalid={text !== null && checkedValue(text, range) === null ? true : undefined}
      className="w-20 min-w-0 rounded border border-rule-strong bg-plate px-2 py-1.5 text-sm tabular-nums disabled:opacity-50 aria-invalid:border-alarm" />
  )
}

/**
 * How the arm feels: five repeatable presets from more control to more speed, with the individual
 * settings under Advanced. The browser's version of Wearable Lab's Live tuning windows. Changes last
 * for the session and never enable or resume motion.
 */
export function ResponseTuning() {
  const lab = useLabStatus()
  const { message, available } = lab
  const trial = useSession((s) => s.trial !== null)
  const [now] = usePolled(() => Date.now(), 5)
  const id = useId()
  const response = available ? message?.response ?? null : null
  const tuning = available ? message?.tuning ?? null : null
  const live = !!response && !!tuning?.connected && tuning.fresh
  const canEdit = live && !trial
  const request = useControllerRequest((m) => m.tuning?.request)
  const send = request.send
  const [local, setLocal] = useState<Partial<Record<ResponseKey, { value: number; at: number }>>>({})

  // Advanced edits: one request in flight, later edits merged and sent with the revision it returns.
  const queued = useRef<RobotChanges | null>(null)
  const inFlight = useRef<{ id: string; at: number } | null>(null)
  const flush = useCallback(() => {
    const changes = queued.current
    const flight = inFlight.current
    if (!changes || (flight && Date.now() - flight.at < REQUEST_TIMEOUT_MS)) return
    let sentId = ''
    const sent = send((current, requestId) => {
      const t = current.tuning
      if (!t?.connected || !t.fresh || !current.response) return null
      sentId = requestId
      return { type: 'tune_robot', changes, request_id: requestId, session: current.session, revision: t.revision }
    }, 'Advanced response settings')
    if (sent) {
      inFlight.current = { id: sentId, at: Date.now() }
      queued.current = null
    }
  }, [send])
  useEffect(() => bus.on('calibration', (update) => {
    const flight = inFlight.current
    if (flight && update.tuning?.request?.id === flight.id) {
      inFlight.current = null
      flush()
    }
  }), [flush])

  const edit = (key: ResponseKey, value: number) => {
    setLocal((previous) => ({ ...previous, [key]: { value, at: Date.now() } }))
    queued.current = mergeChanges(queued.current, changesFor(key, value))
    flush()
  }
  const choose = (body: { profile: number } | { startup: true }, label: string) => {
    queued.current = null
    setLocal({})
    send((current, requestId) => {
      const t = current.tuning
      if (!t?.connected || !t.fresh || !current.response) return null
      return { type: 'tune_robot', ...body, request_id: requestId, session: current.session, revision: t.revision }
    }, label)
  }

  const unavailable = labUnavailable(lab)
  if (!response) {
    return (
      <Plate title="How the arm feels" note="response">
        <p className="text-sm text-ink-2">{unavailable || 'Connect the robot and start Wearable Lab with a robot port to tune its response.'}</p>
      </Plate>
    )
  }
  const shown = shownResponse(response)
  const index = shownProfile(response)
  const applying = !!tuning && tuning.revision !== tuning.applied_revision
  const active = response.profile !== null ? response.profiles[response.profile].name : 'Custom (advanced)'
  const value = (key: ResponseKey) => {
    const mine = local[key]
    return mine && now - mine.at < LOCAL_HOLD_MS ? mine.value : responseValue(shown, key)
  }
  const nudge = (step: -1 | 1) => {
    const next = nudgedProfile(response, step)
    if (next !== null) choose({ profile: next }, `Response: ${response.profiles[next].name}`)
  }

  return (
    <Plate title="How the arm feels" note="response · this session"
      aside={<span className="text-xs text-ink-2" role="status">{applying ? 'Applying…' : `Applied: ${active}`}</span>}>
      <p className="mb-3 text-sm text-ink-2">One choice sets speed, smoothing and how far each joint may run ahead. Switch any time while following.</p>
      <div className="mb-1 flex justify-between text-xs font-semibold tracking-wide text-ink-3"><span>MORE CONTROL</span><span>MORE SPEED</span></div>
      <Segmented label="Response profile" value={index === null ? 'custom' : String(index)} disabled={!canEdit || request.pending}
        options={response.profiles.map((profile, i) => ({ value: String(i), label: profile.name }))}
        onChange={(next) => choose({ profile: Number(next) }, `Response: ${response.profiles[Number(next)].name}`)}
        className="w-full justify-between" />
      <p className="mt-2 text-sm text-ink-2">{index !== null ? response.profiles[index].description
        : 'Custom advanced settings. Choose a profile to return to the five repeatable steps.'}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" disabled={!canEdit || request.pending || nudgedProfile(response, -1) === null} onClick={() => nudge(-1)}>‹ More control</Button>
        <Button size="sm" disabled={!canEdit || request.pending || nudgedProfile(response, 1) === null} onClick={() => nudge(1)}>More speed ›</Button>
        <Button size="sm" variant="quiet" disabled={!canEdit || request.pending || !response.startup} onClick={() => choose({ startup: true }, 'Response: reset to startup')}>Reset to startup</Button>
      </div>
      {request.error && <p className="mt-2 text-xs text-alarm-ink" role="alert">{request.error}</p>}
      {trial && <p className="mt-2 text-xs text-caution-ink">Finish the current trial before changing the response.</p>}
      {!live && <p className="mt-2 text-xs text-ink-2">Waiting for fresh robot readings.</p>}
      <details className="mt-3 rounded-md border border-rule">
        <summary className="cursor-pointer px-3 py-2 text-sm font-semibold">Advanced</summary>
        <div className="grid gap-3 px-3 pb-3">
          {FIELDS.map(({ key, label, unit, step }) => {
            const range = responseRange(key)
            const current = value(key)
            return (
              <div key={key} className="grid grid-cols-[minmax(7rem,1fr)_minmax(6rem,2fr)_auto] items-center gap-3">
                <label htmlFor={`${id}-${key}`} className="text-sm">{label} <span className="text-xs text-ink-3">({unit})</span></label>
                <input type="range" aria-label={label} min={range[0]} max={range[1]} step={step} value={current} disabled={!canEdit}
                  onChange={(event) => edit(key, Number(event.target.value))} className="w-full accent-touch-600" />
                <NumberBox id={`${id}-${key}`} value={current} range={range} step={step} disabled={!canEdit} onCommit={(next) => edit(key, next)} />
              </div>
            )
          })}
          <p className="text-xs text-ink-3">Less smoothing responds sooner but can add jitter. Lead is how far a command may run ahead of the
            measured joint; raise it gradually if LEAD limits motion. Control rate is a target; compare it with the measured Hz in Robot control.</p>
        </div>
      </details>
    </Plate>
  )
}
