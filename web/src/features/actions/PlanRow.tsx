// One change on the plan: rank, verb, what it acts on, what it does, the time it saves and its
// SQL to copy. It opens in the side pane; beside the pane the row keeps its rank, name and
// saving. Kept indexes read on the same grid with no rank, button or saving.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { ListRow } from '@rs/ui-new/divided-list'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Text } from '@rs/ui-new/text'
import { type MouseEvent, useEffect, useId, useRef, useState } from 'react'
import type { KeptIndex, PlanCard, PlanTier } from '@/lib/monitor/actions'
import {
  caption,
  keptRow,
  planName,
  saves,
  type VerbTone,
  whatItDoes,
} from './actionsModel'
import { useCopy } from './parts'
import { Reveal } from './Reveal'

/**
 * rank, verb, change, what it does, saves, Copy SQL, chevron; narrower screens drop what it
 * does.
 */
export const PLAN_COLUMNS = 'var(--plan-columns)'
export const PLAN_GRID =
  '[--plan-columns:40px_60px_minmax(0,1fr)_110px_110px_16px] desktop:[--plan-columns:40px_60px_minmax(0,1.6fr)_minmax(0,1fr)_110px_110px_16px]'
/** Beside the open pane: rank, verb, change and saves. */
export const PLAN_GRID_COMPACT =
  '[--plan-columns:40px_60px_minmax(0,1fr)_110px]'
/** The row that opens the rest of the kept indexes: its line spans to the chevron. */
const MORE_COLUMNS = '40px 60px minmax(0,1fr) 16px'

const verbTone: Record<VerbTone, string> = {
  positive: 'text-content-positive-soft',
  negative: 'text-content-negative-soft',
  neutral: 'text-content-layout-2',
}

/** The rank mark carries the priority: tinted on Do first, neutral on Then, hairline on Later. */
export const rankMark = (tier: PlanTier) =>
  tier === 'first'
    ? 'bg-surface-info-soft text-content-info-soft'
    : tier === 'later'
      ? 'border border-border-layout-2 text-content-layout-3'
      : 'bg-surface-primary-soft text-content-layout-2'

const savesSize = (tier: PlanTier) =>
  tier === 'first'
    ? 'text-headline-3 text-content-layout-1'
    : tier === 'later'
      ? 'text-body-small text-content-layout-3'
      : 'text-body-medium text-content-layout-1'

export function PlanRow({
  card,
  primary,
  lead = false,
  selected = false,
  compact = false,
  withKind = true,
  onOpen,
}: {
  card: PlanCard
  /** The view's one cream button: the first change's Copy SQL. */
  primary: boolean
  /** The first Do first change: its saving reads one size larger. */
  lead?: boolean
  /** The change open in the pane: raised, with the info border, and scrolled into view. */
  selected?: boolean
  /** Beside the open pane: rank, name and saving only, so the pane holds the actions. */
  compact?: boolean
  /** The kind word opens the line under the name, unless the group's title says it. */
  withKind?: boolean
  onOpen: (id: number) => void
}) {
  const id = useId()
  const named = planName(card)
  const rowRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (selected) rowRef.current?.scrollIntoView?.({ block: 'nearest' })
  }, [selected])
  const tier = card.priority ?? 'later'
  const [copied, copy] = useCopy()
  const what = whatItDoes(card)
  // The whole row opens it; its own buttons and links keep their clicks.
  const onRow = (event: MouseEvent<HTMLElement>) => {
    const target = event.target as HTMLElement
    // Clicks from a menu or popover portal bubble here too, from outside the row.
    if (!event.currentTarget.contains(target)) return
    if (target.closest('button, a, input')) return
    onOpen(card.id)
  }
  return (
    <div
      ref={rowRef}
      id={`action-${card.id}`}
      data-testid="plan-row"
      data-rank={card.rank ?? undefined}
      data-tier={tier}
    >
      <ListRow
        interactive
        className={cn(
          'min-h-16 py-3',
          tier === 'first' &&
            !selected &&
            'relative before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-series-1',
          selected &&
            '-mx-3 rounded-panel border border-border-info-soft bg-surface-primary-soft px-3 hover:bg-surface-primary-soft'
        )}
        onClick={onRow}
      >
        <span
          data-testid="plan-rank"
          className={cn(
            'inline-flex size-6.5 items-center justify-center justify-self-end rounded-control text-label-small tabular-nums',
            rankMark(tier)
          )}
        >
          {card.rank}
        </span>
        <span
          id={`${id}-verb`}
          data-testid="plan-verb"
          className={cn(
            'font-medium',
            lead ? 'text-body-medium' : 'text-body-small',
            verbTone[named.tone]
          )}
        >
          {named.verb}
        </span>
        <Pressable
          id={`action-${card.id}-open`}
          onClick={() => onOpen(card.id)}
          aria-current={selected || undefined}
          aria-labelledby={`${id}-verb ${id}-name`}
          className="min-w-0 text-left"
        >
          <span
            id={`${id}-name`}
            className={cn(
              'block text-body-medium',
              tier === 'later'
                ? 'font-normal text-content-layout-3'
                : 'font-medium text-content-layout-1'
            )}
          >
            {named.name}
            {named.detail ? (
              <span
                className={cn(
                  'font-normal',
                  tier !== 'later' && 'text-content-layout-2'
                )}
              >
                {' '}
                {named.detail}
              </span>
            ) : null}
          </span>
          <Text
            as="span"
            level="caption"
            className="mt-0.5 block text-content-layout-3"
          >
            {caption(card, withKind)}
            {what.sub ? (
              <span className={compact ? undefined : 'desktop:hidden'}>
                , {what.sub}
              </span>
            ) : null}
          </Text>
        </Pressable>
        {compact ? null : (
          <span
            className={cn(
              'min-w-0 text-body-small max-desktop:hidden',
              tier === 'later'
                ? 'text-content-layout-3'
                : 'text-content-layout-2'
            )}
          >
            {what.text}
            {what.sub ? (
              <span className="mt-0.5 block text-caption text-content-layout-3">
                {what.sub}
              </span>
            ) : null}
          </span>
        )}
        <span
          data-testid="plan-saves"
          className="flex flex-wrap items-baseline justify-end gap-x-1 tabular-nums"
        >
          <span
            className={cn(
              'whitespace-nowrap',
              compact && tier === 'first'
                ? 'text-headline-4 text-content-layout-1'
                : lead
                  ? 'text-headline-2 text-content-layout-1'
                  : savesSize(tier)
            )}
          >
            {saves(card.saves_ms_per_min)}
          </span>
          <span className="whitespace-nowrap text-caption text-content-layout-3">
            a min
          </span>
        </span>
        {compact ? null : (
          <>
            <span className="justify-self-end">
              {card.sql ? (
                <Button
                  variant="primary"
                  modifier={primary ? 'solid' : 'ghost'}
                  size="small"
                  label={copied ? 'Copied' : 'Copy SQL'}
                  onClick={() => copy(card.sql ?? '')}
                />
              ) : null}
            </span>
            <Icon
              name="chevron-right"
              label=""
              aria-hidden="true"
              size="small"
              className="justify-self-end text-content-layout-3"
            />
          </>
        )}
      </ListRow>
    </div>
  )
}

