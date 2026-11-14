import type { Settings } from './settings'

/** Explicit launch URL opts into live data, overriding a previously saved simulator setting. */
export function launchSettings(search: string): Partial<Settings> {
  const params = new URLSearchParams(search)
  if (params.get('source') !== 'live') return {}
  const patch: Partial<Settings> = { source: 'websocket' }
  for (const [key, scheme] of [['wsUrl', 'ws:'], ['cameraUrl', 'http:']] as const) {
    const value = params.get(key)
    if (!value) continue
    try {
      const url = new URL(value)
      if (url.protocol === scheme && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
        && !url.username && !url.password) patch[key] = value
    } catch { /* Keep configured settings when a launch link is malformed. */ }
  }
  return patch
}
