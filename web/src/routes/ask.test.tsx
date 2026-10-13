import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

let passwordLocked = false
let connectivityChecking = false
let aiBlocked = false
let resolvedTarget: string | null = 'orders_db'
const openAddDatabase = vi.fn()
const ensureReachable = vi.fn(async () => true)

vi.mock('../components/AiSetupNotice', () => ({
  AiSetupNotice: () =>
    aiBlocked ? <div data-testid="ai-setup-notice" /> : null,
  useAiBlocked: () => aiBlocked,
}))
vi.mock('../components/configure/addDatabase', () => ({
  useOpenAddDatabase: () => openAddDatabase,
}))
vi.mock('../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'orders_db', setTarget: vi.fn() }),
  useTargetResolution: () => ({
    target: resolvedTarget,
    setTarget: vi.fn(),
    isResolving: false,
    isUnavailable: false,
    refetch: vi.fn(),
  }),
}))
const filter = vi.hoisted(() => ({
  db: null as string | null,
  setDb: vi.fn(),
  databases: [] as { name: string; state: string | null; monitored: boolean }[],
}))
vi.mock('../hooks/useDbFilter', () => ({
  useDbFilter: () => ({ db: filter.db, setDb: filter.setDb }),
}))
vi.mock('../lib/monitor/hooks', () => ({
  useConfiguredDatabases: () => ({
    databases: filter.databases,
    doFirst: null,
    isPending: false,
    isError: false,
  }),
}))
vi.mock('../lib/useTargetPasswordLock', () => ({
  useTargetPasswordLock: () => ({
    isLocked: passwordLocked,
    message: 'Unlock orders_db to ask a question.',
    missingTargetRequirements: ['password'],
    keyringAvailable: false,
  }),
}))
vi.mock('../lib/useTargetConnectivityGate', () => ({
  useTargetConnectivityGate: () => ({
    failure: null,
    isChecking: connectivityChecking,
    ensureReachable,
    reset: vi.fn(),
  }),
}))
vi.mock('../components/AskPanel', () => ({
  AskPanel: ({
    target,
    disabled,
    beforeRun,
  }: {
    target?: string
    disabled?: boolean
    beforeRun?: () => Promise<boolean>
  }) => (
    <div
      data-disabled={String(disabled)}
      data-has-preflight={String(Boolean(beforeRun))}
      data-target={target}
      data-testid="ask-panel"
    />
  ),
}))
vi.mock('../components/TargetLockNotice', () => ({
  TargetLockNotice: () => <div data-testid="target-lock-notice" />,
}))
vi.mock('../components/TargetConnectivityNotice', () => ({
  TargetConnectivityNotice: ({ isChecking }: { isChecking: boolean }) => (
    <div data-testid="target-connectivity-notice">
      {isChecking ? 'checking' : 'settled'}
    </div>
  ),
}))

import { AskPage } from './-ask-page'
import { Route } from './ask'

beforeEach(() => {
  passwordLocked = false
  connectivityChecking = false
  aiBlocked = false
  resolvedTarget = 'orders_db'
  openAddDatabase.mockClear()
  ensureReachable.mockClear()
  filter.db = null
  filter.databases = [{ name: 'orders_db', state: null, monitored: true }]
})

afterEach(cleanup)

describe('ask route', () => {
  it('accepts a question to prefill and the retired-agents flag, nothing else', () => {
    const validateSearch = Route.options.validateSearch as (
      search: Record<string, unknown>
    ) => Record<string, unknown>
    expect(validateSearch({ sql: 'select 1', from: 'agents' })).toEqual({
      sql: 'select 1',
      from: 'agents',
    })
    expect(validateSearch({ view: 'chat', from: 'elsewhere' })).toEqual({})
    expect(validateSearch({ db: 'demo' })).toEqual({ db: 'demo' })
  })

  it('accepts the sample values for the placeholders and drops malformed ones', () => {
    const validateSearch = Route.options.validateSearch as (
      search: Record<string, unknown>
    ) => Record<string, unknown>
    const params = [{ name: '$1', value: '7' }]
    expect(
      validateSearch({ sql: 'select $1', params: [...params, { name: 1 }] })
    ).toEqual({ sql: 'select $1', params })
    expect(validateSearch({ sql: 'select $1', params: 'x' })).toEqual({
      sql: 'select $1',
    })
  })
})

