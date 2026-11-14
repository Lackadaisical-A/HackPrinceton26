import { JOINTS, LIMITS } from '../../config'
import { bus } from '../../telemetry/bus'
import { Plate } from '../../ui/Plate'
import { usePolled } from '../signals/usePolled'

/** The test input is separate from arm feedback, so IMUs can replace it later. */
export function ControlInputPanel() {
  const [sample] = usePolled(() => {
    const robot = bus.latest('robot')
    return { robot, stale: bus.ageMs('robot') >= LIMITS.streamSilentMs
      || (robot !== null && Date.now() / 1000 - robot.t >= LIMITS.streamSilentMs / 1000) }
  }, 4, (a, b) => a.robot === b.robot && a.stale === b.stale)
  const control = sample.robot?.control
  if (!control) return null
  const status = sample.stale ? 'Controller or input data lost'
    : !control.input_fresh ? 'Waiting for fresh potentiometer data'
    : control.feedback === 'preview' ? 'Dry run · robot not connected'
    : control.enabled ? 'Movement enabled' : 'Movement paused · torque off'

  return (
    <Plate title="Potentiometer test" note={status}>
      <p className="mb-3 text-sm text-ink-2">
        In the controller terminal: <kbd>E</kbd> enable/pause, <kbd>T</kbd> torque off,
        {' '}<kbd>C</kbd> recenter relative control, <kbd>Q</kbd> quit.
      </p>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        {JOINTS.map((joint, i) => (
          <div key={joint.key} className="flex justify-between gap-2">
            <dt className="text-ink-2">{joint.label} target</dt>
            <dd className="num">{!sample.stale && control.input_fresh && control.target
              ? `${control.target[i].toFixed(1)}${joint.unit}` : '—'}</dd>
          </div>
        ))}
        {sample.robot?.slider && <div className="col-span-2 flex justify-between gap-2">
          <dt>Slider {control.feedback === 'preview' ? 'preview' : 'position'}</dt>
          <dd className="num">{sample.stale ? '—' : `${sample.robot.slider.pos.toFixed(1)}%`}</dd>
        </div>}
      </dl>
      <p className="mt-3 text-xs text-ink-3">Temporary pot input. IMUs will replace it for the final control system.</p>
    </Plate>
  )
}
