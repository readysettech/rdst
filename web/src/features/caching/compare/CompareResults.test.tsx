import { describe, expect, it } from 'vitest'
import { compareQueryOutcomePresentation } from './CompareResults'
import type { CompareQueryOutcome } from './compareRuns'

function outcome(
  status: CompareQueryOutcome['status'],
  overrides: Partial<CompareQueryOutcome> = {}
): CompareQueryOutcome {
  return { cacheId: 'one', label: 'One', status, ...overrides }
}

describe('compareQueryOutcomePresentation', () => {
  it('presents a measurement as positive', () => {
    expect(compareQueryOutcomePresentation(outcome('succeeded'))).toEqual({
      label: 'Measured',
      variant: 'positive',
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
