import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConfigureForm } from './ConfigureForm'

describe('ConfigureForm connection URL parsing', () => {
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

  it('generates the internal password lookup name from the target name', () => {
    const onSubmit = vi.fn()

    render(<ConfigureForm onSubmit={onSubmit} />)

    fireEvent.change(
      screen.getByPlaceholderText('postgresql://user@host:5432/database'),
      {
        target: {
          // Fictional fixture credentials for the URL parser.
          value: 'postgresql://alice:secret@db.example.com:5432/app_db', // trufflehog:ignore
        },
      }
    )
    fireEvent.click(screen.getByRole('button', { name: 'Parse' }))
    fireEvent.change(screen.getByPlaceholderText('my-database'), {
      target: { value: 'customer prod' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Add Target' }))

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'customer prod',
        password_env: 'RDST_CUSTOMER_PROD_PASSWORD',
      })
    )
  })
})

describe('ConfigureForm sandbox behavior', () => {
  afterEach(() => {
    cleanup()
  })

  it('does not expose or submit a persistent Readyset deployment option', () => {
    const onSubmit = vi.fn()

    render(<ConfigureForm onSubmit={onSubmit} />)

    expect(
      screen.queryByRole('switch', { name: 'Deploy Readyset now' })
    ).toBeNull()

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

    const submitted = onSubmit.mock.calls[0]?.[0]
    expect(submitted).toBeDefined()
    expect(submitted).not.toHaveProperty('deploy')
  })

  it('does not expose persistent deployment while editing a target', () => {
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
