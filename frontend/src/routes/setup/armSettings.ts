import {
  ARM_LENGTH_KEYS, ARM_LENGTH_RANGE, ARM_POSITION_KEYS, ARM_POSITION_RANGE, SENSOR_ROLES,
  type ArmSettings, type SensorAxis, type SensorRole,
} from '../../telemetry/contracts'

type NumberKey = (typeof ARM_LENGTH_KEYS)[number] | (typeof ARM_POSITION_KEYS)[number]

/** The form's text, as typed. */
export type ArmDraft = Record<NumberKey, string> & { axes: Record<SensorRole, SensorAxis> }

export const ARM_FIELDS: { key: NumberKey; label: string; range: readonly [number, number] }[] = [
  { key: 'upper_cm', label: 'Shoulder → elbow (cm)', range: ARM_LENGTH_RANGE },
  { key: 'forearm_cm', label: 'Elbow → wrist (cm)', range: ARM_LENGTH_RANGE },
  { key: 'hand_cm', label: 'Wrist → fingertips (cm)', range: ARM_LENGTH_RANGE },
  { key: 'bicep_from_elbow_pct', label: 'Bicep sensor: % from elbow toward shoulder', range: ARM_POSITION_RANGE },
  { key: 'forearm_from_wrist_pct', label: 'Forearm sensor: % from wrist toward elbow', range: ARM_POSITION_RANGE },
  { key: 'hand_from_wrist_pct', label: 'Hand sensor: % from wrist toward fingertips', range: ARM_POSITION_RANGE },
]

export const ROLE_LABELS: Record<SensorRole, string> = { upper: 'Bicep', forearm: 'Forearm', hand: 'Hand' }

const KEYS = [...ARM_LENGTH_KEYS, ...ARM_POSITION_KEYS]
/** Up to three decimals, without trailing zeros: 33.333, 25, 18.5. */
const show = (value: number): string => String(Math.round(value * 1000) / 1000)

export function draftFrom(values: ArmSettings): ArmDraft {
  return {
    ...(Object.fromEntries(KEYS.map((key) => [key, show(values[key])])) as Record<NumberKey, string>),
    axes: { ...values.axes },
  }
}

/** The settings a draft describes, or the first problem with it (matching Python's wording). */
export function parseDraft(draft: ArmDraft): { settings: ArmSettings; error: null } | { settings: null; error: string } {
  const numbers: Partial<Record<NumberKey, number>> = {}
  for (const { key, label, range: [lo, hi] } of ARM_FIELDS) {
    const text = draft[key].trim()
    const value = Number(text)
    if (text === '' || !Number.isFinite(value) || value < lo || value > hi) {
      const kind = ARM_LENGTH_KEYS.includes(key as (typeof ARM_LENGTH_KEYS)[number]) ? 'Segment lengths must be 5-60 cm' : 'Sensor positions must be 0-100% along their segment'
      return { settings: null, error: `${label.replace(/ \(.*\)$/, '')}: ${kind}.` }
    }
    numbers[key] = value
  }
  return { settings: { ...(numbers as Record<NumberKey, number>), axes: { ...draft.axes } }, error: null }
}

/** Whether two settings are the same within display precision. */
export function sameSettings(a: ArmSettings, b: ArmSettings): boolean {
  return KEYS.every((key) => Math.abs(a[key] - b[key]) < 0.0005) && SENSOR_ROLES.every((role) => a.axes[role] === b.axes[role])
}

/** All three axes chosen by hand: pose A alone calibrates. Otherwise A and B learn the mounting. */
export const manualAxes = (settings: { axes: Record<SensorRole, SensorAxis> }): boolean =>
  SENSOR_ROLES.every((role) => settings.axes[role] !== 'Auto')
