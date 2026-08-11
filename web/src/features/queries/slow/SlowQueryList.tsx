import { Button } from '@rs/ui-new/button'
import { AnimatePresence, m } from '@rs/ui-new/motion'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { QueryCard } from '../../../components/QueryCard'
import type { TopQuery } from '../../../types/top'
import { SlowQueryMenu } from './SlowQueryMenu'
import type { SlowQueryRowViewModel } from './slowQueryResultTypes'

interface SlowQueryListProps {
  rows: SlowQueryRowViewModel[]
  onAnalyze: (query: TopQuery) => void
  onCache?: (query: TopQuery) => void
  cachingHash?: string | null
}

export function SlowQueryList({
  rows,
  onAnalyze,
  onCache,
  cachingHash,
}: SlowQueryListProps) {
  return (
    <div className="space-y-3" data-testid="top-query-list">
      <AnimatePresence mode="popLayout">
        {rows.map((row, index) => {
          const { query, hasRunning, meta, cached } = row

          return (
            <m.div
              key={query.query_hash}
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 10 }}
              transition={{ duration: 0.2, delay: index * 0.03 }}
            >
              <QueryCard
                data-testid="top-query-row"
                data-query-hash={query.query_hash}
                className={
                  hasRunning ? 'ring-1 ring-border-positive-soft' : undefined
                }
                sql={query.query_text}
                leading={
                  <Text
                    as="span"
                    level="mono-small"
                    className="w-5 shrink-0 text-right text-content-layout-3 tabular-nums"
                  >
                    {index + 1}
                  </Text>
                }
                badges={
                  hasRunning ? (
                    <Tag
                      size="small"
                      variant="positive"
                      modifier="solid"
                      label={`${query.current_instances_running} running`}
                    />
                  ) : undefined
                }
                meta={meta}
                menu={
                  <SlowQueryMenu
                    query={query}
                    onCache={onCache}
                    caching={cachingHash === query.query_hash}
                    cached={cached}
                  />
                }
                primaryAction={
                  <Button
                    variant="primary"
                    modifier="ghost"
                    size="small"
                    icon="speedometer"
                    iconPosition="left"
                    label="Analyze"
                    onClick={() => onAnalyze(query)}
                  />
                }
              />
            </m.div>
          )
        })}
      </AnimatePresence>
    </div>
  )
}
