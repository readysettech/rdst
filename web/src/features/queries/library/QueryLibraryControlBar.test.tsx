import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { QueryLibraryControlBar } from './QueryLibraryControlBar'
import { QUERY_LIBRARY_DEFAULT_DISPLAY_PROPERTIES } from './queryLibraryDisplay'

// jsdom has no PointerEvent constructor, but Radix's DropdownMenu trigger
// opens on pointerdown (not click). A thin MouseEvent-based polyfill gives
// fireEvent.pointerDown a real `button`/`ctrlKey` so the menu actually opens.
class PointerEventPolyfill extends MouseEvent {
  pointerId: number
  pointerType: string
  isPrimary: boolean
  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init)
    this.pointerId = init.pointerId ?? 0
    this.pointerType = init.pointerType ?? 'mouse'
    this.isPrimary = init.isPrimary ?? true
  }
}
if (typeof window.PointerEvent === 'undefined') {
  // @ts-expect-error test-only polyfill, narrower than the DOM lib type
  window.PointerEvent = PointerEventPolyfill
}

function openDropdown(trigger: HTMLElement) {
  fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false })
}

function renderControlBar(
  overrides: Partial<Parameters<typeof QueryLibraryControlBar>[0]> = {}
) {
  return render(
    <QueryLibraryControlBar
      idPrefix="test"
      searchTerm=""
      onSearchChange={vi.fn()}
      filters={{
        view: 'all',
        source: 'all',
        params: 'all',
        activity: 'all',
        impact: 'all',
      }}
      onFilterChange={vi.fn()}
      onClearFilter={vi.fn()}
      onClearFilters={vi.fn()}
      sort="highest-impact"
      onSortChange={vi.fn()}
      starred={false}
      onStarredChange={vi.fn()}
      displayMode="card-2"
      onDisplayModeChange={vi.fn()}
      properties={QUERY_LIBRARY_DEFAULT_DISPLAY_PROPERTIES}
      onToggleProperty={vi.fn()}
      selection={{
        facetCounts: {
          view: {
            all: 0,
            new: 0,
            'high-impact': 0,
            'needs-analysis': 0,
            'ready-to-cache': 0,
            cached: 0,
          },
          source: { all: 0, observed: 0, ask: 0, manual: 0, file: 0, scan: 0 },
          params: {
            all: 0,
            'without-parameters': 0,
            'values-ready': 0,
            'values-needed': 0,
          },
          activity: {
            all: 0,
            '1m': 0,
            '1h': 0,
            '8h': 0,
            '24h': 0,
            '7d': 0,
            '30d': 0,
          },
          impact: { all: 0, '1m': 0, '10m': 0, '1h': 0 },
        },
      }}
      {...overrides}
    />
  )
}

afterEach(cleanup)

describe('Queries Display menu', () => {
  it('offers only Card 1 and Card 2, hiding the rows/list view', () => {
    renderControlBar()

    openDropdown(screen.getByRole('button', { name: 'Display' }))

    expect(screen.getByRole('button', { name: 'Card 1' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Card 2' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'List' })).toBeNull()
  })

  it('calls onDisplayModeChange with the selected card variant', () => {
    const onDisplayModeChange = vi.fn()
    renderControlBar({ onDisplayModeChange })

    openDropdown(screen.getByRole('button', { name: 'Display' }))
    fireEvent.click(screen.getByRole('button', { name: 'Card 1' }))

    expect(onDisplayModeChange).toHaveBeenCalledWith('card-1')
  })
})

describe('Queries Starred filter', () => {
  it('lives inside Filter, separate from the computed statuses', () => {
    renderControlBar()

    // Nothing outside the panel: the star is a filter, and filters live there.
    expect(screen.queryByRole('button', { name: 'Starred only' })).toBeNull()

    openDropdown(screen.getByRole('button', { name: 'Filter' }))

    const starred = screen.getByRole('button', { name: 'Starred only' })
    expect(starred.getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByText('Status')).toBeTruthy()
    // The mark is the user's own, so it never became a computed status value.
    expect(screen.queryByText(/Saved/)).toBeNull()
  })

  it('asks for the shortlist without disturbing the active status', () => {
    const onStarredChange = vi.fn()
    const onFilterChange = vi.fn()
    renderControlBar({
      onStarredChange,
      onFilterChange,
      filters: {
        view: 'needs-analysis',
        source: 'all',
        params: 'all',
        activity: 'all',
        impact: 'all',
      },
    })

    openDropdown(screen.getByRole('button', { name: 'Filter' }))
    fireEvent.click(screen.getByRole('button', { name: 'Starred only' }))

    expect(onStarredChange).toHaveBeenCalledWith(true)
    expect(onFilterChange).not.toHaveBeenCalled()
  })

  it('stays visible as a removable chip while the panel is closed', () => {
    const onStarredChange = vi.fn()
    renderControlBar({ starred: true, onStarredChange })

    fireEvent.click(
      screen.getByRole('button', { name: 'Remove Starred filter' })
    )
    expect(onStarredChange).toHaveBeenCalledWith(false)
  })

  it('counts the star among the filters the panel holds', () => {
    renderControlBar({ starred: true })

    const filter = screen.getByRole('button', { name: 'Filter' })
    expect(filter.textContent).toContain('1')

    openDropdown(filter)
    expect(
      screen
        .getByRole('button', { name: 'Starred only' })
        .getAttribute('aria-pressed')
    ).toBe('true')
  })
})
