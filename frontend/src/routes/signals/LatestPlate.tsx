import { useEffect, useRef, useState } from 'react'
import { schedule } from '../../render/scheduler'
import { bus } from '../../telemetry/bus'
import type { Topic } from '../../telemetry/contracts'
import { TOPICS } from '../../telemetry/contracts'
import { Button } from '../../ui/Button'
import { Chip } from '../../ui/Chip'
import { IconPause, IconPlay } from '../../ui/icons'
import { Plate } from '../../ui/Plate'
import { Segmented } from '../../ui/Segmented'
import { pretty, TOPIC_LABELS } from './helpers'
import { usePolled } from './usePolled'

const TOPIC_OPTIONS = TOPICS.map((topic) => ({ value: topic, label: TOPIC_LABELS[topic] }))

/** The newest message on one stream. Written straight to the DOM, so it costs no React renders. */
function Message({ topic, paused }: { topic: Topic; paused: boolean }) {
  const pre = useRef<HTMLPreElement | null>(null)
  const [hasData] = usePolled(() => bus.state(topic).count > 0, 2)

  useEffect(() => {
    const el = pre.current
    if (!el || paused) return
    let shown: unknown = null
    const paint = (): void => {
      const latest = bus.latest(topic)
      if (latest === null || latest === shown) return
      shown = latest
      el.textContent = pretty(latest)
    }
    paint()
    return schedule(paint, { el, hz: 4 })
  }, [topic, paused, hasData])

  if (!hasData) {
    return (
      <p className="flex h-[21rem] items-center justify-center rounded-md bg-well px-4 text-center text-sm text-ink">
        Nothing has arrived on this stream since the source was connected.
      </p>
    )
  }
  return (
    <pre
      ref={pre}
      tabIndex={0}
      aria-label={`Latest ${TOPIC_LABELS[topic]} message`}
      className="num h-[21rem] overflow-auto rounded-md bg-well p-3 text-xs text-ink"
    />
  )
}

export function LatestPlate() {
  const [topic, setTopic] = useState<Topic>('vision')
  const [paused, setPaused] = useState(false)

  return (
    <Plate
      title="Latest message"
      note="refreshed four times a second"
      aside={
        <>
          {paused && <Chip>Paused</Chip>}
          <Button size="sm" onClick={() => setPaused(!paused)} icon={paused ? <IconPlay size={14} /> : <IconPause size={14} />}>
            {paused ? 'Resume' : 'Pause'}
          </Button>
        </>
      }
    >
      <Segmented
        label="Stream"
        value={topic}
        options={TOPIC_OPTIONS}
        onChange={(next) => {
          setTopic(next)
          setPaused(false) // a frozen message from another stream would be misleading
        }}
        className="mb-3"
      />
      {/* Keyed by stream, so switching never leaves the previous stream's message on screen. */}
      <Message key={topic} topic={topic} paused={paused} />
    </Plate>
  )
}
