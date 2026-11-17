import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import {
  addFlowFor,
  endAddFlow,
  handOffAddFlow,
  newAddAttempt,
} from '@/lib/addFlow'
import { trackEvent } from '@/lib/analytics'
import { settingsFixture } from '@/lib/monitor/fixtures'
import { renderWithClient } from '@/test-utils'

const mutate = vi.hoisted(() => vi.fn())
const account = vi.hoisted(() => ({
  signedIn: true,
  limit: {} as Record<string, unknown>,
}))
const prereq = vi.hoisted(() => ({
  phase: 'ready',
  result: undefined as unknown,
  recheck: vi.fn(),
}))
const saved = vi.hoisted(() => ({ value: {} as Record<string, unknown> }))
vi.mock('@tanstack/react-router', () => ({
  useRouter: () => null,
  Link: ({ to, children }: { to: string; children: string }) => (
    <a href={to}>{children}</a>
  ),
}))
vi.mock('@/lib/monitor/hooks', async (original) => ({
  ...(await original<typeof import('@/lib/monitor/hooks')>()),
  useMonitorSettings: () => ({
    data: {
      ...settingsFixture('localdev'),
      enabled: false,
      configured: false,
      ...saved.value,
    },
  }),
  useMonitoringLimit: () => ({
    data: { signed_in: account.signedIn, ...account.limit },
  }),
  useUpdateMonitorSettings: () => ({
    mutate,
    isPending: false,
    isError: false,
  }),
}))

// A build that asks before AI (`required`), and the user's answer so far.
const aiConsent = vi.hoisted(() => ({
  required: false,
  consent: 'granted' as 'granted' | 'denied' | 'pending',
  decide: vi.fn(),
}))
vi.mock('@/lib/aiConsent', () => ({
  useAiConsent: () => ({
    required: aiConsent.required,
    consent: aiConsent.consent,
    off: aiConsent.required && aiConsent.consent !== 'granted',
    declined: aiConsent.required && aiConsent.consent === 'denied',
    loading: false,
    failed: false,
    decide: aiConsent.decide,
    deciding: false,
    error: null,
    resetError: () => {},
  }),
}))

vi.mock('@/lib/accountStatus', () => ({
  useSignedOut: () => !account.signedIn,
}))

// Sign-in itself is the dialog's own business; here it opens and finishes on a click.
vi.mock('@/components/TrialRegistrationDialog', () => ({
  AccountLoginDialog: ({
    isOpen,
    onClose,
    onSuccess,
  }: {
    isOpen: boolean
    onClose: () => void
    onSuccess?: () => void
  }) =>
    isOpen ? (
      <div data-testid="sign-in-dialog">
        <button type="button" onClick={() => onSuccess?.()}>
          Finish sign-in
        </button>
        <button type="button" onClick={onClose}>
          Close sign-in
        </button>
      </div>
    ) : null,
}))

// Saving a key is the key dialog's own business; here it saves on a click.
vi.mock('@/components/AiProviderGate', () => ({
  AnthropicKeyDialog: ({
    isOpen,
    onClose,
    onSuccess,
  }: {
    isOpen: boolean
    onClose: () => void
    onSuccess: () => void
  }) =>
    isOpen ? (
      <div data-testid="key-dialog">
        <button
          type="button"
          onClick={() => {
            onSuccess()
            onClose()
          }}
        >
          Save key
        </button>
        <button type="button" onClick={onClose}>
          Close key
        </button>
      </div>
    ) : null,
}))

vi.mock('@/features/prerequisites', () => ({
  usePrerequisites: () => ({
    phase: prereq.phase,
    result: prereq.result,
    recheck: prereq.recheck,
  }),
  PrerequisitesChecker: ({ state }: { state: { phase: string } }) => (
    <div data-testid="prereq-checker">{state.phase}</div>
  ),
}))

vi.mock('@/lib/analytics', async (original) => ({
  ...(await original<typeof import('@/lib/analytics')>()),
  trackEvent: vi.fn(),
}))

