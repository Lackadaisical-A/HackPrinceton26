/** A real minus sign, so negative numbers are as wide as positive ones with a plus. */
const MINUS = '−'

/** Fixed decimals with an explicit sign: "+0.12", "−0.05". Keeps a live column from jittering. */
export function signed(value: number, digits = 2): string {
  const text = Math.abs(value).toFixed(digits)
  if (Number(text) === 0) return ` ${text}`
  return (value < 0 ? MINUS : '+') + text
}

export function fixed(value: number, digits = 2): string {
  return value < 0 ? MINUS + Math.abs(value).toFixed(digits) : value.toFixed(digits)
}

/** "0:42", "12:05", "1:02:03". */
export function clock(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = String(s % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
}

/** Wall-clock time of day, "14:03:27". */
export function timeOfDay(epochMs: number): string {
  const d = new Date(epochMs)
  const two = (n: number) => String(n).padStart(2, '0')
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`
}

/** "20261114-140327", for file names. */
export function fileStamp(epochMs: number): string {
  const d = new Date(epochMs)
  const two = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}${two(d.getMonth() + 1)}${two(d.getDate())}` +
    `-${two(d.getHours())}${two(d.getMinutes())}${two(d.getSeconds())}`
  )
}

export function sentenceCase(text: string): string {
  return text.length ? text[0].toUpperCase() + text.slice(1) : text
}

/** Seconds with one decimal and a non-breaking space before the unit: "12.4 s". */
export function seconds(value: number): string {
  return `${value.toFixed(1)} s`
}

/** Triggers a browser download of text content. */
export function downloadText(filename: string, text: string | Blob, type = 'text/plain'): void {
  const blob = typeof text === 'string' ? new Blob([text], { type }) : text
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.append(a)
  a.click()
  a.remove()
  // Give the browser a moment to start the download before the URL is released.
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
