import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { Link } from '@tanstack/react-router'
import { formatMeta, shortHash } from '../../../lib/formatters'
import { CompareLiveChart } from './CompareLiveChart'
import { COMPARE_QUEUED_MESSAGE, type CompareQueryOutcome } from './compareRuns'
import {
  CompareLaneDot,
  compareQueryOutcomePresentation,
  compareQueryStatusChip,
  formatQps,
} from './compareUi'

// Every card reserves the same frame, so the measuring card's border marks it
// without resizing the stack when it settles. A border sits inside the card's
// own box, which keeps it whole inside the page's scroll container.
const CARD_FRAME = 'border-2 border-transparent'

interface LaneReadout {
  qps: number
  p95: number
  errors: number
}

/** The numbers this query produced: its own result, or its latest sample. */
function laneReadouts(outcome: CompareQueryOutcome) {
  const result = outcome.result
  if (result) {
    return {
      upstream: {
        qps: result.origin.throughput_rps,
        p95: result.origin.p95_ms,
        errors: result.origin.errors,
      },
      readyset: {
        qps: result.readyset.throughput_rps,
        p95: result.readyset.p95_ms,
        errors: result.readyset.errors,
      },
    }
  }
  const sample = outcome.timeline[outcome.timeline.length - 1]
  if (!sample) return null
  return {
    upstream: {
      qps: sample.origin.throughput_rps,
      p95: sample.origin.p95_ms,
      errors: sample.origin.errors,
    },
    readyset: {
      qps: sample.readyset.throughput_rps,
      p95: sample.readyset.p95_ms,
      errors: sample.readyset.errors,
    },
  }
}

function Lane({
  label,
  readout,
  readyset = false,
}: {
  label: string
  readout: LaneReadout
  readyset?: boolean
}) {
  return (
    <VStack className="items-start gap-0.5">
      <HStack className="items-center gap-2">
        <CompareLaneDot lane={readyset ? 'readyset' : 'upstream'} />
        <Text level="label-small" className="text-content-layout-1">
          {label}
        </Text>
      </HStack>
      <Text
        level="headline-2"
        className={cn(
          'tabular-nums',
          readyset ? 'text-content-positive-soft' : 'text-content-layout-1'
        )}
      >
        {formatQps(readout.qps)}
      </Text>
      <Text level="caption" className="text-content-layout-3 tabular-nums">
        QPS · {readout.p95.toFixed(1)} ms p95
      </Text>
    </VStack>
  )
}

function QueryCardTitle({
  outcome,
  queryHash,
}: {
  outcome: CompareQueryOutcome
  queryHash?: string
}) {
  return (
    <HStack className="min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
      <Text
        level="label-medium"
        className="min-w-0 truncate font-semibold text-content-layout-1"
      >
        {outcome.label || 'Query'}
      </Text>
      {queryHash && (
        <Text level="mono-small" className="text-content-layout-3">
          {shortHash(queryHash)}
        </Text>
      )}
    </HStack>
  )
}

function ViewQueryAction({
  queryHash,
  onOpenQueries,
}: {
  queryHash?: string
  onOpenQueries: () => void
}) {
  if (!queryHash) {
    return (
      <Button
        size="small"
        variant="primary"
        modifier="ghost"
        label="View queries"
        icon="folder-file"
        onClick={onOpenQueries}
      />
    )
  }
  return (
    <Text level="label-small">
      <Link
        to="/queries"
        search={{ analyze: queryHash, tab: 'overview' }}
        className="text-content-primary-solid hover:underline"
      >
        View query
      </Link>
    </Text>
  )
}

/**
 * One query in the batch, in the shape the Query Library's impact card uses:
 * a rail of numbers on the left, that query's own curve on the right, and a
 * footer band carrying the verdict and the way back to the query.
 *
 * The card has three shapes, so the page stays short while a batch runs: a
 * queued query is a single row, the measuring query is raised and live, and a
 * settled query is the full anatomy, frozen.
 */
