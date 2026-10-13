import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { selectGlobalTarget } from '../../features/database/targetSelection'
import { startBootstrapRun } from '../../lib/backgroundRuns'
import { useConfigure } from '../../lib/useConfigure'
import { useSystemStatus } from '../../lib/useSystemStatus'
import type { ConfigureTargetDetail } from '../../types/configure'
import {
  AddDatabaseDialog,
  connectionFrom,
  newDatabaseName,
  serverVersion,
} from './AddDatabaseDialog'
import type { AddRequest } from './addDatabase'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-router')>()),
  useNavigate: () => navigate,
}))
vi.mock('../../lib/useConfigure', () => ({ useConfigure: vi.fn() }))
vi.mock('../../lib/useSystemStatus', () => ({ useSystemStatus: vi.fn() }))
vi.mock('../../lib/backgroundRuns', () => ({ startBootstrapRun: vi.fn() }))
vi.mock('../../features/database/targetSelection', () => ({
  selectGlobalTarget: vi.fn(),
}))
vi.mock('../../lib/tunnels', () => ({
  fetchSshKeys: vi.fn().mockResolvedValue([]),
  fetchSshProfiles: vi.fn().mockResolvedValue([]),
  fetchSshDirectory: vi.fn(),
  importSshKey: vi.fn(),
}))
// The import has its own suite; here it only has to open and hand back. The provider
// list is the real one.
vi.mock('./ProviderImport', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./ProviderImport')>()),
  ProviderImport: ({ initialTab }: { initialTab: string }) => (
    <span>{`Import source: ${initialTab}`}</span>
  ),
}))

const configure = {
  testConnection: vi.fn(),
  addTarget: vi.fn(),
  updateTarget: vi.fn(),
  setDefaultTarget: vi.fn(),
}

function renderDialog(
  request: AddRequest | null = 'form',
  taken: string[] = []
) {
  vi.mocked(useSystemStatus).mockReturnValue({
    data: { targets: taken.map((name) => ({ name })) },
  } as never)
  const onClose = vi.fn()
  renderWithClient(<AddDatabaseDialog request={request} onClose={onClose} />)
  return { onClose }
}

const field = (label: string) =>
  screen.getByLabelText(label) as HTMLInputElement
const type = (label: string, value: string) =>
  fireEvent.change(field(label), { target: { value } })

beforeEach(() => {
  vi.clearAllMocks()
  configure.testConnection.mockResolvedValue({ target: 'x', connected: true })
  configure.addTarget.mockResolvedValue(undefined)
  configure.updateTarget.mockResolvedValue(undefined)
  configure.setDefaultTarget.mockResolvedValue(undefined)
  vi.mocked(useConfigure).mockReturnValue(configure as never)
})

afterEach(() => {
  cleanup()
  localStorage.clear()
})

const CAPTURE_MISSING = {
  available: false,
  source: 'pg_stat_statements',
  title: 'Enable historical query discovery',
  detail: 'missing',
  instructions: [
    'Add pg_stat_statements to shared_preload_libraries, keeping any libraries already listed, and restart PostgreSQL.',
    'Run CREATE EXTENSION IF NOT EXISTS pg_stat_statements; in this database.',
  ],
  config: ["shared_preload_libraries = 'pg_stat_statements'"],
  sql: ['CREATE EXTENSION IF NOT EXISTS pg_stat_statements;'],
}

const solidLabels = () =>
  screen
    .getAllByRole('button')
    .filter((button) => button.className.includes('bg-surface-primary-solid'))
    .map((button) => button.textContent)

