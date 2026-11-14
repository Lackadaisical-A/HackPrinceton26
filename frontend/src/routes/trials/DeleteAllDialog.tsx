import { useEffect, useId, useRef } from 'react'
import { Button } from '../../ui/Button'
import { originBreakdown, trialCount, type Origin } from './stats'

/**
 * Asks before every saved trial is deleted. Cancel comes first, so it takes the focus when the
 * dialog opens and a stray Enter does no harm.
 */
export function DeleteAllDialog({
  open,
  counts,
  onCancel,
  onConfirm,
}: {
  open: boolean
  counts: Record<Origin, number>
  onCancel: () => void
  onConfirm: () => void
}) {
  const ref = useRef<HTMLDialogElement | null>(null)
  const titleId = useId()
  const total = counts.live + counts.simulated + counts.sample

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    else if (!open && dialog.open) dialog.close()
  }, [open])

  return (
    <dialog
      ref={ref}
      onClose={onCancel}
      onClick={(event) => {
        // A click on the backdrop lands on the dialog element itself.
        if (event.target === ref.current) onCancel()
      }}
      aria-labelledby={titleId}
      className="m-auto w-[min(28rem,calc(100vw-2rem))] rounded-lg border border-rule-strong bg-plate p-0 text-ink"
    >
      <div className="p-5">
        <h2 id={titleId} className="text-lg font-semibold text-ink">
          {total === 1 ? 'Delete the 1 saved trial?' : `Delete all ${total} trials?`}
        </h2>
        <p className="mt-2 text-base text-ink-2">
          This deletes every trial saved in this browser ({originBreakdown(counts)}). It cannot be undone.
        </p>
        <p className="mt-2 text-sm text-ink-2">To keep a copy, cancel and export a CSV first.</p>
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <Button onClick={onCancel}>Cancel</Button>
          <Button variant="danger" onClick={onConfirm}>
            Delete {trialCount(total)}
          </Button>
        </div>
      </div>
    </dialog>
  )
}
