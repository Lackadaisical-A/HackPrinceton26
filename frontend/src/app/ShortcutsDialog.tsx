import { useEffect, useRef } from 'react'
import { Button } from '../ui/Button'
import { Kbd } from '../ui/Kbd'
import { SHORTCUTS } from './shortcuts'

export function ShortcutsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement | null>(null)

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    else if (!open && dialog.open) dialog.close()
  }, [open])

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(event) => {
        // A click on the backdrop lands on the dialog element itself.
        if (event.target === ref.current) onClose()
      }}
      aria-labelledby="shortcuts-title"
      className="m-auto w-[min(30rem,calc(100vw-2rem))] rounded-lg border border-rule-strong bg-plate p-0 text-ink"
    >
      <div className="p-5">
        <h2 id="shortcuts-title" className="text-lg font-semibold">
          Keyboard shortcuts
        </h2>
        <p className="mt-1 text-sm text-ink-2">Single keys, so a hand can stay on the keyboard during a trial.</p>
        <dl className="mt-4 grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-2.5 text-sm">
          {SHORTCUTS.map((s) => (
            <div key={s.keys} className="contents">
              <dt>
                <Kbd className="h-6 min-w-6 text-xs">{s.keys}</Kbd>
              </dt>
              <dd>{s.does}</dd>
            </div>
          ))}
        </dl>
        <div className="mt-5 flex justify-end">
          <Button variant="solid" onClick={onClose} autoFocus>
            Close
          </Button>
        </div>
      </div>
    </dialog>
  )
}
