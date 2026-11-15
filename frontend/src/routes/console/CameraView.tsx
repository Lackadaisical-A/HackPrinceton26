import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { getSimulator, sendLabelCommand } from '../../app/runtime'
import { LIMITS } from '../../config'
import { sentenceCase, signed } from '../../lib/format'
import type { Draw } from '../../render/useCanvas'
import { useCanvas } from '../../render/useCanvas'
import { alertsFor, useSession, type VisionState } from '../../state/session'
import { useSettings } from '../../state/settings'
import { bus } from '../../telemetry/bus'
import type { BBox, DistanceHint } from '../../telemetry/contracts'
import { nameProblem, normalizeName } from '../../telemetry/labels'
import type { SimFrame } from '../../telemetry/sources/scenario'
import { classifyVision } from '../../telemetry/vision'
import { AlertRow } from '../../ui/AlertTag'
import { Chip } from '../../ui/Chip'
import { cx } from '../../ui/cx'
import { IconWarning } from '../../ui/icons'
import { Readout } from '../../ui/Readout'
import { useCameraStream } from './cameraStream'
import { CameraSourceSelector } from './CameraSourceSelector'
import { drawOverlay, drawTagDraft, newOverlayMemory, type DragBox } from './overlay'
import { drawScene } from './scene'
import { TagBar, TagLayer, type TagBarProps } from './TagBox'

const LOST: Partial<Record<VisionState, { title: string; body: string }>> = {
  degraded: { title: 'Vision feed lost', body: 'Guidance is hidden until fresh frames arrive.' },
  silent: { title: 'No vision data', body: 'Messages have stopped. Guidance is hidden until they resume.' },
  late: { title: 'Vision data delayed', body: 'Frames are more than half a second old. Guidance is hidden.' },
}

const DISTANCE_WORDS: Record<DistanceHint, string> = { far: 'Far', near: 'Near', in_reach: 'In reach' }

/** How long a spoken sentence stays on screen. */
const CAPTION_MS = 6000

function statusLine(state: VisionState, label: string | null, aligned: boolean): string {
  switch (state) {
    case 'ok':
      return `${label ? sentenceCase(label) : 'Target'} locked${aligned ? ', aligned' : ''}`
    case 'no_target':
      return 'No target in view'
    case 'low_confidence':
      return 'Not sure yet'
    case 'waiting':
      return 'Waiting for vision data'
    default:
      return 'Guidance hidden'
  }
}

function readRate(): string {
  const hz = bus.state('vision').hz
  return hz > 0 ? `${hz.toFixed(0).padStart(2)} fps` : ' – fps'
}

/** Capture-to-screen age of the newest frame. Assumes this computer and the publisher agree on the time. */
function readAge(): string {
  const latest = bus.latest('vision')
  if (!latest || bus.ageMs('vision') > 2000) return '– ms'
  const age = Math.max(0, Date.now() - latest.t * 1000)
  if (age > 9999) return 'over 10 s'
  const text = `${age.toFixed(0).padStart(3)} ms`
  return age >= LIMITS.ageWarningMs ? `${text}, late` : age >= LIMITS.ageCautionMs ? `${text}, slow` : text
}

function readOffset(): string {
  const latest = bus.latest('vision')
  const usable =
    latest?.guidance && classifyVision(latest, bus.ageMs('vision'), Date.now() / 1000) === 'ok' ? latest.guidance : null
  return usable ? `x ${signed(usable.dx)}  y ${signed(usable.dy)}` : 'x  –     y  –   '
}

function Caption() {
  const caption = useSession((s) => s.caption)
  // The timestamp of the caption whose time on screen has run out.
  const [expired, setExpired] = useState(() => (caption && Date.now() - caption.at >= CAPTION_MS ? caption.at : 0))

  useEffect(() => {
    if (!caption) return
    const left = Math.max(0, CAPTION_MS - (Date.now() - caption.at))
    const timer = setTimeout(() => setExpired(caption.at), left)
    return () => clearTimeout(timer)
  }, [caption])

  const visible = caption !== null && expired !== caption.at
  return (
    <p
      aria-live="polite"
      className={cx(
        'max-w-full self-center rounded-sm bg-window/80 px-3 py-1.5 text-center text-sm font-medium text-window-ink transition-opacity duration-200 sm:text-base',
        visible ? 'opacity-100' : 'opacity-0',
      )}
    >
      {visible ? caption.text : ' '}
    </p>
  )
}

