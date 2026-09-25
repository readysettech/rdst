import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Dropdown } from '@rs/ui-new/dropdown'
import { Icon } from '@rs/ui-new/icon'
import { IconButton } from '@rs/ui-new/icon-button'
import { Pressable } from '@rs/ui-new/pressable'
import { StatusRipple } from '@rs/ui-new/status'
import { Text } from '@rs/ui-new/text'
import { useToast } from '@rs/ui-new/use-toast'
import { Fragment, type ReactNode, useEffect, useId, useState } from 'react'
import type { InboxItem } from '@/lib/monitor/types'
import { MiniTrend, TONE_COLOR } from '../monitor-kit/charts'
import { formatMs, plural } from '../monitor-kit/format'
import { MuteMenu } from '../monitor-kit/MuteMenu'
import { SeverityIcon } from '../monitor-kit/SeverityIcon'
import { itemBand, itemTone } from '../monitor-kit/severity'
import { ActionTiles } from './ActionTiles'
import { actionTiles, adviceNotes } from './actionTileModel'
import { itemLink, itemRoute, routeHref } from './inboxLinks'
import {
  closedLabel,
  type SentStamp,
  sentStamp,
  summaryTokens,
} from './inboxModel'
import { incidentWindow } from './incidentModel'
import {
  type ItemLine,
  itemLine,
  itemReason,
  recurringText,
  smallerText,
} from './itemHeadline'
import { type ItemStory, itemStory } from './itemStory'
import { Occurrences, OnsetClusters } from './OnsetClusters'
import {
  burstRoute,
  groupBreakdown,
  memberJumps,
  shownFrame,
} from './onsetClusterModel'
import { FiguresText, QueryLineLink, QueryLineList } from './QueryLines'
import {
  type CloseAction,
  type InboxActions,
  SNOOZE_OPTIONS,
} from './useInboxActions'

const DOT = '\u00b7'

function trendFormatter(type: string) {
  if (type === 'rollback_or_deadlock_spike')
    return (value: number) => `${Math.round(value)} a minute`
  if (type === 'cache_hit_ratio_drop')
    return (value: number) => `${(value * 100).toFixed(1)}%`
  if (type === 'traffic_spike' || type === 'query_stopped_running')
    return (value: number) => `${value.toFixed(2)} calls/s`
  if (type === 'connections_near_max')
    return (value: number) => `${Math.round(value)} connections`
  if (type === 'lock_contention')
    return (value: number) => `${Math.round(value)} waiting`
  return (value: number) => `${formatMs(value)} ms`
}

/** The incident window on the item's own sparkline, when the readings show it. */
function trendHighlight(item: InboxItem) {
  const window = incidentWindow(item)
  return window?.measured ? { start: window.start, end: window.end } : null
}

/** When the alert went out, with the full timestamps on hover. */
export function SentTime({
  stamp,
  prefix,
  short = false,
}: {
  stamp: SentStamp
  /** Leads the label: 'Latest' gives 'Latest sent 9:42 PM'. */
  prefix?: string
  /** Leave the note ('no alert sent') to the tooltip. */
  short?: boolean
}) {
  const text = short ? stamp.short : stamp.label
  const label = prefix
    ? `${prefix} ${text.charAt(0).toLowerCase()}${text.slice(1)}`
    : text
  return (
    <time dateTime={stamp.iso} title={stamp.title}>
      {label}
    </time>
  )
}

/** Caption parts joined by middle dots; empty parts drop out. */
export function Caption({ parts }: { parts: ReactNode[] }) {
  const shown = parts.filter(
    (part) => part !== null && part !== false && part !== ''
  )
  return (
    <>
      {shown.map((part, index) => (
        <Fragment key={index}>
          {index > 0 && ` ${DOT} `}
          {part}
        </Fragment>
      ))}
    </>
  )
}

/** The database an item belongs to, named the same way on every row. */
export function DatabaseName({ name }: { name: string }) {
  return (
    <span
      className="inline-flex items-center gap-1 align-bottom font-medium text-content-layout-2"
      data-testid="item-database"
    >
      <Icon
        name="database"
        label=""
        aria-hidden="true"
        size="small"
        className="h-3.5 w-3.5"
      />
      {name}
    </span>
  )
}

