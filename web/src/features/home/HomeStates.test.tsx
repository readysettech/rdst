import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from '@tanstack/react-router'
import { act, cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { AddDatabaseHost } from '../../components/configure/AddDatabaseDialog'
import { caFixtures } from '../../lib/monitor/fixturesCa'
import { useConfigure } from '../../lib/useConfigure'
import { useSystemStatus } from '../../lib/useSystemStatus'
import { HomeEmpty, homeCaption } from './HomeStates'

vi.mock('../../lib/useConfigure', () => ({ useConfigure: vi.fn() }))
vi.mock('../../lib/useSystemStatus', () => ({ useSystemStatus: vi.fn() }))

afterEach(cleanup)

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
  it('says there is no database yet, with one action and what follows', async () => {
    await renderHome()

    expect(await screen.findByText('No database yet.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Add a database' })).toBeTruthy()
    expect(screen.getByText('After you add one')).toBeTruthy()
    expect(screen.getByText('as soon as it connects')).toBeTruthy()
    expect(screen.getByText('at 12 AM')).toBeTruthy()
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
