import { Link, useRouteError } from 'react-router'
import { Button } from '../ui/Button'

/** Shown when a screen throws while rendering or loading. */
export function RouteError() {
  const error = useRouteError()
  const detail = error instanceof Error ? error.message : typeof error === 'string' ? error : null

  return (
    <div className="mx-auto max-w-xl p-6">
      <h1 className="text-xl font-semibold text-ink">This screen stopped working</h1>
      <p className="mt-2 text-base text-ink-2">
        Reload the page to start again. Saved trials and settings are kept. A recording in progress is lost.
      </p>
      {detail && <pre className="num mt-4 overflow-x-auto rounded-md bg-well p-3 text-xs text-ink">{detail}</pre>}
      <div className="mt-5">
        <Button variant="solid" onClick={() => window.location.reload()}>
          Reload
        </Button>
      </div>
    </div>
  )
}

export function NotFound() {
  return (
    <div className="mx-auto max-w-xl py-10">
      <h1 className="text-xl font-semibold text-ink">No screen at this address</h1>
      <p className="mt-2 text-base text-ink-2">
        The address may be mistyped.{' '}
        <Link to="/" className="font-medium text-ink underline underline-offset-2">
          Open the console
        </Link>
      </p>
    </div>
  )
}