/**
 * The indexes the plan keeps: the first one, then a row that opens the rest. They have no rank,
 * no button and no saving.
 */
export function KeptRows({
  indexes,
  compact = false,
  withKind = true,
}: {
  indexes: KeptIndex[]
  compact?: boolean
  withKind?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [first, ...rest] = indexes
  if (!first) return null
  return (
    <>
      <KeptRow index={first} compact={compact} withKind={withKind} />
      {rest.length ? (
        <>
          <ListRow interactive columns={MORE_COLUMNS} asChild>
            <Pressable
              aria-expanded={open}
              onClick={() => setOpen((value) => !value)}
              className="w-full text-left"
              data-testid="kept-more"
            >
              <span />
              <span />
              <Text
                as="span"
                level="body-small"
                className="min-w-0 text-content-layout-2"
              >
                {rest.length} more to keep, your queries read these or they
                enforce a key
              </Text>
              <Icon
                name="chevron-down"
                label=""
                aria-hidden="true"
                size="small"
                className={cn(
                  'justify-self-end text-content-layout-3 transition-transform duration-fast',
                  open && 'rotate-180'
                )}
              />
            </Pressable>
          </ListRow>
          <Reveal open={open}>
            {rest.map((index) => (
              <KeptRow
                key={`${index.database}.${index.index_name}`}
                index={index}
                compact={compact}
                withKind={withKind}
              />
            ))}
          </Reveal>
        </>
      ) : null}
    </>
  )
}

function KeptRow({
  index,
  compact,
  withKind,
}: {
  index: KeptIndex
  compact: boolean
  withKind: boolean
}) {
  const row = keptRow(index, withKind)
  return (
    <div data-testid="kept-row">
      <ListRow className="min-h-16 py-3">
        <span />
        <span className="text-body-small font-medium text-content-layout-2">
          Keep
        </span>
        <span className="min-w-0">
          <Text
            as="span"
            level="body-medium"
            className="block font-normal text-content-layout-2"
          >
            {index.index_name} on {index.table}
          </Text>
          <Text
            as="span"
            level="caption"
            className="mt-0.5 block text-content-layout-3"
          >
            {row.sub}
            <span className={compact ? undefined : 'desktop:hidden'}>
              , {row.what.text.toLowerCase()} {row.what.sub}
            </span>
          </Text>
        </span>
        {compact ? null : (
          <span className="min-w-0 text-body-small text-content-layout-2 max-desktop:hidden">
            {row.what.text}
            <span className="mt-0.5 block text-caption text-content-layout-3">
              {row.what.sub}
            </span>
          </span>
        )}
        <span />
        {compact ? null : (
          <>
            <span />
            <span />
          </>
        )}
      </ListRow>
    </div>
  )
}
