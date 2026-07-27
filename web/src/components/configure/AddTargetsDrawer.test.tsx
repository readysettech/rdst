import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fleetStatusStub, renderWithClient } from '@/test-utils'
import { fetchEnvRequirements, setEnvSecret } from '../../lib/api'
import {
  bulkAddFleetTargets,
  type DiscoveredFleetMember,
  fetchFleetDiscoverPreview,
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
  fetchFleetDiscoverPreview: vi.fn(),
  fetchFleetTargets: vi.fn(),
  useFleetImport: vi.fn(),
  updateFleetTargetCredentials: vi.fn(),
  useFleetStatus: vi.fn(() => fleetStatusStub({ state: 'idle' })),
}))
// The AWS panel owns its own credential polling; the drawer only hosts it.
vi.mock('../aws/AwsConnectionPanel', () => ({
  AwsConnectionPanel: () => <div data-testid="aws-connection-panel" />,
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

  it('opens on the requested source tab', () => {
    renderDrawer({ initialTab: 'csv' })

    expect(screen.getByText(/columns: name, host, engine/)).toBeTruthy()
  })

  it('previews discovered instances and adds only the new checked ones', async () => {
    const { onTargetsAdded } = renderDrawer({ initialTab: 'aws' })

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

  it('surfaces a discovery failure without leaving the regions step', async () => {
    vi.mocked(fetchFleetDiscoverPreview).mockRejectedValue(
      new Error('No AWS credentials')
    )
    renderDrawer({ initialTab: 'aws' })

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
