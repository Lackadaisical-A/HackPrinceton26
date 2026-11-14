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
    <section aria-labelledby={id} className={cx('min-w-0 rounded-lg border border-rule bg-plate transition-[border-color,box-shadow] duration-200 ease-out',
        'hover:border-touch-200 hover:shadow-[0_6px_20px_-12px_rgb(94_17_112/0.35)]', className)}>
      <header className="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 px-4 pt-2.5 pb-2">
        <h2 id={id} className="text-sm font-semibold text-ink">
          {title}
        </h2>
        {note && <span className="text-xs text-ink-3">{note}</span>}
        {aside && <div className="ml-auto flex flex-wrap items-center justify-end gap-2">{aside}</div>}
      </header>
      <div className={cx('px-4 pb-4', bodyClassName)}>{children}</div>
    </section>
  )
}
