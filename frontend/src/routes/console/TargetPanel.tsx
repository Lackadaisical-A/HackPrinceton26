import { useMemo } from 'react'
import { selectTarget } from '../../app/runtime'
import { ANY_TARGET, describeLearned, targetOptions } from '../../lib/targets'
import { useSession } from '../../state/session'
import { Plate } from '../../ui/Plate'
import { Segmented } from '../../ui/Segmented'

/**
 * The object vision looks for and guides the gripper to: any object, one of the detector's classes, or any name
 * the operator taught. Vision keeps to the one object it locked; choosing here again (the same name too) makes it
 * let go and pick afresh at the crosshair.
 */
export function TargetPanel() {
  const target = useSession((s) => s.targetLabel)
  const labels = useSession((s) => s.labels)
  const idle = useSession((s) => s.link.state === 'idle')
  const options = useMemo(() => targetOptions(labels), [labels])
  const learned = describeLearned(labels)

  return (
    <Plate title="Target" note={labels?.busy ? `learning ${labels.busy}…` : undefined}>
      <div className="flex flex-col items-start gap-2">
        <Segmented
          label="Target object"
          value={target ?? ANY_TARGET}
          options={options}
          onChange={(value) => selectTarget(value === ANY_TARGET ? null : value)}
          disabled={idle}
        />
        <p className="text-xs text-ink-2">
          {idle
            ? 'Connect a data source to choose a target.'
            : 'Vision stays on the one object it locked. Choose again to move to another.'}
          {!idle && learned && <> Learned: {learned}.</>}
        </p>
      </div>
    </Plate>
  )
}
