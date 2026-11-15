import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { AIM_DEFAULT, DEFAULT_CAMERA_URL, DEFAULT_WS_URL } from '../config'
import { clampAim, type Aim } from '../lib/aim'
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
  /**
   * Where the gripper's grasp point shows in the camera picture: the crosshair the operator can drag. Vision measures
   * guidance from it (`set_aim`); the console sends it again whenever vision's messages start.
   */
  aim: Aim
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

/** Lays a saved state over the current one. The saved aim is checked, because it is sent to vision as it is. */
export function mergeSettings<T extends Settings>(saved: unknown, current: T): T {
  const fields = typeof saved === 'object' && saved !== null ? (saved as Partial<Settings>) : {}
  return { ...current, ...fields, aim: 'aim' in fields ? clampAim(fields.aim) : current.aim }
}

export const useSettings = create<SettingsStore>()(
  persist(
    (set) => ({
      source: 'simulator',
      wsUrl: DEFAULT_WS_URL,
      cameraUrl: DEFAULT_CAMERA_URL,
      speak: false,
      aim: { ...AIM_DEFAULT },
      update: (patch) => set(patch),
    }),
    {
      name: 'paul.settings',
      version: 2,
      migrate: (saved, version) => migrateSettings(saved, version) as SettingsStore,
      partialize: ({ source, wsUrl, cameraUrl, speak, aim }) => ({ source, wsUrl, cameraUrl, speak, aim }),
      // A save from before the crosshair could move has no aim and keeps the default; a damaged one is replaced.
      merge: (saved, current) => mergeSettings(saved, current),
    },
  ),
)
