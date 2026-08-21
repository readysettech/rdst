import { Button } from '@rs/ui-new/button'
import { EmptyState } from '@rs/ui-new/empty-state'
import { For } from '@rs/ui-new/for'
import { InteractiveRow } from '@rs/ui-new/interactive-row'
import { Show } from '@rs/ui-new/show'
import { Skeleton } from '@rs/ui-new/skeleton'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import type { ReactNode } from 'react'
import { ComparisonCard } from '../../../components/CacheComparison'
import { QueryMetricRow } from '../../../components/QueryMetricRow'
import { QueryCardSql } from '../../../components/query-card/QueryCardSql'
import type { QueryRegistryEntry } from '../../../lib/api'
import { useCacheTestRunForQuery } from '../../../lib/backgroundRuns'
import {
  formatDuration,
  formatMeta,
  formatMs,
  formatTimestamp,
} from '../../../lib/formatters'
import { OBSERVED_EVIDENCE_PROVENANCE } from '../../../lib/queryEvidence'
import { formatDbTime, queryImpactMs } from '../../../lib/queryImpact'
import { isCacheRunResult } from '../../../types/cache'
import { analysisOutcome } from '../results/resultsSelectors'
import { relativeAge } from '../results/storedAnalysis'
import { useAnalysisHistory } from '../results/useStoredAnalysis'
import { compareOutcomeSummary } from './compareOutcome'

interface AnalyzeDrawerOverviewProps {
  entry: QueryRegistryEntry
  /** The stored analysis the Analyze tab is showing, if it has one. */
  currentAnalysisId?: string
  /** The target the library is reading, when the row carries none. */
  target?: string | null
  /** Open one stored analysis, which is the Analyze tab's job. */
  onOpenAnalysis: (analysisId: string) => void
  /** Measure the query again rather than reading what it already has. */
  onAnalyzeAgain: () => void
}

function Section({
  label,
  action,
  children,
}: {
  label: string
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <VStack className="items-stretch gap-2">
      <HStack className="items-center justify-between gap-3">
        <Text level="overline" className="text-content-layout-3">
          {label}
        </Text>
        {action}
      </HStack>
      {children}
    </VStack>
  )
}

/** The workload behind the query, in the card's own stat treatment. */
function EvidencePanel({ entry }: { entry: QueryRegistryEntry }) {
  const impactMs = queryImpactMs(entry)
  const runs = entry.observation_count ?? entry.frequency ?? 0
  const avgMs = entry.avg_duration_ms ?? 0
  const maxMs = entry.max_duration_ms ?? 0
  // Database time is the number this library ranks on, so it leads wherever
  // there is one; a query observed without it leads with how often it ran.
  const lead =
    impactMs > 0
      ? { label: 'Database time', value: formatDbTime(impactMs) }
      : { label: 'Observed runs', value: runs.toLocaleString() }

  return (
    <div
      title={OBSERVED_EVIDENCE_PROVENANCE}
      className="grid gap-6 rounded-lg border border-border-layout-1 p-4 laptop:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] laptop:gap-8"
    >
      <VStack className="items-start gap-1">
        <Text level="caption" className="text-content-layout-3">
          {lead.label}
        </Text>
        <Text level="headline-2" className="tabular-nums text-content-layout-1">
          {lead.value}
        </Text>
        <Text level="caption" className="text-content-layout-3">
          Across the captured workload
        </Text>
      </VStack>
      <VStack className="items-stretch justify-center gap-3">
        <Show when={impactMs > 0 && runs > 0}>
          <QueryMetricRow
            icon="database"
            label="Observed runs"
            value={runs.toLocaleString()}
          />
        </Show>
        <Show when={avgMs > 0}>
          <QueryMetricRow
            icon="observe"
            label="Avg latency"
            value={formatMs(avgMs)}
          />
        </Show>
        <Show when={maxMs > 0}>
          <QueryMetricRow
            icon="speedometer"
            label="Max latency"
            value={formatDuration(maxMs)}
          />
        </Show>
      </VStack>
    </div>
  )
}

/**
 * What this query already is and what has already been learned about it, in
 * one read: the workload behind it, its SQL, every analysis it has, and how it
 * compared against Readyset. Recall, not measurement — nothing here starts a
 * run.
 */
