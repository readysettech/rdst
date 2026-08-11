import { Show } from '@rs/ui-new/show'
import { HStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'

interface SlowQueryContextProps {
  targetLabel: string
  sourceLabel: string
  engineLabel?: string
}

function formatEngineLabel(engine?: string) {
  if (!engine) return undefined
  if (engine.toLowerCase().includes('postgres')) return 'PostgreSQL'
  if (engine.toLowerCase().includes('mysql')) return 'MySQL'
  return engine
}

export function SlowQueryContext({
  targetLabel,
  sourceLabel,
  engineLabel,
}: SlowQueryContextProps) {
  const formattedEngineLabel = formatEngineLabel(engineLabel)

  return (
    <HStack
      className="gap-x-3 gap-y-1 items-center flex-wrap"
      data-testid="slow-query-context"
    >
      <HStack className="gap-1.5 items-center">
        <Text level="caption" className="text-content-layout-3">
          Target
        </Text>
        <Text level="label-small" className="text-content-layout-2">
          {targetLabel}
        </Text>
      </HStack>

      <Text level="caption" className="text-content-layout-3">
        ·
      </Text>

      <HStack className="gap-1.5 items-center">
        <Text level="caption" className="text-content-layout-3">
          Source
        </Text>
        <Text level="label-small" className="text-content-layout-2">
          {sourceLabel}
        </Text>
      </HStack>

      <Show when={!!formattedEngineLabel}>
        <Text level="caption" className="text-content-layout-3">
          ·
        </Text>
        <HStack className="gap-1.5 items-center">
          <Text level="caption" className="text-content-layout-3">
            Engine
          </Text>
          <Text level="label-small" className="text-content-layout-2">
            {formattedEngineLabel}
          </Text>
        </HStack>
      </Show>
    </HStack>
  )
}
