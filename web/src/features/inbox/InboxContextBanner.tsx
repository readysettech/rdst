// Why you're here: the Inbox item a Database page link came from, when its change began
// and whether it's still happening. The chart below is zoomed to that window.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { IconButton } from '@rs/ui-new/icon-button'
import { Text } from '@rs/ui-new/text'
import { Link, useRouterState } from '@tanstack/react-router'
import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useRef,
} from 'react'
import type { InboxItem } from '@/lib/monitor/types'
import { SeverityIcon } from '../monitor-kit/SeverityIcon'
import { itemTone, TONE_EDGE_CLASS } from '../monitor-kit/severity'
import { bannerText } from './bannerText'

export function InboxContextBanner({
  item,
  queryKey,
  zoomed,
  onShowDay,
  onDismiss,
  className,
}: {
  item: InboxItem
  /** The query the page opened; a group item's member has its own window. */
  queryKey?: string | null
  /** The chart is zoomed to the window; offers the way back to the full day. */
  zoomed: boolean
  onShowDay: () => void
  onDismiss: () => void
  className?: string
}) {
  // A time frame link from a many-queries alert names its own cluster.
  const zoom = useRouterState({
    select: (state) => (state.location.search as { zoom?: unknown }).zoom,
  })
  const text = bannerText(item, queryKey, undefined, parseZoom(zoom))
  if (!text) return null
  // Still happening keeps the alert's color; over reads green; unknown stays quiet.
  const tone =
    text.tone === 'ongoing'
      ? itemTone(item)
      : text.tone === 'over'
        ? 'positive'
        : 'neutral'
  return (
    <section
      aria-label="From your Inbox"
      data-testid="inbox-context"
      className={cn(
        'flex flex-wrap items-center gap-x-4 gap-y-2 rounded-panel border border-l-2 border-border-layout-1 bg-surface-layout-1 py-3 pl-3 pr-2',
        tone === 'neutral' ? 'border-l-border-layout-2' : TONE_EDGE_CLASS[tone],
        className
      )}
    >
      <div className="flex min-w-0 flex-1 basis-64 items-start gap-3">
        <SeverityIcon type={item.type} tone={tone} size="small" />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <Text level="caption" className="text-content-layout-3">
            From your Inbox{' '}
            <Link
              to="/inbox"
              search={{ item: item.id }}
              className="rounded-control text-content-layout-2 underline decoration-content-layout-3/50 underline-offset-4 outline-none hover:text-content-primary-soft focus-visible:shadow-focus"
            >
              {item.title}
            </Link>
          </Text>
          <Text level="body-medium" className="text-content-layout-1">
            <span className="font-medium">{text.lead}</span>
            {text.state && (
              <span
                data-testid="inbox-context-state"
                className={cn(
                  'ml-1.5',
                  text.tone === 'over'
                    ? 'text-content-positive-soft'
                    : 'text-content-layout-2'
                )}
              >
                {text.state}
              </span>
            )}
          </Text>
          {text.note && (
            <Text level="body-small" className="text-content-layout-3">
              {text.note}
            </Text>
          )}
        </div>
      </div>
      <div className="ml-auto flex shrink-0 items-center gap-1">
        {zoomed && (
          <Button
            label="Show the last 24 hours"
            modifier="ghost"
            size="small"
            onClick={onShowDay}
          />
        )}
        <IconButton
          icon="close"
          label="Dismiss"
          modifier="ghost"
          size="small"
          onClick={onDismiss}
        />
      </div>
    </section>
  )
}

function parseZoom(value: unknown): [number, number] | null {
  const match = typeof value === 'string' ? /^(\d+)-(\d+)$/.exec(value) : null
  return match ? [Number(match[1]), Number(match[2])] : null
}

/** The banner for the query a page opened, so its detail can show it at the top. */
export const QueryContextBanner = createContext<{
  queryKey: string
  banner: ReactNode
  /** Tells the page the detail shows the banner, so it leaves its own out. */
  onShown: (shown: boolean) => void
} | null>(null)

/** Clear of the page's sticky list toolbar. */
const TOOLBAR_PX = 160

/**
 * Renders the page's Inbox banner when it belongs to this query; until a detail shows
 * it, the page shows it above the chart. Arriving from the Inbox, the banner is brought
 * into view once the list has settled, so the page opens on why you're here.
 */
export function QueryContextSlot({ queryKey }: { queryKey: string }) {
  const context = useContext(QueryContextBanner)
  const ref = useRef<HTMLDivElement>(null)
  const shown = context?.queryKey === queryKey
  const onShown = context?.onShown
  useEffect(() => {
    if (!shown || !onShown) return
    onShown(true)
    return () => onShown(false)
  }, [shown, onShown])
  useEffect(() => {
    if (!shown) return
    const timer = window.setTimeout(() => {
      const box = ref.current?.getBoundingClientRect()
      if (box && (box.top < TOOLBAR_PX || box.bottom > window.innerHeight))
        ref.current?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
    }, 600)
    return () => window.clearTimeout(timer)
  }, [shown])
  return shown ? <div ref={ref}>{context.banner}</div> : null
}
