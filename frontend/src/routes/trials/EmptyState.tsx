import { Link } from 'react-router'
import { Button } from '../../ui/Button'
import { Kbd } from '../../ui/Kbd'
import { Plate } from '../../ui/Plate'
import type { Origin } from './stats'

const LINK = 'font-medium text-ink underline underline-offset-2'

function HowToRun() {
  return (
    <ol className="mt-3 max-w-[64ch] list-decimal space-y-1.5 pl-5 text-base text-ink-2">
      <li>
        Open the console and turn haptics on or off with <Kbd>H</Kbd>. That choice is the trial's condition, and it is
        fixed while the trial runs.
      </li>
      <li>
        Press Start trial, or <Kbd>T</Kbd>.
      </li>
      <li>
        Press <Kbd>D</Kbd> each time the object is dropped and <Kbd>F</Kbd> each time it is deformed.
      </li>
      <li>
        Press <Kbd>T</Kbd> again to finish. The trial is saved in this browser and appears here.
      </li>
    </ol>
  )
}

/** Shown when no trial of any origin is saved. */
export function NoTrials({ onLoadSample }: { onLoadSample: () => void }) {
  return (
    <Plate title="No trials yet">
      <p className="max-w-[64ch] text-base text-ink-2">
        A trial is one timed attempt at the task, run either with haptic feedback or without it. Each one records how
        long it took, how many times the object was dropped or deformed, and the highest relative grip. This page sets
        the two conditions side by side.
      </p>
      <h3 className="mt-5 text-sm font-semibold text-ink">Run a trial</h3>
      <HowToRun />
      <p className="mt-3 text-base">
        <Link to="/" className={LINK}>
          Open the console
        </Link>
      </p>
      <h3 className="mt-6 text-sm font-semibold text-ink">Try the page first</h3>
      <p className="mt-2 max-w-[64ch] text-base text-ink-2">
        The sample set is twelve made-up trials. They are marked as sample data, kept apart from real trials, and can
        be removed with one button.
      </p>
      <div className="mt-3">
        <Button variant="solid" onClick={onLoadSample}>
          Load sample set
        </Button>
      </div>
    </Plate>
  )
}

const NONE: Record<Origin, { title: string; text: string }> = {
  live: {
    title: 'No live trials yet',
    text: 'Live trials are the ones run with the arm connected. They are the only trials that count as evidence.',
  },
  simulated: {
    title: 'No simulated trials',
    text: 'Trials run while the console is on the simulator are listed here, apart from live ones.',
  },
  sample: {
    title: 'The sample set is not loaded',
    text: 'The sample set is twelve made-up trials for trying this page. It is kept apart from real trials.',
  },
}

/** Shown when other origins have trials but the one on show has none. */
export function NoneOfOrigin({ origin, onLoadSample }: { origin: Origin; onLoadSample: () => void }) {
  const { title, text } = NONE[origin]
  return (
    <Plate title={title}>
      <p className="max-w-[64ch] text-base text-ink-2">{text}</p>
      {origin === 'sample' ? (
        <div className="mt-3">
          <Button onClick={onLoadSample}>Load sample set</Button>
        </div>
      ) : (
        <>
          <HowToRun />
          <p className="mt-3 text-base">
            <Link to="/" className={LINK}>
              Open the console
            </Link>
          </p>
        </>
      )}
    </Plate>
  )
}
