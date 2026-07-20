// Audit report / workload rendering views — moved out of the `/audit` route
// module into this route-ignored sibling (TanStack skips `-`-prefixed files) so
// the code-splitter can relocate the `SQLDisplay` → CodeMirror import out of the
// eager entry chunk. These views are shared by two split components: `AuditPage`
// in `audit.tsx` and `AuditRunDetailPage` in `audit_.runs.$runId.tsx`. When they
// lived in `audit.tsx` and were `export`ed for the detail route to reuse, they
// (and their `SQLDisplay` import) were pinned into the eager route reference
// module and CodeMirror got modulepreloaded on every route. [FIX-1 / Defect D-1]
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { Card } from '@rs/ui-new/card'
import { CopyButton } from '@rs/ui-new/copy-button'
import { Icon } from '@rs/ui-new/icon'
import { Scrollable } from '@rs/ui-new/scrollable'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useDisclosure } from '@rs/ui-new/use-disclosure'
import { Link } from '@tanstack/react-router'
import { SQLDisplay } from '../components/SQLDisplay'
import { TableHeaderCell } from '../components/TableHeaderCell'
import { formatMs } from '../lib/formatters'
import type {
  AuditReport,
  HealthAnalysis,
  HealthFinding,
  WorkloadAnalysis,
  WorkloadQuery,
  WorkloadRun,
  WorkloadSummary,
} from '../types/audit'

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

export function formatDate(iso: string | null | undefined): string {
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

export function StatCard({
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

export function SectionCard({
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
          <Tag
            variant={verdict.variant}
            modifier="ghost"
            label={verdict.label}
          />
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

export function AuditReportView({ report }: { report: AuditReport }) {
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
                        <TableHeaderCell>Query</TableHeaderCell>
                        <TableHeaderCell align="right" className="w-24">
                          Calls
                        </TableHeaderCell>
                        <TableHeaderCell align="right" className="w-24">
                          Avg
                        </TableHeaderCell>
                        <TableHeaderCell align="right" className="w-24">
                          % Time
                        </TableHeaderCell>
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

export function formatDuration(seconds: number | undefined): string {
  if (!seconds) return '-'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const mins = Math.floor(seconds / 60)
  const rem = Math.round(seconds % 60)
  return rem ? `${mins}m ${rem}s` : `${mins}m`
}

// Bottleneck / caching-candidate / capacity lists come back from the LLM as
// either plain strings or objects with varying keys. Pull a readable heading
// (and optional detail) so a bullet never renders a raw object as a React
// child (which throws and blanks the whole run view).
function bulletContent(item: string | Record<string, unknown>): {
  heading: string
  detail: string | null
} {
  if (typeof item === 'string') return { heading: item, detail: null }
  const pick = (...keys: string[]): string | null => {
    for (const k of keys) {
      const v = item[k]
      if (typeof v === 'string' && v.trim()) return v
    }
    return null
  }
  const heading = pick(
    'description',
    'reason',
    'recommendation',
    'action',
    'summary',
    'title',
    'category',
  )
  const detail = pick('recommendation', 'estimated_benefit', 'details', 'impact')
  return {
    heading: heading ?? JSON.stringify(item),
    detail: detail && detail !== heading ? detail : null,
  }
}

function BulletList({
  items,
}: {
  items: (string | Record<string, unknown>)[]
}) {
  return (
    <VStack className="gap-2 items-stretch">
      {items.map((item, index) => {
        const { heading, detail } = bulletContent(item)
        return (
          <HStack key={index} className="gap-2 items-start">
            <div className="w-1.5 h-1.5 rounded-full bg-content-layout-3 mt-2 shrink-0" />
            <VStack className="gap-0.5 items-start min-w-0">
              <Text level="body-small" className="text-content-layout-2 min-w-0">
                {heading}
              </Text>
              <Show when={!!detail}>
                <Text level="caption" className="text-content-layout-3 min-w-0">
                  {detail}
                </Text>
              </Show>
            </VStack>
          </HStack>
        )
      })}
    </VStack>
  )
}

function WorkloadQueriesTable({ queries }: { queries: WorkloadQuery[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr className="bg-surface-layout-2/30">
            <TableHeaderCell>Query</TableHeaderCell>
            <TableHeaderCell align="right" className="w-24">
              Calls
            </TableHeaderCell>
            <TableHeaderCell align="right" className="w-24">
              Avg
            </TableHeaderCell>
            <TableHeaderCell align="right" className="w-24">
              % Time
            </TableHeaderCell>
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
              {priorities.map((item, index) => {
                // optimization_priorities is an LLM-generated list; entries are
                // a plain string or an object whose keys vary across audit
                // versions. Read the closest field for each slot so any shape
                // renders as text instead of crashing the run view.
                const rank =
                  typeof item === 'string'
                    ? index + 1
                    : (item.rank ?? item.priority ?? index + 1)
                const heading =
                  typeof item === 'string'
                    ? item
                    : item.action ||
                      item.description ||
                      item.recommendation ||
                      item.category ||
                      `Priority ${rank}`
                const body =
                  typeof item === 'string'
                    ? null
                    : ([item.details, item.recommendation, item.description].find(
                        (d) => d && d !== heading,
                      ) ?? null)
                const caption =
                  typeof item === 'string'
                    ? ''
                    : [
                        item.impact ? `${item.impact} impact` : null,
                        item.effort ? `${item.effort} effort` : null,
                      ]
                        .filter(Boolean)
                        .join(', ')
                return (
                  <HStack key={index} className="gap-3 items-start">
                    <div className="w-6 h-6 rounded-md bg-surface-primary-soft flex items-center justify-center shrink-0">
                      <Text
                        level="caption"
                        className="text-content-primary-soft font-semibold"
                      >
                        {rank}
                      </Text>
                    </div>
                    <VStack className="gap-0.5 items-start min-w-0">
                      <Text
                        level="body-small"
                        className="text-content-layout-1 min-w-0"
                      >
                        {heading}
                      </Text>
                      <Show when={!!body}>
                        <Text
                          level="body-small"
                          className="text-content-layout-2 min-w-0"
                        >
                          {body}
                        </Text>
                      </Show>
                      <Show when={!!caption}>
                        <Text level="caption" className="text-content-layout-3">
                          {caption}
                        </Text>
                      </Show>
                    </VStack>
                  </HStack>
                )
              })}
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
export function WorkloadReportView({
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

export function WorkloadRunView({ run }: { run: WorkloadRun }) {
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
