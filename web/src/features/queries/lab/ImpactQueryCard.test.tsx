import { Button } from '@rs/ui-new/button'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { QueryCardImpact } from '../../../components/QueryCardImpact'

afterEach(cleanup)

describe('QueryCardImpact', () => {
  it('keeps the QueryCard contract around the 16rem impact rail', () => {
    render(
      <QueryCardImpact
        sql="select * from users"
        title="Users"
        rail={<div>2.2 h database time</div>}
        meta="449,685 runs"
        data-testid="impact-query"
        data-query-hash="abc"
        secondaryActions={<Button label="Analyze" />}
        primaryAction={<Button label="Compare & test" />}
      />
    )

    const card = screen.getByTestId('impact-query')
    const layout = screen
      .getByText('2.2 h database time')
      .closest('[class*="grid-cols"]')

    expect(card.getAttribute('data-query-hash')).toBe('abc')
    expect(layout?.className).toContain(
      'laptop:grid-cols-[16rem_minmax(0,1fr)]'
    )
    expect(screen.getByText('Users')).toBeTruthy()
    expect(screen.getByTitle('select * from users')).toBeTruthy()

    const footer = screen.getByTestId('query-card-footer-content')
    expect(
      within(footer)
        .getAllByRole('button')
        .map((button) => button.textContent)
    ).toEqual(['Analyze', 'Compare & test'])
  })

  it('keeps whole-card selection keyboard accessible', () => {
    const onSelect = vi.fn()
    render(
      <QueryCardImpact
        selectable
        selected
        onSelect={onSelect}
        selectionLabel="Select query"
        sql="select 1"
        rail={<div>Impact</div>}
      />
    )

    const card = screen.getByRole('button', { name: 'Select query' })
    expect(card.getAttribute('aria-pressed')).toBe('true')
    fireEvent.keyDown(card, { key: 'Enter' })
    expect(onSelect).toHaveBeenCalledTimes(1)
  })

  it('supports the wider detail rail without changing the default layout', () => {
    render(
      <QueryCardImpact
        sql="select 1"
        rail={<div>Detail evidence</div>}
        railSize="wide"
      />
    )

    const layout = screen
      .getByText('Detail evidence')
      .closest('[class*="grid-cols"]')

    expect(layout?.className).toContain(
      'laptop:grid-cols-[24rem_minmax(0,1fr)]'
    )
  })

  it('can render SQL expanded on first paint for detail surfaces', () => {
    render(
      <QueryCardImpact
        sql={'select 1\nfrom users\nwhere active = true'}
        rail={<div>Detail evidence</div>}
        sqlInitiallyExpanded
      />
    )

    expect(screen.getByLabelText('Collapse SQL')).toBeTruthy()
  })

  it('preserves editor and expansion slots for interactive library rows', () => {
    render(
      <QueryCardImpact
        sql="select 1"
        rail={<div>Impact</div>}
        editor={<div>SQL editor</div>}
        expansion={<div>Query details</div>}
      />
    )

    expect(screen.getByText('SQL editor')).toBeTruthy()
    expect(screen.getByText('Query details')).toBeTruthy()
    expect(screen.queryByTitle('select 1')).toBeNull()
  })

  it('isolates content and footer geometry for shared-layout transitions', () => {
    const { container } = render(
      <QueryCardImpact
        sql="select 1"
        rail={<div>Impact</div>}
        meta="hash abc123"
        motionLayout
        primaryAction={<Button label="Analyze" />}
      />
    )

    expect(
      container.querySelector('[data-query-layout-region="content"]')
    ).toBeTruthy()
    expect(
      container.querySelector('[data-query-layout-region="footer"]')
    ).toBeTruthy()
  })
})
