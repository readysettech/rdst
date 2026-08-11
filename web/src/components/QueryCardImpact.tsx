import { cn } from '@rs/tailwind-base'
import { Card } from '@rs/ui-new/card-2'
import { m } from '@rs/ui-new/motion'
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react'
import type { QueryCardProps } from './QueryCard'
import { QueryCardFooter } from './query-card/QueryCardFooter'
import { QueryCardHeader } from './query-card/QueryCardHeader'
import { QueryCardSelectionIndicator } from './query-card/QueryCardSelectionIndicator'
import { QueryCardSql } from './query-card/QueryCardSql'

export type QueryCardImpactProps = QueryCardProps & {
  rail: ReactNode
  railSize?: 'base' | 'wide'
  expansionPlacement?: 'before-footer' | 'after-footer'
  motionLayout?: boolean
}

function LayoutRegion({
  children,
  enabled,
  name,
}: {
  children: ReactNode
  enabled: boolean
  name: 'content' | 'footer' | 'expansion'
}) {
  if (!enabled) return children

  return (
    <m.div
      layout="position"
      className="min-w-0"
      data-query-layout-region={name}
    >
      {children}
    </m.div>
  )
}

/**
 * Impact-led alternative to the canonical QueryCard.
 *
 * It keeps the same interaction contract while reserving a stable rail for
 * workload evidence. QueryCard remains the default presentation.
 */
export function QueryCardImpact({
  sql,
  dialect,
  sqlInitiallyExpanded,
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
  onSelect,
  selectionLabel,
  editor,
  expansion,
  rail,
  railSize = 'base',
  expansionPlacement = 'after-footer',
  motionLayout = false,
  className,
  highlighted = false,
  'data-testid': dataTestid,
  'data-query-hash': dataQueryHash,
  'data-cache-id': dataCacheId,
}: QueryCardImpactProps) {
  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onSelect?.()
    }
  }

  const selectableProps = selectable
    ? {
        role: 'button',
        tabIndex: 0,
        'aria-label': selectionLabel,
        'aria-pressed': selected,
        onClick: onSelect,
        onKeyDown: handleKeyDown,
      }
    : {}

  const expansionContent = expansion ? (
    <Card.Content className="px-4 py-3">{expansion}</Card.Content>
  ) : null

  return (
    <Card
      {...selectableProps}
      data-testid={dataTestid}
      data-query-hash={dataQueryHash}
      data-cache-id={dataCacheId}
      data-highlighted={highlighted ? 'true' : undefined}
      className={cn(
        'overflow-hidden transition-[box-shadow] duration-500',
        selectable &&
          'cursor-pointer transition-[box-shadow,transform] focus-visible:outline-none focus-visible:shadow-focus hover:shadow-elevation-2',
        selectable &&
          (selected
            ? 'ring-2 ring-border-primary-soft shadow-elevation-2'
            : 'hover:bg-surface-raised'),
        highlighted &&
          'scroll-mt-28 bg-surface-primary-soft ring-2 ring-border-primary-soft ring-offset-2 ring-offset-surface-layout-1 shadow-elevation-2',
        className
      )}
    >
      <LayoutRegion enabled={motionLayout} name="content">
        <Card.Content
          className="overflow-hidden rounded-none border-0 bg-transparent p-0"
          data-testid="query-card-main-content"
        >
          <div
            className={cn(
              'grid',
              railSize === 'wide'
                ? 'laptop:grid-cols-[24rem_minmax(0,1fr)]'
                : 'laptop:grid-cols-[16rem_minmax(0,1fr)]'
            )}
          >
            <div className="border-b border-border-layout-1 bg-surface-layout-2/30 p-5 laptop:border-r laptop:border-b-0">
              {rail}
            </div>

            <div className="min-w-0">
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
                  expandable={!selectable}
                  copyable={!selectable}
                />
              )}
            </div>
          </div>
        </Card.Content>
      </LayoutRegion>

      {expansion && expansionPlacement === 'before-footer' ? (
        <LayoutRegion enabled={motionLayout} name="expansion">
          {expansionContent}
        </LayoutRegion>
      ) : null}

      <LayoutRegion enabled={motionLayout} name="footer">
        <QueryCardFooter
          meta={meta}
          secondaryActions={secondaryActions}
          primaryAction={primaryAction}
          detailsOpen={detailsOpen}
          onToggleDetails={onToggleDetails}
        />
      </LayoutRegion>

      {expansion && expansionPlacement === 'after-footer' ? (
        <LayoutRegion enabled={motionLayout} name="expansion">
          {expansionContent}
        </LayoutRegion>
      ) : null}
    </Card>
  )
}
