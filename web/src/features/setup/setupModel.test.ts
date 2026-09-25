import { describe, expect, it } from 'vitest'
import {
  completedSetupSteps,
  hasSetupSignal,
  isSetupComplete,
  SETUP_STEP_COUNT,
  type SetupProgress,
  setupSteps,
} from './setupModel'

function progress(overrides: Partial<SetupProgress> = {}): SetupProgress {
  return { target: 'orders', connected: false, ...overrides }
}

describe('setupModel', () => {
  it('is two steps: connect a database, then turn on monitoring', () => {
    const steps = setupSteps(progress({ connected: true }))

    expect(steps).toHaveLength(SETUP_STEP_COUNT)
    expect(steps.map((step) => [step.label, step.to, step.done])).toEqual([
      ['Connect a database', '/onboarding', true],
      ['Turn on monitoring', '/database', false],
    ])
  })

  it('never counts schema discovery or library activity as setup', () => {
    const done = progress({
      schema_built: true,
      queries_found: true,
      analyzed: true,
      compared: true,
    })
    expect(completedSetupSteps(done)).toBe(0)
  })

  it('keeps every why-line to eight words or fewer', () => {
    for (const step of setupSteps(progress())) {
      expect(step.why.split(' ').length).toBeLessThanOrEqual(8)
    }
  })

  it('counts completion from the signals alone', () => {
    expect(completedSetupSteps(progress())).toBe(0)
    expect(completedSetupSteps(progress({ connected: true }))).toBe(1)
    expect(isSetupComplete(progress({ connected: true }))).toBe(false)
    expect(
      isSetupComplete(progress({ connected: true, monitoring: true }))
    ).toBe(true)
  })

  it('treats a reported error as unknown rather than as zero progress', () => {
    expect(hasSetupSignal(undefined)).toBe(false)
    expect(hasSetupSignal(progress({ error: 'no config' }))).toBe(false)
    expect(hasSetupSignal(progress())).toBe(true)
  })
})
