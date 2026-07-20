import { useNavigate } from "@tanstack/react-router";
import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";
import { Alert } from "@rs/ui-new/alert";
import { BaseInputText } from "@rs/ui-new/base-input-text";
import { Button } from "@rs/ui-new/button";
import { Card } from "@rs/ui-new/card";
import { CopyButton } from "@rs/ui-new/copy-button";
import { Dropdown } from "@rs/ui-new/dropdown";
import { Icon } from "@rs/ui-new/icon";
import { Show } from "@rs/ui-new/show";
import { Tag } from "@rs/ui-new/tag";
import { Text } from "@rs/ui-new/text";
import { HStack, VStack } from "@rs/ui-new/stack";
import { m, AnimatePresence } from "@rs/ui-new/motion";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@rs/ui-new/tooltip";
import { toast } from "@rs/ui-new/use-toast";
import { useQueryRegistry } from "../lib/useQueryRegistry";
import { PathPicker } from "../components/PathPicker";
import { SQLInput } from "../components/SQLInput";
import { SQLDisplay } from "../components/SQLDisplay";
import { useTarget } from "../hooks/useTarget";
import { useCacheAction } from "../lib/useCacheAction";
import { useCacheRun } from "../lib/useCache";
import type { CacheRunResult } from "../types/cache";
import { fillCapturedParams, hasParameters } from "../lib/sqlParameters";
import { QueryCacheStatus } from "../components/QueryCacheStatus";
import { ComparisonCard } from "../components/CacheComparison";
import { ParameterDialog } from "../components/top";
import { collapseWhitespace } from "../lib/collapseWhitespace";

import { formatTimestamp, formatDuration } from "../lib/formatters";
import {
  byImpact,
  formatDbTime,
  formatImpactCaption,
  formatRunCount,
  isHeroCandidate,
  isNotCacheable,
  queryImpactMs,
} from "../lib/queryImpact";

type SourceVariant = "informative" | "rising" | "positive" | "neutral";

// Front-end label + semantic-tone map for registry source slugs. Centralized so
// the readable name and colour are defined once and cannot drift from the raw
// backend values (Slow Queries → info, Ask → rising, Cache → positive,
// Manual → neutral). Resolves the "source tags render raw slugs" finding.
const SOURCE_META: Record<string, { label: string; variant: SourceVariant }> = {
  "top-historical": { label: "Slow Queries", variant: "informative" },
  top: { label: "Slow Queries", variant: "informative" },
  ask: { label: "Ask", variant: "rising" },
  prompt: { label: "Ask", variant: "rising" },
  cache: { label: "Cache", variant: "positive" },
  web: { label: "Manual", variant: "neutral" },
  manual: { label: "Manual", variant: "neutral" },
  file: { label: "Manual", variant: "neutral" },
};

function getSourceMeta(source: string): { label: string; variant: SourceVariant } {
  return SOURCE_META[source] ?? { label: "Manual", variant: "neutral" };
}

function getCollapsedPreview(sql: string, maxLen = 120): string {
  const collapsed = collapseWhitespace(sql);
  return collapsed.length > maxLen ? `${collapsed.slice(0, maxLen)}...` : collapsed;
}

