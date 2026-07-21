/**
 * Top Queries page component — moved out of the route config into this
 * route-ignored sibling (TanStack skips `-`-prefixed files) so the code-splitter
 * can relocate its `../components` barrel import (which re-exports the CodeMirror
 * SQL-editor stack) out of the eager entry chunk. The route file imports
 * `TopPage` only for its `component:` wrapper; the tests import it from here.
 * See evidence/gates-final.md §Defect D-1.
 */

import { Button } from '@rs/ui-new/button'
import { CopyButton } from '@rs/ui-new/copy-button'
import { Icon } from '@rs/ui-new/icon'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { toast } from '@rs/ui-new/use-toast'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { TargetLockNotice } from '../components'
import {
  hasParameters,
  ParameterDialog,
  TopFilters,
  TopHeader,
  TopQueryTable,
} from '../components/top'
import { useTarget } from '../hooks/useTarget'
import { useCacheAction } from '../lib/useCacheAction'
import { useQueryRegistry } from '../lib/useQueryRegistry'
import { useTargetPasswordLock } from '../lib/useTargetPasswordLock'
import { useTop } from '../lib/useTop'
import type {
  TopDbLimitWarningEventData,
  TopMode,
  TopQuery,
} from '../types/top'

/**
 * React-query cache key for the filters that produced the last completed run.
 * Persisted alongside the run results (kept in useTop's own cache) so a restored
 * view after navigation shows the same mode/source/sort the results were fetched
 * with — not a reset default that would misrepresent the listed rows. Scoped by
 * target so switching A→B doesn't restore A's filters under B (mirrors the
 * run-snapshot key in useTop). [FIX-4 / top#1 / feedback-triage-2 §1.2]
 */
const topLastFiltersQK = (target: string) =>
  ['top', 'lastFilters', target] as const

interface TopFiltersSnapshot {
  mode: TopMode
  source: string
  sort: string
  limit: number
  filterPattern: string
  duration: number
  autoSave: boolean
  minFreq: number
  minLoadPct: number
}

function formatKB(bytes: number) {
  return `${Math.round(bytes / 1024)} KB`
}

function DbLimitWarning({
  warning: w,
}: {
  warning: TopDbLimitWarningEventData
}) {
  const isPostgres = w.db_engine.includes('postgres')
  const sql = isPostgres
    ? `ALTER SYSTEM SET ${w.setting_name} = ${w.recommended_bytes};`
    : `SET GLOBAL ${w.setting_name} = ${w.recommended_bytes};`

  return (
    <div className="bg-surface-warning-soft/50 rounded-xl border border-border-warning-soft p-5">
      <HStack className="gap-3 items-start">
        <div className="w-8 h-8 rounded-lg bg-surface-warning-soft flex items-center justify-center shrink-0 mt-0.5">
          <Icon
            name="alert"
            label="Warning"
            className="w-4 h-4 text-content-warning-soft"
          />
        </div>
        <VStack className="gap-2 items-start flex-1 min-w-0">
          <VStack className="gap-0.5 items-start">
            <Text level="label-small" className="text-content-warning-soft">
              Low Database Query Size Limit
            </Text>
            <Text level="body-small" className="text-content-layout-2">
              <code className="font-mono">{w.setting_name}</code> is set to{' '}
              {formatKB(w.db_limit_bytes)}. Increase to at least{' '}
              {formatKB(w.recommended_bytes)} to avoid query truncation.
            </Text>
          </VStack>
          <div className="w-full relative group">
            <pre className="px-3 py-2.5 rounded-lg bg-surface-layout-1 text-xs font-mono text-content-layout-1 overflow-x-auto">
              {sql}
              {isPostgres ? '\n-- Then restart PostgreSQL' : ''}
            </pre>
            <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
              <CopyButton text={sql} />
            </div>
          </div>
        </VStack>
      </HStack>
    </div>
  )
}

