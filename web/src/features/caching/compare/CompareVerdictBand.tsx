import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { formatSecondsShort } from '../../../lib/formatters'
import { summarizeSustainedComparison } from '../comparisonMetrics'
import {
  type CompareBatchSnapshot,
  isNotComparableCompareOutcome,
} from './compareRuns'
import {
  CompareLaneDot,
  compareBatchProgressLine,
  compareBatchRemainingSeconds,
  comparePreparationState,
  compareStatusLabel,
  formatQps,
  formatSpeedup,
} from './compareUi'
import {
  type CompareController,
  DEFAULT_COMPARE_DURATION,
} from './useCompareController'

// An incomplete batch reads as classification, not as an app failure: most of
// what keeps a query out of "compared" is Readyset declining to cache it or a
// pre-flight check ruling the two lanes out, not something breaking.
function compareBatchOutcomeSummary(snapshot: CompareBatchSnapshot): string {
  const notComparable = snapshot.queryOutcomes.filter(
    isNotComparableCompareOutcome
  ).length
  const failed = snapshot.failed - notComparable
  const parts = [`${snapshot.succeeded} compared`]
  if (notComparable > 0) parts.push(`${notComparable} not comparable`)
  if (failed > 0) parts.push(`${failed} failed`)
  if (snapshot.cancelled > 0) parts.push(`${snapshot.cancelled} cancelled`)
  return parts.join(', ')
}

/**
 * The whole batch on one row: what it found, how far along it is, and the
 * actions that act on the batch rather than on a single query. Each query's
 * own numbers, curve and verdict live on its own card below.
 */
export function CompareVerdictBand({
  controller,
}: {
  controller: CompareController
}) {
  const snapshot = controller.snapshot
  if (!snapshot) return null
  const running = snapshot.status === 'running'
  const preparation = comparePreparationState(snapshot)
  const results = snapshot.results
  const comparison = summarizeSustainedComparison(
    results.map(({ result }) => result)
  )
  const readysetWins = comparison.winner === 'readyset'
  const durationSeconds =
    controller.batch?.durationSeconds ?? DEFAULT_COMPARE_DURATION
  const remaining = compareBatchRemainingSeconds(snapshot, durationSeconds)
  const progressLine = compareBatchProgressLine(snapshot)

  const headline = running
    ? preparation
      ? 'Preparing comparison'
      : 'Comparing upstream and Readyset'
    : results.length === 0
      ? 'No measurements completed'
      : readysetWins
        ? `${formatSpeedup(comparison.speedup)}× faster with Readyset`
        : `${Math.abs(comparison.improvementPercent).toFixed(0)}% slower with Readyset`

  const detail = running
    ? `${progressLine}${remaining > 0 ? ` · ~${formatSecondsShort(remaining)} left` : ''}`
    : `${progressLine} · ${compareBatchOutcomeSummary(snapshot)}`
  // The two lanes compress to one line here, each behind the colour key its
  // curve carries; the per-query cards below carry the same split query by
  // query.
  const totals =
    results.length > 0
      ? {
          upstream: `Upstream ${formatQps(comparison.upstream.throughput)} QPS · ${comparison.upstream.p95.toFixed(1)} ms p95`,
          readyset: `Readyset ${formatQps(comparison.readyset.throughput)} QPS · ${comparison.readyset.p95.toFixed(1)} ms p95`,
          requests: `${(
            comparison.upstream.completed + comparison.readyset.completed
          ).toLocaleString()} requests`,
        }
      : null

  return (
    <Card>
      <Card.Content className="flex flex-wrap items-center gap-x-6 gap-y-3 px-5 py-4">
        {/* One announcement per state change: the per-second samples on the
            cards below would otherwise re-read the whole band every tick. */}
        <span aria-live="polite" className="sr-only">
          {progressLine}
        </span>
        {running && <Spinner size="base" color="primary-soft" />}
        <VStack className="min-w-0 flex-1 items-start gap-1">
          <HStack className="flex-wrap items-center gap-3">
            <Text level="subtitle-1" className="text-content-layout-1">
              {headline}
            </Text>
            <Tag
              variant={
                running
                  ? 'informative'
                  : snapshot.status === 'complete'
                    ? 'positive'
                    : snapshot.status === 'partial'
                      ? 'warning'
                      : 'negative'
              }
              modifier="solid"
              label={preparation?.label ?? compareStatusLabel(snapshot.status)}
            />
          </HStack>
          <Text level="caption" className="text-content-layout-2">
            {detail}
          </Text>
          {preparation ? (
            <Text level="caption" className="text-content-layout-3">
              {preparation.message}
            </Text>
          ) : totals ? (
            <HStack className="flex-wrap items-center gap-x-4 gap-y-1">
              <HStack className="items-center gap-1.5">
                <CompareLaneDot lane="upstream" />
                <Text
                  level="caption"
                  className="text-content-layout-3 tabular-nums"
                >
                  {totals.upstream}
                </Text>
              </HStack>
              <HStack className="items-center gap-1.5">
                <CompareLaneDot lane="readyset" />
                <Text
                  level="caption"
                  className="text-content-layout-3 tabular-nums"
                >
                  {totals.readyset}
                </Text>
              </HStack>
              <Text
                level="caption"
                className="text-content-layout-3 tabular-nums"
              >
                {totals.requests}
              </Text>
            </HStack>
          ) : null}
        </VStack>

        <HStack className="flex-wrap items-center justify-end gap-2">
          {running ? (
            <>
              <Text level="caption" className="text-content-layout-3">
                This comparison continues if you leave the page.
              </Text>
              <Button
                size="small"
                variant="negative"
                modifier="ghost"
                label="Stop comparison"
                icon="close"
                iconPosition="left"
                onClick={() => void controller.cancelComparison()}
              />
            </>
          ) : (
            <>
              <Button
                size="small"
                variant="primary"
                modifier="ghost"
                label={`History ${controller.historyEntries.length}`}
                icon="observe"
                onClick={() => controller.setHistoryOpen(true)}
              />
              <Button
                size="small"
                variant="primary"
                modifier="outline"
                label="Adjust"
                icon="settings"
                onClick={controller.clearBatch}
              />
              <Button
                size="small"
                variant="rising"
                modifier="solid"
                label="Run again"
                icon="play"
                onClick={() => {
                  controller.clearBatch()
                  controller.setReviewOpen(true)
                }}
              />
            </>
          )}
        </HStack>
      </Card.Content>
    </Card>
  )
}
