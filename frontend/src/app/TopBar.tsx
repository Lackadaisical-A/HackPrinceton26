import { useRef } from 'react'
import { NavLink, useLocation } from 'react-router'
import { clock } from '../lib/format'
import { alertsFor, useSession } from '../state/session'
import { useSettings } from '../state/settings'
import { AlertRow } from '../ui/AlertTag'
import { Button } from '../ui/Button'
import { Chip } from '../ui/Chip'
import { cx } from '../ui/cx'
import { IconKeyboard, IconMark, IconRecord, IconSpeaker, IconSpeakerOff, IconStop } from '../ui/icons'
import { Readout } from '../ui/Readout'
import { useIndicator } from '../ui/useIndicator'
import { switchToSimulator, toggleRecording, toggleSpeak } from './runtime'

const SCREENS = [
  { to: '/', label: 'Console', end: true },
  { to: '/trials', label: 'Trials', end: false },
  { to: '/signals', label: 'Signals', end: false },
]

/** Where the data is coming from. Simulated data is labelled here on every screen. */
function SourceStatus() {
  const link = useSession((s) => s.link)
  const alerts = useSession((s) => s.alerts)
  const linkAlerts = alertsFor(alerts, 'link')

  if (link.state === 'simulated')
    return (
      <Chip tone="sim-window">
        <span aria-hidden="true" className="live-dot text-window-ink-2" />
        Simulated data
      </Chip>
    )
  if (link.state === 'open')
    return (
      <Chip tone="window">
        <span aria-hidden="true" className="live-dot text-touch-300" />
        Live data
      </Chip>
    )
  if (link.state === 'connecting')
    return (
      <Chip tone="window">
        <span aria-hidden="true" className="rec-breathe inline-block size-2 rounded-full bg-window-ink-2" />
        Connecting to the bridge
      </Chip>
    )
  if (link.state === 'retrying') {
    return (
      <span className="flex flex-wrap items-center gap-2">
        <AlertRow alerts={linkAlerts} onWindow />
        <Button size="sm" variant="window" onClick={switchToSimulator}>
          Use simulated data
        </Button>
      </span>
    )
  }
  return (
    <span className="flex items-center gap-2">
      <Chip tone="window">Not connected</Chip>
      <NavLink to="/signals" className="focus-window text-xs font-semibold text-window-ink underline underline-offset-2">
        Choose a source
      </NavLink>
    </span>
  )
}

function RecordButton() {
  const recording = useSession((s) => s.recording)
  const idle = useSession((s) => s.link.state === 'idle')

  if (!recording) {
    return (
      <Button
        variant="window"
        onClick={toggleRecording}
        disabled={idle}
        hint="R"
        icon={<IconRecord className="text-alarm-window" />}
        title="Saves every telemetry message to a .jsonl file. Video is not recorded."
      >
        Record
      </Button>
    )
  }
  const startedAt = recording.startedAt
  return (
    <Button
      variant="window-alarm"
      onClick={toggleRecording}
      pressed
      hint="R"
      icon={<IconStop className="rec-breathe text-plate" />}
      aria-label="Stop recording and save the file"
    >
      <Readout read={() => clock((Date.now() - startedAt) / 1000)} hz={2} width={4} />
      Stop
    </Button>
  )
}

export function TopBar({ onHelp }: { onHelp: () => void }) {
  const speak = useSettings((s) => s.speak)
  const nav = useRef<HTMLElement | null>(null)
  const { pathname } = useLocation()
  const marker = useIndicator(nav, '[aria-current="page"]', pathname)

  return (
    <header className="sticky top-0 z-40 border-b border-window-ink/10 bg-window/95 text-window-ink shadow-[0_8px_24px_-16px_rgb(28_11_33/0.8)] backdrop-blur-md">
      <div className="mx-auto flex max-w-[1680px] flex-wrap items-center gap-x-5 gap-y-1 px-3 md:px-4">
        <div className="flex items-center gap-2 py-2.5">
          <IconMark size={28} className="transition-transform duration-500 ease-spring hover:scale-110" />
          <span className="text-lg font-extrabold tracking-[0.2em] text-window-ink">PAUL</span>
        </div>

        <nav ref={nav} aria-label="Screens" className="relative isolate flex items-center gap-0.5 self-stretch">
          {SCREENS.map((screen) => (
            <NavLink
              key={screen.to}
              to={screen.to}
              end={screen.end}
              className={({ isActive }) =>
                cx(
                  'focus-window relative flex h-8 items-center rounded-full px-3.5 text-sm font-semibold transition-colors duration-150',
                  isActive ? 'text-window' : 'text-window-ink-2 hover:bg-window-ink/8 hover:text-window-ink',
                )
              }
            >
              {screen.label}
            </NavLink>
          ))}
          {/* A light pill behind the current screen that glides between screens. */}
          <span
            aria-hidden="true"
            className="pointer-events-none absolute top-[calc(50%-1rem)] left-0 -z-10 h-8 rounded-full bg-window-ink shadow-[0_2px_10px_-2px_rgb(237_150_215/0.45)]"
            style={marker}
          />
        </nav>

        <div className="ml-auto flex flex-wrap items-center justify-end gap-2 py-1.5">
          <SourceStatus />
          <Button
            variant="window-quiet"
            pressed={speak}
            onClick={toggleSpeak}
            icon={speak ? <IconSpeaker /> : <IconSpeakerOff />}
            aria-label="Read captions aloud"
            title={speak ? 'Captions are read aloud (S)' : 'Captions are not read aloud (S)'}
          />
          <RecordButton />
          <Button
            variant="window-quiet"
            onClick={onHelp}
            icon={<IconKeyboard />}
            aria-label="Keyboard shortcuts"
            title="Keyboard shortcuts (?)"
            className="max-md:hidden"
          />
        </div>
      </div>
    </header>
  )
}
