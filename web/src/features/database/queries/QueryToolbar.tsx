// The one filter bar of the query list (owner review 3): the time range first (it
// scopes the chart and the list), then search, status (with the classification
// findings as its second group), table and sort. It sticks to the top while the list
// scrolls.
import { cn } from '@rs/tailwind-base'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import {
  Dropdown,
  DropdownContent,
  DropdownItem,
  DropdownLabel,
  DropdownSeparator,
  DropdownTrigger,
} from '@rs/ui-new/dropdown-rf'
import { Icon } from '@rs/ui-new/icon'
import { IconButton } from '@rs/ui-new/icon-button'
import { useEffect, useRef } from 'react'
import { type LiveRange, RangeControl } from '@/features/monitor-kit/charts'
import type { HistorySpan } from '@/lib/monitor/types'
import { CustomRangePicker } from './CustomRangePicker'
import { CLASSIFICATION_FILTERS } from './jev'
import {
  FILTER_LABELS,
  type FilterCounts,
  QUERY_SORTS,
  type QueryFilter,
  type QuerySort,
  SORT_HINTS,
  SORT_LABELS,
} from './querySelectors'
import { QUERY_STATUSES } from './queryStatus'

function isTypingTarget(element: EventTarget | null) {
  if (!(element instanceof HTMLElement)) return false
  return (
    element.isContentEditable ||
    ['INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName)
  )
}

interface MenuOption<T extends string> {
  value: T
  label: string
  /** Queries behind the option, right-aligned in the menu. */
  count?: number
  hint?: string
  group?: string
}

function Menu<T extends string>({
  label,
  value,
  valueLabel,
  options,
  onChange,
  active,
  showLabel = active,
  testId,
}: {
  label: string
  value: T
  /** The trigger's text; with `active`, it follows the menu's name. */
  valueLabel: string
  options: MenuOption<T>[]
  onChange: (value: T) => void
  /** Highlights the trigger when the filter narrows the list. */
  active?: boolean
  /** Shows the menu's name before its value (on by default while active). */
  showLabel?: boolean
  testId?: string
}) {
  return (
    <Dropdown>
      <DropdownTrigger asChild>
        <Button
          variant="primary"
          modifier={active ? 'outline' : 'ghost'}
          size="small"
          label=""
          aria-label={showLabel ? `${label}, ${valueLabel}` : valueLabel}
          innerClassName="gap-1.5"
          data-testid={testId}
          className={cn(active && 'border-border-primary-soft')}
        >
          {showLabel ? (
            <span className="text-caption text-content-layout-3">{label}</span>
          ) : null}
          <span className="max-w-44 truncate">{valueLabel}</span>
          <Icon
            name="chevron-down"
            label=""
            aria-hidden="true"
            className="h-3.5 w-3.5 shrink-0"
          />
        </Button>
      </DropdownTrigger>
      <DropdownContent
        align="start"
        className="max-h-80 min-w-56 overflow-y-auto"
      >
        {options.map((option, index) => (
          <div key={option.value}>
            {option.group && option.group !== options[index - 1]?.group ? (
              <>
                {index > 0 ? <DropdownSeparator /> : null}
                <DropdownLabel>{option.group}</DropdownLabel>
              </>
            ) : null}
            <DropdownItem
              active={option.value === value}
              onSelect={() => onChange(option.value)}
              className="[&>div]:w-full [&>div]:min-w-0"
            >
              <span title={option.hint} className="min-w-0 truncate">
                {option.label}
              </span>
              {option.count != null ? (
                <span
                  className={cn(
                    'ml-auto shrink-0 pl-4 text-label-medium tabular-nums text-content-layout-3',
                    option.count === 0 && 'opacity-60'
                  )}
                >
                  {option.count}
                </span>
              ) : null}
            </DropdownItem>
          </div>
        ))}
      </DropdownContent>
    </Dropdown>
  )
}

/** The zoomed window: the list's main filter when it's set. */
export interface TimeFrame {
  /** "2:10 to 2:25 PM", "Mon, Sep 28, 2:10 PM to 3:25 PM". */
  label: string
  /** Opened from an Inbox item. */
  fromInbox?: boolean
  onClear: () => void
}

function TimeFrameChip({ frame }: { frame: TimeFrame }) {
  return (
    <span
      className="inline-flex h-8 max-w-full items-center gap-1.5 rounded-pill border border-border-primary-soft bg-surface-primary-soft pr-1 pl-3 text-label-small text-content-primary-soft"
      data-testid="time-frame-chip"
    >
      <span className="shrink-0 text-caption text-content-layout-3">
        {frame.fromInbox ? 'From your Inbox' : 'Time frame'}
      </span>
      <span className="truncate tabular-nums">{frame.label}</span>
      <IconButton
        variant="primary"
        modifier="ghost"
        size="small"
        icon="close"
        label="Remove the time frame"
        onClick={frame.onClear}
        className="h-6 w-6"
      />
    </span>
  )
}

/** The list narrowed to an Inbox item's queries: '3 queries from this alert'. */
function PickedChip({
  count,
  onClear,
}: {
  count: number
  onClear: () => void
}) {
  return (
    <span
      className="inline-flex h-8 max-w-full items-center gap-1.5 rounded-pill border border-border-primary-soft bg-surface-primary-soft pr-1 pl-3 text-label-small text-content-primary-soft"
      data-testid="picked-queries-chip"
    >
      <span className="truncate tabular-nums">
        {count === 1 ? '1 query' : `${count} queries`} from this alert
      </span>
      <IconButton
        variant="primary"
        modifier="ghost"
        size="small"
        icon="close"
        label="Show every query"
        onClick={onClear}
        className="h-6 w-6"
      />
    </span>
  )
}

export interface QueryToolbarProps {
  range?: LiveRange
  onRangeChange?: (range: LiveRange) => void
  ranges?: readonly LiveRange[]
  history?: HistorySpan | null
  timeFrame?: TimeFrame | null
  /** The window shown now; a custom time frame starts from it. */
  domain?: [number, number]
  /** Sets a custom time frame (from/to), the same as a zoom. */
  onCustomRange?: (window: [number, number]) => void
  search: string
  onSearchChange: (value: string) => void
  filter: QueryFilter
  onFilterChange: (value: QueryFilter) => void
  sort: QuerySort
  onSortChange: (value: QuerySort) => void
  counts: FilterCounts
  /** Tables in the window with how many queries touch each. */
  tables?: { table: string; count: number }[]
  table?: string | null
  onTableChange?: (table: string | null) => void
  /** The Inbox item's queries the list is narrowed to (`queries=` in the URL). */
  picked?: readonly string[] | null
  onClearPicked?: () => void
  className?: string
}

export function QueryToolbar({
  range,
  onRangeChange,
  ranges,
  history,
  timeFrame = null,
  domain,
  onCustomRange,
  search,
  onSearchChange,
  filter,
  onFilterChange,
  sort,
  onSortChange,
  counts,
  tables = [],
  table = null,
  onTableChange,
  picked = null,
  onClearPicked,
  className,
}: QueryToolbarProps) {
  const inputRef = useRef<HTMLInputElement>(null)

  // `/` focuses search from anywhere on the page, like the Queries page.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey)
        return
      if (isTypingTarget(event.target)) return
      event.preventDefault()
      inputRef.current?.focus()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  const narrowed =
    filter !== 'all' ||
    Boolean(table) ||
    Boolean(search.trim()) ||
    Boolean(picked)

  return (
    <div
      role="toolbar"
      aria-label="Query filters"
      className={className}
      data-testid="query-toolbar"
    >
      <div className="flex flex-wrap items-center gap-2">
        {range && onRangeChange ? (
          <div className="flex max-w-full items-center gap-1">
            <RangeControl<LiveRange>
              value={range}
              onChange={onRangeChange}
              history={history}
              ranges={ranges}
              className="min-w-0"
            />
            {domain && onCustomRange ? (
              <CustomRangePicker
                domain={domain}
                history={history}
                active={Boolean(timeFrame) && !timeFrame?.fromInbox}
                onApply={onCustomRange}
              />
            ) : null}
          </div>
        ) : null}
        {timeFrame ? <TimeFrameChip frame={timeFrame} /> : null}
        {picked && onClearPicked ? (
          <PickedChip count={picked.length} onClear={onClearPicked} />
        ) : null}
        <Menu
          label="Status"
          value={filter}
          valueLabel={
            filter === 'all'
              ? 'Status'
              : `${FILTER_LABELS[filter]} (${counts[filter]})`
          }
          active={filter !== 'all'}
          onChange={onFilterChange}
          testId="filter-status"
          options={[
            {
              value: 'all' as QueryFilter,
              label: FILTER_LABELS.all,
              count: counts.all,
            },
            ...QUERY_STATUSES.map((value) => ({
              value: value as QueryFilter,
              label: FILTER_LABELS[value],
              count: counts[value],
              hint:
                value === 'attention'
                  ? 'Queries with an open Inbox item, Watch included'
                  : undefined,
              group: 'Status',
            })),
            {
              value: 'analyzed' as QueryFilter,
              label: FILTER_LABELS.analyzed,
              count: counts.analyzed,
              group: 'Status',
            },
            ...CLASSIFICATION_FILTERS.map((value) => ({
              value: value as QueryFilter,
              label: FILTER_LABELS[value],
              count: counts[value],
              group: 'Classification',
            })),
          ]}
        />
        {onTableChange && tables.length > 0 ? (
          <Menu
            label="Table"
            value={table ?? ''}
            valueLabel={table ?? 'Tables'}
            active={Boolean(table)}
            onChange={(value) => onTableChange(value || null)}
            testId="filter-table"
            options={[
              { value: '', label: 'All tables' },
              ...tables.map(({ table: name, count }) => ({
                value: name,
                label: name,
                count,
              })),
            ]}
          />
        ) : null}
        <Menu
          label="Sort"
          value={sort}
          valueLabel={SORT_LABELS[sort]}
          showLabel
          onChange={onSortChange}
          testId="sort"
          options={QUERY_SORTS.map((value) => ({
            value,
            label: SORT_LABELS[value],
            hint: SORT_HINTS[value],
          }))}
        />
        {narrowed ? (
          <Button
            variant="primary"
            modifier="link"
            size="small"
            label="Clear filters"
            onClick={() => {
              onSearchChange('')
              onFilterChange('all')
              onTableChange?.(null)
              onClearPicked?.()
            }}
          />
        ) : null}
        <div className="relative min-w-40 flex-1">
          <BaseInputText
            ref={inputRef}
            id="database-query-search"
            name="database-query-search"
            placeholder="Search"
            aria-label="Search queries"
            icon="search"
            iconPosition="left"
            value={search}
            onChange={(event) => onSearchChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                onSearchChange('')
                event.currentTarget.blur()
              }
            }}
          />
          {search ? (
            <IconButton
              variant="primary"
              modifier="ghost"
              size="small"
              icon="close"
              label="Clear search"
              onClick={() => {
                onSearchChange('')
                inputRef.current?.focus()
              }}
              className="absolute top-1/2 right-1 -translate-y-1/2"
            />
          ) : (
            <kbd className="pointer-events-none absolute top-1/2 right-3 hidden -translate-y-1/2 rounded-md border border-border-layout-1 px-1.5 font-mono text-mono-small text-content-layout-3 tablet:block">
              /
            </kbd>
          )}
        </div>
      </div>
    </div>
  )
}
