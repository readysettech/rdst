import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Radix Dialog focuses content on open and may call scrollIntoView; jsdom lacks
// it. Install a fake so opening the modal never throws.
const originalScrollIntoView = Element.prototype.scrollIntoView

// CachePage now lives in the route-ignored `-cache-page` sibling and takes
// `pendingQuery` as a prop (the route wrapper in `cache.tsx` owns `Route` /
// `validateSearch`), so the sibling only pulls `Link` from the router here.
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => (
    <a href={to}>{children}</a>
  ),
}))

// Deployed + running cache with an empty list → the "State 2/3 empty" view, which
// carries both the header "Add cache" trigger and the empty-state CTA.
vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    if (queryKey[0] === 'cache-status')
      return {
        data: {
          deployed: true,
          running: true,
          endpoint: 'postgresql://localhost:5433/app',
        },
        isLoading: false,
      }
    if (queryKey[0] === 'cache-list')
      return { data: { caches: [] }, isLoading: false }
    return { data: undefined, isLoading: false }
  },
  useMutation: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    isSuccess: false,
    error: null,
    reset: vi.fn(),
    variables: undefined,
  }),
}))

vi.mock('../hooks/useTarget', () => ({ useTarget: () => ({ target: 'prod' }) }))

vi.mock('../lib/useTargetPasswordLock', () => ({
  useTargetPasswordLock: () => ({
    isLocked: false,
    message: '',
    missingTargetRequirements: [],
    keyringAvailable: true,
  }),
}))

const idleFlow = {
  state: 'idle' as const,
  progress: undefined,
  result: undefined,
  error: undefined,
  reset: vi.fn(),
}

vi.mock('../lib/useCache', () => ({
  addCacheQuery: vi.fn(),
  cacheLifecycle: vi.fn(),
  deleteCacheQuery: vi.fn(),
  dropAllCacheQueries: vi.fn(),
  fetchCacheList: vi.fn(),
  fetchCacheStatus: vi.fn(),
  removeCacheTarget: vi.fn(),
  useCacheDeploy: () => ({ deploy: vi.fn(), cancel: vi.fn(), ...idleFlow }),
  useCacheRun: () => ({ run: vi.fn(), ...idleFlow }),
}))

vi.mock('../components', () => ({ TargetLockNotice: () => null }))
vi.mock('../components/HandRaiser', () => ({ HandRaiser: () => null }))
vi.mock('../components/top', () => ({
  hasParameters: () => false,
  ParameterDialog: () => null,
}))

// Replace the CodeMirror-backed editor with a plain textarea so the modal body
// mounts cheaply while keeping the same value/onChange contract.
vi.mock('../components/SQLInput', () => ({
  SQLInput: ({
    value,
    onChange,
    placeholder,
  }: {
    value: string
    onChange: (v: string) => void
    placeholder?: string
  }) => (
    <textarea
      data-testid="cache-sql-input"
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}))

import { CachePage } from './-cache-page'

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => {
  cleanup()
  Element.prototype.scrollIntoView = originalScrollIntoView
})

function addCacheTriggers() {
  return screen.getAllByRole('button', { name: /Add cache/ })
}

describe('cache Add-Cache modal', () => {
  it('keeps the add flow out of the page until a trigger is pressed', () => {
    render(<CachePage />)
    // The SQL editor is not on the page — it lives in the (closed) modal.
    expect(screen.queryByTestId('cache-sql-input')).toBeNull()
    // Both a header trigger and an empty-state CTA offer to add.
    expect(addCacheTriggers().length).toBeGreaterThanOrEqual(2)
  })

  it('opens the modal from the header trigger and closes it via Cancel', () => {
    render(<CachePage />)
    fireEvent.click(addCacheTriggers()[0])

    // Modal is open: editor + the Check & Cache action are present.
    expect(screen.getByTestId('cache-sql-input')).not.toBeNull()
    expect(screen.getByRole('button', { name: /Check & Cache/ })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /Cancel/ }))
    expect(screen.queryByTestId('cache-sql-input')).toBeNull()
  })

  it('also opens the modal from the empty-state CTA', () => {
    render(<CachePage />)
    // Second trigger is the empty-state CTA.
    const triggers = addCacheTriggers()
    fireEvent.click(triggers[triggers.length - 1])
    expect(screen.getByTestId('cache-sql-input')).not.toBeNull()
  })

  it('disables Check & Cache until SQL is entered, enables once typed', () => {
    render(<CachePage />)
    fireEvent.click(addCacheTriggers()[0])

    const submit = screen.getByRole('button', {
      name: /Check & Cache/,
    }) as HTMLButtonElement
    expect(submit.disabled).toBe(true)

    fireEvent.change(screen.getByTestId('cache-sql-input'), {
      target: { value: 'SELECT 1' },
    })
    expect(submit.disabled).toBe(false)
  })
})
