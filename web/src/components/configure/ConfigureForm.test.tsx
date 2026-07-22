import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
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

  afterEach(() => {
    cleanup()
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
        password: 'secret',
        password_env: 'RDST_APP_DB_PASSWORD',
        tls: true,
      })
    )
  })

  it('defaults the password environment name from the target name', () => {
    const onSubmit = vi.fn()

    render(<ConfigureForm onSubmit={onSubmit} />)

    fireEvent.change(screen.getByPlaceholderText('my-database'), {
      target: { value: 'customer prod' },
    })

    expect(
      (screen.getByPlaceholderText('RDST_MY_DATABASE_PASSWORD') as HTMLInputElement)
        .value,
    ).toBe('RDST_CUSTOMER_PROD_PASSWORD')
  })
})


describe('ConfigureForm deploy option', () => {
  beforeAll(() => {
    vi.stubGlobal('ResizeObserver', ResizeObserverMock)
  })

  afterAll(() => {
    vi.unstubAllGlobals()
  })

  afterEach(() => {
    cleanup()
  })

  it('is pre-checked in add mode and included in the submit payload', () => {
    const onSubmit = vi.fn()

    render(<ConfigureForm onSubmit={onSubmit} />)

    const deploySwitch = screen.getByRole('switch', {
      name: 'Deploy Readyset now',
    })
    expect(deploySwitch.getAttribute('aria-checked')).toBe('true')

    fireEvent.change(
      screen.getByPlaceholderText('postgresql://user@host:5432/database'),
      {
        target: {
          value: 'postgresql://alice:secret@db.example.com:5432/app_db', // trufflehog:ignore
        },
      }
    )
    fireEvent.click(screen.getByRole('button', { name: 'Parse' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add Target' }))

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ deploy: true })
    )
  })

  it('unchecking it submits deploy: false', () => {
    const onSubmit = vi.fn()

    render(<ConfigureForm onSubmit={onSubmit} />)

    fireEvent.click(screen.getByRole('switch', { name: 'Deploy Readyset now' }))
    fireEvent.change(
      screen.getByPlaceholderText('postgresql://user@host:5432/database'),
      {
        target: {
          value: 'postgresql://alice:secret@db.example.com:5432/app_db', // trufflehog:ignore
        },
      }
    )
    fireEvent.click(screen.getByRole('button', { name: 'Parse' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add Target' }))

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ deploy: false })
    )
  })

  it('is absent when editing an existing target', () => {
    render(
      <ConfigureForm
        initialData={{
          name: 'imdb',
          engine: 'postgresql',
          host: 'localhost',
          port: 5432,
          database: 'imdb',
          user: 'postgres',
        }}
      />
    )

    expect(
      screen.queryByRole('switch', { name: 'Deploy Readyset now' })
    ).toBeNull()
  })
})