// Light, front-end-only readable label derived from the SQL when a query has no
// user-given name — gives every row a trigger word to scan (lead clause / first
// table, e.g. "COUNT on tags"). No API call. Resolves the "(unnamed) + hash"
// scannability finding.
function deriveQueryName(sql: string): string {
  const s = collapseWhitespace(sql).trim();
  if (!s) return "Untitled query";
  const verbMatch = s.match(/^(select|insert|update|delete|with|create|alter|drop|truncate)\b/i);
  const verb = verbMatch ? verbMatch[1].toLowerCase() : "";
  const aggMatch = s.match(/\b(count|sum|avg|min|max)\s*\(/i);
  const agg = aggMatch ? aggMatch[1].toUpperCase() : "";
  const tableMatch =
    s.match(/\bfrom\s+["'`[]?([\w.]+)/i) ||
    s.match(/\binto\s+["'`[]?([\w.]+)/i) ||
    s.match(/^update\s+["'`[]?([\w.]+)/i);
  const table = tableMatch ? (tableMatch[1].split(".").pop() ?? tableMatch[1]) : "";
  const verbTitle = verb ? verb.charAt(0).toUpperCase() + verb.slice(1) : "";
  if (agg && table) return `${agg} on ${table}`;
  if (verbTitle && table) return `${verbTitle} · ${table}`;
  if (table) return table;
  if (verbTitle) return verbTitle;
  return s.length > 40 ? `${s.slice(0, 40)}…` : s;
}

// Build the concrete SQL to benchmark, filling parameters from captured values.
// Returns null when a value is missing, so the caller can prompt for it instead
// of running a degenerate query.
function concreteSqlForTest(sql: string, stored: Record<string, unknown>): string | null {
  const filled = fillCapturedParams(sql, stored);
  return hasParameters(filled) ? null : filled;
}

// Single-select source filter chip. Selected state carries a tick icon (a
// non-colour structural cue) plus a filled/bordered look, so selection never
// leans on colour alone.
function FilterChip({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      data-active={active}
      data-testid="source-chip"
      data-source={label}
      onClick={onClick}
      className="inline-flex items-center gap-1.5 h-8 pl-2.5 pr-3 rounded-full text-label-small transition-all cursor-pointer border whitespace-nowrap
        data-[active=true]:bg-surface-primary-soft/30 data-[active=true]:border-surface-primary-solid data-[active=true]:text-content-layout-1
        data-[active=false]:bg-surface-layout-2 data-[active=false]:border-border-layout-1 data-[active=false]:text-content-layout-3 data-[active=false]:hover:border-border-layout-2 data-[active=false]:hover:text-content-layout-2"
    >
      <Show when={active}>
        <Icon name="tick" label="Selected" className="w-3.5 h-3.5 shrink-0" />
      </Show>
      <span>{label}</span>
      <span className="tabular-nums text-content-layout-3">{count}</span>
    </button>
  );
}

export function QueryRegistryPage({ deepLinkHash }: { deepLinkHash?: string }) {
  const navigate = useNavigate();
  const { target } = useTarget();
  const {
    queries,
    isLoading,
    isFetching,
    total,
    listError,
    offset,
    nextPage,
    prevPage,
    resetPagination,
    removeQuery,
    updateTag,
    addMutation: addQueryMutation,
    updateSqlMutation,
    importMutation,
  } = useQueryRegistry(150, target);
  const [searchTerm, setSearchTerm] = useState("");
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [editingHash, setEditingHash] = useState<string | null>(null);
  const [tagDraft, setTagDraft] = useState("");
  const [confirmingHash, setConfirmingHash] = useState<string | null>(null);
  const [newSql, setNewSql] = useState("");
  const [showAddForm, setShowAddForm] = useState(false);
  const [expandedHash, setExpandedHash] = useState<string | null>(null);
  const [editingSqlHash, setEditingSqlHash] = useState<string | null>(null);
  const [sqlDraft, setSqlDraft] = useState("");
  const [showImportForm, setShowImportForm] = useState(false);
  const [importPath, setImportPath] = useState("");
  const [importUpdate, setImportUpdate] = useState(false);

  // Deep-link (deepLinkHash prop from the route wrapper): focus one query by hash
  // (served-cache "View in Queries" links and the Analyze "Set up caching"
  // handoff). Degrades to the plain list on no match.
  useEffect(() => {
    if (!deepLinkHash) return;
    setExpandedHash(deepLinkHash);
    const timer = setTimeout(() => {
      document
        .querySelector(`[data-query-hash="${deepLinkHash}"]`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 150);
    return () => clearTimeout(timer);
  }, [deepLinkHash]);

  // Cache integration. "Cache & test" caches the query, then immediately runs
  // an origin-vs-cache comparison so the row can show measured proof, not just
  // "Cached" (rdst-41p.3). cacheRun is a single-flight hook, so testingHash
  // tracks which row owns the in-flight run and where its result belongs.
  const cacheRun = useCacheRun();
  const [testingHash, setTestingHash] = useState<string | null>(null);
  const [runResults, setRunResults] = useState<Record<string, CacheRunResult>>({});
  // A query whose benchmark needs parameter values we do not have. The dialog
  // collects them (pre-filled with any captured values), then the substituted
  // SQL runs, so every query can be tested (rdst-41p.11).
  const [paramDialog, setParamDialog] = useState<{ hash: string; sql: string } | null>(null);

  const runConcreteTest = (hash: string, sql: string) => {
    if (!target) return;
    setTestingHash(hash);
    void cacheRun.run({ query: sql, target, iterations: 15, warmup: 5 });
  };

  // Benchmark one query origin-vs-cache: fill parameters from captured values,
  // or prompt for them when we cannot. Shared by cache-then-test and the Test
  // action on already-cached rows.
  const runTest = (hash: string, sql: string, stored: Record<string, unknown>) => {
    if (!target) return;
    const runSql = concreteSqlForTest(sql, stored);
    if (runSql) runConcreteTest(hash, runSql);
    else setParamDialog({ hash, sql });
  };

  const { cacheQuery, cachingId: cachingHash, isCached } = useCacheAction({
    target,
    onCached: (sql, hash) => {
      const entry = queries.find((q) => q.hash === hash);
      runTest(hash, sql, entry?.most_recent_params ?? {});
    },
  });

  useEffect(() => {
    if (!testingHash) return;
    if (cacheRun.state === "complete" && cacheRun.result) {
      setRunResults((prev) => ({ ...prev, [testingHash]: cacheRun.result! }));
      setExpandedHash(testingHash); // reveal the before/after card
      setTestingHash(null);
      cacheRun.reset();
    } else if (cacheRun.state === "error") {
      toast({
        title: "Benchmark didn't complete",
        description: cacheRun.error ?? "The query is cached, but the perf test failed to run.",
        variant: "warning",
      });
      setTestingHash(null);
      cacheRun.reset();
    }
  }, [cacheRun.state, cacheRun.result, cacheRun.error, cacheRun.reset, testingHash]);

  const handleCacheQuery = (hash: string, sql: string) => {
    cacheQuery(sql, hash);
  };

  // Filter by search, then order by impact (total DB time). Impactful rows come
  // from Slow Queries telemetry; the rest keep the API's recency order behind
  // them (rdst-41p.2).
  const searchFiltered = useMemo(() => {
    const term = searchTerm.trim().toLowerCase();
    return term
      ? queries.filter(
          (entry) =>
            entry.tag?.toLowerCase().includes(term) || entry.sql.toLowerCase().includes(term),
        )
      : queries;
  }, [queries, searchTerm]);

  // Distinct source labels (aliases collapsed via getSourceMeta) in first-
  // appearance order; the chip row is hidden unless more than one source exists.
  const sourceChips = useMemo(() => {
    const seen = new Set<string>();
    for (const entry of queries) seen.add(getSourceMeta(entry.source).label);
    return Array.from(seen);
  }, [queries]);

  // Per-chip counts track the current search so each number matches what the
  // chip would reveal.
  const sourceCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const entry of searchFiltered) {
      const label = getSourceMeta(entry.source).label;
      counts.set(label, (counts.get(label) ?? 0) + 1);
    }
    return counts;
  }, [searchFiltered]);

  const filteredQueries = useMemo(() => {
    const bySource =
      sourceFilter === "all"
        ? searchFiltered
        : searchFiltered.filter((e) => getSourceMeta(e.source).label === sourceFilter);
    return [...bySource].sort(byImpact);
  }, [searchFiltered, sourceFilter]);

  const isFiltered = searchTerm.trim().length > 0 || sourceFilter !== "all";
  const handleSelectSource = (label: string) => {
    setSourceFilter(label);
    resetPagination();
  };

  // The single biggest win to promote as a hero: highest-impact query that is
  // uncached and not known-uncacheable. Hidden while searching (search is "find
  // a query", not "guide me"). heroMoreCount counts the other such candidates.
  const heroCandidates = useMemo(
    () => (searchTerm.trim() ? [] : filteredQueries.filter((e) => isHeroCandidate(e, isCached(e.hash)))),
    [filteredQueries, searchTerm, isCached],
  );
  const hero = heroCandidates[0] ?? null;
  const heroMoreCount = Math.max(0, heroCandidates.length - 1);
  const hasMeasuredImpact = useMemo(
    () => filteredQueries.some((e) => queryImpactMs(e) > 0),
    [filteredQueries],
  );
  const heroWhy = hero
    ? `Runs ${(hero.observation_count ?? 0).toLocaleString()} times at ${Math.round(hero.avg_duration_ms ?? 0)} ms avg, ${formatDbTime(queryImpactMs(hero))} of database time in all. A cache could serve it far faster.`
    : "";

  // The list excludes the promoted hero, memoized so the exclusion pass does not
  // re-run on every render of this state-heavy component.
  const displayedQueries = useMemo(
    () => (hero ? filteredQueries.filter((e) => e.hash !== hero.hash) : filteredQueries),
    [filteredQueries, hero],
  );

  const handleCreate = () => {
    if (!newSql.trim()) return;
    addQueryMutation.mutate(
      { sql: newSql, target: target || undefined },
      {
        onSuccess: () => {
          setNewSql("");
          setShowAddForm(false);
          toast({ title: "Query added", description: "Saved to your query library.", variant: "positive" });
        },
        onError: (err) => {
          toast({ title: "Couldn't add query", description: err.message, variant: "negative" });
        },
      },
    );
  };

  const handleStartEditSql = (hash: string, sql: string) => {
    setEditingSqlHash(hash);
    setSqlDraft(sql);
  };

  const handleCancelEditSql = () => {
    setEditingSqlHash(null);
    setSqlDraft("");
  };

  const handleSaveSql = (hash: string) => {
    if (!sqlDraft.trim()) return;
    updateSqlMutation.mutate(
      { hash, sql: sqlDraft },
      {
        onSuccess: (result) => {
          setEditingSqlHash(null);
          setSqlDraft("");
          toast({
            title: "Query updated",
            description: result.hash_changed
              ? `SQL saved. New hash: ${result.hash?.slice(0, 8)}`
              : "SQL saved.",
            variant: "positive",
          });
        },
        onError: (err) => {
          toast({ title: "Update failed", description: err.message, variant: "negative" });
        },
      },
    );
  };

  const handleRename = (hash: string, name: string) => {
    updateTag(hash, name);
    setEditingHash(null);
    setTagDraft("");
    toast({ title: "Query renamed", variant: "positive" });
  };

  const handleImport = () => {
    if (!importPath.trim()) return;
    importMutation.mutate(
      { file: importPath.trim(), update: importUpdate, target: target || undefined },
      {
        onSuccess: (result) => {
          if (result.success) {
            toast({
              title: "Import complete",
              description: result.message || `${result.imported} imported`,
              variant: "positive",
            });
          } else {
            toast({
              title: "Import finished with issues",
              description: result.message || `${result.errors?.length ?? 0} errors`,
              variant: "negative",
            });
          }
        },
        onError: (err) => {
          toast({ title: "Import failed", description: err.message, variant: "negative" });
        },
      },
    );
  };

  const handleAnalyze = (sql: string, target?: string, mostRecentParams?: Record<string, unknown>) => {
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

  const pageEnd = offset + queries.length;
  const hasPrevPage = offset > 0;
  const hasNextPage = pageEnd < total;
  const hasPagination = hasPrevPage || hasNextPage;

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
              <Icon name="folder-file" label="Queries" className="w-6 h-6 text-content-primary-soft" />
            </div>
            <VStack className="gap-1 items-start">
              <Text as="h1" level="headline-3" className="text-content-layout-1">
                Queries
              </Text>
              <Text level="body-small" className="text-content-layout-3">
                Find, understand, and speed up the queries running on your database.
              </Text>
            </VStack>
          </HStack>

          <HStack className="gap-2 items-center">
            <Show when={!showImportForm}>
              <Button
                variant="primary"
                modifier="outline"
                label="Import from file"
                icon="folder-file"
                iconPosition="left"
                onClick={() => setShowImportForm(true)}
              />
            </Show>
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

      {/* Import from file Form */}
      <AnimatePresence>
        {showImportForm && (
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
                    <Icon name="folder-file" label="Import" className="w-4 h-4 text-content-layout-3" />
                    <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                      Import Queries from File
                    </Text>
                  </HStack>
                </div>
                <div className="p-5">
                  <VStack className="gap-4 items-stretch">
                    <Text level="body-small" className="text-content-layout-3">
                      Import a local .sql file with semicolon-separated queries. Each query may
                      carry optional <span className="font-mono">-- name:</span> and{" "}
                      <span className="font-mono">-- target:</span> comments.
                    </Text>
                    <div className="grid grid-cols-1 tablet:grid-cols-[2fr_auto_auto] gap-3 items-end">
                      <PathPicker
                        value={importPath}
                        onChange={setImportPath}
                        fileExt="sql"
                        label="File Path"
                        disabled={importMutation.isPending}
                      />
                      <button
                        type="button"
                        onClick={() => setImportUpdate(!importUpdate)}
                        className="h-10 px-4 rounded-lg text-sm font-medium transition-all cursor-pointer border whitespace-nowrap
                          data-[active=true]:bg-surface-primary-soft/30 data-[active=true]:border-surface-primary-solid data-[active=true]:text-content-layout-1
                          data-[active=false]:bg-surface-layout-2 data-[active=false]:border-border-layout-1 data-[active=false]:text-content-layout-3"
                        data-active={importUpdate}
                      >
                        Update existing
                      </button>
                      <Button
                        variant="rising"
                        modifier="solid"
                        label="Import"
                        icon="folder-file"
                        iconPosition="left"
                        onClick={handleImport}
                        loading={importMutation.isPending}
                        disabled={!importPath.trim() || importMutation.isPending}
                      />
                    </div>

                    {importMutation.data && (
                      <VStack className="gap-2 items-stretch bg-surface-layout-2/50 rounded-lg p-4 border border-border-layout-1">
                        <HStack className="gap-2 items-center flex-wrap">
                          <Icon
                            name={importMutation.data.success ? "tick-double" : "alert"}
                            label="Result"
                            className={`w-4 h-4 ${importMutation.data.success ? "text-content-positive-soft" : "text-content-negative-soft"}`}
                          />
                          <Tag size="small" variant="positive" modifier="ghost" label={`${importMutation.data.imported ?? 0} imported`} />
                          <Tag size="small" variant="primary" modifier="ghost" label={`${importMutation.data.updated ?? 0} updated`} />
                          <Tag size="small" variant="warning" modifier="ghost" label={`${importMutation.data.skipped ?? 0} skipped`} />
                          <Tag size="small" variant="negative" modifier="ghost" label={`${importMutation.data.errors?.length ?? 0} errors`} />
                        </HStack>
                        {(importMutation.data.errors ?? []).map((message, index) => (
                          <HStack key={`import-err-${index}`} className="gap-2 items-center">
                            <Icon name="alert" label="Error" className="w-3.5 h-3.5 text-content-negative-soft shrink-0" />
                            <Text level="caption" className="text-content-negative-soft">
                              {message}
                            </Text>
                          </HStack>
                        ))}
                      </VStack>
                    )}
                  </VStack>
                </div>
                <div className="px-5 py-4 border-t border-border-layout-1 bg-surface-layout-1">
                  <HStack className="justify-end gap-2">
                    <Button
                      variant="primary"
                      modifier="ghost"
                      label="Close"
                      onClick={() => {
                        setShowImportForm(false);
                        setImportPath("");
                        importMutation.reset();
                      }}
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
            {/* Toolbar: search (with clear) + filter-aware count + pagination */}
            <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
              <HStack className="justify-between items-center gap-4">
                <div className="relative w-72 max-w-full">
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
                  <Show when={searchTerm.length > 0}>
                    <button
                      type="button"
                      aria-label="Clear search"
                      onClick={() => {
                        setSearchTerm("");
                        resetPagination();
                      }}
                      className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center justify-center w-6 h-6 rounded-md text-content-layout-3 hover:text-content-layout-1 hover:bg-surface-layout-2 transition-colors cursor-pointer"
                    >
                      <Icon name="close" label="Clear search" className="w-4 h-4" />
                    </button>
                  </Show>
                </div>
                <HStack className="gap-3 items-center shrink-0">
                  <Text level="body-small" className="text-content-layout-3">
                    {searchTerm.trim()
                      ? `${filteredQueries.length} of ${total}`
                      : `${total} ${total === 1 ? "query" : "queries"}`}
                  </Text>
                  <Show when={hasPagination}>
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
                  </Show>
                </HStack>
              </HStack>
              {/* Single-select source filter, combined with search (AND). Shown
                  only when more than one source exists, otherwise it is noise. */}
              <Show when={sourceChips.length > 1}>
                <HStack className="gap-1.5 items-center flex-wrap mt-3">
                  <FilterChip
                    label="All"
                    count={searchFiltered.length}
                    active={sourceFilter === "all"}
                    onClick={() => handleSelectSource("all")}
                  />
                  {sourceChips.map((label) => (
                    <FilterChip
                      key={label}
                      label={label}
                      count={sourceCounts.get(label) ?? 0}
                      active={sourceFilter === label}
                      onClick={() => handleSelectSource(label)}
                    />
                  ))}
                </HStack>
              </Show>
            </div>

            {/* Backend failed to read the registry; surface it instead of an empty list */}
            <Show when={!!listError}>
              <div className="px-5 py-3 border-b border-border-layout-1">
                <Alert
                  variant="negative"
                  modifier="outline"
                  label={`Could not load the query registry: ${listError}`}
                />
              </div>
            </Show>

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
                      {isFiltered ? "No matching queries" : "No saved queries"}
                    </Text>
                    <Text level="body-small" className="text-content-layout-3 text-center max-w-sm">
                      {searchTerm
                        ? "Try adjusting your search term."
                        : "Queries you analyze will be saved here for quick access."}
                    </Text>
                  </VStack>
                  <Show when={!!searchTerm}>
                    <Button
                      variant="primary"
                      modifier="ghost"
                      size="small"
                      label="Clear search"
                      icon="close"
                      iconPosition="left"
                      onClick={() => {
                        setSearchTerm("");
                        resetPagination();
                      }}
                    />
                  </Show>
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

            {/* Biggest win — the single highest-impact uncached candidate (rdst-41p.2) */}
            <Show when={!!hero}>
              <div className="rounded-xl border border-border-layout-1 bg-surface-rising-soft p-5 mb-1">
                <Text level="overline" className="text-content-rising-soft uppercase tracking-wider">
                  Your biggest win
                </Text>
                <div className="mt-2.5 flex items-center gap-4 flex-wrap">
                  <VStack className="gap-1.5 items-start flex-1 min-w-0">
                    <div className="font-mono text-content-layout-1 text-sm truncate w-full">
                      {hero?.sql}
                    </div>
                    <Text level="body-small" className="text-content-layout-2">
                      {heroWhy}
                    </Text>
                  </VStack>
                  <Button
                    variant="rising"
                    modifier="solid"
                    icon="database-settings"
                    iconPosition="left"
                    label="Cache & test"
                    loading={!!hero && cachingHash === hero.hash}
                    onClick={() => hero && handleCacheQuery(hero.hash, hero.sql)}
                  />
                </div>
                <Show when={heroMoreCount > 0}>
                  <Text level="caption" className="text-content-layout-3 mt-3">
                    {heroMoreCount} more worth caching below
                  </Text>
                </Show>
              </div>
            </Show>

            {/* No telemetry yet — point users at Slow Queries to populate impact */}
            <Show when={!searchTerm.trim() && !hasMeasuredImpact && filteredQueries.length > 0}>
              <button
                type="button"
                onClick={() => navigate({ to: "/top" })}
                className="w-full text-left rounded-xl border border-border-layout-1 bg-surface-layout-1 px-4 py-3 mb-1 hover:border-border-primary-soft transition-colors"
              >
                <HStack className="gap-2 items-center">
                  <Icon name="observe" label="Slow Queries" className="w-4 h-4 text-content-layout-3" />
                  <Text level="label-small" className="text-content-layout-2">
                    Run Slow Queries to surface your biggest caching wins
                  </Text>
                </HStack>
              </button>
            </Show>

            {/* Query list */}
            <Show when={!isLoading && filteredQueries.length > 0}>
              <div className="p-3 space-y-2 bg-surface-layout-1">
                <AnimatePresence mode="popLayout">
                  {displayedQueries.map((entry) => {
                    const sourceMeta = getSourceMeta(entry.source);
                    const displayName = entry.tag?.trim() || deriveQueryName(entry.sql);
                    const isExpanded = expandedHash === entry.hash;
                    const isRenaming = editingHash === entry.hash;
                    const isEditingSql = editingSqlHash === entry.hash;
                    const cached = isCached(entry.hash);
                    const notCacheable = isNotCacheable(entry.readyset_supported);
                    const isTesting = testingHash === entry.hash;

                    // Telemetry-bearing rows lead with impact; others keep the
                    // run-frequency line (which reads "never run" when unseen).
                    const impactCaption = formatImpactCaption(entry);
                    const runCount = formatRunCount(entry);
                    const metaParts: ReactNode[] = [];
                    if (impactCaption) {
                      metaParts.push(
                        <span key="impact" className="text-content-layout-2">{impactCaption}</span>,
                      );
                      if (runCount) metaParts.push(<span key="runs">{runCount}</span>);
                    } else {
                      metaParts.push(
                        <span key="runs">
                          {entry.frequency > 0
                            ? `${entry.frequency} ${entry.frequency === 1 ? "run" : "runs"}`
                            : "never run"}
                        </span>,
                      );
                    }
                    if ((entry.avg_duration_ms ?? 0) > 0) {
                      metaParts.push(<span key="avg">avg {formatDuration(entry.avg_duration_ms)}</span>);
                    }
                    if (entry.target) {
                      metaParts.push(
                        <span key="target" className="inline-flex items-center gap-1">
                          <Icon name="database" label="Target" className="w-3 h-3" />
                          {entry.target}
                        </span>,
                      );
                    }

                    return (
                      <m.div
                        key={entry.hash}
                        data-testid="query-registry-row"
                        data-query-hash={entry.hash}
                        initial={{ opacity: 0, y: -6 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, x: -16 }}
                        transition={{ duration: 0.18 }}
                        className="rounded-xl bg-surface-layout-2 p-4 transition-all hover:bg-surface-raised hover:shadow-elevation-1 focus-within:bg-surface-raised focus-within:shadow-elevation-1"
                      >
                        {confirmingHash === entry.hash ? (
                          <div className="flex items-center justify-between gap-4 bg-surface-negative-soft/20 rounded-lg p-4 border border-border-negative-soft/30">
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
                            {/* Line 1: identity + source + one primary action + overflow */}
                            <HStack className="justify-between items-start gap-3">
                              <VStack className="gap-1.5 items-start min-w-0 flex-1">
                                {isRenaming ? (
                                  <HStack className="gap-2 items-center w-full min-w-0">
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
                                      onClick={() => handleRename(entry.hash, tagDraft.trim())}
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
                                    <HStack className="gap-2 items-center w-full min-w-0">
                                      <Text level="label-medium" className="text-content-layout-1 font-semibold truncate">
                                        {displayName}
                                      </Text>
                                      <Tag
                                        size="small"
                                        variant={sourceMeta.variant}
                                        modifier="ghost"
                                        label={sourceMeta.label}
                                      />
                                    </HStack>
                                    <Show when={!isEditingSql}>
                                      <button
                                        type="button"
                                        onClick={() => setExpandedHash(isExpanded ? null : entry.hash)}
                                        title={entry.sql}
                                        aria-expanded={isExpanded}
                                        className="text-left w-full min-w-0 rounded-md hover:bg-surface-layout-1 transition-colors cursor-pointer px-1 -mx-1 py-0.5 block"
                                      >
                                        <Text level="mono-small" className="text-content-layout-2 truncate block">
                                          {getCollapsedPreview(entry.sql)}
                                        </Text>
                                      </button>
                                    </Show>
                                  </>
                                )}
                              </VStack>
                              <Show when={!isRenaming && !isEditingSql}>
                                <HStack className="gap-2.5 items-center shrink-0">
                                  <QueryCacheStatus
                                    cached={cached}
                                    readysetSupported={entry.readyset_supported}
                                    testing={isTesting}
                                    speedup={runResults[entry.hash]?.speedup_mean}
                                  />
                                  <Show when={!cached && !notCacheable && !isTesting}>
                                    <TooltipProvider delayDuration={150}>
                                      <Tooltip>
                                        <TooltipTrigger asChild>
                                          <div>
                                            <Button
                                              variant="rising"
                                              modifier="solid"
                                              size="small"
                                              icon="database-settings"
                                              iconPosition="left"
                                              label="Cache & test"
                                              loading={cachingHash === entry.hash}
                                              onClick={() => handleCacheQuery(entry.hash, entry.sql)}
                                            />
                                          </div>
                                        </TooltipTrigger>
                                        <TooltipContent label="Cache this query and measure the speedup vs your database" />
                                      </Tooltip>
                                    </TooltipProvider>
                                  </Show>
                                  <Show when={cached && !isTesting}>
                                    <Button
                                      variant="rising"
                                      modifier="ghost"
                                      size="small"
                                      icon="database-settings"
                                      iconPosition="left"
                                      label={runResults[entry.hash] ? "Re-test" : "Test"}
                                      onClick={() => runTest(entry.hash, entry.sql, entry.most_recent_params ?? {})}
                                    />
                                  </Show>
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
                                  <Dropdown>
                                    <Dropdown.Trigger asChild>
                                      <Button
                                        variant="primary"
                                        modifier="ghost"
                                        size="small"
                                        icon="more"
                                        iconPosition="icon"
                                        label="More actions"
                                      />
                                    </Dropdown.Trigger>
                                    <Dropdown.Content align="end" className="min-w-52">
                                      <Dropdown.Item
                                        leftIcon="filter-edit"
                                        label="Edit SQL"
                                        onSelect={() => handleStartEditSql(entry.hash, entry.sql)}
                                      />
                                      <Dropdown.Item
                                        leftIcon="edit"
                                        label="Rename"
                                        onSelect={() => {
                                          setEditingHash(entry.hash);
                                          setTagDraft(entry.tag || "");
                                        }}
                                      />
                                      <Dropdown.Separator />
                                      <Dropdown.Item
                                        leftIcon="trash"
                                        label="Delete"
                                        className="text-content-negative-soft hover:text-content-negative-soft focus:text-content-negative-soft hover:bg-surface-negative-soft focus:bg-surface-negative-soft"
                                        onSelect={() => setConfirmingHash(entry.hash)}
                                      />
                                    </Dropdown.Content>
                                  </Dropdown>
                                </HStack>
                              </Show>
                            </HStack>

                            {/* Inline SQL editor (opened from the overflow menu) */}
                            {isEditingSql && (
                              <div className="mt-3">
                                <SQLInput
                                  value={sqlDraft}
                                  onChange={setSqlDraft}
                                  placeholder="Edit SQL query..."
                                  minHeight="10rem"
                                  target={entry.target || target}
                                  showPrettify
                                />
                                <HStack className="justify-end gap-2 mt-3">
                                  <Button
                                    variant="primary"
                                    modifier="ghost"
                                    size="small"
                                    label="Cancel"
                                    onClick={handleCancelEditSql}
                                  />
                                  <Button
                                    variant="rising"
                                    modifier="solid"
                                    size="small"
                                    label="Save"
                                    icon="tick"
                                    iconPosition="left"
                                    onClick={() => handleSaveSql(entry.hash)}
                                    loading={updateSqlMutation.isPending}
                                    disabled={!sqlDraft.trim()}
                                  />
                                </HStack>
                              </div>
                            )}

                            {/* Quiet meta line (runs · avg · target) + expand chevron */}
                            <Show when={!isRenaming && !isEditingSql}>
                              <HStack className="justify-between items-center gap-2 mt-2.5">
                                <Text
                                  as="div"
                                  level="caption"
                                  className="text-content-layout-3 flex items-center gap-1.5 flex-wrap min-w-0"
                                >
                                  {metaParts.map((part, i) => (
                                    <Fragment key={i}>
                                      {i > 0 && (
                                        <span aria-hidden className="select-none">
                                          ·
                                        </span>
                                      )}
                                      {part}
                                    </Fragment>
                                  ))}
                                </Text>
                                <button
                                  type="button"
                                  aria-label={isExpanded ? "Hide details" : "Show details"}
                                  aria-expanded={isExpanded}
                                  onClick={() => setExpandedHash(isExpanded ? null : entry.hash)}
                                  className="shrink-0 flex items-center justify-center w-7 h-7 rounded-md text-content-layout-3 hover:text-content-layout-1 hover:bg-surface-layout-1 transition-colors cursor-pointer"
                                >
                                  <Icon
                                    name={isExpanded ? "chevron-up" : "chevron-down"}
                                    label={isExpanded ? "Hide details" : "Show details"}
                                    className="w-4 h-4"
                                  />
                                </button>
                              </HStack>

                              {/* Expanded detail: full SQL (+ sibling Copy), hash, params, timestamps */}
                              <Show when={isExpanded}>
                                <VStack className="gap-3 items-stretch mt-3 pt-3 border-t border-border-layout-1">
                                  {/* Cache & test payoff — origin-vs-cache proof (rdst-41p.4) */}
                                  <Show when={!!runResults[entry.hash]}>
                                    <ComparisonCard
                                      result={runResults[entry.hash]!}
                                      onDismiss={() =>
                                        setRunResults((prev) => {
                                          const next = { ...prev };
                                          delete next[entry.hash];
                                          return next;
                                        })
                                      }
                                    />
                                  </Show>

                                  <VStack className="gap-1.5 items-stretch">
                                    <HStack className="justify-between items-center">
                                      <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                                        SQL
                                      </Text>
                                      <CopyButton text={entry.sql} />
                                    </HStack>
                                    <div className="bg-surface-layout-1 rounded-lg px-3 py-2 overflow-x-auto">
                                      <SQLDisplay sql={entry.sql} wrap />
                                    </div>
                                  </VStack>

                                  <HStack className="gap-x-4 gap-y-1.5 flex-wrap items-center">
                                    <HStack className="gap-1.5 items-center">
                                      <Text level="caption" className="text-content-layout-3">
                                        hash
                                      </Text>
                                      <Text level="mono-small" className="text-content-layout-2">
                                        {entry.hash.slice(0, 8)}
                                      </Text>
                                      <CopyButton text={entry.hash} />
                                    </HStack>
                                    <Show when={(entry.max_duration_ms ?? 0) > 0}>
                                      <Text level="caption" className="text-content-layout-3">
                                        max {formatDuration(entry.max_duration_ms)}
                                      </Text>
                                    </Show>
                                    <Show when={(entry.observation_count ?? 0) > 0}>
                                      <Text level="caption" className="text-content-layout-3">
                                        {entry.observation_count} obs
                                      </Text>
                                    </Show>
                                    <Show
                                      when={
                                        !!entry.most_recent_params &&
                                        Object.keys(entry.most_recent_params).length > 0
                                      }
                                    >
                                      <HStack className="gap-1.5 items-center flex-wrap">
                                        <Text level="caption" className="text-content-layout-3">
                                          params
                                        </Text>
                                        {Object.keys(entry.most_recent_params ?? {}).map((key) => (
                                          <Tag key={key} size="small" variant="neutral" modifier="ghost" label={key} />
                                        ))}
                                      </HStack>
                                    </Show>
                                  </HStack>

                                  <HStack className="gap-x-4 gap-y-1 flex-wrap items-center">
                                    <Text level="caption" className="text-content-layout-3">
                                      Updated {formatTimestamp(entry.last_analyzed)}
                                    </Text>
                                    <Show when={!!entry.first_analyzed && entry.first_analyzed !== entry.last_analyzed}>
                                      <Text level="caption" className="text-content-layout-3">
                                        Created {formatTimestamp(entry.first_analyzed || "")}
                                      </Text>
                                    </Show>
                                  </HStack>
                                </VStack>
                              </Show>
                            </Show>
                          </>
                        )}
                      </m.div>
                    );
                  })}
                </AnimatePresence>
              </div>
            </Show>
          </Card.Content>
        </Card>
      </m.div>

      {/* Collect parameter values for a query whose benchmark needs them (rdst-41p.11) */}
      {paramDialog && (
        <ParameterDialog
          isOpen
          query={paramDialog.sql}
          initialValues={queries.find((q) => q.hash === paramDialog.hash)?.most_recent_params}
          submitLabel="Run test"
          submitIcon="speedometer"
          onClose={() => setParamDialog(null)}
          onSubmit={(substitutedSql) => {
            const { hash } = paramDialog;
            setParamDialog(null);
            runConcreteTest(hash, substitutedSql);
          }}
        />
      )}
    </div>
  );
}
