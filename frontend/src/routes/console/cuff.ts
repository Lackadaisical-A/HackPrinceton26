import { LIMITS } from '../../config'
import { DEG, rotate, type Quat, type Vec3 } from '../../lib/math'
import { FONT_SANS, theme } from '../../render/theme'
import { bus } from '../../telemetry/bus'

/**
 * The armband as a band around the forearm, turned by the IMU's quaternion.
 *
 * Body frame: x runs along the forearm towards the hand, z points out through the top motor.
 * The view is a fixed orthographic three-quarter view, so a level, forward-pointing arm shows
 * the band side-on with the hand to the right.
 */

const SEGMENTS = 40
const RADIUS = 0.62
const HALF_WIDTH = 0.44
const AZIMUTH = -65 * DEG
const ELEVATION = 22 * DEG

// Screen axes of the fixed view, in world coordinates.
const RIGHT: Vec3 = [-Math.sin(AZIMUTH), Math.cos(AZIMUTH), 0]
const UP: Vec3 = [-Math.sin(ELEVATION) * Math.cos(AZIMUTH), -Math.sin(ELEVATION) * Math.sin(AZIMUTH), Math.cos(ELEVATION)]
const TOWARD: Vec3 = [Math.cos(ELEVATION) * Math.cos(AZIMUTH), Math.cos(ELEVATION) * Math.sin(AZIMUTH), Math.sin(ELEVATION)]

const IDENTITY: Quat = [1, 0, 0, 0]
const dot = (a: Readonly<Vec3>, b: Readonly<Vec3>): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

// Scratch space, reused every frame.
const local: Vec3 = [0, 0, 0]
const world: Vec3 = [0, 0, 0]
const nearX = new Float32Array(SEGMENTS + 1)
const nearY = new Float32Array(SEGMENTS + 1)
const farX = new Float32Array(SEGMENTS + 1)
const farY = new Float32Array(SEGMENTS + 1)
const facing = new Float32Array(SEGMENTS + 1)

