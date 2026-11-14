import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/**
 * One run of the demo task. Trials are how PAUL's central claim is tested: does haptic
 * feedback change drops, deformation and completion time?
 */
export interface Trial {
  id: string
  /** Epoch milliseconds. */
  startedAt: number
  durationS: number
  /** The condition: true with haptic feedback, false for the control. */
  haptics: boolean
  /** Counted by the observer during the trial. */
  drops: number
  deformations: number
  outcome: 'completed' | 'aborted'
  /** Highest relative grip (0 to 1) on any pad during the trial. Null if no pressure data arrived. */
  peakGrip: number | null
  /**
   * Where the telemetry came from. Only "live" trials are evidence. "simulated" trials were run
   * against the simulator, and "sample" trials are made-up rows for trying out the page.
   */
  origin: 'live' | 'simulated' | 'sample'
}

interface TrialsStore {
  trials: Trial[]
  add: (trial: Trial) => void
  addMany: (trials: Trial[]) => void
  remove: (id: string) => void
  /** Removes every trial of one origin, or all trials when no origin is given. */
  clear: (origin?: Trial['origin']) => void
}

export const useTrials = create<TrialsStore>()(
  persist(
    (set) => ({
      trials: [],
      add: (trial) => set((s) => ({ trials: [...s.trials, trial] })),
      addMany: (trials) => set((s) => ({ trials: [...s.trials, ...trials] })),
      remove: (id) => set((s) => ({ trials: s.trials.filter((t) => t.id !== id) })),
      clear: (origin) => set((s) => ({ trials: origin ? s.trials.filter((t) => t.origin !== origin) : [] })),
    }),
    { name: 'paul.trials', version: 1, partialize: ({ trials }) => ({ trials }) },
  ),
)

export const newTrialId = (): string =>
  `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
