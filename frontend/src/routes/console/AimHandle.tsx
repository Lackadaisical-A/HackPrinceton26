import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { resetAim, setAim } from '../../app/runtime'
import { clampAim, describeAim, type Aim } from '../../lib/aim'

/**
 * The grab point for the gripper crosshair. The canvas draws the crosshair; this is a transparent button over
 * it that the operator drags to where the jaws really close, so vision measures guidance from there.
 *
 * While the point moves, `onMove` reports it (null once it is saved or dropped) for the canvas to draw at frame
 * rate. It is saved and sent to vision once, when the drag ends (or 200 ms after the last arrow key), not on
 * every move. `onMove` must keep its identity across renders.
 *
 * The picture fills the camera window (object-fill) and is not mirrored, so a fraction of the window is the
 * same fraction of the frame vision sees.
 */

/** How long after the last arrow key the point is sent. Holding a key sends once, not once per repeat. */
const NUDGE_SEND_MS = 200
const NUDGE = 0.01
const NUDGE_LARGE = 0.05

const STEPS: Record<string, [number, number]> = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, -1],
  ArrowDown: [0, 1],
}

export function AimHandle({ aim, onMove }: { aim: Aim; onMove: (point: Aim | null) => void }) {
  // Where the handle sits while the point moves; null when it sits on the saved point.
  const [shown, setShown] = useState<Aim | null>(null)
  const pending = useRef<Aim | null>(null)
  // From the pointer to the point, so grabbing the handle off-centre does not make the crosshair jump.
  const grab = useRef<[number, number] | null>(null)
  const nudgeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const move = (point: Aim | null): void => {
    pending.current = point
    setShown(point)
    onMove(point)
  }

  const commit = (point: Aim): void => {
    setAim(point.x, point.y)
    move(null)
  }

  const cancelNudge = (): void => {
    if (nudgeTimer.current !== null) clearTimeout(nudgeTimer.current)
    nudgeTimer.current = null
  }

  // A nudge still waiting to be sent when the handle goes away (a tag box is being drawn) is sent now.
  useEffect(
    () => () => {
      if (nudgeTimer.current !== null) {
        clearTimeout(nudgeTimer.current)
        if (pending.current) setAim(pending.current.x, pending.current.y)
      }
      pending.current = null
      onMove(null)
    },
    [onMove],
  )

  const fractionOf = (event: PointerEvent<HTMLButtonElement>): [number, number] | null => {
    const rect = event.currentTarget.parentElement?.getBoundingClientRect()
    if (!rect || rect.width === 0 || rect.height === 0) return null
    return [(event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height]
  }

  const point = shown ?? aim

  return (
    <button
      type="button"
      aria-label={`Gripper crosshair, ${describeAim(point)}. Drag it, or use the arrow keys, to where the jaws close; Home re-centres it.`}
      title="Drag to where the jaws close. Double-click re-centres."
      className="focus-window absolute z-[5] size-9 -translate-x-1/2 -translate-y-1/2 cursor-grab touch-none rounded-full transition-[background-color,box-shadow] duration-150 hover:bg-window-ink/10 hover:shadow-[0_0_0_1px_var(--window-ink-2)] active:cursor-grabbing"
      style={{ left: `${point.x * 100}%`, top: `${point.y * 100}%` }}
      onPointerDown={(event) => {
        if (event.button !== 0) return
        const at = fractionOf(event)
        if (!at) return
        cancelNudge()
        event.currentTarget.setPointerCapture(event.pointerId)
        grab.current = [point.x - at[0], point.y - at[1]]
        move(point)
      }}
      onPointerMove={(event) => {
        if (!grab.current) return
        const at = fractionOf(event)
        if (at) move(clampAim({ x: at[0] + grab.current[0], y: at[1] + grab.current[1] }))
      }}
      onPointerUp={() => {
        if (!grab.current) return
        grab.current = null
        if (pending.current) commit(pending.current)
      }}
      onPointerCancel={() => {
        grab.current = null
        move(null)
      }}
      onDoubleClick={() => {
        cancelNudge()
        resetAim()
        move(null)
      }}
      onKeyDown={(event: KeyboardEvent<HTMLButtonElement>) => {
        if (event.key === 'Escape' && grab.current) {
          grab.current = null
          move(null)
        } else if (event.key === 'Home') {
          cancelNudge()
          resetAim()
          move(null)
        } else if (event.key in STEPS) {
          const [sx, sy] = STEPS[event.key]
          const step = event.shiftKey ? NUDGE_LARGE : NUDGE
          const next = clampAim({ x: point.x + sx * step, y: point.y + sy * step })
          move(next)
          cancelNudge()
          nudgeTimer.current = setTimeout(() => {
            nudgeTimer.current = null
            commit(next)
          }, NUDGE_SEND_MS)
        } else {
          return
        }
        event.preventDefault()
      }}
    />
  )
}
