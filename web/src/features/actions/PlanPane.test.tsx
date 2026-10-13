import { cleanup, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { PlanCard } from '@/lib/monitor/actions'
import { globalActionsFixture } from '@/lib/monitor/fixturesActions'
import { renderWithClient } from '@/test-utils'
import { PlanPane } from './PlanPane'

vi.mock('@tanstack/react-router', async () => {
  const { fileRouteModuleMock } = await import('@/test-utils')
  return fileRouteModuleMock()
})

afterEach(cleanup)

const base = globalActionsFixture('ca-database', null, new Map(), new Map())
  .cards[0]

const pane = (card: PlanCard) =>
  renderWithClient(<PlanPane card={card} now={0} onClose={vi.fn()} />)

it('shows how to do it, the evidence, the rows and the note', () => {
  pane({
    ...base,
    steps: 'Run the SQL off-peak\nCheck the plan uses the index',
    evidence: [
      { label: 'Rows read per call', value: '245,290' },
      { label: 'Dead rows', value: '38%' },
    ],
    rows: [{ name: 'work_mem', value: '4MB', detail: 'spills seen' }],
    note: 'Check that it returns the same rows before you ship it',
  })
  const detail = screen.getByTestId('plan-detail')
  expect(within(detail).getByText('How to do it')).toBeTruthy()
  expect(
    within(screen.getByTestId('plan-steps'))
      .getAllByRole('listitem')
      .map((item) => item.textContent)
  ).toEqual(['Run the SQL off-peak', 'Check the plan uses the index'])
  const evidence = screen.getByTestId('plan-evidence')
  expect(evidence.textContent).toContain('Rows read per call')
  expect(evidence.textContent).toContain('245,290')
  expect(screen.getByTestId('plan-rows').textContent).toBe(
    'work_mem: 4MB (spills seen)'
  )
  expect(
    screen.getByText('Check that it returns the same rows before you ship it')
  ).toBeTruthy()
})

it('shows a rewrite next to the query it replaces', () => {
  pane({
    ...base,
    type: 'rewrite_query',
    kind: 'rewrite',
    sql: 'SELECT id FROM posts WHERE owneruserid = $1 LIMIT 50',
    rollback_sql: null,
    original: 'SELECT * FROM posts WHERE owneruserid = $1',
  })
  expect(screen.getByText('Your query')).toBeTruthy()
  expect(screen.getByText('The rewrite')).toBeTruthy()
  expect(screen.queryByText('SQL')).toBeNull()
  expect(screen.getByTestId('plan-original-sql').textContent).toContain(
    'SELECT * FROM posts'
  )
})

it('keeps one SQL block and no steps when the change has none', () => {
  pane({ ...base, steps: null, evidence: [], rows: [], original: null })
  expect(screen.queryByText('How to do it')).toBeNull()
  expect(screen.queryByTestId('plan-evidence')).toBeNull()
  expect(screen.getByText('SQL')).toBeTruthy()
})
