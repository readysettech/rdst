import { formatDuration } from '@rs/ui-new/format'
import { Show } from '@rs/ui-new/show'
import { HStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import type { TopSourceFallback, TopState } from '../../../types/top'

interface SlowQueryRunStatusProps {
  state: TopState
  runtimeSeconds: number
  totalTracked: number
  newlySaved: number
  isRealtime: boolean
  sourceFallback: TopSourceFallback | null
}

function getStatusVariant(
  state: TopState
): 'positive' | 'warning' | 'negative' | 'informative' {
  switch (state) {
    case 'streaming':
      return 'positive'
    case 'loading':
      return 'warning'
    case 'error':
      return 'negative'
    default:
      return 'informative'
  }
}

function getStatusLabel(state: TopState): string {
  switch (state) {
    case 'idle':
      return 'Ready'
    case 'loading':
      return 'Loading'
    case 'streaming':
      return 'Live'
    case 'complete':
      return 'Complete'
    case 'error':
      return 'Error'
    default:
      return state
  }
}

export function SlowQueryRunStatus({
  state,
  runtimeSeconds,
  totalTracked,
  newlySaved,
  isRealtime,
  sourceFallback,
}: SlowQueryRunStatusProps) {
  const isStreaming = isRealtime && state === 'streaming'

  return (
    <HStack className="gap-x-3 gap-y-1.5 items-center flex-wrap">
      <HStack className="gap-1.5 items-center">
        <Tag
          size="base"
          variant={getStatusVariant(state)}
          modifier={state === 'streaming' ? 'solid' : 'ghost'}
          label={`Status: ${getStatusLabel(state)}`}
        />
      </HStack>

      <Show when={isStreaming}>
        <HStack className="gap-1.5 items-center">
          <div className="w-2 h-2 rounded-full bg-content-positive-plain animate-pulse" />
          <Text
            level="label-small"
            className="text-content-positive-plain font-medium"
          >
            {formatDuration({ seconds: runtimeSeconds })}
          </Text>
        </HStack>
        <HStack className="gap-1.5 items-center">
          <Text level="caption" className="text-content-layout-3">
            Tracked
          </Text>
          <Text level="label-small" className="text-content-layout-2">
            {totalTracked}
          </Text>
        </HStack>
      </Show>

      <Show when={newlySaved > 0}>
        <Tag
          size="small"
          variant="positive"
          modifier="ghost"
          label={`${newlySaved} saved`}
        />
      </Show>

      <Show when={sourceFallback !== null}>
        <Tag
          size="small"
          variant="warning"
          modifier="ghost"
          label={`Using ${sourceFallback?.to_source} instead of ${sourceFallback?.from_source}`}
        />
      </Show>
    </HStack>
  )
}
