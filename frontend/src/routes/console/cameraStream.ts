import { useEffect, useState, type RefObject, type SyntheticEvent } from 'react'
import { useSession } from '../../state/session'

/**
 * Keeps the camera picture connected.
 *
 * The picture is an MJPEG stream shown in an <img>. A browser tries an image address once: if
 * vision is not running yet, or stops and starts again, the image stays broken or frozen. This
 * hook asks again, after a failed load and whenever vision data starts arriving again.
 *
 * Measured in Chrome with the real stream: a refused connection reports an error, but a stream
 * that was showing and then loses its connection does not. The picture is simply dropped. So the
 * hook also looks at the image itself when vision data stops.
 */

/** Waits after a failed load: 1 s, 2 s, then every 4 s for as long as the stream is wanted. */
const RETRY_MS = [1000, 2000, 4000] as const

export function retryDelayMs(failures: number): number {
  return RETRY_MS[Math.min(Math.max(failures, 1), RETRY_MS.length) - 1]
}

/**
 * The address to request on a given attempt. A browser does not request an image again while its
 * address is unchanged, so each later attempt carries a counter. The stream ignores the query.
 */
export function streamAddress(url: string, attempt: number): string {
  if (attempt === 0) return url
  try {
    const address = new URL(url)
    address.searchParams.set('attempt', String(attempt))
    return address.href
  } catch {
    return url // not an absolute address; the image fails to load and says so
  }
}

interface Attempts {
  /** The address these counts belong to. Another address starts from nothing. */
  url: string
  attempt: number
  /** Failed loads since the last picture arrived. */
  failures: number
}

const fresh = (url: string): Attempts => ({ url, attempt: 0, failures: 0 })
const forUrl = (attempts: Attempts, url: string): Attempts => (attempts.url === url ? attempts : fresh(url))

/** True for an image that finished loading and has no picture: the address failed or the stream was cut. */
export const hasNoPicture = (image: { complete: boolean; naturalWidth: number }): boolean =>
  image.complete && image.naturalWidth === 0

export interface CameraStream {
  /** The address for the <img>, or null when no stream is wanted. */
  src: string | null
  /** True while the last attempt has failed and no picture is showing. */
  failed: boolean
  onLoad: (event: SyntheticEvent<HTMLImageElement>) => void
  onError: () => void
}

/**
 * @param url    the stream address from the settings; '' means none
 * @param wanted false while the picture is not shown at all (simulated data, not connected)
 * @param image  the <img> that shows the stream, so the hook can see when its picture is dropped
 * @param onSize called with the picture's size when a picture arrives
 */
export function useCameraStream(
  url: string,
  wanted: boolean,
  image: RefObject<HTMLImageElement | null>,
  onSize: (width: number, height: number) => void,
): CameraStream {
  // An empty key while the stream is not wanted, so wanting it again starts from nothing.
  const key = wanted ? url : ''
  const [stored, setStored] = useState(() => fresh(key))
  const { attempt, failures } = forUrl(stored, key)

  // After a failed load, ask again a little later.
  useEffect(() => {
    if (key === '' || failures === 0) return
    const timer = setTimeout(() => {
      setStored((s) => ({ ...forUrl(s, key), attempt: forUrl(s, key).attempt + 1 }))
    }, retryDelayMs(failures))
    return () => clearTimeout(timer)
  }, [key, failures])

  // The stream comes from the vision program, so it stops and starts with the vision data.
  useEffect(() => {
    if (key === '') return
    return useSession.subscribe((now, before) => {
      const state = now.vision.state
      const was = before.vision.state
      if (state === was || state === 'waiting') return
      if (state === 'silent') {
        // Data stopped. If the stream was cut too, the browser has dropped the picture without
        // reporting an error: count that as a failed load, which hides the image and starts the retries.
        if (image.current && hasNoPicture(image.current)) {
          setStored((s) => (forUrl(s, key).failures > 0 ? s : { ...forUrl(s, key), failures: 1 }))
        }
        return
      }
      // Data is arriving: vision is running, and its stream with it. Ask again after a silence even
      // if the image looks fine (a stream that ended can leave its last picture showing), and at
      // once if the last load failed.
      setStored((s) => {
        const current = forUrl(s, key)
        return was === 'silent' || current.failures > 0 ? { ...current, attempt: current.attempt + 1 } : s
      })
    })
  }, [key, image])

  return {
    src: key === '' ? null : streamAddress(key, attempt),
    failed: failures > 0,
    onLoad: (event) => {
      const { naturalWidth, naturalHeight } = event.currentTarget
      if (naturalWidth > 0 && naturalHeight > 0) onSize(naturalWidth, naturalHeight)
      // Some browsers report every picture of the stream; return the same object when nothing changes.
      setStored((s) => (s.url === key && s.failures === 0 ? s : { ...forUrl(s, key), failures: 0 }))
    },
    onError: () => setStored((s) => ({ ...forUrl(s, key), failures: forUrl(s, key).failures + 1 })),
  }
}