export function AnalyzeDrawerOverview({
  entry,
  currentAnalysisId,
  target,
  onOpenAnalysis,
  onAnalyzeAgain,
}: AnalyzeDrawerOverviewProps) {
  const history = useAnalysisHistory(entry.hash)
  const parameterKeys = Object.keys(entry.most_recent_params ?? {})
  // The full paired-latency profile exists only where the test ran. Where this
  // browser has one, it is the richer and more recent reading; everywhere else
  // the server's record of the outcome is all there is to report.
  const localRun = useCacheTestRunForQuery(entry.hash, entry.target || target)
  const localResult = isCacheRunResult(localRun?.result)
    ? localRun.result
    : undefined
  const durableCompare = localResult
    ? null
    : compareOutcomeSummary(entry.last_compare)
  const hasEvidence =
    queryImpactMs(entry) > 0 ||
    (entry.observation_count ?? entry.frequency ?? 0) > 0

  return (
    <VStack
      className="items-stretch gap-6"
      data-testid="analyze-drawer-overview"
    >
      {/* Identity itself belongs to the drawer header; what belongs here is
          the evidence behind the query and the query itself. */}
      <Show when={hasEvidence}>
        <Section label="Evidence">
          <EvidencePanel entry={entry} />
        </Section>
      </Show>

      <Section label="SQL">
        <div className="overflow-hidden rounded-lg border border-border-layout-1">
          <QueryCardSql sql={entry.sql} expandable copyable />
        </div>
      </Section>

      <Section
        label="Analyses"
        action={
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            icon="speedometer"
            iconPosition="left"
            label="Analyze again"
            onClick={onAnalyzeAgain}
          />
        }
      >
        <Show when={history.isLoading}>
          <Skeleton className="h-16 w-full" />
        </Show>
        <Show when={!history.isLoading && history.entries.length === 0}>
          <EmptyState
            icon="speedometer"
            title="No analysis yet"
            body="Analyzing this query explains why it is slow and whether Readyset can cache it."
            action={{
              label: 'Analyze',
              icon: 'speedometer',
              onClick: onAnalyzeAgain,
            }}
          />
        </Show>
        <VStack className="items-stretch gap-1">
          <For each={history.entries} keyExtractor={(run) => run.analysis_id}>
            {(run) => {
              const outcome = analysisOutcome(run)
              const current = run.analysis_id === currentAnalysisId
              return (
                <InteractiveRow
                  label={`Open the analysis from ${relativeAge(run.created_at)}`}
                  active={current}
                  onClick={() => onOpenAnalysis(run.analysis_id)}
                  className="rounded-lg border border-border-layout-1 px-3 py-2"
                >
                  <HStack className="flex-wrap items-center gap-2">
                    <Text level="body-small" className="text-content-layout-1">
                      {relativeAge(run.created_at)}
                    </Text>
                    <Show when={outcome}>
                      {(value) => (
                        <Tag
                          size="small"
                          variant={value.tone}
                          modifier="ghost"
                          label={value.label}
                        />
                      )}
                    </Show>
                    <Show when={current}>
                      <Text level="caption" className="text-content-layout-3">
                        current
                      </Text>
                    </Show>
                  </HStack>
                </InteractiveRow>
              )
            }}
          </For>
        </VStack>
      </Section>

      {/* Two readings of the same thing at two fidelities. The full profile —
          per-lane mean, P50, P95, sample counts and ranges — is held by the
          browser that measured it. The server row survives any browser but
          records only the outcome, so it never gets dressed up as the profile.
          With neither, the section is absent rather than empty. */}
      <Show when={localResult}>
        {(result) => (
          <Section label="Comparison">
            <VStack className="items-stretch gap-1.5">
              <ComparisonCard result={result} appearance="contained" />
              <Text level="caption" className="text-content-layout-3">
                Measured by this browser, from the test it ran on this query.
              </Text>
            </VStack>
          </Section>
        )}
      </Show>
      <Show when={durableCompare}>
        {(summary) => (
          <Section label="Comparison">
            <VStack className="items-stretch gap-1.5">
              <HStack className="flex-wrap items-center gap-2 rounded-lg border border-border-layout-1 px-3 py-2">
                <Tag
                  size="small"
                  variant={summary.tone}
                  modifier="ghost"
                  label={summary.headline}
                />
                <Show when={Boolean(summary.detail)}>
                  <Text level="caption" className="text-content-layout-3">
                    {summary.detail}
                  </Text>
                </Show>
                <Show when={Boolean(summary.when)}>
                  <Text
                    level="caption"
                    className="ml-auto text-content-layout-3"
                  >
                    {summary.when}
                  </Text>
                </Show>
              </HStack>
              <Text level="caption" className="text-content-layout-3">
                The recorded outcome of the last comparison. Run a test here to
                see the full latency profile.
              </Text>
            </VStack>
          </Section>
        )}
      </Show>

      <Section label="Details">
        <VStack className="items-stretch gap-1.5">
          <Show when={parameterKeys.length > 0}>
            <HStack className="flex-wrap items-center gap-1.5">
              <Text level="caption" className="text-content-layout-3">
                params
              </Text>
              {parameterKeys.map((key) => (
                <Tag
                  key={key}
                  size="small"
                  variant="neutral"
                  modifier="ghost"
                  label={key}
                />
              ))}
            </HStack>
          </Show>
          <Text level="caption" className="text-content-layout-3">
            {formatMeta([
              entry.last_analyzed
                ? `updated ${formatTimestamp(entry.last_analyzed).toLowerCase()}`
                : null,
              entry.first_analyzed &&
              entry.first_analyzed !== entry.last_analyzed
                ? `created ${formatTimestamp(entry.first_analyzed).toLowerCase()}`
                : null,
            ])}
          </Text>
        </VStack>
      </Section>
    </VStack>
  )
}
