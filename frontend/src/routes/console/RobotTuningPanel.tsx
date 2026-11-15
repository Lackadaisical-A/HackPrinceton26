import { useEffect, useState } from 'react'
import { sendCommand } from '../../app/runtime'
import { useSession } from '../../state/session'
import { bus } from '../../telemetry/bus'
import { ROBOT_SETTING_RANGES, type CalibrationMessage, type RobotSettings, type RobotTuning } from '../../telemetry/contracts'
import { Button } from '../../ui/Button'
import { Plate } from '../../ui/Plate'
import { usePolled } from '../signals/usePolled'
import { calibrationAvailable } from './calibration'
import { SliderControls } from './SliderControls'

const FIELDS = [
  ['speed_deg_s', 'Arm speed', '°/s', 1],
  ['claw_speed_per_s', 'Claw speed', 'travel/s', 0.1],
  ['max_load', 'Arm load cutoff', 'raw counts', 1],
  ['gripper_max_load', 'Gripper load cutoff', 'raw counts', 1],
  ['gripper_contact_load', 'Grip contact load', 'raw counts', 1],
  ['gripper_hold_load', 'Grip holding load', 'raw counts', 1],
] as const

export function RobotTuningPanel() {
  const [message] = usePolled(() => bus.latest('calibration'), 5)
  // A controller restart discards drafts and acknowledgments from the old instance.
  return <TuningForm key={message?.session ?? 'waiting'} message={message} />
}

