import { create } from 'zustand'
import type { DistanceHint, Phase, VisionStatus } from '../telemetry/contracts'
import type { LinkStatus } from '../telemetry/sources/types'

/**
 * Session state: the discrete facts the interface renders with React.
 *
 * Continuous values (offsets, pressures, joint angles) are deliberately absent. They change
 * 30 to 50 times a second and are drawn straight from the telemetry bus.
 */

/**
 * What the vision stream currently means for the operator.
 * - waiting: no vision message has arrived yet.
 * - silent: messages were arriving and have stopped.
 * - late: messages are arriving, but they describe frames older than the contract allows.
 * The other values are the publisher's own status. Only "ok" carries guidance.
 */
export type VisionState = VisionStatus | 'waiting' | 'silent' | 'late'

export interface VisionView {
  state: VisionState
  phase: Phase
  label: string | null
  distance: DistanceHint | null
  aligned: boolean
}

export type AlertLevel = 'caution' | 'warning'
export type AlertWhere = 'camera' | 'touch' | 'arm' | 'armband' | 'link'

export interface Alert {
  key: string
  level: AlertLevel
  /** The part of the screen the alert concerns. It is shown there, not in a global banner. */
  where: AlertWhere
  text: string
  /** False once the condition has passed but the alert is still held on screen. */
  active: boolean
  since: number
}

export type EventKind = 'caption' | 'phase' | 'alert' | 'trial' | 'record' | 'link' | 'command'

export interface LogEvent {
  id: number
  /** Epoch milliseconds. */
  at: number
  kind: EventKind
  text: string
  level?: AlertLevel
}

export interface ActiveTrial {
  startedAt: number
  haptics: boolean
  drops: number
  deformations: number
}

export interface RecordingState {
  startedAt: number
}

const EVENT_LIMIT = 200

export const INITIAL_VISION: VisionView = {
  state: 'waiting',
  phase: 'search',
  label: null,
  distance: null,
  aligned: false,
}

interface SessionStore {
  link: LinkStatus
  vision: VisionView
  /** The most recent spoken summary. */
  caption: { text: string; at: number } | null
  /**
   * Whether haptic feedback is on. Set from this interface and corrected by the haptics stream
   * whenever that reports otherwise, so the stream is the truth when it exists.
   */
  haptics: boolean
  /** The label the operator asked vision to target. Null means any object. */
  targetLabel: string | null
  alerts: Alert[]
  events: LogEvent[]
  recording: RecordingState | null
  trial: ActiveTrial | null

  setLink: (link: LinkStatus) => void
  setVision: (vision: VisionView) => void
  setCaption: (text: string, at: number) => void
  setHaptics: (enabled: boolean) => void
  setTargetLabel: (label: string | null) => void
  setAlerts: (alerts: Alert[]) => void
  setRecording: (recording: RecordingState | null) => void
  setTrial: (trial: ActiveTrial | null) => void
  log: (kind: EventKind, text: string, level?: AlertLevel) => void
  /** Clears everything that described the previous data source. */
  resetStreams: () => void
}

let nextEventId = 1

export const useSession = create<SessionStore>()((set) => ({
  link: { state: 'idle', detail: null, retryAt: null },
  vision: INITIAL_VISION,
  caption: null,
  haptics: true,
  targetLabel: null,
  alerts: [],
  events: [],
  recording: null,
  trial: null,

  setLink: (link) => set({ link }),
  setVision: (vision) => set({ vision }),
  setCaption: (text, at) => set({ caption: { text, at } }),
  setHaptics: (haptics) => set({ haptics }),
  setTargetLabel: (targetLabel) => set({ targetLabel }),
  setAlerts: (alerts) => set({ alerts }),
  setRecording: (recording) => set({ recording }),
  setTrial: (trial) => set({ trial }),
  log: (kind, text, level) =>
    set((s) => ({
      events: [{ id: nextEventId++, at: Date.now(), kind, text, level }, ...s.events].slice(0, EVENT_LIMIT),
    })),
  resetStreams: () => set({ vision: INITIAL_VISION, caption: null, alerts: [], targetLabel: null }),
}))

/** Alerts that concern one part of the screen, most severe first. */
export function alertsFor(alerts: readonly Alert[], where: AlertWhere): Alert[] {
  return alerts
    .filter((a) => a.where === where)
    .sort((a, b) => Number(b.level === 'warning') - Number(a.level === 'warning'))
}
