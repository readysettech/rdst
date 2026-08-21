import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { EmptyState } from '@rs/ui-new/empty-state'
import { IconButton } from '@rs/ui-new/icon-button'
import { IconTile } from '@rs/ui-new/icon-tile'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { compareStatusLabel } from './CompareRunning'
import type { CompareBatchStatus } from './compareRuns'
import {
  type CompareController,
  initialCompareConcurrency,
  MAX_COMPARE_CONCURRENCY,
} from './useCompareController'

function average(values: number[]) {
  return values.length > 0
    ? values.reduce((sum, value) => sum + value, 0) / values.length
    : 0
}

function statusVariant(status: CompareBatchStatus) {
  if (status === 'complete') return 'positive' as const
  if (status === 'partial' || status === 'running') return 'warning' as const
  return 'negative' as const
}

export function CompareHistory({
  controller,
}: {
  controller: CompareController
}) {
  return (
    <Card>
      <Card.Header className="items-start gap-4 tablet:flex-row tablet:items-center tablet:justify-between">
        <HStack className="min-w-0 items-center gap-3">
          <IconTile icon="observe" size="base" accent="primary" />
          <VStack className="min-w-0 items-start gap-0.5">
            <Card.Title>Comparison history</Card.Title>
            <Card.Description>
              Previous paired runs for {controller.target}. Stored on this
              device.
            </Card.Description>
          </VStack>
        </HStack>
        <Button
          size="small"
          variant="primary"
          modifier="ghost"
          label="Back to comparison"
          icon="arrow-left"
          onClick={() => controller.setHistoryOpen(false)}
        />
      </Card.Header>

      <Card.Content className="p-0">
        {controller.historyEntries.length === 0 && (
          <EmptyState
            layout="compact"
            icon="observe"
            title="No comparisons yet"
            body="Run a comparison to measure a query's speedup on Readyset — every paired run is saved here for this target."
            action={{
              label: 'New comparison',
              icon: 'add',
              onClick: () => {
                controller.clearBatch()
                controller.setHistoryOpen(false)
              },
            }}
          />
        )}
        {controller.historyEntries.map(({ batch, snapshot }) => {
          const speedup = average(
            snapshot.results.map(({ result }) => result.speedup_mean)
          )
          const completedAt = batch.outcome?.completedAt ?? batch.createdAt
          const initialConcurrency = initialCompareConcurrency(
            batch.queries.length
          )
          const loadLabel =
            batch.concurrency > MAX_COMPARE_CONCURRENCY
              ? `${batch.concurrency} clients per lane · legacy run`
              : initialConcurrency < batch.concurrency
                ? `${initialConcurrency} → ${batch.concurrency} total clients per lane`
                : `${batch.concurrency} total clients per lane`
          return (
            <div
              key={batch.id}
              className="grid gap-4 border-b border-border-layout-soft px-6 py-5 last:border-0 tablet:grid-cols-[minmax(0,1fr)_auto_auto]"
            >
              <VStack className="min-w-0 items-start gap-1">
                <HStack className="flex-wrap items-center gap-2">
                  <Text level="label-small" className="text-content-layout-1">
                    {new Date(completedAt).toLocaleString([], {
                      dateStyle: 'medium',
                      timeStyle: 'short',
                    })}
                  </Text>
                  <Tag
                    size="small"
                    variant={statusVariant(snapshot.status)}
                    modifier="ghost"
                    label={compareStatusLabel(snapshot.status)}
                  />
                </HStack>
                <Text level="caption" className="text-content-layout-3">
                  {batch.queries.length}{' '}
                  {batch.queries.length === 1 ? 'query' : 'queries'} ·{' '}
                  {loadLabel} · {batch.durationSeconds}s
                  {speedup > 0
                    ? ` · ${speedup.toFixed(speedup >= 10 ? 0 : 1)}× speedup`
                    : ''}
                </Text>
              </VStack>
              <Text
                level="mono-small"
                className="self-center text-content-layout-3"
              >
                {batch.target}
              </Text>
              <HStack className="items-center gap-1">
                <Button
                  size="small"
                  variant="primary"
                  modifier="outline"
                  label={
                    snapshot.status === 'running'
                      ? 'View progress'
                      : 'View result'
                  }
                  icon="arrow-right"
                  iconPosition="right"
                  onClick={() => controller.openHistoryBatch(batch)}
                />
                {snapshot.status !== 'running' && (
                  <IconButton
                    size="small"
                    variant="negative"
                    modifier="ghost"
                    label="Delete"
                    icon="trash"
                    onClick={() => controller.deleteHistoryBatch(batch.id)}
                  />
                )}
              </HStack>
            </div>
          )
        })}
      </Card.Content>

      {controller.historyEntries.length > 0 && (
        <Card.Footer className="justify-end">
          <Button
            variant="rising"
            modifier="solid"
            label="New comparison"
            icon="add"
            onClick={() => {
              controller.clearBatch()
              controller.setHistoryOpen(false)
            }}
          />
        </Card.Footer>
      )}
    </Card>
  )
}
