import type { TelemetryBus } from '../bus'
import type { Command } from '../contracts'
import { COMMAND_TOPIC, encodeFrame, isTopic, parseFrame, VALIDATORS } from '../contracts'
import type { LinkStatus, Source } from './types'

const BACKOFF_MS = [1000, 2000, 4000, 8000]

/**
 * Live data from the ZMQ-to-WebSocket bridge.
 *
 * Each WebSocket text message is one frame, "<topic> <json>", exactly as the Python side
 * publishes it over ZMQ. Frames that fail validation are counted and dropped, never shown.
 * Commands go back the same way on the `command` topic.
 */
export class WebSocketSource implements Source {
  private readonly bus: TelemetryBus
  private readonly url: string
  private readonly onLink: (status: LinkStatus) => void
  private socket: WebSocket | null = null
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private attempt = 0
  private stopped = true
  private readonly decoder = new TextDecoder()

  constructor(bus: TelemetryBus, url: string, onLink: (status: LinkStatus) => void) {
    this.bus = bus
    this.url = url
    this.onLink = onLink
  }

  start(): void {
    if (!this.stopped) return
    this.stopped = false
    this.attempt = 0
    this.open()
  }

  stop(): void {
    this.stopped = true
    if (this.retryTimer !== null) clearTimeout(this.retryTimer)
    this.retryTimer = null
    const socket = this.socket
    this.socket = null
    if (socket) {
      socket.onopen = socket.onclose = socket.onerror = socket.onmessage = null
      socket.close()
    }
  }

  send(command: Command): boolean {
    if (this.socket?.readyState !== WebSocket.OPEN) return false
    this.socket.send(encodeFrame(COMMAND_TOPIC, { t: Date.now() / 1000, ...command }))
    return true
  }

  private open(): void {
    if (this.attempt === 0) this.onLink({ state: 'connecting', detail: null, retryAt: null })

    let socket: WebSocket
    try {
      socket = new WebSocket(this.url)
    } catch {
      // A malformed address. Retrying cannot fix it, so stop and say so.
      this.stopped = true
      this.onLink({ state: 'idle', detail: `"${this.url}" is not a valid WebSocket address.`, retryAt: null })
      return
    }
    socket.binaryType = 'arraybuffer'
    this.socket = socket

    socket.onopen = () => {
      this.attempt = 0
      this.onLink({ state: 'open', detail: null, retryAt: null })
    }
    socket.onmessage = (event: MessageEvent<string | ArrayBuffer>) => {
      this.receive(typeof event.data === 'string' ? event.data : this.decoder.decode(event.data))
    }
    socket.onclose = () => {
      if (this.stopped || this.socket !== socket) return
      this.socket = null
      const delay = BACKOFF_MS[Math.min(this.attempt, BACKOFF_MS.length - 1)]
      this.attempt++
      this.onLink({
        state: 'retrying',
        detail: `No bridge is answering at ${this.url}.`,
        retryAt: Date.now() + delay,
      })
      this.retryTimer = setTimeout(() => this.open(), delay)
    }
    // An error is always followed by a close event, which does the reporting.
    socket.onerror = () => {}
  }

  private receive(frame: string): void {
    const parsed = parseFrame(frame)
    if (!parsed.topic || !isTopic(parsed.topic)) return // other topics are not for this app
    if (!parsed.ok) {
      this.bus.reject(parsed.topic, parsed.error)
      return
    }
    const error = VALIDATORS[parsed.topic](parsed.payload)
    if (error) this.bus.reject(parsed.topic, error)
    else this.bus.publish(parsed.topic, parsed.payload as never)
  }
}