export function inboxItemLink(id: number) {
  return `${window.location.origin}/inbox?item=${id}`
}

/** The one-line explanation with its numbers set in the primary text color. */
export function SummaryLine({
  summary,
  className,
}: {
  summary: string
  className?: string
}) {
  return (
    <Text level="body-small" className={cn('text-content-layout-2', className)}>
      {summaryTokens(summary).map((token, index) =>
        token.kind === 'arrow' ? (
          <Icon
            key={index}
            name="arrow-right"
            label="to"
            className="mx-1 inline h-3 w-3 align-[-1px] text-content-layout-3"
          />
        ) : token.kind === 'number' ? (
          <span
            key={index}
            className="whitespace-nowrap font-medium tabular-nums text-content-layout-1"
          >
            {token.text}
          </span>
        ) : (
          <span key={index}>{token.text}</span>
        )
      )}
    </Text>
  )
}

/** Resolve + menu (snooze, dismiss): the one close control per alert. */
export function ResolveSplit({
  label = 'Resolve',
  onClose,
}: {
  label?: string
  onClose: (action: CloseAction) => void
}) {
  return (
    <div className="flex items-center">
      <Button
        label={label}
        icon="tick"
        iconPosition="left"
        modifier="outline"
        size="small"
        classMerge="rounded-r-none border-r-0"
        onClick={() => onClose({ kind: 'resolve' })}
      />
      <Dropdown>
        <Dropdown.Trigger asChild>
          <IconButton
            icon="chevron-down"
            label="More ways to close"
            modifier="outline"
            size="small"
            tooltip={false}
            classMerge="rounded-l-none"
          />
        </Dropdown.Trigger>
        <Dropdown.Content align="end" className="min-w-56">
          {SNOOZE_OPTIONS.map((option) => (
            <Dropdown.Item
              key={option.value}
              leftIcon="notification"
              label={option.label}
              onSelect={() => onClose({ kind: 'snooze', for: option.value })}
            />
          ))}
          <Dropdown.Separator />
          <Dropdown.Item
            leftIcon="close"
            label="Dismiss"
            onSelect={() => onClose({ kind: 'dismiss' })}
          />
        </Dropdown.Content>
      </Dropdown>
    </div>
  )
}

function CopyLinkItem({ id }: { id: number }) {
  const { toast } = useToast()
  return (
    <Dropdown.Item
      leftIcon="copy"
      label="Copy link"
      onSelect={() => {
        void navigator.clipboard
          ?.writeText(inboxItemLink(id))
          .then(() => toast({ title: 'Link copied', duration: 2000 }))
      }}
    />
  )
}

export type RowDensity = 'full' | 'compact'

interface InboxItemRowProps {
  item: InboxItem
  actions: InboxActions
  /** Display names by query key, for episode chips. */
  names: Map<string, string>
  density?: RowDensity
  /** Show the target in the caption (several targets on screen). */
  showTarget?: boolean
  highlighted?: boolean
  fresh?: boolean
  /** Replaces the close controls (Muted and Done tabs). */
  trailing?: ReactNode
  /** Extra caption text (who muted or closed it). */
  note?: string
  nested?: boolean
  /** Render as muted (neutral tone, no unread dot). */
  quiet?: boolean
}