export function TopPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { queries: registryQueries, addMutation: addQueryMutation } =
    useQueryRegistry()
  const { target } = useTarget()
  const passwordLock = useTargetPasswordLock(target)

  // Cache integration
  const {
    cacheQuery,
    cachingId: cachingHash,
    isCached,
  } = useCacheAction({ target })

  const handleCacheQuery = useCallback(
    (query: TopQuery) => {
      cacheQuery(query.query_text, query.query_hash)
    },
    [cacheQuery]
  )

  // Filter state — seeded from the last completed run's persisted filters so a
  // return to /top restores the same controls as the restored results.
  const [initialFilters] = useState<TopFiltersSnapshot | null>(() =>
    target
      ? (queryClient.getQueryData<TopFiltersSnapshot>(
          topLastFiltersQK(target)
        ) ?? null)
      : null
  )
  const [mode, setMode] = useState<TopMode>(
    initialFilters?.mode ?? 'historical'
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

  // Validate the filter regex client-side so an invalid pattern shows an inline
  // message and keeps the last good results, instead of being sent to the
  // backend and returning a raw Python `re` error that nukes the table. [QW15]
  const filterPatternError = useMemo(() => {
    // The regex filter is a historical-only control (the field isn't rendered in
    // realtime), so a stale invalid pattern must not block realtime Start — ignore
    // filter_pattern validation entirely when mode === 'realtime'. [C-09]
    if (mode === 'realtime') return null
    if (!filterPattern.trim()) return null
    try {
      new RegExp(filterPattern)
      return null
    } catch (e) {
      return e instanceof Error ? e.message : 'Invalid regular expression'
    }
  }, [filterPattern, mode])

  // Parameter dialog state
  const [paramDialogQuery, setParamDialogQuery] = useState<string | null>(null)
  const [paramDialogStoredParams, setParamDialogStoredParams] = useState<
    Record<string, unknown> | undefined
  >(undefined)

  // Build a lookup from query hash to registry entry for stored params
  const registryParamsByHash = useMemo(() => {
    const map = new Map<string, Record<string, unknown>>()
    for (const entry of registryQueries) {
      if (
        entry.most_recent_params &&
        Object.keys(entry.most_recent_params).length > 0
      ) {
        map.set(entry.hash, entry.most_recent_params)
      }
    }
    return map
  }, [registryQueries])

  // Hook state
  const {
    getTop,
    startRealtime,
    stopRealtime,
    reset,
    state,
    queries,
    connectionInfo,
    sourceFallback,
    dbLimitWarning,
    runtimeSeconds,
    totalTracked,
    newlySaved,
    savedHashes,
    error,
  } = useTop(target)

  // Persist the run's filters on the same signal the results are cached
  // (completion), so the pair restores together. In-progress runs are skipped,
  // keeping the persisted filters tied to the last *successful* run.
  useEffect(() => {
    if (state !== 'complete') return
    // Key by the run's own target (falling back to the selected one) so this
    // stays paired with the run snapshot and a target switch can't rewrite one
    // target's filters under another. [FIX-4]
    const runTarget = connectionInfo?.target ?? target
    if (!runTarget) return
    queryClient.setQueryData<TopFiltersSnapshot>(topLastFiltersQK(runTarget), {
      mode,
      source,
      sort,
      limit,
      filterPattern,
      duration,
      autoSave,
      minFreq,
      minLoadPct,
    })
  }, [
    state,
    mode,
    source,
    sort,
    limit,
    filterPattern,
    duration,
    autoSave,
    minFreq,
    minLoadPct,
    connectionInfo,
    target,
    queryClient,
  ])

  const handleStart = useCallback(() => {
    if (!target || passwordLock.isLocked) return
    // Don't send an invalid regex to the backend — keep the last good results
    // and let the inline message guide the fix. [QW15]
    if (filterPatternError) return

    if (mode === 'realtime') {
      startRealtime(target, {
        limit,
        duration: duration > 0 ? duration : undefined,
        auto_save: autoSave,
        min_freq: minFreq,
        min_load_pct: minLoadPct,
      })
    } else {
      getTop(target, {
        limit,
        source: source as 'auto' | 'pg_stat' | 'activity' | 'digest',
        sort: sort as 'total_time' | 'freq' | 'avg_time' | 'load',
        filter_pattern: filterPattern || undefined,
        auto_save: autoSave,
        min_freq: minFreq,
        min_load_pct: minLoadPct,
      })
    }
  }, [
    target,
    passwordLock.isLocked,
    mode,
    limit,
    duration,
    autoSave,
    source,
    sort,
    filterPattern,
    filterPatternError,
    minFreq,
    minLoadPct,
    startRealtime,
    getTop,
  ])

  const handleStop = useCallback(() => {
    stopRealtime()
  }, [stopRealtime])

  const handleAnalyze = useCallback(
    (query: TopQuery) => {
      if (passwordLock.isLocked) return
      const storedParams = registryParamsByHash.get(query.query_hash)
      // Check if query has parameters that need substitution
      if (hasParameters(query.query_text)) {
        setParamDialogQuery(query.query_text)
        setParamDialogStoredParams(storedParams)
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
    [passwordLock.isLocked, navigate, target, registryParamsByHash]
  )

  const handleParamDialogClose = useCallback(() => {
    setParamDialogQuery(null)
    setParamDialogStoredParams(undefined)
  }, [])

  const handleParamSubmit = useCallback(
    (substitutedQuery: string) => {
      setParamDialogQuery(null)
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
    if (!target || queries.length === 0) return

    const unsavedQueries = queries.filter(
      (query) => !savedHashes.has(query.query_hash)
    )
    if (unsavedQueries.length === 0) {
      toast({
        title: 'Nothing to save',
        description: 'All listed queries are already in the registry.',
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
      (r): r is PromiseRejectedResult => r.status === 'rejected'
    )
    const saved = results.length - failures.length

    if (failures.length === 0) {
      toast({
        title: 'Saved to registry',
        description: `${saved} ${saved === 1 ? 'query' : 'queries'} saved.`,
        variant: 'positive',
      })
    } else {
      const reason = failures[0].reason
      const firstError =
        reason instanceof Error ? reason.message : String(reason)
      toast({
        title: saved > 0 ? 'Saved with errors' : 'Save failed',
        description: `${saved} saved, ${failures.length} failed. ${firstError}`,
        variant: 'negative',
      })
    }
  }, [target, queries, savedHashes, addQueryMutation])

  // Reset when mode changes. reset() drops the cached run; the filters cache is
  // cleared in step so a stale mode/filter pair can't be restored after leaving
  // the page mid-switch.
  const handleModeChange = useCallback(
    (newMode: TopMode) => {
      reset()
      const runTarget = connectionInfo?.target ?? target
      if (runTarget) {
        queryClient.removeQueries({ queryKey: topLastFiltersQK(runTarget) })
      }
      setMode(newMode)
    },
    [reset, queryClient, connectionInfo, target]
  )

  const canSave =
    queries.length > 0 && (state === 'complete' || state === 'streaming')
  const targetLabel = connectionInfo?.target ?? target ?? '—'
  const sourceLabel = connectionInfo?.source ?? source
  const startDisabled = !target || passwordLock.isLocked || !!filterPatternError

  // The one primary CTA for the idle empty state (region B collapses to this).
  const idleAction = (
    <Button
      variant="rising"
      modifier="solid"
      label={
        mode === 'realtime' ? 'Start live monitoring' : 'Find slow queries'
      }
      icon={mode === 'realtime' ? 'play' : 'search'}
      iconPosition="left"
      onClick={handleStart}
      disabled={startDisabled}
    />
  )

  return (
    <div className="space-y-6 w-full">
      <TopHeader
        state={state}
        targetLabel={targetLabel}
        sourceLabel={sourceLabel}
        engineLabel={connectionInfo?.engine}
        runtimeSeconds={runtimeSeconds}
        totalTracked={totalTracked}
        newlySaved={newlySaved}
        isRealtime={mode === 'realtime'}
        sourceFallback={sourceFallback}
      />

      <Show when={!target}>
        <div className="bg-surface-warning-soft rounded-xl border border-border-warning-soft p-4">
          <Text level="body-small" className="text-content-warning-soft">
            Please select a target database from the sidebar to continue.
          </Text>
        </div>
      </Show>
      <Show when={passwordLock.isLocked}>
        <TargetLockNotice
          message={passwordLock.message}
          requirements={passwordLock.missingTargetRequirements}
          keyringAvailable={passwordLock.keyringAvailable}
        />
      </Show>

      <TopFilters
        mode={mode}
        setMode={handleModeChange}
        source={source}
        setSource={setSource}
        limit={limit}
        setLimit={setLimit}
        filterPattern={filterPattern}
        setFilterPattern={setFilterPattern}
        filterPatternError={filterPatternError}
        duration={duration}
        setDuration={setDuration}
        minFreq={minFreq}
        setMinFreq={setMinFreq}
        minLoadPct={minLoadPct}
        setMinLoadPct={setMinLoadPct}
        autoSave={autoSave}
        setAutoSave={setAutoSave}
        state={state}
        onStart={handleStart}
        onStop={handleStop}
        hasTarget={!!target && !passwordLock.isLocked}
        primaryActionHidden={state === 'idle'}
      />

      {dbLimitWarning && <DbLimitWarning warning={dbLimitWarning} />}

      <Show when={error !== null}>
        <div className="bg-surface-negative-soft rounded-xl border border-border-negative-soft p-4">
          <Text level="body-small" className="text-content-negative-soft">
            Error: {error}
          </Text>
        </div>
      </Show>

      <TopQueryTable
        queries={queries}
        state={state}
        isRealtime={mode === 'realtime'}
        onAnalyze={handleAnalyze}
        onCache={handleCacheQuery}
        cachingHash={cachingHash}
        isCached={isCached}
        sort={sort}
        setSort={setSort}
        onSaveAll={handleSaveAll}
        canSave={canSave}
        autoSave={autoSave}
        setAutoSave={setAutoSave}
        idleAction={idleAction}
      />

      <ParameterDialog
        isOpen={paramDialogQuery !== null}
        onClose={handleParamDialogClose}
        onSubmit={handleParamSubmit}
        query={paramDialogQuery || ''}
        initialValues={paramDialogStoredParams}
      />
    </div>
  )
}
