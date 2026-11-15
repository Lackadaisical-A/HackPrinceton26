import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AIM_DEFAULT } from '../config'
import { useSession } from '../state/session'
import { useSettings } from '../state/settings'
import { bus } from '../telemetry/bus'
import type { Command, VisionMessage } from '../telemetry/contracts'
import { Simulator } from '../telemetry/sources/simulator'
import { connect, disconnect, resetAim, setAim, startAimSync } from './runtime'

/** Every command the live link was asked to send, and whether it is "connected". */
const link = vi.hoisted(() => ({ sent: [] as unknown[], open: true }))

vi.mock('../telemetry/sources/websocket', () => ({
  WebSocketSource: class {
    start(): void {}
    stop(): void {}
    send(command: unknown): boolean {
      if (link.open) link.sent.push(command)
      return link.open
    }
  },
}))

const vision = (): VisionMessage => ({
  t: Date.now() / 1000,
  frame_id: 1,
  status: 'no_target',
  phase: 'search',
  target: null,
  guidance: null,
  summary: null,
  alert: null,
})

const aimCommands = () => link.sent.filter((command) => (command as Command).type === 'set_aim')
const lastLog = () => useSession.getState().events[0]

beforeEach(() => {
  link.sent = []
  link.open = true
  useSettings.getState().update({ source: 'websocket', aim: { ...AIM_DEFAULT } })
})

afterEach(() => {
  disconnect()
  vi.restoreAllMocks()
})

describe('setAim', () => {
  it('saves the point and sends it to vision', () => {
    connect()
    setAim(0.62, 0.58)
    expect(useSettings.getState().aim).toEqual({ x: 0.62, y: 0.58 })
    expect(aimCommands()).toEqual([{ type: 'set_aim', x: 0.62, y: 0.58 }])
    expect(lastLog().text).toBe('Crosshair moved to x 62 %, y 58 %')
  })

  it('keeps the point inside the picture', () => {
    connect()
    setAim(1.2, -0.1)
    expect(useSettings.getState().aim).toEqual({ x: 0.95, y: 0.05 })
  })

  it('sends nothing when the point did not move', () => {
    connect()
    setAim(0.5, 0.5)
    expect(aimCommands()).toEqual([])
  })

  it('still saves the point when nothing is connected, and says it goes out later', () => {
    connect()
    link.open = false
    setAim(0.3, 0.4)
    expect(useSettings.getState().aim).toEqual({ x: 0.3, y: 0.4 })
    expect(lastLog()).toMatchObject({ text: expect.stringContaining('sent to vision once connected'), level: 'caution' })
  })

  it('resetAim puts it back in the centre', () => {
    connect()
    setAim(0.3, 0.4)
    resetAim()
    expect(useSettings.getState().aim).toEqual(AIM_DEFAULT)
    expect(aimCommands().at(-1)).toEqual({ type: 'set_aim', ...AIM_DEFAULT })
  })
})

describe('startAimSync', () => {
  let stop: () => void

  beforeEach(() => {
    stop = startAimSync()
  })

  afterEach(() => stop())

  it('sends the saved point with the first vision message after connecting, and only then', () => {
    useSettings.getState().update({ aim: { x: 0.6, y: 0.7 } })
    connect()
    bus.publish('vision', vision(), 1000)
    bus.publish('vision', vision(), 1030)
    bus.publish('vision', vision(), 2900)
    expect(aimCommands()).toEqual([{ type: 'set_aim', x: 0.6, y: 0.7 }])
  })

  it('sends the default too, in case vision still holds a point from an earlier session', () => {
    connect()
    bus.publish('vision', vision(), 1000)
    expect(aimCommands()).toEqual([{ type: 'set_aim', ...AIM_DEFAULT }])
  })

  it('sends it again when vision comes back after a gap, as it may have restarted', () => {
    connect()
    bus.publish('vision', vision(), 1000)
    bus.publish('vision', vision(), 3500)
    expect(aimCommands()).toHaveLength(2)
  })

  it('sends it again after reconnecting', () => {
    connect()
    bus.publish('vision', vision(), 1000)
    connect()
    bus.publish('vision', vision(), 1100)
    expect(aimCommands()).toHaveLength(2)
  })

  it('leaves the simulator alone: it is given the point when it starts', () => {
    const send = vi.spyOn(Simulator.prototype, 'send')
    vi.spyOn(Simulator.prototype, 'start').mockImplementation(() => {})
    useSettings.getState().update({ source: 'simulator', aim: { x: 0.6, y: 0.7 } })
    connect()
    expect(send).toHaveBeenCalledWith({ type: 'set_aim', x: 0.6, y: 0.7 })
    send.mockClear()
    bus.publish('vision', vision(), 1000)
    bus.publish('vision', vision(), 9000)
    expect(send).not.toHaveBeenCalled()
  })
})
