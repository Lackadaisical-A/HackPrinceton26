import { describe, expect, it } from 'vitest'
import type { Topic } from './contracts'
import {
  COMMAND_EXAMPLES,
  COMMAND_TOPIC,
  encodeFrame,
  isTopic,
  parseFrame,
  TOPIC_INFO,
  TOPICS,
  VALIDATORS,
  validateAssist,
  validateHaptics,
  validateImu,
  validatePressure,
  validateRobot,
  validateVision,
} from './contracts'

/** The payload of a topic's documented example, as a fresh object each call. */
function example(topic: Topic): Record<string, unknown> {
  const parsed = parseFrame(TOPIC_INFO[topic].example)
  if (!parsed.ok) throw new Error(`${topic} example does not parse: ${parsed.error}`)
  return parsed.payload as Record<string, unknown>
}

describe('documented examples', () => {
  it.each(TOPICS)('the %s example is a valid frame on its own topic', (topic) => {
    const parsed = parseFrame(TOPIC_INFO[topic].example)
    expect(parsed.ok).toBe(true)
    expect(parsed.topic).toBe(topic)
    expect(VALIDATORS[topic](example(topic))).toBeNull()
  })

  it.each(TOPICS)('the %s example is already compact JSON', (topic) => {
    expect(encodeFrame(topic, example(topic))).toBe(TOPIC_INFO[topic].example)
  })

  it('every command example is a frame on the command topic with a type', () => {
    expect(COMMAND_EXAMPLES.length).toBeGreaterThan(0)
    for (const { frame } of COMMAND_EXAMPLES) {
      const parsed = parseFrame(frame)
      expect(parsed.ok, frame).toBe(true)
      expect(parsed.topic).toBe(COMMAND_TOPIC)
      if (parsed.ok) expect(typeof (parsed.payload as { type?: unknown }).type).toBe('string')
    }
  })
})

describe('every validator', () => {
  it.each(TOPICS)('%s rejects a payload that is not an object', (topic) => {
    for (const payload of [null, undefined, 'text', 42, [1, 2, 3]]) {
      expect(VALIDATORS[topic](payload)).toBe('not an object')
    }
  })

  it.each(TOPICS)('%s rejects a missing or non-finite time', (topic) => {
    for (const t of [undefined, '1731542400', Number.NaN, Infinity]) {
      expect(VALIDATORS[topic]({ ...example(topic), t })).toBe('t must be a number')
    }
  })
})

