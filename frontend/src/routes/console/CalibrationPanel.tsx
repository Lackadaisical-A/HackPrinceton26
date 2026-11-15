import { useEffect, useState } from 'react'
import { sendCommand } from '../../app/runtime'
import { useSession } from '../../state/session'
import { bus } from '../../telemetry/bus'
import type { CalibrationAction, CalibrationMessage } from '../../telemetry/contracts'
import { Button } from '../../ui/Button'
import { cx } from '../../ui/cx'
import { Plate } from '../../ui/Plate'
import { usePolled } from '../signals/usePolled'
import { calibrationAvailable, canCalibrate } from './calibration'

const PHASE_LABELS: Record<CalibrationMessage['phase'], string> = {
  needs_pose_a: 'Start with pose A', needs_pose_b: 'Pose A captured', needs_recenter: 'Recenter needed',
  ready: 'Calibrated', preparing: 'Get into position', sampling: 'Hold still',
}
const ACTION_LABELS: Record<CalibrationAction, string> = {
  capture_a: 'Capture pose A', capture_b: 'Capture pose B', recenter: 'Recenter arm', cancel: 'Cancel / hold arm',
  capture_elbow: 'Learn elbow bend', capture_shoulder: 'Learn shoulder reach',
  capture_pan_left: 'Learn pan left', capture_pan_right: 'Learn pan right',
}
const CAPTURE_LABELS = { A: 'Pose A', B: 'Pose B', C: 'Bent elbow', D: 'Arm raised', E: 'Pan left', F: 'Pan right', R: 'Recenter in pose A' }

function Pose({ lowered, active }: { lowered?: boolean; active: boolean }) {
  return (
    <div className={cx('flex min-w-0 items-center gap-2 rounded-md border p-2.5', active ? 'border-touch-400 bg-touch-100' : 'border-rule bg-plate')}>
      <svg viewBox="0 0 88 72" className="h-16 w-20 shrink-0 text-ink-2" aria-hidden="true">
        <circle cx="22" cy="12" r="7" fill="currentColor" opacity=".2" />
        <path d="M22 24 V62 M10 69 L22 53 L34 69" stroke="currentColor" strokeWidth="5" fill="none" strokeLinecap="round" opacity=".3" />
        <path d={lowered ? 'M25 27 L34 43 L45 62 L53 65' : 'M25 27 H48 H69 L80 29'} stroke="var(--touch-600)" strokeWidth="5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx={lowered ? 34 : 48} cy={lowered ? 43 : 27} r="3" fill="var(--plate)" />
      </svg>
      <div className="min-w-0 text-xs">
        <p className="font-semibold text-ink">{lowered ? 'B · Arm down' : 'A · Reach forward'}</p>
        <p className="mt-1 text-ink-2">{lowered ? 'Straight arm toward the floor. Keep the straps in place.' : 'Straight arm at shoulder height, palm facing down.'}</p>
      </div>
    </div>
  )
}