/** The agent's progress stream, driven by the test. */
class FakeEventSource {
  static last: FakeEventSource | null = null
  url: string
  listeners = new Map<string, (event: MessageEvent<string>) => void>()
  onerror: (() => void) | null = null
  constructor(url: string) {
    this.url = url
    FakeEventSource.last = this
  }
  addEventListener(
    name: string,
    listener: (event: MessageEvent<string>) => void
  ) {
    this.listeners.set(name, listener)
  }
  close() {}
  emit(payload: object) {
    act(() => {
      this.listeners.get('message')?.({
        data: JSON.stringify(payload),
      } as MessageEvent<string>)
    })
  }
}

const { EnableMonitoringDialog } = await import('./EnableMonitoringDialog')

beforeEach(() => {
  mutate.mockReset()
  aiConsent.decide = vi.fn().mockResolvedValue(undefined)
  vi.mocked(trackEvent).mockClear()
  prereq.recheck = vi.fn().mockResolvedValue(undefined)
  FakeEventSource.last = null
  vi.stubGlobal('EventSource', FakeEventSource)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  endAddFlow('reports')
  prereq.result = undefined
  account.signedIn = true
  account.limit = {}
  prereq.phase = 'ready'
  saved.value = {}
  aiConsent.required = false
  aiConsent.consent = 'granted'
})

it('says what monitoring reads and runs and turns it on with one action', () => {
  const onClose = vi.fn()
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={onClose} />
  )
  expect(screen.getByRole('heading', { name: 'Monitor reports' })).toBeTruthy()
  expect(screen.getByTestId('prereq-checker')).toBeTruthy()
  expect(screen.getByTestId('explain-analyze-warning').textContent).toBe(
    'RDST will run EXPLAIN ANALYZE on problem queries. That runs each one for real: read-only SELECTs only, each with a time limit.'
  )
  expect(
    screen.getByText(
      'Reads query statistics every minute and files what needs you in the Inbox. Deep analysis runs EXPLAIN on the busiest queries, or EXPLAIN ANALYZE with Measure real timings, which executes each one read-only with a time limit. Analyze automatically turns it off.'
    )
  ).toBeTruthy()
  expect(
    screen.getByText(
      'Deep analysis is on. Change it any time in Monitoring settings.'
    )
  ).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Not now' })).toBeNull()
  expect(screen.queryByRole('switch')).toBeNull()
  const solids = screen
    .getAllByRole('button')
    .filter((button) => button.className.includes('bg-surface-primary-solid'))
  expect(solids.map((button) => button.textContent)).toEqual([
    'Turn on monitoring',
  ])
  fireEvent.click(screen.getByRole('button', { name: 'Turn on monitoring' }))
  expect(mutate).toHaveBeenCalledWith(
    { enabled: true, deep_analysis: true, analysis_mode: 'execute' },
    expect.anything()
  )
  mutate.mock.calls[0][1].onSuccess()
  expect(onClose).toHaveBeenCalled()
})

it('turning monitoring back on says whether deep analysis is on', () => {
  saved.value = { configured: true, deep_analysis: false }
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  expect(
    screen.getByText(
      'Deep analysis is off. Change it any time in Monitoring settings.'
    )
  ).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /Advanced/ }))
  fireEvent.click(screen.getByRole('switch', { name: 'Analyze automatically' }))
  expect(
    screen.getByText(
      'Deep analysis is on. Change it any time in Monitoring settings.'
    )
  ).toBeTruthy()
})