describe('validateVision', () => {
  const noTarget = (): Record<string, unknown> => ({ ...example('vision'), status: 'no_target', target: null, guidance: null })
  const withTarget = (patch: Record<string, unknown>): Record<string, unknown> => {
    const message = example('vision')
    return { ...message, target: { ...(message.target as object), ...patch } }
  }
  const withGuidance = (patch: Record<string, unknown>): Record<string, unknown> => {
    const message = example('vision')
    return { ...message, guidance: { ...(message.guidance as object), ...patch } }
  }

  it('accepts each status other than ok with no target and no guidance', () => {
    for (const status of ['no_target', 'low_confidence', 'degraded']) {
      expect(validateVision({ ...noTarget(), status })).toBeNull()
    }
  })

  it('rejects an unknown status', () => {
    expect(validateVision({ ...example('vision'), status: 'tracking' })).toBe('unknown status "tracking"')
    expect(validateVision({ ...example('vision'), status: undefined })).toBe('unknown status undefined')
  })

  it('rejects an unknown phase', () => {
    expect(validateVision({ ...example('vision'), phase: 'lift' })).toBe('unknown phase "lift"')
  })

  it('rejects a target or guidance when the status is not ok', () => {
    const reason = 'target and guidance must be null unless status is ok'
    expect(validateVision({ ...example('vision'), status: 'no_target' })).toBe(reason)
    expect(validateVision({ ...noTarget(), target: example('vision').target })).toBe(reason)
    expect(validateVision({ ...noTarget(), status: 'degraded', guidance: example('vision').guidance })).toBe(reason)
  })

  it('requires a target and guidance when the status is ok', () => {
    expect(validateVision({ ...example('vision'), target: null })).toBe('target is required when status is ok')
    expect(validateVision({ ...example('vision'), guidance: null })).toBe('guidance is required when status is ok')
  })

  it('rejects a bounding box with its corners out of order', () => {
    const reason = 'target.bbox corners are out of order'
    expect(validateVision(withTarget({ bbox: [0.62, 0.3, 0.41, 0.58] }))).toBe(reason)
    expect(validateVision(withTarget({ bbox: [0.41, 0.58, 0.62, 0.3] }))).toBe(reason)
    expect(validateVision(withTarget({ bbox: [0.41, 0.3, 0.41, 0.58] }))).toBe(reason)
  })

  it('rejects a bounding box that is not four numbers from 0 to 1', () => {
    const reason = 'target.bbox must be four numbers from 0 to 1'
    expect(validateVision(withTarget({ bbox: [0.41, 0.3, 1.2, 0.58] }))).toBe(reason)
    expect(validateVision(withTarget({ bbox: [-0.1, 0.3, 0.62, 0.58] }))).toBe(reason)
    expect(validateVision(withTarget({ bbox: [0.41, 0.3, 0.62] }))).toBe(reason)
    expect(validateVision(withTarget({ bbox: [0.41, 0.3, 0.62, '0.58'] }))).toBe(reason)
  })

  it('rejects a confidence or an offset outside its range', () => {
    expect(validateVision(withTarget({ confidence: 1.4 }))).toBe('target.confidence must be from 0 to 1')
    expect(validateVision(withGuidance({ dx: -1.5 }))).toBe('guidance.dx and dy must be from -1 to 1')
    expect(validateVision(withGuidance({ dy: 2 }))).toBe('guidance.dx and dy must be from -1 to 1')
  })

  it('rejects an unknown distance hint and a non-boolean aligned flag', () => {
    expect(validateVision(withGuidance({ distance_hint: 'close' }))).toBe('unknown distance_hint "close"')
    expect(validateVision(withGuidance({ aligned: 1 }))).toBe('guidance.aligned must be true or false')
  })

  it('accepts a null summary and rejects one that is not text', () => {
    expect(validateVision({ ...example('vision'), summary: null })).toBeNull()
    expect(validateVision({ ...example('vision'), summary: 7 })).toBe('summary must be a string or null')
  })

  it('accepts a drop alert, no alert, and a message from before alerts existed', () => {
    expect(validateVision({ ...example('vision'), alert: 'dropped' })).toBeNull()
    expect(validateVision({ ...noTarget(), alert: 'dropped' })).toBeNull() // independent of the status
    expect(validateVision({ ...example('vision'), alert: null })).toBeNull()
    const { alert: _, ...older } = example('vision')
    expect(validateVision(older)).toBeNull()
  })

  it('rejects an alert it does not know', () => {
    expect(validateVision({ ...example('vision'), alert: 'fell' })).toBe('unknown alert "fell"')
    expect(validateVision({ ...example('vision'), alert: true })).toBe('unknown alert true')
  })

  it('rejects a frame id that is not a whole number from zero up', () => {
    const reason = 'frame_id must be a non-negative integer'
    expect(validateVision({ ...example('vision'), frame_id: 1.5 })).toBe(reason)
    expect(validateVision({ ...example('vision'), frame_id: -1 })).toBe(reason)
  })
})

describe('validatePressure', () => {
  it('rejects the wrong number of pads', () => {
    expect(validatePressure({ ...example('pressure'), norm: [0.5, 0.4, 0.5] })).toBe('norm must be four numbers from 0 to 1')
    expect(validatePressure({ ...example('pressure'), norm: [0.5, 0.4, 0.5, 0.3, 0.2] })).toBe(
      'norm must be four numbers from 0 to 1',
    )
    expect(validatePressure({ ...example('pressure'), raw: [1840, 1322, 1795] })).toBe('raw must be four numbers')
  })

  it('rejects a relative pressure outside 0 to 1', () => {
    expect(validatePressure({ ...example('pressure'), norm: [0.5, 1.2, 0.5, 0.3] })).toBe(
      'norm must be four numbers from 0 to 1',
    )
    expect(validatePressure({ ...example('pressure'), norm: [0.5, -0.1, 0.5, 0.3] })).toBe(
      'norm must be four numbers from 0 to 1',
    )
  })

  it('accepts the ends of the range', () => {
    expect(validatePressure({ ...example('pressure'), norm: [0, 1, 0, 1] })).toBeNull()
  })
})

