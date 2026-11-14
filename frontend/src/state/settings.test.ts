import { describe, expect, it } from 'vitest'
import { DEFAULT_CAMERA_URL } from '../config'
import { migrateSettings } from './settings'

describe('migrateSettings', () => {
  it('fills the empty camera address that version 1 saved', () => {
    const saved = { source: 'websocket', wsUrl: 'ws://127.0.0.1:8765', cameraUrl: '', speak: true }
    expect(migrateSettings(saved, 1)).toEqual({ ...saved, cameraUrl: DEFAULT_CAMERA_URL })
  })

  it('fills the camera address when an older save has none at all', () => {
    expect(migrateSettings({ source: 'simulator' }, 0)).toEqual({ source: 'simulator', cameraUrl: DEFAULT_CAMERA_URL })
  })

  it('keeps a camera address someone typed', () => {
    const saved = { cameraUrl: 'http://192.168.1.20:8080/video' }
    expect(migrateSettings(saved, 1)).toEqual(saved)
  })

  it('does not touch the object it was given', () => {
    const saved = { cameraUrl: '' }
    migrateSettings(saved, 1)
    expect(saved).toEqual({ cameraUrl: '' })
  })

  it('survives a save that is not an object', () => {
    expect(migrateSettings(null, 1)).toEqual({ cameraUrl: DEFAULT_CAMERA_URL })
    expect(migrateSettings('broken', 1)).toEqual({ cameraUrl: DEFAULT_CAMERA_URL })
  })
})
