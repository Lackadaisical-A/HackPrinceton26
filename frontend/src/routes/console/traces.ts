import { LIMITS, TRACE_WINDOW_S } from '../../config'
import { FONT_MONO, FONT_SANS, theme } from '../../render/theme'
import { bus } from '../../telemetry/bus'
import type { Ring } from '../../telemetry/ring'

/**
 * The two chart-recorder strips under the touch link: grip, and the haptic output it drives.
 *
 * They are separate charts with one value axis each, stacked on a shared time axis, so the two
 * can be read against each other without a dual-axis chart. The paper scrolls left; the pen is
 * at the right edge.
 */

const LEFT = 28
const RIGHT = 12
/** Samples further apart than this are not joined, so a gap in the data stays a gap. */
const GAP_S = 0.4
/** Vertical time rules, in seconds. They are fixed to the clock, so they scroll with the data. */
const TIME_RULE_S = 2

/** The pointer's position over either strip, shared so one crosshair spans both. */
export interface TraceHover {
  x: number | null
}

interface Frame {
  left: number
  right: number
  top: number
  bottom: number
  nowS: number
}

const xOf = (f: Frame, timeS: number): number =>
  f.right - ((f.nowS - timeS) / TRACE_WINDOW_S) * (f.right - f.left)
const yOf = (f: Frame, value: number): number => f.bottom - value * (f.bottom - f.top)

function paper(ctx: CanvasRenderingContext2D, f: Frame, valueRules: readonly number[]): void {
  const t = theme()
  ctx.setLineDash([])
  ctx.lineWidth = 1
  ctx.strokeStyle = t.rule
  ctx.beginPath()
  const first = Math.ceil((f.nowS - TRACE_WINDOW_S) / TIME_RULE_S) * TIME_RULE_S
  for (let s = first; s <= f.nowS; s += TIME_RULE_S) {
    const x = Math.round(xOf(f, s)) + 0.5
    ctx.moveTo(x, f.top)
    ctx.lineTo(x, f.bottom)
  }
  for (const value of valueRules) {
    const y = Math.round(yOf(f, value)) + 0.5
    ctx.moveTo(f.left, y)
    ctx.lineTo(f.right, y)
  }
  ctx.stroke()

  ctx.font = `400 10.5px ${FONT_MONO}`
  ctx.fillStyle = t.ink3
  ctx.textAlign = 'right'
  ctx.textBaseline = 'middle'
  for (const value of valueRules) ctx.fillText(String(value), f.left - 6, yOf(f, value) + 0.5)
}

/** Index of the first row inside the window, less one so the line enters from the left edge. */
function firstVisible(ring: Ring, sinceS: number): number {
  let lo = 0
  let hi = ring.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (ring.at(mid, 0) < sinceS) lo = mid + 1
    else hi = mid
  }
  return Math.max(0, lo - 1)
}

/** Index of the row nearest to a time, or -1 when the nearest is too far away to stand for it. */
function nearest(ring: Ring, timeS: number): number {
  if (ring.length === 0) return -1
  let lo = 0
  let hi = ring.length - 1
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (ring.at(mid, 0) < timeS) lo = mid + 1
    else hi = mid
  }
  if (lo > 0 && timeS - ring.at(lo - 1, 0) < ring.at(lo, 0) - timeS) lo--
  return Math.abs(ring.at(lo, 0) - timeS) <= GAP_S ? lo : -1
}

/** Traces one field of the ring as a path, lifting the pen across gaps. */
function trace(ctx: CanvasRenderingContext2D, f: Frame, ring: Ring, field: number, from: number): void {
  let previous = -Infinity
  for (let i = from; i < ring.length; i++) {
    const s = ring.at(i, 0)
    const x = xOf(f, s)
    const y = yOf(f, ring.at(i, field))
    if (s - previous > GAP_S) ctx.moveTo(x, y)
    else ctx.lineTo(x, y)
    previous = s
  }
}

/** Fills between two fields, or between one field and zero, one unbroken run at a time. */
function band(ctx: CanvasRenderingContext2D, f: Frame, ring: Ring, upper: number, lower: number | null, from: number): void {
  let start = from
  for (let i = from; i <= ring.length; i++) {
    const broken = i === ring.length || (i > start && ring.at(i, 0) - ring.at(i - 1, 0) > GAP_S)
    if (!broken) continue
    if (i - start > 1) {
      ctx.beginPath()
      for (let k = start; k < i; k++) ctx.lineTo(xOf(f, ring.at(k, 0)), yOf(f, ring.at(k, upper)))
      for (let k = i - 1; k >= start; k--) ctx.lineTo(xOf(f, ring.at(k, 0)), yOf(f, lower === null ? 0 : ring.at(k, lower)))
      ctx.closePath()
      ctx.fill()
    }
    start = i
  }
}

