import { useEffect, useRef, useState } from 'react'
import { sendCommand } from '../../app/runtime'
import { MOTORS } from '../../config'
import { newRequestId } from '../../lib/requestId'
import { bus } from '../../telemetry/bus'
import type { CoinAction, CoinStatus } from '../../telemetry/contracts'
import { Button } from '../../ui/Button'
import { cx } from '../../ui/cx'
import { Plate } from '../../ui/Plate'
import { useControllerRequest } from '../console/useControllerRequest'
import { labUnavailable, useLabStatus } from '../console/useLabStatus'

/** Intensity changes while a coin runs are sent at most this often. */
const INTENSITY_MS = 100

function state(coin: CoinStatus): string {
  if (coin.feedback) return `Guidance owns it · ${coin.output.toFixed(0)}%`
  if (coin.output > 0) return `Running ${coin.running_s.toFixed(1)} s · ${coin.output.toFixed(0)}%`
  return 'Idle'
}

/**
 * Manual coin tests, as at the bottom of Wearable Lab: set a level, buzz for 1 s or 10 s, stop.
 * Starting a test turns automatic guidance off; STOP / Esc stops every coin.
 */
export function CoinBench() {
  const lab = useLabStatus()
  const coins = lab.available ? lab.message?.coins ?? null : null
  const request = useControllerRequest((m) => m.coins?.request)
  const [levels, setLevels] = useState<(number | null)[]>([null, null, null, null])
  const pendingLevels = useRef<Map<number, number>>(new Map())
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => { if (timer.current !== null) clearTimeout(timer.current) }, [])

  const level = (i: number) => levels[i] ?? coins?.motors[i]?.percent ?? 40

  const flushLevels = () => {
    timer.current = null
    const current = bus.latest('calibration')
    if (!current) return
    for (const [motor, percent] of pendingLevels.current) {
      sendCommand({ type: 'test_haptic_motor', action: 'intensity', motor, percent, request_id: newRequestId(), session: current.session, revision: current.revision })
    }
    pendingLevels.current.clear()
  }
  const setLevel = (i: number, percent: number) => {
    setLevels((previous) => previous.map((value, j) => (j === i ? percent : value)))
    pendingLevels.current.set(i, percent)
    timer.current ??= setTimeout(flushLevels, INTENSITY_MS)
  }
  const act = (i: number, action: CoinAction) => {
    request.send((current, id) => {
      const coin = current.coins?.motors[i]
      if (!coin || (action !== 'stop' && (!coin.available || !current.coins?.editable))) return null
      return { type: 'test_haptic_motor', action, motor: i, percent: level(i), request_id: id, session: current.session, revision: current.revision }
    }, `${MOTORS[i].label} coin: ${action === 'test' ? 'test 1 s' : action === 'run' ? 'run 10 s' : 'stop'}`)
  }

  return (
    <Plate title="Haptic coins" note="bench test">
      {!coins ? <p className="text-sm text-ink-2">{labUnavailable(lab) || 'This Wearable Lab does not report coin status. Update it and restart.'}</p> : <>
        <div className="grid gap-3 @min-[34rem]:grid-cols-2">
          {coins.motors.map((coin, i) => (
            <div key={MOTORS[i].key} className="min-w-0 rounded-md border border-rule p-3">
              <div className="flex items-baseline justify-between gap-2">
                <h3 className="text-sm font-semibold">{MOTORS[i].label}</h3>
                <span className="text-xs text-ink-3">{MOTORS[i].driver}</span>
              </div>
              <label className="mt-2 flex items-center gap-2 text-xs text-ink-2">
                <span className="w-16 shrink-0 tabular-nums">Level {level(i).toFixed(0)}%</span>
                <input type="range" min={0} max={100} step={1} value={level(i)} disabled={!coin.available}
                  onChange={(event) => setLevel(i, Number(event.target.value))} className="w-full accent-touch-600" aria-label={`${MOTORS[i].label} level`} />
              </label>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <Button size="sm" disabled={!coin.available || !coins.editable || request.pending} onClick={() => act(i, 'test')}>Test 1 s</Button>
                <Button size="sm" disabled={!coin.available || !coins.editable || request.pending} onClick={() => act(i, 'run')}>Run 10 s</Button>
                <Button size="sm" variant="danger" disabled={!coin.available} onClick={() => act(i, 'stop')}>Stop</Button>
              </div>
              <p className={cx('mt-2 text-xs tabular-nums', coin.output > 0 ? 'font-semibold text-touch-700' : 'text-ink-2')} role="status">
                {state(coin)} · {coin.acks} ACKs</p>
              <p className={cx('mt-1 text-xs', coin.available ? 'text-ink-3' : 'text-caution-ink')}>{coin.detail}</p>
            </div>
          ))}
        </div>
        {!coins.editable && <p className="mt-2 text-xs text-caution-ink">Finish the calibration capture before testing coins.</p>}
        {request.error && <p className="mt-2 text-xs text-alarm-ink" role="alert">{request.error}</p>}
        <p className="mt-3 text-xs text-ink-3">ACKs confirm the board received each command; feel each coin to confirm it vibrates. A test turns automatic guidance off.</p>
      </>}
    </Plate>
  )
}
