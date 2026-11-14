import { GRIPPER_INDEX, LIMITS } from '../../config'
import { clamp, DEG } from '../../lib/math'
import { FONT_SANS, theme } from '../../render/theme'
import { bus } from '../../telemetry/bus'

/**
 * A side view of the arm, in the plane of the arm itself, so shoulder pan and wrist roll do not
 * show here (they are in the table beside it).
 *
 * This is a schematic, not a kinematic model. Link lengths are equal and the joint zeroes are a
 * guess at the SO-101's, chosen so that the poses read correctly: the upper arm leans forward
 * as the shoulder lifts, and a larger elbow angle folds the forearm in.
 */

const UPPER = 1
const FOREARM = 1
const HAND = 0.36
const JAW = 0.3
/** The forearm's angle from the upper arm when the elbow reads zero. */
const ELBOW_ZERO_DEG = 60

/** Rest pose, drawn faintly when there is no data. */
const REST = [0, -20, 45, 30, 0, 60]

function capsule(ctx: CanvasRenderingContext2D, ax: number, ay: number, bx: number, by: number, width: number, edge: string, fill: string): void {
  ctx.lineCap = 'round'
  ctx.beginPath()
  ctx.moveTo(ax, ay)
  ctx.lineTo(bx, by)
  ctx.lineWidth = width
  ctx.strokeStyle = edge
  ctx.stroke()
  ctx.lineWidth = width - 2.5
  ctx.strokeStyle = fill
  ctx.stroke()
}

export function drawArm(ctx: CanvasRenderingContext2D, w: number, h: number, nowMs: number): void {
  const t = theme()
  ctx.clearRect(0, 0, w, h)
  ctx.setLineDash([])
  ctx.lineJoin = 'round'

  const robot = bus.ageMs('robot', nowMs) < LIMITS.streamSilentMs ? bus.latest('robot') : null
  const pos = robot ? robot.pos : REST
  const edge = robot ? t.ink : t.ruleStrong

  const groundY = h - 10.5
  const scale = Math.min(w / 2.8, (h - 24) / 1.36)
  const baseX = w / 2 - 0.6 * scale
  const baseY = groundY - 0.26 * scale

  // Angles from vertical, positive towards the front of the arm (to the right).
  const a1 = pos[1] * DEG
  const a2 = a1 + (ELBOW_ZERO_DEG + pos[2]) * DEG
  const a3 = a2 + pos[3] * DEG
  const elbowX = baseX + Math.sin(a1) * UPPER * scale
  const elbowY = baseY - Math.cos(a1) * UPPER * scale
  const wristX = elbowX + Math.sin(a2) * FOREARM * scale
  const wristY = elbowY - Math.cos(a2) * FOREARM * scale
  const handX = wristX + Math.sin(a3) * HAND * scale
  const handY = wristY - Math.cos(a3) * HAND * scale

  // The bench and the pedestal the arm is bolted to.
  ctx.lineWidth = 1
  ctx.strokeStyle = t.ruleStrong
  ctx.beginPath()
  ctx.moveTo(Math.max(4, baseX - 0.8 * scale), groundY)
  ctx.lineTo(Math.min(w - 4, baseX + 2.05 * scale), groundY)
  ctx.stroke()
  const pedestal = 0.2 * scale
  ctx.beginPath()
  ctx.moveTo(baseX - pedestal * 1.15, groundY)
  ctx.lineTo(baseX - pedestal * 0.7, baseY)
  ctx.lineTo(baseX + pedestal * 0.7, baseY)
  ctx.lineTo(baseX + pedestal * 1.15, groundY)
  ctx.closePath()
  ctx.fillStyle = t.well
  ctx.fill()
  ctx.lineWidth = 1.25
  ctx.strokeStyle = edge
  ctx.stroke()

  // Jaws first, so the hand's end cap sits over their roots.
  const open = clamp(pos[GRIPPER_INDEX] / 100, 0, 1)
  const spread = (5 + 17 * open) * DEG
  ctx.lineCap = 'round'
  ctx.lineWidth = Math.max(2, 0.035 * scale)
  ctx.strokeStyle = edge
  ctx.beginPath()
  for (const side of [-1, 1]) {
    const a = a3 + side * spread
    const rootX = handX + Math.cos(a3) * side * 0.06 * scale
    const rootY = handY + Math.sin(a3) * side * 0.06 * scale
    ctx.moveTo(rootX, rootY)
    ctx.lineTo(rootX + Math.sin(a) * JAW * scale, rootY - Math.cos(a) * JAW * scale)
  }
  ctx.stroke()

  const thick = clamp(0.13 * scale, 6, 13)
  capsule(ctx, wristX, wristY, handX, handY, thick * 0.85, edge, t.plate)
  capsule(ctx, elbowX, elbowY, wristX, wristY, thick, edge, t.plate)
  capsule(ctx, baseX, baseY, elbowX, elbowY, thick, edge, t.plate)

  const joint = clamp(0.085 * scale, 4, 8)
  for (const [x, y] of [
    [baseX, baseY],
    [elbowX, elbowY],
    [wristX, wristY],
  ]) {
    ctx.beginPath()
    ctx.arc(x, y, joint, 0, Math.PI * 2)
    ctx.fillStyle = t.plate
    ctx.fill()
    ctx.lineWidth = 1.5
    ctx.strokeStyle = edge
    ctx.stroke()
    ctx.beginPath()
    ctx.arc(x, y, 1.5, 0, Math.PI * 2)
    ctx.fillStyle = edge
    ctx.fill()
  }

  if (!robot) {
    ctx.font = `400 12px ${FONT_SANS}`
    ctx.fillStyle = t.ink2
    ctx.textAlign = 'left'
    ctx.textBaseline = 'top'
    ctx.fillText('No arm data', 4, 4)
  }
}
