import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { trackEvent } from '../../lib/analytics'
import { renderWithClient } from '../../test-utils'
import { SetupSteps } from './SetupSteps'
import { __resetSetupGuideStoreForTests } from './setupGuideStore'
import type { SetupProgress } from './setupModel'
import { __resetSetupProgressEventsForTests } from './useSetupProgress'

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    children,
    ...rest
  }: { to: string; children?: ReactNode } & Record<string, unknown>) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}))

vi.mock('../../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'orders', setTarget: vi.fn() }),
}))

vi.mock('../../lib/analytics', () => ({ trackEvent: vi.fn() }))

const tracked = vi.mocked(trackEvent)

function progress(overrides: Partial<SetupProgress> = {}): SetupProgress {
  return { target: 'orders', connected: true, ...overrides }
}

/** Setup progress from the server, and the target's monitoring setting. */
function stubProgress(value: SetupProgress, monitoring = false) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) =>
      url.startsWith('/api/monitor/')
        ? new Response(JSON.stringify({ enabled: monitoring }), { status: 200 })
        : new Response(JSON.stringify(value), { status: 200 })
    )
  )
}

const row = (id: string) => screen.getByTestId(`setup-step-${id}`)

// The block starts collapsed; the steps are behind its header.
const openSteps = () =>
  fireEvent.click(screen.getByTestId('setup-steps-toggle'))

beforeEach(() => {
  tracked.mockClear()
  __resetSetupGuideStoreForTests()
  __resetSetupProgressEventsForTests()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('SetupSteps', () => {
  it('is two steps, connect and turn on monitoring, with schema left out', async () => {
    stubProgress(progress({ schema_built: false }))
    renderWithClient(<SetupSteps />)

    const block = await screen.findByTestId('setup-steps')
    openSteps()
    expect(block.textContent).toContain('Setup')
    expect(block.textContent).toContain('1 of 2')

    expect(row('connect-database').dataset.done).toBe('true')
    expect(row('turn-on-monitoring').dataset.current).toBe('true')
    expect(screen.getAllByTestId(/^setup-step-/)).toHaveLength(2)
    expect(block.textContent).not.toMatch(/schema/i)
  })

  it('sends the monitoring step to the Database page', async () => {
    stubProgress(progress())
    renderWithClient(<SetupSteps />)

    await screen.findByTestId('setup-steps')
    openSteps()

    const current = row('turn-on-monitoring').querySelector('a')
    expect(current?.getAttribute('href')).toBe('/database')
    expect(current?.getAttribute('aria-current')).toBe('step')
    expect(current?.getAttribute('title')).toBe(
      'Reads query statistics. Never writes.'
    )
    expect(row('connect-database').querySelector('a')).toBeNull()
    expect(screen.getAllByRole('link')).toHaveLength(1)
  })

  it('starts at connecting when no database is connected yet', async () => {
    stubProgress(progress({ target: '', connected: false }))
    renderWithClient(<SetupSteps />)

    const block = await screen.findByTestId('setup-steps')
    openSteps()
    expect(block.textContent).toContain('0 of 2')
    expect(
      row('connect-database').querySelector('a')?.getAttribute('href')
    ).toBe('/onboarding')
  })

  it('carries a focus ring on the current step and on the hide control', async () => {
    stubProgress(progress())
    renderWithClient(<SetupSteps />)

    await screen.findByTestId('setup-steps')
    openSteps()

    const current = row('turn-on-monitoring').querySelector('a')
    expect(current?.className).toContain('focus-visible:ring-2')
    const hide = screen.getByTestId('setup-steps-dismiss')
    expect(hide.getAttribute('aria-label')).toBe('Hide setup guide')
    expect(hide.className).toContain('focus-visible:ring')
  })

  it('names itself as a region, and its motion is opt-in', async () => {
    stubProgress(progress())
    renderWithClient(<SetupSteps />)

    const block = await screen.findByTestId('setup-steps')
    openSteps()
    expect(block.getAttribute('aria-label')).toBe('Setup guide')
    expect(block.tagName).toBe('SECTION')
    expect(block.querySelector('ol')).toBeTruthy()
    expect(row('turn-on-monitoring').querySelector('a')?.className).toContain(
      'motion-safe:transition-colors'
    )
  })

  it('starts collapsed behind a progress readout, and unfolds on request', async () => {
    stubProgress(progress())
    renderWithClient(<SetupSteps />)

    const block = await screen.findByTestId('setup-steps')
    const toggle = screen.getByTestId('setup-steps-toggle')
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryAllByTestId(/^setup-step-/)).toHaveLength(0)
    const bar = block.querySelector('[role="progressbar"]')
    expect(bar?.getAttribute('aria-valuenow')).toBe('1')
    expect(bar?.getAttribute('aria-valuemax')).toBe('2')

    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getAllByTestId(/^setup-step-/)).toHaveLength(2)

    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    await waitFor(() =>
      expect(screen.queryAllByTestId(/^setup-step-/)).toHaveLength(0)
    )
  })

  it('hides on request, and stays hidden across a restart', async () => {
    stubProgress(progress())
    const first = renderWithClient(<SetupSteps />)

    fireEvent.click(await screen.findByTestId('setup-steps-dismiss'))

    expect(tracked).toHaveBeenCalledWith('setup_guide_dismissed')
    expect(screen.queryByTestId('setup-steps')).toBeNull()

    first.unmount()
    // Re-reads the persisted flag, as a fresh app start would.
    __resetSetupGuideStoreForTests()
    renderWithClient(<SetupSteps />)

    await vi.waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.queryByTestId('setup-steps')).toBeNull()
  })

  it('retires itself once monitoring is on', async () => {
    stubProgress(progress(), true)
    renderWithClient(<SetupSteps />)

    await vi.waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        '/api/monitor/targets/orders/settings',
        undefined
      )
    )
    expect(screen.queryByTestId('setup-steps')).toBeNull()
  })

  it('stays hidden while the signals are unknown', async () => {
    stubProgress(progress({ error: 'no targets configured' }))
    renderWithClient(<SetupSteps />)

    await vi.waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.queryByTestId('setup-steps')).toBeNull()
  })
})
