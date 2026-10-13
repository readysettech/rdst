import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import type { PatternRow } from '../../lib/useDemo'
import { PatternList, statusReason } from './PatternList'

afterEach(cleanup)

beforeAll(() => {
  // Radix menus call pointer-capture APIs jsdom omits.
  const proto = HTMLElement.prototype as unknown as Record<string, unknown>
  proto.hasPointerCapture = () => false
  proto.setPointerCapture = () => {}
  proto.releasePointerCapture = () => {}
  proto.scrollIntoView = () => {}
})

function row(overrides: Partial<PatternRow>): PatternRow {
  return {
    key: 'Q',
    title: 'Query',
    sql: 'SELECT 1',
    group: 'workload',
    status: 'pass_through',
    hits: 0,
    postgres_hits: 0,
    readyset_hits: 0,
    direct_avg_ms: null,
    router_avg_ms: null,
    reason: { kind: 'below_rank' },
    log_reason: null,
    ...overrides,
  }
}

const patterns: PatternRow[] = [
  row({
    key: 'A01',
    title: 'SELECT on orders by customer_id',
    sql: 'SELECT * FROM orders WHERE customer_id = 7 ORDER BY created_at DESC LIMIT 20',
    status: 'cached_manual',
    hits: 410,
    direct_avg_ms: 41,
    router_avg_ms: 0.4,
  }),
  row({
    key: 'A02',
    title: 'SELECT on products by category',
    sql: "SELECT * FROM products WHERE category = 'toys'",
    hits: 220,
    direct_avg_ms: 18,
    router_avg_ms: 18.2,
  }),
  row({
    key: 'A03',
    title: 'UPDATE orders by id',
    sql: 'UPDATE orders SET status = 1 WHERE id = 2',
    status: 'unsupported',
    hits: 70,
    direct_avg_ms: 3.4,
    router_avg_ms: 3.5,
  }),
  row({
    key: 'A04',
    title: 'COUNT on orders by status',
    sql: 'SELECT count(*) FROM orders',
    status: 'cached_querypilot',
    hits: 180,
    direct_avg_ms: 63,
    router_avg_ms: 0.5,
  }),
  row({ key: 'A05', title: 'Five', status: 'not_eligible', hits: 2 }),
  row({ key: 'A06', title: 'Six', status: 'denylisted', hits: 9 }),
  row({ key: 'A07', title: 'Seven', hits: 5 }),
  row({ key: 'A08', title: 'Eight', hits: 4 }),
]

function rowOf(title: string) {
  const el = screen.getByText(title).closest('[data-testid="pattern-row"]')
  expect(el).toBeTruthy()
  return el as HTMLElement
}

function renderList(
  overrides: Partial<Parameters<typeof PatternList>[0]> = {}
) {
  const props = {
    patterns,
    mode: 'count_star' as const,
    cacheBudget: 10,
    querypilotOn: false,
    onCache: vi.fn(),
    onUncache: vi.fn(),
    onPolicy: vi.fn(),
    ...overrides,
  }
  renderWithClient(<PatternList {...props} />)
  return props
}

