/**
 * Ranked list of slow queries (region C of the redesign — the primary content).
 *
 * Each row is the canonical QueryCard: the full syntax-highlighted SQL leads, and
 * the per-query metrics fold into ONE muted meta line beneath it — `hash · freq ·
 * total · avg · load` historically, swapping to the `seen · max · avg · load · qps`
 * variant in realtime. The footer carries the row actions: Analyze is the one
 * visible per-row action, Cache lives in a `⋯` overflow. The results header carries
 * the count, the Sort lens, the Save-automatically preference and Save all in
 * the page-owned shared header card above this list.
 * [VIS-011, VIS-016, VIS-017, VIS-110, VIS-119, VIS-127, USE-008]
 */

import { m } from '@rs/ui-new/motion'
import { useMemo } from 'react'
import { DataResponse } from '../../../components/data-response/DataResponse'
import {
  QueryListEmptyState,
  QueryListErrorState,
  QueryListSkeleton,
} from '../../../components/query-list-state'
import { formatMeta, shortHash } from '../../../lib/formatters'
import type { TopQuery, TopState } from '../../../types/top'
import { SlowQueryList } from './SlowQueryList'

export interface SlowQueryResultsProps {
  queries: TopQuery[]
  state: TopState
  isRealtime: boolean
  onAnalyze: (query: TopQuery) => void
  onCache?: (query: TopQuery) => void
  cachingHash?: string | null
  isCached?: (registryHash: string) => boolean
  error?: unknown
  onStart?: () => void
  onRetry?: () => void
  /** True when a pattern or threshold filter could be hiding rows. */
  filtersActive?: boolean
  /** Clears those filters and runs again; the way out of an empty result. */
  onClearFilters?: () => void
}

export function SlowQueryResults({
  queries,
  state,
  isRealtime,
  onAnalyze,
  onCache,
  cachingHash,
  isCached,
  error,
  onStart,
  onRetry,
  filtersActive = false,
  onClearFilters,
}: SlowQueryResultsProps) {
  // Per-row view-models: the hash, the max-latency text, the one muted meta line
  // and the cached flag derive only from the row data, the realtime toggle and
  // the cached-SQL set behind `isCached` — never from the parent's transient
  // state (sort menu, auto-save, streaming ticks). Memoized so an unrelated
  // re-render doesn't rebuild meta strings for up to 200 rows. `isCached` is a
  // stable useCallback keyed on the cache set, so listing it as a dep recomputes
  // exactly when a cache op lands, and no more often. [PS5 item 7b]
  const rowViewModels = useMemo(
    () =>
      queries.map((query) => {
        const hash8 = shortHash(query.query_hash)
        const maxText =
          query.max_duration_ms != null
            ? `${query.max_duration_ms.toFixed(1)}ms`
            : query.total_time
        // The whole metric block collapses into one muted stat line under the
        // SQL — labels folded into values, dot-separated, the owner's approved
        // format. Realtime swaps freq→seen, total→max and appends QPS; the
        // "N running" status stays a top badge. [triage §1.1 /top; USE-002/003
        // fold labels, VIS-011 quiet secondary]
        const meta = isRealtime
          ? formatMeta([
              `hash ${hash8}`,
              `seen ${query.observation_count ?? query.freq}`,
              `max ${maxText}`,
              `avg ${query.avg_time}`,
              `load ${query.pct_load}`,
              query.qps != null ? `qps ${query.qps.toFixed(2)}` : null,
            ])
          : formatMeta([
              `hash ${hash8}`,
              `freq ${query.freq}`,
              `total ${query.total_time}`,
              `avg ${query.avg_time}`,
              `load ${query.pct_load}`,
            ])
        return {
          query,
          hasRunning: (query.current_instances_running ?? 0) > 0,
          meta,
          cached: !!isCached?.(query.query_hash),
        }
      }),
    [queries, isRealtime, isCached]
  )

  return (
    <m.section
      aria-label="Slow queries"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.2 }}
    >
      <DataResponse
        data={rowViewModels}
        isLoading={state === 'loading'}
        error={
          state === 'error'
            ? (error ?? new Error('The slow query request failed'))
            : null
        }
        renderLoading={<QueryListSkeleton />}
        renderError={(loadError) => (
          <QueryListErrorState
            title="Slow queries couldn't be loaded"
            message="Readyset couldn't read query statistics from this database."
            trustworthy="Your filters and query library are unchanged."
            error={loadError}
            onRetry={onRetry}
          />
        )}
        renderEmpty={
          <QueryListEmptyState
            icon={state === 'idle' ? 'observe' : 'folder-file'}
            title={
              state === 'idle'
                ? isRealtime
                  ? 'Monitor slow queries live'
                  : 'Find your slowest queries'
                : 'No slow queries matched'
            }
            body={
              state === 'idle'
                ? isRealtime
                  ? 'Watch database traffic and rank slow queries as they run.'
                  : 'Rank the queries putting the most load on this database.'
                : filtersActive
                  ? 'Your filters ruled out every query this run measured.'
                  : 'Wait for more database traffic, then look again.'
            }
            action={
              state === 'idle' && onStart
                ? {
                    label: isRealtime
                      ? 'Start live monitoring'
                      : 'Find slow queries',
                    icon: isRealtime ? 'play' : 'search',
                    onClick: onStart,
                  }
                : // A filtered-out result needs the way back to every row, not
                  // a re-run of the same filters.
                  state !== 'idle' && filtersActive && onClearFilters
                  ? {
                      label: 'Clear filters',
                      icon: 'filter-reset',
                      onClick: onClearFilters,
                    }
                  : undefined
            }
          />
        }
      >
        {(rows) => (
          <SlowQueryList
            rows={rows}
            onAnalyze={onAnalyze}
            onCache={onCache}
            cachingHash={cachingHash}
          />
        )}
      </DataResponse>
    </m.section>
  )
}
