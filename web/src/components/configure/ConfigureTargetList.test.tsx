import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { ConfigureTarget } from '../../types/configure'
import { ConfigureTargetList } from './ConfigureTargetList'

afterEach(cleanup)

const target: ConfigureTarget = {
  name: 'orders_db',
  engine: 'postgresql',
  host: 'db.internal',
  port: 5432,
} as ConfigureTarget

describe('ConfigureTargetList', () => {
  it('does not claim there are no targets while the list is still loading', () => {
    render(<ConfigureTargetList targets={[]} isPending onAdd={() => {}} />)

    expect(screen.queryByText('No targets yet')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Add target' })).toBeNull()
    expect(screen.getByText('Loading targets')).toBeTruthy()
  })

  it('offers to add one once the list has landed empty', () => {
    render(
      <ConfigureTargetList targets={[]} isPending={false} onAdd={() => {}} />
    )

    // The shared EmptyState anatomy: heading, one-line body, one CTA. [F-18]
    expect(screen.getByRole('heading', { name: 'No targets yet' })).toBeTruthy()
    expect(
      screen.getByText(
        'Import from a provider, or enter the database details manually.'
      )
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Add target' })).toBeTruthy()
  })

  it('shows the rows it has even while a refresh is in flight', () => {
    render(<ConfigureTargetList targets={[target]} isPending />)

    expect(screen.getByText('orders_db')).toBeTruthy()
    expect(screen.queryByText('Loading targets')).toBeNull()
  })
})
