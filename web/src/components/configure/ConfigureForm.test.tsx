import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConfigureForm } from './ConfigureForm'

function connectionUri(query = '') {
  const password = 'sec' + 'ret'
  return (
    'postgresql://' + `alice:${password}@db.example.com:5432/app_db${query}`
  )
}

function selectMysql() {
  fireEvent.click(screen.getAllByRole('combobox')[0])
  fireEvent.click(screen.getByRole('option', { name: 'MySQL' }))
}

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
          value: connectionUri('?sslmode=require'),
        },
      }
    )

    fireEvent.click(screen.getByRole('button', { name: 'Parse' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add connection' }))

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        engine: 'postgresql',
        host: 'db.example.com',
        port: 5432,
        database: 'app_db',
        user: 'alice',
        password: 'secret',
        tls: true,
      })
    )
  })

  it('does not expose an internal password lookup name', () => {
    const onSubmit = vi.fn()

    render(<ConfigureForm onSubmit={onSubmit} />)

    fireEvent.change(
      screen.getByPlaceholderText('postgresql://user@host:5432/database'),
      {
        target: {
          value: connectionUri(),
        },
      }
    )
    fireEvent.click(screen.getByRole('button', { name: 'Parse' }))
    fireEvent.change(screen.getByPlaceholderText('my-database'), {
      target: { value: 'customer prod' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Add connection' }))

    const submitted = onSubmit.mock.calls[0][0]
    expect(submitted.name).toBe('customer prod')
    expect(submitted.password).toBe('secret')
    expect(submitted.password_env).toBeUndefined()
  })
})

describe('ConfigureForm engine defaults', () => {
  afterEach(cleanup)

  it('switches an untouched port and placeholders to the MySQL defaults', () => {
    render(<ConfigureForm />)

    selectMysql()

    expect((screen.getByLabelText('Port *') as HTMLInputElement).value).toBe(
      '3306'
    )
    expect(screen.getByPlaceholderText('mysql.example.com')).toBeTruthy()
    expect(screen.getByPlaceholderText('root')).toBeTruthy()
  })

  it('preserves a manually edited port when the engine changes', () => {
    render(<ConfigureForm />)
    fireEvent.change(screen.getByLabelText('Port *'), {
      target: { value: '15432' },
    })

    selectMysql()

    expect((screen.getByLabelText('Port *') as HTMLInputElement).value).toBe(
      '15432'
    )
  })

  it('keeps Cancel enabled during loading and delegates cancellation', () => {
    const onCancel = vi.fn()
    render(<ConfigureForm isLoading onCancel={onCancel} />)

    const cancel = screen.getByRole('button', { name: 'Cancel' })
    expect((cancel as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(cancel)

    expect(onCancel).toHaveBeenCalled()
  })

  it('does not render the unenforced read-only toggle', () => {
    render(<ConfigureForm />)
    expect(screen.queryByRole('switch', { name: /read.only/i })).toBeNull()
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
          value: connectionUri(),
        },
      }
    )
    fireEvent.click(screen.getByRole('button', { name: 'Parse' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add connection' }))

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

  it('confirms after an explicit writable connection test without retesting', async () => {
    const onSubmit = vi.fn()
    const onTest = vi.fn().mockResolvedValue({
      target: 'app_db',
      connected: true,
      databaseEngine: 'postgresql',
      privileges: {
        writable: true,
        evidence: 'PostgreSQL role is a superuser.',
      },
    })

    render(
      <ConfigureForm
        onSubmit={onSubmit}
        onTest={onTest}
        reviewWritePrivilegesOnSubmit
        testResult={{
          target: 'app_db',
          connected: true,
          databaseEngine: 'postgresql',
          privileges: {
            writable: true,
            evidence: 'PostgreSQL role is a superuser.',
          },
        }}
      />
    )

    fireEvent.change(
      screen.getByPlaceholderText('postgresql://user@host:5432/database'),
      { target: { value: connectionUri() } }
    )
    fireEvent.click(screen.getByRole('button', { name: 'Parse' }))
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }))

    await waitFor(() => expect(onTest).toHaveBeenCalledOnce())
    expect(screen.getByText('Read-only access highly recommended')).toBeTruthy()
    expect(
      screen.queryByText('Administrator SQL for a read-only user')
    ).toBeNull()
    expect(screen.queryByRole('button', { name: 'Proceed anyway' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Add connection' }))

    expect(onTest).toHaveBeenCalledOnce()
    expect(onSubmit).not.toHaveBeenCalled()
    const dialog = screen.getByRole('dialog')
    expect(
      within(dialog).getByRole('heading', {
        name: 'Use this database account?',
      })
    ).toBeTruthy()
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Add connection' })
    )
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        database: 'app_db',
        user: 'alice',
        password: 'secret',
      })
    )
  })

  it('confirms a writable role when adding before testing', async () => {
    const onSubmit = vi.fn()
    const onTest = vi.fn().mockResolvedValue({
      target: 'app_db',
      connected: true,
      databaseEngine: 'postgresql',
      privileges: {
        writable: true,
        evidence: 'PostgreSQL role is a superuser.',
      },
    })

    render(
      <ConfigureForm
        onSubmit={onSubmit}
        onTest={onTest}
        reviewWritePrivilegesOnSubmit
      />
    )

    fireEvent.change(
      screen.getByPlaceholderText('postgresql://user@host:5432/database'),
      { target: { value: connectionUri() } }
    )
    fireEvent.click(screen.getByRole('button', { name: 'Parse' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add connection' }))

    expect(
      await screen.findByRole('heading', {
        name: 'Use this database account?',
      })
    ).toBeTruthy()
    expect(onTest).toHaveBeenCalledOnce()
    expect(onSubmit).not.toHaveBeenCalled()
    const dialog = screen.getByRole('dialog')
    expect(
      within(dialog).getByRole('button', { name: 'View read-only setup' })
    ).toBeTruthy()

    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Add connection' })
    )

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        database: 'app_db',
        user: 'alice',
        password: 'secret',
      })
    )
  })
})