describe('validateImu', () => {
  it('rejects a quaternion that is not unit length', () => {
    expect(validateImu({ ...example('imu'), q: [0.5, 0, 0, 0] })).toBe('q is not a unit quaternion')
    expect(validateImu({ ...example('imu'), q: [0, 0, 0, 0] })).toBe('q is not a unit quaternion')
    expect(validateImu({ ...example('imu'), q: [1, 1, 0, 0] })).toBe('q is not a unit quaternion')
  })

  it('rejects a quaternion that is not four numbers from -1 to 1', () => {
    expect(validateImu({ ...example('imu'), q: [1, 0, 0] })).toBe('q must be four numbers from -1 to 1')
    expect(validateImu({ ...example('imu'), q: [2, 0, 0, 0] })).toBe('q must be four numbers from -1 to 1')
    expect(validateImu({ ...example('imu'), q: 'identity' })).toBe('q must be four numbers from -1 to 1')
  })

  it('accepts the identity and tolerates rounding', () => {
    expect(validateImu({ ...example('imu'), q: [1, 0, 0, 0] })).toBeNull()
    expect(validateImu({ ...example('imu'), q: [0.7071, 0.7071, 0, 0] })).toBeNull()
  })
})

describe('validateRobot', () => {
  const control = { input: 'potentiometer', feedback: 'preview', enabled: false, input_fresh: true, target: [0, 0, 0, 0, 0, 50] }
  it('accepts a dry-run preview without fabricated servo loads', () => {
    expect(validateRobot({ ...example('robot'), load: null, control })).toBeNull()
    expect(validateRobot({ ...example('robot'), control })).toBe('preview must not report measured load')
  })
  it('validates optional controller and slider feedback', () => {
    expect(validateRobot({ ...example('robot'), load: null, control, slider: { pos: 20, target: 40 } })).toBeNull()
    expect(validateRobot({ ...example('robot'), control: { ...control, enabled: 'yes' } })).toBe('invalid robot control state')
    expect(validateRobot({ ...example('robot'), control: { ...control, target: [1] } })).toBe('invalid robot control state')
    expect(validateRobot({ ...example('robot'), slider: { pos: NaN, target: null } })).toBe('invalid slider state')
  })
  it('rejects a load outside -1 to 1', () => {
    expect(validateRobot({ ...example('robot'), load: [0.04, 1.4, 0.22, 0.08, 0.01, 0.12] })).toBe(
      'load must be six numbers from -1 to 1',
    )
    expect(validateRobot({ ...example('robot'), load: [0.04, -1.01, 0.22, 0.08, 0.01, 0.12] })).toBe(
      'load must be six numbers from -1 to 1',
    )
  })

  it('rejects the wrong number of joints', () => {
    expect(validateRobot({ ...example('robot'), pos: [0, 0, 0, 0, 0] })).toBe('pos must be six numbers')
    expect(validateRobot({ ...example('robot'), load: [0, 0, 0, 0, 0] })).toBe('load must be six numbers from -1 to 1')
  })

  it('accepts a negative load', () => {
    expect(validateRobot({ ...example('robot'), load: [-1, -0.5, 0, 0.5, 1, 0] })).toBeNull()
  })
})

describe('validateHaptics', () => {
  it('rejects an unknown cue', () => {
    expect(validateHaptics({ ...example('haptics'), cue: 'buzz' })).toBe('unknown cue "buzz"')
    expect(validateHaptics({ ...example('haptics'), cue: undefined })).toBe('unknown cue undefined')
  })

  it('accepts each known cue and no cue', () => {
    for (const cue of ['guide', 'contact', 'limit', 'resistance', null]) {
      expect(validateHaptics({ ...example('haptics'), cue })).toBeNull()
    }
  })

  it('rejects motor amplitudes that are not four numbers from 0 to 1', () => {
    const reason = 'motors must be four numbers from 0 to 1'
    expect(validateHaptics({ ...example('haptics'), motors: [0, 1.5, 0, 0] })).toBe(reason)
    expect(validateHaptics({ ...example('haptics'), motors: [0, 0.4, 0] })).toBe(reason)
  })

  it('rejects an enabled flag that is not true or false', () => {
    expect(validateHaptics({ ...example('haptics'), enabled: 'yes' })).toBe('enabled must be true or false')
  })
})

