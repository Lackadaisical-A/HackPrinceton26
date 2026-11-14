import { clamp } from '../lib/math'

/**
 * Colours for canvas drawing, read once from the CSS custom properties in styles/index.css so
 * the stylesheet stays the single source of truth.
 */

const NAMES = [
  'panel',
  'plate',
  'well',
  'rule',
  'rule-strong',
  'ink',
  'ink-2',
  'ink-3',
  'window',
  'window-ink',
  'window-ink-2',
  'touch-100',
  'touch-200',
  'touch-300',
  'touch-400',
  'touch-500',
  'touch-600',
  'touch-700',
  'caution',
  'caution-ink',
  'caution-window',
  'alarm',
  'alarm-ink',
  'alarm-window',
] as const

type Name = (typeof NAMES)[number]
type Camel<S extends string> = S extends `${infer A}-${infer B}` ? `${A}${Capitalize<Camel<B>>}` : S
export type Theme = { [K in Name as Camel<K>]: string }

let cached: Theme | null = null

export function theme(): Theme {
  if (cached) return cached
  const style = getComputedStyle(document.documentElement)
  const out: Record<string, string> = {}
  for (const name of NAMES) {
    const key = name.replace(/-(\w)/g, (_, c: string) => c.toUpperCase())
    out[key] = style.getPropertyValue(`--${name}`).trim()
  }
  cached = out as Theme
  return cached
}

export const FONT_SANS = '"Atkinson Hyperlegible Next Variable", ui-sans-serif, system-ui, sans-serif'
export const FONT_MONO = '"Atkinson Hyperlegible Mono Variable", ui-monospace, Consolas, monospace'

// --- The touch ramp ----------------------------------------------------------------------------

const hexToRgb = (hex: string): [number, number, number] => {
  const n = parseInt(hex.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

const STEPS = 48
/** The lowest value drawn with a ramp colour. Below it a mark is shown empty. */
export const TOUCH_FLOOR = 0.02
let lut: string[] | null = null

/**
 * Colour for a relative pressure or motor amplitude from 0 to 1: one hue, light to dark.
 * Values near zero use the well colour, so an idle pad reads as empty and not as "a little".
 */
export function touchColor(value: number): string {
  if (!lut) {
    const t = theme()
    const stops = [t.touch300, t.touch400, t.touch500, t.touch600, t.touch700].map(hexToRgb)
    lut = Array.from({ length: STEPS }, (_, i) => {
      const x = (i / (STEPS - 1)) * (stops.length - 1)
      const a = stops[Math.floor(x)]
      const b = stops[Math.min(stops.length - 1, Math.floor(x) + 1)]
      const f = x - Math.floor(x)
      const mix = (k: 0 | 1 | 2) => Math.round(a[k] + (b[k] - a[k]) * f)
      return `rgb(${mix(0)} ${mix(1)} ${mix(2)})`
    })
  }
  if (value < TOUCH_FLOOR) return theme().well
  return lut[Math.round(clamp(value, 0, 1) * (STEPS - 1))]
}
