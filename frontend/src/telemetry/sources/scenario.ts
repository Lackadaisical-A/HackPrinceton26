import { AIM_DEFAULT, DETECTOR_CLASSES } from '../../config'
import type { Aim } from '../../lib/aim'
import { clamp, lerp, smoothstep } from '../../lib/math'
import type { BBox, DistanceHint, Phase, VisionStatus } from '../contracts'

/**
 * The simulator's script: one pick-and-place, looped.
 *
 * `scenarioAt` is a pure function of time, so the same instant always yields the same frame and
 * the script can be unit-tested. Everything here is invented to exercise the interface. It says
 * nothing about how the real arm, camera or sensors behave.
 */

export const CYCLE_S = 36

/** Start time of each phase within a cycle, in seconds. Search wraps around the end. */
export const PHASE_STARTS: Record<Phase, number> = {
  search: 31.2,
  approach: 2.8,
  grasp: 11,
  carry: 14,
  place: 23,
  release: 27,
}

/** The scripted over-squeeze happens on odd cycles, around this time. */
export const SQUEEZE_AT_S = 18.6

/**
 * What a squeeze peaks at. Squeezes blend toward this level instead of adding to the grip, so
 * the hand's wobble cannot leave one short of LIMITS.gripCaution, and pad noise has headroom.
 */
const SQUEEZE_PEAK = 0.95

export interface SimFrame {
  cycle: number
  phase: Phase
  status: VisionStatus
  label: string
  /** The object in the wrist view. Null when it is out of frame. Set even before it is locked. */
  box: BBox | null
  confidence: number
  dx: number
  dy: number
  distance: DistanceHint
  /** Overall grip level, 0 to 1, before it is spread over the pads. */
  grip: number
  /** True while the object is in the gripper. */
  held: boolean
  /** Degrees, in JOINTS order. The gripper is 0 (closed) to 100 (open). */
  joints: number[]
  /** Relative load, -1 to 1, in JOINTS order. */
  loads: number[]
  /** Armband orientation in radians. */
  roll: number
  pitch: number
  yaw: number
}

const bell = (x: number): number => Math.exp(-x * x)

const KEY_TIMES = [0, 2.8, 11, 14, 18.5, 23, 27, 30, 33, 36]
const KEY_PAN = [-38, -30, -30, -30, 0, 32, 32, 32, 0, -38]
const KEY_LIFT = [-35, -30, 18, 20, -15, -10, 22, 22, -20, -35]
const KEY_ELBOW = [60, 55, 25, 22, 48, 45, 18, 18, 50, 60]
const KEY_WRIST = [30, 28, 38, 40, 30, 30, 42, 42, 30, 30]

function keyed(values: readonly number[], t: number): number {
  for (let i = 1; i < KEY_TIMES.length; i++) {
    if (t <= KEY_TIMES[i]) {
      const span = KEY_TIMES[i] - KEY_TIMES[i - 1]
      return lerp(values[i - 1], values[i], smoothstep((t - KEY_TIMES[i - 1]) / span))
    }
  }
  return values[values.length - 1]
}

/** Width over height of each object as the camera sees it. */
const ASPECT: Record<string, number> = { cup: 0.85, bottle: 0.42, 'pill bottle': 0.58, box: 1.25 }

/** A box of the given centre and frame-area fraction, clipped to the frame. */
function boxAround(cx: number, cy: number, area: number, label: string): BBox | null {
  const aspect = (ASPECT[label] ?? 1) * (9 / 16)
  const w = Math.sqrt(area * aspect)
  const h = area / w
  const x1 = clamp(cx - w / 2, 0, 1)
  const x2 = clamp(cx + w / 2, 0, 1)
  const y1 = clamp(cy - h / 2, 0, 1)
  const y2 = clamp(cy + h / 2, 0, 1)
  return x2 - x1 < 0.02 || y2 - y1 < 0.02 ? null : [x1, y1, x2, y2]
}

function phaseAt(t: number): Phase {
  if (t < PHASE_STARTS.approach) return 'search'
  if (t < PHASE_STARTS.grasp) return 'approach'
  if (t < PHASE_STARTS.carry) return 'grasp'
  if (t < PHASE_STARTS.place) return 'carry'
  if (t < PHASE_STARTS.release) return 'place'
  if (t < PHASE_STARTS.search) return 'release'
  return 'search'
}

export interface ScenarioOptions {
  /** Restrict the target to one label. Null cycles through the detector classes. */
  preferred?: string | null
  /** Seconds since an operator-triggered squeeze, or null. Used by the Signals test switch. */
  sinceSqueezeS?: number | null
  /**
   * Where the gripper shows in the picture (`set_aim`). The operator steers the object there, and dx, dy are measured
   * from it, as vision measures them. Default: the frame centre.
   */
  aim?: Aim
}

