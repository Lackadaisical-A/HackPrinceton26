import { useMemo, useState } from 'react'
import { downloadText, fileStamp } from '../../lib/format'
import { useTrials, type Trial } from '../../state/trials'
import { Button } from '../../ui/Button'
import { Chip } from '../../ui/Chip'
import { IconDownload, IconTrash } from '../../ui/icons'
import { PageHeader } from '../../ui/PageHeader'
import { Plate } from '../../ui/Plate'
import { Segmented } from '../../ui/Segmented'
import { Comparison } from './Comparison'
import { DeleteAllDialog } from './DeleteAllDialog'
import { NoneOfOrigin, NoTrials } from './EmptyState'
import { TrialsTable } from './TrialsTable'
import {
  completedByCondition,
  countByOrigin,
  defaultOrigin,
  ORIGIN_LABEL,
  ORIGINS,
  sampleTrials,
  startedText,
  trialCount,
  trialsCsv,
  trialsOf,
  type Origin,
} from './stats'

const NOT_LIVE: Record<Exclude<Origin, 'live'>, string> = {
  simulated: 'These trials were run against the simulator. They exercise this page and are not evidence about PAUL.',
  sample: 'These rows are made up for trying the page. They are not evidence about PAUL.',
}

/** Hatching marks anything that is not real data. This says so in words as well. */
function NotLiveNotice({ origin }: { origin: Exclude<Origin, 'live'> }) {
  return (
    <div role="note" className="flex overflow-hidden rounded-md border border-rule-strong bg-plate">
      <div aria-hidden="true" className="hatch w-5 shrink-0 border-r border-rule-strong" />
      <p className="flex flex-wrap items-center gap-x-2.5 gap-y-1 px-3 py-2 text-sm text-ink">
        <Chip tone="sim">{ORIGIN_LABEL[origin]} data</Chip>
        <span>{NOT_LIVE[origin]}</span>
      </p>
    </div>
  )
}

/**
 * The Trials screen: the with-haptics and without-haptics conditions side by side, and every
 * trial in a table. One origin is shown at a time, so simulated and sample rows can never be
 * read as part of a live comparison.
 */
export default function TrialsPage() {
  const trials = useTrials((s) => s.trials)
  const add = useTrials((s) => s.add)
  const addMany = useTrials((s) => s.addMany)
  const remove = useTrials((s) => s.remove)
  const clear = useTrials((s) => s.clear)

  /** The origin the user chose. Until they choose, the first origin that has trials is shown. */
  const [picked, setPicked] = useState<Origin | null>(null)
  const [confirming, setConfirming] = useState(false)
  /** The last trial deleted from the table, kept so the deletion can be undone. */
  const [deleted, setDeleted] = useState<Trial | null>(null)

  const counts = useMemo(() => countByOrigin(trials), [trials])
  const origin = picked ?? defaultOrigin(counts)
  const shown = useMemo(() => trialsOf(trials, origin), [trials, origin])
  const groups = useMemo(() => completedByCondition(trials, origin), [trials, origin])
  const aborted = shown.length - groups.withHaptics.length - groups.withoutHaptics.length
  const options = ORIGINS.map((value) => ({ value, label: `${ORIGIN_LABEL[value]} (${counts[value]})` }))

  const loadSample = (): void => {
    addMany(sampleTrials(Date.now()))
    setPicked('sample')
    setDeleted(null)
  }

  const removeSample = (): void => {
    clear('sample')
    if (picked === 'sample') setPicked(null)
    if (deleted?.origin === 'sample') setDeleted(null)
  }

  const exportCsv = (): void => {
    downloadText(`paul-trials-${fileStamp(Date.now())}.csv`, trialsCsv(trials), 'text/csv;charset=utf-8')
  }

  const deleteAll = (): void => {
    clear()
    setConfirming(false)
    setPicked(null)
    setDeleted(null)
  }

  const deleteOne = (trial: Trial): void => {
    remove(trial.id)
    setDeleted(trial)
    // The origin on show is about to be empty, so fall back to one that has trials.
    if (shown.length === 1) setPicked(null)
  }

  const undoDelete = (): void => {
    if (!deleted) return
    add(deleted)
    setPicked(deleted.origin)
    setDeleted(null)
  }

  return (
    <>
      <PageHeader
        title="Trials"
        actions={
          <>
            {counts.sample > 0 ? (
              <Button onClick={removeSample}>Remove sample set</Button>
            ) : (
              <Button onClick={loadSample}>Load sample set</Button>
            )}
            <Button icon={<IconDownload />} onClick={exportCsv} disabled={trials.length === 0}>
              Export CSV
            </Button>
            <Button
              variant="danger"
              icon={<IconTrash />}
              onClick={() => setConfirming(true)}
              disabled={trials.length === 0}
            >
              Delete all
            </Button>
          </>
        }
      >
        Compare trials run with haptic feedback against trials run without it.
      </PageHeader>

      {trials.length === 0 ? (
        <NoTrials onLoadSample={loadSample} />
      ) : (
        <div className="flex flex-col gap-3 md:gap-4">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <span aria-hidden="true" className="text-sm font-medium text-ink-2">
              Data shown
            </span>
            <Segmented label="Data shown" value={origin} options={options} onChange={setPicked} />
          </div>

          {origin !== 'live' && shown.length > 0 && <NotLiveNotice origin={origin} />}

          {shown.length === 0 ? (
            <NoneOfOrigin origin={origin} onLoadSample={loadSample} />
          ) : (
            <>
              <Comparison groups={groups} aborted={aborted} origin={origin} />
              <Plate
                title={`${ORIGIN_LABEL[origin]} trials`}
                note={`${trialCount(shown.length)}, newest first`}
                aside={origin !== 'live' && <Chip tone="sim">{ORIGIN_LABEL[origin]}</Chip>}
              >
                <TrialsTable
                  trials={shown}
                  caption={`${ORIGIN_LABEL[origin]} trials, newest first`}
                  onDelete={deleteOne}
                />
              </Plate>
            </>
          )}
        </div>
      )}

      {/* Stays in view wherever the table is scrolled to. The region is always present so the message is announced. */}
      <div role="status" className="pointer-events-none fixed inset-x-0 bottom-3 z-20 flex justify-center px-3">
        {deleted && (
          <div className="pointer-events-auto flex max-w-full flex-wrap items-center gap-x-3 gap-y-2 rounded-md border-2 border-ink bg-plate px-3 py-2 text-sm text-ink">
            <span>
              Deleted the trial started <span className="num">{startedText(deleted.startedAt)}</span>.
            </span>
            <span className="flex gap-2">
              <Button size="sm" onClick={undoDelete}>
                Undo
              </Button>
              <Button size="sm" variant="quiet" onClick={() => setDeleted(null)}>
                Dismiss
              </Button>
            </span>
          </div>
        )}
      </div>

      <DeleteAllDialog
        open={confirming}
        counts={counts}
        onCancel={() => setConfirming(false)}
        onConfirm={deleteAll}
      />
    </>
  )
}
