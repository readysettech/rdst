import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { trackEvent } from '../../lib/analytics'
import type { AiGate } from '../../lib/useAiGate'
import { renderWithClient } from '../../test-utils'
import { SetupSteps } from './SetupSteps'
import { __resetSetupGuideStoreForTests } from './setupGuideStore'
import type { SetupProgress } from './setupModel'
import { __resetSetupProgressEventsForTests } from './useSetupProgress'

const { gate } = vi.hoisted(() => ({
  gate: { current: { status: 'ready' } as AiGate },
}))

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    search,
    children,
    ...rest
  }: {
    to: string
    search?: Record<string, string>
    children?: ReactNode
  } & Record<string, unknown>) => (
    <a href={search ? `${to}?${new URLSearchParams(search)}` : to} {...rest}>
      {children}
    </a>
  ),
}))

vi.mock('../../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'orders', setTarget: vi.fn() }),
}))

vi.mock('../../lib/useAiGate', () => ({ useAiGate: () => gate.current }))

vi.mock('../../lib/analytics', () => ({ trackEvent: vi.fn() }))

const tracked = vi.mocked(trackEvent)

function progress(overrides: Partial<SetupProgress> = {}): SetupProgress {
  return {
    target: 'orders',
    connected: true,
    schema_built: true,
    queries_found: false,
    analyzed: false,
    compared: false,
    ...overrides,
  }
}

function stubProgress(value: SetupProgress) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(value), { status: 200 }))
  )
}

const row = (id: string) => screen.getByTestId(`setup-step-${id}`)

// The block starts collapsed; the steps are behind its header.
const openSteps = () =>
  fireEvent.click(screen.getByTestId('setup-steps-toggle'))

beforeEach(() => {
  gate.current = { status: 'ready' }
  tracked.mockClear()
  __resetSetupGuideStoreForTests()
  __resetSetupProgressEventsForTests()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('SetupSteps', () => {
  it('renders the five steps as done, current and remaining', async () => {
    stubProgress(progress())
    renderWithClient(<SetupSteps />)

    const block = await screen.findByTestId('setup-steps')
    openSteps()
    expect(block.textContent).toContain('Setup')
    expect(block.textContent).toContain('2 of 5')

    expect(row('connect-database').dataset.done).toBe('true')
    expect(row('build-schema').dataset.done).toBe('true')
    // The first outstanding step is the current one; the rest wait their turn.
    expect(row('find-queries').dataset.current).toBe('true')
    expect(row('analyze-query').dataset.current).toBe('false')
    expect(row('compare').dataset.current).toBe('false')
    expect(screen.getAllByTestId(/^setup-step-/)).toHaveLength(5)
  })

  it('gives the action to the current step alone', async () => {
    stubProgress(progress())
    renderWithClient(<SetupSteps />)

    await screen.findByTestId('setup-steps')
    openSteps()

    const current = row('find-queries').querySelector('a')
    expect(current?.getAttribute('href')).toBe('/queries')
    expect(current?.getAttribute('aria-current')).toBe('step')
    // Its reason rides on the row rather than on a second line.
    expect(current?.getAttribute('title')).toBe(
      'Collects the queries this database actually runs.'
    )
    expect(row('connect-database').querySelector('a')).toBeNull()
    expect(row('compare').querySelector('a')).toBeNull()
    expect(screen.getAllByRole('link')).toHaveLength(1)
  })

  it('sends the analyze step to the query with the most database time', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url.startsWith('/api/query-registry')
          ? new Response(
              JSON.stringify({
                queries: [{ hash: 'top-hash', sql: 'SELECT 1' }],
                facet_counts: {},
                next_cursor: null,
                total: 1,
                freshness: null,
                error: null,
              }),
              { status: 200 }
            )
          : new Response(JSON.stringify(progress({ queries_found: true })), {
              status: 200,
            })
      )
    )
    renderWithClient(<SetupSteps />)

    await screen.findByTestId('setup-steps')
    openSteps()

    await vi.waitFor(() =>
      expect(
        row('analyze-query').querySelector('a')?.getAttribute('href')
      ).toBe('/queries?analyze=top-hash')
    )
  })

  it('surfaces the AI key prerequisite under the step that needs it', async () => {
    gate.current = { status: 'blocked', reason: 'missing' }
    stubProgress(progress({ queries_found: true }))
    renderWithClient(<SetupSteps />)

    await screen.findByTestId('setup-steps')
    openSteps()

    const note = screen.getByTestId('setup-steps-ai-key-note')
    expect(note.textContent).toContain('Needs an AI key')
    expect(note.getAttribute('href')).toBe('/configure')
    // It rides under step 4 rather than becoming a sixth row.
    expect(screen.getAllByTestId(/^setup-step-/)).toHaveLength(5)
    expect(row('analyze-query').contains(note)).toBe(true)
  })

  it('keeps the AI key note out of the way once a key works', async () => {
    stubProgress(progress({ queries_found: true }))
    renderWithClient(<SetupSteps />)

    await screen.findByTestId('setup-steps')
    openSteps()
    expect(screen.queryByTestId('setup-steps-ai-key-note')).toBeNull()
  })

  it('carries a focus ring on the current step and on the hide control', async () => {
    stubProgress(progress())
    renderWithClient(<SetupSteps />)

    await screen.findByTestId('setup-steps')
    openSteps()

    const current = row('find-queries').querySelector('a')
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
    expect(row('find-queries').querySelector('a')?.className).toContain(
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
    expect(bar?.getAttribute('aria-valuenow')).toBe('2')
    expect(bar?.getAttribute('aria-valuemax')).toBe('5')

    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getAllByTestId(/^setup-step-/)).toHaveLength(5)

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

  it('retires itself once every step is complete', async () => {
    stubProgress(
      progress({ queries_found: true, analyzed: true, compared: true })
    )
    renderWithClient(<SetupSteps />)

    await vi.waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.queryByTestId('setup-steps')).toBeNull()
  })

  it('stays hidden while the signals are unknown', async () => {
    stubProgress(progress({ error: 'no targets configured' }))
    renderWithClient(<SetupSteps />)

    await vi.waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.queryByTestId('setup-steps')).toBeNull()
  })
})
