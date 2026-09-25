import { cn } from '@rs/tailwind-base'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Text } from '@rs/ui-new/text'
import { type MouseEvent, useId, useState } from 'react'
import type { InboxItem, Occurrence } from '@/lib/monitor/types'
import { MiniTrend, SERIES_COLOR } from '../monitor-kit/charts'
import { formatClock, plural } from '../monitor-kit/format'
import { routeHref } from './inboxLinks'
import { itemPlace } from './inboxModel'
import {
  type BurstFrame,
  bySize,
  clusterRoute,
  clusterTime,
  jumpFigures,
  jumpSize,
  type MemberJump,
} from './onsetClusterModel'
import { FiguresText, QueryLineLink } from './QueryLines'

/** Members shown per page, and added by each 'Show more'. */
export const MEMBER_PAGE = 10

/** A plain left click stays in the app; modified clicks open the href as usual. */
function inApp(event: MouseEvent, open: () => void) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey)
    return
  event.preventDefault()
  open()
}

const PILL_TONE: Partial<Record<NonNullable<MemberJump['kind']>, string>> = {
  slowed: 'bg-surface-warning-soft text-content-warning-soft',
  'sped up': 'bg-surface-positive-soft text-content-positive-soft',
  stopped: 'bg-surface-layout-2 text-content-layout-2',
}

/** '4.3x', '+24%', 'stopped': the size of one member's move, the same on every row. */
function JumpPill({ jump }: { jump: MemberJump }) {
  const size = jumpSize(jump)
  if (!size) return null
  return (
    <span
      className={cn(
        'inline-flex h-5 items-center rounded-pill px-1.5 text-label-small font-medium tabular-nums whitespace-nowrap',
        (jump.kind && PILL_TONE[jump.kind]) ??
          'bg-surface-layout-2 text-content-layout-1'
      )}
    >
      {size}
    </span>
  )
}

/** '3:04 to 3:15 PM', '3:04 PM, still going'. */
function spanText(jump: MemberJump, ongoing: boolean): string | null {
  if (jump.start == null) return null
  if (jump.end == null)
    return `${formatClock(jump.start)}${ongoing ? ', still going' : ''}`
  return `${formatClock(jump.start)} to ${formatClock(jump.end)}`
}

function MemberRow({
  jump,
  ongoing,
  href,
  onOpen,
}: {
  jump: MemberJump
  ongoing: boolean
  href: string
  onOpen: () => void
}) {
  const figures = jumpFigures(jump)
  const span = spanText(jump, ongoing)
  const spark = jump.onset && jump.onset.points.length > 1 ? jump.onset : null
  return (
    <li
      data-member={jump.key}
      className="grid grid-cols-1 items-center gap-x-4 gap-y-1 px-3 py-2 tablet:grid-cols-[minmax(0,1fr)_auto_5rem]"
    >
      <QueryLineLink
        line={{ key: jump.key, name: jump.name, figures: null }}
        href={href}
        onOpen={onOpen}
        size="small"
      />
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 tablet:flex-col tablet:items-end">
        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
          {figures ? (
            <FiguresText figures={{ ...figures, label: '' }} />
          ) : (
            <Text
              as="span"
              level="body-small"
              className="text-content-layout-3"
            >
              Too few readings
            </Text>
          )}
          <JumpPill jump={jump} />
        </span>
        {span && (
          <Text
            as="span"
            level="caption"
            data-testid="line-onset"
            className="whitespace-nowrap tabular-nums text-content-layout-3"
          >
            {span}
          </Text>
        )}
      </div>
      <div className="hidden justify-end tablet:flex">
        {spark && (
          <MiniTrend
            points={spark.points}
            domain={spark.domain ?? undefined}
            color={
              spark.metric === 'latency'
                ? SERIES_COLOR.latency
                : SERIES_COLOR.throughput
            }
            ariaLabel={`${jump.name}: ${spark.text}`}
            highlight={{ start: spark.start, end: spark.end }}
            height={22}
            minPoints={2}
            interactive={false}
            className="w-20"
          />
        )}
      </div>
    </li>
  )
}

