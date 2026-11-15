import { describe, expect, it } from 'vitest'
import type { AssistResult } from '../telemetry/contracts'
import { assistSpeech, WELLNESS_NUDGE_SPEECH } from './assistSpeech'

const base: AssistResult = {
  scene: 'A person lying in bed.',
  patient: { present: true, observations: [], urgency: 'watch', state: 'eyes_closed' },
  suggestions: [{ text: 'Hand over the water', item: null }],
  items: [],
  touch_checks: [{ kind: 'wellness_nudge', text: 'Nudge the shoulder', reason: 'Still, eyes closed' }],
}

describe('assistSpeech', () => {
  it('asks for the wellness nudge before any other suggestion', () => {
    expect(assistSpeech(base)).toBe(WELLNESS_NUDGE_SPEECH)
  })

  it('puts an emergency first', () => {
    const urgent = { ...base, patient: { ...base.patient, urgency: 'urgent' as const }, suggestions: [{ text: 'Alert the caregiver now', item: null }] }
    expect(assistSpeech(urgent)).toBe('Attention. Alert the caregiver now.')
  })

  it('reads the top suggestion otherwise, and nothing when there is none', () => {
    expect(assistSpeech({ ...base, touch_checks: [] })).toBe('Suggestion: Hand over the water.')
    expect(assistSpeech({ ...base, touch_checks: undefined, suggestions: [] })).toBeNull()
  })
})
