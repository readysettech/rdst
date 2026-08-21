import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { CopyButton } from '@rs/ui-new/copy-button'
import { Show } from '@rs/ui-new/show'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { ComparisonCard } from '../../../components/CacheComparison'
import type { BackgroundRunState } from '../../../lib/backgroundRuns'
import { formatDuration, formatTimestamp } from '../../../lib/formatters'
import { OBSERVED_EVIDENCE_PROVENANCE } from '../../../lib/queryEvidence'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'
import { isCacheRunResult } from '../../../types/cache'
import { analysisOutcome } from '../results/resultsSelectors'
import { useLatestAnalysis } from './useLatestAnalysis'

interface SavedQueryDetailsProps {
  entry: QueryRegistryEntry
  cacheTestRun?: BackgroundRunState
  onDismissRun: (runId: string) => void
  onClose: () => void
  /** Opens the stored analysis read-only. Absent when none is stored yet. */
  onViewAnalysis?: () => void
  showMetadata?: boolean
}

export function SavedQueryDetails({
  entry,
  cacheTestRun,
  onDismissRun,
  onClose,
  onViewAnalysis,
  showMetadata = true,
}: SavedQueryDetailsProps) {
  const isTesting =
    cacheTestRun?.status === 'running' ||
    cacheTestRun?.status === 'reconnecting'
  const comparisonResult = isCacheRunResult(cacheTestRun?.result)
    ? cacheTestRun.result
    : undefined
  const cacheTestRunId = cacheTestRun?.runId
  const resultUnavailable =
    (cacheTestRun?.status === 'done' || cacheTestRun?.status === 'partial') &&
    !comparisonResult
  const parameterKeys = Object.keys(entry.most_recent_params ?? {})
  const lastAnalyzedAt = entry.last_analyzed_at || ''
  const analysisCount = entry.analysis_count ?? 0
  const latestAnalysis = useLatestAnalysis(entry.hash, Boolean(lastAnalyzedAt))
  const outcome = analysisOutcome(latestAnalysis)

  return (
    <VStack className="gap-3 items-stretch">
      <Show when={isTesting}>
        <HStack className="gap-2 rounded-lg border border-border-primary-soft/30 bg-surface-primary-soft/15 px-4 py-3 items-center">
          <span className="shrink-0">
            <Spinner size="base" color="primary-soft" />
          </span>
          <VStack className="gap-0.5 items-start">
            <Text level="label-small" className="text-content-primary-soft">
              Testing in the background
            </Text>
            <Text level="caption" className="text-content-layout-3">
              {cacheTestRun?.message || 'Connecting...'}
            </Text>
          </VStack>
        </HStack>
      </Show>

      <Show when={cacheTestRun?.status === 'failed'}>
        <Alert
          variant="negative"
          modifier="outline"
          label={`Performance test failed: ${cacheTestRun?.message || 'The comparison did not complete.'}`}
        />
      </Show>

      <Show when={resultUnavailable}>
        <Alert
          variant="warning"
          modifier="outline"
          icon="alert"
          iconPosition="left"
          label="Performance result unavailable. Run the test again to refresh it."
        />
      </Show>

      <Show when={comparisonResult}>
        {(result) => (
          <ComparisonCard
            result={result}
            onDelete={
              cacheTestRunId ? () => onDismissRun(cacheTestRunId) : undefined
            }
            onClose={onClose}
          />
        )}
      </Show>

      <Show when={Boolean(lastAnalyzedAt)}>
        <HStack className="justify-between gap-3 flex-wrap items-center rounded-lg border border-border-layout-1 px-4 py-3">
          <VStack className="gap-0.5 items-start">
            <HStack className="gap-2 items-center">
              <Text level="label-small" className="text-content-layout-1">
                Last analysis
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
            </HStack>
            <Text level="caption" className="text-content-layout-3">
              {formatTimestamp(lastAnalyzedAt)}
              {analysisCount > 1 ? ` · ${analysisCount} analyses` : ''}
            </Text>
          </VStack>
          <Show when={Boolean(onViewAnalysis)}>
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              icon="speedometer"
              iconPosition="left"
              label="View analysis"
              title="Opens the stored analysis without re-running it"
              onClick={onViewAnalysis}
            />
          </Show>
        </HStack>
      </Show>

      <Show when={showMetadata}>
        <HStack className="justify-between gap-x-4 gap-y-1.5 flex-wrap items-center">
          <HStack className="gap-x-4 gap-y-1.5 flex-wrap items-center">
            <HStack className="gap-1.5 items-center">
              <Text level="caption" className="text-content-layout-3">
                hash
              </Text>
              <Text level="mono-small" className="text-content-layout-2">
                {entry.hash.slice(0, 8)}
              </Text>
              <CopyButton text={entry.hash} />
            </HStack>

            <Show when={(entry.max_duration_ms ?? 0) > 0}>
              <span title={OBSERVED_EVIDENCE_PROVENANCE}>
                <Text level="caption" className="text-content-layout-3">
                  max {formatDuration(entry.max_duration_ms)}
                </Text>
              </span>
            </Show>

            <Show when={(entry.observation_count ?? 0) > 0}>
              <span title={OBSERVED_EVIDENCE_PROVENANCE}>
                <Text level="caption" className="text-content-layout-3">
                  {entry.observation_count} obs
                </Text>
              </span>
            </Show>

            <Show when={parameterKeys.length > 0}>
              <HStack className="gap-1.5 items-center flex-wrap">
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
          </HStack>

          <HStack className="gap-x-4 gap-y-1 flex-wrap items-center">
            <Text level="caption" className="text-content-layout-3">
              Updated {formatTimestamp(entry.last_analyzed)}
            </Text>
            <Show
              when={
                !!entry.first_analyzed &&
                entry.first_analyzed !== entry.last_analyzed
              }
            >
              <Text level="caption" className="text-content-layout-3">
                Created {formatTimestamp(entry.first_analyzed || '')}
              </Text>
            </Show>
          </HStack>
        </HStack>
      </Show>
    </VStack>
  )
}
