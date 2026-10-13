import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import type { ReactElement } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PrereqCheck, PrereqResult } from './api'
import { checkerPhase } from './model'
import { PrerequisitesChecker } from './PrerequisitesChecker'
import type { PrerequisitesState } from './usePrerequisites'

const check = (
  id: string,
  status: PrereqCheck['status'],
  overrides: Partial<PrereqCheck> = {}
): PrereqCheck => ({
  id,
  label: `${id} label`,
  status,
  detail: status === 'ok' ? '' : `${id} detail`,
  blocking: true,
  recommended: false,
  fix:
    status === 'ok' || status === 'waiting'
      ? null
      : {
          summary: `Fix ${id}.`,
          sql: [`CREATE EXTENSION IF NOT EXISTS ${id};`],
          steps: [`Connect as a superuser for ${id}.`],
          commands: [],
          after: ['Restart PostgreSQL.'],
          restart: true,
          note: null,
          docs_url: 'https://example.test/docs',
        },
  remediation: null,
  ...overrides,
})

const result = (
  checks: PrereqCheck[],
  overrides: Partial<PrereqResult> = {}
) => {
  const blocking = checks.filter((c) => c.blocking && c.status !== 'ok').length
  return {
    target: 'orders',
    engine: 'postgresql',
    database: 'app',
    checked_at: 0,
    provider: 'rds',
    provider_name: 'Amazon RDS',
    ready: blocking === 0,
    blocking,
    attention: checks.filter((c) => !c.blocking && c.status === 'missing')
      .length,
    warnings: 0,
    checks,
    connection_error: null,
    ...overrides,
  } satisfies PrereqResult
}

const state = (
  data: PrereqResult | undefined,
  overrides: Partial<PrerequisitesState> = {}
): PrerequisitesState => ({
  result: data,
  phase: checkerPhase(data, Boolean(overrides.error)),
  checking: false,
  error: null,
  interval: 4000,
  updatedAt: Date.now(),
  recheck: vi.fn(),
  ...overrides,
})

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

/** SQL renders as highlighted tokens: match on the whole statement's text. */
const hasSql = (sql: string) =>
  screen.queryAllByText((_, element) => element?.textContent === sql).length > 0

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const BLOCKED = [
  check('pg_stat_statements_preload', 'ok'),
  check('pg_stat_statements', 'missing'),
  check('read_all_stats', 'ok'),
  check('visibility_probe', 'waiting'),
  check('query_text_size', 'warn', { blocking: false, recommended: true }),
]

