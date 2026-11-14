import { useLayoutEffect, useState, type CSSProperties, type RefObject } from 'react'

/**
 * Measures the element inside `container` that matches `selector` and returns a style that
 * places an absolutely positioned indicator over it. Re-measures when `key` changes and when
 * the container resizes, so the indicator glides to the current choice instead of jumping.
 */
export function useIndicator(container: RefObject<HTMLElement | null>, selector: string, key: unknown): CSSProperties {
  const [box, setBox] = useState<{ left: number; width: number; ready: boolean } | null>(null)

  useLayoutEffect(() => {
    const root = container.current
    if (!root) return
    const measure = (): void => {
      const target = root.querySelector<HTMLElement>(selector)
      if (!target) {
        setBox(null)
        return
      }
      const next = { left: target.offsetLeft, width: target.offsetWidth }
      // The first placement snaps; only later moves are animated.
      setBox((prev) => ({ ...next, ready: prev !== null }))
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(root)
    return () => observer.disconnect()
  }, [container, selector, key])

  if (!box) return { opacity: 0 }
  return {
    transform: `translateX(${box.left}px)`,
    width: box.width,
    transition: box.ready ? 'transform 320ms var(--ease-spring), width 320ms var(--ease-spring), opacity 150ms' : 'none',
  }
}
