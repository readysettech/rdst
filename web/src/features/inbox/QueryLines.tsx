import { cn } from '@rs/tailwind-base'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Text } from '@rs/ui-new/text'
import { type MouseEvent, useState } from 'react'
import { MiniTrend, SERIES_COLOR } from '../monitor-kit/charts'
import type { LineOnset } from './incidentModel'
import type { Figures, QueryLine } from './itemStory'

/** 'Latency 77 ms -> 253 ms': the label quiet, the new value strongest. */
export function FiguresText({
  figures,
  className,
}: {
  figures: Figures
  className?: string
}) {
  return (
    <Text
      as="span"
      level="body-small"
      className={cn(
        'inline-flex flex-wrap items-baseline gap-x-1.5 tabular-nums',
        className
      )}
    >
      {figures.label && (
        <span className="text-content-layout-3">{figures.label}</span>
      )}
      <span className="inline-flex items-baseline gap-x-1.5 whitespace-nowrap">
        {figures.before && (
          <>
            <span className="text-content-layout-2">{figures.before}</span>
            <Icon
              name="arrow-right"
              label="to"
              className="h-3 w-3 shrink-0 self-center text-content-layout-3"
            />
          </>
        )}
        <span className="font-medium text-content-layout-1">
          {figures.after}
        </span>
      </span>
    </Text>
  )
}

const PAGER =
  'flex items-center gap-1 rounded-control px-2 py-1 text-label-medium text-content-layout-2 transition-colors duration-fast hover:bg-surface-layout-1 hover:text-content-layout-1'

/** A plain left click stays in the app; modified clicks open the href as usual. */
function inApp(event: MouseEvent, open: () => void) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey)
    return
  event.preventDefault()
  open()
}

/** One affected query: its name as a link to the Database page, then its numbers. */
export function QueryLineLink({
  line,
  href,
  onOpen,
  size = 'base',
  className,
}: {
  line: QueryLine
  href: string
  onOpen: () => void
  /** 'base' for the lead of a full row, 'small' inside lists and compact rows. */
  size?: 'base' | 'small'
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-0.5',
        className
      )}
    >
      <a
        href={href}
        onClick={(event) => inApp(event, onOpen)}
        data-query-key={line.key}
        title={`Open ${line.name} on the Database page`}
        className={cn(
          'group/q inline-flex min-w-0 max-w-full cursor-pointer items-baseline gap-1 rounded-control font-medium text-content-layout-1 outline-none',
          'transition-colors duration-fast hover:text-content-primary-soft focus-visible:shadow-focus',
          size === 'base' ? 'text-body-medium' : 'text-body-small'
        )}
      >
        <span className="min-w-0 break-words underline decoration-content-layout-3/40 decoration-dotted underline-offset-4 group-hover/q:decoration-current group-hover/q:decoration-solid">
          {line.name}
        </span>
        <Icon
          name="arrow-up-right"
          label=""
          aria-hidden="true"
          className="h-3.5 w-3.5 shrink-0 self-center text-content-layout-3 transition-[color,transform] duration-fast group-hover/q:translate-x-0.5 group-hover/q:-translate-y-0.5 group-hover/q:text-content-primary-soft"
        />
      </a>
      {line.figures && <FiguresText figures={line.figures} />}
    </div>
  )
}

/** A tiny sparkline of one query with its own incident window shaded. */
function OnsetSpark({ onset, name }: { onset: LineOnset; name: string }) {
  if (onset.points.length < 2) return null
  return (
    <MiniTrend
      points={onset.points}
      domain={onset.domain ?? undefined}
      color={
        onset.metric === 'latency'
          ? SERIES_COLOR.latency
          : SERIES_COLOR.throughput
      }
      ariaLabel={`${name}: ${onset.text}`}
      highlight={{ start: onset.start, end: onset.end }}
      height={22}
      minPoints={2}
      interactive={false}
      className="w-20"
    />
  )
}

/**
 * A list row with the query's own change: the name, then its numbers over 'jumped
 * 9:42 PM, 3.1x', then its sparkline. On phones the numbers go under the name and the
 * sparkline is left out.
 */
