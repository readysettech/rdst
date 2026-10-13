import { expect, it } from 'vitest'
import { FIXTURE_NOW, queriesFixture } from '@/lib/monitor/fixtures'
import { rowScore, scoreValue } from './ScorePill'

const query = queriesFixture('localdev', '24h', FIXTURE_NOW).queries[0]

function withSummary(rating: string, efficiency_score: number | null) {
  return {
    ...query,
    last_analysis: {
      ...query.last_analysis!,
      state: 'done' as const,
      summary: { ...query.last_analysis!.summary!, rating, efficiency_score },
    },
  }
}

it('reads the score out of 100', () => {
  const score = rowScore(withSummary('fair', 55))!
  expect(scoreValue(score)).toBe(55)
})

it('scores from the rating when no number is sent', () => {
  const score = rowScore(withSummary('good', null))!
  expect(scoreValue(score)).toBe(80)
})
