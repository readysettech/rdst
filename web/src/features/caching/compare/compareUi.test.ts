import { describe, expect, it } from 'vitest'
import type { CacheCompareRunResult } from '../../../types/cache'
import type { CompareQueryOutcome } from './compareRuns'
import {
  compareBatchDurationEstimate,
  compareQueryOutcomePresentation,
} from './compareUi'

function outcome(
  status: CompareQueryOutcome['status'],
  overrides: Partial<CompareQueryOutcome> = {}
): CompareQueryOutcome {
  return {
    cacheId: 'one',
    label: 'One',
    status,
    timeline: [],
    elapsedSeconds: 0,
    ...overrides,
  }
}

function measured(speedupMean: number) {
  return outcome('succeeded', {
    result: { speedup_mean: speedupMean } as CacheCompareRunResult,
  })
}

describe('compareQueryOutcomePresentation', () => {
  it('presents a measurement as its own speedup, not a generic verdict', () => {
    expect(compareQueryOutcomePresentation(measured(3.2))).toEqual({
      label: '3.2× faster',
      variant: 'positive',
    })
    expect(compareQueryOutcomePresentation(measured(12))).toEqual({
      label: '12× faster',
      variant: 'positive',
    })
  })

  it('does not name a winner when the two lanes are within the tie margin', () => {
    expect(compareQueryOutcomePresentation(measured(1.01))).toEqual({
      label: 'About the same',
      variant: 'warning',
    })
    expect(compareQueryOutcomePresentation(measured(0.5))).toEqual({
      label: '2.0× slower',
      variant: 'warning',
    })
  })

  it('presents a measurement without a stored result as measured', () => {
    expect(compareQueryOutcomePresentation(outcome('succeeded'))).toEqual({
      label: 'Measured',
      variant: 'positive',
    })
  })

  it('separates waiting for the sandbox from measuring', () => {
    expect(compareQueryOutcomePresentation(outcome('queued'))).toEqual({
      label: 'Queued',
      variant: 'neutral',
    })
    expect(compareQueryOutcomePresentation(outcome('running'))).toEqual({
      label: 'Running',
      variant: 'informative',
    })
  })

  it('presents a Readyset-unsupported failure as a warning, not an error', () => {
    expect(
      compareQueryOutcomePresentation(
        outcome('failed', {
          errorCode: 'readyset_unsupported',
          message: 'This query is unsupported by Readyset.',
        })
      )
    ).toEqual({ label: 'Unsupported', variant: 'warning' })
  })

  it('presents a result-mismatch pre-flight exclusion as a warning, not an error', () => {
    expect(
      compareQueryOutcomePresentation(
        outcome('failed', {
          message:
            'This query uses LIMIT without ORDER BY, so the database may ' +
            'return any matching rows and the two results are not comparable.',
        })
      )
    ).toEqual({ label: 'Not comparable', variant: 'warning' })
  })

  it('still presents a genuine failure as negative', () => {
    expect(
      compareQueryOutcomePresentation(
        outcome('failed', { message: 'Speed test failed' })
      )
    ).toEqual({ label: 'Failed', variant: 'negative' })
  })
})

describe('compareBatchDurationEstimate', () => {
  it('discloses that a multi-query batch is serialized, with its cost', () => {
    expect(compareBatchDurationEstimate(4, 30)).toBe(
      '4 queries run one at a time · about 2 minutes'
    )
    expect(compareBatchDurationEstimate(2, 30)).toBe(
      '2 queries run one at a time · about 1 minute'
    )
  })

  it('says nothing about serialization for a single query', () => {
    expect(compareBatchDurationEstimate(1, 30)).toBeNull()
    expect(compareBatchDurationEstimate(0, 30)).toBeNull()
  })
})
