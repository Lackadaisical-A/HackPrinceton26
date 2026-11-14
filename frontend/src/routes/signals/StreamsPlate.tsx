import { LIMITS } from '../../config'
import { bus } from '../../telemetry/bus'
import type { Topic } from '../../telemetry/contracts'
import { TOPIC_INFO, TOPICS } from '../../telemetry/contracts'
import { AlertTag } from '../../ui/AlertTag'
import { cx } from '../../ui/cx'
import { Plate } from '../../ui/Plate'
import { Readout } from '../../ui/Readout'
import { formatAge, formatRate, NONE } from './helpers'
import { StandingChip } from './StandingChip'
import { usePolled } from './usePolled'

/** The facts about a stream that change rarely, so they can go through React. */
interface Flags {
  flow: 'none' | 'arriving' | 'stopped'
  rejected: number
  lastError: string | null
}

function readFlags(topic: Topic): Flags {
  const state = bus.state(topic)
  const flow = state.count === 0 ? 'none' : bus.ageMs(topic) > LIMITS.streamSilentMs ? 'stopped' : 'arriving'
  return { flow, rejected: state.rejected, lastError: state.lastError }
}

const sameFlags = (a: Flags, b: Flags): boolean =>
  a.flow === b.flow && a.rejected === b.rejected && a.lastError === b.lastError

const HEAD = 'px-3 py-2 text-xs font-medium whitespace-nowrap text-ink-3 first:pl-0 last:pr-0'
const CELL = 'px-3 py-2.5 align-middle first:pl-0 last:pr-0'
const LIVE = 'text-right text-ink'

function StreamRow({ topic }: { topic: Topic }) {
  const info = TOPIC_INFO[topic]
  const [flags] = usePolled(() => readFlags(topic), 2, sameFlags)

  return (
    <tr className="border-t border-rule">
      <th scope="row" className={cx(CELL, 'text-left font-medium whitespace-nowrap text-ink')}>
        {info.title}
        <span className="num block text-xs font-normal text-ink-2">{topic}</span>
      </th>
      <td className={CELL}>
        <StandingChip standing={info.standing} />
      </td>
      <td className={cx(CELL, 'whitespace-nowrap')}>
        {flags.flow === 'stopped' ? (
          <AlertTag level="caution" text="Stopped" />
        ) : (
          <span className={flags.flow === 'arriving' ? 'text-ink' : 'text-ink-2'}>
            {flags.flow === 'arriving' ? 'Arriving' : 'Nothing yet'}
          </span>
        )}
      </td>
      <td className={cx(CELL, 'num text-right whitespace-nowrap text-ink-2')}>{info.rateHz}&nbsp;Hz</td>
      <td className={cx(CELL, 'text-right')}>
        <Readout
          read={() => formatRate(bus.state(topic).hz, bus.state(topic).count)}
          hz={4}
          width={8}
          className={LIVE}
        />
      </td>
      <td className={cx(CELL, 'text-right')}>
        <Readout read={() => formatAge(bus.ageMs(topic))} hz={4} width={7} className={LIVE} />
      </td>
      <td className={cx(CELL, 'text-right')}>
        <Readout
          read={() => (bus.state(topic).count === 0 ? NONE : bus.state(topic).count.toLocaleString('en-US'))}
          hz={4}
          width={7}
          className={LIVE}
        />
      </td>
      <td className={cx(CELL, 'num text-right', flags.rejected > 0 ? 'font-semibold text-ink' : 'text-ink-2')}>
        {flags.rejected.toLocaleString('en-US')}
      </td>
      <td className={cx(CELL, 'min-w-48 text-ink')}>{flags.lastError ?? <span className="text-ink-2">{NONE}</span>}</td>
    </tr>
  )
}

export function StreamsPlate({ className }: { className?: string }) {
  return (
    <Plate title="Streams" note="what is arriving right now" className={className}>
      {/* The table scrolls inside the plate. It is focusable so the keyboard can scroll it too. */}
      <div role="region" aria-label="Streams table" tabIndex={0} className="relative overflow-x-auto">
        <table className="w-full min-w-[52rem] border-collapse text-sm">
          <caption className="sr-only">
            One row for each telemetry stream: its standing, whether it is arriving, its rate, and any rejected frames.
          </caption>
          <thead>
            <tr>
              <th scope="col" className={cx(HEAD, 'text-left')}>
                Stream
              </th>
              <th scope="col" className={cx(HEAD, 'text-left')}>
                Standing
              </th>
              <th scope="col" className={cx(HEAD, 'text-left')}>
                State
              </th>
              <th scope="col" className={cx(HEAD, 'text-right')}>
                Nominal rate
              </th>
              <th scope="col" className={cx(HEAD, 'text-right')}>
                Measured rate
              </th>
              <th scope="col" className={cx(HEAD, 'text-right')}>
                Last message
              </th>
              <th scope="col" className={cx(HEAD, 'text-right')}>
                Accepted
              </th>
              <th scope="col" className={cx(HEAD, 'text-right')}>
                Rejected
              </th>
              <th scope="col" className={cx(HEAD, 'text-left')}>
                Last rejection
              </th>
            </tr>
          </thead>
          <tbody>
            {TOPICS.map((topic) => (
              <StreamRow key={topic} topic={topic} />
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 max-w-[72ch] text-xs text-ink-3">
        A frame is rejected when it does not match its format. Rejected frames are counted here and dropped. They never
        reach the console.
      </p>
    </Plate>
  )
}
