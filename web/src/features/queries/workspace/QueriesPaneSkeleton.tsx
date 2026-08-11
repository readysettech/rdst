import { Card } from '@rs/ui-new/card-2'
import { Skeleton } from '@rs/ui-new/skeleton'
import { QueryListSkeleton } from '../../../components/query-list-state'

export function QueriesPaneSkeleton() {
  return (
    <output aria-label="Loading query view" className="block space-y-6">
      <span className="sr-only">Loading query view</span>
      <Card aria-hidden="true">
        <Card.Content className="p-4 space-y-4">
          <div className="flex items-center justify-between gap-4">
            <Skeleton className="h-8 w-48" />
            <Skeleton className="h-9 w-32 rounded-lg" />
          </div>
          <Skeleton className="h-32 w-full" />
        </Card.Content>
      </Card>
      <QueryListSkeleton count={2} />
    </output>
  )
}
