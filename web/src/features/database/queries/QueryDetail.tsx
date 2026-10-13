// The detail pane for one query: the row's chip and numbers in the header, then the
// title, the two history lines, the fix (or the analysis in progress), the query and
// the Readyset line; the four actions in the footer. A write also says what each run
// costs its table.
import { cn } from '@rs/tailwind-base'
import { Button, buttonStyles } from '@rs/ui-new/button'
import { SidePane } from '@rs/ui-new/side-pane'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { type ReactNode, useState } from 'react'
import { MiniTrend } from '@/features/monitor-kit/charts'
import { formatRelative } from '@/features/monitor-kit/format'
import { paramSourceText } from '@/features/queries/results/analysisView'
import { getQueryAnalysis } from '@/lib/monitor/api'
import { useMonitorQuery } from '@/lib/monitor/hooks'
import { monitorKeys } from '@/lib/monitor/keys'
import type {
  AnalyzeJob,
  ApiRange,
  DeepAnalysisDigest,
  MonitorQuery,
} from '@/lib/monitor/types'
import { AnalyzingBlock } from '../analyze/AnalyzingBlock'
import type { AnalyzingStep } from '../analyze/analyzingSteps'
import { queriesWithFixes } from '../benchmark/fixBenchmarkModel'
import {
  formatSharePercent,
  latencyText,
  rowChip,
  rowStatus,
  STATUS_CHIP,
  scorePoor,
  swatchColor,
} from '../databaseModel'
import { analyzability, notAnalyzedLine } from './analyzability'
import { StatusChip } from './QuerySection'
import { analysisLine } from './queryStatus'
import { SqlCode } from './SqlCode'

const ghost = {
  variant: 'primary',
  modifier: 'ghost',
  size: 'small',
} as const

const outlineLink = buttonStyles({
  variant: 'primary',
  modifier: 'outline',
  size: 'small',
}).root()

/** Splits a stored-analysis reference into registry hash and analysis id. */
function parseAnalysisRef(
  ref: string | null | undefined,
  fallbackHash: string
): { hash: string; analysisId: string } | null {
  if (!ref) return null
  const marker = '/analysis/'
  const at = ref.indexOf(marker)
  if (at < 0) return { hash: fallbackHash, analysisId: ref }
  const hash = ref.slice(0, at).replace(/\/+$/, '').split('/').pop()
  const analysisId = ref.slice(at + marker.length).replace(/\/+$/, '')
  return hash && analysisId ? { hash, analysisId } : null
}

function Block({
  title,
  children,
  className,
}: {
  title: string
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn('mt-6', className)}>
      <Text as="h4" level="caption" className="mb-2 text-content-layout-3">
        {title}
      </Text>
      {children}
    </div>
  )
}

function History({
  title,
  value,
  points,
  color,
  format,
}: {
  title: string
  value: string
  points: { x: number; y: number | null }[]
  color: string
  format: (value: number) => string
}) {
  return (
    <div className="relative">
      <Text as="h4" level="caption" className="mb-2 text-content-layout-3">
        {title}
      </Text>
      <Text
        as="span"
        level="caption"
        className="absolute top-0 right-0 tabular-nums text-content-layout-1"
      >
        {value}
      </Text>
      <div className="h-18">
        <MiniTrend
          ariaLabel={title}
          points={points}
          color={color}
          height={72}
          resolution={60}
          yMin="auto"
          formatValue={format}
        />
      </div>
    </div>
  )
}

/** The plan score out of 100, in the warning tone when it's poor. */
function PlanScore({ score }: { score: number }) {
  return (
    <Text
      as="p"
      level="body-small"
      className="mb-2 text-content-layout-2"
      data-testid="pane-plan-score"
    >
      Plan score{' '}
      <span
        className={cn(
          'font-medium tabular-nums',
          scorePoor(score)
            ? 'text-content-warning-soft'
            : 'text-content-layout-1'
        )}
      >
        {Math.round(score)}
      </span>
      <span className="text-content-layout-3">/100</span>
    </Text>
  )
}

