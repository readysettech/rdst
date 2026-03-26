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
import { CacheButton } from '../CacheButton';
import type { TopQuery, TopState } from '../../types/top';

interface TopQueryTableProps {
  queries: TopQuery[];
  state: TopState;
  isRealtime: boolean;
  onAnalyze: (query: TopQuery) => void;
  onCache?: (query: TopQuery) => void;
  cachingHash?: string | null;
  isCached?: (sql: string) => boolean;
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
  onCache,
  cachingHash,
  isCached,
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

          {/* Query rows */}
          <div className="divide-y divide-border-layout-1">
            <AnimatePresence mode="popLayout">
              {queries.map((query, index) => {
                const isExpanded = expandedHash === query.query_hash;
                const hasRunning = (query.current_instances_running ?? 0) > 0;
                const collapsedPreview = getCollapsedPreview(query.query_text);

                return (
                  <m.div
                    key={query.query_hash}
                    initial={{ opacity: 0, y: -10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: 10 }}
                    transition={{ duration: 0.2, delay: index * 0.03 }}
                    className={`group px-5 py-3.5 transition-colors ${hasRunning ? 'bg-surface-positive-soft/20' : 'hover:bg-surface-layout-2/30'}`}
                  >
                    {/* Row 1: Hash + Actions */}
                    <HStack className="justify-between items-center gap-3">
                      <HStack className="gap-2 items-center">
                        <Text as="span" level="mono-small" className="text-content-layout-3">
                          {query.query_hash.slice(0, 8)}
                        </Text>
                        <Show when={hasRunning}>
                          <Tag size="small" variant="positive" modifier="solid" label={`${query.current_instances_running} running`} />
                        </Show>
                      </HStack>
                      <div className="shrink-0 flex gap-1 items-center">
                        {onCache && (
                          <CacheButton
                            cached={!!isCached?.(query.query_text)}
                            loading={cachingHash === query.query_hash}
                            onClick={() => onCache(query)}
                          />
                        )}
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
                    </HStack>

                    {/* Row 2: SQL */}
                    <button
                      type="button"
                      onClick={() => setExpandedHash(isExpanded ? null : query.query_hash)}
                      className="mt-2 text-left bg-surface-layout-2 px-3 py-2 rounded-lg hover:ring-1 hover:ring-border-primary-soft transition-all cursor-pointer w-full block"
                      title={query.query_text}
                    >
                      <SQLDisplay
                        sql={
                          isExpanded
                            ? query.query_text
                            : collapsedPreview.length > 120
                              ? `${collapsedPreview.slice(0, 120)}...`
                              : collapsedPreview
                        }
                        wrap={isExpanded}
                        showCopy={isExpanded}
                      />
                    </button>

                    {/* Row 3: Stats */}
                    <HStack className="mt-2 gap-2 flex-wrap items-center">
                      <Tag
                        size="small"
                        variant="informative"
                        modifier="ghost"
                        label={`Freq: ${isRealtime && query.observation_count !== undefined ? query.observation_count : query.freq}`}
                      />
                      <Tag
                        size="small"
                        variant="warning"
                        modifier="ghost"
                        label={`${isRealtime ? 'Max' : 'Total'}: ${isRealtime && query.max_duration_ms !== undefined ? `${query.max_duration_ms.toFixed(1)}ms` : query.total_time}`}
                      />
                      <Tag size="small" variant="warning" modifier="ghost" label={`Avg: ${query.avg_time}`} />
                      <Tag size="small" variant="primary" modifier="ghost" label={`Load: ${query.pct_load}`} />
                      <Show when={isRealtime && query.qps !== undefined}>
                        <Tag size="small" variant="informative" modifier="ghost" label={`QPS: ${query.qps?.toFixed(2)}`} />
                      </Show>
                    </HStack>
                  </m.div>
                );
              })}
            </AnimatePresence>
          </div>
        </Card.Content>
      </Card>
    </m.div>
  );
}
