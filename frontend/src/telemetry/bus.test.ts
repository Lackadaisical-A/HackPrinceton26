import { describe, expect, it } from 'vitest'
import { TelemetryBus } from './bus'
import type { HapticsMessage, ImuMessage, PressureMessage, Topic } from './contracts'
import { TOPICS } from './contracts'

const imu = (t = 0): ImuMessage => ({ t, q: [1, 0, 0, 0] })
const pressure = (norm: number[]): PressureMessage => ({ t: 0, raw: [0, 0, 0, 0], norm })
const haptics = (motors: number[], enabled = true): HapticsMessage => ({ t: 0, enabled, motors, cue: null })

/** Publishes `count` messages evenly over the interval that ends at `untilMs`. */
function stream(bus: TelemetryBus, count: number, fromMs: number, untilMs: number): void {
  for (let i = 1; i <= count; i++) bus.publish('imu', imu(), fromMs + ((untilMs - fromMs) * i) / count)
}

describe('TelemetryBus', () => {
  it('starts with nothing on any topic', () => {
    const bus = new TelemetryBus()
    for (const topic of TOPICS) {
      expect(bus.latest(topic)).toBeNull()
      expect(bus.state(topic)).toEqual({ latest: null, receivedMs: 0, count: 0, hz: 0, rejected: 0, lastError: null })
      expect(bus.ageMs(topic, 5000)).toBe(Infinity)
    }
  })

  describe('publish', () => {
    it('keeps the newest message and counts every one', () => {
      const bus = new TelemetryBus()
      const first = imu(1)
      const second = imu(2)
      bus.publish('imu', first, 1000)
      expect(bus.latest('imu')).toBe(first)
      bus.publish('imu', second, 1020)
      expect(bus.latest('imu')).toBe(second)
      expect(bus.state('imu').count).toBe(2)
      expect(bus.state('imu').receivedMs).toBe(1020)
    })

    it('keeps topics apart', () => {
      const bus = new TelemetryBus()
      bus.publish('imu', imu(), 1000)
      expect(bus.state('imu').count).toBe(1)
      for (const topic of TOPICS.filter((t) => t !== 'imu')) {
        expect(bus.state(topic).count).toBe(0)
        expect(bus.latest(topic)).toBeNull()
      }
    })
  })

  describe('ageMs', () => {
    it('is infinite until a message arrives, so nothing can look fresh by accident', () => {
      const bus = new TelemetryBus()
      expect(bus.ageMs('vision', 0)).toBe(Infinity)
      expect(bus.ageMs('vision', 123_456)).toBe(Infinity)
    })

    it('is the time since the newest message arrived', () => {
      const bus = new TelemetryBus()
      bus.publish('imu', imu(), 1000)
      expect(bus.ageMs('imu', 1000)).toBe(0)
      expect(bus.ageMs('imu', 1180)).toBe(180)
      bus.publish('imu', imu(), 1500)
      expect(bus.ageMs('imu', 1520)).toBe(20)
      expect(bus.ageMs('pressure', 1520)).toBe(Infinity)
    })
  })

  describe('listeners', () => {
    it('calls a topic listener with the message and its arrival time', () => {
      const bus = new TelemetryBus()
      const seen: [ImuMessage, number][] = []
      bus.on('imu', (message, receivedMs) => seen.push([message, receivedMs]))
      const message = imu(7)
      bus.publish('imu', message, 1234)
      expect(seen).toEqual([[message, 1234]])
    })

    it('does not call a listener for another topic', () => {
      const bus = new TelemetryBus()
      let calls = 0
      bus.on('pressure', () => calls++)
      bus.publish('imu', imu(), 1000)
      expect(calls).toBe(0)
    })

    it('has already stored the message when the listener runs', () => {
      const bus = new TelemetryBus()
      let latestInside: ImuMessage | null = null
      let countInside = -1
      bus.on('imu', () => {
        latestInside = bus.latest('imu')
        countInside = bus.state('imu').count
      })
      const message = imu()
      bus.publish('imu', message, 1000)
      expect(latestInside).toBe(message)
      expect(countInside).toBe(1)
    })

    it('stops calling a listener once it unsubscribes, and leaves the others', () => {
      const bus = new TelemetryBus()
      let first = 0
      let second = 0
      const stop = bus.on('imu', () => first++)
      bus.on('imu', () => second++)
      bus.publish('imu', imu(), 1000)
      stop()
      bus.publish('imu', imu(), 1020)
      expect(first).toBe(1)
      expect(second).toBe(2)
      stop() // a second call does nothing
      bus.publish('imu', imu(), 1040)
      expect(second).toBe(3)
    })

    it('calls an any-topic listener with the topic, and stops when it unsubscribes', () => {
      const bus = new TelemetryBus()
      const seen: [Topic, number][] = []
      const stop = bus.onAny((topic, _message, receivedMs) => seen.push([topic, receivedMs]))
      bus.publish('imu', imu(), 1000)
      bus.publish('pressure', pressure([0.1, 0.1, 0.1, 0.1]), 1010)
      stop()
      bus.publish('imu', imu(), 1020)
      expect(seen).toEqual([
        ['imu', 1000],
        ['pressure', 1010],
      ])
    })
  })

  describe('reject', () => {
    it('counts the frame and keeps the latest reason, without touching accepted data', () => {
      const bus = new TelemetryBus()
      const message = imu()
      bus.publish('imu', message, 1000)
      bus.reject('imu', 'q is not a unit quaternion')
      bus.reject('imu', 't must be a number')
      const state = bus.state('imu')
      expect(state.rejected).toBe(2)
      expect(state.lastError).toBe('t must be a number')
      expect(state.count).toBe(1)
      expect(state.latest).toBe(message)
      expect(bus.state('vision').rejected).toBe(0)
    })

    it('does not make a stream look alive', () => {
      const bus = new TelemetryBus()
      bus.reject('vision', 'not an object')
      expect(bus.ageMs('vision', 1000)).toBe(Infinity)
      expect(bus.latest('vision')).toBeNull()
    })
  })

  describe('sampleRates', () => {
    it('only sets a baseline on the first call', () => {
      const bus = new TelemetryBus()
      stream(bus, 30, 0, 900)
      bus.sampleRates(1000)
      expect(bus.state('imu').hz).toBe(0)
    })

    it('measures the rate over a known interval', () => {
      const bus = new TelemetryBus()
      bus.sampleRates(1000)
      stream(bus, 50, 1000, 2000)
      bus.sampleRates(2000)
      expect(bus.state('imu').hz).toBe(50)
      expect(bus.state('pressure').hz).toBe(0)
    })

    it('does not count messages from before the baseline', () => {
      const bus = new TelemetryBus()
      stream(bus, 400, 0, 900)
      bus.sampleRates(1000)
      stream(bus, 25, 1000, 1500)
      bus.sampleRates(1500)
      expect(bus.state('imu').hz).toBe(50)
    })

    it('waits for at least 200 ms of data before measuring', () => {
      const bus = new TelemetryBus()
      bus.sampleRates(1000)
      stream(bus, 5, 1000, 1100)
      bus.sampleRates(1100)
      expect(bus.state('imu').hz).toBe(0)
      // The short interval was not thrown away: it is part of the next measurement.
      bus.sampleRates(1500)
      expect(bus.state('imu').hz).toBe(10)
    })

    it('smooths a changing rate', () => {
      const bus = new TelemetryBus()
      bus.sampleRates(1000)
      stream(bus, 50, 1000, 2000)
      bus.sampleRates(2000)
      stream(bus, 100, 2000, 3000)
      bus.sampleRates(3000)
      expect(bus.state('imu').hz).toBeCloseTo(50 * 0.6 + 100 * 0.4, 9)
    })

    it('drops to zero at once when a stream stops', () => {
      const bus = new TelemetryBus()
      bus.sampleRates(1000)
      stream(bus, 50, 1000, 2000)
      bus.sampleRates(2000)
      expect(bus.state('imu').hz).toBe(50)
      bus.sampleRates(2250)
      expect(bus.state('imu').hz).toBe(0)
    })

    it('shows the true rate at once when a stopped stream resumes', () => {
      const bus = new TelemetryBus()
      bus.sampleRates(1000)
      stream(bus, 50, 1000, 2000)
      bus.sampleRates(2000)
      bus.sampleRates(3000)
      stream(bus, 30, 3000, 4000)
      bus.sampleRates(4000)
      expect(bus.state('imu').hz).toBe(30)
    })
  })

  describe('grip trace', () => {
    it('records the time in seconds with the highest and lowest pad', () => {
      const bus = new TelemetryBus()
      bus.publish('pressure', pressure([0.25, 0.75, 0.5, 0.375]), 2000)
      expect(bus.gripTrace.length).toBe(1)
      expect(bus.gripTrace.last(0)).toBe(2)
      expect(bus.gripTrace.last(1)).toBe(0.75)
      expect(bus.gripTrace.last(2)).toBe(0.25)
    })

    it('adds a row for each pressure message, and none for other topics', () => {
      const bus = new TelemetryBus()
      bus.publish('pressure', pressure([0.5, 0.5, 0.5, 0.5]), 1000)
      bus.publish('imu', imu(), 1010)
      bus.publish('pressure', pressure([0.25, 0.5, 0.5, 0.5]), 1020)
      expect(bus.gripTrace.length).toBe(2)
      expect(bus.gripTrace.at(0, 0)).toBe(1)
      expect(bus.gripTrace.at(1, 0)).toBe(1.02)
      expect(bus.hapticTrace.length).toBe(0)
    })

    it('keeps the highest value seen on each pad', () => {
      const bus = new TelemetryBus()
      bus.publish('pressure', pressure([0.25, 0.75, 0.5, 0.375]), 1000)
      bus.publish('pressure', pressure([0.625, 0.125, 0.5, 0.875]), 1020)
      expect([...bus.padPeak]).toEqual([0.625, 0.75, 0.5, 0.875])
    })

    it('forgets the pad peaks on request, and nothing else', () => {
      const bus = new TelemetryBus()
      bus.publish('pressure', pressure([0.25, 0.75, 0.5, 0.375]), 1000)
      bus.resetPadPeaks()
      expect([...bus.padPeak]).toEqual([0, 0, 0, 0])
      expect(bus.gripTrace.length).toBe(1)
      expect(bus.state('pressure').count).toBe(1)
    })
  })

  describe('haptic trace', () => {
    it('records the strongest commanded motor', () => {
      const bus = new TelemetryBus()
      bus.publish('haptics', haptics([0, 0.5, 0.25, 0]), 3000)
      expect(bus.hapticTrace.length).toBe(1)
      expect(bus.hapticTrace.last(0)).toBe(3)
      expect(bus.hapticTrace.last(1)).toBe(0.5)
      expect(bus.gripTrace.length).toBe(0)
    })

    it('records zero while haptics are off, whatever the motors say', () => {
      const bus = new TelemetryBus()
      bus.publish('haptics', haptics([0, 0.5, 0.25, 0], false), 3000)
      expect(bus.hapticTrace.last(1, -1)).toBe(0)
    })
  })

  describe('reset', () => {
    it('clears every sample, count, rate and rejection', () => {
      const bus = new TelemetryBus()
      bus.sampleRates(1000)
      stream(bus, 50, 1000, 2000)
      bus.publish('pressure', pressure([0.25, 0.75, 0.5, 0.375]), 2000)
      bus.publish('haptics', haptics([0, 0.5, 0, 0]), 2000)
      bus.reject('vision', 'not an object')
      bus.sampleRates(2000)

      bus.reset()

      for (const topic of TOPICS) {
        expect(bus.state(topic)).toEqual({ latest: null, receivedMs: 0, count: 0, hz: 0, rejected: 0, lastError: null })
        expect(bus.ageMs(topic, 3000)).toBe(Infinity)
      }
      expect(bus.gripTrace.length).toBe(0)
      expect(bus.hapticTrace.length).toBe(0)
      expect([...bus.padPeak]).toEqual([0, 0, 0, 0])
    })

    it('starts rate measurement again from a new baseline', () => {
      const bus = new TelemetryBus()
      bus.sampleRates(1000)
      stream(bus, 50, 1000, 2000)
      bus.sampleRates(2000)
      bus.reset()

      stream(bus, 10, 5000, 5900)
      bus.sampleRates(6000) // baseline only
      expect(bus.state('imu').hz).toBe(0)
      stream(bus, 30, 6000, 7000)
      bus.sampleRates(7000)
      expect(bus.state('imu').hz).toBe(30)
    })

    it('keeps listeners subscribed', () => {
      const bus = new TelemetryBus()
      let calls = 0
      bus.on('imu', () => calls++)
      bus.reset()
      bus.publish('imu', imu(), 1000)
      expect(calls).toBe(1)
    })
  })
})
