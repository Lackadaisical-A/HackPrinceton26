import { getSimulator } from '../../app/runtime'
import { LIMITS } from '../../config'
import { useSession } from '../../state/session'
import { Button } from '../../ui/Button'
import { Plate } from '../../ui/Plate'
import { Switch } from '../../ui/Switch'
import { usePolled } from './usePolled'

function Tests() {
  const [stalled, refreshStalled] = usePolled(() => getSimulator()?.visionStalled ?? false, 2)
  const [held] = usePolled(() => getSimulator()?.frame.held ?? false, 4)

  return (
    <div className="flex flex-col divide-y divide-rule">
      <div className="pb-4">
        <Switch
          label="Stall the vision feed"
          checked={stalled}
          onChange={(next) => {
            getSimulator()?.setVisionStalled(next)
            refreshStalled()
          }}
        />
        <p className="mt-2 max-w-[62ch] text-sm text-ink-2">
          Stops fresh vision frames, as a stalled camera would. On the console, “Vision feed lost” should appear within
          about half a second and the guidance should disappear. Turn this off to bring the feed back.
        </p>
      </div>

      <div className="pt-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <Button onClick={() => getSimulator()?.squeezeNow()} disabled={!held}>
            Squeeze now
          </Button>
          <span className="text-sm text-ink-2">
            {held ? 'The gripper is holding something.' : 'Nothing is held right now. Wait for the next grasp.'}
          </span>
        </div>
        <p className="mt-2 max-w-[62ch] text-sm text-ink-2">
          Grips too hard for a moment. On the console, the grip trace should rise past the {LIMITS.gripCaution} limit
          line and “Grip near sensor limit” should appear. It only works while the gripper is holding something.
        </p>
      </div>
    </div>
  )
}

export function SimulatorTestsPlate() {
  const simulated = useSession((s) => s.link.state === 'simulated')
  return (
    <Plate title="Simulator tests" note="check how the console shows a fault">
      {simulated ? (
        <Tests />
      ) : (
        <p className="max-w-[62ch] text-sm text-ink-2">
          These tests exist only for simulated data. To use them, choose the simulator as the data source.
        </p>
      )}
    </Plate>
  )
}
