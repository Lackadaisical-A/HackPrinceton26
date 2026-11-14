import { GRIPPER_INDEX, LIMITS, MOTORS } from '../../config'
import { clamp, DEG, lerp } from '../../lib/math'
import { FONT_MONO, FONT_SANS, theme, TOUCH_FLOOR, touchColor } from '../../render/theme'
import { bus } from '../../telemetry/bus'

/**
 * The touch link, drawn as hardware: the gripper's four pads on the left, the armband's four
 * motors on the right, and the wire between them. With haptics off the wire is drawn open,
 * which is the control condition of the experiment.
 *
 * The gripper is seen from the front: fingertips at the top, pads on the inner faces, and the
 * fingers slide on their rail as the real gripper opens and closes.
 */

export const DIAGRAM_HEIGHT = 138

const FINGER_W = 22
const FINGER_H = 84
const FINGER_TOP = 12
const PAD_W = 9
const PAD_H = 34
const NUMBER_COL = 36
const TRAVEL = 44
const GRIPPER_CX = NUMBER_COL + 6 + FINGER_W + TRAVEL / 2
const RING_R = 30

const fresh = (topic: 'pressure' | 'haptics' | 'robot', nowMs: number): boolean =>
  bus.ageMs(topic, nowMs) < LIMITS.streamSilentMs

/**
 * @param linked whether haptic feedback is on
 */
