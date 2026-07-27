import { InlineNotice } from '@rs/ui-new/error-state'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useState } from 'react'
import {
  formatDate,
  formatMoney,
  formatReportNumber,
  formatSizeMb,
  formatStatisticsWindow,
  formatUptime,
  shortEngineVersion,
  VERDICT_LABELS,
} from '../../../lib/auditReportFormat'
import type {
  InnerReportTab,
  QueryReportTab,
} from '../../../lib/auditReportLocation'
import {
  currentSearchParams,
  INNER_REPORT_TAB_DESCRIPTIONS,
  INNER_REPORT_TABS,
  updateReportLocation,
  useReportLocationVersion,
} from '../../../lib/auditReportLocation'
import {
  linkedIndexRecommendations,
  queryTabForHash,
  reportAnchorPrefix,
  reportRole,
  visibleReportTags,
} from '../../../lib/auditReportModel'
import { formatMs } from '../../../lib/formatters'
import type {
  AuditReport,
  WorkloadIndexRecommendation,
  WorkloadRun,
} from '../../../types/audit'
import { HealthDetailSection, HealthReportSections } from './HealthSections'
import { CaptureSummarySection, UnifiedQueriesSection } from './QueriesSection'
import {
  FindingsList,
  InstanceClassValue,
  ReportEmptyState,
  ReportTabs,
  SectionCard,
  StatCard,
  SupportingCard,
} from './ReportPrimitives'
import { SavingsTabContent, SizingTabContent } from './SizingSavingsTabs'
import { VerdictCard } from './VerdictCard'
import { WorkloadAnalysisView } from './WorkloadAnalysis'

