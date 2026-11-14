import { clamp } from '../lib/math'

/** Below this smoothed load a joint shows no glow, so sensor noise does not flicker. */
export const GLOW_FLOOR = 0.05
/** Time constant of the ease toward a new load, in seconds (about 95 % settled in 150 ms). */
export const GLOW_TAU_S = 0.05
/** Halo size at the floor and at full load, as a multiple of its base size. */
export const HALO_SCALE_MIN = 1
export const HALO_SCALE_MAX = 2.2

/** Target glow for one joint: |load| clamped to 0..1, or 0 when there is no reading. */
export function glowTarget(load: readonly number[] | null | undefined, i: number): number {
  const value = load?.[i]
  if (value === undefined || !Number.isFinite(value)) return 0
  return clamp(Math.abs(value), 0, 1)
}

/** Exponential approach from `current` to `target` over `dtS` seconds. */
export function easeGlow(current: number, target: number, dtS: number, tauS = GLOW_TAU_S): number {
  if (dtS <= 0) return current
  const next = current + (target - current) * (1 - Math.exp(-dtS / tauS))
  // Snap the tail so a released joint actually reaches zero.
  return Math.abs(next - target) < 1e-3 ? target : next
}

/** What a smoothed load looks like: 0 below the floor, else its level 0..1 and halo scale. */
export function glowLook(level: number): { strength: number; scale: number } {
  if (level < GLOW_FLOOR) return { strength: 0, scale: HALO_SCALE_MIN }
  const strength = clamp((level - GLOW_FLOOR) / (1 - GLOW_FLOOR), 0, 1)
  return { strength, scale: HALO_SCALE_MIN + (HALO_SCALE_MAX - HALO_SCALE_MIN) * strength }
}
