import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { IconTile } from '@rs/ui-new/icon-tile'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { compareBatchProgressLine } from './CompareRunRail'
import type { CompareBatchSnapshot, CompareBatchStatus } from './compareRuns'
import { CompareSummaryRow } from './compareUi'
import {
  type CompareController,
  MAX_COMPARE_CONCURRENCY,
} from './useCompareController'

function RunLane({
  label,
  detail,
  qps,
  p95,
  errors,
}: {
  label: string
  detail: string
  qps: number
  p95: number
  errors: number
}) {
  return (
    <div className="min-w-0 p-5">
      <HStack className="items-center gap-3">
        <IconTile
          icon="database"
          size="sm"
          accent={label === 'Readyset' ? 'positive' : 'info'}
        />
        <VStack className="items-start gap-0.5">
          <Text level="label-small" className="text-content-layout-1">
            {label}
          </Text>
          <Text level="caption" className="text-content-layout-3">
            {detail}
          </Text>
        </VStack>
      </HStack>
      <div className="mt-5 grid grid-cols-3 gap-4 border-t border-border-layout-soft pt-4">
        <CompareSummaryRow
          label="Actual QPS"
          value={qps.toFixed(qps >= 10 ? 0 : 1)}
          tone={label === 'Readyset' ? 'positive' : 'neutral'}
        />
        <CompareSummaryRow label="p95" value={`${p95.toFixed(1)} ms`} />
        <CompareSummaryRow
          label="Errors"
          value={`${errors}`}
          tone={errors > 0 ? 'warning' : 'neutral'}
        />
      </div>
    </div>
  )
}

export function compareStatusLabel(status: CompareBatchStatus) {
  if (status === 'complete') return 'Complete'
  if (status === 'partial') return 'Completed with errors'
  if (status === 'failed') return 'Failed'
  if (status === 'cancelled') return 'Cancelled'
  return 'Comparing'
}

/**
 * The batch has started but nothing has measured yet, so there is no curve to
 * read and the honest thing to report is what the sandbox is doing.
 */
export function comparePreparationState(snapshot: CompareBatchSnapshot) {
  if (snapshot.status !== 'running') return null
  if (snapshot.queryOutcomes.some((outcome) => outcome.timeline.length > 0)) {
    return null
  }
  const active = snapshot.runs.find((run) =>
    ['running', 'reconnecting', 'needs_key'].includes(run.status)
  )
  const stage = active?.stage ?? 'connecting'
  const label =
    stage === 'queued' ? 'Queued' : stage === 'warming' ? 'Warming' : 'Starting'
  return {
    label,
    message: active?.message || 'Preparing the temporary Readyset sandbox.',
  }
}

export function CompareRunning({
  controller,
}: {
  controller: CompareController
}) {
  const snapshot = controller.snapshot
  if (!snapshot) return null
  const running = snapshot.status === 'running'
  const preparation = comparePreparationState(snapshot)
  const selected = controller.selectedOutcome
  const latest = selected?.timeline[selected.timeline.length - 1]

  return (
    <Card>
      <Card.Header className="items-start gap-4 tablet:flex-row tablet:items-center tablet:justify-between">
        <HStack className="items-center gap-3">
          <IconTile icon="play" size="base" accent="primary" />
          <VStack className="items-start gap-0.5">
            <Card.Title>
              {preparation
                ? 'Preparing comparison'
                : 'Comparing upstream and Readyset'}
            </Card.Title>
            <Card.Description>
              {preparation
                ? preparation.message
                : 'Queries run one at a time; throughput is measured independently per lane.'}
            </Card.Description>
          </VStack>
        </HStack>
        <Tag
          variant="informative"
          modifier="solid"
          label={preparation?.label ?? compareStatusLabel(snapshot.status)}
        />
      </Card.Header>
      <Card.Content>
        {/* One announcement per state change: the per-second samples below
            would otherwise re-read the whole card every tick. */}
        <span aria-live="polite" className="sr-only">
          {compareBatchProgressLine(snapshot)}
        </span>
        {preparation ? (
          <HStack className="items-center gap-4 rounded-xl border border-border-primary-soft bg-surface-primary-soft px-5 py-4">
            <Spinner size="base" color="primary-soft" />
            <VStack className="items-start gap-0.5">
              <Text level="label-small" className="text-content-layout-1">
                {preparation.label}
              </Text>
              <Text level="body-small" className="text-content-layout-2">
                {preparation.message}
              </Text>
            </VStack>
          </HStack>
        ) : (
          <VStack className="items-stretch gap-5">
            <HStack className="items-center justify-between gap-4">
              <Text level="caption" className="text-content-layout-3">
                Latest sample from {selected?.label || 'the selected query'}
              </Text>
              <Text
                level="label-small"
                className="text-content-layout-1 tabular-nums"
              >
                {controller.concurrency} of {MAX_COMPARE_CONCURRENCY} clients
                per lane
                {controller.updatingLoad ? ' · increasing…' : ''}
              </Text>
            </HStack>

            <div className="grid overflow-hidden rounded-xl border border-border-layout-soft tablet:grid-cols-2 tablet:divide-x tablet:divide-border-layout-soft">
              <RunLane
                label="Upstream"
                detail={`${controller.concurrency} concurrent clients`}
                qps={latest?.origin.throughput_rps ?? 0}
                p95={latest?.origin.p95_ms ?? 0}
                errors={latest?.origin.errors ?? 0}
              />
              <RunLane
                label="Readyset"
                detail={`${controller.concurrency} concurrent clients`}
                qps={latest?.readyset.throughput_rps ?? 0}
                p95={latest?.readyset.p95_ms ?? 0}
                errors={latest?.readyset.errors ?? 0}
              />
            </div>
          </VStack>
        )}
      </Card.Content>
      <Card.Footer>
        <Text level="caption" className="mr-auto text-content-layout-3">
          This comparison continues if you leave the page.
        </Text>
        {running && (
          <Button
            variant="negative"
            modifier="ghost"
            label="Stop comparison"
            icon="close"
            iconPosition="left"
            onClick={() => void controller.cancelComparison()}
          />
        )}
      </Card.Footer>
    </Card>
  )
}
