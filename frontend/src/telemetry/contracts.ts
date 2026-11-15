/**
 * Message contracts between PAUL's processes and this app.
 *
 * On the wire every message is one text frame: the topic, a space, then compact JSON. This is
 * the same encoding the Python side uses over ZMQ (computer_vision/paul_vision/messages.py), so
 * the ZMQ-to-WebSocket bridge can relay frames without rewriting them.
 *
 * `vision`, `pressure` and vision commands are defined by the vision module.
 * `robot` is published by the pot and IMU controllers; the IMU controller also publishes `imu`.
 * The wearable controller implements `haptics` and `set_haptics`. See TOPIC_INFO below.
 * Wearable Lab (wearable_test/console.py) implements `calibration` and every robot command: calibration,
 * tuning, arm dimensions, joint directions, coin tests, and enabling/stopping the arm and claw. An enable is
 * only a request: the serial worker re-checks fresh input, loads and limits before any motion.
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
  /**
   * Target centre minus the aim point (where the gripper shows: the frame centre unless guidance.aim or set_aim moves
   * it), -1 to 1. Positive is right and down in gripper terms.
   */
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

/** Hand IMU orientation as a unit quaternion, as the BNO085 firmware prints it. */
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

/** What the wearable controller commands, not a measurement of vibration or force. */
export interface HapticsMessage {
  t: number
  enabled: boolean
  /** Commanded amplitude per motor, 0 to 1. Motor order: see MOTORS in config.ts. */
  motors: number[]
  cue: HapticCue | null
  detail?: string
  acknowledged?: boolean[]
}

export const CALIBRATION_PHASES = ['needs_pose_a', 'needs_pose_b', 'needs_recenter', 'ready', 'preparing', 'sampling'] as const
export type CalibrationAction = 'capture_a' | 'capture_b' | 'capture_elbow' | 'capture_shoulder' | 'capture_pan_left' | 'capture_pan_right' | 'recenter' | 'cancel'

export const ROBOT_SETTING_RANGES = {
  speed_deg_s: [10, 360], claw_speed_per_s: [0.1, 3], max_load: [1, 1023], gripper_max_load: [1, 1023],
  gripper_contact_load: [20, 350], gripper_hold_load: [10, 300],
} as const
export type RobotSettings = Record<keyof typeof ROBOT_SETTING_RANGES, number> & { load_stops_enabled: boolean }
export type SliderAction = 'release' | 'zero' | 'hundred' | 'enable'
export interface SliderControl {
  referenced: boolean
  enabled: boolean
  pos: number | null
  target: number | null
  detail: string
  request: { id: string; ok: boolean; detail: string } | null
}
/** `play_smart` lets Gemini choose each action (../assist); `resume` answers its request for the operator. */
export type ReplayAction = 'record' | 'play' | 'play_smart' | 'resume' | 'stop'
export const REPLAY_PHASES = ['empty', 'ready', 'recording', 'returning', 'playing', 'finishing', 'smart'] as const
export const SMART_STATES = ['waiting', 'acting', 'asking', 'done'] as const
/** Smart replay as the robot reports it: the turn it waits on, the action in hand and the last result. */
export interface SmartAgentStatus {
  /** Names one smart replay; Gemini's answers carry it back. */
  run: string | null
  state: (typeof SMART_STATES)[number]
  /** The turn the robot is waiting on (from 1). */
  seq: number
  action: { action: string | null; why: string } | null
  /** `seq` 0 is the opening result, before any action. */
  result: { seq: number; ok: boolean; action: string | null; detail: string }
  count: number
  detail: string
  carrying: boolean
  arm?: { joints: Record<string, number>; gripper: number; holding: boolean; closed_endpoint: boolean; load: number }
}
export interface ReplayStatus {
  phase: (typeof REPLAY_PHASES)[number]
  revision: number
  duration_s: number
  elapsed_s: number
  samples: number
  includes_slider: boolean
  detail: string
  request: { id: string; ok: boolean; applied: boolean; detail: string } | null
  /** Grasps and releases sensed during the recording, in seconds from its start. */
  events?: { t: number; kind: 'grasp' | 'release' }[]
  /** Times of the poses smart replay may name. */
  waypoints?: number[]
  /** Wall-clock start of the recording; names the take for the `task` topic. */
  recorded_wall?: number | null
  agent?: SmartAgentStatus | null
}
export interface RobotTuning {
  values: RobotSettings
  grasp?: { holding: boolean; load: number; detail: string } | null
  revision: number
  applied_revision: number
  connected: boolean
  fresh: boolean
  enabled: boolean
  can_reconnect?: boolean
  detail?: string
  reconnect_request?: { id: string; ok: boolean; detail: string } | null
  request: { id: string; ok: boolean; applied: boolean; detail: string; revision: number | null } | null
}

/** SO-101 arm joints in wire order, without the gripper. Mirrors robot_control/joints.py BODY. */
export const BODY_JOINTS = ['shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll'] as const
export type BodyJoint = (typeof BODY_JOINTS)[number]
export const ROBOT_JOINTS = [...BODY_JOINTS, 'gripper'] as const
export type RobotJoint = (typeof ROBOT_JOINTS)[number]

/** Mirrors robot_control/tuning.py RANGES and LEAD_RANGE: how the arm follows, applied live for the session. */
export const RESPONSE_RANGES = { speed_deg_s: [10, 360], response_ms: [1, 100], control_hz: [20, 100] } as const
export const LEAD_RANGE = [1, 45] as const
export interface ResponseSettings {
  speed_deg_s: number
  response_ms: number
  control_hz: number
  joint_lead_deg: Record<BodyJoint, number>
}
/** What `tune_robot` may change: speed/load controls and response settings, any subset. */
export type RobotChanges = Partial<RobotSettings> & Partial<Omit<ResponseSettings, 'joint_lead_deg'>>
  & { joint_lead_deg?: Partial<Record<BodyJoint, number>> }
