import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { Button } from '../../ui/Button'
import { cameraControlUrl, readCameraResponse, type CameraSources } from './cameraSources'

/** A compact select for the slim bar under the camera. */
const SELECT_CLASS =
  'h-7 min-w-44 max-w-full flex-initial rounded-sm border border-rule bg-plate px-2 text-xs text-ink ' +
  'transition-[border-color,box-shadow] duration-150 hover:border-rule-strong/60 ' +
  'focus:border-ink focus:shadow-[0_0_0_3px_var(--touch-200)] focus-visible:outline-none ' +
  'disabled:cursor-not-allowed disabled:text-ink-3 disabled:hover:border-rule'

function IconRefresh() {
  return (
    <svg width={16} height={16} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M13.25 8a5.25 5.25 0 1 1-1.54-3.71" />
      <path d="M13.25 2.5v2.75H10.5" />
    </svg>
  )
}

/** One slim row: label, select, refresh, status note. Wraps the note below on narrow screens. */
function CameraBar({ id, children }: { id: string; children: ReactNode }) {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1">
      <label htmlFor={id} className="text-xs font-medium text-ink-3">Camera</label>
      {children}
    </div>
  )
}

function OfflineCameraSelector() {
  const id = useId()
  const noteId = useId()
  return (
    <CameraBar id={id}>
      <select id={id} className={SELECT_CLASS} aria-describedby={noteId} disabled value="">
        <option value="">Live bridge only</option>
      </select>
      <p id={noteId} className="min-w-0 flex-1 basis-40 truncate text-xs text-ink-3">
        <Link to="/signals" className="font-medium text-ink-2 underline underline-offset-2">Connect to the live bridge in Signals</Link>
        {' '}to choose a camera
      </p>
    </CameraBar>
  )
}

function LiveCameraSelector({ url }: { url: string }) {
  const id = useId()
  const noteId = useId()
  const [status, setStatus] = useState<CameraSources | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(true)
  const actions = useRef({ refresh: () => {}, select: (_id: string) => {} })

  useEffect(() => {
    let stopped = false
    let sequence = 0
    let timer: ReturnType<typeof setTimeout>
    let controller: AbortController | null = null

    async function request(action: 'poll' | 'refresh' | 'select', cameraId?: string) {
      const seq = ++sequence
      clearTimeout(timer)
      controller?.abort()
      const current = new AbortController()
      controller = current
      const timeout = setTimeout(() => current.abort(), 8000)
      let delay = 2500
      if (action !== 'poll') setBusy(true)
      try {
        const selecting = action === 'select'
        const response = await fetch(cameraControlUrl(url, selecting ? 'source' : 'sources', action === 'refresh'), {
          method: selecting ? 'POST' : 'GET',
          headers: selecting ? { 'Content-Type': 'application/json' } : undefined,
          body: selecting ? JSON.stringify({ id: cameraId }) : undefined,
          signal: current.signal,
          cache: 'no-store',
        })
        const next = await readCameraResponse(response)
        if (stopped || sequence !== seq) return
        setStatus(next)
        setError(null)
        delay = next.pending_id ? 500 : 2500
      } catch (cause) {
        if (stopped || sequence !== seq) return
        setError(cause instanceof Error && cause.name !== 'TypeError' && cause.name !== 'AbortError'
          ? cause.message : 'Camera selection is unavailable. Check that vision is running, then Refresh.')
      } finally {
        clearTimeout(timeout)
        if (!stopped && sequence === seq) {
          setBusy(false)
          timer = setTimeout(() => void request('poll'), delay)
        }
      }
    }
    actions.current = {
      refresh: () => void request('refresh'),
      select: (cameraId) => void request('select', cameraId),
    }
    void request('refresh')
    return () => {
      stopped = true
      clearTimeout(timer)
      controller?.abort()
    }
  }, [url])

  const pending = status?.pending_id
  const missing = status && !status.sources.some((source) => source.id === status.active_id)
  const problem = error ?? status?.error
  const note = pending
    ? `Switching to ${status.sources.find((source) => source.id === pending)?.name ?? 'camera'}…`
    : problem ?? (status && !status.can_switch
      ? 'Camera switching is unavailable while video recording is active.'
      : status?.sources.length === 0 ? 'No cameras found. Connect a camera, then Refresh.'
      : status ? `Active: ${status.active_name}` : 'Finding available cameras…')

  return (
    <CameraBar id={id}>
      <select id={id} className={SELECT_CLASS} aria-describedby={noteId}
        value={status?.active_id ?? ''}
        disabled={busy || !!pending || !!error || !status?.can_switch || !status.sources.length}
        onChange={(event) => actions.current.select(event.target.value)}>
        {!status && <option value="">Finding cameras…</option>}
        {missing && <option value={status.active_id ?? ''} disabled>{status.active_name} (current)</option>}
        {status?.sources.map((source) => <option key={source.id} value={source.id}>{source.name} · {source.index}</option>)}
      </select>
      <Button variant="quiet" size="sm" icon={<IconRefresh />} disabled={busy || !!pending}
        onClick={() => actions.current.refresh()} aria-label="Refresh camera sources" title="Refresh camera sources" />
      <p id={noteId} role="status" title={note}
        className={`min-w-0 flex-1 basis-40 truncate text-xs ${problem ? 'text-alarm-ink' : 'text-ink-3'}`}>{note}</p>
    </CameraBar>
  )
}

export function CameraSourceSelector({ url, live }: { url: string; live: boolean }) {
  if (!live || !url) return <OfflineCameraSelector />
  return <LiveCameraSelector key={url} url={url} />
}
