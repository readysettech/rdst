import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

let passwordLocked = false
let connectivityChecking = false
let connectivityFailure: { target: string; message: string } | null = null
let aiBlocked = false
const ensureReachable = vi.fn(async () => !connectivityFailure)

vi.mock('../components/AiSetupNotice', () => ({
  AiSetupNotice: ({ feature }: { feature: string }) =>
    aiBlocked ? <div data-testid="ai-setup-notice">{feature}</div> : null,
  useAiBlocked: () => aiBlocked,
}))

vi.mock('../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'orders_db', setTarget: vi.fn() }),
  useTargetResolution: () => ({
    target: 'orders_db',
    setTarget: vi.fn(),
    isResolving: false,
    isUnavailable: false,
    refetch: vi.fn(),
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
    failure: connectivityFailure,
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
vi.mock('../components/SemanticLayerBadge', () => ({
  SemanticLayerBadge: () => <div data-testid="semantic-layer-badge" />,
}))
vi.mock('../components/TargetDropdown', () => ({
  TargetDropdown: () => <div data-testid="target-dropdown" />,
}))
vi.mock('../components/TargetLockNotice', () => ({
  TargetLockNotice: () => <div data-testid="target-lock-notice" />,
}))
vi.mock('../components/TargetConnectivityNotice', () => ({
  TargetConnectivityNotice: ({
    failure,
    isChecking,
  }: {
    failure: unknown
    isChecking: boolean
  }) => (
    <div
      data-failed={String(Boolean(failure))}
      data-testid="target-connectivity-notice"
    >
      {isChecking ? 'checking' : 'settled'}
    </div>
  ),
}))

import { AskPage } from './-ask-page'
import { Route } from './ask'

beforeEach(() => {
  passwordLocked = false
  connectivityChecking = false
  connectivityFailure = null
  aiBlocked = false
  ensureReachable.mockClear()
})

afterEach(() => {
  cleanup()
})

describe('ask route', () => {
  it('accepts only the retired-agents arrival flag, never a view switcher', () => {
    const validateSearch = Route.options.validateSearch as (
      search: Record<string, unknown>
    ) => Record<string, unknown>
    expect(validateSearch({ from: 'agents' })).toEqual({ from: 'agents' })
    expect(validateSearch({ view: 'chat', from: 'elsewhere' })).toEqual({})
  })
})

describe('AskPage workspace', () => {
  it('renders Ask as one focused workspace with target provenance', () => {
    render(<AskPage />)

    expect(screen.getByRole('heading', { name: 'Ask' })).toBeTruthy()
    expect(
      screen.getByText(
        'Turn a database question into a verified answer and reusable SQL.'
      )
    ).toBeTruthy()
    expect(screen.getByTestId('semantic-layer-badge')).toBeTruthy()
    expect(screen.getByTestId('target-dropdown')).toBeTruthy()

    const panel = screen.getByTestId('ask-panel')
    expect(panel.getAttribute('data-target')).toBe('orders_db')
    expect(panel.getAttribute('data-disabled')).toBe('false')
    expect(panel.getAttribute('data-has-preflight')).toBe('true')
    expect(screen.queryByRole('tab')).toBeNull()
    expect(screen.queryByText('Conversations')).toBeNull()
  })

  it('says what happened when the reader arrives from the retired /agents URL', () => {
    render(<AskPage movedFrom="agents" />)

    expect(screen.getByRole('alert').textContent).toMatch(/rdst CLI/i)
    expect(screen.getByText('Agents was retired')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Got it' }))
    expect(screen.queryAllByText('Agents was retired')).toHaveLength(0)
  })

  it('shows no arrival notice when Ask is opened directly', () => {
    render(<AskPage />)

    expect(screen.queryAllByText('Agents was retired')).toHaveLength(0)
  })

  it('keeps Ask visible but blocks submissions while connectivity is checked', () => {
    connectivityChecking = true
    render(<AskPage />)

    expect(screen.getByTestId('target-connectivity-notice').textContent).toBe(
      'checking'
    )
    expect(screen.getByTestId('ask-panel').getAttribute('data-disabled')).toBe(
      'true'
    )
  })

  it('asks for an AI provider in place, without replacing the page', () => {
    aiBlocked = true
    render(<AskPage />)

    expect(screen.getByRole('heading', { name: 'Ask' })).toBeTruthy()
    expect(screen.getByTestId('ai-setup-notice').textContent).toBe('Ask')
    expect(screen.getByTestId('ask-panel').getAttribute('data-disabled')).toBe(
      'true'
    )
  })

  it('keeps the workspace visible but disables Ask when the target is locked', () => {
    passwordLocked = true
    render(<AskPage />)

    expect(screen.getByTestId('target-lock-notice')).toBeTruthy()
    expect(screen.getByTestId('ask-panel').getAttribute('data-disabled')).toBe(
      'true'
    )
  })
})