describe('AddDatabaseDialog', () => {
  it('lays out the mock: a string, the fields, a name, Advanced, a caption, Test and one solid', () => {
    renderDialog()

    expect(screen.getByRole('dialog', { name: 'Add a database' })).toBeTruthy()
    for (const label of [
      'Connection string',
      'Host',
      'Port',
      'Database',
      'User',
      'Password',
      'Name',
    ])
      expect(field(label)).toBeTruthy()
    expect(screen.getByText('or fill in the fields')).toBeTruthy()
    expect(screen.getByText('or connect one by hand')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Advanced/ })).toBeTruthy()
    expect(
      screen.getByText(
        'RDST checks what monitoring needs and asks before it turns on.'
      )
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Test' })).toBeTruthy()
    expect(solidLabels()).toEqual(['Add and monitor'])
  })

  it('names the database after its database unless a name is typed', async () => {
    renderDialog('form', ['shop'])

    type('Database', 'shop')
    expect(field('Name').placeholder).toBe('shop-2')

    type('Host', 'db.internal')
    type('User', 'reader')
    type('Name', 'storefront')
    fireEvent.click(screen.getByRole('button', { name: 'Add and monitor' }))

    await waitFor(() => expect(navigate).toHaveBeenCalled())
    expect(configure.testConnection).toHaveBeenCalledWith(
      'storefront',
      expect.objectContaining({ name: 'storefront', database: 'shop' })
    )
    expect(configure.addTarget.mock.calls[0][0].name).toBe('storefront')
  })

  it('refuses a name another database has, once, at the top', () => {
    renderDialog('form', ['shop'])

    type('Connection string', 'postgresql://rdst@localhost/demo')
    type('Name', 'Shop')
    fireEvent.click(screen.getByRole('button', { name: 'Add and monitor' }))

    expect(
      screen.getAllByText('A database named Shop already exists.')
    ).toHaveLength(1)
    expect(configure.testConnection).not.toHaveBeenCalled()
  })

  it('says what is missing when the form is sent empty', () => {
    renderDialog()

    fireEvent.click(screen.getByRole('button', { name: 'Test' }))

    expect(
      screen.getByText(
        'Fill in the host, database and user, or paste a connection string.'
      )
    ).toBeTruthy()
    expect(configure.testConnection).not.toHaveBeenCalled()
  })

  it('names the database it already watches and adds nothing', async () => {
    configure.addTarget.mockRejectedValue(new Error('Already added as shop.'))
    renderDialog('form', ['shop'])

    type('Connection string', 'postgresql://rdst@localhost/shop')
    fireEvent.click(screen.getByRole('button', { name: 'Add and monitor' }))

    expect(await screen.findAllByText('Already added as shop.')).toHaveLength(1)
    expect(startBootstrapRun).not.toHaveBeenCalled()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('says which server answered', async () => {
    configure.testConnection.mockResolvedValue({
      target: 'demo',
      connected: true,
      engine: 'PostgreSQL 16.3 on aarch64-unknown-linux-gnu, compiled by gcc',
    })
    renderDialog()

    type('Connection string', 'postgresql://rdst@localhost/demo')
    fireEvent.click(screen.getByRole('button', { name: 'Test' }))

    expect(
      await screen.findByText('Connected to PostgreSQL 16.3.')
    ).toBeTruthy()
  })

  it('reads a connection string into the fields and tests what it says', async () => {
    renderDialog()

    type(
      'Connection string',
      'postgresql://rdst:secret@127.0.0.1:15432/demo?sslmode=require'
    )
    expect(field('Host').value).toBe('127.0.0.1')
    expect(field('Port').value).toBe('15432')
    expect(field('Database').value).toBe('demo')
    expect(field('User').value).toBe('rdst')
    // The string wins while it is there.
    expect(field('Host').disabled).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: 'Test' }))

    await waitFor(() =>
      expect(configure.testConnection).toHaveBeenCalledWith(
        'demo',
        expect.objectContaining({
          name: 'demo',
          engine: 'postgresql',
          host: '127.0.0.1',
          port: 15432,
          database: 'demo',
          user: 'rdst',
          password: 'secret',
          tls: true,
        })
      )
    )
    expect(await screen.findByText('Connected.')).toBeTruthy()
    expect(configure.addTarget).not.toHaveBeenCalled()
  })

  it('says what a string it cannot read should look like', () => {
    renderDialog()

    type('Connection string', 'not a url')
    fireEvent.click(screen.getByRole('button', { name: 'Test' }))

    expect(
      screen.getByText('Expected postgresql://user@host:5432/database')
    ).toBeTruthy()
    expect(configure.testConnection).not.toHaveBeenCalled()
  })

  it('adds from the fields and opens the database on the turn-on dialog', async () => {
    renderDialog('form', ['shop'])

    type('Host', 'db.internal')
    type('Port', '5432')
    type('Database', 'shop')
    type('User', 'reader')
    type('Password', 'pw')
    fireEvent.click(screen.getByRole('button', { name: 'Add and monitor' }))

    await waitFor(() => expect(navigate).toHaveBeenCalled())
    const added = configure.addTarget.mock.calls[0][0]
    expect(added).toMatchObject({
      name: 'shop-2',
      engine: 'postgresql',
      host: 'db.internal',
      port: 5432,
      database: 'shop',
      user: 'reader',
      password: 'pw',
    })
    // It tests before it saves.
    expect(configure.testConnection.mock.invocationCallOrder[0]).toBeLessThan(
      configure.addTarget.mock.invocationCallOrder[0]
    )
    // Not the first database, so the default stays.
    expect(configure.setDefaultTarget).not.toHaveBeenCalled()
    expect(startBootstrapRun).toHaveBeenCalledWith('shop-2')
    // Monitoring turns on from the turn-on dialog, after its checks and warning.
    expect(navigate).toHaveBeenCalledWith({
      to: '/database',
      search: { target: 'shop-2', panel: 'enable' },
    })
  })

  it('opens the new database by name so the URL carries it', async () => {
    renderDialog()

    type('Connection string', 'postgresql://rdst@localhost/demo')
    fireEvent.click(screen.getByRole('button', { name: 'Add and monitor' }))

    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/database',
        search: { target: 'demo', panel: 'enable' },
      })
    )
  })

  it('makes the first database the default', async () => {
    renderDialog()

    type('Connection string', 'postgresql://rdst@localhost/demo')
    fireEvent.click(screen.getByRole('button', { name: 'Add and monitor' }))

    await waitFor(() =>
      expect(configure.setDefaultTarget).toHaveBeenCalledWith('demo')
    )
  })

  it('keeps the dialog and says why when the database does not answer', async () => {
    configure.testConnection.mockResolvedValue({
      target: 'demo',
      connected: false,
      error: 'connection refused',
    })
    renderDialog()

    type('Connection string', 'postgresql://rdst@localhost/demo')
    fireEvent.click(screen.getByRole('button', { name: 'Add and monitor' }))

    // Once, at the top of the body.
    expect(await screen.findAllByText('connection refused')).toHaveLength(1)
    expect(configure.addTarget).not.toHaveBeenCalled()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('says what to change when the query statistics are unreadable and holds the add', async () => {
    configure.testConnection.mockResolvedValue({
      target: 'shop',
      connected: true,
      queryCapture: CAPTURE_MISSING,
    })
    renderDialog()

    type('Host', 'db.internal')
    type('Database', 'shop')
    type('User', 'reader')
    fireEvent.click(screen.getByRole('button', { name: 'Add and monitor' }))

    expect(
      await screen.findByText("Monitoring can't read query statistics yet")
    ).toBeTruthy()
    for (const step of CAPTURE_MISSING.instructions)
      expect(screen.getByText(step)).toBeTruthy()
    expect(
      screen.getByText("shared_preload_libraries = 'pg_stat_statements'")
    ).toBeTruthy()
    expect(screen.getAllByRole('button', { name: 'Copy' })).toHaveLength(2)
    expect(configure.addTarget).not.toHaveBeenCalled()
    expect(solidLabels()).toEqual(['Add and monitor'])

    // Clicking again re-tests; still unreadable, nothing is added.
    fireEvent.click(screen.getByRole('button', { name: 'Add and monitor' }))
    await waitFor(() =>
      expect(configure.testConnection).toHaveBeenCalledTimes(2)
    )
    expect(configure.addTarget).not.toHaveBeenCalled()
    expect(navigate).not.toHaveBeenCalled()

    // Fixed on the server: the next click adds and opens the turn-on dialog.
    configure.testConnection.mockResolvedValue({
      target: 'shop',
      connected: true,
      queryCapture: { ...CAPTURE_MISSING, available: true, instructions: [] },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Add and monitor' }))
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/database',
        search: { target: 'shop', panel: 'enable' },
      })
    )
    expect(configure.addTarget.mock.calls[0][0].name).toBe('shop')
  })

  it('adds a database whose statistics are unreadable only from the quiet link, with monitoring off', async () => {
    configure.testConnection.mockResolvedValue({
      target: 'shop',
      connected: true,
      queryCapture: CAPTURE_MISSING,
    })
    renderDialog()

    type('Host', 'db.internal')
    type('Database', 'shop')
    type('User', 'reader')
    fireEvent.click(screen.getByRole('button', { name: 'Add and monitor' }))
    const link = await screen.findByRole('button', {
      name: 'Add without monitoring',
    })
    expect(
      screen.getByText(
        /RDST can't watch this database until the setup steps above are done/
      )
    ).toBeTruthy()
    // Blocked by default: only the solid button is primary, and nothing is added yet.
    expect(solidLabels()).toEqual(['Add and monitor'])
    expect(configure.addTarget).not.toHaveBeenCalled()

    fireEvent.click(link)
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/database',
        search: { target: 'shop' },
      })
    )
    expect(configure.addTarget.mock.calls[0][0].name).toBe('shop')
    // It saves without testing again.
    expect(configure.testConnection).toHaveBeenCalledTimes(1)
  })

  it('adds without asking when the query statistics are readable', async () => {
    configure.testConnection.mockResolvedValue({
      target: 'demo',
      connected: true,
      queryCapture: { ...CAPTURE_MISSING, available: true, instructions: [] },
    })
    renderDialog()

    type('Connection string', 'postgresql://rdst@localhost/demo')
    fireEvent.click(screen.getByRole('button', { name: 'Test' }))
    expect(
      await screen.findByText('Connected. Query statistics are readable.')
    ).toBeTruthy()
    expect(
      screen.queryByText("Monitoring can't read query statistics yet")
    ).toBeNull()
    expect(solidLabels()).toEqual(['Add and monitor'])
  })

  it('lists the providers first, each with its logo, above the form', () => {
    renderDialog()

    expect(screen.getByText('Import from')).toBeTruthy()
    const providers = ['AWS', 'Supabase', 'Neon', 'DigitalOcean', 'CSV file']
    const rows = providers.map((name) => screen.getByRole('button', { name }))
    for (const name of ['AWS', 'Supabase', 'Neon', 'DigitalOcean'])
      expect(screen.getByRole('img', { name })).toBeTruthy()
    const string = field('Connection string')
    for (const row of rows)
      expect(
        row.compareDocumentPosition(string) & Node.DOCUMENT_POSITION_FOLLOWING
      ).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: 'Import from a provider' })
    ).toBeNull()
  })

  it('opens a provider from its row and comes back', () => {
    renderDialog()

    fireEvent.click(screen.getByRole('button', { name: 'Supabase' }))
    expect(
      screen.getByRole('dialog', { name: 'Import from Supabase' })
    ).toBeTruthy()
    expect(screen.getByText('Import source: supabase')).toBeTruthy()
    expect(screen.queryByLabelText('Connection string')).toBeNull()

    // One title row: the way back sits before the title.
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(screen.getByLabelText('Connection string')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Back' })).toBeNull()

    // Escape in a provider view goes back too, and leaves the dialog open.
    fireEvent.click(screen.getByRole('button', { name: 'Supabase' }))
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(screen.getByRole('dialog', { name: 'Add a database' })).toBeTruthy()
    expect(screen.getByLabelText('Connection string')).toBeTruthy()
  })

  it('starts every opening on the connection form', () => {
    vi.mocked(useSystemStatus).mockReturnValue({
      data: { targets: [] },
    } as never)
    const { rerender } = renderWithClient(
      <AddDatabaseDialog request="form" onClose={vi.fn()} />
    )
    fireEvent.click(screen.getByRole('button', { name: 'AWS' }))
    rerender(<AddDatabaseDialog request={null} onClose={vi.fn()} />)
    rerender(<AddDatabaseDialog request="form" onClose={vi.fn()} />)

    expect(screen.getByRole('dialog', { name: 'Add a database' })).toBeTruthy()
  })

  it('opens straight on a provider when asked for one', () => {
    renderDialog('aws')

    expect(screen.getByText('Import source: aws')).toBeTruthy()
  })

  it('stays closed without a request', () => {
    renderDialog(null)

    expect(screen.queryByText('Add a database')).toBeNull()
  })
})

