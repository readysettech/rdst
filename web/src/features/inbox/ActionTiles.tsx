import { cn } from '@rs/tailwind-base'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Text } from '@rs/ui-new/text'
import { useToast } from '@rs/ui-new/use-toast'
import type { MouseEvent } from 'react'
import type { InboxItem } from '@/lib/monitor/types'
import type { ActionTile, TileKind } from './actionTileModel'
import { routeHref } from './inboxLinks'
import type { InboxActions } from './useInboxActions'

const TRAILING = {
  open: 'arrow-up-right',
  copy: 'copy',
  resolve: 'tick',
} as const

const TILE = cn(
  'group/tile flex w-full min-w-0 items-start gap-3 rounded-panel border border-border-layout-1 bg-surface-layout-1 p-3 text-left outline-none',
  'transition-[background-color,border-color,box-shadow] duration-fast ease-base',
  'hover:bg-surface-raised hover:shadow-elevation-1 focus-visible:shadow-focus'
)

/** Each kind of step keeps its own color: its icon chip, and the border on hover. */
const KIND_CLASS: Record<TileKind, { chip: string; hover: string }> = {
  chart: {
    chip: 'bg-surface-info-soft text-content-info-soft',
    hover: 'hover:border-border-info-soft',
  },
  frame: {
    chip: 'bg-surface-rising-soft text-content-rising-soft',
    hover: 'hover:border-border-rising-soft',
  },
  plan: {
    chip: 'bg-surface-primary-soft text-content-primary-soft',
    hover: 'hover:border-border-primary-soft',
  },
  resolve: {
    chip: 'bg-surface-positive-soft text-content-positive-soft',
    hover: 'hover:border-border-positive-soft',
  },
  copy: {
    chip: 'bg-surface-layout-2 text-content-layout-2',
    hover: 'hover:border-border-layout-2',
  },
}

function TileBody({ tile }: { tile: ActionTile }) {
  const sql = tile.action.kind === 'copy' ? tile.action.text : null
  return (
    <>
      <span
        aria-hidden
        className={cn(
          'flex h-8 w-8 shrink-0 items-center justify-center rounded-control',
          KIND_CLASS[tile.kind].chip
        )}
      >
        <Icon name={tile.icon} label="" className="h-4 w-4" />
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <Text as="span" level="label-medium" className="text-content-layout-1">
          {tile.title}
        </Text>
        {tile.detail && (
          <Text as="span" level="body-small" className="text-content-layout-2">
            {tile.detail}
          </Text>
        )}
        {sql && (
          <code className="min-w-0 truncate font-mono text-mono-small text-content-layout-2">
            {sql}
          </code>
        )}
      </span>
      <Icon
        name={TRAILING[tile.action.kind]}
        label=""
        aria-hidden="true"
        className="mt-0.5 h-3.5 w-3.5 shrink-0 text-content-layout-3 transition-[color,transform] duration-fast group-hover/tile:text-content-primary-soft group-hover/tile:translate-x-0.5"
      />
    </>
  )
}

/** A plain left click stays in the app; modified clicks open the href as usual. */
function inApp(event: MouseEvent, open: () => void) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey)
    return
  event.preventDefault()
  open()
}

/** Next steps as tiles: each one opens the right place, copies SQL or closes the alert. */
export function ActionTiles({
  item,
  tiles,
  actions,
  className,
}: {
  item: InboxItem
  tiles: ActionTile[]
  actions: InboxActions
  className?: string
}) {
  const { toast } = useToast()
  if (tiles.length === 0) return null
  return (
    <section
      aria-label="What you can do"
      className={cn('flex max-w-4xl flex-col gap-1.5', className)}
    >
      <Text as="h4" level="caption" className="text-content-layout-3">
        What you can do
      </Text>
      <ul
        className={cn(
          'grid gap-2',
          tiles.length > 1 && 'tablet:grid-cols-2',
          tiles.length > 2 && 'desktop:grid-cols-3'
        )}
      >
        {tiles.map((tile) => {
          const { action } = tile
          return (
            <li key={tile.id} className="flex min-w-0">
              {action.kind === 'open' ? (
                <a
                  href={routeHref(action.route)}
                  data-tile-kind={tile.kind}
                  className={cn(TILE, KIND_CLASS[tile.kind].hover)}
                  onClick={(event) =>
                    inApp(event, () => actions.openRoute(item, action.route))
                  }
                >
                  <TileBody tile={tile} />
                </a>
              ) : (
                <Pressable
                  data-tile-kind={tile.kind}
                  className={cn(TILE, KIND_CLASS[tile.kind].hover)}
                  aria-label={
                    action.kind === 'copy'
                      ? `${tile.title}: copy ${action.text}`
                      : `${tile.title} ${tile.detail}`
                  }
                  onClick={() => {
                    if (action.kind === 'resolve')
                      actions.close([item], { kind: 'resolve' })
                    else
                      void navigator.clipboard
                        ?.writeText(action.text)
                        .then(() =>
                          toast({ title: 'SQL copied', duration: 2000 })
                        )
                  }}
                >
                  <TileBody tile={tile} />
                </Pressable>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
