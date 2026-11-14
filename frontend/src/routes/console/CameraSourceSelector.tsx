import { useEffect, useId, useRef, useState } from 'react'
import { Button } from '../../ui/Button'
import { INPUT_CLASS } from '../../ui/Field'
import { cameraControlUrl, readCameraResponse, type CameraSources } from './cameraSources'

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
    <div className="mb-2 rounded-md border border-rule bg-plate p-3">
      <div className="flex items-end gap-2">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <label htmlFor={id} className="text-sm font-medium text-ink">Camera source</label>
          <select id={id} className={INPUT_CLASS} aria-describedby={noteId}
            value={status?.active_id ?? ''}
            disabled={busy || !!pending || !!error || !status?.can_switch || !status.sources.length}
            onChange={(event) => actions.current.select(event.target.value)}>
            {!status && <option value="">Finding cameras…</option>}
            {missing && <option value={status.active_id ?? ''} disabled>{status.active_name} (current)</option>}
            {status?.sources.map((source) => <option key={source.id} value={source.id}>{source.name} · {source.index}</option>)}
          </select>
        </div>
        <Button disabled={busy || !!pending} onClick={() => actions.current.refresh()} aria-label="Refresh camera sources">
          Refresh
        </Button>
      </div>
      <p id={noteId} role="status" className={`mt-1.5 text-xs ${problem ? 'text-alarm-ink' : 'text-ink-3'}`}>{note}</p>
    </div>
  )
}

export function CameraSourceSelector({ url, live }: { url: string; live: boolean }) {
  if (!live || !url) return (
    <p className="mb-2 text-xs text-ink-3">Camera source: connect to Live bridge in Signals to choose a camera.</p>
  )
  return <LiveCameraSelector key={url} url={url} />
}
