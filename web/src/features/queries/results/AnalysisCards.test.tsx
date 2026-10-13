import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AlsoCard, FixCard, RewritesCard } from './AnalysisCards'

afterEach(cleanup)

const fix = {
  sql: 'CREATE INDEX idx_orders_channel ON orders (channel)',
  serves: 'Speculative index on channel.',
  cost_before: null,
  cost_after: null,
  size: null,
  write_cost: null,
}

describe('FixCard', () => {
  it('names the statement that turns planner verification on', () => {
    render(
      <FixCard
        fix={fix}
        onCopy={vi.fn()}
        plannerSetup={{ installSql: 'CREATE EXTENSION IF NOT EXISTS hypopg;' }}
      />
    )
    const setup = screen.getByTestId('planner-setup')
    expect(setup.textContent).toContain('hypopg is not enabled')
    expect(setup.textContent).toContain(
      'CREATE EXTENSION IF NOT EXISTS hypopg;'
    )
    expect(within(setup).getByRole('button', { name: /Copy SQL/ })).toBeTruthy()
    expect(screen.queryByText(/Planner uses it/)).toBeNull()
  })

  it('shows the planner line only with both costs, and no setup line', () => {
    render(
      <FixCard
        fix={{ ...fix, cost_before: 49586, cost_after: 35.8 }}
        onCopy={vi.fn()}
      />
    )
    expect(screen.getByText('Planner uses it: cost 49,586 to 36')).toBeTruthy()
    expect(screen.queryByTestId('planner-setup')).toBeNull()
  })
})

describe('RewritesCard', () => {
  it('shows each rewrite with its SQL and a copy button', () => {
    render(
      <RewritesCard
        method={null}
        rewrites={[
          {
            name: 'Filter before sort',
            before_ms: 100,
            after_ms: 40,
            runs: null,
            outcome: 'faster',
            sql: 'SELECT id FROM posts WHERE owneruserid = 7',
          },
        ]}
      />
    )
    const block = screen.getByTestId('statement-copy')
    expect(block.textContent).toContain('owneruserid = 7')
    expect(within(block).getByRole('button', { name: /Copy SQL/ })).toBeTruthy()
  })
})

describe('AlsoCard', () => {
  it('shows each further index with its SQL, and the other suggestions as lines', () => {
    render(
      <AlsoCard
        also={[
          {
            title: 'Add an index on orders(channel)',
            detail: 'Serves the channel filter.',
            sql: 'CREATE INDEX ON orders (channel)',
          },
          { title: 'Select fewer columns', detail: null },
        ]}
      />
    )
    const index = screen.getByTestId('also-index')
    expect(index.textContent).toContain('Serves the channel filter.')
    expect(index.textContent).toContain('CREATE INDEX ON orders (channel)')
    expect(within(index).getByRole('button', { name: /Copy SQL/ })).toBeTruthy()
    expect(screen.getByText('Select fewer columns')).toBeTruthy()
  })
})
