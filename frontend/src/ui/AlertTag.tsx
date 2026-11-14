import type { Alert, AlertLevel } from '../state/session'
import { cx } from './cx'
import { IconCaution, IconWarning } from './icons'

const LEVEL_WORD: Record<AlertLevel, string> = { caution: 'Caution', warning: 'Warning' }

export interface AlertTagProps {
  level: AlertLevel
  text: string
  /** False for an alert whose cause has passed but which is still held on screen. */
  active?: boolean
  /** Use the brighter steps meant for the dark camera window. */
  onWindow?: boolean
  className?: string
}

/**
 * One abnormal state. Caution is a triangle on amber; warning is an octagon on red. The shape
 * and the wording carry the meaning, so it survives greyscale and colour-blind viewing.
 */
export function AlertTag({ level, text, active = true, onWindow = false, className }: AlertTagProps) {
  const Icon = level === 'warning' ? IconWarning : IconCaution
  const fill = !active
    ? onWindow
      ? 'border-window-ink-2 text-window-ink'
      : 'border-rule-strong text-ink-2'
    : level === 'warning'
      ? onWindow
        ? 'border-alarm-window bg-alarm-window text-window'
        : 'border-alarm bg-alarm text-plate'
      : onWindow
        ? 'border-caution-window bg-caution-window text-window'
        : 'border-caution-ink bg-caution text-ink'

  return (
    <span
      className={cx(
        'pop-in inline-flex min-h-6 items-center gap-1.5 rounded-xs border px-1.5 py-0.5 text-xs font-semibold',
        fill,
        className,
      )}
    >
      <Icon size={14} className="shrink-0" />
      <span className="sr-only">{LEVEL_WORD[level]}: </span>
      {active ? text : `Earlier: ${text[0].toLowerCase()}${text.slice(1)}`}
    </span>
  )
}

/** Every alert for one part of the screen, as a row of tags. Renders nothing when there are none. */
export function AlertRow({ alerts, onWindow, className }: { alerts: readonly Alert[]; onWindow?: boolean; className?: string }) {
  if (alerts.length === 0) return null
  return (
    <span className={cx('inline-flex flex-wrap items-center gap-1.5', className)}>
      {alerts.map((a) => (
        <AlertTag key={a.key} level={a.level} text={a.text} active={a.active} onWindow={onWindow} />
      ))}
    </span>
  )
}
