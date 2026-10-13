import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type Selection = typeof import('./selectedTarget') &
  typeof import('./targetSelection')
let selection: Selection
const fetchMock = vi.fn(
  async (_url: string, _init?: RequestInit) => new Response('{}')
)

beforeEach(async () => {
  vi.resetModules()
  localStorage.clear()
  fetchMock.mockClear()
  vi.stubGlobal('fetch', fetchMock)
  selection = {
    ...(await import('./selectedTarget')),
    ...(await import('./targetSelection')),
  }
})

afterEach(() => vi.unstubAllGlobals())

const saved = () =>
  fetchMock.mock.calls.map(([, init]) => JSON.parse(String(init?.body)))

describe('selected database', () => {
  it('takes the backend selection on load over the browser cache', () => {
    localStorage.setItem('rdst_selected_target', 'old')
    const changed = vi.fn()
    window.addEventListener('rdst_target_changed', changed)
    selection.hydrateSelectedTarget('prod')
    window.removeEventListener('rdst_target_changed', changed)
    expect(selection.readSelectedTarget()).toBe('prod')
    expect(changed).toHaveBeenCalledTimes(1)
    expect(fetchMock).not.toHaveBeenCalled()
    // Only the first read on load applies.
    selection.hydrateSelectedTarget('other')
    expect(selection.readSelectedTarget()).toBe('prod')
  })

  it('holds a default filled in before the backend answered and drops it if one is saved', () => {
    selection.storeSelectedTarget('default-db', true)
    expect(selection.readSelectedTarget()).toBe('default-db')
    expect(fetchMock).not.toHaveBeenCalled()
    selection.hydrateSelectedTarget('prod')
    expect(selection.readSelectedTarget()).toBe('prod')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('keeps and saves a database a link chose before the backend answered', () => {
    selection.storeSelectedTarget('default-db', true)
    selection.selectGlobalTarget('orders')
    selection.storeSelectedTarget('default-db', true)
    selection.hydrateSelectedTarget('billing')
    expect(selection.readSelectedTarget()).toBe('orders')
    expect(saved()).toEqual([{ selected_target: 'orders' }])
  })

  it('saves the browser selection when the backend has none yet', () => {
    localStorage.setItem('rdst_selected_target', 'analytics')
    selection.hydrateSelectedTarget(null)
    expect(saved()).toEqual([{ selected_target: 'analytics' }])
  })

  it('saves each later change, and clearing it', () => {
    selection.hydrateSelectedTarget('prod')
    selection.selectGlobalTarget(' orders ')
    selection.storeSelectedTarget(null)
    expect(saved()).toEqual([
      { selected_target: 'orders' },
      { selected_target: '' },
    ])
    expect(selection.readSelectedTarget()).toBeNull()
  })

  it('keeps the browser cache when the backend does not keep a selection', () => {
    localStorage.setItem('rdst_selected_target', 'analytics')
    selection.hydrateSelectedTarget(undefined)
    expect(selection.readSelectedTarget()).toBe('analytics')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
