/**
 * Canonical query-card container.
 *
 * The container owns composition and interaction only. Header, SQL body,
 * footer, and selection presentation live in focused child components under
 * `query-card/`, so every query surface shares one anatomy without moving
 * page-specific behavior into the card.
 */

import { cn } from '@rs/tailwind-base'
import { Card } from '@rs/ui-new/card-2'
import type { KeyboardEvent as ReactKeyboardEvent } from 'react'
import { QueryCardFooter } from './query-card/QueryCardFooter'
import { QueryCardHeader } from './query-card/QueryCardHeader'
import { QueryCardSelectionIndicator } from './query-card/QueryCardSelectionIndicator'
import { QueryCardSql } from './query-card/QueryCardSql'
import type { QueryCardProps } from './query-card/types'

export type { QueryCardProps } from './query-card/types'

export function QueryCard({
  sql,
  dialect,
  sqlInitiallyExpanded,
  truncateOneLine = false,
  leading,
  title,
  badges,
  menu,
  meta,
  secondaryActions,
  primaryAction,
  detailsOpen = false,
  onToggleDetails,
  selectable = false,
  selected = false,
  selectionDisabled = false,
  onSelect,
  selectionLabel,
  editor,
  expansion,
  className,
  highlighted = false,
  'data-testid': dataTestid,
  'data-query-hash': dataQueryHash,
  'data-cache-id': dataCacheId,
}: QueryCardProps) {
  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (selectionDisabled) return
    if (event.target !== event.currentTarget) return
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onSelect?.()
    }
  }

  const selectableProps = selectable
    ? {
        role: 'button',
        tabIndex: selectionDisabled ? -1 : 0,
        'aria-label': selectionLabel,
        'aria-pressed': selected,
        'aria-disabled': selectionDisabled || undefined,
        onClick: selectionDisabled ? undefined : onSelect,
        onKeyDown: handleKeyDown,
      }
    : {}

  const dataAttrs = {
    'data-testid': dataTestid,
    'data-query-hash': dataQueryHash,
    'data-cache-id': dataCacheId,
  }

  return (
    <Card
      {...dataAttrs}
      {...selectableProps}
      data-highlighted={highlighted ? 'true' : undefined}
      className={cn(
        'transition-[box-shadow] duration-500',
        selectable &&
          !selectionDisabled &&
          'cursor-pointer transition-[box-shadow,transform] focus-visible:outline-none focus-visible:shadow-focus hover:shadow-elevation-2',
        selectable && selectionDisabled && 'cursor-not-allowed opacity-50',
        selectable &&
          !selectionDisabled &&
          (selected
            ? 'ring-2 ring-border-primary-soft shadow-elevation-2'
            : 'hover:bg-surface-raised'),
        highlighted &&
          'scroll-mt-28 bg-surface-primary-soft ring-2 ring-border-primary-soft ring-offset-2 ring-offset-surface-layout-1 shadow-elevation-2',
        className
      )}
    >
      <Card.Content
        className="p-0 overflow-hidden"
        data-testid="query-card-main-content"
      >
        <QueryCardHeader
          leading={
            selectable ? (
              <QueryCardSelectionIndicator selected={selected} />
            ) : (
              leading
            )
          }
          title={title}
          badges={badges}
          menu={menu}
        />
        {editor ? (
          <div className="bg-surface-layout-1/50 p-4">{editor}</div>
        ) : (
          <QueryCardSql
            sql={sql}
            dialect={dialect}
            initiallyExpanded={sqlInitiallyExpanded}
            truncateOneLine={truncateOneLine}
            expandable={!selectable}
            copyable={!selectable}
          />
        )}
      </Card.Content>

      <QueryCardFooter
        meta={meta}
        secondaryActions={secondaryActions}
        primaryAction={primaryAction}
        detailsOpen={detailsOpen}
        onToggleDetails={onToggleDetails}
      />

      {expansion ? (
        <Card.Content className="px-4 py-3">{expansion}</Card.Content>
      ) : null}
    </Card>
  )
}
