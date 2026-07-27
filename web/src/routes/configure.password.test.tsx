import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createTestQueryClient,
  fileRouteModuleMock,
  fleetStatusStub,
  makeTarget,
  renderWithClient,
} from '@/test-utils'
import { setEnvSecret } from '../lib/api'
import { useTrialSource } from '../lib/trialQueries'
import { useConfigure } from '../lib/useConfigure'
import {
  fetchFleetAwsStatus,
  fetchFleetTargets,
  useFleetStatus,
} from '../lib/useFleet'
import type { FleetConnectivityEvent } from '../types/fleet'
import { Route } from './configure'

vi.mock('@tanstack/react-router', () =>
  fileRouteModuleMock({
    createFileRoute: () => (options: Record<string, unknown>) => ({
      ...options,
      useSearch: () => ({}),
    }),
    useLocation: ({
      select,
    }: {
      select: (location: { hash: string }) => unknown
    }) => select({ hash: '' }),
    useNavigate: () => vi.fn(),
    useRouter: () => ({ history: { push: vi.fn() } }),
  })
)
vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  setEnvSecret: vi.fn(),
}))
vi.mock('../lib/useConfigure', () => ({ useConfigure: vi.fn() }))
vi.mock('../lib/useFleet', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/useFleet')>()),
  fetchFleetAwsStatus: vi.fn(),
  fetchFleetTargets: vi.fn(),
  useFleetStatus: vi.fn(),
}))
vi.mock('../lib/trialQueries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/trialQueries')>()),
  useTrialSource: vi.fn(),
}))
vi.mock('../lib/useSystemStatus', () => ({
  useSystemStatus: () => ({ data: undefined }),
}))
vi.mock('../lib/useAnthropicValidity', () => ({
  useAnthropicValidity: () => ({
    data: undefined,
    isFetching: false,
    refetch: vi.fn(),
  }),
}))
vi.mock('../components/aws/AwsConnectionPanel', () => ({
  AwsConnectionPanel: () => <div data-testid="aws-connection-panel" />,
}))
// The drawer and the dev tools own their own polling and are not part of the
// row's password path; the connection list itself renders for real.
vi.mock('../components/configure', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../components/configure')>()),
  AddTargetsDrawer: () => null,
  DevSettingsSection: () => null,
}))

// The route declares its component as a split-module loader, which is how the
// router itself reaches the page.
const { component: loadConfigurePage } = Route as unknown as {
  component: () => Promise<{ component: () => ReactElement }>
}

// Resolved at module scope: the split-module transform is the slowest step in
// this suite, and it does not depend on any per-test mock.
const configurePage = loadConfigurePage()

const passwordFailure: FleetConnectivityEvent = {
  type: 'connectivity',
  target_name: 'orders',
  status: 'failed',
  error: 'password authentication failed for user "app_ro"',
  code: 'TARGET_PASSWORD_REQUIRED',
  latency_ms: null,
  server_version: null,
  password_env: 'RDST_ORDERS_PASSWORD',
}

function mockUseConfigure(listTargets: () => Promise<void>) {
  vi.mocked(useConfigure).mockReturnValue({
    listTargets,
    getTarget: vi.fn(),
    addTarget: vi.fn(),
    updateTarget: vi.fn(),
    removeTarget: vi.fn(),
    setDefaultTarget: vi.fn(),
    testConnection: vi.fn(),
    state: 'success',
    targets: [
      {
        name: 'orders',
        engine: 'postgresql',
        host: 'orders.test',
        port: 5432,
        database: 'orders',
        has_password: false,
        is_default: true,
      },
    ],
    defaultTarget: 'orders',
    connectionTestResult: null,
    error: null,
    loading: false,
  })
}

describe('Settings row password save', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchFleetTargets).mockResolvedValue({
      members: [makeTarget({ name: 'orders', user: 'app_ro' })],
      groups: [],
      count: 1,
    })
    vi.mocked(fetchFleetAwsStatus).mockResolvedValue({
      has_credentials: false,
      method: null,
      identity_arn: null,
      account: null,
      active_profile: null,
      available_profiles: [],
      region: null,
    })
    vi.mocked(useTrialSource).mockReturnValue({
      envRequirements: {
        keyring_available: true,
        requirements: [
          {
            kind: 'target_password',
            accepted_names: ['RDST_ORDERS_PASSWORD'],
            target: 'orders',
            satisfied: false,
            source: 'missing',
          },
        ],
      },
      anthropicRequirement: undefined,
      anthropicSource: undefined,
      isTrialSource: false,
      trialStatus: undefined,
    })
    vi.mocked(setEnvSecret).mockResolvedValue({
      success: true,
      name: 'RDST_ORDERS_PASSWORD',
      persisted: true,
      session_only: false,
    })
  })

  afterEach(cleanup)

  it('republishes the row and re-checks only that target', async () => {
    // The row reads "Password needed" from `has_password` on a target list the
    // dialog does not own, so a saved password only clears it if the page
    // reloads that list and re-checks the row.
    const check = vi.fn().mockResolvedValue(undefined)
    const listTargets = vi.fn().mockResolvedValue(undefined)
    mockUseConfigure(listTargets)
    vi.mocked(useFleetStatus).mockReturnValue(
      fleetStatusStub({
        check,
        state: 'idle',
        results: { orders: passwordFailure },
      }) as ReturnType<typeof useFleetStatus>
    )
    const client = createTestQueryClient()
    const ConfigurePage = (await configurePage).component

    renderWithClient(<ConfigurePage />, client)

    const needed = await screen.findAllByText('Password needed')
    expect(needed.length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: /Set password/ }))

    // Only what the save publishes counts; the mount already listed and swept.
    listTargets.mockClear()
    check.mockClear()
    const invalidate = vi.spyOn(client, 'invalidateQueries')

    fireEvent.change(
      await screen.findByPlaceholderText('Enter Password (orders)'),
      { target: { value: 'orders-secret' } }
    )
    fireEvent.click(screen.getByRole('button', { name: /Save Secrets/ }))

    await waitFor(() =>
      expect(check).toHaveBeenCalledWith(undefined, ['orders'])
    )
    expect(listTargets).toHaveBeenCalled()
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['fleet-targets'] })
  })
})
