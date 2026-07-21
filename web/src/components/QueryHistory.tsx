import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { Icon } from '@rs/ui-new/icon'
import { m } from '@rs/ui-new/motion'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { Link } from '@tanstack/react-router'
import { formatMeta, formatTimestamp } from '../lib/formatters'
import type { QueryRegistryEntry } from '../lib/useQueryRegistry'
import { QueryCard } from './QueryCard'

interface QueryHistoryProps {
  queries: QueryRegistryEntry[]
  onSelect: (sql: string) => void
}

// Recent is a quiet, tertiary list: only the 5 most recent stay in the default
// view; everything else is one click away behind "View all →". [VIS-011, USE-014]
const RECENT_LIMIT = 5

export function QueryHistory({ queries, onSelect }: QueryHistoryProps) {
  const displayQueries = queries.slice(0, RECENT_LIMIT)

  // First-run / empty state: no inert "Recent" header — just an illustrated
  // block so the feature reads as intentional, not a blank slot. [VIS-102, VIS-103]
  if (queries.length === 0) {
    return (
      <Card className="w-full">
        <Card.Content className="py-12">
          <VStack className="gap-4 items-center">
            <div className="w-14 h-14 rounded-2xl bg-surface-layout-2 flex items-center justify-center">
              <Icon
                name="folder-file"
                label="No history"
                className="w-7 h-7 text-content-layout-3"
              />
            </div>
            <VStack className="gap-1 items-center">
              <Text level="headline-5" className="text-content-layout-2">
                Your analyzed queries will show up here
              </Text>
              <Text
                level="body-small"
                className="text-content-layout-3 text-center max-w-sm"
              >
                Paste a query above and hit Analyze — recent runs land here for
                quick re-use.
              </Text>
            </VStack>
          </VStack>
        </Card.Content>
      </Card>
    )
  }

  return (
    // The whole Recent section (header + list) sits in ONE container panel that
    // matches the Saved Queries panel, so the cards keep the standard card colour
    // against it — one global card surface, separated by the panel step, not by a
    // per-page card tint. [owner: match Saved Queries; USE-097, VIS-104]
    <Card className="w-full overflow-hidden">
      <Card.Content className="p-0">
        {/* Header — only shown once there is content to act on */}
        <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
          <HStack className="justify-between items-center">
            <HStack className="gap-2 items-center">
              <Icon
                name="folder-file"
                label="Recent queries"
                className="w-4 h-4 text-content-layout-3"
              />
              <Text level="label-small" className="text-content-layout-2">
                Recent
              </Text>
              <Tag
                size="small"
                variant="informative"
                modifier="ghost"
                label={`${queries.length}`}
              />
            </HStack>
            <Link
              to="/query-registry"
              className="text-sm text-content-primary-soft hover:underline whitespace-nowrap"
            >
              View all →
            </Link>
          </HStack>
        </div>

        {/* Query list — the cards sit on a lighter inner container so the
            standard card colour steps off it (same as Saved Queries). */}
        <div className="p-3 space-y-2 bg-surface-layout-1">
          {displayQueries.map((entry, index) => {
            // Recent is a quiet, clickable list; the whole card selects the query
            // into the editor (owner's model: some cards clickable). The metrics
            // fold into one muted meta line; the card's built-in top-right Copy is
            // the single copy affordance, and "Use query" is the one footer action.
            // [VIS-111, USE-097, USE-002/003]
            const meta = formatMeta([
              `last run ${formatTimestamp(entry.last_analyzed)}`,
              entry.frequency > 1 ? `runs ${entry.frequency}` : null,
            ])
            return (
              <m.div
                key={entry.hash}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.2, delay: index * 0.05 }}
              >
                <QueryCard
                  data-query-hash={entry.hash}
                  clickable
                  onClick={() => onSelect(entry.sql)}
                  sql={entry.sql}
                  badges={
                    <>
                      <Show when={entry.tag}>
                        <Tag
                          size="small"
                          variant="primary"
                          modifier="ghost"
                          label={entry.tag}
                        />
                      </Show>
                      <Show when={entry.target}>
                        <Tag
                          size="small"
                          variant="informative"
                          modifier="ghost"
                          label={entry.target}
                        />
                      </Show>
                    </>
                  }
                  meta={meta}
                  actions={
                    <Button
                      variant="primary"
                      modifier="ghost"
                      size="small"
                      icon="play"
                      iconPosition="left"
                      label="Use query"
                      className="shrink-0"
                      onClick={(e) => {
                        e.stopPropagation()
                        onSelect(entry.sql)
                      }}
                    />
                  }
                />
              </m.div>
            )
          })}
        </div>
      </Card.Content>
    </Card>
  )
}
