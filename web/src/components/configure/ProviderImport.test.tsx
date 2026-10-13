import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fleetStatusStub, renderWithClient } from '@/test-utils'
import { fetchEnvRequirements, setEnvSecret } from '../../lib/api'
import * as csv from '../../lib/csv'
import {
  bulkAddFleetTargets,
  type DiscoveredFleetMember,
  fetchFleetAwsStatus,
  fetchFleetDigitaloceanStatus,
  fetchFleetDiscoverPreview,
  fetchFleetNeonStatus,
  fetchFleetSupabaseStatus,
  fetchFleetTargets,
  useFleetImport,
} from '../../lib/useFleet'
import { type DatabasesChange, onDatabasesChanged } from './addDatabase'
import { ProviderImport } from './ProviderImport'

vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-router')>()),
  useNavigate: () => vi.fn(),
}))
vi.mock('../../lib/api', () => ({
  fetchEnvRequirements: vi.fn(),
  setEnvSecret: vi.fn(),
}))
vi.mock('../../lib/useFleet', () => ({
  bulkAddFleetTargets: vi.fn(),
  fetchFleetAwsStatus: vi.fn(),
  fetchFleetDigitaloceanStatus: vi.fn(),
  fetchFleetDiscoverPreview: vi.fn(),
  fetchFleetNeonStatus: vi.fn(),
  fetchFleetSupabaseStatus: vi.fn(),
  fetchFleetTargets: vi.fn(),
  useFleetImport: vi.fn(),
  updateFleetTargetCredentials: vi.fn(),
  useFleetStatus: vi.fn(() => fleetStatusStub({ state: 'idle' })),
}))
// The provider panels own their own credential polling; the import only hosts them.
vi.mock('../aws/AwsConnectionPanel', () => ({
  AwsConnectionPanel: () => <div data-testid="aws-connection-panel" />,
}))
vi.mock('../supabase/SupabaseConnectionPanel', () => ({
  SupabaseConnectionPanel: () => (
    <div data-testid="supabase-connection-panel" />
  ),
}))
vi.mock('../neon/NeonConnectionPanel', () => ({
  NeonConnectionPanel: () => <div data-testid="neon-connection-panel" />,
}))
vi.mock('../digitalocean/DigitalOceanConnectionPanel', () => ({
  DigitalOceanConnectionPanel: () => (
    <div data-testid="digitalocean-connection-panel" />
  ),
}))

const discovered: DiscoveredFleetMember[] = [
  {
    name: 'orders-writer',
    engine: 'postgresql',
    host: 'orders.test',
    port: 5432,
    database: 'orders',
    user: 'master',
    password_env: 'RDST_ORDERS_PASSWORD',
    group: 'orders-cluster',
    tags: ['role:writer'],
    instance_class: 'db.r6g.large',
    region: 'us-east-1',
    already_exists: false,
  },
  {
    name: 'orders-reader',
    engine: 'postgresql',
    host: 'orders-reader.test',
    port: 5432,
    database: 'orders',
    user: 'master',
    password_env: 'RDST_ORDERS_PASSWORD',
    group: 'orders-cluster',
    tags: ['role:reader'],
    instance_class: 'db.r6g.large',
    region: 'us-east-1',
    already_exists: true,
  },
]

let changes: DatabasesChange[] = []
let unsubscribe: () => void = () => {}

function renderImport(
  props: Partial<Parameters<typeof ProviderImport>[0]> = {}
) {
  const onClose = vi.fn()
  const { client } = renderWithClient(
    <ProviderImport initialTab="aws" onClose={onClose} {...props} />
  )
  return { client, onClose }
}

const discoverEnabled = () =>
  waitFor(() =>
    expect(
      (screen.getByRole('button', { name: 'Discover' }) as HTMLButtonElement)
        .disabled
    ).toBe(false)
  )

async function addDiscoveredWriter() {
  await discoverEnabled()
  fireEvent.click(screen.getByRole('button', { name: 'Discover' }))
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Add 1 selected' })).toBeTruthy()
  )
  fireEvent.click(screen.getByRole('button', { name: 'Add 1 selected' }))
  await waitFor(() =>
    expect(screen.getByTestId('credentials-step')).toBeTruthy()
  )
}

