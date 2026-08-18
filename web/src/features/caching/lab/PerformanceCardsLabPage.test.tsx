import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PerformanceCardsLabPage } from './PerformanceCardsLabPage'

vi.mock('@tanstack/react-router', async () => ({
  Link: (await import('@/test-utils')).LinkStub,
  useNavigate: () => vi.fn(),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('PerformanceCardsLabPage', () => {
  it('renders the setup layout with five selectable canonical cards', () => {
    render(<PerformanceCardsLabPage />)

    expect(screen.getByText('Performance cards')).toBeTruthy()
    expect(screen.getByText('Design prototype')).toBeTruthy()

    // Selectable cards are the only Select/Deselect-named controls carrying
    // selection state; the header's Select all button carries none.
    const cards = screen
      .getAllByRole('button', { name: /^(?:Select|Deselect) / })
      .filter((element) => element.hasAttribute('aria-pressed'))
    expect(cards).toHaveLength(5)

    // Run summary panel with the shared Suggest values placement.
    expect(screen.getByText('Run summary')).toBeTruthy()
    expect(screen.getByText('Parameter readiness')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Suggest values' })).toBeTruthy()
  })

  it('hosts parameter inputs inside the selected card without toggling selection', () => {
    render(<PerformanceCardsLabPage />)

    // The parameterized fixture starts selected.
    const selected = screen.getByRole('button', {
      name: 'Deselect Orders by status',
    })
    expect(selected.getAttribute('aria-pressed')).toBe('true')

    const inputs = screen.getAllByPlaceholderText(
      'Enter a representative value'
    )
    expect(inputs).toHaveLength(2)

    // Inputs render inside the card body but outside the selection control,
    // so interacting with them can never toggle selection.
    const card = selected.closest('[data-query-hash]')
    expect(card?.contains(inputs[0])).toBe(true)
    expect(selected.contains(inputs[0])).toBe(false)

    fireEvent.click(inputs[0])
    fireEvent.change(inputs[0], { target: { value: 'fulfilled' } })
    expect(
      screen
        .getByRole('button', { name: 'Deselect Orders by status' })
        .getAttribute('aria-pressed')
    ).toBe('true')

    // The parameter region sits above the hash/target meta footer band.
    const footer = card?.querySelector(
      '[data-testid="query-card-footer-content"]'
    )
    expect(footer).toBeTruthy()
    expect(
      inputs[0].compareDocumentPosition(footer as Element) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()

    // Deselecting via the selection control removes the parameter region.
    fireEvent.click(selected)
    expect(
      screen.queryByPlaceholderText('Enter a representative value')
    ).toBeNull()
  })

  it('collapses unselected cards to one line and formats on selection', () => {
    render(<PerformanceCardsLabPage />)

    const sqlTitle = 'SELECT id, name FROM categories ORDER BY name LIMIT 20'
    expect(screen.getByTitle(sqlTitle).className).toContain('truncate')

    fireEvent.click(
      screen.getByRole('button', { name: 'Select Categories list' })
    )
    expect(screen.getByTitle(sqlTitle).className).not.toContain('truncate')
  })

  it('carries prior not-cacheable evidence on its fixture card', () => {
    render(<PerformanceCardsLabPage />)

    expect(screen.getByText(/Last check: not cacheable/)).toBeTruthy()
  })
})