export function drawCuff(ctx: CanvasRenderingContext2D, w: number, h: number, nowMs: number): void {
  const t = theme()
  ctx.clearRect(0, 0, w, h)
  ctx.setLineDash([])
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'

  const imu = bus.ageMs('imu', nowMs) < LIMITS.streamSilentMs ? bus.latest('imu') : null
  const q: Readonly<Quat> = imu ? imu.q : IDENTITY
  const edge = imu ? t.ink : t.ruleStrong
  const scale = Math.min(w / 3.1, h / 1.95)
  const cx = w / 2
  const cy = h / 2 + 2

  let px = 0
  let py = 0
  const project = (turn: Readonly<Quat>, x: number, y: number, z: number): void => {
    local[0] = x
    local[1] = y
    local[2] = z
    rotate(turn, local, world)
    px = cx + dot(world, RIGHT) * scale
    py = cy - dot(world, UP) * scale
  }

  // Where the forearm points when the armband is level: a fixed reference to read the tilt against.
  project(IDENTITY, -1.3, 0, 0)
  const restAx = px
  const restAy = py
  project(IDENTITY, 1.35, 0, 0)
  ctx.beginPath()
  ctx.moveTo(restAx, restAy)
  ctx.lineTo(px, py)
  ctx.setLineDash([2, 4])
  ctx.lineWidth = 1
  ctx.strokeStyle = t.ruleStrong
  ctx.stroke()
  ctx.setLineDash([])

  // The two rims of the band, and which way each strip of its surface faces.
  for (let i = 0; i <= SEGMENTS; i++) {
    const a = (i / SEGMENTS) * Math.PI * 2
    const y = Math.cos(a)
    const z = -Math.sin(a)
    project(q, -HALF_WIDTH, y * RADIUS, z * RADIUS)
    nearX[i] = px
    nearY[i] = py
    project(q, HALF_WIDTH, y * RADIUS, z * RADIUS)
    farX[i] = px
    farY[i] = py
    local[0] = 0
    local[1] = y
    local[2] = z
    facing[i] = dot(rotate(q, local, world), TOWARD)
  }
  const front = (i: number): boolean => facing[i] + facing[i + 1] > 0

  const strips = (wantFront: boolean): void => {
    ctx.beginPath()
    for (let i = 0; i < SEGMENTS; i++) {
      if (front(i) !== wantFront) continue
      ctx.moveTo(nearX[i], nearY[i])
      ctx.lineTo(nearX[i + 1], nearY[i + 1])
      ctx.lineTo(farX[i + 1], farY[i + 1])
      ctx.lineTo(farX[i], farY[i])
      ctx.closePath()
    }
  }
  const rims = (wantFront: boolean): void => {
    ctx.beginPath()
    for (let i = 0; i < SEGMENTS; i++) {
      if (front(i) !== wantFront) continue
      ctx.moveTo(nearX[i], nearY[i])
      ctx.lineTo(nearX[i + 1], nearY[i + 1])
      ctx.moveTo(farX[i], farY[i])
      ctx.lineTo(farX[i + 1], farY[i + 1])
    }
  }

  // The inside of the band, seen through its open ends.
  strips(false)
  ctx.fillStyle = t.well
  ctx.fill()
  ctx.lineWidth = 1
  ctx.strokeStyle = t.well
  ctx.stroke()
  rims(false)
  ctx.strokeStyle = t.ruleStrong
  ctx.stroke()

  // The forearm, as a line through the band with an arrowhead at the hand.
  project(q, -1.25, 0, 0)
  const tailX = px
  const tailY = py
  project(q, 1.3, 0, 0)
  const tipX = px
  const tipY = py
  const length = Math.hypot(tipX - tailX, tipY - tailY) || 1
  const ux = (tipX - tailX) / length
  const uy = (tipY - tailY) / length
  ctx.beginPath()
  ctx.moveTo(tailX, tailY)
  ctx.lineTo(tipX, tipY)
  // Foreshortened to nothing when the arm points at the viewer, so the head shrinks with it.
  const head = Math.min(7, length * 0.2)
  ctx.moveTo(tipX - head * (ux * 0.9 - uy * 0.5), tipY - head * (uy * 0.9 + ux * 0.5))
  ctx.lineTo(tipX, tipY)
  ctx.lineTo(tipX - head * (ux * 0.9 + uy * 0.5), tipY - head * (uy * 0.9 - ux * 0.5))
  ctx.lineWidth = 1.5
  ctx.strokeStyle = edge
  ctx.stroke()

  // The outside of the band, over the part of the forearm that is inside it.
  strips(true)
  ctx.fillStyle = t.plate
  ctx.fill()
  ctx.lineWidth = 1
  ctx.strokeStyle = t.plate
  ctx.stroke()
  rims(true)
  ctx.lineWidth = 1.5
  ctx.strokeStyle = edge
  ctx.stroke()
  // The two edges of the silhouette, where the surface turns away.
  ctx.beginPath()
  for (let i = 0; i < SEGMENTS; i++) {
    const next = (i + 1) % SEGMENTS
    if (front(i) === front(next)) continue
    ctx.moveTo(nearX[i + 1], nearY[i + 1])
    ctx.lineTo(farX[i + 1], farY[i + 1])
  }
  ctx.stroke()

  // Orientation ticks, not motor locations. Physical outputs are shown in the touch panel.
  ctx.font = `400 10.5px ${FONT_SANS}`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'alphabetic'
  ;[-90, 0, 90, 180].forEach((angle, i) => {
    const y = Math.cos(angle * DEG)
    const z = -Math.sin(angle * DEG)
    local[0] = 0
    local[1] = y
    local[2] = z
    const visible = dot(rotate(q, local, world), TOWARD) > 0
    project(q, 0, y * RADIUS, z * RADIUS)
    if (!visible) return
    ctx.beginPath()
    ctx.arc(px, py, i === 0 ? 4 : 3.5, 0, Math.PI * 2)
    ctx.fillStyle = i === 0 ? edge : t.plate
    ctx.fill()
    ctx.lineWidth = 1.5
    ctx.strokeStyle = edge
    ctx.stroke()
    if (i === 0 && imu) {
      ctx.lineWidth = 3
      ctx.strokeStyle = t.plate
      ctx.strokeText('top', px, py - 8)
      ctx.fillStyle = t.ink2
      ctx.fillText('top', px, py - 8)
    }
  })

  ctx.textAlign = 'left'
  ctx.fillStyle = t.ink2
  if (!imu) {
    ctx.font = `400 12px ${FONT_SANS}`
    ctx.textBaseline = 'top'
    ctx.fillText('No armband data', 4, 4)
  } else {
    ctx.textAlign = ux >= 0 ? 'left' : 'right'
    ctx.textBaseline = 'middle'
    ctx.fillText('hand', tipX + (ux >= 0 ? 6 : -6), tipY)
  }
}
