import { useCallback, useEffect, useRef } from 'react'
import { JOINTS, LIMITS } from '../../config'
import { fixed, signed } from '../../lib/format'
import { clamp } from '../../lib/math'
import { schedule } from '../../render/scheduler'
import type { Draw } from '../../render/useCanvas'
import { useCanvas } from '../../render/useCanvas'
import { alertsFor, useSession } from '../../state/session'
import { bus } from '../../telemetry/bus'
import { AlertRow } from '../../ui/AlertTag'
import { Plate } from '../../ui/Plate'
import { drawArm } from './armSchematic'

/** How often the numbers are rewritten. The markers move faster than this; text cannot be read faster. */
const TEXT_EVERY_MS = 125

/**
 * Six joints as a table: where each is in its range, and how hard it is working.
 * One scheduler task moves every marker with a transform, so nothing here re-renders.
 */
function JointRows() {
  const body = useRef<HTMLTableSectionElement | null>(null)

  useEffect(() => {
    const el = body.current
    if (!el) return
    const markers = [...el.querySelectorAll<HTMLElement>('[data-marker]')]
    const fills = [...el.querySelectorAll<HTMLElement>('[data-load]')]
    const positions = [...el.querySelectorAll<HTMLElement>('[data-position]')]
    const loads = [...el.querySelectorAll<HTMLElement>('[data-load-text]')]
    let textAt = -Infinity

    return schedule(
      (nowMs) => {
        const robot = bus.ageMs('robot', nowMs) < LIMITS.streamSilentMs ? bus.latest('robot') : null
        const writeText = nowMs - textAt >= TEXT_EVERY_MS
        if (writeText) textAt = nowMs

        JOINTS.forEach((joint, i) => {
          const marker = markers[i]
          const fill = fills[i]
          if (!robot) {
            marker.style.opacity = '0'
            fill.style.transform = 'scaleX(0)'
            if (writeText) {
              positions[i].textContent = '–'
              loads[i].textContent = '–'
            }
            return
          }
          const at = clamp((robot.pos[i] - joint.min) / (joint.max - joint.min), 0, 1)
          const load = clamp(Math.abs(robot.load[i]), 0, 1)
          marker.style.opacity = '1'
          marker.style.transform = `translateX(${((at - 1) * 100).toFixed(2)}%)`
          fill.style.transform = `scaleX(${load.toFixed(3)})`
          if (writeText) {
            positions[i].textContent =
              joint.unit === '%' ? `${robot.pos[i].toFixed(0)} %` : `${signed(robot.pos[i], 1)}°`
            loads[i].textContent = fixed(load, 2)
          }
        })
      },
      { el, hz: 30 },
    )
  }, [])

  return (
    <table className="w-full border-collapse text-xs">
      <thead>
        <tr className="text-left text-ink-3">
          <th scope="col" className="pb-1 font-normal">
            Joint
          </th>
          <th scope="col" className="pb-1 pl-3 font-normal">
            Position in range
          </th>
          <th scope="col" className="pb-1 pl-3 font-normal">
            Load, 0 to 1
          </th>
        </tr>
      </thead>
      <tbody ref={body}>
        {JOINTS.map((joint) => (
          <tr key={joint.key} className="border-t border-rule">
            <th scope="row" className="py-1.5 text-left font-normal whitespace-nowrap text-ink">
              {joint.label}
            </th>
            <td className="w-[44%] py-1.5 pl-3">
              <div className="flex items-center gap-2">
                <div className="relative h-3 min-w-8 flex-1" aria-hidden="true">
                  <div className="absolute inset-x-0 top-1/2 h-px bg-rule-strong" />
                  {/* End stops, and the midpoint for joints that turn both ways. */}
                  <div className="absolute top-0.5 bottom-0.5 left-0 w-px bg-rule-strong" />
                  <div className="absolute top-0.5 right-0 bottom-0.5 w-px bg-rule-strong" />
                  {joint.min < 0 && <div className="absolute top-1 bottom-1 left-1/2 w-px bg-rule-strong" />}
                  <div data-marker className="pointer-events-none absolute inset-0 opacity-0 will-change-transform">
                    <div className="absolute inset-y-0 right-0 w-[3px] translate-x-1/2 rounded-full bg-ink" />
                  </div>
                </div>
                <span data-position className="num w-[7ch] shrink-0 text-right text-ink">
                  –
                </span>
              </div>
            </td>
            <td className="w-[30%] py-1.5 pl-3">
              <div className="flex items-center gap-2">
                <div className="relative h-1.5 min-w-6 flex-1 overflow-hidden rounded-xs bg-well" aria-hidden="true">
                  <div data-load className="absolute inset-0 origin-left scale-x-0 bg-ink-2 will-change-transform" />
                  {/* The level at which a high-load caution is raised. */}
                  <div className="absolute inset-y-0 w-px bg-ink" style={{ left: `${LIMITS.loadCaution * 100}%` }} />
                </div>
                <span data-load-text className="num w-[4ch] shrink-0 text-right text-ink">
                  –
                </span>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function ArmPanel() {
  const alerts = useSession((s) => s.alerts)
  const canvas = useCanvas(
    useCallback<Draw>((ctx, w, h, nowMs) => drawArm(ctx, w, h, nowMs), []),
    30,
  )

  return (
    <Plate title="Arm" note="side view, schematic" aside={<AlertRow alerts={alertsFor(alerts, 'arm')} />} bodyClassName="@container">
      <div className="grid items-center gap-x-5 gap-y-3 @lg:grid-cols-[11.5rem_minmax(0,1fr)]">
        <canvas
          ref={canvas}
          className="block h-36 w-full @lg:h-40"
          role="img"
          aria-label="Side view of the arm in its current pose. The joint values are in the table."
        />
        <JointRows />
      </div>
    </Plate>
  )
}