export function CalibrationPanel() {
  const link = useSession((s) => s.link.state)
  const trial = useSession((s) => s.trial !== null)
  const [sample] = usePolled(() => ({ message: bus.latest('calibration'), age: bus.ageMs('calibration'), wall: Date.now() }), 5)
  const [request, setRequest] = useState<{ id: string; session: string; at: number; sent: boolean; reply: CalibrationMessage['request'] } | null>(null)
  // Keep our acknowledgment even when another console sends a subsequent request.
  useEffect(() => bus.on('calibration', (update) => {
    setRequest((current) => current && !current.reply && current.session === update.session && current.id === update.request?.id
      ? { ...current, reply: update.request } : current)
  }), [])
  const { message, age, wall } = sample
  const available = calibrationAvailable(message, age, link === 'open', wall)
  const sameSession = request !== null && request.session === message?.session
  const reply = sameSession ? request.reply ?? (message?.request?.id === request.id ? message.request : null) : null
  const pending = request !== null && request.sent && !reply && sameSession && wall - request.at < 3000 && link === 'open'
  const capturing = available && (message?.phase === 'preparing' || message?.phase === 'sampling')
  const ready = available && message?.phase === 'ready' && message.input_fresh
  const can = (action: CalibrationAction) => canCalibrate(action, message, action === 'cancel' ? link === 'open' : available, pending, trial)

  const act = (action: CalibrationAction) => {
    // Recheck the actual bus at click time, including data arriving since the last render.
    const current = bus.latest('calibration')
    const session = useSession.getState()
    const live = calibrationAvailable(current, bus.ageMs('calibration'), session.link.state === 'open')
    if (!current || !canCalibrate(action, current, action === 'cancel' ? session.link.state === 'open' : live, pending, session.trial !== null)) return
    const id = crypto.randomUUID()
    const sent = sendCommand({ type: 'calibrate_arm', action, request_id: id, session: current.session, revision: current.revision })
    setRequest({ id, session: current.session, at: Date.now(), sent, reply: null })
    session.log('command', `${ACTION_LABELS[action]}: ${sent ? 'requested' : 'no connection'}`, sent ? undefined : 'caution')
  }

  let requestError = ''
  if (sameSession && !request.sent) requestError = 'Request not sent. Reconnect and try again.'
  else if (reply && !reply.ok) requestError = reply.detail
  else if (sameSession && request.sent && !reply && !pending) requestError = 'No confirmation from the controller. Check its status before trying again.'

  const unavailable = link === 'simulated'
    ? 'Calibration uses your three wearable sensors. Switch to live data and start the IMU controller to calibrate.'
    : 'Waiting for Wearable Lab calibration status. Run the IMU demo with the updated controller and bridge.'
  const status = !available ? 'Unavailable' : !message?.input_fresh ? 'Waiting for sensors' : PHASE_LABELS[message.phase]

  return (
    <Plate title="Arm-to-robot calibration" note="wearable → SO-101" aside={<span className={cx('rounded-full px-2 py-1 text-xs font-medium', ready ? 'bg-touch-100 text-touch-700' : 'bg-well text-ink-2')}>{status}</span>}>
      <div className="flex flex-col gap-3">
        <p className="text-sm text-ink-2">Match your arm to the robot. Each capture gives you 3 seconds to get ready, then 2 seconds to hold still.</p>
        {available && message?.saved_config && <div className="rounded-md bg-well px-3 py-2 text-xs text-ink-2" role="status">
          <p className="font-semibold">{message.saved_config.state === 'loaded' ? 'Saved configuration loaded' : message.saved_config.state === 'saved' ? 'Configuration saved automatically' : message.saved_config.state === 'error' ? 'Configuration could not be saved or loaded' : message.saved_config.state === 'demo' ? 'Demo configuration' : 'Calibration saves automatically'}</p>
          <p className="mt-1">{message.saved_config.detail}</p>
          <p className="mt-1 break-all text-ink-3">{message.saved_config.path}</p>
        </div>}
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs" aria-label="Calibration sensors">
          {['Bicep', 'Forearm', 'Hand'].map((name, i) => (
            <span key={name} className="flex items-center gap-1.5 text-ink-2">
              <span className={cx('h-1.5 w-1.5 rounded-full', available && message?.sensors[i] ? 'bg-touch-600' : 'bg-rule-strong')} aria-hidden="true" />
              {name} · {available && message?.sensors[i] ? 'Live' : 'Waiting'}
            </span>
          ))}
        </div>
        <div className="grid gap-2 @min-[30rem]:grid-cols-2">
          <Pose active={available && (message?.phase === 'needs_pose_a' || message?.phase === 'needs_recenter' || message?.capture_pose === 'A' || message?.capture_pose === 'R')} />
          <Pose lowered active={available && (message?.phase === 'needs_pose_b' || message?.capture_pose === 'B')} />
        </div>
        <div className="rounded-md bg-well px-3 py-2.5 text-sm" role="status" aria-live="polite" aria-atomic="true">
          {capturing && message ? (
            <>
              <p className="font-semibold text-ink">{message.phase === 'preparing' ? 'Get ready' : 'Hold still'} · {message.capture_pose && CAPTURE_LABELS[message.capture_pose]} · {Math.ceil(message.remaining_s)}s</p>
              <progress className="mt-2 h-1.5 w-full accent-touch-600" max={5} value={message.phase === 'preparing' ? 3 - message.remaining_s : 5 - message.remaining_s} aria-label="Calibration capture progress" />
            </>
          ) : <p className="text-ink-2">{available ? message?.detail : unavailable}</p>}
          {pending && <p className="mt-1 text-ink-2">Waiting for controller confirmation…</p>}
        </div>
        {requestError && <p role="alert" className="text-sm text-alarm-ink">{requestError}</p>}
        {trial && <p className="text-xs text-caution-ink">Finish the current trial before changing calibration.</p>}
        <div className="flex flex-wrap gap-2">
          <Button variant={message?.can_recenter ? 'outline' : 'solid'} disabled={!can('capture_a')} onClick={() => act('capture_a')}>
            {message?.can_recenter ? 'Redo A/B' : 'Capture pose A'}
          </Button>
          <Button variant={message?.phase === 'needs_pose_b' ? 'solid' : 'outline'} disabled={!can('capture_b')} onClick={() => act('capture_b')}>Capture pose B</Button>
          <Button variant={message?.can_recenter ? 'solid' : 'outline'} disabled={!can('recenter')} onClick={() => act('recenter')}>Recenter arm</Button>
          {message && link !== 'simulated' && <Button variant="danger" disabled={!can('cancel')} onClick={() => act('cancel')}>{capturing || pending ? 'Cancel / hold arm' : 'Stop / hold arm'}</Button>}
        </div>
        <div className="border-t border-rule pt-3">
          <h3 className="text-sm font-semibold text-ink">Use the full joint range</h3>
          <p className="mt-1 text-xs text-ink-2">After A/B or recenter, teach your comfortable endpoints. These map your movement to the robot’s full elbow and shoulder travel.</p>
          <div className="mt-3 grid gap-3 @min-[30rem]:grid-cols-2">
            <div className="flex flex-col items-start gap-2">
              <p className="text-xs text-ink-2"><strong>C · Bent elbow.</strong> Bend your elbow as far as comfortable and hold still.</p>
              <Button disabled={!can('capture_elbow')} onClick={() => act('capture_elbow')}>Learn elbow bend</Button>
              <span className="text-xs text-ink-3">{available && message?.motion_ranges?.elbow_max_deg !== undefined ? `Saved: straight to ${Math.round(message.motion_ranges.elbow_max_deg)}° bend` : 'Elbow range not learned'}</span>
            </div>
            <div className="flex flex-col items-start gap-2">
              <p className="text-xs text-ink-2"><strong>D · Arm raised.</strong> Raise your straight arm to your highest comfortable reach.</p>
              <Button disabled={!can('capture_shoulder')} onClick={() => act('capture_shoulder')}>Learn shoulder reach</Button>
              <span className="text-xs text-ink-3">{available && message?.motion_ranges?.shoulder_max_deg !== undefined ? `Saved: ${Math.round(message.motion_ranges.shoulder_min_deg ?? -90)}° to +${Math.round(message.motion_ranges.shoulder_max_deg)}° elevation` : 'Shoulder range not learned'}</span>
            </div>
          </div>
          {available && !message?.range_capture && <p className="mt-2 text-xs text-caution-ink">Restart Wearable Lab to enable range capture.</p>}
          <div className="mt-3 border-t border-rule pt-3">
            <h4 className="text-sm font-semibold">Shoulder pan · left / right</h4>
            <p className="mt-1 text-xs text-ink-2">Keep your torso still and reach to each comfortable side, with your wrist out from your shoulder. Capture both sides. Your smaller sweep will span the robot’s full pan travel; pose A stays centered.</p>
            <div className="mt-3 grid gap-3 @min-[30rem]:grid-cols-2">
              <div className="flex flex-col items-start gap-2">
                <Button disabled={!can('capture_pan_left')} onClick={() => act('capture_pan_left')}>Learn pan left</Button>
                <span className="text-xs text-ink-3">{available && message?.motion_ranges?.pan_left_deg !== undefined ? `Left saved: ${Math.round(message.motion_ranges.pan_left_deg)}°` : 'Left range not learned'}</span>
              </div>
              <div className="flex flex-col items-start gap-2">
                <Button disabled={!can('capture_pan_right')} onClick={() => act('capture_pan_right')}>Learn pan right</Button>
                <span className="text-xs text-ink-3">{available && message?.motion_ranges?.pan_right_deg !== undefined ? `Right saved: ${Math.round(message.motion_ranges.pan_right_deg)}°` : 'Right range not learned'}</span>
              </div>
            </div>
            {available && !message?.pan_capture && <p className="mt-2 text-xs text-caution-ink">Restart Wearable Lab to enable shoulder pan capture.</p>}
          </div>
        </div>
        <p className="text-xs text-ink-3">{ready
          ? 'Calibration ready. Check the preview ghost on the 3D arm, then use Align & follow in Robot control. Enable the claw separately.'
          : 'Capture pauses the arm and haptics. Use Recenter with pose A after a restart; redo A/B if a sensor strap moves.'}</p>
      </div>
    </Plate>
  )
}
