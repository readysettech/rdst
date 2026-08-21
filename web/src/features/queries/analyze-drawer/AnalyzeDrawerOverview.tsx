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
import { QueryCardSql } from '../../../components/query-card/QueryCardSql'
import type { QueryRegistryEntry } from '../../../lib/api'
import {
  formatDuration,
  formatMeta,
  formatMs,
  formatTimestamp,
} from '../../../lib/formatters'
import { OBSERVED_EVIDENCE_PROVENANCE } from '../../../lib/queryEvidence'
import {
  formatDbTime,
  formatRunCount,
  queryImpactMs,
} from '../../../lib/queryImpact'
import { analysisOutcome } from '../results/resultsSelectors'
import { relativeAge } from '../results/storedAnalysis'
import { useAnalysisHistory } from '../results/useStoredAnalysis'
import { compareOutcomeSummary } from './compareOutcome'

interface AnalyzeDrawerOverviewProps {
  entry: QueryRegistryEntry
  /** The stored analysis the Analyze tab is showing, if it has one. */
  currentAnalysisId?: string
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

/**
 * What this query already is and what has already been learned about it, in
 * one read: identity, its SQL, every analysis it has, and the outcome of the
 * last comparison. Recall, not measurement — nothing here starts a run.
 */
export function AnalyzeDrawerOverview({
  entry,
  currentAnalysisId,
  onOpenAnalysis,
  onAnalyzeAgain,
}: AnalyzeDrawerOverviewProps) {
  const history = useAnalysisHistory(entry.hash)
  const compare = compareOutcomeSummary(entry.last_compare)
  const parameterKeys = Object.keys(entry.most_recent_params ?? {})
  const evidence = formatMeta([
    formatRunCount(entry),
    (entry.avg_duration_ms ?? 0) > 0
      ? `avg ${formatMs(entry.avg_duration_ms)}`
      : null,
    (entry.max_duration_ms ?? 0) > 0
      ? `max ${formatDuration(entry.max_duration_ms)}`
      : null,
    queryImpactMs(entry) > 0
      ? `${formatDbTime(queryImpactMs(entry))} of database time`
      : null,
  ])

  return (
    <VStack
      className="items-stretch gap-6"
      data-testid="analyze-drawer-overview"
    >
      {/* Identity itself belongs to the drawer header; what belongs here is
          the evidence behind the query and the query itself. */}
      <VStack className="items-stretch gap-2">
        <Show when={Boolean(evidence)}>
          <span title={OBSERVED_EVIDENCE_PROVENANCE}>
            <Text level="caption" className="text-content-layout-3">
              {evidence}
            </Text>
          </span>
        </Show>
        <div className="overflow-hidden rounded-lg border border-border-layout-1">
          <QueryCardSql sql={entry.sql} expandable copyable />
        </div>
      </VStack>

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

      {/* Absent until the server has recorded a comparison for this query.
          There is no device-local fallback here: a comparison nobody can see
          from another browser is exactly the bug this row fixes. */}
      <Show when={compare}>
        {(summary) => (
          <Section label="Last comparison">
            <HStack className="flex-wrap items-center gap-2 rounded-lg border border-border-layout-1 px-3 py-2">
              <Tag
                size="small"
                variant={summary.tone}
                modifier="ghost"
                label={summary.headline}
              />
              <Show when={Boolean(summary.when)}>
                <Text level="caption" className="text-content-layout-3">
                  {summary.when}
                </Text>
              </Show>
              <Show when={Boolean(summary.detail)}>
                <Text level="caption" className="ml-auto text-content-layout-3">
                  {summary.detail}
                </Text>
              </Show>
            </HStack>
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
