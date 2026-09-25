import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { markSegments, QueryTextBody } from './QueryTextBody'

afterEach(cleanup)

describe('markSegments', () => {
  it('widens matches to whole words so SQL coloring holds', () => {
    expect(markSegments('SELECT id FROM orders', 'ord')).toEqual([
      { text: 'SELECT id FROM ', mark: false },
      { text: 'orders', mark: true },
    ])
  })

  it('marks every occurrence, case-insensitively', () => {
    const marked = markSegments('orders JOIN Orders', 'ORDERS').filter(
      (segment) => segment.mark
    )
    expect(marked.map((segment) => segment.text)).toEqual(['orders', 'Orders'])
  })

  it('returns the text unmarked for an empty search', () => {
    expect(markSegments('SELECT 1', '  ')).toEqual([
      { text: 'SELECT 1', mark: false },
    ])
  })
})

describe('QueryTextBody', () => {
  it('explains missing text instead of rendering a blank band', () => {
    render(
      <QueryTextBody
        text=""
        textStatus="unavailable"
        textReason="Postgres didn't keep the text for this statement."
        fingerprint="da7e0c1478fd"
        tables={['orders']}
      />
    )
    expect(screen.getByText('Query text unavailable')).toBeTruthy()
    expect(
      screen.getByText("Postgres didn't keep the text for this statement.")
    ).toBeTruthy()
    expect(screen.getByText('fingerprint da7e0c1478fd')).toBeTruthy()
    expect(screen.getByText('orders')).toBeTruthy()
  })

  it('marks search matches inside the SQL', () => {
    const { container } = render(
      <QueryTextBody
        text="SELECT id FROM orders"
        textStatus="ok"
        fingerprint="abc"
        highlight="orders"
      />
    )
    expect(container.querySelector('mark')?.textContent).toBe('orders')
  })
})
