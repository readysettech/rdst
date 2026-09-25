import {
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resetStubState, setFixtureTiming } from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import { renderWithClient } from '@/test-utils'

const routerState = vi.hoisted(() => ({
  search: {} as Record<string, unknown>,
  navigate: vi.fn(),
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => routerState.navigate,
  useRouterState: ({
    select,
  }: {
    select: (state: { location: { search: unknown } }) => unknown
  }) => select({ location: { search: routerState.search } }),
  Link: ({ children }: { children?: ReactNode }) => <a href="/">{children}</a>,
}))

import { revealListTop, QuerySection as Section } from './QuerySection'

// jsdom has no PointerEvent; Radix menus open on pointerdown.
class PointerEventPolyfill extends MouseEvent {
  pointerId = 0
  pointerType = 'mouse'
  isPrimary = true
}
if (typeof window.PointerEvent === 'undefined') {
  // @ts-expect-error test-only polyfill, narrower than the DOM lib type
  window.PointerEvent = PointerEventPolyfill
}

function QuerySection({ target, range }: { target: string; range: '24h' }) {
  return (
    <Section
      target={target}
      range={range}
      domain={[0, 4_000_000_000]}
      model={null}
      scopeLabel="in the last 24 hours"
      hoverKey={null}
      onHoverKey={() => undefined}
    />
  )
}

/** Every row across the pages (each page shows ten). */
async function allRows() {
  await screen.findAllByTestId('monitor-query-row')
  const rows = [...screen.getAllByTestId('monitor-query-row')]
  for (let i = 0; i < 10; i++) {
    const next = screen.queryByRole('button', { name: 'Next' })
    if (!next || (next as HTMLButtonElement).disabled) break
    fireEvent.click(next)
    rows.push(...screen.getAllByTestId('monitor-query-row'))
  }
  return rows
}

beforeEach(() => {
  setFixtureTiming(0)
  resetStubState()
  setFixtureOverride('localdev')
  routerState.search = {}
  routerState.navigate.mockReset()
})

afterEach(() => {
  cleanup()
  setFixtureOverride(undefined)
})

