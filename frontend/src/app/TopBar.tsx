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
      <Chip tone="sim">
        <span aria-hidden="true" className="live-dot text-ink-2" />
        Simulated data
      </Chip>
    )
  if (link.state === 'open')
    return (
      <Chip>
        <span aria-hidden="true" className="live-dot text-touch-500" />
        Live data
      </Chip>
    )
  if (link.state === 'connecting')
    return (
      <Chip>
        <span aria-hidden="true" className="rec-breathe inline-block size-2 rounded-full bg-ink-3" />
        Connecting to the bridge
      </Chip>
    )
  if (link.state === 'retrying') {
    return (
      <span className="flex flex-wrap items-center gap-2">
        <AlertRow alerts={linkAlerts} />
        <Button size="sm" onClick={switchToSimulator}>
          Use simulated data
        </Button>
      </span>
    )
  }
  return (
    <span className="flex items-center gap-2">
      <Chip>Not connected</Chip>
      <NavLink to="/signals" className="text-xs font-medium text-ink underline underline-offset-2">
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
        onClick={toggleRecording}
        disabled={idle}
        hint="R"
        icon={<IconRecord className="text-alarm" />}
        title="Saves every telemetry message to a .jsonl file. Video is not recorded."
      >
        Record
      </Button>
    )
  }
  const startedAt = recording.startedAt
  return (
    <Button
      variant="solid"
      onClick={toggleRecording}
      pressed
      hint="R"
      icon={<IconStop className="rec-breathe text-alarm-window" />}
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
    <header className="border-b border-rule bg-panel">
      <div className="mx-auto flex max-w-[1680px] flex-wrap items-center gap-x-5 gap-y-1 px-3 md:px-4">
        <div className="flex items-center gap-2 py-2.5">
          <IconMark size={28} className="transition-transform duration-500 ease-spring hover:scale-110" />
          <span className="text-base font-bold tracking-[0.08em] text-ink">PAUL</span>
        </div>

        <nav ref={nav} aria-label="Screens" className="relative flex self-stretch">
          {SCREENS.map((screen) => (
            <NavLink
              key={screen.to}
              to={screen.to}
              end={screen.end}
              className={({ isActive }) =>
                cx(
                  'relative isolate flex items-center px-3 text-sm font-medium transition-colors duration-150',
                  // A soft wash on hover, inset from the bar's edges.
                  'before:absolute before:inset-x-1 before:inset-y-2 before:-z-10 before:rounded-sm before:bg-well before:opacity-0 before:transition-opacity before:duration-150 hover:before:opacity-100',
                  isActive ? 'text-ink' : 'text-ink-2 hover:text-ink',
                )
              }
            >
              {screen.label}
            </NavLink>
          ))}
          {/* The marker under the current screen sits on the bar's bottom rule and glides between screens. */}
          <span aria-hidden="true" className="pointer-events-none absolute -bottom-px left-0 flex h-0.5 justify-center" style={marker}>
            <span className="h-full w-[calc(100%-1.5rem)] rounded-full bg-ink" />
          </span>
        </nav>

        <div className="ml-auto flex flex-wrap items-center justify-end gap-2 py-1.5">
          <SourceStatus />
          <Button
            variant="quiet"
            pressed={speak}
            onClick={toggleSpeak}
            icon={speak ? <IconSpeaker /> : <IconSpeakerOff />}
            aria-label="Read captions aloud"
            title={speak ? 'Captions are read aloud (S)' : 'Captions are not read aloud (S)'}
            className="w-10 px-0 md:w-9"
          />
          <RecordButton />
          <Button
            variant="quiet"
            onClick={onHelp}
            icon={<IconKeyboard />}
            aria-label="Keyboard shortcuts"
            title="Keyboard shortcuts (?)"
            className="w-10 px-0 max-md:hidden md:w-9"
          />
        </div>
      </div>
    </header>
  )
}