describe('PatternList', () => {
  it('lists the queries with two latencies, a status word and a row action', () => {
    const props = renderList()
    expect(
      screen.getByRole('heading', { level: 2, name: 'Queries' })
    ).toBeTruthy()
    expect(screen.getByText('8 on orders, 2 cached')).toBeTruthy()

    const cached = rowOf('SELECT on orders by customer_id')
    expect(cached.textContent).toContain(
      'SELECT * FROM orders WHERE customer_id = $1 ORDER BY created_at DESC LIMIT 20'
    )
    expect(within(cached).getByText('Cached by hand').className).toContain(
      'text-content-positive-soft'
    )
    expect(cached.textContent).toContain('41ms')
    expect(cached.textContent).toContain('0.4ms')
    fireEvent.click(
      within(cached).getByRole('button', {
        name: 'Remove SELECT on orders by customer_id',
      })
    )
    expect(props.onUncache).toHaveBeenCalledWith(
      'A01',
      'SELECT on orders by customer_id'
    )

    const uncached = rowOf('SELECT on products by category')
    expect(
      within(uncached).getByText('Not cached yet').className
    ).not.toContain('text-content-positive-soft')
    expect(uncached.textContent).toContain('\u2014')
    fireEvent.click(
      within(uncached).getByRole('button', {
        name: 'Cache SELECT on products by category',
      })
    )
    expect(props.onCache).toHaveBeenCalledWith(
      'A02',
      'SELECT on products by category'
    )

    const passes = rowOf('UPDATE orders by id')
    expect(within(passes).getByText('Passes through')).toBeTruthy()
    expect(
      within(passes).queryByRole('button', { name: /^(Cache|Remove) / })
    ).toBeNull()
    expect(
      within(rowOf('COUNT on orders by status')).getByText(
        'Cached by QueryPilot'
      )
    ).toBeTruthy()
  })

  it('shows six rows, then the rest on request', () => {
    renderList()
    expect(screen.getAllByTestId('pattern-row')).toHaveLength(6)
    fireEvent.click(screen.getByRole('button', { name: '2 more queries' }))
    expect(screen.getAllByTestId('pattern-row')).toHaveLength(8)
    expect(screen.queryByRole('button', { name: '2 more queries' })).toBeNull()
  })

  it('raises the highlighted rows and reveals them past the fold', () => {
    renderList({ highlightedKeys: ['A08'] })
    expect(screen.getAllByTestId('pattern-row')).toHaveLength(8)
    expect(rowOf('Eight').getAttribute('data-open')).toBe('true')
    expect(rowOf('Seven').getAttribute('data-open')).toBeNull()
  })

  it('holds the row actions while QueryPilot owns caching', () => {
    renderList({ querypilotOn: true })
    const cache = screen.getByRole('button', {
      name: 'Cache SELECT on products by category',
    }) as HTMLButtonElement
    expect(cache.disabled).toBe(true)
  })

  it('offers the policy from the section header', async () => {
    const props = renderList()
    const trigger = screen.getByRole('button', {
      name: 'Policy: most frequent',
    })
    fireEvent.keyDown(trigger, { key: 'Enter' })
    fireEvent.click(
      await screen.findByRole('menuitem', { name: 'Most expensive' })
    )
    expect(props.onPolicy).toHaveBeenCalledWith('sum_time')
  })

  it('explains each status word in a tooltip on hover or focus', async () => {
    renderList()
    const word = within(rowOf('SELECT on products by category')).getByText(
      'Not cached yet'
    )
    fireEvent.focus(word)
    expect(
      (
        await screen.findAllByText(
          'Not cached: not among the top 10 most frequently run queries right now.'
        )
      ).length
    ).toBeGreaterThan(0)
    expect(word.textContent).toBe('Not cached yet')
  })
})

describe('statusReason', () => {
  it('gives the one-sentence reason behind every status', () => {
    const budget = 10
    expect(
      statusReason(
        row({
          status: 'cached_querypilot',
          hits: 5,
          reason: { kind: 'selected', metric: 'sum_time_us' },
        }),
        'count_star',
        budget
      )
    ).toBe('Cached: one of the top 10 most expensive queries right now.')
    expect(
      statusReason(
        row({ hits: 5, reason: { kind: 'below_rank' } }),
        'count_star',
        1
      )
    ).toBe(
      'Not cached: not among the top 1 most frequently run query right now.'
    )
    expect(
      statusReason(
        row({
          status: 'not_eligible',
          hits: 3,
          reason: { kind: 'below_min_execution' },
        }),
        'count_star',
        budget
      )
    ).toBe(
      "Not cached yet: this query hasn't run enough times to be considered."
    )
    expect(
      statusReason(
        row({
          status: 'not_eligible',
          hits: 0,
          reason: { kind: 'below_min_execution' },
        }),
        'count_star',
        budget
      )
    ).toBe(
      'Waiting for traffic. This query is measured once the workload starts.'
    )
    expect(
      statusReason(
        row({ status: 'denylisted', hits: 9, reason: { kind: 'denylisted' } }),
        'count_star',
        budget
      )
    ).toBe('Blocked: excluded from automatic caching in this demo.')
    expect(
      statusReason(
        row({
          status: 'unsupported',
          hits: 9,
          reason: { kind: 'unsupported' },
        }),
        'count_star',
        budget
      )
    ).toBe("Passes through: Readyset can't cache this SQL shape in the demo.")
    expect(
      statusReason(
        row({ status: 'cached_manual', hits: 9, reason: { kind: 'manual' } }),
        'count_star',
        budget
      )
    ).toBe(
      'Cached manually. Turning QueryPilot on drops manual caches and lets it manage caching.'
    )
    expect(
      statusReason(
        row({ hits: 9, reason: { kind: 'not_select_shaped' } }),
        'count_star',
        budget
      )
    ).toBe('Skipped: not shaped for automatic selection.')
  })
})
