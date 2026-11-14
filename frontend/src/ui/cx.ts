/** Joins class names, skipping anything falsy. */
export function cx(...parts: (string | false | null | undefined)[]): string {
  let out = ''
  for (const part of parts) {
    if (part) out += out ? ` ${part}` : part
  }
  return out
}
