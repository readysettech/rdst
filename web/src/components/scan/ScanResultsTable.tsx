/**
 * Results table for Scan — grouped by file, with modal SQL detail view
 * Div-based stacked row layout (no <table>)
 */

import { useState, useMemo, useCallback } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { Button } from '@rs/ui-new/button';
import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { Tag } from '@rs/ui-new/tag';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Card } from '@rs/ui-new/card';
import { Show } from '@rs/ui-new/show';
import { m, AnimatePresence } from '@rs/ui-new/motion';
import { Modal, ModalContent, ModalContentContainer } from '@rs/ui-new/modal';
import { Highlight } from '@rs/ui-new/highlight';
import { SQLDisplay } from '../SQLDisplay';
import { useFormatSql } from '../../lib/useFormatSql';
import { collapseWhitespace } from '../../lib/collapseWhitespace';
import type { ScanQuery, ScanState } from '../../types/scan';

interface ScanResultsTableProps {
  queries: ScanQuery[];
  state: ScanState;
  target: string | null;
}

interface FileGroup {
  file: string;
  queries: ScanQuery[];
}

function groupByFile(queries: ScanQuery[]): FileGroup[] {
  const map = new Map<string, ScanQuery[]>();
  for (const q of queries) {
    const existing = map.get(q.file);
    if (existing) {
      existing.push(q);
    } else {
      map.set(q.file, [q]);
    }
  }
  return Array.from(map.entries()).map(([file, queries]) => ({ file, queries }));
}

function guessOrmLanguage(ormType: string): 'js' | 'python' {
  const pythonOrms = ['sqlalchemy', 'django', 'peewee', 'tortoise'];
  return pythonOrms.some((o) => ormType.toLowerCase().includes(o)) ? 'python' : 'js';
}