describe('PrerequisitesChecker', () => {
  it('shows skeleton rows while the first answer loads', () => {
    renderWithClient(
      <PrerequisitesChecker target="orders" state={state(undefined)} />
    )
    expect(
      screen.getByRole('heading', { name: 'Checking orders' })
    ).toBeTruthy()
    expect(
      screen
        .getByRole('list', { name: 'Prerequisites' })
        .getAttribute('aria-busy')
    ).toBe('true')
  })

  it('lists each check with its status and opens the first fix', () => {
    renderWithClient(
      <PrerequisitesChecker target="orders" state={state(result(BLOCKED))} />
    )
    const checker = screen.getByTestId('prereq-checker')
    expect(checker.getAttribute('data-phase')).toBe('blocked')
    expect(screen.getByRole('heading', { name: 'One fix needed' })).toBeTruthy()
    expect(screen.getByText('Amazon RDS')).toBeTruthy()

    const required = screen.getByRole('list', { name: 'Prerequisites' })
    const statuses = Array.from(required.querySelectorAll('[data-check]')).map(
      (row) => [row.getAttribute('data-check'), row.getAttribute('data-status')]
    )
    expect(statuses).toEqual([
      ['pg_stat_statements_preload', 'ok'],
      ['pg_stat_statements', 'missing'],
      ['read_all_stats', 'ok'],
      ['visibility_probe', 'waiting'],
    ])
    // The failing check's fix is open, with copyable SQL, steps and restart.
    expect(hasSql('CREATE EXTENSION IF NOT EXISTS pg_stat_statements;')).toBe(
      true
    )
    expect(screen.getByText('Run in app')).toBeTruthy()
    expect(screen.getByText('Needs a restart')).toBeTruthy()
    expect(screen.getByText('Restart PostgreSQL.')).toBeTruthy()
    expect(
      screen.getByRole('link', { name: /Open the provider guide/ })
    ).toBeTruthy()
    expect(
      screen
        .getByRole('progressbar', { name: 'Required checks passed' })
        .getAttribute('aria-valuenow')
    ).toBe('2')
    // Recommendations sit apart, with their fix closed.
    const recommended = screen.getByRole('list', {
      name: 'Recommended settings',
    })
    expect(within(recommended).getByText('Recommended')).toBeTruthy()
    expect(hasSql('CREATE EXTENSION IF NOT EXISTS query_text_size;')).toBe(
      false
    )
    fireEvent.click(
      within(recommended).getByRole('button', { name: /Show how to fix/ })
    )
    expect(hasSql('CREATE EXTENSION IF NOT EXISTS query_text_size;')).toBe(true)
  })

  it('shows the live check state and re-checks on demand', () => {
    const recheck = vi.fn()
    const { rerender } = renderWithClient(
      <PrerequisitesChecker
        target="orders"
        state={state(result(BLOCKED), { recheck })}
      />
    )
    expect(screen.getByTestId('prereq-live').textContent).toMatch(
      /Next check in \d s/
    )
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
    expect(recheck).toHaveBeenCalled()

    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <PrerequisitesChecker
          target="orders"
          state={state(result(BLOCKED), { checking: true })}
        />
      </QueryClientProvider>
    )
    expect(
      screen.getByTestId('prereq-live').getAttribute('data-checking')
    ).toBe('true')
    expect(screen.getByTestId('prereq-live').textContent).toBe('Checking')
  })

  it('offers skip while blocked and hides continue', () => {
    const onSkip = vi.fn()
    const onContinue = vi.fn()
    renderWithClient(
      <PrerequisitesChecker
        target="orders"
        state={state(result(BLOCKED))}
        onSkip={onSkip}
        onContinue={onContinue}
        skipLabel="Set up later"
      />
    )
    expect(screen.queryByRole('button', { name: 'Continue' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Set up later' }))
    expect(onSkip).toHaveBeenCalled()
  })

  it('finishes on the ready tick and a continue action', () => {
    const onContinue = vi.fn()
    const onPhaseChange = vi.fn()
    const ready = result([
      check('pg_stat_statements', 'ok'),
      check('visibility_probe', 'ok'),
      check('query_text_size', 'warn', { blocking: false, recommended: true }),
    ])
    renderWithClient(
      <PrerequisitesChecker
        target="orders"
        state={state(ready, { interval: false })}
        onContinue={onContinue}
        onPhaseChange={onPhaseChange}
        onSkip={vi.fn()}
      />
    )
    expect(screen.getByTestId('prereq-ready-mark')).toBeTruthy()
    expect(
      screen.getByRole('heading', { name: 'Ready to monitor' })
    ).toBeTruthy()
    expect(
      screen.getByText('RDST can see every query orders runs.')
    ).toBeTruthy()
    expect(onPhaseChange).toHaveBeenCalledWith('ready')
    // Passed rows collapse to one line; the recommendation keeps its detail.
    expect(screen.queryByText('Passed')).toBeNull()
    expect(screen.getByText('query_text_size detail')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Skip for now' })).toBeNull()
    expect(screen.getByText('Amazon RDS').tagName).toBe('P')
    const continueButton = screen.getByRole('button', { name: 'Continue' })
    expect(continueButton.querySelector('svg')).toBeNull()
    fireEvent.click(continueButton)
    expect(onContinue).toHaveBeenCalled()
  })

  it('keeps the rows and skips the mark when celebrate is off', () => {
    const ready = result([check('pg_stat_statements', 'ok')])
    renderWithClient(
      <PrerequisitesChecker
        target="orders"
        state={state(ready)}
        celebrate={false}
      />
    )
    expect(screen.queryByTestId('prereq-ready-mark')).toBeNull()
    expect(
      screen.getByRole('heading', { name: 'Ready to monitor' })
    ).toBeTruthy()
    expect(
      document.querySelector(
        '[data-check="pg_stat_statements"][data-status="ok"]'
      )
    ).toBeTruthy()
  })

  it("lets monitoring start when only other users' SQL stays hidden", () => {
    const attention = result([
      check('pg_stat_statements', 'ok'),
      check('read_all_stats', 'missing', { blocking: false }),
      check('visibility_probe', 'ok'),
    ])
    const onContinue = vi.fn()
    renderWithClient(
      <PrerequisitesChecker
        target="orders"
        state={state(attention)}
        onContinue={onContinue}
      />
    )
    expect(
      screen.getByTestId('prereq-checker').getAttribute('data-phase')
    ).toBe('attention')
    expect(
      screen.getByRole('heading', { name: 'Monitoring can start' })
    ).toBeTruthy()
    expect(screen.getByText('Needs a fix')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(onContinue).toHaveBeenCalled()
  })

  it('explains a connection failure and keeps trying', () => {
    const unreachable = result([], {
      connection_error: 'password authentication failed',
      ready: false,
    })
    renderWithClient(
      <PrerequisitesChecker target="orders" state={state(unreachable)} />
    )
    expect(
      screen.getByRole('heading', { name: "Can't reach orders" })
    ).toBeTruthy()
    expect(
      screen.getAllByText('password authentication failed').length
    ).toBeGreaterThan(0)
  })

  it('fetches the target and picks up a fix on the next check', async () => {
    const answers = [
      result(BLOCKED),
      result([
        check('pg_stat_statements', 'ok'),
        check('visibility_probe', 'ok'),
      ]),
    ]
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => answers.shift() ?? answers[0],
    }))
    vi.stubGlobal('fetch', fetchMock)
    renderWithClient(<PrerequisitesChecker target="orders db" />)

    await screen.findByRole('heading', { name: 'One fix needed' })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/configure/targets/orders%20db/prerequisites',
      expect.anything()
    )
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
    await waitFor(() =>
      expect(screen.getByTestId('prereq-ready-mark')).toBeTruthy()
    )
  })

  it('names the password step when the target has no password', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 409,
        json: async () => ({ detail: { code: 'TARGET_PASSWORD_REQUIRED' } }),
      }))
    )
    renderWithClient(<PrerequisitesChecker target="orders" />)
    expect(
      (await screen.findAllByText('Enter the password for orders to check it.'))
        .length
    ).toBeGreaterThan(0)
  })
})
