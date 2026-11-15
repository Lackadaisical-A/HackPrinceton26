import { describe, expect, it } from 'vitest'
import { DETECTOR_CLASSES } from '../config'
import { validateLabels } from '../telemetry/contracts'
import { ANY_TARGET, describeLearned, targetNames, targetOptions } from './targets'

const labels = {
  classes: ['cup', 'bottle'],
  tags: [{ name: 'vaseline', views: 4 }, { name: 'cup', views: 1 }, { name: 'airpods', views: 1 }],
}

describe('targetNames', () => {
  it('falls back to the built-in classes before vision has said what it knows', () => {
    expect(targetNames(null)).toEqual([...DETECTOR_CLASSES])
  })

  it('lists the classes, then every taught name, each once', () => {
    expect(targetNames(labels)).toEqual(['cup', 'bottle', 'vaseline', 'airpods'])
  })
})

describe('targetOptions', () => {
  it('offers any object first, then every name', () => {
    expect(targetOptions(labels)).toEqual([
      { value: ANY_TARGET, label: 'Any object' },
      { value: 'cup', label: 'Cup' },
      { value: 'bottle', label: 'Bottle' },
      { value: 'vaseline', label: 'Vaseline' },
      { value: 'airpods', label: 'Airpods' },
    ])
  })

  it('never collides with a name', () => {
    expect(targetNames(labels)).not.toContain(ANY_TARGET)
    expect(ANY_TARGET).toContain('#') // vision refuses a hash sign in a name
  })
})

describe('describeLearned', () => {
  it('names each taught object with its number of views', () => {
    expect(describeLearned(labels)).toBe('vaseline (4 views), cup (1 view), airpods (1 view)')
    expect(describeLearned({ classes: ['cup'], tags: [] })).toBe('')
    expect(describeLearned(null)).toBe('')
  })
})

describe('validateLabels', () => {
  const ok = { t: 1731542400.1, ...labels, busy: null }

  it('accepts what vision sends', () => {
    expect(validateLabels(ok)).toBeNull()
    expect(validateLabels({ ...ok, tags: [], busy: 'vaseline' })).toBeNull()
  })

  it.each([
    ['classes that are not names', { ...ok, classes: ['cup', ''] }],
    ['classes that are not a list', { ...ok, classes: 'cup' }],
    ['a tag without views', { ...ok, tags: [{ name: 'vaseline' }] }],
    ['a tag with no views', { ...ok, tags: [{ name: 'vaseline', views: 0 }] }],
    ['a tag listed twice', { ...ok, tags: [{ name: 'a', views: 1 }, { name: 'a', views: 2 }] }],
    ['an empty busy name', { ...ok, busy: '' }],
    ['a missing busy', { t: ok.t, classes: ok.classes, tags: ok.tags }],
  ])('rejects %s', (_, message) => {
    expect(validateLabels(message)).not.toBeNull()
  })
})