export function InboxItemRow({
  item,
  actions,
  names,
  density,
  showTarget = true,
  highlighted = false,
  fresh = false,
  trailing,
  note,
  nested = false,
  quiet = false,
}: InboxItemRowProps) {
  const band = itemBand(item)
  const closed = item.state === 'resolved' || item.state === 'dismissed'
  const recovered = band === 'recovered'
  const muted = item.muted_by !== null || quiet
  // Closed and muted alerts no longer demand anything: only good news keeps its color.
  const liveTone = itemTone(item)
  const tone =
    (closed || muted) && liveTone !== 'positive' ? 'neutral' : liveTone
  const rowDensity = density ?? (recovered || closed ? 'compact' : 'full')
  const unread = item.state === 'new' && !closed && !muted
  const singleKey = item.query_keys.length === 1 ? item.query_keys[0] : null
  const link = itemLink(item)
  const openButton = (
    <Button
      label={link.label}
      modifier="ghost"
      size="small"
      icon="arrow-up-right"
      iconPosition="right"
      classMerge="hidden tablet:flex"
      onClick={() => actions.openInDatabase(item)}
    />
  )
  const menu = (
    <MuteMenu
      target={item.target}
      queryKey={singleKey}
      queryLabel={singleKey ? item.subject_label : undefined}
      type={item.type}
      label={`More actions for ${item.title}`}
      extraItems={
        <>
          <Dropdown.Item
            leftIcon="arrow-up-right"
            label={link.label}
            className="tablet:hidden"
            onSelect={() => actions.openInDatabase(item)}
          />
          <CopyLinkItem id={item.id} />
        </>
      }
    />
  )
  const controls =
    trailing ??
    (recovered ? (
      <>
        {openButton}
        {/* Still open but measured back to normal: resolving it is the way out. */}
        <Button
          label={closed ? 'Got it' : 'Resolve'}
          icon="tick"
          iconPosition="left"
          modifier="outline"
          size="small"
          onClick={() =>
            closed
              ? actions.acknowledge([item])
              : actions.close([item], { kind: 'resolve' })
          }
        />
      </>
    ) : (
      <ResolveSplit onClose={(action) => actions.close([item], action)} />
    ))
  const caption = (
    <Caption
      parts={[
        // Full rows name the database on their folded line.
        rowDensity === 'compact' && (showTarget || !singleKey) ? (
          <DatabaseName key="database" name={item.target} />
        ) : null,
        // Closed and recovered rows also carry a close time; keep them to one line.
        <SentTime
          key="sent"
          stamp={sentStamp(item)}
          short={closed || recovered}
        />,
        item.state === 'snoozed' ? 'Snoozed' : null,
        closed || recovered ? closedLabel(item) : null,
        note ?? null,
      ]}
    />
  )

  const title = (
    <Text
      as="h3"
      level={
        rowDensity === 'compact'
          ? 'label-medium'
          : band === 'act_now'
            ? 'headline-5'
            : 'subtitle-1'
      }
      className={cn(
        'min-w-0 text-content-layout-1',
        rowDensity === 'compact' && 'max-w-full shrink-0 truncate',
        !unread && rowDensity === 'compact' && 'text-content-layout-2'
      )}
    >
      {item.title}
    </Text>
  )

  const story = itemStory(item, names)
  // A many-queries alert shows each member's own sparkline instead.
  const showTrend =
    item.trend.length > 1 &&
    !(story.queries.length > 1 && item.incident?.queries.length)
  // Live items start folded to one line; a linked item opens.
  const [open, setOpen] = useState(highlighted)
  useEffect(() => {
    if (highlighted) setOpen(true)
  }, [highlighted])
  const panelId = useId()

  const unreadDot = (
    <span
      className={cn('flex w-2 shrink-0 justify-center', !unread && 'invisible')}
    >
      {unread && (
        <StatusRipple
          color="primary"
          label="Unread"
          className="[&>*:nth-child(2)]:sr-only"
        />
      )}
    </span>
  )

  if (rowDensity === 'compact')
    return (
      <article
        data-inbox-item={item.id}
        className={cn(
          'group relative flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5 transition-colors duration-fast ease-base hover:bg-surface-raised',
          nested && 'pl-10',
          highlighted && 'bg-surface-raised shadow-focus',
          fresh && 'inbox-fresh'
        )}
      >
        {unreadDot}
        <SeverityIcon type={item.type} tone={tone} size="small" />
        <div className="flex min-w-0 flex-1 basis-56 flex-col gap-0.5">
          {title}
          <CompactStory item={item} story={story} actions={actions} />
          <ReasonLine item={item} tone={tone} />
        </div>
        <Text
          level="caption"
          className="ml-7 basis-full text-content-layout-3 tablet:ml-0 tablet:basis-auto tablet:shrink-0 tablet:whitespace-nowrap"
        >
          {caption}
        </Text>
        <div
          className={cn(
            HOVER_ACTIONS,
            'ml-auto laptop:pointer-fine:top-1/2 laptop:pointer-fine:-translate-y-1/2'
          )}
        >
          {!closed && !trailing && openButton}
          {controls}
          {menu}
        </div>
      </article>
    )

  const toggle = () => setOpen((value) => !value)
  return (
    <article
      data-inbox-item={item.id}
      data-open={open || undefined}
      className={cn(
        'group relative grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-3 px-4 transition-colors duration-fast ease-base hover:bg-surface-raised tablet:gap-x-4 desktop:grid-cols-[auto_minmax(0,1fr)_auto]',
        open ? 'py-4' : 'py-3',
        nested && 'pl-10',
        highlighted && 'bg-surface-raised shadow-focus',
        fresh && 'inbox-fresh'
      )}
    >
      <div className="flex items-start gap-2 pt-0.5">
        {unreadDot}
        <SeverityIcon type={item.type} tone={tone} />
      </div>
      <div className="flex min-w-0 flex-col gap-1.5">
        <Text
          as="h3"
          level={band === 'act_now' ? 'headline-5' : 'subtitle-1'}
          className="min-w-0 text-content-layout-1"
        >
          <Pressable
            onClick={toggle}
            aria-expanded={open}
            aria-controls={panelId}
            className="-mx-1 flex max-w-full items-start gap-1.5 rounded-control px-1 text-left outline-none focus-visible:shadow-focus"
          >
            <Icon
              name="chevron-right"
              label=""
              aria-hidden="true"
              className={cn(
                'mt-[0.3em] h-3.5 w-3.5 shrink-0 text-content-layout-3 transition-transform duration-fast',
                open && 'rotate-90'
              )}
            />
            <span className="min-w-0">{item.title}</span>
          </Pressable>
        </Text>
        <div className="flex flex-col gap-0.5 tablet:pl-5">
          <FoldedLine
            line={itemLine(item, names)}
            tone={tone}
            database={showTarget ? item.target : null}
          />
          <ReasonLine item={item} tone={tone} />
        </div>
        {open && (
          <div
            id={panelId}
            className="flex min-w-0 flex-col gap-1.5 tablet:pl-5"
          >
            <StoryBlock
              item={item}
              story={story}
              actions={actions}
              names={names}
            />
            <ItemNotes item={item} />
            <Text level="caption" className="text-content-layout-3">
              {caption}
            </Text>
          </div>
        )}
      </div>
      {open && showTrend && (
        <div className="hidden items-start self-start pt-1 pr-4 desktop:flex">
          <MiniTrend
            points={item.trend.map((point) => ({
              x: point.ts,
              y: point.value,
            }))}
            color={TONE_COLOR[tone]}
            ariaLabel={`Trend around the alert for ${item.subject_label}`}
            marker={{ x: item.created_at, label: 'Alert filed' }}
            highlight={trendHighlight(item)}
            formatValue={trendFormatter(item.type)}
            height={36}
            className="w-32"
          />
        </div>
      )}
      {open && !muted && (
        <ActionTiles
          item={item}
          tiles={actionTiles(item)}
          actions={actions}
          className="col-span-full tablet:col-[2/-1] tablet:pl-5"
        />
      )}
      <div
        className={cn(
          HOVER_ACTIONS,
          'col-span-full justify-end laptop:pointer-fine:top-2.5',
          showTrend && 'desktop:pointer-fine:right-44'
        )}
      >
        {!closed && openButton}
        {controls}
        {menu}
      </div>
    </article>
  )
}

