import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const captureMock = vi.hoisted(() => vi.fn())
const initMock = vi.hoisted(() => vi.fn())
const registerMock = vi.hoisted(() => vi.fn())
const identifyMock = vi.hoisted(() => vi.fn())
const resetMock = vi.hoisted(() => vi.fn())
const fetchMock = vi.hoisted(() => vi.fn())

vi.mock('posthog-js', () => ({
  default: {
    init: initMock,
    register: registerMock,
    identify: identifyMock,
    reset: resetMock,
    setPersonProperties: vi.fn(),
    capture: captureMock,
    opt_out_capturing: vi.fn(),
    stopSessionRecording: vi.fn(),
  },
}))

const listeners: [string, EventListenerOrEventListenerObject][] = []
beforeEach(() => {
  const add = window.addEventListener.bind(window)
  vi.spyOn(window, 'addEventListener').mockImplementation(
    (type, listener, options) => {
      if (listener && (type === 'error' || type === 'unhandledrejection'))
        listeners.push([type, listener])
      add(type, listener, options)
    }
  )
})
afterEach(() => {
  for (const [type, listener] of listeners.splice(0))
    window.removeEventListener(type, listener)
  vi.restoreAllMocks()
})

describe('analytics', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.stubEnv('DEV', false)
    captureMock.mockClear()
    initMock.mockClear()
    registerMock.mockClear()
    identifyMock.mockClear()
    resetMock.mockClear()
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        telemetry_enabled: true,
        distinct_id: 'device-1',
        device_id: 'device-1',
        account_kind: 'device_only',
        email: '',
        internal_user: false,
      }),
    })
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
  })

  it('does not capture events before analytics is initialized', async () => {
    const { trackEvent } = await import('./analytics')
    trackEvent('compare_run', { query_count: 1 })
    expect(captureMock).not.toHaveBeenCalled()
  })

  it('stays a no-op when no PostHog key is configured', async () => {
    const { initAnalytics, trackEvent } = await import('./analytics')
    await initAnalytics()
    trackEvent('load_test_run')
    expect(initMock).not.toHaveBeenCalled()
    expect(captureMock).not.toHaveBeenCalled()
  })

  it('honors the server-side telemetry opt-out', async () => {
    vi.stubEnv('VITE_POSTHOG_KEY', 'test-key')
    const { initAnalytics, trackEvent } = await import('./analytics')
    await initAnalytics(false)
    trackEvent('load_test_run')

    expect(initMock).not.toHaveBeenCalled()
    expect(captureMock).not.toHaveBeenCalled()
  })

  it('captures a named event with its properties once initialized', async () => {
    vi.stubEnv('VITE_POSTHOG_KEY', 'test-key')
    const { initAnalytics, trackEvent } = await import('./analytics')
    await initAnalytics()

    trackEvent('analysis_started', { origin: 'ask' })
    expect(captureMock).toHaveBeenCalledWith('analysis_started', {
      origin: 'ask',
    })

    trackEvent('back_to_origin', { origin: 'scan' })
    expect(captureMock).toHaveBeenCalledWith('back_to_origin', {
      origin: 'scan',
    })

    trackEvent('nav_item_clicked', { label: 'Ask' })
    expect(captureMock).toHaveBeenCalledWith('nav_item_clicked', {
      label: 'Ask',
    })

    trackEvent('compare_run', { query_count: 4 })
    expect(captureMock).toHaveBeenCalledWith('compare_run', {
      query_count: 4,
    })

    trackEvent('compare_abandoned', { query_count: 4, completed: 1 })
    expect(captureMock).toHaveBeenCalledWith('compare_abandoned', {
      query_count: 4,
      completed: 1,
    })
  })

  it('captures property-less events with no second argument', async () => {
    vi.stubEnv('VITE_POSTHOG_KEY', 'test-key')
    const { initAnalytics, trackEvent } = await import('./analytics')
    await initAnalytics()

    trackEvent('load_test_run')
    expect(captureMock).toHaveBeenCalledWith('load_test_run', undefined)
  })

  it('registers rdst as a super property alongside platform on init', async () => {
    vi.stubEnv('VITE_POSTHOG_KEY', 'test-key')
    const { initAnalytics } = await import('./analytics')
    await initAnalytics()

    expect(initMock).toHaveBeenCalledTimes(1)
    expect(registerMock).toHaveBeenCalledWith(
      expect.objectContaining({ app: 'rdst', platform: 'web' })
    )
  })

  it('shares the install ID and switches to the account person profile', async () => {
    vi.stubEnv('VITE_POSTHOG_KEY', 'test-key')
    const { initAnalytics, setAnalyticsAccountId } = await import('./analytics')
    await initAnalytics()
    expect(initMock).toHaveBeenCalledWith(
      'test-key',
      expect.objectContaining({
        bootstrap: { distinctID: 'device-1', isIdentifiedID: false },
      })
    )
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        telemetry_enabled: true,
        distinct_id: 'usr_42',
        device_id: 'device-1',
        account_kind: 'signed_in',
        email: 'person@example.com',
        internal_user: true,
      }),
    })
    setAnalyticsAccountId('account-hash')
    await vi.waitFor(() =>
      expect(identifyMock).toHaveBeenCalledWith('usr_42', {
        account_kind: 'signed_in',
        email: 'person@example.com',
      })
    )
    expect(resetMock).toHaveBeenCalledTimes(1)
    expect(registerMock).toHaveBeenCalledWith(
      expect.objectContaining({ internal_user: true })
    )
    expect(
      registerMock.mock.calls.every(([props]) => !('email' in props))
    ).toBe(true)
  })

  it('returns to the install ID on sign-out and keeps the surface properties', async () => {
    vi.stubEnv('VITE_POSTHOG_KEY', 'test-key')
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        telemetry_enabled: true,
        distinct_id: 'usr_42',
        device_id: 'device-1',
        account_kind: 'signed_in',
        email: 'person@example.com',
        internal_user: false,
      }),
    })
    const { initAnalytics, setAnalyticsAccountId } = await import('./analytics')
    await initAnalytics()
    setAnalyticsAccountId(null)
    await vi.waitFor(() => expect(resetMock).toHaveBeenCalledTimes(1))
    expect(registerMock).toHaveBeenCalledWith({ distinct_id: 'device-1' })
    expect(registerMock).toHaveBeenLastCalledWith({ distinct_id: 'device-1' })
    expect(registerMock).toHaveBeenCalledWith(
      expect.objectContaining({
        device_id: 'device-1',
        surface: 'web',
        internal_user: false,
      })
    )
  })

  it('excludes readyset.io accounts at startup regardless of email case', async () => {
    vi.stubEnv('VITE_POSTHOG_KEY', 'test-key')
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        telemetry_enabled: true,
        account_kind: 'signed_in',
        email: 'employee@ReadySet.IO',
      }),
    })
    const { initAnalytics, trackEvent } = await import('./analytics')
    await initAnalytics()
    trackEvent('load_test_run')
    expect(initMock).not.toHaveBeenCalled()
    expect(captureMock).not.toHaveBeenCalled()
  })

  it.each([
    'internal',
    'opt-out',
  ])('stops all capture after an %s identity refresh', async (kind) => {
    vi.stubEnv('VITE_POSTHOG_KEY', 'test-key')
    const { initAnalytics, setAnalyticsAccountId, trackEvent } = await import(
      './analytics'
    )
    const { default: posthog } = await import('posthog-js')
    vi.mocked(posthog.opt_out_capturing).mockClear()
    vi.mocked(posthog.stopSessionRecording).mockClear()
    await initAnalytics()
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        telemetry_enabled: kind !== 'opt-out',
        distinct_id: 'employee',
        device_id: 'device-1',
        account_kind: 'signed_in',
        email:
          kind === 'internal' ? 'employee@READYSET.io' : 'person@example.com',
      }),
    })
    setAnalyticsAccountId('employee')
    await vi.waitFor(() => expect(posthog.opt_out_capturing).toHaveBeenCalled())
    expect(posthog.stopSessionRecording).toHaveBeenCalled()
    expect(identifyMock).not.toHaveBeenCalled()
    trackEvent('load_test_run')
    expect(captureMock).not.toHaveBeenCalled()
  })
  it('stays silent if the local identity cannot be read', async () => {
    vi.stubEnv('VITE_POSTHOG_KEY', 'test-key')
    fetchMock.mockRejectedValue(new Error('offline'))
    const { initAnalytics } = await import('./analytics')
    await initAnalytics()
    expect(initMock).not.toHaveBeenCalled()
  })

  it('marks every event with its surface so funnels can split desktop from web', async () => {
    vi.stubEnv('VITE_POSTHOG_KEY', 'test-key')
    const { initAnalytics } = await import('./analytics')
    await initAnalytics()

    expect(registerMock).toHaveBeenCalledWith(
      expect.objectContaining({ surface: 'web', is_desktop: false })
    )
  })

  it('reports uncaught UI errors as app_error once initialized, never their raw text', async () => {
    vi.stubEnv('VITE_POSTHOG_KEY', 'test-key')
    const { initAnalytics } = await import('./analytics')
    await initAnalytics()

    window.dispatchEvent(
      new ErrorEvent('error', {
        error: new RangeError("no row for 'orders' at db.example.com"),
      })
    )
    expect(captureMock).toHaveBeenCalledWith('app_error', {
      source: 'web',
      component: 'window',
      error_class: 'RangeError',
      message: 'no row for [value] at [host]',
      handled: false,
      suppressed_repeats: 0,
    })
  })
})