export interface ResponseStatus {
  values: ResponseSettings
  /** Accepted but not yet applied at a control-cycle boundary. */
  pending: ResponseSettings | null
  startup: ResponseSettings | null
  /** Index into `profiles` when the values equal a named profile, else null (custom). */
  profile: number | null
  pending_profile: number | null
  /** Precise to Fast. The values live only in Python; send `profile` to choose one. */
  profiles: { name: string; description: string }[]
}

/** A controller's answer to one request: accepted (`ok`), and for some, carried out (`applied`). */
export interface Reply {
  id: string
  ok: boolean
  detail: string
  applied?: boolean
}

/** Mirrors wearable_test/model.py validate_settings and AXES. */
export const ARM_LENGTH_KEYS = ['upper_cm', 'forearm_cm', 'hand_cm'] as const
export const ARM_POSITION_KEYS = ['bicep_from_elbow_pct', 'forearm_from_wrist_pct', 'hand_from_wrist_pct'] as const
export const ARM_LENGTH_RANGE = [5, 60] as const
export const ARM_POSITION_RANGE = [0, 100] as const
export const SENSOR_ROLES = ['upper', 'forearm', 'hand'] as const
export type SensorRole = (typeof SENSOR_ROLES)[number]
export const SENSOR_AXES = ['Auto', '+X', '-X', '+Y', '-Y', '+Z', '-Z'] as const
export type SensorAxis = (typeof SENSOR_AXES)[number]
export type ArmSettings = Record<(typeof ARM_LENGTH_KEYS)[number] | (typeof ARM_POSITION_KEYS)[number], number>
  & { axes: Record<SensorRole, SensorAxis> }
export interface ArmSettingsStatus {
  values: ArmSettings
  /** False during a capture or while the desktop dimensions dialog is open. */
  editable: boolean
  request: Reply | null
}

export interface DirectionsStatus {
  reverse: Record<RobotJoint, boolean>
  /** False during a capture or while the arm, claw or a grasp hold is active. */
  editable: boolean
  request: Reply | null
}

/** One vibration coin, in MOTORS order (config.ts). */
export interface CoinStatus {
  available: boolean
  /** The level the next manual test uses. */
  percent: number
  /** The level being commanded now. */
  output: number
  running_s: number
  acks: number
  /** Automatic guidance/resistance owns the coin. */
  feedback: boolean
  detail: string
}
export interface CoinsStatus {
  motors: CoinStatus[]
  editable: boolean
  request: Reply | null
}

export interface WearableStatus {
  /** Bicep, forearm, hand. */
  imus: { live: boolean; hz: number | null; age_ms: number | null }[]
  /** WROOM (hand, joystick, palm coins), then S3 (bicep, forearm, forearm coins). */
  boards: { name: string; port: string; connected: boolean; driver: string; message: string }[]
  /** Raw ADC 0..4095 and the calibrated output -1..1. */
  joystick: { x: number; y: number; nx: number; ny: number; pressed: boolean; fresh: boolean } | null
  joystick_center: { x: number | null; y: number | null; deadzone: number | null }
}

export type Vec3 = [number, number, number]
/** The wearer's arm as Wearable Lab estimates it, metres, shoulder at the origin, z up. */
export interface ArmGeometry {
  /** Shoulder, elbow, wrist, fingertips. */
  joints: Vec3[]
  /** Bicep, forearm, hand sensor positions. */
  markers: Vec3[]
  /** Each sensor board's x, y, z axes. */
  boards: [Vec3, Vec3, Vec3][]
  palm_side: Vec3
  elbow_deg: number | null
  wrist_deg: number | null
  calibrated: boolean
}

export const ROBOT_MODES = ['off', 'paused', 'aligning', 'live', 'replay'] as const
export type RobotMode = (typeof ROBOT_MODES)[number]
/** One row of the physical joint table. Angles are URDF degrees; position/sent are raw servo counts. */
export interface PhysicalJoint {
  actual_deg: number | null
  /** Where Align & follow would move this joint from the wearer's current pose. */
  preview_deg: number | null
  position: number | null
  sent: number | null
  gap_deg: number | null
  /** RATE (speed cap), LEAD (motor lags) or RANGE (beyond saved travel), else empty. */
  flag: string
  load: number | null
  torque: boolean
}
export interface PhysicalStatus {
  port: string
  connected: boolean
  fresh: boolean
  mode: RobotMode
  claw_enabled: boolean
  grip_hold: boolean
  can_enable_arm: boolean
  can_enable_claw: boolean
  mapping_mode: 'absolute' | 'relative'
  alignment_deg_s: number | null
  message: string
  input_status: string
  alignment_progress: string
  limited: string[]
  loop_hz: number | null
  read_ms: number | null
  joints: PhysicalJoint[]
}
export type RobotAction = 'enable_arm' | 'enable_claw' | 'stop'
export type CoinAction = 'test' | 'run' | 'intensity' | 'stop'

