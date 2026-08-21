import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const navigateSpy = vi.fn()

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual('@tanstack/react-router')
  return {
    ...actual,
    useNavigate: () => navigateSpy,
  }
})

vi.mock('../features/caching/compare/ComparePage', () => ({
  ComparePage: ({ initialQueryHash }: { initialQueryHash?: string }) => (
    <div
      data-testid="compare-page"
      data-initial-query-hash={initialQueryHash}
    />
  ),
}))

vi.mock('./-benchmark-page', () => ({
  BenchmarkPage: ({ selectedRunId }: { selectedRunId?: string }) => (
    <div data-testid="load-test-page" data-run-id={selectedRunId} />
  ),
}))

import { CachingPage } from './-caching-page'
import { Route } from './cache'

afterEach(() => {
  cleanup()
  navigateSpy.mockClear()
})

describe('cache route validateSearch', () => {
  const validate = Route.options.validateSearch as (
    s: Record<string, unknown>
  ) => { view?: string; hash?: string; run?: string }

  it('passes a known view through unchanged', () => {
    expect(validate({ view: 'compare' })).toEqual({
      view: 'compare',
      hash: undefined,
      run: undefined,
    })
    expect(validate({ view: 'caches' })).toEqual({
      view: 'quick',
      hash: undefined,
      run: undefined,
    })
  })

  it('normalizes legacy benchmark links to Load test', () => {
    expect(validate({ view: 'benchmark' })).toEqual({
      view: 'load-test',
      hash: undefined,
      run: undefined,
    })
  })

  it('redirects legacy Quick test links to the owning query', () => {
    const beforeLoad = Route.options.beforeLoad as (context: {
      search: { view?: string; hash?: string }
    }) => void
    let thrown: unknown

    try {
      beforeLoad({ search: { view: 'quick', hash: 'query-1' } })
    } catch (error) {
      thrown = error
    }

    expect(thrown).toMatchObject({
      options: {
        to: '/queries',
        search: { hash: 'query-1' },
        replace: true,
      },
    })
  })

  it('drops an unknown view', () => {
    expect(validate({ view: 'bogus' })).toEqual({
      view: undefined,
      hash: undefined,
      run: undefined,
    })
    expect(validate({})).toEqual({
      view: undefined,
      hash: undefined,
      run: undefined,
    })
  })
})

describe('CachingPage workspace', () => {
  it('renders only the two dedicated performance-test modes', () => {
    render(<CachingPage view="compare" />)
    expect(screen.getByRole('heading', { name: 'Benchmarks' })).toBeTruthy()
    expect(screen.queryByRole('tab', { name: 'Quick test' })).toBeNull()
    expect(screen.getByRole('tab', { name: 'Compare' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'Load test' })).toBeTruthy()
    expect(screen.getByTestId('compare-page')).toBeTruthy()
  })

  it('renders Compare as a distinct mode', () => {
    render(<CachingPage view="compare" />)
    expect(screen.getByTestId('compare-page')).toBeTruthy()
  })

  it('uses a hash deep link to preselect a comparison query', () => {
    render(<CachingPage view="compare" deepLinkHash="quick-query" />)
    expect(
      screen.getByTestId('compare-page').getAttribute('data-initial-query-hash')
    ).toBe('quick-query')
  })

  it('passes a background run into Load test', () => {
    render(<CachingPage view="load-test" selectedRunId="load-run" />)
    expect(
      screen.getByTestId('load-test-page').getAttribute('data-run-id')
    ).toBe('load-run')
  })
})
