import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { QueryRegistryEntry } from '../../../../lib/useQueryRegistry'
import { AnalyzeHistory } from './AnalyzeHistory'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: React.ReactNode }) => (
    <a href="/queries?view=saved">{children}</a>
  ),
}))

afterEach(cleanup)

function query(
  overrides: Partial<QueryRegistryEntry> = {}
): QueryRegistryEntry {
  return {
    sql: 'SELECT * FROM users',
    hash: 'query-1',
    tag: 'Users',
    last_analyzed: new Date().toISOString(),
    target: 'prod',
    frequency: 2,
    source: 'manual',
    ...overrides,
  } as QueryRegistryEntry
}

describe('AnalyzeHistory', () => {
  it('renders the card-2 empty state', () => {
    render(<AnalyzeHistory queries={[]} onSelect={vi.fn()} />)

    expect(
      screen.getByText('Your analyzed queries will show up here')
    ).toBeTruthy()
  })

  it('renders query-card skeletons while recent queries load', () => {
    render(
      <AnalyzeHistory
        queries={[]}
        onSelect={vi.fn()}
        isLoading
        error="offline"
      />
    )

    expect(screen.getAllByTestId('query-card-skeleton')).toHaveLength(2)
    expect(
      screen.queryByText('Your analyzed queries will show up here')
    ).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('renders a recoverable error without replacing the editor', () => {
    const onRetry = vi.fn()
    render(
      <AnalyzeHistory
        queries={[]}
        onSelect={vi.fn()}
        error="connection refused"
        onRetry={onRetry}
      />
    )

    expect(screen.getByText("Recent queries couldn't be loaded")).toBeTruthy()
    expect(
      screen.getByText('The query in the editor is unchanged.')
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('renders static query cards with one explicit reuse action', () => {
    const onSelect = vi.fn()
    const entry = query()
    render(<AnalyzeHistory queries={[entry]} onSelect={onSelect} />)

    expect(screen.getByRole('heading', { name: 'Recent queries' })).toBeTruthy()
    expect(screen.getByRole('link', { name: /View all/ })).toBeTruthy()

    const card = document.querySelector(`[data-query-hash="${entry.hash}"]`)
    expect(card?.getAttribute('role')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Load query' }))
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith(entry)
  })

  it('limits the recent list to five queries', () => {
    const queries = Array.from({ length: 7 }, (_, index) =>
      query({ hash: `query-${index}`, sql: `SELECT ${index}` })
    )
    render(<AnalyzeHistory queries={queries} onSelect={vi.fn()} />)

    expect(screen.getAllByRole('button', { name: 'Load query' })).toHaveLength(
      5
    )
  })
})
