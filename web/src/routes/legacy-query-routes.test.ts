import { beforeEach, describe, expect, it, vi } from 'vitest'

const redirectSpy = vi.hoisted(() =>
  vi.fn((options: unknown) => {
    throw options
  })
)
const toastSpy = vi.hoisted(() => vi.fn())

vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (options: unknown) => ({ options }),
  redirect: redirectSpy,
}))

vi.mock('@rs/ui-new/use-toast', () => ({ toast: toastSpy }))

import { Route as AnalyzeRoute } from './analyze'
import { Route as QueryRegistryRoute } from './query-registry'
import { Route as TopRoute } from './top'

type BeforeLoad<TSearch = never> = (context: { search: TSearch }) => never

function expectRedirect(beforeLoad: () => never, expected: unknown) {
  expect(beforeLoad).toThrow()
  expect(redirectSpy).toHaveBeenLastCalledWith(expected)
}

beforeEach(() => {
  redirectSpy.mockClear()
  toastSpy.mockClear()
})

describe('legacy query route redirects', () => {
  it('redirects /top to the Query Library sorted by slowest average', () => {
    const beforeLoad = TopRoute.options.beforeLoad as BeforeLoad

    expectRedirect(() => beforeLoad({ search: undefined as never }), {
      to: '/queries',
      search: { sort: 'slowest-average' },
    })
  })

  it('tells a /top bookmark where the view went', () => {
    const beforeLoad = TopRoute.options.beforeLoad as BeforeLoad

    expect(() => beforeLoad({ search: undefined as never })).toThrow()
    expect(toastSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Top queries now live in the Query Library',
      })
    )
  })

  it('redirects /analyze to the unified Queries library', () => {
    const beforeLoad = AnalyzeRoute.options.beforeLoad as BeforeLoad

    expectRedirect(() => beforeLoad({ search: undefined as never }), {
      to: '/queries',
    })
  })

  it('preserves query-registry deep links without adding a filter', () => {
    const validateSearch = QueryRegistryRoute.options.validateSearch as (
      search: Record<string, unknown>
    ) => { hash?: string; run?: string }
    const search = validateSearch({ hash: 'query-hash', run: 'run-id' })
    const beforeLoad = QueryRegistryRoute.options.beforeLoad as BeforeLoad<
      typeof search
    >

    expectRedirect(() => beforeLoad({ search }), {
      to: '/queries',
      search: {
        hash: 'query-hash',
        run: 'run-id',
      },
    })
  })
})
