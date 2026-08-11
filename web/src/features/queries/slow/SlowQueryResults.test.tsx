import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { TopQuery } from '../../../types/top'
import { SlowQueryResults } from './SlowQueryResults'

afterEach(cleanup)

function query(hash: string, sql: string): TopQuery {
  return {
    query_hash: hash,
    query_text: sql,
    normalized_query: sql,
    freq: 4,
    total_time: '1.2s',
    avg_time: '0.3s',
    pct_load: '12%',
  }
}

describe('SlowQueryResults', () => {
  it('renders the shared skeleton-backed empty state while idle', () => {
    const onStart = vi.fn()
    render(
      <SlowQueryResults
        queries={[]}
        state="idle"
        isRealtime={false}
        onAnalyze={vi.fn()}
        onStart={onStart}
      />
    )

    expect(screen.getByText('Find your slowest queries')).toBeTruthy()
    expect(screen.getAllByTestId('query-card-skeleton')).toHaveLength(2)

    fireEvent.click(screen.getByRole('button', { name: /Find slow queries/ }))
    expect(onStart).toHaveBeenCalledTimes(1)
  })

  it('uses live-monitoring copy for the realtime idle state', () => {
    render(
      <SlowQueryResults
        queries={[]}
        state="idle"
        isRealtime
        onAnalyze={vi.fn()}
        onStart={vi.fn()}
      />
    )

    expect(screen.getByText('Monitor slow queries live')).toBeTruthy()
    expect(
      screen.getByRole('button', { name: /Start live monitoring/ })
    ).toBeTruthy()
  })

  it('uses canonical query-card skeletons while loading', () => {
    render(
      <SlowQueryResults
        queries={[]}
        state="loading"
        isRealtime={false}
        onAnalyze={vi.fn()}
      />
    )

    expect(screen.getAllByTestId('query-card-skeleton')).toHaveLength(3)
    expect(screen.queryByText('No slow queries matched')).toBeNull()
  })

  it('renders a recoverable list error', () => {
    const onRetry = vi.fn()
    render(
      <SlowQueryResults
        queries={[]}
        state="error"
        isRealtime={false}
        onAnalyze={vi.fn()}
        error="permission denied"
        onRetry={onRetry}
      />
    )

    expect(screen.getByText("Slow queries couldn't be loaded")).toBeTruthy()
    expect(
      screen.getByText('Your filters and query library are unchanged.')
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('renders the shared empty state after a completed empty run', () => {
    render(
      <SlowQueryResults
        queries={[]}
        state="complete"
        isRealtime={false}
        onAnalyze={vi.fn()}
      />
    )

    expect(screen.getByText('No slow queries matched')).toBeTruthy()
    expect(screen.getAllByTestId('query-card-skeleton')).toHaveLength(2)
  })

  it('keeps query cards in a free-standing results section', () => {
    render(
      <SlowQueryResults
        queries={[
          query('hash-1', 'select id, name from users'),
          query('hash-2', 'select count(*) from orders'),
        ]}
        state="complete"
        isRealtime={false}
        onAnalyze={vi.fn()}
        onCache={vi.fn()}
      />
    )

    const section = screen.getByRole('region', { name: 'Slow queries' })
    const list = screen.getByTestId('top-query-list')

    expect(list.parentElement).toBe(section)
    expect(screen.getAllByTestId('top-query-row')).toHaveLength(2)
    expect(screen.queryByRole('heading', { name: 'Slow queries' })).toBeNull()

    const firstCard = screen.getAllByTestId('top-query-row')[0]
    const header = within(firstCard).getByTestId('query-card-header')
    const footer = within(firstCard).getByTestId('query-card-footer-content')
    expect(
      within(header).getByRole('button', { name: 'More actions' })
    ).toBeTruthy()
    expect(within(header).getByText('1')).toBeTruthy()
    expect(within(footer).getByRole('button', { name: /Analyze/ })).toBeTruthy()
    expect(
      within(footer).queryByRole('button', { name: 'More actions' })
    ).toBeNull()
  })
})
