import type { WearableStatus } from '../../telemetry/contracts'
import { cx } from '../../ui/cx'
import { Plate } from '../../ui/Plate'
import { labUnavailable, useLabStatus } from '../console/useLabStatus'

const IMUS = ['Bicep · S3 / 0x4A', 'Forearm · S3 / 0x4B', 'Hand · WROOM']
const BOARDS: Record<string, string> = { wroom: 'WROOM (hand, joystick, palm coins)', s3: 'S3 (bicep, forearm, forearm coins)' }

function Joystick({ joystick, center }: { joystick: WearableStatus['joystick']; center: WearableStatus['joystick_center'] }) {
  const live = !!joystick?.fresh
  const x = live ? 60 + 50 * joystick.nx : 60
  const y = live ? 60 - 50 * joystick.ny : 60
  const neutral = live && joystick.nx === 0 && joystick.ny === 0
  return (
    <div className="flex flex-wrap items-center gap-4">
      <svg viewBox="0 0 120 120" className="size-28 shrink-0 rounded-md bg-well" role="img"
        aria-label={live ? `Joystick ${neutral ? 'centred' : 'deflected'}${joystick.pressed ? ', pressed' : ''}` : 'Joystick reading stale'}>
        <rect x="10" y="10" width="100" height="100" fill="none" stroke="var(--rule-strong)" />
        <path d="M60 10 V110 M10 60 H110" stroke="var(--rule)" />
        {center.deadzone !== null && <rect x={60 - 50 * center.deadzone} y={60 - 50 * center.deadzone} width={100 * center.deadzone}
          height={100 * center.deadzone} fill="var(--touch-100)" stroke="var(--touch-200)" />}
        {live && <circle cx={x} cy={y} r="6" fill={joystick.pressed ? 'var(--caution)' : 'var(--touch-600)'} />}
      </svg>
      <div className="text-xs tabular-nums text-ink-2">
        {live ? <>
          <p>X {joystick.x} → {joystick.nx >= 0 ? '+' : ''}{joystick.nx.toFixed(2)}</p>
          <p>Y {joystick.y} → {joystick.ny >= 0 ? '+' : ''}{joystick.ny.toFixed(2)}</p>
          <p className="mt-1 font-semibold text-ink">{neutral ? 'Centred' : 'Deflected'} · button {joystick.pressed ? 'pressed' : 'released'}</p>
        </> : <p className="text-caution-ink">No fresh joystick reading. Check the WROOM firmware and D2 / D4 / D5.</p>}
        <p className="mt-1 text-ink-3">Centre X {center.x ?? '—'} / Y {center.y ?? '—'} · {center.deadzone !== null ? Math.round(center.deadzone * 100) : '—'}% dead zone</p>
      </div>
    </div>
  )
}

/** The wearable's sensors and boards, as Wearable Lab's sidebar shows them. */
export function WearableHealth() {
  const lab = useLabStatus(10)
  const wearable = lab.available ? lab.message?.wearable ?? null : null
  return (
    <Plate title="Sensors" note="3 IMUs · 2 boards · joystick">
      {!wearable ? <p className="text-sm text-ink-2">{labUnavailable(lab) || 'This Wearable Lab does not report sensor health. Update it and restart.'}</p> : <>
        <ul className="grid gap-1.5 text-sm" aria-label="IMUs">
          {wearable.imus.map((imu, i) => (
            <li key={IMUS[i]} className="flex items-center justify-between gap-3">
              <span className="flex items-center gap-2"><span aria-hidden="true" className={cx('size-2 rounded-full', imu.live ? 'bg-touch-600' : 'bg-alarm')} />{IMUS[i]}</span>
              <span className={cx('text-xs tabular-nums', imu.live ? 'text-ink-2' : 'font-semibold text-alarm-ink')}>
                {imu.live ? `${imu.hz?.toFixed(0) ?? '—'} Hz · ${imu.age_ms ?? '—'} ms ago` : 'Stale: no fresh orientation'}
              </span>
            </li>
          ))}
        </ul>
        <ul className="mt-3 grid gap-1 border-t border-rule pt-3 text-xs text-ink-2" aria-label="Boards">
          {wearable.boards.map((board) => (
            <li key={board.name}>
              <span className="font-semibold text-ink">{BOARDS[board.name] ?? board.name}</span> · {board.port || 'no port'} ·{' '}
              <span className={board.connected ? '' : 'font-semibold text-alarm-ink'}>{board.connected ? 'connected' : 'offline'}</span> · DRV2605L {board.driver}
              {!board.connected && board.message && <span className="block text-ink-3">{board.message}</span>}
            </li>
          ))}
        </ul>
        <div className="mt-3 border-t border-rule pt-3">
          <h3 className="mb-2 text-sm font-semibold">Hand joystick</h3>
          <Joystick joystick={wearable.joystick} center={wearable.joystick_center} />
        </div>
      </>}
    </Plate>
  )
}
