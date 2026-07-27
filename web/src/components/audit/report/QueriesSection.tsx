/**
 * Query-bearing report sections. This is the only report module that pulls in
 * SQLDisplay, so the CodeMirror stack stays inside the lazily loaded report
 * chunk.
 */

import { CopyButton } from '@rs/ui-new/copy-button'
import { Icon } from '@rs/ui-new/icon'
import { Scrollable } from '@rs/ui-new/scrollable'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@rs/ui-new/tooltip'
import { useEffect, useId, useMemo, useState } from 'react'
import { formatDuration } from '../../../lib/auditReportFormat'
import type { QueryReportTab } from '../../../lib/auditReportLocation'
import { QUERY_FOCUS_EVENT } from '../../../lib/auditReportLocation'
import type { UnifiedQueryRow } from '../../../lib/auditReportModel'
import {
  hashesMatch,
  indexRecommendationTarget,
  mergeQueryRows,
  queryKey,
  queryRowAnchorId,
} from '../../../lib/auditReportModel'
import { formatMs } from '../../../lib/formatters'
import type {
  ReadysetComparison,
  WorkloadIndexRecommendation,
  WorkloadQuery,
  WorkloadSummary,
} from '../../../types/audit'
import { SQLDisplay } from '../../SQLDisplay'
import { TableHeaderCell } from '../../TableHeaderCell'
import { SectionCard, StatCard } from './ReportPrimitives'

export function QueryFingerprint({ hash }: { hash: string }) {
  const shortHash = hash.slice(0, 8).toUpperCase()
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex">
            <Tag
              size="small"
              variant="muted"
              modifier="ghost"
              label={shortHash}
            />
          </span>
        </TooltipTrigger>
        <TooltipContent label={`Query fingerprint: ${hash}`} />
      </Tooltip>
    </TooltipProvider>
  )
}

export function QuerySqlDisclosure({
  sql,
  hash,
}: {
  sql: string
  hash?: string
}) {
  const [open, setOpen] = useState(false)
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        aria-label={open ? 'Collapse full SQL' : 'Expand full SQL'}
        title={open ? 'Collapse full SQL' : 'Expand full SQL'}
        onClick={() => setOpen((value) => !value)}
        className="cursor-pointer text-content-primary-soft w-fit"
      >
        <Icon
          name={open ? 'chevron-up' : 'chevron-down'}
          label=""
          aria-hidden="true"
          className="w-4 h-4"
        />
      </button>
      {open && (
        <div className="bg-surface-layout-2 rounded-lg mt-2 overflow-hidden">
          <HStack className="justify-between items-center px-3 py-2 border-b border-border-layout-1">
            {hash ? <QueryFingerprint hash={hash} /> : <span />}
            <CopyButton text={sql} />
          </HStack>
          <Scrollable className="max-h-64">
            <div className="px-3 py-2">
              <SQLDisplay sql={sql} wrap />
            </div>
          </Scrollable>
        </div>
      )}
    </div>
  )
}