export function CompareQueryCard({
  outcome,
  queryHash,
  durationSeconds,
  onOpenQueries,
}: {
  outcome: CompareQueryOutcome
  queryHash?: string
  durationSeconds: number
  onOpenQueries: () => void
}) {
  const status = compareQueryStatusChip(outcome)

  if (outcome.status === 'queued') {
    return (
      <Card data-cache-id={outcome.cacheId} className={CARD_FRAME}>
        <Card.Content className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
          <QueryCardTitle outcome={outcome} queryHash={queryHash} />
          <HStack className="items-center gap-3">
            <Text level="caption" className="text-content-layout-3">
              {outcome.message || COMPARE_QUEUED_MESSAGE}
            </Text>
            <Tag
              size="small"
              variant={status.variant}
              modifier="ghost"
              label={status.label}
            />
          </HStack>
        </Card.Content>
      </Card>
    )
  }

  const running = outcome.status === 'running'
  const readouts = laneReadouts(outcome)
  const speedup =
    outcome.status === 'succeeded'
      ? compareQueryOutcomePresentation(outcome)
      : null
  const elapsed = Math.round(outcome.elapsedSeconds)
  const percent = Math.min(
    100,
    Math.round(outcome.percent ?? (elapsed / durationSeconds) * 100)
  )
  const secondsLeft = Math.max(0, durationSeconds - elapsed)
  const errors = readouts
    ? readouts.upstream.errors + readouts.readyset.errors
    : 0
  const meta = formatMeta([
    outcome.result
      ? `${Math.round(outcome.result.elapsed_seconds)}s measured`
      : running
        ? `${elapsed}s of ${durationSeconds}s`
        : null,
    readouts ? `${errors} ${errors === 1 ? 'error' : 'errors'}` : null,
    queryHash ? `hash ${shortHash(queryHash)}` : null,
  ])

  return (
    <Card
      data-cache-id={outcome.cacheId}
      className={cn(
        'overflow-hidden',
        CARD_FRAME,
        // The measuring query is the one the reader is waiting on, so it is
        // marked out from the cards that are already settled or still waiting.
        running && 'border-border-primary-soft'
      )}
    >
      <Card.Content className="overflow-hidden rounded-none border-0 bg-transparent p-0">
        <div className="grid laptop:grid-cols-[13rem_minmax(0,1fr)]">
          <VStack className="items-stretch gap-4 border-b border-border-layout-1 bg-surface-layout-2/30 p-5 laptop:border-r laptop:border-b-0">
            {readouts ? (
              <>
                <Lane label="Upstream" readout={readouts.upstream} />
                <Lane label="Readyset" readout={readouts.readyset} readyset />
              </>
            ) : (
              <Text level="caption" className="text-content-layout-3">
                No measurement was produced for this query.
              </Text>
            )}
            {/* Only a measurement has a speedup; every other verdict is the
                footer band's chip, so it is never chipped twice. */}
            {speedup && (
              <HStack>
                <Tag
                  size="small"
                  variant={speedup.variant}
                  modifier="ghost"
                  label={speedup.label}
                />
              </HStack>
            )}
          </VStack>

          <VStack className="min-w-0 items-stretch gap-3 p-5">
            <QueryCardTitle outcome={outcome} queryHash={queryHash} />
            {running && (
              <VStack className="items-stretch gap-1.5">
                <Text level="caption" className="text-content-primary-soft">
                  Running · {percent}% · ~{secondsLeft}s left
                </Text>
                <HStack className="items-center gap-2">
                  <div
                    role="progressbar"
                    aria-label={`${outcome.label} progress`}
                    aria-valuenow={percent}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    className="h-1.5 flex-1 overflow-hidden rounded-full bg-border-layout-1"
                  >
                    <div
                      className="h-full rounded-full bg-surface-primary-solid transition-[width] duration-slow"
                      style={{ width: `${percent}%` }}
                    />
                  </div>
                </HStack>
              </VStack>
            )}
            {/* Batches settled before per-query curves were retained have
                nothing to draw; the card still carries their verdict. */}
            {outcome.timeline.length > 0 ? (
              <CompareLiveChart
                timeline={outcome.timeline}
                queryLabel={outcome.label || 'this query'}
                live={running}
              />
            ) : (
              <Text level="body-small" className="text-content-layout-2">
                {outcome.message ||
                  'No measurement curve was kept for this query.'}
              </Text>
            )}
          </VStack>
        </div>
      </Card.Content>

      <Card.Content className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <HStack className="min-w-0 flex-wrap items-center gap-3">
          <Tag
            size="small"
            variant={status.variant}
            modifier="ghost"
            label={status.label}
          />
          {meta && (
            <Text level="mono-small" className="text-content-layout-3">
              {meta}
            </Text>
          )}
        </HStack>
        <ViewQueryAction queryHash={queryHash} onOpenQueries={onOpenQueries} />
      </Card.Content>
    </Card>
  )
}
