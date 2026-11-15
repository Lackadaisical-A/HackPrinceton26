/**
 * Message contracts between PAUL's processes and this app.
 *
 * On the wire every message is one text frame: the topic, a space, then compact JSON. This is
 * the same encoding the Python side uses over ZMQ (computer_vision/paul_vision/messages.py), so
 * the ZMQ-to-WebSocket bridge can relay frames without rewriting them.
 *
 * `vision`, `pressure` and every command but `set_haptics` are defined by the vision module.
 * `robot` is published by the pot controller (and can be reused by the IMU controller).
 * `imu`, `haptics` and `set_haptics` remain proposals. See TOPIC_INFO below.
 */

export const VISION_STATUSES = ['ok', 'no_target', 'low_confidence', 'degraded'] as const
export type VisionStatus = (typeof VISION_STATUSES)[number]

export const PHASES = ['search', 'approach', 'grasp', 'carry', 'place', 'release'] as const
export type Phase = (typeof PHASES)[number]

export const DISTANCE_HINTS = ['far', 'near', 'in_reach'] as const
export type DistanceHint = (typeof DISTANCE_HINTS)[number]

export const HAPTIC_CUES = ['guide', 'contact', 'limit', 'resistance'] as const
export type HapticCue = (typeof HAPTIC_CUES)[number]

/** Mirrors paul_vision.phase.ALERTS. */
export const VISION_ALERTS = ['dropped'] as const
export type VisionAlert = (typeof VISION_ALERTS)[number]

export type BBox = [x1: number, y1: number, x2: number, y2: number]

export interface VisionTarget {
  track_id: number
  label: string
  source: string
  /** Normalised corners, each 0 to 1. */
  bbox: BBox
  confidence: number
}

export interface VisionGuidance {
  /** Target centre minus frame centre, -1 to 1. Positive is right and down in gripper terms. */
  dx: number
  dy: number
  distance_hint: DistanceHint
  /** True inside the deadzone. */
  aligned: boolean
}

export interface VisionMessage {
  /** Capture time of the frame, Unix seconds. */
  t: number
  frame_id: number
  status: VisionStatus
  phase: Phase
  /** Null unless status is "ok". */
  target: VisionTarget | null
  /** Null unless status is "ok". */
  guidance: VisionGuidance | null
  /** A sentence to speak, or null when there is nothing new to say. */
  summary: string | null
  /**
   * "dropped" while contact was lost in carry (repeated on every message for phase.alert_hold_s), else null.
   * Independent of status. Absent in recordings made before vision milestone 2.
   */
  alert?: VisionAlert | null
}

export interface PressureMessage {
  t: number
  /** Raw sensor counts, one per pad. */
  raw: number[]
  /** Relative pressure per pad, 0 to 1. Not calibrated to a force. */
  norm: number[]
}

/** Proposed. Armband orientation as a unit quaternion, as the BNO085 firmware prints it. */
export interface ImuMessage {
  t: number
  q: [w: number, x: number, y: number, z: number]
}

/** Joint order: see JOINTS in config.ts. Input method is independent of measured feedback. */
export interface RobotMessage {
  t: number
  /** Degrees, except the gripper, which is 0 (closed) to 100 (open). */
  pos: number[]
  /** Signed servo load / 1000, clamped to -1..1; null when unavailable or previewing. */
  load: number[] | null
  control?: {
    input: 'potentiometer' | 'imu'
    enabled: boolean
    input_fresh: boolean
    feedback: 'measured' | 'preview'
    target: number[] | null
  }
  /** Optional seventh axis; never changes the six-joint array order. */
  slider?: { pos: number; target: number | null }
}

/** Proposed. What the haptics layer is commanding, not what the wearer feels. */
export interface HapticsMessage {
  t: number
  enabled: boolean
  /** Commanded amplitude per motor, 0 to 1. Motor order: see MOTORS in config.ts. */
  motors: number[]
  cue: HapticCue | null
}

export interface TopicMap {
  vision: VisionMessage
  pressure: PressureMessage
  imu: ImuMessage
  robot: RobotMessage
  haptics: HapticsMessage
}

export type Topic = keyof TopicMap
export const TOPICS = ['vision', 'pressure', 'imu', 'robot', 'haptics'] as const satisfies readonly Topic[]

export type Command =
  /** A detector class or a name the operator taught. */
  | { type: 'select_target'; label: string }
  | { type: 'clear_target' }
  | { type: 'set_phase'; phase: Phase }
  /** Teach a name from a box on the picture (normalised corners), or from the locked target when bbox is null. */
  | { type: 'tag_visual'; name: string; bbox: BBox | null }
  /** Teach a name from a written description; the prompt defaults to the name. */
  | { type: 'tag_text'; name: string; prompt?: string }
  | { type: 'untag'; name: string }
  /** Proposed. */
  | { type: 'set_haptics'; enabled: boolean }

