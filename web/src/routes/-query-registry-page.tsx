// Query Registry page component — moved out of the route config into this
// route-ignored sibling (TanStack skips `-`-prefixed files) so the code-splitter
// can relocate its QueryCard → SQLDisplay/SQLInput imports (the CodeMirror
// SQL-editor stack) out of the eager entry chunk. Referencing an exported page
// as the route `component:` pins it (and its transitive CodeMirror imports) into
// the eager entry; the non-exported wrapper in `query-registry.tsx` imports
// `QueryRegistryPage` only for its `component:`, and the tests import it from
// here. [FIX-1 / Defect D-1]

import { Alert } from '@rs/ui-new/alert'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { Dropdown } from '@rs/ui-new/dropdown'
import { Icon } from '@rs/ui-new/icon'
import { AnimatePresence, m } from '@rs/ui-new/motion'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@rs/ui-new/tooltip'
import { toast } from '@rs/ui-new/use-toast'
import { useNavigate } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { PathPicker } from '../components/PathPicker'
import { QueryCard } from '../components/QueryCard'
import { SQLDisplay } from '../components/SQLDisplay'
import { SQLInput } from '../components/SQLInput'
import { useTarget } from '../hooks/useTarget'
import { collapseWhitespace } from '../lib/collapseWhitespace'
import { formatDuration, formatMeta, formatTimestamp } from '../lib/formatters'
import { useCacheAction } from '../lib/useCacheAction'
import { useQueryRegistry } from '../lib/useQueryRegistry'

type SourceVariant = 'informative' | 'rising' | 'positive' | 'neutral'

// Front-end label + semantic-tone map for registry source slugs. Centralized so
// the readable name and colour are defined once and cannot drift from the raw
// backend values (Slow Queries → info, Ask → rising, Cache → positive,
// Manual → neutral). Resolves the "source tags render raw slugs" finding.
const SOURCE_META: Record<string, { label: string; variant: SourceVariant }> = {
  'top-historical': { label: 'Slow Queries', variant: 'informative' },
  top: { label: 'Slow Queries', variant: 'informative' },
  ask: { label: 'Ask', variant: 'rising' },
  prompt: { label: 'Ask', variant: 'rising' },
  cache: { label: 'Cache', variant: 'positive' },
  web: { label: 'Manual', variant: 'neutral' },
  manual: { label: 'Manual', variant: 'neutral' },
  file: { label: 'Manual', variant: 'neutral' },
}

function getSourceMeta(source: string): {
  label: string
  variant: SourceVariant
} {
  return SOURCE_META[source] ?? { label: 'Manual', variant: 'neutral' }
}

