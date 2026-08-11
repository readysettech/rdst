import { HStack } from '@rs/ui-new/stack'
import type { ReactNode } from 'react'

interface QueryCardHeaderProps {
  leading?: ReactNode
  title?: ReactNode
  badges?: ReactNode
  menu?: ReactNode
}

export function QueryCardHeader({
  leading,
  title,
  badges,
  menu,
}: QueryCardHeaderProps) {
  if (!leading && !title && !badges && !menu) return null

  return (
    <HStack
      className="justify-between items-center gap-3 px-4 min-h-[54px] py-2 border-b-(length:--border-base) border-b-border-layout-1"
      data-testid="query-card-header"
    >
      <HStack className="gap-3 items-center min-w-0 flex-1">
        {leading}
        <HStack className="gap-2 items-center flex-wrap min-w-0">
          {title}
          {badges}
        </HStack>
      </HStack>
      {menu ? <div className="shrink-0">{menu}</div> : null}
    </HStack>
  )
}
