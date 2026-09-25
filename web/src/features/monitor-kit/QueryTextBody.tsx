import { cn } from '@rs/tailwind-base'
import { CopyButton } from '@rs/ui-new/copy-button'
import { Icon } from '@rs/ui-new/icon'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import {
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { SqlTokens } from '@/components/SqlTokens'
import type { QueryTextStatus } from '@/lib/monitor/types'
import { useFormatSql } from '@/lib/useFormatSql'

export interface QueryTextBodyProps {
  /** Raw statement text; empty when the database kept none. */
  text: string
  textStatus: QueryTextStatus
  /** Human sentence explaining a missing or cut statement. */
  textReason?: string | null
  /** Query key, shown as the secondary fingerprint when text is missing. */
  fingerprint: string
  tables?: string[]
  /** Search text to mark inside the SQL. */
  highlight?: string
  /** Collapsed height in lines; the band expands on click. */
  maxLines?: 4 | 8 | 10
  expandable?: boolean
  copyable?: boolean
  dialect?: 'postgresql' | 'mysql'
  className?: string
}

const LINE_WIDTH = 80
const CLAMP: Record<4 | 8 | 10, string> = {
  4: 'max-h-20',
  8: 'max-h-40',
  10: 'max-h-50',
}

type Segment = { text: string; mark: boolean }

/** Splits text on case-insensitive matches, widened to whole words so SQL colors hold. */
export function markSegments(text: string, search: string): Segment[] {
  const needle = search.trim().toLowerCase()
  if (!needle || !text) return [{ text, mark: false }]
  const haystack = text.toLowerCase()
  const ranges: Array<[number, number]> = []
  let from = 0
  while (from <= haystack.length) {
    const at = haystack.indexOf(needle, from)
    if (at < 0) break
    let start = at
    let end = at + needle.length
    while (start > 0 && /\w/.test(text[start - 1])) start -= 1
    while (end < text.length && /\w/.test(text[end])) end += 1
    const last = ranges.at(-1)
    if (last && start <= last[1]) last[1] = Math.max(last[1], end)
    else ranges.push([start, end])
    from = end
  }
  if (ranges.length === 0) return [{ text, mark: false }]
  const segments: Segment[] = []
  let cursor = 0
  for (const [start, end] of ranges) {
    if (start > cursor)
      segments.push({ text: text.slice(cursor, start), mark: false })
    segments.push({ text: text.slice(start, end), mark: true })
    cursor = end
  }
  if (cursor < text.length)
    segments.push({ text: text.slice(cursor), mark: false })
  return segments
}

function MarkedSql({ sql, highlight }: { sql: string; highlight?: string }) {
  const segments = useMemo(
    () => markSegments(sql, highlight ?? ''),
    [sql, highlight]
  )
  if (segments.length === 1 && !segments[0].mark) return <SqlTokens sql={sql} />
  return (
    <code className="block whitespace-pre-wrap break-words font-mono text-mono-large">
      {segments.map((segment, index) =>
        segment.mark ? (
          <mark
            key={index}
            className="rounded-sm bg-surface-warning-soft text-inherit outline outline-1 outline-border-warning-soft"
          >
            <SqlTokens sql={segment.text} className="inline" />
          </mark>
        ) : (
          <SqlTokens key={index} sql={segment.text} className="inline" />
        )
      )}
    </code>
  )
}

function UnavailableText({
  reason,
  fingerprint,
  tables,
}: {
  reason?: string | null
  fingerprint: string
  tables?: string[]
}) {
  return (
    <VStack
      className="items-start gap-2 px-4 py-4"
      data-testid="query-text-unavailable"
    >
      <HStack className="items-center gap-2">
        <Icon
          name="eye-off"
          label=""
          aria-hidden="true"
          className="h-4 w-4 shrink-0 text-content-layout-3"
        />
        <Text level="body-medium" className="italic text-content-layout-2">
          Query text unavailable
        </Text>
      </HStack>
      {reason ? (
        <Text level="body-small" className="text-content-layout-3">
          {reason}
        </Text>
      ) : null}
      <HStack className="flex-wrap items-center gap-2">
        <Text level="mono-small" className="text-content-layout-3">
          fingerprint {fingerprint}
        </Text>
        {(tables ?? []).map((table) => (
          <Tag
            key={table}
            size="small"
            variant="neutral"
            modifier="outline"
            label={table}
          />
        ))}
      </HStack>
    </VStack>
  )
}

/**
 * A monitored statement as formatted, highlighted SQL, with the same band anatomy
 * as the Queries page card (line numbers, copy, click to expand). A statement the
 * database did not keep renders a reason and its fingerprint, never a blank band.
 */
export function QueryTextBody({
  text,
  textStatus,
  textReason,
  fingerprint,
  tables,
  highlight,
  maxLines = 4,
  expandable = true,
  copyable = true,
  dialect,
  className,
}: QueryTextBodyProps) {
  const missing = textStatus === 'unavailable' || !text.trim()
  const formatted = useFormatSql(missing ? null : text, dialect, {
    lineWidth: LINE_WIDTH,
    expressionWidth: LINE_WIDTH,
    multilineProjection: true,
  })
  const displaySql = formatted ?? text
  const lines = displaySql.split('\n')
  const matchLine = useMemo(() => {
    const needle = highlight?.trim().toLowerCase()
    if (!needle) return -1
    return lines.findIndex((line) => line.toLowerCase().includes(needle))
  }, [highlight, lines])
  const [expanded, setExpanded] = useState(false)
  const [overflows, setOverflows] = useState(false)
  const bodyRef = useRef<HTMLDivElement>(null)
  const open = expanded || matchLine >= maxLines

  useLayoutEffect(() => {
    const element = bodyRef.current
    if (!element || !expandable || open) return
    setOverflows(element.scrollHeight > element.clientHeight + 1)
  }, [displaySql, expandable, open])

  useEffect(() => {
    const element = bodyRef.current
    if (
      !element ||
      !expandable ||
      open ||
      typeof ResizeObserver === 'undefined'
    )
      return
    const observer = new ResizeObserver(() =>
      setOverflows(element.scrollHeight > element.clientHeight + 1)
    )
    observer.observe(element)
    return () => observer.disconnect()
  }, [expandable, open])

  if (missing)
    return (
      <div className={cn('bg-surface-layout-1/50', className)}>
        <UnavailableText
          reason={textReason}
          fingerprint={fingerprint}
          tables={tables}
        />
      </div>
    )

  const canToggle = expandable && (overflows || expanded)
  const toggle = () => {
    if (!canToggle || window.getSelection()?.toString()) return
    setExpanded((value) => !value)
  }
  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      toggle()
    }
  }

  return (
    <div className={cn('relative bg-surface-layout-1/50', className)}>
      {copyable ? (
        <div className="absolute top-3 right-3 z-10 rounded-lg bg-surface-layout-1 shadow-elevation-1">
          <CopyButton text={text} />
        </div>
      ) : null}
      <div
        {...(canToggle
          ? {
              role: 'button',
              tabIndex: 0,
              'aria-expanded': open,
              'aria-label': open ? 'Collapse SQL' : 'Expand SQL',
              onClick: toggle,
              onKeyDown,
            }
          : {})}
        className={cn(
          'min-w-0 px-4 py-4 focus-visible:outline-none focus-visible:shadow-focus',
          copyable && 'pr-14',
          canToggle && 'cursor-pointer'
        )}
      >
        <div
          ref={bodyRef}
          className={cn(
            'grid grid-cols-[auto_minmax(0,1fr)] items-start gap-3 overflow-hidden',
            expandable && !open ? CLAMP[maxLines] : 'max-h-none'
          )}
          title={text}
        >
          <div
            aria-hidden="true"
            className="select-none border-r-(length:--border-base) border-r-border-layout-1 pr-3 text-right font-mono text-mono-large text-content-layout-3/50"
          >
            {lines.map((_, index) => (
              <span className="block" key={index}>
                {index + 1}
              </span>
            ))}
          </div>
          <MarkedSql sql={displaySql} highlight={highlight} />
        </div>
        {textStatus === 'truncated' ? (
          <HStack className="mt-3 items-center gap-2">
            <Icon
              name="info"
              label=""
              aria-hidden="true"
              className="h-3.5 w-3.5 shrink-0 text-content-layout-3"
            />
            <Text level="caption" className="text-content-layout-3">
              {textReason ?? 'Text was longer than the capture limit.'}
            </Text>
          </HStack>
        ) : null}
      </div>
      {expandable && overflows && !open ? (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-surface-layout-2/50 to-transparent"
        />
      ) : null}
    </div>
  )
}
