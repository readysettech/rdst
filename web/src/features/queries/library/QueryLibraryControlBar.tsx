import { cn } from '@rs/tailwind-base'
import { BaseInputSelect } from '@rs/ui-new/base-input-select'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Dropdown } from '@rs/ui-new/dropdown'
import { Icon } from '@rs/ui-new/icon'
import { IconButton } from '@rs/ui-new/icon-button'
import { Label } from '@rs/ui-new/label'
import { Show } from '@rs/ui-new/show'
import { HStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useState } from 'react'
import {
  QUERY_LIBRARY_DEFAULT_DISPLAY_PROPERTIES,
  QUERY_LIBRARY_DISPLAY_MODES,
  QUERY_LIBRARY_DISPLAY_PROPERTIES,
  type QueryLibraryDisplayMode,
  type QueryLibraryDisplayProperty,
} from './queryLibraryDisplay'
import {
  QUERY_LIBRARY_ACTIVITY_LABELS,
  QUERY_LIBRARY_IMPACT_LABELS,
  QUERY_LIBRARY_PARAMETER_LABELS,
  QUERY_LIBRARY_SORT_LABELS,
  QUERY_LIBRARY_SOURCE_LABELS,
  QUERY_LIBRARY_VIEW_LABELS,
  type QueryLibrarySelection,
} from './queryLibrarySelectors'
import {
  QUERY_LIBRARY_ACTIVITY_WINDOWS,
  QUERY_LIBRARY_IMPACT_FILTERS,
  QUERY_LIBRARY_PARAMETER_FILTERS,
  QUERY_LIBRARY_SORTS,
  QUERY_LIBRARY_SOURCES,
  QUERY_LIBRARY_VIEWS,
  type QueryLibraryActivityWindow,
  type QueryLibraryFilterKey,
  type QueryLibraryImpactFilter,
  type QueryLibraryParameterFilter,
  type QueryLibrarySort,
  type QueryLibrarySource,
  type QueryLibraryView,
} from './queryLibraryState'

export type QueryLibraryFilters = {
  view: QueryLibraryView
  source: QueryLibrarySource
  params: QueryLibraryParameterFilter
  activity: QueryLibraryActivityWindow
  impact: QueryLibraryImpactFilter
}

type FilterCategory = {
  key: QueryLibraryFilterKey
  label: string
  icon:
    | 'filter-edit'
    | 'database'
    | 'adjustment-horizontal'
    | 'speedometer'
    | 'observe'
}

const FILTER_CATEGORIES: FilterCategory[] = [
  { key: 'view', label: 'Status', icon: 'filter-edit' },
  { key: 'source', label: 'Source', icon: 'database' },
  {
    key: 'params',
    label: 'Readiness',
    icon: 'adjustment-horizontal',
  },
  { key: 'impact', label: 'Database time', icon: 'speedometer' },
  { key: 'activity', label: 'Last activity', icon: 'observe' },
]

const FILTER_DEFAULTS: QueryLibraryFilters = {
  view: 'all',
  source: 'all',
  params: 'all',
  activity: 'all',
  impact: 'all',
}

const FILTER_LABELS = {
  view: QUERY_LIBRARY_VIEW_LABELS,
  source: QUERY_LIBRARY_SOURCE_LABELS,
  params: QUERY_LIBRARY_PARAMETER_LABELS,
  activity: QUERY_LIBRARY_ACTIVITY_LABELS,
  impact: QUERY_LIBRARY_IMPACT_LABELS,
}

type QueryLibraryControlBarProps = {
  idPrefix: string
  searchTerm: string
  onSearchChange: (value: string) => void
  filters: QueryLibraryFilters
  onFilterChange: <Key extends QueryLibraryFilterKey>(
    key: Key,
    value: QueryLibraryFilters[Key]
  ) => void
  onClearFilter: (key: QueryLibraryFilterKey) => void
  onClearFilters: () => void
  sort: QueryLibrarySort
  onSortChange: (sort: QueryLibrarySort) => void
  displayMode: QueryLibraryDisplayMode
  onDisplayModeChange: (mode: QueryLibraryDisplayMode) => void
  properties: QueryLibraryDisplayProperty[]
  onToggleProperty: (property: QueryLibraryDisplayProperty) => void
  selection: Pick<QueryLibrarySelection, 'facetCounts'>
  resultCount?: number
}

