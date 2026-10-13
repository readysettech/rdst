import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', async () => {
  const { fileRouteModuleMock } = await import('@/test-utils')
  return fileRouteModuleMock({
    redirect: (options: unknown) => ({ redirect: options }),
  })
})
vi.mock('../features/database/DatabasePage', () => ({
  DatabasePage: () => null,
}))

import { Route } from './database'

const beforeLoad = (
  Route as unknown as {
    beforeLoad: (context: { search: Record<string, string> }) => void
  }
).beforeLoad

function thrownBy(search: Record<string, string>): unknown {
  try {
    beforeLoad({ search })
  } catch (error) {
    return error
  }
  return undefined
}

afterEach(() => localStorage.clear())

describe('database route', () => {
  it('sends old Actions tab links to this database on the Actions page', () => {
    expect(thrownBy({ target: 'analytics', view: 'actions' })).toEqual({
      redirect: { to: '/actions', search: { db: 'analytics' }, replace: true },
    })
    expect(localStorage.getItem('rdst_selected_target')).toBe('analytics')
  })

  it('names the selected database for an index plan link without one', () => {
    localStorage.setItem('rdst_selected_target', 'demo')
    expect(thrownBy({ view: 'indexes' })).toEqual({
      redirect: { to: '/actions', search: { db: 'demo' }, replace: true },
    })
  })

  it('keeps Live and Report links on the page', () => {
    expect(thrownBy({ view: 'report' })).toBeUndefined()
    expect(thrownBy({ view: 'live' })).toBeUndefined()
  })
})
