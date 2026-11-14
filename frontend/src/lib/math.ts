export const clamp = (v: number, lo: number, hi: number): number => (v < lo ? lo : v > hi ? hi : v)

export const lerp = (a: number, b: number, t: number): number => a + (b - a) * t

export const smoothstep = (t: number): number => {
  const x = clamp(t, 0, 1)
  return x * x * (3 - 2 * x)
}

/** 0 at or below `lo`, 1 at or above `hi`, eased in between. */
export const ramp = (v: number, lo: number, hi: number): number => smoothstep((v - lo) / (hi - lo))

export const DEG = Math.PI / 180

export type Quat = [w: number, x: number, y: number, z: number]
export type Vec3 = [x: number, y: number, z: number]

/** Quaternion from roll (x), pitch (y), yaw (z) in radians, applied in Z-Y-X order. */
export function quatFromEuler(roll: number, pitch: number, yaw: number): Quat {
  const cr = Math.cos(roll / 2)
  const sr = Math.sin(roll / 2)
  const cp = Math.cos(pitch / 2)
  const sp = Math.sin(pitch / 2)
  const cy = Math.cos(yaw / 2)
  const sy = Math.sin(yaw / 2)
  return [
    cr * cp * cy + sr * sp * sy,
    sr * cp * cy - cr * sp * sy,
    cr * sp * cy + sr * cp * sy,
    cr * cp * sy - sr * sp * cy,
  ]
}

/** Roll, pitch, yaw in radians from a quaternion. Tolerates an unnormalised input. */
export function eulerFromQuat(q: Readonly<Quat>): { roll: number; pitch: number; yaw: number } {
  const n = Math.hypot(q[0], q[1], q[2], q[3]) || 1
  const w = q[0] / n
  const x = q[1] / n
  const y = q[2] / n
  const z = q[3] / n
  const roll = Math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
  const pitch = Math.asin(clamp(2 * (w * y - z * x), -1, 1))
  const yaw = Math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
  return { roll, pitch, yaw }
}

/** Rotates `v` by `q`, writing into `out` to avoid allocating in draw loops. */
export function rotate(q: Readonly<Quat>, v: Readonly<Vec3>, out: Vec3): Vec3 {
  const [w, x, y, z] = q
  const [vx, vy, vz] = v
  // t = 2 * cross(q.xyz, v)
  const tx = 2 * (y * vz - z * vy)
  const ty = 2 * (z * vx - x * vz)
  const tz = 2 * (x * vy - y * vx)
  // v + w * t + cross(q.xyz, t)
  out[0] = vx + w * tx + (y * tz - z * ty)
  out[1] = vy + w * ty + (z * tx - x * tz)
  out[2] = vz + w * tz + (x * ty - y * tx)
  return out
}

/** Median of a list. Returns NaN for an empty list. */
export function median(values: readonly number[]): number {
  if (values.length === 0) return NaN
  const sorted = [...values].sort((a, b) => a - b)
  const mid = sorted.length >> 1
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}
