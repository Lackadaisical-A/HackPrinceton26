import type { ReactNode } from 'react'
import { cx } from './cx'

/** A key cap. Inherits the text colour, so it works on any surface. */
export function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <kbd
      className={cx(
        'num inline-flex h-[1.125rem] min-w-[1.125rem] items-center justify-center rounded-xs border border-current/35 px-1 text-[0.6875rem] leading-none font-medium',
        className,
      )}
    >
      {children}
    </kbd>
  )
}
