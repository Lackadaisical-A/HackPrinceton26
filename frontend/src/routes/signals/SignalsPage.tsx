import { PageHeader } from '../../ui/PageHeader'
import { CommandPlate } from './CommandPlate'
import { FormatsPlate } from './FormatsPlate'
import { LatestPlate } from './LatestPlate'
import { SimulatorTestsPlate } from './SimulatorTestsPlate'
import { SourcePlate } from './SourcePlate'
import { StreamsPlate } from './StreamsPlate'

/** The integration screen: where data comes from, what is arriving, and the formats expected. */
export default function SignalsPage() {
  return (
    <>
      <PageHeader title="Signals">
        Choose where the data comes from, check what is arriving, and look up the message formats this app expects.
      </PageHeader>
      <div className="stagger grid grid-cols-1 items-start gap-3 md:gap-4 min-[1100px]:grid-cols-2">
        <SourcePlate />
        <div className="grid min-w-0 grid-cols-1 gap-3 md:gap-4">
          <SimulatorTestsPlate />
          <CommandPlate />
        </div>
        <StreamsPlate className="min-[1100px]:col-span-2" />
        <LatestPlate />
        <FormatsPlate />
      </div>
    </>
  )
}