describe('AskPage', () => {
  it('opens with the bar naming the target and the box as the page', () => {
    render(<AskPage />)
    expect(screen.getByRole('heading', { level: 1, name: 'Ask' })).toBeTruthy()
    expect(screen.getByRole('banner').textContent).toContain('orders_db')
    // With one database the filter names it and offers no All databases.
    expect(screen.getByTestId('database-filter').textContent).toBe('orders_db')
    expect(screen.queryByText(/^Asks /)).toBeNull()
    const panel = screen.getByTestId('ask-panel')
    expect(panel.getAttribute('data-target')).toBe('orders_db')
    expect(panel.getAttribute('data-disabled')).toBe('false')
    expect(panel.getAttribute('data-has-preflight')).toBe('true')
  })

  it('runs against the database the filter names', () => {
    filter.db = 'analytics'
    filter.databases = [
      { name: 'demo', state: 'watching', monitored: true },
      { name: 'analytics', state: 'watching', monitored: true },
    ]
    render(<AskPage />)
    expect(screen.getByTestId('ask-panel').getAttribute('data-target')).toBe(
      'analytics'
    )
    expect(screen.getByTestId('database-filter').textContent).toBe('analytics')
    expect(screen.queryByText(/the first monitored database/)).toBeNull()
  })

  it('asks the first monitored database for All databases, and says so', () => {
    filter.databases = [
      { name: 'staging', state: 'not_monitored', monitored: false },
      { name: 'demo', state: 'watching', monitored: true },
    ]
    render(<AskPage />)
    expect(screen.getByTestId('ask-panel').getAttribute('data-target')).toBe(
      'demo'
    )
    expect(
      screen.getByText('Asks demo, the first monitored database.')
    ).toBeTruthy()
  })

  it('keeps the box but blocks questions while connectivity is checked', () => {
    connectivityChecking = true
    render(<AskPage />)
    expect(screen.getByTestId('target-connectivity-notice').textContent).toBe(
      'checking'
    )
    expect(screen.getByTestId('ask-panel').getAttribute('data-disabled')).toBe(
      'true'
    )
  })

  it('asks for an AI provider in place and blocks questions until then', () => {
    aiBlocked = true
    render(<AskPage />)
    expect(screen.getByTestId('ai-setup-notice')).toBeTruthy()
    expect(screen.getByTestId('ask-panel').getAttribute('data-disabled')).toBe(
      'true'
    )
  })

  it('drops a stored database that is no longer configured', () => {
    resolvedTarget = 'ci_shop'
    filter.db = 'ci_shop'
    filter.databases = []
    render(<AskPage />)
    expect(screen.queryByText(/Asks ci_shop/)).toBeNull()
    expect(screen.getByTestId('database-filter').textContent).toBe(
      'All databases'
    )
    expect(screen.getByText('No database yet')).toBeTruthy()
    expect(screen.queryByTestId('ask-panel')).toBeNull()
  })

  it('offers to add a database when there is none', () => {
    resolvedTarget = null
    filter.databases = []
    render(<AskPage />)
    expect(screen.getByText('No database yet')).toBeTruthy()
    expect(screen.queryByTestId('ask-panel')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Add a database' }))
    expect(openAddDatabase).toHaveBeenCalledOnce()
  })

  it('disables Ask when the target is locked', () => {
    passwordLocked = true
    render(<AskPage />)
    expect(screen.getByTestId('target-lock-notice')).toBeTruthy()
    expect(screen.getByTestId('ask-panel').getAttribute('data-disabled')).toBe(
      'true'
    )
  })
})