it('keeps the analysis choices folded under Advanced', () => {
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  fireEvent.click(screen.getByRole('button', { name: /Advanced/ }))
  fireEvent.click(screen.getByRole('switch', { name: 'Measure real timings' }))
  expect(screen.queryByTestId('explain-analyze-warning')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Turn on monitoring' }))
  expect(mutate).toHaveBeenCalledWith(
    { enabled: true, deep_analysis: true, analysis_mode: 'plans_only' },
    expect.anything()
  )
})

it('says plainly that missing query stats block monitoring, with Check again and Finish later', async () => {
  prereq.phase = 'blocked'
  prereq.result = {
    engine: 'postgresql',
    checks: [{ id: 'pg_stat_statements', blocking: true, status: 'missing' }],
  }
  const onClose = vi.fn()
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={onClose} />
  )
  expect(screen.getByTestId('monitoring-blocked').textContent).toContain(
    "Query stats are off, so monitoring can't start"
  )
  expect(
    screen.queryByRole('button', { name: 'Turn on monitoring' })
  ).toBeNull()
  expect(
    screen.getByText('reports is saved. Finish any time from its page.')
  ).toBeTruthy()
  const solids = screen
    .getAllByRole('button')
    .filter((button) => button.className.includes('bg-surface-primary-solid'))
  expect(solids.map((button) => button.textContent)).toEqual(['Check again'])

  fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
  await waitFor(() => expect(prereq.recheck).toHaveBeenCalled())
  expect(mutate).not.toHaveBeenCalled()

  fireEvent.click(screen.getByRole('button', { name: 'Finish later' }))
  expect(onClose).toHaveBeenCalled()
})

it('reports where an add attempt stopped when it closes before monitoring is on', () => {
  prereq.phase = 'blocked'
  const attempt = newAddAttempt()
  handOffAddFlow('reports', { attempt, via: 'url' })
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  expect(trackEvent).toHaveBeenCalledWith('monitoring_dialog_shown', {
    via: 'url',
  })
  fireEvent.click(screen.getByRole('button', { name: 'Finish later' }))
  expect(trackEvent).toHaveBeenCalledWith('database_add_abandoned', {
    last_stage: 'monitoring_blocked',
    via: 'url',
    seconds_open: expect.any(Number),
  })
  expect(addFlowFor('reports')).toBeNull()
})

it('follows a database the agent added until the agent turns monitoring on', () => {
  prereq.phase = 'blocked'
  handOffAddFlow('reports', {
    attempt: newAddAttempt(),
    via: 'agent',
    agentSince: 1700000000,
  })
  const onClose = vi.fn()
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={onClose} />
  )
  const stream = FakeEventSource.last as FakeEventSource
  expect(stream.url).toBe('/api/agent-connect/events?since=1700000000')

  stream.emit({ step: 'needs_setup', target: 'reports', check: 'query_stats' })
  expect(prereq.recheck).toHaveBeenCalledTimes(1)
  expect(trackEvent).toHaveBeenCalledWith('database_add_test_result', {
    ok: true,
    error_class: null,
    via: 'agent',
  })

  // Another database's progress is someone else's.
  stream.emit({ step: 'tested', target: 'orders' })
  expect(prereq.recheck).toHaveBeenCalledTimes(1)

  stream.emit({ step: 'tested', target: 'reports' })
  expect(prereq.recheck).toHaveBeenCalledTimes(2)
  stream.emit({ step: 'monitoring_enabled', target: 'reports' })
  expect(onClose).toHaveBeenCalled()
  expect(addFlowFor('reports')).toBeNull()
  expect(trackEvent).not.toHaveBeenCalledWith(
    'database_add_abandoned',
    expect.anything()
  )
})

it("when the agent asks to turn monitoring on, says it's the user's turn and stays open until they click", () => {
  handOffAddFlow('reports', {
    attempt: newAddAttempt(),
    via: 'agent',
    agentSince: 1700000000,
  })
  const onClose = vi.fn()
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={onClose} />
  )
  expect(screen.queryByTestId('agent-awaiting-user')).toBeNull()
  const stream = FakeEventSource.last as FakeEventSource

  // Another database's request is someone else's.
  stream.emit({ step: 'awaiting_user', target: 'orders' })
  expect(screen.queryByTestId('agent-awaiting-user')).toBeNull()

  stream.emit({ step: 'awaiting_user', target: 'reports', ts: 1700000005 })
  expect(screen.getByTestId('agent-awaiting-user').textContent).toContain(
    'Your turn: read and click Turn on monitoring'
  )
  expect(prereq.recheck).toHaveBeenCalledTimes(1)
  expect(screen.getByTestId('explain-analyze-warning')).toBeTruthy()
  expect(onClose).not.toHaveBeenCalled()
  expect(mutate).not.toHaveBeenCalled()
  expect(addFlowFor('reports')).not.toBeNull()

  fireEvent.click(screen.getByRole('button', { name: 'Turn on monitoring' }))
  expect(mutate).toHaveBeenCalledWith(
    { enabled: true, deep_analysis: true, analysis_mode: 'execute' },
    expect.anything()
  )
  expect(onClose).not.toHaveBeenCalled()
  mutate.mock.calls[0][1].onSuccess()
  expect(onClose).toHaveBeenCalled()
  expect(addFlowFor('reports')).toBeNull()
})

