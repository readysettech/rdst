import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fleetStatusStub, renderWithClient } from '@/test-utils'
import { fetchEnvRequirements, setEnvSecret } from '../../lib/api'
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
import { AddTargetsDrawer } from './AddTargetsDrawer'

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
// The provider panels own their own credential polling; the drawer only hosts
// them.
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

function renderDrawer(
  props: Partial<Parameters<typeof AddTargetsDrawer>[0]> = {}
) {
  const onTargetsAdded = vi.fn()
  const onCredentialsClosed = vi.fn()
  const onRecheckTargets = vi.fn()
  const onClose = vi.fn()
  const { client } = renderWithClient(
    <AddTargetsDrawer
      open
      onClose={onClose}
      onTargetsAdded={onTargetsAdded}
      onCredentialsClosed={onCredentialsClosed}
      onRecheckTargets={onRecheckTargets}
      {...props}
    />
  )
  return {
    client,
    onTargetsAdded,
    onCredentialsClosed,
    onRecheckTargets,
    onClose,
  }
}

describe('AddTargetsDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      requirements: [],
    })
    vi.mocked(fetchFleetTargets).mockResolvedValue({
      members: [],
      groups: [],
      count: 0,
    })
    // AWS Discovery is gated on a signed-in account; default the drawer's
    // shared status query to signed-in so the AWS tab can discover.
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

  afterEach(cleanup)

  it('starts with one integration picker and switches to manual setup', () => {
    renderDrawer({
      manualContent: <div>Manual connection form</div>,
    })

    expect(screen.getByText('Choose an integration')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'AWS' })).toBeTruthy()
    expect(screen.queryByText('Manual connection form')).toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: 'Manual setup' }))

    expect(screen.getByText('Manual connection form')).toBeTruthy()
    expect(screen.queryByText('Choose an integration')).toBeNull()
  })

  it('opens on the requested source tab', () => {
    renderDrawer({ initialTab: 'csv' })

    expect(screen.getByText(/columns: name, host, engine/)).toBeTruthy()
  })

  it('previews discovered instances and adds only the new checked ones', async () => {
    const { onTargetsAdded } = renderDrawer({ initialTab: 'aws' })

    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: 'Discover' }) as HTMLButtonElement)
          .disabled
      ).toBe(false)
    )
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))

    await waitFor(() =>
      expect(
        screen.getByText('Choose which databases to add to your fleet.')
      ).toBeTruthy()
    )
    // Already-imported instances are not selectable and never travel in the
    // bulk-add body.
    expect(screen.queryByLabelText('Select orders-reader')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Add 1 selected' }))

    await waitFor(() =>
      expect(bulkAddFleetTargets).toHaveBeenCalledWith([discovered[0]])
    )
    // The page's target rows are local state, so the drawer has to ask for the
    // refresh itself.
    await waitFor(() => expect(onTargetsAdded).toHaveBeenCalled())
    await waitFor(() =>
      expect(screen.getByTestId('credentials-step')).toBeTruthy()
    )
  })

  it('supports global and per-group selection and shows searched regions', async () => {
    renderDrawer({ initialTab: 'aws' })
    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: 'Discover' }) as HTMLButtonElement)
          .disabled
      ).toBe(false)
    )
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))
    await waitFor(() =>
      expect(screen.getByLabelText('Select orders-writer')).toBeTruthy()
    )

    expect(screen.getAllByText('us-east-1')).toHaveLength(2)
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

  it('discovers Supabase projects without a region picker', async () => {
    vi.mocked(fetchFleetSupabaseStatus).mockResolvedValue({
      connected: true,
      method: 'oauth',
      detail: null,
      organizations: [{ slug: 'acme', name: 'Acme' }],
    })
    renderDrawer({ initialTab: 'supabase' })

    expect(screen.queryByText('Regions')).toBeNull()
    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: 'Discover' }) as HTMLButtonElement)
          .disabled
      ).toBe(false)
    )
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))

    await waitFor(() =>
      expect(fetchFleetDiscoverPreview).toHaveBeenCalledWith({
        provider: 'supabase',
      })
    )
    expect(
      screen.getByText('Choose which databases to add to your fleet.')
    ).toBeTruthy()
  })

  it('discovers Neon projects once the API key is connected', async () => {
    vi.mocked(fetchFleetNeonStatus).mockResolvedValue({
      connected: true,
      method: 'api_key',
      detail: null,
    })
    renderDrawer({ initialTab: 'neon' })

    expect(screen.queryByText('Regions')).toBeNull()
    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: 'Discover' }) as HTMLButtonElement)
          .disabled
      ).toBe(false)
    )
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))

    await waitFor(() =>
      expect(fetchFleetDiscoverPreview).toHaveBeenCalledWith({
        provider: 'neon',
      })
    )
    expect(
      screen.getByText('Choose which databases to add to your fleet.')
    ).toBeTruthy()
  })

  it('discovers DigitalOcean databases once the account is signed in', async () => {
    vi.mocked(fetchFleetDigitaloceanStatus).mockResolvedValue({
      connected: true,
      method: 'oauth',
      detail: null,
    })
    renderDrawer({ initialTab: 'digitalocean' })

    expect(screen.queryByText('Regions')).toBeNull()
    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: 'Discover' }) as HTMLButtonElement)
          .disabled
      ).toBe(false)
    )
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))

    await waitFor(() =>
      expect(fetchFleetDiscoverPreview).toHaveBeenCalledWith({
        provider: 'digitalocean',
      })
    )
    expect(
      screen.getByText('Choose which databases to add to your fleet.')
    ).toBeTruthy()
  })

  it('keeps Neon discovery disarmed until a key is stored', async () => {
    vi.mocked(fetchFleetNeonStatus).mockResolvedValue({
      connected: false,
      method: null,
      detail: null,
    })
    renderDrawer({ initialTab: 'neon' })

    expect(screen.getByTestId('neon-connection-panel')).toBeTruthy()
    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: 'Discover' }) as HTMLButtonElement)
          .disabled
      ).toBe(true)
    )
    expect(fetchFleetDiscoverPreview).not.toHaveBeenCalled()
  })

  it('keeps AWS discovery disarmed until the account is signed in', async () => {
    vi.mocked(fetchFleetAwsStatus).mockResolvedValue({
      has_credentials: false,
      method: null,
      identity_arn: null,
      account: null,
      active_profile: null,
      available_profiles: ['dev'],
      region: null,
    })
    renderDrawer({ initialTab: 'aws' })

    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: 'Discover' }) as HTMLButtonElement)
          .disabled
      ).toBe(true)
    )
    expect(
      screen.getByText('Sign in with AWS to discover instances')
    ).toBeTruthy()
    expect(fetchFleetDiscoverPreview).not.toHaveBeenCalled()
  })

  it('surfaces a discovery failure without leaving the regions step', async () => {
    vi.mocked(fetchFleetDiscoverPreview).mockRejectedValue(
      new Error('No AWS credentials')
    )
    renderDrawer({ initialTab: 'aws' })

    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: 'Discover' }) as HTMLButtonElement)
          .disabled
      ).toBe(false)
    )
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))

    await waitFor(() =>
      expect(screen.getByText('No AWS credentials')).toBeTruthy()
    )
    expect(screen.getByRole('button', { name: 'Discover' })).toBeTruthy()
  })

  it('republishes the target rows as soon as credentials are saved', async () => {
    // The page holds its rows and its connectivity outside this drawer, so a
    // saved password only clears "Password needed" if the drawer says so.
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
    const { client, onTargetsAdded, onRecheckTargets } = renderDrawer({
      initialTab: 'aws',
    })

    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: 'Discover' }) as HTMLButtonElement)
          .disabled
      ).toBe(false)
    )
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Add 1 selected' })
      ).toBeTruthy()
    )
    fireEvent.click(screen.getByRole('button', { name: 'Add 1 selected' }))
    await waitFor(() =>
      expect(screen.getByTestId('credentials-step')).toBeTruthy()
    )

    // Only what the save itself publishes counts; the add already refreshed.
    onTargetsAdded.mockClear()
    const invalidate = vi.spyOn(client, 'invalidateQueries')

    // The drawer renders into a portal, so the inputs live outside `container`.
    fireEvent.change(
      document.querySelector(
        'input[name="credentials-password-orders-writer"]'
      ) as HTMLInputElement,
      { target: { value: 'orders-secret' } }
    )
    fireEvent.click(screen.getByRole('button', { name: /Save and check/ }))

    await waitFor(() =>
      expect(onRecheckTargets).toHaveBeenCalledWith(['orders-writer'])
    )
    expect(onTargetsAdded).toHaveBeenCalled()
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['fleet-targets'] })
  })

  it('asks for a fresh connectivity sweep when the credentials step is left', async () => {
    const { onCredentialsClosed, onClose } = renderDrawer({ initialTab: 'aws' })

    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: 'Discover' }) as HTMLButtonElement)
          .disabled
      ).toBe(false)
    )
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Add 1 selected' })
      ).toBeTruthy()
    )
    fireEvent.click(screen.getByRole('button', { name: 'Add 1 selected' }))
    await waitFor(() =>
      expect(screen.getByTestId('credentials-step')).toBeTruthy()
    )

    fireEvent.click(screen.getByRole('button', { name: 'Set up later' }))

    expect(onClose).toHaveBeenCalled()
    expect(onCredentialsClosed).toHaveBeenCalled()
  })
})