function filterValueLabel<Key extends QueryLibraryFilterKey>(
  key: Key,
  value: QueryLibraryFilters[Key]
) {
  return FILTER_LABELS[key][value as never]
}

function ActiveFilterChip({
  filterKey,
  value,
  onClear,
}: {
  filterKey: QueryLibraryFilterKey
  value: QueryLibraryFilters[QueryLibraryFilterKey]
  onClear: () => void
}) {
  if (value === FILTER_DEFAULTS[filterKey]) return null
  const category = FILTER_CATEGORIES.find(({ key }) => key === filterKey)
  if (!category) return null

  return (
    <Button
      size="small"
      variant="primary"
      modifier="ghost"
      label={`${category.label}: ${filterValueLabel(filterKey, value)}`}
      icon="close"
      iconPosition="right"
      aria-label={`Remove ${category.label} filter`}
      onClick={onClear}
    />
  )
}

function FilterMenu({
  idPrefix,
  filters,
  selection,
  onFilterChange,
}: Pick<QueryLibraryControlBarProps, 'idPrefix'> &
  Pick<
    QueryLibraryControlBarProps,
    'filters' | 'selection' | 'onFilterChange'
  >) {
  const [open, setOpen] = useState(false)
  const activeFilterCount = Object.entries(filters).filter(
    ([key, value]) => value !== FILTER_DEFAULTS[key as QueryLibraryFilterKey]
  ).length

  const setFilterValue = (key: QueryLibraryFilterKey, value: string) => {
    switch (key) {
      case 'view':
        onFilterChange('view', value as QueryLibraryView)
        break
      case 'source':
        onFilterChange('source', value as QueryLibrarySource)
        break
      case 'params':
        onFilterChange('params', value as QueryLibraryParameterFilter)
        break
      case 'impact':
        onFilterChange('impact', value as QueryLibraryImpactFilter)
        break
      case 'activity':
        onFilterChange('activity', value as QueryLibraryActivityWindow)
        break
    }
  }

  const optionsFor = (key: QueryLibraryFilterKey) => {
    switch (key) {
      case 'view':
        return QUERY_LIBRARY_VIEWS.map((value) => ({
          value,
          label: `${QUERY_LIBRARY_VIEW_LABELS[value]} · ${selection.facetCounts.view[value]}`,
          disabled:
            selection.facetCounts.view[value] === 0 && value !== filters.view,
        }))
      case 'source':
        return QUERY_LIBRARY_SOURCES.map((value) => ({
          value,
          label: `${QUERY_LIBRARY_SOURCE_LABELS[value]} · ${selection.facetCounts.source[value]}`,
          disabled:
            selection.facetCounts.source[value] === 0 &&
            value !== filters.source,
        }))
      case 'params':
        return QUERY_LIBRARY_PARAMETER_FILTERS.map((value) => ({
          value,
          label: `${QUERY_LIBRARY_PARAMETER_LABELS[value]} · ${selection.facetCounts.params[value]}`,
          disabled:
            selection.facetCounts.params[value] === 0 &&
            value !== filters.params,
        }))
      case 'impact':
        return QUERY_LIBRARY_IMPACT_FILTERS.map((value) => ({
          value,
          label: `${QUERY_LIBRARY_IMPACT_LABELS[value]} · ${selection.facetCounts.impact[value]}`,
          disabled:
            selection.facetCounts.impact[value] === 0 &&
            value !== filters.impact,
        }))
      case 'activity':
        return QUERY_LIBRARY_ACTIVITY_WINDOWS.map((value) => ({
          value,
          label: `${QUERY_LIBRARY_ACTIVITY_LABELS[value]} · ${selection.facetCounts.activity[value]}`,
          disabled:
            selection.facetCounts.activity[value] === 0 &&
            value !== filters.activity,
        }))
    }
  }

  return (
    <Dropdown open={open} onOpenChange={setOpen}>
      <Dropdown.Trigger asChild>
        <Button
          size="base"
          variant="primary"
          modifier="ghost"
          label=""
          aria-label="Filter"
          aria-expanded={open}
          innerClassName="gap-1.5"
        >
          <Icon name="filter" label="" aria-hidden="true" className="h-4 w-4" />
          <span>Filter</span>
          <Show when={activeFilterCount > 0}>
            <span className="flex h-5 min-w-5 items-center justify-center rounded-full bg-surface-primary-solid px-1 text-label-extra-small text-content-primary-solid">
              {activeFilterCount}
            </span>
          </Show>
          <Icon
            name="chevron-down"
            label=""
            aria-hidden="true"
            className={cn(
              'h-3.5 w-3.5 transition-transform duration-fast ease-base',
              open && 'rotate-180'
            )}
          />
        </Button>
      </Dropdown.Trigger>
      <Dropdown.Content
        align="start"
        className="w-[30rem] max-w-[calc(100vw-2rem)] bg-surface-layout-2 p-0 shadow-elevation-3"
      >
        <Dropdown.Label>Filter queries</Dropdown.Label>
        <div className="divide-y divide-border-layout-1 px-4">
          {FILTER_CATEGORIES.map((category) => {
            const filterId = `${idPrefix}-filter-${category.key}`
            return (
              <div
                key={category.key}
                className="grid grid-cols-[8.5rem_minmax(0,1fr)] items-center gap-4 py-3"
              >
                <HStack className="min-w-0 items-center gap-2">
                  <Icon
                    name={category.icon}
                    label=""
                    aria-hidden="true"
                    className="h-4 w-4 text-content-layout-3"
                  />
                  <Label
                    htmlFor={filterId}
                    className="whitespace-nowrap text-label-small text-content-layout-2"
                  >
                    {category.label}
                  </Label>
                </HStack>
                <BaseInputSelect
                  id={filterId}
                  name={filterId}
                  value={filters[category.key]}
                  options={optionsFor(category.key)}
                  onValueChange={(value) => setFilterValue(category.key, value)}
                />
              </div>
            )
          })}
        </div>
      </Dropdown.Content>
    </Dropdown>
  )
}

