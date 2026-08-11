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
import { SettingsPage } from '../features/settings/SettingsPage'
import { setEnvSecret } from '../lib/api'
import { useTrialSource } from '../lib/trialQueries'
import { useConfigure } from '../lib/useConfigure'
import {
  fetchFleetAwsStatus,
  fetchFleetTargets,
  useFleetStatus,
} from '../lib/useFleet'
import { useSystemStatus } from '../lib/useSystemStatus'
import type { FleetConnectivityEvent } from '../types/fleet'
import { Route } from './configure'

const routerSpies = vi.hoisted(() => ({
  navigate: vi.fn(),
  push: vi.fn(),
}))

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
    useNavigate: () => routerSpies.navigate,
    useRouter: () => ({ history: { push: routerSpies.push } }),
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
vi.mock('../lib/useSystemStatus', () => ({ useSystemStatus: vi.fn() }))
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
  AddTargetsDrawer: ({
    open,
    initialTab,
    onClose,
  }: {
    open: boolean
    initialTab: string
    onClose: () => void
  }) =>
    open ? (
      <div data-testid="add-targets-drawer">
        <span>{`Provider: ${initialTab}`}</span>
        <button type="button" onClick={onClose}>
          Close provider drawer
        </button>
      </div>
    ) : null,
  DevSettingsSection: () => null,
}))

// The route declares its component as a split-module loader, which is how the
// router itself reaches the page.
const { component: loadConfigurePage } = Route as unknown as {
  component: () => Promise<{ component: () => ReactElement }>
}

// Resolve during module collection: the split-module transform is the slowest
// step in this suite, and it does not belong to the per-test behavior timeout.
const ConfigurePage = (await loadConfigurePage()).component

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
  const value = {
    listTargets,
    getTarget: vi.fn(),
    addTarget: vi.fn(),
    updateTarget: vi.fn(),
    removeTarget: vi.fn(),
    setDefaultTarget: vi.fn(),
    testConnection: vi.fn(),
    cancel: vi.fn(),
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
  } satisfies ReturnType<typeof useConfigure>
  vi.mocked(useConfigure).mockReturnValue(value)
  return value
}

