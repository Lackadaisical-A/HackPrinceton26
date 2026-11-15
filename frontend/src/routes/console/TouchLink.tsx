import { useCallback, useRef, type PointerEvent } from 'react'
import { setHaptics } from '../../app/runtime'
import { LIMITS, TRACE_WINDOW_S } from '../../config'
import type { Draw } from '../../render/useCanvas'
import { useCanvas } from '../../render/useCanvas'
import { alertsFor, useSession } from '../../state/session'
import { bus } from '../../telemetry/bus'
import { AlertRow } from '../../ui/AlertTag'
import { Kbd } from '../../ui/Kbd'
import { Plate } from '../../ui/Plate'
import { Readout } from '../../ui/Readout'
import { Switch } from '../../ui/Switch'
import { PadTiles } from './PadTiles'
import { drawGripTrace, drawHapticTrace, type TraceHover } from './traces'

function readGrip(): string {
  if (bus.ageMs('pressure') > LIMITS.streamSilentMs) return '  – '
  return bus.gripTrace.last(1).toFixed(2)
}

function readMotor(): string {
  const latest = bus.latest('haptics')
  if (!latest || bus.ageMs('haptics') > LIMITS.streamSilentMs) return '  – '
  return bus.hapticTrace.last(1).toFixed(2)
}

/**
 * The touch link: what the gripper feels, and what the armband is told to play in response.
 * This is the panel the experiment is about, so the haptics switch lives here.
 */
export function TouchLink() {
  const haptics = useSession((s) => s.haptics)
  const inTrial = useSession((s) => s.trial !== null)
  const idle = useSession((s) => s.link.state === 'idle')
  const alerts = useSession((s) => s.alerts)
  const touchAlerts = alertsFor(alerts, 'touch')

  const hover = useRef<TraceHover>({ x: null })
  const onMove = (event: PointerEvent<HTMLDivElement>): void => {
    hover.current.x = event.clientX - event.currentTarget.getBoundingClientRect().left
  }
  const onLeave = (): void => {
    hover.current.x = null
  }

  const grip = useCanvas(useCallback<Draw>((ctx, w, h, nowMs) => drawGripTrace(ctx, w, h, nowMs, hover.current.x), []))
  const motor = useCanvas(useCallback<Draw>((ctx, w, h, nowMs) => drawHapticTrace(ctx, w, h, nowMs, hover.current.x), []))

  return (
    <Plate
      title="Touch link"
      // An alert takes the note's place instead of joining the row, so the panel below does not
      // jump at the moment the operator is squeezing.
      note={touchAlerts.length > 0 ? <AlertRow alerts={touchAlerts} /> : 'relative levels, not calibrated force'}
      aside={
        <Switch
          label={
            inTrial ? (
              <>
                Haptics <span className="ml-1 text-xs font-normal text-ink-2">locked</span>
              </>
            ) : (
              <>
                Haptics <Kbd className="ml-1 max-md:hidden">H</Kbd>
              </>
            )
          }
          tone="touch"
          checked={haptics}
          onChange={setHaptics}
          disabled={inTrial || idle}
          description={
            inTrial ? 'Locked while a trial is running.' : idle ? 'Connect a data source to change this.' : undefined
          }
        />
      }
    >
      <PadTiles />
      <p className="mt-2 text-sm text-ink-2">
        {idle
          ? 'No data source is connected, so the pads and motors read nothing.'
          : haptics
            ? 'Pad pressure is driving the armband motors.'
            : 'Control condition: the pads still report grip, but nothing is sent to the armband.'}
      </p>

      <div className="mt-4 border-t border-rule pt-3" onPointerMove={onMove} onPointerLeave={onLeave}>
        <div className="flex items-baseline justify-between gap-3">
          <h3 className="text-sm font-medium text-ink">Grip, hardest-pressed pad</h3>
          <Readout read={readGrip} width={4} className="text-lg font-medium text-ink" />
        </div>
        <canvas
          ref={grip}
          className="mt-1 block h-28 w-full touch-pan-y"
          role="img"
          aria-label={`Grip over the last ${TRACE_WINDOW_S} seconds: the hardest-pressed pad as a line, with a band down to the lightest pad.`}
        />
        <div className="mt-2 flex items-baseline justify-between gap-3">
          <h3 className="text-sm font-medium text-ink">
            Haptic output, strongest motor
            {!haptics && <span className="ml-2 font-normal text-ink-2">off</span>}
          </h3>
          <Readout read={readMotor} width={4} className="text-lg font-medium text-ink" />
        </div>
        <canvas
          ref={motor}
          className="mt-1 block h-[4.5rem] w-full touch-pan-y"
          role="img"
          aria-label={`Commanded haptic output over the last ${TRACE_WINDOW_S} seconds.`}
        />
      </div>
    </Plate>
  )
}
