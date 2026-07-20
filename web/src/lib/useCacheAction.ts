/**
 * Shared hook + component for the "Cache" button used across
 * Top Queries, Query Registry, Scan Results, and Analyze Results.
 *
 * Handles: deploy (if needed) → dry-run check → create cache → toast feedback.
 * Checks the backend cache list to persist "Cached" state across refreshes.
 */

import { useState, useCallback, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from '@rs/ui-new/use-toast';
import { cachedRegistryHashes, deployAndCache, fetchCacheList } from './useCache';

interface UseCacheActionOptions {
  target: string | null;
  /** Fired after a cache is successfully created, e.g. to chain a perf test. */
  onCached?: (sql: string, id: string) => void;
}

interface UseCacheActionReturn {
  /** Trigger cache for a query. Handles deploy + create + toast. */
  cacheQuery: (sql: string, id: string) => void;
  /** Hash currently being cached (loading state). */
  cachingId: string | null;
  /** Check whether a query (by its registry hash) is already cached. */
  isCached: (registryHash: string) => boolean;
  /** Whether a cache operation is in progress. */
  isPending: boolean;
}

export function useCacheAction({ target, onCached }: UseCacheActionOptions): UseCacheActionReturn {
  const queryClient = useQueryClient();
  const [cachingId, setCachingId] = useState<string | null>(null);
  // Session-level cache for immediate feedback before the list refetches
  const [sessionCached, setSessionCached] = useState<Record<string, true>>({});

  // Fetch the actual cache list from backend
  const { data: cacheList } = useQuery({
    queryKey: ['cache-list', target],
    queryFn: () => fetchCacheList(target!),
    enabled: !!target,
    staleTime: 30_000,
  });

  // Cached-ness is matched by registry hash (see cachedRegistryHashes), not
  // re-derived from SQL text which cannot see through ReadySet's parameter
  // rewriting. Clear the optimistic session cache once the authoritative list
  // arrives.
  const cachedHashSet = useMemo(() => {
    if (cacheList?.caches) setSessionCached({});
    return cachedRegistryHashes(cacheList);
  }, [cacheList]);

  const isCached = useCallback(
    (registryHash: string): boolean =>
      cachedHashSet.has(registryHash) || !!sessionCached[registryHash],
    [cachedHashSet, sessionCached],
  );

  const mutation = useMutation({
    mutationFn: async ({ sql }: { sql: string; id: string }) => {
      return deployAndCache(target!, sql);
    },
    onSuccess: (_data, { sql, id }) => {
      setCachingId(null);
      setSessionCached((prev) => ({ ...prev, [id]: true }));
      queryClient.invalidateQueries({ queryKey: ['status'] });
      queryClient.invalidateQueries({ queryKey: ['cache-status', target] });
      queryClient.invalidateQueries({ queryKey: ['cache-list', target] });
      toast({
        title: 'Query cached',
        description: 'Query is now served from ReadySet cache.',
        variant: 'positive',
      });
      onCached?.(sql, id);
    },
    onError: (err: Error) => {
      setCachingId(null);
      toast({
        title: 'Cache failed',
        description: `${err.message}. Go to Cache page to troubleshoot.`,
        variant: 'negative',
      });
    },
  });

  const cacheQuery = useCallback(
    (sql: string, id: string) => {
      if (!target) return;
      setCachingId(id);
      mutation.mutate({ sql, id });
    },
    [target, mutation],
  );

  return {
    cacheQuery,
    cachingId,
    isCached,
    isPending: mutation.isPending,
  };
}
