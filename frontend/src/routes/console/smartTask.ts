import type { ReplayStatus, TaskBrief, TaskMessage } from '../../telemetry/contracts'

/** Gemini's `task` message for this recording, if it is about this recording. */
export function taskFor(task: TaskMessage | null | undefined, replay: ReplayStatus | null | undefined): TaskMessage | null {
  const recording = replay?.recorded_wall ?? null
  return task && recording !== null && task.recording === recording ? task : null
}

/** The brief a smart replay may start from: studied, for this recording, and confirmed by the operator. */
export function confirmedBrief(task: TaskMessage | null | undefined, replay: ReplayStatus | null | undefined,
  confirmed: number | null): TaskBrief | null {
  const current = taskFor(task, replay)
  return current?.status === 'ok' && replay?.phase === 'ready' && confirmed === replay.recorded_wall ? current.brief : null
}
