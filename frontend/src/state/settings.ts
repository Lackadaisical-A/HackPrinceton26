import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { DEFAULT_CAMERA_URL, DEFAULT_WS_URL } from '../config'
import type { SourceKind } from '../telemetry/sources/types'

export interface Settings {
  /** Which data source to start with. */
  source: SourceKind
  /** Address of the ZMQ-to-WebSocket bridge. */
  wsUrl: string
  /** MJPEG address of the wrist camera stream, used with live data. Empty means none. */
  cameraUrl: string
  /** Read status sentences aloud with the browser's speech synthesis. */
  speak: boolean
}

interface SettingsStore extends Settings {
  update: (patch: Partial<Settings>) => void
}

/**
 * Brings settings saved by an older version of the app up to date.
 *
 * Version 1 had no camera stream to point at, so its saved address is empty unless someone typed
 * one. Version 2 fills that empty address with the vision module's stream, once. An address
 * cleared after that stays cleared.
 */
export function migrateSettings(saved: unknown, version: number): Partial<Settings> {
  const settings: Partial<Settings> = typeof saved === 'object' && saved !== null ? { ...saved } : {}
  if (version < 2 && !settings.cameraUrl) settings.cameraUrl = DEFAULT_CAMERA_URL
  return settings
}

export const useSettings = create<SettingsStore>()(
  persist(
    (set) => ({
      source: 'simulator',
      wsUrl: DEFAULT_WS_URL,
      cameraUrl: DEFAULT_CAMERA_URL,
      speak: false,
      update: (patch) => set(patch),
    }),
    {
      name: 'paul.settings',
      version: 2,
      migrate: (saved, version) => migrateSettings(saved, version) as SettingsStore,
      partialize: ({ source, wsUrl, cameraUrl, speak }) => ({ source, wsUrl, cameraUrl, speak }),
    },
  ),
)