it('says where the dialog was opened from when no add led here', () => {
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  expect(trackEvent).toHaveBeenCalledWith('monitoring_dialog_shown', {
    via: 'page',
  })
})

it('signed in, turns on with one action and asks for no sign-in', () => {
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  expect(
    screen.getByRole('button', { name: 'Turn on monitoring' })
  ).toBeTruthy()
  expect(screen.queryByTestId('monitoring-signin-gate')).toBeNull()
  expect(
    screen.queryByRole('button', { name: 'Get started with Readyset' })
  ).toBeNull()
  expect(trackEvent).not.toHaveBeenCalledWith(
    'monitoring_signin_gate_shown',
    expect.anything()
  )
})

it('signed out without a key, signs up or adds a key to turn monitoring on', () => {
  account.signedIn = false
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  expect(screen.getByTestId('monitoring-signin-gate').textContent).toContain(
    "Monitoring's AI comes with a free Readyset account."
  )
  expect(
    screen.queryByRole('button', { name: 'Turn on monitoring' })
  ).toBeNull()
  expect(
    screen.getByRole('button', { name: 'use your own Anthropic key' })
  ).toBeTruthy()
  expect(
    screen.queryByRole('button', { name: 'Turn on without AI' })
  ).toBeNull()
  expect(trackEvent).toHaveBeenCalledWith('monitoring_signin_gate_shown', {
    via: 'page',
  })
})

it('signed out, signing in turns monitoring on with the choices made', async () => {
  account.signedIn = false
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  fireEvent.click(screen.getByRole('button', { name: /Advanced/ }))
  fireEvent.click(screen.getByRole('switch', { name: 'Measure real timings' }))
  fireEvent.click(
    screen.getByRole('button', { name: 'Get started with Readyset' })
  )
  expect(trackEvent).toHaveBeenCalledWith('monitoring_signin_gate_choice', {
    choice: 'sign_in',
  })
  expect(mutate).not.toHaveBeenCalled()

  fireEvent.click(screen.getByRole('button', { name: 'Finish sign-in' }))
  expect(trackEvent).toHaveBeenCalledWith('monitoring_signin_gate_choice', {
    choice: 'signed_in_after',
  })
  await waitFor(() =>
    expect(mutate).toHaveBeenCalledWith(
      { enabled: true, deep_analysis: true, analysis_mode: 'plans_only' },
      expect.anything()
    )
  )
  expect(screen.queryByTestId('sign-in-dialog')).toBeNull()
})

it('signed out, closing sign-in leaves monitoring off', () => {
  account.signedIn = false
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  fireEvent.click(
    screen.getByRole('button', { name: 'Get started with Readyset' })
  )
  fireEvent.click(screen.getByRole('button', { name: 'Close sign-in' }))
  expect(mutate).not.toHaveBeenCalled()
})

it("signed out, the agent's turn-on request names the sign-in button", () => {
  account.signedIn = false
  handOffAddFlow('reports', {
    attempt: newAddAttempt(),
    via: 'agent',
    agentSince: 1700000000,
  })
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  FakeEventSource.last?.emit({ step: 'awaiting_user', target: 'reports' })
  expect(screen.getByTestId('agent-awaiting-user').textContent).toContain(
    'Your turn: read and click Get started with Readyset'
  )
})

it("with the account's AI slots full, says deep analysis waits for a free one", () => {
  account.limit = { target_fits: false, limit: 3, in_use: 3 }
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  expect(
    screen.getByText('Deep analysis is on, and runs once an AI slot is free.')
  ).toBeTruthy()
  expect(
    screen.queryByText(
      'Deep analysis is on. Change it any time in Monitoring settings.'
    )
  ).toBeNull()
})

