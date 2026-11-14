import { describe, expect, it } from 'vitest'
import { launchSettings } from './launch'

describe('launch settings', () => {
  it('opens live data and the matching local camera without a manual settings step', () => {
    expect(launchSettings('?source=live&wsUrl=ws%3A%2F%2F127.0.0.1%3A8765&cameraUrl=http%3A%2F%2F127.0.0.1%3A18766%2Fstream'))
      .toEqual({ source: 'websocket', wsUrl: 'ws://127.0.0.1:8765', cameraUrl: 'http://127.0.0.1:18766/stream' })
  })
  it('preserves saved settings without an explicit live launch', () => {
    expect(launchSettings('')).toEqual({})
  })
  it('ignores remote or malformed addresses', () => {
    expect(launchSettings('?source=live&wsUrl=ws://example.com&cameraUrl=oops')).toEqual({ source: 'websocket' })
    expect(launchSettings('?source=live&cameraUrl=javascript:alert(1)')).toEqual({ source: 'websocket' })
  })
})
