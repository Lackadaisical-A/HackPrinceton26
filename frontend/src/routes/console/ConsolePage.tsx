import type { CSSProperties } from 'react'
import { ArmbandPanel } from './ArmbandPanel'
import { AssistPanel } from './AssistPanel'
import { ArmPanel } from './ArmPanel'
import { CameraView } from './CameraView'
import { EventLog } from './EventLog'
import { TargetPanel } from './TargetPanel'
import { TouchLink } from './TouchLink'
import { TrialBar } from './TrialBar'
import { ControlInputPanel } from './ControlInputPanel'
import { CalibrationPanel } from './CalibrationPanel'
import { RobotControlPanel } from './RobotControlPanel'
import { ReplayPanel } from './ReplayPanel'
import { RobotTuningPanel } from './RobotTuningPanel'
import { bus } from '../../telemetry/bus'
import { usePolled } from '../signals/usePolled'

/**
 * The operator's screen. Two columns on a wide display: what the arm sees and what is being
 * done on the left, what the arm feels and where it is on the right. On a narrow display the
 * columns dissolve and the panels follow one order, with the trial controls kept near the top.
 */
export default function ConsolePage() {
  const [potInput] = usePolled(() => bus.latest('robot')?.control?.input === 'potentiometer')
  return (
    <>
      <h1 className="sr-only">Console</h1>
      <div className="grid gap-3 md:gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(23rem,1fr)] lg:items-start">
        <div className="contents lg:flex lg:min-w-0 lg:flex-col lg:gap-4">
          <div className="rise-in order-1 min-w-0 lg:order-none" style={{ '--i': 0 } as CSSProperties}>
            <CameraView />
          </div>
          <div className="rise-in order-2 min-w-0 lg:order-none" style={{ '--i': 1 } as CSSProperties}>
            <TargetPanel />
            <div className="mt-3 md:mt-4">
              <AssistPanel />
            </div>
            <div className="mt-3 md:mt-4">
              <ReplayPanel />
            </div>
          </div>
          <div className="rise-in order-3 min-w-0 lg:order-none" style={{ '--i': 2 } as CSSProperties}>
            <TrialBar />
          </div>
          <div className="rise-in order-7 min-w-0 lg:order-none" style={{ '--i': 3 } as CSSProperties}>
            <EventLog />
          </div>
        </div>
        <div className="contents lg:flex lg:min-w-0 lg:flex-col lg:gap-4">
          <div className="rise-in order-4 min-w-0 lg:order-none" style={{ '--i': 1 } as CSSProperties}>
            <TouchLink />
          </div>
          <div className="rise-in order-5 min-w-0 lg:order-none" style={{ '--i': 2 } as CSSProperties}>
            {!potInput && <div className="@container mb-3 md:mb-4"><RobotControlPanel /></div>}
            {!potInput && <div className="@container mb-3 md:mb-4"><CalibrationPanel /></div>}
            {!potInput && <div className="@container mb-3 md:mb-4"><RobotTuningPanel /></div>}
            <ArmPanel />
          </div>
          <div className="rise-in order-6 min-w-0 lg:order-none" style={{ '--i': 3 } as CSSProperties}>
            {potInput ? <ControlInputPanel /> : <ArmbandPanel />}
          </div>
        </div>
      </div>
    </>
  )
}
