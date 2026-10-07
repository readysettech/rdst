// The kinds of change as a fixed row of cards: Indexes, Server settings, Maintenance, Query
// rewrites (Caching only when it has changes). Each always shows where it stands, so a new
// change lands on a card that's already there: its count rolls up and rings once.
import { cn } from '@rs/tailwind-base'
import { m } from '@rs/ui-new/motion'
import { Pressable } from '@rs/ui-new/pressable'
import { useEffect, useRef, useState } from 'react'
import { RollingNumber } from '@/features/monitor-kit/RollingNumber'
import type { Category, CategoryStatus } from './actionsModel'

export interface StripCard {
  kind: Category
  label: string
  status: CategoryStatus
}

export function CategoryStrip({
  cards,
  active,
  onSelect,
}: {
  cards: StripCard[]
  active: Category
  onSelect: (kind: Category) => void
}) {
  return (
    <div
      role="tablist"
      aria-label="Kinds of change"
      data-testid="action-sections"
      className={cn(
        'grid grid-cols-2 gap-3',
        cards.length > 4 ? 'laptop:grid-cols-5' : 'laptop:grid-cols-4'
      )}
    >
      {cards.map((card) => (
        <Card
          key={card.kind}
          card={card}
          selected={card.kind === active}
          onSelect={() => onSelect(card.kind)}
        />
      ))}
    </div>
  )
}

function Card({
  card,
  selected,
  onSelect,
}: {
  card: StripCard
  selected: boolean
  onSelect: () => void
}) {
  const { state, count, line } = card.status
  const busy = state === 'checking' || state === 'waiting'
  const ring = useRiseRing(count)
  return (
    <Pressable
      role="tab"
      aria-selected={selected}
      onClick={onSelect}
      data-testid="action-section"
      data-state={state}
      className={cn(
        'relative flex min-w-0 flex-col gap-1.5 rounded-panel border px-4 pb-4 pt-3.5 text-left transition-colors duration-fast',
        selected
          ? 'border-border-layout-2 bg-surface-layout-2'
          : 'border-border-layout-1 bg-surface-layout-1 hover:bg-surface-layout-2/50'
      )}
    >
      <span className="flex items-center justify-between gap-3">
        <span
          className={cn(
            'truncate text-label-large',
            selected || state === 'found'
              ? 'text-content-layout-1'
              : 'text-content-layout-2'
          )}
        >
          {card.label}
        </span>
        {busy ? (
          <span
            aria-hidden="true"
            className="h-1.5 w-1.5 shrink-0 rounded-full bg-content-rising-plain mk-breathe"
          />
        ) : (
          <span
            className={cn(
              'relative shrink-0 text-label-large tabular-nums',
              count ? 'text-content-layout-1' : 'text-content-layout-3'
            )}
          >
            <RollingNumber value={String(count)} />
          </span>
        )}
      </span>
      <span
        className={cn(
          'truncate text-body-small',
          state === 'found' ? 'text-content-layout-2' : 'text-content-layout-3'
        )}
      >
        {line}
      </span>
      {card.status.sub ? (
        <span className="-mt-1 text-body-small text-content-layout-3">
          {card.status.sub}
        </span>
      ) : null}
      {ring ? (
        <m.span
          key={ring}
          aria-hidden="true"
          initial={{ opacity: 0.8 }}
          animate={{ opacity: 0 }}
          transition={{ duration: 1.2, ease: 'easeOut' }}
          className="pointer-events-none absolute inset-0 rounded-panel bg-surface-rising-soft"
        />
      ) : null}
      {selected ? (
        <m.span
          layoutId="action-category-bar"
          aria-hidden="true"
          className="absolute inset-x-4 bottom-0 h-0.5 rounded-full bg-content-layout-1"
        />
      ) : null}
    </Pressable>
  )
}

/** A key that changes each time the count goes up after the first render, for one ring. */
function useRiseRing(count: number): number {
  const last = useRef(count)
  const [ring, setRing] = useState(0)
  useEffect(() => {
    if (count > last.current) setRing((value) => value + 1)
    last.current = count
  }, [count])
  return ring
}
