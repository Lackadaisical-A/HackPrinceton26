import { useRef, type KeyboardEvent } from 'react'
import { cx } from './cx'
import { useIndicator } from './useIndicator'

export interface SegmentedProps<T extends string> {
  /** Names the group for assistive technology. */
  label: string
  value: T
  options: readonly { value: T; label: string }[]
  onChange: (value: T) => void
  disabled?: boolean
  className?: string
}

/** A single choice from a few options, all visible at once. Arrow keys move the selection. */
export function Segmented<T extends string>({ label, value, options, onChange, disabled, className }: SegmentedProps<T>) {
  const group = useRef<HTMLDivElement | null>(null)
  const thumb = useIndicator(group, '[aria-checked="true"]', value)

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    const step =
      event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 0
    if (step === 0) return
    event.preventDefault()
    const index = options.findIndex((o) => o.value === value)
    const next = options[(index + step + options.length) % options.length]
    onChange(next.value)
    group.current?.querySelector<HTMLButtonElement>(`[data-value="${CSS.escape(next.value)}"]`)?.focus()
  }

  return (
    <div
      ref={group}
      role="radiogroup"
      aria-label={label}
      onKeyDown={onKeyDown}
      className={cx('relative isolate inline-flex max-w-full flex-wrap gap-0.5 rounded-sm border border-rule bg-well p-[3px] shadow-[inset_0_1px_2px_rgb(42_16_48/0.06)]', className)}
    >
      {/* The selection is a thumb that slides between options. */}
      <span aria-hidden="true" className="absolute top-[3px] bottom-[3px] left-0 -z-10 rounded-xs bg-ink shadow-[0_2px_6px_-2px_rgb(42_16_48/0.5)]" style={thumb} />
      {options.map((option) => {
        const selected = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={selected ? 0 : -1}
            data-value={option.value}
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className={cx(
              'h-8 rounded-xs px-3 text-sm font-semibold whitespace-nowrap transition-colors duration-200 md:h-7',
              'disabled:cursor-not-allowed disabled:opacity-50',
              selected ? 'text-plate' : 'text-ink-2 hover:text-ink',
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