describe('isTopic', () => {
  it('knows the five topics and nothing else', () => {
    for (const topic of TOPICS) expect(isTopic(topic)).toBe(true)
    expect(isTopic(COMMAND_TOPIC)).toBe(false)
    expect(isTopic('Vision')).toBe(false)
    expect(isTopic('')).toBe(false)
  })
})

describe('parseFrame', () => {
  it('splits at the first space, so JSON may contain spaces', () => {
    expect(parseFrame('vision {"summary": "Cup near, slightly left."}')).toEqual({
      ok: true,
      topic: 'vision',
      payload: { summary: 'Cup near, slightly left.' },
    })
  })

  it('returns the topic even when this app does not know it', () => {
    expect(parseFrame('battery {"v":7.4}')).toEqual({ ok: true, topic: 'battery', payload: { v: 7.4 } })
  })

  it('rejects a frame with no topic prefix', () => {
    const failure = { ok: false, topic: null, error: 'frame has no topic prefix' }
    expect(parseFrame('{"t":1}')).toEqual(failure)
    expect(parseFrame('vision')).toEqual(failure)
    expect(parseFrame(' {"t":1}')).toEqual(failure)
    expect(parseFrame('')).toEqual(failure)
  })

  it('rejects a payload that is not JSON, and still says which topic it was on', () => {
    const failure = { ok: false, topic: 'pressure', error: 'payload is not valid JSON' }
    expect(parseFrame('pressure {"t":1,')).toEqual(failure)
    expect(parseFrame('pressure not json')).toEqual(failure)
    expect(parseFrame('pressure ')).toEqual(failure)
  })
})

describe('encodeFrame', () => {
  it('writes the topic, a space, then compact JSON', () => {
    expect(encodeFrame('command', { t: 1.5, type: 'clear_target' })).toBe('command {"t":1.5,"type":"clear_target"}')
  })

  it('round-trips through parseFrame', () => {
    const payload = { t: 1731542400.2, type: 'select_target', label: 'pill bottle', nested: { list: [1, 2.5, null] } }
    expect(parseFrame(encodeFrame(COMMAND_TOPIC, payload))).toEqual({ ok: true, topic: COMMAND_TOPIC, payload })
  })
})

describe('validateAssist', () => {
  const ok = {
    t: 1731542400.5,
    seq: 2,
    status: 'ok',
    detail: null,
    model: 'gemini-2.5-flash',
    result: {
      scene: 'A person in bed.',
      patient: { present: true, observations: ['Reaching'], urgency: 'watch' },
      suggestions: [{ text: 'Hand the bottle', item: 'bottle' }, { text: 'Check in', item: null }],
      items: [{ name: 'bottle', prompt: 'clear bottle', reason: 'Thirst', visible: true, known: true }],
    },
  }

  it('accepts an answer, a working and an error message', () => {
    expect(validateAssist(ok)).toBeNull()
    expect(validateAssist({ ...ok, status: 'working', detail: 'Looking.', result: null })).toBeNull()
    expect(validateAssist({ ...ok, status: 'error', detail: 'No key.', model: null, result: null })).toBeNull()
  })

  it('accepts the documented example', () => {
    const frame = parseFrame(TOPIC_INFO.assist.example)
    expect(frame.ok && validateAssist(frame.payload)).toBeNull()
  })

  it('rejects a result that does not match the status, and malformed items', () => {
    expect(validateAssist({ ...ok, status: 'working' })).not.toBeNull()
    expect(validateAssist({ ...ok, result: null })).not.toBeNull()
    expect(validateAssist({ ...ok, result: { ...ok.result, patient: { ...ok.result.patient, urgency: 'panic' } } })).not.toBeNull()
    expect(validateAssist({ ...ok, result: { ...ok.result, items: [{ name: '', prompt: '', reason: '', visible: true, known: true }] } })).not.toBeNull()
  })
})
