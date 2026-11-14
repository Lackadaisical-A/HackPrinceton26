import type { Command } from '../contracts'

export type SourceKind = 'simulator' | 'websocket'

/**
 * - simulated: the built-in simulator is running.
 * - connecting: opening the WebSocket for the first time.
 * - open: connected to the bridge.
 * - retrying: the connection failed or dropped; another attempt is scheduled.
 * - idle: nothing is running.
 */
export type LinkState = 'simulated' | 'connecting' | 'open' | 'retrying' | 'idle'

export interface LinkStatus {
  state: LinkState
  /** A plain-language reason, shown beside the state when present. */
  detail: string | null
  /** Epoch milliseconds of the next connection attempt, while retrying. */
  retryAt: number | null
}

export interface Source {
  start(): void
  stop(): void
  /** Sends a command upstream. Returns false when it could not be sent. */
  send(command: Command): boolean
}