export function AuditReportView({
  report,
  aiCredentialInvalid = false,
  indexRecommendations,
  querySectionId,
  section,
  onFocusQuery,
}: {
  report: AuditReport
  aiCredentialInvalid?: boolean
  indexRecommendations?: WorkloadIndexRecommendation[]
  querySectionId?: string
  section: 'overview' | 'detailed-analysis' | 'next-steps'
  onFocusQuery?: (hash: string) => void
}) {
  const metrics = report.metrics || {}
  const sizing = report.sizing || {}
  const cacheOpp = report.cache_opportunity || {}
  const health = report.health_analysis
  const healthOk =
    !!health && !health.error && health.health_score !== undefined
  const verdict =
    VERDICT_LABELS[sizing.verdict || 'unknown'] || VERDICT_LABELS.unknown
  const topQueries = report.top_queries || []
  const anchorPrefix = reportAnchorPrefix(report)
  const role = reportRole(report.tags)
  const tags = visibleReportTags(report.tags)
  const nextSteps =
    (health?.recommended_actions?.length || 0) > 0
      ? health!.recommended_actions!
      : [
          ...(sizing.suggested_instance_class
            ? [
                {
                  title: `Rightsize to ${sizing.suggested_instance_class}`,
                  body:
                    sizing.explanation ||
                    'Validate the smaller instance class under representative peak load before changing production.',
                },
              ]
            : []),
          ...((cacheOpp.score ?? 0) >= 40
            ? [
                {
                  title: 'Evaluate Readyset caching',
                  body:
                    (cacheOpp.score ?? 0) >= 70
                      ? 'Read-heavy workload with highly repetitive queries makes this target an excellent fit for a caching layer.'
                      : 'Partial read repetition suggests caching would yield measurable benefit.',
                },
              ]
            : []),
        ]

  return (
    <VStack className="gap-8 items-stretch w-full">
      {section === 'overview' && (
        <>
          {/* PRIMARY — the verdict (raised) */}
          <VerdictCard
            report={report}
            aiCredentialInvalid={aiCredentialInvalid}
          />

          {health?.executive_summary && (
            <SectionCard icon="document-validation" title="Executive Summary">
              <div className="p-6 max-w-4xl">
                <Text
                  level="body-small"
                  className="text-content-layout-2 leading-relaxed"
                >
                  {health.executive_summary}
                </Text>
              </div>
            </SectionCard>
          )}

          <SectionCard icon="document-validation" title="Top Findings">
            <div className="p-6">
              {(health?.top_findings?.length || 0) > 0 ? (
                <FindingsList findings={health!.top_findings!} />
              ) : (
                <Text level="body-small" className="text-content-layout-3">
                  No critical findings were identified.
                </Text>
              )}
            </div>
          </SectionCard>

          {/* SECONDARY — three supporting scores */}
          <div className="grid grid-cols-1 tablet:grid-cols-3 gap-5">
            <SupportingCard icon="sparkles" title="Cache Opportunity">
              <HStack className="gap-2 items-baseline">
                <Text
                  level="headline-4"
                  className="text-content-layout-1 tabular-nums"
                >
                  {formatReportNumber(cacheOpp.score, 0)}
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
              {(cacheOpp.score ?? 0) >= 70 && (
                <Text level="caption" className="text-content-positive-soft">
                  Strong candidate for Readyset caching.
                </Text>
              )}
              {(cacheOpp.score ?? 0) >= 40 && (cacheOpp.score ?? 0) < 70 && (
                <Text level="caption" className="text-content-warning-soft">
                  Good candidate for Readyset caching.
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
                <InstanceClassValue report={report} />
              </HStack>
              {sizing.potential_savings_usd != null &&
              sizing.potential_savings_usd > 0 ? (
                <Text level="caption" className="text-content-positive-soft">
                  Save ~{formatMoney(sizing.potential_savings_usd)}/mo
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
              {sizing.current_monthly_cost_usd != null && (
                <Text level="caption" className="text-content-layout-2">
                  Current {formatMoney(sizing.current_monthly_cost_usd)}/mo
                  {sizing.suggested_monthly_cost_usd != null
                    ? ` · Suggested ${formatMoney(sizing.suggested_monthly_cost_usd)}/mo`
                    : ''}
                </Text>
              )}
              {sizing.readyset_projected_savings_usd != null && (
                <Text level="caption" className="text-content-positive-soft">
                  Readyset projected savings{' '}
                  {formatMoney(sizing.readyset_projected_savings_usd)}/mo
                  {sizing.readyset_projected_class
                    ? ` · ${sizing.readyset_projected_class}`
                    : ''}
                  {sizing.readyset_projected_cost_usd != null
                    ? ` at ${formatMoney(sizing.readyset_projected_cost_usd)}/mo`
                    : ''}
                </Text>
              )}
              {sizing.explanation && (
                <Text level="caption" className="text-content-layout-3">
                  {sizing.explanation}
                </Text>
              )}
            </SupportingCard>

            <SupportingCard icon="observe" title="Query Activity">
              <HStack className="gap-2 items-baseline">
                <Text
                  level="headline-4"
                  className="text-content-layout-1 tabular-nums"
                >
                  {report.workload?.unique_queries ??
                    report.workload?.queries?.length ??
                    topQueries.length}
                </Text>
                <Text level="caption" className="text-content-layout-3">
                  Queries observed
                </Text>
              </HStack>
            </SupportingCard>
          </div>
        </>
      )}

      {section === 'detailed-analysis' && (
        <>
          {/* SECONDARY — AI findings + actions (only with a credential) */}
          {healthOk && <HealthDetailSection health={health!} />}

          {/* Health analysis degradation (no-key path) */}
          {health?.error && (
            <InlineNotice
              errorClass="provider"
              title="AI analysis unavailable"
              message={health.error}
              trustworthy="The metrics, sizing verdict, and cache opportunity above come straight from the database and are unaffected."
            />
          )}

          {/* Full details are intentionally expanded for CLI parity. */}
          <div>
            <VStack className="gap-8 items-stretch">
              {/* Overview */}
              <SectionCard icon="database" title="Database Overview">
                {(report.host ||
                  report.region ||
                  report.database ||
                  role ||
                  report.group ||
                  tags.length > 0) && (
                  <div className="px-6 pt-5">
                    <HStack className="gap-2 items-center flex-wrap">
                      {report.host && (
                        <Tag
                          size="small"
                          variant="muted"
                          modifier="ghost"
                          label={`Host: ${report.host}`}
                        />
                      )}
                      {report.region && (
                        <Tag
                          size="small"
                          variant="muted"
                          modifier="ghost"
                          label={`Region: ${report.region}`}
                        />
                      )}
                      {report.database && (
                        <Tag
                          size="small"
                          variant="muted"
                          modifier="ghost"
                          label={`Database: ${report.database}`}
                        />
                      )}
                      {role && (
                        <Tag
                          size="small"
                          variant={
                            role === 'writer' ? 'primary' : 'informative'
                          }
                          modifier="ghost"
                          label={`Role: ${role}`}
                        />
                      )}
                      {report.group && (
                        <Tag
                          size="small"
                          variant="muted"
                          modifier="ghost"
                          label={`Group: ${report.group}`}
                        />
                      )}
                      {tags.map((tag) => (
                        <Tag
                          key={tag}
                          size="small"
                          variant="muted"
                          modifier="ghost"
                          label={tag}
                        />
                      ))}
                    </HStack>
                  </div>
                )}
                <div className="p-4 grid grid-cols-1 desktop:grid-cols-3 gap-4">
                  <VStack className="gap-2 items-stretch">
                    <Text level="caption" className="text-content-layout-3">
                      Database
                    </Text>
                    <div className="grid grid-cols-2 gap-2">
                      <StatCard
                        compact
                        label="Engine"
                        value={shortEngineVersion(
                          report.engine,
                          metrics.server_version
                        )}
                      />
                      <StatCard
                        compact
                        label="Database size"
                        value={formatSizeMb(metrics.database_size_mb)}
                        hint={metrics.storage_type || undefined}
                      />
                      <StatCard
                        compact
                        label="Uptime"
                        value={formatUptime(metrics.uptime_seconds)}
                      />
                      <StatCard
                        compact
                        label="Storage"
                        value={
                          metrics.storage_allocated_gb
                            ? `${metrics.storage_allocated_gb.toFixed(0)} GB`
                            : '-'
                        }
                        hint={
                          metrics.storage_used_pct != null
                            ? `${formatReportNumber(metrics.storage_used_pct)}% used`
                            : undefined
                        }
                      />
                    </div>
                  </VStack>
                  <VStack className="gap-2 items-stretch">
                    <Text level="caption" className="text-content-layout-3">
                      Workload
                    </Text>
                    <div className="grid grid-cols-2 gap-2">
                      <StatCard
                        compact
                        label="Connections"
                        value={`${metrics.active_connections ?? '-'} / ${metrics.max_connections ?? '-'}`}
                        hint={
                          metrics.connection_utilization_pct !== undefined
                            ? `${metrics.connection_utilization_pct.toFixed(0)}% utilized`
                            : undefined
                        }
                      />
                      <StatCard
                        compact
                        label="Idle"
                        value={`${metrics.idle_connections ?? '-'}`}
                      />
                      <StatCard
                        compact
                        label="Cache hit"
                        value={
                          metrics.cache_hit_rate !== undefined
                            ? `${metrics.cache_hit_rate.toFixed(1)}%`
                            : '-'
                        }
                      />
                      <StatCard
                        compact
                        label="Read / write"
                        value={
                          metrics.read_pct !== undefined
                            ? `${metrics.read_pct.toFixed(0)}% / ${(metrics.write_pct ?? 0).toFixed(0)}%`
                            : '-'
                        }
                      />
                      <StatCard
                        compact
                        label="Tracked queries"
                        value={`${metrics.tracked_query_count ?? '-'}`}
                        hint={
                          metrics.tracked_query_count == null ||
                          metrics.tracked_query_count === 0
                            ? 'No tracked queries because query statistics were unavailable during this check.'
                            : undefined
                        }
                      />
                      <StatCard
                        compact
                        label="Total query time"
                        value={formatMs(metrics.total_query_time_ms)}
                      />
                    </div>
                  </VStack>
                  <VStack className="gap-2 items-stretch">
                    <Text level="caption" className="text-content-layout-3">
                      Memory & topology
                    </Text>
                    <div className="grid grid-cols-2 gap-2">
                      <StatCard
                        compact
                        label="Shared buffers"
                        value={formatSizeMb(metrics.shared_buffers_mb)}
                      />
                      <StatCard
                        compact
                        label="Working set"
                        value={formatSizeMb(metrics.working_set_mb)}
                      />
                      <StatCard
                        compact
                        label="Replication"
                        value={metrics.is_replica ? 'Replica' : 'Primary'}
                        hint={
                          metrics.replication_lag_seconds != null
                            ? `${formatReportNumber(metrics.replication_lag_seconds)}s lag`
                            : undefined
                        }
                      />
                      <StatCard
                        compact
                        label="Statistics window"
                        value={
                          metrics.stats_reset_at
                            ? `Last reset ${formatDate(metrics.stats_reset_at)}`
                            : formatStatisticsWindow(
                                metrics.stats_window_seconds
                              )
                        }
                      />
                    </div>
                  </VStack>
                </div>
              </SectionCard>

              <HealthReportSections
                report={report}
                indexRecommendations={indexRecommendations}
                querySectionId={querySectionId}
                onFocusQuery={onFocusQuery}
                content="details"
              />
            </VStack>
          </div>
        </>
      )}

      {section === 'next-steps' && nextSteps.length > 0 && (
        <div id={`${anchorPrefix}-next-steps`} className="scroll-mt-6">
          <SectionCard
            icon="document-validation"
            title={`${sizing.current_monthly_cost_usd != null ? 'Section 4' : 'Section 3'} · Next Steps`}
          >
            <div className="p-6">
              <VStack className="gap-5 items-stretch">
                {nextSteps.map((action, index) => (
                  <HStack key={index} className="gap-3 items-start">
                    <div className="w-6 h-6 rounded-md bg-surface-primary-soft flex items-center justify-center shrink-0">
                      <Text
                        level="caption"
                        className="text-content-primary-soft font-semibold"
                      >
                        {'rank' in action && action.rank
                          ? action.rank
                          : index + 1}
                      </Text>
                    </div>
                    <VStack className="gap-1.5 items-start">
                      <Text
                        level="label-small"
                        className="text-content-layout-1"
                      >
                        {action.title}
                      </Text>
                      {action.body && (
                        <Text
                          level="body-small"
                          className="text-content-layout-2"
                        >
                          {action.body}
                        </Text>
                      )}
                    </VStack>
                  </HStack>
                ))}
              </VStack>
            </div>
          </SectionCard>
        </div>
      )}
      {section === 'next-steps' && nextSteps.length === 0 && (
        <ReportEmptyState>
          No recommended next steps were generated for this run.
        </ReportEmptyState>
      )}
    </VStack>
  )
}

function InnerAuditReportTabs({
  report,
  aiCredentialInvalid,
  nested = false,
}: {
  report: AuditReport
  aiCredentialInvalid: boolean
  nested?: boolean
}) {
  const locationVersion = useReportLocationVersion()
  const params = currentSearchParams(locationVersion)
  const requestedTab = params.get('tab')
  const selectedTab = INNER_REPORT_TABS.some((tab) => tab.id === requestedTab)
    ? (requestedTab as InnerReportTab)
    : 'overview'
  const requestedQueryTab = params.get('queryTab')
  const hasCapturedQueries =
    (report.workload?.queries?.length || 0) > 0 ||
    (report.readyset_comparison?.queries?.length || 0) > 0 ||
    (report.workload?.readyset_comparison?.queries?.length || 0) > 0
  const queryTab: QueryReportTab =
    requestedQueryTab === 'captured' || requestedQueryTab === 'historical'
      ? requestedQueryTab
      : hasCapturedQueries
        ? 'captured'
        : 'historical'
  const [focusHash, setFocusHash] = useState<string>()
  const workload = report.workload
  const workloadQueries = workload?.queries || []
  const comparison = report.readyset_comparison ?? workload?.readyset_comparison
  const querySectionId = `${reportAnchorPrefix(report)}-queries`
  const indexRecommendations = [
    ...linkedIndexRecommendations(workload?.analysis),
    ...(report.health_analysis?.index_suggestions ?? []).map((suggestion) => ({
      sql: suggestion.sql,
      reason: suggestion.reason,
    })),
  ]
  const hasDetailedData = !!(
    report.metrics ||
    report.health_report ||
    report.health_analysis ||
    workload?.analysis
  )
  const hasNextStepData = !!(
    report.health_analysis?.recommended_actions?.length ||
    workload?.analysis?.capacity_insights?.length ||
    workload?.analysis?.optimization_priorities?.length ||
    report.sizing?.suggested_instance_class ||
    (report.cache_opportunity?.score ?? 0) >= 40
  )

  const selectTab = (tab: InnerReportTab) => {
    setFocusHash(undefined)
    updateReportLocation({
      tab,
      queryTab: tab === 'queries' ? queryTab : undefined,
    })
  }
  const focusQuery = (hash: string) => {
    const nextQueryTab = queryTabForHash(report, hash)
    setFocusHash(hash)
    updateReportLocation({
      tab: 'queries',
      queryTab: nextQueryTab,
    })
  }

  return (
    <VStack className="gap-6 items-stretch w-full">
      <ReportTabs
        label="Database report sections"
        tabs={INNER_REPORT_TABS}
        selected={selectedTab}
        onSelect={selectTab}
        nested={nested}
        description={INNER_REPORT_TAB_DESCRIPTIONS[selectedTab]}
      />
      <div role="tabpanel" data-report-tab={selectedTab}>
        {selectedTab === 'overview' && (
          <AuditReportView
            report={report}
            aiCredentialInvalid={aiCredentialInvalid}
            section="overview"
          />
        )}
        {selectedTab === 'queries' && (
          <VStack className="gap-6 items-stretch">
            {workload && (
              <CaptureSummarySection
                summary={workload}
                queries={workloadQueries}
                durationSeconds={workload.duration_seconds}
              />
            )}
            <UnifiedQueriesSection
              id={querySectionId}
              capturedQueries={workloadQueries}
              historicalQueries={report.top_queries}
              comparison={comparison}
              liveCapture={(workload?.duration_seconds ?? 0) > 0}
              totalQueries={
                workload?.total_executions ?? workload?.total_queries
              }
              queryTab={queryTab}
              onQueryTabChange={(tab) =>
                updateReportLocation(
                  { tab: 'queries', queryTab: tab },
                  { replace: true }
                )
              }
              focusHash={focusHash}
            />
          </VStack>
        )}
        {selectedTab === 'sizing' && <SizingTabContent report={report} />}
        {selectedTab === 'savings' && <SavingsTabContent report={report} />}
        {selectedTab === 'detailed-analysis' &&
          (hasDetailedData ? (
            <VStack className="gap-6 items-stretch">
              <AuditReportView
                report={report}
                aiCredentialInvalid={aiCredentialInvalid}
                section="detailed-analysis"
                indexRecommendations={indexRecommendations}
                querySectionId={querySectionId}
                onFocusQuery={focusQuery}
              />
              {workload?.analysis && (
                <WorkloadAnalysisView
                  analysis={workload.analysis}
                  content="details"
                />
              )}
            </VStack>
          ) : (
            <ReportEmptyState>
              No detailed analysis data was collected for this run.
            </ReportEmptyState>
          ))}
        {selectedTab === 'next-steps' &&
          (hasNextStepData ? (
            <VStack className="gap-6 items-stretch">
              <AuditReportView
                report={report}
                aiCredentialInvalid={aiCredentialInvalid}
                section="next-steps"
              />
              {workload?.analysis && (
                <WorkloadAnalysisView
                  analysis={workload.analysis}
                  content="next-steps"
                />
              )}
            </VStack>
          ) : (
            <ReportEmptyState>
              No recommended next steps were generated for this run.
            </ReportEmptyState>
          ))}
      </div>
    </VStack>
  )
}

export function WorkloadRunView({
  run,
  aiCredentialInvalid = false,
}: {
  run: WorkloadRun
  aiCredentialInvalid?: boolean
}) {
  const queries = run.queries || []
  const report: AuditReport = {
    ...run,
    engine: run.engine || run.db_engine,
    workload: {
      unique_queries: queries.length,
      total_executions:
        run.total_queries ??
        queries.reduce((acc, query) => acc + (query.calls ?? 0), 0),
      total_query_time_ms: run.total_query_time_ms,
      duration_seconds: run.duration_seconds,
      queries,
      analysis: run.analysis,
      readyset_comparison: run.readyset_comparison,
    },
  }
  return (
    <FullAuditReportView
      report={report}
      aiCredentialInvalid={aiCredentialInvalid}
    />
  )
}

export function FleetTargetDetail({
  result,
  aiCredentialInvalid,
}: {
  result: AuditReport
  aiCredentialInvalid: boolean
}) {
  return (
    <VStack className="gap-8 items-stretch p-6">
      <Text
        as="h2"
        level="headline-2"
        className="text-content-layout-1 break-words"
      >
        {result.target_name || result.host || 'Database target'}
      </Text>
      {result.error && (
        <InlineNotice
          errorClass="database"
          title={`${result.target_name || 'Target'} failed`}
          message={result.error}
        />
      )}
      <FullAuditReportView
        report={result}
        aiCredentialInvalid={aiCredentialInvalid}
        nested
      />
    </VStack>
  )
}

export function FullAuditReportView({
  report,
  aiCredentialInvalid,
  nested = false,
}: {
  report: AuditReport
  aiCredentialInvalid: boolean
  nested?: boolean
}) {
  return (
    <InnerAuditReportTabs
      report={report}
      aiCredentialInvalid={aiCredentialInvalid}
      nested={nested}
    />
  )
}
