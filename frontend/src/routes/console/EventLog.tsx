import { timeOfDay } from '../../lib/format'
import { useSession, type EventKind } from '../../state/session'
import { IconCaution, IconWarning } from '../../ui/icons'
import { Plate } from '../../ui/Plate'

const KIND_WORD: Record<EventKind, string> = {
  caption: 'Spoken',
  phase: 'Phase',
  alert: 'Alert',
  trial: 'Trial',
  record: 'Recording',
  link: 'Link',
  command: 'Command',
}

/** The store keeps more; this many is plenty to scroll back through during a run. */
const SHOWN = 60

/** Everything that has happened, newest first: what was spoken, phase changes, alerts and actions. */
export function EventLog() {
  const events = useSession((s) => s.events)

  return (
    <Plate title="Event log" note={events.length > 0 ? 'newest first' : undefined}>
      {events.length === 0 ? (
        <p className="text-sm text-ink-2">Nothing has happened yet. Events appear here as the task runs.</p>
      ) : (
        <ol tabIndex={0} aria-label="Events, newest first" className="-mr-2 max-h-60 overflow-y-auto pr-2">
          {events.slice(0, SHOWN).map((event) => (
            <li
              key={event.id}
              className="row-in grid grid-cols-[4.75rem_minmax(0,1fr)] gap-x-3 border-t border-rule py-1.5 first:border-t-0 sm:grid-cols-[4.5rem_5.25rem_minmax(0,1fr)]"
            >
              <span className="flex flex-col sm:contents">
                <time className="num text-xs leading-5 text-ink-2">{timeOfDay(event.at)}</time>
                <span className="text-xs leading-5 text-ink-2">{KIND_WORD[event.kind]}</span>
              </span>
              <span className="text-sm text-ink">
                {event.level === 'warning' && <IconWarning size={14} className="mr-1.5 inline-block align-[-2px] text-alarm-ink" />}
                {event.level === 'caution' && <IconCaution size={14} className="mr-1.5 inline-block align-[-2px] text-caution-ink" />}
                {event.text}
              </span>
            </li>
          ))}
        </ol>
      )}
    </Plate>
  )
}
