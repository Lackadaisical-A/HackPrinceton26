import type { Alert, AlertLevel, AlertWhere } from '../state/session'

export interface Condition {
  key: string
  level: AlertLevel
  where: AlertWhere
  text: string
  /** Whether the condition holds right now. */
  on: boolean
  /**
   * Latched alerts stay on screen for a minimum time after a brief event (a grip spike), so the
   * operator does not miss it. Unlatched alerts describe a continuing state (a lost feed) and
   * clear the moment the state ends, so the screen never claims a fault that has passed.
   */
  latch: boolean
}

export interface AlertUpdate {
  alerts: Alert[]
  /** Alerts that began in this update. */
  raised: Alert[]
  changed: boolean
}

/**
 * Turns a list of conditions into the alerts to display. Pure state machine: time comes in as
 * an argument, so it can be tested without timers.
 */
export class AlertTracker {
  private readonly held = new Map<string, Alert>()
  private readonly holdMs: number

  constructor(holdMs: number) {
    this.holdMs = holdMs
  }

  update(conditions: readonly Condition[], nowMs: number): AlertUpdate {
    const raised: Alert[] = []
    let changed = false

    for (const c of conditions) {
      const current = this.held.get(c.key)
      if (c.on) {
        if (!current) {
          const alert: Alert = { key: c.key, level: c.level, where: c.where, text: c.text, active: true, since: nowMs }
          this.held.set(c.key, alert)
          raised.push(alert)
          changed = true
        } else if (!current.active || current.text !== c.text || current.level !== c.level) {
          this.held.set(c.key, { ...current, active: true, text: c.text, level: c.level })
          changed = true
        }
      } else if (current) {
        const expired = !c.latch || nowMs - current.since >= this.holdMs
        if (expired) {
          this.held.delete(c.key)
          changed = true
        } else if (current.active) {
          this.held.set(c.key, { ...current, active: false })
          changed = true
        }
      }
    }

    return { alerts: [...this.held.values()], raised, changed }
  }

  clear(): void {
    this.held.clear()
  }
}
