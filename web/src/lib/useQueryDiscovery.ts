import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { subscribeShared } from './sse/sharedEventSource'
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
  capture_warning?: {
    source: string
    instructions: string[]
  } | null
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
    capture_warning: null,
    error: null,
  }
}

function parse<T>(raw: string): T | null {
  try {
    return JSON.parse(raw) as T
  } catch {
    return null
  }
}

/**
 * Own the app-wide discovery transport. Mount this once in the root shell;
 * Query Library screens consume the snapshot from React Query instead of
 * opening page-local collectors. The stream is shared by every open tab (one
 * connection per target), so tabs never starve the browser's connection pool.
 */
export function useQueryDiscoveryTransport(target?: string | null) {
  const queryClient = useQueryClient()

  useEffect(() => {
    if (!target) return
    const params = new URLSearchParams({ target })

    const queryKey = queryRegistryQueryKey(target)
    let lastRefresh = Number.NEGATIVE_INFINITY
    let timer: ReturnType<typeof setTimeout> | undefined
    const refresh = () => {
      timer = undefined
      lastRefresh = Date.now()
      void queryClient.invalidateQueries({ queryKey })
    }
    // Classification can emit an event for each row. Keep a trailing refresh
    // so bursts update the list at most once every five seconds.
    const scheduleRefresh = () => {
      if (timer !== undefined) return
      const updatedAt = queryClient
        .getQueryCache()
        .findAll({ queryKey })
        .reduce(
          (latest, query) => Math.max(latest, query.state.dataUpdatedAt),
          0
        )
      const delay = Math.max(
        0,
        5_000 - (Date.now() - Math.max(lastRefresh, updatedAt))
      )
      if (delay === 0) refresh()
      else timer = setTimeout(refresh, delay)
    }

    const receive = (raw: string) => {
      const snapshot = parse<QueryDiscoverySnapshot>(raw)
      if (!snapshot || snapshot.target !== target) return
      queryClient.setQueryData(queryDiscoveryQueryKey(target), snapshot)
      // A no-change poll (changed === false) only refreshes the snapshot;
      // refetch registry lists just for this target when a change is signaled
      // or the backend predates the changed flag.
      if (snapshot.state === 'watching' && snapshot.changed !== false) {
        scheduleRefresh()
      }
    }

    // When the server can't replay missed events it sends a resync with the
    // current state; publish it and refetch the registry once since deltas may
    // have been dropped.
    const resync = (raw: string) => {
      const payload = parse<QueryDiscoveryResync>(raw)
      if (!payload || payload.state?.target !== target) return
      queryClient.setQueryData(queryDiscoveryQueryKey(target), {
        ...payload.state,
        cursor: payload.cursor,
      })
      scheduleRefresh()
    }

    const unsubscribe = subscribeShared(
      `/api/query-registry/discovery/stream?${params}`,
      [
        'discovery_snapshot',
        'discovery_update',
        'discovery_error',
        'assessment_update',
        'resync',
      ],
      (name, raw) => {
        if (name === 'resync') resync(raw)
        else if (name === 'assessment_update') scheduleRefresh()
        else receive(raw)
      }
    )
    return () => {
      if (timer !== undefined) clearTimeout(timer)
      unsubscribe()
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
