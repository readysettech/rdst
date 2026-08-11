import { Button } from '@rs/ui-new/button'
import { m } from '@rs/ui-new/motion'
import { Show } from '@rs/ui-new/show'
import { Tag } from '@rs/ui-new/tag'
import { QueryCard } from '../../../../components/QueryCard'
import { formatMeta, formatTimestamp } from '../../../../lib/formatters'
import type { QueryRegistryEntry } from '../../../../lib/useQueryRegistry'

interface AnalyzeHistoryListProps {
  queries: QueryRegistryEntry[]
  onSelect: (entry: QueryRegistryEntry) => void
}

export function AnalyzeHistoryList({
  queries,
  onSelect,
}: AnalyzeHistoryListProps) {
  return (
    <div className="space-y-3">
      {queries.map((entry, index) => {
        const meta = formatMeta([
          `last run ${formatTimestamp(entry.last_analyzed)}`,
          entry.frequency > 1 ? `runs ${entry.frequency}` : null,
        ])
        const hasBadges = !!(entry.tag || entry.target)

        return (
          <m.div
            key={entry.hash}
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.2, delay: index * 0.05 }}
          >
            <QueryCard
              data-query-hash={entry.hash}
              sql={entry.sql}
              badges={
                hasBadges ? (
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
                ) : undefined
              }
              meta={meta}
              primaryAction={
                <Button
                  variant="primary"
                  modifier="ghost"
                  size="small"
                  icon="play"
                  iconPosition="left"
                  label="Load query"
                  className="shrink-0"
                  onClick={() => onSelect(entry)}
                />
              }
            />
          </m.div>
        )
      })}
    </div>
  )
}