export function UnifiedQueriesTable({
  rows,
  showBenchmark,
  sectionId,
  highlightedHash,
}: {
  rows: UnifiedQueryRow[]
  showBenchmark: boolean
  sectionId: string
  highlightedHash?: string
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[860px]">
        <thead>
          <tr className="bg-surface-layout-2/30">
            <TableHeaderCell>Query</TableHeaderCell>
            <TableHeaderCell align="right" className="w-20">
              Calls
            </TableHeaderCell>
            <TableHeaderCell align="right" className="w-20">
              Avg
            </TableHeaderCell>
            <TableHeaderCell align="right" className="w-20">
              % Time
            </TableHeaderCell>
            {showBenchmark && (
              <>
                <TableHeaderCell className="w-32">Cacheability</TableHeaderCell>
                <TableHeaderCell className="w-64">
                  Benchmark timing
                </TableHeaderCell>
              </>
            )}
          </tr>
        </thead>
        <tbody className="divide-y divide-border-layout-1">
          {rows.map((row, index) => {
            const sql = row.query_text || row.normalized_query || ''
            const benchmark = row.benchmark
            return (
              <tr
                key={queryKey(row) || index}
                id={
                  row.query_hash
                    ? queryRowAnchorId(sectionId, row.query_hash)
                    : undefined
                }
                className={`scroll-mt-6 hover:bg-surface-layout-2/50 transition-colors duration-500 align-top ${
                  row.query_hash &&
                  hashesMatch(row.query_hash, highlightedHash ?? '')
                    ? 'bg-surface-primary-soft/25 ring-1 ring-inset ring-border-primary-soft'
                    : ''
                }`}
              >
                <td className="px-4 py-3 min-w-96">
                  <VStack className="gap-2 items-stretch">
                    <HStack className="gap-2 items-start">
                      {sql ? (
                        <div className="min-w-0" title={sql}>
                          <Text
                            level="mono-small"
                            className="text-content-layout-2 line-clamp-2 break-all"
                          >
                            {sql}
                          </Text>
                        </div>
                      ) : (
                        <Text
                          level="caption"
                          className="text-content-layout-3 italic"
                        >
                          Query text was not retained
                        </Text>
                      )}
                      {!sql && row.query_hash && (
                        <QueryFingerprint hash={row.query_hash} />
                      )}
                    </HStack>
                    {sql && (
                      <QuerySqlDisclosure sql={sql} hash={row.query_hash} />
                    )}
                  </VStack>
                </td>
                <td className="px-3 py-3 text-right">
                  <Text
                    level="mono-small"
                    className="text-content-layout-2 tabular-nums"
                  >
                    {row.calls?.toLocaleString() ?? '-'}
                  </Text>
                </td>
                <td className="px-3 py-3 text-right">
                  <Text
                    level="mono-small"
                    className="text-content-layout-2 tabular-nums"
                  >
                    {formatMs(row.avg_time_ms)}
                  </Text>
                </td>
                <td className="px-3 py-3 text-right">
                  <Text
                    level="mono-small"
                    className="text-content-layout-2 tabular-nums"
                  >
                    {row.pct_total_time != null
                      ? `${row.pct_total_time.toFixed(1)}%`
                      : '-'}
                  </Text>
                </td>
                {showBenchmark && (
                  <>
                    <td className="px-3 py-3">
                      {benchmark ? (
                        <TooltipProvider delayDuration={150}>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span className="inline-flex">
                                <Tag
                                  size="small"
                                  variant={
                                    benchmark.supported ? 'positive' : 'muted'
                                  }
                                  modifier="ghost"
                                  label={
                                    benchmark.supported
                                      ? 'Cacheable'
                                      : 'Not cacheable'
                                  }
                                />
                              </span>
                            </TooltipTrigger>
                            <TooltipContent
                              label={
                                benchmark.supported
                                  ? 'Readyset can cache this query'
                                  : benchmark.reason ||
                                    'Readyset cannot cache this query'
                              }
                            />
                          </Tooltip>
                        </TooltipProvider>
                      ) : (
                        <Text level="caption" className="text-content-layout-3">
                          Not tested
                        </Text>
                      )}
                    </td>
                    <td className="px-3 py-3">
                      {benchmark?.supported ? (
                        <div className="grid grid-cols-2 gap-x-4 gap-y-1 min-w-56">
                          <VStack className="gap-0.5 items-start">
                            <Text
                              level="caption"
                              className="text-content-layout-3"
                            >
                              Upstream avg
                            </Text>
                            <Text
                              level="mono-small"
                              className="text-content-layout-2 tabular-nums"
                            >
                              {formatMs(benchmark.upstream_ms)}
                            </Text>
                          </VStack>
                          <VStack className="gap-0.5 items-start">
                            <Text
                              level="caption"
                              className="text-content-layout-3"
                            >
                              Readyset avg
                            </Text>
                            <Text
                              level="mono-small"
                              className="text-content-positive-soft tabular-nums"
                            >
                              {formatMs(benchmark.readyset_ms)}
                            </Text>
                          </VStack>
                          {benchmark.speedup != null && (
                            <Text
                              level="mono-small"
                              className="text-content-positive-soft tabular-nums col-span-2"
                            >
                              {benchmark.speedup.toLocaleString(undefined, {
                                maximumFractionDigits: 1,
                              })}
                              x speedup
                            </Text>
                          )}
                        </div>
                      ) : (
                        <Text
                          level="mono-small"
                          className="text-content-layout-3"
                        >
                          -
                        </Text>
                      )}
                    </td>
                  </>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function ReadysetBenchmarkSkipped({
  totalQueries,
  liveCapture,
}: {
  totalQueries?: number
  liveCapture: boolean
}) {
  const noQueries = totalQueries === 0
  return (
    <div className="px-5 py-4 border-t border-border-layout-1">
      <VStack className="gap-2 items-start max-w-3xl">
        <HStack className="gap-2 items-center">
          <Tag
            size="small"
            variant="informative"
            modifier="ghost"
            label="Skipped"
          />
          <Text level="label-small" className="text-content-layout-1">
            Readyset benchmark was not run
          </Text>
        </HStack>
        <Text level="body-small" className="text-content-layout-2">
          {noQueries
            ? 'No live queries were captured during the audit window, so there was nothing to benchmark against Readyset. Re-run with live traffic on the database.'
            : liveCapture
              ? 'The live capture completed without a Readyset comparison. Start Docker Desktop, then re-run the health check to benchmark the captured workload.'
              : 'This was a metrics-only health check, so no live workload was captured for a Readyset benchmark. Re-run with a capture window and live database traffic.'}
        </Text>
      </VStack>
    </div>
  )
}

export function UnifiedQueriesSection({
  id,
  capturedQueries = [],
  historicalQueries = [],
  comparison,
  liveCapture = false,
  totalQueries,
  emptyQueriesSlot,
  queryTab,
  onQueryTabChange,
  focusHash,
}: {
  id?: string
  capturedQueries?: WorkloadQuery[]
  historicalQueries?: WorkloadQuery[]
  comparison?: ReadysetComparison | null
  liveCapture?: boolean
  totalQueries?: number
  emptyQueriesSlot?: React.ReactNode
  queryTab?: QueryReportTab
  onQueryTabChange?: (tab: QueryReportTab) => void
  focusHash?: string
}) {
  const generatedId = useId().replace(/:/g, '')
  const sectionId = id ?? `queries-${generatedId}`
  const liveQueries = useMemo(() => {
    if (capturedQueries.length > 0) return capturedQueries
    return (comparison?.queries || []).map((query) => ({
      query_hash: query.query_hash,
      query_text: query.query_text,
    }))
  }, [capturedQueries, comparison])
  const hasLiveQueries = liveQueries.length > 0
  const [activeTab, setActiveTab] = useState<'captured' | 'historical'>(
    hasLiveQueries ? 'captured' : 'historical'
  )
  const [pendingFocusHash, setPendingFocusHash] = useState<string>()
  const [highlightedHash, setHighlightedHash] = useState<string>()
  const requestedTab = queryTab ?? activeTab
  const selectedTab = requestedTab
  const rows = useMemo(
    () =>
      selectedTab === 'captured'
        ? mergeQueryRows({
            primary: liveQueries,
            supplemental: historicalQueries,
            comparison,
          })
        : mergeQueryRows({
            primary: historicalQueries,
            comparison,
            includeUnmatchedBenchmarks: false,
          }),
    [comparison, historicalQueries, liveQueries, selectedTab]
  )
  const titleCount = new Set(
    [...liveQueries, ...historicalQueries].map(queryKey)
  ).size
  const showBenchmark = !!comparison && (comparison.queries?.length || 0) > 0

  useEffect(() => {
    const focusQuery = (event: Event) => {
      const detail = (event as CustomEvent<{ sectionId: string; hash: string }>)
        .detail
      if (!detail || detail.sectionId !== sectionId || !detail.hash) return
      const hash = detail.hash.toLowerCase()
      const capturedMatch = liveQueries.find((query) =>
        hashesMatch(query.query_hash?.toLowerCase() ?? '', hash)
      )
      const historicalMatch = historicalQueries.find((query) =>
        hashesMatch(query.query_hash?.toLowerCase() ?? '', hash)
      )
      const nextTab =
        capturedMatch || !historicalMatch ? 'captured' : 'historical'
      setActiveTab(nextTab)
      onQueryTabChange?.(nextTab)
      const resolvedHash =
        capturedMatch?.query_hash ?? historicalMatch?.query_hash ?? detail.hash
      setHighlightedHash(resolvedHash.toLowerCase())
      setPendingFocusHash(resolvedHash)
    }
    window.addEventListener(QUERY_FOCUS_EVENT, focusQuery)
    return () => window.removeEventListener(QUERY_FOCUS_EVENT, focusQuery)
  }, [historicalQueries, liveQueries, onQueryTabChange, sectionId])

  useEffect(() => {
    if (!focusHash) return
    window.dispatchEvent(
      new CustomEvent(QUERY_FOCUS_EVENT, {
        detail: { sectionId, hash: focusHash },
      })
    )
  }, [focusHash, sectionId])

  useEffect(() => {
    if (!pendingFocusHash) return
    const frame = requestAnimationFrame(() => {
      const element = document.getElementById(
        queryRowAnchorId(sectionId, pendingFocusHash)
      )
      element?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
      setPendingFocusHash(undefined)
    })
    return () => cancelAnimationFrame(frame)
  }, [pendingFocusHash, selectedTab, sectionId])

  useEffect(() => {
    if (!highlightedHash) return
    const timer = window.setTimeout(() => setHighlightedHash(undefined), 1800)
    return () => window.clearTimeout(timer)
  }, [highlightedHash])

  return (
    <div id={sectionId} className="scroll-mt-6">
      <SectionCard icon="observe" title={`Queries (${titleCount})`}>
        {comparison && (
          <div className="p-4 grid grid-cols-3 gap-3 border-b border-border-layout-1">
            <StatCard
              compact
              label="Queries tested"
              value={`${comparison.queries_tested ?? comparison.queries?.length ?? 0}`}
            />
            <StatCard
              compact
              label="Cached"
              value={`${comparison.supported_count ?? '-'}`}
            />
            <StatCard
              compact
              label="Avg speedup"
              value={
                comparison.avg_speedup != null
                  ? `${comparison.avg_speedup.toLocaleString(undefined, { maximumFractionDigits: 1 })}x`
                  : '-'
              }
              valueClassName="text-content-positive-soft"
            />
          </div>
        )}
        <div
          role="tablist"
          aria-label="Query activity"
          className="flex gap-1 px-4 pt-3 border-b border-border-layout-1 overflow-x-auto"
        >
          {(
            [
              ['captured', `Captured (live) (${liveQueries.length})`],
              [
                'historical',
                `Historical (top by time) (${historicalQueries.length})`,
              ],
            ] as const
          ).map(([tab, label]) => (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={selectedTab === tab}
              onClick={() => {
                setActiveTab(tab)
                onQueryTabChange?.(tab)
              }}
              className={`px-3 py-2 text-sm whitespace-nowrap cursor-pointer border-b-2 ${
                selectedTab === tab
                  ? 'border-border-primary-soft text-content-primary-soft'
                  : 'border-transparent text-content-layout-3 hover:text-content-layout-2'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        {rows.length > 0 ? (
          <>
            <UnifiedQueriesTable
              rows={rows}
              showBenchmark={showBenchmark}
              sectionId={sectionId}
              highlightedHash={highlightedHash}
            />
            {showBenchmark && (
              <div className="px-5 py-3 border-t border-border-layout-1">
                <Text level="caption" className="text-content-layout-3">
                  Upstream is the capture-window average from your database's
                  own statistics; Readyset is a warm shallow-cache measurement.
                  No extra load was sent to your database.
                </Text>
              </div>
            )}
          </>
        ) : (
          (emptyQueriesSlot ?? (
            <div className="p-5">
              <Text level="body-small" className="text-content-layout-3">
                No query data was collected for this run.
              </Text>
            </div>
          ))
        )}
        {!comparison && (
          <ReadysetBenchmarkSkipped
            totalQueries={totalQueries}
            liveCapture={liveCapture}
          />
        )}
      </SectionCard>
    </div>
  )
}

export function IndexRecommendationView({
  rec,
  index,
  querySectionId,
  onFocusQuery,
}: {
  rec: WorkloadIndexRecommendation
  index: number
  querySectionId?: string
  onFocusQuery?: (hash: string) => void
}) {
  const ddl = rec.sql?.trim() || rec.create_index_sql?.trim()
  const target = indexRecommendationTarget(rec)
  const targetLabel = target.table
    ? `${target.table}${target.columns.length > 0 ? `(${target.columns.join(', ')})` : ''}`
    : target.columns.length > 0
      ? `the relevant table (${target.columns.join(', ')})`
      : 'the relevant table and query columns'
  const affectedQueries = [
    ...(rec.affected_queries ?? []),
    ...(rec.query_hashes ?? []),
    ...(rec.queries ?? []),
  ]
    .map((hash) => hash.trim())
    .filter(Boolean)
    .filter((hash, position, all) => all.indexOf(hash) === position)

  return (
    <div className="rounded-lg border border-border-layout-1 overflow-hidden">
      <div className="px-3 py-2 bg-surface-layout-2/50 border-b border-border-layout-1">
        <HStack className="gap-2 items-center justify-between">
          <Text level="label-small" className="text-content-layout-1">
            {ddl
              ? target.table || `Recommended index ${index + 1}`
              : `Add an index on ${targetLabel}`}
          </Text>
          {rec.estimated_impact && (
            <Tag
              size="small"
              variant="positive"
              modifier="ghost"
              label={`${rec.estimated_impact} impact`}
            />
          )}
        </HStack>
      </div>
      {ddl ? (
        <div className="bg-surface-layout-2">
          <HStack className="justify-between items-center px-3 py-2 border-b border-border-layout-1">
            <Text
              level="caption"
              className="text-content-layout-3 uppercase tracking-wider"
            >
              Create index
            </Text>
            <CopyButton text={ddl} />
          </HStack>
          <div className="px-3 py-3 overflow-x-auto">
            <SQLDisplay sql={ddl} />
          </div>
        </div>
      ) : (
        <div className="px-3 py-3 bg-surface-layout-2/30">
          <Text level="caption" className="text-content-warning-soft">
            Exact CREATE INDEX DDL was not included. Confirm the columns and
            index order before applying this recommendation.
          </Text>
        </div>
      )}
      {(rec.reason || (affectedQueries.length > 0 && querySectionId)) && (
        <div className="px-3 py-3 border-t border-border-layout-1">
          <VStack className="gap-2 items-start">
            {affectedQueries.length > 0 && querySectionId && (
              <HStack className="gap-1 items-center flex-wrap">
                <Text level="caption" className="text-content-layout-3">
                  Speeds up {affectedQueries.length === 1 ? 'query' : 'queries'}
                </Text>
                {affectedQueries.map((hash, position) => (
                  <span key={hash}>
                    <a
                      href={`#${queryRowAnchorId(querySectionId, hash)}`}
                      className="text-content-primary-soft hover:underline"
                      onClick={(event) => {
                        event.preventDefault()
                        if (onFocusQuery) {
                          onFocusQuery(hash)
                          return
                        }
                        window.dispatchEvent(
                          new CustomEvent(QUERY_FOCUS_EVENT, {
                            detail: { sectionId: querySectionId, hash },
                          })
                        )
                      }}
                    >
                      <Text as="span" level="mono-small">
                        {hash.slice(0, 8).toUpperCase()}
                      </Text>
                    </a>
                    {position < affectedQueries.length - 1 ? ', ' : ''}
                  </span>
                ))}
              </HStack>
            )}
            {rec.reason && (
              <Text level="body-small" className="text-content-layout-2">
                {rec.reason}
              </Text>
            )}
          </VStack>
        </div>
      )}
    </div>
  )
}

export function CaptureSummarySection({
  summary,
  queries,
  durationSeconds,
}: {
  summary?: WorkloadSummary | null
  queries: WorkloadQuery[]
  durationSeconds?: number
}) {
  return (
    <SectionCard icon="observe" title="Capture Summary">
      <div className="p-6 grid grid-cols-1 tablet:grid-cols-2 desktop:grid-cols-4 gap-5">
        <StatCard label="Duration" value={formatDuration(durationSeconds)} />
        <StatCard
          label="Unique Queries"
          value={`${summary?.unique_queries ?? queries.length}`}
        />
        <StatCard
          label="Executions"
          value={
            (summary?.total_executions ?? summary?.total_queries) != null
              ? (summary?.total_executions ??
                  summary?.total_queries)!.toLocaleString()
              : '-'
          }
        />
        <StatCard
          label="Total Query Time"
          value={formatMs(summary?.total_query_time_ms)}
        />
      </div>
    </SectionCard>
  )
}
