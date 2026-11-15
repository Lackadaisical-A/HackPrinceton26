import { LABEL_LIMITS } from '../config'
import type { BBox } from './contracts'

/**
 * The console's side of vision's label library (computer_vision/paul_vision/labels.py and label_manager.py):
 * the name rules, and a box drawn on the picture. Pure, so it can be tested without a canvas.
 */

/** The labels of vision's two station markers. A tag never takes one (label_manager.RESERVED_NAMES). */
export const STATION_NAMES = ['pick station', 'drop station'] as const

/** Mirrors labels.normalize_name: lower case, single spaces, no space at either end. */
export const normalizeName = (text: string): string => text.toLowerCase().split(/\s+/).filter(Boolean).join(' ')

/** Why vision would refuse `name` (already normalised) as a tag name, or null. Mirrors labels.name_problem. */
export function nameProblem(name: string): string | null {
  if (!name) return 'Type a name.'
  if (name.length > LABEL_LIMITS.nameChars) return `Use at most ${LABEL_LIMITS.nameChars} characters.`
  if (name.includes('/') || name.includes('#')) return 'Names cannot contain a slash or a hash sign.'
  if ((STATION_NAMES as readonly string[]).includes(name)) return 'That name belongs to a station.'
  return null
}

const clamp01 = (v: number): number => Math.min(1, Math.max(0, v))
const round4 = (v: number): number => Math.round(v * 10_000) / 10_000

/**
 * The box between two corners of a drag, in fractions of the picture, corners in order and kept inside it.
 * Null when a side is shorter than labels.min_box_side: a click or a slip, not a box.
 */
export function boxFromDrag(ax: number, ay: number, bx: number, by: number): BBox | null {
  const x1 = round4(clamp01(Math.min(ax, bx)))
  const y1 = round4(clamp01(Math.min(ay, by)))
  const x2 = round4(clamp01(Math.max(ax, bx)))
  const y2 = round4(clamp01(Math.max(ay, by)))
  if (x2 - x1 < LABEL_LIMITS.minBoxSide || y2 - y1 < LABEL_LIMITS.minBoxSide) return null
  return [x1, y1, x2, y2]
}
