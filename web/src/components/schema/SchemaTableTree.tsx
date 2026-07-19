import { useState } from 'react'
import { Text } from '@rs/ui-new/text'
import { Tag } from '@rs/ui-new/tag'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Show } from '@rs/ui-new/show'
import { m, AnimatePresence } from '@rs/ui-new/motion'
import type { SchemaTable, SchemaTableColumn, SchemaTableRelationship } from '../../types/schema'

interface SchemaTableTreeProps {
  tables: SchemaTable[]
  onEditColumn?: (tableName: string, column: SchemaTableColumn) => void
  onEditTable?: (table: SchemaTable) => void
  onEditEnum?: (tableName: string, column: SchemaTableColumn) => void
  onEditRelationship?: (tableName: string, relationship: SchemaTableRelationship) => void
}

export function SchemaTableTree({
  tables,
  onEditColumn,
  onEditTable,
  onEditEnum,
  onEditRelationship,
}: SchemaTableTreeProps) {
  const [expandedTables, setExpandedTables] = useState<Set<string>>(new Set())

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
      <div className="px-6 py-12">
        <VStack className="gap-3 items-center">
          <div className="w-12 h-12 rounded-xl bg-surface-layout-2 flex items-center justify-center">
            <Icon name="layers" label="No tables" className="w-6 h-6 text-content-layout-3" />
          </div>
          <Text level="body-medium" className="text-content-layout-3">
            No tables in semantic layer
          </Text>
        </VStack>
      </div>
    )
  }

  return (
    <div className="divide-y divide-border-layout-1">
      {tables.map((table) => {
        const isExpanded = expandedTables.has(table.name)
        return (
          <div key={table.name} className="overflow-hidden">
            {/* Table header */}
            <button
              type="button"
              onClick={() => toggleTable(table.name)}
              className="w-full px-5 py-4 flex items-center gap-3 hover:bg-surface-layout-2/50 transition-colors text-left group"
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
                <Show when={!!table.description}>
                  <Text
                    level="body-small"
                    className="text-content-layout-3 line-clamp-1 break-words mt-0.5"
                  >
                    {table.description}
                  </Text>
                </Show>
              </div>
              <Icon
                name="edit"
                label="Edit"
                className="w-4 h-4 text-content-layout-3 opacity-0 group-hover:opacity-100 transition-opacity"
              />
            </button>

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
                          label="Edit Table"
                          onClick={() => onEditTable?.(table)}
                        />
                      </div>
                    </Show>

                    {/* Business context */}
                    <Show when={!!table.business_context}>
                      <div className="py-3 mb-4 rounded-lg bg-surface-primary-soft/10 border border-border-primary-soft/30 px-4">
                        <HStack className="gap-2 items-center mb-1">
                          <Icon name="info" label="Context" className="w-3.5 h-3.5 text-content-primary-soft" />
                          <Text level="label-small" className="text-content-primary-soft">
                            Business Context
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
                                      Enum Values
                                    </Text>
                                    <div className="flex flex-wrap gap-1.5">
                                      {Object.entries(col.enum_values || {})
                                        .slice(0, 8)
                                        .map(([value, meaning]) => (
                                          <Tag
                                            key={value}
                                            size="small"
                                            modifier="outline"
                                            label={meaning ? `${value}: ${meaning}` : value}
                                          />
                                        ))}
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
                                <Show when={!!onEditEnum && (Object.keys(col.enum_values ?? {}).length > 0 || col.data_type?.includes('enum'))}>
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
    </div>
  )
}
