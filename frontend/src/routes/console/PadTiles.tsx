import { useEffect, useRef } from 'react'
import { LIMITS, PADS } from '../../config'
import { schedule } from '../../render/scheduler'
import { touchColor } from '../../render/theme'
import { bus } from '../../telemetry/bus'

/** Above this level the tile is dark enough that light text keeps its contrast. */
const LIGHT_TEXT_FROM = 0.45
const DARK_TEXT = 'var(--ink)'
const LIGHT_TEXT = 'var(--plate)'
const EMPTY_TEXT = 'var(--ink-3)'
const CAUTION_RING = 'inset 0 0 0 2px var(--caution)'

/**
 * One tile per gripper pad: the pad's relative pressure as a number, on a box that darkens as
 * the number rises. Written straight to the DOM at 30 Hz, so 50 Hz pressure never re-renders React.
 */
export function PadTiles() {
  const group = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const el = group.current
    if (!el) return
    const tiles = [...el.querySelectorAll<HTMLElement>('[data-pad]')]
    const values = [...el.querySelectorAll<HTMLElement>('[data-pad-value]')]
    // What was last written per tile, so unchanged frames touch nothing.
    const shown = tiles.map(() => ({ bg: '', color: '', text: '', ring: '' }))

    return schedule(
      (nowMs) => {
        const latest = bus.ageMs('pressure', nowMs) > LIMITS.streamSilentMs ? null : bus.latest('pressure')
        PADS.forEach((_, i) => {
          const raw = latest?.norm[i]
          const fresh = raw !== undefined && Number.isFinite(raw)
          const value = fresh ? Math.min(1, Math.max(0, raw)) : 0
          const next = fresh
            ? {
                bg: touchColor(value),
                color: value > LIGHT_TEXT_FROM ? LIGHT_TEXT : DARK_TEXT,
                text: value.toFixed(2),
                ring: value >= LIMITS.gripCaution ? CAUTION_RING : 'none',
              }
            : { bg: touchColor(0), color: EMPTY_TEXT, text: '–', ring: 'none' }
          const prev = shown[i]
          const tile = tiles[i]
          if (next.bg !== prev.bg) tile.style.backgroundColor = next.bg
          if (next.color !== prev.color) tile.style.color = next.color
          if (next.ring !== prev.ring) tile.style.boxShadow = next.ring
          if (next.text !== prev.text) values[i].textContent = next.text
          shown[i] = next
        })
      },
      { el, hz: 30 },
    )
  }, [])

  return (
    <div>
      <div className="text-xs text-ink-2">Gripper pads</div>
      <div ref={group} role="group" aria-label="Gripper pad pressure, 0 to 1" className="mt-1 grid grid-cols-4 gap-2">
        {PADS.map((pad) => (
          <div
            key={pad.key}
            data-pad={pad.key}
            className="flex h-20 flex-col justify-between rounded-md border border-rule p-2"
            style={{
              backgroundColor: 'var(--well)',
              color: EMPTY_TEXT,
              transition: 'background-color 120ms linear, color 120ms linear',
            }}
          >
            <span className="text-xs leading-tight opacity-80">{pad.label}</span>
            <span data-pad-value className="num text-xl leading-none font-semibold">
              –
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
