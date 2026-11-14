import { ArmbandPanel } from './ArmbandPanel'
import { ArmPanel } from './ArmPanel'
import { CameraView } from './CameraView'
import { EventLog } from './EventLog'
import { TaskPanel } from './TaskPanel'
import { TouchLink } from './TouchLink'
import { TrialBar } from './TrialBar'

/**
 * The operator's screen. Two columns on a wide display: what the arm sees and what is being
 * done on the left, what the arm feels and where it is on the right. On a narrow display the
 * columns dissolve and the panels follow one order, with the trial controls kept near the top.
 */
export default function ConsolePage() {
  return (
    <>
      <h1 className="sr-only">Console</h1>
      <div className="grid gap-3 md:gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(23rem,1fr)] lg:items-start">
        <div className="contents lg:flex lg:min-w-0 lg:flex-col lg:gap-4">
          <div className="order-1 min-w-0 lg:order-none">
            <CameraView />
          </div>
          <div className="order-2 min-w-0 lg:order-none">
            <TaskPanel />
          </div>
          <div className="order-3 min-w-0 lg:order-none">
            <TrialBar />
          </div>
          <div className="order-7 min-w-0 lg:order-none">
            <EventLog />
          </div>
        </div>
        <div className="contents lg:flex lg:min-w-0 lg:flex-col lg:gap-4">
          <div className="order-4 min-w-0 lg:order-none">
            <TouchLink />
          </div>
          <div className="order-5 min-w-0 lg:order-none">
            <ArmPanel />
          </div>
          <div className="order-6 min-w-0 lg:order-none">
            <ArmbandPanel />
          </div>
        </div>
      </div>
    </>
  )
}
