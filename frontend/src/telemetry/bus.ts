import { TRACE_WINDOW_S } from '../config'
import type { Topic, TopicMap } from './contracts'
import { TOPICS } from './contracts'
import { Ring } from './ring'

/**
 * The telemetry bus keeps high-rate data out of React.
 *
 * Sources publish into it. Canvas renderers and readouts pull the latest sample on their own
 * schedule. Only discrete changes (phase, status, alerts) are copied into the UI store, by
 * telemetry/derive.ts.
 */

export interface TopicState<K extends Topic = Topic> {
  latest: TopicMap[K] | null
  /** performance.now() when the latest message arrived. 0 if none has. */
  receivedMs: number
  /** Messages accepted since the last reset. */
  count: number
  /** Measured arrival rate, refreshed by sampleRates(). */
  hz: number
  /** Frames dropped because they failed validation. */
  rejected: number
  lastError: string | null
}

type Listener<K extends Topic> = (message: TopicMap[K], receivedMs: number) => void
type AnyListener = (topic: Topic, message: TopicMap[Topic], receivedMs: number) => void

const emptyState = <K extends Topic>(): TopicState<K> => ({
  latest: null,
  receivedMs: 0,
  count: 0,
  hz: 0,
  rejected: 0,
  lastError: null,
})

/** Trace history is sized for the nominal 50 Hz streams with headroom. */
const TRACE_CAPACITY = TRACE_WINDOW_S * 100

export class TelemetryBus {
  private readonly states = Object.fromEntries(TOPICS.map((t) => [t, emptyState()])) as {
    [K in Topic]: TopicState<K>
  }
  private readonly listeners = Object.fromEntries(TOPICS.map((t) => [t, new Set()])) as {
    [K in Topic]: Set<Listener<K>>
  }
  private readonly anyListeners = new Set<AnyListener>()
  private readonly rateMarks = Object.fromEntries(TOPICS.map((t) => [t, 0])) as Record<Topic, number>
  private rateSampledMs = 0

  /** Rows of [seconds, highest pad, lowest pad], for the grip trace. */
  readonly gripTrace = new Ring(TRACE_CAPACITY, 3)
  /** Rows of [seconds, strongest motor], for the haptic output trace. */
  readonly hapticTrace = new Ring(TRACE_CAPACITY, 2)
  /** Highest relative pressure seen on each pad since the last reset. */
  readonly padPeak = new Float32Array(4)

  publish<K extends Topic>(topic: K, message: TopicMap[K], nowMs: number = performance.now()): void {
    const state = this.states[topic]
    state.latest = message
    state.receivedMs = nowMs
    state.count++

    if (topic === 'pressure') {
      const norm = (message as TopicMap['pressure']).norm
      let hi = 0
      let lo = 1
      for (let i = 0; i < 4; i++) {
        const v = norm[i]
        if (v > hi) hi = v
        if (v < lo) lo = v
        if (v > this.padPeak[i]) this.padPeak[i] = v
      }
      this.gripTrace.push(nowMs / 1000, hi, lo)
    } else if (topic === 'haptics') {
      const haptics = message as TopicMap['haptics']
      this.hapticTrace.push(nowMs / 1000, haptics.enabled ? Math.max(...haptics.motors) : 0)
    }

    for (const listener of this.listeners[topic]) listener(message, nowMs)
    for (const listener of this.anyListeners) listener(topic, message, nowMs)
  }

  /** Records a frame that failed validation, so the Signals page can show why. */
  reject(topic: Topic, error: string): void {
    const state = this.states[topic]
    state.rejected++
    state.lastError = error
  }

  latest<K extends Topic>(topic: K): TopicMap[K] | null {
    return this.states[topic].latest
  }

  state<K extends Topic>(topic: K): Readonly<TopicState<K>> {
    return this.states[topic]
  }

  /** Milliseconds since the topic's last message arrived. Infinity if none has. */
  ageMs(topic: Topic, nowMs: number = performance.now()): number {
    const state = this.states[topic]
    return state.count === 0 ? Infinity : nowMs - state.receivedMs
  }

  on<K extends Topic>(topic: K, listener: Listener<K>): () => void {
    this.listeners[topic].add(listener)
    return () => this.listeners[topic].delete(listener)
  }

  onAny(listener: AnyListener): () => void {
    this.anyListeners.add(listener)
    return () => this.anyListeners.delete(listener)
  }

  /** Refreshes each topic's measured rate. Call a few times a second. */
  sampleRates(nowMs: number = performance.now()): void {
    if (this.rateSampledMs === 0) {
      // First call: only set the baseline, so the first figure is not diluted by startup time.
      this.rateSampledMs = nowMs
      for (const topic of TOPICS) this.rateMarks[topic] = this.states[topic].count
      return
    }
    const elapsed = nowMs - this.rateSampledMs
    if (elapsed < 200) return
    for (const topic of TOPICS) {
      const state = this.states[topic]
      const instant = ((state.count - this.rateMarks[topic]) * 1000) / elapsed
      // Smooth lightly so the displayed figure is readable, but snap to zero when a stream stops.
      state.hz = instant === 0 ? 0 : state.hz === 0 ? instant : state.hz * 0.6 + instant * 0.4
      this.rateMarks[topic] = state.count
    }
    this.rateSampledMs = nowMs
  }

  resetPadPeaks(): void {
    this.padPeak.fill(0)
  }

  /** Clears all samples. Called when the data source changes, so old data never looks current. */
  reset(): void {
    for (const topic of TOPICS) {
      Object.assign(this.states[topic], emptyState())
      this.rateMarks[topic] = 0
    }
    this.rateSampledMs = 0
    this.gripTrace.clear()
    this.hapticTrace.clear()
    this.padPeak.fill(0)
  }
}

export const bus = new TelemetryBus()
