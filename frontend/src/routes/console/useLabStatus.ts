import { useSession } from '../../state/session'
import { bus } from '../../telemetry/bus'
import type { CalibrationMessage } from '../../telemetry/contracts'
import { usePolled } from '../signals/usePolled'
import { calibrationAvailable } from './calibration'

export interface LabStatus {
  /** The newest Wearable Lab status, however old. */
  message: CalibrationMessage | null
  /** Fresh enough to act on, over an open link. Controls are disabled otherwise. */
  available: boolean
  linkOpen: boolean
  simulated: boolean
}

/** Wearable Lab's status (the `calibration` topic), polled for panels that show and act on it. */
export function useLabStatus(hz = 5): LabStatus {
  const link = useSession((s) => s.link.state)
  const [message] = usePolled(() => bus.latest('calibration'), hz)
  const [now] = usePolled(() => Date.now(), hz)
  return {
    message,
    available: calibrationAvailable(message, bus.ageMs('calibration'), link === 'open', now),
    linkOpen: link === 'open',
    simulated: link === 'simulated',
  }
}

/** One line saying why lab controls are unavailable, or '' when they are available. */
export function labUnavailable({ message, available, simulated }: LabStatus): string {
  if (available) return ''
  if (simulated) return 'Simulated data has no wearable controller. Switch to live data and start Wearable Lab.'
  if (!message) return 'Waiting for Wearable Lab. Start it with .\\demo.ps1 -Controller imu (or .\\sensor.ps1 wearable-test for sensors only) and the bridge.'
  return 'Wearable Lab status is stale. Check that it and the bridge are still running.'
}
