import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { SchemaTable, SchemaTableColumn } from '../../types/schema'
import { SchemaTableTree } from './SchemaTableTree'

function column(
  name: string,
  description: string | null = null
): SchemaTableColumn {
  return {
    name,
    data_type: 'text',
    description,
    unit: null,
    is_pii: false,
    enum_values: null,
  }
}

function table(
  name: string,
  columns: SchemaTableColumn[] = [column('id')],
  description: string | null = null
): SchemaTable {
  return {
    name,
    description,
    business_context: null,
    row_estimate: null,
    columns,
    relationships: [],
  }
}

const tables: SchemaTable[] = [
  table(
    'customers',
    [column('id'), column('email_address')],
    'Customer accounts'
  ),
  table('orders', [column('id'), column('total')]),
  table('shipments', [column('id'), column('carrier', 'Delivery partner')]),
]

function searchBox(): HTMLInputElement {
  return screen.getByRole('textbox', {
    name: 'Search tables and columns',
  }) as HTMLInputElement
}

afterEach(cleanup)

describe('SchemaTableTree search', () => {
  it('filters tables by name and reports the visible count', () => {
    render(<SchemaTableTree tables={tables} />)

    expect(screen.getByText('3 tables')).toBeDefined()

    fireEvent.change(searchBox(), { target: { value: 'ship' } })

    expect(screen.getByText('1 of 3 tables')).toBeDefined()
    expect(screen.getByText('shipments')).toBeDefined()
    expect(screen.queryByText('orders')).toBeNull()
  })

  it('matches column names and opens the table that matched', () => {
    render(<SchemaTableTree tables={tables} />)

    fireEvent.change(searchBox(), { target: { value: 'email' } })

    expect(screen.getByText('customers')).toBeDefined()
    expect(screen.queryByText('orders')).toBeNull()
    // The match is inside the table, so the table opens to show why.
    expect(screen.getByText('email_address')).toBeDefined()
  })

  it('matches column descriptions', () => {
    render(<SchemaTableTree tables={tables} />)

    fireEvent.change(searchBox(), { target: { value: 'delivery partner' } })

    expect(screen.getByText('shipments')).toBeDefined()
    expect(screen.queryByText('customers')).toBeNull()
  })

  it('offers an empty state that clears the search', () => {
    render(<SchemaTableTree tables={tables} />)

    fireEvent.change(searchBox(), { target: { value: 'nothing here' } })
    expect(screen.getByText('No tables match "nothing here"')).toBeDefined()

    fireEvent.click(screen.getByRole('button', { name: 'Show all tables' }))

    expect(screen.getByText('3 tables')).toBeDefined()
    expect(screen.getByText('orders')).toBeDefined()
  })

  it('clears the search on Escape', () => {
    render(<SchemaTableTree tables={tables} />)

    fireEvent.change(searchBox(), { target: { value: 'ship' } })
    fireEvent.keyDown(searchBox(), { key: 'Escape' })

    expect(searchBox().value).toBe('')
    expect(screen.getByText('3 tables')).toBeDefined()
  })

  it('focuses the search box when "/" is pressed', () => {
    render(<SchemaTableTree tables={tables} />)

    fireEvent.keyDown(document, { key: '/' })

    expect(document.activeElement).toBe(searchBox())
  })

  it('reveals a long table list one page at a time', () => {
    const many = Array.from({ length: 95 }, (_, index) =>
      table(`fact_table_${String(index).padStart(3, '0')}`)
    )
    render(<SchemaTableTree tables={many} />)

    expect(screen.getByText('fact_table_039')).toBeDefined()
    expect(screen.queryByText('fact_table_040')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Show 40 more tables' }))
    expect(screen.getByText('fact_table_079')).toBeDefined()

    fireEvent.click(screen.getByRole('button', { name: 'Show 15 more tables' }))
    expect(screen.getByText('fact_table_094')).toBeDefined()
  })
})
