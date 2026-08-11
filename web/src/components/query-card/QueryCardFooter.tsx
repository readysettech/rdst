import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { HStack } from '@rs/ui-new/stack'
import type { ReactNode } from 'react'

interface QueryCardFooterProps {
  meta?: ReactNode
  secondaryActions?: ReactNode
  primaryAction?: ReactNode
  detailsOpen: boolean
  onToggleDetails?: () => void
}

export function QueryCardFooter({
  meta,
  secondaryActions,
  primaryAction,
  detailsOpen,
  onToggleDetails,
}: QueryCardFooterProps) {
  if (!meta && !secondaryActions && !primaryAction && !onToggleDetails) {
    return null
  }

  return (
    <Card.Content
      className="flex items-center justify-between gap-3 flex-wrap px-4 py-3"
      data-testid="query-card-footer-content"
    >
      {meta ? (
        <div className="min-w-0 flex-1 rounded px-3 py-2 text-mono-small font-mono text-content-layout-3 wrap-break-word">
          {meta}
        </div>
      ) : null}
      <HStack className="ml-auto gap-2 items-center shrink-0">
        {onToggleDetails ? (
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Details"
            aria-expanded={detailsOpen}
            onClick={onToggleDetails}
          />
        ) : null}
        {secondaryActions}
        {primaryAction}
      </HStack>
    </Card.Content>
  )
}
