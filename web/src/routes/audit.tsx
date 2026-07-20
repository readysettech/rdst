import { cn } from '@rs/tailwind-base'
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { BaseInputSelect } from '@rs/ui-new/base-input-select'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { CopyButton } from '@rs/ui-new/copy-button'
import { Icon } from '@rs/ui-new/icon'
import { AnimatePresence, m } from '@rs/ui-new/motion'
import { Scrollable } from '@rs/ui-new/scrollable'
import { Show } from '@rs/ui-new/show'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useDisclosure } from '@rs/ui-new/use-disclosure'
import { toast } from '@rs/ui-new/use-toast'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useState } from 'react'
import { TargetLockNotice } from '../components'
import { SQLDisplay } from '../components/SQLDisplay'
import { useTarget } from '../hooks/useTarget'
import { useTrialSource } from '../lib/trialQueries'
import { useAnthropicValidity } from '../lib/useAnthropicValidity'
import {
  fetchAuditRuns,
  fetchRunDetail,
  isWorkloadRun,
  useAuditCapture,
  useAuditRun,
} from '../lib/useAudit'
import { useTargetPasswordLock } from '../lib/useTargetPasswordLock'
import type {
  AuditReport,
  AuditRunSummary,
  HealthAnalysis,
  HealthFinding,
  WorkloadAnalysis,
  WorkloadQuery,
  WorkloadRun,
  WorkloadSummary,
} from '../types/audit'

const CAPTURE_DURATIONS: Array<{
  label: string
  long: string
  seconds: number
}> = [
  { label: '30s', long: '30 seconds', seconds: 30 },
  { label: '1m', long: '1 minute', seconds: 60 },
  { label: '5m', long: '5 minutes', seconds: 300 },
  { label: '15m', long: '15 minutes', seconds: 900 },
]

