import { describe, expect, it } from 'vitest'
import type { VisionState, VisionView } from '../state/session'
import type { VisionMessage } from './contracts'
import { classifyVision, hasGuidance, sameView, viewOf, VISION_STATE_TEXT } from './vision'

/** Capture time of the test messages, in Unix seconds. */
const T = 1_731_542_400

const ok = (patch: Partial<VisionMessage> = {}): VisionMessage => ({
  t: T,
  frame_id: 1042,
  status: 'ok',
  phase: 'approach',
  target: { track_id: 7, label: 'cup', source: 'yolo', bbox: [0.41, 0.3, 0.62, 0.58], confidence: 0.91 },
  guidance: { dx: -0.12, dy: 0.05, distance_hint: 'near', aligned: true },
  summary: null,
  alert: null,
  ...patch,
})

const without = (status: 'no_target' | 'low_confidence' | 'degraded', patch: Partial<VisionMessage> = {}): VisionMessage =>
  ok({ status, target: null, guidance: null, ...patch })

/** The watchdog's heartbeat, from a vision build that always reported the search phase. */
const heartbeat = (patch: Partial<VisionMessage> = {}): VisionMessage => without('degraded', { phase: 'search', ...patch })

const previous: VisionView = { state: 'ok', phase: 'carry', label: 'box', distance: 'in_reach', aligned: true }

describe('classifyVision', () => {
  it('is waiting until the first message arrives', () => {
    expect(classifyVision(null, Infinity, T)).toBe('waiting')
    expect(classifyVision(null, 0, T)).toBe('waiting')
  })

  it('passes a fresh status through', () => {
    expect(classifyVision(ok(), 20, T + 0.03)).toBe('ok')
    expect(classifyVision(without('no_target'), 20, T + 0.03)).toBe('no_target')
    expect(classifyVision(without('low_confidence'), 20, T + 0.03)).toBe('low_confidence')
    expect(classifyVision(heartbeat(), 20, T + 0.03)).toBe('degraded')
  })

  it('is silent once nothing has arrived for more than half a second', () => {
    expect(classifyVision(ok(), 500, T + 0.1)).toBe('ok')
    expect(classifyVision(ok(), 501, T + 0.1)).toBe('silent')
    expect(classifyVision(without('no_target'), 501, T + 0.1)).toBe('silent')
    expect(classifyVision(without('low_confidence'), 2000, T + 0.1)).toBe('silent')
  })

  it('allows for the half-second gap between heartbeats while degraded', () => {
    expect(classifyVision(heartbeat(), 501, T + 0.6)).toBe('degraded')
    expect(classifyVision(heartbeat(), 1250, T + 1.3)).toBe('degraded')
    expect(classifyVision(heartbeat(), 1251, T + 1.3)).toBe('silent')
  })

  it('is late when the capture time is more than half a second old', () => {
    expect(classifyVision(ok(), 20, T + 0.5)).toBe('ok')
    expect(classifyVision(ok(), 20, T + 0.51)).toBe('late')
    expect(classifyVision(without('no_target'), 20, T + 3)).toBe('late')
    expect(classifyVision(without('low_confidence'), 20, T + 3)).toBe('late')
  })

  it('does not call a heartbeat late: the feed is already known to be lost', () => {
    expect(classifyVision(heartbeat(), 20, T + 3)).toBe('degraded')
  })

  it('reports silence ahead of lateness', () => {
    expect(classifyVision(ok(), 800, T + 3)).toBe('silent')
  })

  it('accepts a capture time slightly ahead of this clock', () => {
    expect(classifyVision(ok(), 20, T - 0.2)).toBe('ok')
  })
})

describe('hasGuidance', () => {
  it('is true only for a locked target', () => {
    const states: VisionState[] = ['ok', 'no_target', 'low_confidence', 'degraded', 'waiting', 'silent', 'late']
    expect(states.filter(hasGuidance)).toEqual(['ok'])
  })
})

describe('viewOf', () => {
  it('shows the target, distance and alignment while locked', () => {
    expect(viewOf(ok(), 'ok', previous)).toEqual({
      state: 'ok',
      phase: 'approach',
      label: 'cup',
      distance: 'near',
      aligned: true,
    })
  })

  it('hides them for every state other than ok, even if the message still carries them', () => {
    // A message that says "ok" but arrived late, or was followed by silence, is not guidance.
    for (const state of ['late', 'silent'] as const) {
      expect(viewOf(ok(), state, previous)).toEqual({
        state,
        phase: 'approach',
        label: null,
        distance: null,
        aligned: false,
      })
    }
  })

  it('shows no target details when the message has none', () => {
    for (const status of ['no_target', 'low_confidence'] as const) {
      expect(viewOf(without(status, { phase: 'search' }), status, previous)).toEqual({
        state: status,
        phase: 'search',
        label: null,
        distance: null,
        aligned: false,
      })
    }
  })

  it('takes the phase from the message when it knows it', () => {
    expect(viewOf(ok({ phase: 'grasp' }), 'ok', previous).phase).toBe('grasp')
    expect(viewOf(without('no_target', { phase: 'release' }), 'no_target', previous).phase).toBe('release')
    expect(viewOf(ok({ phase: 'place' }), 'late', previous).phase).toBe('place')
  })

  it('keeps the previous phase during a degraded heartbeat, even one that says search', () => {
    expect(viewOf(heartbeat(), 'degraded', previous)).toEqual({
      state: 'degraded',
      phase: 'carry',
      label: null,
      distance: null,
      aligned: false,
    })
    // Still degraded underneath once the heartbeats themselves stop.
    expect(viewOf(heartbeat(), 'silent', previous).phase).toBe('carry')
  })

  it('keeps the previous phase before any message has arrived', () => {
    expect(viewOf(null, 'waiting', previous)).toEqual({
      state: 'waiting',
      phase: 'carry',
      label: null,
      distance: null,
      aligned: false,
    })
  })

  it('never shows details without a message, whatever the state says', () => {
    expect(viewOf(null, 'ok', previous)).toMatchObject({ label: null, distance: null, aligned: false })
  })
})

describe('sameView', () => {
  it('is true for equal views', () => {
    expect(sameView(previous, { ...previous })).toBe(true)
  })

  it('is false when any field differs', () => {
    const changes: Partial<VisionView>[] = [
      { state: 'late' },
      { phase: 'place' },
      { label: 'cup' },
      { label: null },
      { distance: 'near' },
      { aligned: false },
    ]
    for (const change of changes) expect(sameView(previous, { ...previous, ...change }), JSON.stringify(change)).toBe(false)
  })
})

describe('VISION_STATE_TEXT', () => {
  it('has a line for every state', () => {
    const states: VisionState[] = ['ok', 'no_target', 'low_confidence', 'degraded', 'waiting', 'silent', 'late']
    for (const state of states) expect(VISION_STATE_TEXT[state].length).toBeGreaterThan(0)
    expect(VISION_STATE_TEXT.degraded).toBe('Vision feed lost')
  })
})
