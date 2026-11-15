import { describe, expect, it } from 'vitest'
import { AIM_DEFAULT, DEFAULT_CAMERA_URL } from '../config'
import { mergeSettings, migrateSettings, useSettings } from './settings'

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

describe('mergeSettings', () => {
  const current = useSettings.getState()

  it('keeps the default crosshair when the save has none', () => {
    const merged = mergeSettings({ speak: false }, current)
    expect(merged.aim).toEqual(AIM_DEFAULT)
    expect(merged.speak).toBe(false)
  })

  it('restores a saved crosshair', () => {
    expect(mergeSettings({ aim: { x: 0.62, y: 0.58 } }, current).aim).toEqual({ x: 0.62, y: 0.58 })
  })

  it('pulls a crosshair saved off the picture back inside it', () => {
    expect(mergeSettings({ aim: { x: 1.4, y: -0.2 } }, current).aim).toEqual({ x: 0.95, y: 0.05 })
  })

  it('falls back to the default for a damaged crosshair', () => {
    for (const aim of [null, 'centre', { x: 0.5 }, { x: Number.NaN, y: 0.5 }, { x: '0.5', y: 0.5 }]) {
      expect(mergeSettings({ aim }, current).aim).toEqual(AIM_DEFAULT)
    }
  })

  it('survives a save that is not an object', () => {
    expect(mergeSettings(null, current)).toEqual(current)
  })
})
