import { fileStamp, downloadText } from '../lib/format'
import { useSession } from '../state/session'
import { useSettings } from '../state/settings'
import { newTrialId, useTrials } from '../state/trials'
import { bus } from '../telemetry/bus'
import type { Command } from '../telemetry/contracts'
import { MAX_LINES, Recorder } from '../telemetry/recorder'
import { Simulator } from '../telemetry/sources/simulator'
import type { Source } from '../telemetry/sources/types'
import { WebSocketSource } from '../telemetry/sources/websocket'
import { getTrialPeak, holdHapticsSetting, resetTrialPeak } from './derive'

/**
 * The app's actions: connecting a data source, sending commands, recording, and running
 * trials. Buttons and keyboard shortcuts both call these, so each action has one definition.
 */

export const recorder = new Recorder(bus)

let source: Source | null = null
let simulator: Simulator | null = null

/** The running simulator, or null when live data is in use. */
export const getSimulator = (): Simulator | null => simulator

/** Starts the data source chosen in settings, replacing whichever was running. */
export function connect(): void {
  const { source: kind, wsUrl } = useSettings.getState()
  const session = useSession.getState()

  if (session.recording) stopRecording('The data source changed')
  if (session.trial) finishTrial('aborted')
  source?.stop()
  simulator = null
  bus.reset()
  session.resetStreams()

  if (kind === 'simulator') {
    simulator = new Simulator(bus)
    source = simulator
    session.setLink({ state: 'simulated', detail: null, retryAt: null })
    // Carry the haptics setting over, so the switch and the simulator agree.
    simulator.send({ type: 'set_haptics', enabled: session.haptics })
    simulator.start()
    session.log('link', 'Simulator started')
    return
  }

  let wasOpen = false
  source = new WebSocketSource(bus, wsUrl, (status) => {
    const store = useSession.getState()
    if (status.state === 'open') {
      wasOpen = true
      store.log('link', `Connected to ${wsUrl}`)
    } else if (status.state === 'retrying' && wasOpen) {
      wasOpen = false
      store.log('link', 'Connection to the bridge dropped', 'warning')
    }
    store.setLink(status)
  })
  source.start()
}

export function disconnect(): void {
  const session = useSession.getState()
  if (session.recording) stopRecording('Disconnected')
  if (session.trial) finishTrial('aborted')
  source?.stop()
  source = null
  simulator = null
  bus.reset()
  session.resetStreams()
  session.setLink({ state: 'idle', detail: null, retryAt: null })
  session.log('link', 'Disconnected')
}

/** Switches to the simulator. Always an explicit choice: the app never falls back to it silently. */
export function switchToSimulator(): void {
  useSettings.getState().update({ source: 'simulator' })
  connect()
}

export function sendCommand(command: Command): boolean {
  const sent = source?.send(command) ?? false
  if (sent) recorder.note({ type: 'command', command })
  return sent
}

// --- Haptics and target -----------------------------------------------------------------------

export function setHaptics(enabled: boolean): void {
  const session = useSession.getState()
  if (session.trial || enabled === session.haptics) return // the condition is fixed while a trial runs
  if (!sendCommand({ type: 'set_haptics', enabled })) {
    session.log('command', 'Haptics not changed: no connection', 'caution')
    return
  }
  holdHapticsSetting()
  session.setHaptics(enabled)
  session.log('command', enabled ? 'Haptics on' : 'Haptics off (control condition)')
}

export const toggleHaptics = (): void => setHaptics(!useSession.getState().haptics)

/** Asks vision to target one label, or any object when `label` is null. */
export function selectTarget(label: string | null): void {
  const session = useSession.getState()
  const sent = sendCommand(label ? { type: 'select_target', label } : { type: 'clear_target' })
  if (!sent) {
    session.log('command', 'Target not changed: no connection', 'caution')
    return
  }
  session.setTargetLabel(label)
  session.log('command', label ? `Target set to ${label}` : 'Target set to any object')
}

export function toggleSpeak(): void {
  const settings = useSettings.getState()
  if (settings.speak && 'speechSynthesis' in window) window.speechSynthesis.cancel()
  settings.update({ speak: !settings.speak })
}

// --- Recording ----------------------------------------------------------------------------------

export function startRecording(): void {
  const session = useSession.getState()
  if (session.recording || session.link.state === 'idle') return
  const { source: kind, wsUrl } = useSettings.getState()
  recorder.start(kind === 'simulator' ? { source: kind } : { source: kind, url: wsUrl }, () =>
    stopRecording(`The recording reached its limit of ${MAX_LINES.toLocaleString()} messages`),
  )
  session.setRecording({ startedAt: Date.now() })
  session.log('record', 'Started')
}

export function stopRecording(reason?: string): void {
  const session = useSession.getState()
  if (!session.recording) return
  const count = recorder.count
  const blob = recorder.stop()
  const simulated = useSettings.getState().source === 'simulator'
  downloadText(`paul-${simulated ? 'simulated' : 'session'}-${fileStamp(session.recording.startedAt)}.jsonl`, blob)
  session.setRecording(null)
  session.log('record', `${reason ? `${reason}. ` : ''}Saved ${count.toLocaleString()} messages`)
}

export const toggleRecording = (): void =>
  useSession.getState().recording ? stopRecording() : startRecording()

// --- Trials -------------------------------------------------------------------------------------

export function startTrial(): void {
  const session = useSession.getState()
  if (session.trial || session.link.state === 'idle') return
  resetTrialPeak()
  const trial = { startedAt: Date.now(), haptics: session.haptics, drops: 0, deformations: 0 }
  session.setTrial(trial)
  session.log('trial', `Started ${trial.haptics ? 'with' : 'without'} haptics`)
  recorder.note({ type: 'trial_start', haptics: trial.haptics })
}

export function finishTrial(outcome: 'completed' | 'aborted'): void {
  const session = useSession.getState()
  const trial = session.trial
  if (!trial) return
  const durationS = (Date.now() - trial.startedAt) / 1000
  useTrials.getState().add({
    id: newTrialId(),
    startedAt: trial.startedAt,
    durationS,
    haptics: trial.haptics,
    drops: trial.drops,
    deformations: trial.deformations,
    outcome,
    peakGrip: getTrialPeak(),
    origin: session.link.state === 'simulated' ? 'simulated' : 'live',
  })
  session.setTrial(null)
  session.log('trial', outcome === 'completed' ? `Finished in ${durationS.toFixed(1)} s` : 'Aborted')
  recorder.note({ type: 'trial_end', outcome, durationS, drops: trial.drops, deformations: trial.deformations })
}

function mark(kind: 'drops' | 'deformations', text: string): void {
  const session = useSession.getState()
  if (!session.trial) return
  session.setTrial({ ...session.trial, [kind]: session.trial[kind] + 1 })
  session.log('trial', text)
  recorder.note({ type: kind === 'drops' ? 'drop' : 'deformation' })
}

export const markDrop = (): void => mark('drops', 'Drop marked')
export const markDeformation = (): void => mark('deformations', 'Deformation marked')
