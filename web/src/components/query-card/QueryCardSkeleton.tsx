import { Card } from '@rs/ui-new/card-2'
import { Skeleton } from '@rs/ui-new/skeleton'
import { HStack } from '@rs/ui-new/stack'

/**
 * Loading counterpart of the canonical QueryCard. Its two content bands and
 * internal rhythm match the real header/SQL/footer anatomy to avoid layout
 * shifts when query data arrives.
 */
export function QueryCardSkeleton() {
  return (
    <Card aria-hidden="true" data-testid="query-card-skeleton">
      <Card.Content className="p-0 overflow-hidden">
        <HStack className="justify-between items-center gap-3 px-4 min-h-[54px] py-2 border-b-(length:--border-base) border-b-border-layout-1">
          <HStack className="gap-3 items-center min-w-0 flex-1">
            <Skeleton className="w-5 h-4 shrink-0" />
            <Skeleton className="w-36 h-5" />
            <Skeleton className="w-16 h-5 rounded-full" />
          </HStack>
          <Skeleton className="w-8 h-8 rounded-lg shrink-0" />
        </HStack>
        <div className="px-4 py-4 pr-14 space-y-3">
          <Skeleton className="w-3/4 h-4" />
          <Skeleton className="w-11/12 h-4" />
          <Skeleton className="w-2/3 h-4" />
        </div>
      </Card.Content>
      <Card.Content className="flex items-center justify-between gap-3 px-4 py-3">
        <Skeleton className="w-1/2 h-8" />
        <Skeleton className="w-24 h-8 rounded-lg" />
      </Card.Content>
    </Card>
  )
}
