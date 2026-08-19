import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { Progress } from '@rs/ui-new/progress'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { cancelActiveAudit } from '../../lib/auditSession'
import { formatSecondsClock } from '../../lib/formatters'
import { ActivityPulse } from './ActivityPulse'

function activityForPhase(phase: string | undefined): string {
  if (!phase || phase === 'config' || phase === 'connect') {
    return 'Connecting to the database'
  }
  if (['snapshot_start', 'snapshot', 'collect', 'metrics'].includes(phase)) {
    return 'Collecting database metrics'
  }
  if (phase === 'capture' || phase === 'snapshot_end') {
    return 'Capturing live database activity'
  }
  if (phase === 'analysis') return 'Analyzing captured workload'
  if (phase === 'readyset') return 'Benchmarking against Readyset'
  if (phase === 'storage' || phase === 'save') return 'Saving health check'
  if (phase === 'insights') return 'Generating combined insights'
  return 'Running health check'
}

export function RunProgress({
  phase,
  statusMessage,
  durationSeconds,
  elapsedSeconds,
  totalElapsedSeconds,
}: {
  phase: string | undefined
  statusMessage: string | undefined
  durationSeconds: number
  elapsedSeconds: number
  // Wall-clock seconds since the run started, unclamped. Keeps the clock
  // moving through the collection and analysis phases so a long run never
  // looks hung after the capture window ends.
  totalElapsedSeconds?: number
}) {
  const captureActive = phase === 'capture' || phase === 'snapshot_end'
  const hasCaptureDuration = captureActive && durationSeconds > 0
  const percent = hasCaptureDuration
    ? Math.min(100, (elapsedSeconds / durationSeconds) * 100)
    : undefined
  const runningClock =
    !hasCaptureDuration && totalElapsedSeconds !== undefined && totalElapsedSeconds >= 0
      ? `${formatSecondsClock(totalElapsedSeconds)} elapsed`
      : ''
  const label = activityForPhase(phase)
  const detail =
    phase === 'readyset'
      ? 'Running the Readyset comparison…'
      : statusMessage || 'Running health check…'

  return (
    <Card className="w-full">
      <Card.Content>
        <VStack className="gap-4 items-stretch p-1">
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1 items-center">
            <HStack className="gap-2 items-center min-w-0">
              <ActivityPulse label={`${label} in progress`} />
              <Text level="label-small" className="text-content-layout-1">
                {label}
              </Text>
            </HStack>
            <Text
              level="caption"
              className="text-content-layout-3 tabular-nums text-right"
            >
              {hasCaptureDuration
                ? `${formatSecondsClock(elapsedSeconds)} / ${formatSecondsClock(durationSeconds)}`
                : runningClock}
            </Text>
            <div
              className="col-start-1 min-w-0"
              title={statusMessage}
            >
              <Text
                level="caption"
                className="text-content-layout-3 truncate"
              >
                {detail}
              </Text>
            </div>
            <Button
              className="row-span-2 col-start-2 row-start-1"
              variant="negative"
              modifier="outline"
              size="small"
              label="Cancel"
              icon="close"
              iconPosition="left"
              onClick={cancelActiveAudit}
            />
          </div>
          <Show when={percent !== undefined}>
            <Progress value={percent ?? 0} max={100} />
          </Show>
        </VStack>
      </Card.Content>
    </Card>
  )
}
