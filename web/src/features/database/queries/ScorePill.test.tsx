import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { FIXTURE_NOW, queriesFixture } from '@/lib/monitor/fixtures'
import {
  GradeTile,
  GradeTilePlaceholder,
  gradeLabel,
  rowScore,
  scoreBand,
} from './ScorePill'

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

describe('scoreBand', () => {
  it('uses three quiet steps: good from 70, fair from 40, poor below', () => {
    expect(scoreBand(98)).toBe('good')
    expect(scoreBand(70)).toBe('good')
    expect(scoreBand(69)).toBe('fair')
    expect(scoreBand(40)).toBe('fair')
    expect(scoreBand(39)).toBe('poor')
  })
})

it('shows the score out of 100, never a letter', () => {
  const score = rowScore(withSummary('fair', 55))!
  const { container } = render(<GradeTile score={score} />)
  expect(screen.getByText('55')).toBeTruthy()
  expect(screen.getByText('/100')).toBeTruthy()
  expect(gradeLabel(score)).toBe('Score 55 of 100')
  expect(container.querySelector('[data-band=fair]')).toBeTruthy()
})

it('scores from the rating when no number is sent', () => {
  const score = rowScore(withSummary('good', null))!
  expect(gradeLabel(score)).toBe('Rated good')
  render(<GradeTile score={score} />)
  expect(screen.getByText('80')).toBeTruthy()
})

it('says "No score" in a placeholder of the same size', () => {
  render(
    <GradeTilePlaceholder label="No score: RDST doesn't run UPDATE statements" />
  )
  expect(
    screen.getByRole('img', {
      name: "No score: RDST doesn't run UPDATE statements",
    })
  ).toBeTruthy()
})