// Below laptop, or on touch, the actions sit in the row. With a mouse they float
// over the row's right edge on hover or keyboard focus, and stay while a menu is open.
export const HOVER_ACTIONS = cn(
  'flex shrink-0 items-center gap-1 transition-opacity duration-fast ease-base',
  'laptop:pointer-fine:absolute laptop:pointer-fine:right-3 laptop:pointer-fine:rounded-panel',
  'laptop:pointer-fine:bg-surface-raised laptop:pointer-fine:p-1 laptop:pointer-fine:shadow-elevation-1',
  'laptop:pointer-fine:pointer-events-none laptop:pointer-fine:opacity-0',
  'laptop:pointer-fine:group-hover:pointer-events-auto laptop:pointer-fine:group-hover:opacity-100',
  'laptop:pointer-fine:group-focus-within:pointer-events-auto laptop:pointer-fine:group-focus-within:opacity-100',
  'laptop:pointer-fine:has-[[data-state=open]]:pointer-events-auto laptop:pointer-fine:has-[[data-state=open]]:opacity-100'
)

/** Where a query line of this item opens: the item's own link for its query. */
export function queryHref(item: InboxItem, key: string) {
  const link = itemLink(item)
  const own = link.kind === 'query' && link.query_key === key
  return routeHref(itemRoute(item, own ? undefined : key))
}

