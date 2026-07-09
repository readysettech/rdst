import { useState } from 'react';
import { createFileRoute } from '@tanstack/react-router';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@rs/ui-new/button';
import { Card } from '@rs/ui-new/card';
import { Icon } from '@rs/ui-new/icon';
import type { IconStrokeName } from '@rs/ui-icons/icon-name';
import { Show } from '@rs/ui-new/show';
import { Spinner } from '@rs/ui-new/spinner';
import { Tag } from '@rs/ui-new/tag';
import { Scrollable } from '@rs/ui-new/scrollable';
import { Text } from '@rs/ui-new/text';
import { HStack, VStack } from '@rs/ui-new/stack';
import { m, AnimatePresence } from '@rs/ui-new/motion';
import { toast } from '@rs/ui-new/use-toast';
import { CopyButton } from '@rs/ui-new/copy-button';
import { SQLDisplay } from '../components/SQLDisplay';
import { TargetLockNotice } from '../components';
import { useTarget } from '../hooks/useTarget';
import { useTargetPasswordLock } from '../lib/useTargetPasswordLock';
import {
  fetchAuditRuns,
  fetchRunDetail,
  isWorkloadRun,
  useAuditCapture,
  useAuditRun,
} from '../lib/useAudit';
import type {
  AuditReport,
  AuditRunSummary,
  HealthFinding,
  WorkloadAnalysis,
  WorkloadQuery,
  WorkloadRun,
  WorkloadSummary,
} from '../types/audit';

const CAPTURE_DURATIONS: Array<{ label: string; seconds: number }> = [
  { label: '30s', seconds: 30 },
  { label: '1m', seconds: 60 },
  { label: '5m', seconds: 300 },
  { label: '15m', seconds: 900 },
];

export const Route = createFileRoute('/audit')({
  component: AuditPage,
});

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function formatSizeMb(mb: number | undefined): string {
  if (mb === undefined || mb === null) return '-';
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${Math.round(mb)} MB`;
}

function formatUptime(seconds: number | undefined): string {
  if (!seconds) return '-';
  const days = Math.floor(seconds / 86400);
  if (days >= 1) return `${days}d ${Math.floor((seconds % 86400) / 3600)}h`;
  const hours = Math.floor(seconds / 3600);
  if (hours >= 1) return `${hours}h ${Math.floor((seconds % 3600) / 60)}m`;
  return `${Math.floor(seconds / 60)}m`;
}

function formatMs(ms: number | undefined): string {
  if (ms === undefined || ms === null) return '-';
  if (ms < 1) return '<1ms';
  if (ms < 1000) return `${ms.toFixed(1)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '-';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function severityVariant(severity: string | undefined): 'negative' | 'warning' | 'positive' | 'informative' {
  switch (severity) {
    case 'crit': return 'negative';
    case 'warn': return 'warning';
    case 'ok': return 'positive';
    default: return 'informative';
  }
}

function healthScoreColor(score: number): string {
  if (score >= 75) return 'text-content-positive-soft';
  if (score >= 60) return 'text-content-warning-soft';
  return 'text-content-negative-soft';
}

const VERDICT_LABELS: Record<string, { label: string; variant: 'positive' | 'warning' | 'negative' | 'informative' }> = {
  right_sized: { label: 'Right-sized', variant: 'positive' },
  oversized: { label: 'Oversized', variant: 'warning' },
  under_provisioned: { label: 'Under-provisioned', variant: 'negative' },
  unknown: { label: 'Unknown', variant: 'informative' },
};

// ---------------------------------------------------------------------------
// Report sub-components
// ---------------------------------------------------------------------------

function StatCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="bg-surface-layout-2/50 rounded-xl p-4 border border-border-layout-1">
      <VStack className="gap-1 items-start">
        <Text level="caption" className="text-content-layout-3 uppercase tracking-wider">
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
  );
}

function SectionCard({
  icon,
  title,
  children,
}: {
  icon: IconStrokeName;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="w-full overflow-hidden">
      <Card.Content className="p-0">
        <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
          <HStack className="gap-2 items-center">
            <Icon name={icon} label={title} className="w-4 h-4 text-content-layout-3" />
            <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
              {title}
            </Text>
          </HStack>
        </div>
        {children}
      </Card.Content>
    </Card>
  );
}

function FindingsList({ findings }: { findings: HealthFinding[] }) {
  return (
    <VStack className="gap-2 items-stretch">
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
  );
}

