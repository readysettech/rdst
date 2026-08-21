import { cn } from '@rs/tailwind-base'
import { Card } from '@rs/ui-new/card-2'
import { Icon, type IconStrokeName } from '@rs/ui-new/icon'
import { InteractiveRow } from '@rs/ui-new/interactive-row'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { formatSecondsShort } from '../../../lib/formatters'
import type { CompareBatchSnapshot, CompareQueryOutcome } from './compareRuns'
import { COMPARE_QUEUED_MESSAGE } from './compareRuns'
import { compareQueryOutcomePresentation } from './compareUi'
import {
  type CompareController,
  DEFAULT_COMPARE_DURATION,
} from './useCompareController'

/**
 * One line naming what the whole batch is doing, from the snapshot's own
 * counters. The sandbox measures one query at a time, so "running" is the
 * single query holding the lease and "queued" is everything behind it.
 */
export function compareBatchProgressLine(
  snapshot: CompareBatchSnapshot
): string {
  // "done", not "compared": a not-comparable or failed query is finished
  // without having been compared; the summary card carries that split.
  const parts = [`${snapshot.completed} of ${snapshot.total} done`]
  if (snapshot.running > 0) parts.push(`${snapshot.running} running`)
  if (snapshot.queued > 0) parts.push(`${snapshot.queued} queued`)
  return parts.join(' · ')
}

/**
 * Seconds of measurement still ahead: whatever the running query has left,
 * plus a full duration for every query still waiting for the sandbox.
 */
export function compareBatchRemainingSeconds(
  snapshot: CompareBatchSnapshot,
  durationSeconds: number
): number {
  return snapshot.queryOutcomes.reduce((remaining, outcome) => {
    if (outcome.status === 'queued') return remaining + durationSeconds
    if (outcome.status === 'running') {
      return remaining + Math.max(0, durationSeconds - outcome.elapsedSeconds)
    }
    return remaining
  }, 0)
}

type RowState = {
  /** `null` renders the running spinner instead of a static glyph. */
  icon: IconStrokeName | null
  tone: string
  detail: string
}

/**
 * Every row carries a glyph, a worded state and a tone -- three distinctions,
 * so the state never rests on colour alone.
 */
function compareRailRowState(
  outcome: CompareQueryOutcome,
  durationSeconds: number
): RowState {
  if (outcome.status === 'queued') {
    return {
      icon: 'minus',
      tone: 'text-content-layout-3',
      detail: outcome.message || COMPARE_QUEUED_MESSAGE,
    }
  }
  if (outcome.status === 'running') {
    const elapsed = Math.round(outcome.elapsedSeconds)
    const left = Math.max(0, durationSeconds - elapsed)
    return {
      icon: null,
      tone: 'text-content-primary-soft',
      detail: `Running · ${elapsed}s of ${durationSeconds}s · ${left}s left`,
    }
  }
  if (outcome.status === 'succeeded') {
    return {
      icon: 'tick-double',
      tone: 'text-content-layout-3',
      detail: 'Compared',
    }
  }
  if (outcome.status === 'cancelled') {
    return {
      icon: 'close',
      tone: 'text-content-warning-soft',
      detail: outcome.message || 'Comparison was cancelled.',
    }
  }
  // A query Readyset declined to cache, or one the two lanes cannot be
  // compared on, is a verdict about the query -- an information glyph, not
  // an alert. Only a genuine failure goes negative.
  const presentation = compareQueryOutcomePresentation(outcome)
  const failed = presentation.variant === 'negative'
  return {
    icon: failed ? 'alert' : 'info',
    tone: failed ? 'text-content-negative-soft' : 'text-content-warning-soft',
    detail: outcome.message || 'No comparison measurement was produced.',
  }
}

function CompareRunRailRow({
  outcome,
  durationSeconds,
  selected,
  onSelect,
}: {
  outcome: CompareQueryOutcome
  durationSeconds: number
  selected: boolean
  onSelect: () => void
}) {
  const running = outcome.status === 'running'
  const state = compareRailRowState(outcome, durationSeconds)
  const presentation = compareQueryOutcomePresentation(outcome)
  const percent = running
    ? Math.min(
        100,
        Math.round(
          outcome.percent ?? (outcome.elapsedSeconds / durationSeconds) * 100
        )
      )
    : 0

  return (
    <InteractiveRow
      label={`Show ${outcome.label} in the comparison chart`}
      active={selected}
      aria-current={selected ? 'true' : undefined}
      onClick={onSelect}
      className={cn(
        'border-b border-border-layout-soft px-4 py-3 last:border-0',
        'hover:bg-surface-layout-2/50',
        // The measuring query is the one the reader is waiting on, so it is
        // raised above the rows that are already settled or still waiting.
        running && 'bg-surface-layout-2',
        selected && 'border-l-2 border-l-border-primary-soft pl-3.5'
      )}
    >
      <HStack className="items-start gap-3">
        <div className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center">
          {state.icon ? (
            <Icon
              size="small"
              name={state.icon}
              label=""
              className={state.tone}
            />
          ) : (
            <Spinner size="base" color="primary-soft" />
          )}
        </div>
        <VStack className="min-w-0 flex-1 items-stretch gap-1">
          <HStack className="items-center justify-between gap-2">
            <Text
              level="label-small"
              className={cn(
                'min-w-0 truncate',
                running || selected
                  ? 'text-content-layout-1'
                  : 'text-content-layout-2'
              )}
            >
              {outcome.label || 'Query'}
            </Text>
            {selected && (
              <Tag
                size="small"
                variant="primary"
                modifier="ghost"
                label="Showing"
              />
            )}
          </HStack>
          <Text level="caption" className={cn('line-clamp-2', state.tone)}>
            {state.detail}
          </Text>
          {running && (
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
              <Text
                level="mono-small"
                className="text-content-layout-2 tabular-nums"
              >
                {percent}%
              </Text>
            </HStack>
          )}
          {outcome.status !== 'running' && outcome.status !== 'queued' && (
            <HStack>
              <Tag
                size="small"
                variant={presentation.variant}
                modifier="ghost"
                label={presentation.label}
              />
            </HStack>
          )}
        </VStack>
      </HStack>
    </InteractiveRow>
  )
}

/**
 * One row per query in the batch, present from the moment the run starts and
 * kept after it finishes. It answers "what is happening, to which query, and
 * how much is left" without opening the jobs sidebar, and selects which
 * query the comparison chart draws.
 */
export function CompareRunRail({
  controller,
}: {
  controller: CompareController
}) {
  const snapshot = controller.snapshot
  if (!snapshot || snapshot.queryOutcomes.length === 0) return null
  const durationSeconds =
    controller.batch?.durationSeconds ?? DEFAULT_COMPARE_DURATION
  const remaining = compareBatchRemainingSeconds(snapshot, durationSeconds)

  return (
    <Card>
      <Card.Header className="items-start gap-2">
        <Card.Title>Queries in this comparison</Card.Title>
        <Card.Description>
          {compareBatchProgressLine(snapshot)}
          {remaining > 0 ? ` · ~${formatSecondsShort(remaining)} left` : ''}
        </Card.Description>
      </Card.Header>
      <Card.Content className="p-0">
        {snapshot.queryOutcomes.map((outcome) => (
          <CompareRunRailRow
            key={outcome.runId ?? outcome.cacheId}
            outcome={outcome}
            durationSeconds={durationSeconds}
            selected={controller.selectedOutcome?.cacheId === outcome.cacheId}
            onSelect={() => controller.pinQuery(outcome.cacheId)}
          />
        ))}
      </Card.Content>
    </Card>
  )
}