it('asking first, says what AI sees and turns monitoring on with AI from one button', async () => {
  aiConsent.required = true
  aiConsent.consent = 'pending'
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  expect(screen.getByTestId('ai-disclosure').textContent).toContain(
    'SQL text and query plans'
  )
  fireEvent.click(screen.getByRole('button', { name: 'Allow AI and turn on' }))
  await waitFor(() => expect(mutate).toHaveBeenCalledOnce())
  expect(aiConsent.decide).toHaveBeenCalledWith('granted')
  expect(mutate.mock.calls[0][0]).toMatchObject({ enabled: true })
})

it('asking first, continuing without AI says what stops before turning monitoring on', async () => {
  aiConsent.required = true
  aiConsent.consent = 'pending'
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  fireEvent.click(screen.getByRole('button', { name: 'Continue without AI' }))
  expect(
    screen.getByRole('heading', { name: 'Monitor without AI?' })
  ).toBeTruthy()
  expect(screen.getByTestId('ai-off-comparison').textContent).toContain(
    'Fixes and index suggestions'
  )
  expect(mutate).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Back' }))
  expect(screen.getByRole('heading', { name: 'Monitor reports' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Continue without AI' }))
  fireEvent.click(screen.getByRole('button', { name: 'Monitor without AI' }))
  await waitFor(() => expect(mutate).toHaveBeenCalledOnce())
  expect(aiConsent.decide).toHaveBeenCalledWith('denied')
})

it('asking first and signed out, allows AI once the sign-in finishes', async () => {
  aiConsent.required = true
  aiConsent.consent = 'pending'
  account.signedIn = false
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  fireEvent.click(screen.getByRole('button', { name: 'Allow AI and sign in' }))
  expect(await screen.findByTestId('sign-in-dialog')).toBeTruthy()
  expect(aiConsent.decide).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Finish sign-in' }))
  expect(aiConsent.decide).toHaveBeenCalledWith('granted')
  await waitFor(() => expect(mutate).toHaveBeenCalledOnce())
})

it('asking first and signed out, closing the sign-in leaves the question open', async () => {
  aiConsent.required = true
  aiConsent.consent = 'pending'
  account.signedIn = false
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  fireEvent.click(screen.getByRole('button', { name: 'Allow AI and sign in' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Close sign-in' }))
  expect(aiConsent.decide).not.toHaveBeenCalled()
  expect(mutate).not.toHaveBeenCalled()
  expect(
    screen.getByRole('button', { name: 'Allow AI and sign in' })
  ).toBeTruthy()
  expect(
    screen.getByRole('button', { name: 'Continue without AI' })
  ).toBeTruthy()
})

it('asking first and signed out, an own key allows AI and turns monitoring on', async () => {
  aiConsent.required = true
  aiConsent.consent = 'pending'
  account.signedIn = false
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  expect(screen.getByTestId('monitoring-own-key-note').textContent).toContain(
    'Slow-query alerts still need a free Readyset account.'
  )
  fireEvent.click(
    screen.getByRole('button', { name: 'use your own Anthropic key' })
  )
  expect(aiConsent.decide).not.toHaveBeenCalled()
  fireEvent.click(await screen.findByRole('button', { name: 'Save key' }))
  expect(aiConsent.decide).toHaveBeenCalledWith('granted')
  await waitFor(() => expect(mutate).toHaveBeenCalledOnce())
  expect(screen.queryByTestId('sign-in-dialog')).toBeNull()
})

it('signed out with an own key, turns monitoring on with no sign-in', () => {
  account.signedIn = false
  account.limit = { own_key: true }
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  expect(screen.queryByTestId('monitoring-signin-gate')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Turn on monitoring' }))
  expect(mutate).toHaveBeenCalledOnce()
})

it('with AI turned off, monitors without it and needs no sign-in', () => {
  aiConsent.required = true
  aiConsent.consent = 'denied'
  account.signedIn = false
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  expect(screen.getByTestId('monitoring-ai-off')).toBeTruthy()
  expect(screen.queryByTestId('monitoring-signin-gate')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Turn on monitoring' }))
  expect(mutate).toHaveBeenCalledOnce()
  expect(aiConsent.decide).not.toHaveBeenCalled()
})