describe('Settings row password save', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useSystemStatus).mockReturnValue({
      data: undefined,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useSystemStatus>)
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

  it('keeps settings areas mutually exclusive while switching panels', async () => {
    mockUseConfigure(vi.fn().mockResolvedValue(undefined))
    vi.mocked(useFleetStatus).mockReturnValue(
      fleetStatusStub({ state: 'idle' }) as ReturnType<typeof useFleetStatus>
    )
    renderWithClient(<SettingsPage search={{}} />)

    expect(await screen.findByText('Database connections')).toBeTruthy()
    expect(screen.queryByText('AI keys')).toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: 'AI' }))
    expect(await screen.findByText('AI keys')).toBeTruthy()
    expect(screen.queryByText('Database connections')).toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: 'Data & privacy' }))
    expect(
      await screen.findByText('Loading local storage details…')
    ).toBeTruthy()
    expect(screen.queryByText('AI keys')).toBeNull()
  })

  it('opens an edit deep link and clears it when the form is cancelled', async () => {
    const configure = mockUseConfigure(vi.fn().mockResolvedValue(undefined))
    configure.getTarget.mockResolvedValue({
      name: 'orders',
      engine: 'postgresql',
      host: 'orders.test',
      port: 5432,
      database: 'orders',
      user: 'app_ro',
      has_password: true,
      is_default: true,
    })
    vi.mocked(useFleetStatus).mockReturnValue(
      fleetStatusStub({ state: 'idle' }) as ReturnType<typeof useFleetStatus>
    )
    renderWithClient(<SettingsPage search={{ edit: 'orders' }} />)

    await waitFor(() =>
      expect(configure.getTarget).toHaveBeenCalledWith('orders')
    )
    expect(await screen.findByText('Edit connection')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(routerSpies.navigate).toHaveBeenCalledWith({
      to: '/configure',
      search: { panel: 'connections' },
      replace: true,
    })
  })

  it('returns to the owning feature when a connection repair is cancelled', async () => {
    const configure = mockUseConfigure(vi.fn().mockResolvedValue(undefined))
    configure.getTarget.mockResolvedValue({
      name: 'orders',
      engine: 'postgresql',
      host: 'orders.test',
      port: 5432,
      database: 'orders',
      user: 'app_ro',
      has_password: true,
      is_default: true,
    })
    vi.mocked(useFleetStatus).mockReturnValue(
      fleetStatusStub({ state: 'idle' }) as ReturnType<typeof useFleetStatus>
    )
    renderWithClient(
      <SettingsPage search={{ edit: 'orders', returnTo: '/audit' }} />
    )

    expect(await screen.findByText('Edit connection')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(routerSpies.push).toHaveBeenCalledWith('/audit')
    expect(routerSpies.navigate).not.toHaveBeenCalledWith({
      to: '/configure',
      search: { panel: 'connections' },
      replace: true,
    })
  })

  it('opens legacy AI deep links and removes the action state on close', async () => {
    mockUseConfigure(vi.fn().mockResolvedValue(undefined))
    vi.mocked(useFleetStatus).mockReturnValue(
      fleetStatusStub({ state: 'idle' }) as ReturnType<typeof useFleetStatus>
    )
    renderWithClient(<SettingsPage search={{ section: 'ai' }} />)

    expect(await screen.findByText('AI keys')).toBeTruthy()
    expect(
      (await screen.findAllByText(/Anthropic API Key/)).length
    ).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(routerSpies.navigate).toHaveBeenCalledWith({
      to: '/configure',
      search: { panel: 'ai' },
      replace: true,
    })
  })

  it('returns to the owning feature when its AI-key dialog is cancelled', async () => {
    mockUseConfigure(vi.fn().mockResolvedValue(undefined))
    vi.mocked(useFleetStatus).mockReturnValue(
      fleetStatusStub({ state: 'idle' }) as ReturnType<typeof useFleetStatus>
    )
    renderWithClient(
      <SettingsPage search={{ section: 'ai', returnTo: '/ask' }} />
    )

    expect(
      (await screen.findAllByText('Update Anthropic API key')).length
    ).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(routerSpies.push).toHaveBeenCalledWith('/ask')
    expect(routerSpies.navigate).not.toHaveBeenCalledWith({
      to: '/configure',
      search: { panel: 'ai' },
      replace: true,
    })
  })

  it('opens provider deep links on the requested source and cleans the URL on close', async () => {
    mockUseConfigure(vi.fn().mockResolvedValue(undefined))
    vi.mocked(useFleetStatus).mockReturnValue(
      fleetStatusStub({ state: 'idle' }) as ReturnType<typeof useFleetStatus>
    )
    renderWithClient(<SettingsPage search={{ add: 'csv' }} />)

    expect(await screen.findByText('Provider: csv')).toBeTruthy()
    fireEvent.click(
      screen.getByRole('button', { name: 'Close provider drawer' })
    )

    expect(routerSpies.navigate).toHaveBeenCalledWith({
      to: '/configure',
      search: { panel: 'connections' },
      replace: true,
    })
  })

  it('shows a recoverable privacy error instead of an endless loading state', async () => {
    const refetch = vi.fn()
    vi.mocked(useSystemStatus).mockReturnValue({
      data: undefined,
      isError: true,
      refetch,
    } as unknown as ReturnType<typeof useSystemStatus>)
    mockUseConfigure(vi.fn().mockResolvedValue(undefined))
    vi.mocked(useFleetStatus).mockReturnValue(
      fleetStatusStub({ state: 'idle' }) as ReturnType<typeof useFleetStatus>
    )
    renderWithClient(<SettingsPage search={{ panel: 'privacy' }} />)

    expect(
      await screen.findByText("Local storage details couldn't be loaded")
    ).toBeTruthy()
    expect(screen.queryByText('Loading local storage details…')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(refetch).toHaveBeenCalledTimes(1)
  })
})
