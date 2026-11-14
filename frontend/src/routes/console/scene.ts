import type { SimFrame } from '../../telemetry/sources/scenario'

/**
 * A schematic wrist-camera view for the simulator: a bench, the object, and the gripper jaws.
 * It is deliberately plain grey shapes, and the window labels it "Simulated scene", so nobody
 * mistakes it for footage. It exists so the overlay has something to point at.
 */

let backdrop: { w: number; h: number; fill: CanvasGradient } | null = null

function bench(ctx: CanvasRenderingContext2D, w: number, h: number): CanvasGradient {
  if (!backdrop || backdrop.w !== w || backdrop.h !== h) {
    const fill = ctx.createRadialGradient(w / 2, h * 0.55, h * 0.1, w / 2, h * 0.55, Math.hypot(w, h) * 0.62)
    fill.addColorStop(0, '#33213a')
    fill.addColorStop(1, '#150a19')
    backdrop = { w, h, fill }
  }
  return backdrop.fill
}

const BODY = '#a493a6'
const LIGHT = '#d3c6d4'
const DARK = '#62506a'
const SHADE = 'rgb(28 11 33 / 0.22)'

function drawObject(ctx: CanvasRenderingContext2D, label: string, x: number, y: number, bw: number, bh: number): void {
  const cx = x + bw / 2

  // Contact shadow on the bench.
  ctx.beginPath()
  ctx.ellipse(cx, y + bh, bw * 0.58, Math.max(2, bh * 0.05), 0, 0, Math.PI * 2)
  ctx.fillStyle = 'rgb(0 0 0 / 0.38)'
  ctx.fill()

  if (label === 'cup') {
    const rim = Math.max(2, bh * 0.08)
    const inset = bw * 0.1
    ctx.beginPath()
    ctx.moveTo(x, y + rim)
    ctx.lineTo(x + bw, y + rim)
    ctx.lineTo(x + bw - inset, y + bh)
    ctx.lineTo(x + inset, y + bh)
    ctx.closePath()
    ctx.fillStyle = BODY
    ctx.fill()
    ctx.beginPath()
    ctx.moveTo(cx + bw * 0.12, y + rim)
    ctx.lineTo(x + bw, y + rim)
    ctx.lineTo(x + bw - inset, y + bh)
    ctx.lineTo(cx + bw * 0.1, y + bh)
    ctx.closePath()
    ctx.fillStyle = SHADE
    ctx.fill()
    ctx.beginPath()
    ctx.ellipse(cx, y + rim, bw / 2, rim, 0, 0, Math.PI * 2)
    ctx.fillStyle = LIGHT
    ctx.fill()
    ctx.beginPath()
    ctx.ellipse(cx, y + rim, bw * 0.42, rim * 0.72, 0, 0, Math.PI * 2)
    ctx.fillStyle = DARK
    ctx.fill()
    return
  }

  if (label === 'bottle') {
    const capH = bh * 0.07
    const neckH = bh * 0.2
    const neckW = bw * 0.42
    ctx.beginPath()
    ctx.roundRect(x, y + capH + neckH, bw, bh - capH - neckH, Math.min(bw * 0.2, 10))
    ctx.fillStyle = BODY
    ctx.fill()
    ctx.beginPath()
    ctx.moveTo(cx - neckW / 2, y + capH)
    ctx.lineTo(cx + neckW / 2, y + capH)
    ctx.lineTo(x + bw, y + capH + neckH + bw * 0.2)
    ctx.lineTo(x, y + capH + neckH + bw * 0.2)
    ctx.closePath()
    ctx.fill()
    ctx.fillStyle = SHADE
    ctx.fillRect(cx + bw * 0.14, y + capH + neckH, bw * 0.36, bh - capH - neckH - 2)
    ctx.fillStyle = LIGHT
    ctx.fillRect(cx - neckW * 0.58, y, neckW * 1.16, capH + 1)
    return
  }

  if (label === 'pill bottle') {
    const capH = bh * 0.22
    ctx.beginPath()
    ctx.roundRect(x + bw * 0.06, y + capH - 1, bw * 0.88, bh - capH + 1, Math.min(bw * 0.12, 8))
    ctx.fillStyle = BODY
    ctx.fill()
    ctx.fillStyle = SHADE
    ctx.fillRect(cx + bw * 0.12, y + capH, bw * 0.32, bh - capH - 2)
    // The label panel.
    ctx.fillStyle = LIGHT
    ctx.fillRect(x + bw * 0.14, y + capH + (bh - capH) * 0.28, bw * 0.5, (bh - capH) * 0.38)
    ctx.beginPath()
    ctx.roundRect(x, y, bw, capH, Math.min(bw * 0.08, 5))
    ctx.fillStyle = LIGHT
    ctx.fill()
    return
  }

  // A box: front face with a lighter top face above it.
  const top = bh * 0.2
  const skew = bw * 0.12
  ctx.fillStyle = BODY
  ctx.fillRect(x, y + top, bw - skew, bh - top)
  ctx.beginPath()
  ctx.moveTo(x, y + top)
  ctx.lineTo(x + skew, y)
  ctx.lineTo(x + bw, y)
  ctx.lineTo(x + bw - skew, y + top)
  ctx.closePath()
  ctx.fillStyle = LIGHT
  ctx.fill()
  ctx.beginPath()
  ctx.moveTo(x + bw - skew, y + top)
  ctx.lineTo(x + bw, y)
  ctx.lineTo(x + bw, y + bh - top)
  ctx.lineTo(x + bw - skew, y + bh)
  ctx.closePath()
  ctx.fillStyle = DARK
  ctx.fill()
}

