import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cx } from './cx'
import { Kbd } from './Kbd'

type Variant = 'solid' | 'outline' | 'quiet' | 'touch' | 'danger' | 'window' | 'window-quiet' | 'window-alarm'
type Size = 'sm' | 'md'

const VARIANTS: Record<Variant, string> = {
  solid: 'bg-ink text-plate shadow-[0_1px_0_rgb(255_255_255/0.08)_inset,0_4px_12px_-6px_rgb(42_16_48/0.6)] hover:bg-ink-2',
  outline: 'border border-rule-strong/55 bg-plate text-ink shadow-xs hover:border-rule-strong hover:bg-well',
  quiet: 'text-ink-2 hover:bg-well hover:text-ink',
  // Indigo is the touch channel's colour, so this variant is for haptics controls only.
  touch: 'bg-touch-600 text-plate hover:bg-touch-700',
  danger: 'border border-rule-strong/55 bg-plate text-alarm-ink hover:border-alarm-ink hover:bg-well',
  // For controls that sit on the dark plum surfaces: the top bar and the camera window.
  window: 'focus-window border border-window-ink-2/35 bg-window-ink/[0.06] text-window-ink hover:border-window-ink-2/70 hover:bg-window-ink/12',
  'window-quiet': 'focus-window text-window-ink-2 hover:bg-window-ink/10 hover:text-window-ink',
  'window-alarm': 'focus-window bg-alarm text-plate shadow-[0_0_0_3px_rgb(208_29_33/0.25)] hover:bg-alarm-ink',
}

const SIZES: Record<Size, string> = {
  sm: 'h-7 gap-1.5 text-xs',
  md: 'h-10 gap-2 text-sm md:h-9',
}

/** Side padding for buttons with a label. Icon-only buttons are square instead. */
const PADDING: Record<Size, string> = { sm: 'px-2.5', md: 'px-3.5' }
const SQUARE: Record<Size, string> = { sm: 'w-7', md: 'w-10 md:w-9' }

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
        'inline-flex shrink-0 items-center justify-center rounded-sm font-semibold whitespace-nowrap select-none',
        'transition-[color,background-color,border-color,transform,box-shadow] duration-150 ease-out',
        'enabled:active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-45',
        VARIANTS[variant],
        SIZES[size],
        children == null && !hint ? SQUARE[size] : PADDING[size],
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
