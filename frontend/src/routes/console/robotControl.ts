import type { BodyJoint, CalibrationMessage, PhysicalStatus, ResponseSettings, ResponseStatus } from '../../telemetry/contracts'
import { LEAD_RANGE, RESPONSE_RANGES } from '../../telemetry/contracts'

/** Why the arm cannot be enabled now, from the controller's own status. */
export function enableHint(message: CalibrationMessage, physical: PhysicalStatus): string {
  if (!physical.connected) return 'Robot not connected. Reconnect it below.'
  if (!physical.fresh) return 'Robot readings are stale.'
  if (message.phase === 'preparing' || message.phase === 'sampling') return 'Finish the capture first.'
  if (message.phase !== 'ready') return 'Calibrate the arm, or recenter in pose A, first.'
  if (physical.mode === 'replay') return 'Stop the replay first.'
  if (physical.mode === 'live' || physical.mode === 'aligning') return 'The arm is enabled. Use STOP to pause it.'
  return physical.input_status || 'Wearable input is not ready.'
}

/** An advanced response field: a top-level setting or one joint's lead. */
export type ResponseKey = keyof typeof RESPONSE_RANGES | `lead:${BodyJoint}`

export function responseValue(values: ResponseSettings, key: ResponseKey): number {
  return key.startsWith('lead:') ? values.joint_lead_deg[key.slice(5) as BodyJoint] : values[key as keyof typeof RESPONSE_RANGES]
}

export function responseRange(key: ResponseKey): readonly [number, number] {
  return key.startsWith('lead:') ? LEAD_RANGE : RESPONSE_RANGES[key as keyof typeof RESPONSE_RANGES]
}

/** The settings the arm is moving toward: accepted changes first, else the applied ones. */
export const shownResponse = (response: ResponseStatus): ResponseSettings => response.pending ?? response.values

/** The profile those settings match, or null for custom advanced settings. */
export const shownProfile = (response: ResponseStatus): number | null =>
  response.pending ? response.pending_profile : response.profile

/** A neighbouring profile for the less/more buttons, or null at either end or from custom. */
export function nudgedProfile(response: ResponseStatus, step: -1 | 1): number | null {
  const index = shownProfile(response)
  if (index === null) return null
  const next = index + step
  return next >= 0 && next < response.profiles.length ? next : null
}

/** A typed value within its range and snapped to the step, else null. */
export function checkedValue(text: string, [lo, hi]: readonly [number, number]): number | null {
  if (text.trim() === '') return null
  const value = Number(text)
  return Number.isFinite(value) && value >= lo && value <= hi ? value : null
}