const saved: ConfigureTargetDetail = {
  name: 'orders',
  engine: 'postgresql',
  host: 'orders.internal',
  port: 6432,
  database: 'orders',
  user: 'app_ro',
  read_only: true,
  tls: true,
  ssh: { host: 'bastion.example.com', port: 22, user: 'ec2-user' },
  has_password: true,
  is_default: true,
}

function renderEdit(taken: string[] = ['orders', 'billing']) {
  vi.mocked(useSystemStatus).mockReturnValue({
    data: { targets: taken.map((name) => ({ name })) },
  } as never)
  const onClose = vi.fn()
  const onSaved = vi.fn()
  renderWithClient(
    <AddDatabaseDialog
      request={null}
      edit={saved}
      onClose={onClose}
      onSaved={onSaved}
    />
  )
  return { onClose, onSaved }
}

describe('AddDatabaseDialog editing', () => {
  it('opens on the saved fields with no providers and no string, Test and one solid Save changes', () => {
    renderEdit()

    expect(screen.getByRole('dialog', { name: 'Edit database' })).toBeTruthy()
    expect(screen.queryByLabelText('Connection string')).toBeNull()
    expect(screen.queryByRole('button', { name: 'AWS' })).toBeNull()
    expect(field('Name').value).toBe('orders')
    expect(field('Host').value).toBe('orders.internal')
    expect(field('Port').value).toBe('6432')
    expect(field('User').value).toBe('app_ro')
    expect(field('Password').value).toBe('')
    expect(field('Password').placeholder).toBe('Leave blank to keep it')
    expect(
      screen.getByText(
        'Stored in your local secret store, never in the database configuration.'
      )
    ).toBeTruthy()
    // A saved jump host or TLS opens Advanced.
    expect(
      screen
        .getByRole('button', { name: /Advanced/ })
        .getAttribute('aria-expanded')
    ).toBe('true')
    expect(screen.getByText('Monitoring only reads.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Test' })).toBeTruthy()
    expect(solidLabels()).toEqual(['Save changes'])
  })

  it('offers Save anyway after a failed Test', async () => {
    configure.testConnection.mockResolvedValue({
      target: 'orders',
      connected: false,
      error: 'Connection refused',
    })
    renderEdit()

    fireEvent.click(screen.getByRole('button', { name: 'Test' }))
    expect(await screen.findByText('Connection refused')).toBeTruthy()
    expect(solidLabels()).toEqual(['Save anyway'])
  })

  it('tests under the saved name, so the saved password fills a blank one', async () => {
    renderEdit()

    type('Name', 'orders-eu')
    fireEvent.click(screen.getByRole('button', { name: 'Test' }))

    await waitFor(() =>
      expect(configure.testConnection).toHaveBeenCalledWith(
        'orders',
        expect.objectContaining({ name: 'orders-eu', password: undefined })
      )
    )
  })

  it('shows the query statistics notice on Test and keeps Save changes', async () => {
    configure.testConnection.mockResolvedValue({
      target: 'orders',
      connected: true,
      queryCapture: CAPTURE_MISSING,
    })
    renderEdit()

    fireEvent.click(screen.getByRole('button', { name: 'Test' }))

    expect(
      await screen.findByText("Monitoring can't read query statistics yet")
    ).toBeTruthy()
    expect(solidLabels()).toEqual(['Save changes'])
  })

  it('saves a rename with the connection, follows the selection and hands back the name', async () => {
    localStorage.setItem('rdst_selected_target', 'orders')
    const { onSaved } = renderEdit()

    type('Name', 'orders-eu')
    type('Host', 'orders-2.internal')
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('orders-eu'))
    expect(configure.updateTarget).toHaveBeenCalledWith(
      'orders',
      expect.objectContaining({
        name: 'orders-eu',
        engine: 'postgresql',
        host: 'orders-2.internal',
        port: 6432,
        read_only: true,
        tls: true,
        password: undefined,
        ssh: { host: 'bastion.example.com', port: 22, user: 'ec2-user' },
      })
    )
    expect(selectGlobalTarget).toHaveBeenCalledWith('orders-eu')
    expect(configure.testConnection).not.toHaveBeenCalled()
  })

  it('keeps the name when the field is cleared', async () => {
    const { onSaved } = renderEdit()

    type('Name', '')
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('orders'))
    expect(selectGlobalTarget).not.toHaveBeenCalled()
  })

  it('shows why a save failed, once, and stays open', async () => {
    configure.updateTarget.mockRejectedValue(
      new Error('orders is running an analysis or a report.')
    )
    const { onSaved } = renderEdit()

    type('Name', 'orders-eu')
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    expect(
      await screen.findAllByText('orders is running an analysis or a report.')
    ).toHaveLength(1)
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('refuses the name of another database', () => {
    renderEdit()

    type('Name', 'billing')
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    expect(
      screen.getByText('A database named billing already exists.')
    ).toBeTruthy()
    expect(configure.updateTarget).not.toHaveBeenCalled()
  })
})

