import { toast } from '@rs/ui-new/use-toast'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { hasParameters } from '../../../components/top'
import { useTarget } from '../../../hooks/useTarget'
import { useCacheAction } from '../../../lib/useCacheAction'
import { useQueryRegistry } from '../../../lib/useQueryRegistry'
import { useTargetPasswordLock } from '../../../lib/useTargetPasswordLock'
import { useTop } from '../../../lib/useTop'
import type { TopMode, TopQuery } from '../../../types/top'
import {
  getSlowQueryFilterError,
  type SlowQueryFiltersSnapshot,
  slowQueryLastFiltersQueryKey,
} from './slowQueryFilters'

export function useSlowQueriesController({
  initialMode,
}: {
  initialMode?: TopMode
} = {}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { target } = useTarget()
  const passwordLock = useTargetPasswordLock(target)
  const { queries: registryQueries, addMutation: addQueryMutation } =
    useQueryRegistry(100, target)

  const {
    cacheQuery,
    cachingId: cachingHash,
    isCached,
  } = useCacheAction({ target })

  const [initialFilters] = useState<SlowQueryFiltersSnapshot | null>(() =>
    target
      ? (queryClient.getQueryData<SlowQueryFiltersSnapshot>(
          slowQueryLastFiltersQueryKey(target)
        ) ?? null)
      : null
  )
  const [mode, setMode] = useState<TopMode>(
    initialMode ?? initialFilters?.mode ?? 'historical'
  )
  const [source, setSource] = useState(initialFilters?.source ?? 'auto')
  const [sort, setSort] = useState(initialFilters?.sort ?? 'total_time')
  const [limit, setLimit] = useState(initialFilters?.limit ?? 10)
  const [filterPattern, setFilterPattern] = useState(
    initialFilters?.filterPattern ?? ''
  )
  const [duration, setDuration] = useState(initialFilters?.duration ?? 0)
  const [autoSave, setAutoSave] = useState(initialFilters?.autoSave ?? true)
  const [minFreq, setMinFreq] = useState(initialFilters?.minFreq ?? 0)
  const [minLoadPct, setMinLoadPct] = useState(initialFilters?.minLoadPct ?? 0)
  const filterPatternError = useMemo(
    () => getSlowQueryFilterError(filterPattern, mode),
    [filterPattern, mode]
  )

  const [parameterQuery, setParameterQuery] = useState<string | null>(null)
  const [parameterQueryHash, setParameterQueryHash] = useState<string | null>(null)
  const [parameterValues, setParameterValues] = useState<
    Record<string, unknown> | undefined
  >(undefined)

  const registryParamsByHash = useMemo(() => {
    const values = new Map<string, Record<string, unknown>>()
    for (const entry of registryQueries) {
      if (
        entry.most_recent_params &&
        Object.keys(entry.most_recent_params).length > 0
      ) {
        values.set(entry.hash, entry.most_recent_params)
      }
    }
    return values
  }, [registryQueries])

  const registryHashes = useMemo(
    () => new Set(registryQueries.map((query) => query.hash)),
    [registryQueries]
  )

  const run = useTop(target)

  useEffect(() => {
    if (run.state !== 'complete') return

    const runTarget = run.connectionInfo?.target ?? target
    if (!runTarget) return

    queryClient.setQueryData<SlowQueryFiltersSnapshot>(
      slowQueryLastFiltersQueryKey(runTarget),
      {
        mode,
        source,
        sort,
        limit,
        filterPattern,
        duration,
        autoSave,
        minFreq,
        minLoadPct,
      }
    )
  }, [
    run.state,
    run.connectionInfo,
    target,
    mode,
    source,
    sort,
    limit,
    filterPattern,
    duration,
    autoSave,
    minFreq,
    minLoadPct,
    queryClient,
  ])

  const handleStart = useCallback(() => {
    if (!target || passwordLock.isLocked || filterPatternError) return

    if (mode === 'realtime') {
      run.startRealtime(target, {
        limit,
        duration: duration > 0 ? duration : undefined,
        auto_save: autoSave,
        min_freq: minFreq,
        min_load_pct: minLoadPct,
      })
      return
    }

    run.getTop(target, {
      limit,
      source: source as 'auto' | 'pg_stat' | 'activity' | 'digest',
      sort: sort as 'total_time' | 'freq' | 'avg_time' | 'load',
      filter_pattern: filterPattern || undefined,
      auto_save: autoSave,
      min_freq: minFreq,
      min_load_pct: minLoadPct,
    })
  }, [
    target,
    passwordLock.isLocked,
    filterPatternError,
    mode,
    run.startRealtime,
    run.getTop,
    limit,
    duration,
    autoSave,
    minFreq,
    minLoadPct,
    source,
    sort,
    filterPattern,
  ])

  const handleModeChange = useCallback(
    (nextMode: TopMode) => {
      run.reset()
      const runTarget = run.connectionInfo?.target ?? target
      if (runTarget) {
        queryClient.removeQueries({
          queryKey: slowQueryLastFiltersQueryKey(runTarget),
        })
      }
      setMode(nextMode)
    },
    [run.reset, run.connectionInfo, target, queryClient]
  )

  const handleCacheQuery = useCallback(
    (query: TopQuery) => {
      cacheQuery(query.query_text, query.query_hash)
    },
    [cacheQuery]
  )

  const handleAnalyze = useCallback(
    (query: TopQuery) => {
      if (passwordLock.isLocked) return

      const storedParams = registryParamsByHash.get(query.query_hash)
      if (hasParameters(query.query_text)) {
        setParameterQuery(query.query_text)
        setParameterQueryHash(query.query_hash ?? null)
        setParameterValues(storedParams)
        return
      }

      navigate({
        to: '/results',
        search: {
          query: query.query_text,
          target: target || undefined,
          params: storedParams ? JSON.stringify(storedParams) : undefined,
        },
      })
    },
    [passwordLock.isLocked, registryParamsByHash, navigate, target]
  )

  const closeParameterDialog = useCallback(() => {
    setParameterQuery(null)
    setParameterQueryHash(null)
    setParameterValues(undefined)
  }, [])

  const handleParameterSubmit = useCallback(
    (substitutedQuery: string) => {
      setParameterQuery(null)
      setParameterValues(undefined)
      navigate({
        to: '/results',
        search: {
          query: substitutedQuery,
          target: target || undefined,
        },
      })
    },
    [navigate, target]
  )

  const handleSaveAll = useCallback(async () => {
    if (!target || run.queries.length === 0) return

    const unsavedQueries = run.queries.filter(
      (query) =>
        !run.savedHashes.has(query.query_hash) &&
        !registryHashes.has(query.query_hash)
    )
    if (unsavedQueries.length === 0) {
      toast({
        title: 'Nothing to save',
        description: 'Every listed query is already saved.',
        variant: 'primary',
      })
      return
    }

    const results = await Promise.allSettled(
      unsavedQueries.map((query) =>
        addQueryMutation.mutateAsync({ sql: query.query_text, target })
      )
    )
    const failures = results.filter(
      (result): result is PromiseRejectedResult => result.status === 'rejected'
    )
    const saved = results.length - failures.length

    if (failures.length === 0) {
      toast({
        title: 'Queries saved',
        description: `${saved} ${saved === 1 ? 'query' : 'queries'} added to Saved.`,
        variant: 'positive',
      })
      return
    }

    const reason = failures[0].reason
    const firstError = reason instanceof Error ? reason.message : String(reason)
    toast({
      title: saved > 0 ? 'Some queries were not saved' : 'Queries not saved',
      description: `${saved} saved, ${failures.length} failed. ${firstError}`,
      variant: 'negative',
    })
  }, [target, run.queries, run.savedHashes, registryHashes, addQueryMutation])

  const canRun = !!target && !passwordLock.isLocked
  const canSave =
    run.queries.length > 0 &&
    (run.state === 'complete' || run.state === 'streaming')

  return {
    target: {
      name: target,
      label: run.connectionInfo?.target ?? target ?? '—',
      canRun,
      passwordLock,
    },
    filters: {
      mode,
      setMode: handleModeChange,
      source,
      setSource,
      sort,
      setSort,
      limit,
      setLimit,
      filterPattern,
      setFilterPattern,
      filterPatternError,
      duration,
      setDuration,
      autoSave,
      setAutoSave,
      minFreq,
      setMinFreq,
      minLoadPct,
      setMinLoadPct,
    },
    run: {
      ...run,
      sourceLabel: run.connectionInfo?.source ?? source,
      canSave,
    },
    cache: {
      cachingHash,
      isCached,
    },
    parameterDialog: {
      query: parameterQuery,
      queryHash: parameterQueryHash,
      initialValues: parameterValues,
      close: closeParameterDialog,
      submit: handleParameterSubmit,
    },
    actions: {
      start: handleStart,
      stop: run.stopRealtime,
      analyze: handleAnalyze,
      cache: handleCacheQuery,
      saveAll: handleSaveAll,
    },
  }
}

export type SlowQueriesController = ReturnType<typeof useSlowQueriesController>
