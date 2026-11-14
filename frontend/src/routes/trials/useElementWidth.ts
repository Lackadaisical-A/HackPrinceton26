import { useLayoutEffect, useRef, useState, type RefObject } from 'react'

/**
 * The width of an element in whole CSS pixels, kept current as the layout changes.
 * It is 0 until the element is measured, which happens before the first paint.
 */
export function useElementWidth<T extends HTMLElement>(): [RefObject<T | null>, number] {
  const ref = useRef<T | null>(null)
  const [width, setWidth] = useState(0)

  useLayoutEffect(() => {
    const element = ref.current
    if (!element) return
    setWidth(element.clientWidth)
    const observer = new ResizeObserver((entries) => {
      const next = Math.floor(entries[entries.length - 1].contentRect.width)
      setWidth((current) => (current === next ? current : next))
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  return [ref, width]
}
