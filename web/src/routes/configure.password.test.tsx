import {
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
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
import { fetchAccountStatus, setAiProvider, setEnvSecret } from '../lib/api'
import { useTrialSource } from '../lib/trialQueries'
import { testTunnel } from '../lib/tunnels'
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
  fetchAccountStatus: vi.fn(),
  setAiProvider: vi.fn(),
  setEnvSecret: vi.fn(),
}))
vi.mock('../lib/useConfigure', () => ({ useConfigure: vi.fn() }))
vi.mock('../lib/tunnels', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/tunnels')>()),
  fetchTunnelStatuses: vi.fn(async () => []),
  testTunnel: vi.fn(),
}))
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
        telemetry_enabled: true,
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
      envRequirementsQuery: {} as never,
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
    vi.mocked(setAiProvider).mockImplementation(async (provider) => ({
      success: true,
      provider,
    }))
    vi.mocked(fetchAccountStatus).mockResolvedValue({
      signed_in: true,
      email: 'demo@example.com',
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
    fireEvent.click(screen.getByRole('button', { name: /Save secrets/ }))

    await waitFor(() =>
      expect(check).toHaveBeenCalledWith(undefined, ['orders'])
    )
    expect(listTargets).toHaveBeenCalled()
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['fleet-targets'] })
  })

  it('says what each key covers, and the AI monitoring count, under AI', async () => {
    mockUseConfigure(vi.fn().mockResolvedValue(undefined))
    vi.mocked(useFleetStatus).mockReturnValue(
      fleetStatusStub({ state: 'idle' }) as ReturnType<typeof useFleetStatus>
    )
    renderWithClient(<SettingsPage search={{}} />)
    const routing = await screen.findByTestId('ai-key-routing')
    expect(within(routing).getByText('What each key covers')).toBeTruthy()
    expect(routing.closest('#ai')).toBeTruthy()
  })

  it('lays every group out on one page, with no tabs and no solid button', async () => {
    mockUseConfigure(vi.fn().mockResolvedValue(undefined))
    vi.mocked(useFleetStatus).mockReturnValue(
      fleetStatusStub({ state: 'idle' }) as ReturnType<typeof useFleetStatus>
    )
    renderWithClient(<SettingsPage search={{}} />)

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Settings' })
    ).toBeTruthy()
    for (const group of [
      'Account',
      'AI',
      'Notifications',
      'Appearance',
      'Data',
      'About',
      'Databases',
    ])
      expect(
        screen.getByRole('heading', { level: 2, name: group })
      ).toBeTruthy()
    expect(screen.queryByRole('tab')).toBeNull()
    // Dark is the one theme RDST draws; Light and Font size wait until they apply.
    expect(screen.getByText('Light colors are coming.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^Theme/ })).toBeNull()
    expect(screen.queryByText('Font size')).toBeNull()
    expect(
      screen
        .getAllByRole('button')
        .filter((button) =>
          button.className.includes('bg-surface-primary-solid')
        )
    ).toEqual([])
  })

  it('lands silently when a retired route sent the reader here', async () => {
    mockUseConfigure(vi.fn().mockResolvedValue(undefined))
    vi.mocked(useFleetStatus).mockReturnValue(
      fleetStatusStub({ state: 'idle' }) as ReturnType<typeof useFleetStatus>
    )
    renderWithClient(<SettingsPage search={{ from: 'fleet' }} />)

    expect(
      await screen.findByRole('heading', { name: 'Databases' })
    ).toBeTruthy()
    expect(screen.queryByText('Fleet moved into Settings')).toBeNull()
  })

  it('switches from an Anthropic key to an existing Readyset account', async () => {
    mockUseConfigure(vi.fn().mockResolvedValue(undefined))
    vi.mocked(useFleetStatus).mockReturnValue(
      fleetStatusStub({ state: 'idle' }) as ReturnType<typeof useFleetStatus>
    )
    vi.mocked(useTrialSource).mockReturnValue({
      envRequirements: {
        keyring_available: true,
        telemetry_enabled: true,
        requirements: [],
      },
      envRequirementsQuery: {} as never,
      anthropicRequirement: {
        kind: 'anthropic_api_key',
        accepted_names: ['ANTHROPIC_API_KEY'],
        target: null,
        satisfied: true,
        source: 'secure_store',
        selected_provider: 'claude',
        anthropic_key_configured: true,
        readyset_account_connected: true,
      },
      anthropicSource: 'secure_store',
      isTrialSource: false,
      trialStatus: undefined,
    })

    renderWithClient(<SettingsPage search={{ panel: 'ai' }} />)

    const hosted = await screen.findByRole('switch', {
      name: 'Hosted by Readyset',
    })
    expect(hosted.getAttribute('aria-checked')).toBe('false')
    fireEvent.click(hosted)

    await waitFor(() =>
      expect(vi.mocked(setAiProvider).mock.calls[0]?.[0]).toBe('readyset')
    )
  })

  it('switches from the included AI back to a saved Anthropic key', async () => {
    mockUseConfigure(vi.fn().mockResolvedValue(undefined))
    vi.mocked(useFleetStatus).mockReturnValue(
      fleetStatusStub({ state: 'idle' }) as ReturnType<typeof useFleetStatus>
    )
    vi.mocked(useTrialSource).mockReturnValue({
      envRequirements: {
        keyring_available: true,
        telemetry_enabled: true,
        requirements: [],
      },
      envRequirementsQuery: {} as never,
      anthropicRequirement: {
        kind: 'anthropic_api_key',
        accepted_names: ['ANTHROPIC_API_KEY'],
        target: null,
        satisfied: true,
        source: 'readyset_account',
        selected_provider: 'readyset',
        anthropic_key_configured: true,
        readyset_account_connected: true,
      },
      anthropicSource: 'readyset_account',
      isTrialSource: false,
      trialStatus: undefined,
    })

    renderWithClient(<SettingsPage search={{ panel: 'ai' }} />)

    const hosted = await screen.findByRole('switch', {
      name: 'Hosted by Readyset',
    })
    expect(hosted.getAttribute('aria-checked')).toBe('true')
    fireEvent.click(hosted)

    await waitFor(() =>
      expect(vi.mocked(setAiProvider).mock.calls[0]?.[0]).toBe('claude')
    )
  })

  it('opens an edit deep link and clears it when the dialog is closed', async () => {
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
    expect(
      await screen.findByRole('dialog', { name: 'Edit database' })
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Close modal' }))

    expect(routerSpies.navigate).toHaveBeenCalledWith({
      to: '/configure',
      search: { panel: 'connections' },
      replace: true,
    })
  })

  it('saves an edit from the dialog and returns to the database list', async () => {
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
    configure.updateTarget.mockResolvedValue(undefined)
    vi.mocked(useFleetStatus).mockReturnValue(
      fleetStatusStub({ state: 'idle' }) as ReturnType<typeof useFleetStatus>
    )
    renderWithClient(<SettingsPage search={{ edit: 'orders' }} />)

    const dialog = await screen.findByRole('dialog', { name: 'Edit database' })
    fireEvent.change(within(dialog).getByLabelText('Name'), {
      target: { value: 'orders-eu' },
    })
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Save changes' })
    )

    await waitFor(() =>
      expect(routerSpies.navigate).toHaveBeenCalledWith({
        to: '/configure',
        search: { panel: 'connections' },
        replace: true,
      })
    )
    expect(configure.updateTarget).toHaveBeenCalledWith(
      'orders',
      expect.objectContaining({ name: 'orders-eu', host: 'orders.test' })
    )
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

    expect(
      await screen.findByRole('dialog', { name: 'Edit database' })
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Close modal' }))

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

    expect(await screen.findByText('Use my own key')).toBeTruthy()
    expect(
      (await screen.findAllByText(/Anthropic API key/)).length
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

  it('opens the add-database dialog over Settings from the Databases group', async () => {
    mockUseConfigure(vi.fn().mockResolvedValue(undefined))
    vi.mocked(useFleetStatus).mockReturnValue(
      fleetStatusStub({ state: 'idle' }) as ReturnType<typeof useFleetStatus>
    )
    renderWithClient(<SettingsPage search={{}} />)

    fireEvent.click(
      await screen.findByRole('button', { name: 'Add a database' })
    )

    const opened = routerSpies.navigate.mock.calls.at(-1)?.[0] as {
      to: string
      search: (current: Record<string, unknown>) => Record<string, unknown>
    }
    expect(opened.to).toBe('.')
    expect(opened.search({ panel: 'connections' })).toEqual({
      panel: 'connections',
      add: 1,
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

describe('Settings > Test tunnel', () => {
  afterEach(cleanup)

  async function testTunnelFromMenu() {
    const configure = mockUseConfigure(vi.fn().mockResolvedValue(undefined))
    vi.mocked(useConfigure).mockReturnValue({
      ...configure,
      targets: configure.targets.map((target) => ({
        ...target,
        ssh: { host: 'bastion.test', port: 22, user: 'deploy' },
      })),
    })
    vi.mocked(useFleetStatus).mockReturnValue(
      fleetStatusStub({ state: 'idle' }) as ReturnType<typeof useFleetStatus>
    )
    renderWithClient(<SettingsPage search={{}} />)
    fireEvent.keyDown(
      await screen.findByRole('button', { name: 'More actions for orders' }),
      { key: 'Enter' }
    )
    fireEvent.click(screen.getByRole('menuitem', { name: 'Test tunnel' }))
  }

  it('says under the row that the tunnel works', async () => {
    vi.mocked(testTunnel).mockResolvedValue({
      ok: true,
      stage: 'database',
      message: "Tunnel test passed for 'orders'.",
    })
    await testTunnelFromMenu()
    expect(vi.mocked(testTunnel)).toHaveBeenCalledWith('orders')
    expect(
      await screen.findByText(
        'Tunnel works. The jump host and the database are reachable.'
      )
    ).toBeTruthy()
  })

  it("shows the backend's reason under the row when the tunnel fails", async () => {
    const message =
      "Couldn't reach the SSH jump host bastion.test:22. Check that it's running and that this computer can reach it."
    vi.mocked(testTunnel).mockResolvedValue({
      ok: false,
      stage: 'tunnel',
      category: 'ssh_jump_unreachable',
      message,
    })
    await testTunnelFromMenu()
    expect(await screen.findByText(message)).toBeTruthy()
  })
})
