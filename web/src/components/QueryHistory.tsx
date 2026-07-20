import { Button } from '@rs/ui-new/button';
import { Scrollable } from '@rs/ui-new/scrollable';
import { Show } from '@rs/ui-new/show';
import { Tag } from '@rs/ui-new/tag';
import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Card } from '@rs/ui-new/card';
import { m } from '@rs/ui-new/motion';
import { Link } from '@tanstack/react-router';
import { SQLDisplay } from './SQLDisplay';
import type { QueryRegistryEntry } from '../lib/useQueryRegistry';
import { formatTimestamp } from '../lib/formatters';

interface QueryHistoryProps {
  queries: QueryRegistryEntry[];
  onSelect: (sql: string) => void;
}

// Recent is a quiet, tertiary list: only the 5 most recent stay in the default
// view; everything else is one click away behind "View all →". [VIS-011, USE-014]
const RECENT_LIMIT = 5;

export function QueryHistory({ queries, onSelect }: QueryHistoryProps) {
  const displayQueries = queries.slice(0, RECENT_LIMIT);

  // First-run / empty state: no inert "Recent" header — just an illustrated
  // block so the feature reads as intentional, not a blank slot. [VIS-102, VIS-103]
  if (queries.length === 0) {
    return (
      <Card className="w-full">
        <Card.Content className="py-12">
          <VStack className="gap-4 items-center">
            <div className="w-14 h-14 rounded-2xl bg-surface-layout-2 flex items-center justify-center">
              <Icon name="folder-file" label="No history" className="w-7 h-7 text-content-layout-3" />
            </div>
            <VStack className="gap-1 items-center">
              <Text level="headline-5" className="text-content-layout-2">
                Your analyzed queries will show up here
              </Text>
              <Text level="body-small" className="text-content-layout-3 text-center max-w-sm">
                Paste a query above and hit Analyze — recent runs land here for quick re-use.
              </Text>
            </VStack>
          </VStack>
        </Card.Content>
      </Card>
    );
  }

  return (
    <div className="space-y-3 w-full">
      {/* Header — only shown once there is content to act on */}
      <HStack className="justify-between items-center px-1">
        <HStack className="gap-2 items-center">
          <Icon name="folder-file" label="Recent queries" className="w-4 h-4 text-content-layout-3" />
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
                    <div className="w-full rounded-lg bg-surface-layout-2">
                      <Scrollable className="max-h-32">
                        <div className="px-3 py-2">
                          <SQLDisplay
                            sql={entry.sql}
                            className="text-sm"
                            wrap
                            showCopy
                          />
                        </div>
                      </Scrollable>
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
