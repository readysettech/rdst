import { describe, expect, it } from 'vitest'
import {
  progressFraction,
  STEP_LABELS,
  stepsForPercent,
  stepsForPhase,
} from './analyzingSteps'

describe('analyzingSteps', () => {
  it('marks the step under way from the phase monitoring reports', () => {
    expect(stepsForPhase('run').map((step) => step.state)).toEqual([
      'done',
      'done',
      'now',
      'todo',
    ])
    expect(stepsForPhase(null).map((step) => step.state)).toEqual([
      'now',
      'todo',
      'todo',
      'todo',
    ])
    expect(stepsForPhase('advise')[3]).toEqual({
      label: STEP_LABELS[3],
      state: 'now',
    })
  })

  it('reads a quarter of the run per step from a percent', () => {
    expect(stepsForPercent(10)[0].state).toBe('now')
    expect(stepsForPercent(60)[2].state).toBe('now')
    expect(stepsForPercent(100)[3].state).toBe('now')
  })

  it('fills the line by the steps done plus half the current one', () => {
    expect(progressFraction(stepsForPhase('run'))).toBe(0.625)
    expect(progressFraction(stepsForPhase(null))).toBe(0.125)
  })
})