/** Ten rows at a time, with 'Show 10 more' and 'Show fewer' on one line. */
function MemberPage({
  members,
  ongoing,
  hrefFor,
  onOpen,
}: {
  members: MemberJump[]
  ongoing: boolean
  hrefFor: (key: string) => string
  onOpen: (key: string) => void
}) {
  const [shown, setShown] = useState(MEMBER_PAGE)
  const visible = members.slice(0, shown)
  const left = members.length - visible.length
  return (
    <>
      <ul className="divide-y divide-border-layout-1">
        {visible.map((jump) => (
          <MemberRow
            key={jump.key}
            jump={jump}
            ongoing={ongoing}
            href={hrefFor(jump.key)}
            onOpen={() => onOpen(jump.key)}
          />
        ))}
      </ul>
      {members.length > MEMBER_PAGE && (
        <div className="flex items-center gap-1 border-t border-border-layout-1 px-1.5 py-1">
          {left > 0 && (
            <PagerButton
              label={`Show ${Math.min(MEMBER_PAGE, left)} more`}
              icon="chevron-down"
              onClick={() => setShown((count) => count + MEMBER_PAGE)}
            />
          )}
          {shown > MEMBER_PAGE && (
            <PagerButton
              label="Show fewer"
              icon="chevron-up"
              onClick={() => setShown(MEMBER_PAGE)}
            />
          )}
          <Text
            as="span"
            level="caption"
            className="ml-auto pr-1.5 tabular-nums text-content-layout-3"
          >
            {visible.length} of {members.length}
          </Text>
        </div>
      )}
    </>
  )
}

function PagerButton({
  label,
  icon,
  onClick,
}: {
  label: string
  icon: 'chevron-down' | 'chevron-up'
  onClick: () => void
}) {
  return (
    <Pressable
      onClick={onClick}
      className="flex items-center gap-1 rounded-control px-2 py-1 text-label-medium text-content-layout-2 transition-colors duration-fast hover:bg-surface-layout-1 hover:text-content-layout-1"
    >
      {label}
      <Icon name={icon} label="" aria-hidden="true" className="h-3.5 w-3.5" />
    </Pressable>
  )
}

/**
 * One burst's members: '12 queries', its time frame link, and the members one click away
 * (folded at first, ten at a time once open).
 */
export function OnsetClusters({
  item,
  jumps,
  frame,
  hrefFor,
  onOpen,
  onOpenFrame,
}: {
  item: InboxItem
  jumps: MemberJump[]
  /** The burst's onsets, first to last; null when no member's onset shows. */
  frame: BurstFrame | null
  hrefFor: (key: string) => string
  onOpen: (key: string) => void
  onOpenFrame: () => void
}) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const members = [...jumps].sort(bySize)
  const count = plural(members.length, 'query', 'queries')
  return (
    <section
      aria-label="Queries in this burst"
      className="max-w-4xl rounded-panel border border-border-layout-1 bg-surface-layout-2/50"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1.5 py-1.5">
        <Pressable
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          aria-controls={panelId}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-control px-1.5 py-1 text-left transition-colors duration-fast hover:bg-surface-layout-1"
        >
          <Icon
            name="chevron-right"
            label=""
            aria-hidden="true"
            className={cn(
              'h-3.5 w-3.5 shrink-0 text-content-layout-3 transition-transform duration-fast',
              open && 'rotate-90'
            )}
          />
          <Text
            as="span"
            level="label-medium"
            className="min-w-0 text-content-layout-1"
          >
            {open ? `Hide the ${count}` : `Show the ${count}`}
          </Text>
          {frame && (
            <Text as="span" level="caption" className="text-content-layout-3">
              {clusterTime(frame)}
            </Text>
          )}
        </Pressable>
        {frame && (
          <a
            href={routeHref(clusterRoute(item, frame))}
            onClick={(event) => inApp(event, onOpenFrame)}
            aria-label={`Open the Database page ${clusterTime(frame)}`}
            className="group/frame ml-7 flex items-center gap-1 rounded-control px-1.5 py-1 text-label-medium text-content-primary-soft outline-none transition-colors duration-fast hover:bg-surface-primary-soft focus-visible:shadow-focus tablet:ml-0"
          >
            Open this time frame
            <Icon
              name="arrow-up-right"
              label=""
              aria-hidden="true"
              className="h-3.5 w-3.5 transition-transform duration-fast group-hover/frame:translate-x-0.5"
            />
          </a>
        )}
      </div>
      {open && (
        <div
          id={panelId}
          className="mx-1.5 mb-1.5 overflow-hidden rounded-control border border-border-layout-1 bg-surface-layout-1"
        >
          <MemberPage
            members={members}
            ongoing={itemPlace(item) !== 'resolved'}
            hrefFor={hrefFor}
            onOpen={onOpen}
          />
        </div>
      )}
    </section>
  )
}

