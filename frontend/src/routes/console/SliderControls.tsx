import { useEffect, useState } from 'react'
import { sendCommand } from '../../app/runtime'
import { useSession } from '../../state/session'
import { bus } from '../../telemetry/bus'
import type { CalibrationMessage, SliderAction, SliderControl } from '../../telemetry/contracts'
import { Button } from '../../ui/Button'
import { calibrationAvailable } from './calibration'
import { usePolled } from '../signals/usePolled'

export function SliderControls({ message, live, trial }: { message: CalibrationMessage | null; live: boolean; trial: boolean }) {
  const [request, setRequest] = useState<{ id: string; at: number; sent: boolean; reply: SliderControl['request'] } | null>(null)
  const [now] = usePolled(() => Date.now(), 5)
  useEffect(() => bus.on('calibration', (update) => {
    const reply = update.slider?.request
    if (update.session !== message?.session || !reply) return
    setRequest((current) => current?.id === reply.id ? { ...current, reply } : current)
  }), [message?.session])
  const slider = message?.slider
  const reply = request?.reply ?? (slider?.request?.id === request?.id ? slider?.request : null)
  const pending = !!request?.sent && !reply && now - request.at < 3000
  const available = live && !!slider && !pending
  const act = (action: SliderAction) => {
    const current = bus.latest('calibration')
    if (!current?.slider || !current.tuning?.fresh || !current.tuning.connected || (pending && action !== 'release')
      || !calibrationAvailable(current, bus.ageMs('calibration'), useSession.getState().link.state === 'open')
      || (action !== 'release' && useSession.getState().trial)) return
    const id = crypto.randomUUID()
    const sent = sendCommand({ type: 'control_slider', action, request_id: id, session: current.session, revision: current.revision })
    setRequest({ id, at: Date.now(), sent, reply: null })
  }
  const error = request && !request.sent ? 'Slider request not sent.' : reply?.ok === false ? reply.detail
    : request && !reply && !pending ? 'No slider confirmation. Check the controller before retrying.' : ''
  return <div className="mt-4 border-t border-rule pt-3">
    <h3 className="text-sm font-semibold">Joystick X → slider</h3>
    <p className="mt-1 text-xs text-ink-2">Release the slider and place it at a rail endpoint. Mark that endpoint after each restart, then center X and enable. Push X to travel; release X to hold.</p>
    <div className="mt-3 flex flex-wrap gap-2">
      <Button disabled={!live || !slider} onClick={() => act('release')}>Release slider</Button>
      <Button disabled={!available || trial || slider?.enabled} onClick={() => act('zero')}>At 0% endpoint</Button>
      <Button disabled={!available || trial || slider?.enabled} onClick={() => act('hundred')}>At 100% endpoint</Button>
      <Button variant="solid" disabled={!available || trial || !slider?.referenced || slider.enabled || !message?.input_fresh || message.phase !== 'ready'} onClick={() => act('enable')}>Enable X control</Button>
    </div>
    <p role="status" className="mt-2 text-xs text-ink-2">{live ? slider?.detail ?? 'Restart the controller for slider support.' : 'Waiting for slider controller.'}</p>
    {live && slider?.referenced && <p className="mt-1 text-xs text-ink-2">Position {slider.pos?.toFixed(1)}% · X control {slider.enabled ? 'ON' : 'OFF'}</p>}
    {pending && <p className="mt-1 text-xs text-ink-2">Waiting for slider confirmation…</p>}
    {error && <p role="alert" className="mt-1 text-xs text-alarm-ink">{error}</p>}
  </div>
}
