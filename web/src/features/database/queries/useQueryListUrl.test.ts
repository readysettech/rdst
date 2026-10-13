import { renderHook } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { useQueryListUrl } from './useQueryListUrl'

const router = vi.hoisted(() => ({
  navigate: vi.fn(),
  search: {} as Record<string, unknown>,
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => router.navigate,
  useRouterState: ({
    select,
  }: {
    select: (state: { location: { search: unknown } }) => unknown
  }) => select({ location: { search: router.search } }),
}))

it('opens a query and closes the side panel in one write', () => {
  router.search = { view: 'live', panel: 'analyze' }
  const { result } = renderHook(() => useQueryListUrl())
  result.current.open('k1', undefined, { closePanel: true })
  expect(router.navigate).toHaveBeenCalledOnce()
  expect(router.navigate.mock.calls[0][0]).toMatchObject({
    search: { view: 'live', query: 'k1' },
    replace: true,
  })
  expect(router.navigate.mock.calls[0][0].search).not.toHaveProperty('panel')
})