function CopyFix({ sql }: { sql: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <Button
      variant="primary"
      modifier="solid"
      size="small"
      label={copied ? 'Copied' : 'Copy fix'}
      onClick={() => {
        void navigator.clipboard?.writeText(sql).then(() => {
          setCopied(true)
          window.setTimeout(() => setCopied(false), 2000)
        })
      }}
    />
  )
}

/** The latest finished analysis as the pane reads it; null until one exists. */
export function useQueryDigest(target: string, key: string, enabled: boolean) {
  return useQuery({
    queryKey: monitorKeys.queryAnalysis(target, key),
    queryFn: () => getQueryAnalysis(target, key),
    enabled: enabled && Boolean(target && key),
    staleTime: 60_000,
    retry: false,
  })
}

/** "Estimated" from a plan, "Measured" when the query ran. */
export function provenance(digest: DeepAnalysisDigest): {
  tag: string
  reason: string | null
} {
  const measured =
    digest.mode === 'analyze' || digest.fix?.expected_gain?.measured
  const source = paramSourceText(digest.param_source)
  if (measured) return { tag: 'Measured', reason: source }
  return {
    tag: 'Estimated',
    reason:
      source ??
      (digest.params?.length
        ? 'planner, with sampled values'
        : 'planner only, no sampled values'),
  }
}

export function readysetLine(
  digest: DeepAnalysisDigest | undefined,
  analyzed = true
): string {
  const facts = digest?.readyset
  if (!facts)
    return analyzed
      ? 'Checked for cache blockers after an analysis.'
      : 'Not checked yet. Analyze checks it for cache blockers.'
  if (facts.blockers.length)
    return `${facts.blockers.length} ${facts.blockers.length === 1 ? 'blocker' : 'blockers'} found.`
  return `No blockers found, ${facts.verified ? 'verified' : 'not verified'}.`
}

const WRITE = /^\s*(?:\/\*.*?\*\/\s*)?(?:INSERT|UPDATE|DELETE)\b/is

/** What every run of a write costs its table: the rows written and the indexes kept current. */
function WriteCost({
  target,
  query,
  range,
}: {
  target: string
  query: MonitorQuery
  range: ApiRange
}) {
  const cost = useMonitorQuery(target, query.key, range).data?.write_cost
  if (!cost) return null
  const rate =
    cost.table_writes_per_s < 0.05
      ? 'almost no writes'
      : `${cost.table_writes_per_s.toFixed(1)} rows written a second`
  return (
    <Block title="What each write costs">
      <div
        className="space-y-1 text-body-small text-content-layout-2"
        data-testid="write-cost"
      >
        <p>
          {cost.table} takes {rate}, and each write keeps{' '}
          {cost.indexes_maintained}{' '}
          {cost.indexes_maintained === 1 ? 'index' : 'indexes'} up to date.
        </p>
        {cost.hot_ratio != null ? (
          <p>
            {Math.round(cost.hot_ratio * 100)}% of its updates skip index
            maintenance because they change no indexed column.
          </p>
        ) : null}
        {cost.set_columns.length ? (
          <p>It changes {cost.set_columns.join(', ')}.</p>
        ) : null}
      </div>
    </Block>
  )
}

export interface QueryDetailProps {
  target: string
  query: MonitorQuery
  now: number
  /** The page's range, for the write's cost. */
  range: ApiRange
  onClose: () => void
  onPrev?: () => void
  onNext?: () => void
  /** A deep analysis is under way: its steps replace the fix. */
  analyzing?: { steps: AnalyzingStep[]; aboutSeconds: number | null } | null
  /** The index plan's statement for this query's open action: the fix shown and copied. */
  planSql?: string | null
  /** Monitoring is on: a held analysis may wait for a sign-in. */
  monitored?: boolean
  /** This query's waiting analysis job, for why it's on hold and when it starts. */
  job?: AnalyzeJob | null
  onAnalyzeAgain: () => void
  onBenchmark: () => void
  onCancel?: () => void
}

