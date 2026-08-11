import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { EmptyState, type EmptyStateAction } from '@rs/ui-new/empty-state'
import type { ReactNode } from 'react'
import { QueryListSkeleton } from './QueryListSkeleton'

interface QueryListEmptyStateProps {
  icon: IconStrokeName
  title: string
  body: ReactNode
  action?: EmptyStateAction
  secondaryAction?: EmptyStateAction
  placeholderCount?: number
}

/**
 * Query-list empty state with the same content silhouette as loading. The
 * frozen skeleton preserves context while the gradient and EmptyState explain
 * why there are no rows and, when useful, provide one recovery action.
 */
export function QueryListEmptyState({
  icon,
  title,
  body,
  action,
  secondaryAction,
  placeholderCount = 2,
}: QueryListEmptyStateProps) {
  return (
    <div className="relative overflow-hidden rounded-[1.25rem]">
      <div aria-hidden="true" className="opacity-45 [&_#skeleton]:animate-none">
        <QueryListSkeleton count={placeholderCount} />
      </div>
      <div
        className="absolute inset-0 flex items-start justify-center"
        style={{
          backgroundImage:
            'linear-gradient(to top, var(--color-surface-layout-2) 0%, color-mix(in srgb, var(--color-surface-layout-2) 84%, transparent) 58%, color-mix(in srgb, var(--color-surface-layout-2) 30%, transparent) 100%)',
        }}
      >
        <EmptyState
          icon={icon}
          title={title}
          body={body}
          action={action}
          secondaryAction={secondaryAction}
        />
      </div>
    </div>
  )
}