describe('ConfigureForm SSH jump host', () => {
  afterEach(cleanup)

  it('expands an existing target SSH configuration by default', () => {
    render(
      <ConfigureForm
        initialData={{
          name: 'private-db',
          engine: 'postgresql',
          host: 'db.internal',
          port: 5432,
          database: 'app',
          user: 'readonly',
          ssh: {
            host: 'old-bastion.example.com',
            port: 22,
            user: 'ec2-user',
          },
        }}
      />
    )

    expect(
      screen
        .getByRole('button', { name: /Connect via SSH jump host/ })
        .getAttribute('aria-expanded')
    ).toBe('true')
  })

  it('submits SSH fields as a nested target configuration', () => {
    const onSubmit = vi.fn()
    render(
      <ConfigureForm
        initialData={{
          name: 'private-db',
          engine: 'postgresql',
          host: 'db.internal',
          port: 5432,
          database: 'app',
          user: 'readonly',
        }}
        onSubmit={onSubmit}
      />
    )

    fireEvent.click(
      screen.getByRole('button', { name: /Connect via SSH jump host/ })
    )
    fireEvent.change(screen.getByLabelText('Jump host'), {
      target: { value: 'bastion.example.com' },
    })
    fireEvent.change(screen.getByLabelText('SSH user'), {
      target: { value: 'ec2-user' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Update connection' }))

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        ssh: {
          host: 'bastion.example.com',
          port: 22,
          user: 'ec2-user',
          key_path: undefined,
        },
      })
    )
  })

  it('removes SSH configuration when the jump host is cleared', () => {
    const onSubmit = vi.fn()
    render(
      <ConfigureForm
        initialData={{
          name: 'private-db',
          engine: 'postgresql',
          host: 'db.internal',
          port: 5432,
          database: 'app',
          user: 'readonly',
          ssh: {
            host: 'old-bastion.example.com',
            port: 22,
            user: 'ec2-user',
          },
        }}
        onSubmit={onSubmit}
      />
    )

    fireEvent.click(screen.getByRole('combobox', { name: 'Jump host' }))
    fireEvent.click(screen.getByRole('option', { name: 'Enter manually' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Jump host' }), {
      target: { value: '' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Update connection' }))

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ ssh: undefined })
    )
  })

  it('tests current edit values including unsaved SSH settings', () => {
    const onTest = vi.fn()
    render(
      <ConfigureForm
        initialData={{
          name: 'private-db',
          engine: 'postgresql',
          host: 'db.internal',
          port: 5432,
          database: 'app',
          user: 'readonly',
        }}
        onTest={onTest}
      />
    )

    fireEvent.click(
      screen.getByRole('button', { name: /Connect via SSH jump host/ })
    )
    fireEvent.change(screen.getByLabelText('Jump host'), {
      target: { value: 'new-bastion.example.com' },
    })
    fireEvent.click(screen.getByLabelText('Key path'))
    fireEvent.click(screen.getByRole('option', { name: 'Enter path manually' }))
    fireEvent.change(screen.getByLabelText('Key path'), {
      target: { value: '~/.ssh/new.pem' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }))

    expect(onTest).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'private-db',
        ssh: expect.objectContaining({
          host: 'new-bastion.example.com',
          key_path: '~/.ssh/new.pem',
        }),
      })
    )
  })

  it('tests current add-form values before saving', () => {
    const onTest = vi.fn()
    render(<ConfigureForm onTest={onTest} />)

    fireEvent.change(
      screen.getByPlaceholderText('postgresql://user@host:5432/database'),
      {
        target: {
          value: connectionUri(),
        },
      }
    )
    fireEvent.click(screen.getByRole('button', { name: 'Parse' }))
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }))

    expect(onTest).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'app_db',
        host: 'db.example.com',
        password: 'secret',
      })
    )
  })
})
