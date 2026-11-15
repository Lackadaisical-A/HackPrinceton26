import { useEffect, useRef } from 'react'
import { LIMITS, MOTORS } from '../../config'
import { schedule } from '../../render/scheduler'
import { bus } from '../../telemetry/bus'

/** Physical layout: forearm above, palm below; indexes retain the wire order. */
export function HapticTiles() {
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = root.current
    if (!el) return
    const tiles = [...el.querySelectorAll<HTMLElement>('[data-motor]')]
    const status = el.querySelector<HTMLElement>('[data-feedback-status]')!
    return schedule((nowMs) => {
      const latest = bus.ageMs('haptics', nowMs) <= LIMITS.streamSilentMs ? bus.latest('haptics') : null
      status.textContent = latest?.detail ?? (latest ? (latest.enabled ? 'Feedback enabled' : 'Feedback off') : 'Waiting for the wearable controller')
      tiles.forEach((tile) => {
        const i = Number(tile.dataset.motor)
        const level = latest?.motors[i] ?? 0
        tile.style.backgroundColor = level > 0 ? `color-mix(in srgb, var(--touch-400) ${Math.round(15 + 45 * level)}%, var(--plate))` : 'var(--well)'
        tile.querySelector<HTMLElement>('[data-level]')!.textContent = latest ? `${Math.round(level * 100)}%` : '–'
        tile.querySelector<HTMLElement>('[data-ack]')!.textContent = level > 0 ? (latest?.acknowledged?.[i] ? 'Acknowledged' : 'Commanded') : ''
      })
    }, { el, hz: 20 })
  }, [])
  return <div ref={root} className="mt-3 border-t border-rule pt-3">
    <div className="grid grid-cols-2 gap-2" aria-label="Vibration motors: forearm above, palm below">
      {[2, 3, 0, 1].map((i) => <div key={MOTORS[i].key} data-motor={i} className="rounded-md border border-rule p-2" style={{ backgroundColor: 'var(--well)' }}>
        <div className="text-xs text-ink-2">{MOTORS[i].label}{i > 1 ? ' · up' : ' · down'}</div>
        <div data-level className="num mt-1 text-lg font-medium">–</div>
        <div data-ack className="min-h-4 text-xs text-ink-2" />
      </div>)}
    </div>
    <p data-feedback-status className="mt-2 text-sm">Waiting for the wearable controller</p>
    <p className="mt-1 text-xs text-ink-2">Move toward the taps. Steady palm vibration = grip resistance; steady forearm vibration = arm resistance. Levels are commanded output, not force.</p>
  </div>
}