export function openQuery(item: InboxItem, key: string, actions: InboxActions) {
  const link = itemLink(item)
  if (link.kind === 'query' && link.query_key === key)
    actions.openInDatabase(item)
  else actions.openInDatabase(item, key)
}

/** The affected queries with their numbers, then the plain explanation. */
function StoryBlock({
  item,
  story,
  actions,
  names,
}: {
  item: InboxItem
  story: ItemStory
  actions: InboxActions
  names: Map<string, string>
}) {
  const [first] = story.queries
  const measured = story.queries.filter((line) => line.figures).length
  const notes = adviceNotes(item)
  const explanation = [story.explanation, ...notes].join(' ')
  if (story.queries.length > 1 && item.incident?.queries.length)
    return (
      <GroupStory
        item={item}
        actions={actions}
        names={names}
        explanation={explanation}
      />
    )
  return (
    <>
      {story.queries.length === 1 && first && (
        <QueryLineLink
          line={first}
          href={queryHref(item, first.key)}
          onOpen={() => openQuery(item, first.key, actions)}
        />
      )}
      {story.queries.length > 1 && (
        <QueryLineList
          lines={story.queries}
          heading={
            measured > 0
              ? `${plural(story.queries.length, 'query', 'queries')}, the most affected first`
              : undefined
          }
          hrefFor={(key) => queryHref(item, key)}
          onOpen={(key) => openQuery(item, key, actions)}
        />
      )}
      {story.figures && (
        <FiguresText figures={story.figures} className="text-body-medium" />
      )}
      <SummaryLine summary={explanation} className="max-w-3xl" />
    </>
  )
}

/**
 * A many-queries alert, one burst: what its members did, a link to its time frame, and
 * the members one click away.
 */
function GroupStory({
  item,
  actions,
  names,
  explanation,
}: {
  item: InboxItem
  actions: InboxActions
  names: Map<string, string>
  explanation: string
}) {
  const jumps = memberJumps(item, names)
  const breakdown = groupBreakdown(jumps)
  const occurrences = item.occurrences ?? []
  const frame = shownFrame(jumps, occurrences)
  return (
    <>
      <SummaryLine summary={breakdown ?? explanation} className="max-w-3xl" />
      {occurrences.length > 1 && (
        <Occurrences
          item={item}
          entries={occurrences}
          onOpenFrame={(occurrence) =>
            actions.openRoute(item, burstRoute(item, occurrence))
          }
        />
      )}
      <OnsetClusters
        item={item}
        jumps={jumps}
        frame={frame}
        hrefFor={(key) => queryHref(item, key)}
        onOpen={(key) => openQuery(item, key, actions)}
        onOpenFrame={() =>
          frame && actions.openRoute(item, burstRoute(item, frame))
        }
      />
    </>
  )
}

