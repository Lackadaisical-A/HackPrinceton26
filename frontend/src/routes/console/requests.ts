import type { BodyJoint, Reply, RobotChanges } from '../../telemetry/contracts'

/** How long a request may go unconfirmed before the panel says so. Requests are never retried. */
export const REQUEST_TIMEOUT_MS = 3000

/** A request this console sent, waiting for the controller's answer in the calibration status. */
export interface SentRequest {
  id: string
  /** The controller instance it was sent to; a restart makes the request meaningless. */
  session: string
  at: number
  sent: boolean
}

export interface RequestState {
  /** Sent and waiting within the timeout. */
  pending: boolean
  /** Accepted, and carried out when the panel needs that. */
  done: boolean
  /** Why the request failed or could not be confirmed, else ''. */
  error: string
  /** The controller's latest words about it. */
  detail: string
}

const IDLE: RequestState = { pending: false, done: false, error: '', detail: '' }

/**
 * Where one request stands. `needApplied` waits for the controller to report that it carried the
 * request out (an enable), not only that it accepted it.
 */
export function requestStatus(request: SentRequest | null, reply: Reply | null, currentSession: string | null,
  nowMs: number, linkOpen: boolean, needApplied = false, timeoutMs = REQUEST_TIMEOUT_MS): RequestState {
  if (!request) return IDLE
  if (!request.sent) return { ...IDLE, error: 'Request not sent. Reconnect and try again.' }
  if (reply && reply.id === request.id) {
    if (!reply.ok) return { ...IDLE, error: reply.detail || 'Refused by the controller.' }
    if (!needApplied || reply.applied === true) return { ...IDLE, done: true, detail: reply.detail }
  }
  if (currentSession !== null && currentSession !== request.session) {
    return { ...IDLE, error: 'The controller restarted. Check its status before trying again.' }
  }
  if (linkOpen && nowMs - request.at < timeoutMs) return { ...IDLE, pending: true, detail: reply?.detail ?? '' }
  return {
    ...IDLE,
    error: reply ? `Accepted but not confirmed: ${reply.detail}` : 'No confirmation from the controller. Check its status before trying again.',
  }
}

/** Same answer, to skip re-rendering when a status repeats it. */
export const sameReply = (a: Reply | null, b: Reply | null): boolean =>
  a === b || (!!a && !!b && a.id === b.id && a.ok === b.ok && a.applied === b.applied && a.detail === b.detail)

/** Combine two batches of tuning changes; later values win, per-joint leads merge. */
export function mergeChanges(a: RobotChanges | null, b: RobotChanges): RobotChanges {
  if (!a) return { ...b, ...(b.joint_lead_deg ? { joint_lead_deg: { ...b.joint_lead_deg } } : {}) }
  const leads: Partial<Record<BodyJoint, number>> | undefined = a.joint_lead_deg || b.joint_lead_deg
    ? { ...a.joint_lead_deg, ...b.joint_lead_deg } : undefined
  return { ...a, ...b, ...(leads ? { joint_lead_deg: leads } : {}) }
}

export const isEmptyChanges = (changes: RobotChanges | null): boolean =>
  !changes || Object.entries(changes).every(([key, value]) =>
    key === 'joint_lead_deg' ? Object.keys(value as object).length === 0 : value === undefined)