export function drawTouchDiagram(ctx: CanvasRenderingContext2D, w: number, h: number, nowMs: number, linked: boolean): void {
  const t = theme()
  ctx.clearRect(0, 0, w, h)
  ctx.setLineDash([])
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'

  const pressure = fresh('pressure', nowMs) ? bus.latest('pressure') : null
  const haptics = fresh('haptics', nowMs) ? bus.latest('haptics') : null
  const robot = fresh('robot', nowMs) ? bus.latest('robot') : null
  const opening = robot ? clamp(robot.pos[GRIPPER_INDEX] / 100, 0, 1) : 0.6

  // --- Gripper --------------------------------------------------------------------------------
  const gap = lerp(12, TRAVEL, opening)
  const leftX = GRIPPER_CX - gap / 2 - FINGER_W
  const rightX = GRIPPER_CX + gap / 2
  const railY = FINGER_TOP + FINGER_H + 3

  // The rail the fingers slide on.
  ctx.beginPath()
  ctx.roundRect(GRIPPER_CX - TRAVEL / 2 - FINGER_W - 3, railY, TRAVEL + FINGER_W * 2 + 6, 9, 3)
  ctx.fillStyle = t.well
  ctx.fill()
  ctx.lineWidth = 1.25
  ctx.strokeStyle = t.ink2
  ctx.stroke()

  for (const side of [0, 1] as const) {
    const x = side === 0 ? leftX : rightX
    ctx.beginPath()
    ctx.roundRect(x, FINGER_TOP, FINGER_W, FINGER_H, [7, 7, 2, 2])
    ctx.fillStyle = t.plate
    ctx.fill()
    ctx.lineWidth = 1.25
    ctx.strokeStyle = t.ink2
    ctx.stroke()

    // Pad order in the pressure message: left tip, left base, right tip, right base.
    for (const row of [0, 1] as const) {
      const index = side * 2 + row
      const value = pressure ? pressure.norm[index] : 0
      const padX = side === 0 ? x + FINGER_W - PAD_W - 2 : x + 2
      const padY = FINGER_TOP + 5 + row * (PAD_H + 6)
      ctx.beginPath()
      ctx.roundRect(padX, padY, PAD_W, PAD_H, 2.5)
      ctx.fillStyle = touchColor(value)
      ctx.fill()
      if (value < TOUCH_FLOOR) {
        ctx.lineWidth = 1
        ctx.strokeStyle = t.ruleStrong
        ctx.stroke()
      }

      // The reading and the pad's name sit level with the pad, in a fixed column outside the
      // fingers' travel, so the numbers hold still while the gripper opens and closes.
      const textX = side === 0 ? NUMBER_COL : GRIPPER_CX + TRAVEL / 2 + FINGER_W + 6
      ctx.textAlign = side === 0 ? 'right' : 'left'
      ctx.textBaseline = 'alphabetic'
      ctx.font = `500 12.5px ${FONT_MONO}`
      ctx.fillStyle = t.ink
      ctx.fillText(pressure ? value.toFixed(2) : '–', textX, padY + 15)
      ctx.font = `400 11px ${FONT_SANS}`
      ctx.fillStyle = t.ink2
      ctx.fillText(row === 0 ? 'tip' : 'base', textX, padY + 28)
    }
  }

  // --- Armband --------------------------------------------------------------------------------
  const ringX = w - RING_R - 16
  const ringY = FINGER_TOP + FINGER_H / 2 + 6
  ctx.beginPath()
  ctx.arc(ringX, ringY, RING_R + 5, 0, Math.PI * 2)
  ctx.arc(ringX, ringY, RING_R - 5, 0, Math.PI * 2, true)
  ctx.fillStyle = t.well
  ctx.fill('evenodd')
  ctx.lineWidth = 1.25
  ctx.strokeStyle = t.ink2
  ctx.beginPath()
  ctx.arc(ringX, ringY, RING_R + 5, 0, Math.PI * 2)
  ctx.stroke()
  ctx.beginPath()
  ctx.arc(ringX, ringY, RING_R - 5, 0, Math.PI * 2)
  ctx.stroke()

  let strongest = 0
  MOTORS.forEach((motor, i) => {
    const value = linked && haptics?.enabled ? haptics.motors[i] : 0
    if (value > strongest) strongest = value
    const mx = ringX + RING_R * Math.cos(motor.angleDeg * DEG)
    const my = ringY + RING_R * Math.sin(motor.angleDeg * DEG)
    ctx.beginPath()
    ctx.arc(mx, my, 7.5, 0, Math.PI * 2)
    ctx.fillStyle = touchColor(value)
    ctx.fill()
    ctx.lineWidth = value < TOUCH_FLOOR ? 1 : 2
    ctx.strokeStyle = value < TOUCH_FLOOR ? t.ruleStrong : t.plate
    ctx.stroke()
  })

  // The cue being played, named in the middle of the ring.
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.font = `600 12px ${FONT_SANS}`
  ctx.fillStyle = linked ? t.ink : t.ink2
  ctx.fillText(!linked ? 'off' : !haptics ? '–' : (haptics.cue ?? 'idle'), ringX, ringY + 0.5)

  // --- The wire -------------------------------------------------------------------------------
  const wireY = railY + 4.5
  const fromX = GRIPPER_CX + TRAVEL / 2 + FINGER_W + 3
  const toY = ringY + RING_R + 5
  const active = linked && strongest >= TOUCH_FLOOR
  const breakX = fromX + (ringX - fromX) * 0.5

  ctx.lineWidth = active ? 2.5 : 1.5
  ctx.strokeStyle = active ? touchColor(strongest) : t.ink2
  ctx.beginPath()
  if (linked) {
    ctx.moveTo(fromX, wireY)
    ctx.lineTo(ringX - 6, wireY)
    ctx.quadraticCurveTo(ringX, wireY, ringX, wireY - 6)
    ctx.lineTo(ringX, toY)
    // A chevron pointing from the pads to the motors: the direction the signal travels.
    ctx.moveTo(breakX - 4, wireY - 4.5)
    ctx.lineTo(breakX + 1, wireY)
    ctx.lineTo(breakX - 4, wireY + 4.5)
  } else {
    // An open switch: the wire stops, and the contact arm is lifted clear of it.
    ctx.moveTo(fromX, wireY)
    ctx.lineTo(breakX - 9, wireY)
    ctx.lineTo(breakX + 6, wireY - 10)
    ctx.moveTo(breakX + 9, wireY)
    ctx.lineTo(ringX - 6, wireY)
    ctx.quadraticCurveTo(ringX, wireY, ringX, wireY - 6)
    ctx.lineTo(ringX, toY)
  }
  ctx.stroke()
  if (!linked) {
    ctx.fillStyle = t.plate
    for (const x of [breakX - 9, breakX + 9]) {
      ctx.beginPath()
      ctx.arc(x, wireY, 2.5, 0, Math.PI * 2)
      ctx.fill()
      ctx.stroke()
    }
  }

  ctx.textAlign = 'center'
  ctx.textBaseline = 'alphabetic'
  ctx.font = `400 11px ${FONT_SANS}`
  ctx.fillStyle = t.ink2
  ctx.fillText(linked ? 'pads drive motors' : 'link open', (fromX + ringX) / 2, wireY + 18)
}
