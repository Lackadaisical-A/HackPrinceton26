import { afterEach, describe, expect, it, vi } from 'vitest'
import { TelemetryBus } from '../../telemetry/bus'
import type { ArmGeometry, ArmSettings, CalibrationMessage } from '../../telemetry/contracts'
import fixture from '../../telemetry/fixtures/calibration_full.json'
import { Recorder } from '../../telemetry/recorder'
import { draftFrom, manualAxes, parseDraft, sameSettings } from './armSettings'
import { armParts, DEFAULT_VIEW, makeProjection, orbit, zoomed } from './humanArm'

const status = (): CalibrationMessage => structuredClone(fixture) as unknown as CalibrationMessage
const settings = (): ArmSettings => status().arm_settings!.values

describe('arm settings form', () => {
  it('round-trips the controller values without a change', () => {
    const values = settings()
    const parsed = parseDraft(draftFrom(values))
    expect(parsed.error).toBeNull()
    expect(sameSettings(parsed.settings!, values)).toBe(true)
  })
  it('detects an edit and refuses out-of-range or empty values with the reason', () => {
    const draft = draftFrom(settings())
    const edited = parseDraft({ ...draft, upper_cm: '32' })
    expect(sameSettings(edited.settings!, settings())).toBe(false)
    expect(parseDraft({ ...draft, hand_cm: '4' }).error).toMatch(/5-60 cm/)
    expect(parseDraft({ ...draft, bicep_from_elbow_pct: '' }).error).toMatch(/0-100%/)
    expect(parseDraft({ ...draft, forearm_cm: 'abc' }).settings).toBeNull()
  })
  it('notices when every axis is set by hand', () => {
    expect(manualAxes({ axes: { upper: '+X', forearm: '-Y', hand: '+Z' } })).toBe(true)
    expect(manualAxes({ axes: { upper: '+X', forearm: 'Auto', hand: '+Z' } })).toBe(false)
  })
})

describe('the 3-IMU arm drawing', () => {
  const geometry = (): ArmGeometry => status().geometry!
  it('projects like Wearable Lab: the view centre sits at the screen centre', () => {
    const project = makeProjection(DEFAULT_VIEW, 400, 300)
    const [x, y] = project([0.25, 0, -0.12])
    expect(x).toBeCloseTo(200)
    expect(y).toBeCloseTo(150)
  })
  it('keeps tilt and zoom within the desktop limits', () => {
    expect(orbit(DEFAULT_VIEW, 0, 1000).elevation).toBe(80)
    expect(orbit(DEFAULT_VIEW, 0, -1000).elevation).toBe(-70)
    let view = DEFAULT_VIEW
    for (let i = 0; i < 40; i++) view = zoomed(view, -1)
    expect(view.zoom).toBe(2.5)
  })
  it('draws three bones and three sensors, back to front', () => {
    const parts = armParts(geometry(), makeProjection(DEFAULT_VIEW, 400, 300))
    expect(parts.filter((p) => p.kind === 'bone')).toHaveLength(3)
    expect(parts.filter((p) => p.kind === 'sensor')).toHaveLength(3)
    expect(parts.map((p) => p.depth)).toEqual([...parts.map((p) => p.depth)].sort((a, b) => a - b))
  })
})

describe('recording Wearable Lab status', () => {
  afterEach(() => vi.useRealTimers())
  it('keeps at most one calibration frame a second, and every other topic', () => {
    vi.useFakeTimers()
    const bus = new TelemetryBus()
    const recorder = new Recorder(bus)
    recorder.start({ source: 'websocket' }, () => {})
    for (let i = 0; i < 20; i++) {
      bus.publish('calibration', status())
      bus.publish('haptics', { t: 1, enabled: false, motors: [0, 0, 0, 0], cue: null })
      vi.advanceTimersByTime(100)
    }
    expect(recorder.count).toBe(20 + 2)
    recorder.stop()
  })
})
