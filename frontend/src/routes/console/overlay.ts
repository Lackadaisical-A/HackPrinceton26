import { DEADZONE } from '../../config'
import type { Aim } from '../../lib/aim'
import { prefersReducedMotion } from '../../render/scheduler'
import { FONT_SANS } from '../../render/theme'
import type { BBox, VisionMessage } from '../../telemetry/contracts'

/**
 * The guidance overlay on the camera window.
 *
 * It is drawn in white with a dark halo, so it reads on any footage, and it changes shape
 * instead of colour: open corner brackets while steering, a closed box once aligned. The
 * conventions follow the vision module's own debug overlay (paul_vision/overlay.py).
 */

const HALO = 'rgb(28 11 33 / 0.6)'
const MARK = '251 242 248'

/** Display state carried between frames, so the box moves smoothly between 30 Hz messages. */
export interface OverlayMemory {
  box: BBox | null
  trackId: number
}

export const newOverlayMemory = (): OverlayMemory => ({ box: null, trackId: -1 })

function ink(ctx: CanvasRenderingContext2D, width: number, alpha: number, trace: () => void): void {
  ctx.beginPath()
  trace()
  ctx.lineWidth = width + 2.5
  ctx.strokeStyle = HALO
  ctx.stroke()
  ctx.lineWidth = width
  ctx.strokeStyle = `rgb(${MARK} / ${alpha})`
  ctx.stroke()
}

/**
 * @param usable true only when guidance may be shown (status "ok" and fresh)
 * @param aim where the gripper shows in the picture: the crosshair and the aligned zone are drawn there, as vision
 *   measures guidance from it (`set_aim`)
 */
export function drawOverlay(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  message: VisionMessage | null,
  usable: boolean,
  memory: OverlayMemory,
  dtMs: number,
  aim: Aim,
): void {
  const cx = aim.x * w
  const cy = aim.y * h
  const target = usable ? (message?.target ?? null) : null
  const guidance = usable ? (message?.guidance ?? null) : null
  const aligned = guidance?.aligned ?? false
  const faint = usable ? 1 : 0.4

  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.setLineDash([])

  // The gripper's aim point, and the zone inside which the target counts as aligned.
  const rx = (w * DEADZONE.enter) / 2
  const ry = (h * DEADZONE.enter) / 2
  if (!aligned) ctx.setLineDash([3, 5])
  ink(ctx, 1.25, faint * 0.9, () => ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2))
  ctx.setLineDash([])
  ink(ctx, 1.25, faint, () => {
    const gap = 4
    const arm = 9
    ctx.moveTo(cx - gap - arm, cy)
    ctx.lineTo(cx - gap, cy)
    ctx.moveTo(cx + gap, cy)
    ctx.lineTo(cx + gap + arm, cy)
    ctx.moveTo(cx, cy - gap - arm)
    ctx.lineTo(cx, cy - gap)
    ctx.moveTo(cx, cy + gap)
    ctx.lineTo(cx, cy + gap + arm)
  })

  if (!target || !guidance) {
    memory.box = null
    return
  }

  // Ease toward the newest box. A new track snaps, so the box never slides between objects.
  // With reduced motion requested, every box snaps: the easing is smoothing, not data.
  const goal = target.bbox
  if (!memory.box || memory.trackId !== target.track_id) {
    memory.box = [goal[0], goal[1], goal[2], goal[3]]
    memory.trackId = target.track_id
  } else {
    const k = dtMs > 0 && !prefersReducedMotion() ? 1 - Math.exp(-dtMs / 35) : 1
    for (let i = 0; i < 4; i++) memory.box[i] += (goal[i] - memory.box[i]) * k
  }
  const x1 = memory.box[0] * w
  const y1 = memory.box[1] * h
  const x2 = memory.box[2] * w
  const y2 = memory.box[3] * h
  const tx = (x1 + x2) / 2
  const ty = (y1 + y2) / 2

  if (aligned) {
    ink(ctx, 2, 1, () => ctx.rect(x1, y1, x2 - x1, y2 - y1))
    ctx.beginPath()
    ctx.arc(cx, cy, 5.5, 0, Math.PI * 2)
    ctx.fillStyle = HALO
    ctx.fill()
    ctx.beginPath()
    ctx.arc(cx, cy, 3.5, 0, Math.PI * 2)
    ctx.fillStyle = `rgb(${MARK})`
    ctx.fill()
  } else {
    const len = Math.max(8, Math.min(22, (x2 - x1) * 0.28, (y2 - y1) * 0.28))
    ink(ctx, 2, 1, () => {
      ctx.moveTo(x1, y1 + len)
      ctx.lineTo(x1, y1)
      ctx.lineTo(x1 + len, y1)
      ctx.moveTo(x2 - len, y1)
      ctx.lineTo(x2, y1)
      ctx.lineTo(x2, y1 + len)
      ctx.moveTo(x2, y2 - len)
      ctx.lineTo(x2, y2)
      ctx.lineTo(x2 - len, y2)
      ctx.moveTo(x1 + len, y2)
      ctx.lineTo(x1, y2)
      ctx.lineTo(x1, y2 - len)
    })

    // An arrow from the edge of the aligned zone toward the target: the way to move the gripper.
    const dx = tx - cx
    const dy = ty - cy
    const distance = Math.hypot(dx, dy)
    const ux = dx / (distance || 1)
    const uy = dy / (distance || 1)
    const start = Math.hypot(ux * rx, uy * ry) + 6
    const end = distance - 6
    if (end - start > 14) {
      const ax = cx + ux * end
      const ay = cy + uy * end
      const head = 8
      ink(ctx, 1.75, 1, () => {
        ctx.moveTo(cx + ux * start, cy + uy * start)
        ctx.lineTo(ax, ay)
        ctx.moveTo(ax - ux * head - uy * head * 0.6, ay - uy * head + ux * head * 0.6)
        ctx.lineTo(ax, ay)
        ctx.lineTo(ax - ux * head + uy * head * 0.6, ay - uy * head - ux * head * 0.6)
      })
    }
    ctx.beginPath()
    ctx.arc(tx, ty, 2.5, 0, Math.PI * 2)
    ctx.fillStyle = `rgb(${MARK})`
    ctx.fill()
  }

  drawTag(ctx, w, `${target.label}  ${target.confidence.toFixed(2)}`, x1, y1)
}

