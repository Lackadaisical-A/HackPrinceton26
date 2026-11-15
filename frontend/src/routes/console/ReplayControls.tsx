import { useEffect, useRef, useState } from 'react'
import { sendCommand } from '../../app/runtime'
import { useSession } from '../../state/session'
import { bus } from '../../telemetry/bus'
import type { CalibrationMessage, ReplayAction, ReplayStatus } from '../../telemetry/contracts'
import { Button } from '../../ui/Button'
import { calibrationAvailable } from './calibration'
import { Segmented } from '../../ui/Segmented'
import { SmartReplay } from './SmartReplay'
import { confirmedBrief } from './smartTask'
import { usePolled } from '../signals/usePolled'

const BUSY = ['returning', 'playing', 'finishing', 'smart']
type Mode = 'exact' | 'smart'
const MODES = [{ value: 'exact', label: 'Replay exactly' }, { value: 'smart', label: 'Smart replay (Gemini)' }] as const

export function ReplayControls({ message, live, trial }: { message: CalibrationMessage | null; live: boolean; trial: boolean }) {
  const [now] = usePolled(() => Date.now(), 5)
  const [request, setRequest] = useState<{ id: string; at: number; sent: boolean; reply: ReplayStatus['request'] } | null>(null)
  const clickTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [mode, setMode] = useState<Mode>('exact')
  const [confirmed, setConfirmed] = useState<number | null>(null)
  const replay = message?.replay
  const link = useSession((s) => s.link.state)
  const robotReady = !!message?.tuning?.connected && !!message.tuning.fresh
  const reply = request?.reply ?? (replay?.request?.id === request?.id ? replay?.request : null)
  const pending = !!request?.sent && reply?.ok !== false && !reply?.applied && now - request.at < 3000
  const playing = !!replay && BUSY.includes(replay.phase)
  // A smart replay in progress is shown whatever the switch says.
  const smart = replay?.phase === 'smart' || (mode === 'smart' && !playing)
  const available = live && robotReady && !!replay && !trial && !pending && !!message?.input_fresh && message.phase === 'ready'
  useEffect(() => bus.on('calibration', (update) => {
    const ack = update.replay?.request
    if (!ack || update.session !== message?.session) return
    setRequest((current) => current?.id === ack.id ? { ...current, reply: ack } : current)
  }), [message?.session])
  useEffect(() => {
    // Cancel a deferred single click on disconnect, recenter, Stop, or another replay action.
    return () => { if (clickTimer.current !== null) clearTimeout(clickTimer.current); clickTimer.current = null }
  }, [message?.session, message?.revision, replay?.revision, live, trial, robotReady])
  const act = (action: ReplayAction) => {
    if (clickTimer.current !== null) clearTimeout(clickTimer.current)
    clickTimer.current = null
    const current = bus.latest('calibration')
    const session = useSession.getState()
    if (!current?.replay || session.link.state !== 'open') return
    if (action !== 'stop' && (session.trial || pending || current.phase !== 'ready' || !current.input_fresh
      || !current.tuning?.connected || !current.tuning.fresh
      || !calibrationAvailable(current, bus.ageMs('calibration'), true))) return
    const id = crypto.randomUUID()
    const sent = sendCommand({ type: 'replay_robot', action, request_id: id, session: current.session,
      revision: current.revision, replay_revision: current.replay.revision })
    setRequest({ id, at: Date.now(), sent, reply: null })
    session.log('command', `Robot replay ${action}: ${sent ? 'requested' : 'not sent'}`, sent ? undefined : 'caution')
  }
  const click = () => {
    if (!available) return
    if (playing) { act('stop'); return }
    if (clickTimer.current !== null) { act('record'); return }
    clickTimer.current = setTimeout(() => {
      clickTimer.current = null
      const latest = bus.latest('calibration')?.replay
      if (latest?.phase !== 'ready') return
      if (!smart) act('play')
      else if (confirmedBrief(bus.latest('task'), latest, confirmed)) act('play_smart')
    }, 350)
  }
  const error = request && !request.sent ? 'Replay request not sent.' : reply?.ok === false ? reply.detail
    : request && !reply?.applied && !pending ? 'No replay confirmation. Check the robot status.' : ''
  return <div>
    <Segmented label="How to replay" value={smart ? 'smart' : 'exact'} options={MODES} disabled={playing}
      onChange={(value) => setMode(value)} />
    <p className="mt-2 text-xs text-ink-2">{smart
      ? 'Double-click this button to start or finish recording. Once you confirm what Gemini understood below, single-click to let Gemini drive. The joystick button always replays exactly; hold it for 0.7s to stop.'
      : 'On this button or the joystick: double-click to start or finish recording; single-click to return to the first pose and replay. Hold the joystick button for 0.7s to stop.'}</p>
    <div className="mt-3 flex flex-wrap gap-2">
      <Button variant={replay?.phase === 'recording' ? 'danger' : 'solid'} disabled={!available} onClick={click}>
        {replay?.phase === 'smart' ? 'Stop smart replay' : playing ? 'Stop replay' : replay?.phase === 'recording' ? 'Recording · double-click to finish'
          : smart ? 'Smart replay / double-click to record' : 'Replay / double-click to record'}
      </Button>
      <Button variant="danger" disabled={!message?.replay || link !== 'open'} onClick={() => act('stop')}>Stop / hold</Button>
    </div>
    <p role="status" className="mt-2 text-sm text-ink-2">{live && replay ? replay.detail : 'Connect the updated IMU controller for replay.'}</p>
    {replay && <p className="mt-1 text-xs text-ink-3">{replay.duration_s.toFixed(1)}s recorded · {replay.includes_slider ? 'Arm, claw and slider' : 'Arm and claw'} · This session · 120s maximum</p>}
    {playing && replay && replay.phase !== 'smart' && <progress className="mt-2 h-1.5 w-full accent-touch-600" max={Math.max(.1, replay.duration_s)} value={replay.elapsed_s} aria-label="Replay progress" />}
    {pending && <p className="mt-1 text-xs text-ink-2">Waiting for robot confirmation…</p>}
    {error && <p role="alert" className="mt-1 text-xs text-alarm-ink">{error}</p>}
    {smart && <SmartReplay replay={live ? replay : null} available={available} act={act} confirmed={confirmed} onConfirm={setConfirmed} />}
  </div>
}
