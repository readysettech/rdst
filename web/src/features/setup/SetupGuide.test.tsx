import { cleanup, fireEvent, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { trackEvent } from '../../lib/analytics'
import type { AiGate } from '../../lib/useAiGate'
import { renderWithClient } from '../../test-utils'
import { SetupGuide } from './SetupGuide'
import {
  __resetSetupGuideStoreForTests,
  markSetupGuideAutoExpanded,
  requestSetupGuide,
} from './setupGuideStore'
import type { SetupProgress } from './setupModel'
import { __resetSetupProgressEventsForTests } from './useSetupProgress'

const { location, gate } = vi.hoisted(() => ({
  location: { pathname: '/' },
  gate: { current: { status: 'ready' } as AiGate },
}))

vi.mock('@tanstack/react-router', () => ({
  useRouterState: () => ({ location }),
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

beforeEach(() => {
  location.pathname = '/'
  gate.current = { status: 'ready' }
  tracked.mockClear()
  __resetSetupGuideStoreForTests()
  __resetSetupProgressEventsForTests()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('SetupGuide', () => {
  it('shows the count next to the bar, never the bar alone', async () => {
    markSetupGuideAutoExpanded()
    stubProgress(progress())
    renderWithClient(<SetupGuide />)

    const pill = await screen.findByTestId('setup-guide-pill')
    expect(pill.textContent).toContain('Setup · 2 of 5')
    expect(pill.getAttribute('aria-label')).toBe(
      'Setup guide, 2 of 5 steps done'
    )
  })

  it('hides itself once every step is complete', async () => {
    const done = progress({
      queries_found: true,
      analyzed: true,
      compared: true,
    })
    stubProgress(done)
    renderWithClient(<SetupGuide />)

    await vi.waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.queryByTestId('setup-guide-pill')).toBeNull()
  })

  it('stays hidden while the signals are unknown', async () => {
    stubProgress(progress({ error: 'no targets configured' }))
    renderWithClient(<SetupGuide />)

    await vi.waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.queryByTestId('setup-guide-pill')).toBeNull()
  })

  it.each([
    '/demo',
    '/onboarding',
  ])('never renders on %s, which owns that corner or is the step', async (pathname) => {
    location.pathname = pathname
    stubProgress(progress())
    renderWithClient(<SetupGuide />)

    await vi.waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.queryByTestId('setup-guide-pill')).toBeNull()
  })

  it('expands automatically exactly once per install, on Home', async () => {
    stubProgress(progress())
    const first = renderWithClient(<SetupGuide />)

    expect(await screen.findByTestId('setup-guide-panel')).toBeTruthy()
    // The automatic expansion must not steal focus.
    expect(document.activeElement).toBe(document.body)

    first.unmount()
    renderWithClient(<SetupGuide />)

    expect(await screen.findByTestId('setup-guide-pill')).toBeTruthy()
    expect(screen.queryByTestId('setup-guide-panel')).toBeNull()
  })

  it('leaves a working page alone: the pill waits to be clicked', async () => {
    // An open panel covers the corner it floats over. Home is where the guide
    // introduces itself; everywhere else it stays a pill until asked for.
    location.pathname = '/configure'
    stubProgress(progress())
    renderWithClient(<SetupGuide />)

    expect(await screen.findByTestId('setup-guide-pill')).toBeTruthy()
    expect(screen.queryByTestId('setup-guide-panel')).toBeNull()

    fireEvent.click(screen.getByTestId('setup-guide-pill'))
    expect(screen.getByTestId('setup-guide-panel')).toBeTruthy()
  })

  it('opens on click and closes on Escape without a backdrop', async () => {
    markSetupGuideAutoExpanded()
    stubProgress(progress())
    renderWithClient(<SetupGuide />)

    fireEvent.click(await screen.findByTestId('setup-guide-pill'))
    expect(screen.getByTestId('setup-guide-panel')).toBeTruthy()
    expect(tracked).toHaveBeenCalledWith('setup_guide_opened')

    fireEvent.keyDown(document, { key: 'Escape' })
    await vi.waitFor(() =>
      expect(screen.queryByTestId('setup-guide-panel')).toBeNull()
    )
    expect(screen.getByTestId('setup-guide-pill')).toBeTruthy()
  })

  it('links each incomplete step to the surface that completes it', async () => {
    stubProgress(progress())
    renderWithClient(<SetupGuide />)

    await screen.findByTestId('setup-guide-panel')

    const href = (id: string) =>
      screen
        .getByTestId(`setup-step-${id}`)
        .querySelector('a')
        ?.getAttribute('href')

    expect(href('find-queries')).toBe('/queries')
    expect(href('analyze-query')).toBe('/queries')
    expect(href('compare')).toBe('/cache')
    // Completed steps collapse to label + check, with nothing left to do.
    expect(screen.getByTestId('setup-step-connect-database').dataset.done).toBe(
      'true'
    )
    expect(href('connect-database')).toBeUndefined()
  })

  it('shows the why-line only while a step is incomplete', async () => {
    stubProgress(progress())
    renderWithClient(<SetupGuide />)

    await screen.findByTestId('setup-guide-panel')

    expect(screen.getByTestId('setup-step-find-queries').textContent).toContain(
      'Collects the queries this database actually runs.'
    )
    expect(
      screen.getByTestId('setup-step-connect-database').textContent
    ).not.toContain('Readyset watches this database for queries.')
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
    renderWithClient(<SetupGuide />)

    await screen.findByTestId('setup-guide-panel')

    await vi.waitFor(() =>
      expect(
        screen
          .getByTestId('setup-step-analyze-query')
          .querySelector('a')
          ?.getAttribute('href')
      ).toBe('/queries?analyze=top-hash')
    )
  })

  it('keeps the analyze step on the library when no query is observed yet', async () => {
    stubProgress(progress({ queries_found: false }))
    renderWithClient(<SetupGuide />)

    await screen.findByTestId('setup-guide-panel')

    expect(
      screen
        .getByTestId('setup-step-analyze-query')
        .querySelector('a')
        ?.getAttribute('href')
    ).toBe('/queries')
  })

  it('surfaces the AI key prerequisite under the step that needs it', async () => {
    gate.current = { status: 'blocked', reason: 'missing' }
    stubProgress(progress())
    renderWithClient(<SetupGuide />)

    await screen.findByTestId('setup-guide-panel')

    const note = screen.getByTestId('setup-guide-ai-key-note')
    expect(note.textContent).toContain('Needs an AI key')
    expect(note.getAttribute('href')).toBe('/configure')
    // It rides under step 4 rather than becoming a sixth row.
    expect(screen.getAllByTestId(/^setup-step-/)).toHaveLength(5)
    expect(screen.getByTestId('setup-step-analyze-query').contains(note)).toBe(
      true
    )
  })

  it('keeps the AI key note out of the way once a key works', async () => {
    stubProgress(progress())
    renderWithClient(<SetupGuide />)

    await screen.findByTestId('setup-guide-panel')
    expect(screen.queryByTestId('setup-guide-ai-key-note')).toBeNull()
  })

  it('dismisses permanently, and does not come back on its own', async () => {
    markSetupGuideAutoExpanded()
    stubProgress(progress())
    const first = renderWithClient(<SetupGuide />)

    fireEvent.click(await screen.findByTestId('setup-guide-pill'))
    fireEvent.click(screen.getByTestId('setup-guide-dismiss'))

    expect(tracked).toHaveBeenCalledWith('setup_guide_dismissed')
    expect(screen.queryByTestId('setup-guide-pill')).toBeNull()

    first.unmount()
    __resetSetupGuideStoreForTests()
    renderWithClient(<SetupGuide />)

    await vi.waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.queryByTestId('setup-guide-pill')).toBeNull()
  })

  it('comes back opened when the sidebar asks for it', async () => {
    markSetupGuideAutoExpanded()
    stubProgress(progress())
    renderWithClient(<SetupGuide />)

    fireEvent.click(await screen.findByTestId('setup-guide-pill'))
    fireEvent.click(screen.getByTestId('setup-guide-dismiss'))
    tracked.mockClear()

    fireEvent.click(document.body)
    requestSetupGuide()

    expect(await screen.findByTestId('setup-guide-panel')).toBeTruthy()
    expect(tracked).toHaveBeenCalledWith('setup_guide_opened')
  })
})

describe('SetupGuide placement', () => {
  it('floats over the shell rather than displacing it', async () => {
    markSetupGuideAutoExpanded()
    stubProgress(progress())
    const { container } = renderWithClient(<SetupGuide />)

    await screen.findByTestId('setup-guide-pill')
    const root = container.firstElementChild as HTMLElement
    expect(root.className).toContain('fixed')
    expect(root.className).toContain('bottom-4')
    expect(root.className).toContain('right-4')
  })
})
