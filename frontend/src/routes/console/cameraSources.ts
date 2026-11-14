export interface CameraSource {
  id: string
  index: number
  name: string
}

export interface CameraSources {
  sources: CameraSource[]
  active_id: string | null
  active_name: string
  pending_id: string | null
  generation: number
  error: string | null
  can_switch: boolean
}

/** Camera controls live beside the configured MJPEG stream, including custom ports. */
export function cameraControlUrl(streamUrl: string, action: 'sources' | 'source', refresh = false): string {
  const url = new URL(streamUrl)
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Set an HTTP camera stream address in Signals.')
  url.pathname = `/${action}`
  url.search = refresh ? '?refresh=1' : ''
  url.hash = ''
  return url.toString()
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function parseCameraSources(value: unknown): CameraSources {
  const nullableString = (item: unknown) => item === null || typeof item === 'string'
  if (
    !record(value) || !Array.isArray(value.sources) ||
    !value.sources.every((item: unknown) => record(item) && typeof item.id === 'string' && item.id.length > 0 &&
      typeof item.name === 'string' && Number.isInteger(item.index) && (item.index as number) >= 0) ||
    !nullableString(value.active_id) || typeof value.active_name !== 'string' ||
    !nullableString(value.pending_id) || !nullableString(value.error) ||
    !Number.isInteger(value.generation) || typeof value.can_switch !== 'boolean'
  ) throw new Error('Camera selection is unavailable. Update or restart the vision service, then Refresh.')
  return value as unknown as CameraSources
}

export async function readCameraResponse(response: Response): Promise<CameraSources> {
  const value: unknown = await response.json()
  if (!response.ok) {
    throw new Error(record(value) && typeof value.error === 'string' ? value.error : 'Camera selection is unavailable.')
  }
  return parseCameraSources(value)
}
