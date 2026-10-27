import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'

const configure = vi.hoisted(() => ({
  getTarget: vi.fn(),
  updateTarget: vi.fn(),
  testConnection: vi.fn(),
  connectionTestResult: null,
}))
vi.mock('@/lib/useConfigure', () => ({ useConfigure: () => configure }))

const { ConnectionRepair, connectionWarning } = await import(
  './ConnectionRepair'
)

const detail = {
  name: 'database',
  engine: 'postgresql',
  host: 'localhost',
  port: 5432,
  database: 'database',
  user: 'user',
  has_password: true,
  is_default: false,
}

beforeEach(() => {
  configure.getTarget.mockReset().mockResolvedValue(detail)
  configure.updateTarget.mockReset().mockResolvedValue(undefined)
})
afterEach(cleanup)

it('names the host and the likely cause', () => {
  expect(connectionWarning(detail, 'Connection refused')).toBe(
    'localhost:5432 refused the connection. Is PostgreSQL running?'
  )
  expect(connectionWarning(detail, 'timeout')).toBe('localhost:5432: timeout')
  expect(connectionWarning(null, null)).toBe("The database can't be reached.")
})

it('shows the saved fields, keeps the password unless typed, and checks again', async () => {
  const onSaved = vi.fn()
  const onClose = vi.fn()
  renderWithClient(
    <ConnectionRepair
      target="database"
      open
      onClose={onClose}
      reason='connection to server at "localhost" (::1), port 5432 failed: Connection refused'
      onSaved={onSaved}
    />
  )
  expect(
    screen.getByRole('heading', { name: "Can't reach database" })
  ).toBeTruthy()
  expect(
    await screen.findAllByText(
      'localhost:5432 refused the connection. Is PostgreSQL running?'
    )
  ).not.toHaveLength(0)
  expect(
    screen.getByText('Monitoring resumes by itself once it connects.')
  ).toBeTruthy()
  const host = await screen.findByLabelText('Host')
  expect((host as HTMLInputElement).value).toBe('localhost')
  expect(
    (screen.getByLabelText('Password') as HTMLInputElement).placeholder
  ).toBe('Leave blank to keep it')
  expect(screen.getByRole('button', { name: /Details/ })).toBeTruthy()
  fireEvent.change(screen.getByLabelText('Database'), {
    target: { value: 'shop' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
  await waitFor(() => expect(onSaved).toHaveBeenCalled())
  expect(configure.updateTarget).toHaveBeenCalledWith(
    'database',
    expect.objectContaining({
      host: 'localhost',
      port: 5432,
      database: 'shop',
      password: undefined,
    })
  )
  expect(onClose).toHaveBeenCalled()
})

it('asks for the password again before a new host gets it', async () => {
  const onSaved = vi.fn()
  renderWithClient(
    <ConnectionRepair
      target="database"
      open
      onClose={vi.fn()}
      reason="Connection refused"
      onSaved={onSaved}
    />
  )
  const host = await screen.findByLabelText('Host')
  fireEvent.change(host, { target: { value: '127.0.0.1' } })
  expect(
    (screen.getByLabelText('Password') as HTMLInputElement).placeholder
  ).toBe('Enter it again for the new address')
  fireEvent.click(screen.getByRole('button', { name: 'Check again' }))

  expect(
    await screen.findByText(
      'Enter the password again: RDST sends a saved password to a new address only when you type it.'
    )
  ).toBeTruthy()
  expect(configure.updateTarget).not.toHaveBeenCalled()

  fireEvent.change(screen.getByLabelText('Password'), {
    target: { value: 'n3w' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
  await waitFor(() => expect(onSaved).toHaveBeenCalled())
  expect(configure.updateTarget).toHaveBeenCalledWith(
    'database',
    expect.objectContaining({ host: '127.0.0.1', password: 'n3w' })
  )
})