export function scenarioAt(time: number, options: ScenarioOptions = {}): SimFrame {
  const cycle = Math.floor(time / CYCLE_S)
  const t = time - cycle * CYCLE_S
  const label = options.preferred ?? DETECTOR_CLASSES[cycle % DETECTOR_CLASSES.length]
  const phase = phaseAt(t)
  const { x: ax, y: ay } = options.aim ?? AIM_DEFAULT

  // --- Where the object sits in the wrist view -------------------------------------------------
  let cx = 0
  let cy = 0
  let area = 0
  let visible = true
  let confidence = 0

  if (t < 2) {
    visible = false
  } else if (t < 2.8) {
    // Sliding in from the left edge: seen, but not yet trusted.
    const p = (t - 2) / 0.8
    cx = lerp(0.06, 0.22, p)
    cy = 0.34
    area = 0.016
    confidence = lerp(0.2, 0.38, p)
  } else if (t < 11) {
    // The operator steers toward it. The wobble is their hand, and it settles as they close in.
    const p = smoothstep((t - 2.8) / 7.6)
    const loose = (1 - p) ** 0.8
    cx = lerp(0.22, ax, p) + loose * (0.07 * Math.sin(1.9 * time) + 0.03 * Math.sin(4.3 * time + 1))
    cy = lerp(0.34, ay + 0.03, p) + loose * (0.05 * Math.sin(1.4 * time + 2) + 0.02 * Math.sin(3.7 * time))
    area = lerp(0.016, 0.26, p ** 1.6)
    confidence = lerp(0.52, 0.93, p) + 0.015 * Math.sin(7.1 * time)
  } else if (t < 28.5) {
    // In the gripper: large, between the jaws, moving a little with the hand.
    cx = ax + 0.01 * Math.sin(2.1 * time)
    cy = ay + 0.05 + 0.008 * Math.sin(2.9 * time)
    area = lerp(0.26, 0.3, smoothstep((t - 11) / 2))
    confidence = 0.92 + 0.015 * Math.sin(5.3 * time)
  } else if (t < PHASE_STARTS.search) {
    // Set down. The arm backs away, then pans off toward the next object.
    const back = smoothstep((t - 28.5) / 1.5)
    const away = smoothstep((t - 30) / 1.6)
    cx = lerp(ax, ax - 0.04, back) + away * 0.62
    cy = lerp(ay + 0.05, ay + 0.1, back)
    area = lerp(0.3, 0.07, back) * (1 - 0.5 * away)
    confidence = lerp(0.92, 0.6, away)
  } else {
    visible = false
  }

  const box = visible ? boxAround(cx, cy, area, label) : null
  const status: VisionStatus = !box ? 'no_target' : t >= 2 && t < 2.8 ? 'low_confidence' : 'ok'
  const dx = box ? clamp(((box[0] + box[2]) / 2 - ax) * 2, -1, 1) : 0
  const dy = box ? clamp(((box[1] + box[3]) / 2 - ay) * 2, -1, 1) : 0
  const distance: DistanceHint = area >= 0.2 ? 'in_reach' : area >= 0.05 ? 'near' : 'far'

  // --- Gripper and grip ------------------------------------------------------------------------
  const closing = smoothstep((t - 11.4) / 1.4)
  const opening = smoothstep((t - 27.3) / 1.2)
  const gripper = lerp(lerp(85, 30, closing), 85, opening)

  let grip = 0.56 * smoothstep((t - 12.3) / 1.0) * (1 - smoothstep((t - 27.3) / 0.7))
  const held = t >= 12.6 && t < 27.6
  if (held) {
    grip += 0.04 * Math.sin(1.3 * time) + 0.02 * Math.sin(3.7 * time)
    grip += 0.06 * bell((t - 25.5) / 0.4) // touchdown
    if (cycle % 2 === 1) grip = lerp(grip, SQUEEZE_PEAK, bell((t - SQUEEZE_AT_S) / 0.7)) // the scripted over-squeeze
  }
  if (options.sinceSqueezeS != null && grip > 0.05) {
    grip = lerp(grip, SQUEEZE_PEAK, bell((options.sinceSqueezeS - 0.6) / 0.5))
  }
  grip = clamp(grip, 0, 1)

  // --- Arm and armband -------------------------------------------------------------------------
  const roll = 0.16 * Math.sin(0.9 * time) + 0.05 * Math.sin(2.3 * time)
  const pan = keyed(KEY_PAN, t) + 1.1 * Math.sin(1.7 * time)
  const lift = keyed(KEY_LIFT, t) + 0.9 * Math.sin(2.1 * time + 1)
  const elbow = keyed(KEY_ELBOW, t) + 0.8 * Math.sin(1.3 * time + 2)
  const wrist = keyed(KEY_WRIST, t) + 0.7 * Math.sin(2.6 * time)
  const carrying = held ? 1 : 0

  return {
    cycle,
    phase,
    status,
    label,
    box,
    confidence: clamp(confidence, 0, 1),
    dx,
    dy,
    distance,
    grip,
    held,
    joints: [pan, lift, elbow, wrist, (roll * 180) / Math.PI, gripper],
    loads: [
      0.04 + 0.03 * Math.sin(1.1 * time),
      0.12 + 0.3 * carrying + 0.04 * Math.sin(2.3 * time),
      0.08 + 0.2 * carrying + 0.03 * Math.sin(1.9 * time + 1),
      0.05 + 0.1 * carrying + 0.02 * Math.sin(3.1 * time),
      0.02 * Math.sin(2.7 * time),
      clamp(0.75 * grip, 0, 1),
    ],
    roll,
    pitch: ((-8 + 0.5 * lift) * Math.PI) / 180,
    yaw: (0.8 * pan * Math.PI) / 180,
  }
}

// --- Spoken summaries, in the vision module's wording ------------------------------------------

const DISTANCE_WORDS: Record<DistanceHint, string> = { far: 'ahead', near: 'near', in_reach: 'in reach' }

function axisWords(value: number, negative: string, positive: string): string | null {
  const size = Math.abs(value)
  if (size <= 0.1) return null
  return `${size <= 0.35 ? 'slightly ' : ''}${value < 0 ? negative : positive}`
}

/** "Cup near, slightly left." Mirrors the templates in paul_vision/summary.py. */
export function summaryFor(label: string, distance: DistanceHint, dx: number, dy: number): string {
  const parts = [axisWords(dx, 'left', 'right'), axisWords(dy, 'up', 'down')].filter((p) => p !== null)
  const direction = parts.length ? parts.join(' and ') : 'centered'
  return `${label[0].toUpperCase()}${label.slice(1)} ${DISTANCE_WORDS[distance]}, ${direction}.`
}
