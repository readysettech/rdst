import { cn } from '@rs/tailwind-base'
import { Card } from '@rs/ui-new/card-2'
import { Skeleton } from '@rs/ui-new/skeleton'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'

export function compareErrorDetail(error: unknown) {
  if (error instanceof Error) return error.message
  if (typeof error === 'string') return error
  return error ? String(error) : undefined
}

export function CompareSkeleton() {
  return (
    <Card aria-label="Loading comparison">
      <Card.Header>
        <Skeleton className="h-5 w-44" />
        <Skeleton className="h-4 w-80 max-w-full" />
      </Card.Header>
      <Card.Content>
        <div className="grid gap-6 tablet:grid-cols-3">
          <VStack className="items-stretch gap-3 tablet:col-span-2">
            <Skeleton className="h-16 w-full rounded-xl" />
            <Skeleton className="h-16 w-full rounded-xl" />
            <Skeleton className="h-16 w-full rounded-xl" />
          </VStack>
          <Skeleton className="h-60 w-full rounded-xl" />
        </div>
      </Card.Content>
      <Card.Footer>
        <Skeleton className="h-9 w-36 rounded-lg" />
      </Card.Footer>
    </Card>
  )
}

export function CompareSummaryRow({
  label,
  value,
  tone = 'neutral',
}: {
  label: string
  value: string
  tone?: 'neutral' | 'positive' | 'warning'
}) {
  return (
    <HStack className="items-start justify-between gap-4 border-b border-border-layout-soft py-3 last:border-0">
      <Text level="caption" className="text-content-layout-3">
        {label}
      </Text>
      <Text
        level="label-small"
        className={cn(
          'text-right',
          tone === 'positive'
            ? 'text-content-positive-soft'
            : tone === 'warning'
              ? 'text-content-warning-soft'
              : 'text-content-layout-1'
        )}
      >
        {value}
      </Text>
    </HStack>
  )
}
