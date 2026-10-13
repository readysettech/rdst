import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { SqlCode } from './SqlCode'

afterEach(cleanup)

const SQL =
  'CREATE INDEX CONCURRENTLY idx_order_items_product_id ON public.order_items (product_id, id);\nSELECT 1'

describe('SqlCode', () => {
  it('wraps long lines under a hanging indent instead of scrolling', () => {
    render(<SqlCode sql={SQL} testId="sql" />)
    const block = screen.getByTestId('sql')
    expect(block.className).toContain('whitespace-pre-wrap')
    expect(block.className).toContain('wrap-anywhere')
    expect(block.className).not.toContain('whitespace-pre ')
    expect(block.closest('[data-radix-scroll-area-viewport]')).toBeNull()
    const lines = block.querySelectorAll<HTMLElement>('[data-sql-line]')
    expect(lines).toHaveLength(2)
    for (const line of lines) {
      expect(line.style.paddingLeft).toBe('2ch')
      expect(line.style.textIndent).toBe('-2ch')
    }
  })

  it('keeps the keyword colours, the text, and a break after each dot', () => {
    render(<SqlCode sql={SQL} testId="sql" />)
    const block = screen.getByTestId('sql')
    expect(block.textContent).toBe(SQL)
    expect(
      [...block.querySelectorAll('.text-series-2')].map((el) => el.textContent)
    ).toEqual(['CREATE INDEX CONCURRENTLY', 'ON', 'SELECT'])
    const first = block.querySelector('[data-sql-line]') as HTMLElement
    expect(first.innerHTML).toContain('public.<wbr>order_items')
  })

  it('colours a rollback the same way', () => {
    render(<SqlCode sql="DROP INDEX CONCURRENTLY public.idx;" testId="sql" />)
    expect(
      [...screen.getByTestId('sql').querySelectorAll('.text-series-2')].map(
        (el) => el.textContent
      )
    ).toEqual(['DROP INDEX CONCURRENTLY'])
  })
})
