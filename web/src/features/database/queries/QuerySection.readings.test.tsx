import { cleanup, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { FIXTURE_NOW, queriesFixture } from '@/lib/monitor/fixtures'
import type { AnalyzeJob } from '@/lib/monitor/types'
import { renderWithClient } from '@/test-utils'

const state = vi.hoisted(() => ({
  reading: 100,
  jobs: [] as AnalyzeJob[],
  first: false,
}))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useRouterState: ({ select }: { select: (state: unknown) => unknown }) =>
    select({ location: { search: {} } }),
}))
vi.mock('@/lib/monitor/hooks', () => ({
  useMonitorQueries: () => ({
    data: {
      total: 2,
      queries: [],
      history: { last_ts: state.reading },
    },
  }),
  useMonitorQuery: () => ({}),
  useAnalyzeJobs: () => ({
    data: {
      jobs: state.jobs,
      queue: { first_hour: state.first ? {} : null },
    },
  }),
  useEnqueueAnalysis: () => ({ mutate: vi.fn() }),
  useInbox: () => ({}),
}))
vi.mock('@/lib/useQueryRegistry', () => ({
  useQueryRegistry: () => ({ queries: [] }),
}))

import { QuerySection } from './QuerySection'

const base = queriesFixture('localdev', '24h', FIXTURE_NOW)
  .queries.filter((query) => /^select/i.test(query.text))
  .slice(0, 2)
const rows = (a: number, b: number) =>
  base.map((query, index) => ({
    ...query,
    key: `q${index}`,
    db_time_ms: index ? b : a,
  }))
const section = (queries: ReturnType<typeof rows>) => (
  <QuerySection
    target="db"
    range="24h"
    domain={[0, 4000000000]}
    model={null}
    scopeLabel="in the last 24 hours"
    hoverKey={null}
    onHoverKey={() => {}}
    scopedRows={queries}
    history={{
      first_ts: 0,
      last_ts: 100,
      span_seconds: 100,
      resolutions: {
        60: { first_ts: 0 },
        300: { first_ts: null },
        3600: { first_ts: null },
        86400: { first_ts: null },
      },
    }}
  />
)
const order = () =>
  screen.getAllByTestId('monitor-query-row').map((row) => row.id)
afterEach(() => {
  cleanup()
  state.jobs = []
  state.first = false
  state.reading = 100
})

it('uses arriving query readings when health history is still older', () => {
  const { rerender } = renderWithClient(section(rows(100, 50)))
  expect(order()).toEqual(['query-q0', 'query-q1'])
  state.reading = 110
  rerender(section(rows(100, 300)))
  expect(order()).toEqual(['query-q1', 'query-q0'])
})

it('reranks when rows arrive after the reading timestamp has already advanced', () => {
  const cached = rows(100, 50)
  const { rerender } = renderWithClient(section(cached))
  state.reading = 110
  rerender(section(cached))
  expect(order()).toEqual(['query-q0', 'query-q1'])
  rerender(section(rows(100, 300)))
  expect(order()).toEqual(['query-q1', 'query-q0'])
})