function OnsetRow({
  line,
  href,
  onOpen,
}: {
  line: QueryLine
  href: string
  onOpen: () => void
}) {
  return (
    <li className="grid grid-cols-1 items-center gap-x-4 gap-y-1 px-3 py-2 tablet:grid-cols-[minmax(0,1fr)_auto_5rem]">
      <QueryLineLink
        line={{ ...line, figures: null }}
        href={href}
        onOpen={onOpen}
        size="small"
      />
      <div className="flex flex-wrap items-baseline gap-x-3 tablet:flex-col tablet:items-end tablet:gap-y-0.5">
        {line.figures && <FiguresText figures={line.figures} />}
        {line.onset && (
          <Text
            as="span"
            level="caption"
            data-testid="line-onset"
            className="whitespace-nowrap tabular-nums text-content-layout-3"
          >
            {line.figures ? line.onset.when : line.onset.text}
          </Text>
        )}
      </div>
      <div className="hidden justify-end tablet:flex">
        {line.onset && <OnsetSpark onset={line.onset} name={line.name} />}
      </div>
    </li>
  )
}

/** At most this many lines before 'Show N more'. */
const PREVIEW = 5
/** Each 'Show more' adds this many lines. */
const PAGE = 10

/**
 * Several affected queries, most impact first: the first five with their numbers,
 * then ten more at a time, with 'Show fewer' to fold back.
 */
export function QueryLineList({
  lines,
  hrefFor,
  onOpen,
  heading,
}: {
  lines: QueryLine[]
  hrefFor: (key: string) => string
  onOpen: (key: string) => void
  heading?: string
}) {
  // Lines with numbers lead; the preview stops where the numbers do.
  const measured = lines.filter((line) => line.figures).length
  const preview =
    measured > 0 ? Math.min(Math.max(measured, 3), PREVIEW) : PREVIEW
  const [limit, setLimit] = useState(preview)
  const capped = lines.length > preview + 1
  const shown = capped ? lines.slice(0, limit) : lines
  const more = lines.length - shown.length
  // When the readings show when queries changed, every row gets the timing columns.
  const timed = lines.some((line) => line.onset)
  return (
    <div className="max-w-3xl overflow-hidden rounded-panel border border-border-layout-1 bg-surface-layout-2/50">
      {heading && (
        <Text
          as="p"
          level="caption"
          className="border-b border-border-layout-1 px-3 py-1.5 text-content-layout-3"
        >
          {heading}
        </Text>
      )}
      <ul className="divide-y divide-border-layout-1">
        {shown.map((line) =>
          timed ? (
            <OnsetRow
              key={line.key}
              line={line}
              href={hrefFor(line.key)}
              onOpen={() => onOpen(line.key)}
            />
          ) : (
            <li key={line.key} className="px-3 py-2">
              <QueryLineLink
                line={line}
                href={hrefFor(line.key)}
                onOpen={() => onOpen(line.key)}
                size="small"
                className="justify-between"
              />
            </li>
          )
        )}
      </ul>
      {capped && (
        <div className="flex items-center gap-1 border-t border-border-layout-1 px-1.5 py-1">
          {more > 0 && (
            <Pressable
              onClick={() => setLimit((value) => value + PAGE)}
              className={PAGER}
            >
              Show {Math.min(PAGE, more)} more
              <Icon
                name="chevron-down"
                label=""
                aria-hidden="true"
                className="h-3.5 w-3.5"
              />
            </Pressable>
          )}
          {limit > preview && (
            <Pressable onClick={() => setLimit(preview)} className={PAGER}>
              Show fewer
              <Icon
                name="chevron-up"
                label=""
                aria-hidden="true"
                className="h-3.5 w-3.5"
              />
            </Pressable>
          )}
          <Text
            as="span"
            level="caption"
            className="ml-auto pr-1.5 tabular-nums text-content-layout-3"
          >
            {shown.length} of {lines.length}
          </Text>
        </div>
      )}
    </div>
  )
}