export const COMMAND_TOPIC = 'command'

export interface TopicInfo {
  title: string
  /** "contract": defined by an existing module. "proposed": defined here, awaiting agreement. */
  standing: 'contract' | 'proposed'
  /** Nominal publish rate. */
  rateHz: number
  /** Where the data comes from. */
  origin: string
  example: string
}

export const TOPIC_INFO: Record<Topic, TopicInfo> = {
  vision: {
    title: 'Vision guidance',
    standing: 'contract',
    rateHz: 30,
    origin: 'computer_vision, ZMQ port 5555',
    example:
      'vision {"t":1731542400.123,"frame_id":1042,"status":"ok","phase":"approach","target":{"track_id":7,"label":"cup","source":"yolo","bbox":[0.41,0.3,0.62,0.58],"confidence":0.91},"guidance":{"dx":-0.12,"dy":0.05,"distance_hint":"near","aligned":false},"summary":"Cup near, slightly left.","alert":null}',
  },
  pressure: {
    title: 'Gripper pressure',
    standing: 'contract',
    rateHz: 50,
    origin: 'pressure bridge, ZMQ port 5556',
    example: 'pressure {"t":1731542400.131,"raw":[1840,1322,1795,1290],"norm":[0.52,0.36,0.5,0.35]}',
  },
  imu: {
    title: 'Armband orientation',
    standing: 'proposed',
    rateHz: 50,
    origin: 'BNO085 over serial, 50 Hz',
    example: 'imu {"t":1731542400.14,"q":[0.98481,0.08716,-0.1219,0.0871]}',
  },
  robot: {
    title: 'Arm joints',
    standing: 'contract',
    rateHz: 10,
    origin: 'pot / arm controller, ZMQ port 5558 (measured feedback or labeled dry run)',
    example:
      'robot {"t":1731542400.15,"pos":[-12.4,31,44.5,28,3.2,62],"load":[0.04,0.31,0.22,0.08,0.01,0.12]}',
  },
  haptics: {
    title: 'Haptic output',
    standing: 'proposed',
    rateHz: 50,
    origin: 'haptics layer (DRV2605L drivers)',
    example: 'haptics {"t":1731542400.16,"enabled":true,"motors":[0,0.42,0,0],"cue":"guide"}',
  },
}

export const COMMAND_EXAMPLES: { frame: string; standing: 'contract' | 'proposed' }[] = [
  { frame: 'command {"t":1731542400.2,"type":"select_target","label":"pill bottle"}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"clear_target"}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"set_phase","phase":"search"}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"tag_visual","name":"my flask","bbox":[0.3,0.25,0.55,0.7]}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"tag_visual","name":"my flask","bbox":null}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"tag_text","name":"pill bottle","prompt":"small orange pill bottle"}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"untag","name":"my flask"}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"set_haptics","enabled":false}', standing: 'proposed' },
]

// --- Validation -------------------------------------------------------------------------------
// Each validator returns null when the message is valid, or a short reason when it is not.

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const isNumber = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const within = (v: unknown, lo: number, hi: number): v is number => isNumber(v) && v >= lo && v <= hi

function numberList(v: unknown, length: number, lo: number, hi: number): v is number[] {
  return Array.isArray(v) && v.length === length && v.every((n) => within(n, lo, hi))
}

/**
 * The checks of paul_vision.messages.validate_vision that the console relies on. Lenient where the Python side
 * is strict: extra keys pass, and `alert` may be missing (recordings made before vision milestone 2).
 */
export function validateVision(m: unknown): string | null {
  if (!isObject(m)) return 'not an object'
  if (!isNumber(m.t)) return 't must be a number'
  if (!Number.isInteger(m.frame_id) || (m.frame_id as number) < 0) return 'frame_id must be a non-negative integer'
  if (!VISION_STATUSES.includes(m.status as VisionStatus)) return `unknown status ${JSON.stringify(m.status)}`
  if (!PHASES.includes(m.phase as Phase)) return `unknown phase ${JSON.stringify(m.phase)}`
  if (m.summary !== null && typeof m.summary !== 'string') return 'summary must be a string or null'
  if (m.alert !== undefined && m.alert !== null && !VISION_ALERTS.includes(m.alert as VisionAlert)) {
    return `unknown alert ${JSON.stringify(m.alert)}`
  }

  if (m.status !== 'ok') {
    if (m.target !== null || m.guidance !== null) return 'target and guidance must be null unless status is ok'
    return null
  }

  const target = m.target
  if (!isObject(target)) return 'target is required when status is ok'
  if (!Number.isInteger(target.track_id)) return 'target.track_id must be an integer'
  if (typeof target.label !== 'string' || target.label === '') return 'target.label must be a non-empty string'
  if (typeof target.source !== 'string') return 'target.source must be a string'
  if (!numberList(target.bbox, 4, 0, 1)) return 'target.bbox must be four numbers from 0 to 1'
  if (target.bbox[0] >= target.bbox[2] || target.bbox[1] >= target.bbox[3]) return 'target.bbox corners are out of order'
  if (!within(target.confidence, 0, 1)) return 'target.confidence must be from 0 to 1'

  const guidance = m.guidance
  if (!isObject(guidance)) return 'guidance is required when status is ok'
  if (!within(guidance.dx, -1, 1) || !within(guidance.dy, -1, 1)) return 'guidance.dx and dy must be from -1 to 1'
  if (!DISTANCE_HINTS.includes(guidance.distance_hint as DistanceHint)) {
    return `unknown distance_hint ${JSON.stringify(guidance.distance_hint)}`
  }
  if (typeof guidance.aligned !== 'boolean') return 'guidance.aligned must be true or false'
  return null
}

