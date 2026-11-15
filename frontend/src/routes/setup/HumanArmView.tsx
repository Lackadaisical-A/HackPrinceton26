import { useCallback, useEffect, useRef } from 'react'
import { FONT_SANS, theme } from '../../render/theme'
import { useCanvas, type Draw } from '../../render/useCanvas'
import { bus } from '../../telemetry/bus'
import type { SensorRole, Vec3 } from '../../telemetry/contracts'
import { Button } from '../../ui/Button'
import { Plate } from '../../ui/Plate'
import { CALIBRATION_FRESH_MS } from '../console/calibration'
import { armParts, axisTip, board, DEFAULT_VIEW, makeProjection, orbit, palm, zoomed, type ArmView } from './humanArm'

/** Readable on the light well, and distinct from each other and from alarm red. */
const ROLE_COLORS: Record<SensorRole, string> = { upper: '#2f6fdb', forearm: '#0f9b7a', hand: '#c77c0a' }
const ROLE_NAMES: Record<SensorRole, string> = { upper: 'Bicep', forearm: 'Forearm', hand: 'Hand' }
const AXIS_COLORS = ['#d9434b', '#2e9e5b', '#3f6fd8']
const LABEL_OFFSETS: Record<SensorRole, [number, number]> = { upper: [8, -48], forearm: [8, 42], hand: [8, -44] }

/**
 * Your arm as the three IMUs see it (Wearable Lab's 3D view): move your arm after calibrating and
 * check it matches. Positions come from orientation and arm lengths; the shoulder stays fixed.
 */
