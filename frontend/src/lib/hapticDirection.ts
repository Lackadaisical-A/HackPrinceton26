/** Camera +x = right, +y = down. Output order: palm L/R, forearm L/R. */
export function hapticDirection(dx: number, dy: number, amplitude: number): number[] {
  const x = Math.abs(dx) > 0.12 ? dx : 0
  const y = Math.abs(dy) > 0.12 ? dy : 0
  const scale = Math.abs(x) + Math.abs(y)
  return scale === 0 ? [0, 0, 0, 0] : [-x + y, x + y, -x - y, x - y].map((v) => Math.max(0, v) / scale * amplitude)
}
