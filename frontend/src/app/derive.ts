import { JOINTS, LIMITS } from '../config'
import { assistSpeech } from '../lib/assistSpeech'
import { sentenceCase } from '../lib/format'
import { useSession } from '../state/session'
import { useSettings } from '../state/settings'
import { AlertTracker, type Condition } from '../telemetry/alerts'
import { bus } from '../telemetry/bus'
import type { Topic } from '../telemetry/contracts'
import { classifyVision, sameView, viewOf } from '../telemetry/vision'

/**
 * Watches the telemetry bus and copies only discrete changes into the session store:
 * vision state, phase, captions, the haptics setting and alerts. This is the one place where
 * 30 to 50 Hz data is reduced to the handful of React updates the interface needs.
 */

let trialPeak: number | null = null
let gripHighSeen = false
/** Vision repeats `alert: "dropped"` for about a second; the flag makes sure the 10 Hz tick sees it. */
let droppedSeen = false
let loadHighJoint: number | null = null
let slowSinceMs: number | null = null
let hapticsHoldUntilMs = 0

/** Highest relative grip seen since the last reset, or null if no pressure data has arrived. */
export const getTrialPeak = (): number | null => trialPeak
export const resetTrialPeak = (): void => {
  trialPeak = null
}

/** After a local toggle, ignore the stream briefly so an in-flight old value does not undo it. */
export const holdHapticsSetting = (): void => {
  hapticsHoldUntilMs = performance.now() + 600
}

function speak(text: string): void {
  if (!useSettings.getState().speak || !('speechSynthesis' in window)) return
  window.speechSynthesis.cancel() // a newer sentence replaces one still being read
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(text))
}

const SILENT_STREAMS: { topic: Topic; where: Condition['where']; text: string }[] = [
  { topic: 'pressure', where: 'touch', text: 'No pressure data' },
  { topic: 'haptics', where: 'touch', text: 'No haptics data' },
  { topic: 'robot', where: 'arm', text: 'No arm data' },
  { topic: 'imu', where: 'armband', text: 'No armband data' },
]

