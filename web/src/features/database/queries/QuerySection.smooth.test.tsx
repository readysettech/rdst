// Live updates change rows in place; page and filter changes start with rows closed.
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
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

import { QuerySection } from './QuerySection'

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

function Section({ sticky }: { sticky?: ReactNode }) {
  return (
    <QuerySection
      target="localdev"
      range="24h"
      domain={[0, 4_000_000_000]}
      model={null}
      scopeLabel="in the last 24 hours"
      hoverKey={null}
      onHoverKey={() => undefined}
      stickyChart={sticky}
    />
  )
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

it('keeps every row node through a live refetch, with no skeleton', async () => {
  const { client } = renderWithClient(<Section />)
  const before = new Map(
    (await screen.findAllByTestId('monitor-query-row')).map((row) => [
      row.id,
      row,
    ])
  )
  const reads = client
    .getQueryCache()
    .findAll({ queryKey: ['monitor', 'localdev', 'queries'] })
    .reduce((sum, query) => sum + query.state.dataUpdateCount, 0)
  await client.invalidateQueries({
    queryKey: ['monitor', 'localdev', 'queries'],
  })
  await waitFor(() =>
    expect(
      client
        .getQueryCache()
        .findAll({ queryKey: ['monitor', 'localdev', 'queries'] })
        .reduce((sum, query) => sum + query.state.dataUpdateCount, 0)
    ).toBeGreaterThan(reads)
  )
  expect(screen.queryByTestId('query-card-skeleton')).toBeNull()
  for (const row of screen.getAllByTestId('monitor-query-row'))
    expect(before.get(row.id)).toBe(row)
})

it('closes the open row when the page changes', async () => {
  const { queriesFixture, FIXTURE_NOW } = await import('@/lib/monitor/fixtures')
  const first = queriesFixture('localdev', '24h', FIXTURE_NOW).queries[0]
  routerState.search = { query: first.key, tab: 'performance' }
  renderWithClient(<Section />)
  await screen.findByTestId('query-detail')
  routerState.navigate.mockReset()
  fireEvent.click(screen.getByRole('button', { name: 'Page 2' }))
  expect(routerState.navigate).toHaveBeenCalledWith(
    expect.objectContaining({
      search: expect.not.objectContaining({ query: first.key }),
      resetScroll: false,
    })
  )
})

it('hangs the sticky chart below the bar, out of the page flow', async () => {
  renderWithClient(<Section sticky={<div data-testid="strip" />} />)
  await screen.findAllByTestId('monitor-query-row')
  const holder = screen.getByTestId('strip').parentElement
  expect(holder?.className).toMatch(/\babsolute\b/)
  expect(holder?.className).toMatch(/\btop-full\b/)
})

it('closes the open row when the filter changes', async () => {
  const { queriesFixture, FIXTURE_NOW } = await import('@/lib/monitor/fixtures')
  const first = queriesFixture('localdev', '24h', FIXTURE_NOW).queries[0]
  routerState.search = { query: first.key, tab: 'performance' }
  renderWithClient(<Section />)
  await screen.findByTestId('query-detail')
  fireEvent.pointerDown(screen.getByTestId('filter-status'), {
    button: 0,
    ctrlKey: false,
  })
  fireEvent.click(
    await screen.findByRole('menuitem', { name: /^Needs attention/ })
  )
  const search = routerState.navigate.mock.lastCall?.[0]?.search
  expect(search).toMatchObject({ filter: 'attention' })
  expect(search).not.toHaveProperty('query')
})