export interface CalibrationMessage {
  t: number
  /** Controller instance and mutation counter; queued captures cannot undo Stop/reset. */
  session: string
  revision: number
  phase: (typeof CALIBRATION_PHASES)[number]
  capture_pose: 'A' | 'B' | 'C' | 'D' | 'E' | 'F' | 'R' | null
  remaining_s: number
  input_fresh: boolean
  /** Bicep, forearm, hand. */
  sensors: boolean[]
  can_recenter: boolean
  range_capture?: boolean
  pan_capture?: boolean
  saved_config?: { path: string; state: 'none' | 'saved' | 'loaded' | 'error' | 'demo'; detail: string } | null
  tuning?: RobotTuning | null
  slider?: SliderControl | null
  replay?: ReplayStatus | null
  motion_ranges?: { elbow_max_deg?: number; shoulder_min_deg?: number; shoulder_max_deg?: number; pan_left_deg?: number; pan_right_deg?: number }
  detail: string
  /** Acknowledges starting/refusing a request; phase reports actual completion. */
  request: { id: string; ok: boolean; detail: string } | null
  /** Enable/stop acknowledgment; `applied` once the serial worker has actually enabled. */
  control_request?: Reply | null
  /** The physical robot worker; null when Wearable Lab runs without a robot port. */
  physical?: PhysicalStatus | null
  response?: ResponseStatus | null
  arm_settings?: ArmSettingsStatus | null
  directions?: DirectionsStatus | null
  coins?: CoinsStatus | null
  wearable?: WearableStatus | null
  geometry?: ArmGeometry | null
}

export const ASSIST_STATUSES = ['working', 'ok', 'error'] as const
export type AssistStatus = (typeof ASSIST_STATUSES)[number]
export const URGENCIES = ['none', 'watch', 'urgent'] as const
export type Urgency = (typeof URGENCIES)[number]

export interface AssistItem {
  /** Lowercase name: a known detector class or tag when `known`, else a new name to teach. */
  name: string
  /** Short visual description, sent as the tag_text prompt (at most 100 characters). */
  prompt: string
  reason: string
  /** Whether Gemini saw it in the picture. */
  visible: boolean
  /** Whether vision can already find it (detector.classes or a taught name) when the analysis ran. */
  known: boolean
}

/** One picture cannot tell sleep from unconsciousness: "eyes_closed" is both, and a wellness nudge tells them apart. */
export const PATIENT_STATES = ['awake', 'eyes_closed', 'distressed', 'unclear', 'not_visible'] as const
export type PatientState = (typeof PATIENT_STATES)[number]

/** Gentle touches the operator can make with the padded gripper. Mirrors assist/paul_assist/analysis.py TOUCH_KINDS. */
export const TOUCH_KINDS = [
  'wellness_nudge',
  'attention_tap',
  'guided_handover',
  'weight_check',
  'move_within_reach',
  'call_button',
  'comfort_adjust',
] as const
export type TouchKind = (typeof TOUCH_KINDS)[number]

export interface TouchCheck {
  kind: TouchKind
  /** The action for the operator. */
  text: string
  reason: string
}

export interface AssistResult {
  scene: string
  /** `state` is absent in messages from before touch checks existed. */
  patient: { present: boolean; observations: string[]; urgency: Urgency; state?: PatientState }
  /** Suggested touches, the wellness nudge first when there is one. Absent in older messages. */
  touch_checks?: TouchCheck[]
  suggestions: { text: string; item: string | null }[]
  items: AssistItem[]
}

/**
 * Gemini's reading of one camera picture (../assist). Advisory only: nothing acts on it without the operator.
 * Sent on each change and repeated every 5 s, so `seq` tells a new answer from a repeat.
 */
export interface AssistMessage {
  t: number
  seq: number
  status: AssistStatus
  detail: string | null
  model: string | null
  /** Present only when status is "ok". */
  result: AssistResult | null
}

export interface TaskBrief {
  summary: string
  rule: string
  done_when: string
  objects: { name: string; description: string }[]
  /** `from`..`to` are waypoint indices of the recording. */
  steps: { text: string; from: number; to: number; expect: string }[]
}

/** What Gemini understood from one recording (../assist). The operator confirms it before a smart replay. */
export interface TaskMessage {
  t: number
  seq: number
  status: AssistStatus
  detail: string | null
  model: string | null
  /** The recording this is about: its `replay.recorded_wall`. */
  recording: number | null
  /** Present exactly when status is "ok". */
  brief: TaskBrief | null
}

/** Gemini's answer for one smart-replay turn (../assist). The robot validates and carries out the action. */
export interface AgentMessage {
  t: number
  run: string | null
  /** The turn this action answers; null on a status-only message. */
  seq: number | null
  turn: number | null
  status: AssistStatus
  detail: string | null
  model: string | null
  action?: string
  why?: string
}

/** A name the operator taught vision, with how many views (pictures and descriptions) it has of the object. */
export interface LearnedTag {
  name: string
  views: number
}

/**
 * What vision can target: its detector classes, then the names the operator taught. `busy` is the name being
 * learned or forgotten right now. Mirrors computer_vision/paul_vision/messages.py (build_labels_message).
 */
export interface LabelsMessage {
  t: number
  classes: string[]
  tags: LearnedTag[]
  busy: string | null
}

export interface TopicMap {
  vision: VisionMessage
  labels: LabelsMessage
  pressure: PressureMessage
  imu: ImuMessage
  robot: RobotMessage
  haptics: HapticsMessage
  calibration: CalibrationMessage
  assist: AssistMessage
  task: TaskMessage
  agent: AgentMessage
}

export type Topic = keyof TopicMap
export const TOPICS = ['vision', 'labels', 'pressure', 'imu', 'robot', 'haptics', 'calibration', 'assist', 'task', 'agent'] as const satisfies readonly Topic[]

