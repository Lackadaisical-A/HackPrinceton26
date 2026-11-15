import { stopRobot } from '../../app/runtime'
import { JOINTS } from '../../config'
import type { PhysicalStatus, RobotMode } from '../../telemetry/contracts'
import { Button } from '../../ui/Button'
import { cx } from '../../ui/cx'
import { IconStop } from '../../ui/icons'
import { Plate } from '../../ui/Plate'
import { ReconnectRobot } from './ReconnectRobot'
import { enableHint } from './robotControl'
import { useControllerRequest } from './useControllerRequest'
import { labUnavailable, useLabStatus } from './useLabStatus'

const MODES: Record<RobotMode, { label: string; className: string }> = {
  off: { label: 'Arm off', className: 'bg-well text-ink-2' },
  paused: { label: 'Paused', className: 'bg-caution/15 text-caution-ink' },
  aligning: { label: 'Aligning', className: 'bg-caution/15 text-caution-ink' },
  live: { label: 'Live', className: 'bg-touch-100 text-touch-700' },
  replay: { label: 'Replay', className: 'bg-touch-100 text-touch-700' },
}

const fixed = (value: number | null, digits = 1, unit = '') => (value === null ? '—' : `${value > 0 && unit === '°' ? '+' : ''}${value.toFixed(digits)}${unit}`)

function JointTable({ physical }: { physical: PhysicalStatus }) {
  return (
    <details className="mt-3 rounded-md border border-rule">
      <summary className="cursor-pointer px-3 py-2 text-sm font-semibold">Joint table</summary>
      <div className="overflow-x-auto px-3 pb-3">
        <table className="w-full min-w-[34rem] text-left text-xs tabular-nums">
          <thead className="text-ink-3">
            <tr>
              <th scope="col" className="py-1 pr-2 font-medium">Joint</th>
              <th scope="col" className="py-1 pr-2 font-medium">Actual</th>
              <th scope="col" className="py-1 pr-2 font-medium">Preview</th>
              <th scope="col" className="py-1 pr-2 font-medium">Sent raw</th>
              <th scope="col" className="py-1 pr-2 font-medium">Gap</th>
              <th scope="col" className="py-1 pr-2 font-medium">Limiting</th>
              <th scope="col" className="py-1 pr-2 font-medium">Load</th>
              <th scope="col" className="py-1 font-medium">Torque</th>
            </tr>
          </thead>
          <tbody>
            {physical.joints.map((joint, i) => (
              <tr key={JOINTS[i].key} className="border-t border-rule">
                <th scope="row" className="py-1 pr-2 font-medium text-ink">{JOINTS[i].label}</th>
                <td className="py-1 pr-2">{joint.actual_deg !== null ? fixed(joint.actual_deg, 1, '°') : fixed(joint.position, 0)}</td>
                <td className="py-1 pr-2">{fixed(joint.preview_deg, 1, '°')}</td>
                <td className="py-1 pr-2">{fixed(joint.sent, 0)}</td>
                <td className="py-1 pr-2">{fixed(joint.gap_deg, 1, '°')}</td>
                <td className={cx('py-1 pr-2', joint.flag && 'font-semibold text-caution-ink')}>{joint.flag || '—'}</td>
                <td className="py-1 pr-2">{fixed(joint.load, 0)}</td>
                <td className="py-1">{joint.torque ? 'On' : 'Off'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-xs text-ink-3">Actual and Preview are URDF angles (the claw shows raw counts). Sent is the servo command.
          RATE = speed cap, LEAD = motor lags, RANGE = request beyond saved travel.</p>
      </div>
    </details>
  )
}

/**
 * Start and stop the physical SO-101: the browser's version of Wearable Lab's robot controls. Enable
 * buttons only ask; the serial worker checks fresh input, load and limits, and its answer is shown.
 */
export function RobotControlPanel() {
  const lab = useLabStatus()
  const { message, available } = lab
  const physical = available ? message?.physical ?? null : null
  const enable = useControllerRequest((m) => m.control_request, { needApplied: true })
  const absolute = physical?.mapping_mode !== 'relative'
  const armLabel = absolute ? 'Align & follow' : 'Enable arm · relative'

  const act = (action: 'enable_arm' | 'enable_claw') => {
    enable.send((current, id) => {
      const p = current.physical
      if (!p || !(action === 'enable_arm' ? p.can_enable_arm : p.can_enable_claw)) return null
      return { type: 'control_robot', action, request_id: id, session: current.session, revision: current.revision }
    }, action === 'enable_arm' ? armLabel : 'Enable claw')
  }

  const mode = physical ? MODES[physical.mode] : null
  const unavailable = labUnavailable(lab)
  return (
    <Plate title="Robot control" note={physical?.port ? `SO-101 · ${physical.port}` : 'SO-101'}
      aside={mode && <span className={cx('rounded-full px-2 py-1 text-xs font-semibold', mode.className)} role="status">
        {mode.label}{physical?.claw_enabled ? ' · claw on' : ''}</span>}>
      {unavailable && <p className="mb-3 text-sm text-ink-2">{unavailable}</p>}
      {available && !physical && <p className="mb-3 text-sm text-ink-2">Wearable Lab is running without a robot. You can calibrate and test the
        sensors; restart it with a robot port to drive the arm.</p>}
      {available && <ReconnectRobot message={message} />}
      {physical && message && <>
        <div className="rounded-md bg-well px-3 py-2.5 text-sm" role="status" aria-live="polite">
          <p className="font-medium text-ink">{physical.message || 'Waiting for the robot.'}</p>
          {physical.alignment_progress && <p className="mt-1 text-ink-2">{physical.alignment_progress}</p>}
          {physical.input_status && physical.input_status !== 'Wearable input LIVE' && <p className="mt-1 text-ink-2">Input: {physical.input_status}</p>}
          {physical.limited.length > 0 && <p className="mt-1 text-caution-ink">Saved joint range reached: {physical.limited.map((name) => name.replace('_', ' ')).join(', ')}</p>}
          <p className="mt-1 text-xs text-ink-3">{physical.fresh ? 'Readings live' : 'Readings stale'} · {fixed(physical.loop_hz, 0)} Hz · reads {fixed(physical.read_ms)} ms</p>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="solid" disabled={!physical.can_enable_arm || enable.pending} onClick={() => act('enable_arm')}>{armLabel}</Button>
          <Button disabled={!physical.can_enable_claw || enable.pending} onClick={() => act('enable_claw')}>Enable claw</Button>
          <Button variant="danger" icon={<IconStop />} hint="Esc" onClick={() => stopRobot('robot control')}>STOP / hold</Button>
        </div>
        {!physical.can_enable_arm && <p className="mt-2 text-xs text-ink-2">{enableHint(message, physical)}</p>}
        {enable.pending && <p className="mt-2 text-xs text-ink-2" role="status">Waiting for the robot to confirm…</p>}
        {enable.error && <p className="mt-2 text-xs text-alarm-ink" role="alert">{enable.error}</p>}
        <JointTable physical={physical} />
        <p className="mt-3 text-xs text-ink-3">
          {absolute
            ? `Align & follow moves the arm at ${fixed(physical.alignment_deg_s, 0)}°/s to the preview pose, then follows you live. Hold still until Live. `
            : 'The arm follows changes from where it is now. '}
          Enable the claw separately, with the joystick centred. STOP or Esc holds position with torque on; closing this page does not stop the arm.
        </p>
      </>}
      {!physical && available && <div className="mt-1"><Button variant="danger" icon={<IconStop />} hint="Esc" onClick={() => stopRobot('robot control')}>STOP coins</Button></div>}
    </Plate>
  )
}
