/**
 * A unique id for a controller request. `crypto.randomUUID` exists only in secure contexts, so a
 * console opened over plain http on the LAN falls back to `getRandomValues`, which is always there.
 */
export function newRequestId(): string {
  const c = globalThis.crypto
  if (typeof c?.randomUUID === 'function') return c.randomUUID()
  const bytes = new Uint8Array(16)
  c.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}