function drawJaws(ctx: CanvasRenderingContext2D, w: number, h: number, openPercent: number): void {
  const cx = w / 2
  const open = openPercent / 100
  const gapTip = w * (0.105 + 0.21 * open)
  const gapNear = gapTip * 1.45
  const tipY = h * 0.6
  const thickTip = w * 0.05
  const thickNear = w * 0.1

  for (const side of [-1, 1]) {
    ctx.beginPath()
    ctx.moveTo(cx + side * gapNear, h + 2)
    ctx.lineTo(cx + side * gapTip, tipY)
    ctx.lineTo(cx + side * (gapTip + thickTip), tipY - h * 0.012)
    ctx.lineTo(cx + side * (gapNear + thickNear), h + 2)
    ctx.closePath()
    ctx.fillStyle = '#2a1c30'
    ctx.fill()
    ctx.lineWidth = 1
    ctx.strokeStyle = '#4a3a52'
    ctx.stroke()
    // The pad on the inner face.
    ctx.beginPath()
    ctx.moveTo(cx + side * gapNear, h + 2)
    ctx.lineTo(cx + side * gapTip, tipY)
    ctx.lineWidth = Math.max(2, w * 0.006)
    ctx.strokeStyle = '#66566c'
    ctx.stroke()
  }
}

/** @param dimmed draws the scene darkened, for when the feed is lost and the picture is stale */
export function drawScene(ctx: CanvasRenderingContext2D, w: number, h: number, frame: SimFrame, dimmed: boolean): void {
  ctx.setLineDash([])
  ctx.fillStyle = bench(ctx, w, h)
  ctx.fillRect(0, 0, w, h)

  // Bench lines that slide as the arm pans and lifts, so camera motion is visible.
  const pan = frame.joints[0]
  const lift = frame.joints[1]
  const horizon = h * (0.2 - lift * 0.0035)
  const vanishX = w * (0.5 - pan * 0.006)
  ctx.lineWidth = 1
  ctx.strokeStyle = 'rgb(251 242 248 / 0.055)'
  ctx.beginPath()
  for (let i = -6; i <= 6; i++) {
    ctx.moveTo(vanishX, horizon)
    ctx.lineTo(w / 2 + i * w * 0.24 - pan * w * 0.004, h)
  }
  for (let i = 1; i <= 5; i++) {
    const y = horizon + (h - horizon) * (i / 5) ** 2
    ctx.moveTo(0, y)
    ctx.lineTo(w, y)
  }
  ctx.stroke()
  ctx.fillStyle = 'rgb(28 11 33 / 0.5)'
  ctx.fillRect(0, 0, w, Math.max(0, horizon))

  if (frame.box) {
    const [x1, y1, x2, y2] = frame.box
    drawObject(ctx, frame.label, x1 * w, y1 * h, (x2 - x1) * w, (y2 - y1) * h)
  }
  drawJaws(ctx, w, h, frame.joints[5])

  if (dimmed) {
    ctx.fillStyle = 'rgb(28 11 33 / 0.6)'
    ctx.fillRect(0, 0, w, h)
  }
}
