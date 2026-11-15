import type { ArmGeometry, SensorRole, Vec3 } from '../../telemetry/contracts'

/**
 * The 3-IMU arm drawing from Wearable Lab (wearable_test/run.py ArmCanvas), as pure geometry:
 * the same orbit camera, projection and painter's order, so both views agree.
 */

export interface ArmView {
  azimuth: number
  elevation: number
  zoom: number
}

export const DEFAULT_VIEW: ArmView = { azimuth: -65, elevation: 24, zoom: 1 }
export const ROLES: readonly SensorRole[] = ['upper', 'forearm', 'hand']

const add = (a: Vec3, b: Vec3): Vec3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
const scale = (a: Vec3, k: number): Vec3 => [a[0] * k, a[1] * k, a[2] * k]
const dot = (a: Vec3, b: Vec3): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]

/** Screen x, y in CSS pixels and a depth used only for drawing order. */
export type Project = (p: Vec3) => [number, number, number]

export function makeProjection({ azimuth, elevation, zoom }: ArmView, width: number, height: number): Project {
  const az = azimuth * Math.PI / 180
  const el = elevation * Math.PI / 180
  const normal: Vec3 = [Math.cos(el) * Math.cos(az), Math.cos(el) * Math.sin(az), Math.sin(el)]
  const right: Vec3 = [-Math.sin(az), Math.cos(az), 0]
  const up = cross(normal, right)
  const factor = Math.min(width / 1.08, height / 1.04) * zoom
  return (point) => {
    const p = add(point, [-0.25, 0, 0.12])
    return [width / 2 + factor * dot(right, p), height / 2 - factor * dot(up, p), dot(normal, p)]
  }
}

/** Drag: horizontal orbits, vertical tilts within Wearable Lab's limits. */
export function orbit(view: ArmView, dx: number, dy: number): ArmView {
  return { ...view, azimuth: view.azimuth + dx * 0.4, elevation: Math.max(-70, Math.min(80, view.elevation + dy * 0.3)) }
}

export function zoomed(view: ArmView, wheelDeltaY: number): ArmView {
  const factor = wheelDeltaY < 0 ? 1.1 : 1 / 1.1
  return { ...view, zoom: Math.max(0.5, Math.min(2.5, view.zoom * factor)) }
}

export type ArmPart =
  | { kind: 'bone'; role: SensorRole; a: Vec3; b: Vec3; depth: number }
  | { kind: 'sensor'; role: SensorRole; at: Vec3; axes: [Vec3, Vec3, Vec3]; depth: number }

/** Bones and sensor boards, back to front. */
export function armParts(geometry: ArmGeometry, project: Project): ArmPart[] {
  const parts: ArmPart[] = []
  ROLES.forEach((role, i) => {
    const a = geometry.joints[i]
    const b = geometry.joints[i + 1]
    parts.push({ kind: 'bone', role, a, b, depth: project(add(a, scale(add(b, scale(a, -1)), 0.5)))[2] })
    parts.push({ kind: 'sensor', role, at: geometry.markers[i], axes: geometry.boards[i], depth: project(geometry.markers[i])[2] + 0.015 })
  })
  return parts.sort((x, y) => x.depth - y.depth)
}

/** The four corners of the palm, around the hand bone. */
export function palm(a: Vec3, b: Vec3, side: Vec3): Vec3[] {
  return [add(a, scale(side, -0.045)), add(b, scale(side, -0.045)), add(b, scale(side, 0.045)), add(a, scale(side, 0.045))]
}

/** A sensor board's outline, from its x and y axes. */
export function board(at: Vec3, axes: [Vec3, Vec3, Vec3]): Vec3[] {
  return ([[-1, -1], [1, -1], [1, 1], [-1, 1]] as const).map(([x, y]) => add(add(at, scale(axes[0], x * 0.026)), scale(axes[1], y * 0.019)))
}

export const axisTip = (at: Vec3, axis: Vec3): Vec3 => add(at, scale(axis, 0.055))
