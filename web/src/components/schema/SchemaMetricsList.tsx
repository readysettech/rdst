import { Text } from '@rs/ui-new/text'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Show } from '@rs/ui-new/show'
import { m } from '@rs/ui-new/motion'
import type { SchemaMetric } from '../../types/schema'

interface SchemaMetricsListProps {
  metrics: SchemaMetric[]
  onEdit?: (metric: SchemaMetric) => void
}

export function SchemaMetricsList({ metrics, onEdit }: SchemaMetricsListProps) {
  if (metrics.length === 0) {
    return (
      <div className="px-6 py-12">
        <VStack className="gap-3 items-center">
          <div className="w-12 h-12 rounded-xl bg-surface-layout-2 flex items-center justify-center">
            <Icon name="speedometer" label="No metrics" className="w-6 h-6 text-content-layout-3" />
          </div>
          <VStack className="gap-1 items-center">
            <Text level="body-medium" className="text-content-layout-3">
              No metrics defined
            </Text>
            <Text level="caption" className="text-content-layout-3">
              Add metrics to define reusable calculations for your data
            </Text>
          </VStack>
        </VStack>
      </div>
    )
  }

  return (
    <div className="divide-y divide-border-layout-1">
      {metrics.map((metric, index) => (
        <m.div
          key={metric.name}
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.2, delay: index * 0.03 }}
          className="px-5 py-4 hover:bg-surface-layout-2/30 transition-colors"
        >
          <div className="flex items-start gap-4">
            <div className="w-10 h-10 rounded-lg bg-surface-positive-soft/20 flex items-center justify-center shrink-0">
              <Icon name="speedometer" label="Metric" className="w-5 h-5 text-content-positive-soft" />
            </div>
            <div className="flex-1 min-w-0">
              <Text level="label-medium" className="text-content-layout-1">
                {metric.name}
              </Text>
              <Text level="body-small" className="text-content-layout-2 mt-1">
                {metric.definition}
              </Text>
              <div className="mt-3">
                <HStack className="gap-2 items-center mb-2">
                  <Icon name="folder-file" label="SQL" className="w-3.5 h-3.5 text-content-layout-3" />
                  <Text level="caption" className="text-content-layout-3">
                    SQL Expression
                  </Text>
                </HStack>
                <code className="block text-xs bg-surface-layout-2 px-3 py-2 rounded-lg font-mono text-content-layout-1 overflow-x-auto">
                  {metric.sql}
                </code>
              </div>
            </div>
            <Show when={!!onEdit}>
              <Button
                modifier="ghost"
                size="small"
                icon="edit"
                iconPosition="icon"
                label="Edit"
                onClick={() => onEdit?.(metric)}
              />
            </Show>
          </div>
        </m.div>
      ))}
    </div>
  )
}
