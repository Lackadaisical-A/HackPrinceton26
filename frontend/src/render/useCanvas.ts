import { useEffect, useRef, type RefObject } from 'react'
import { schedule } from './scheduler'

/** Draws one frame. Coordinates are CSS pixels; the backing store is already scaled. */
export type Draw = (ctx: CanvasRenderingContext2D, width: number, height: number, nowMs: number, dtMs: number) => void

/** Past 2x, extra canvas pixels cost fill time and are not visible. */
const MAX_DPR = 2

/**
 * Sizes a canvas to its CSS box and runs `draw` on the shared animation loop.
 * Give the canvas its size with CSS; this hook keeps the backing store in step.
 *
 * @param hz at most this many frames a second; omit for every display frame
 */
export function useCanvas(draw: Draw, hz?: number): RefObject<HTMLCanvasElement | null> {
  const ref = useRef<HTMLCanvasElement | null>(null)
  const drawRef = useRef(draw)

  useEffect(() => {
    drawRef.current = draw
  }, [draw])

  useEffect(() => {
    const canvas = ref.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return

    let width = 0
    let height = 0
    let dpr = 1

    const resize = (): void => {
      const box = canvas.getBoundingClientRect()
      dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR)
      width = box.width
      height = box.height
      const w = Math.max(1, Math.round(width * dpr))
      const h = Math.max(1, Math.round(height * dpr))
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w
        canvas.height = h
      }
    }

    const paint = (nowMs: number, dtMs: number): void => {
      if (width < 2 || height < 2) return
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      drawRef.current(ctx, width, height, nowMs, dtMs)
    }

    const sizes = new ResizeObserver(() => {
      resize()
      // Repaint at once, so a resized canvas is never shown blank between frames.
      paint(performance.now(), 0)
    })
    sizes.observe(canvas)
    resize()

    const stop = schedule(paint, { el: canvas, hz })
    return () => {
      stop()
      sizes.disconnect()
    }
  }, [hz])

  return ref
}
