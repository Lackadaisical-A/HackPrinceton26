import { useId, type InputHTMLAttributes, type ReactNode } from 'react'
import { cx } from './cx'

export const INPUT_CLASS =
  'h-10 w-full min-w-0 rounded-sm border border-rule-strong bg-plate px-2.5 text-sm text-ink placeholder:text-ink-3 disabled:opacity-50 md:h-9'

export interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  label: string
  /** Guidance shown under the field. */
  help?: ReactNode
  /** What is wrong and how to fix it. Replaces the help text. */
  error?: string | null
  /** Use the mono face, for addresses and other literal values. */
  literal?: boolean
}

/** A labelled text input with optional help and error text. */
export function TextField({ label, help, error, literal, className, ...rest }: TextFieldProps) {
  const id = useId()
  const noteId = useId()
  const note = error ?? help
  return (
    <div className={cx('flex min-w-0 flex-col gap-1', className)}>
      <label htmlFor={id} className="text-sm font-medium text-ink">
        {label}
      </label>
      <input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={note ? noteId : undefined}
        spellCheck={false}
        autoComplete="off"
        className={cx(INPUT_CLASS, literal && 'num', error && 'border-alarm')}
        {...rest}
      />
      {note && (
        <p id={noteId} className={cx('text-xs', error ? 'font-medium text-alarm-ink' : 'text-ink-3')}>
          {note}
        </p>
      )}
    </div>
  )
}
