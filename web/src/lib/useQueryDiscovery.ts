import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'

export type QueryDiscoveryState = 'starting' | 'watching' | 'unavailable'

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

function parseSnapshot(event: MessageEvent<string>) {
  try {
    return JSON.parse(event.data) as QueryDiscoverySnapshot
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
      const snapshot = parseSnapshot(event as MessageEvent<string>)
      if (!snapshot || snapshot.target !== target) return
      cursorByTarget.current.set(target, snapshot.cursor)
      queryClient.setQueryData(queryDiscoveryQueryKey(target), snapshot)
      if (snapshot.state === 'watching') {
        void queryClient.invalidateQueries({ queryKey: ['queryRegistry'] })
      }
    }

    source.addEventListener('discovery_snapshot', receive)
    source.addEventListener('discovery_update', receive)
    source.addEventListener('discovery_error', receive)

    return () => {
      source.removeEventListener('discovery_snapshot', receive)
      source.removeEventListener('discovery_update', receive)
      source.removeEventListener('discovery_error', receive)
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
