import {
  type InfiniteData,
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  addQueryToRegistry,
  fetchQueryRegistry,
  fetchQueryRegistryReadModel,
  type ImportQueriesResponse,
  importQueries,
  markQueryReviewed,
  QueryRegistryCursorError,
  type QueryRegistryEntry,
  type QueryRegistryReadModelPage,
  removeQueryFromRegistry,
  setQueryStarred,
  updateQuerySql,
  updateQueryTag,
} from './api'

export type { QueryRegistryEntry }

/** Every registry read, whatever its target: the widest invalidation prefix. */
export const QUERY_REGISTRY_ROOT_KEY = ['queryRegistry'] as const

/**
 * Target-scoped registry key prefix. Full list keys append limit and offset,
 * so invalidating this prefix refetches only the given target's variants.
 */
export const queryRegistryQueryKey = (target?: string | null) =>
  [...QUERY_REGISTRY_ROOT_KEY, target ?? null] as const

export function useQueryRegistry(
  initialLimit = 100,
  target?: string | null,
  options?: { enabled?: boolean }
) {
  const queryClient = useQueryClient()
  const [limit, setLimit] = useState(initialLimit)
  const [offset, setOffset] = useState(0)

  // Scope the list to the selected database when a target is given, and reset
  // pagination when it changes so a stale offset never spans a smaller list.
  useEffect(() => {
    setOffset(0)
  }, [target])

  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: [...queryRegistryQueryKey(target), limit, offset],
    queryFn: () => fetchQueryRegistry(limit, offset, target),
    staleTime: 30 * 1000,
    placeholderData: keepPreviousData,
    enabled: options?.enabled ?? true,
  })
  const queries = data?.queries ?? []
  const total = data?.total ?? 0
  const listError =
    data?.error ??
    (error instanceof Error
      ? error.message
      : error
        ? 'Failed to load the query registry'
        : null)

  useEffect(() => {
    if (total > 0 && offset >= total) {
      setOffset(Math.max(total - limit, 0))
    }
  }, [total, offset, limit])

  const addMutation = useMutation({
    mutationFn: ({ sql, target }: { sql: string; target?: string }) =>
      addQueryToRegistry(sql, target).then((result) => {
        if (!result.success) {
          throw new Error(result.error || 'Failed to add query')
        }
        return result
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queryRegistry'] })
    },
  })

  const removeMutation = useMutation({
    mutationFn: (hash: string) =>
      removeQueryFromRegistry(hash).then((result) => {
        if (!result.success) {
          throw new Error(result.error || 'Failed to remove query')
        }
        return result
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queryRegistry'] })
    },
  })

  const updateTagMutation = useMutation({
    mutationFn: ({ hash, tag }: { hash: string; tag: string }) =>
      updateQueryTag(hash, tag).then((result) => {
        if (!result.success) {
          throw new Error(result.error || 'Failed to update query tag')
        }
        return result
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queryRegistry'] })
    },
  })

  const markReviewedMutation = useMutation({
    mutationFn: ({ hash, target }: { hash: string; target: string }) =>
      markQueryReviewed(hash, target).then((result) => {
        if (!result.success) {
          throw new Error(result.error || 'Failed to mark query reviewed')
        }
        return result
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queryRegistry'] })
    },
  })

  const updateSqlMutation = useMutation({
    mutationFn: ({ hash, sql }: { hash: string; sql: string }) =>
      updateQuerySql(hash, sql).then((result) => {
        if (!result.success) {
          throw new Error(result.error || 'Failed to update SQL')
        }
        return result
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queryRegistry'] })
    },
  })

  const importMutation = useMutation<
    ImportQueriesResponse,
    Error,
    { file: string; update?: boolean; target?: string }
  >({
    mutationFn: ({ file, update, target }) =>
      importQueries(file, { update, target }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queryRegistry'] })
    },
  })

  const addQuery = (sql: string, target?: string) => {
    addMutation.mutate({ sql, target })
  }

  const removeQuery = (hash: string) => {
    removeMutation.mutate(hash)
  }

  const updateTag = (hash: string, tag: string) => {
    updateTagMutation.mutate({ hash, tag })
  }

  const nextPage = useCallback(() => {
    setOffset((prev) => prev + limit)
  }, [limit])

  const prevPage = useCallback(() => {
    setOffset((prev) => Math.max(prev - limit, 0))
  }, [limit])

  const resetPagination = useCallback(() => {
    setOffset(0)
  }, [])

  return {
    queries,
    isLoading,
    isFetching,
    total,
    listError,
    refetch,
    limit,
    offset,
    setLimit,
    setOffset,
    nextPage,
    prevPage,
    resetPagination,
    addQuery,
    addMutation,
    removeQuery,
    markReviewedMutation,
    updateTag,
    updateSqlMutation,
    importMutation,
  }
}

/** One row's star, as the optimistic cache patch writes it. */
type StarUpdate = { hash: string; starred: boolean; starred_at: string | null }

/**
 * Write a star into one cached registry payload. Every shape cached under the
 * registry prefix keeps its rows in a `queries` array — the paged read model
 * nests one per page — so the patch walks to that array and returns the
 * payload untouched when this query is not in it.
 */
function patchCachedStar(data: unknown, update: StarUpdate): unknown {
  if (!data || typeof data !== 'object') return data
  const paged = data as { pages?: unknown[] }
  if (Array.isArray(paged.pages)) {
    return {
      ...data,
      pages: paged.pages.map((page) => patchCachedStar(page, update)),
    }
  }
  const listed = data as { queries?: QueryRegistryEntry[] }
  if (!Array.isArray(listed.queries)) return data
  if (!listed.queries.some((entry) => entry.hash === update.hash)) return data
  return {
    ...data,
    queries: listed.queries.map((entry) =>
      entry.hash === update.hash
        ? { ...entry, starred: update.starred, starred_at: update.starred_at }
        : entry
    ),
  }
}

/**
 * The star, set and cleared from wherever a query is shown. The cached rows
 * are rewritten before the request leaves, so one click flips the affordance;
 * a failed request restores the exact rows that click replaced.
 */
export function useStarQuery(target?: string | null) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ hash, starred }: { hash: string; starred: boolean }) =>
      setQueryStarred(hash, starred, target),
    onMutate: async ({ hash, starred }) => {
      await queryClient.cancelQueries({ queryKey: QUERY_REGISTRY_ROOT_KEY })
      const snapshot = queryClient.getQueriesData({
        queryKey: QUERY_REGISTRY_ROOT_KEY,
      })
      const update: StarUpdate = {
        hash,
        starred,
        starred_at: starred ? new Date().toISOString() : null,
      }
      queryClient.setQueriesData(
        { queryKey: QUERY_REGISTRY_ROOT_KEY },
        (data: unknown) => patchCachedStar(data, update)
      )
      return { snapshot }
    },
    onError: (_error, _variables, context) => {
      for (const [key, data] of context?.snapshot ?? []) {
        queryClient.setQueryData(key, data)
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_REGISTRY_ROOT_KEY })
    },
  })
}

