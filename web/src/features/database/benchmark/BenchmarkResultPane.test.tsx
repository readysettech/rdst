import { cleanup, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { resetStubState, setFixtureTiming } from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import { CA_NOW, caFixtures } from '@/lib/monitor/fixturesCa'
import { renderWithClient } from '@/test-utils'
import { BenchmarkResultPane } from './BenchmarkResultPane'
import { queriesWithFixes } from './fixBenchmarkModel'

beforeEach(() => {
  setFixtureTiming(0)
  resetStubState()
  setFixtureOverride('ca-database')
})
afterEach(() => {
  cleanup()
  setFixtureOverride(undefined)
})

it('lists each fix with before, after and change, the verdict and the method', async () => {
  const queries = queriesWithFixes(
    caFixtures('ca-database').queries('demo', '1h', 500, 0).queries
  )
  renderWithClient(
    <BenchmarkResultPane
      target="demo"
      queries={queries}
      ranAt={CA_NOW - 120}
      now={CA_NOW}
      onRerun={vi.fn()}
      onClose={vi.fn()}
    />
  )
  const pane = screen.getByRole('region', { name: 'Benchmark, 9 queries' })
  expect(within(pane).getByText('Benchmark')).toBeTruthy()
  expect(within(pane).getByText('9 queries, 2 min ago')).toBeTruthy()
  expect(within(pane).getByText('Before, ms')).toBeTruthy()
  expect(await within(pane).findByText('Estimate, ms')).toBeTruthy()
  expect(within(pane).getByText('SELECT on votes by postid')).toBeTruthy()
  expect(await within(pane).findByText('65.4')).toBeTruthy()
  expect(
    await within(pane).findByText('All 9 faster with their fixes.', {
      exact: false,
    })
  ).toBeTruthy()
  expect(
    within(pane).getByText('Total time on demo would fall 54%.', {
      exact: false,
    })
  ).toBeTruthy()
  expect(within(pane).getByText('3.10')).toBeTruthy()
  expect(within(pane).getByText('-96%')).toBeTruthy()
  expect(within(pane).getByText('31.5')).toBeTruthy()
  expect(within(pane).getByText('-53%')).toBeTruthy()
  expect(
    within(pane).getByText(
      'Estimated from the planner on demo: each fix tested as a hypothetical index, the same estimate Actions shows.'
    )
  ).toBeTruthy()
  const solids = within(pane)
    .getAllByRole('button')
    .filter((button) => button.className.includes('bg-surface-primary-solid'))
  expect(solids.map((button) => button.textContent)).toEqual(['Copy all fixes'])
  expect(within(pane).getByRole('button', { name: 'Rerun' })).toBeTruthy()
})
