// The filter and sort row under a page bar, one markup on every page that filters: the scope
// with its counts, the database, optional kind chips (none on means all) and an optional sort
// at the far right.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Dropdown } from '@rs/ui-new/dropdown'
import { Pressable } from '@rs/ui-new/pressable'
import { SegmentedControl } from '@rs/ui-new/segmented-control'
import type { ReactNode } from 'react'
import type { ConfiguredDatabase } from '@/lib/monitor/hooks'
import { DatabaseFilter, filterItemClass, Tick } from './DatabaseFilter'

export interface FilterOption<V extends string = string> {
  value: V
  label: string
  /** Shown after the label in the quiet tone; null or absent shows none. */
  count?: number | null
}

export interface FilterScope<V extends string = string> {
  value: V
  options: FilterOption<V>[]
  onChange: (value: V) => void
  /** Names the control for assistive tech. */
  label?: string
}

export interface FilterDatabase {
  /** Null is all databases. */
  value: string | null
  onChange: (db: string | null) => void
  /** This page's count per database. */
  counts?: Readonly<Record<string, number>>
  /** The databases to offer; every configured database by default. */
  databases?: readonly ConfiguredDatabase[]
}

export interface FilterKinds<V extends string = string> {
  /** Null is every kind: no chip on. */
  value: V | null
  options: FilterOption<V>[]
  onChange: (value: V | null) => void
  label?: string
}

export interface FilterSort<V extends string = string> {
  value: V
  options: Omit<FilterOption<V>, 'count'>[]
  onChange: (value: V) => void
}

export interface FilterRowProps<
  S extends string = string,
  K extends string = string,
  O extends string = string,
> {
  scope: FilterScope<S>
  database: FilterDatabase
  kinds?: FilterKinds<K>
  sort?: FilterSort<O>
  /** Padding for where the row sits: a queue panel's inset or a page bar's. */
  className?: string
}

/** One filter chip: a label, an optional count, and on or off. */
export function FilterChip({
  label,
  count,
  on,
  toggle = true,
  onClick,
  children,
  className,
  'aria-label': ariaLabel,
}: {
  label: string
  count?: number | null
  on: boolean
  /** A toggle says it is pressed; a chip that clears a filter does not. */
  toggle?: boolean
  onClick: () => void
  /** After the label and count, such as a clear mark. */
  children?: ReactNode
  className?: string
  'aria-label'?: string
}) {
  return (
    <Pressable
      aria-pressed={toggle ? on : undefined}
      aria-label={ariaLabel}
      onClick={onClick}
      className={cn(
        'inline-flex h-7 items-center gap-1.5 rounded-pill border px-3 text-body-small transition-colors duration-fast ease-base',
        on
          ? 'border-transparent bg-surface-primary-soft text-content-layout-1'
          : count === 0
            ? 'border-border-layout-2 text-content-layout-3 hover:text-content-layout-2'
            : 'border-border-layout-2 text-content-layout-2 hover:border-border-layout-1 hover:text-content-layout-1',
        className
      )}
    >
      <span>{label}</span>
      {count != null && (
        <span className="tabular-nums text-content-layout-3"> {count}</span>
      )}
      {children}
    </Pressable>
  )
}

export function KindChips<K extends string>({
  kinds,
}: {
  kinds: FilterKinds<K>
}) {
  return (
    <fieldset
      aria-label={kinds.label ?? 'Kind'}
      className="flex min-w-0 flex-wrap gap-1.5"
    >
      {kinds.options.map((option) => {
        const on = option.value === kinds.value
        return (
          <FilterChip
            key={option.value}
            label={option.label}
            count={option.count}
            on={on}
            onClick={() => kinds.onChange(on ? null : option.value)}
          />
        )
      })}
    </fieldset>
  )
}

export function SortMenu<O extends string>({ sort }: { sort: FilterSort<O> }) {
  const current =
    sort.options.find((option) => option.value === sort.value)?.label ??
    sort.value
  return (
    <div className="ml-auto">
      <Dropdown>
        <Dropdown.Trigger asChild>
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            icon="chevron-down"
            iconPosition="right"
            label={current}
            aria-label={`Sort: ${current}`}
          />
        </Dropdown.Trigger>
        <Dropdown.Content
          align="end"
          className="w-56 min-w-56 rounded-panel bg-surface-primary-soft p-1.5"
        >
          {sort.options.map((option) => (
            <Dropdown.ItemWithChildren
              key={option.value}
              active={option.value === sort.value}
              className={filterItemClass}
              onClick={() => sort.onChange(option.value)}
            >
              <span className="min-w-0 flex-1 truncate">{option.label}</span>
              <Tick on={option.value === sort.value} />
            </Dropdown.ItemWithChildren>
          ))}
        </Dropdown.Content>
      </Dropdown>
    </div>
  )
}

export function FilterRow<
  S extends string = string,
  K extends string = string,
  O extends string = string,
>({ scope, database, kinds, sort, className }: FilterRowProps<S, K, O>) {
  return (
    <fieldset
      aria-label="Filters"
      data-testid="filter-row"
      className={cn(
        'flex min-h-12 min-w-0 shrink-0 flex-wrap items-center gap-3 border-b border-border-layout-2',
        className
      )}
    >
      <SegmentedControl<S>
        mode="radio"
        size="small"
        aria-label={scope.label ?? 'Scope'}
        value={scope.value}
        onValueChange={scope.onChange}
        segments={scope.options.map((option) => ({
          value: option.value,
          label: option.label,
          count: option.count,
        }))}
      />
      <DatabaseFilter
        value={database.value}
        onChange={database.onChange}
        counts={database.counts}
        databases={database.databases}
      />
      {kinds && kinds.options.length > 0 && <KindChips kinds={kinds} />}
      {sort && <SortMenu sort={sort} />}
    </fieldset>
  )
}