/** One line under a compact row's title: the query and its numbers, else the explanation. */
function CompactStory({
  item,
  story,
  actions,
}: {
  item: InboxItem
  story: ItemStory
  actions: InboxActions
}) {
  const [first] = story.queries
  const rest = story.queries.length - 1
  const tail = story.figures ? null : (first?.figures ?? null)
  return (
    <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-0.5">
      {first && (
        <QueryLineLink
          line={{ ...first, figures: tail }}
          href={queryHref(item, first.key)}
          onOpen={() => openQuery(item, first.key, actions)}
          size="small"
        />
      )}
      {rest > 0 && (
        <Text level="body-small" className="text-content-layout-3">
          and {plural(rest, 'other query', 'other queries')}
        </Text>
      )}
      {story.figures && <FiguresText figures={story.figures} />}
      {!tail && !story.figures && (
        <SummaryLine
          summary={story.explanation}
          className="min-w-0 max-w-full truncate text-content-layout-3"
        />
      )}
    </div>
  )
}

/** 'COUNT on orders and 5 more queries · since 9:42 PM · Still happening'. */
function FoldedLine({
  line,
  tone,
  database,
}: {
  line: ItemLine
  tone: string
  /** The item's database, named first so every row says where it happened. */
  database?: string | null
}) {
  return (
    <Text
      as="p"
      level="body-small"
      data-testid="item-line"
      className="min-w-0 text-content-layout-2"
    >
      <Caption
        parts={[
          database ? <DatabaseName key="database" name={database} /> : null,
          line.who === database ? null : (
            <span key="who" className="font-medium text-content-layout-1">
              {line.who}
            </span>
          ),
          line.when,
          line.state && (
            <span
              key="state"
              className={cn(
                line.ongoing
                  ? tone === 'negative'
                    ? 'text-content-negative-soft'
                    : 'text-content-warning-soft'
                  : 'text-content-positive-soft'
              )}
            >
              {line.state}
            </span>
          ),
        ]}
      />
    </Text>
  )
}

const REASON_TONE: Record<string, string> = {
  negative: 'text-content-negative-soft',
  warning: 'text-content-warning-soft',
  positive: 'text-content-positive-soft',
  neutral: 'text-content-layout-2',
}

/** True when a reason only restates the title ('The daily report is ready'). */
function sameWords(reason: string, title: string) {
  const words = (text: string) =>
    text
      .toLowerCase()
      .replace(/[^a-z0-9 ]/g, '')
      .split(' ')
      .filter((word) => word && !['the', 'is', 'a', 'an'].includes(word))
      .join(' ')
  return words(reason) === words(title)
}

/** 'Latency up 3.2x on a top-10 query today', or 'Back to normal: ...' once closed. */
function ReasonLine({ item, tone }: { item: InboxItem; tone: string }) {
  const reason = itemReason(item)
  if (!reason || sameWords(reason, item.title)) return null
  const split = reason.indexOf(': ')
  const [label, rest] =
    split > 0 ? [reason.slice(0, split), reason.slice(split + 2)] : ['', reason]
  return (
    <Text
      as="p"
      level="caption"
      data-testid="item-reason"
      className="min-w-0 text-content-layout-3"
    >
      {label && (
        <span className={cn('font-medium', REASON_TONE[tone])}>{label}: </span>
      )}
      {rest}
    </Text>
  )
}

/** Why a scheduled burst still alerted, and the smaller queries that moved too. */
function ItemNotes({ item }: { item: InboxItem }) {
  const recurring = item.recurring ? recurringText(item.recurring) : null
  const smaller = smallerText(item.smaller_moved)
  if (!recurring && !smaller) return null
  return (
    <div className="flex flex-col gap-0.5">
      {recurring && (
        <Text
          level="body-small"
          data-testid="item-recurring"
          className="flex items-center gap-1.5 text-content-layout-2"
        >
          <Icon
            name="info"
            label=""
            aria-hidden="true"
            className="h-3.5 w-3.5 shrink-0 text-content-layout-3"
          />
          {recurring}
        </Text>
      )}
      {smaller && (
        <Text
          level="caption"
          data-testid="item-smaller"
          className="text-content-layout-3"
        >
          {smaller}
        </Text>
      )}
    </div>
  )
}
