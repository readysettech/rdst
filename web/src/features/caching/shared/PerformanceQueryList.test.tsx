import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  PerformanceCacheabilityNote,
  PerformanceQueryCard,
} from './PerformanceQueryList'

afterEach(cleanup)

describe('PerformanceQueryCard', () => {
  const sql = 'SELECT id, email\nFROM users\nWHERE status = :status'
  // getByTitle collapses attribute whitespace before matching.
  const sqlTitle = 'SELECT id, email FROM users WHERE status = :status'

  function renderCard(overrides: {
    selected?: boolean
    hasParameters?: boolean
    disabled?: boolean
  }) {
    const onSelect = vi.fn()
    render(
      <PerformanceQueryCard
        queryHash="row-1"
        selected={overrides.selected ?? false}
        disabled={overrides.disabled}
        onSelect={onSelect}
        title="Users by status"
        sql={sql}
        parameterCount={overrides.hasParameters ? 1 : 0}
        parameterContent={
          overrides.hasParameters ? (
            <div data-testid="parameter-grid" />
          ) : undefined
        }
      />
    )
    return { onSelect }
  }

  it('shows formatted SQL and inline parameters while selected', () => {
    renderCard({
      selected: true,
      hasParameters: true,
    })

    const code = screen.getByTitle(sqlTitle)
    expect(code.textContent).toContain('\nFROM users')
    expect(code.textContent).toContain('WHERE status = :status')
    expect(code.className).not.toContain('truncate')
    expect(code.className).toContain('whitespace-pre-wrap')
    expect(screen.getByTestId('parameter-grid')).toBeTruthy()
    expect(
      screen
        .getByRole('button', { name: 'Deselect Users by status' })
        .getAttribute('aria-pressed')
    ).toBe('true')
  })

  it('keeps the compact one-line preview while unselected', () => {
    renderCard({ selected: false, hasParameters: true })

    const code = screen.getByTitle(sqlTitle)
    expect(code.textContent).toBe(
      'SELECT id, email FROM users WHERE status = :status'
    )
    expect(code.className).toContain('truncate')
    expect(screen.queryByTestId('parameter-grid')).toBeNull()
  })

  it('formats a selected query even when it has no parameters', () => {
    renderCard({ selected: true, hasParameters: false })

    const code = screen.getByTitle(sqlTitle)
    expect(code.textContent).toContain('\nFROM users')
    expect(code.className).not.toContain('truncate')
    expect(
      screen.getByRole('button', { name: 'Deselect Users by status' })
    ).toBeTruthy()
  })

  it('exposes disabled selection without invoking the callback', () => {
    const { onSelect } = renderCard({ disabled: true })

    const card = screen.getByRole('button', { name: 'Select Users by status' })
    expect(card.getAttribute('aria-disabled')).toBe('true')
    fireEvent.click(card)
    expect(onSelect).not.toHaveBeenCalled()
  })

  it('disables the selected parameterized card interaction too', () => {
    const { onSelect } = renderCard({
      selected: true,
      hasParameters: true,
      disabled: true,
    })

    const card = screen.getByRole('button', {
      name: 'Deselect Users by status',
    })
    expect(card.getAttribute('aria-disabled')).toBe('true')
    expect(card.getAttribute('tabindex')).toBe('-1')
    fireEvent.click(card)
    fireEvent.keyDown(card, { key: 'Enter' })
    expect(onSelect).not.toHaveBeenCalled()
  })
})

describe('PerformanceCacheabilityNote', () => {
  it('renders timestamped evidence for a confirmed not-cacheable verdict', () => {
    render(
      <PerformanceCacheabilityNote
        readysetSupported="unsupported: unsupported query"
        checkedAt={new Date(Date.now() - 5 * 60_000).toISOString()}
      />
    )
    expect(screen.getByText('Last check: not cacheable (5m ago)')).toBeTruthy()
  })

  it('omits the timestamp when the registry has none', () => {
    render(
      <PerformanceCacheabilityNote readysetSupported="unsupported: aggregate" />
    )
    expect(screen.getByText('Last check: not cacheable')).toBeTruthy()
  })

  it.each([
    '',
    'yes',
    'pending',
  ])('asserts nothing for verdict %j', (verdict) => {
    const { container } = render(
      <PerformanceCacheabilityNote
        readysetSupported={verdict}
        checkedAt="2026-01-01T00:00:00Z"
      />
    )
    expect(container.innerHTML).toBe('')
  })
})
