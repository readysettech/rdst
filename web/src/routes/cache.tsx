import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { CopyButton } from '@rs/ui-new/copy-button'
import { Dropdown } from '@rs/ui-new/dropdown'
import { Icon } from '@rs/ui-new/icon'
import { AnimatePresence, m } from '@rs/ui-new/motion'
import { Scrollable } from '@rs/ui-new/scrollable'
import { Show } from '@rs/ui-new/show'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { toast } from '@rs/ui-new/use-toast'
import { useDisclosure } from '@rs/ui-new/use-disclosure'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { TargetLockNotice } from '../components'
import { HandRaiser } from '../components/HandRaiser'
import { SQLDisplay } from '../components/SQLDisplay'
import { SQLInput } from '../components/SQLInput'
import { hasParameters, ParameterDialog } from '../components/top'
import { useTarget } from '../hooks/useTarget'
import {
  addCacheQuery,
  cacheLifecycle,
  deleteCacheQuery,
  dropAllCacheQueries,
  fetchCacheList,
  fetchCacheStatus,
  removeCacheTarget,
  useCacheDeploy,
  useCacheRun,
} from '../lib/useCache'
import { useTargetPasswordLock } from '../lib/useTargetPasswordLock'
import type {
  CacheAddResponse,
  CacheEntry,
  CacheLifecycleOperation,
  CacheRunResult,
} from '../types/cache'

type CacheSearch = {
  query?: string
}

export const Route = createFileRoute('/cache')({
  validateSearch: (search: Record<string, unknown>): CacheSearch => ({
    query: typeof search.query === 'string' ? search.query : undefined,
  }),
  component: CachePage,
})

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatusDot({ running }: { running: boolean }) {
  return (
    <span className="relative flex h-2.5 w-2.5">
      {running && (
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-surface-positive-solid opacity-75" />
      )}
      <span
        className={`relative inline-flex rounded-full h-2.5 w-2.5 ${
          running ? 'bg-surface-positive-solid' : 'bg-surface-layout-soft'
        }`}
      />
    </span>
  )
}

function EndpointCard({
  endpoint,
  running,
  target,
  onRedeploy,
  onRemove,
  onEndpointRegistered,
  onLifecycle,
  pendingLifecycleOp,
  isRedeploying,
  isRemoving,
}: {
  endpoint: string | null | undefined
  running: boolean
  target: string
  onRedeploy?: () => void
  onRemove?: () => void
  onEndpointRegistered?: () => void
  onLifecycle?: (operation: CacheLifecycleOperation) => void
  pendingLifecycleOp?: CacheLifecycleOperation | null
  isRedeploying?: boolean
  isRemoving?: boolean
}) {
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [endpointHost, setEndpointHost] = useState('')
  const [endpointPort, setEndpointPort] = useState('5433')
  const needsEndpoint = !endpoint

  const registerMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/cache/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target,
          cache_host: endpointHost.trim(),
          cache_port: Number.parseInt(endpointPort, 10) || 5433,
        }),
      })
      if (!res.ok) throw new Error(`Failed: ${res.status}`)
      const data = await res.json()
      if (data.error) throw new Error(data.error)
      return data
    },
    onSuccess: () => {
      toast({
        title: 'Connected',
        description: 'Cache endpoint configured.',
        variant: 'positive',
      })
      onEndpointRegistered?.()
    },
    onError: (err: Error) => {
      toast({
        title: 'Connection failed',
        description: err.message,
        variant: 'negative',
      })
    },
  })

  return (
    <m.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: 0.1 }}
    >
      <Card className="w-full overflow-hidden">
        <Card.Content className="p-0">
          {needsEndpoint ? (
            <>
              <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
                <HStack className="justify-between items-center">
                  <HStack className="gap-2 items-center">
                    <StatusDot running={running} />
                    <Text
                      level="overline"
                      className="text-content-layout-3 uppercase tracking-wider"
                    >
                      Endpoint Required
                    </Text>
                  </HStack>
                  {onRemove && !confirmRemove && (
                    <Button
                      variant="negative"
                      modifier="ghost"
                      size="small"
                      label="Remove"
                      icon="trash"
                      iconPosition="left"
                      onClick={() => setConfirmRemove(true)}
                    />
                  )}
                  {confirmRemove && (
                    <HStack className="gap-1">
                      <Button
                        variant="primary"
                        modifier="ghost"
                        size="small"
                        label="Cancel"
                        onClick={() => setConfirmRemove(false)}
                      />
                      <Button
                        variant="negative"
                        modifier="solid"
                        size="small"
                        label="Confirm Remove"
                        icon="trash"
                        iconPosition="left"
                        loading={isRemoving}
                        onClick={() => {
                          onRemove?.()
                          setConfirmRemove(false)
                        }}
                      />
                    </HStack>
                  )}
                </HStack>
              </div>
              <div className="p-5">
                <VStack className="gap-3 items-start">
                  <Text level="body-small" className="text-content-layout-2">
                    ReadySet was deployed. Enter the host and port where RDST
                    can reach it.
                  </Text>
                  <div className="grid grid-cols-[2fr_1fr_auto] gap-3 w-full items-end">
                    <div>
                      <Text
                        level="caption"
                        className="text-content-layout-3 mb-1 block"
                      >
                        ReadySet Host
                      </Text>
                      <BaseInputText
                        name="endpoint-host"
                        value={endpointHost}
                        onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                          setEndpointHost(e.target.value)
                        }
                        placeholder="host or IP"
                      />
                    </div>
                    <div>
                      <Text
                        level="caption"
                        className="text-content-layout-3 mb-1 block"
                      >
                        Port
                      </Text>
                      <BaseInputText
                        name="endpoint-port"
                        value={endpointPort}
                        onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                          setEndpointPort(e.target.value)
                        }
                        placeholder="5433"
                      />
                    </div>
                    <Button
                      variant="primary"
                      modifier="solid"
                      label="Connect"
                      icon="connect"
                      iconPosition="left"
                      onClick={() => registerMutation.mutate()}
                      loading={registerMutation.isPending}
                      disabled={!endpointHost.trim()}
                    />
                  </div>
                </VStack>
              </div>
            </>
          ) : (
            /* Calm health strip: status + reachability, the connection string
               demoted to a compact copyable field, and lifecycle Restart /
               Stop / Remove collapsed into one "···" overflow menu so the
               destructive action no longer competes at rest (VIS-011,
               VIS-022/023; redesign rows 5–6). */
            <div className="px-5 py-3 bg-surface-layout-2/50">
              <HStack className="justify-between items-center gap-3 flex-wrap">
                <HStack className="gap-3 items-center min-w-0">
                  <StatusDot running={running} />
                  <VStack className="gap-0 items-start min-w-0">
                    <Text
                      level="overline"
                      className="text-content-layout-3 uppercase tracking-wider"
                    >
                      {running ? 'Cache Running' : 'Cache Stopped'}
                    </Text>
                    <Text level="caption" className="text-content-layout-3">
                      {running
                        ? 'Reachable — point your app at the endpoint'
                        : 'Not reachable — start the cache to route queries'}
                    </Text>
                  </VStack>
                </HStack>
                <HStack className="gap-2 items-center min-w-0">
                  <div className="flex items-center gap-1 bg-surface-layout-2 rounded-lg pl-3 pr-1 py-1 border border-border-layout-1 min-w-0 max-w-[16rem]">
                    <Text
                      level="mono-small"
                      className="text-content-layout-2 truncate"
                    >
                      {endpoint}
                    </Text>
                    <CopyButton text={endpoint} />
                  </div>
                  {pendingLifecycleOp && <Spinner size="base" />}
                  {confirmRemove ? (
                    <HStack className="gap-1">
                      <Button
                        variant="primary"
                        modifier="ghost"
                        size="small"
                        label="Cancel"
                        onClick={() => setConfirmRemove(false)}
                      />
                      <Button
                        variant="negative"
                        modifier="solid"
                        size="small"
                        label="Confirm Remove"
                        icon="trash"
                        iconPosition="left"
                        loading={isRemoving}
                        onClick={() => {
                          onRemove?.()
                          setConfirmRemove(false)
                        }}
                      />
                    </HStack>
                  ) : (
                    (onLifecycle || onRedeploy || onRemove) && (
                      <Dropdown>
                        <Dropdown.Trigger asChild>
                          <button
                            type="button"
                            aria-label="Cache actions"
                            className="flex items-center justify-center h-8 w-8 rounded-lg text-content-layout-3 hover:text-content-layout-1 hover:bg-surface-layout-2 transition-colors cursor-pointer"
                          >
                            <Icon
                              name="more"
                              label="Cache actions"
                              className="w-4 h-4"
                            />
                          </button>
                        </Dropdown.Trigger>
                        <Dropdown.Content align="end">
                          {running ? (
                            onLifecycle && (
                              <>
                                <Dropdown.Item
                                  leftIcon="database-settings"
                                  label="Restart"
                                  disabled={!!pendingLifecycleOp}
                                  onClick={() => onLifecycle('restart')}
                                />
                                <Dropdown.Item
                                  leftIcon="minus"
                                  label="Stop"
                                  disabled={!!pendingLifecycleOp}
                                  onClick={() => onLifecycle('stop')}
                                />
                              </>
                            )
                          ) : (
                            <>
                              {onLifecycle && (
                                <Dropdown.Item
                                  leftIcon="play"
                                  label="Start"
                                  disabled={!!pendingLifecycleOp}
                                  onClick={() => onLifecycle('start')}
                                />
                              )}
                              {onRedeploy && (
                                <Dropdown.Item
                                  leftIcon="database-settings"
                                  label="Redeploy"
                                  disabled={isRedeploying || !!pendingLifecycleOp}
                                  onClick={onRedeploy}
                                />
                              )}
                            </>
                          )}
                          {onRemove && <Dropdown.Separator />}
                          {onRemove && (
                            <Dropdown.Item
                              leftIcon="trash"
                              label="Remove…"
                              disabled={!!pendingLifecycleOp}
                              onClick={() => setConfirmRemove(true)}
                            />
                          )}
                        </Dropdown.Content>
                      </Dropdown>
                    )
                  )}
                </HStack>
              </HStack>
            </div>
          )}
        </Card.Content>
      </Card>
    </m.div>
  )
}

