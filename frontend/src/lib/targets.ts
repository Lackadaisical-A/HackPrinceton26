import { DETECTOR_CLASSES } from '../config'
import type { LabelsMessage } from '../telemetry/contracts'
import { sentenceCase } from './format'

/** The picker's value for "no particular object". Never a name vision was taught: a name cannot hold a hash sign. */
export const ANY_TARGET = '#any'

export interface TargetOption {
  value: string
  label: string
}

type Labels = Pick<LabelsMessage, 'classes' | 'tags'> | null

/**
 * Every name vision can target: its detector classes, then the names the operator taught, each once.
 * Before vision has said what it knows, the classes this console was built with.
 */
export function targetNames(labels: Labels): string[] {
  if (!labels) return [...DETECTOR_CLASSES]
  return [...new Set([...labels.classes, ...labels.tags.map((tag) => tag.name)])]
}

/** The choices of a target picker: any object, then every name vision can target. */
export function targetOptions(labels: Labels): TargetOption[] {
  return [{ value: ANY_TARGET, label: 'Any object' }, ...targetNames(labels).map((name) => ({ value: name, label: sentenceCase(name) }))]
}

/** The taught names, as a phrase: "vaseline (4 views), airpods (1 view)". Empty when nothing was taught. */
export function describeLearned(labels: Labels): string {
  if (!labels) return ''
  return labels.tags.map((tag) => `${tag.name} (${tag.views} ${tag.views === 1 ? 'view' : 'views'})`).join(', ')
}
