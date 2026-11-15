import type { PhysicalStatus } from '../telemetry/contracts'

/** Middle of the claw's travel, for when no measured claw position is known. */
const CLAW_FALLBACK = 50

/**
 * The pose Align & follow would move the arm to: Wearable Lab's preview angle for each arm joint
 * (URDF degrees, the units of `robot.pos`), with the claw where it is measured. Null when any arm
 * joint has no preview (not calibrated, input stale, or relative mapping).
 */
export function ghostPose(physical: PhysicalStatus | null | undefined, measured: readonly number[] | null | undefined): number[] | null {
  if (!physical || physical.joints.length < 6) return null
  const arm = physical.joints.slice(0, 5).map((joint) => joint.preview_deg)
  if (arm.some((value) => value === null || !Number.isFinite(value))) return null
  const claw = measured && measured.length === 6 && Number.isFinite(measured[5]) ? measured[5] : CLAW_FALLBACK
  return [...(arm as number[]), claw]
}

/** Same pose, to skip re-posing the model when nothing moved. */
export const samePose = (a: readonly number[] | null, b: readonly number[] | null): boolean =>
  a === b || (!!a && !!b && a.length === b.length && a.every((value, i) => Math.abs(value - b[i]) < 0.05))
