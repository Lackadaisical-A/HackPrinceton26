import { useEffect, useState } from 'react'
import { sendCommand } from '../../app/runtime'
import { useSession } from '../../state/session'
import { bus } from '../../telemetry/bus'
import type { CalibrationMessage, RobotTuning } from '../../telemetry/contracts'
import { Button } from '../../ui/Button'
import { usePolled } from '../signals/usePolled'
import { calibrationAvailable } from './calibration'

export function ReconnectRobot({ message }: { message: CalibrationMessage | null }) {
  const link = useSession((s) => s.link.state)
  const [now] = usePolled(() => Date.now(), 5)
  const [request, setRequest] = useState<{ id: string; at: number; sent: boolean; reply: RobotTuning['reconnect_request'] } | null>(null)
  useEffect(() => bus.on('calibration', (update) => {
    const reply = update.tuning?.reconnect_request
    if (update.session !== message?.session || !reply) return
    setRequest((current) => current?.id === reply.id ? { ...current, reply } : current)
  }), [message?.session])
  const tuning = message?.tuning
  const reply = request?.reply ?? (tuning?.reconnect_request?.id === request?.id ? tuning?.reconnect_request : null)
  const pending = !!request?.sent && !reply && now - request.at < 3000
  const available = calibrationAvailable(message, bus.ageMs('calibration'), link === 'open', now)
  const reconnect = () => {
    const current = bus.latest('calibration')
    if (pending || !current?.tuning?.can_reconnect
      || !calibrationAvailable(current, bus.ageMs('calibration'), useSession.getState().link.state === 'open')) return
    const id = crypto.randomUUID()
    const sent = sendCommand({ type: 'reconnect_robot', request_id: id, session: current.session, revision: current.revision })
    setRequest({ id, at: Date.now(), sent, reply: null })
  }
  if (!tuning || tuning.connected || !available) return null
  const error = request && !request.sent ? 'Reconnect request not sent.' : reply?.ok === false ? reply.detail
    : request && !reply && !pending ? 'No reconnect confirmation. Check the controller.' : ''
  return <div className="mb-3 rounded-md border border-rule bg-well p-3">
    <p role="status" className="mb-2 text-sm text-ink-2">{tuning.detail || 'Robot disconnected.'}</p>
    <Button disabled={!tuning.can_reconnect || pending} onClick={reconnect}>Reconnect robot</Button>
    <p className="mt-2 text-xs text-ink-2">Reconnects without enabling movement. Arm, claw and slider need manual enable afterward.</p>
    {pending && <p className="mt-1 text-xs text-ink-2">Requesting connection…</p>}
    {error && <p role="alert" className="mt-1 text-xs text-alarm-ink">{error}</p>}
  </div>
}
