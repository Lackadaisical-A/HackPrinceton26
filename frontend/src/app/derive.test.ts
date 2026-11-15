import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LIMITS } from '../config'
import { useSession } from '../state/session'
import { bus } from '../telemetry/bus'
import type { VisionMessage } from '../telemetry/contracts'
import { startDerive } from './derive'

/** The tick that turns conditions into alerts. */
const TICK_MS = 100

const vision = (patch: Partial<VisionMessage> = {}): VisionMessage => ({
  t: Date.now() / 1000,
  frame_id: 1,
  status: 'no_target',
  phase: 'carry',
  target: null,
  guidance: null,
  summary: null,
  alert: null,
  ...patch,
})

const dropAlert = () => useSession.getState().alerts.find((alert) => alert.key === 'object-dropped')

describe('the drop alert', () => {
  let stop: () => void

  beforeEach(() => {
    vi.useFakeTimers()
    bus.reset()
    useSession.getState().resetStreams()
    stop = startDerive()
  })

  afterEach(() => {
    stop()
    vi.useRealTimers()
    bus.reset()
    useSession.getState().resetStreams()
  })

  it('is raised by a single message that carries it, even between two ticks', () => {
    bus.publish('vision', vision({ alert: 'dropped' }))
    bus.publish('vision', vision())
    vi.advanceTimersByTime(TICK_MS)
    expect(dropAlert()).toMatchObject({ level: 'warning', where: 'camera', text: 'Object may have dropped', active: true })
    expect(useSession.getState().events[0]).toMatchObject({ kind: 'alert', text: 'Object may have dropped' })
  })

  it('stays on screen for the hold time after vision stops sending it, then clears', () => {
    bus.publish('vision', vision({ alert: 'dropped' }))
    vi.advanceTimersByTime(TICK_MS)
    bus.publish('vision', vision())
    vi.advanceTimersByTime(TICK_MS)
    expect(dropAlert()?.active).toBe(false) // shown as earlier, not as now
    vi.advanceTimersByTime(LIMITS.alertHoldMs)
    expect(dropAlert()).toBeUndefined()
  })

  it('is not raised by messages without it, or by one from before alerts existed', () => {
    const { alert: _, ...older } = vision()
    bus.publish('vision', older as VisionMessage)
    bus.publish('vision', vision({ status: 'degraded' }))
    vi.advanceTimersByTime(TICK_MS * 3)
    expect(dropAlert()).toBeUndefined()
  })
})