describe('serverVersion', () => {
  it('keeps the product and version', () => {
    expect(serverVersion('PostgreSQL 16.3 on x86_64, compiled')).toBe(
      'PostgreSQL 16.3'
    )
    expect(serverVersion('MySQL 8.0.36')).toBe('MySQL 8.0.36')
    expect(serverVersion(undefined)).toBeNull()
  })
})

describe('newDatabaseName', () => {
  it('uses the database name, made unique', () => {
    expect(newDatabaseName('shop', [])).toBe('shop')
    expect(newDatabaseName('shop', ['shop', 'shop-2'])).toBe('shop-3')
    expect(newDatabaseName('my db!', [])).toBe('my-db')
    expect(newDatabaseName('', [])).toBe('database')
  })
})

describe('connectionFrom', () => {
  const empty = { host: '', port: '', database: '', user: '', password: '' }

  it('needs host, port, database and user from the fields', () => {
    expect(connectionFrom('', empty)).toBeNull()
    expect(
      connectionFrom('', { ...empty, host: 'h', port: '5432', database: 'd' })
    ).toBeNull()
    expect(
      connectionFrom('', { ...empty, host: 'h', database: 'd', user: 'u' })
        ?.port
    ).toBe(5432)
  })

  it('reads MySQL from its port and the engine from a string', () => {
    const fields = {
      host: 'h',
      port: '3306',
      database: 'd',
      user: 'u',
      password: '',
    }
    expect(connectionFrom('', fields)?.engine).toBe('mysql')
    expect(connectionFrom('mysql://u@h/d', empty)?.engine).toBe('mysql')
    expect(connectionFrom('postgres://u@h/d', empty)?.port).toBe(5432)
  })
})