export type Command =
  /** A detector class or a name the operator taught. */
  | { type: 'select_target'; label: string }
  | { type: 'clear_target' }
  | { type: 'set_phase'; phase: Phase }
  /** Measure guidance from this point of the picture (fractions 0 to 1): where the gripper's grasp point shows. */
  | { type: 'set_aim'; x: number; y: number }
  /** Teach a name from a box on the picture (normalised corners), or from the locked target when bbox is null. */
  | { type: 'tag_visual'; name: string; bbox: BBox | null }
  /**
   * Teach a name from a written description; the prompt defaults to the name. `prompts` adds several descriptions
   * of one object in a single change (the assist process sends Gemini's this way; the console does not).
   */
  | { type: 'tag_text'; name: string; prompt?: string; prompts?: string[] }
  | { type: 'untag'; name: string }
  /** Ask the assist process (Gemini) to analyse the current camera picture. */
  | { type: 'assist_request' }
  /** Wearable vibration only. */
  | { type: 'set_haptics'; enabled: boolean }
  | { type: 'calibrate_arm'; action: CalibrationAction; request_id: string; session: string; revision: number }
  /** Exactly one of: explicit changes, a response profile index, or the startup response settings. */
  | ({ type: 'tune_robot'; request_id: string; session: string; revision: number }
    & ({ changes: RobotChanges } | { profile: number } | { startup: true }))
  | { type: 'control_slider'; action: SliderAction; request_id: string; session: string; revision: number }
  | { type: 'reconnect_robot'; request_id: string; session: string; revision: number }
  | { type: 'replay_robot'; action: ReplayAction; request_id: string; session: string; revision: number; replay_revision: number }
  /** Stop needs no matching session or revision: it is always honoured. */
  | { type: 'control_robot'; action: RobotAction; request_id: string; session: string; revision: number }
  /** Saving clears the current calibration, as in the desktop dialog. */
  | { type: 'configure_arm'; settings: ArmSettings; request_id: string; session: string; revision: number }
  | { type: 'set_joint_direction'; joint: RobotJoint; reverse: boolean; request_id: string; session: string; revision: number }
  /** `motor` indexes MOTORS (config.ts); `percent` is ignored by stop. */
  | { type: 'test_haptic_motor'; action: CoinAction; motor: number; percent: number; request_id: string; session: string; revision: number }

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
  task: {
    title: 'Smart replay task (Gemini)',
    standing: 'proposed',
    rateHz: 0.2,
    origin: 'assist (Gemini), ZMQ port 5563; after each recording, repeated every 5 s',
    example:
      'task {"t":1731542400.5,"seq":2,"status":"ok","detail":null,"model":"gemini-2.5-flash","recording":1731542380.2,"brief":{"summary":"Moves the white bottle into the left bin.","rule":"White bottles go left.","done_when":"No bottles remain on the table.","objects":[{"name":"white bottle","description":"small white pill bottle"}],"steps":[{"text":"Pick up the bottle","from":0,"to":3,"expect":"bottle held"}]}}',
  },
  agent: {
    title: 'Smart replay action (Gemini)',
    standing: 'proposed',
    rateHz: 2,
    origin: 'assist (Gemini), ZMQ port 5563; one per turn while a smart replay waits, repeated until taken',
    example:
      'agent {"t":1731542410.5,"run":"1731542405-7","seq":3,"turn":3,"status":"ok","detail":null,"model":"gemini-2.5-flash","action":"nudge","why":"The bottle is left of the fingers.","direction":"left","amount":2}',
  },
  assist: {
    title: 'Scene assist (Gemini)',
    standing: 'contract',
    rateHz: 0.2,
    origin: 'assist (Gemini), ZMQ port 5563; on request, repeated every 5 s',
    example:
      'assist {"t":1731542400.5,"seq":2,"status":"ok","detail":null,"model":"gemini-2.5-flash","result":{"scene":"A person in bed beside a table with a bottle.","patient":{"present":true,"observations":["Reaching toward the table"],"urgency":"watch","state":"awake"},"suggestions":[{"text":"Hand the patient the bottle","item":"bottle"}],"items":[{"name":"bottle","prompt":"clear water bottle","reason":"Thirst","visible":true,"known":true}],"touch_checks":[{"kind":"guided_handover","text":"Hold the bottle to their hand; let go when you feel them take it","reason":"Unsteady reach"}]}}',
  },
  calibration: {
    title: 'Arm calibration',
    standing: 'contract',
    rateHz: 10,
    origin: 'Wearable Lab, ZMQ port 5562',
    example: 'calibration {"t":1731542400.15,"session":"example","revision":0,"phase":"needs_pose_a","capture_pose":null,"remaining_s":0,"input_fresh":true,"sensors":[true,true,true],"can_recenter":false,"detail":"Capture pose A.","request":null}',
  },
  labels: {
    title: 'Vision targets',
    standing: 'contract',
    rateHz: 0.5,
    origin: 'computer_vision, ZMQ port 5555; on every change, repeated every 2 s',
    example: 'labels {"t":1731542400.1,"classes":["cup","bottle","pill bottle","box"],"tags":[{"name":"my flask","views":4}],"busy":null}',
  },
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
    standing: 'contract',
    rateHz: 10,
    origin: 'BNO085 hand IMU, 50 Hz serial sampled at 10 Hz on ZMQ port 5558',
    example: 'imu {"t":1731542400.14,"q":[0.98481,0.08716,-0.1219,0.0871]}',
  },
  robot: {
    title: 'Arm joints',
    standing: 'contract',
    rateHz: 10,
    origin: 'pot / IMU arm controller, ZMQ port 5558 (measured feedback or labeled dry run)',
    example:
      'robot {"t":1731542400.15,"pos":[-12.4,31,44.5,28,3.2,62],"load":[0.04,0.31,0.22,0.08,0.01,0.12]}',
  },
  haptics: {
    title: 'Haptic output',
    standing: 'contract',
    rateHz: 25,
    origin: 'wearable controller: palm L/R, forearm L/R; ZMQ port 5561',
    example: 'haptics {"t":1731542400.16,"enabled":true,"motors":[0,0.42,0,0],"cue":"guide"}',
  },
}

