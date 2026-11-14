import { useCallback } from 'react'
import { LIMITS } from '../../config'
import { signed } from '../../lib/format'
import { DEG, eulerFromQuat } from '../../lib/math'
import type { Draw } from '../../render/useCanvas'
import { useCanvas } from '../../render/useCanvas'
import { alertsFor, useSession } from '../../state/session'
import { bus } from '../../telemetry/bus'
import { AlertRow } from '../../ui/AlertTag'
import { Plate } from '../../ui/Plate'
import { Readout } from '../../ui/Readout'
import { drawCuff } from './cuff'

function readAngle(axis: 'roll' | 'pitch' | 'yaw'): string {
  const imu = bus.latest('imu')
  if (!imu || bus.ageMs('imu') > LIMITS.streamSilentMs) return '     –'
  return `${signed(eulerFromQuat(imu.q)[axis] / DEG, 1).padStart(6)}°`
}

const readRoll = (): string => readAngle('roll')
const readPitch = (): string => readAngle('pitch')
const readYaw = (): string => readAngle('yaw')

function readRate(): string {
  const hz = bus.state('imu').hz
  return hz > 0 ? `${hz.toFixed(0)} Hz` : '–'
}

/** The wearable's orientation: the input that drives the arm. */
export function ArmbandPanel() {
  const alerts = useSession((s) => s.alerts)
  const canvas = useCanvas(
    useCallback<Draw>((ctx, w, h, nowMs) => drawCuff(ctx, w, h, nowMs), []),
    30,
  )

  return (
    <Plate title="Armband" note="orientation from the IMU" aside={<AlertRow alerts={alertsFor(alerts, 'armband')} />}>
      <div className="flex items-center gap-4">
        <canvas
          ref={canvas}
          className="block h-32 min-w-0 flex-1"
          role="img"
          aria-label="The armband drawn at its current orientation. Roll, pitch and yaw are listed beside it."
        />
        <dl className="grid shrink-0 grid-cols-[auto_auto] items-baseline gap-x-3 gap-y-1 text-sm">
          <dt className="text-ink-2">Roll</dt>
          <dd className="text-right font-medium text-ink">
            <Readout read={readRoll} width={7} />
          </dd>
          <dt className="text-ink-2">Pitch</dt>
          <dd className="text-right font-medium text-ink">
            <Readout read={readPitch} width={7} />
          </dd>
          <dt className="text-ink-2">Yaw</dt>
          <dd className="text-right font-medium text-ink">
            <Readout read={readYaw} width={7} />
          </dd>
          <dt className="mt-1.5 text-xs text-ink-3">Updates</dt>
          <dd className="mt-1.5 text-right text-xs text-ink-2">
            <Readout read={readRate} hz={2} />
          </dd>
        </dl>
      </div>
    </Plate>
  )
}
