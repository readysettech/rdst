import type { IconStrokeName } from '@rs/ui-new/icon'
import { IconTile } from '@rs/ui-new/icon-tile'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'

export function ResultFact({
  icon,
  label,
  value,
}: {
  icon: IconStrokeName
  label: string
  value: string
}) {
  return (
    <HStack className="min-w-0 items-center gap-3">
      <IconTile icon={icon} size="base" accent="primary" />
      <VStack className="min-w-0 items-start gap-0.5">
        <Text level="caption" className="text-content-layout-3">
          {label}
        </Text>
        <Text
          level="mono-medium"
          className="wrap-break-word text-content-layout-1"
        >
          {value}
        </Text>
      </VStack>
    </HStack>
  )
}