function EmptyState({ message, icon }: { message: string; icon: 'search' | 'folder-file' }) {
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

interface QueryDetailModalProps {
  query: ScanQuery | null;
  onClose: () => void;
  onAnalyze: () => void;
}

function QueryDetailModal({ query, onClose, onAnalyze }: QueryDetailModalProps) {
  const formattedSql = useFormatSql(query?.sql ?? null);
  const displaySql = formattedSql ?? query?.sql ?? '';

  return (
    <Modal open={!!query} onOpenChange={(open) => !open && onClose()}>
      <ModalContentContainer open={!!query}>
        <ModalContent size="large" className="p-0 gap-0">
          {query && (
            <>
              {/* Header */}
              <div className="flex items-center justify-between px-5 py-4 border-b border-border-layout-1 bg-surface-layout-1">
                <div className="flex items-center gap-3 min-w-0 flex-1">
                  <div className="p-2 rounded-lg bg-surface-layout-2 shrink-0">
                    <Icon
                      name="layers"
                      label="Query"
                      size="base"
                      className="text-content-layout-2"
                    />
                  </div>
                  <div className="min-w-0">
                    <Text as="h2" level="headline-5" className="text-content-layout-1 truncate">
                      {query.function || query.class || '?'}()
                    </Text>
                    <HStack className="gap-2 items-center">
                      <Text level="caption" className="text-content-layout-3">
                        {query.file}:{query.start_line}
                      </Text>
                      {query.status === 'sql' && (
                        <Tag size="small" variant="positive" modifier="ghost" label="sql" />
                      )}
                      {query.status === 'skipped' && (
                        <Tag size="small" variant="informative" modifier="ghost" label="skip" />
                      )}
                      {query.issues.length > 0 && (
                        <Tag
                          size="small"
                          variant="warning"
                          modifier="ghost"
                          label={`${query.issues.length} issue${query.issues.length === 1 ? '' : 's'}`}
                        />
                      )}
                    </HStack>
                  </div>
                </div>
              </div>

              {/* Content */}
              <div className="p-5 space-y-5 max-h-[60vh] overflow-auto">
                {/* ORM Code */}
                <div>
                  <Text
                    as="label"
                    level="label-small"
                    className="text-content-layout-3 uppercase tracking-wider block mb-2"
                  >
                    ORM Code
                  </Text>
                  <div className="bg-surface-layout-2 rounded-lg p-3 overflow-auto">
                    <Highlight lang={guessOrmLanguage(query.orm_type)}>
                      {query.orm_code}
                    </Highlight>
                  </div>
                </div>

                {/* SQL */}
                {query.sql && (
                  <div>
                    <Text
                      as="label"
                      level="label-small"
                      className="text-content-layout-3 uppercase tracking-wider block mb-2"
                    >
                      SQL
                    </Text>
                    <div className="bg-surface-layout-2 rounded-lg p-3 overflow-auto">
                      <SQLDisplay sql={displaySql} wrap />
                    </div>
                  </div>
                )}

                {/* Issues */}
                {query.issues.length > 0 && (
                  <div>
                    <Text
                      as="label"
                      level="label-small"
                      className="text-content-layout-3 uppercase tracking-wider block mb-2"
                    >
                      Issues
                    </Text>
                    <div className="space-y-1.5">
                      {query.issues.map((issue, i) => (
                        <HStack key={i} className="gap-2 items-start">
                          <Icon
                            name="alert"
                            label="Issue"
                            className="w-3.5 h-3.5 text-content-warning-soft shrink-0 mt-0.5"
                          />
                          <Text level="body-small" className="text-content-layout-2">
                            {issue}
                          </Text>
                        </HStack>
                      ))}
                    </div>
                  </div>
                )}

                {/* Skip reason */}
                {query.status === 'skipped' && query.skip_reason && (
                  <div>
                    <Text
                      as="label"
                      level="label-small"
                      className="text-content-layout-3 uppercase tracking-wider block mb-2"
                    >
                      Skip Reason
                    </Text>
                    <Text level="body-small" className="text-content-layout-3 italic">
                      {query.skip_reason}
                    </Text>
                  </div>
                )}
              </div>

              {/* Footer */}
              <div className="flex justify-end gap-3 px-5 py-4 border-t border-border-layout-1 bg-surface-layout-1">
                <Button
                  variant="primary"
                  modifier="ghost"
                  label="Close"
                  onClick={onClose}
                />
                <Show when={query.status === 'sql' && !!query.sql}>
                  <Button
                    variant="primary"
                    modifier="solid"
                    label="Analyze Query"
                    icon="speedometer"
                    iconPosition="left"
                    onClick={onAnalyze}
                  />
                </Show>
              </div>
            </>
          )}
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  );
}

interface QueryRowProps {
  query: ScanQuery;
  qIdx: number;
  onViewDetail: () => void;
  onAnalyze: () => void;
}

function QueryRow({ query, qIdx, onViewDetail, onAnalyze }: QueryRowProps) {
  const collapsedSql = query.sql ? collapseWhitespace(query.sql) : '';
  const sqlPreview =
    collapsedSql.length > 120 ? `${collapsedSql.slice(0, 120)}...` : collapsedSql;

  return (
    <m.div
      initial={{ opacity: 0, y: -5 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 5 }}
      transition={{ duration: 0.15, delay: qIdx * 0.02 }}
      className="group px-5 py-3.5 hover:bg-surface-layout-2/30 transition-colors"
    >
      {/* Line 1: Identity + metadata + action */}
      <HStack className="justify-between items-center gap-3">
        <HStack className="gap-2.5 items-center min-w-0 flex-1">
          <Text as="span" level="mono-small" className="text-content-layout-2 shrink-0">
            :{query.start_line}
          </Text>
          <Text as="span" level="mono-small" className="text-content-layout-1 truncate">
            {query.function || query.class || '?'}()
          </Text>
          {query.status === 'sql' && (
            <Tag size="small" variant="positive" modifier="ghost" label="sql" />
          )}
          {query.status === 'skipped' && (
            <Tag size="small" variant="informative" modifier="ghost" label="skip" />
          )}
          {query.status === 'pending' && (
            <Tag size="small" variant="informative" modifier="ghost" label="pending" />
          )}
          {query.issues.length > 0 && (
            <Tag
              size="small"
              variant="warning"
              modifier="ghost"
              label={`${query.issues.length} issue${query.issues.length === 1 ? '' : 's'}`}
            />
          )}
        </HStack>
        <div className="shrink-0">
          <Show when={query.status === 'sql' && !!query.sql}>
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              icon="speedometer"
              iconPosition="left"
              label="Analyze"
              onClick={onAnalyze}
            />
          </Show>
        </div>
      </HStack>

      {/* Line 2: SQL preview pill + ORM type */}
      <HStack className="mt-1.5 gap-3 items-center">
        <div className="flex-1 min-w-0">
          {query.status === 'sql' && query.sql ? (
            <button
              type="button"
              onClick={onViewDetail}
              className="text-left bg-surface-layout-2 px-2.5 py-1.5 rounded-lg hover:bg-surface-primary-soft transition-colors cursor-pointer max-w-full overflow-hidden flex items-center gap-2"
              title="View query detail"
            >
              <Icon
                name="eye"
                label="View detail"
                className="w-3 h-3 text-content-layout-3 shrink-0"
              />
              <Text level="mono-small" className="text-content-layout-2 truncate">
                {sqlPreview}
              </Text>
            </button>
          ) : query.status === 'skipped' ? (
            <Text level="mono-small" className="text-content-layout-3 italic truncate block">
              {query.skip_reason || 'Skipped'}
            </Text>
          ) : (
            <Text level="mono-small" className="text-content-layout-3">--</Text>
          )}
        </div>
        <Text as="span" level="caption" className="text-content-layout-3 shrink-0">
          {query.orm_type || query.terminal_method}
        </Text>
      </HStack>
    </m.div>
  );
}

export function ScanResultsTable({ queries, state, target }: ScanResultsTableProps) {
  const navigate = useNavigate();
  const [detailQuery, setDetailQuery] = useState<ScanQuery | null>(null);
  const fileGroups = useMemo(() => groupByFile(queries), [queries]);

  // Track which files are expanded (default: all collapsed)
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set());

  const toggleFile = useCallback((file: string) => {
    setExpandedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(file)) {
        next.delete(file);
      } else {
        next.add(file);
      }
      return next;
    });
  }, []);

  const handleAnalyze = useCallback(
    (query: ScanQuery) => {
      if (!query.sql || query.status !== 'sql') return;
      navigate({
        to: '/results',
        search: { query: query.sql, target: target || undefined },
      });
    },
    [navigate, target]
  );

  if (state === 'idle') {
    return null;
  }

  if (state === 'scanning' && queries.length === 0) {
    return null;
  }

  if (state === 'error') {
    return null;
  }

  if (queries.length === 0 && state === 'complete') {
    return (
      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.2 }}
      >
        <EmptyState
          icon="folder-file"
          message="No ORM queries found in the scanned directory."
        />
      </m.div>
    );
  }

  return (
    <>
      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.2 }}
      >
        <Card className="w-full overflow-hidden">
          <Card.Content className="p-0">
            {/* Section header */}
            <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
              <HStack className="justify-between items-center">
                <HStack className="gap-2 items-center">
                  <Icon name="layers" label="Results" className="w-4 h-4 text-content-layout-3" />
                  <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                    Extracted Queries
                  </Text>
                </HStack>
                <HStack className="gap-2">
                  <Tag
                    size="small"
                    variant="informative"
                    modifier="ghost"
                    label={`${queries.length} quer${queries.length === 1 ? 'y' : 'ies'}`}
                  />
                  <Tag
                    size="small"
                    variant="informative"
                    modifier="ghost"
                    label={`${fileGroups.length} file${fileGroups.length === 1 ? '' : 's'}`}
                  />
                </HStack>
              </HStack>
            </div>

            {/* Query list */}
            <div className="divide-y divide-border-layout-1">
              <AnimatePresence mode="popLayout">
                {fileGroups.map((group) => {
                  const isCollapsed = !expandedFiles.has(group.file);
                  const sqlCount = group.queries.filter(
                    (q) => q.status === 'sql'
                  ).length;

                  return (
                    <div key={`file-${group.file}`}>
                      {/* File group header */}
                      <m.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="bg-surface-layout-2/40 cursor-pointer hover:bg-surface-layout-2/60 transition-colors px-5 py-2.5"
                        onClick={() => toggleFile(group.file)}
                      >
                        <HStack className="gap-2.5 items-center min-w-0">
                          <Icon
                            name={isCollapsed ? 'chevron-right' : 'chevron-down'}
                            label="Toggle"
                            className="w-4 h-4 text-content-layout-3 shrink-0"
                          />
                          <Icon
                            name="folder-file"
                            label="File"
                            className="w-4 h-4 text-content-layout-3 shrink-0"
                          />
                          <Text level="label-small" className="text-content-layout-1 truncate flex-1">
                            {group.file}
                          </Text>
                          <HStack className="gap-1.5 shrink-0">
                            <Tag
                              size="small"
                              variant="informative"
                              modifier="ghost"
                              label={`${group.queries.length} quer${group.queries.length === 1 ? 'y' : 'ies'}`}
                            />
                            {sqlCount < group.queries.length && (
                              <Tag
                                size="small"
                                variant="positive"
                                modifier="ghost"
                                label={`${sqlCount} sql`}
                              />
                            )}
                          </HStack>
                        </HStack>
                      </m.div>

                      {/* Query rows */}
                      {!isCollapsed && (
                        <div className="divide-y divide-border-layout-1/50">
                          {group.queries.map((query, qIdx) => (
                            <QueryRow
                              key={`q-${query.snippet_hash}-${qIdx}`}
                              query={query}
                              qIdx={qIdx}
                              onViewDetail={() => setDetailQuery(query)}
                              onAnalyze={() => handleAnalyze(query)}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </AnimatePresence>
            </div>
          </Card.Content>
        </Card>
      </m.div>

      <QueryDetailModal
        query={detailQuery}
        onClose={() => setDetailQuery(null)}
        onAnalyze={() => {
          if (detailQuery) {
            handleAnalyze(detailQuery);
          }
        }}
      />
    </>
  );
}
