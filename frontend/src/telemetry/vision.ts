import { LIMITS } from '../config'
import type { VisionState, VisionView } from '../state/session'
import type { VisionMessage } from './contracts'

/** While the publisher's watchdog is sending `degraded` heartbeats, they arrive this far apart. */
const HEARTBEAT_MS = 500

/**
 * Applies the vision contract's two consumer rules:
 *   1. Act only on status "ok".
 *   2. Treat silence, or a `t` older than 0.5 s, as no guidance.
 *
 * @param latest   the newest vision message, or null if none has arrived
 * @param silentMs milliseconds since that message arrived
 * @param wallS    the current time in Unix seconds
 */
export function classifyVision(latest: VisionMessage | null, silentMs: number, wallS: number): VisionState {
  if (!latest) return 'waiting'
  // Heartbeats are 0.5 s apart by design, so allow for that before calling the stream silent.
  const silentLimit = latest.status === 'degraded' ? HEARTBEAT_MS * 2.5 : LIMITS.visionStaleS * 1000
  if (silentMs > silentLimit) return 'silent'
  if (latest.status !== 'degraded' && wallS - latest.t > LIMITS.visionStaleS) return 'late'
  return latest.status
}

/** True when guidance may be shown and acted on. */
export const hasGuidance = (state: VisionState): boolean => state === 'ok'

/** The discrete facts the interface shows for a message in a given state. */
export function viewOf(latest: VisionMessage | null, state: VisionState, previous: VisionView): VisionView {
  const usable = hasGuidance(state) && latest !== null
  return {
    state,
    // A degraded heartbeat repeats the last published phase (vision before 14 Nov 2026 always said "search"),
    // which the previous view already shows, so keep that.
    phase: latest && latest.status !== 'degraded' ? latest.phase : previous.phase,
    label: usable ? (latest.target?.label ?? null) : null,
    distance: usable ? (latest.guidance?.distance_hint ?? null) : null,
    aligned: usable ? (latest.guidance?.aligned ?? false) : false,
  }
}

export function sameView(a: VisionView, b: VisionView): boolean {
  return (
    a.state === b.state &&
    a.phase === b.phase &&
    a.label === b.label &&
    a.distance === b.distance &&
    a.aligned === b.aligned
  )
}

/** A short status line for each state, in the operator's terms. */
export const VISION_STATE_TEXT: Record<VisionState, string> = {
  ok: 'Target locked',
  no_target: 'No target',
  low_confidence: 'Not sure yet',
  degraded: 'Vision feed lost',
  silent: 'No vision data',
  late: 'Vision data delayed',
  waiting: 'Waiting for vision',
}
