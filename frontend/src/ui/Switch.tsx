import { useId, type ReactNode } from 'react'
import { cx } from './cx'

export interface SwitchProps {
  checked: boolean
  onChange: (checked: boolean) => void
  label: ReactNode
  /** Use the touch channel's indigo when on. For haptics only. */
  tone?: 'ink' | 'touch'
  disabled?: boolean
  /** Explains why the switch is disabled, or adds detail. Read after the label. */
  description?: string
  className?: string
}

/** An on/off control that takes effect at once. State is shown by position and by the word. */
export function Switch({ checked, onChange, label, tone = 'ink', disabled, description, className }: SwitchProps) {
  const labelId = useId()
  const descriptionId = useId()
  return (
    <span className={cx('inline-flex items-center gap-2', className)}>
      <span id={labelId} className="text-sm font-medium text-ink">
        {label}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={labelId}
        aria-describedby={description ? descriptionId : undefined}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cx(
          'relative h-6 w-11 shrink-0 rounded-full border transition-colors duration-150',
          'disabled:cursor-not-allowed disabled:opacity-50',
          checked
            ? tone === 'touch'
              ? 'border-touch-600 bg-touch-600'
              : 'border-ink bg-ink'
            : 'border-rule-strong bg-well',
        )}
      >
        <span
          className={cx(
            'absolute top-0.5 left-0.5 size-[1.125rem] rounded-full transition-[transform,background-color] duration-250 ease-spring',
            checked ? 'translate-x-5 bg-plate' : 'bg-ink-2',
          )}
        />
      </button>
      <span className="num w-6 text-xs text-ink-2" aria-hidden="true">
        {checked ? 'On' : 'Off'}
      </span>
      {description && (
        <span id={descriptionId} className="sr-only">
          {description}
        </span>
      )}
    </span>
  )
}
