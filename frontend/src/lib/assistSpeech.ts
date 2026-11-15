import type { AssistResult } from '../telemetry/contracts'

/** Said for a still patient with eyes closed: one picture cannot tell sleep from unconsciousness. */
export const WELLNESS_NUDGE_SPEECH = 'Nudge the patient gently to check for wellness.'

const sentence = (text: string): string => (/[.!?]$/.test(text) ? text : `${text}.`)

/**
 * The one line read aloud for a new assist answer, most important first: an emergency, then the wellness
 * nudge, then the top suggestion. Null when there is nothing worth interrupting the operator for.
 */
export function assistSpeech(result: AssistResult): string | null {
  const first = result.suggestions[0]?.text
  if (result.patient.urgency === 'urgent') return `Attention. ${sentence(first ?? result.scene)}`
  if (result.touch_checks?.some((check) => check.kind === 'wellness_nudge')) return WELLNESS_NUDGE_SPEECH
  return first ? `Suggestion: ${sentence(first)}` : null
}
