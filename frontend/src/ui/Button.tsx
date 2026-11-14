import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cx } from './cx'
import { Kbd } from './Kbd'

type Variant = 'solid' | 'outline' | 'quiet' | 'touch' | 'danger'
type Size = 'sm' | 'md'

const VARIANTS: Record<Variant, string> = {
  solid: 'bg-ink text-plate hover:bg-ink-2',
  outline: 'border border-rule-strong bg-plate text-ink hover:bg-well',
  quiet: 'text-ink-2 hover:bg-well hover:text-ink',
  // Indigo is the touch channel's colour, so this variant is for haptics controls only.
  touch: 'bg-touch-600 text-plate hover:bg-touch-700',
  danger: 'border border-rule-strong bg-plate text-alarm-ink hover:bg-well',
}

const SIZES: Record<Size, string> = {
  sm: 'h-7 gap-1.5 px-2 text-xs',
  md: 'h-10 gap-2 px-3 text-sm md:h-9',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  /** The keyboard shortcut for this action, shown at the end of the button. */
  hint?: string
  /** For toggle buttons. Sets aria-pressed. */
  pressed?: boolean
  icon?: ReactNode
}

export function Button({
  variant = 'outline',
  size = 'md',
  hint,
  pressed,
  icon,
  className,
  children,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      aria-pressed={pressed}
      className={cx(
        'inline-flex shrink-0 items-center justify-center rounded-sm font-medium whitespace-nowrap select-none',
        'transition-[color,background-color,border-color,transform,box-shadow] duration-150 ease-out',
        'enabled:active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-45',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {icon}
      {children}
      {hint && <Kbd className="ml-0.5 max-md:hidden">{hint}</Kbd>}
    </button>
  )
}
