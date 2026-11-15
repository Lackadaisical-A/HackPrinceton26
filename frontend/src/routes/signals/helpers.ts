import type { Topic } from '../../telemetry/contracts'

/** Shown in a cell that has no value yet. Never a zero, which would read as a measurement. */
export const NONE = '–'

const NBSP = ' '

export const TOPIC_LABELS: Record<Topic, string> = {
  vision: 'Vision',
  labels: 'Targets',
  pressure: 'Pressure',
  imu: 'IMU',
  robot: 'Robot',
  haptics: 'Haptics',
  calibration: 'Calibration',
  assist: 'Assist',
  task: 'Smart task',
  agent: 'Smart action',
}

/** Returns what is wrong with a bridge address and how to fix it, or null when it is usable. */
export function checkBridgeAddress(text: string): string | null {
  const value = text.trim()
  if (value === '') return 'Enter the bridge address, for example ws://127.0.0.1:8765.'
  if (!/^wss?:\/\//i.test(value)) {
    return 'The address must start with ws:// or wss://. For example, ws://127.0.0.1:8765.'
  }
  try {
    if (!new URL(value).hostname) throw new Error('no host')
  } catch {
    return 'This address is incomplete. Add a host and a port after ws://, for example ws://127.0.0.1:8765.'
  }
  return null
}

/** The camera stream is optional, so an empty field is valid. */
export function checkCameraAddress(text: string): string | null {
  const value = text.trim()
  if (value === '') return null
  if (!/^https?:\/\//i.test(value)) {
    return 'The camera address must start with http:// or https://. Leave the field empty if there is no stream.'
  }
  try {
    if (!new URL(value).hostname) throw new Error('no host')
  } catch {
    return 'This address is incomplete. Add a host after http://, for example http://127.0.0.1:8766/stream.'
  }
  return null
}

/** Time since a message arrived: "18 ms", "2.4 s", "3 min". Infinity means nothing has arrived. */
export function formatAge(ms: number): string {
  if (!Number.isFinite(ms)) return NONE
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}${NBSP}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}${NBSP}s`
  return `${Math.floor(ms / 60_000)}${NBSP}min`
}

/** A measured rate, or the placeholder when the stream has delivered nothing. */
export function formatRate(hz: number, count: number): string {
  return count === 0 ? NONE : `${hz.toFixed(1)}${NBSP}Hz`
}

/**
 * JSON for reading: one key to a line, but a list of plain values stays on one line, so a
 * bounding box or a row of motor amplitudes reads as the single thing it is.
 */
export function pretty(value: unknown, indent = ''): string {
  if (Array.isArray(value)) {
    if (value.every((v) => v === null || typeof v !== 'object')) {
      return `[${value.map((v) => JSON.stringify(v) ?? 'null').join(', ')}]`
    }
    const inner = `${indent}  `
    return `[\n${value.map((v) => inner + pretty(v, inner)).join(',\n')}\n${indent}]`
  }
  if (typeof value === 'object' && value !== null) {
    const entries = Object.entries(value)
    if (entries.length === 0) return '{}'
    const inner = `${indent}  `
    return `{\n${entries.map(([k, v]) => `${inner}${JSON.stringify(k)}: ${pretty(v, inner)}`).join(',\n')}\n${indent}}`
  }
  return JSON.stringify(value) ?? 'null'
}
