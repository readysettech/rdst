import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { EmptyState } from '@rs/ui-new/empty-state'
import { Icon } from '@rs/ui-new/icon'
import { IconButton } from '@rs/ui-new/icon-button'
import { Label } from '@rs/ui-new/label'
import { AnimatePresence, m } from '@rs/ui-new/motion'
import { Pressable } from '@rs/ui-new/pressable'
import { Show } from '@rs/ui-new/show'
import { HStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import type { SchemaTable, SchemaTableColumn, SchemaTableRelationship } from '../../types/schema'

interface SchemaTableTreeProps {
  tables: SchemaTable[]
  onEditColumn?: (tableName: string, column: SchemaTableColumn) => void
  onEditTable?: (table: SchemaTable) => void
  onEditEnum?: (tableName: string, column: SchemaTableColumn) => void
  onEditRelationship?: (tableName: string, relationship: SchemaTableRelationship) => void
  /** Re-runs introspection; the way out of a layer that holds no tables. */
  onRefreshStructure?: () => void
}

// An enum value counts as documented only when it carries a real meaning. The
// profiler seeds un-curated values with a dev placeholder ("TODO: describe
// '<v>'"); that string must never surface in primary content (audit HIGH). Bare
// or placeholder meanings render as the bare value in a muted "unlabeled" tone
// instead of raw dev text.
const isDocumentedMeaning = (meaning?: string | null): boolean => {
  if (!meaning) return false
  const trimmed = meaning.trim()
  if (trimmed.length === 0) return false
  return !/^todo\b/i.test(trimmed)
}

// A real semantic layer runs to a hundred-plus tables, so the list reveals one
// page at a time and search is the intended way in.
const TABLE_PAGE_SIZE = 40

interface TableMatch {
  table: SchemaTable
  /** The query hit a column rather than the table's own name or description. */
  viaColumn: boolean
}

const contains = (value: string | null | undefined, query: string): boolean =>
  !!value && value.toLowerCase().includes(query)

// Search spans table and column identity plus the annotations that are the
// layer's whole point, so "email" finds the column that carries it.
function matchTables(tables: SchemaTable[], query: string): TableMatch[] {
  if (query.length === 0) {
    return tables.map((table) => ({ table, viaColumn: false }))
  }
  const matches: TableMatch[] = []
  for (const table of tables) {
    const onTable =
      contains(table.name, query) || contains(table.description, query)
    const onColumn = table.columns.some(
      (column) =>
        contains(column.name, query) || contains(column.description, query)
    )
    if (onTable || onColumn) matches.push({ table, viaColumn: !onTable })
  }
  return matches
}

export function SchemaTableTree({
  tables,
  onEditColumn,
  onEditTable,
  onEditEnum,
  onEditRelationship,
  onRefreshStructure,
}: SchemaTableTreeProps) {
  const [expandedTables, setExpandedTables] = useState<Set<string>>(new Set())
  const [search, setSearch] = useState('')
  const [visibleCount, setVisibleCount] = useState(TABLE_PAGE_SIZE)
  const searchRef = useRef<HTMLInputElement>(null)
  const searchId = useId()

  const query = search.trim().toLowerCase()
  const matches = useMemo(() => matchTables(tables, query), [tables, query])
  const shown = matches.slice(0, visibleCount)
  const remaining = matches.length - shown.length

  const changeSearch = (value: string) => {
    setSearch(value)
    setVisibleCount(TABLE_PAGE_SIZE)
  }

  // "/" is the search shortcut every list surface in the app should answer to.
  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if (event.key !== '/') return
      if (event.metaKey || event.ctrlKey || event.altKey) return
      const active = document.activeElement
      if (
        active instanceof HTMLElement &&
        (active.isContentEditable ||
          /^(input|textarea|select)$/i.test(active.tagName))
      ) {
        return
      }
      event.preventDefault()
      searchRef.current?.focus()
    }
    document.addEventListener('keydown', focusSearch)
    return () => document.removeEventListener('keydown', focusSearch)
  }, [])

  const toggleTable = (tableName: string) => {
    setExpandedTables((prev) => {
      const next = new Set(prev)
      if (next.has(tableName)) {
        next.delete(tableName)
      } else {
        next.add(tableName)
      }
      return next
    })
  }

  if (tables.length === 0) {
    return (
      <EmptyState
        icon="layers"
        title="No tables in the semantic layer"
        body="Introspection found nothing to describe. Refresh the structure once the database has tables, or check that this target points at the schema you expect."
        action={
          onRefreshStructure
            ? { label: 'Refresh structure', icon: 'observe', onClick: onRefreshStructure }
            : undefined
        }
      />
    )
  }

  return (
    <div className="divide-y divide-border-layout-1">
      <div className="px-5 py-3">
        <HStack className="gap-3 items-center justify-between flex-wrap">
          <div className="relative w-80 max-w-full">
            <Label htmlFor={searchId} className="sr-only">
              Search tables and columns
            </Label>
            <BaseInputText
              ref={searchRef}
              id={searchId}
              name={searchId}
              placeholder="Search tables and columns"
              icon="search"
              iconPosition="left"
              value={search}
              onChange={(event) => changeSearch(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape' && search.length > 0) {
                  event.preventDefault()
                  changeSearch('')
                }
              }}
            />
            <Show when={search.length > 0}>
              <IconButton
                variant="primary"
                modifier="ghost"
                size="small"
                icon="close"
                label="Clear search"
                onClick={() => changeSearch('')}
                className="absolute right-1 top-1/2 -translate-y-1/2"
              />
            </Show>
            <Show when={search.length === 0}>
              <kbd className="absolute right-3 top-1/2 -translate-y-1/2 rounded-md border border-border-layout-1 bg-surface-raised px-1.5 py-0.5 text-[11px] font-medium text-content-layout-2">
                /
              </kbd>
            </Show>
          </div>
          <Text level="body-small" className="text-content-layout-3">
            {matches.length === tables.length
              ? `${tables.length} ${tables.length === 1 ? 'table' : 'tables'}`
              : `${matches.length} of ${tables.length} tables`}
          </Text>
        </HStack>
      </div>

      <Show when={matches.length === 0}>
        <EmptyState
          layout="compact"
          icon="search"
          title={`No tables match "${search.trim()}"`}
          body="Search covers table and column names and their descriptions."
          action={{ label: 'Show all tables', onClick: () => changeSearch('') }}
        />
      </Show>

      {shown.map(({ table, viaColumn }) => {
        // A column-only match opens the table, showing why it matched.
        const isExpanded = expandedTables.has(table.name) || viaColumn
        return (
          <div key={table.name} className="overflow-hidden">
            {/* Table header */}
            <Pressable
              type="button"
              onClick={() => toggleTable(table.name)}
              className="w-full px-5 py-2 flex items-center gap-3 hover:bg-surface-layout-2/50 transition-colors text-left group"
            >
              <m.div
                animate={{ rotate: isExpanded ? 90 : 0 }}
                transition={{ duration: 0.2 }}
                className="shrink-0"
              >
                <Icon name="chevron-right" label="Expand" className="w-4 h-4 text-content-layout-3" />
              </m.div>
              <div className="flex-1 min-w-0 overflow-hidden">
                <HStack className="gap-2 items-center flex-wrap">
                  <Text level="label-medium" className="text-content-layout-1">
                    {table.name}
                  </Text>
                  <Tag size="small" variant="informative" modifier="ghost" label={`${table.columns.length} cols`} />
                  <Show when={table.relationships.length > 0}>
                    <Tag size="small" variant="positive" modifier="ghost" label={`${table.relationships.length} rels`} />
                  </Show>
                </HStack>
              </div>
              <Icon
                name="edit"
                label="Edit"
                className="w-4 h-4 text-content-layout-3 opacity-60 group-hover:opacity-100 group-focus-visible:opacity-100 transition-opacity"
              />
            </Pressable>

            {/* Expanded content */}
            <AnimatePresence>
              {isExpanded && (
                <m.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  className="overflow-hidden"
                >
                  <div className="px-5 pb-5 bg-surface-layout-2/30">
                    {/* Table actions */}
                    <Show when={!!onEditTable}>
                      <div className="py-3 flex justify-end border-b border-border-layout-1 mb-4">
                        <Button
                          modifier="ghost"
                          size="small"
                          icon="edit"
                          iconPosition="left"
                          label="Edit table"
                          onClick={() => onEditTable?.(table)}
                        />
                      </div>
                    </Show>

                    <Show when={!!table.description}>
                      <Text
                        level="body-small"
                        className="text-content-layout-2 break-words mb-4"
                      >
                        {table.description}
                      </Text>
                    </Show>

                    {/* Business context */}
                    <Show when={!!table.business_context}>
                      <div className="py-3 mb-4 rounded-lg bg-surface-primary-soft/10 border border-border-primary-soft/30 px-4">
                        <HStack className="gap-2 items-center mb-1">
                          <Icon name="info" label="Context" className="w-3.5 h-3.5 text-content-primary-soft" />
                          <Text level="label-small" className="text-content-primary-soft">
                            Business context
                          </Text>
                        </HStack>
                        <Text level="body-small" className="text-content-layout-2 break-words">
                          {table.business_context}
                        </Text>
                      </div>
                    </Show>

                    {/* Columns */}
                    <div className="mb-4">
                      <HStack className="gap-2 items-center mb-3">
                        <Icon name="layers" label="Columns" className="w-3.5 h-3.5 text-content-layout-3" />
                        <Text level="label-small" className="text-content-layout-3 uppercase tracking-wider">
                          Columns
                        </Text>
                      </HStack>
                      <div className="space-y-2">
                        {table.columns.map((col, index) => (
                          <m.div
                            key={col.name}
                            initial={{ opacity: 0, x: -10 }}
                            animate={{ opacity: 1, x: 0 }}
                            transition={{ duration: 0.2, delay: index * 0.03 }}
                            className="py-3 px-4 rounded-lg bg-surface-layout-1 border border-border-layout-1 overflow-hidden"
                          >
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0 flex-1 overflow-hidden">
                                <HStack className="gap-2 items-center flex-wrap">
                                  <code className="text-sm font-mono text-content-layout-1 bg-surface-layout-2 px-2 py-0.5 rounded">
                                    {col.name}
                                  </code>
                                  <Show when={!!col.data_type}>
                                    <Text level="caption" className="text-content-layout-3">
                                      {col.data_type}
                                    </Text>
                                  </Show>
                                  <Show when={col.is_pii}>
                                    <Tag size="small" label="PII" variant="negative" modifier="solid" />
                                  </Show>
                                </HStack>
                                <Show when={!!col.description}>
                                  <Text level="body-small" className="text-content-layout-2 break-words whitespace-pre-wrap mt-2">
                                    {col.description}
                                  </Text>
                                </Show>
                                <Show when={col.enum_values && Object.keys(col.enum_values).length > 0}>
                                  <div className="mt-3 pt-3 border-t border-border-layout-1">
                                    <Text level="label-small" className="text-content-layout-3 mb-2">
                                      Enum values
                                    </Text>
                                    <div className="flex flex-wrap gap-1.5">
                                      {Object.entries(col.enum_values || {})
                                        .slice(0, 8)
                                        .map(([value, meaning]) =>
                                          isDocumentedMeaning(meaning) ? (
                                            <Tag
                                              key={value}
                                              size="small"
                                              modifier="outline"
                                              label={`${value}: ${meaning}`}
                                            />
                                          ) : (
                                            <Tag
                                              key={value}
                                              size="small"
                                              variant="muted"
                                              modifier="outline"
                                              label={value}
                                              title="Value meaning not documented yet"
                                            />
                                          ),
                                        )}
                                      <Show when={Object.keys(col.enum_values || {}).length > 8}>
                                        <Tag
                                          size="small"
                                          modifier="ghost"
                                          label={`+${Object.keys(col.enum_values || {}).length - 8} more`}
                                        />
                                      </Show>
                                    </div>
                                  </div>
                                </Show>
                              </div>
                              <HStack className="gap-1 shrink-0">
                                <Show when={!!onEditEnum && (Object.keys(col.enum_values ?? {}).length > 0 || col.data_type === 'enum')}>
                                  <Button
                                    modifier="ghost"
                                    size="small"
                                    icon="menu"
                                    iconPosition="icon"
                                    label="Enum"
                                    onClick={() => onEditEnum?.(table.name, col)}
                                  />
                                </Show>
                                <Show when={!!onEditColumn}>
                                  <Button
                                    modifier="ghost"
                                    size="small"
                                    icon="edit"
                                    iconPosition="icon"
                                    label="Edit"
                                    onClick={() => onEditColumn?.(table.name, col)}
                                  />
                                </Show>
                              </HStack>
                            </div>
                          </m.div>
                        ))}
                      </div>
                    </div>

                    {/* Relationships */}
                    <Show when={table.relationships.length > 0}>
                      <div className="pt-4 border-t border-border-layout-1">
                        <HStack className="gap-2 items-center mb-3">
                          <Icon name="connect" label="Relationships" className="w-3.5 h-3.5 text-content-layout-3" />
                          <Text level="label-small" className="text-content-layout-3 uppercase tracking-wider">
                            Relationships
                          </Text>
                        </HStack>
                        <div className="space-y-2">
                          {table.relationships.map((rel, idx) => (
                            <m.div
                              key={idx}
                              initial={{ opacity: 0, x: -10 }}
                              animate={{ opacity: 1, x: 0 }}
                              transition={{ duration: 0.2, delay: idx * 0.03 }}
                              className="flex items-center justify-between gap-3 py-2 px-3 rounded-lg bg-surface-layout-1 border border-border-layout-1"
                            >
                              <HStack className="gap-3 items-center flex-wrap min-w-0 flex-1">
                                <HStack className="gap-2 items-center">
                                  <Icon name="arrow-right" label="To" className="w-4 h-4 text-content-layout-3" />
                                  <Text level="label-small" className="text-content-layout-1">
                                    {rel.target_table}
                                  </Text>
                                </HStack>
                                <Tag size="small" label={rel.relationship_type} modifier="outline" />
                                <code className="text-xs text-content-layout-3 font-mono break-all bg-surface-layout-2 px-2 py-0.5 rounded">
                                  {rel.join_pattern}
                                </code>
                              </HStack>
                              <Show when={!!onEditRelationship}>
                                <Button
                                  modifier="ghost"
                                  size="small"
                                  icon="edit"
                                  iconPosition="icon"
                                  label="Edit"
                                  onClick={() => onEditRelationship?.(table.name, rel)}
                                />
                              </Show>
                            </m.div>
                          ))}
                        </div>
                      </div>
                    </Show>
                  </div>
                </m.div>
              )}
            </AnimatePresence>
          </div>
        )
      })}

      <Show when={remaining > 0}>
        <div className="px-5 py-3">
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label={`Show ${Math.min(remaining, TABLE_PAGE_SIZE)} more tables`}
            onClick={() => setVisibleCount((count) => count + TABLE_PAGE_SIZE)}
          />
        </div>
      </Show>
    </div>
  )
}