export function CameraView() {
  const linkState = useSession((s) => s.link.state)
  const vision = useSession((s) => s.vision)
  const alerts = useSession((s) => s.alerts)
  const cameraUrl = useSettings((s) => s.cameraUrl)
  const simulated = linkState === 'simulated'
  const live = linkState === 'open' || linkState === 'connecting' || linkState === 'retrying'

  const [aspect, setAspect] = useState(16 / 9)
  const image = useRef<HTMLImageElement>(null)
  const stream = useCameraStream(cameraUrl, live, image, (width, height) => setAspect(width / height))

  const memory = useRef(newOverlayMemory())
  const sceneFrame = useRef<SimFrame | null>(null)

  // Teaching a name from a drawn box. Only live vision can learn one; the simulator has no detector.
  const [tagText, setTagText] = useState('')
  const [drawing, setDrawing] = useState(false)
  const [tagNote, setTagNote] = useState<TagBarProps['note']>(null)
  const draft = useRef<DragBox | null>(null)
  const tagName = normalizeName(tagText)
  const tagUnavailable =
    linkState === 'open'
      ? null
      : simulated
        ? 'Tagging needs live vision: the simulator has no detector.'
        : 'Connect to the live bridge to tag objects.'
  const isDrawing = drawing && tagUnavailable === null

  const stopDrawing = useCallback((note: TagBarProps['note']) => {
    draft.current = null
    setDrawing(false)
    setTagNote(note)
  }, [])

  const startDrawing = (): void => {
    const problem = nameProblem(tagName)
    if (problem) {
      setTagNote({ text: problem, problem: true })
      return
    }
    setDrawing(true)
    setTagNote({ text: `Drag a box around ${tagName} on the picture. Esc cancels.`, problem: false })
  }

  const onBox = (box: BBox | null): void => {
    if (!box) {
      setTagNote({ text: 'That was a click, not a box. Drag from one corner of the object to the other.', problem: true })
      return
    }
    const sent = sendLabelCommand({ type: 'tag_visual', name: tagName, bbox: box })
    stopDrawing(
      sent
        ? { text: `Sent. Vision answers in the caption: “Learning ${tagName}.”, then “… is ready.”`, problem: false }
        : { text: 'Not sent: the connection dropped.', problem: true },
    )
  }

  useEffect(() => {
    if (!isDrawing) return
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      stopDrawing(null)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [isDrawing, stopDrawing])

  const draw = useCallback<Draw>(
    (ctx, w, h, nowMs, dtMs) => {
      const latest = bus.latest('vision')
      const state = classifyVision(latest, bus.ageMs('vision', nowMs), Date.now() / 1000)
      const simulator = simulated ? getSimulator() : null
      if (simulator) {
        // A stalled camera keeps showing its last picture, so hold the scene still too.
        if (!simulator.visionStalled || !sceneFrame.current) sceneFrame.current = simulator.frame
        drawScene(ctx, w, h, sceneFrame.current, state in LOST)
      } else {
        ctx.clearRect(0, 0, w, h)
      }
      drawOverlay(ctx, w, h, latest, state === 'ok', memory.current, dtMs)
      if (draft.current) drawTagDraft(ctx, w, h, draft.current, tagName)
    },
    [simulated, tagName],
  )
  const canvas = useCanvas(draw)

  const lost = LOST[vision.state]
  const cameraAlerts = alertsFor(alerts, 'camera').filter((a) => a.key !== 'vision-lost')
  const showStream = stream.src !== null && !stream.failed
  const usable = vision.state === 'ok'

  return (
    <>
    <section
      aria-label="Wrist camera"
      className="relative w-full overflow-hidden rounded-lg bg-window text-window-ink max-sm:!aspect-[4/3]"
      style={{ aspectRatio: simulated ? 16 / 9 : aspect }}
    >
      {/* Stays mounted while it fails, hidden, so the next attempt can load into it. */}
      {stream.src !== null && (
        <img
          ref={image}
          src={stream.src}
          alt="Live view from the wrist camera"
          onLoad={stream.onLoad}
          onError={stream.onError}
          className={cx(
            'absolute inset-0 size-full object-fill',
            stream.failed && 'invisible',
            lost && 'brightness-50 grayscale',
          )}
        />
      )}
      <canvas ref={canvas} className="absolute inset-0 size-full" aria-hidden="true" />

      {/* Top: what the task is doing, and how fresh the picture is. */}
      <div className="absolute inset-x-0 top-0 flex items-start justify-between gap-2 p-2 sm:p-3">
        <div className="flex min-w-0 flex-col items-start gap-1.5">
          <div className="rounded-sm bg-window/70 px-2.5 py-1.5">
            <p className="text-lg leading-tight font-semibold">
              {sentenceCase(vision.phase)}
              {lost && <span className="ml-2 text-xs font-normal text-window-ink-2">last known</span>}
            </p>
            <p className="text-sm text-window-ink-2">{statusLine(vision.state, vision.label, vision.aligned)}</p>
          </div>
          <AlertRow alerts={cameraAlerts} onWindow />
        </div>
        <dl className="grid shrink-0 grid-cols-[auto_auto] gap-x-2 rounded-sm bg-window/70 px-2.5 py-1.5 text-xs text-window-ink-2">
          <dt>Vision</dt>
          <dd className="text-right text-window-ink">
            <Readout read={readRate} hz={4} />
          </dd>
          <dt>Frame age</dt>
          <dd className="text-right text-window-ink">
            <Readout read={readAge} hz={4} />
          </dd>
        </dl>
      </div>

      {/* Centre: the reason there is no guidance, when there is none. */}
      {lost && linkState !== 'idle' && (
        <div className="absolute inset-0 grid place-items-center p-4">
          <div role="alert" className="max-w-sm rounded-md border border-alarm-window bg-window/90 px-4 py-3">
            <p className="flex items-center gap-2 text-lg font-semibold">
              <IconWarning size={20} className="shrink-0 text-alarm-window" />
              {lost.title}
            </p>
            <p className="mt-1 text-sm text-window-ink-2">{lost.body}</p>
          </div>
        </div>
      )}
      {linkState === 'idle' && (
        <div className="absolute inset-0 grid place-items-center p-4">
          <div className="max-w-sm rounded-md bg-window/90 px-4 py-3 text-center">
            <p className="text-lg font-semibold">Not connected</p>
            <p className="mt-1 text-sm text-window-ink-2">
              Nothing is sending data.{' '}
              <Link to="/signals" className="focus-window font-medium text-window-ink underline underline-offset-2">
                Choose a data source
              </Link>
            </p>
          </div>
        </div>
      )}
      {live && !showStream && !lost && (
        <p className="absolute inset-x-0 top-1/2 mx-auto mt-16 max-w-xs px-4 text-center text-xs text-window-ink-2">
          {stream.failed
            ? `The camera stream at ${cameraUrl} did not load. Trying again every few seconds. `
            : 'No camera stream is set. '}
          The overlay is drawn from vision data alone.{' '}
          <Link to="/signals" className="focus-window font-medium text-window-ink underline underline-offset-2">
            {stream.failed ? 'Check the stream address' : 'Set the stream address'}
          </Link>
        </p>
      )}

      {/* Bottom: the spoken sentence, the numbers behind it, and the simulation label. */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-col gap-2 p-2 sm:p-3">
        <Caption />
        <div className="flex items-end justify-between gap-2">
          <dl className="grid grid-cols-[auto_auto] gap-x-2 rounded-sm bg-window/70 px-2.5 py-1.5 text-xs text-window-ink-2">
            <dt>Distance</dt>
            <dd className="text-window-ink">{usable && vision.distance ? DISTANCE_WORDS[vision.distance] : '–'}</dd>
            <dt>Offset</dt>
            <dd className="text-window-ink">
              <Readout read={readOffset} hz={8} />
            </dd>
          </dl>
          {simulated && <Chip tone="window">Simulated scene</Chip>}
        </div>
      </div>

      {isDrawing && (
        <TagLayer
          onDrag={(box) => {
            draft.current = box
          }}
          onBox={onBox}
        />
      )}
    </section>
    <CameraSourceSelector url={cameraUrl} live={live} />
    <TagBar
      name={tagText}
      onName={(text) => {
        setTagText(text)
        if (tagNote?.problem) setTagNote(null)
      }}
      drawing={isDrawing}
      onDraw={startDrawing}
      onCancel={() => stopDrawing(null)}
      unavailable={tagUnavailable}
      note={tagNote}
    />
    </>
  )
}