function AuditReportView({ report }: { report: AuditReport }) {
  const metrics = report.metrics || {};
  const sizing = report.sizing || {};
  const cacheOpp = report.cache_opportunity || {};
  const health = report.health_analysis;
  const healthOk = health && !health.error && health.health_score !== undefined;
  const verdict = VERDICT_LABELS[sizing.verdict || 'unknown'] || VERDICT_LABELS.unknown;
  const topQueries = report.top_queries || [];

  return (
    <VStack className="gap-6 items-stretch w-full">
      {/* Overview */}
      <SectionCard icon="database" title="Overview">
        <div className="p-5 grid grid-cols-2 tablet:grid-cols-4 gap-4">
          <StatCard label="Engine" value={report.engine || '-'} hint={metrics.server_version} />
          <StatCard label="Database Size" value={formatSizeMb(metrics.database_size_mb)} hint={metrics.storage_type || undefined} />
          <StatCard label="Uptime" value={formatUptime(metrics.uptime_seconds)} />
          <StatCard
            label="Connections"
            value={`${metrics.active_connections ?? '-'} / ${metrics.max_connections ?? '-'}`}
            hint={metrics.connection_utilization_pct !== undefined ? `${metrics.connection_utilization_pct.toFixed(0)}% utilized` : undefined}
          />
          <StatCard
            label="Cache Hit Rate"
            value={metrics.cache_hit_rate !== undefined ? `${metrics.cache_hit_rate.toFixed(1)}%` : '-'}
          />
          <StatCard
            label="Read / Write"
            value={
              metrics.read_pct !== undefined
                ? `${metrics.read_pct.toFixed(0)}% / ${(metrics.write_pct ?? 0).toFixed(0)}%`
                : '-'
            }
          />
          <StatCard label="Tracked Queries" value={`${metrics.tracked_query_count ?? '-'}`} />
          <StatCard
            label="Storage"
            value={metrics.storage_allocated_gb ? `${metrics.storage_allocated_gb.toFixed(0)} GB` : '-'}
            hint={metrics.storage_used_pct != null ? `${metrics.storage_used_pct}% used` : undefined}
          />
        </div>
      </SectionCard>

      {/* Health analysis */}
      {healthOk && (
        <SectionCard icon="document-validation" title="Health Analysis">
          <div className="p-5">
            <div className="grid grid-cols-[auto_1fr] gap-6 items-start">
              <VStack className="gap-1 items-center px-4">
                <Text level="headline-1" className={`tabular-nums ${healthScoreColor(health.health_score!)}`}>
                  {health.health_score}
                </Text>
                <Tag
                  variant={health.health_score! >= 75 ? 'positive' : health.health_score! >= 60 ? 'warning' : 'negative'}
                  modifier="ghost"
                  label={health.health_label || 'SCORE'}
                />
              </VStack>
              <VStack className="gap-4 items-start min-w-0">
                {health.health_score_rationale && (
                  <Text level="body-small" className="text-content-layout-2">
                    {health.health_score_rationale}
                  </Text>
                )}
                {health.executive_summary && (
                  <Text level="body-small" className="text-content-layout-3">
                    {health.executive_summary}
                  </Text>
                )}
                {(health.findings?.length || 0) > 0 && <FindingsList findings={health.findings!} />}
              </VStack>
            </div>
            {(health.recommended_actions?.length || 0) > 0 && (
              <div className="mt-5 pt-5 border-t border-border-layout-1">
                <Text level="overline" className="text-content-layout-3 uppercase tracking-wider block mb-3">
                  Recommended Actions
                </Text>
                <VStack className="gap-3 items-stretch">
                  {health.recommended_actions!.map((action, index) => (
                    <HStack key={index} className="gap-3 items-start">
                      <div className="w-6 h-6 rounded-md bg-surface-primary-soft flex items-center justify-center shrink-0">
                        <Text level="caption" className="text-content-primary-soft font-semibold">
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
      )}
      {health?.error && (
        <div className="px-5 py-3 bg-surface-warning-soft/20 border border-border-warning-soft rounded-xl">
          <HStack className="gap-2 items-center">
            <Icon name="alert" label="Warning" className="w-4 h-4 text-content-warning-soft" />
            <Text level="body-small" className="text-content-warning-soft">
              Health analysis unavailable: {health.error}
            </Text>
          </HStack>
        </div>
      )}

      {/* Sizing + Cache opportunity */}
      <div className="grid grid-cols-1 tablet:grid-cols-2 gap-6">
        <SectionCard icon="adjustment-horizontal" title="Sizing">
          <div className="p-5">
            <VStack className="gap-3 items-start">
              <HStack className="gap-2 items-center">
                <Tag variant={verdict.variant} modifier="ghost" label={verdict.label} />
                {report.instance_class && (
                  <Text level="mono-small" className="text-content-layout-3">
                    {report.instance_class}
                  </Text>
                )}
              </HStack>
              {sizing.explanation && (
                <Text level="body-small" className="text-content-layout-2">
                  {sizing.explanation}
                </Text>
              )}
              {sizing.potential_savings_usd != null && sizing.potential_savings_usd > 0 && (
                <Text level="body-small" className="text-content-positive-soft">
                  Potential savings: ${sizing.potential_savings_usd.toFixed(0)}/mo
                  {sizing.suggested_instance_class ? ` on ${sizing.suggested_instance_class}` : ''}
                </Text>
              )}
            </VStack>
          </div>
        </SectionCard>

        <SectionCard icon="sparkles" title="Cache Opportunity">
          <div className="p-5">
            <VStack className="gap-3 items-start">
              <HStack className="gap-3 items-center">
                <Text level="headline-4" className="text-content-layout-1 tabular-nums">
                  {cacheOpp.score ?? '-'}
                </Text>
                <Tag
                  variant={cacheOpp.level === 'high' ? 'positive' : cacheOpp.level === 'medium' ? 'warning' : 'informative'}
                  modifier="ghost"
                  label={(cacheOpp.level || 'unknown').toUpperCase()}
                />
              </HStack>
              {cacheOpp.explanation && (
                <Text level="body-small" className="text-content-layout-2">
                  {cacheOpp.explanation}
                </Text>
              )}
            </VStack>
          </div>
        </SectionCard>
      </div>

      {/* Top queries */}
      <Show when={topQueries.length > 0}>
        <SectionCard icon="observe" title={`Top Queries (${topQueries.length})`}>
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
                  <tr key={query.query_hash || index} className="hover:bg-surface-layout-2/50 transition-colors">
                    <td className="px-4 py-3">
                      <div className="bg-surface-layout-2 rounded-lg max-w-2xl">
                        <Scrollable className="max-h-24">
                          <div className="px-3 py-2">
                            <SQLDisplay sql={query.query_text || ''} wrap />
                          </div>
                        </Scrollable>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Text level="mono-small" className="text-content-layout-2 tabular-nums">
                        {query.calls?.toLocaleString() ?? '-'}
                      </Text>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Text level="mono-small" className="text-content-layout-2 tabular-nums">
                        {formatMs(query.avg_time_ms)}
                      </Text>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Text level="mono-small" className="text-content-layout-2 tabular-nums">
                        {query.pct_total_time != null ? `${query.pct_total_time}%` : '-'}
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
  );
}

// ---------------------------------------------------------------------------
// Workload capture report
// ---------------------------------------------------------------------------

function formatDuration(seconds: number | undefined): string {
  if (!seconds) return '-';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const rem = Math.round(seconds % 60);
  return rem ? `${mins}m ${rem}s` : `${mins}m`;
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
  );
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
            <tr key={query.query_hash || index} className="hover:bg-surface-layout-2/50 transition-colors">
              <td className="px-4 py-3">
                <div className="bg-surface-layout-2 rounded-lg max-w-2xl">
                  <Scrollable className="max-h-24">
                    <div className="px-3 py-2">
                      <SQLDisplay sql={query.query_text || query.normalized_query || ''} wrap />
                    </div>
                  </Scrollable>
                </div>
              </td>
              <td className="px-4 py-3 text-right">
                <Text level="mono-small" className="text-content-layout-2 tabular-nums">
                  {query.calls?.toLocaleString() ?? '-'}
                </Text>
              </td>
              <td className="px-4 py-3 text-right">
                <Text level="mono-small" className="text-content-layout-2 tabular-nums">
                  {formatMs(query.avg_time_ms)}
                </Text>
              </td>
              <td className="px-4 py-3 text-right">
                <Text level="mono-small" className="text-content-layout-2 tabular-nums">
                  {query.pct_total_time != null ? `${query.pct_total_time.toFixed(1)}%` : '-'}
                </Text>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function WorkloadAnalysisView({ analysis }: { analysis: WorkloadAnalysis }) {
  const bottlenecks = analysis.top_bottlenecks || [];
  const indexRecs = analysis.index_recommendations || [];
  const cachingCandidates = analysis.caching_candidates || [];
  const capacityInsights = analysis.capacity_insights || [];
  const priorities = analysis.optimization_priorities || [];
  const hasScore = analysis.health_score !== undefined && analysis.health_score !== null;

  return (
    <VStack className="gap-6 items-stretch w-full">
      <SectionCard icon="document-validation" title="Workload Analysis">
        <div className="p-5">
          <div className="grid grid-cols-[auto_1fr] gap-6 items-start">
            {hasScore && (
              <VStack className="gap-1 items-center px-4">
                <Text level="headline-1" className={`tabular-nums ${healthScoreColor(analysis.health_score!)}`}>
                  {analysis.health_score}
                </Text>
                <Tag
                  variant={analysis.health_score! >= 75 ? 'positive' : analysis.health_score! >= 60 ? 'warning' : 'negative'}
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
                  <Text level="caption" className="text-content-layout-3 uppercase tracking-wider">
                    Read / Write
                  </Text>
                  <Tag size="small" variant="informative" modifier="ghost" label={analysis.read_write_ratio} />
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
        <SectionCard icon="adjustment-horizontal" title={`Index Recommendations (${indexRecs.length})`}>
          <div className="p-5">
            <VStack className="gap-4 items-stretch">
              {indexRecs.map((rec, index) => (
                <VStack key={index} className="gap-2 items-stretch">
                  {rec.sql && (
                    <div className="bg-surface-layout-2 rounded-lg overflow-hidden">
                      <HStack className="justify-between items-center px-3 py-2 border-b border-border-layout-1">
                        <Text level="caption" className="text-content-layout-3 uppercase tracking-wider">
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
                    <Text level="caption" className="text-content-positive-soft">
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
                    <Text level="caption" className="text-content-primary-soft font-semibold">
                      {index + 1}
                    </Text>
                  </div>
                  <Text level="body-small" className="text-content-layout-2 min-w-0">
                    {item}
                  </Text>
                </HStack>
              ))}
            </VStack>
          </div>
        </SectionCard>
      )}
    </VStack>
  );
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
  summary?: WorkloadSummary | null;
  analysis?: WorkloadAnalysis | null;
  queries: WorkloadQuery[];
  durationSeconds: number | undefined;
}) {
  return (
    <VStack className="gap-6 items-stretch w-full">
      <SectionCard icon="observe" title="Capture Summary">
        <div className="p-5 grid grid-cols-2 tablet:grid-cols-4 gap-4">
          <StatCard label="Duration" value={formatDuration(durationSeconds)} />
          <StatCard label="Unique Queries" value={`${summary?.unique_queries ?? queries.length}`} />
          <StatCard
            label="Executions"
            value={summary?.total_executions != null ? summary.total_executions.toLocaleString() : '-'}
          />
          <StatCard label="Total Query Time" value={formatMs(summary?.total_query_time_ms)} />
        </div>
      </SectionCard>

      {analysis && <WorkloadAnalysisView analysis={analysis} />}

      <Show when={queries.length > 0}>
        <SectionCard icon="observe" title={`Captured Queries (${queries.length})`}>
          <WorkloadQueriesTable queries={queries} />
        </SectionCard>
      </Show>
    </VStack>
  );
}

function WorkloadRunView({ run }: { run: WorkloadRun }) {
  const queries = run.queries || [];
  const summary: WorkloadSummary = {
    unique_queries: run.total_queries ?? queries.length,
    total_executions: queries.reduce((acc, q) => acc + (q.calls ?? 0), 0),
    total_query_time_ms: run.total_query_time_ms,
    duration_seconds: run.duration_seconds,
    queries,
  };
  return (
    <WorkloadReportView
      summary={summary}
      analysis={run.analysis}
      queries={queries}
      durationSeconds={run.duration_seconds}
    />
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

function AuditPage() {
  const queryClient = useQueryClient();
  const { target } = useTarget();
  const passwordLock = useTargetPasswordLock(target);

  const {
    run,
    state: runState,
    statusMessage,
    report: liveReport,
    error: runError,
    reset,
  } = useAuditRun();

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
  } = useAuditCapture();

  const [captureDuration, setCaptureDuration] = useState<number>(60);

  // A run loaded from history; cleared when a new live run starts.
  const [loadedReport, setLoadedReport] = useState<AuditReport | null>(null);
  const [loadedWorkload, setLoadedWorkload] = useState<WorkloadRun | null>(null);
  const [loadedRunId, setLoadedRunId] = useState<string | null>(null);
  const [loadingRunId, setLoadingRunId] = useState<string | null>(null);

  const isRunning = runState === 'running';
  const isCapturing = captureState === 'capturing' || captureState === 'analyzing';
  const busy = isRunning || isCapturing;
  const report = loadedReport ?? liveReport;

  const { data: runsData, refetch: refetchRuns } = useQuery({
    queryKey: ['audit-runs', target],
    queryFn: () => fetchAuditRuns(target!),
    enabled: !!target,
    staleTime: 30_000,
  });
  const runs = runsData?.runs || [];

  const clearLoaded = () => {
    setLoadedReport(null);
    setLoadedWorkload(null);
    setLoadedRunId(null);
  };

  const handleRun = async () => {
    if (!target) return;
    clearLoaded();
    resetCapture();
    await run(target);
    queryClient.invalidateQueries({ queryKey: ['audit-runs', target] });
    refetchRuns();
  };

  const handleCapture = async () => {
    if (!target) return;
    clearLoaded();
    reset();
    await runCapture(target, { duration: captureDuration });
    queryClient.invalidateQueries({ queryKey: ['audit-runs', target] });
    refetchRuns();
  };

  const handleLoadRun = async (summary: AuditRunSummary) => {
    setLoadingRunId(summary.run_id);
    try {
      const data = await fetchRunDetail(summary.run_id);
      reset();
      resetCapture();
      if (isWorkloadRun(data)) {
        setLoadedReport(null);
        setLoadedWorkload(data);
      } else {
        setLoadedWorkload(null);
        setLoadedReport(data as AuditReport);
      }
      setLoadedRunId(summary.run_id);
    } catch (err) {
      toast({
        title: 'Failed to load run',
        description: err instanceof Error ? err.message : String(err),
        variant: 'negative',
      });
    } finally {
      setLoadingRunId(null);
    }
  };

  return (
    <div className="space-y-6 w-full">
      {/* Hero Header */}
      <m.div
        className="space-y-4"
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className="justify-between items-start">
          <HStack className="gap-4 items-center">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-info-soft to-surface-primary-soft flex items-center justify-center">
              <Icon name="document-validation" label="Health Check" className="w-6 h-6 text-content-info-soft" />
            </div>
            <VStack className="gap-1 items-start">
              <Text as="h1" level="headline-3" className="text-content-layout-1">
                Health Check
              </Text>
              <Text level="body-small" className="text-content-layout-3">
                Full audit of "{target}": sizing verdict, slow spots, and cache opportunities.
              </Text>
            </VStack>
          </HStack>
          <HStack className="gap-3 items-center">
            <HStack className="gap-2 items-center rounded-2xl border border-border-layout-1 bg-surface-layout-2/50 px-2 py-1.5">
              <HStack className="gap-1 items-center">
                {CAPTURE_DURATIONS.map((option) => (
                  <button
                    key={option.seconds}
                    type="button"
                    onClick={() => setCaptureDuration(option.seconds)}
                    disabled={busy}
                    className={`px-2.5 py-1 rounded-lg text-xs tabular-nums transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                      captureDuration === option.seconds
                        ? 'bg-surface-primary-soft text-content-primary-soft'
                        : 'text-content-layout-3 hover:bg-surface-layout-2'
                    }`}
                  >
                    {option.label}
                  </button>
                ))}
              </HStack>
              {isCapturing ? (
                <Button
                  variant="negative"
                  modifier="outline"
                  label="Cancel"
                  icon="close"
                  iconPosition="left"
                  onClick={cancelCapture}
                />
              ) : (
                <Button
                  variant="rising"
                  modifier="outline"
                  label="Capture Workload"
                  icon="observe"
                  iconPosition="left"
                  onClick={handleCapture}
                  disabled={busy || !target || passwordLock.isLocked}
                />
              )}
            </HStack>
            <Button
              variant="primary"
              modifier="solid"
              label={report ? 'Run New Audit' : 'Run Audit'}
              icon="play"
              iconPosition="left"
              onClick={handleRun}
              loading={isRunning}
              disabled={busy || !target || passwordLock.isLocked}
            />
          </HStack>
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
                    {statusMessage || 'Running audit...'}
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
            <Icon name="alert" label="Error" className="w-4 h-4 text-content-negative-soft" />
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
                  <HStack className="gap-3 items-center justify-between">
                    <HStack className="gap-3 items-center">
                      <Spinner size="base" />
                      <Text level="body-small" className="text-content-layout-2">
                        {captureState === 'analyzing'
                          ? captureStatus || 'Analyzing captured workload...'
                          : captureStatus || 'Capturing live workload...'}
                      </Text>
                    </HStack>
                    {captureProgress && (
                      <Text level="mono-small" className="text-content-layout-3 tabular-nums shrink-0">
                        {Math.round(captureProgress.elapsedSeconds)}s
                        {captureProgress.totalSeconds ? ` / ${captureProgress.totalSeconds}s` : ''}
                      </Text>
                    )}
                  </HStack>

                  {captureProgress?.totalSeconds ? (
                    <div className="h-2 w-full rounded-full bg-surface-layout-2 overflow-hidden">
                      <div
                        className="h-full rounded-full bg-surface-primary-solid transition-[width] duration-500 ease-linear"
                        style={{
                          width: `${Math.min(
                            100,
                            (captureProgress.elapsedSeconds / captureProgress.totalSeconds) * 100,
                          )}%`,
                        }}
                      />
                    </div>
                  ) : null}

                  {captureProgress && (
                    <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
                      <StatCard label="Unique Queries" value={`${captureProgress.uniqueQueries}`} />
                      <StatCard label="Executions" value={captureProgress.totalExecutions.toLocaleString()} />
                      <StatCard label="TPS" value={captureProgress.tps.toFixed(1)} />
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
            <Icon name="alert" label="Warning" className="w-4 h-4 text-content-warning-soft" />
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
            <Icon name="alert" label="Error" className="w-4 h-4 text-content-negative-soft" />
            <Text level="body-small" className="text-content-negative-soft">
              {captureError}
            </Text>
          </HStack>
        </div>
      </Show>

      {/* Live capture result */}
      {captureState === 'complete' && captureResult && !loadedRunId && (
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
        >
          <VStack className="gap-3 items-stretch">
            <HStack className="gap-2 items-center">
              <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                Latest capture: {captureResult.runId}
              </Text>
            </HStack>
            <WorkloadReportView
              summary={captureResult.summary}
              analysis={captureResult.analysis}
              queries={captureResult.summary?.queries || []}
              durationSeconds={captureResult.summary?.duration_seconds ?? captureDuration}
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
                <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
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
                <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                  {loadedRunId ? `Saved run: ${loadedRunId}` : 'Latest audit'}
                </Text>
                <Text level="caption" className="text-content-layout-3">
                  {formatDate(report.audited_at)}
                </Text>
              </HStack>
            </HStack>
            <AuditReportView report={report} />
          </VStack>
        </m.div>
      )}

      {/* Past runs */}
      <Show when={runs.length > 0}>
        <SectionCard icon="folder-file" title={`Past Runs (${runs.length})`}>
          <div className="divide-y divide-border-layout-1">
            {runs.map((summary) => {
              const isCapture = (summary.duration_seconds ?? 0) > 0;
              const runLabel = isCapture ? 'Workload capture' : 'Quick audit';
              return (
                <button
                  key={summary.run_id}
                  type="button"
                  onClick={() => handleLoadRun(summary)}
                  className={`group w-full text-left px-5 py-3 hover:bg-surface-layout-2/50 transition-colors cursor-pointer ${
                    loadedRunId === summary.run_id ? 'bg-surface-primary-soft/10' : ''
                  }`}
                >
                  <HStack className="justify-between items-center gap-4">
                    <VStack className="gap-0.5 items-start min-w-0">
                      <HStack className="gap-2 items-baseline min-w-0">
                        <Text level="label-medium" className="text-content-layout-1 shrink-0">
                          {runLabel}
                        </Text>
                        <Text level="caption" className="text-content-layout-3 truncate">
                          {formatDate(summary.started_at)}
                        </Text>
                      </HStack>
                      <Text level="mono-small" className="text-content-layout-3 truncate">
                        {summary.run_id}
                      </Text>
                    </VStack>
                    <HStack className="gap-2 items-center shrink-0">
                      {loadingRunId === summary.run_id && <Spinner size="base" />}
                      {isCapture && (
                        <Tag
                          size="small"
                          variant="warning"
                          modifier="ghost"
                          label={formatDuration(summary.duration_seconds)}
                        />
                      )}
                      {summary.has_analysis && (
                        <Tag size="small" variant="positive" modifier="ghost" label="Analyzed" />
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
              );
            })}
          </div>
        </SectionCard>
      </Show>
    </div>
  );
}
