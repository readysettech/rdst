import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { BaseInputText } from "@rs/ui-new/base-input-text";
import { Button } from "@rs/ui-new/button";
import { Card } from "@rs/ui-new/card";
import { Icon } from "@rs/ui-new/icon";
import { Show } from "@rs/ui-new/show";
import { Tag } from "@rs/ui-new/tag";
import { Text } from "@rs/ui-new/text";
import { HStack, VStack } from "@rs/ui-new/stack";
import { m, AnimatePresence } from "@rs/ui-new/motion";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@rs/ui-new/tooltip";
import { useQueryRegistry } from "../lib/useQueryRegistry";
import { SQLInput } from "../components/SQLInput";
import { SQLDisplay } from "../components/SQLDisplay";
import { useTarget } from "../hooks/useTarget";
import { useCacheAction } from "../lib/useCacheAction";
import { CacheButton } from "../components/CacheButton";
import { collapseWhitespace } from "../lib/collapseWhitespace";

export const Route = createFileRoute("/query-registry")({
  component: QueryRegistryPage,
});

import { formatTimestamp, formatDuration } from "../lib/formatters";

function formatSource(source: string): string {
  switch (source) {
    case "analyze":
      return "Analyze";
    case "prompt":
      return "Ask";
    case "web":
      return "Web";
    case "top":
      return "Top";
    case "file":
      return "File";
    case "manual":
      return "Manual";
    default:
      return source || "Manual";
  }
}

function getCollapsedPreview(sql: string, maxLen = 120): string {
  const collapsed = collapseWhitespace(sql);
  return collapsed.length > maxLen ? `${collapsed.slice(0, maxLen)}...` : collapsed;
}

function getSourceVariant(source: string): "positive" | "warning" | "informative" | "primary" {
  switch (source) {
    case "top":
      return "warning";
    case "analyze":
      return "primary";
    case "prompt":
      return "positive";
    default:
      return "informative";
  }
}

