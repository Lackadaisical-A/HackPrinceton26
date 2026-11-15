import { AIM_DEFAULT, DEADZONE, LIMITS } from '../../config'
import type { Aim } from '../../lib/aim'
import { clamp, quatFromEuler } from '../../lib/math'
import type { TelemetryBus } from '../bus'
import type { Command, HapticCue, Phase, Topic, VisionMessage } from '../contracts'
import { TOPIC_INFO, TOPICS } from '../contracts'
import { CYCLE_S, PHASE_STARTS, scenarioAt, summaryFor, type SimFrame } from './scenario'
import type { Source } from './types'

const round = (value: number, digits: number): number => {
  const scale = 10 ** digits
  return Math.round(value * scale) / scale
}

/** Mirrors summary.min_interval_s in the vision config. */
const SUMMARY_GAP_S = 2
/** Mirrors watchdog.stall_timeout_s and heartbeat_s. */
const STALL_S = 0.5

/**
 * Publishes a scripted pick-and-place onto the bus at each topic's nominal rate, so the app
 * can be built and demonstrated before the ZMQ-to-WebSocket bridge exists.
 */
export class Simulator implements Source {
  /** The current scripted frame. The simulated camera scene reads this directly. */
  frame: SimFrame = scenarioAt(0)

  private readonly bus: TelemetryBus
  private timer: ReturnType<typeof setInterval> | null = null
  private originMs = 0
  private readonly due = Object.fromEntries(TOPICS.map((t) => [t, 0])) as Record<Topic, number>

  private preferred: string | null = null
  private aim: Aim = { ...AIM_DEFAULT }
  private hapticsEnabled = true
  private aligned = false
  private frameId = 0
  /** What the watchdog's heartbeat repeats, as vision's does. */
  private lastPhase: Phase = 'search'
  private lastSummary = ''
  private lastSummaryS = -SUMMARY_GAP_S
  private hadTarget = false
  private squeezeAtMs: number | null = null

  private stalled = false
  private stalledAtMs = 0
  private stallBeats = 0

  constructor(bus: TelemetryBus) {
    this.bus = bus
  }

  start(): void {
    if (this.timer !== null) return
    const now = performance.now()
    this.originMs = now
    for (const topic of TOPICS) this.due[topic] = now
    this.timer = setInterval(() => this.tick(performance.now()), 8)
  }

  stop(): void {
    if (this.timer !== null) clearInterval(this.timer)
    this.timer = null
  }

  send(command: Command): boolean {
    switch (command.type) {
      case 'select_target':
        this.preferred = command.label
        this.restartCycle()
        break
      case 'clear_target':
        this.preferred = null
        break
      case 'set_haptics':
        this.hapticsEnabled = command.enabled
        break
      case 'set_phase':
        this.jumpTo(PHASE_STARTS[command.phase])
        break
      case 'set_aim': {
        // As vision does: a point off the picture is refused, not guessed at.
        const inside = (v: number) => Number.isFinite(v) && v >= 0 && v <= 1
        if (!inside(command.x) || !inside(command.y)) return false
        this.aim = { x: command.x, y: command.y }
        this.aligned = false // earned again from the new point, as vision's smoothing starts over
        break
      }
      case 'tag_visual':
      case 'tag_text':
      case 'untag':
        // The scripted scene has no detector to teach, so say the command was not taken rather than applied.
        return false
    }
    return true
  }

  // --- Test switches, used by the Signals page ---------------------------------------------------

  get visionStalled(): boolean {
    return this.stalled
  }

  /** Stops fresh vision frames, as if the camera had stalled. The watchdog heartbeat takes over. */
  setVisionStalled(stalled: boolean): void {
    if (stalled === this.stalled) return
    this.stalled = stalled
    this.stalledAtMs = performance.now()
    this.stallBeats = 0
  }

  /** Squeezes too hard for a moment. Only has an effect while the gripper is holding something. */
  squeezeNow(): void {
    this.squeezeAtMs = performance.now()
  }

  // -----------------------------------------------------------------------------------------------

  private restartCycle(): void {
    this.jumpTo(0)
  }

  private jumpTo(cycleSeconds: number): void {
    const now = performance.now()
    const elapsed = (now - this.originMs) / 1000
    const cycleStart = Math.floor(elapsed / CYCLE_S) * CYCLE_S
    this.originMs = now - (cycleStart + cycleSeconds) * 1000
    this.hadTarget = false
  }

  private tick(nowMs: number): void {
    const time = (nowMs - this.originMs) / 1000
    const sinceSqueezeS = this.squeezeAtMs === null ? null : (nowMs - this.squeezeAtMs) / 1000
    if (sinceSqueezeS !== null && sinceSqueezeS > 3) this.squeezeAtMs = null
    const frame = scenarioAt(time, { preferred: this.preferred, sinceSqueezeS, aim: this.aim })
    this.frame = frame

    const offset = Math.hypot(frame.dx, frame.dy)
    if (frame.status !== 'ok') this.aligned = false
    else if (offset <= DEADZONE.enter) this.aligned = true
    else if (offset > DEADZONE.exit) this.aligned = false

    for (const topic of TOPICS) {
      if (nowMs < this.due[topic]) continue
      const period = 1000 / TOPIC_INFO[topic].rateHz
      // After a pause (a background tab), skip the backlog instead of bursting to catch up.
      this.due[topic] = nowMs - this.due[topic] > 250 ? nowMs + period : this.due[topic] + period
      this.emit(topic, frame, time, nowMs)
    }
  }