function formatMs(ms: number): string {
  if (ms < 1) return '<1ms'
  if (ms < 1000) return `${ms.toFixed(1)}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

/** Visual latency bar — width proportional to value relative to max. */
function LatencyBar({
  label,
  value,
  maxValue,
  variant,
  delay = 0,
}: {
  label: string
  value: number
  maxValue: number
  variant: 'origin' | 'cache-win' | 'cache-lose'
  delay?: number
}) {
  const pct = maxValue > 0 ? Math.max((value / maxValue) * 100, 2) : 2
  const barColor =
    variant === 'cache-win'
      ? 'bg-surface-positive-solid'
      : variant === 'origin'
        ? 'bg-content-layout-3/40'
        : 'bg-surface-warning-solid/70'
  const textColor =
    variant === 'cache-win'
      ? 'text-content-positive-soft'
      : 'text-content-layout-1'

  return (
    <div className="flex items-center gap-3">
      <Text
        level="caption"
        className="text-content-layout-3 w-8 text-right shrink-0"
      >
        {label}
      </Text>
      <div className="flex-1 h-6 bg-surface-layout-2/50 rounded-md overflow-hidden relative">
        <m.div
          className={`h-full rounded-md ${barColor}`}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.6, delay, ease: [0.22, 1, 0.36, 1] }}
        />
      </div>
      <Text
        level="mono-small"
        className={`w-16 text-right shrink-0 tabular-nums ${textColor}`}
      >
        {formatMs(value)}
      </Text>
    </div>
  )
}

function ComparisonResult({
  result,
  onDismiss,
}: {
  result: CacheRunResult
  onDismiss: () => void
}) {
  const isWinner = result.winner === 'readyset'
  const maxLatency = Math.max(
    result.origin_stats.mean,
    result.origin_stats.p50,
    result.origin_stats.p95,
    result.cache_stats.mean,
    result.cache_stats.p50,
    result.cache_stats.p95
  )
  const speedupDisplay = isWinner
    ? `${result.speedup_mean.toFixed(1)}x`
    : `${Math.abs(result.improvement_pct).toFixed(0)}%`

  return (
    <m.tr
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      <td colSpan={4} className="px-4 py-3">
        <m.div
          className="rounded-xl border border-border-layout-1 bg-surface-layout-1/80 overflow-hidden"
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35 }}
        >
          {/* Header strip */}
          <div
            className={`px-5 py-3 flex items-center justify-between ${
              isWinner
                ? 'bg-surface-positive-soft/15 border-b border-border-positive-soft/30'
                : 'bg-surface-warning-soft/10 border-b border-border-warning-soft/30'
            }`}
          >
            <HStack className="gap-3 items-center">
              {/* Speedup badge */}
              <m.div
                className={`flex items-center justify-center rounded-lg px-3 py-1.5 font-mono text-sm font-medium tracking-tight ${
                  isWinner
                    ? 'bg-surface-positive-solid text-white'
                    : 'bg-surface-warning-solid text-white'
                }`}
                initial={{ scale: 0.8, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                transition={{ duration: 0.3, delay: 0.2 }}
              >
                {isWinner ? (
                  <>{speedupDisplay} faster</>
                ) : (
                  <>{speedupDisplay} slower</>
                )}
              </m.div>
              <Text level="label-small" className="text-content-layout-2">
                {isWinner
                  ? 'ReadySet cache outperforms origin'
                  : 'Origin is faster — cache may need warming'}
              </Text>
            </HStack>
            <button
              type="button"
              onClick={onDismiss}
              className="p-1 rounded-md hover:bg-surface-layout-2 transition-colors text-content-layout-3 hover:text-content-layout-1 cursor-pointer"
            >
              <Icon name="close" label="Dismiss" className="w-4 h-4" />
            </button>
          </div>

          {/* Comparison bars */}
          <div className="px-5 py-4 grid grid-cols-2 gap-6">
            {/* Origin column */}
            <div className="space-y-2">
              <HStack className="gap-2 items-center mb-1">
                <div className="w-2 h-2 rounded-full bg-content-layout-3/40" />
                <Text
                  level="overline"
                  className="text-content-layout-3 uppercase tracking-widest text-[10px]"
                >
                  Origin
                </Text>
              </HStack>
              <LatencyBar
                label="Mean"
                value={result.origin_stats.mean}
                maxValue={maxLatency}
                variant="origin"
                delay={0.1}
              />
              <LatencyBar
                label="P50"
                value={result.origin_stats.p50}
                maxValue={maxLatency}
                variant="origin"
                delay={0.15}
              />
              <LatencyBar
                label="P95"
                value={result.origin_stats.p95}
                maxValue={maxLatency}
                variant="origin"
                delay={0.2}
              />
            </div>

            {/* Cache column */}
            <div className="space-y-2">
              <HStack className="gap-2 items-center mb-1">
                <div
                  className={`w-2 h-2 rounded-full ${isWinner ? 'bg-surface-positive-solid' : 'bg-surface-warning-solid/70'}`}
                />
                <Text
                  level="overline"
                  className="text-content-layout-3 uppercase tracking-widest text-[10px]"
                >
                  ReadySet
                </Text>
              </HStack>
              <LatencyBar
                label="Mean"
                value={result.cache_stats.mean}
                maxValue={maxLatency}
                variant={isWinner ? 'cache-win' : 'cache-lose'}
                delay={0.25}
              />
              <LatencyBar
                label="P50"
                value={result.cache_stats.p50}
                maxValue={maxLatency}
                variant={isWinner ? 'cache-win' : 'cache-lose'}
                delay={0.3}
              />
              <LatencyBar
                label="P95"
                value={result.cache_stats.p95}
                maxValue={maxLatency}
                variant={isWinner ? 'cache-win' : 'cache-lose'}
                delay={0.35}
              />
            </div>
          </div>

          {/* Footer */}
          <div className="px-5 py-2 border-t border-border-layout-1/50 flex items-center justify-between">
            <Text level="caption" className="text-content-layout-3">
              {result.iterations} iterations &middot; min{' '}
              {formatMs(
                Math.min(result.origin_stats.min, result.cache_stats.min)
              )}{' '}
              &middot; max{' '}
              {formatMs(
                Math.max(result.origin_stats.max, result.cache_stats.max)
              )}
            </Text>
            <HStack className="gap-4">
              {(['min', 'max', 'p99'] as const).map((stat) => (
                <HStack key={stat} className="gap-1.5 items-center">
                  <Text
                    level="caption"
                    className="text-content-layout-3 uppercase text-[10px]"
                  >
                    {stat}
                  </Text>
                  <Text
                    level="mono-small"
                    className="text-content-layout-2 tabular-nums text-xs"
                  >
                    {formatMs(result.cache_stats[stat])}
                  </Text>
                </HStack>
              ))}
            </HStack>
          </div>
        </m.div>
      </td>
    </m.tr>
  )
}

function CachedQueryRow({
  entry,
  onDelete,
  onRun,
  isDeleting,
  isRunning,
  isBenchBusy,
  runProgressMessage,
  runResult,
  onDismissResult,
}: {
  entry: CacheEntry
  onDelete: (id: string) => void
  onRun: (query: string) => void
  isDeleting: boolean
  isRunning: boolean
  isBenchBusy: boolean
  runProgressMessage: string | undefined
  runResult: CacheRunResult | undefined
  onDismissResult: () => void
}) {
  const [confirming, setConfirming] = useState(false)

  if (confirming) {
    return (
      <m.tr
        key={entry.cache_id}
        data-testid="cache-query-row"
        data-cache-id={entry.cache_id}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="bg-surface-negative-soft/10"
      >
        <td colSpan={4} className="px-4 py-4">
          <div className="flex items-center justify-between gap-4 bg-surface-negative-soft/20 rounded-lg p-4 border border-border-negative-soft">
            <HStack className="gap-3 items-center flex-1 min-w-0">
              <Icon
                name="alert"
                label="Warning"
                className="w-5 h-5 text-content-negative-soft shrink-0"
              />
              <VStack className="gap-1 items-start min-w-0">
                <Text level="label-small" className="text-content-layout-1">
                  Remove this cached query?
                </Text>
                <Text
                  level="mono-small"
                  className="text-content-layout-3 truncate max-w-md"
                >
                  {entry.cache_name}
                </Text>
              </VStack>
            </HStack>
            <HStack className="gap-2 shrink-0">
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                label="Cancel"
                onClick={() => setConfirming(false)}
              />
              <Button
                variant="negative"
                modifier="solid"
                size="small"
                label="Remove"
                icon="trash"
                iconPosition="left"
                loading={isDeleting}
                onClick={() => onDelete(entry.cache_id)}
              />
            </HStack>
          </div>
        </td>
      </m.tr>
    )
  }

  return (
    <>
      <m.tr
        key={entry.cache_id}
        data-testid="cache-query-row"
        data-cache-id={entry.cache_id}
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, x: -20 }}
        transition={{ duration: 0.2 }}
        className="group hover:bg-surface-layout-2/50 transition-colors"
      >
        <td className="px-4 py-3">
          <VStack className="gap-1 items-start">
            <Text level="mono-small" className="text-content-layout-2">
              {entry.cache_name}
            </Text>
            <Tag
              size="small"
              variant="informative"
              modifier="ghost"
              label={entry.type}
            />
          </VStack>
        </td>
        <td className="px-4 py-3">
          <div className="bg-surface-layout-2 rounded-lg max-w-lg">
            <Scrollable className="max-h-24">
              <div className="px-3 py-2">
                <SQLDisplay sql={entry.query} wrap />
              </div>
            </Scrollable>
          </div>
        </td>
        <td className="px-4 py-3 text-center">
          {/* TTL is a neutral value, not info/warn — grey Tag (§4.4). */}
          <Tag
            size="small"
            variant="neutral"
            modifier="ghost"
            label={entry.ttl}
          />
        </td>
        <td className="px-4 py-3 text-right">
          <HStack className="gap-1 justify-end">
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              icon="speedometer"
              iconPosition="left"
              label="Bench"
              onClick={() => onRun(entry.query)}
              loading={isRunning}
              disabled={isBenchBusy}
            />
            <Button
              variant="negative"
              modifier="ghost"
              size="small"
              icon="trash"
              iconPosition="icon"
              label="Delete"
              onClick={() => setConfirming(true)}
            />
          </HStack>
        </td>
      </m.tr>
      <AnimatePresence>
        {isRunning && runProgressMessage && (
          <m.tr
            key="progress"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <td colSpan={4} className="px-4 py-2">
              <HStack className="gap-2 items-center">
                <Spinner size="base" />
                <Text level="caption" className="text-content-layout-3">
                  {runProgressMessage}
                </Text>
              </HStack>
            </td>
          </m.tr>
        )}
        {runResult && (
          <ComparisonResult result={runResult} onDismiss={onDismissResult} />
        )}
      </AnimatePresence>
    </>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

function CachePage() {
  const { query: pendingQuery } = Route.useSearch()
  const queryClient = useQueryClient()
  const { target } = useTarget()
  const passwordLock = useTargetPasswordLock(target)

  // Deploy hook
  const {
    deploy,
    cancel: cancelDeploy,
    state: deployState,
    progress: deployProgress,
    result: deployResult,
    error: deployError,
    reset: resetDeploy,
  } = useCacheDeploy()

  // Run comparison hook
  const {
    run: runComparison,
    state: runState,
    progress: runProgress,
    result: runResult,
    error: runError,
    reset: resetRun,
  } = useCacheRun()
  const [runningCacheId, setRunningCacheId] = useState<string | null>(null)
  const [runResults, setRunResults] = useState<Record<string, CacheRunResult>>(
    {}
  )

  // Parameter dialog state for queries that need parameter values
  const [paramDialog, setParamDialog] = useState<{
    cacheId: string
    sql: string
  } | null>(null)

  // When a run completes, stash the result keyed by cache ID
  useEffect(() => {
    if (runState === 'complete' && runResult && runningCacheId) {
      setRunResults((prev) => ({ ...prev, [runningCacheId]: runResult }))
      setRunningCacheId(null)
    } else if (runState === 'error') {
      setRunningCacheId(null)
    }
  }, [runState, runResult, runningCacheId])

  const executeRun = (cacheId: string, sql: string) => {
    if (!target) return
    setRunningCacheId(cacheId)
    setRunResults((prev) => {
      const next = { ...prev }
      delete next[cacheId]
      return next
    })
    resetRun()
    runComparison({ query: sql, target, iterations: 15, warmup: 5 })
  }

  const handleRun = (cacheId: string, query: string) => {
    if (!target) return
    if (hasParameters(query)) {
      // Query has parameters — show dialog to collect values
      setParamDialog({ cacheId, sql: query })
    } else {
      // No parameters — run directly
      executeRun(cacheId, query)
    }
  }

  // Add cache form state — pre-fill from search param
  const [addQuery, setAddQuery] = useState(pendingQuery || '')
  const [dryRunResult, setDryRunResult] = useState<CacheAddResponse | null>(
    null
  )
  // Bumped after a successful cache so the CodeMirror editor fully remounts
  // (clears its buffer/history), not just its controlled value. [QW16]
  const [editorResetKey, setEditorResetKey] = useState(0)
  const [showDropAllConfirm, setShowDropAllConfirm] = useState(false)
  const autoCacheTriggered = useRef(false)

  // Cache status
  const { data: cacheStatus, isLoading: isLoadingStatus } = useQuery({
    queryKey: ['cache-status', target],
    queryFn: () => fetchCacheStatus(target!),
    enabled: !!target && !passwordLock.isLocked,
    staleTime: 30_000,
    // Container start returns before ReadySet has necessarily bound its SQL
    // endpoint. Poll quickly while deployed-but-unreachable so users do not
    // have to press Start repeatedly, then return to the normal slow cadence.
    refetchInterval: (query) => {
      const status = query.state.data
      return status?.deployed && !status.running ? 1_000 : 60_000
    },
  })

  // Cache list (only when deployed)
  const { data: cacheList, isLoading: isLoadingList } = useQuery({
    queryKey: ['cache-list', target],
    queryFn: () => fetchCacheList(target!),
    enabled:
      !!target && !passwordLock.isLocked && cacheStatus?.deployed === true,
    staleTime: 10_000,
  })

  // Dry-run check mutation — auto-creates cache if supported
  const checkMutation = useMutation({
    mutationFn: async (query: string): Promise<CacheAddResponse> => {
      const result = await addCacheQuery({
        query,
        target: target!,
        dry_run: true,
      })
      if ('error' in result) {
        throw new Error(result.error)
      }
      return result
    },
    onSuccess: (data) => {
      setDryRunResult(data)
      if (data.supported) {
        const sql = data.query || addQuery.trim()
        if (sql && target) {
          createMutation.mutate(sql)
        }
      }
    },
  })

  // Create cache mutation
  const createMutation = useMutation({
    mutationFn: async (query: string): Promise<CacheAddResponse> => {
      const result = await addCacheQuery({
        query,
        target: target!,
        dry_run: false,
      })
      if ('error' in result) {
        throw new Error(result.error)
      }
      return result
    },
    onSuccess: () => {
      setAddQuery('')
      setDryRunResult(null)
      setEditorResetKey((k) => k + 1)
      queryClient.invalidateQueries({ queryKey: ['cache-list', target] })
    },
  })

  // Delete single cache
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const deleteMutation = useMutation({
    mutationFn: (cacheId: string) => {
      setDeletingId(cacheId)
      return deleteCacheQuery(cacheId, target!)
    },
    onSuccess: () => {
      setDeletingId(null)
      queryClient.invalidateQueries({ queryKey: ['cache-list', target] })
    },
    onError: () => setDeletingId(null),
  })

  // Drop all caches
  const dropAllMutation = useMutation({
    mutationFn: () => dropAllCacheQueries(target!),
    onSuccess: () => {
      setShowDropAllConfirm(false)
      queryClient.invalidateQueries({ queryKey: ['cache-list', target] })
    },
  })

  // Container lifecycle (start / stop / restart)
  const lifecycleMutation = useMutation({
    mutationFn: (operation: CacheLifecycleOperation) =>
      cacheLifecycle(target!, operation),
    onSuccess: (data, operation) => {
      const titles: Record<CacheLifecycleOperation, string> = {
        start: 'Cache started',
        stop: 'Cache stopped',
        restart: 'Cache restarted',
      }
      toast({
        title: titles[operation],
        description: data.detail || undefined,
        variant: 'positive',
      })
      queryClient.invalidateQueries({ queryKey: ['cache-status', target] })
      if (operation !== 'stop') {
        // ReadySet binds its SQL port a few seconds after the container
        // starts; refetch again once it has had time to come up.
        setTimeout(() => {
          queryClient.invalidateQueries({ queryKey: ['cache-status', target] })
        }, 5000)
      }
    },
    onError: (err: Error, operation) => {
      toast({
        title: `Cache ${operation} failed`,
        description: err.message,
        variant: 'negative',
      })
    },
  })
  const pendingLifecycleOp = lifecycleMutation.isPending
    ? lifecycleMutation.variables
    : null

  // Remove cache target (undeploy)
  const removeMutation = useMutation({
    mutationFn: () => removeCacheTarget(target!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['status'] })
      queryClient.invalidateQueries({ queryKey: ['cache-status', target] })
      queryClient.invalidateQueries({ queryKey: ['cache-list', target] })
    },
  })

  // Auto-cache pending query after deploy completes
  useEffect(() => {
    if (
      deployState === 'complete' &&
      pendingQuery &&
      target &&
      !autoCacheTriggered.current
    ) {
      autoCacheTriggered.current = true
      // Small delay to let status query refetch and confirm deployed
      const timer = setTimeout(async () => {
        try {
          const check = await addCacheQuery({
            query: pendingQuery,
            target,
            dry_run: true,
          })
          if ('error' in check) {
            // Silently fail — user can still manually cache
            return
          }
          if (check.supported) {
            await addCacheQuery({ query: pendingQuery, target, dry_run: false })
            queryClient.invalidateQueries({ queryKey: ['cache-list', target] })
            setAddQuery('')
            setDryRunResult({
              success: true,
              supported: true,
              query: pendingQuery,
              detail: 'Cache deployed and query cached automatically.',
            })
          } else {
            setDryRunResult(check)
          }
        } catch {
          // Silently fail — user can still manually cache
        }
      }, 1500)
      return () => clearTimeout(timer)
    }
  }, [deployState, pendingQuery, target, queryClient])

  // Deploy mode state
  type DeployMode = 'docker' | 'systemd' | 'kubernetes' | 'remote'
  type RemoteRuntime = 'docker' | 'systemd'
  const [deployMode, setDeployMode] = useState<DeployMode>('docker')
  const [k8sNamespace, setK8sNamespace] = useState('readyset')
  const [remoteDest, setRemoteDest] = useState('') // user@host format
  const [remoteRuntime, setRemoteRuntime] = useState<RemoteRuntime>('docker')
  // Systemd / Kubernetes / Remote defer behind one collapsed disclosure so the
  // recommended Docker path reads as the single decision (redesign rows 2–4).
  const [otherDeployOpen, setOtherDeployOpen] = useDisclosure({})

  // Clear deploy state when switching modes
  const switchMode = (mode: DeployMode) => {
    resetDeploy()
    setDeployMode(mode)
  }

  const handleDeploy = async () => {
    if (!target) return
    resetDeploy()
    autoCacheTriggered.current = false
    if (deployMode === 'kubernetes') {
      await deploy({ target, mode: 'kubernetes', namespace: k8sNamespace })
    } else if (deployMode === 'remote') {
      const parts = remoteDest.trim().split('@')
      const sshUser = parts.length > 1 ? parts[0] : 'root'
      const sshHost = parts.length > 1 ? parts[1] : parts[0]
      await deploy({
        target,
        mode: remoteRuntime,
        host: sshHost,
        ssh_user: sshUser,
      })
    } else if (deployMode === 'systemd') {
      await deploy({ target, mode: 'systemd' })
    } else {
      await deploy({ target, mode: 'docker' })
    }
    queryClient.invalidateQueries({ queryKey: ['cache-status', target] })
  }

  // Toast on deploy error
  useEffect(() => {
    if (deployState === 'error' && deployError) {
      toast({
        title: 'Deploy failed',
        description: deployError,
        variant: 'negative',
      })
      resetDeploy()
    }
  }, [deployState, deployError, resetDeploy])

  // Toast on deploy success
  useEffect(() => {
    if (deployState === 'complete') {
      toast({
        title: 'Cache deployed',
        description: 'ReadySet is running.',
        variant: 'positive',
      })
    }
  }, [deployState])

  const canDeploy = deployMode !== 'remote' || remoteDest.trim() !== ''

  const handleCheckAndCache = () => {
    if (!addQuery.trim() || !target) return
    // Clear any stale success/error from a previous run so a fresh check never
    // shows a green "success" beside a new red error. [QW16]
    checkMutation.reset()
    createMutation.reset()
    setDryRunResult(null)
    checkMutation.mutate(addQuery.trim())
  }

  const isDeployed = cacheStatus?.deployed === true
  const isRunning = cacheStatus?.running === true
  const caches = cacheList?.caches || []
  const isDeploying = deployState === 'deploying'

  // A query carried from Analyze/Ask pre-loads the editor but must NOT silently
  // fire a ~4 GB container deploy on arrival. The deploy stays an explicit,
  // cost-disclosed, cancelable confirm below; only the auto-cache after a
  // user-initiated deploy remains. [diagnose-to-fix MoT 3; caching HIGH]
  const carriedQuery = Boolean(pendingQuery) && !isDeployed

  // One deploy action, rendered in whichever zone owns the current mode: with
  // the recommended Docker card (primary) or, for a selected Systemd/K8s/Remote
  // mode, on its own row OUTSIDE the disclosure (so collapsing it never hides the
  // deploy button). Handlers stay wired identically; only where it renders changes.
  const deployCostLine =
    deployMode === 'docker' || deployMode === 'remote'
      ? 'Starts a ReadySet container (~4 GB RAM, 2 CPUs) · ~1–2 min'
      : deployMode === 'systemd'
        ? 'Installs a ReadySet systemd service (~4 GB RAM, 2 CPUs) · ~1–2 min'
        : 'Provisions a ReadySet pod in your cluster · ~1–2 min'
  const deployLabel =
    deployMode === 'kubernetes'
      ? 'Deploy to Kubernetes'
      : deployMode === 'remote'
        ? 'Deploy to Remote'
        : deployMode === 'systemd'
          ? 'Deploy with Systemd'
          : `Deploy cache for "${target}"`
  const deployActionRow = (
    <HStack className="justify-between items-center gap-3 flex-wrap">
      <HStack className="gap-2 items-center">
        <Icon
          name="info"
          label="Cost"
          className="w-3.5 h-3.5 text-content-layout-3 shrink-0"
        />
        <Text level="caption" className="text-content-layout-3">
          {deployCostLine}
        </Text>
      </HStack>
      <Button
        variant="primary"
        modifier="solid"
        label={deployLabel}
        icon="play"
        iconPosition="left"
        onClick={handleDeploy}
        loading={isDeploying}
        disabled={isDeploying || !canDeploy}
      />
    </HStack>
  )

  return (
    <div className="space-y-6 w-full">
      {/* Hero Header — renders at full opacity from first paint (no entrance
          fade) so navigating to /cache never shows a dimmed-blank flash before
          content settles (caching LOW: first-paint jank; VIS-102). */}
      <div className="space-y-4">
        <HStack className="justify-between items-start">
          <HStack className="gap-4 items-center">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center">
              <Icon
                name="database-settings"
                label="Cache"
                className="w-6 h-6 text-content-positive-soft"
              />
            </div>
            <VStack className="gap-1 items-start">
              <HStack className="gap-3 items-center">
                <Text
                  as="h1"
                  level="headline-3"
                  className="text-content-layout-1"
                >
                  ReadySet Cache
                </Text>
                {isDeployed && (
                  <Tag
                    variant={isRunning ? 'positive' : 'warning'}
                    modifier="ghost"
                    label={isRunning ? 'Running' : 'Stopped'}
                  />
                )}
              </HStack>
              <Text level="body-small" className="text-content-layout-3">
                Cache slow queries for sub-millisecond response times with
                ReadySet.
              </Text>
            </VStack>
          </HStack>
        </HStack>
      </div>

      {/* Password lock */}
      {passwordLock.isLocked && (
        <TargetLockNotice
          message={passwordLock.message}
          requirements={passwordLock.missingTargetRequirements}
          keyringAvailable={passwordLock.keyringAvailable}
        />
      )}

      {/* Loading state */}
      <Show when={!passwordLock.isLocked && isLoadingStatus}>
        <div className="p-16">
          <VStack className="gap-4 items-center">
            <div className="w-14 h-14 rounded-2xl bg-surface-layout-2 flex items-center justify-center">
              <Spinner size="base" />
            </div>
            <Text level="body-small" className="text-content-layout-3">
              Checking cache status...
            </Text>
          </VStack>
        </div>
      </Show>

      {/* ================================================================= */}
      {/* STATE 1: Not deployed                                             */}
      {/* ================================================================= */}
      <Show when={!passwordLock.isLocked && !isLoadingStatus && !isDeployed}>
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.1 }}
        >
          {/* A query carried from Analyze/Ask: tell the user it's held and will
              be cached once a cache exists — no re-paste. [T13] */}
          {carriedQuery && (
            <div className="mb-4 rounded-xl border border-border-info-soft bg-surface-info-soft/40 p-4">
              <HStack className="gap-3 items-start">
                <Icon
                  name="querypilot"
                  label="Carried query"
                  className="w-5 h-5 text-content-info-soft mt-0.5 shrink-0"
                />
                <VStack className="gap-1 items-start min-w-0">
                  <Text level="label-medium" className="text-content-layout-1">
                    Your query is ready to cache
                  </Text>
                  <Text level="body-small" className="text-content-layout-2">
                    Carried over from Analyze — deploy a cache below and it will
                    be cached automatically. Nothing to re-paste.
                  </Text>
                  <code className="mt-1 block max-w-full truncate font-mono text-caption text-content-layout-3">
                    {pendingQuery}
                  </code>
                </VStack>
              </HStack>
            </div>
          )}
          <Card className="w-full overflow-hidden">
            <Card.Content className="p-0">
              {/* Explanation banner — the value prop collapses from three
                  competing feature cards to one calm, de-emphasized line so the
                  deploy decision wins by quiet neighbors, not by shouting louder
                  (redesign row 1; VIS-016, VIS-114, VIS-011). */}
              <div className="p-8 border-b border-border-layout-1">
                <VStack className="gap-2 items-start max-w-2xl">
                  <Text
                    as="h2"
                    level="headline-4"
                    className="text-content-layout-1"
                  >
                    Deploy a cache for "{target}"
                  </Text>
                  <Text level="body-small" className="text-content-layout-2">
                    Serves cached queries in ~1&nbsp;ms; everything else passes
                    straight through to your database — no app changes.
                  </Text>
                </VStack>
              </div>

              {/* Deploy mode selector + action */}
              <div className="p-6 bg-surface-layout-2/30">
                {/* PRIMARY: recommended Docker path — one pre-selected card
                    with a [Recommended] tag + border-primary accent so the
                    normal choice reads first; the other three modes defer into
                    the disclosure below (redesign rows 1–4; VIS-011, VIS-125). */}
                <button
                  type="button"
                  onClick={() => switchMode('docker')}
                  className={`w-full text-left p-4 rounded-xl border-2 transition-all cursor-pointer shadow-soft ${
                    deployMode === 'docker'
                      ? 'border-border-primary-solid bg-surface-primary-soft/10'
                      : 'border-border-layout-1 hover:border-border-layout-2 bg-transparent'
                  }`}
                >
                  <HStack className="gap-3 items-start">
                    <div
                      className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${
                        deployMode === 'docker'
                          ? 'bg-surface-primary-soft'
                          : 'bg-surface-layout-2'
                      }`}
                    >
                      <Icon
                        name="database"
                        label="Docker"
                        className={`w-4 h-4 ${
                          deployMode === 'docker'
                            ? 'text-content-primary-soft'
                            : 'text-content-layout-3'
                        }`}
                      />
                    </div>
                    <VStack className="gap-0.5 items-start min-w-0">
                      <HStack className="gap-2 items-center">
                        <Text
                          level="label-small"
                          className="text-content-layout-1"
                        >
                          Docker
                        </Text>
                        <Tag
                          size="small"
                          variant="informative"
                          modifier="ghost"
                          label="Recommended"
                        />
                      </HStack>
                      <Text level="caption" className="text-content-layout-3">
                        Local container — the standard choice on this machine
                      </Text>
                    </VStack>
                  </HStack>
                </button>

                {/* Primary CTA, grouped with the recommended Docker card */}
                {deployMode === 'docker' && (
                  <div className="mt-5">{deployActionRow}</div>
                )}

                {/* TERTIARY: other deploy modes behind one collapsed disclosure
                    (use-disclosure). Systemd / Kubernetes / Remote keep their
                    exact controls inside; the deploy action for the selected
                    non-docker mode renders just below, OUTSIDE the disclosure, so
                    it survives collapse (redesign rows 2–4; USE-008). */}
                <div className="mt-5 border-t border-border-layout-1 pt-5">
                  <button
                    type="button"
                    onClick={() => setOtherDeployOpen(!otherDeployOpen)}
                    aria-expanded={otherDeployOpen}
                    className="flex items-center gap-2 text-content-layout-2 hover:text-content-layout-1 transition-colors cursor-pointer"
                  >
                    <Icon
                      name="chevron-down"
                      label="Toggle other deploy options"
                      className={`w-4 h-4 transition-transform ${
                        otherDeployOpen ? 'rotate-180' : ''
                      }`}
                    />
                    <Text level="label-small" className="text-content-layout-2">
                      Other deploy options (Systemd · Kubernetes · Remote)
                    </Text>
                  </button>

                  <AnimatePresence initial={false}>
                    {otherDeployOpen && (
                      <m.div
                        key="other-deploy"
                        className="overflow-hidden"
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: 'auto' }}
                        exit={{ opacity: 0, height: 0 }}
                        transition={{ duration: 0.2 }}
                      >
                        <div className="pt-5">
                          {/* Responsive grid for the deferred modes (was a fixed
                              4-col grid — caching LOW). */}
                          <div className="grid grid-cols-1 tablet:grid-cols-3 gap-3 mb-5">
                            {[
                              {
                                mode: 'systemd' as DeployMode,
                                icon: 'settings' as const,
                                title: 'Systemd',
                                desc: 'Local service (Linux)',
                              },
                              {
                                mode: 'kubernetes' as DeployMode,
                                icon: 'connect' as const,
                                title: 'Kubernetes',
                                desc: 'Deploy to k8s cluster',
                              },
                              {
                                mode: 'remote' as DeployMode,
                                icon: 'arrow-up-right' as const,
                                title: 'Remote Host',
                                desc: 'Deploy via SSH',
                              },
                            ].map((opt) => (
                              <button
                                key={opt.mode}
                                type="button"
                                onClick={() => switchMode(opt.mode)}
                                className={`text-left p-4 rounded-xl border-2 transition-all cursor-pointer ${
                                  deployMode === opt.mode
                                    ? 'border-surface-primary-solid bg-surface-primary-soft/10'
                                    : 'border-border-layout-1 hover:border-border-layout-2 bg-transparent'
                                }`}
                              >
                                <HStack className="gap-3 items-start">
                                  <div
                                    className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${
                                      deployMode === opt.mode
                                        ? 'bg-surface-primary-soft'
                                        : 'bg-surface-layout-2'
                                    }`}
                                  >
                                    <Icon
                                      name={opt.icon}
                                      label={opt.title}
                                      className={`w-4 h-4 ${
                                        deployMode === opt.mode
                                          ? 'text-content-primary-soft'
                                          : 'text-content-layout-3'
                                      }`}
                                    />
                                  </div>
                                  <VStack className="gap-0.5 items-start">
                                    <Text
                                      level="label-small"
                                      className={
                                        deployMode === opt.mode
                                          ? 'text-content-layout-1'
                                          : 'text-content-layout-2'
                                      }
                                    >
                                      {opt.title}
                                    </Text>
                                    <Text
                                      level="caption"
                                      className="text-content-layout-3"
                                    >
                                      {opt.desc}
                                    </Text>
                                  </VStack>
                                </HStack>
                              </button>
                            ))}
                          </div>

                          {/* Conditional inputs */}
                          <AnimatePresence mode="wait">
                            {deployMode === 'kubernetes' && (
                              <m.div
                                key="k8s-inputs"
                                // overflow-hidden clips the content to the
                                // animated height so it can't spill past the box
                                // and let the Deploy row ride up over these
                                // inputs (P60).
                                className="mb-5 space-y-3 overflow-hidden"
                                initial={{ opacity: 0, height: 0 }}
                                animate={{ opacity: 1, height: 'auto' }}
                                exit={{ opacity: 0, height: 0 }}
                                transition={{ duration: 0.2 }}
                              >
                                <div className="max-w-xs">
                                  <Text
                                    level="caption"
                                    className="text-content-layout-3 mb-1 block"
                                  >
                                    Namespace
                                  </Text>
                                  <BaseInputText
                                    name="k8s-namespace"
                                    value={k8sNamespace}
                                    onChange={(
                                      e: React.ChangeEvent<HTMLInputElement>
                                    ) => setK8sNamespace(e.target.value)}
                                    placeholder="readyset"
                                  />
                                </div>
                                <Text
                                  level="caption"
                                  className="text-content-layout-3"
                                >
                                  Requires kubectl configured with cluster access
                                  on this machine.
                                </Text>
                              </m.div>
                            )}
                            {deployMode === 'remote' && (
                              <m.div
                                key="remote-inputs"
                                // Clip to the animated height so the SSH/Runtime
                                // inputs (incl. the Systemd toggle) can't
                                // overflow and be overlapped by the Deploy button
                                // below (P60, functional).
                                className="mb-5 space-y-3 overflow-hidden"
                                initial={{ opacity: 0, height: 0 }}
                                animate={{ opacity: 1, height: 'auto' }}
                                exit={{ opacity: 0, height: 0 }}
                                transition={{ duration: 0.2 }}
                              >
                                <div className="grid grid-cols-[1fr_auto] gap-3 items-end">
                                  <div>
                                    <Text
                                      level="caption"
                                      className="text-content-layout-3 mb-1 block"
                                    >
                                      SSH Destination
                                    </Text>
                                    <BaseInputText
                                      name="remote-dest"
                                      value={remoteDest}
                                      onChange={(
                                        e: React.ChangeEvent<HTMLInputElement>
                                      ) => setRemoteDest(e.target.value)}
                                      placeholder="user@hostname"
                                    />
                                  </div>
                                  <div>
                                    <Text
                                      level="caption"
                                      className="text-content-layout-3 mb-1 block"
                                    >
                                      Runtime
                                    </Text>
                                    <HStack className="gap-1 h-10">
                                      {(['docker', 'systemd'] as const).map(
                                        (rt) => (
                                          <button
                                            key={rt}
                                            type="button"
                                            onClick={() => setRemoteRuntime(rt)}
                                            className={`flex-1 h-full px-4 rounded-lg text-sm font-medium transition-all cursor-pointer ${
                                              remoteRuntime === rt
                                                ? 'bg-surface-primary-soft/30 text-content-layout-1 border border-surface-primary-solid'
                                                : 'bg-surface-layout-2 text-content-layout-3 border border-border-layout-1 hover:border-border-layout-2'
                                            }`}
                                          >
                                            {rt === 'docker'
                                              ? 'Docker'
                                              : 'Systemd'}
                                          </button>
                                        )
                                      )}
                                    </HStack>
                                  </div>
                                </div>
                              </m.div>
                            )}
                          </AnimatePresence>

                        </div>
                      </m.div>
                    )}
                  </AnimatePresence>

                  {/* Deploy action for the selected non-docker mode renders
                      OUTSIDE the disclosure, so collapsing "Other deploy options"
                      never leaves the mode selected with no deploy button. The
                      cost line + label stay mode-aware. */}
                  {deployMode !== 'docker' && (
                    <div className="mt-5">{deployActionRow}</div>
                  )}
                </div>

                {/* Deploy progress */}
                <AnimatePresence>
                  {isDeploying && deployProgress && (
                    <m.div
                      className="mt-5"
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      exit={{ opacity: 0, height: 0 }}
                      transition={{ duration: 0.3 }}
                    >
                      <HStack className="gap-3 items-center mb-3 justify-between">
                        <HStack className="gap-3 items-center min-w-0">
                          <Spinner size="base" />
                          <Text
                            level="body-small"
                            className="text-content-layout-2"
                          >
                            {deployProgress.message}
                          </Text>
                        </HStack>
                        {/* Cancel the in-flight deploy — wires the previously
                            unused useCacheDeploy.cancel(). [caching HIGH; T13] */}
                        <Button
                          variant="primary"
                          modifier="ghost"
                          size="small"
                          label="Cancel"
                          icon="close"
                          iconPosition="left"
                          onClick={cancelDeploy}
                        />
                      </HStack>
                      {deployProgress.percent > 0 && (
                        <div className="w-full h-1.5 bg-surface-layout-2 rounded-full overflow-hidden">
                          <m.div
                            className="h-full bg-surface-positive-solid rounded-full"
                            initial={{ width: 0 }}
                            animate={{ width: `${deployProgress.percent}%` }}
                            transition={{ duration: 0.3 }}
                          />
                        </div>
                      )}
                    </m.div>
                  )}
                </AnimatePresence>
              </div>
            </Card.Content>
          </Card>
        </m.div>
      </Show>

      {/* ================================================================= */}
      {/* STATE 2 & 3: Deployed (empty or with caches)                      */}
      {/* ================================================================= */}
      <Show when={!passwordLock.isLocked && !isLoadingStatus && isDeployed}>
        {/* Endpoint card */}
        <EndpointCard
          endpoint={cacheStatus?.endpoint || deployResult?.endpoint}
          running={isRunning}
          target={target!}
          onRedeploy={handleDeploy}
          onRemove={() => removeMutation.mutate()}
          onEndpointRegistered={() =>
            queryClient.invalidateQueries({
              queryKey: ['cache-status', target],
            })
          }
          onLifecycle={(operation) => lifecycleMutation.mutate(operation)}
          pendingLifecycleOp={pendingLifecycleOp}
          isRedeploying={isDeploying}
          isRemoving={removeMutation.isPending}
        />

        {/* Cached Queries */}
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.15 }}
        >
          <Card className="w-full overflow-hidden">
            <Card.Content className="p-0">
              {/* Header */}
              <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
                <HStack className="justify-between items-center gap-4">
                  <HStack className="gap-2 items-center">
                    <Icon
                      name="layers"
                      label="Caches"
                      className="w-4 h-4 text-content-layout-3"
                    />
                    <Text
                      level="overline"
                      className="text-content-layout-3 uppercase tracking-wider"
                    >
                      Cached Queries
                    </Text>
                    <Tag
                      size="small"
                      variant="informative"
                      modifier="ghost"
                      label={`${caches.length}`}
                    />
                  </HStack>
                  <Show when={caches.length > 0}>
                    {showDropAllConfirm ? (
                      <HStack className="gap-2">
                        <Button
                          variant="primary"
                          modifier="ghost"
                          size="small"
                          label="Cancel"
                          onClick={() => setShowDropAllConfirm(false)}
                        />
                        <Button
                          variant="negative"
                          modifier="solid"
                          size="small"
                          label="Confirm Drop All"
                          icon="trash"
                          iconPosition="left"
                          loading={dropAllMutation.isPending}
                          onClick={() => dropAllMutation.mutate()}
                        />
                      </HStack>
                    ) : (
                      <Button
                        variant="negative"
                        modifier="ghost"
                        size="small"
                        label="Drop All"
                        icon="trash"
                        iconPosition="left"
                        onClick={() => setShowDropAllConfirm(true)}
                      />
                    )}
                  </Show>
                </HStack>
              </div>

              {/* Loading */}
              <Show when={isLoadingList}>
                <div className="p-12">
                  <VStack className="gap-3 items-center">
                    <Spinner size="base" />
                    <Text level="body-small" className="text-content-layout-3">
                      Loading cached queries...
                    </Text>
                  </VStack>
                </div>
              </Show>

              {/* Empty state */}
              <Show when={!isLoadingList && caches.length === 0}>
                <div className="p-12">
                  <VStack className="gap-4 items-center">
                    <div className="w-14 h-14 rounded-2xl bg-surface-layout-2 flex items-center justify-center">
                      <Icon
                        name="layers"
                        label="No caches"
                        className="w-7 h-7 text-content-layout-3"
                      />
                    </div>
                    <VStack className="gap-2 items-center">
                      <Text
                        level="headline-5"
                        className="text-content-layout-2"
                      >
                        No queries cached yet
                      </Text>
                      <Text
                        level="body-small"
                        className="text-content-layout-3 text-center max-w-sm"
                      >
                        Add a cache below, or go to the{' '}
                        <Link
                          to="/query-registry"
                          className="text-content-primary-soft hover:underline"
                        >
                          Query Registry
                        </Link>{' '}
                        to cache saved queries.
                      </Text>
                    </VStack>
                  </VStack>
                </div>
              </Show>

              {/* Cached queries table */}
              <Show when={!isLoadingList && caches.length > 0}>
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="bg-surface-layout-2/30">
                        <th className="px-4 py-3 text-left text-xs text-content-layout-3 uppercase tracking-wider font-medium w-48">
                          Cache
                        </th>
                        <th className="px-4 py-3 text-left text-xs text-content-layout-3 uppercase tracking-wider font-medium">
                          Query
                        </th>
                        <th className="px-4 py-3 text-center text-xs text-content-layout-3 uppercase tracking-wider font-medium w-20">
                          TTL
                        </th>
                        <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-16" />
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-layout-1">
                      <AnimatePresence mode="popLayout">
                        {caches.map((entry) => (
                          <CachedQueryRow
                            key={entry.cache_id}
                            entry={entry}
                            onDelete={(id) => deleteMutation.mutate(id)}
                            onRun={(query) => handleRun(entry.cache_id, query)}
                            isDeleting={deletingId === entry.cache_id}
                            isRunning={runningCacheId === entry.cache_id}
                            isBenchBusy={runningCacheId !== null}
                            runProgressMessage={
                              runningCacheId === entry.cache_id
                                ? runProgress?.message
                                : undefined
                            }
                            runResult={runResults[entry.cache_id]}
                            onDismissResult={() =>
                              setRunResults((prev) => {
                                const next = { ...prev }
                                delete next[entry.cache_id]
                                return next
                              })
                            }
                          />
                        ))}
                      </AnimatePresence>
                    </tbody>
                  </table>
                </div>
              </Show>

              {/* Drop all error */}
              <Show when={dropAllMutation.isError}>
                <div className="px-5 py-3 bg-surface-negative-soft/30 border-t border-border-negative-soft">
                  <HStack className="gap-2 items-center">
                    <Icon
                      name="alert"
                      label="Error"
                      className="w-4 h-4 text-content-negative-soft"
                    />
                    <Text
                      level="body-small"
                      className="text-content-negative-soft"
                    >
                      {dropAllMutation.error?.message ||
                        'Failed to drop caches'}
                    </Text>
                  </HStack>
                </div>
              </Show>

              {/* Run comparison error */}
              <Show when={runState === 'error' && !!runError}>
                <div className="px-5 py-3 bg-surface-negative-soft/30 border-t border-border-negative-soft">
                  <HStack className="gap-2 items-center">
                    <Icon
                      name="alert"
                      label="Error"
                      className="w-4 h-4 text-content-negative-soft"
                    />
                    <Text
                      level="body-small"
                      className="text-content-negative-soft"
                    >
                      {runError}
                    </Text>
                  </HStack>
                </div>
              </Show>
            </Card.Content>
          </Card>
        </m.div>

        {/* Add Cache Section */}
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.2 }}
        >
          <Card className="w-full overflow-hidden">
            <Card.Content className="p-0">
              <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
                <HStack className="gap-2 items-center">
                  <Icon
                    name="add"
                    label="Add"
                    className="w-4 h-4 text-content-layout-3"
                  />
                  <Text
                    level="overline"
                    className="text-content-layout-3 uppercase tracking-wider"
                  >
                    Add Cache
                  </Text>
                </HStack>
              </div>
              <div className="p-5">
                <VStack className="gap-4 items-stretch">
                  <SQLInput
                    key={editorResetKey}
                    value={addQuery}
                    onChange={(val) => {
                      setAddQuery(val)
                      setDryRunResult(null)
                    }}
                    onSubmit={handleCheckAndCache}
                    target={target}
                    placeholder="Enter a SQL query to cache..."
                    minHeight="6rem"
                    showPrettify
                  />
                  <HStack className="justify-end gap-2">
                    <Button
                      variant="primary"
                      modifier="solid"
                      label="Check & Cache"
                      icon="tick"
                      iconPosition="left"
                      onClick={handleCheckAndCache}
                      loading={checkMutation.isPending}
                      disabled={!addQuery.trim() || checkMutation.isPending}
                    />
                  </HStack>
                </VStack>
              </div>

              {/* Dry-run result */}
              <AnimatePresence>
                {dryRunResult && (
                  <m.div
                    className="border-t border-border-layout-1"
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: 'auto' }}
                    exit={{ opacity: 0, height: 0 }}
                    transition={{ duration: 0.3 }}
                  >
                    {dryRunResult.supported ? (
                      <div className="p-5 bg-surface-positive-soft/30">
                        <HStack className="gap-3 items-center">
                          <div className="w-8 h-8 rounded-lg bg-surface-positive-soft flex items-center justify-center">
                            {createMutation.isPending ? (
                              <Spinner size="base" />
                            ) : (
                              <Icon
                                name="tick-double"
                                label="Cached"
                                className="w-4 h-4 text-content-positive-soft"
                              />
                            )}
                          </div>
                          <VStack className="gap-0.5 items-start">
                            <Text
                              level="label-small"
                              className="text-content-positive-soft"
                            >
                              {createMutation.isPending
                                ? 'Caching query...'
                                : createMutation.isSuccess
                                  ? 'Query cached'
                                  : 'Query is cacheable'}
                            </Text>
                            {dryRunResult.detail && (
                              <Text
                                level="caption"
                                className="text-content-layout-3"
                              >
                                {dryRunResult.detail}
                              </Text>
                            )}
                          </VStack>
                        </HStack>
                      </div>
                    ) : (
                      <div className="p-5 bg-surface-negative-soft/30">
                        <HStack className="gap-3 items-start">
                          <div className="w-8 h-8 rounded-lg bg-surface-negative-soft flex items-center justify-center shrink-0">
                            <Icon
                              name="close"
                              label="Not supported"
                              className="w-4 h-4 text-content-negative-soft"
                            />
                          </div>
                          <VStack className="gap-1 items-start">
                            <Text
                              level="label-small"
                              className="text-content-negative-soft"
                            >
                              Query cannot be cached
                            </Text>
                            {dryRunResult.detail && (
                              <Text
                                level="body-small"
                                className="text-content-layout-2"
                              >
                                {dryRunResult.detail}
                              </Text>
                            )}
                          </VStack>
                        </HStack>
                      </div>
                    )}
                  </m.div>
                )}
              </AnimatePresence>

              {/* Check error */}
              <AnimatePresence>
                {checkMutation.isError && (
                  <m.div
                    className="border-t border-border-layout-1"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                  >
                    <div className="p-5 bg-surface-negative-soft/30">
                      <HStack className="gap-2 items-center">
                        <Icon
                          name="alert"
                          label="Error"
                          className="w-4 h-4 text-content-negative-soft"
                        />
                        <Text
                          level="body-small"
                          className="text-content-negative-soft"
                        >
                          {checkMutation.error?.message ||
                            'Failed to check cacheability'}
                        </Text>
                      </HStack>
                    </div>
                  </m.div>
                )}
              </AnimatePresence>

              {/* Create success */}
              <AnimatePresence>
                {createMutation.isSuccess && (
                  <m.div
                    className="border-t border-border-layout-1"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                  >
                    <div className="p-4 bg-surface-positive-soft/30">
                      <HStack className="gap-2 items-center">
                        <Icon
                          name="tick-double"
                          label="Created"
                          className="w-4 h-4 text-content-positive-soft"
                        />
                        <Text
                          level="body-small"
                          className="text-content-positive-soft"
                        >
                          Cache created successfully.
                        </Text>
                      </HStack>
                    </div>
                  </m.div>
                )}
              </AnimatePresence>

              {/* PQL hand-raiser (dma.4): first cache on the user's own target.
                  The demo runs its own isolated flow, so any target here is an
                  own target — no extra guard needed. */}
              {createMutation.isSuccess && target && (
                <div className="px-4 pb-4">
                  <HandRaiser
                    signal="cache_created"
                    tone="positive"
                    message={`This cache is live on ${target}. Running it beyond one instance (replicas, HA, a fleet)? We help teams run Readyset in production.`}
                  />
                </div>
              )}

              {/* Create error */}
              <AnimatePresence>
                {createMutation.isError && (
                  <m.div
                    className="border-t border-border-layout-1"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                  >
                    <div className="p-4 bg-surface-negative-soft/30">
                      <HStack className="gap-2 items-center">
                        <Icon
                          name="alert"
                          label="Error"
                          className="w-4 h-4 text-content-negative-soft"
                        />
                        <Text
                          level="body-small"
                          className="text-content-negative-soft"
                        >
                          {createMutation.error?.message ||
                            'Failed to create cache'}
                        </Text>
                      </HStack>
                    </div>
                  </m.div>
                )}
              </AnimatePresence>
            </Card.Content>
          </Card>
        </m.div>
      </Show>

      {/* Parameter dialog for parameterized queries */}
      {paramDialog && (
        <ParameterDialog
          isOpen
          query={paramDialog.sql}
          submitLabel="Run Comparison"
          submitIcon="play"
          onClose={() => setParamDialog(null)}
          onSubmit={(substitutedSql) => {
            const { cacheId } = paramDialog
            setParamDialog(null)
            executeRun(cacheId, substitutedSql)
          }}
        />
      )}
    </div>
  )
}
