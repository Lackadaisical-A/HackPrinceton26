import { useState } from 'react'
import { connect, disconnect, switchToSimulator } from '../../app/runtime'
import { DEFAULT_CAMERA_URL, DEFAULT_WS_URL } from '../../config'
import { useSession } from '../../state/session'
import { useSettings } from '../../state/settings'
import type { SourceKind } from '../../telemetry/sources/types'
import { AlertTag } from '../../ui/AlertTag'
import { Button } from '../../ui/Button'
import { TextField } from '../../ui/Field'
import { Plate } from '../../ui/Plate'
import { Readout } from '../../ui/Readout'
import { Segmented } from '../../ui/Segmented'
import { checkBridgeAddress, checkCameraAddress } from './helpers'

const SOURCES: readonly { value: SourceKind; label: string }[] = [
  { value: 'simulator', label: 'Simulator' },
  { value: 'websocket', label: 'Live bridge' },
]

/** What is running right now, in words. */
function LinkStatus() {
  const link = useSession((s) => s.link)
  const url = useSettings((s) => s.wsUrl)
  const address = <span className="num break-all">{url}</span>

  if (link.state === 'retrying') {
    const retryAt = link.retryAt
    return (
      <div className="flex flex-col items-start gap-2">
        <AlertTag level="warning" text="Bridge not reachable" />
        <p role="status">{link.detail ?? <>No bridge is answering at {address}.</>}</p>
        {retryAt !== null && (
          <p>
            Trying again in{' '}
            <Readout read={() => String(Math.max(0, Math.ceil((retryAt - Date.now()) / 1000)))} hz={2} width={1} /> s.
          </p>
        )}
        <Button size="sm" onClick={switchToSimulator}>
          Use simulated data
        </Button>
      </div>
    )
  }

  return (
    <p role="status">
      {link.state === 'simulated' && 'The simulator is running. Every value in this app is simulated.'}
      {link.state === 'connecting' && <>Connecting to {address}.</>}
      {link.state === 'open' && <>Connected to {address}. The data is live.</>}
      {link.state === 'idle' && (link.detail ? `Not connected. ${link.detail}` : 'Not connected. No data is arriving.')}
    </p>
  )
}

export function SourcePlate() {
  const idle = useSession((s) => s.link.state === 'idle')
  const [kind, setKind] = useState<SourceKind>(() => useSettings.getState().source)
  const [wsUrl, setWsUrl] = useState(() => useSettings.getState().wsUrl)
  const [cameraUrl, setCameraUrl] = useState(() => useSettings.getState().cameraUrl)
  // Errors appear once a field has been left or the form submitted, not while first typing.
  const [checked, setChecked] = useState(false)

  const live = kind === 'websocket'
  const wsError = live ? checkBridgeAddress(wsUrl) : null
  const cameraError = live ? checkCameraAddress(cameraUrl) : null

  const apply = (): void => {
    setChecked(true)
    if (wsError || cameraError) return
    useSettings
      .getState()
      .update(live ? { source: kind, wsUrl: wsUrl.trim(), cameraUrl: cameraUrl.trim() } : { source: kind })
    connect()
  }

  return (
    <Plate title="Data source">
      <div className="rounded-md bg-well px-3 py-2.5 text-sm text-ink">
        <LinkStatus />
      </div>

      <form
        noValidate
        className="mt-4 flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault()
          apply()
        }}
      >
        <div className="flex flex-col items-start gap-1.5">
          <span className="text-sm font-medium text-ink" aria-hidden="true">
            Source
          </span>
          <Segmented label="Source" value={kind} options={SOURCES} onChange={setKind} />
        </div>

        {live ? (
          <>
            <TextField
              label="Bridge address"
              literal
              inputMode="url"
              placeholder={DEFAULT_WS_URL}
              value={wsUrl}
              onChange={(event) => setWsUrl(event.target.value)}
              onBlur={() => setChecked(true)}
              error={checked ? wsError : null}
              help="The WebSocket address of the ZMQ-to-WebSocket bridge."
            />
            <TextField
              label="Wrist camera stream (optional)"
              literal
              inputMode="url"
              placeholder={DEFAULT_CAMERA_URL}
              value={cameraUrl}
              onChange={(event) => setCameraUrl(event.target.value)}
              onBlur={() => setChecked(true)}
              error={checked ? cameraError : null}
              help="An MJPEG address. The vision module serves the camera it is watching at the address shown. With live data, the console shows this stream behind the guidance overlay. Without it, the overlay is drawn on a blank window."
            />
          </>
        ) : (
          <p className="max-w-[62ch] text-sm text-ink-2">
            The simulator plays a scripted pick and place on a loop, so every screen can be tried without hardware.
            Nothing it shows was measured.
          </p>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="solid">
            {live ? 'Connect' : 'Use simulated data'}
          </Button>
          <Button onClick={disconnect} disabled={idle}>
            Disconnect
          </Button>
        </div>
        <p className="max-w-[62ch] text-xs text-ink-3">
          Changing the source clears every stream. A running trial ends as aborted, and a recording in progress is
          saved first.
        </p>
      </form>

      <div className="mt-4 border-t border-rule pt-3 text-sm text-ink-2">
        <p className="max-w-[62ch]">Live data needs two programs running on this computer, each in its own terminal:</p>
        <ol className="mt-2 flex max-w-[62ch] list-decimal flex-col gap-1.5 pl-5">
          <li>
            Vision, in <span className="num">computer_vision/</span>, which watches the wrist camera and serves its
            picture: <span className="num break-all">uv run python -m paul_vision --source wrist</span>.
            Choose a connected device using <strong>Camera source</strong> above the Console's video.
            Use <strong>Refresh</strong> after plugging in a camera.
          </li>
          <li>
            The bridge, in <span className="num">bridge/</span>, which passes the data to this page and commands back:{' '}
            <span className="num break-all">uv run python -m paul_bridge</span>
          </li>
        </ol>
        <p className="mt-2 max-w-[62ch]">
          They can be started in either order, before or after connecting. This app never switches to simulated data
          on its own: if the bridge does not answer, it says so and keeps trying.
        </p>
      </div>
    </Plate>
  )
}
