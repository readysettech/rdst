import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from '@tanstack/react-router'
import { act, cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { AddDatabaseHost } from '../../components/configure/AddDatabaseDialog'
import { scopedKey } from '../../lib/installationScope'
import { caFixtures } from '../../lib/monitor/fixturesCa'
import { useConfigure } from '../../lib/useConfigure'
import { useSystemStatus } from '../../lib/useSystemStatus'
import { HomeEmpty, homeCaption } from './HomeStates'

vi.mock('../../lib/useConfigure', () => ({ useConfigure: vi.fn() }))
vi.mock('../../lib/useSystemStatus', () => ({ useSystemStatus: vi.fn() }))

afterEach(cleanup)
// The add-database pop-up opens once per session; most tests start after it has.
beforeEach(() =>
  sessionStorage.setItem(scopedKey('rdst_get_started_add-database'), '1')
)
afterEach(() => sessionStorage.clear())

async function renderHome(path = '/') {
  vi.mocked(useConfigure).mockReturnValue({} as never)
  vi.mocked(useSystemStatus).mockReturnValue({
    data: { targets: [] },
  } as never)
  const root = createRootRoute({
    component: () => (
      <>
        <AddDatabaseHost />
        <Outlet />
      </>
    ),
  })
  const home = createRoute({
    getParentRoute: () => root,
    path: '/',
    component: HomeEmpty,
  })
  const router = createRouter({
    routeTree: root.addChildren([home]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  await act(() => router.load())
  renderWithClient(<RouterProvider router={router} />)
  return router
}

describe('HomeEmpty', () => {
  it('pops the add-database dialog up once per session', async () => {
    sessionStorage.clear()
    // The first-run tour comes first on a new install; this install has seen it.
    localStorage.setItem(scopedKey('rdst_onboarding_tour_done'), '1')
    const router = await renderHome()

    expect(await screen.findByLabelText('Connection string')).toBeTruthy()
    expect(router.state.location.search).toEqual({ add: 1 })
    expect(
      sessionStorage.getItem(scopedKey('rdst_get_started_add-database'))
    ).toBe('1')
  })

  it('starts on connecting a database, with signing in last and optional', async () => {
    await renderHome()

    expect(await screen.findByText('Connect your database')).toBeTruthy()
    const steps = screen.getAllByRole('listitem')
    expect(steps.map((step) => step.getAttribute('aria-current'))).toEqual([
      'step',
      null,
      null,
    ])
    expect(steps[0].querySelector('svg')).toBeNull()
    expect(steps[1].textContent).toContain('Turn on monitoring')
    expect(steps[2].textContent).toContain('Sign in to Readyset')
    expect(steps[2].textContent).toContain('AI works without an account.')
    expect(screen.getByRole('button', { name: 'Add a database' })).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/Signed in as/)
  })

  it('opens the add-database dialog over Home', async () => {
    const router = await renderHome()

    fireEvent.click(
      await screen.findByRole('button', { name: 'Add a database' })
    )

    expect(await screen.findByLabelText('Connection string')).toBeTruthy()
    expect(router.state.location.pathname).toBe('/')
    expect(router.state.location.search).toEqual({ add: 1 })
  })

  it('drops the request from the URL when the dialog closes', async () => {
    const router = await renderHome('/?add=1')

    expect(await screen.findByLabelText('Connection string')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Close modal' }))

    await vi.waitFor(() => expect(router.state.location.search).toEqual({}))
  })
})

describe('homeCaption', () => {
  const status = () => caFixtures('ca-home').home().status

  it('counts the watched databases out of every configured one', () => {
    expect(homeCaption(status(), 3)).toBe(
      'Watching 2 of 3 databases. Last reading 9:56:30. Next reports at 12 AM.'
    )
    expect(homeCaption(status(), 2)).toBe(
      'Watching 2 databases. Last reading 9:56:30. Next reports at 12 AM.'
    )
    expect(homeCaption({ ...status(), watching: 1 }, 1)).toBe(
      'Watching 1 database. Last reading 9:56:30. Next report at 12 AM.'
    )
  })
})