export const COMMAND_EXAMPLES: { frame: string; standing: 'contract' | 'proposed' }[] = [
  { frame: 'command {"t":1731542400.2,"type":"select_target","label":"pill bottle"}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"clear_target"}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"set_phase","phase":"search"}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"set_aim","x":0.52,"y":0.61}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"tag_visual","name":"my flask","bbox":[0.3,0.25,0.55,0.7]}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"tag_visual","name":"my flask","bbox":null}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"tag_text","name":"pill bottle","prompt":"small orange pill bottle"}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"tag_text","name":"my flask","prompts":["steel flask with black cap","round black cap seen from above"]}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"untag","name":"my flask"}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"set_haptics","enabled":false}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"assist_request"}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"calibrate_arm","action":"capture_a","request_id":"unique-id","session":"from-calibration-status","revision":0}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"control_robot","action":"enable_arm","request_id":"unique-id","session":"from-calibration-status","revision":0}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"control_robot","action":"stop","request_id":"unique-id","session":"","revision":0}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"tune_robot","profile":2,"request_id":"unique-id","session":"from-calibration-status","revision":0}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"set_joint_direction","joint":"wrist_roll","reverse":true,"request_id":"unique-id","session":"from-calibration-status","revision":0}', standing: 'contract' },
  { frame: 'command {"t":1731542400.2,"type":"test_haptic_motor","action":"test","motor":0,"percent":40,"request_id":"unique-id","session":"from-calibration-status","revision":0}', standing: 'contract' },
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

export function validateLabels(m: unknown): string | null {
  if (!isObject(m)) return 'not an object'
  if (!isNumber(m.t)) return 't must be a number'
  const isName = (v: unknown): v is string => typeof v === 'string' && v !== ''
  if (!Array.isArray(m.classes) || !m.classes.every(isName)) return 'classes must be a list of names'
  if (!Array.isArray(m.tags) || !m.tags.every((tag) => isObject(tag) && isName(tag.name)
    && Number.isSafeInteger(tag.views) && (tag.views as number) >= 1)) return 'tags must be a list of {name, views}'
  if (new Set(m.tags.map((tag) => tag.name)).size !== m.tags.length) return 'a tag is listed twice'
  if (m.busy !== null && !isName(m.busy)) return 'busy must be a name or null'
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
  if (m.detail !== undefined && typeof m.detail !== 'string') return 'detail must be a string'
  if (m.acknowledged !== undefined && (!Array.isArray(m.acknowledged) || m.acknowledged.length !== 4
    || !m.acknowledged.every((value) => typeof value === 'boolean'))) return 'acknowledged must be four booleans'
  if (m.cue !== null && !HAPTIC_CUES.includes(m.cue as HapticCue)) return `unknown cue ${JSON.stringify(m.cue)}`
  return null
}

const isBool = (v: unknown): v is boolean => typeof v === 'boolean'
const isString = (v: unknown): v is string => typeof v === 'string'
const orNull = (v: unknown, lo: number, hi: number): boolean => v === null || within(v, lo, hi)

function isReply(v: unknown, applied = false): boolean {
  return v === null || (isObject(v) && isString(v.id) && isBool(v.ok) && isString(v.detail)
    && (applied ? isBool(v.applied) : v.applied === undefined || isBool(v.applied)))
}

function isVec3(v: unknown, limit: number): v is Vec3 {
  return numberList(v, 3, -limit, limit)
}

function validatePhysical(p: Record<string, unknown>): string | null {
  if (!isString(p.port) || !isBool(p.connected) || !isBool(p.fresh) || !ROBOT_MODES.includes(p.mode as RobotMode)
    || !isBool(p.claw_enabled) || !isBool(p.grip_hold) || !isBool(p.can_enable_arm) || !isBool(p.can_enable_claw)
    || !['absolute', 'relative'].includes(p.mapping_mode as string) || !orNull(p.alignment_deg_s, 0, 1000)
    || !isString(p.message) || !isString(p.input_status) || !isString(p.alignment_progress)
    || !Array.isArray(p.limited) || !p.limited.every(isString)
    || !orNull(p.loop_hz, 0, 10000) || !orNull(p.read_ms, 0, 100000)) return 'invalid physical robot status'
  if (!Array.isArray(p.joints) || p.joints.length !== ROBOT_JOINTS.length || !p.joints.every((j) => isObject(j)
    && orNull(j.actual_deg, -720, 720) && orNull(j.preview_deg, -720, 720) && orNull(j.position, -8192, 8192)
    && orNull(j.sent, -8192, 8192) && orNull(j.gap_deg, -720, 720) && isString(j.flag)
    && orNull(j.load, -8192, 8192) && isBool(j.torque))) return 'invalid physical joint table'
  return null
}

function isResponseSettings(v: unknown): boolean {
  if (!isObject(v) || !isObject(v.joint_lead_deg)) return false
  const leads = v.joint_lead_deg
  return Object.entries(RESPONSE_RANGES).every(([key, [lo, hi]]) => within(v[key], lo, hi))
    && BODY_JOINTS.every((name) => within(leads[name], LEAD_RANGE[0], LEAD_RANGE[1]))
}

function validateResponse(r: Record<string, unknown>): string | null {
  if (!isResponseSettings(r.values) || (r.pending !== null && !isResponseSettings(r.pending))
    || (r.startup !== null && !isResponseSettings(r.startup))) return 'invalid response settings'
  if (!Array.isArray(r.profiles) || r.profiles.length < 1 || r.profiles.length > 10
    || !r.profiles.every((p) => isObject(p) && isString(p.name) && isString(p.description))) return 'invalid response profiles'
  for (const index of [r.profile, r.pending_profile]) {
    if (index !== null && (!Number.isSafeInteger(index) || (index as number) < 0 || (index as number) >= r.profiles.length)) {
      return 'invalid response profile'
    }
  }
  return null
}

function validateArmSettings(a: Record<string, unknown>): string | null {
  const v = a.values
  if (!isObject(v) || !ARM_LENGTH_KEYS.every((key) => within(v[key], ARM_LENGTH_RANGE[0], ARM_LENGTH_RANGE[1]))
    || !ARM_POSITION_KEYS.every((key) => within(v[key], ARM_POSITION_RANGE[0], ARM_POSITION_RANGE[1]))
    || !isObject(v.axes) || !SENSOR_ROLES.every((role) => SENSOR_AXES.includes((v.axes as Record<string, unknown>)[role] as SensorAxis))
    || !isBool(a.editable) || !isReply(a.request)) return 'invalid arm settings'
  return null
}

function validateDirections(d: Record<string, unknown>): string | null {
  const reverse = d.reverse
  if (!isObject(reverse) || !ROBOT_JOINTS.every((name) => isBool(reverse[name])) || !isBool(d.editable) || !isReply(d.request)) {
    return 'invalid joint directions'
  }
  return null
}

function validateCoins(c: Record<string, unknown>): string | null {
  if (!Array.isArray(c.motors) || c.motors.length !== 4 || !c.motors.every((m) => isObject(m) && isBool(m.available)
    && within(m.percent, 0, 100) && within(m.output, 0, 100) && within(m.running_s, 0, 600)
    && Number.isSafeInteger(m.acks) && (m.acks as number) >= 0 && isBool(m.feedback) && isString(m.detail))
    || !isBool(c.editable) || !isReply(c.request)) return 'invalid coin status'
  return null
}

function validateWearable(w: Record<string, unknown>): string | null {
  if (!Array.isArray(w.imus) || w.imus.length !== 3 || !w.imus.every((i) => isObject(i) && isBool(i.live)
    && orNull(i.hz, 0, 10000) && (i.age_ms === null || (isNumber(i.age_ms) && i.age_ms >= 0)))) return 'invalid IMU health'
  if (!Array.isArray(w.boards) || w.boards.length !== 2 || !w.boards.every((b) => isObject(b) && isString(b.name)
    && isString(b.port) && isBool(b.connected) && isString(b.driver) && isString(b.message))) return 'invalid board health'
  const j = w.joystick
  if (j !== null && (!isObject(j) || !within(j.x, 0, 4095) || !within(j.y, 0, 4095) || !orNull(j.nx, -1, 1)
    || !orNull(j.ny, -1, 1) || !isBool(j.pressed) || !isBool(j.fresh))) return 'invalid joystick reading'
  const c = w.joystick_center
  if (!isObject(c) || !orNull(c.x, 0, 4095) || !orNull(c.y, 0, 4095) || !orNull(c.deadzone, 0, 1)) return 'invalid joystick calibration'
  return null
}

function validateGeometry(g: Record<string, unknown>): string | null {
  if (!Array.isArray(g.joints) || g.joints.length !== 4 || !g.joints.every((p) => isVec3(p, 5))
    || !Array.isArray(g.markers) || g.markers.length !== 3 || !g.markers.every((p) => isVec3(p, 5))
    || !Array.isArray(g.boards) || g.boards.length !== 3
    || !g.boards.every((axes) => Array.isArray(axes) && axes.length === 3 && axes.every((a) => isVec3(a, 1.01)))
    || !isVec3(g.palm_side, 1.01) || !orNull(g.elbow_deg, 0, 180) || !orNull(g.wrist_deg, 0, 180)
    || !isBool(g.calibrated)) return 'invalid arm geometry'
  return null
}

/** Optional sections of the calibration status. Each is checked only when present and not null. */
const CALIBRATION_SECTIONS: [string, (v: Record<string, unknown>) => string | null][] = [
  ['physical', validatePhysical],
  ['response', validateResponse],
  ['arm_settings', validateArmSettings],
  ['directions', validateDirections],
  ['coins', validateCoins],
  ['wearable', validateWearable],
  ['geometry', validateGeometry],
]

export function validateCalibration(m: unknown): string | null {
  if (!isObject(m)) return 'not an object'
  if (!isNumber(m.t)) return 't must be a number'
  if (typeof m.session !== 'string' || !m.session || !Number.isSafeInteger(m.revision) || (m.revision as number) < 0) return 'invalid calibration session'
  if (!CALIBRATION_PHASES.includes(m.phase as CalibrationMessage['phase'])) return 'invalid calibration phase'
  if (m.capture_pose !== null && !['A', 'B', 'C', 'D', 'E', 'F', 'R'].includes(m.capture_pose as string)) return 'invalid capture pose'
  if (['preparing', 'sampling'].includes(m.phase as string) !== (m.capture_pose !== null)) return 'capture pose must match phase'
  if (!within(m.remaining_s, 0, 5)) return 'invalid capture countdown'
  if (typeof m.input_fresh !== 'boolean' || typeof m.can_recenter !== 'boolean') return 'invalid calibration readiness'
  if (!Array.isArray(m.sensors) || m.sensors.length !== 3 || !m.sensors.every((s) => typeof s === 'boolean')) return 'sensors must be three booleans'
  if (typeof m.detail !== 'string') return 'detail must be a string'
  if (m.replay !== undefined && m.replay !== null) {
    const r = m.replay
    if (!isObject(r) || !REPLAY_PHASES.includes(r.phase as ReplayStatus['phase']) || !Number.isSafeInteger(r.revision)
      || (r.revision as number) < 0 || !within(r.duration_s, 0, 120) || !within(r.elapsed_s, 0, 120)
      || !Number.isSafeInteger(r.samples) || !within(r.samples, 0, 6002)
      || typeof r.includes_slider !== 'boolean' || typeof r.detail !== 'string') return 'invalid replay status'
    if (r.request !== null && (!isObject(r.request) || typeof r.request.id !== 'string' || typeof r.request.ok !== 'boolean'
      || typeof r.request.applied !== 'boolean' || typeof r.request.detail !== 'string')) return 'invalid replay acknowledgment'
    if (r.recorded_wall !== undefined && r.recorded_wall !== null && !isNumber(r.recorded_wall)) return 'invalid replay recording time'
    if (r.waypoints !== undefined && (!Array.isArray(r.waypoints) || !r.waypoints.every((t) => within(t, 0, 120)))) return 'invalid replay waypoints'
    if (r.events !== undefined && (!Array.isArray(r.events) || !r.events.every((e) => isObject(e) && within(e.t, 0, 120)
      && (e.kind === 'grasp' || e.kind === 'release')))) return 'invalid replay events'
    if (r.agent !== undefined && r.agent !== null) {
      const a = r.agent
      if (!isObject(a) || !SMART_STATES.includes(a.state as SmartAgentStatus['state']) || !Number.isSafeInteger(a.seq)
        || (a.run !== null && typeof a.run !== 'string') || !Number.isSafeInteger(a.count) || typeof a.detail !== 'string'
        || typeof a.carrying !== 'boolean') return 'invalid smart replay status'
      if (a.action !== null && (!isObject(a.action) || (a.action.action !== null && typeof a.action.action !== 'string')
        || typeof a.action.why !== 'string')) return 'invalid smart replay action'
      const result = a.result
      if (!isObject(result) || !Number.isSafeInteger(result.seq) || typeof result.ok !== 'boolean'
        || (result.action !== null && typeof result.action !== 'string') || typeof result.detail !== 'string') return 'invalid smart replay result'
    }
  }
  if (m.slider !== undefined && m.slider !== null) {
    const s = m.slider
    if (!isObject(s) || typeof s.referenced !== 'boolean' || typeof s.enabled !== 'boolean' || typeof s.detail !== 'string'
      || (s.pos !== null && !within(s.pos, 0, 100)) || (s.target !== null && !within(s.target, 0, 100))) return 'invalid slider control'
    if (s.request !== null && (!isObject(s.request) || typeof s.request.id !== 'string'
      || typeof s.request.ok !== 'boolean' || typeof s.request.detail !== 'string')) return 'invalid slider acknowledgment'
  }
  if (m.tuning !== undefined && m.tuning !== null) {
    const c = m.tuning
    if (!isObject(c) || !isObject(c.values) || !Number.isSafeInteger(c.revision) || (c.revision as number) < 0
      || !Number.isSafeInteger(c.applied_revision) || (c.applied_revision as number) < 0
      || (c.applied_revision as number) > (c.revision as number)
      || typeof c.connected !== 'boolean' || typeof c.fresh !== 'boolean' || typeof c.enabled !== 'boolean'
      || typeof c.values.load_stops_enabled !== 'boolean') return 'invalid robot tuning state'
    if ((c.can_reconnect !== undefined && typeof c.can_reconnect !== 'boolean')
      || (c.detail !== undefined && typeof c.detail !== 'string')) return 'invalid connection status'
    if (c.reconnect_request !== undefined && c.reconnect_request !== null && (!isObject(c.reconnect_request)
      || typeof c.reconnect_request.id !== 'string' || typeof c.reconnect_request.ok !== 'boolean'
      || typeof c.reconnect_request.detail !== 'string')) return 'invalid reconnect acknowledgment'
    for (const [key, [lo, hi]] of Object.entries(ROBOT_SETTING_RANGES)) {
      if ((key === 'gripper_contact_load' || key === 'gripper_hold_load') && c.values[key] === undefined) continue
      if (!within(c.values[key], lo, hi)) return 'invalid robot tuning value'
    }
    if (isNumber(c.values.gripper_hold_load) && isNumber(c.values.gripper_contact_load)
      && c.values.gripper_hold_load > c.values.gripper_contact_load) return 'holding load exceeds contact load'
    if (c.grasp !== undefined && c.grasp !== null && (!isObject(c.grasp) || typeof c.grasp.holding !== 'boolean'
      || !within(c.grasp.load, 0, 1023) || typeof c.grasp.detail !== 'string')) return 'invalid grasp state'
    if (c.request !== null && (!isObject(c.request) || typeof c.request.id !== 'string'
      || typeof c.request.ok !== 'boolean' || typeof c.request.applied !== 'boolean' || typeof c.request.detail !== 'string'
      || (c.request.revision !== null && (!Number.isSafeInteger(c.request.revision) || (c.request.revision as number) < 0)))) return 'invalid tuning acknowledgment'
  }
  if (m.range_capture !== undefined && typeof m.range_capture !== 'boolean') return 'invalid range capture capability'
  if (m.pan_capture !== undefined && typeof m.pan_capture !== 'boolean') return 'invalid pan capture capability'
  if (m.saved_config !== undefined && m.saved_config !== null && (!isObject(m.saved_config)
    || typeof m.saved_config.path !== 'string' || typeof m.saved_config.detail !== 'string'
    || !['none', 'saved', 'loaded', 'error', 'demo'].includes(m.saved_config.state as string))) return 'invalid saved config status'
  if (m.motion_ranges !== undefined) {
    if (!isObject(m.motion_ranges)) return 'invalid learned ranges'
    for (const [key, lo, hi] of [['elbow_max_deg', 45, 175], ['shoulder_min_deg', -90, -20], ['shoulder_max_deg', 30, 90], ['pan_left_deg', 15, 150], ['pan_right_deg', 15, 150]] as const) {
      if (m.motion_ranges[key] !== undefined && !within(m.motion_ranges[key], lo, hi)) return 'invalid learned range'
    }
  }
  if (m.request !== null && (!isObject(m.request) || typeof m.request.id !== 'string'
    || typeof m.request.ok !== 'boolean' || typeof m.request.detail !== 'string')) return 'invalid calibration acknowledgment'
  if (m.control_request !== undefined && !isReply(m.control_request, true)) return 'invalid robot control acknowledgment'
  for (const [key, check] of CALIBRATION_SECTIONS) {
    const section = m[key]
    if (section === undefined || section === null) continue
    if (!isObject(section)) return `invalid ${key}`
    const error = check(section)
    if (error) return error
  }
  return null
}

const isText = (v: unknown): v is string => typeof v === 'string'

export function validateAssist(m: unknown): string | null {
  if (!isObject(m)) return 'not an object'
  if (!isNumber(m.t)) return 't must be a number'
  if (!Number.isSafeInteger(m.seq) || (m.seq as number) < 0) return 'seq must be a non-negative integer'
  if (!ASSIST_STATUSES.includes(m.status as AssistStatus)) return `unknown status ${JSON.stringify(m.status)}`
  if (m.detail !== null && !isText(m.detail)) return 'detail must be a string or null'
  if (m.model !== null && !isText(m.model)) return 'model must be a string or null'
  if ((m.status === 'ok') !== (m.result !== null)) return 'result is required with status ok and only then'
  if (m.result === null) return null
  const r = m.result
  if (!isObject(r) || !isText(r.scene)) return 'result.scene must be a string'
  const p = r.patient
  if (!isObject(p) || typeof p.present !== 'boolean' || !Array.isArray(p.observations) || !p.observations.every(isText)
    || !URGENCIES.includes(p.urgency as Urgency)
    || (p.state !== undefined && !PATIENT_STATES.includes(p.state as PatientState))) return 'invalid result.patient'
  if (r.touch_checks !== undefined && (!Array.isArray(r.touch_checks) || !r.touch_checks.every((c) => isObject(c)
    && TOUCH_KINDS.includes(c.kind as TouchKind) && isText(c.text) && isText(c.reason)))) return 'invalid result.touch_checks'
  if (!Array.isArray(r.suggestions) || !r.suggestions.every((s) => isObject(s) && isText(s.text)
    && (s.item === null || isText(s.item)))) return 'invalid result.suggestions'
  if (!Array.isArray(r.items) || !r.items.every((i) => isObject(i) && isText(i.name) && i.name !== ''
    && isText(i.prompt) && isText(i.reason) && typeof i.visible === 'boolean' && typeof i.known === 'boolean')) {
    return 'invalid result.items'
  }
  return null
}

export function validateTask(m: unknown): string | null {
  if (!isObject(m)) return 'not an object'
  if (!isNumber(m.t)) return 't must be a number'
  if (!Number.isSafeInteger(m.seq) || (m.seq as number) < 0) return 'seq must be a non-negative integer'
  if (!ASSIST_STATUSES.includes(m.status as AssistStatus)) return `unknown status ${JSON.stringify(m.status)}`
  if (m.detail !== null && !isText(m.detail)) return 'detail must be a string or null'
  if (m.model !== null && !isText(m.model)) return 'model must be a string or null'
  if (m.recording !== null && !isNumber(m.recording)) return 'recording must be a number or null'
  if ((m.status === 'ok') !== (m.brief !== null)) return 'brief is required with status ok and only then'
  if (m.brief === null) return null
  const b = m.brief
  if (!isObject(b) || !isText(b.summary) || !isText(b.rule) || !isText(b.done_when)) return 'invalid brief'
  if (!Array.isArray(b.objects) || !b.objects.every((o) => isObject(o) && isText(o.name) && isText(o.description))) return 'invalid brief.objects'
  if (!Array.isArray(b.steps) || !b.steps.every((s) => isObject(s) && isText(s.text) && isText(s.expect)
    && Number.isSafeInteger(s.from) && Number.isSafeInteger(s.to))) return 'invalid brief.steps'
  return null
}

export function validateAgent(m: unknown): string | null {
  if (!isObject(m)) return 'not an object'
  if (!isNumber(m.t)) return 't must be a number'
  if (m.run !== null && !isText(m.run)) return 'run must be a string or null'
  if (m.seq !== null && !Number.isSafeInteger(m.seq)) return 'seq must be an integer or null'
  if (m.turn !== null && !Number.isSafeInteger(m.turn)) return 'turn must be an integer or null'
  if (!ASSIST_STATUSES.includes(m.status as AssistStatus)) return `unknown status ${JSON.stringify(m.status)}`
  if (m.detail !== null && !isText(m.detail)) return 'detail must be a string or null'
  if (m.model !== null && !isText(m.model)) return 'model must be a string or null'
  if ((m.seq !== null) !== isText(m.action)) return 'an action is required with seq and only then'
  if (m.why !== undefined && !isText(m.why)) return 'why must be a string'
  return null
}

export const VALIDATORS: Record<Topic, (m: unknown) => string | null> = {
  assist: validateAssist,
  task: validateTask,
  agent: validateAgent,
  vision: validateVision,
  labels: validateLabels,
  pressure: validatePressure,
  imu: validateImu,
  robot: validateRobot,
  haptics: validateHaptics,
  calibration: validateCalibration,
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
