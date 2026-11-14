/**
 * One requestAnimationFrame loop for every canvas and live readout.
 *
 * Tasks registered with an element are skipped while that element is off screen, and the whole
 * loop stops while the tab is hidden. A task may ask for a lower rate than the display's.
 */

export type Run = (nowMs: number, dtMs: number) => void

interface Task {
  run: Run
  intervalMs: number
  lastMs: number
  onScreen: boolean
}

const tasks = new Set<Task>()
const tasksByElement = new Map<Element, Set<Task>>()
let frame = 0
let observer: IntersectionObserver | null = null

function loop(nowMs: number): void {
  frame = 0
  for (const task of tasks) {
    if (!task.onScreen) continue
    const dt = task.lastMs === 0 ? 0 : nowMs - task.lastMs
    // Half a frame of tolerance, so a 30 Hz task on a 60 Hz display does not slip to 20 Hz.
    if (task.lastMs !== 0 && dt < task.intervalMs - 8) continue
    task.lastMs = nowMs
    task.run(nowMs, dt)
  }
  wake()
}

function wake(): void {
  if (frame === 0 && tasks.size > 0 && !document.hidden) frame = requestAnimationFrame(loop)
}

function watch(el: Element, task: Task): void {
  observer ??= new IntersectionObserver((entries) => {
    for (const entry of entries) {
      const group = tasksByElement.get(entry.target)
      if (!group) continue
      for (const t of group) {
        t.onScreen = entry.isIntersecting
        // Forget the last run time, so the first frame back does not see a huge time step.
        if (entry.isIntersecting) t.lastMs = 0
      }
    }
  })
  let group = tasksByElement.get(el)
  if (!group) {
    group = new Set()
    tasksByElement.set(el, group)
    observer.observe(el)
  }
  group.add(task)
}

function unwatch(el: Element, task: Task): void {
  const group = tasksByElement.get(el)
  if (!group) return
  group.delete(task)
  if (group.size === 0) {
    tasksByElement.delete(el)
    observer?.unobserve(el)
  }
}

if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (frame !== 0) cancelAnimationFrame(frame)
      frame = 0
      return
    }
    for (const task of tasks) task.lastMs = 0
    wake()
  })
}

export interface ScheduleOptions {
  /** Skip the task while this element is off screen. */
  el?: Element | null
  /** Run at most this many times a second. Defaults to every frame. */
  hz?: number
}

/** Registers a task with the shared loop. Returns a function that removes it. */
export function schedule(run: Run, options: ScheduleOptions = {}): () => void {
  const task: Task = {
    run,
    intervalMs: options.hz ? 1000 / options.hz : 0,
    lastMs: 0,
    onScreen: true,
  }
  tasks.add(task)
  const el = options.el ?? null
  if (el) watch(el, task)
  wake()
  return () => {
    tasks.delete(task)
    if (el) unwatch(el, task)
  }
}

// --- Reduced motion ----------------------------------------------------------------------------

let reducedMotion: boolean | null = null

/**
 * True when the person has asked for less motion. Decorative motion is dropped. Data is still
 * drawn at full rate, because a trace that lags the sensor would be a worse instrument.
 */
export function prefersReducedMotion(): boolean {
  if (reducedMotion === null) {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    reducedMotion = query.matches
    query.addEventListener('change', (event) => {
      reducedMotion = event.matches
    })
  }
  return reducedMotion
}
