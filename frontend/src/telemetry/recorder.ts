import type { TelemetryBus } from './bus'
import { TOPICS } from './contracts'

/** Lines are flushed into a Blob in chunks, so a long recording does not sit on the JS heap. */
const CHUNK_LINES = 4000
/** About 90 minutes at the full simulated rate. */
export const MAX_LINES = 1_000_000
/**
 * Wearable Lab's calibration status is setup state (about 6 KB at 10 Hz), not trial data: one per
 * second is kept, so a recording shows each change without growing by megabytes a minute.
 */
export const CALIBRATION_RECORD_MS = 1000

export interface RecordingHeader {
  source: 'simulator' | 'websocket'
  /** The bridge address, for live recordings. */
  url?: string
}

/**
 * Records every telemetry message, in arrival order, as JSON Lines.
 *
 * Line 1 is a header. Each later line is {"topic", "recv", "msg"}: `recv` is the arrival time in
 * Unix seconds and `msg` is the message exactly as received, with its own capture time `t`.
 * Interface events (trial start, drops) are recorded on the "ui" topic. Video is not recorded.
 */
export class Recorder {
  private readonly bus: TelemetryBus
  private unsubscribe: (() => void) | null = null
  private chunks: Blob[] = []
  private pending: string[] = []
  private lines = 0
  private onFull: (() => void) | null = null
  private calibrationAtMs = -Infinity

  constructor(bus: TelemetryBus) {
    this.bus = bus
  }

  get active(): boolean {
    return this.unsubscribe !== null
  }

  /** Messages recorded so far. */
  get count(): number {
    return this.lines
  }

  start(header: RecordingHeader, onFull: () => void): void {
    if (this.active) return
    this.chunks = []
    this.pending = []
    this.lines = 0
    this.calibrationAtMs = -Infinity
    this.onFull = onFull
    this.write({
      type: 'header',
      app: 'paul-frontend',
      format: 1,
      started: new Date().toISOString(),
      simulated: header.source === 'simulator',
      ...header,
      topics: TOPICS,
      note: 'Each line: {topic, recv, msg}. recv is arrival time in Unix seconds. Video is not recorded.',
    })
    this.unsubscribe = this.bus.onAny((topic, msg) => {
      if (topic === 'calibration') {
        const nowMs = Date.now()
        if (nowMs - this.calibrationAtMs < CALIBRATION_RECORD_MS) return
        this.calibrationAtMs = nowMs
      }
      this.write({ topic, recv: Math.round(Date.now()) / 1000, msg })
      this.lines++
      if (this.lines >= MAX_LINES) this.onFull?.()
    })
  }

  /** Adds an interface event (a trial marker, a toggled setting) to the recording. */
  note(event: Record<string, unknown>): void {
    if (this.active) this.write({ topic: 'ui', recv: Math.round(Date.now()) / 1000, msg: event })
  }

  /** Stops recording and returns the file contents. */
  stop(): Blob {
    this.unsubscribe?.()
    this.unsubscribe = null
    this.onFull = null
    this.flush()
    const blob = new Blob(this.chunks, { type: 'application/x-ndjson' })
    this.chunks = []
    return blob
  }

  private write(line: unknown): void {
    this.pending.push(JSON.stringify(line))
    if (this.pending.length >= CHUNK_LINES) this.flush()
  }

  private flush(): void {
    if (this.pending.length === 0) return
    this.chunks.push(new Blob([this.pending.join('\n'), '\n']))
    this.pending = []
  }
}
