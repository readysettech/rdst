import { Icon } from '@rs/ui-new/icon'
import { HStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'

/**
 * One measured fact, read left to right: what it is, then what it measures.
 * The shared row keeps a query's evidence looking the same on its card and in
 * its Overview.
 */
export function QueryMetricRow({
  icon,
  label,
  value,
}: {
  icon: 'database' | 'observe' | 'speedometer'
  label: string
  value: string
}) {
  return (
    <HStack className="min-w-0 items-center gap-2">
      <Icon
        name={icon}
        label=""
        aria-hidden="true"
        className="h-4 w-4 shrink-0 text-content-layout-3"
      />
      <Text level="caption" className="truncate text-content-layout-3">
        {label}
      </Text>
      <Text
        level="mono-small"
        className="ml-auto shrink-0 text-content-layout-1"
      >
        {value}
      </Text>
    </HStack>
  )
}