describe('QuerySection', () => {
  it('pages ten rows at a time, each with a name and SQL or a reason', async () => {
    renderWithClient(<QuerySection target="localdev" range="24h" />)
    const rows = await screen.findAllByTestId('monitor-query-row')
    expect(rows).toHaveLength(10)
    expect(screen.getByTestId('query-pages')).toBeTruthy()
    expect(screen.getByTestId('query-list-sentence').textContent).toBe(
      'Queries'
    )
    // Filters sit above the rows.
    const toolbar = screen.getByTestId('query-toolbar')
    expect(
      toolbar.compareDocumentPosition(rows[0]) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    const all = await allRows()
    expect(all.length).toBeGreaterThan(10)
    for (const row of all) {
      expect(row.textContent?.trim().length).toBeGreaterThan(0)
      const hasSql = row.querySelector('code')
      const hasReason = row.querySelector('[data-testid="row-reason"]')
      expect(
        Boolean(hasSql) ||
          Boolean(hasReason) ||
          /didn't keep|not captured|statement/i.test(row.textContent ?? '')
      ).toBe(true)
    }
  })

  it('hides writes until asked, and counts them on the toggle', async () => {
    renderWithClient(<QuerySection target="localdev" range="24h" />)
    const writes = /^\s*(UPDATE|INSERT|DELETE)\b/i
    const shown = await allRows()
    expect(
      shown.some((row) =>
        writes.test(row.querySelector('code')?.textContent ?? '')
      )
    ).toBe(false)
    const toggle = screen.getByTestId('writes-toggle')
    const count = Number(toggle.textContent?.match(/\((\d+)\)/)?.[1])
    expect(toggle.textContent).toMatch(/^Show writes \(\d+\)$/)
    expect(count).toBeGreaterThan(0)
    fireEvent.click(toggle)
    expect(screen.getByTestId('writes-toggle').textContent).toBe(
      `Hide writes (${count})`
    )
    const all = await allRows()
    expect(all.length).toBeGreaterThanOrEqual(shown.length + count)
  })

  it('has no multi-select benchmark mode', async () => {
    renderWithClient(<QuerySection target="localdev" range="24h" />)
    await screen.findAllByTestId('monitor-query-row')
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
    const toolbar = screen.getByTestId('query-toolbar')
    expect(
      within(toolbar).queryByRole('button', { name: /Benchmark/ })
    ).toBeNull()
    expect(screen.queryByTestId('benchmark-selection-bar')).toBeNull()
  })

  it('expands a row from a click anywhere on it', async () => {
    renderWithClient(<QuerySection target="localdev" range="24h" />)
    const rows = await screen.findAllByTestId('monitor-query-row')
    const line = (rows[0].querySelector('code') ??
      rows[0].querySelector('[data-testid="row-reason"]')) as HTMLElement
    fireEvent.click(line)
    expect(routerState.navigate).toHaveBeenCalledWith(
      expect.objectContaining({
        search: expect.objectContaining({ query: rows[0].dataset.queryHash }),
      })
    )
  })

  it("turns off Benchmark for queries RDST won't run", async () => {
    const { queriesFixture, FIXTURE_NOW } = await import(
      '@/lib/monitor/fixtures'
    )
    const sleeper = queriesFixture('localdev', '24h', FIXTURE_NOW).queries.find(
      (query) => /pg_sleep/.test(query.text)
    )
    routerState.search = { query: sleeper?.key, tab: 'performance' }
    renderWithClient(<QuerySection target="localdev" range="24h" />)
    const detail = await screen.findByTestId('query-detail')
    const button = within(detail).getByRole('button', { name: 'Benchmark' })
    expect((button as HTMLButtonElement).disabled).toBe(true)
    expect(button.getAttribute('title')).toMatch(/pg_sleep/)
  })

  it('leads the detail with Deep analyze, and says why a write has none', async () => {
    const { queriesFixture, FIXTURE_NOW } = await import(
      '@/lib/monitor/fixtures'
    )
    const queries = queriesFixture('localdev', '24h', FIXTURE_NOW).queries
    const read = queries.find(
      (query) =>
        /^\s*select/i.test(query.text) &&
        !/pg_sleep/.test(query.text) &&
        query.last_analysis?.state !== 'done' &&
        query.analyzable !== false
    )
    routerState.search = { query: read?.key, tab: 'performance' }
    const view = renderWithClient(
      <QuerySection target="localdev" range="24h" />
    )
    let detail = await screen.findByTestId('query-detail')
    const actions = within(detail).getAllByRole('button')
    expect(actions[0]?.textContent).toMatch(
      /Deep analyze this query|View analysis progress/
    )
    // The analysis section comes before the SQL and the classification.
    const regions = within(detail)
      .getAllByRole('region')
      .map((region) => region.getAttribute('aria-label'))
    expect(regions.indexOf('Deep analysis')).toBeLessThan(
      regions.indexOf('SQL')
    )
    view.unmount()

    const write = queries.find((query) =>
      /^\s*(update|insert|delete)/i.test(query.text)
    )
    expect(write).toBeTruthy()
    routerState.search = { query: write?.key, tab: 'performance' }
    renderWithClient(<QuerySection target="localdev" range="24h" />)
    detail = await screen.findByTestId('query-detail')
    expect(
      within(detail).queryByRole('button', { name: 'Deep analyze this query' })
    ).toBeNull()
    expect(within(detail).getByTestId('not-analyzed-line').textContent).toMatch(
      /No deep analysis\..*(change|analyze)/
    )
  })

  it('filters per keystroke without a request', async () => {
    renderWithClient(<QuerySection target="localdev" range="24h" />)
    const before = (await screen.findAllByTestId('monitor-query-row')).length
    fireEvent.change(screen.getByLabelText('Search queries'), {
      target: { value: 'pg_sleep' },
    })
    expect(before).toBeGreaterThan(1)
    await waitFor(
      () => expect(screen.getAllByTestId('monitor-query-row')).toHaveLength(1),
      { timeout: 3000 }
    )
  })

  it('opens the inline detail for a deep-linked query', async () => {
    const { queriesFixture, FIXTURE_NOW } = await import(
      '@/lib/monitor/fixtures'
    )
    const first = queriesFixture('localdev', '24h', FIXTURE_NOW).queries[0]
    routerState.search = { query: first.key, tab: 'performance' }
    renderWithClient(<QuerySection target="localdev" range="24h" />)
    const detail = await screen.findByTestId('query-detail')
    for (const name of ['Latency and calls', 'SQL', 'Deep analysis'])
      expect(within(detail).getByRole('region', { name })).toBeTruthy()
    // Benchmarks shows once there's a run; the Benchmark button starts one.
    expect(
      within(detail).queryByRole('region', { name: 'Benchmarks' })
    ).toBeNull()
    expect(
      within(detail).getByRole('button', { name: 'Benchmark' })
    ).toBeTruthy()
  })

  it('writes filter changes to the URL with replace', async () => {
    renderWithClient(<QuerySection target="localdev" range="24h" />)
    await screen.findAllByTestId('monitor-query-row')
    fireEvent.pointerDown(screen.getByTestId('filter-status'), {
      button: 0,
      ctrlKey: false,
    })
    fireEvent.click(
      await screen.findByRole('menuitem', { name: /^Needs attention/ })
    )
    expect(routerState.navigate).toHaveBeenCalledWith(
      expect.objectContaining({
        replace: true,
        search: expect.objectContaining({ filter: 'attention' }),
      })
    )
  })
})

describe('revealListTop', () => {
  function section(headingTop: number, barBottom: number) {
    const root = document.createElement('section')
    root.innerHTML =
      '<div data-sticky-toolbar></div><h2 data-testid="query-list-sentence"></h2>'
    const rect = (top: number, bottom = top) => ({ top, bottom }) as DOMRect
    ;(root.firstElementChild as HTMLElement).getBoundingClientRect = () =>
      rect(0, barBottom)
    ;(root.lastElementChild as HTMLElement).getBoundingClientRect = () =>
      rect(headingTop)
    document.body.append(root)
    return root
  }

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('scrolls the list heading back under the toolbar when it is above the view', () => {
    const scrollBy = vi.fn()
    window.scrollBy = scrollBy
    revealListTop(section(-900, 150))
    expect(scrollBy).toHaveBeenCalledWith({ top: -1058, behavior: 'smooth' })
  })

  it('leaves the page alone when the heading is already in view', () => {
    const scrollBy = vi.fn()
    window.scrollBy = scrollBy
    revealListTop(section(300, 150))
    expect(scrollBy).not.toHaveBeenCalled()
  })
})
