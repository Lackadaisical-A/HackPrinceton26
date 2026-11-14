import type { ReactNode } from 'react'
import { cx } from './cx'

type Tone = 'neutral' | 'sim' | 'touch' | 'window'

const TONES: Record<Tone, string> = {
  neutral: 'border-rule-strong text-ink-2',
  // Hatching marks anything that is not real data, wherever it appears.
  sim: 'hatch border-rule-strong text-ink',
  touch: 'border-touch-300 bg-touch-100 text-touch-700',
  window: 'border-window-ink-2/50 bg-window/70 text-window-ink',
}

/** A small, non-interactive label. */
export function Chip({ tone = 'neutral', className, children }: { tone?: Tone; className?: string; children: ReactNode }) {
  return (
    <span
      className={cx(
        'inline-flex h-6 shrink-0 items-center gap-1.5 rounded-xs border px-1.5 text-xs font-medium whitespace-nowrap',
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}