// Light, front-end-only readable label derived from the SQL when a query has no
// user-given name — gives every row a trigger word to scan (lead clause / first
// table, e.g. "COUNT on tags"). No API call. Resolves the "(unnamed) + hash"
// scannability finding.
function deriveQueryName(sql: string): string {
  const s = collapseWhitespace(sql).trim()
  if (!s) return 'Untitled query'
  const verbMatch = s.match(
    /^(select|insert|update|delete|with|create|alter|drop|truncate)\b/i
  )
  const verb = verbMatch ? verbMatch[1].toLowerCase() : ''
  const aggMatch = s.match(/\b(count|sum|avg|min|max)\s*\(/i)
  const agg = aggMatch ? aggMatch[1].toUpperCase() : ''
  const tableMatch =
    s.match(/\bfrom\s+["'`[]?([\w.]+)/i) ||
    s.match(/\binto\s+["'`[]?([\w.]+)/i) ||
    s.match(/^update\s+["'`[]?([\w.]+)/i)
  const table = tableMatch
    ? (tableMatch[1].split('.').pop() ?? tableMatch[1])
    : ''
  const verbTitle = verb ? verb.charAt(0).toUpperCase() + verb.slice(1) : ''
  if (agg && table) return `${agg} on ${table}`
  if (verbTitle && table) return `${verbTitle} · ${table}`
  if (table) return table
  if (verbTitle) return verbTitle
  return s.length > 40 ? `${s.slice(0, 40)}…` : s
}

// One source filter chip. Selected state does NOT rely on colour alone: a solid
// primary border + fill + a leading tick together mark the active chip (USE-005,
// VIS-119). Mirrors the "Update existing" data-active toggle already used in
// this file so the two chip idioms stay consistent (USE-097).
function FilterChip({
  label,
  count,
  active,
  onClick,
}: {
  label: string
  count: number
  active: boolean
  onClick: () => void
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
  )
}

export function QueryRegistryPage() {
  const navigate = useNavigate()
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
  } = useQueryRegistry(150)
  const [searchTerm, setSearchTerm] = useState('')
  // Single-select source filter; "all" is the default (owner: "ilk olarak all
  // secili gelir"). Client-side only — the route has no search-param pattern to
  // extend, so no URL sync is invented. [triage §1.6]
  const [sourceFilter, setSourceFilter] = useState<string>('all')
  const [editingHash, setEditingHash] = useState<string | null>(null)
  const [tagDraft, setTagDraft] = useState('')
  const [confirmingHash, setConfirmingHash] = useState<string | null>(null)
  const [newSql, setNewSql] = useState('')
  const [showAddForm, setShowAddForm] = useState(false)
  const [editingSqlHash, setEditingSqlHash] = useState<string | null>(null)
  const [sqlDraft, setSqlDraft] = useState('')
  const [showImportForm, setShowImportForm] = useState(false)
  const [importPath, setImportPath] = useState('')
  const [importUpdate, setImportUpdate] = useState(false)
  const { target } = useTarget()

  // Cache integration
  const {
    cacheQuery,
    cachingId: cachingHash,
    isCached,
  } = useCacheAction({ target })

  const handleCacheQuery = (hash: string, sql: string) => {
    cacheQuery(sql, hash)
  }

  // Text search runs first; the single-select source chip narrows on top (AND).
  // Keeping the two stages separate lets chip counts stay consistent with the
  // *searched* set and lets the "N of total" line reflect both. [USE-037/038]
  const searchFiltered = useMemo(() => {
    if (!searchTerm.trim()) return queries
    const lowerTerm = searchTerm.toLowerCase()
    return queries.filter(
      (entry) =>
        entry.tag?.toLowerCase().includes(lowerTerm) ||
        entry.sql.toLowerCase().includes(lowerTerm)
    )
  }, [queries, searchTerm])

  // Chips are derived from the loaded list so the row only offers sources that
  // actually exist (no dead filters). Built from all loaded queries — not the
  // search result — so chips don't appear/vanish while typing. Sources that map
  // to the same readable label (top/top-historical → "Slow Queries") collapse to
  // one chip. [triage §1.6; VIS-108 selectable chips]
  const sourceChips = useMemo(() => {
    const seen = new Map<string, SourceVariant>()
    for (const entry of queries) {
      const meta = getSourceMeta(entry.source)
      if (!seen.has(meta.label)) seen.set(meta.label, meta.variant)
    }
    return Array.from(seen, ([label, variant]) => ({ label, variant }))
  }, [queries])

  // Per-chip counts reflect the current search so the number matches what a chip
  // would reveal. One pass over the already-searched list — cheap.
  const sourceCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const entry of searchFiltered) {
      const label = getSourceMeta(entry.source).label
      counts.set(label, (counts.get(label) ?? 0) + 1)
    }
    return counts
  }, [searchFiltered])

  const filteredQueries = useMemo(() => {
    if (sourceFilter === 'all') return searchFiltered
    return searchFiltered.filter(
      (entry) => getSourceMeta(entry.source).label === sourceFilter
    )
  }, [searchFiltered, sourceFilter])

  // Per-row view-models derived ONLY from the data inputs (the filtered list +
  // the cached-SQL set that backs `isCached`) — never from the rename / SQL /
  // delete-confirm drafts that also live in page state. So a keystroke in one
  // row's rename or SQL-edit field no longer re-runs deriveQueryName + meta
  // building across all 150 rows; only a data change (refetch, search, source
  // chip, or a cache op) recomputes them. The transient per-row flags stay in
  // the render below since they DO depend on those drafts. [PS5 item 7a]
  const rowViewModels = useMemo(
    () =>
      filteredQueries.map((entry) => ({
        entry,
        sourceMeta: getSourceMeta(entry.source),
        displayName: entry.tag?.trim() || deriveQueryName(entry.sql),
        cached: isCached(entry.sql),
        // The metric block folds into one muted meta line:
        // runs · avg · target · updated. [triage §1.1 /query-registry row;
        // USE-002/003 fold labels into values]
        meta: formatMeta([
          `runs ${entry.frequency > 0 ? entry.frequency : '—'}`,
          (entry.avg_duration_ms ?? 0) > 0
            ? `avg ${formatDuration(entry.avg_duration_ms)}`
            : null,
          entry.target ?? null,
          `updated ${formatTimestamp(entry.last_analyzed)}`,
        ]),
      })),
    [filteredQueries, isCached]
  )

  const isFiltered = searchTerm.trim().length > 0 || sourceFilter !== 'all'

  const handleSelectSource = (label: string) => {
    setSourceFilter(label)
    resetPagination()
  }

  const handleClearFilters = () => {
    setSearchTerm('')
    setSourceFilter('all')
    resetPagination()
  }

  const handleCreate = () => {
    if (!newSql.trim()) return
    addQueryMutation.mutate(
      { sql: newSql, target: target || undefined },
      {
        onSuccess: () => {
          setNewSql('')
          setShowAddForm(false)
          toast({
            title: 'Query added',
            description: 'Saved to your query library.',
            variant: 'positive',
          })
        },
        onError: (err) => {
          toast({
            title: "Couldn't add query",
            description: err.message,
            variant: 'negative',
          })
        },
      }
    )
  }

  const handleStartEditSql = (hash: string, sql: string) => {
    setEditingSqlHash(hash)
    setSqlDraft(sql)
  }

  const handleCancelEditSql = () => {
    setEditingSqlHash(null)
    setSqlDraft('')
  }

  const handleSaveSql = (hash: string) => {
    if (!sqlDraft.trim()) return
    updateSqlMutation.mutate(
      { hash, sql: sqlDraft },
      {
        onSuccess: (result) => {
          setEditingSqlHash(null)
          setSqlDraft('')
          toast({
            title: 'Query updated',
            description: result.hash_changed
              ? `SQL saved. New hash: ${result.hash?.slice(0, 8)}`
              : 'SQL saved.',
            variant: 'positive',
          })
        },
        onError: (err) => {
          toast({
            title: 'Update failed',
            description: err.message,
            variant: 'negative',
          })
        },
      }
    )
  }

  const handleRename = (hash: string, name: string) => {
    updateTag(hash, name)
    setEditingHash(null)
    setTagDraft('')
    toast({ title: 'Query renamed', variant: 'positive' })
  }

  const handleImport = () => {
    if (!importPath.trim()) return
    importMutation.mutate(
      {
        file: importPath.trim(),
        update: importUpdate,
        target: target || undefined,
      },
      {
        onSuccess: (result) => {
          if (result.success) {
            toast({
              title: 'Import complete',
              description: result.message || `${result.imported} imported`,
              variant: 'positive',
            })
          } else {
            toast({
              title: 'Import finished with issues',
              description:
                result.message || `${result.errors?.length ?? 0} errors`,
              variant: 'negative',
            })
          }
        },
        onError: (err) => {
          toast({
            title: 'Import failed',
            description: err.message,
            variant: 'negative',
          })
        },
      }
    )
  }

  const handleAnalyze = (
    sql: string,
    target?: string,
    mostRecentParams?: Record<string, unknown>
  ) => {
    navigate({
      to: '/results',
      search: {
        query: sql.trim(),
        target: target || undefined,
        params:
          mostRecentParams && Object.keys(mostRecentParams).length > 0
            ? JSON.stringify(mostRecentParams)
            : undefined,
      },
    })
  }

  const pageEnd = offset + queries.length
  const hasPrevPage = offset > 0
  const hasNextPage = pageEnd < total
  const hasPagination = hasPrevPage || hasNextPage

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
              <Icon
                name="folder-file"
                label="Saved Queries"
                className="w-6 h-6 text-content-primary-soft"
              />
            </div>
            <VStack className="gap-1 items-start">
              <Text
                as="h1"
                level="headline-3"
                className="text-content-layout-1"
              >
                Saved Queries
              </Text>
              <Text level="body-small" className="text-content-layout-3">
                Your saved SQL queries — reopen one to Analyze or Cache.
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
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3 }}
          >
            <Card className="w-full overflow-hidden">
              <Card.Content className="p-0">
                <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
                  <HStack className="gap-2 items-center">
                    <Icon
                      name="add"
                      label="Add"
                      className="w-4 h-4 text-content-layout-3"
                    />
                    <Text
                      level="overline"
                      className="text-content-layout-3 uppercase tracking-wider"
                    >
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
                        setShowAddForm(false)
                        setNewSql('')
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
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3 }}
          >
            <Card className="w-full overflow-hidden">
              <Card.Content className="p-0">
                <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
                  <HStack className="gap-2 items-center">
                    <Icon
                      name="folder-file"
                      label="Import"
                      className="w-4 h-4 text-content-layout-3"
                    />
                    <Text
                      level="overline"
                      className="text-content-layout-3 uppercase tracking-wider"
                    >
                      Import Queries from File
                    </Text>
                  </HStack>
                </div>
                <div className="p-5">
                  <VStack className="gap-4 items-stretch">
                    <Text level="body-small" className="text-content-layout-3">
                      Import a local .sql file with semicolon-separated queries.
                      Each query may carry optional{' '}
                      <span className="font-mono">-- name:</span> and{' '}
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
                        disabled={
                          !importPath.trim() || importMutation.isPending
                        }
                      />
                    </div>

                    {importMutation.data && (
                      <VStack className="gap-2 items-stretch bg-surface-layout-2/50 rounded-lg p-4 border border-border-layout-1">
                        <HStack className="gap-2 items-center flex-wrap">
                          <Icon
                            name={
                              importMutation.data.success
                                ? 'tick-double'
                                : 'alert'
                            }
                            label="Result"
                            className={`w-4 h-4 ${importMutation.data.success ? 'text-content-positive-soft' : 'text-content-negative-soft'}`}
                          />
                          <Tag
                            size="small"
                            variant="positive"
                            modifier="ghost"
                            label={`${importMutation.data.imported ?? 0} imported`}
                          />
                          <Tag
                            size="small"
                            variant="primary"
                            modifier="ghost"
                            label={`${importMutation.data.updated ?? 0} updated`}
                          />
                          <Tag
                            size="small"
                            variant="warning"
                            modifier="ghost"
                            label={`${importMutation.data.skipped ?? 0} skipped`}
                          />
                          <Tag
                            size="small"
                            variant="negative"
                            modifier="ghost"
                            label={`${importMutation.data.errors?.length ?? 0} errors`}
                          />
                        </HStack>
                        {(importMutation.data.errors ?? []).map(
                          (message, index) => (
                            <HStack
                              key={`import-err-${index}`}
                              className="gap-2 items-center"
                            >
                              <Icon
                                name="alert"
                                label="Error"
                                className="w-3.5 h-3.5 text-content-negative-soft shrink-0"
                              />
                              <Text
                                level="caption"
                                className="text-content-negative-soft"
                              >
                                {message}
                              </Text>
                            </HStack>
                          )
                        )}
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
                        setShowImportForm(false)
                        setImportPath('')
                        importMutation.reset()
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
            {/* Toolbar: search (with clear) + filter-aware count + pagination,
                then a source filter-chip row (All + one chip per source). */}
            <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
              <VStack className="gap-3 items-stretch">
                <HStack className="justify-between items-center gap-4">
                  <div className="relative w-72 max-w-full">
                    <BaseInputText
                      name="search"
                      placeholder="Search queries..."
                      icon="search"
                      iconPosition="left"
                      value={searchTerm}
                      onChange={(e) => {
                        setSearchTerm(e.target.value)
                        resetPagination()
                      }}
                    />
                    <Show when={searchTerm.length > 0}>
                      <button
                        type="button"
                        aria-label="Clear search"
                        onClick={() => {
                          setSearchTerm('')
                          resetPagination()
                        }}
                        className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center justify-center w-6 h-6 rounded-md text-content-layout-3 hover:text-content-layout-1 hover:bg-surface-layout-2 transition-colors cursor-pointer"
                      >
                        <Icon
                          name="close"
                          label="Clear search"
                          className="w-4 h-4"
                        />
                      </button>
                    </Show>
                  </div>
                  <HStack className="gap-3 items-center shrink-0">
                    <Text level="body-small" className="text-content-layout-3">
                      {isFiltered
                        ? `${filteredQueries.length} of ${total}`
                        : `${total} ${total === 1 ? 'query' : 'queries'}`}
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

                {/* Source filter chips — single-select, "All" default, combines
                  with search (AND). Only shown when more than one source exists,
                  otherwise the row is noise. Selected state carries three
                  non-colour cues (tick + solid border + fill) so it never leans
                  on colour alone. [triage §1.6; USE-005/VIS-119; USE-097 mirrors
                  the "Update existing" chip idiom already in this file] */}
                <Show when={sourceChips.length > 1}>
                  <HStack className="gap-1.5 items-center flex-wrap">
                    <FilterChip
                      label="All"
                      count={searchFiltered.length}
                      active={sourceFilter === 'all'}
                      onClick={() => handleSelectSource('all')}
                    />
                    {sourceChips.map((chip) => (
                      <FilterChip
                        key={chip.label}
                        label={chip.label}
                        count={sourceCounts.get(chip.label) ?? 0}
                        active={sourceFilter === chip.label}
                        onClick={() => handleSelectSource(chip.label)}
                      />
                    ))}
                  </HStack>
                </Show>
              </VStack>
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
                    <Icon
                      name="folder-file"
                      label="Loading"
                      className="w-7 h-7 text-content-layout-3 animate-pulse"
                    />
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
                    <Icon
                      name="folder-file"
                      label="Empty"
                      className="w-7 h-7 text-content-layout-3"
                    />
                  </div>
                  <VStack className="gap-2 items-center">
                    <Text level="headline-5" className="text-content-layout-2">
                      {isFiltered ? 'No matching queries' : 'No saved queries'}
                    </Text>
                    <Text
                      level="body-small"
                      className="text-content-layout-3 text-center max-w-sm"
                    >
                      {isFiltered
                        ? 'Try a different search or filter.'
                        : 'Queries you analyze will be saved here for quick access.'}
                    </Text>
                  </VStack>
                  <Show when={isFiltered}>
                    <Button
                      variant="primary"
                      modifier="ghost"
                      size="small"
                      label="Clear filters"
                      icon="close"
                      iconPosition="left"
                      onClick={handleClearFilters}
                    />
                  </Show>
                  <Show when={!isFiltered && !showAddForm}>
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
              <div className="p-3 space-y-2 bg-surface-layout-1">
                <AnimatePresence mode="popLayout">
                  {rowViewModels.map((row) => {
                    const { entry, sourceMeta, displayName, cached, meta } = row
                    const isRenaming = editingHash === entry.hash
                    const isEditingSql = editingSqlHash === entry.hash
                    const isConfirming = confirmingHash === entry.hash

                    return (
                      <m.div
                        key={entry.hash}
                        initial={{ opacity: 0, y: -6 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, x: -16 }}
                        transition={{ duration: 0.18 }}
                      >
                        <QueryCard
                          data-testid="query-registry-row"
                          data-query-hash={entry.hash}
                          sql={entry.sql}
                          meta={meta}
                          title={
                            <Text
                              level="label-medium"
                              className="text-content-layout-1 font-semibold truncate"
                            >
                              {displayName}
                            </Text>
                          }
                          badges={
                            <Tag
                              size="small"
                              variant={sourceMeta.variant}
                              modifier="ghost"
                              label={sourceMeta.label}
                            />
                          }
                          actions={
                            <>
                              <TooltipProvider delayDuration={150}>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <div>
                                      {/* Softened to outline so the page's single
                                          solid primary is "Add Query" — 31 solid row
                                          buttons flattened the hierarchy [Saved 1;
                                          VIS-011/016, VIS-022/023]. */}
                                      <Button
                                        variant="primary"
                                        modifier="outline"
                                        size="small"
                                        icon="speedometer"
                                        iconPosition="left"
                                        label="Analyze"
                                        onClick={() =>
                                          handleAnalyze(
                                            entry.sql,
                                            entry.target,
                                            entry.most_recent_params
                                          )
                                        }
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
                                <Dropdown.Content
                                  align="end"
                                  className="min-w-52"
                                >
                                  <Dropdown.Item
                                    leftIcon={
                                      cached
                                        ? 'tick-double'
                                        : 'database-settings'
                                    }
                                    label={cached ? 'Cached' : 'Cache'}
                                    disabled={
                                      cached || cachingHash === entry.hash
                                    }
                                    onSelect={() =>
                                      handleCacheQuery(entry.hash, entry.sql)
                                    }
                                  />
                                  <Dropdown.Item
                                    leftIcon="filter-edit"
                                    label="Edit SQL"
                                    onSelect={() =>
                                      handleStartEditSql(entry.hash, entry.sql)
                                    }
                                  />
                                  <Dropdown.Item
                                    leftIcon="edit"
                                    label="Rename"
                                    onSelect={() => {
                                      setEditingHash(entry.hash)
                                      setTagDraft(entry.tag || '')
                                    }}
                                  />
                                  <Dropdown.Separator />
                                  <Dropdown.Item
                                    leftIcon="trash"
                                    label="Delete"
                                    className="text-content-negative-soft hover:text-content-negative-soft focus:text-content-negative-soft hover:bg-surface-negative-soft focus:bg-surface-negative-soft"
                                    onSelect={() =>
                                      setConfirmingHash(entry.hash)
                                    }
                                  />
                                </Dropdown.Content>
                              </Dropdown>
                            </>
                          }
                        >
                          {/* Transient inline states own the full surface. */}
                          {isConfirming ? (
                            <div className="flex items-center justify-between gap-4 bg-surface-negative-soft/20 rounded-lg p-4 border border-border-negative-soft/30">
                              <HStack className="gap-3 items-center flex-1 min-w-0">
                                <Icon
                                  name="alert"
                                  label="Warning"
                                  className="w-5 h-5 text-content-negative-soft shrink-0"
                                />
                                <VStack className="gap-1 items-start min-w-0">
                                  <Text
                                    level="label-small"
                                    className="text-content-layout-1"
                                  >
                                    Delete this query?
                                  </Text>
                                  <div className="bg-surface-layout-2 px-2 py-1 rounded max-w-md overflow-hidden">
                                    <SQLDisplay
                                      sql={
                                        entry.sql.length > 60
                                          ? `${entry.sql.slice(0, 60)}...`
                                          : entry.sql
                                      }
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
                                    removeQuery(entry.hash)
                                    setConfirmingHash(null)
                                  }}
                                />
                              </HStack>
                            </div>
                          ) : isRenaming ? (
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
                                onClick={() =>
                                  handleRename(entry.hash, tagDraft.trim())
                                }
                              />
                              <Button
                                variant="primary"
                                modifier="ghost"
                                size="small"
                                icon="close"
                                iconPosition="icon"
                                label="Cancel"
                                onClick={() => {
                                  setEditingHash(null)
                                  setTagDraft('')
                                }}
                              />
                            </HStack>
                          ) : isEditingSql ? (
                            <>
                              <HStack className="gap-2 items-center w-full min-w-0">
                                <Text
                                  level="label-medium"
                                  className="text-content-layout-1 font-semibold truncate"
                                >
                                  {displayName}
                                </Text>
                                <Tag
                                  size="small"
                                  variant={sourceMeta.variant}
                                  modifier="ghost"
                                  label={sourceMeta.label}
                                />
                              </HStack>
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
                            </>
                          ) : null}
                        </QueryCard>
                      </m.div>
                    )
                  })}
                </AnimatePresence>
              </div>
            </Show>
          </Card.Content>
        </Card>
      </m.div>
    </div>
  )
}