export const Route = createFileRoute('/audit')({
  component: AuditPage,
})

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function formatSizeMb(mb: number | undefined): string {
  if (mb === undefined || mb === null) return '-'
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`
  return `${Math.round(mb)} MB`
}

function formatUptime(seconds: number | undefined): string {
  if (!seconds) return '-'
  const days = Math.floor(seconds / 86400)
  if (days >= 1) return `${days}d ${Math.floor((seconds % 86400) / 3600)}h`
  const hours = Math.floor(seconds / 3600)
  if (hours >= 1) return `${hours}h ${Math.floor((seconds % 3600) / 60)}m`
  return `${Math.floor(seconds / 60)}m`
}

function formatMs(ms: number | undefined): string {
  if (ms === undefined || ms === null) return '-'
  if (ms < 1) return '<1ms'
  if (ms < 1000) return `${ms.toFixed(1)}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '-'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

function severityVariant(
  severity: string | undefined
): 'negative' | 'warning' | 'positive' | 'informative' {
  switch (severity) {
    case 'crit':
      return 'negative'
    case 'warn':
      return 'warning'
    case 'ok':
      return 'positive'
    default:
      return 'informative'
  }
}

function healthScoreColor(score: number): string {
  if (score >= 75) return 'text-content-positive-soft'
  if (score >= 60) return 'text-content-warning-soft'
  return 'text-content-negative-soft'
}

const VERDICT_LABELS: Record<
  string,
  {
    label: string
    variant: 'positive' | 'warning' | 'negative' | 'informative'
  }
> = {
  right_sized: { label: 'Right-sized', variant: 'positive' },
  oversized: { label: 'Oversized', variant: 'warning' },
  under_provisioned: { label: 'Under-provisioned', variant: 'negative' },
  unknown: { label: 'Unknown', variant: 'informative' },
}

// ---------------------------------------------------------------------------
// Report sub-components
// ---------------------------------------------------------------------------

function StatCard({
  label,
  value,
  hint,
}: {
  label: string
  value: string
  hint?: string
}) {
  return (
    <div className="bg-surface-layout-2/50 rounded-xl p-4 border border-border-layout-1">
      <VStack className="gap-1 items-start">
        <Text
          level="caption"
          className="text-content-layout-3 uppercase tracking-wider"
        >
          {label}
        </Text>
        <Text level="headline-5" className="text-content-layout-1 tabular-nums">
          {value}
        </Text>
        {hint && (
          <Text level="caption" className="text-content-layout-3">
            {hint}
          </Text>
        )}
      </VStack>
    </div>
  )
}

function SectionCard({
  icon,
  title,
  children,
}: {
  icon: IconStrokeName
  title: string
  children: React.ReactNode
}) {
  return (
    <Card className="w-full overflow-hidden">
      <Card.Content className="p-0">
        <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
          <HStack className="gap-2 items-center">
            <Icon
              name={icon}
              label={title}
              className="w-4 h-4 text-content-layout-3"
            />
            <Text
              level="overline"
              className="text-content-layout-3 uppercase tracking-wider"
            >
              {title}
            </Text>
          </HStack>
        </div>
        {children}
      </Card.Content>
    </Card>
  )
}

/**
 * A quiet, secondary metric card for the report's supporting-scores row —
 * lighter than a SectionCard, lets each card compose its own body. Sits at
 * content-layout weight so the raised verdict card stays the one focal point
 * (VIS-011, VIS-017).
 */
function SupportingCard({
  icon,
  title,
  children,
}: {
  icon: IconStrokeName
  title: string
  children: React.ReactNode
}) {
  return (
    <div className="bg-surface-layout-1 rounded-xl p-4 border border-border-layout-1">
      <VStack className="gap-2 items-start">
        <HStack className="gap-2 items-center">
          <Icon
            name={icon}
            label={title}
            className="w-3.5 h-3.5 text-content-layout-3"
          />
          <Text
            level="overline"
            className="text-content-layout-3 uppercase tracking-wider"
          >
            {title}
          </Text>
        </HStack>
        {children}
      </VStack>
    </div>
  )
}

function FindingsList({ findings }: { findings: HealthFinding[] }) {
  return (
    // gap-4 BETWEEN findings > gap-3 WITHIN a finding row (§1 grouping).
    <VStack className="gap-4 items-stretch">
      {findings.map((finding, index) => (
        <HStack key={index} className="gap-3 items-start">
          <Tag
            size="small"
            variant={severityVariant(finding.severity)}
            modifier="ghost"
            label={(finding.severity || 'info').toUpperCase()}
          />
          <VStack className="gap-0.5 items-start min-w-0">
            <Text level="label-small" className="text-content-layout-1">
              {finding.title}
            </Text>
            {finding.body && (
              <Text level="body-small" className="text-content-layout-2">
                {finding.body}
              </Text>
            )}
          </VStack>
        </HStack>
      ))}
    </VStack>
  )
}

/**
 * PRIMARY of the report view: the verdict, raised via the elevation-token scale
 * (VIS-075/076/080/105) — depth by a lightness step + a dark-tuned shadow, not
 * a border or a raw shadow-xl. The AI health-score badge is the single accent
 * of the report state (VIS-013/016/097). Degrades gracefully with no key: the
 * sizing verdict leads and a muted note points at Configure (H-4).
 */
function VerdictCard({ report }: { report: AuditReport }) {
  const sizing = report.sizing || {}
  const health = report.health_analysis
  const healthOk =
    !!health && !health.error && health.health_score !== undefined
  const verdict =
    VERDICT_LABELS[sizing.verdict || 'unknown'] || VERDICT_LABELS.unknown
  const summary =
    health?.health_score_rationale ||
    health?.executive_summary ||
    sizing.explanation

  return (
    <div className="rounded-[1.25rem] bg-surface-raised shadow-elevation-1 p-6">
      <VStack className="gap-4 items-stretch">
        <Text
          level="overline"
          className="text-content-layout-2 uppercase tracking-wider"
        >
          Verdict
        </Text>
        <HStack className="gap-3 items-center flex-wrap">
          {healthOk && (
            <HStack className="gap-2 items-baseline">
              <Text
                level="headline-1"
                className={`tabular-nums ${healthScoreColor(health!.health_score!)}`}
              >
                {health!.health_score}
              </Text>
              <Text level="body-small" className="text-content-layout-2">
                / 100
              </Text>
              <Tag
                variant={
                  health!.health_score! >= 75
                    ? 'positive'
                    : health!.health_score! >= 60
                      ? 'warning'
                      : 'negative'
                }
                modifier="ghost"
                label={health!.health_label || 'SCORE'}
              />
            </HStack>
          )}
          <Tag variant={verdict.variant} modifier="ghost" label={verdict.label} />
          {report.instance_class && (
            <Text level="mono-small" className="text-content-layout-2">
              {report.instance_class}
            </Text>
          )}
        </HStack>
        {summary && (
          <Text level="body-small" className="text-content-layout-2">
            {summary}
          </Text>
        )}
        {!healthOk && (
          <HStack className="gap-1.5 items-center flex-wrap">
            <Icon
              name="sparkles"
              label=""
              aria-hidden="true"
              className="w-3.5 h-3.5 text-content-layout-2 shrink-0"
            />
            <Text level="caption" className="text-content-layout-2">
              AI health score unavailable
              {health?.error ? ` — ${health.error}` : ''}.
            </Text>
            <Link to="/configure" className="hover:underline">
              <Text level="caption" className="text-content-primary-soft">
                Configure
              </Text>
            </Link>
          </HStack>
        )}
      </VStack>
    </div>
  )
}

/**
 * SECONDARY of the report view: the AI findings + recommended actions. Only
 * renders when a credential resolved (the score itself lives in the verdict
 * hero, so it is not repeated here).
 */
function HealthDetailSection({ health }: { health: HealthAnalysis }) {
  return (
    <SectionCard icon="document-validation" title="AI Analysis">
      <div className="p-5">
        <VStack className="gap-4 items-start min-w-0">
          {health.executive_summary && (
            <Text level="body-small" className="text-content-layout-2">
              {health.executive_summary}
            </Text>
          )}
          {(health.findings?.length || 0) > 0 && (
            <FindingsList findings={health.findings!} />
          )}
        </VStack>
        {(health.recommended_actions?.length || 0) > 0 && (
          <div className="mt-5 pt-5 border-t border-border-layout-1">
            <Text
              level="overline"
              className="text-content-layout-3 uppercase tracking-wider block mb-3"
            >
              Recommended Actions
            </Text>
            {/* gap-4 BETWEEN actions > gap-3 WITHIN a row (§1 grouping). */}
            <VStack className="gap-4 items-stretch">
              {health.recommended_actions!.map((action, index) => (
                <HStack key={index} className="gap-3 items-start">
                  <div className="w-6 h-6 rounded-md bg-surface-primary-soft flex items-center justify-center shrink-0">
                    <Text
                      level="caption"
                      className="text-content-primary-soft font-semibold"
                    >
                      {action.rank ?? index + 1}
                    </Text>
                  </div>
                  <VStack className="gap-0.5 items-start min-w-0">
                    <Text level="label-small" className="text-content-layout-1">
                      {action.title}
                    </Text>
                    <Text level="body-small" className="text-content-layout-2">
                      {action.body}
                    </Text>
                  </VStack>
                </HStack>
              ))}
            </VStack>
          </div>
        )}
      </div>
    </SectionCard>
  )
}

function AuditReportView({ report }: { report: AuditReport }) {
  const metrics = report.metrics || {}
  const sizing = report.sizing || {}
  const cacheOpp = report.cache_opportunity || {}
  const health = report.health_analysis
  const healthOk =
    !!health && !health.error && health.health_score !== undefined
  const verdict =
    VERDICT_LABELS[sizing.verdict || 'unknown'] || VERDICT_LABELS.unknown
  const topQueries = report.top_queries || []
  const [detailsOpen, setDetailsOpen] = useDisclosure({})

  return (
    <VStack className="gap-6 items-stretch w-full">
      {/* PRIMARY — the verdict (raised) */}
      <VerdictCard report={report} />

      {/* SECONDARY — three supporting scores */}
      <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
        <SupportingCard icon="sparkles" title="Cache Opportunity">
          <HStack className="gap-2 items-baseline">
            <Text
              level="headline-4"
              className="text-content-layout-1 tabular-nums"
            >
              {cacheOpp.score ?? '-'}
            </Text>
            <Tag
              size="small"
              variant={
                cacheOpp.level === 'high'
                  ? 'positive'
                  : cacheOpp.level === 'medium'
                    ? 'warning'
                    : 'informative'
              }
              modifier="ghost"
              label={(cacheOpp.level || 'unknown').toUpperCase()}
            />
          </HStack>
          {cacheOpp.explanation && (
            <Text
              level="caption"
              className="text-content-layout-3 line-clamp-2"
            >
              {cacheOpp.explanation}
            </Text>
          )}
        </SupportingCard>

        <SupportingCard icon="adjustment-horizontal" title="Sizing">
          <HStack className="gap-2 items-center flex-wrap">
            <Tag
              size="small"
              variant={verdict.variant}
              modifier="ghost"
              label={verdict.label}
            />
            {report.instance_class && (
              <Text level="mono-small" className="text-content-layout-3">
                {report.instance_class}
              </Text>
            )}
          </HStack>
          {sizing.potential_savings_usd != null &&
          sizing.potential_savings_usd > 0 ? (
            <Text level="caption" className="text-content-positive-soft">
              Save ~${sizing.potential_savings_usd.toFixed(0)}/mo
              {sizing.suggested_instance_class
                ? ` on ${sizing.suggested_instance_class}`
                : ''}
            </Text>
          ) : sizing.explanation ? (
            <Text
              level="caption"
              className="text-content-layout-3 line-clamp-2"
            >
              {sizing.explanation}
            </Text>
          ) : null}
        </SupportingCard>

        <SupportingCard icon="observe" title="Top Queries">
          <HStack className="gap-2 items-baseline">
            <Text
              level="headline-4"
              className="text-content-layout-1 tabular-nums"
            >
              {topQueries.length}
            </Text>
            <Text level="caption" className="text-content-layout-3">
              {topQueries.length === 1 ? 'hot spot' : 'hot spots'}
            </Text>
          </HStack>
        </SupportingCard>
      </div>

      {/* SECONDARY — AI findings + actions (only with a credential) */}
      {healthOk && <HealthDetailSection health={health!} />}

      {/* Health analysis degradation (no-key path) */}
      {health?.error && (
        <div className="px-5 py-3 bg-surface-warning-soft/20 border border-border-warning-soft rounded-xl">
          <HStack className="gap-2 items-center">
            <Icon
              name="alert"
              label="Warning"
              className="w-4 h-4 text-content-warning-soft"
            />
            <Text level="body-small" className="text-content-warning-soft">
              Health analysis unavailable: {health.error}
            </Text>
          </HStack>
        </div>
      )}

      {/* TERTIARY — raw numbers behind a Details disclosure */}
      <div className="border-t border-border-layout-1 pt-4">
        <button
          type="button"
          aria-expanded={detailsOpen}
          onClick={() => setDetailsOpen(!detailsOpen)}
          className="group flex items-center gap-2 text-content-layout-2 hover:text-content-layout-1 transition-colors"
        >
          <Icon
            name="chevron-right"
            label=""
            aria-hidden="true"
            className={`w-4 h-4 transition-transform ${
              detailsOpen ? 'rotate-90' : ''
            }`}
          />
          <Text level="label-small">
            {detailsOpen ? 'Hide details' : 'Details'} — overview metrics, full
            query list
          </Text>
        </button>

        {detailsOpen && (
          <VStack className="gap-6 items-stretch mt-4">
            {/* Overview */}
            <SectionCard icon="database" title="Overview">
              <div className="p-5 grid grid-cols-2 tablet:grid-cols-4 gap-4">
                <StatCard
                  label="Engine"
                  value={report.engine || '-'}
                  hint={metrics.server_version}
                />
                <StatCard
                  label="Database Size"
                  value={formatSizeMb(metrics.database_size_mb)}
                  hint={metrics.storage_type || undefined}
                />
                <StatCard
                  label="Uptime"
                  value={formatUptime(metrics.uptime_seconds)}
                />
                <StatCard
                  label="Connections"
                  value={`${metrics.active_connections ?? '-'} / ${metrics.max_connections ?? '-'}`}
                  hint={
                    metrics.connection_utilization_pct !== undefined
                      ? `${metrics.connection_utilization_pct.toFixed(0)}% utilized`
                      : undefined
                  }
                />
                <StatCard
                  label="Cache Hit Rate"
                  value={
                    metrics.cache_hit_rate !== undefined
                      ? `${metrics.cache_hit_rate.toFixed(1)}%`
                      : '-'
                  }
                />
                <StatCard
                  label="Read / Write"
                  value={
                    metrics.read_pct !== undefined
                      ? `${metrics.read_pct.toFixed(0)}% / ${(metrics.write_pct ?? 0).toFixed(0)}%`
                      : '-'
                  }
                />
                <StatCard
                  label="Tracked Queries"
                  value={`${metrics.tracked_query_count ?? '-'}`}
                />
                <StatCard
                  label="Storage"
                  value={
                    metrics.storage_allocated_gb
                      ? `${metrics.storage_allocated_gb.toFixed(0)} GB`
                      : '-'
                  }
                  hint={
                    metrics.storage_used_pct != null
                      ? `${metrics.storage_used_pct}% used`
                      : undefined
                  }
                />
              </div>
            </SectionCard>

            {/* Top queries */}
            <Show when={topQueries.length > 0}>
              <SectionCard
                icon="observe"
                title={`Top Queries (${topQueries.length})`}
              >
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="bg-surface-layout-2/30">
                        <th className="px-4 py-3 text-left text-xs text-content-layout-3 uppercase tracking-wider font-medium">
                          Query
                        </th>
                        <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-24">
                          Calls
                        </th>
                        <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-24">
                          Avg
                        </th>
                        <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-24">
                          % Time
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-layout-1">
                      {topQueries.map((query, index) => (
                        <tr
                          key={query.query_hash || index}
                          className="hover:bg-surface-layout-2/50 transition-colors"
                        >
                          <td className="px-4 py-3">
                            <div className="bg-surface-layout-2 rounded-lg max-w-2xl">
                              <Scrollable className="max-h-24">
                                <div className="px-3 py-2">
                                  <SQLDisplay
                                    sql={query.query_text || ''}
                                    wrap
                                  />
                                </div>
                              </Scrollable>
                            </div>
                          </td>
                          <td className="px-4 py-3 text-right">
                            <Text
                              level="mono-small"
                              className="text-content-layout-2 tabular-nums"
                            >
                              {query.calls?.toLocaleString() ?? '-'}
                            </Text>
                          </td>
                          <td className="px-4 py-3 text-right">
                            <Text
                              level="mono-small"
                              className="text-content-layout-2 tabular-nums"
                            >
                              {formatMs(query.avg_time_ms)}
                            </Text>
                          </td>
                          <td className="px-4 py-3 text-right">
                            <Text
                              level="mono-small"
                              className="text-content-layout-2 tabular-nums"
                            >
                              {query.pct_total_time != null
                                ? `${query.pct_total_time}%`
                                : '-'}
                            </Text>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </SectionCard>
            </Show>
          </VStack>
        )}
      </div>
    </VStack>
  )
}

// ---------------------------------------------------------------------------
// Workload capture report
// ---------------------------------------------------------------------------

function formatDuration(seconds: number | undefined): string {
  if (!seconds) return '-'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const mins = Math.floor(seconds / 60)
  const rem = Math.round(seconds % 60)
  return rem ? `${mins}m ${rem}s` : `${mins}m`
}

function BulletList({ items }: { items: string[] }) {
  return (
    <VStack className="gap-2 items-stretch">
      {items.map((item, index) => (
        <HStack key={index} className="gap-2 items-start">
          <div className="w-1.5 h-1.5 rounded-full bg-content-layout-3 mt-2 shrink-0" />
          <Text level="body-small" className="text-content-layout-2 min-w-0">
            {item}
          </Text>
        </HStack>
      ))}
    </VStack>
  )
}

function WorkloadQueriesTable({ queries }: { queries: WorkloadQuery[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr className="bg-surface-layout-2/30">
            <th className="px-4 py-3 text-left text-xs text-content-layout-3 uppercase tracking-wider font-medium">
              Query
            </th>
            <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-24">
              Calls
            </th>
            <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-24">
              Avg
            </th>
            <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-24">
              % Time
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-layout-1">
          {queries.map((query, index) => (
            <tr
              key={query.query_hash || index}
              className="hover:bg-surface-layout-2/50 transition-colors"
            >
              <td className="px-4 py-3">
                <div className="bg-surface-layout-2 rounded-lg max-w-2xl">
                  <Scrollable className="max-h-24">
                    <div className="px-3 py-2">
                      <SQLDisplay
                        sql={query.query_text || query.normalized_query || ''}
                        wrap
                      />
                    </div>
                  </Scrollable>
                </div>
              </td>
              <td className="px-4 py-3 text-right">
                <Text
                  level="mono-small"
                  className="text-content-layout-2 tabular-nums"
                >
                  {query.calls?.toLocaleString() ?? '-'}
                </Text>
              </td>
              <td className="px-4 py-3 text-right">
                <Text
                  level="mono-small"
                  className="text-content-layout-2 tabular-nums"
                >
                  {formatMs(query.avg_time_ms)}
                </Text>
              </td>
              <td className="px-4 py-3 text-right">
                <Text
                  level="mono-small"
                  className="text-content-layout-2 tabular-nums"
                >
                  {query.pct_total_time != null
                    ? `${query.pct_total_time.toFixed(1)}%`
                    : '-'}
                </Text>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function WorkloadAnalysisView({ analysis }: { analysis: WorkloadAnalysis }) {
  const bottlenecks = analysis.top_bottlenecks || []
  const indexRecs = analysis.index_recommendations || []
  const cachingCandidates = analysis.caching_candidates || []
  const capacityInsights = analysis.capacity_insights || []
  const priorities = analysis.optimization_priorities || []
  const hasScore =
    analysis.health_score !== undefined && analysis.health_score !== null

  return (
    <VStack className="gap-6 items-stretch w-full">
      <SectionCard icon="document-validation" title="Workload Analysis">
        <div className="p-5">
          <div className="grid grid-cols-[auto_1fr] gap-6 items-start">
            {hasScore && (
              <VStack className="gap-1 items-center px-4">
                <Text
                  level="headline-1"
                  className={`tabular-nums ${healthScoreColor(analysis.health_score!)}`}
                >
                  {analysis.health_score}
                </Text>
                <Tag
                  variant={
                    analysis.health_score! >= 75
                      ? 'positive'
                      : analysis.health_score! >= 60
                        ? 'warning'
                        : 'negative'
                  }
                  modifier="ghost"
                  label="SCORE"
                />
              </VStack>
            )}
            <VStack className="gap-3 items-start min-w-0">
              {analysis.workload_characterization && (
                <Text level="body-small" className="text-content-layout-2">
                  {analysis.workload_characterization}
                </Text>
              )}
              {analysis.read_write_ratio && (
                <HStack className="gap-2 items-center">
                  <Text
                    level="caption"
                    className="text-content-layout-3 uppercase tracking-wider"
                  >
                    Read / Write
                  </Text>
                  <Tag
                    size="small"
                    variant="informative"
                    modifier="ghost"
                    label={analysis.read_write_ratio}
                  />
                </HStack>
              )}
            </VStack>
          </div>
        </div>
      </SectionCard>

      {bottlenecks.length > 0 && (
        <SectionCard icon="alert" title="Top Bottlenecks">
          <div className="p-5">
            <BulletList items={bottlenecks} />
          </div>
        </SectionCard>
      )}

      {indexRecs.length > 0 && (
        <SectionCard
          icon="adjustment-horizontal"
          title={`Index Recommendations (${indexRecs.length})`}
        >
          <div className="p-5">
            <VStack className="gap-4 items-stretch">
              {indexRecs.map((rec, index) => (
                <VStack key={index} className="gap-2 items-stretch">
                  {rec.sql && (
                    <div className="bg-surface-layout-2 rounded-lg overflow-hidden">
                      <HStack className="justify-between items-center px-3 py-2 border-b border-border-layout-1">
                        <Text
                          level="caption"
                          className="text-content-layout-3 uppercase tracking-wider"
                        >
                          {rec.table ? rec.table : 'DDL'}
                        </Text>
                        <CopyButton text={rec.sql} />
                      </HStack>
                      <div className="px-3 py-2 overflow-x-auto">
                        <SQLDisplay sql={rec.sql} />
                      </div>
                    </div>
                  )}
                  {rec.reason && (
                    <Text level="body-small" className="text-content-layout-2">
                      {rec.reason}
                    </Text>
                  )}
                  {rec.estimated_impact && (
                    <Text
                      level="caption"
                      className="text-content-positive-soft"
                    >
                      Estimated impact: {rec.estimated_impact}
                    </Text>
                  )}
                </VStack>
              ))}
            </VStack>
          </div>
        </SectionCard>
      )}

      <div className="grid grid-cols-1 tablet:grid-cols-2 gap-6">
        {cachingCandidates.length > 0 && (
          <SectionCard icon="sparkles" title="Caching Candidates">
            <div className="p-5">
              <BulletList items={cachingCandidates} />
            </div>
          </SectionCard>
        )}
        {capacityInsights.length > 0 && (
          <SectionCard icon="database" title="Capacity Insights">
            <div className="p-5">
              <BulletList items={capacityInsights} />
            </div>
          </SectionCard>
        )}
      </div>

      {priorities.length > 0 && (
        <SectionCard icon="observe" title="Optimization Priorities">
          <div className="p-5">
            <VStack className="gap-3 items-stretch">
              {priorities.map((item, index) => (
                <HStack key={index} className="gap-3 items-start">
                  <div className="w-6 h-6 rounded-md bg-surface-primary-soft flex items-center justify-center shrink-0">
                    <Text
                      level="caption"
                      className="text-content-primary-soft font-semibold"
                    >
                      {index + 1}
                    </Text>
                  </div>
                  <Text
                    level="body-small"
                    className="text-content-layout-2 min-w-0"
                  >
                    {item}
                  </Text>
                </HStack>
              ))}
            </VStack>
          </div>
        </SectionCard>
      )}
    </VStack>
  )
}

/**
 * Renders a workload capture report — either the live capture result (from the
 * SSE `complete` event's summary/analysis) or a saved WorkloadRun loaded from
 * history. Callers pass whichever fields they have.
 */
function WorkloadReportView({
  summary,
  analysis,
  queries,
  durationSeconds,
}: {
  summary?: WorkloadSummary | null
  analysis?: WorkloadAnalysis | null
  queries: WorkloadQuery[]
  durationSeconds: number | undefined
}) {
  return (
    <VStack className="gap-6 items-stretch w-full">
      <SectionCard icon="observe" title="Capture Summary">
        <div className="p-5 grid grid-cols-2 tablet:grid-cols-4 gap-4">
          <StatCard label="Duration" value={formatDuration(durationSeconds)} />
          <StatCard
            label="Unique Queries"
            value={`${summary?.unique_queries ?? queries.length}`}
          />
          <StatCard
            label="Executions"
            value={
              summary?.total_executions != null
                ? summary.total_executions.toLocaleString()
                : '-'
            }
          />
          <StatCard
            label="Total Query Time"
            value={formatMs(summary?.total_query_time_ms)}
          />
        </div>
      </SectionCard>

      {analysis && <WorkloadAnalysisView analysis={analysis} />}

      <Show when={queries.length > 0}>
        <SectionCard
          icon="observe"
          title={`Captured Queries (${queries.length})`}
        >
          <WorkloadQueriesTable queries={queries} />
        </SectionCard>
      </Show>
    </VStack>
  )
}

function WorkloadRunView({ run }: { run: WorkloadRun }) {
  const queries = run.queries || []
  const summary: WorkloadSummary = {
    unique_queries: run.total_queries ?? queries.length,
    total_executions: queries.reduce((acc, q) => acc + (q.calls ?? 0), 0),
    total_query_time_ms: run.total_query_time_ms,
    duration_seconds: run.duration_seconds,
    queries,
  }
  return (
    <WorkloadReportView
      summary={summary}
      analysis={run.analysis}
      queries={queries}
      durationSeconds={run.duration_seconds}
    />
  )
}

// ---------------------------------------------------------------------------
// Idle-view launcher
// ---------------------------------------------------------------------------

/**
 * Pre-run status: the AI-insights dependency, surfaced up front instead of
 * after a wasted run (H-4; USE-065, USE-077). Renders the *actual* key state,
 * not a static "needs a key": a valid key reads "ready", the pre-resolve window
 * reads "Checking…", and only a missing/rejected key shows the config-needed
 * warning + Configure link. Reuses the cached `useAnthropicValidity` probe
 * (C-04) gated on key presence — no new endpoint. [C-09]
 */
function AiInsightsBadge() {
  const { anthropicRequirement, isTrialSource, trialStatus } = useTrialSource()
  const isTrialExhausted =
    isTrialSource &&
    (trialStatus?.status === 'exhausted' || trialStatus?.active === false)
  // Presence gates the probe so we never ping the provider without a key.
  const hasKey =
    (Boolean(anthropicRequirement?.satisfied) || isTrialSource) &&
    !isTrialExhausted
  const validityQuery = useAnthropicValidity(hasKey)
  const validity = validityQuery.data
  // Enabled-but-unresolved is the neutral "unknown" window, not a green claim.
  const checking = hasKey && validityQuery.isFetching && !validity

  // Valid → say so (accent positive); unknown → neutral "Checking…".
  if (validity?.valid) {
    return (
      <HStack className="gap-2 items-center rounded-xl border border-border-positive-soft bg-surface-positive-soft/30 px-3 py-1.5">
        <Icon
          name="sparkles"
          label=""
          aria-hidden="true"
          className="w-3.5 h-3.5 text-content-positive-soft shrink-0"
        />
        <Text level="caption" className="text-content-positive-soft">
          AI insights: ready
        </Text>
      </HStack>
    )
  }

  if (checking) {
    return (
      <HStack className="gap-2 items-center rounded-xl border border-border-layout-1 bg-surface-layout-2/40 px-3 py-1.5">
        <Icon
          name="sparkles"
          label=""
          aria-hidden="true"
          className="w-3.5 h-3.5 text-content-layout-3 shrink-0"
        />
        <Text level="caption" className="text-content-layout-3">
          AI insights: checking…
        </Text>
      </HStack>
    )
  }

  // Missing or rejected → the original config-needed copy + Configure link.
  return (
    <HStack className="gap-2 items-center rounded-xl border border-border-warning-soft bg-surface-warning-soft/30 px-3 py-1.5">
      <Icon
        name="sparkles"
        label=""
        aria-hidden="true"
        className="w-3.5 h-3.5 text-content-warning-soft shrink-0"
      />
      <Text level="caption" className="text-content-warning-soft">
        AI insights: needs a key
      </Text>
      <Text level="caption" className="text-content-layout-3">
        ·
      </Text>
      <Link
        to="/configure"
        className="hover:underline inline-flex items-center gap-0.5"
      >
        <Text level="caption" className="text-content-warning-soft">
          Configure
        </Text>
        <Icon
          name="chevron-right"
          label=""
          aria-hidden="true"
          className="w-3 h-3 text-content-warning-soft"
        />
      </Link>
    </HStack>
  )
}

/**
 * Data-handling disclosure, adjacent to the actions (H-1; USE-065, USE-066).
 * Corrects the discovery-era fear that audit emails the report — the web path
 * does not. (Presentational only: the queries_saved event stays untouched.)
 */
function DataHandlingNote() {
  return (
    <HStack className="gap-2 items-start">
      <Icon
        name="info"
        label=""
        aria-hidden="true"
        className="w-3.5 h-3.5 mt-0.5 text-content-layout-3 shrink-0"
      />
      <Text level="caption" className="text-content-layout-3">
        Runs locally on your machine. The report is saved here — nothing is
        emailed. Captured queries are added to your Saved Queries.
      </Text>
    </HStack>
  )
}

/**
 * One path (icon, title, meta, optional controls, action). The emphasized card
 * is raised via the elevation token and holds the single primary action; the
 * quieter card recedes to content-layout weight. Emphasis is bought by
 * de-emphasizing the neighbor, not by adding colour (VIS-016, VIS-108/109).
 */
function ModeCard({
  emphasized = false,
  icon,
  title,
  meta,
  controls,
  action,
}: {
  emphasized?: boolean
  icon: IconStrokeName
  title: string
  meta: string
  controls?: React.ReactNode
  action: React.ReactNode
}) {
  return (
    <div
      className={cn(
        'rounded-[1.25rem] p-5 h-full',
        emphasized
          ? 'bg-surface-raised shadow-elevation-1 border border-border-primary-soft'
          : 'bg-surface-layout-1 border border-border-layout-1'
      )}
    >
      <VStack className="gap-4 items-stretch h-full justify-between">
        <VStack className="gap-3 items-start">
          <div
            className={cn(
              'w-10 h-10 rounded-xl flex items-center justify-center',
              emphasized ? 'bg-surface-primary-soft' : 'bg-surface-layout-2'
            )}
          >
            <Icon
              name={icon}
              label=""
              aria-hidden="true"
              className={cn(
                'w-5 h-5',
                emphasized
                  ? 'text-content-primary-soft'
                  : 'text-content-layout-2'
              )}
            />
          </div>
          <VStack className="gap-1 items-start">
            <Text level="subtitle-2" className="text-content-layout-1">
              {title}
            </Text>
            <Text level="body-small" className="text-content-layout-2">
              {meta}
            </Text>
          </VStack>
          {controls}
        </VStack>
        {action}
      </VStack>
    </div>
  )
}

/**
 * The idle-view launcher: two self-explanatory mode cards. `hero` renders the
 * polished empty-state above them (illustration + one-line job); the compact
 * form ("Run another check") sits under a report so both paths stay reachable
 * without a second wall of controls (VIS-102, VIS-011; H-2).
 */
function RunLauncher({
  hero,
  target,
  disabled,
  runLoading,
  runLabel,
  onRun,
  onCapture,
  captureDuration,
  onDurationChange,
}: {
  hero: boolean
  target: string | null
  disabled: boolean
  runLoading: boolean
  runLabel: string
  onRun: () => void
  onCapture: () => void
  captureDuration: number
  onDurationChange: (seconds: number) => void
}) {
  const durationOptions = CAPTURE_DURATIONS.map((d) => ({
    value: String(d.seconds),
    label: d.long,
  }))

  return (
    <VStack className="gap-6 items-stretch">
      {hero ? (
        <VStack className="gap-3 items-center text-center pt-2">
          <div className="w-14 h-14 rounded-2xl bg-surface-primary-soft flex items-center justify-center">
            <Icon
              name="document-validation"
              label=""
              aria-hidden="true"
              className="w-7 h-7 text-content-primary-soft"
            />
          </div>
          <Text level="headline-4" className="text-content-layout-1 max-w-md">
            One check. A plain-English verdict on how "{target}" is sized, where
            it's slow, and what to cache.
          </Text>
        </VStack>
      ) : (
        <Text
          level="overline"
          className="text-content-layout-3 uppercase tracking-wider"
        >
          Run another check
        </Text>
      )}

      <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
        {/* PRIMARY — instant snapshot */}
        <ModeCard
          emphasized
          icon="speedometer"
          title="Instant snapshot"
          meta="~10s · reads current metrics now"
          action={
            <Button
              variant="primary"
              modifier="solid"
              label={runLabel}
              icon="play"
              iconPosition="left"
              onClick={onRun}
              loading={runLoading}
              disabled={disabled}
              fullWidth
            />
          }
        />

        {/* SECONDARY — live capture */}
        <ModeCard
          icon="observe"
          title="Live capture"
          meta="records real traffic, then analyzes what ran"
          controls={
            <VStack className="gap-1.5 items-start w-full">
              <Text level="caption" className="text-content-layout-3">
                Record for
              </Text>
              <BaseInputSelect
                name="capture-duration"
                options={durationOptions}
                value={String(captureDuration)}
                onValueChange={(v) => onDurationChange(Number(v))}
                disabled={disabled}
                triggerClassName="h-9"
              />
            </VStack>
          }
          action={
            <Button
              variant="rising"
              modifier="outline"
              label="Start capture"
              icon="observe"
              iconPosition="left"
              onClick={onCapture}
              disabled={disabled}
              fullWidth
            />
          }
        />
      </div>

      <DataHandlingNote />
    </VStack>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

function AuditPage() {
  const queryClient = useQueryClient()
  const { target } = useTarget()
  const passwordLock = useTargetPasswordLock(target)

  const {
    run,
    state: runState,
    statusMessage,
    report: liveReport,
    error: runError,
    reset,
  } = useAuditRun()

  const {
    run: runCapture,
    cancel: cancelCapture,
    reset: resetCapture,
    state: captureState,
    statusMessage: captureStatus,
    analysisWarning,
    progress: captureProgress,
    result: captureResult,
    error: captureError,
  } = useAuditCapture()

  const [captureDuration, setCaptureDuration] = useState<number>(60)

  // A run loaded from history; cleared when a new live run starts.
  const [loadedReport, setLoadedReport] = useState<AuditReport | null>(null)
  const [loadedWorkload, setLoadedWorkload] = useState<WorkloadRun | null>(null)
  const [loadedRunId, setLoadedRunId] = useState<string | null>(null)
  const [loadingRunId, setLoadingRunId] = useState<string | null>(null)

  const isRunning = runState === 'running'
  const isCapturing =
    captureState === 'capturing' || captureState === 'analyzing'
  const busy = isRunning || isCapturing
  const report = loadedReport ?? liveReport

  const { data: runsData, refetch: refetchRuns } = useQuery({
    queryKey: ['audit-runs', target],
    queryFn: () => fetchAuditRuns(target!),
    enabled: !!target,
    staleTime: 30_000,
  })
  const runs = runsData?.runs || []

  const clearLoaded = () => {
    setLoadedReport(null)
    setLoadedWorkload(null)
    setLoadedRunId(null)
  }

  const handleRun = async () => {
    if (!target) return
    clearLoaded()
    resetCapture()
    await run(target)
    queryClient.invalidateQueries({ queryKey: ['audit-runs', target] })
    refetchRuns()
  }

  const handleCapture = async () => {
    if (!target) return
    clearLoaded()
    reset()
    await runCapture(target, { duration: captureDuration })
    queryClient.invalidateQueries({ queryKey: ['audit-runs', target] })
    refetchRuns()
  }

  const handleLoadRun = async (summary: AuditRunSummary) => {
    setLoadingRunId(summary.run_id)
    try {
      const data = await fetchRunDetail(summary.run_id)
      reset()
      resetCapture()
      if (isWorkloadRun(data)) {
        setLoadedReport(null)
        setLoadedWorkload(data)
      } else {
        setLoadedWorkload(null)
        setLoadedReport(data as AuditReport)
      }
      setLoadedRunId(summary.run_id)
    } catch (err) {
      toast({
        title: 'Failed to load run',
        description: err instanceof Error ? err.message : String(err),
        variant: 'negative',
      })
    } finally {
      setLoadingRunId(null)
    }
  }

  // A capture result currently occupies the stage (live, not a loaded run).
  const captureComplete =
    captureState === 'complete' && !!captureResult && !loadedRunId
  // Something already fills the stage (report / capture result / loaded run).
  const showStageResult =
    (!!report && !loadedWorkload) || captureComplete || !!loadedWorkload
  // Idle, pre-run: the empty-state hero + the AI-insights badge belong here.
  const isIdle = !busy && !showStageResult

  const launcherDisabled = busy || !target || passwordLock.isLocked
  const runActionLabel = report ? 'Run new audit' : 'Run audit'

  return (
    <div className="space-y-6 w-full">
      {/* Hero Header */}
      <m.div
        className="space-y-4"
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className="justify-between items-start gap-4 flex-wrap">
          <HStack className="gap-4 items-center">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center">
              <Icon
                name="document-validation"
                label="Health Check"
                className="w-6 h-6 text-content-info-soft"
              />
            </div>
            <VStack className="gap-1 items-start">
              <Text
                as="h1"
                level="headline-3"
                className="text-content-layout-1"
              >
                Health Check
              </Text>
              <Text level="body-small" className="text-content-layout-3">
                Full audit of "{target}": sizing verdict, slow spots, and cache
                opportunities.
              </Text>
            </VStack>
          </HStack>
          {/* Status slot: pre-run AI-insights dependency (H-4). */}
          {isIdle && <AiInsightsBadge />}
        </HStack>
      </m.div>

      {/* Password lock */}
      {passwordLock.isLocked && (
        <TargetLockNotice
          message={passwordLock.message}
          requirements={passwordLock.missingTargetRequirements}
          keyringAvailable={passwordLock.keyringAvailable}
        />
      )}

      {/* Run progress */}
      <AnimatePresence>
        {isRunning && (
          <m.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3 }}
          >
            <Card className="w-full">
              <Card.Content>
                <HStack className="gap-3 items-center p-2">
                  <Spinner size="base" />
                  <Text level="body-small" className="text-content-layout-2">
                    {statusMessage || 'Auditing ' + (target ?? '') + '…'}
                  </Text>
                </HStack>
              </Card.Content>
            </Card>
          </m.div>
        )}
      </AnimatePresence>

      {/* Run error */}
      <Show when={runState === 'error' && !!runError}>
        <div className="px-5 py-3 bg-surface-negative-soft/30 border border-border-negative-soft rounded-xl">
          <HStack className="gap-2 items-center">
            <Icon
              name="alert"
              label="Error"
              className="w-4 h-4 text-content-negative-soft"
            />
            <Text level="body-small" className="text-content-negative-soft">
              {runError}
            </Text>
          </HStack>
        </div>
      </Show>

      {/* Live capture panel */}
      <AnimatePresence>
        {isCapturing && (
          <m.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3 }}
          >
            <Card className="w-full">
              <Card.Content>
                <VStack className="gap-4 items-stretch p-2">
                  <HStack className="gap-3 items-center justify-between flex-wrap">
                    <HStack className="gap-3 items-center">
                      <Spinner size="base" />
                      <Text
                        level="body-small"
                        className="text-content-layout-2"
                      >
                        {captureState === 'analyzing'
                          ? captureStatus || 'Analyzing captured workload...'
                          : captureStatus || 'Capturing live workload...'}
                      </Text>
                    </HStack>
                    <HStack className="gap-3 items-center">
                      {captureProgress && (
                        <Text
                          level="mono-small"
                          className="text-content-layout-3 tabular-nums shrink-0"
                        >
                          {Math.round(captureProgress.elapsedSeconds)}s
                          {captureProgress.totalSeconds
                            ? ` / ${captureProgress.totalSeconds}s`
                            : ''}
                        </Text>
                      )}
                      <Button
                        variant="negative"
                        modifier="outline"
                        size="small"
                        label="Cancel"
                        icon="close"
                        iconPosition="left"
                        onClick={cancelCapture}
                      />
                    </HStack>
                  </HStack>

                  {captureProgress?.totalSeconds ? (
                    <div className="h-2 w-full rounded-full bg-surface-layout-2 overflow-hidden">
                      <div
                        className="h-full rounded-full bg-surface-primary-solid transition-[width] duration-500 ease-linear"
                        style={{
                          width: `${Math.min(
                            100,
                            (captureProgress.elapsedSeconds /
                              captureProgress.totalSeconds) *
                              100
                          )}%`,
                        }}
                      />
                    </div>
                  ) : null}

                  {captureProgress && (
                    <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
                      <StatCard
                        label="Unique Queries"
                        value={`${captureProgress.uniqueQueries}`}
                      />
                      <StatCard
                        label="Executions"
                        value={captureProgress.totalExecutions.toLocaleString()}
                      />
                      <StatCard
                        label="TPS"
                        value={captureProgress.tps.toFixed(1)}
                      />
                      <StatCard
                        label="Cache Hit"
                        value={
                          captureProgress.cacheHitRatio != null
                            ? `${captureProgress.cacheHitRatio.toFixed(1)}%`
                            : '-'
                        }
                        hint={`${captureProgress.activeConnections} conns`}
                      />
                    </div>
                  )}
                </VStack>
              </Card.Content>
            </Card>
          </m.div>
        )}
      </AnimatePresence>

      {/* Capture analysis warning (graceful degradation) */}
      <Show when={!!analysisWarning}>
        <div className="px-5 py-3 bg-surface-warning-soft/20 border border-border-warning-soft rounded-xl">
          <HStack className="gap-2 items-center">
            <Icon
              name="alert"
              label="Warning"
              className="w-4 h-4 text-content-warning-soft"
            />
            <Text level="body-small" className="text-content-warning-soft">
              {analysisWarning}
            </Text>
          </HStack>
        </div>
      </Show>

      {/* Capture error */}
      <Show when={captureState === 'error' && !!captureError}>
        <div className="px-5 py-3 bg-surface-negative-soft/30 border border-border-negative-soft rounded-xl">
          <HStack className="gap-2 items-center">
            <Icon
              name="alert"
              label="Error"
              className="w-4 h-4 text-content-negative-soft"
            />
            <Text level="body-small" className="text-content-negative-soft">
              {captureError}
            </Text>
          </HStack>
        </div>
      </Show>

      {/* Idle empty-state hero + two mode cards (the launcher) */}
      {isIdle && (
        <m.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          <RunLauncher
            hero
            target={target}
            disabled={launcherDisabled}
            runLoading={isRunning}
            runLabel={runActionLabel}
            onRun={handleRun}
            onCapture={handleCapture}
            captureDuration={captureDuration}
            onDurationChange={setCaptureDuration}
          />
        </m.div>
      )}

      {/* Live capture result */}
      {captureComplete && (
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
        >
          <VStack className="gap-3 items-stretch">
            <HStack className="gap-2 items-center">
              <Text
                level="overline"
                className="text-content-layout-3 uppercase tracking-wider"
              >
                Latest capture: {captureResult!.runId}
              </Text>
            </HStack>
            <WorkloadReportView
              summary={captureResult!.summary}
              analysis={captureResult!.analysis}
              queries={captureResult!.summary?.queries || []}
              durationSeconds={
                captureResult!.summary?.duration_seconds ?? captureDuration
              }
            />
          </VStack>
        </m.div>
      )}

      {/* Loaded workload run (from history) */}
      {loadedWorkload && (
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
        >
          <VStack className="gap-3 items-stretch">
            <HStack className="justify-between items-center">
              <HStack className="gap-2 items-center">
                <Text
                  level="overline"
                  className="text-content-layout-3 uppercase tracking-wider"
                >
                  Saved capture: {loadedRunId}
                </Text>
                <Text level="caption" className="text-content-layout-3">
                  {formatDate(loadedWorkload.started_at)}
                </Text>
              </HStack>
            </HStack>
            <WorkloadRunView run={loadedWorkload} />
          </VStack>
        </m.div>
      )}

      {/* Report */}
      {report && !isRunning && !loadedWorkload && (
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
        >
          <VStack className="gap-3 items-stretch">
            <HStack className="justify-between items-center">
              <HStack className="gap-2 items-center">
                <Text
                  level="overline"
                  className="text-content-layout-3 uppercase tracking-wider"
                >
                  {loadedRunId ? `Saved run: ${loadedRunId}` : 'Latest audit'}
                </Text>
                <Text level="caption" className="text-content-layout-3">
                  Saved · {formatDate(report.audited_at)}
                </Text>
              </HStack>
            </HStack>
            <AuditReportView report={report} />
          </VStack>
        </m.div>
      )}

      {/* Run another check — keeps both paths reachable after a result. */}
      {!busy && showStageResult && (
        <RunLauncher
          hero={false}
          target={target}
          disabled={launcherDisabled}
          runLoading={isRunning}
          runLabel={runActionLabel}
          onRun={handleRun}
          onCapture={handleCapture}
          captureDuration={captureDuration}
          onDurationChange={setCaptureDuration}
        />
      )}

      {/* Past runs — hidden entirely until at least one run exists (VIS-103). */}
      <Show when={runs.length > 0}>
        <SectionCard icon="folder-file" title={`Past Runs (${runs.length})`}>
          <div className="divide-y divide-border-layout-1">
            {runs.map((summary) => {
              const isCapture = (summary.duration_seconds ?? 0) > 0
              const runLabel = isCapture ? 'Workload capture' : 'Quick audit'
              return (
                <button
                  key={summary.run_id}
                  type="button"
                  onClick={() => handleLoadRun(summary)}
                  className={`group w-full text-left px-5 py-3 hover:bg-surface-layout-2/50 transition-colors cursor-pointer ${
                    loadedRunId === summary.run_id
                      ? 'bg-surface-primary-soft/10'
                      : ''
                  }`}
                >
                  <HStack className="justify-between items-center gap-4">
                    <VStack className="gap-0.5 items-start min-w-0">
                      <HStack className="gap-2 items-baseline min-w-0">
                        <Text
                          level="label-medium"
                          className="text-content-layout-1 shrink-0"
                        >
                          {runLabel}
                        </Text>
                        <Text
                          level="caption"
                          className="text-content-layout-3 truncate"
                        >
                          {formatDate(summary.started_at)}
                        </Text>
                      </HStack>
                      <Text
                        level="mono-small"
                        className="text-content-layout-3 truncate"
                      >
                        {summary.run_id}
                      </Text>
                    </VStack>
                    <HStack className="gap-2 items-center shrink-0">
                      {loadingRunId === summary.run_id && (
                        <Spinner size="base" />
                      )}
                      {isCapture && (
                        <Tag
                          size="small"
                          variant="warning"
                          modifier="ghost"
                          label={formatDuration(summary.duration_seconds)}
                        />
                      )}
                      {summary.has_analysis && (
                        <Tag
                          size="small"
                          variant="positive"
                          modifier="ghost"
                          label="Analyzed"
                        />
                      )}
                      <Tag
                        size="small"
                        variant="informative"
                        modifier="ghost"
                        label={summary.source || 'audit'}
                      />
                      <Icon
                        name="chevron-right"
                        label="Open run"
                        className="w-4 h-4 text-content-layout-3 opacity-0 group-hover:opacity-100 transition-opacity"
                      />
                    </HStack>
                  </HStack>
                </button>
              )
            })}
          </div>
        </SectionCard>
      </Show>
    </div>
  )
}