function DisplayMenu({
  displayMode,
  onDisplayModeChange,
  properties,
  onToggleProperty,
}: Pick<
  QueryLibraryControlBarProps,
  'displayMode' | 'onDisplayModeChange' | 'properties' | 'onToggleProperty'
>) {
  return (
    <Dropdown>
      <Dropdown.Trigger asChild>
        <Button
          size="base"
          variant="primary"
          modifier="ghost"
          label=""
          aria-label="Display"
          innerClassName="gap-1.5"
        >
          <Icon
            name="adjustment-horizontal"
            label=""
            aria-hidden="true"
            className="h-4 w-4"
          />
          <span>Display</span>
          <Icon
            name="chevron-down"
            label=""
            aria-hidden="true"
            className="h-3.5 w-3.5"
          />
        </Button>
      </Dropdown.Trigger>
      <Dropdown.Content align="end" className="w-96 p-0">
        <Dropdown.Label>Display options</Dropdown.Label>
        <div className="grid grid-cols-3 gap-2 border-b border-border-layout-1 p-3">
          {QUERY_LIBRARY_DISPLAY_MODES.map((option) => {
            const selected = displayMode === option.value
            return (
              <Button
                key={option.value}
                size="base"
                variant="primary"
                modifier="ghost"
                label={option.label}
                icon={option.icon}
                iconPosition="left"
                aria-pressed={selected}
                onClick={() => onDisplayModeChange(option.value)}
                className={cn(
                  'justify-center',
                  selected
                    ? 'bg-surface-primary-soft text-content-primary-soft'
                    : 'bg-surface-layout-2 text-content-layout-2 hover:bg-surface-raised'
                )}
              />
            )
          })}
        </div>
        <div className="space-y-3 p-4">
          <Text level="overline" className="text-content-layout-3">
            Visible properties
          </Text>
          <div className="flex flex-wrap gap-2">
            {QUERY_LIBRARY_DISPLAY_PROPERTIES.map((property) => {
              const selected = properties.includes(property.value)
              return (
                <Button
                  key={property.value}
                  size="small"
                  variant="primary"
                  modifier="ghost"
                  label={property.label}
                  icon={selected ? 'tick' : 'add'}
                  iconPosition="left"
                  aria-pressed={selected}
                  onClick={() => onToggleProperty(property.value)}
                  className={cn(
                    !selected &&
                      'bg-surface-layout-2 text-content-layout-2 hover:bg-surface-raised'
                  )}
                />
              )
            })}
          </div>
          <Show when={properties.length === 0}>
            <Text level="caption" className="text-content-warning-soft">
              Query identity remains visible; optional context is hidden.
            </Text>
          </Show>
        </div>
      </Dropdown.Content>
    </Dropdown>
  )
}

