import { cn } from '@rs/tailwind-base'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import type { InboxItem } from '@/lib/monitor/types'
import {
  bandOf,
  kindOf,
  queueStamp,
  queueTime,
  rowMeta,
  runningText,
} from './inboxModel'

/**
 * The severity mark: the only red on the queue is the Act now dot; Watch points on, a closed
 * item carries the tick, and a change that did not move is neutral.
 */
function SeverityMark({ item }: { item: InboxItem }) {
  const band = bandOf(item)
  return (
    <span
      aria-hidden="true"
      className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center"
    >
      {band === 'act_now' ? (
        <span className="h-2 w-2 rounded-pill bg-content-negative-plain" />
      ) : band === 'watch' ? (
        <Icon
          name="arrow-right"
          label=""
          size="small"
          className="text-content-layout-3"
        />
      ) : kindOf(item) === 'no_change_yet' ? (
        <Icon
          name="minus"
          label=""
          size="small"
          className="text-content-layout-3"
        />
      ) : (
        <Icon
          name="tick"
          label=""
          size="small"
          className="text-content-positive-soft"
        />
      )}
    </span>
  )
}

interface InboxItemRowProps {
  item: InboxItem
  selected: boolean
  now: number
  onSelect: (item: InboxItem) => void
  /** Arrived on the live stream after the first load: flashes once. */
  fresh?: boolean
}

/** One compact queue row: the mark, the title and one line of meta, the time over the database. */
export function InboxItemRow({
  item,
  selected,
  now,
  onSelect,
  fresh = false,
}: InboxItemRowProps) {
  const band = bandOf(item)
  const live = runningText(item, now)
  return (
    <Pressable
      data-inbox-item={item.id}
      aria-current={selected ? 'true' : undefined}
      aria-label={item.title}
      onClick={() => onSelect(item)}
      className={cn(
        'mx-2 grid w-[calc(100%-1rem)] grid-cols-[18px_1fr_auto] items-start gap-3 rounded-panel py-3 pr-5 pl-6 text-left transition-colors duration-fast ease-base hover:bg-surface-primary-soft',
        selected && 'bg-surface-primary-soft',
        fresh && 'inbox-fresh'
      )}
    >
      <SeverityMark item={item} />
      <span className="min-w-0">
        <span
          className={cn(
            'block truncate text-body-small',
            band === 'closed'
              ? 'text-content-layout-2'
              : 'font-medium text-content-layout-1'
          )}
        >
          {item.title}
        </span>
        <span className="mt-0.5 flex flex-wrap items-center gap-x-2 text-caption text-content-layout-3">
          <span className="truncate">{rowMeta(item)}</span>
          {live && (
            <span className="inline-flex items-center gap-1.5 text-content-negative-soft">
              <span
                aria-hidden="true"
                className="h-1.5 w-1.5 rounded-pill bg-content-negative-plain"
              />
              {live}
            </span>
          )}
        </span>
      </span>
      <span className="flex flex-col items-end pt-0.5 text-right">
        <span className="text-label-extra-small font-normal tabular-nums text-content-layout-3">
          {queueTime(queueStamp(item), now)}
        </span>
        <span
          data-testid="inbox-item-database"
          className="mt-0.5 whitespace-nowrap text-caption text-content-layout-3"
        >
          {item.target}
        </span>
      </span>
    </Pressable>
  )
}
