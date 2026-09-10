import { describe, expect, it } from 'vitest'
import type { CacheCompareRunResult } from '../../../types/cache'
import type { CompareQueryOutcome } from './compareRuns'
import {
  compareBatchDurationEstimate,
  compareQueryOutcomePresentation,
  compareQueryStatusChip,
  compareStatusPresentation,
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
      icon: 'minus',
    })
    expect(compareQueryOutcomePresentation(outcome('running'))).toEqual({
      label: 'Running',
      variant: 'informative',
      icon: 'play',
    })
  })

  it('reads a stopped comparison as neutral rather than as a fault', () => {
    expect(compareQueryOutcomePresentation(outcome('cancelled'))).toEqual({
      label: 'Cancelled',
      variant: 'neutral',
      icon: 'minus',
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
    ).toEqual({ label: 'Unsupported', variant: 'warning', icon: 'alert' })
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
    ).toEqual({ label: 'Not comparable', variant: 'warning', icon: 'alert' })
  })

  it('still presents a genuine failure as negative', () => {
    expect(
      compareQueryOutcomePresentation(
        outcome('failed', { message: 'Speed test failed' })
      )
    ).toEqual({ label: 'Failed', variant: 'negative', icon: 'close' })
  })
})

describe('compareQueryStatusChip', () => {
  it('classifies a measured query rather than repeating its speedup', () => {
    expect(compareQueryStatusChip(measured(3.2))).toEqual({
      label: 'Compared',
      variant: 'positive',
      icon: 'tick-double',
    })
  })

  it('falls back to the outcome verdict for everything else', () => {
    expect(
      compareQueryStatusChip(
        outcome('failed', { message: 'Speed test failed' })
      )
    ).toEqual({ label: 'Failed', variant: 'negative', icon: 'close' })
  })
})

describe('compareStatusPresentation', () => {
  // GUIDELINES section 4: a batch that failed must not read like one that
  // completed, and every status carries colour + icon + label.
  it('gives each batch status its own tone and icon', () => {
    expect(compareStatusPresentation('running')).toEqual({
      label: 'Comparing',
      variant: 'informative',
      icon: 'play',
    })
    expect(compareStatusPresentation('complete')).toEqual({
      label: 'Complete',
      variant: 'positive',
      icon: 'tick-double',
    })
    expect(compareStatusPresentation('partial')).toEqual({
      label: 'Completed with errors',
      variant: 'warning',
      icon: 'alert',
    })
    expect(compareStatusPresentation('failed')).toEqual({
      label: 'Failed',
      variant: 'negative',
      icon: 'close',
    })
    expect(compareStatusPresentation('cancelled')).toEqual({
      label: 'Cancelled',
      variant: 'neutral',
      icon: 'minus',
    })
  })

  it('never gives two statuses the same tone', () => {
    const tones = (
      ['running', 'complete', 'partial', 'failed', 'cancelled'] as const
    ).map((status) => compareStatusPresentation(status).variant)
    expect(new Set(tones).size).toBe(tones.length)
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