export function validatePressure(m: unknown): string | null {
  if (!isObject(m)) return 'not an object'
  if (!isNumber(m.t)) return 't must be a number'
  if (!Array.isArray(m.raw) || m.raw.length !== 4 || !m.raw.every(isNumber)) return 'raw must be four numbers'
  if (!numberList(m.norm, 4, 0, 1)) return 'norm must be four numbers from 0 to 1'
  return null
}

export function validateImu(m: unknown): string | null {
  if (!isObject(m)) return 'not an object'
  if (!isNumber(m.t)) return 't must be a number'
  if (!numberList(m.q, 4, -1.001, 1.001)) return 'q must be four numbers from -1 to 1'
  const norm = Math.hypot(...m.q)
  if (norm < 0.9 || norm > 1.1) return 'q is not a unit quaternion'
  return null
}

export function validateRobot(m: unknown): string | null {
  if (!isObject(m)) return 'not an object'
  if (!isNumber(m.t)) return 't must be a number'
  if (!Array.isArray(m.pos) || m.pos.length !== 6 || !m.pos.every(isNumber)) return 'pos must be six numbers'
  if (m.load !== null && !numberList(m.load, 6, -1, 1)) return 'load must be six numbers from -1 to 1'
  if (m.control !== undefined) {
    const c = m.control
    if (!isObject(c) || !['potentiometer', 'imu'].includes(c.input as string)
      || typeof c.enabled !== 'boolean' || typeof c.input_fresh !== 'boolean'
      || !['measured', 'preview'].includes(c.feedback as string)
      || (c.target !== null && (!Array.isArray(c.target) || c.target.length !== 6 || !c.target.every(isNumber)))) {
      return 'invalid robot control state'
    }
    if (c.feedback === 'preview' && m.load !== null) return 'preview must not report measured load'
  }
  if (m.slider !== undefined && (!isObject(m.slider) || !isNumber(m.slider.pos)
    || (m.slider.target !== null && !isNumber(m.slider.target)))) return 'invalid slider state'
  return null
}

export function validateHaptics(m: unknown): string | null {
  if (!isObject(m)) return 'not an object'
  if (!isNumber(m.t)) return 't must be a number'
  if (typeof m.enabled !== 'boolean') return 'enabled must be true or false'
  if (!numberList(m.motors, 4, 0, 1)) return 'motors must be four numbers from 0 to 1'
  if (m.cue !== null && !HAPTIC_CUES.includes(m.cue as HapticCue)) return `unknown cue ${JSON.stringify(m.cue)}`
  return null
}

export const VALIDATORS: Record<Topic, (m: unknown) => string | null> = {
  vision: validateVision,
  pressure: validatePressure,
  imu: validateImu,
  robot: validateRobot,
  haptics: validateHaptics,
}

export const isTopic = (name: string): name is Topic => (TOPICS as readonly string[]).includes(name)

// --- Wire encoding ----------------------------------------------------------------------------

export type ParsedFrame = { ok: true; topic: string; payload: unknown } | { ok: false; topic: string | null; error: string }

/** Splits "<topic> <json>" and parses the JSON. Never throws. */
export function parseFrame(frame: string): ParsedFrame {
  const space = frame.indexOf(' ')
  if (space <= 0) return { ok: false, topic: null, error: 'frame has no topic prefix' }
  const topic = frame.slice(0, space)
  try {
    return { ok: true, topic, payload: JSON.parse(frame.slice(space + 1)) as unknown }
  } catch {
    return { ok: false, topic, error: 'payload is not valid JSON' }
  }
}

export function encodeFrame(topic: string, payload: unknown): string {
  return `${topic} ${JSON.stringify(payload)}`
}
