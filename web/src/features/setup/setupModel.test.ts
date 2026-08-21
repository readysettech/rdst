import { describe, expect, it } from 'vitest'
import {
  completedSetupSteps,
  hasSetupSignal,
  isSetupComplete,
  isSetupGuideSuppressed,
  SETUP_STEP_COUNT,
  type SetupProgress,
  setupSteps,
} from './setupModel'

function progress(overrides: Partial<SetupProgress> = {}): SetupProgress {
  return {
    target: 'orders',
    connected: false,
    schema_built: false,
    queries_found: false,
    analyzed: false,
    compared: false,
    ...overrides,
  }
}

describe('setupModel', () => {
  it('maps every signal to one labelled step and its deep link', () => {
    const steps = setupSteps(progress({ connected: true, schema_built: true }))

    expect(steps).toHaveLength(SETUP_STEP_COUNT)
    expect(steps.map((step) => [step.label, step.to, step.done])).toEqual([
      ['Connect a database', '/onboarding', true],
      ['Build the schema', '/schema', true],
      ['Find your queries', '/queries', false],
      ['Analyze a query', '/queries', false],
      ['Compare origin vs Readyset', '/cache', false],
    ])
  })

  it('names the query the analyze step should open, when one is known', () => {
    const withQuery = setupSteps(progress(), { analyzeHash: 'top-hash' })
    expect(withQuery[3].search).toEqual({ analyze: 'top-hash' })
    expect(withQuery[3].to).toBe('/queries')

    // Nothing observed yet: the library itself is the honest destination.
    expect(setupSteps(progress())[3].search).toBeUndefined()
  })

  it('keeps every why-line to eight words or fewer', () => {
    for (const step of setupSteps(progress())) {
      expect(step.why.split(' ').length).toBeLessThanOrEqual(8)
    }
  })

  it('counts completion from the signals alone', () => {
    expect(completedSetupSteps(progress())).toBe(0)
    expect(completedSetupSteps(progress({ analyzed: true }))).toBe(1)
    expect(isSetupComplete(progress({ connected: true }))).toBe(false)
    expect(
      isSetupComplete(
        progress({
          connected: true,
          schema_built: true,
          queries_found: true,
          analyzed: true,
          compared: true,
        })
      )
    ).toBe(true)
  })

  it('treats a reported error as unknown rather than as zero progress', () => {
    expect(hasSetupSignal(undefined)).toBe(false)
    expect(hasSetupSignal(progress({ error: 'no config' }))).toBe(false)
    expect(hasSetupSignal(progress())).toBe(true)
  })

  it('suppresses itself on the demo tour and on onboarding itself', () => {
    expect(isSetupGuideSuppressed('/demo')).toBe(true)
    expect(isSetupGuideSuppressed('/onboarding')).toBe(true)
    expect(isSetupGuideSuppressed('/')).toBe(false)
    expect(isSetupGuideSuppressed('/queries')).toBe(false)
  })
})