export function QueryLibraryControlBar({
  idPrefix,
  searchTerm,
  onSearchChange,
  filters,
  onFilterChange,
  onClearFilter,
  onClearFilters,
  sort,
  onSortChange,
  displayMode,
  onDisplayModeChange,
  properties = QUERY_LIBRARY_DEFAULT_DISPLAY_PROPERTIES,
  onToggleProperty,
  selection,
  resultCount,
}: QueryLibraryControlBarProps) {
  const activeFilterCount = Object.entries(filters).filter(
    ([key, value]) => value !== FILTER_DEFAULTS[key as QueryLibraryFilterKey]
  ).length
  const searchId = `${idPrefix}-search`
  const sortId = `${idPrefix}-sort`

  return (
    <div className="space-y-3">
      <HStack className="items-center justify-between gap-3 flex-wrap">
        <HStack className="items-center gap-2">
          <FilterMenu
            idPrefix={idPrefix}
            filters={filters}
            selection={selection}
            onFilterChange={onFilterChange}
          />
          <DisplayMenu
            displayMode={displayMode}
            onDisplayModeChange={onDisplayModeChange}
            properties={properties}
            onToggleProperty={onToggleProperty}
          />
          <div className="w-48 max-w-full">
            <Label htmlFor={sortId} className="sr-only">
              Order queries
            </Label>
            <BaseInputSelect
              id={sortId}
              name={sortId}
              value={sort}
              options={QUERY_LIBRARY_SORTS.map((value) => ({
                value,
                label: QUERY_LIBRARY_SORT_LABELS[value],
              }))}
              onValueChange={(value) => onSortChange(value as QueryLibrarySort)}
            />
          </div>
          <Show when={typeof resultCount === 'number'}>
            <Text level="body-small" className="px-1 text-content-layout-3">
              {resultCount} {resultCount === 1 ? 'query' : 'queries'}
            </Text>
          </Show>
        </HStack>

        <div className="relative ml-auto w-80 max-w-full">
          <Label htmlFor={searchId} className="sr-only">
            Search by name, hash, or SQL
          </Label>
          <BaseInputText
            id={searchId}
            name={searchId}
            placeholder="Search name, hash, or SQL..."
            icon="search"
            iconPosition="left"
            value={searchTerm}
            onChange={(event) => onSearchChange(event.target.value)}
          />
          <Show when={searchTerm.length > 0}>
            <IconButton
              variant="primary"
              modifier="ghost"
              size="small"
              icon="close"
              label="Clear search"
              onClick={() => onSearchChange('')}
              className="absolute right-1 top-1/2 -translate-y-1/2"
            />
          </Show>
        </div>
      </HStack>

      <Show when={activeFilterCount > 0}>
        <HStack className="items-center gap-2 flex-wrap">
          {(Object.keys(FILTER_DEFAULTS) as QueryLibraryFilterKey[]).map(
            (filterKey) => (
              <ActiveFilterChip
                key={filterKey}
                filterKey={filterKey}
                value={filters[filterKey]}
                onClear={() => onClearFilter(filterKey)}
              />
            )
          )}
          <Button
            size="small"
            variant="primary"
            modifier="link"
            label="Clear filters"
            onClick={onClearFilters}
          />
        </HStack>
      </Show>
    </div>
  )
}
