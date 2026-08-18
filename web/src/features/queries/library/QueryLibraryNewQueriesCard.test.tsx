import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { QueryLibraryNewQueriesCard } from './QueryLibraryNewQueriesCard'
import type { QueryLibraryController } from './useQueryLibraryController'

vi.mock('../../../components/AnimatedSurfaceBackdrop', () => ({
  AnimatedSurfaceBackdrop: () => null,
}))

function makeController({
  pendingNewCount = 0,
  pendingUpdatedCount = 0,
  newVisibleCount = 0,
  revealPending = vi.fn(),
}: {
  pendingNewCount?: number
  pendingUpdatedCount?: number
  newVisibleCount?: number
  revealPending?: () => void
}) {
  return {
    registry: { markReviewedMutation: { isPending: false } },
    library: {
      pendingNewCount,
      pendingUpdatedCount,
      newVisibleCount,
      revealPending,
      markAllReviewed: vi.fn(),
    },
  } as unknown as QueryLibraryController
}

afterEach(cleanup)

describe('QueryLibraryNewQueriesCard', () => {
  it('labels lifecycle-new pending rows as new and reveals in place', () => {
    const revealPending = vi.fn()
    render(
      <QueryLibraryNewQueriesCard
        controller={makeController({ pendingNewCount: 1, revealPending })}
      />
    )

    expect(screen.getByText('1 new query discovered')).toBeDefined()
    fireEvent.click(screen.getByRole('button', { name: 'Show 1 new query' }))
    expect(revealPending).toHaveBeenCalledOnce()
  })

  it('labels pending rows without lifecycle-new status as updated', () => {
    const revealPending = vi.fn()
    render(
      <QueryLibraryNewQueriesCard
        controller={makeController({ pendingUpdatedCount: 1, revealPending })}
      />
    )

    expect(screen.getByText('1 query updated')).toBeDefined()
    fireEvent.click(
      screen.getByRole('button', { name: 'Show 1 updated query' })
    )
    expect(revealPending).toHaveBeenCalledOnce()
  })

  it('labels a mixed pending batch with both counts', () => {
    render(
      <QueryLibraryNewQueriesCard
        controller={makeController({
          pendingNewCount: 2,
          pendingUpdatedCount: 1,
        })}
      />
    )

    expect(screen.getByText('2 new, 1 updated queries')).toBeDefined()
    expect(
      screen.getByRole('button', { name: 'Show 2 new, 1 updated' })
    ).toBeDefined()
  })

  it('offers review when new rows are already visible', () => {
    render(
      <QueryLibraryNewQueriesCard
        controller={makeController({ newVisibleCount: 3 })}
      />
    )

    expect(screen.getByText('3 new queries ready to review')).toBeDefined()
    expect(
      screen.getByRole('button', { name: 'Mark all reviewed' })
    ).toBeDefined()
  })

  it('renders nothing without pending or reviewable rows', () => {
    const { container } = render(
      <QueryLibraryNewQueriesCard controller={makeController({})} />
    )

    expect(container.firstChild).toBeNull()
  })
})
