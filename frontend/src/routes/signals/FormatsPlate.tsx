import { LIMITS } from '../../config'
import { COMMAND_EXAMPLES, TOPIC_INFO, TOPICS } from '../../telemetry/contracts'
import { Plate } from '../../ui/Plate'
import { StandingChip } from './StandingChip'

const FRAME = 'num rounded-md bg-well p-3 text-xs break-all whitespace-pre-wrap text-ink'

export function FormatsPlate({ className }: { className?: string }) {
  return (
    <Plate title="Message formats" note="what this app expects on the wire" className={className}>
      <div className="max-w-[72ch] text-sm text-ink-2">
        <p>
          Every message is one text frame: the topic, a space, then compact JSON. A contract format is defined by a
          module that exists today (computer_vision). A proposed format is this frontend’s suggestion: nothing publishes
          it yet, and it can change once the team agrees on it.
        </p>
        <p className="mt-2">Two rules apply to anything that reads vision messages:</p>
        <ul className="mt-1 list-disc pl-5">
          <li>
            Act only on messages whose <span className="num">status</span> is <span className="num">"ok"</span>.
          </li>
          <li>
            Treat silence, or a <span className="num">t</span> older than {LIMITS.visionStaleS} s, as no guidance.
          </li>
        </ul>
      </div>

      <ul className="mt-4">
        {TOPICS.map((topic) => {
          const info = TOPIC_INFO[topic]
          return (
            <li key={topic} className="border-t border-rule py-3.5">
              <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                <h3 className="text-sm font-semibold text-ink">{info.title}</h3>
                <span className="num text-xs text-ink-2">{topic}</span>
                <StandingChip standing={info.standing} />
              </div>
              <dl className="mt-1.5 flex flex-wrap gap-x-6 gap-y-0.5 text-sm">
                <div className="flex gap-1.5">
                  <dt className="text-ink-3">From</dt>
                  <dd className="text-ink">{info.origin}</dd>
                </div>
                <div className="flex gap-1.5">
                  <dt className="text-ink-3">Nominal rate</dt>
                  <dd className="num text-ink">{info.rateHz}&nbsp;Hz</dd>
                </div>
              </dl>
              <pre className={`${FRAME} mt-2`}>{info.example}</pre>
            </li>
          )
        })}

        <li className="border-t border-rule pt-3.5">
          <h3 className="text-sm font-semibold text-ink">Commands</h3>
          <p className="mt-1 max-w-[72ch] text-sm text-ink-2">
            Sent back to the bridge the same way, on the <span className="num">command</span> topic.
          </p>
          <ul className="mt-2 flex flex-col gap-2">
            {COMMAND_EXAMPLES.map((example) => (
              <li key={example.frame} className="flex flex-col items-start gap-1.5">
                <StandingChip standing={example.standing} />
                <pre className={`${FRAME} w-full`}>{example.frame}</pre>
              </li>
            ))}
          </ul>
        </li>
      </ul>
    </Plate>
  )
}