export function startDerive(): () => void {
  const tracker = new AlertTracker(LIMITS.alertHoldMs)
  const session = useSession

  const offVision = bus.on('vision', (message) => {
    const store = session.getState()
    const state = classifyVision(message, 0, Date.now() / 1000)
    const view = viewOf(message, state, store.vision)
    if (message.alert === 'dropped') droppedSeen = true
    if (!sameView(view, store.vision)) {
      if (view.phase !== store.vision.phase) store.log('phase', sentenceCase(view.phase))
      store.setVision(view)
    }
    if (message.summary && state !== 'late') {
      store.setCaption(message.summary, Date.now())
      store.log('caption', message.summary)
      speak(message.summary)
    }
  })

  const offLabels = bus.on('labels', (message) => {
    const store = session.getState()
    const next = { classes: message.classes, tags: message.tags, busy: message.busy }
    // Repeated every 2 s: only a change is worth a render.
    if (JSON.stringify(next) !== JSON.stringify(store.labels)) store.setLabels(next)
  })

  const offPressure = bus.on('pressure', (message) => {
    let hi = 0
    for (const v of message.norm) if (v > hi) hi = v
    if (hi >= LIMITS.gripCaution) gripHighSeen = true
    if (session.getState().trial) trialPeak = Math.max(trialPeak ?? 0, hi)
  })

  const offRobot = bus.on('robot', (message) => {
    if (!message.load) return
    for (let i = 0; i < message.load.length; i++) {
      if (Math.abs(message.load[i]) >= LIMITS.loadCaution) loadHighJoint = i
    }
  })

  const offAssist = bus.on('assist', (message) => {
    const store = session.getState()
    const previous = store.assist
    if (previous && previous.seq === message.seq && previous.t === message.t) return // the 5 s repeat
    store.setAssist(message)
    if (previous && previous.seq === message.seq) return // the process restarted; nothing new to announce
    if (message.status === 'error' && message.detail) store.log('assist', message.detail, 'caution')
    if (message.status !== 'ok' || !message.result) return
    const { result } = message
    store.log('assist', result.scene, result.patient.urgency === 'urgent' ? 'warning' : undefined)
    for (const check of result.touch_checks ?? []) store.log('assist', `Touch check: ${check.text}`)
    const line = assistSpeech(result)
    if (line) speak(line)
  })

  const offHaptics = bus.on('haptics', (message, receivedMs) => {
    if (receivedMs < hapticsHoldUntilMs) return
    if (message.enabled !== session.getState().haptics) session.getState().setHaptics(message.enabled)
  })

  const timer = setInterval(() => {
    const now = performance.now()
    const store = session.getState()
    bus.sampleRates(now)

    // Silence and lateness can only be noticed by the clock, not by a message arriving.
    const latest = bus.latest('vision')
    const state = classifyVision(latest, bus.ageMs('vision', now), Date.now() / 1000)
    if (state !== store.vision.state) store.setVision(viewOf(latest, state, store.vision))

    const visionHz = bus.state('vision').hz
    const flowing = state === 'ok' || state === 'no_target' || state === 'low_confidence'
    if (flowing && visionHz > 0 && visionHz < LIMITS.fpsCaution) slowSinceMs ??= now
    else slowSinceMs = null

    const assist = store.assist
    const urgency = assist?.status === 'ok' ? assist.result?.patient.urgency : undefined
    const conditions: Condition[] = [
      {
        key: 'patient-urgent',
        level: 'warning',
        where: 'camera',
        text: 'Assist: patient may need help now',
        on: urgency === 'urgent',
        latch: false,
      },
      {
        key: 'object-dropped',
        level: 'warning',
        where: 'camera',
        text: 'Object may have dropped',
        on: droppedSeen,
        latch: true,
      },
      {
        key: 'vision-lost',
        level: 'warning',
        where: 'camera',
        text: state === 'late' ? 'Vision data delayed' : state === 'silent' ? 'No vision data' : 'Vision feed lost',
        on: state === 'degraded' || state === 'silent' || state === 'late',
        latch: false,
      },
      {
        key: 'vision-slow',
        level: visionHz < LIMITS.fpsWarning ? 'warning' : 'caution',
        where: 'camera',
        text: 'Vision rate low',
        on: slowSinceMs !== null && now - slowSinceMs > 1500,
        latch: false,
      },
      {
        key: 'grip-limit',
        level: 'caution',
        where: 'touch',
        text: 'Grip near sensor limit',
        on: gripHighSeen,
        latch: true,
      },
      {
        key: 'load-high',
        level: 'caution',
        where: 'arm',
        text: `High load: ${JOINTS[loadHighJoint ?? 0].label.toLowerCase()}`,
        on: loadHighJoint !== null,
        latch: true,
      },
      ...SILENT_STREAMS.map(({ topic, where, text }): Condition => ({
        key: `${topic}-silent`,
        level: 'caution',
        where,
        text,
        // Only a stream that was flowing and stopped. One that never started is not a fault.
        on: bus.state(topic).count > 0 && bus.ageMs(topic, now) > LIMITS.streamSilentMs,
        latch: false,
      })),
      {
        key: 'link',
        level: 'warning',
        where: 'link',
        text: 'Bridge not reachable',
        on: store.link.state === 'retrying',
        latch: false,
      },
    ]
    gripHighSeen = false
    droppedSeen = false
    loadHighJoint = null

    const update = tracker.update(conditions, now)
    if (update.changed) store.setAlerts(update.alerts)
    for (const alert of update.raised) store.log('alert', alert.text, alert.level)
  }, 100)

  return () => {
    offVision()
    offLabels()
    offPressure()
    offRobot()
    offHaptics()
    offAssist()
    clearInterval(timer)
    tracker.clear()
    slowSinceMs = null
    droppedSeen = false
  }
}
