import { fireEvent, render, screen } from '@testing-library/react'
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { ConfigureForm } from './ConfigureForm'

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

describe('ConfigureForm connection URL parsing', () => {
  beforeAll(() => {
    vi.stubGlobal('ResizeObserver', ResizeObserverMock)
  })

  afterAll(() => {
    vi.unstubAllGlobals()
  })

  it('enables TLS when parsing a PostgreSQL URL with sslmode=require', () => {
    const onSubmit = vi.fn()

    render(<ConfigureForm onSubmit={onSubmit} />)

    fireEvent.change(
      screen.getByPlaceholderText('postgresql://user@host:5432/database'),
      {
        target: {
          value:
            'postgresql://alice:secret@db.example.com:5432/app_db?sslmode=require',
        },
      }
    )

    fireEvent.click(screen.getByRole('button', { name: 'Parse' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add Target' }))

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        engine: 'postgresql',
        host: 'db.example.com',
        port: 5432,
        database: 'app_db',
        user: 'alice',
        tls: true,
      })
    )
  })
})
