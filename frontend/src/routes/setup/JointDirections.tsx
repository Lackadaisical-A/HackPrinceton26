import { useSession } from '../../state/session'
import { ROBOT_JOINTS, type RobotJoint } from '../../telemetry/contracts'
import { Plate } from '../../ui/Plate'
import { Switch } from '../../ui/Switch'
import { useControllerRequest } from '../console/useControllerRequest'
import { labUnavailable, useLabStatus } from '../console/useLabStatus'

/** Wearable Lab's names, matching its URDF preview window. */
const LABELS: Record<RobotJoint, string> = {
  shoulder_pan: 'Base turn', shoulder_lift: 'Shoulder lift', elbow_flex: 'Elbow bend',
  wrist_flex: 'Wrist bend', wrist_roll: 'Wrist roll', gripper: 'Claw',
}

/**
 * Per-joint "Reverse direction", formerly only in the desktop URDF preview. It changes how your
 * movement maps to the preview and the physical arm, so the controller refuses it while moving.
 */
export function JointDirections() {
  const lab = useLabStatus()
  const trial = useSession((s) => s.trial !== null)
  const directions = lab.available ? lab.message?.directions ?? null : null
  const request = useControllerRequest((m) => m.directions?.request)
  const editable = !!directions?.editable && !trial && !request.pending

  const flip = (joint: RobotJoint, reverse: boolean) => {
    request.send((current, id) => current.directions?.editable
      ? { type: 'set_joint_direction', joint, reverse, request_id: id, session: current.session, revision: current.revision } : null,
    `${LABELS[joint]} direction: ${reverse ? 'reversed' : 'normal'}`)
  }

  return (
    <Plate title="Joint directions" note="this session">
      {!directions ? <p className="text-sm text-ink-2">{labUnavailable(lab) || 'This Wearable Lab does not report joint directions. Update it and restart.'}</p> : <>
        <p className="mb-3 text-sm text-ink-2">If a joint on the preview moves the opposite way to you, reverse it here. The robot follows the same mapping.</p>
        <div className="grid gap-2 @min-[28rem]:grid-cols-2">
          {ROBOT_JOINTS.map((joint) => (
            <Switch key={joint} label={`${LABELS[joint]} reversed`} checked={directions.reverse[joint]} disabled={!editable}
              onChange={(reverse) => flip(joint, reverse)} className="justify-between rounded-md border border-rule px-3 py-2" />
          ))}
        </div>
        {!directions.editable && <p className="mt-2 text-xs text-caution-ink">STOP / hold the robot (and finish any capture) before changing a direction.</p>}
        {request.pending && <p className="mt-2 text-xs text-ink-2" role="status">Waiting for Wearable Lab…</p>}
        {request.error && <p className="mt-2 text-xs text-alarm-ink" role="alert">{request.error}</p>}
        <p className="mt-3 text-xs text-ink-3">A change pauses following; use Align &amp; follow again afterwards. Directions reset when Wearable Lab restarts.</p>
      </>}
    </Plate>
  )
}