  private emit(topic: Topic, frame: SimFrame, time: number, nowMs: number): void {
    const wall = Date.now() / 1000
    switch (topic) {
      case 'vision': {
        const message = this.visionMessage(frame, time, wall, nowMs)
        if (message) this.bus.publish('vision', message, nowMs)
        break
      }
      case 'pressure': {
        const noise = (i: number) => 0.012 * Math.sin(time * (11 + 3 * i) + i)
        const shape = [1, 0.72, 0.94, 0.68]
        const norm = shape.map((s, i) => round(clamp(frame.grip > 0.01 ? frame.grip * s + noise(i) : 0, 0, 1), 3))
        this.bus.publish('pressure', { t: round(wall, 3), raw: norm.map((n) => Math.round(180 + n * 3200)), norm }, nowMs)
        break
      }
      case 'imu': {
        const q = quatFromEuler(frame.roll, frame.pitch, frame.yaw).map((v) => round(v, 5))
        this.bus.publish('imu', { t: round(wall, 3), q: q as [number, number, number, number] }, nowMs)
        break
      }
      case 'robot':
        this.bus.publish(
          'robot',
          { t: round(wall, 3), pos: frame.joints.map((v) => round(v, 1)), load: frame.loads.map((v) => round(v, 3)) },
          nowMs,
        )
        break
      case 'haptics': {
        const { motors, cue } = this.hapticOutput(frame, time)
        this.bus.publish(
          'haptics',
          { t: round(wall, 3), enabled: this.hapticsEnabled, motors: motors.map((v) => round(v, 3)), cue },
          nowMs,
        )
        break
      }
    }
  }

  private visionMessage(frame: SimFrame, time: number, wall: number, nowMs: number): VisionMessage | null {
    if (this.stalled) {
      // The publisher's watchdog: `degraded` once the feed has been quiet for the stall timeout,
      // then again at every heartbeat. The first one carries the spoken line.
      const beatsDue = Math.floor((nowMs - this.stalledAtMs) / 1000 / STALL_S)
      if (beatsDue <= this.stallBeats) return null
      this.stallBeats = beatsDue
      return {
        t: round(wall, 3),
        frame_id: this.frameId,
        status: 'degraded',
        phase: this.lastPhase,
        target: null,
        guidance: null,
        summary: beatsDue === 1 ? 'Vision feed lost.' : null,
        alert: null,
      }
    }

    this.frameId++
    const ok = frame.status === 'ok' && frame.box !== null
    // Capture-to-publish delay, about what the vision module measures on an idle laptop.
    const t = round(wall - (0.014 + 0.003 * Math.sin(time * 5.7)), 3)

    let summary: string | null = null
    let wanted: string | null = null
    if (ok && frame.phase === 'approach') wanted = summaryFor(frame.label, frame.distance, frame.dx, frame.dy)
    else if (frame.status === 'no_target' && this.hadTarget) wanted = 'Target lost.'
    if (wanted && wanted !== this.lastSummary && time - this.lastSummaryS >= SUMMARY_GAP_S) {
      summary = wanted
      this.lastSummary = wanted
      this.lastSummaryS = time
    }
    if (ok) this.hadTarget = true
    else if (frame.status === 'no_target' && summary === 'Target lost.') this.hadTarget = false
    this.lastPhase = frame.phase

    return {
      t,
      frame_id: this.frameId,
      status: frame.status,
      phase: frame.phase,
      target: ok
        ? {
            track_id: 1 + frame.cycle,
            label: frame.label,
            source: 'yolo',
            bbox: frame.box!.map((v) => round(v, 3)) as [number, number, number, number],
            confidence: round(frame.confidence, 2),
          }
        : null,
      guidance: ok
        ? { dx: round(frame.dx, 3), dy: round(frame.dy, 3), distance_hint: frame.distance, aligned: this.aligned }
        : null,
      summary,
      alert: null,
    }
  }

  /** What the haptics layer would command for this frame. Motor order: up, right, down, left. */
  private hapticOutput(frame: SimFrame, time: number): { motors: number[]; cue: HapticCue | null } {
    const off = { motors: [0, 0, 0, 0], cue: null }
    if (!this.hapticsEnabled) return off

    if (frame.grip >= LIMITS.gripCaution) {
      const pulse = (time * 8) % 1 < 0.5 ? 1 : 0.15
      return { motors: [pulse, pulse, pulse, pulse], cue: 'limit' }
    }
    if (frame.grip > 0.05) {
      const level = 0.15 + 0.6 * frame.grip
      return { motors: [level, level, level, level], cue: 'contact' }
    }
    if (frame.phase === 'approach' && frame.status === 'ok' && !this.aligned && !this.stalled) {
      // Pulse the motor on the side the hand should move toward.
      const pulse = (time * 4) % 1 < 0.6 ? 1 : 0
      const drive = (v: number) => (v > 0.05 ? pulse * (0.15 + 0.8 * clamp((v - 0.05) / 0.5, 0, 1)) : 0)
      return { motors: [drive(-frame.dy), drive(frame.dx), drive(frame.dy), drive(-frame.dx)], cue: 'guide' }
    }
    return off
  }
}
