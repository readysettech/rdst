import { describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', async () => {
  const { fileRouteModuleMock } = await import('@/test-utils')
  return fileRouteModuleMock()
})
vi.mock('@/features/actions/ActionsPage', () => ({
  ActionsPage: () => null,
}))

import type { ActionsSearch } from './actions'
import { Route } from './actions'

const options = Route as unknown as {
  staticData: { layout: string }
  validateSearch: (search: Record<string, unknown>) => ActionsSearch
  component: unknown
}

describe('actions route', () => {
  it('renders the Actions page in the split layout, beside its side pane', () => {
    expect(options.staticData.layout).toBe('split')
    expect(options.component).toBeTypeOf('function')
  })

  it('keeps the database, the scope, a kind and the grouping', () => {
    expect(
      options.validateSearch({
        db: 'demo',
        scope: 'applied',
        kind: 'index',
        group: 'priority',
      })
    ).toEqual({
      db: 'demo',
      scope: 'applied',
      kind: 'index',
      group: 'priority',
    })
  })

  it('drops what it does not know', () => {
    expect(
      options.validateSearch({ db: '', scope: 'later', target: 'demo', x: 1 })
    ).toEqual({})
  })
})
