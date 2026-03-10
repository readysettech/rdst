/**
 * Table displaying top queries with actions
 */

import { useState } from 'react';
import { Button } from '@rs/ui-new/button';
import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { Tag } from '@rs/ui-new/tag';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Card } from '@rs/ui-new/card';
import { Show } from '@rs/ui-new/show';
import { m, AnimatePresence } from '@rs/ui-new/motion';
import { SQLDisplay } from '../SQLDisplay';
import type { TopQuery, TopState } from '../../types/top';

interface TopQueryTableProps {
  queries: TopQuery[];
  state: TopState;
  isRealtime: boolean;
  onAnalyze: (query: TopQuery) => void;
}

function getCollapsedPreview(sql: string): string {
  return sql.replace(/\s+/g, ' ').trim();
}

function EmptyState({ message, icon }: { message: string; icon: "observe" | "speedometer" | "folder-file" }) {
  return (
    <Card className="w-full">
      <Card.Content className="py-16">
        <VStack className="gap-4 items-center">
          <div className="w-14 h-14 rounded-2xl bg-surface-layout-2 flex items-center justify-center">
            <Icon name={icon} label="Empty" className="w-7 h-7 text-content-layout-3" />
          </div>
          <Text level="body-small" className="text-content-layout-3 text-center max-w-md">
            {message}
          </Text>
        </VStack>
      </Card.Content>
    </Card>
  );
}

export function TopQueryTable({
  queries,
  state,
  isRealtime,
  onAnalyze,
}: TopQueryTableProps) {
  const [expandedHash, setExpandedHash] = useState<string | null>(null);

  if (state === 'idle') {
    return (
      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.2 }}
      >
        <EmptyState
          icon="observe"
          message="Configure options and click 'Get Top Queries' or 'Start Monitoring' to begin analyzing your database queries."
        />
      </m.div>
    );
  }

  if (state === 'loading') {
    return (
      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.2 }}
      >
        <EmptyState
          icon="speedometer"
          message="Loading queries from your database..."
        />
      </m.div>
    );
  }

  if (state === 'error') {
    return null;
  }

  if (queries.length === 0 && (state === 'complete' || state === 'streaming')) {
    return (
      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.2 }}
      >
        <EmptyState
          icon="folder-file"
          message="No queries found. Try adjusting the filter or waiting for more activity."
        />
      </m.div>
    );
  }

  return (
    <m.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.2 }}
    >
      <Card className="w-full overflow-hidden">
        <Card.Content className="p-0">
          {/* Table header */}
          <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
            <HStack className="justify-between items-center">
              <HStack className="gap-2 items-center">
                <Icon name="layers" label="Results" className="w-4 h-4 text-content-layout-3" />
                <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                  Query Results
                </Text>
              </HStack>
              <Tag
                size="small"
                variant="informative"
                modifier="ghost"
                label={`${queries.length} quer${queries.length === 1 ? 'y' : 'ies'}`}
              />
            </HStack>
          </div>

          {/* Table */}
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="bg-surface-layout-2/30">
                  <th className="px-4 py-3 text-left text-xs text-content-layout-3 uppercase tracking-wider font-medium w-10">
                    #
                  </th>
                  <th className="px-4 py-3 text-left text-xs text-content-layout-3 uppercase tracking-wider font-medium w-24">
                    Hash
                  </th>
                  <th className="px-4 py-3 text-left text-xs text-content-layout-3 uppercase tracking-wider font-medium">
                    Query
                  </th>
                  <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-20">
                    {isRealtime ? 'Obs' : 'Freq'}
                  </th>
                  <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-24">
                    {isRealtime ? 'Max Dur' : 'Total'}
                  </th>
                  <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-20">
                    Avg
                  </th>
                  <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-16">
                    Load
                  </th>
                  <Show when={isRealtime}>
                    <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-16">
                      Now
                    </th>
                  </Show>
                  <th className="px-4 py-3 text-right text-xs text-content-layout-3 uppercase tracking-wider font-medium w-28">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-layout-1">
                <AnimatePresence mode="popLayout">
                  {queries.map((query, index) => {
                    const isExpanded = expandedHash === query.query_hash;
                    const hasRunning = (query.current_instances_running ?? 0) > 0;
                    const collapsedPreview = getCollapsedPreview(query.query_text);

                    return (
                      <m.tr
                        key={query.query_hash}
                        initial={{ opacity: 0, y: -10 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: 10 }}
                        transition={{ duration: 0.2, delay: index * 0.03 }}
                        className={`group transition-colors ${hasRunning ? 'bg-surface-positive-soft/20' : 'hover:bg-surface-layout-2/50'}`}
                      >
                        <td className="px-4 py-3 align-top">
                          <Text as="span" level="mono-small" className="text-content-layout-3">
                            {index + 1}
                          </Text>
                        </td>
                        <td className="px-4 py-3 align-top">
                          <Text as="span" level="mono-small" className="text-content-layout-2">
                            {query.query_hash.slice(0, 8)}
                          </Text>
                        </td>
                        <td className="px-4 py-3 align-top">
                          <button
                            type="button"
                            onClick={() =>
                              setExpandedHash(isExpanded ? null : query.query_hash)
                            }
                            className="text-left bg-surface-layout-2 px-3 py-2 rounded-lg hover:bg-surface-primary-soft transition-colors cursor-pointer max-w-lg block"
                            title={query.query_text}
                          >
                            <SQLDisplay
                              sql={
                                isExpanded
                                  ? query.query_text
                                  : collapsedPreview.length > 80
                                    ? `${collapsedPreview.slice(0, 80)}...`
                                    : collapsedPreview
                              }
                              wrap={isExpanded}
                            />
                          </button>
                        </td>
                        <td className="px-4 py-3 align-top text-right">
                          <Text as="span" level="mono-small" className="text-content-layout-1">
                            {isRealtime && query.observation_count !== undefined
                              ? query.observation_count
                              : query.freq}
                          </Text>
                        </td>
                        <td className="px-4 py-3 align-top text-right">
                          <Text as="span" level="mono-small" className="text-content-layout-1">
                            {isRealtime && query.max_duration_ms !== undefined
                              ? `${query.max_duration_ms.toFixed(1)}ms`
                              : query.total_time}
                          </Text>
                        </td>
                        <td className="px-4 py-3 align-top text-right">
                          <Text as="span" level="mono-small" className="text-content-layout-1">
                            {query.avg_time}
                          </Text>
                        </td>
                        <td className="px-4 py-3 align-top text-right">
                          <Text as="span" level="mono-small" className="text-content-layout-1">
                            {query.pct_load}
                          </Text>
                        </td>
                        <Show when={isRealtime}>
                          <td className="px-4 py-3 align-top text-right">
                            <Show when={hasRunning}>
                              <Tag
                                size="small"
                                variant="positive"
                                modifier="solid"
                                label={String(query.current_instances_running)}
                              />
                            </Show>
                            <Show when={!hasRunning}>
                              <Text as="span" level="mono-small" className="text-content-layout-3">
                                -
                              </Text>
                            </Show>
                          </td>
                        </Show>
                        <td className="px-4 py-3 align-top">
                          <div className="flex justify-end">
                            <Button
                              variant="primary"
                              modifier="ghost"
                              size="small"
                              icon="speedometer"
                              iconPosition="left"
                              label="Analyze"
                              onClick={() => onAnalyze(query)}
                            />
                          </div>
                        </td>
                      </m.tr>
                    );
                  })}
                </AnimatePresence>
              </tbody>
            </table>
          </div>
        </Card.Content>
      </Card>
    </m.div>
  );
}
