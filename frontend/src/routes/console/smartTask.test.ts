import { describe, expect, it } from 'vitest'
import type { ReplayStatus, TaskMessage } from '../../telemetry/contracts'
import { confirmedBrief, taskFor } from './smartTask'

const brief = { summary: 's', rule: 'r', done_when: 'd', objects: [], steps: [{ text: 't', from: 0, to: 1, expect: 'e' }] }
const replay = (over: Partial<ReplayStatus> = {}): ReplayStatus => ({ phase: 'ready', revision: 1, duration_s: 5, elapsed_s: 0,
  samples: 10, includes_slider: false, detail: '', request: null, recorded_wall: 100, ...over })
const task = (over: Partial<TaskMessage> = {}): TaskMessage => ({ t: 1, seq: 1, status: 'ok', detail: null, model: 'm',
  recording: 100, brief, ...over })

describe('smart replay task', () => {
  it('matches a task to its recording only', () => {
    expect(taskFor(task(), replay())).not.toBeNull()
    expect(taskFor(task({ recording: 99 }), replay())).toBeNull()
    expect(taskFor(task(), replay({ recorded_wall: null }))).toBeNull()
    expect(taskFor(null, replay())).toBeNull()
  })

  it('needs a studied, confirmed brief and a ready recording to start', () => {
    expect(confirmedBrief(task(), replay(), 100)).toBe(brief)
    expect(confirmedBrief(task(), replay(), null)).toBeNull()
    expect(confirmedBrief(task(), replay(), 99)).toBeNull()
    expect(confirmedBrief(task({ status: 'working', brief: null }), replay(), 100)).toBeNull()
    expect(confirmedBrief(task(), replay({ phase: 'recording' }), 100)).toBeNull()
  })
})
