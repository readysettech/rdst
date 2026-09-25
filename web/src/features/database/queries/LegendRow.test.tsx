import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { FIXTURE_NOW, queriesFixture } from '@/lib/monitor/fixtures'
import { LegendRow, type LegendRowProps } from './LegendRow'
import type { QueryStatus } from './queryStatus'

afterEach(cleanup)

const fixture = queriesFixture('localdev', '24h', FIXTURE_NOW).queries[0]

function renderRow(overrides: Partial<LegendRowProps>) {
  return render(
    <LegendRow
      query={fixture}
      color="red"
      status={{
        id: 'healthy',
        label: 'Healthy',
        tone: 'neutral',
        reason: null,
        itemId: null,
      }}
      open={false}
      raised={false}
      dimmed={false}
      onToggle={() => undefined}
      onHover={() => undefined}
      {...overrides}
    />
  )
}

const attention: QueryStatus = {
  id: 'attention',
  label: 'Needs attention',
  tone: 'negative',
  reason: 'Traffic up 3x since 9:42 PM',
  itemId: 4,
}

it('shows the backend name, one Status chip and its reason line', () => {
  renderRow({
    query: { ...fixture, display_name: 'SELECT on orders (132 columns)' },
    status: attention,
  })
  expect(screen.getByText('SELECT on orders (132 columns)')).toBeTruthy()
  expect(screen.getByText('Needs attention')).toBeTruthy()
  expect(screen.getByText('Traffic up 3x since 9:42 PM')).toBeTruthy()
  expect(document.querySelectorAll('[data-chip=status]')).toHaveLength(1)
})

it('shows no chip and no reason line for a healthy query', () => {
  renderRow({})
  expect(document.querySelector('[data-chip=status]')).toBeNull()
  expect(screen.queryByTestId('row-reason')).toBeNull()
})

it('shows the score out of 100, never a letter or a gauge', () => {
  const scored = {
    ...fixture,
    last_analysis: {
      ...fixture.last_analysis!,
      state: 'done' as const,
      summary: {
        ...fixture.last_analysis!.summary!,
        efficiency_score: 40,
        rating: 'poor',
      },
    },
  }
  renderRow({ query: scored })
  const tile = document.querySelector('[data-chip=grade]')
  expect(tile?.textContent).toBe('40/100')
  expect(tile?.getAttribute('aria-label')).toContain('Score 40 of 100')
  expect(document.querySelector('circle')).toBeNull()
})

it('keeps a same-size "No score" tile that says why', () => {
  renderRow({
    query: {
      ...fixture,
      text: 'UPDATE sessions SET last_seen_at = now() WHERE id = $1',
      analyzable: undefined,
      last_analysis: null,
    },
    status: {
      id: 'not_analyzed',
      label: 'Not analyzed yet',
      tone: 'neutral',
      reason: "UPDATE changes data, so RDST doesn't run it.",
      itemId: null,
    },
  })
  expect(
    screen.getByRole('img', {
      name: "No score: RDST doesn't run UPDATE statements",
    })
  ).toBeTruthy()
  expect(screen.getByText('Not analyzed yet')).toBeTruthy()
  expect(screen.queryByTestId('row-reason')).toBeNull()
})

const notAnalyzed: QueryStatus = {
  id: 'not_analyzed',
  label: 'Not analyzed yet',
  tone: 'neutral',
  reason: null,
  itemId: null,
}

it('offers Analyze now on a query RDST can analyze', () => {
  let clicked = 0
  renderRow({
    query: { ...fixture, last_analysis: null },
    status: notAnalyzed,
    finding: 'Missing index',
    onAnalyze: () => {
      clicked += 1
    },
  })
  screen.getByRole('button', { name: 'Analyze now' }).click()
  expect(clicked).toBe(1)
  expect(screen.getByText('Missing index')).toBeTruthy()
  expect(screen.queryByText('Not analyzed yet')).toBeNull()
})

it('shows a queued query in the score column', () => {
  renderRow({
    query: { ...fixture, last_analysis: null },
    status: notAnalyzed,
    activity: { kind: 'queued', label: 'Queued', position: 2, soon: true },
    onAnalyze: () => undefined,
  })
  const tile = document.querySelector('[data-chip=grade-activity]')
  expect(tile?.getAttribute('data-activity')).toBe('queued')
  expect(tile?.className).toContain('rdst-score-queued')
  expect(tile?.className).toContain('border-dashed')
  expect(tile?.querySelector('.rdst-score-trace')).toBeNull()
  expect(tile?.textContent).toBe('Queued')
  expect(
    screen.getByRole('img', { name: 'Queued for deep analysis' })
  ).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Analyze now' })).toBeNull()
  expect(document.querySelector('[data-chip=grade-placeholder]')).toBeNull()
})

it('shows the running step beside the name and a tracing tile', () => {
  renderRow({
    query: { ...fixture, last_analysis: null },
    status: notAnalyzed,
    activity: {
      kind: 'running',
      label: 'Analyzing now',
      step: 'Writing recommendations',
      soon: true,
    },
  })
  const tile = document.querySelector('[data-chip=grade-activity]')
  expect(tile?.getAttribute('data-activity')).toBe('running')
  expect(tile?.querySelector('rect.rdst-score-trace')).toBeTruthy()
  expect(screen.getByRole('status').textContent).toBe('Writing recommendations')
})

it('keeps the classification on the row, scored or not', () => {
  const scored = {
    ...fixture,
    last_analysis: {
      ...fixture.last_analysis!,
      state: 'done' as const,
      summary: { ...fixture.last_analysis!.summary!, efficiency_score: 80 },
    },
  }
  renderRow({ query: scored, finding: 'Missing index' })
  expect(document.querySelector('[data-chip=finding]')?.textContent).toContain(
    'Missing index'
  )
})

it('outlines a row brought into view from the Analyses list', () => {
  renderRow({ highlighted: true })
  expect(document.querySelector('[data-highlighted=true]')).toBeTruthy()
})
