import { toast } from '@rs/ui-new/use-toast'
import { useNavigate } from '@tanstack/react-router'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTarget } from '../../../hooks/useTarget'
import {
  dismissBackgroundRun,
  startCacheTestRun,
  useBackgroundRuns,
} from '../../../lib/backgroundRuns'
import { formatDbTime, queryImpactMs } from '../../../lib/queryImpact'
import {
  type QueryRegistryEntry,
  useQueryRegistry,
} from '../../../lib/useQueryRegistry'
import type { AddQueryMode } from './AddQueryDialog'
import {
  concreteSqlForTest,
  deriveQueryName,
  selectSavedQueries,
} from './savedQuerySelectors'

/**
 * Externally fetched registry rows (the Query Library read model). List state
 * replaces the controller's own fetch; mutations still come from the registry.
 */
export type QueryRegistryListSource = {
  queries: QueryRegistryEntry[]
  total: number
  isLoading: boolean
  isFetching: boolean
  listError: string | null
  refetch: () => Promise<unknown>
}

export function useSavedQueriesController({
  deepLinkHash,
  deepLinkRunId,
  onQueryAdded,
  onDeepLinkConsumed,
  list,
}: {
  deepLinkHash?: string
  deepLinkRunId?: string
  onQueryAdded?: (hash?: string | null) => void
  onDeepLinkConsumed?: (hash: string) => void
  list?: QueryRegistryListSource
}) {
  const navigate = useNavigate()
  const { target } = useTarget()
  const ownRegistry = useQueryRegistry(150, target, { enabled: !list })
  const registry = list ? { ...ownRegistry, ...list } : ownRegistry
  const {
    queries,
    total,
    offset,
    resetPagination,
    addMutation,
    updateSqlMutation,
    importMutation,
  } = registry

  const [searchTerm, setSearchTerm] = useState('')
  const [sourceFilter, setSourceFilter] = useState<string>('all')
  const [editingHash, setEditingHash] = useState<string | null>(null)
  const [tagDraft, setTagDraft] = useState('')
  const [confirmingHash, setConfirmingHash] = useState<string | null>(null)
  const [newSql, setNewSql] = useState('')
  const [showAddDialog, setShowAddDialog] = useState(false)
  const [addQueryMode, setAddQueryMode] = useState<AddQueryMode>('add')
  const [expandedHash, setExpandedHash] = useState<string | null>(null)
  const [highlightedHash, setHighlightedHash] = useState<string | null>(
    deepLinkHash ?? null
  )
  const [editingSqlHash, setEditingSqlHash] = useState<string | null>(null)
  const [sqlDraft, setSqlDraft] = useState('')
  const [hashAliases, setHashAliases] = useState<Record<string, string>>({})
  const [importPath, setImportPath] = useState('')
  const [importUpdate, setImportUpdate] = useState(false)
  const [paramDialog, setParamDialog] = useState<{
    hash: string
    sql: string
  } | null>(null)
  const [startingTestHash, setStartingTestHash] = useState<string | null>(null)
  const backgroundRuns = useBackgroundRuns()
  const reviewRequests = useRef(new Set<string>())
  const markReviewedRef = useRef<(hash: string) => void>(() => undefined)
  const onDeepLinkConsumedRef = useRef(onDeepLinkConsumed)

  const markReviewed = useCallback(
    (hash: string) => {
      if (!target) return
      const entry = queries.find((query) => query.hash === hash)
      const requestKey = `${target}:${hash}`
      if (!entry?.is_new || reviewRequests.current.has(requestKey)) return
      reviewRequests.current.add(requestKey)
      registry.markReviewedMutation.mutate(
        { hash, target },
        {
          onError: (error) => {
            reviewRequests.current.delete(requestKey)
            toast({
              title: "Couldn't mark query reviewed",
              description: error.message,
              variant: 'negative',
            })
          },
        }
      )
    },
    [queries, registry.markReviewedMutation, target]
  )

  const markAllReviewed = useCallback(
    async (hashes: string[]) => {
      if (!target) return
      const uniqueHashes = [...new Set(hashes)]
      if (uniqueHashes.length === 0) return

      const results = await Promise.allSettled(
        uniqueHashes.map((hash) =>
          registry.markReviewedMutation.mutateAsync({ hash, target })
        )
      )
      const failures = results.filter((result) => result.status === 'rejected')
      const reviewed = results.length - failures.length

      toast({
        title:
          failures.length === 0
            ? 'Queries marked reviewed'
            : 'Some queries were not updated',
        description:
          failures.length === 0
            ? `${reviewed} ${reviewed === 1 ? 'query' : 'queries'} removed from New.`
            : `${reviewed} reviewed, ${failures.length} failed.`,
        variant: failures.length === 0 ? 'positive' : 'negative',
      })
    },
    [registry.markReviewedMutation, target]
  )

  useEffect(() => {
    markReviewedRef.current = markReviewed
  }, [markReviewed])

  useEffect(() => {
    onDeepLinkConsumedRef.current = onDeepLinkConsumed
  }, [onDeepLinkConsumed])

  useEffect(() => {
    setHighlightedHash(deepLinkHash ?? null)
    if (!deepLinkHash) return
    setExpandedHash(deepLinkHash)
  }, [deepLinkHash])

  const deepLinkAvailable = Boolean(
    deepLinkHash && queries.some((query) => query.hash === deepLinkHash)
  )

  useEffect(() => {
    if (!deepLinkHash || !deepLinkAvailable) return

    // The exact-hash read model may resolve after the first page rendered.
    // Review and consume the link only once that query is actually present;
    // a slow request must never lose its reveal to a wall-clock timeout.
    markReviewedRef.current(deepLinkHash)

    let scrollTimer: ReturnType<typeof setTimeout> | null = null
    let clearTimer: ReturnType<typeof setTimeout> | null = null
    const reveal = () => {
      const linkedCard = Array.from(
        document.querySelectorAll<HTMLElement>('[data-query-hash]')
      ).find((element) => element.dataset.queryHash === deepLinkHash)
      if (!linkedCard) {
        scrollTimer = setTimeout(reveal, 100)
        return
      }

      linkedCard.scrollIntoView({ behavior: 'smooth', block: 'center' })
      clearTimer = setTimeout(() => {
        setHighlightedHash(null)
        onDeepLinkConsumedRef.current?.(deepLinkHash)
      }, 1_600)
    }

    scrollTimer = setTimeout(reveal, 100)
    return () => {
      if (scrollTimer) clearTimeout(scrollTimer)
      if (clearTimer) clearTimeout(clearTimer)
    }
  }, [deepLinkAvailable, deepLinkHash])

  const cacheTestRuns = useMemo(
    () =>
      backgroundRuns.filter(
        (run) =>
          (run.kind === 'speed_test' || run.kind === 'cache_test') &&
          run.target === target
      ),
    [backgroundRuns, target]
  )

  const activeCacheTest = cacheTestRuns.find(
    (run) =>
      run.status === 'running' ||
      run.status === 'reconnecting' ||
      run.status === 'needs_key'
  )
  const cachingHash = startingTestHash ?? activeCacheTest?.queryHash ?? null
  const isCached = useCallback(
    (hash: string) =>
      cacheTestRuns.some(
        (run) =>
          run.queryHash === hash && run.status === 'done' && Boolean(run.result)
      ),
    [cacheTestRuns]
  )

  const cacheRunFor = useCallback(
    (hash: string) => {
      const linked = deepLinkRunId
        ? cacheTestRuns.find(
            (run) =>
              run.runId === deepLinkRunId &&
              run.queryHash === hash &&
              run.target === target
          )
        : undefined
      return (
        linked ??
        [...cacheTestRuns]
          .reverse()
          .find((run) => run.queryHash === hash && run.target === target)
      )
    },
    [cacheTestRuns, deepLinkRunId, target]
  )

  // The comparison attaches inline on the expanded card via background runs.
  // Starting one must leave the library's URL-owned state (view, search,
  // deep-link params) untouched so the visible list never changes underneath
  // the user.
  const runConcreteTest = useCallback(
    (hash: string, sql: string) => {
      if (!target) return
      setExpandedHash(hash)
      setStartingTestHash(hash)
      const entry = queries.find((query) => query.hash === hash)
      void startCacheTestRun({
        query: sql,
        target,
        query_hash: hash,
        label: entry?.tag?.trim() || deriveQueryName(entry?.sql || sql),
        iterations: 15,
        warmup: 5,
      }).then((runId) => {
        setStartingTestHash(null)
        if (!runId) {
          toast({
            title: 'Comparison could not start',
            description: 'Open Jobs for details.',
            variant: 'negative',
          })
          return
        }
        toast({
          title: 'Cache test started',
          description:
            'Readyset will use a temporary cache and remove it after the test.',
          variant: 'positive',
        })
      })
    },
    [queries, target]
  )

  const runTest = useCallback(
    (hash: string, sql: string, stored: Record<string, unknown>) => {
      if (!target) return
      const runSql = concreteSqlForTest(sql, stored)
      if (runSql) runConcreteTest(hash, runSql)
      else setParamDialog({ hash, sql })
    },
    [runConcreteTest, target]
  )

  const selection = useMemo(() => {
    const selected = selectSavedQueries({
      queries,
      searchTerm,
      sourceFilter,
      isCached,
    })

    // A linked query must remain a regular QueryCard so it can carry the
    // persistent outline and selected-query badge. The merchandising hero is
    // useful during browsing, but it obscures identity during a handoff.
    if (deepLinkHash && selected.hero?.hash === deepLinkHash) {
      return {
        ...selected,
        hero: null,
        displayedQueries: selected.filteredQueries,
      }
    }

    return selected
  }, [deepLinkHash, isCached, queries, searchTerm, sourceFilter])

  const heroWhy = selection.hero
    ? `Runs ${(selection.hero.observation_count ?? 0).toLocaleString()} times at ${Math.round(selection.hero.avg_duration_ms ?? 0)} ms avg, ${formatDbTime(queryImpactMs(selection.hero))} of database time in all. A cache could serve it far faster.`
    : ''

  const closeAddDialog = useCallback(() => {
    setShowAddDialog(false)
    setAddQueryMode('add')
    setNewSql('')
    setImportPath('')
    setImportUpdate(false)
    importMutation.reset()
  }, [importMutation])

  const openAddDialog = useCallback(() => {
    setAddQueryMode('add')
    setShowAddDialog(true)
  }, [])

  const handleCreate = () => {
    if (!newSql.trim()) return
    addMutation.mutate(
      { sql: newSql, target: target || undefined },
      {
        onSuccess: (result) => {
          closeAddDialog()
          onQueryAdded?.(result.hash)
          toast({
            title: 'Query added',
            description: result.hash
              ? 'Saved and highlighted in your query library.'
              : 'Saved to your query library.',
            variant: 'positive',
          })
        },
        onError: (error) => {
          toast({
            title: "Couldn't add query",
            description: error.message,
            variant: 'negative',
          })
        },
      }
    )
  }

  const handleSaveSql = (hash: string) => {
    if (!sqlDraft.trim()) return
    updateSqlMutation.mutate(
      { hash, sql: sqlDraft },
      {
        onSuccess: (result) => {
          if (result.hash_changed && result.hash) {
            setHashAliases((current) => ({
              ...current,
              [hash]: result.hash as string,
            }))
            setExpandedHash((current) =>
              current === hash ? (result.hash as string) : current
            )
            setHighlightedHash((current) =>
              current === hash ? (result.hash as string) : current
            )
            // A pending add-query deep link narrows the read model to this
            // exact hash. The rewrite retires that hash, so release the link
            // or the list narrows to a query that no longer exists.
            onDeepLinkConsumedRef.current?.(hash)
          }
          setEditingSqlHash(null)
          setSqlDraft('')
          toast({
            title: 'Query updated',
            description: result.hash_changed
              ? `SQL saved. New hash: ${result.hash?.slice(0, 8)}`
              : 'SQL saved.',
            variant: 'positive',
          })
        },
        onError: (error) => {
          toast({
            title: 'Update failed',
            description: error.message,
            variant: 'negative',
          })
        },
      }
    )
  }

  const handleRename = (hash: string, name: string) => {
    registry.updateTag(hash, name)
    setEditingHash(null)
    setTagDraft('')
    toast({ title: 'Query renamed', variant: 'positive' })
  }

  const handleImport = () => {
    if (!importPath.trim()) return
    importMutation.mutate(
      {
        file: importPath.trim(),
        update: importUpdate,
        target: target || undefined,
      },
      {
        onSuccess: (result) => {
          if (result.success) {
            toast({
              title: 'Import complete',
              description: result.message || `${result.imported} imported`,
              variant: 'positive',
            })
          } else {
            toast({
              title: 'Import finished with issues',
              description:
                result.message || `${result.errors?.length ?? 0} errors`,
              variant: 'negative',
            })
          }
        },
        onError: (error) => {
          toast({
            title: 'Import failed',
            description: error.message,
            variant: 'negative',
          })
        },
      }
    )
  }

  const handleAnalyze = (
    sql: string,
    queryTarget?: string,
    mostRecentParams?: Record<string, unknown>
  ) => {
    void navigate({
      to: '/results',
      search: {
        query: sql.trim(),
        target: queryTarget || undefined,
        params:
          mostRecentParams && Object.keys(mostRecentParams).length > 0
            ? JSON.stringify(mostRecentParams)
            : undefined,
      },
    })
  }

  const setSearch = (value: string) => {
    setSearchTerm(value)
    resetPagination()
  }

  const selectSource = (value: string) => {
    setSourceFilter(value)
    resetPagination()
  }

  const clearFilters = () => {
    setSearchTerm('')
    setSourceFilter('all')
    resetPagination()
  }

  const pageEnd = offset + queries.length
  const hasPrevPage = offset > 0
  const hasNextPage = pageEnd < total

  return {
    target,
    registry,
    selection,
    heroWhy,
    searchTerm,
    sourceFilter,
    setSearch,
    selectSource,
    clearFilters,
    pagination: {
      hasPrevPage,
      hasNextPage,
      hasPagination: hasPrevPage || hasNextPage,
    },
    addDialog: {
      open: showAddDialog,
      mode: addQueryMode,
      setMode: setAddQueryMode,
      openDialog: openAddDialog,
      closeDialog: closeAddDialog,
      sql: newSql,
      setSql: setNewSql,
      save: handleCreate,
      saving: addMutation.isPending,
      importPath,
      setImportPath,
      importUpdate,
      setImportUpdate,
      importQueries: handleImport,
      importing: importMutation.isPending,
      importResult: importMutation.data,
    },
    rowState: {
      expandedHash,
      highlightedHash,
      editingHash,
      tagDraft,
      confirmingHash,
      editingSqlHash,
      sqlDraft,
      hashAliases,
    },
    rowActions: {
      cacheRunFor,
      isCached,
      cachingHash,
      toggleExpanded: (hash: string) => {
        if (expandedHash !== hash) markReviewed(hash)
        setExpandedHash((current) => (current === hash ? null : hash))
      },
      markReviewed,
      markAllReviewed,
      startRename: (hash: string, name: string) => {
        setEditingHash(hash)
        setTagDraft(name)
      },
      setTagDraft,
      cancelRename: () => {
        setEditingHash(null)
        setTagDraft('')
      },
      rename: handleRename,
      startEditSql: (hash: string, sql: string) => {
        setEditingSqlHash(hash)
        setSqlDraft(sql)
      },
      setSqlDraft,
      updateSqlPending: updateSqlMutation.isPending,
      cancelEditSql: () => {
        setEditingSqlHash(null)
        setSqlDraft('')
      },
      saveSql: handleSaveSql,
      confirmDelete: setConfirmingHash,
      cancelDelete: () => setConfirmingHash(null),
      deleteQuery: (hash: string) => {
        registry.removeQuery(hash)
        setConfirmingHash(null)
      },
      cacheQuery: (hash: string, sql: string) => {
        const entry = queries.find((query) => query.hash === hash)
        runTest(hash, sql, entry?.most_recent_params ?? {})
      },
      runTest,
      analyze: handleAnalyze,
      dismissRun: dismissBackgroundRun,
    },
    navigation: {
      openBenchmark: () =>
        void navigate({ to: '/cache', search: { view: 'compare' } }),
      openSlowQueries: () =>
        void navigate({ to: '/queries', search: { view: 'high-impact' } }),
    },
    paramDialog,
    closeParamDialog: () => setParamDialog(null),
    submitParamDialog: (sql: string) => {
      if (!paramDialog) return
      const { hash } = paramDialog
      setParamDialog(null)
      runConcreteTest(hash, sql)
    },
  }
}

export type SavedQueriesController = ReturnType<
  typeof useSavedQueriesController
>
