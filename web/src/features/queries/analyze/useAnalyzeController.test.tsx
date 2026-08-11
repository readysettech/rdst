import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  addQuery: vi.fn(),
  refetch: vi.fn(),
  target: 'prod' as string | null,
  lock: {
    isResolved: true,
    isLocked: false,
    targetName: 'prod',
    message: '',
    missingTargetRequirements: [],
    keyringAvailable: true,
  },
  registry: {
    queries: [],
    isLoading: false,
    listError: null as unknown,
  },
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mocks.navigate,
}))

vi.mock('../../../hooks/useTarget', () => ({
  useTarget: () => ({ target: mocks.target }),
}))

vi.mock('../../../lib/useQueryRegistry', () => ({
  useQueryRegistry: () => ({
    ...mocks.registry,
    addQuery: mocks.addQuery,
    refetch: mocks.refetch,
  }),
}))

vi.mock('../../../lib/useTargetPasswordLock', () => ({
  useTargetPasswordLock: () => mocks.lock,
}))

import { useAnalyzeController } from './useAnalyzeController'

const originalScrollIntoView = Element.prototype.scrollIntoView
const originalMatchMedia = window.matchMedia

function queryEntry(
  overrides: Partial<QueryRegistryEntry> = {}
): QueryRegistryEntry {
  return {
    frequency: 1,
    hash: 'query-1',
    last_analyzed: '2026-07-23T00:00:00Z',
    source: 'manual',
    sql: 'SELECT 1',
    tag: '',
    target: 'prod',
    ...overrides,
  }
}

beforeEach(() => {
  mocks.navigate.mockReset()
  mocks.addQuery.mockReset()
  mocks.refetch.mockReset()
  mocks.target = 'prod'
  mocks.lock.isLocked = false
  mocks.lock.targetName = 'prod'
  mocks.registry.queries = []
  mocks.registry.isLoading = false
  mocks.registry.listError = null
  Element.prototype.scrollIntoView = vi.fn()
  window.matchMedia = undefined as unknown as typeof window.matchMedia
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  Element.prototype.scrollIntoView = originalScrollIntoView
  window.matchMedia = originalMatchMedia
})

describe('useAnalyzeController', () => {
  it('saves and hands the exact query context to Results', () => {
    const { result } = renderHook(() => useAnalyzeController())

    act(() => {
      result.current.editor.setValue('  SELECT * FROM users  ')
      result.current.editor.setFast(true)
    })
    act(() => result.current.actions.analyze())

    expect(mocks.addQuery).toHaveBeenCalledWith('SELECT * FROM users', 'prod')
    expect(mocks.navigate).toHaveBeenCalledWith({
      to: '/results',
      search: {
        query: 'SELECT * FROM users',
        target: 'prod',
        fast: true,
      },
    })
  })

  it('does not navigate with blank SQL or a locked target', () => {
    const { result, rerender } = renderHook(() => useAnalyzeController())

    act(() => result.current.actions.analyze())
    expect(mocks.navigate).not.toHaveBeenCalled()

    mocks.lock.isLocked = true
    rerender()
    act(() => {
      result.current.editor.setValue('SELECT 1')
    })
    act(() => result.current.actions.analyze())

    expect(mocks.addQuery).not.toHaveBeenCalled()
    expect(mocks.navigate).not.toHaveBeenCalled()
  })

  it('loads captured parameters, focuses the editor, and confirms the change', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useAnalyzeController())
    const editor = document.createElement('div')
    const content = document.createElement('div')
    content.className = 'cm-content'
    content.tabIndex = -1
    editor.append(content)
    document.body.append(editor)
    result.current.editor.ref.current = editor

    act(() => {
      result.current.actions.selectHistory(
        queryEntry({
          sql: 'SELECT * FROM users WHERE id = $1',
          most_recent_params: { p1: 42 },
        })
      )
    })

    expect(result.current.editor.value).toBe(
      'SELECT * FROM users WHERE id = 42'
    )
    expect(editor.scrollIntoView).toHaveBeenCalledWith({
      behavior: 'smooth',
      block: 'nearest',
    })
    expect(document.activeElement).toBe(content)
    expect(result.current.editor.isHighlighted).toBe(true)

    act(() => vi.advanceTimersByTime(700))
    expect(result.current.editor.isHighlighted).toBe(false)
    editor.remove()
  })

  it('uses instant scrolling when reduced motion is preferred', () => {
    window.matchMedia = ((query: string) =>
      ({
        matches: query.includes('reduce'),
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        onchange: null,
        dispatchEvent: vi.fn(),
      }) as unknown as MediaQueryList) as typeof window.matchMedia

    const { result } = renderHook(() => useAnalyzeController())
    const editor = document.createElement('div')
    result.current.editor.ref.current = editor

    act(() => {
      result.current.actions.selectHistory(queryEntry())
    })

    expect(editor.scrollIntoView).toHaveBeenCalledWith({
      behavior: 'auto',
      block: 'nearest',
    })
    editor.remove()
  })

  it('exposes history loading, error, and retry state', () => {
    mocks.registry.isLoading = true
    mocks.registry.listError = new Error('offline')
    const { result } = renderHook(() => useAnalyzeController())

    expect(result.current.history.isLoading).toBe(true)
    expect(result.current.history.error).toEqual(new Error('offline'))

    act(() => result.current.history.retry())
    expect(mocks.refetch).toHaveBeenCalledTimes(1)
  })
})