function crosshair(ctx: CanvasRenderingContext2D, f: Frame, ring: Ring, field: number, hoverX: number | null): void {
  if (hoverX === null || hoverX < f.left || hoverX > f.right) return
  const t = theme()
  const timeS = f.nowS - ((f.right - hoverX) / (f.right - f.left)) * TRACE_WINDOW_S
  const index = nearest(ring, timeS)

  const x = Math.round(hoverX) + 0.5
  ctx.setLineDash([])
  ctx.lineWidth = 1
  ctx.strokeStyle = t.ink2
  ctx.beginPath()
  ctx.moveTo(x, f.top)
  ctx.lineTo(x, f.bottom)
  ctx.stroke()
  if (index < 0) return

  const value = ring.at(index, field)
  const px = xOf(f, ring.at(index, 0))
  const py = yOf(f, value)
  ctx.beginPath()
  ctx.arc(px, py, 4, 0, Math.PI * 2)
  ctx.fillStyle = t.ink
  ctx.fill()
  ctx.lineWidth = 2
  ctx.strokeStyle = t.plate
  ctx.stroke()

  const ago = f.nowS - ring.at(index, 0)
  const text = `${value.toFixed(2)}, ${ago < 0.05 ? 'now' : `${ago.toFixed(1)} s ago`}`
  ctx.font = `500 11.5px ${FONT_MONO}`
  const width = ctx.measureText(text).width + 12
  const boxX = px + 8 + width > f.right ? px - 8 - width : px + 8
  const boxY = Math.min(f.bottom - 20, Math.max(f.top, py - 24))
  ctx.beginPath()
  ctx.roundRect(boxX, boxY, width, 20, 2)
  ctx.fillStyle = t.plate
  ctx.fill()
  ctx.lineWidth = 1
  ctx.strokeStyle = t.ruleStrong
  ctx.stroke()
  ctx.fillStyle = t.ink
  ctx.textAlign = 'left'
  ctx.textBaseline = 'middle'
  ctx.fillText(text, boxX + 6, boxY + 10.5)
}

/** The pen: a dot on the newest sample, shown only while the stream is flowing. */
function pen(ctx: CanvasRenderingContext2D, f: Frame, ring: Ring, field: number, color: string): void {
  if (ring.length === 0 || f.nowS - ring.last(0) > GAP_S) return
  ctx.beginPath()
  ctx.arc(f.right, yOf(f, ring.last(field)), 4.5, 0, Math.PI * 2)
  ctx.fillStyle = color
  ctx.fill()
  ctx.lineWidth = 2
  ctx.strokeStyle = theme().plate
  ctx.stroke()
}

export function drawGripTrace(ctx: CanvasRenderingContext2D, w: number, h: number, nowMs: number, hoverX: number | null): void {
  const t = theme()
  const ring = bus.gripTrace
  const f: Frame = { left: LEFT, right: w - RIGHT, top: 8, bottom: h - 6, nowS: nowMs / 1000 }
  ctx.clearRect(0, 0, w, h)
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  paper(ctx, f, [0, 0.5, 1])

  ctx.save()
  ctx.beginPath()
  ctx.rect(f.left, 0, f.right - f.left + RIGHT, h)
  ctx.clip()

  const from = firstVisible(ring, f.nowS - TRACE_WINDOW_S)
  // The spread between the lightest and the hardest-pressed pad.
  ctx.fillStyle = t.touch200
  ctx.globalAlpha = 0.75
  band(ctx, f, ring, 1, 2, from)
  ctx.globalAlpha = 1

  // The level at which the caution is raised. A reference line, so it stays in ink, not amber.
  const limitY = Math.round(yOf(f, LIMITS.gripCaution)) + 0.5
  ctx.setLineDash([5, 4])
  ctx.lineWidth = 1
  ctx.strokeStyle = t.ink2
  ctx.beginPath()
  ctx.moveTo(f.left, limitY)
  ctx.lineTo(f.right, limitY)
  ctx.stroke()
  ctx.setLineDash([])

  ctx.beginPath()
  trace(ctx, f, ring, 1, from)
  ctx.lineWidth = 2
  ctx.strokeStyle = t.touch600
  ctx.stroke()
  ctx.restore()

  // The limit's label, knocked out of whatever runs behind it.
  ctx.font = `400 11px ${FONT_SANS}`
  ctx.textAlign = 'left'
  ctx.textBaseline = 'top'
  ctx.lineWidth = 3
  ctx.strokeStyle = t.plate
  const label = `caution at ${LIMITS.gripCaution}`
  ctx.strokeText(label, f.left + 5, limitY + 4)
  ctx.fillStyle = t.ink2
  ctx.fillText(label, f.left + 5, limitY + 4)

  pen(ctx, f, ring, 1, t.touch600)
  crosshair(ctx, f, ring, 1, hoverX)
}

export function drawHapticTrace(ctx: CanvasRenderingContext2D, w: number, h: number, nowMs: number, hoverX: number | null): void {
  const t = theme()
  const ring = bus.hapticTrace
  const f: Frame = { left: LEFT, right: w - RIGHT, top: 7, bottom: h - 18, nowS: nowMs / 1000 }
  ctx.clearRect(0, 0, w, h)
  ctx.lineCap = 'butt'
  ctx.lineJoin = 'round'
  paper(ctx, f, [0, 1])

  ctx.save()
  ctx.beginPath()
  ctx.rect(f.left, 0, f.right - f.left + RIGHT, h)
  ctx.clip()
  const from = firstVisible(ring, f.nowS - TRACE_WINDOW_S)
  ctx.fillStyle = t.touch300
  ctx.globalAlpha = 0.6
  band(ctx, f, ring, 1, null, from)
  ctx.globalAlpha = 1
  ctx.beginPath()
  trace(ctx, f, ring, 1, from)
  ctx.lineWidth = 1.5
  ctx.strokeStyle = t.touch600
  ctx.stroke()
  ctx.restore()

  // The time axis, shared with the grip strip above.
  ctx.font = `400 10.5px ${FONT_MONO}`
  ctx.fillStyle = t.ink3
  ctx.textBaseline = 'alphabetic'
  ctx.textAlign = 'left'
  ctx.fillText(`${TRACE_WINDOW_S} s ago`, f.left, h - 3)
  ctx.textAlign = 'center'
  ctx.fillText(`${TRACE_WINDOW_S / 2} s ago`, (f.left + f.right) / 2, h - 3)
  ctx.textAlign = 'right'
  ctx.fillText('now', f.right, h - 3)

  pen(ctx, f, ring, 1, t.touch600)
  crosshair(ctx, f, ring, 1, hoverX)
}