/** A box's label: on its top edge, or inside it when the box touches the frame's top. */
function drawTag(ctx: CanvasRenderingContext2D, w: number, text: string, x1: number, y1: number): void {
  ctx.font = `600 12px ${FONT_SANS}`
  const tagW = ctx.measureText(text).width + 12
  const tagH = 20
  const tagX = Math.max(4, Math.min(w - tagW - 4, x1 - 1))
  const tagY = y1 - tagH - 5 >= 4 ? y1 - tagH - 5 : y1 + 5
  ctx.beginPath()
  ctx.roundRect(tagX, tagY, tagW, tagH, 2)
  ctx.fillStyle = 'rgb(28 11 33 / 0.82)'
  ctx.fill()
  ctx.fillStyle = `rgb(${MARK})`
  ctx.textBaseline = 'middle'
  ctx.textAlign = 'left'
  ctx.fillText(text, tagX + 6, tagY + tagH / 2 + 0.5)
}

/** Two corners of a box being dragged, in fractions of the picture, in the order the pointer gave them. */
export interface DragBox {
  ax: number
  ay: number
  bx: number
  by: number
}

/** The box the operator is drawing to teach vision a name. Dashed, so it never reads as a detection. */
export function drawTagDraft(ctx: CanvasRenderingContext2D, w: number, h: number, box: DragBox, name: string): void {
  const x1 = Math.min(box.ax, box.bx) * w
  const y1 = Math.min(box.ay, box.by) * h
  const x2 = Math.max(box.ax, box.bx) * w
  const y2 = Math.max(box.ay, box.by) * h
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.setLineDash([6, 4])
  ink(ctx, 2, 1, () => ctx.rect(x1, y1, x2 - x1, y2 - y1))
  ctx.setLineDash([])
  if (name) drawTag(ctx, w, name, x1, y1)
}
