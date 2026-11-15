/**
 * Hardware and threshold assumptions, in one place.
 *
 * Values marked "assumed" are the frontend's working guess until the hardware team confirms
 * them. Changing them here changes every screen.
 */

/** Detector classes, mirrored from computer_vision/config.yaml (detector.classes). */
export const DETECTOR_CLASSES = ['cup', 'bottle', 'pill bottle', 'box'] as const

export interface JointSpec {
  key: string
  label: string
  /** Display range. Assumed: approximate SO-101 ranges, not measured on the real arm. */
  min: number
  max: number
  unit: '°' | '%'
}

/** Order of `robot.pos` and `robot.load`. Follows the LeRobot SO-101 motor order. */
export const JOINTS: readonly JointSpec[] = [
  { key: 'shoulder_pan', label: 'Shoulder pan', min: -110, max: 110, unit: '°' },
  { key: 'shoulder_lift', label: 'Shoulder lift', min: -100, max: 100, unit: '°' },
  { key: 'elbow_flex', label: 'Elbow', min: -100, max: 100, unit: '°' },
  { key: 'wrist_flex', label: 'Wrist flex', min: -100, max: 100, unit: '°' },
  { key: 'wrist_roll', label: 'Wrist roll', min: -160, max: 160, unit: '°' },
  { key: 'gripper', label: 'Gripper', min: 0, max: 100, unit: '%' },
]

export const GRIPPER_INDEX = 5

/** Order of `pressure.norm`. Assumed: two pads on each finger, tip then base. */
export const PADS = [
  { key: 'left_tip', label: 'Left tip', finger: 'left', position: 'tip' },
  { key: 'left_base', label: 'Left base', finger: 'left', position: 'base' },
  { key: 'right_tip', label: 'Right tip', finger: 'right', position: 'tip' },
  { key: 'right_base', label: 'Right base', finger: 'right', position: 'base' },
] as const

/** Order of `haptics.motors`. Assumed: four motors around the forearm. */
export const MOTORS = [
  { key: 'up', label: 'Top', angleDeg: -90 },
  { key: 'right', label: 'Right', angleDeg: 0 },
  { key: 'down', label: 'Bottom', angleDeg: 90 },
  { key: 'left', label: 'Left', angleDeg: 180 },
] as const

export const LIMITS = {
  /** Vision contract: silence, or a `t` older than this, means no guidance. */
  visionStaleS: 0.5,
  /** Relative grip at or above this raises a caution. Assumed. */
  gripCaution: 0.9,
  /** Absolute joint load at or above this raises a caution. Assumed. */
  loadCaution: 0.85,
  /** Vision rate below these raises a caution, then a warning. */
  fpsCaution: 15,
  fpsWarning: 5,
  /** Frame age bands in milliseconds. */
  ageCautionMs: 150,
  ageWarningMs: 300,
  /** A stream that was flowing and stops for this long raises a caution. */
  streamSilentMs: 1000,
  /** An alert stays visible at least this long, so a brief event is not missed. */
  alertHoldMs: 10_000,
} as const

/**
 * Mirrors the labels section of computer_vision/config.yaml: max_name_chars, max_prompt_chars and min_box_side.
 * Vision checks again and says why it refuses; these only stop the console sending what it would refuse.
 */
export const LABEL_LIMITS = { nameChars: 40, promptChars: 100, minBoxSide: 0.04 } as const

/** Mirrors guidance.deadzone_enter / deadzone_exit in computer_vision/config.yaml. */
export const DEADZONE = { enter: 0.1, exit: 0.14 } as const

/**
 * Mirrors guidance.aim in computer_vision/config.yaml: where the gripper's grasp point shows in the camera picture, as
 * fractions of its width and height. The operator can drag the crosshair elsewhere; `set_aim` tells vision.
 */
export const AIM_DEFAULT = { x: 0.5, y: 0.5 } as const

/** The crosshair stays at least this far (a fraction of the picture) from each edge, so it can always be grabbed. */
export const AIM_LIMIT = 0.05

/** Where the ZMQ-to-WebSocket bridge listens (`bridge/`, `python -m paul_bridge`). */
export const DEFAULT_WS_URL = 'ws://127.0.0.1:8765'

/**
 * Where the vision module serves the wrist camera as MJPEG (stream.port in
 * computer_vision/config.yaml). The picture comes from vision, not from the browser's own camera
 * access, because only one program can hold the camera at a time.
 */
export const DEFAULT_CAMERA_URL = 'http://127.0.0.1:8766/stream'

/** Seconds of history kept for the grip and haptic traces. */
export const TRACE_WINDOW_S = 12
