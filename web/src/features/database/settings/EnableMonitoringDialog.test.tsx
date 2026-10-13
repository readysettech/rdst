import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { settingsFixture } from '@/lib/monitor/fixtures'
import { renderWithClient } from '@/test-utils'

const mutate = vi.hoisted(() => vi.fn())
const account = vi.hoisted(() => ({
  signedIn: true,
  limit: {} as Record<string, unknown>,
}))
const prereq = vi.hoisted(() => ({ phase: 'ready' }))
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

vi.mock('@/features/prerequisites', () => ({
  usePrerequisites: () => ({ phase: prereq.phase }),
  PrerequisitesChecker: ({ state }: { state: { phase: string } }) => (
    <div data-testid="prereq-checker">{state.phase}</div>
  ),
}))

const { EnableMonitoringDialog } = await import('./EnableMonitoringDialog')

beforeEach(() => mutate.mockReset())
afterEach(() => {
  cleanup()
  account.signedIn = true
  account.limit = {}
  prereq.phase = 'ready'
  saved.value = {}
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

it('holds Turn on monitoring until the prerequisites pass', () => {
  prereq.phase = 'blocked'
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  const button = screen.getByRole('button', { name: 'Turn on monitoring' })
  expect(button.hasAttribute('disabled')).toBe(true)
  expect(
    screen.getByText(
      'Run the setup steps above first. This check updates on its own once it passes.'
    )
  ).toBeTruthy()
  fireEvent.click(button)
  expect(mutate).not.toHaveBeenCalled()
})

it('signed out, signing in leads and turning on stays secondary', () => {
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  expect(
    screen.getByRole('button', { name: 'Turn on monitoring' })
  ).toBeTruthy()
  expect(
    screen.queryByRole('button', { name: 'Sign in to Readyset' })
  ).toBeNull()
  cleanup()

  account.signedIn = false
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  expect(
    screen.getByRole('button', { name: 'Sign in to Readyset' })
  ).toBeTruthy()
  expect(
    screen.getByRole('button', { name: 'Turn on monitoring' })
  ).toBeTruthy()
  expect(screen.getByText(/You're signed out\./)).toBeTruthy()
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

it('signed out, says deep analysis runs once you sign in', () => {
  account.signedIn = false
  renderWithClient(
    <EnableMonitoringDialog target="reports" open onClose={vi.fn()} />
  )
  expect(
    screen.getByText('Deep analysis is on, and runs once you sign in.')
  ).toBeTruthy()
  expect(screen.queryByText(/^Deep analysis is on\. /)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /Advanced/ }))
  fireEvent.click(screen.getByRole('switch', { name: 'Analyze automatically' }))
  expect(
    screen.getByText(
      'Deep analysis is off. Change it any time in Monitoring settings.'
    )
  ).toBeTruthy()
})
