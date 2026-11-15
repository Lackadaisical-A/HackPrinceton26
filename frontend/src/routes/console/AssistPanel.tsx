import { useEffect, useState } from 'react'
import { findAssistItem, requestAssist } from '../../app/runtime'
import { sentenceCase } from '../../lib/format'
import { useSession } from '../../state/session'
import type { AssistItem, PatientState, TouchCheck, TouchKind, Urgency } from '../../telemetry/contracts'
import { Button } from '../../ui/Button'
import { Chip } from '../../ui/Chip'
import { cx } from '../../ui/cx'
import { Plate } from '../../ui/Plate'
import { Switch } from '../../ui/Switch'

/** Auto mode asks this often. Gemini is slow and costs quota, so never faster. */
const AUTO_INTERVAL_MS = 20_000

const URGENCY: Record<Urgency, { text: string; className: string }> = {
  none: { text: 'No concerns seen', className: 'border-rule-strong text-ink-2' },
  watch: { text: 'Keep an eye on', className: 'border-caution bg-caution/15 text-caution-ink' },
  urgent: { text: 'May need help now', className: 'border-alarm bg-alarm text-plate' },
}

const PATIENT_STATE: Record<PatientState, string> = {
  awake: 'Awake',
  eyes_closed: 'Eyes closed: asleep or unresponsive?',
  distressed: 'Appears distressed',
  unclear: 'State unclear',
  not_visible: 'No patient in view',
}

const TOUCH_LABEL: Record<TouchKind, string> = {
  wellness_nudge: 'Wellness nudge',
  attention_tap: 'Get attention',
  guided_handover: 'Feel the handover',
  weight_check: 'Feel the weight',
  move_within_reach: 'Move within reach',
  call_button: 'Press call button',
  comfort_adjust: 'Comfort',
}

/** Suggested gentle touches. The operator makes them with the gripper and feels them through the pads. */
function TouchChecks({ checks }: { checks: TouchCheck[] }) {
  return (
    <div>
      <h3 className="text-xs font-semibold text-ink-2">Touch checks</h3>
      <ul className="mt-1 space-y-1.5">
        {checks.map((check) => (
          <li
            key={check.kind}
            className={cx(
              'rounded-md border px-3 py-2 text-sm',
              check.kind === 'wellness_nudge' ? 'border-caution bg-caution/15' : 'border-rule',
            )}
          >
            <span className="font-semibold text-ink">{TOUCH_LABEL[check.kind]}: </span>
            <span className="text-ink">{check.text}</span>
            {check.reason && <span className="block text-xs text-ink-2">{check.reason}</span>}
          </li>
        ))}
      </ul>
      <p className="mt-1 text-xs text-ink-3">Light touches only: shoulder, arm or hand. Never the head, face, neck or chest.</p>
    </div>
  )
}

function ItemRow({ item }: { item: AssistItem }) {
  const idle = useSession((s) => s.link.state === 'idle')
  const find = useSession((s) => (s.assistFind?.name === item.name ? s.assistFind : null))
  const target = useSession((s) => s.targetLabel)
  const active = target === item.name
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-ink">
          {sentenceCase(item.name)}
          <span className="text-xs font-normal text-ink-3">
            {item.visible ? 'in view' : 'not in view'} · {item.known ? 'vision knows it' : 'new to vision'}
          </span>
        </div>
        {item.reason && <p className="text-xs text-ink-2">{item.reason}</p>}
        {find && find.state !== 'selected' && (
          <p className={cx('text-xs', find.state === 'failed' ? 'text-alarm-ink' : 'text-ink-2')} role="status">
            {find.detail}
          </p>
        )}
      </div>
      <Button
        size="sm"
        variant={active ? 'solid' : 'outline'}
        disabled={idle || find?.state === 'teaching'}
        onClick={() => findAssistItem(item)}
        title={item.known ? 'Make this the target' : `Teach vision “${item.prompt}”, then make it the target`}
      >
        {active ? 'Target' : find?.state === 'teaching' ? 'Learning…' : item.known ? 'Find' : 'Learn & find'}
      </Button>
    </li>
  )
}

/**
 * Gemini's reading of the camera picture: what it sees, visible signs about the patient, suggested actions and
 * the items the patient may need. Advisory only: buttons change vision's target, never the arm.
 */
export function AssistPanel() {
  const assist = useSession((s) => s.assist)
  const idle = useSession((s) => s.link.state === 'idle')
  const [auto, setAuto] = useState(false)
  const working = assist?.status === 'working'

  useEffect(() => {
    if (!auto || idle) return
    requestAssist()
    const timer = setInterval(requestAssist, AUTO_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [auto, idle])

  // While a new analysis runs or after an error, the last good answer stays below the status line.
  const shown = useSession((s) => s.assistResult)

  return (
    <Plate
      title="Assist"
      note="Gemini · advisory, not a diagnosis"
      aside={
        <>
          <Switch checked={auto} onChange={setAuto} label="Auto" disabled={idle} description="Analyse every 20 seconds" />
          <Button size="sm" variant="solid" disabled={idle || working} onClick={() => requestAssist()}>
            {working ? 'Analysing…' : 'Analyse scene'}
          </Button>
        </>
      }
    >
      {assist?.status === 'error' && (
        <p className="mb-2 text-sm text-alarm-ink" role="status">
          {assist.detail ?? 'The analysis failed.'}
        </p>
      )}
      {!shown ? (
        <p className="text-sm text-ink-2">
          {idle
            ? 'Connect a data source to use the assistant.'
            : working
              ? 'Looking at the scene…'
              : 'Ask Gemini what it sees: the patient’s visible state, what to do next, and items to fetch.'}
        </p>
      ) : (
        <div className={cx('space-y-3 transition-opacity', working && 'opacity-60')}>
          <div className="flex flex-wrap items-start gap-2">
            <p className="min-w-0 flex-1 text-sm text-ink">{shown.scene}</p>
            {shown.patient.present && (
              <Chip className={URGENCY[shown.patient.urgency].className}>{URGENCY[shown.patient.urgency].text}</Chip>
            )}
          </div>
          {shown.patient.present && shown.patient.state && (
            <p className="text-xs font-medium text-ink-2">Patient: {PATIENT_STATE[shown.patient.state]}</p>
          )}
          {(shown.touch_checks?.length ?? 0) > 0 && <TouchChecks checks={shown.touch_checks ?? []} />}
          {shown.patient.observations.length > 0 && (
            <div>
              <h3 className="text-xs font-semibold text-ink-2">Patient observations</h3>
              <ul className="mt-1 list-disc pl-5 text-sm text-ink">
                {shown.patient.observations.map((o) => (
                  <li key={o}>{o}</li>
                ))}
              </ul>
            </div>
          )}
          {shown.suggestions.length > 0 && (
            <div>
              <h3 className="text-xs font-semibold text-ink-2">Suggested actions</h3>
              <ol className="mt-1 list-decimal pl-5 text-sm text-ink">
                {shown.suggestions.map((s) => (
                  <li key={s.text}>{s.text}</li>
                ))}
              </ol>
            </div>
          )}
          {shown.items.length > 0 && (
            <div className="border-t border-rule pt-2">
              <h3 className="text-xs font-semibold text-ink-2">Items the patient may need</h3>
              <ul className="divide-y divide-rule">
                {shown.items.map((item) => (
                  <ItemRow key={item.name} item={item} />
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Plate>
  )
}
