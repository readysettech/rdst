import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { dbFilterStorageKey, useDbFilter } from './useDbFilter'

const router = vi.hoisted(() => ({
  search: {} as Record<string, unknown>,
  navigate: vi.fn(),
}))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => router.navigate,
  useRouterState: ({ select }: { select: (state: unknown) => unknown }) =>
    select({ location: { search: router.search } }),
}))

/** The search the last navigate wrote, applied to `current`. */
function lastSearch(current: Record<string, unknown>) {
  const call = router.navigate.mock.calls.at(-1)?.[0] as {
    search: (prev: Record<string, unknown>) => Record<string, unknown>
    replace?: boolean
  }
  return { search: call.search(current), replace: call.replace }
}

afterEach(() => {
  cleanup()
  localStorage.clear()
  router.search = {}
  router.navigate.mockReset()
})

describe('useDbFilter', () => {
  it('is all databases with nothing in the URL or the page memory', () => {
    const { result } = renderHook(() => useDbFilter('inbox'))
    expect(result.current.db).toBeNull()
  })

  it('reads the URL first', () => {
    localStorage.setItem(dbFilterStorageKey('inbox'), 'analytics')
    router.search = { db: 'demo' }
    const { result } = renderHook(() => useDbFilter('inbox'))
    expect(result.current.db).toBe('demo')
  })

  it("falls back to the page's own memory, kept apart from other pages", () => {
    localStorage.setItem('rdst_filter_db:actions', 'analytics')
    expect(renderHook(() => useDbFilter('actions')).result.current.db).toBe(
      'analytics'
    )
    expect(renderHook(() => useDbFilter('home')).result.current.db).toBeNull()
  })

  it('writes a remembered choice to the URL on arrival, replacing the entry', () => {
    localStorage.setItem('rdst_filter_db:actions', 'analytics')
    renderHook(() => useDbFilter('actions'))
    expect(router.navigate).toHaveBeenCalledOnce()
    expect(lastSearch({ tab: 'plan' })).toEqual({
      search: { tab: 'plan', db: 'analytics' },
      replace: true,
    })
    router.navigate.mockReset()
    router.search = { db: 'demo' }
    renderHook(() => useDbFilter('actions'))
    expect(router.navigate).not.toHaveBeenCalled()
  })

  it('writes the URL and the memory, and All databases clears both', () => {
    const { result } = renderHook(() => useDbFilter('ask'))
    act(() => result.current.setDb('demo'))
    expect(localStorage.getItem('rdst_filter_db:ask')).toBe('demo')
    expect(result.current.db).toBe('demo')
    expect(lastSearch({ sql: 'select 1' })).toEqual({
      search: { sql: 'select 1', db: 'demo' },
      replace: true,
    })

    act(() => result.current.setDb(null))
    expect(localStorage.getItem('rdst_filter_db:ask')).toBeNull()
    expect(result.current.db).toBeNull()
    expect(lastSearch({ sql: 'select 1', db: 'demo' }).search).toEqual({
      sql: 'select 1',
    })
  })
})
