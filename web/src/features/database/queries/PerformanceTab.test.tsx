import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { FIXTURE_NOW, queriesFixture } from '@/lib/monitor/fixtures'
import { PerformanceTab } from './PerformanceTab'

vi.mock('@/lib/monitor/hooks', () => ({
  useQuerySeries: () => ({
    data: {
      res: 60,
      history: { first_ts: FIXTURE_NOW - 3600, span_seconds: 3600 },
      points: [
        {
          ts: FIXTURE_NOW - 120,
          calls: null,
          qps: null,
          mean_ms: null,
          total_ms: null,
        },
      ],
      markers: [],
      detail_note: 'Detail is kept for the top 1,000 queries by load',
    },
    isError: false,
  }),
}))

afterEach(cleanup)

it('shows the retention note and renders charts with missing tail series', () => {
  const query = queriesFixture('localdev', '1h', FIXTURE_NOW).queries[0]
  render(<PerformanceTab query={query} target="localdev" range="1h" />)
  expect(
    screen.getByText('Detail is kept for the top 1,000 queries by load')
  ).toBeTruthy()
  expect(screen.getAllByText('Mean latency')[0]).toBeTruthy()
  expect(screen.getAllByText('Calls per second')[0]).toBeTruthy()
})
