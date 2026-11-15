import { AIM_DEFAULT, AIM_LIMIT } from '../config'
import { clamp } from './math'

/** A point of the camera picture, as fractions of its width and height: where the gripper's grasp point shows. */
export interface Aim {
  x: number
  y: number
}

const isFiniteNumber = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

/**
 * The point kept inside the picture, AIM_LIMIT from each edge, and rounded to what is sent (3 decimals).
 * Anything that is not an object with two finite numbers (a damaged save) becomes the default.
 */
export function clampAim(value: unknown): Aim {
  const point = typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {}
  if (!isFiniteNumber(point.x) || !isFiniteNumber(point.y)) return { ...AIM_DEFAULT }
  const fit = (v: number) => Math.round(clamp(v, AIM_LIMIT, 1 - AIM_LIMIT) * 1000) / 1000
  return { x: fit(point.x), y: fit(point.y) }
}

export const sameAim = (a: Aim, b: Aim): boolean => Math.abs(a.x - b.x) < 5e-4 && Math.abs(a.y - b.y) < 5e-4

export const isDefaultAim = (aim: Aim): boolean => sameAim(aim, AIM_DEFAULT)

/** "x 52 %, y 61 %": for the log and for screen readers. */
export const describeAim = (aim: Aim): string => `x ${Math.round(aim.x * 100)} %, y ${Math.round(aim.y * 100)} %`
