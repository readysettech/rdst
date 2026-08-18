import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { queryRegistryQueryKey } from './useQueryRegistry'

export type QueryDiscoveryState = 'starting' | 'watching' | 'unavailable'

export type QueryDiscoveryStats = {
  collection_duration_ms?: number
  db_fetch_duration_ms?: number
  rows_returned?: number
  new_identity_count?: number
  refreshed_identity_count?: number
  system_skipped?: number
  persistence_ms?: number
  service_reused?: boolean
  deltas_computed?: boolean
  epoch_id?: string
  epoch_changed?: boolean
  incremental?: boolean
}

export type QueryDiscoverySnapshot = {
  cursor: number
  target: string
  state: QueryDiscoveryState
  updated_at: string
  source: string
  engine: string
  query_count: number
  new_hashes: string[]
  error: string | null
  // Absent on older backends; false marks a no-change poll.
  changed?: boolean
  stats?: QueryDiscoveryStats | null
}

export type QueryDiscoveryResync = {
  cursor: number
  reason: 'cursor_not_retained' | 'subscriber_overflow'
  latest_seq: number
  state: Omit<QueryDiscoverySnapshot, 'cursor'>
}

export const queryDiscoveryQueryKey = (target: string) =>
  ['queryDiscovery', target] as const

function initialSnapshot(target: string): QueryDiscoverySnapshot {
  return {
    cursor: 0,
    target,
    state: 'starting',
    updated_at: '',
    source: '',
    engine: '',
    query_count: 0,
    new_hashes: [],
    error: null,
  }
}

function parseEvent<T>(event: MessageEvent<string>): T | null {
  try {
    return JSON.parse(event.data) as T
  } catch {
    return null
  }
}

/**
 * Own the app-wide discovery transport. Mount this once in the root shell;
 * Query Library screens consume the snapshot from React Query instead of
 * opening page-local collectors.
 */
export function useQueryDiscoveryTransport(target?: string | null) {
  const queryClient = useQueryClient()
  const cursorByTarget = useRef(new Map<string, number>())

  useEffect(() => {
    if (!target || typeof EventSource === 'undefined') return

    const params = new URLSearchParams({ target })
    const cursor = cursorByTarget.current.get(target)
    if (cursor != null && cursor > 0) params.set('cursor', String(cursor))

    const source = new EventSource(
      `/api/query-registry/discovery/stream?${params}`
    )

    const receive = (event: Event) => {
      const snapshot = parseEvent<QueryDiscoverySnapshot>(
        event as MessageEvent<string>
      )
      if (!snapshot || snapshot.target !== target) return
      cursorByTarget.current.set(target, snapshot.cursor)
      queryClient.setQueryData(queryDiscoveryQueryKey(target), snapshot)
      // A no-change poll (changed === false) only refreshes the snapshot;
      // refetch registry lists just for this target when a change is signaled
      // or the backend predates the changed flag.
      if (snapshot.state === 'watching' && snapshot.changed !== false) {
        void queryClient.invalidateQueries({
          queryKey: queryRegistryQueryKey(target),
        })
      }
    }

    // The server replays missed events with durable ids; when the cursor is
    // no longer retained it sends a resync on the open stream instead of an
    // error. Adopt its cursor, publish the embedded snapshot, and refetch the
    // registry once since deltas may have been dropped.
    const resync = (event: Event) => {
      const payload = parseEvent<QueryDiscoveryResync>(
        event as MessageEvent<string>
      )
      if (!payload || payload.state?.target !== target) return
      cursorByTarget.current.set(target, payload.cursor)
      queryClient.setQueryData(queryDiscoveryQueryKey(target), {
        ...payload.state,
        cursor: payload.cursor,
      })
      void queryClient.invalidateQueries({
        queryKey: queryRegistryQueryKey(target),
      })
    }

    source.addEventListener('discovery_snapshot', receive)
    source.addEventListener('discovery_update', receive)
    source.addEventListener('discovery_error', receive)
    source.addEventListener('resync', resync)

    return () => {
      source.removeEventListener('discovery_snapshot', receive)
      source.removeEventListener('discovery_update', receive)
      source.removeEventListener('discovery_error', receive)
      source.removeEventListener('resync', resync)
      source.close()
    }
  }, [queryClient, target])
}

export function useQueryDiscoverySnapshot(target?: string | null) {
  return useQuery({
    queryKey: queryDiscoveryQueryKey(target ?? ''),
    queryFn: async () => initialSnapshot(target ?? ''),
    initialData: () => initialSnapshot(target ?? ''),
    enabled: false,
    staleTime: Number.POSITIVE_INFINITY,
  })
}
