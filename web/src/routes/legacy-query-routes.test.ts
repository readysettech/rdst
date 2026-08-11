import { beforeEach, describe, expect, it, vi } from 'vitest'

const redirectSpy = vi.hoisted(() =>
  vi.fn((options: unknown) => {
    throw options
  })
)

vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (options: unknown) => ({ options }),
  redirect: redirectSpy,
}))

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
})

describe('legacy query route redirects', () => {
  it('redirects /top to the high-impact Query Library view', () => {
    const beforeLoad = TopRoute.options.beforeLoad as BeforeLoad

    expectRedirect(() => beforeLoad({ search: undefined as never }), {
      to: '/queries',
      search: { view: 'high-impact' },
    })
  })

  it('redirects /analyze to the unified Queries library', () => {
    const beforeLoad = AnalyzeRoute.options.beforeLoad as BeforeLoad

    expectRedirect(() => beforeLoad({ search: undefined as never }), {
      to: '/queries',
    })
  })

  it('preserves query-registry hash and run deep links in Queries', () => {
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
        view: 'saved',
        hash: 'query-hash',
        run: 'run-id',
      },
    })
  })
})
