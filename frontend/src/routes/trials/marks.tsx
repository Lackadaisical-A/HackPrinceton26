import { DOT_R } from './chartLayout'

/**
 * One trial's mark, for use inside an SVG. The two conditions differ in fill, not only in
 * colour: a trial with haptics is a filled dot in the touch colour, and one without is a hollow
 * ring in ink. The plate-coloured halo keeps a mark clear of its neighbours and of the lines
 * behind it.
 */
export function Mark({ x, y, haptics, halo = true }: { x: number; y: number; haptics: boolean; halo?: boolean }) {
  return (
    <>
      {halo && <circle cx={x} cy={y} r={DOT_R + 2} fill="var(--plate)" />}
      {haptics ? (
        <circle cx={x} cy={y} r={DOT_R} fill="var(--touch-600)" />
      ) : (
        <circle cx={x} cy={y} r={DOT_R - 0.75} fill="var(--plate)" stroke="var(--ink)" strokeWidth={1.5} />
      )}
    </>
  )
}

/** The same mark as a small inline picture, for labels and table cells. The text beside it carries the meaning. */
export function ConditionGlyph({ haptics }: { haptics: boolean }) {
  const size = DOT_R * 2 + 2
  return (
    <svg
      width={size}
      height={size}
      viewBox={`${-size / 2} ${-size / 2} ${size} ${size}`}
      aria-hidden="true"
      focusable="false"
      className="shrink-0"
    >
      <Mark x={0} y={0} haptics={haptics} halo={false} />
    </svg>
  )
}
