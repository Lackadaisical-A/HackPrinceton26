import { useCallback, useEffect, useRef, useState } from 'react'
import { sendCommand } from '../../app/runtime'
import { newRequestId } from '../../lib/requestId'
import { useSession } from '../../state/session'
import { bus } from '../../telemetry/bus'
import type { CalibrationMessage, Command, Reply } from '../../telemetry/contracts'
import { usePolled } from '../signals/usePolled'
import { calibrationAvailable } from './calibration'
import { requestStatus, sameReply, type RequestState, type SentRequest } from './requests'

export interface ControllerRequest extends RequestState {
  /**
   * Builds and sends one request from the newest status, rechecked at click time. `build` returns
   * null to refuse (the controller state no longer allows it). Returns whether it was sent.
   */
  send: (build: (current: CalibrationMessage, id: string) => Command | null, label: string, options?: { allowStale?: boolean }) => boolean
  /** Forget the last request (after the operator starts something new). */
  clear: () => void
  request: SentRequest | null
}

/**
 * One request at a time to Wearable Lab, answered through the calibration status. `select` picks this
 * panel's acknowledgment slot; only the answer with our own id is kept, so another console's
 * requests never look like ours.
 */
export function useControllerRequest(select: (message: CalibrationMessage) => Reply | null | undefined,
  { needApplied = false }: { needApplied?: boolean } = {}): ControllerRequest {
  const link = useSession((s) => s.link.state)
  const [now] = usePolled(() => Date.now(), 5)
  const [request, setRequest] = useState<SentRequest | null>(null)
  const [reply, setReply] = useState<Reply | null>(null)
  const requestRef = useRef<SentRequest | null>(null)
  const selectRef = useRef(select)
  useEffect(() => { selectRef.current = select }, [select])

  useEffect(() => bus.on('calibration', (update) => {
    const current = requestRef.current
    if (!current || update.session !== current.session) return
    const answer = selectRef.current(update)
    // The latest answer for our id: an enable is first accepted, then reported as applied.
    if (answer && answer.id === current.id) setReply((previous) => (sameReply(previous, answer) ? previous : answer))
  }), [])

  const send = useCallback<ControllerRequest['send']>((build, label, options) => {
    const current = bus.latest('calibration')
    const session = useSession.getState()
    const open = session.link.state === 'open'
    if (!current || (!options?.allowStale && !calibrationAvailable(current, bus.ageMs('calibration'), open))) return false
    const id = newRequestId()
    const command = build(current, id)
    if (!command) return false
    const sent = sendCommand(command)
    const next = { id, session: current.session, at: Date.now(), sent }
    requestRef.current = next
    setRequest(next)
    setReply(null)
    session.log('command', `${label}: ${sent ? 'requested' : 'not sent'}`, sent ? undefined : 'caution')
    return sent
  }, [])

  const clear = useCallback(() => {
    requestRef.current = null
    setRequest(null)
    setReply(null)
  }, [])

  const session = bus.latest('calibration')?.session ?? null
  return { send, clear, request, ...requestStatus(request, reply, session, now, link === 'open', needApplied) }
}
