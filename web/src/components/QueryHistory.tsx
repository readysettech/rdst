import { Button } from '@rs/ui-new/button';
import { Show } from '@rs/ui-new/show';
import { Tag } from '@rs/ui-new/tag';
import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Card } from '@rs/ui-new/card';
import { m } from '@rs/ui-new/motion';
import { SQLDisplay } from './SQLDisplay';
import type { QueryRegistryEntry } from '../lib/useQueryRegistry';
import { formatTimestamp } from '../lib/formatters';

interface QueryHistoryProps {
  queries: QueryRegistryEntry[];
  onSelect: (sql: string) => void;
}

function truncateQuery(query: string, maxLength = 80): string {
  const singleLine = query.replace(/\s+/g, ' ').trim();
  if (singleLine.length <= maxLength) return singleLine;
  return singleLine.slice(0, maxLength) + '...';
}

export function QueryHistory({ queries, onSelect }: QueryHistoryProps) {
  const displayQueries = queries.slice(0, 5);

  if (displayQueries.length === 0) {
    return (
      <Card className="w-full">
        <Card.Content className="py-12">
          <VStack className="gap-4 items-center">
            <div className="w-14 h-14 rounded-2xl bg-surface-layout-2 flex items-center justify-center">
              <Icon name="folder-file" label="No history" className="w-7 h-7 text-content-layout-3" />
            </div>
            <VStack className="gap-1 items-center">
              <Text level="headline-5" className="text-content-layout-2">
                No recent queries
              </Text>
              <Text level="body-small" className="text-content-layout-3 text-center max-w-sm">
                Your analyzed queries will appear here for quick access
              </Text>
            </VStack>
          </VStack>
        </Card.Content>
      </Card>
    );
  }

  return (
    <div className="space-y-3 w-full">
      {/* Header */}
      <HStack className="justify-between items-center px-1">
        <HStack className="gap-2 items-center">
          <Icon name="folder-file" label="Recent queries" className="w-4 h-4 text-content-layout-3" />
          <Text level="label-small" className="text-content-layout-2">
            Recent Queries
          </Text>
        </HStack>
        <Show when={queries.length > 5}>
          <Tag
            variant="informative"
            modifier="ghost"
            size="small"
            label={`Showing 5 of ${queries.length}`}
          />
        </Show>
      </HStack>

      {/* Query list */}
      <div className="space-y-2">
        {displayQueries.map((entry, index) => (
          <m.div
            key={entry.hash}
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.2, delay: index * 0.05 }}
          >
            <Card
              className="w-full group hover:border-border-primary-soft transition-colors cursor-pointer"
              clickable
              onClick={() => onSelect(entry.sql)}
            >
              <Card.Content className="p-4">
                <HStack className="gap-4 items-start">
                  {/* Query content */}
                  <VStack className="gap-2 flex-1 min-w-0 items-start">
                    {/* SQL Preview */}
                    <div className="w-full overflow-hidden rounded-lg bg-surface-layout-2 px-3 py-2">
                      <SQLDisplay
                        sql={truncateQuery(entry.sql)}
                        className="text-sm"
                        wrap={false}
                      />
                    </div>

                    {/* Metadata row */}
                    <HStack className="gap-3 items-center flex-wrap">
                      {/* Timestamp */}
                      <HStack className="gap-1.5 items-center">
                        <Icon name="observe" label="Time" className="w-3.5 h-3.5 text-content-layout-3" />
                        <Text level="caption" className="text-content-layout-3">
                          {formatTimestamp(entry.last_analyzed)}
                        </Text>
                      </HStack>

                      {/* Tags */}
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

                      {/* Frequency badge */}
                      <Show when={entry.frequency > 1}>
                        <HStack className="gap-1 items-center px-2 py-0.5 rounded-md bg-surface-layout-2">
                          <Icon name="layers" label="Frequency" className="w-3 h-3 text-content-layout-3" />
                          <Text level="caption" className="text-content-layout-3 font-medium">
                            ×{entry.frequency}
                          </Text>
                        </HStack>
                      </Show>
                    </HStack>
                  </VStack>

                  {/* Action button */}
                  <Button
                    variant="primary"
                    modifier="ghost"
                    size="small"
                    icon="play"
                    iconPosition="icon"
                    label="Use query"
                    className="shrink-0"
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelect(entry.sql);
                    }}
                  />
                </HStack>
              </Card.Content>
            </Card>
          </m.div>
        ))}
      </div>
    </div>
  );
}