describe('telemetry receiver privacy and gates', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    vi.stubEnv('VITE_POSTHOG_KEY', 'isolated-test-key')
    vi.stubEnv('DEV', false)
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        telemetry_enabled: true,
        distinct_id: 'install',
        device_id: 'install',
        account_kind: 'device_only',
        email: '',
        internal_user: false,
      }),
    })
  })
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
    delete window.rdstDesktop
  })

  it.each([
    'dev',
    'webdriver',
    'desktop',
    'identity',
  ])('gates %s', async (gate) => {
    if (gate === 'dev') vi.stubEnv('DEV', true)
    if (gate === 'webdriver') vi.stubGlobal('navigator', { webdriver: true })
    if (gate === 'desktop')
      window.rdstDesktop = {
        telemetryDisabled: true,
      } as typeof window.rdstDesktop
    if (gate === 'identity')
      fetchMock.mockResolvedValue({
        ok: true,
        json: async () => ({ telemetry_enabled: false }),
      })
    const { initAnalytics, trackEvent } = await import('./analytics')
    await initAnalytics()
    trackEvent('load_test_run')
    expect(initMock).not.toHaveBeenCalled()
    expect(captureMock).not.toHaveBeenCalled()
  })

  it.each([
    'window',
    'promise',
    'error_boundary',
  ])('captures the real %s error trigger once', async (source) => {
    const { initAnalytics } = await import('./analytics')
    const { reportAppError } = await import('./appErrors')
    await initAnalytics()
    const error = new Error(
      'at canary.prod.internal /private/canary/file canary@example.com password=secret_canary SELECT canary_column FROM orders'
    )
    const trigger = () => {
      if (source === 'window')
        window.dispatchEvent(new ErrorEvent('error', { error }))
      else if (source === 'promise') {
        const event = new Event('unhandledrejection')
        Object.defineProperty(event, 'reason', { value: error })
        window.dispatchEvent(event)
      } else reportAppError('error_boundary', error)
    }
    trigger()
    trigger()
    expect(captureMock).toHaveBeenCalledTimes(1)
    const [name, props] = captureMock.mock.calls[0]
    expect(name).toBe('app_error')
    expect(Object.keys(props).sort()).toEqual(
      [
        'source',
        'component',
        'error_class',
        'message',
        'handled',
        'suppressed_repeats',
      ].sort()
    )
    expect(props).toMatchObject({
      source: 'web',
      component: source,
      error_class: 'Error',
      handled: source === 'error_boundary',
      suppressed_repeats: 0,
    })
    expect(typeof props.message).toBe('string')
    expect(JSON.stringify(captureMock.mock.calls)).not.toMatch(
      /canary|secret_canary/
    )
  })

  it('scrubs the serialized automatic SDK payload and blocks DOM recording', async () => {
    const { initAnalytics } = await import('./analytics')
    await initAnalytics()
    const options = initMock.mock.calls[0][1]
    const payload = options.before_send({
      event: '$exception',
      properties: {
        $current_url: 'https://canary.prod.internal/private/canary/file',
        $pathname: '/canary',
        $title: 'canary_column',
        exceptions: [
          {
            message:
              'password=secret_canary canary@example.com SELECT canary_column FROM orders',
            stack: [{ filename: '/private/canary/file' }],
          },
        ],
        sql: 'SELECT canary_column FROM orders',
        host: 'canary.prod.internal',
      },
    })
    expect(JSON.stringify(payload)).not.toMatch(/canary|secret_canary/)
    expect(options.session_recording?.maskAllInputs).toBe(true)
  })
})

vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))
vi.mock('@rs/ui-new/use-toast', () => ({
  useToast: () => ({ toast: vi.fn() }),
}))
vi.mock('@/lib/monitor/hooks', () => ({
  useInboxAction: () => ({ mutate: vi.fn(), mutateAsync: vi.fn() }),
}))

it.each([
  'route',
  'database',
] as const)('inbox_item_opened from the real %s action', async (via) => {
  vi.resetModules()
  vi.clearAllMocks()
  vi.stubEnv('DEV', false)
  vi.stubEnv('VITE_POSTHOG_KEY', 'isolated-test-key')
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockResolvedValue({
    ok: true,
    json: async () => ({
      telemetry_enabled: true,
      distinct_id: 'install',
      device_id: 'install',
      account_kind: 'device_only',
      email: '',
      internal_user: false,
    }),
  })
  const { initAnalytics } = await import('./analytics')
  const { useInboxActions } = await import('@/features/inbox/useInboxActions')
  await initAnalytics()
  const { result, unmount } = renderHook(() => useInboxActions())
  const item = {
    id: 1,
    target: 'canary-target',
    type: 'query_slower',
    severity_band: 'watch',
    state: 'new',
    created_at: Date.now() / 1000,
    subject: 'aaaaaaaaaaaa',
    title: 'SELECT canary_column FROM orders',
    query_keys: [],
    severity: 0.5,
    body_json: '{}',
    updated_at: Date.now() / 1000,
    resolved_at: null,
    resolved_by: null,
    seen_count: 0,
    reopened_count: 0,
    tone: 'warning',
    summary: 'Finding',
    subject_label: 'Query',
    group_key: 'query',
    muted_by: null,
    trend: [],
  } as import('@/lib/monitor/types').InboxItem
  act(() => {
    if (via === 'route')
      result.current.openRoute(item, {
        to: '/database',
        search: { target: item.target },
      })
    else result.current.openInDatabase(item)
  })
  expect(captureMock).toHaveBeenCalledTimes(1)
  const [event, props] = captureMock.mock.calls[0]
  expect(event).toBe('inbox_item_opened')
  expect(Object.keys(props).sort()).toEqual(
    ['kind', 'band', 'state', 'age_bucket', 'via'].sort()
  )
  expect(props).toMatchObject({
    kind: 'query_slower',
    band: 'watch',
    state: 'new',
    via,
  })
  expect(typeof props.age_bucket).toBe('string')
  expect(JSON.stringify(captureMock.mock.calls)).not.toContain('canary')
  unmount()
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})