export function HumanArmView() {
  const view = useRef<ArmView>(DEFAULT_VIEW)
  const drag = useRef<{ x: number; y: number } | null>(null)

  const draw = useCallback<Draw>((ctx, width, height) => {
    const colors = theme()
    ctx.clearRect(0, 0, width, height)
    ctx.fillStyle = colors.well
    ctx.fillRect(0, 0, width, height)
    const latest = bus.latest('calibration')
    const geometry = latest?.geometry ?? null
    const fresh = latest !== null && bus.ageMs('calibration') < CALIBRATION_FRESH_MS && latest.input_fresh
    const project = makeProjection(view.current, width, height)
    const line = (a: Vec3, b: Vec3, color: string, lineWidth: number, dash: number[] = []) => {
      const [ax, ay] = project(a)
      const [bx, by] = project(b)
      ctx.strokeStyle = color
      ctx.lineWidth = lineWidth
      ctx.lineCap = 'round'
      ctx.setLineDash(dash)
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke()
      ctx.setLineDash([])
    }
    const polygon = (points: Vec3[], fill: string, stroke: string) => {
      ctx.beginPath()
      points.forEach((point, i) => { const [x, y] = project(point); if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y) })
      ctx.closePath()
      ctx.fillStyle = fill; ctx.fill()
      ctx.strokeStyle = stroke; ctx.lineWidth = 2; ctx.stroke()
    }
    for (let i = -6; i <= 10; i++) {
      const v = i * 0.1
      line([-0.6, v, -0.55], [1, v, -0.55], colors.rule, 1)
      line([v, -0.6, -0.55], [v, 1, -0.55], colors.rule, 1)
    }
    // Torso and shoulders, for orientation.
    line([0, 0, 0], [0, 0, -0.5], 'rgb(42 16 48 / 0.12)', 32)
    line([0, -0.15, -0.02], [0, 0.15, -0.02], 'rgb(42 16 48 / 0.12)', 24)
    ctx.font = `600 13px ${FONT_SANS}`
    ctx.fillStyle = colors.ink2
    ctx.textBaseline = 'middle'
    if (!geometry) {
      ctx.fillText('Waiting for Wearable Lab…', 16, 22)
      return
    }
    const live = geometry.calibrated && fresh
    for (const part of armParts(geometry, project)) {
      const color = live ? ROLE_COLORS[part.role] : colors.ink3
      if (part.kind === 'bone') {
        line(part.a, part.b, 'rgb(42 16 48 / 0.16)', part.role === 'upper' ? 30 : 23)
        line(part.a, part.b, color, 5)
        if (part.role === 'hand') polygon(palm(part.a, part.b, geometry.palm_side), 'rgb(199 124 10 / 0.18)', color)
      } else {
        polygon(board(part.at, part.axes), colors.plate, color)
        part.axes.forEach((axis, i) => line(part.at, axisTip(part.at, axis), AXIS_COLORS[i], 2))
      }
    }
    ;(['Shoulder anchor', 'Elbow', 'Wrist'] as const).forEach((label, i) => {
      const [x, y] = project(geometry.joints[i])
      ctx.beginPath(); ctx.arc(x, y, 7, 0, Math.PI * 2)
      ctx.fillStyle = colors.ink; ctx.fill()
      ctx.strokeStyle = colors.well; ctx.lineWidth = 2; ctx.stroke()
      ctx.font = `500 12px ${FONT_SANS}`; ctx.fillStyle = colors.ink2
      ctx.fillText(label, x + 12, y + 18)
    })
    ;(['upper', 'forearm', 'hand'] as const).forEach((role, i) => {
      const [x, y] = project(geometry.markers[i])
      const [dx, dy] = LABEL_OFFSETS[role]
      ctx.strokeStyle = ROLE_COLORS[role]; ctx.lineWidth = 1; ctx.setLineDash([3, 3])
      ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + dx, y + dy); ctx.stroke(); ctx.setLineDash([])
      ctx.font = `700 12px ${FONT_SANS}`; ctx.fillStyle = ROLE_COLORS[role]
      ctx.fillText(ROLE_NAMES[role], x + dx + 3, y + dy)
    })
    ctx.font = `700 12px ${FONT_SANS}`; ctx.fillStyle = colors.ink2
    ctx.fillText(!geometry.calibrated ? 'REFERENCE POSE · CALIBRATION NEEDED' : fresh ? 'LIVE ORIENTATION · ESTIMATED POSITION' : 'READINGS STALE · LAST POSE HELD', 16, 20)
    ctx.font = `500 14px ${FONT_SANS}`; ctx.fillStyle = colors.ink
    const angle = (value: number | null) => (value === null ? '—' : `${value.toFixed(0)}°`)
    ctx.fillText(`Elbow bend ${angle(geometry.elbow_deg)}   ·   Wrist bend ${angle(geometry.wrist_deg)}`, 16, 42)
  }, [])
  const canvas = useCanvas(draw, 30)

  // Wheel zoom needs a non-passive listener so the page does not scroll as well.
  useEffect(() => {
    const el = canvas.current
    if (!el) return
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      view.current = zoomed(view.current, event.deltaY)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [canvas])

  return (
    <Plate title="Your arm" note="3 IMUs" aside={<Button size="sm" onClick={() => { view.current = DEFAULT_VIEW }}>Reset view</Button>}>
      <canvas ref={canvas} className="h-80 w-full touch-none rounded-md" role="img"
        aria-label="Your arm as estimated from the three IMUs. Drag to orbit; scroll to zoom."
        onPointerDown={(event) => { drag.current = { x: event.clientX, y: event.clientY }; event.currentTarget.setPointerCapture(event.pointerId) }}
        onPointerMove={(event) => {
          if (!drag.current) return
          view.current = orbit(view.current, event.clientX - drag.current.x, event.clientY - drag.current.y)
          drag.current = { x: event.clientX, y: event.clientY }
        }}
        onPointerUp={() => { drag.current = null }} onPointerCancel={() => { drag.current = null }} />
      <p className="mt-2 text-xs text-ink-3">After calibrating, move your arm and check this follows. Board axes: x red, y green, z blue.
        Positions are estimated from orientation and your arm lengths; the shoulder stays fixed.</p>
    </Plate>
  )
}