function TuningForm({ message }: { message: CalibrationMessage | null }) {
  const link = useSession((s) => s.link.state)
  const trial = useSession((s) => s.trial !== null)
  const [now] = usePolled(() => Date.now(), 5)
  const [draft, setDraft] = useState<Partial<Record<keyof RobotSettings, string | boolean>>>({})
  const [request, setRequest] = useState<{ id: string; at: number; sent: boolean; reply: RobotTuning['request'] } | null>(null)
  useEffect(() => bus.on('calibration', (update) => {
    const reply = update.tuning?.request
    if (update.session !== message?.session || !reply) return
    setRequest((current) => current?.id === reply.id && !current.reply?.applied ? { ...current, reply } : current)
  }), [message?.session])
  const tuning = message?.tuning
  const live = calibrationAvailable(message, bus.ageMs('calibration'), link === 'open', now)
    && tuning?.connected && tuning.fresh
  const reply = request?.reply ?? (tuning?.request?.id === request?.id ? tuning?.request : null)
  const pending = !!request?.sent && !reply?.applied && reply?.ok !== false && now - request.at < 3000
  const busy = pending || !!tuning && tuning.revision !== tuning.applied_revision
  const canEdit = !!live && !trial && !busy
  // After confirmation, show applied values until the operator starts another edit.
  const edits = reply?.applied ? {} : draft
  const changes: Partial<RobotSettings> = {}
  let valid = true
  for (const [key] of FIELDS) {
    if (edits[key] === undefined) continue
    const value = Number(edits[key])
    const [lo, hi] = ROBOT_SETTING_RANGES[key]
    if (!Number.isFinite(value) || value < lo || value > hi) valid = false
    else if (value !== tuning?.values[key]) changes[key] = value
  }
  if (typeof edits.load_stops_enabled === 'boolean' && edits.load_stops_enabled !== tuning?.values.load_stops_enabled) {
    changes.load_stops_enabled = edits.load_stops_enabled
  }
  if (Number(edits.gripper_hold_load ?? tuning?.values.gripper_hold_load)
    > Number(edits.gripper_contact_load ?? tuning?.values.gripper_contact_load)) valid = false
  const edit = (key: keyof RobotSettings, value: string | boolean) => {
    setDraft({ ...edits, [key]: value })
    if (reply?.applied) setRequest(null)
  }
  const apply = () => {
    const current = bus.latest('calibration')
    const session = useSession.getState()
    if (!valid || !Object.keys(changes).length || !canEdit || !current?.tuning
      || current.session !== message?.session || !current.tuning.connected || !current.tuning.fresh
      || !calibrationAvailable(current, bus.ageMs('calibration'), session.link.state === 'open') || session.trial) return
    const id = crypto.randomUUID()
    const sent = sendCommand({ type: 'tune_robot', changes, request_id: id, session: current.session, revision: current.tuning.revision })
    setRequest({ id, at: Date.now(), sent, reply: null })
    session.log('command', `Robot settings: ${sent ? 'requested' : 'not sent'}`, sent ? undefined : 'caution')
  }
  const error = request && !request.sent ? 'Settings not sent. Reconnect and try again.'
    : reply?.ok === false ? reply.detail
      : request && !reply?.applied && !pending ? 'No confirmation from the controller. Review applied values before retrying.' : ''
  return (
    <Plate title="Robot speed & load" note="live controls" aside={<span className="text-xs text-ink-2">{live ? tuning?.enabled ? 'Following' : 'Paused' : 'Unavailable'}</span>}>
      <p className="mb-3 text-sm text-ink-2">Adjust speed and software load stops independently. Changes last this session.</p>
      {!live && <p className="mb-3 text-xs text-ink-2">Connect the arm and start the updated IMU controller to adjust settings.</p>}
      <label className="mb-3 flex items-center gap-2 text-sm font-semibold">
        <input type="checkbox" checked={Boolean(edits.load_stops_enabled ?? tuning?.values.load_stops_enabled ?? false)} disabled={!canEdit}
          onChange={(event) => edit('load_stops_enabled', event.target.checked)} className="h-4 w-4 accent-touch-600" />
        Software load stops
      </label>
      <p className="mb-3 text-xs text-ink-2">Default off. When on, a load reading at or above either cutoff pauses motion. Servo firmware protections are unchanged.</p>
      <div className="grid gap-3 @min-[30rem]:grid-cols-2">
        {FIELDS.filter(([key]) => !key.startsWith('gripper_contact') && !key.startsWith('gripper_hold') || tuning?.values[key] !== undefined).map(([key, label, unit, step]) => (
          <div key={key} className="min-w-0 rounded-md border border-rule p-3">
            <label htmlFor={`robot-${key}`} className="text-sm font-semibold">{label}</label>
            <div className="mt-2 flex items-center gap-2">
              <input id={`robot-${key}`} type="number" min={ROBOT_SETTING_RANGES[key][0]} max={ROBOT_SETTING_RANGES[key][1]} step={step}
                value={String(edits[key] ?? tuning?.values[key] ?? '')} disabled={!canEdit}
                onChange={(event) => edit(key, event.target.value)}
                className="min-w-0 w-24 rounded border border-rule-strong bg-plate px-2 py-1.5 text-sm tabular-nums disabled:opacity-50" />
              <span className="text-xs text-ink-2">{unit}</span>
            </div>
            <p className="mt-2 text-xs text-ink-3">Applied: {live ? `${tuning?.values[key]} ${unit}` : '—'}</p>
            <p className="mt-1 text-xs text-ink-3">Range {ROBOT_SETTING_RANGES[key][0]}–{ROBOT_SETTING_RANGES[key][1]}</p>
          </div>
        ))}
      </div>
      {tuning?.grasp && <div className="mt-3 rounded-md border border-rule p-3" role="status">
        <p className="text-sm font-semibold">{live ? tuning.grasp.holding ? 'Holding object' : 'Gripper contact sensing' : 'Gripper status unavailable'}</p>
        <p className="mt-1 text-xs text-ink-2">{live ? `${tuning.grasp.detail} Load: ${Math.round(tuning.grasp.load)} raw counts.` : 'Waiting for fresh robot readings.'}</p>
        <p className="mt-1 text-xs text-ink-3">Contact load detects a blocked closing grip. Holding load adjusts the squeeze after contact; keep it at or below contact load. Opening releases immediately. These work with software load stops off.</p>
      </div>}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button variant="solid" disabled={!canEdit || !valid || !Object.keys(changes).length} onClick={apply}>Apply settings</Button>
        <Button disabled={!canEdit || !Object.keys(edits).length} onClick={() => { setDraft({}); setRequest(null) }}>Discard edits</Button>
      </div>
      <p role="status" className="mt-2 text-xs text-ink-2">{busy ? 'Waiting for controller confirmation…' : reply?.applied ? 'Settings applied.' : live ? `Software load stops: ${tuning?.values.load_stops_enabled ? 'ON' : 'OFF'}. Applying settings does not enable motion.` : 'Waiting for confirmed settings.'}</p>
      {trial && <p className="mt-2 text-xs text-caution-ink">Finish the current trial before adjusting settings.</p>}
      {!valid && <p role="alert" className="mt-2 text-xs text-alarm-ink">Enter values within the displayed limits, with holding load at or below contact load.</p>}
      {error && <p role="alert" className="mt-2 text-xs text-alarm-ink">{error}</p>}
      <SliderControls message={message} live={!!live} trial={trial} />
    </Plate>
  )
}
