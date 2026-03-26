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
import { deployAndCache, fetchCacheList } from './useCache';
import { collapseWhitespace } from './collapseWhitespace';

function normalizeSQL(sql: string): string {
  return collapseWhitespace(sql).toLowerCase().replace(/;$/, '');
}

interface UseCacheActionOptions {
  target: string | null;
}

interface UseCacheActionReturn {
  /** Trigger cache for a query. Handles deploy + create + toast. */
  cacheQuery: (sql: string, id: string) => void;
  /** Hash currently being cached (loading state). */
  cachingId: string | null;
  /** Check if a query (by SQL text) is already cached. */
  isCached: (sql: string) => boolean;
  /** Whether a cache operation is in progress. */
  isPending: boolean;
}

export function useCacheAction({ target }: UseCacheActionOptions): UseCacheActionReturn {
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

  // Build a set of normalized cached SQL texts.
  // Clear session cache when backend data arrives (it is now authoritative).
  const cachedSqlSet = useMemo(() => {
    const set = new Set<string>();
    if (cacheList?.caches) {
      for (const cache of cacheList.caches) {
        if (cache.query) set.add(normalizeSQL(cache.query));
      }
      setSessionCached({});
    }
    return set;
  }, [cacheList]);

  const isCached = useCallback(
    (sql: string): boolean => {
      const normalized = normalizeSQL(sql);
      return cachedSqlSet.has(normalized) || !!sessionCached[normalized];
    },
    [cachedSqlSet, sessionCached],
  );

  const mutation = useMutation({
    mutationFn: async ({ sql }: { sql: string; id: string }) => {
      return deployAndCache(target!, sql);
    },
    onSuccess: (_data, { sql }) => {
      setCachingId(null);
      setSessionCached((prev) => ({ ...prev, [normalizeSQL(sql)]: true }));
      queryClient.invalidateQueries({ queryKey: ['status'] });
      queryClient.invalidateQueries({ queryKey: ['cache-status', target] });
      queryClient.invalidateQueries({ queryKey: ['cache-list', target] });
      toast({
        title: 'Query cached',
        description: 'Query is now served from ReadySet cache.',
        variant: 'positive',
      });
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