describe('ProviderImport', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    changes = []
    unsubscribe = onDatabasesChanged((change) => changes.push(change))
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      telemetry_enabled: true,
      requirements: [],
    })
    vi.mocked(fetchFleetTargets).mockResolvedValue({
      members: [],
      groups: [],
      count: 0,
    })
    // AWS discovery is gated on a signed-in account; default to signed in.
    vi.mocked(fetchFleetAwsStatus).mockResolvedValue({
      has_credentials: true,
      method: 'sso',
      identity_arn: 'arn:aws:sts::1:assumed-role/dev/mike',
      account: '1',
      active_profile: 'dev',
      available_profiles: ['dev'],
      region: 'us-east-1',
    })
    vi.mocked(fetchFleetDiscoverPreview).mockResolvedValue({
      members: discovered,
      errors: [],
    })
    vi.mocked(bulkAddFleetTargets).mockResolvedValue({
      imported: 1,
      skipped: 0,
      target_names: ['orders-writer'],
    })
    vi.mocked(setEnvSecret).mockImplementation(async ({ name }) => ({
      success: true,
      name,
      persisted: true,
      session_only: false,
      message: null,
    }))
    vi.mocked(useFleetImport).mockReturnValue({
      runImport: vi.fn(),
      state: 'idle',
      progress: [],
      result: undefined,
      errors: [],
      reset: vi.fn(),
    })
  })

  afterEach(() => {
    unsubscribe()
    cleanup()
  })

  it('opens on the requested source', () => {
    renderImport({ initialTab: 'csv' })

    expect(
      screen.getByText(/Include name, host, and engine in each row/)
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Choose CSV file' })).toBeTruthy()
    expect(
      (screen.getByRole('button', { name: 'Import' }) as HTMLButtonElement)
        .disabled
    ).toBe(true)
    const download = vi.spyOn(csv, 'downloadCsv').mockImplementation(() => {})
    fireEvent.click(screen.getByRole('button', { name: 'Download template' }))
    expect(download).toHaveBeenCalledWith(
      expect.stringMatching(/^name,host,port,database,user,engine\n/),
      'rdst-databases.csv'
    )
  })

  it.each([
    [{ skipped: 0, errors: 0 }, false],
    [{ skipped: 1, errors: 1 }, true],
  ])('a CSV import with %o left out stays on its summary: %s', async (left, stays) => {
    const completion = {
      type: 'import_complete' as const,
      success: true,
      imported: 1,
      target_names: ['orders'],
      ...left,
    }
    vi.mocked(useFleetImport).mockReturnValue({
      runImport: vi.fn().mockResolvedValue(completion),
      state: 'complete',
      progress: [],
      result: completion,
      errors: stays ? ['Row 3 (Orders): Same name as row 2'] : [],
      reset: vi.fn(),
    })
    const { container } = renderWithClient(
      <ProviderImport initialTab="csv" onClose={vi.fn()} />
    )
    const input = container.querySelector(
      'input[type="file"]'
    ) as HTMLInputElement
    // jsdom's File has no text(); the import only reads the name and the text.
    const file = { name: 'fleet.csv', text: async () => 'name,host,engine\n' }
    fireEvent.change(input, { target: { files: [file] } })
    await waitFor(() => expect(screen.getByText('fleet.csv')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Import' }))

    if (!stays) {
      expect(await screen.findByTestId('credentials-step')).toBeTruthy()
      return
    }
    const next = await screen.findByRole('button', { name: 'Continue' })
    expect(screen.queryByTestId('credentials-step')).toBeNull()
    expect(screen.getByText('Row 3 (Orders): Same name as row 2')).toBeTruthy()
    expect(screen.getByText('1 imported, 1 skipped, 1 errors')).toBeTruthy()
    fireEvent.click(next)
    expect(await screen.findByTestId('credentials-step')).toBeTruthy()
  })

  it('previews discovered databases and adds only the new checked ones', async () => {
    const onStepChange = vi.fn()
    renderImport({ initialTab: 'aws', onStepChange })

    await discoverEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))

    await waitFor(() =>
      expect(screen.getByText('Choose which databases to add.')).toBeTruthy()
    )
    // Already-added databases are not selectable and never travel in the
    // bulk-add body.
    expect(screen.queryByLabelText('Select orders-reader')).toBeNull()
    expect(screen.getByText(/PostgreSQL at orders.test:5432\/orders, writer/))
    // The footer holds one way back to the regions and one solid.
    expect(screen.getByRole('button', { name: 'Back to regions' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Discover' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Add 1 selected' }))

    await waitFor(() =>
      expect(bulkAddFleetTargets).toHaveBeenCalledWith([discovered[0]])
    )
    // Pages that list databases hold their rows locally, so they hear it.
    await waitFor(() => expect(changes.length).toBeGreaterThan(0))
    await waitFor(() =>
      expect(screen.getByTestId('credentials-step')).toBeTruthy()
    )
    expect(onStepChange).toHaveBeenCalledWith('credentials')
  })

  it('marks selected regions as pressed and sends exactly those', async () => {
    renderImport({ initialTab: 'aws' })
    const east2 = await screen.findByRole('button', { name: 'us-east-2' })
    expect(east2.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(east2)
    expect(
      screen
        .getByRole('button', { name: 'us-east-2' })
        .getAttribute('aria-pressed')
    ).toBe('false')
    await discoverEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))
    await waitFor(() => expect(fetchFleetDiscoverPreview).toHaveBeenCalled())
    const sent = vi
      .mocked(fetchFleetDiscoverPreview)
      .mock.calls.at(-1)?.[0] as {
      regions: string[]
    }
    expect(sent.regions).not.toContain('us-east-2')
    expect(sent.regions).toContain('us-east-1')
  })

  it('keeps Aurora readers out of the import until cluster monitoring', async () => {
    vi.mocked(fetchFleetDiscoverPreview).mockResolvedValueOnce({
      members: [discovered[0], { ...discovered[1], already_exists: false }],
      errors: [],
    } as never)
    renderImport({ initialTab: 'aws' })
    await discoverEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))
    await waitFor(() =>
      expect(screen.getByText(/readers can't be added yet/)).toBeTruthy()
    )
    expect(
      screen.getByLabelText('Select orders-writer').getAttribute('aria-checked')
    ).toBe('true')
    // No checkbox at all: a reader can't be added.
    expect(screen.queryByLabelText('Select orders-reader')).toBeNull()
    expect(screen.queryByText(/coming soon/i)).toBeNull()
  })

  it('supports global and per-group selection and shows searched regions', async () => {
    renderImport({ initialTab: 'aws' })
    await discoverEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))
    await waitFor(() =>
      expect(screen.getByLabelText('Select orders-writer')).toBeTruthy()
    )

    expect(screen.getAllByText(/us-east-1$/)).toHaveLength(2)
    fireEvent.click(
      screen.getAllByRole('button', { name: 'Clear selection' })[0]
    )
    expect(
      screen.getByLabelText('Select orders-writer').getAttribute('aria-checked')
    ).toBe('false')

    fireEvent.click(screen.getAllByRole('button', { name: 'Select all' })[1])
    expect(
      screen.getByLabelText('Select orders-writer').getAttribute('aria-checked')
    ).toBe('true')
    fireEvent.click(
      screen.getAllByRole('button', { name: 'Clear selection' })[0]
    )
    expect(
      screen.getByLabelText('Select orders-writer').getAttribute('aria-checked')
    ).toBe('false')
  })

  it.each([
    [
      'supabase',
      () =>
        vi.mocked(fetchFleetSupabaseStatus).mockResolvedValue({
          connected: true,
          method: 'oauth',
          detail: null,
          organizations: [{ slug: 'acme', name: 'Acme' }],
        }),
    ],
    [
      'neon',
      () =>
        vi.mocked(fetchFleetNeonStatus).mockResolvedValue({
          connected: true,
          method: 'api_key',
          detail: null,
        }),
    ],
    [
      'digitalocean',
      () =>
        vi.mocked(fetchFleetDigitaloceanStatus).mockResolvedValue({
          connected: true,
          method: 'oauth',
          detail: null,
        }),
    ],
  ] as const)('discovers %s databases once the account is connected', async (provider, connect) => {
    connect()
    renderImport({ initialTab: provider })

    expect(screen.queryByText('Regions')).toBeNull()
    // A connected account discovers without a click.
    await waitFor(() =>
      expect(fetchFleetDiscoverPreview).toHaveBeenCalledWith({ provider })
    )
    expect(screen.getByText('Choose which databases to add.')).toBeTruthy()
    // An account has no regions to go back to.
    expect(screen.queryByRole('button', { name: /^Back/ })).toBeNull()
  })

  it('hides Neon discovery until a key is stored', async () => {
    vi.mocked(fetchFleetNeonStatus).mockResolvedValue({
      connected: false,
      method: null,
      detail: null,
    })
    renderImport({ initialTab: 'neon' })

    expect(screen.getByTestId('neon-connection-panel')).toBeTruthy()
    await waitFor(() => expect(fetchFleetNeonStatus).toHaveBeenCalled())
    expect(
      await screen.findByText('Connect a Neon API key to discover databases.')
    ).toBeTruthy()
    // Neon databases are public: no jump-host hint.
    expect(screen.queryByText(/SSH jump/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Discover' })).toBeNull()
    expect(fetchFleetDiscoverPreview).not.toHaveBeenCalled()
  })

  it('hides AWS discovery until the account is signed in', async () => {
    vi.mocked(fetchFleetAwsStatus).mockResolvedValue({
      has_credentials: false,
      method: null,
      identity_arn: null,
      account: null,
      active_profile: null,
      available_profiles: ['dev'],
      region: null,
    })
    renderImport({ initialTab: 'aws' })

    await waitFor(() => expect(fetchFleetAwsStatus).toHaveBeenCalled())
    expect(
      await screen.findByText(
        'Sign in with AWS SSO to discover databases, or go back and paste a connection string.'
      )
    ).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Discover' })).toBeNull()
    expect(fetchFleetDiscoverPreview).not.toHaveBeenCalled()
  })

  it('surfaces a discovery failure without leaving the regions step', async () => {
    vi.mocked(fetchFleetDiscoverPreview).mockRejectedValue(
      new Error('No AWS credentials')
    )
    renderImport({ initialTab: 'aws' })

    await discoverEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))

    expect(await screen.findAllByText('No AWS credentials')).not.toHaveLength(0)
    expect(screen.getByRole('button', { name: 'Discover' })).toBeTruthy()
  })

  it('republishes the databases as soon as credentials are saved', async () => {
    vi.mocked(fetchFleetTargets).mockResolvedValue({
      members: [
        {
          name: 'orders-writer',
          engine: 'postgresql',
          host: 'orders.test',
          port: 5432,
          database: 'orders',
          user: 'master',
          password_env: 'RDST_ORDERS_PASSWORD',
          has_password: false,
        },
      ],
      groups: [],
      count: 1,
    })
    const { client } = renderImport({ initialTab: 'aws' })
    await addDiscoveredWriter()

    // Only what the save itself publishes counts; the add already refreshed.
    changes = []
    const invalidate = vi.spyOn(client, 'invalidateQueries')

    fireEvent.change(
      document.querySelector(
        'input[name="credentials-password-orders-writer"]'
      ) as HTMLInputElement,
      { target: { value: 'orders-secret' } }
    )
    fireEvent.click(screen.getByRole('button', { name: /Save and check/ }))

    await waitFor(() =>
      expect(changes).toContainEqual({ recheck: ['orders-writer'] })
    )
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['fleet-targets'] })
  })

  it('asks for a fresh connectivity sweep when the credentials step is left', async () => {
    const { onClose } = renderImport({ initialTab: 'aws' })
    await addDiscoveredWriter()

    fireEvent.click(screen.getByRole('button', { name: 'Set up later' }))

    expect(onClose).toHaveBeenCalled()
    await waitFor(() => expect(changes).toContainEqual({ recheckAll: true }))
  })
})
