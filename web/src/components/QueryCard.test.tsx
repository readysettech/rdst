import { Button } from '@rs/ui-new/button'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { QueryCard } from './QueryCard'

afterEach(cleanup)

describe('QueryCard', () => {
  it('renders the canonical bands and preserves data hooks', () => {
    render(
      <QueryCard
        sql="select * from users"
        title="Users"
        meta="2 runs"
        data-testid="query-row"
        data-query-hash="abc"
        data-cache-id="cache-1"
      />
    )

    const card = screen.getByTestId('query-row')
    expect(card.getAttribute('data-query-hash')).toBe('abc')
    expect(card.getAttribute('data-cache-id')).toBe('cache-1')
    expect(screen.getByText('Users')).toBeTruthy()
    expect(screen.getByText('2 runs')).toBeTruthy()
    expect(screen.getByTitle('select * from users')).toBeTruthy()

    const main = screen.getByTestId('query-card-main-content')
    const header = screen.getByTestId('query-card-header')
    const footer = screen.getByTestId('query-card-footer-content')
    expect(main.parentElement).toBe(card)
    expect(header.parentElement).toBe(main)
    expect(footer.parentElement).toBe(card)
  })

  it('keeps whole-card query selection keyboard accessible', () => {
    const onClick = vi.fn()
    render(
      <QueryCard
        selectable
        selected
        onSelect={onClick}
        selectionLabel="Select query: Selected query"
        sql="select 1"
        title="Selected query"
      />
    )

    const card = screen.getByRole('button', {
      name: 'Select query: Selected query',
    })
    expect(card.getAttribute('aria-pressed')).toBe('true')
    fireEvent.keyDown(card, { key: ' ' })
    expect(onClick).toHaveBeenCalledTimes(1)
    expect(within(card).queryByRole('button')).toBeNull()
  })

  it('formats card SQL into readable clauses without changing the raw value', async () => {
    const sql =
      'select users.id, users.email, users.created_at from users where users.status = :status and users.created_at > :created_after order by users.created_at desc'

    render(<QueryCard sql={sql} />)

    const code = screen.getByTitle(sql)
    await waitFor(() => {
      expect(code.textContent).toContain('SELECT\n  users.id')
    })

    expect(code.textContent).toContain('\nFROM users')
    expect(code.textContent).toContain('\nWHERE users.status = :status')
    expect(code.textContent).toContain(
      '\n  AND users.created_at > :created_after'
    )
    expect(code.getAttribute('title')).toBe(sql)
  })

  it('keeps normal cards static and delegates interaction to explicit actions', () => {
    const onAction = vi.fn()
    render(
      <QueryCard
        data-testid="recent-query"
        sql="select id from users"
        title="Recent query"
        primaryAction={<Button label="Use query" onClick={onAction} />}
      />
    )

    expect(screen.getByTestId('recent-query').getAttribute('role')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Use query' }))
    expect(onAction).toHaveBeenCalledTimes(1)
  })

  it('renders secondary actions before the rightmost primary action', () => {
    render(
      <QueryCard
        sql="select 1"
        secondaryActions={<Button label="Analyze" />}
        primaryAction={<Button label="Compare & test" />}
      />
    )

    const footer = screen.getByTestId('query-card-footer-content')
    expect(
      within(footer)
        .getAllByRole('button')
        .map((button) => button.textContent)
    ).toEqual(['Analyze', 'Compare & test'])
  })

  it('keeps edit mode inside the canonical header, content, and footer bands', () => {
    render(
      <QueryCard
        sql="select 1"
        title="Edit query"
        editor={<div>SQL editor</div>}
        primaryAction={<Button label="Save" />}
        data-testid="query-row"
      />
    )

    const card = screen.getByTestId('query-row')
    expect(screen.getByTestId('query-card-header').parentElement).toBe(
      screen.getByTestId('query-card-main-content')
    )
    expect(screen.getByText('SQL editor')).toBeTruthy()
    expect(screen.getByTestId('query-card-footer-content').parentElement).toBe(
      card
    )
    expect(screen.queryByTitle('select 1')).toBeNull()
  })
})
