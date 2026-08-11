import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { SQLSchemaStatus } from './SQLSchemaStatus'

afterEach(() => cleanup())

describe('SQLSchemaStatus', () => {
  it('shows the connected schema context', () => {
    render(
      <SQLSchemaStatus
        isLoading={false}
        schema={{
          tables: {
            posts: [],
            users: [],
          },
          dialect: 'postgresql',
        }}
      />
    )

    expect(screen.getByText('2 tables')).toBeTruthy()
    expect(screen.getByText('postgresql')).toBeTruthy()
  })

  it('shows schema loading progress', () => {
    render(<SQLSchemaStatus isLoading />)

    expect(screen.getByText('Connecting to database...')).toBeTruthy()
  })

  it('shows the targetless state', () => {
    render(<SQLSchemaStatus isLoading={false} />)

    expect(screen.getByText('No target selected')).toBeTruthy()
  })
})