/** Filter and sort values as the read model understands them. */
export type QueryRegistryReadModelSpec = {
  search: string
  view: string
  source: string
  params: string
  activity: string
  impact: string
  sort: string
  /** The star: an independent boolean that composes with every other value. */
  starred: boolean
}

export const QUERY_REGISTRY_READ_MODEL_PAGE_SIZE = 100

/**
 * Read-model registry list: the server filters, sorts, and counts facets over
 * the full target-scoped set and pages by opaque cursor. Keys extend the
 * target-scoped prefix, so discovery invalidation refetches every loaded page
 * sequentially (deep paging makes that proportionally expensive). A spec
 * change is a new key that starts without a cursor, so stale cursors never
 * outlive their filters.
 */
export function useQueryRegistryReadModel(
  spec: QueryRegistryReadModelSpec,
  target?: string | null,
  pageSize = QUERY_REGISTRY_READ_MODEL_PAGE_SIZE
) {
  const queryClient = useQueryClient()
  const { search, view, source, params, activity, impact, sort, starred } = spec
  const queryKey = useMemo(
    () =>
      [
        ...queryRegistryQueryKey(target),
        'read-model',
        { search, view, source, params, activity, impact, sort, starred },
        pageSize,
      ] as const,
    [
      search,
      view,
      source,
      params,
      activity,
      impact,
      sort,
      starred,
      pageSize,
      target,
    ]
  )

  const query = useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam }) =>
      fetchQueryRegistryReadModel({
        target,
        search,
        view,
        source,
        params,
        activity,
        impact,
        sort,
        starred,
        limit: pageSize,
        cursor: pageParam ?? undefined,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    staleTime: 30 * 1000,
    placeholderData: keepPreviousData,
    retry: (failureCount, error) =>
      !(error instanceof QueryRegistryCursorError) && failureCount < 3,
  })

  // A stale cursor is a restart signal, not an error state: keep page one,
  // drop the tail, and refetch so the cursor chain starts fresh.
  const { error, refetch } = query
  useEffect(() => {
    if (!(error instanceof QueryRegistryCursorError)) return
    queryClient.setQueryData<
      InfiniteData<QueryRegistryReadModelPage, string | null>
    >(queryKey, (data) =>
      data
        ? {
            pages: data.pages.slice(0, 1),
            pageParams: data.pageParams.slice(0, 1),
          }
        : data
    )
    void refetch()
  }, [error, queryClient, queryKey, refetch])

  const pages = query.data?.pages
  const queries = useMemo(() => {
    if (!pages) return []
    // Rows can shift across page boundaries while paging; keep the first
    // occurrence so hashes (and React keys) stay unique.
    const seen = new Set<string>()
    const rows: QueryRegistryEntry[] = []
    for (const page of pages) {
      for (const entry of page.queries) {
        if (seen.has(entry.hash)) continue
        seen.add(entry.hash)
        rows.push(entry)
      }
    }
    return rows
  }, [pages])

  const lastPage = pages?.[pages.length - 1]
  const restarting = error instanceof QueryRegistryCursorError
  const listError = restarting
    ? null
    : (lastPage?.error ??
      (error instanceof Error
        ? error.message
        : error
          ? 'Failed to load the query registry'
          : null))

  return {
    queries,
    total: lastPage?.total ?? 0,
    facetCounts: lastPage?.facet_counts ?? null,
    freshness: lastPage?.freshness ?? null,
    pageCount: pages?.length ?? 0,
    isLoading: query.isLoading,
    isFetching: query.isFetching,
    isPlaceholder: query.isPlaceholderData,
    listError,
    refetch,
    hasNextPage: query.hasNextPage && !query.isPlaceholderData,
    fetchNextPage: query.fetchNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
  }
}