const times = (count: number) => (count === 1 ? 'once' : `${count} times`)

/** '3.1x', '+40%': an occurrence's size, the same way member rows show theirs. */
function occurrenceSize(size: number | null): string | null {
  if (size == null) return null
  return jumpSize({ kind: size >= 1 ? 'jumped' : 'dropped', change: size - 1 })
}

/**
 * A repeating burst's occurrences, newest first, folded at first: each with its time
 * frame, its size and its own link to that time frame on the Database page.
 */
export function Occurrences({
  item,
  entries,
  onOpenFrame,
}: {
  item: InboxItem
  entries: Occurrence[]
  onOpenFrame: (frame: BurstFrame) => void
}) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  return (
    <section
      aria-label="Each time it happened"
      className="max-w-4xl rounded-panel border border-border-layout-1 bg-surface-layout-2/50"
    >
      <Pressable
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={panelId}
        className="m-1.5 flex items-center gap-2 rounded-control px-1.5 py-1 text-left transition-colors duration-fast hover:bg-surface-layout-1"
      >
        <Icon
          name="chevron-right"
          label=""
          aria-hidden="true"
          className={cn(
            'h-3.5 w-3.5 shrink-0 text-content-layout-3 transition-transform duration-fast',
            open && 'rotate-90'
          )}
        />
        <Text as="span" level="label-medium" className="text-content-layout-1">
          {open
            ? `Hide the ${times(entries.length)}`
            : `Show the ${times(entries.length)}`}
        </Text>
      </Pressable>
      {open && (
        <ul
          id={panelId}
          className="mx-1.5 mb-1.5 divide-y divide-border-layout-1 overflow-hidden rounded-control border border-border-layout-1 bg-surface-layout-1"
        >
          {entries.map((entry) => {
            const frame = { start: entry.start, last: entry.end ?? entry.start }
            const size = occurrenceSize(entry.size)
            return (
              <li
                key={entry.start}
                data-occurrence={entry.start}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2"
              >
                <Text
                  as="span"
                  level="body-small"
                  className="min-w-0 flex-1 tabular-nums text-content-layout-1"
                >
                  {entry.end == null && itemPlace(item) !== 'resolved'
                    ? `${clusterTime({ start: entry.start, last: entry.start })}, still going`
                    : clusterTime(frame)}
                </Text>
                <Text
                  as="span"
                  level="caption"
                  className="text-content-layout-3"
                >
                  {plural(entry.query_count, 'query', 'queries')}
                </Text>
                {size && (
                  <span
                    className={cn(
                      'inline-flex h-5 items-center rounded-pill px-1.5 text-label-small font-medium tabular-nums',
                      entry.verdict === 'bigger'
                        ? 'bg-surface-warning-soft text-content-warning-soft'
                        : 'bg-surface-layout-2 text-content-layout-1'
                    )}
                  >
                    {size}
                  </span>
                )}
                {entry.verdict === 'bigger' && (
                  <Text
                    as="span"
                    level="caption"
                    className="text-content-warning-soft"
                  >
                    Bigger than usual
                  </Text>
                )}
                {entry.quiet && (
                  <Text
                    as="span"
                    level="caption"
                    className="text-content-layout-3"
                  >
                    Usual
                  </Text>
                )}
                <a
                  href={routeHref(clusterRoute(item, frame))}
                  onClick={(event) => inApp(event, () => onOpenFrame(frame))}
                  aria-label={`Open the Database page ${clusterTime(frame)}`}
                  className="group/frame flex items-center gap-1 rounded-control px-1.5 py-1 text-label-medium text-content-primary-soft outline-none transition-colors duration-fast hover:bg-surface-primary-soft focus-visible:shadow-focus"
                >
                  Open this time frame
                  <Icon
                    name="arrow-up-right"
                    label=""
                    aria-hidden="true"
                    className="h-3.5 w-3.5"
                  />
                </a>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
