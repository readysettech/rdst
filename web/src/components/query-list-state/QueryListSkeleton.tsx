import { QueryCardSkeleton } from '../query-card/QueryCardSkeleton'

export function QueryListSkeleton({ count = 3 }: { count?: number }) {
  return (
    <div className="space-y-3" data-testid="query-list-skeleton">
      <span className="sr-only">Loading queries</span>
      {Array.from({ length: count }, (_, index) => (
        <QueryCardSkeleton key={index} />
      ))}
    </div>
  )
}
