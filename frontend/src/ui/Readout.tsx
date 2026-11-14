import { useEffect, useRef } from 'react'
import { schedule } from '../render/scheduler'
import { cx } from './cx'

export interface ReadoutProps {
  /** Returns the text to show. Called on the shared animation loop, never during render. */
  read: () => string
  /** Updates a second. Eight is as fast as a changing number can be read. */
  hz?: number
  /** Reserved width in characters, so neighbours do not shift as the value changes. */
  width?: number
  className?: string
}

/**
 * A live number. It writes straight to the DOM a few times a second, so a 50 Hz stream never
 * causes a React render.
 */
export function Readout({ read, hz = 8, width, className }: ReadoutProps) {
  const ref = useRef<HTMLSpanElement | null>(null)
  const readRef = useRef(read)

  useEffect(() => {
    readRef.current = read
  }, [read])

  useEffect(() => {
    const el = ref.current
    if (!el) return
    let shown: string | null = null
    return schedule(
      () => {
        const next = readRef.current()
        if (next !== shown) {
          el.textContent = next
          shown = next
        }
      },
      { el, hz },
    )
  }, [hz])

  return (
    <span
      ref={ref}
      className={cx('num inline-block whitespace-pre', className)}
      style={width ? { minWidth: `${width}ch` } : undefined}
    />
  )
}
