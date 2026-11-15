import { useId, useState } from 'react'
import { useSession } from '../../state/session'
import { SENSOR_AXES, SENSOR_ROLES, type ArmSettingsStatus, type SensorAxis } from '../../telemetry/contracts'
import { Button } from '../../ui/Button'
import { INPUT_CLASS } from '../../ui/Field'
import { Plate } from '../../ui/Plate'
import { useControllerRequest } from '../console/useControllerRequest'
import { labUnavailable, useLabStatus } from '../console/useLabStatus'
import { ARM_FIELDS, draftFrom, manualAxes, parseDraft, ROLE_LABELS, sameSettings, type ArmDraft } from './armSettings'

function ArmSettingsForm({ status, trial }: { status: ArmSettingsStatus; trial: boolean }) {
  const id = useId()
  const request = useControllerRequest((m) => m.arm_settings?.request)
  const [draft, setDraft] = useState<ArmDraft | null>(null)
  const [confirming, setConfirming] = useState(false)
  // After the controller confirms, show its values until the operator edits again.
  const shown = !request.done && draft ? draft : draftFrom(status.values)
  const parsed = parseDraft(shown)
  const changed = !!parsed.settings && !sameSettings(parsed.settings, status.values)
  const editable = status.editable && !trial && !request.pending

  const edit = (next: ArmDraft) => {
    if (request.done || request.error) request.clear()
    setConfirming(false)
    setDraft(next)
  }
  const save = () => {
    const settings = parsed.settings
    if (!settings) return
    request.send((current, requestId) => current.arm_settings?.editable
      ? { type: 'configure_arm', settings, request_id: requestId, session: current.session, revision: current.revision } : null,
    'Arm dimensions and sensor axes')
    setConfirming(false)
  }

  return <>
    <div className="grid items-end gap-3 @min-[34rem]:grid-cols-2 @min-[48rem]:grid-cols-3">
      {ARM_FIELDS.map(({ key, label, range }) => (
        <div key={key} className="flex min-w-0 flex-col gap-1">
          <label htmlFor={`${id}-${key}`} className="text-sm font-medium">{label}</label>
          <input id={`${id}-${key}`} type="number" inputMode="decimal" min={range[0]} max={range[1]} step="any" value={shown[key]}
            disabled={!editable} onChange={(event) => edit({ ...shown, [key]: event.target.value })} className={`${INPUT_CLASS} num`} />
        </div>
      ))}
      {SENSOR_ROLES.map((role) => (
        <div key={role} className="flex min-w-0 flex-col gap-1">
          <label htmlFor={`${id}-${role}`} className="text-sm font-medium">{ROLE_LABELS[role]}: board axis toward fingers in pose A</label>
          <select id={`${id}-${role}`} value={shown.axes[role]} disabled={!editable} className={INPUT_CLASS}
            onChange={(event) => edit({ ...shown, axes: { ...shown.axes, [role]: event.target.value as SensorAxis } })}>
            {SENSOR_AXES.map((axis) => <option key={axis} value={axis}>{axis}</option>)}
          </select>
        </div>
      ))}
    </div>
    <p className="mt-3 text-xs text-ink-3">
      Auto learns any mounting from poses A and B. {parsed.settings && manualAxes(parsed.settings) ? 'With all three axes set by hand, pose A alone calibrates. ' : ''}
      Sensor positions run along the limb centreline. Saved on the Wearable Lab computer only.</p>
    {parsed.error && <p className="mt-2 text-xs text-alarm-ink" role="alert">{parsed.error}</p>}
    <div className="mt-3 flex flex-wrap items-center gap-2">
      {!confirming
        ? <Button variant="solid" disabled={!editable || !changed} onClick={() => setConfirming(true)}>Save and recalibrate…</Button>
        : <>
          <span className="text-sm font-medium text-caution-ink" role="alert">This clears the current calibration and holds the arm. Save?</span>
          <Button variant="solid" disabled={!editable || !changed} onClick={save}>Save and recalibrate</Button>
          <Button variant="quiet" onClick={() => setConfirming(false)}>Cancel</Button>
        </>}
      <Button variant="quiet" disabled={!draft || request.pending} onClick={() => { setDraft(null); setConfirming(false); request.clear() }}>Discard edits</Button>
    </div>
    {request.pending && <p className="mt-2 text-xs text-ink-2" role="status">Waiting for Wearable Lab…</p>}
    {request.done && <p className="mt-2 text-xs text-ink-2" role="status">{request.detail}</p>}
    {request.error && <p className="mt-2 text-xs text-alarm-ink" role="alert">{request.error}</p>}
    {!status.editable && <p className="mt-2 text-xs text-caution-ink">Finish the capture, or close the desktop Dimensions dialog, to edit.</p>}
    {trial && <p className="mt-2 text-xs text-caution-ink">Finish the current trial before changing calibration settings.</p>}
  </>
}

/** Arm lengths and sensor mounting: Wearable Lab's Dimensions / sensor axes dialog. */
export function ArmSettingsPanel() {
  const lab = useLabStatus()
  const trial = useSession((s) => s.trial !== null)
  const status = lab.available ? lab.message?.arm_settings ?? null : null
  return (
    <Plate title="Arm dimensions and sensor axes" note="clears calibration when saved">
      {/* Keyed by controller instance: a restart discards drafts made against the old one. */}
      {status ? <ArmSettingsForm key={lab.message?.session} status={status} trial={trial} />
        : <p className="text-sm text-ink-2">{labUnavailable(lab) || 'This Wearable Lab does not report arm settings. Update it and restart.'}</p>}
    </Plate>
  )
}
