import { lazy, Suspense, type CSSProperties, type ReactNode } from 'react'
import { bus } from '../../telemetry/bus'
import { PageHeader } from '../../ui/PageHeader'
import { CalibrationPanel } from '../console/CalibrationPanel'
import { ResponseTuning } from '../console/ResponseTuning'
import { RobotControlPanel } from '../console/RobotControlPanel'
import { RobotTuningPanel } from '../console/RobotTuningPanel'
import { usePolled } from '../signals/usePolled'
import { ArmSettingsPanel } from './ArmSettingsPanel'
import { CoinBench } from './CoinBench'
import { HumanArmView } from './HumanArmView'
import { JointDirections } from './JointDirections'
import { WearableHealth } from './WearableHealth'

const ArmModel = lazy(() => import('../console/ArmModel'))

function Step({ n, title, lead, children }: { n: number; title: string; lead: string; children: ReactNode }) {
  return (
    <section aria-labelledby={`setup-step-${n}`} className="rise-in" style={{ '--i': n } as CSSProperties}>
      <div className="mb-2 flex items-baseline gap-3">
        <span aria-hidden="true" className="num flex size-7 shrink-0 items-center justify-center rounded-full bg-ink text-sm font-bold text-plate">{n}</span>
        <div>
          <h2 id={`setup-step-${n}`} className="text-lg font-bold text-ink">{title}</h2>
          <p className="text-sm text-ink-2">{lead}</p>
        </div>
      </div>
      <div className="grid items-start gap-3 md:gap-4 lg:grid-cols-2">{children}</div>
    </section>
  )
}

/**
 * Calibrate and start the robot from the browser, in the order the desktop Wearable Lab asks for it.
 * Every control here drives the same Wearable Lab process as its desktop windows; both stay in step.
 */
export default function SetupPage() {
  const [potInput] = usePolled(() => bus.latest('robot')?.control?.input === 'potentiometer')
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Setup">Calibrate the wearable and start the SO-101, step by step. Esc or STOP / hold stops the robot from any screen.</PageHeader>
      {potInput && <p className="rounded-md border border-caution/40 bg-caution/10 px-3 py-2 text-sm text-caution-ink">
        The potentiometer controller is running. This page sets up the IMU wearable; start it with .\demo.ps1 -Controller imu.</p>}
      <Step n={1} title="Check the sensors" lead="All three IMUs live, both boards connected, joystick centred. Buzz each coin to feel it.">
        <div className="@container"><WearableHealth /></div>
        <div className="@container"><CoinBench /></div>
      </Step>
      <Step n={2} title="Measure your arm" lead="Optional. Enter your arm lengths and where each sensor sits; saving clears calibration.">
        <div className="@container lg:col-span-2"><ArmSettingsPanel /></div>
      </Step>
      <Step n={3} title="Calibrate" lead="Capture pose A then B, then the elbow, shoulder and pan ranges. After a restart, recenter in pose A.">
        <div className="@container"><CalibrationPanel /></div>
        <div className="@container"><HumanArmView /></div>
      </Step>
      <Step n={4} title="Verify the robot preview" lead="Move your arm: the ghost shows where Align & follow will take the robot. Reverse any joint that moves the wrong way.">
        <div className="@container">
          <Suspense fallback={<div className="flex h-80 items-center justify-center rounded-md bg-well text-sm text-ink-2" role="status">Loading SO-101 viewer…</div>}>
            <ArmModel />
          </Suspense>
        </div>
        <div className="@container"><JointDirections /></div>
      </Step>
      <Step n={5} title="Tune how it follows" lead="Pick a feel from more control to more speed, and set software load stops. Changes last this session.">
        <div className="@container"><ResponseTuning /></div>
        <div className="@container"><RobotTuningPanel /></div>
      </Step>
      <Step n={6} title="Run" lead="Hold your arm still, then Align & follow. Enable the claw separately.">
        <div className="@container lg:col-span-2"><RobotControlPanel /></div>
      </Step>
    </div>
  )
}
