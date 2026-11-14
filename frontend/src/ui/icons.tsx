import type { ReactNode, SVGProps } from 'react'

/** Drawn for this app on a 16 px grid. All are decorative: the text beside them carries meaning. */

type IconProps = Omit<SVGProps<SVGSVGElement>, 'children'> & { size?: number }

function Svg({ size = 16, children, ...rest }: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  )
}

/** The PAUL mark: two gripper jaws closing on one point of contact. */
export function IconMark({ size = 20, ...rest }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false" {...rest}>
      {/* Operator and robot as two circles; the shared lens is where they connect. */}
      <circle cx="12" cy="16" r="9" fill="var(--touch-400)" />
      <circle cx="20" cy="16" r="9" fill="var(--touch-200)" />
      <path d="M16 8.6a9 9 0 0 1 0 14.8a9 9 0 0 1 0-14.8z" fill="var(--touch-600)" />
    </svg>
  )
}

/** Caution: a triangle. Shape distinguishes it from a warning without relying on colour. */
export function IconCaution(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8 2.2 14.4 13.5H1.6Z" />
      <path d="M8 6.6v3.2" />
      <path d="M8 11.6v.1" />
    </Svg>
  )
}

/** Warning: an octagon. */
export function IconWarning(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5.5 1.8h5l3.7 3.7v5l-3.7 3.7h-5L1.8 10.5v-5Z" />
      <path d="M8 4.8v4" />
      <path d="M8 11.2v.1" />
    </Svg>
  )
}

export function IconRecord(props: IconProps) {
  return (
    <Svg {...props} stroke="none" fill="currentColor">
      <circle cx="8" cy="8" r="4.5" />
    </Svg>
  )
}

export function IconStop(props: IconProps) {
  return (
    <Svg {...props} stroke="none" fill="currentColor">
      <rect x="4" y="4" width="8" height="8" rx="1" />
    </Svg>
  )
}

export function IconPause(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5.5 3.5v9M10.5 3.5v9" />
    </Svg>
  )
}

export function IconPlay(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5 3.2v9.6L12.5 8Z" />
    </Svg>
  )
}

export function IconSpeaker(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M2 6.2v3.6h2.4L8 12.8V3.2L4.4 6.2Z" />
      <path d="M10.6 5.6a3.4 3.4 0 0 1 0 4.8M12.6 3.6a6.2 6.2 0 0 1 0 8.8" />
    </Svg>
  )
}

export function IconSpeakerOff(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M2 6.2v3.6h2.4L8 12.8V3.2L4.4 6.2Z" />
      <path d="m10.8 6 3.6 4M14.4 6l-3.6 4" />
    </Svg>
  )
}

export function IconKeyboard(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="1.5" y="3.5" width="13" height="9" rx="1.5" />
      <path d="M4.2 6.4h.1M6.7 6.4h.1M9.2 6.4h.1M11.7 6.4h.1M4.5 9.6h7" />
    </Svg>
  )
}

export function IconDownload(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8 2.5v7.4M4.8 7 8 10.2 11.2 7M2.8 13.2h10.4" />
    </Svg>
  )
}

export function IconTrash(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M2.8 4.4h10.4M6.2 4.4V2.8h3.6v1.6M4.2 4.4l.6 8.8h6.4l.6-8.8M6.8 7v3.6M9.2 7v3.6" />
    </Svg>
  )
}

export function IconClose(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="m3.8 3.8 8.4 8.4M12.2 3.8l-8.4 8.4" />
    </Svg>
  )
}

export function IconCheck(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="m3 8.4 3.2 3.2L13 4.8" />
    </Svg>
  )
}

export function IconTable(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="1.8" y="2.8" width="12.4" height="10.4" rx="1.2" />
      <path d="M1.8 6.2h12.4M1.8 9.7h12.4M6 6.2v7" />
    </Svg>
  )
}
