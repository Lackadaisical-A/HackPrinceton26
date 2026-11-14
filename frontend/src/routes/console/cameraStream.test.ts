import { describe, expect, it } from 'vitest'
import { hasNoPicture, retryDelayMs, streamAddress } from './cameraStream'

describe('streamAddress', () => {
  it('is the address itself on the first attempt', () => {
    expect(streamAddress('http://127.0.0.1:8766/stream', 0)).toBe('http://127.0.0.1:8766/stream')
  })

  it('differs on every later attempt, so the browser asks again', () => {
    const first = streamAddress('http://127.0.0.1:8766/stream', 1)
    const second = streamAddress('http://127.0.0.1:8766/stream', 2)
    expect(first).toBe('http://127.0.0.1:8766/stream?attempt=1')
    expect(second).toBe('http://127.0.0.1:8766/stream?attempt=2')
  })

  it('keeps a query the address already has', () => {
    expect(streamAddress('http://camera.local/video?channel=2', 3)).toBe('http://camera.local/video?channel=2&attempt=3')
  })

  it('replaces its own counter instead of adding another', () => {
    expect(streamAddress('http://127.0.0.1:8766/stream?attempt=1', 2)).toBe('http://127.0.0.1:8766/stream?attempt=2')
  })

  it('leaves an address it cannot read unchanged', () => {
    expect(streamAddress('not an address', 4)).toBe('not an address')
  })
})

describe('retryDelayMs', () => {
  it('waits longer after each failure, up to four seconds', () => {
    expect([1, 2, 3, 4, 50].map(retryDelayMs)).toEqual([1000, 2000, 4000, 4000, 4000])
  })

  it('never returns less than the first wait', () => {
    expect(retryDelayMs(0)).toBe(1000)
  })
})

describe('hasNoPicture', () => {
  it('is true once loading has finished without a picture', () => {
    expect(hasNoPicture({ complete: true, naturalWidth: 0 })).toBe(true)
  })

  it('is false while the image is still loading', () => {
    expect(hasNoPicture({ complete: false, naturalWidth: 0 })).toBe(false)
  })

  it('is false while a picture is showing', () => {
    expect(hasNoPicture({ complete: true, naturalWidth: 960 })).toBe(false)
  })
})
