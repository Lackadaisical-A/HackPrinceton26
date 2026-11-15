import { useId, useRef, type PointerEvent } from 'react'
import type { BBox } from '../../telemetry/contracts'
import { boxFromDrag } from '../../telemetry/labels'
import { Button } from '../../ui/Button'
import { cx } from '../../ui/cx'
import type { DragBox } from './overlay'

/**
 * Teaching vision a name from a box drawn on the camera picture (`tag_visual` with a bbox).
 *
 * The picture fills the camera window (object-fill) and vision's stream is not mirrored, so a fraction of the
 * window is the same fraction of the frame vision sees: the box goes out as it was drawn.
 */

const INPUT_CLASS =
  'h-7 w-44 min-w-0 rounded-sm border border-rule bg-plate px-2 text-xs text-ink placeholder:text-ink-3 ' +
  'transition-[border-color,box-shadow] duration-150 hover:border-rule-strong/60 ' +
  'focus:border-ink focus:shadow-[0_0_0_3px_var(--touch-200)] focus-visible:outline-none ' +
  'disabled:cursor-not-allowed disabled:text-ink-3 disabled:hover:border-rule'

/**
 * Covers the camera window while a box is being drawn, so nothing under it takes the pointer.
 * `onDrag` reports the box under the pointer on every move (null once it is let go or cancelled), for the
 * canvas to draw; `onBox` reports the finished box, or null for a click.
 */
export function TagLayer({
  onDrag,
  onBox,
}: {
  onDrag: (box: DragBox | null) => void
  onBox: (box: BBox | null) => void
}) {
  const anchor = useRef<[number, number] | null>(null)

  const at = (event: PointerEvent<HTMLDivElement>): [number, number] => {
    const rect = event.currentTarget.getBoundingClientRect()
    return [(event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height]
  }

  return (
    <div
      aria-hidden="true"
      className="absolute inset-0 z-10 cursor-crosshair touch-none"
      onPointerDown={(event) => {
        if (event.button !== 0) return
        event.currentTarget.setPointerCapture(event.pointerId)
        const [x, y] = at(event)
        anchor.current = [x, y]
        onDrag({ ax: x, ay: y, bx: x, by: y })
      }}
      onPointerMove={(event) => {
        if (!anchor.current) return
        const [x, y] = at(event)
        onDrag({ ax: anchor.current[0], ay: anchor.current[1], bx: x, by: y })
      }}
      onPointerUp={(event) => {
        if (!anchor.current) return
        const [ax, ay] = anchor.current
        const [x, y] = at(event)
        anchor.current = null
        onDrag(null)
        onBox(boxFromDrag(ax, ay, x, y))
      }}
      onPointerCancel={() => {
        anchor.current = null
        onDrag(null)
      }}
    />
  )
}

export interface TagBarProps {
  name: string
  onName: (text: string) => void
  /** True while the operator is drawing a box. */
  drawing: boolean
  onDraw: () => void
  onCancel: () => void
  /** Null when tagging is possible; otherwise why not. */
  unavailable: string | null
  note: { text: string; problem: boolean } | null
}

/** The slim row under the camera: a name, and the button that starts drawing its box. */
export function TagBar({ name, onName, drawing, onDraw, onCancel, unavailable, note }: TagBarProps) {
  const id = useId()
  const noteId = useId()
  const text = unavailable ?? note?.text ?? 'Name an object, then draw a box around it on the picture.'
  return (
    <form
      className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1"
      onSubmit={(event) => {
        event.preventDefault()
        if (drawing) onCancel()
        else onDraw()
      }}
    >
      <label htmlFor={id} className="text-xs font-medium text-ink-3">
        Tag
      </label>
      <input
        id={id}
        className={INPUT_CLASS}
        value={name}
        onChange={(event) => onName(event.target.value)}
        placeholder="name, e.g. my flask"
        spellCheck={false}
        autoComplete="off"
        disabled={unavailable !== null || drawing}
        aria-describedby={noteId}
      />
      <Button type="submit" size="sm" variant={drawing ? 'quiet' : 'outline'} disabled={unavailable !== null}>
        {drawing ? 'Cancel' : 'Draw box'}
      </Button>
      <p
        id={noteId}
        role="status"
        title={text}
        className={cx('min-w-0 flex-1 basis-40 truncate text-xs', note?.problem && !unavailable ? 'text-alarm-ink' : 'text-ink-3')}
      >
        {text}
      </p>
    </form>
  )
}
