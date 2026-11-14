import { useId, type ReactNode } from 'react'
import { cx } from './cx'

export interface PlateProps {
  title: string
  /** A short qualifier beside the title, such as "relative, uncalibrated". */
  note?: ReactNode
  /** Controls and status at the right of the header. */
  aside?: ReactNode
  className?: string
  bodyClassName?: string
  children: ReactNode
}

/** A titled instrument plate: the unit every panel on the console is built from. */
export function Plate({ title, note, aside, className, bodyClassName, children }: PlateProps) {
  const id = useId()
  return (
    <section aria-labelledby={id} className={cx('min-w-0 rounded-lg border border-rule/60 bg-plate shadow-[var(--shadow-plate)]',
        'transition-[border-color,box-shadow,transform] duration-200 ease-out',
        'hover:border-touch-200/80 hover:shadow-[var(--shadow-plate-hover)]', className)}>
      <header className="flex min-h-12 flex-wrap items-center gap-x-3 gap-y-1 px-4 pt-3 pb-2">
        <h2 id={id} className="flex items-center gap-2 text-base font-bold tracking-[-0.01em] text-ink">
          <span aria-hidden="true" className="h-3.5 w-1 rounded-full bg-ink" />
          {title}
        </h2>
        {note && <span className="text-xs text-ink-3">{note}</span>}
        {aside && <div className="ml-auto flex flex-wrap items-center justify-end gap-2">{aside}</div>}
      </header>
      <div className={cx('px-4 pb-4', bodyClassName)}>{children}</div>
    </section>
  )
}
