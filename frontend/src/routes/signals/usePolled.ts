import { useCallback, useEffect, useRef, useState } from 'react'
import { schedule } from '../../render/scheduler'

/**
 * Reads a value that lives outside React a few times a second, and re-renders only when it
 * changes. For discrete facts (a count, a flag). Fast-changing numbers belong in a Readout.
 *
 * @returns the value, and a function that re-reads it at once (after an action that changes it)
 */
export function usePolled<T>(read: () => T, hz = 2, same: (a: T, b: T) => boolean = Object.is): [T, () => void] {
  const [value, setValue] = useState(read)
  const readRef = useRef(read)
  const sameRef = useRef(same)
  const shown = useRef(value)

  useEffect(() => {
    readRef.current = read
    sameRef.current = same
  }, [read, same])

  const refresh = useCallback(() => {
    const next = readRef.current()
    if (sameRef.current(shown.current, next)) return
    shown.current = next
    setValue(() => next)
  }, [])

  useEffect(() => schedule(refresh, { hz }), [hz, refresh])

  return [value, refresh]
}