export function QueryDetail({
  target,
  query,
  now,
  range,
  onClose,
  onPrev,
  onNext,
  analyzing = null,
  planSql = null,
  monitored = true,
  job = null,
  onAnalyzeAgain,
  onBenchmark,
  onCancel,
}: QueryDetailProps) {
  const navigate = useNavigate()
  const status = rowStatus(query)
  const digest = useQueryDigest(
    target,
    query.key,
    !analyzing && status !== 'not_analyzed' && status !== 'stuck'
  )
  const color = swatchColor(query.color_index)
  const ref = parseAnalysisRef(digest.data?.analysis_ref, query.key)
  const fix = digest.data?.fix ?? null
  const fixSql = planSql ?? query.suggested_fix?.sql ?? fix?.sql ?? null
  const source = digest.data ? provenance(digest.data) : null
  const isWrite = WRITE.test(query.text)
  // A statement RDST never analyzes has no Analyze; it says why instead.
  const gate = analyzability(query)
  const canAnalyze = gate.ok && query.text_status !== 'unavailable'
  const line = analyzing || !canAnalyze ? null : analysisLine(query, job, now)
  const hasFix = queriesWithFixes([query]).length > 0
  const score =
    query.plan_score ??
    digest.data?.score ??
    digest.data?.efficiency_score ??
    null

  return (
    <SidePane
      aria-label={query.display_name}
      chip={
        <StatusChip
          chip={analyzing ? STATUS_CHIP.analyzing : rowChip(query, monitored)}
        />
      }
      meta={
        <>
          <span>{query.qps.toFixed(2)} calls/s</span>
          <span>{latencyText(query.mean_ms)} ms</span>
          <span>{formatSharePercent(query.db_time_share)} of time</span>
          {score != null && (
            <span
              data-testid="pane-header-score"
              className={cn(scorePoor(score) && 'text-content-warning-soft')}
            >
              Plan score {Math.round(score)}
            </span>
          )}
        </>
      }
      onPrev={onPrev}
      onNext={onNext}
      onClose={onClose}
      footer={
        analyzing ? (
          <Button {...ghost} label="Cancel" onClick={onCancel} />
        ) : (
          <>
            {ref ? (
              <Link
                to="/results"
                search={
                  {
                    query: query.text,
                    target,
                    hash: ref.hash,
                    key: query.key,
                    analysisId: ref.analysisId,
                  } as never
                }
                className={outlineLink}
              >
                Full analysis
              </Link>
            ) : null}
            {canAnalyze && (
              <Button
                {...ghost}
                label={
                  query.last_analysis?.finished_at != null
                    ? 'Analyze again'
                    : 'Analyze'
                }
                onClick={onAnalyzeAgain}
              />
            )}
            {hasFix && (
              <Button
                {...ghost}
                label="Estimate fix gain"
                onClick={onBenchmark}
              />
            )}
            {query.text_status !== 'unavailable' && (
              <Button
                {...ghost}
                label="Send to Ask"
                onClick={() =>
                  void navigate({
                    to: '/ask',
                    search: {
                      db: target,
                      sql: query.text,
                      params: digest.data?.params?.length
                        ? digest.data.params
                        : undefined,
                    } as never,
                  })
                }
              />
            )}
          </>
        )
      }
    >
      <Text as="h2" level="headline-3" className="mb-5 text-content-layout-1">
        {query.display_name}
      </Text>
      <div className="grid grid-cols-2 gap-4">
        <History
          title="Mean latency"
          value={`${latencyText(query.mean_ms)} ms`}
          points={query.series.map((point) => ({
            x: point.ts,
            y: point.mean_ms,
          }))}
          color={color}
          format={(value) => `${latencyText(value)} ms`}
        />
        <History
          title="Calls per second"
          value={query.qps.toFixed(2)}
          points={query.series.map((point) => ({ x: point.ts, y: point.qps }))}
          color={color}
          format={(value) => `${value.toFixed(2)}/s`}
        />
      </div>

      {!gate.ok && query.text_status !== 'unavailable' ? (
        <Text
          as="p"
          level="body-small"
          className="mt-6 text-content-layout-2"
          data-testid="not-analyzed-line"
        >
          <span className="font-medium text-content-layout-1">
            No deep analysis.
          </span>{' '}
          {notAnalyzedLine(query, gate)}
        </Text>
      ) : null}
      {line ? (
        <div
          className="mt-6 flex flex-wrap items-center gap-x-3 gap-y-2"
          data-testid="analysis-line"
        >
          <Text
            as="p"
            level="body-small"
            className={
              line.tone === 'negative'
                ? 'text-content-negative-soft'
                : 'text-content-layout-2'
            }
          >
            {line.text}
          </Text>
          {line.action ? (
            <Button
              variant="primary"
              modifier="outline"
              size="small"
              label={line.action === 'retry' ? 'Retry' : 'Analyze now'}
              onClick={onAnalyzeAgain}
            />
          ) : null}
        </div>
      ) : null}

      {isWrite && <WriteCost target={target} query={query} range={range} />}

      {analyzing ? (
        <div className="mt-6">
          <AnalyzingBlock
            steps={analyzing.steps}
            aboutSeconds={analyzing.aboutSeconds}
          />
        </div>
      ) : fixSql ? (
        <Block title="Fix">
          {score != null && <PlanScore score={score} />}
          <SqlCode sql={fixSql} testId="fix-sql" />
          <div className="mt-2.5 flex items-center gap-2">
            <CopyFix sql={fixSql} />
            {/CONCURRENTLY/i.test(fixSql) && (
              <Text as="span" level="caption" className="text-content-layout-3">
                Builds without blocking writes.
              </Text>
            )}
          </div>
          {fix?.why && (
            <Text
              as="p"
              level="body-small"
              className="mt-3 text-content-layout-2"
            >
              {fix.why}
            </Text>
          )}
          {digest.data && source && (
            <div className="mt-2 flex flex-wrap items-center gap-2 text-caption text-content-layout-3">
              <Tag
                size="small"
                variant="neutral"
                modifier="outline"
                className="rounded-pill"
                label={source.tag}
              />
              {source.reason && <span>{source.reason}</span>}
              {digest.data.analyzed_at != null && (
                <span>
                  analyzed {formatRelative(digest.data.analyzed_at, now)}
                </span>
              )}
            </div>
          )}
          {fix?.kind === 'index' && (
            <Text
              as="p"
              level="caption"
              className="mt-1.5 text-content-layout-3"
            >
              RDST confirms the fix when the index appears.
            </Text>
          )}
        </Block>
      ) : digest.data ? (
        <Block title={isWrite ? 'How it finds its rows' : 'Analysis'}>
          {score != null && <PlanScore score={score} />}
          <Text as="p" level="body-small" className="text-content-layout-2">
            {digest.data.headline}
          </Text>
          {source && (
            <div className="mt-2 flex flex-wrap items-center gap-2 text-caption text-content-layout-3">
              <Tag
                size="small"
                variant="neutral"
                modifier="outline"
                className="rounded-pill"
                label={source.tag}
              />
              {source.reason && <span>{source.reason}</span>}
              {digest.data.analyzed_at != null && (
                <span>
                  analyzed {formatRelative(digest.data.analyzed_at, now)}
                </span>
              )}
            </div>
          )}
        </Block>
      ) : null}

      <Block title="Query">
        <SqlCode sql={query.text} testId="query-sql" />
      </Block>

      {!analyzing && (
        <Block title="Readyset">
          <div className="flex items-center gap-2.5 text-body-small text-content-layout-2">
            <span
              aria-hidden="true"
              className={cn(
                'h-1.75 w-1.75 shrink-0 rounded-full',
                digest.data?.readyset?.verified
                  ? 'bg-content-positive-plain'
                  : 'bg-content-layout-3'
              )}
            />
            <span>{readysetLine(digest.data, status !== 'not_analyzed')}</span>
          </div>
        </Block>
      )}
    </SidePane>
  )
}
