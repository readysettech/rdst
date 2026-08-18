import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { QueryRegistryFreshness } from '../../../lib/api'
import type { QueryDiscoverySnapshot } from '../../../lib/useQueryDiscovery'
import {
  DISCOVERY_BACKOFF_NOTE,
  DISCOVERY_CADENCE_NOTE,
  DISCOVERY_COVERAGE_NOTE,
  QueryLibraryDiscoveryStatus,
} from './QueryLibraryDiscoveryStatus'
import type { QueryLibraryController } from './useQueryLibraryController'

afterEach(cleanup)

function snapshot(
  overrides: Partial<QueryDiscoverySnapshot> = {}
): QueryDiscoverySnapshot {
  return {
    cursor: 1,
    target: 'demo',
    state: 'watching',
    updated_at: new Date().toISOString(),
    source: 'top',
    engine: 'postgres',
    query_count: 57,
    new_hashes: [],
    error: null,
    ...overrides,
  }
}

function makeController(
  discovery: QueryDiscoverySnapshot,
  freshness: QueryRegistryFreshness | null = null
) {
  return {
    target: 'demo',
    library: { discovery, freshness },
  } as unknown as QueryLibraryController
}

function openDisclosure() {
  fireEvent.click(screen.getByRole('button', { name: /discovery details/i }))
}

describe('QueryLibraryDiscoveryStatus', () => {
  it('discloses the last check stats from the live snapshot', () => {
    render(
      <QueryLibraryDiscoveryStatus
        controller={makeController(
          snapshot({
            stats: {
              collection_duration_ms: 142.4,
              rows_returned: 57,
              new_identity_count: 2,
              system_skipped: 3,
              incremental: true,
            },
          })
        )}
      />
    )

    expect(screen.getByText('Watching demo')).toBeTruthy()
    openDisclosure()
    expect(
      screen.getByText(
        'Last check: 142 ms, scanned 57 database statement records, 2 new, 3 system statements excluded'
      )
    ).toBeTruthy()
    expect(screen.getByText('Incremental check')).toBeTruthy()
    expect(screen.getByText(DISCOVERY_CADENCE_NOTE)).toBeTruthy()
    expect(screen.getByText('Last successful check just now')).toBeTruthy()
    expect(screen.getByText(DISCOVERY_COVERAGE_NOTE)).toBeTruthy()
  })

  it('labels a non-incremental pass as a full sweep', () => {
    render(
      <QueryLibraryDiscoveryStatus
        controller={makeController(
          snapshot({ stats: { rows_returned: 5, incremental: false } })
        )}
      />
    )

    openDisclosure()
    expect(
      screen.getByText('Last check: scanned 5 database statement records')
    ).toBeTruthy()
    expect(screen.getByText('Full sweep')).toBeTruthy()
  })

  it('falls back to read-model freshness before the stream connects', () => {
    const twoMinutesAgo = new Date(Date.now() - 2 * 60 * 1000).toISOString()
    render(
      <QueryLibraryDiscoveryStatus
        controller={makeController(
          snapshot({ state: 'starting', updated_at: '' }),
          { state: 'watching', last_success_at: twoMinutesAgo, epoch_id: 'e1' }
        )}
      />
    )

    expect(screen.getByText('Starting discovery')).toBeTruthy()
    openDisclosure()
    expect(screen.getByText('Last successful check 2m ago')).toBeTruthy()
  })

  it('reports no successful check when neither source has one', () => {
    render(
      <QueryLibraryDiscoveryStatus
        controller={makeController(
          snapshot({ state: 'starting', updated_at: '' })
        )}
      />
    )

    openDisclosure()
    expect(screen.getByText('No successful check yet')).toBeTruthy()
  })

  it('shows the error and the backoff note when discovery is unavailable', () => {
    render(
      <QueryLibraryDiscoveryStatus
        controller={makeController(
          snapshot({
            state: 'unavailable',
            error: 'connection to demo refused',
          })
        )}
      />
    )

    expect(screen.getByText('Discovery unavailable')).toBeTruthy()
    openDisclosure()
    expect(screen.getByText('connection to demo refused')).toBeTruthy()
    expect(screen.getByText(DISCOVERY_BACKOFF_NOTE)).toBeTruthy()
    expect(screen.getByText(DISCOVERY_COVERAGE_NOTE)).toBeTruthy()
    expect(screen.queryByText(DISCOVERY_CADENCE_NOTE)).toBeNull()
  })

  it('keeps the relative last-check label ticking while open', () => {
    vi.useFakeTimers()
    try {
      render(
        <QueryLibraryDiscoveryStatus
          controller={makeController(
            snapshot({ updated_at: new Date().toISOString() })
          )}
        />
      )

      openDisclosure()
      expect(screen.getByText('Last successful check just now')).toBeTruthy()

      act(() => {
        vi.advanceTimersByTime(2.5 * 60 * 1000)
      })
      expect(screen.getByText('Last successful check 2m ago')).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })
})