function QueryRegistryPage() {
  const navigate = useNavigate();
  const {
    queries,
    isLoading,
    isFetching,
    total,
    offset,
    nextPage,
    prevPage,
    resetPagination,
    removeQuery,
    updateTag,
    addMutation: addQueryMutation,
  } = useQueryRegistry(150);
  const [searchTerm, setSearchTerm] = useState("");
  const [editingHash, setEditingHash] = useState<string | null>(null);
  const [tagDraft, setTagDraft] = useState("");
  const [confirmingHash, setConfirmingHash] = useState<string | null>(null);
  const [newSql, setNewSql] = useState("");
  const [showAddForm, setShowAddForm] = useState(false);
  const [expandedHash, setExpandedHash] = useState<string | null>(null);
  const { target } = useTarget();

  // Cache integration
  const { cacheQuery, cachingId: cachingHash, isCached } = useCacheAction({ target });

  const handleCacheQuery = (hash: string, sql: string) => {
    cacheQuery(sql, hash);
  };

  const filteredQueries = useMemo(() => {
    if (!searchTerm.trim()) return queries;
    const lowerTerm = searchTerm.toLowerCase();
    return queries.filter(
      (entry) =>
        entry.tag?.toLowerCase().includes(lowerTerm) ||
        entry.sql.toLowerCase().includes(lowerTerm)
    );
  }, [queries, searchTerm]);

  const handleCreate = () => {
    if (!newSql.trim()) return;
    addQueryMutation.mutate(
      { sql: newSql, target: target || undefined },
      {
        onSuccess: () => {
          setNewSql("");
          setShowAddForm(false);
        },
      },
    );
  };

  const handleAnalyze = (sql: string, target?: string, mostRecentParams?: Record<string, string | number>) => {
    navigate({
      to: "/results",
      search: {
        query: sql.trim(),
        target: target || undefined,
        params: mostRecentParams && Object.keys(mostRecentParams).length > 0
          ? JSON.stringify(mostRecentParams)
          : undefined,
      },
    });
  };

  const pageStart = total === 0 ? 0 : offset + 1;
  const pageEnd = offset + queries.length;
  const hasPrevPage = offset > 0;
  const hasNextPage = pageEnd < total;

  return (
    <div className="space-y-6 w-full">
      {/* Hero Header */}
      <m.div
        className="space-y-4"
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className="justify-between items-start">
          <HStack className="gap-4 items-center">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center">
              <Icon name="folder-file" label="Query Registry" className="w-6 h-6 text-content-primary-soft" />
            </div>
            <VStack className="gap-1 items-start">
              <Text as="h1" level="headline-3" className="text-content-layout-1">
                Query Registry
              </Text>
              <Text level="body-small" className="text-content-layout-3">
                Saved queries from analysis sessions. Use the Analyze action to run a query.
              </Text>
            </VStack>
          </HStack>

          <Show when={!showAddForm}>
            <Button
              variant="primary"
              modifier="solid"
              label="Add Query"
              icon="add"
              iconPosition="left"
              onClick={() => setShowAddForm(true)}
            />
          </Show>
        </HStack>
      </m.div>

      {/* Add Query Form */}
      <AnimatePresence>
        {showAddForm && (
          <m.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3 }}
          >
            <Card className="w-full overflow-hidden">
              <Card.Content className="p-0">
                <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
                  <HStack className="gap-2 items-center">
                    <Icon name="add" label="Add" className="w-4 h-4 text-content-layout-3" />
                    <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                      Add New Query
                    </Text>
                  </HStack>
                </div>
                <div className="p-5">
                  <SQLInput
                    value={newSql}
                    onChange={setNewSql}
                    placeholder="Enter your SQL query..."
                    minHeight="12rem"
                    target={target}
                    showPrettify
                  />
                </div>
                <div className="px-5 py-4 border-t border-border-layout-1 bg-surface-layout-1">
                  <HStack className="justify-end gap-2">
                    <Button
                      variant="primary"
                      modifier="ghost"
                      label="Cancel"
                      onClick={() => {
                        setShowAddForm(false);
                        setNewSql("");
                      }}
                    />
                    <Button
                      variant="rising"
                      modifier="solid"
                      label="Save Query"
                      icon="tick"
                      iconPosition="left"
                      onClick={handleCreate}
                      loading={addQueryMutation.isPending}
                      disabled={!newSql.trim()}
                    />
                  </HStack>
                </div>
              </Card.Content>
            </Card>
          </m.div>
        )}
      </AnimatePresence>

      {/* Query List */}
      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.1 }}
      >
        <Card className="w-full overflow-hidden">
          <Card.Content className="p-0">
            {/* Header with search */}
            <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
              <HStack className="justify-between items-center gap-4">
                <HStack className="gap-2 items-center">
                  <Icon name="layers" label="Queries" className="w-4 h-4 text-content-layout-3" />
                  <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                    Saved Queries
                  </Text>
                  <Tag
                    size="small"
                    variant="informative"
                    modifier="ghost"
                    label={
                      total > 0
                        ? `Showing ${pageStart}-${Math.min(pageEnd, total)} of ${total}`
                        : `${queries.length} total`
                    }
                  />
                </HStack>
                <HStack className="gap-3 items-center">
                  <div className="w-64">
                    <BaseInputText
                      name="search"
                      placeholder="Search queries..."
                      icon="search"
                      iconPosition="left"
                      value={searchTerm}
                      onChange={(e) => {
                        setSearchTerm(e.target.value);
                        resetPagination();
                      }}
                    />
                  </div>
                  <HStack className="gap-2">
                    <Button
                      variant="primary"
                      modifier="ghost"
                      size="small"
                      label="Previous"
                      icon="arrow-left"
                      iconPosition="left"
                      disabled={!hasPrevPage || isFetching}
                      onClick={() => prevPage()}
                    />
                    <Button
                      variant="primary"
                      modifier="ghost"
                      size="small"
                      label="Next"
                      icon="arrow-right"
                      iconPosition="right"
                      disabled={!hasNextPage || isFetching}
                      onClick={() => nextPage()}
                    />
                  </HStack>
                </HStack>
              </HStack>
            </div>

            {/* Loading state */}
            <Show when={isLoading}>
              <div className="p-16">
                <VStack className="gap-4 items-center">
                  <div className="w-14 h-14 rounded-2xl bg-surface-layout-2 flex items-center justify-center">
                    <Icon name="folder-file" label="Loading" className="w-7 h-7 text-content-layout-3 animate-pulse" />
                  </div>
                  <Text level="body-small" className="text-content-layout-3">
                    Loading queries...
                  </Text>
                </VStack>
              </div>
            </Show>

            {/* Empty state */}
            <Show when={!isLoading && filteredQueries.length === 0}>
              <div className="p-16">
                <VStack className="gap-4 items-center">
                  <div className="w-14 h-14 rounded-2xl bg-surface-layout-2 flex items-center justify-center">
                    <Icon name="folder-file" label="Empty" className="w-7 h-7 text-content-layout-3" />
                  </div>
                  <VStack className="gap-2 items-center">
                    <Text level="headline-5" className="text-content-layout-2">
                      {searchTerm ? "No matching queries" : "No saved queries"}
                    </Text>
                    <Text level="body-small" className="text-content-layout-3 text-center max-w-sm">
                      {searchTerm
                        ? "Try adjusting your search term."
                        : "Queries you analyze will be saved here for quick access."}
                    </Text>
                  </VStack>
                  <Show when={!searchTerm && !showAddForm}>
                    <Button
                      variant="primary"
                      modifier="outline"
                      label="Add your first query"
                      icon="add"
                      iconPosition="left"
                      onClick={() => setShowAddForm(true)}
                    />
                  </Show>
                </VStack>
              </div>
            </Show>

            {/* Query list */}
            <Show when={!isLoading && filteredQueries.length > 0}>
              <div className="divide-y divide-border-layout-1">
                <AnimatePresence mode="popLayout">
                  {filteredQueries.map((entry, index) => (
                    <m.div
                      key={entry.hash}
                      initial={{ opacity: 0, y: -10 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, x: -20 }}
                      transition={{ duration: 0.2, delay: index * 0.02 }}
                      className="group px-5 py-3.5 hover:bg-surface-layout-2/30 transition-colors"
                    >
                      {confirmingHash === entry.hash ? (
                        <div className="flex items-center justify-between gap-4 bg-surface-negative-soft/20 rounded-lg p-4 border border-border-negative/30">
                          <HStack className="gap-3 items-center flex-1 min-w-0">
                            <Icon name="alert" label="Warning" className="w-5 h-5 text-content-negative-soft shrink-0" />
                            <VStack className="gap-1 items-start min-w-0">
                              <Text level="label-small" className="text-content-layout-1">
                                Delete this query?
                              </Text>
                              <div className="bg-surface-layout-2 px-2 py-1 rounded max-w-md overflow-hidden">
                                <SQLDisplay
                                  sql={entry.sql.length > 60 ? `${entry.sql.slice(0, 60)}...` : entry.sql}
                                  wrap={false}
                                />
                              </div>
                            </VStack>
                          </HStack>
                          <HStack className="gap-2 shrink-0">
                            <Button
                              variant="primary"
                              modifier="ghost"
                              size="small"
                              label="Cancel"
                              onClick={() => setConfirmingHash(null)}
                            />
                            <Button
                              variant="negative"
                              modifier="solid"
                              size="small"
                              label="Delete"
                              icon="trash"
                              iconPosition="left"
                              onClick={() => {
                                removeQuery(entry.hash);
                                setConfirmingHash(null);
                              }}
                            />
                          </HStack>
                        </div>
                      ) : (
                        <>
                          {/* Row 1: Identity + Source + Actions */}
                          <HStack className="justify-between items-center gap-3">
                            <HStack className="gap-2 items-center min-w-0 flex-1">
                              {editingHash === entry.hash ? (
                                <HStack className="gap-2 items-center flex-1 min-w-0">
                                  <div className="flex-1">
                                    <BaseInputText
                                      name={`edit-tag-${entry.hash}`}
                                      placeholder="Enter name"
                                      value={tagDraft}
                                      onChange={(e) => setTagDraft(e.target.value)}
                                    />
                                  </div>
                                  <Button
                                    variant="primary"
                                    modifier="ghost"
                                    size="small"
                                    icon="tick"
                                    iconPosition="icon"
                                    label="Save"
                                    onClick={() => {
                                      updateTag(entry.hash, tagDraft.trim());
                                      setEditingHash(null);
                                      setTagDraft("");
                                    }}
                                  />
                                  <Button
                                    variant="primary"
                                    modifier="ghost"
                                    size="small"
                                    icon="close"
                                    iconPosition="icon"
                                    label="Cancel"
                                    onClick={() => {
                                      setEditingHash(null);
                                      setTagDraft("");
                                    }}
                                  />
                                </HStack>
                              ) : (
                                <>
                                  <Text level="label-small" className="text-content-layout-1 font-medium truncate">
                                    {entry.tag || "(unnamed)"}
                                  </Text>
                                  <Text level="mono-small" className="text-content-layout-3 shrink-0">
                                    {entry.hash.slice(0, 8)}
                                  </Text>
                                  <Tag
                                    size="small"
                                    variant={getSourceVariant(entry.source)}
                                    modifier="ghost"
                                    label={formatSource(entry.source)}
                                  />
                                </>
                              )}
                            </HStack>
                            <div className="shrink-0 flex gap-1 items-center">
                              <div className="opacity-0 group-hover:opacity-100 transition-opacity flex gap-1">
                                <Button
                                  variant="primary"
                                  modifier="ghost"
                                  size="small"
                                  icon="edit"
                                  iconPosition="icon"
                                  label="Rename"
                                  onClick={() => {
                                    setEditingHash(entry.hash);
                                    setTagDraft(entry.tag || "");
                                  }}
                                />
                                <Button
                                  variant="negative"
                                  modifier="ghost"
                                  size="small"
                                  icon="trash"
                                  iconPosition="icon"
                                  label="Delete"
                                  onClick={() => setConfirmingHash(entry.hash)}
                                />
                              </div>
                              <TooltipProvider delayDuration={150}>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <div>
                                      <Button
                                        variant="primary"
                                        modifier="ghost"
                                        size="small"
                                        icon="speedometer"
                                        iconPosition="left"
                                        label="Analyze"
                                        onClick={() => handleAnalyze(entry.sql, entry.target, entry.most_recent_params)}
                                      />
                                    </div>
                                  </TooltipTrigger>
                                  <TooltipContent label="Analyze this query" />
                                </Tooltip>
                              </TooltipProvider>
                              <CacheButton
                                cached={isCached(entry.sql)}
                                loading={cachingHash === entry.hash}
                                onClick={() => handleCacheQuery(entry.hash, entry.sql)}
                              />
                            </div>
                          </HStack>

                          {/* Row 2: SQL */}
                          <button
                            type="button"
                            onClick={() => setExpandedHash(expandedHash === entry.hash ? null : entry.hash)}
                            className="mt-2 text-left bg-surface-layout-2 px-3 py-2 rounded-lg hover:ring-1 hover:ring-border-primary-soft transition-all cursor-pointer w-full block"
                            title={entry.sql}
                          >
                            <SQLDisplay
                              sql={
                                expandedHash === entry.hash
                                  ? entry.sql
                                  : getCollapsedPreview(entry.sql)
                              }
                              wrap={expandedHash === entry.hash}
                              showCopy={expandedHash === entry.hash}
                            />
                          </button>

                          {/* Row 3: Metadata */}
                          <HStack className="mt-2 gap-2 flex-wrap items-center">
                            <Tag size="small" variant="informative" modifier="ghost" label={`Runs: ${entry.frequency}`} />
                            <Tag size="small" variant="warning" modifier="ghost" label={`Avg ${formatDuration(entry.avg_duration_ms)}`} />
                            <Tag size="small" variant="warning" modifier="ghost" label={`Max ${formatDuration(entry.max_duration_ms)}`} />
                            <Show when={(entry.observation_count ?? 0) > 0}>
                              <Tag size="small" variant="positive" modifier="ghost" label={`${entry.observation_count} obs`} />
                            </Show>
                            <Show when={entry.most_recent_params && Object.keys(entry.most_recent_params).length > 0}>
                              <Tag size="small" variant="primary" modifier="ghost" label="Stored params" />
                            </Show>
                            <span className="text-content-layout-3 text-xs select-none">·</span>
                            <Show when={!!entry.target}>
                              <HStack className="gap-1 items-center">
                                <Icon name="database" label="Target" className="w-3 h-3 text-content-layout-3" />
                                <Text level="mono-small" className="text-content-layout-3">
                                  {entry.target}
                                </Text>
                              </HStack>
                            </Show>
                            <HStack className="gap-1 items-center">
                              <Icon name="observe" label="Time" className="w-3 h-3 text-content-layout-3" />
                              <Text level="caption" className="text-content-layout-3">
                                {formatTimestamp(entry.last_analyzed)}
                              </Text>
                            </HStack>
                            <Show when={entry.first_analyzed && entry.first_analyzed !== entry.last_analyzed}>
                              <Text level="caption" className="text-content-layout-3">
                                First seen {formatTimestamp(entry.first_analyzed || "")}
                              </Text>
                            </Show>
                          </HStack>
                        </>
                      )}
                    </m.div>
                  ))}
                </AnimatePresence>
              </div>
            </Show>
          </Card.Content>
        </Card>
      </m.div>
    </div>
  );
}
