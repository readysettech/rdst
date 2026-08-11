import { Alert } from '@rs/ui-new/alert'
import { CopyButton } from '@rs/ui-new/copy-button'
import { Show } from '@rs/ui-new/show'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { ComparisonCard } from '../../../components/CacheComparison'
import type { BackgroundRunState } from '../../../lib/backgroundRuns'
import { formatDuration, formatTimestamp } from '../../../lib/formatters'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'
import { isCacheRunResult } from '../../../types/cache'

interface SavedQueryDetailsProps {
  entry: QueryRegistryEntry
  cacheTestRun?: BackgroundRunState
  onDismissRun: (runId: string) => void
  onClose: () => void
  showMetadata?: boolean
}

export function SavedQueryDetails({
  entry,
  cacheTestRun,
  onDismissRun,
  onClose,
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
              <Text level="caption" className="text-content-layout-3">
                max {formatDuration(entry.max_duration_ms)}
              </Text>
            </Show>

            <Show when={(entry.observation_count ?? 0) > 0}>
              <Text level="caption" className="text-content-layout-3">
                {entry.observation_count} obs
              </Text>
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
