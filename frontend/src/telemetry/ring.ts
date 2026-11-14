/**
 * Fixed-capacity ring buffer of numeric rows, backed by one typed array.
 * Pushing never allocates, so it is safe to call at sensor rate.
 */
export class Ring {
  readonly stride: number
  readonly capacity: number
  private readonly data: Float64Array
  private head = 0
  private count = 0

  constructor(capacity: number, stride: number) {
    this.capacity = capacity
    this.stride = stride
    this.data = new Float64Array(capacity * stride)
  }

  get length(): number {
    return this.count
  }

  /** Appends one row. Missing fields are stored as 0. */
  push(a: number, b = 0, c = 0, d = 0): void {
    const base = this.head * this.stride
    this.data[base] = a
    if (this.stride > 1) this.data[base + 1] = b
    if (this.stride > 2) this.data[base + 2] = c
    if (this.stride > 3) this.data[base + 3] = d
    this.head = (this.head + 1) % this.capacity
    if (this.count < this.capacity) this.count++
  }

  /** Field `field` of row `index`, where index 0 is the oldest row still held. */
  at(index: number, field = 0): number {
    const row = (this.head - this.count + index + this.capacity * 2) % this.capacity
    return this.data[row * this.stride + field]
  }

  /** The newest row's field, or `fallback` when empty. */
  last(field = 0, fallback = 0): number {
    return this.count ? this.at(this.count - 1, field) : fallback
  }

  clear(): void {
    this.head = 0
    this.count = 0
  }
}
