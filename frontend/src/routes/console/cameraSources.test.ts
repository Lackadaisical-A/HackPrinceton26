import { describe, expect, it } from 'vitest'
import { cameraControlUrl, parseCameraSources, readCameraResponse } from './cameraSources'

const status = {
  sources: [{ id: 'usb', index: 0, name: 'Robot USB' }], active_id: 'usb', active_name: 'Robot USB',
  pending_id: null, generation: 0, error: null, can_switch: true,
}

describe('camera selection API', () => {
  it('uses the configured stream host and port without stream retry parameters', () => {
    expect(cameraControlUrl('http://127.0.0.1:18766/stream?attempt=3#unused', 'sources', true))
      .toBe('http://127.0.0.1:18766/sources?refresh=1')
    expect(cameraControlUrl('https://localhost:1234/stream', 'source')).toBe('https://localhost:1234/source')
    expect(() => cameraControlUrl('file:///stream', 'sources')).toThrow()
  })
  it('accepts named devices and an empty discovery result', () => {
    expect(parseCameraSources(status)).toEqual(status)
    expect(parseCameraSources({ ...status, sources: [], active_id: null }).sources).toEqual([])
  })
  it.each([null, {}, { ...status, sources: [{ id: 'bad', index: '0', name: 'Camera' }] },
    { ...status, can_switch: 'true' }, { ...status, active_id: 0 }])('rejects malformed status %j', (value) => {
    expect(() => parseCameraSources(value)).toThrow('Camera selection is unavailable')
  })
  it('surfaces selection failures instead of accepting the requested camera', async () => {
    await expect(readCameraResponse(new Response(JSON.stringify({ error: 'Camera disconnected' }), { status: 400 })))
      .rejects.toThrow('Camera disconnected')
  })
})
