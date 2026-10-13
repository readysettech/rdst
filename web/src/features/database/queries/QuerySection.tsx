// The Queries section of the Database page: one divided list, a neutral chip and the
// fix line as each row's subtitle, with Status and Tables menus, search and the two
// ghost actions in its toolbar. A row click opens the detail pane; while the pane is
// open, or the list is narrow, the list keeps four columns. In the first minutes rows say
// "Analyzing" and wait for their score and trend; without monitoring the list is a scan's
// totals.
import { cn } from '@rs/tailwind-base'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import {
  DividedList,
  type DividedListColumn,
  ListRow,
} from '@rs/ui-new/divided-list'
import { Dropdown } from '@rs/ui-new/dropdown'
import { ErrorState } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { useReducedMotion } from '@rs/ui-new/motion'
import { Pressable } from '@rs/ui-new/pressable'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { MiniTrend } from '@/features/monitor-kit/charts'
import { useElementWidth } from '@/features/monitor-kit/charts/chartHooks'
import type { AnalyzeJob, MonitorQuery } from '@/lib/monitor/types'
import {
  ACTIVITY_LABEL,
  DASH,
  dailyCalls,
  deltaLabel,
  filterRows,
  formatSharePercent,
  latencyText,
  newestJobs,
  PAGE_SIZE,
  pagerLabel,
  type RowActivity,
  type RowSort,
  rowActivity,
  rowChip,
  STATUS_FILTERS,
  type StatusChip as StatusChipSpec,
  scorePoor,
  sectionMeta,
  sortRows,
  swatchColor,
  tablesOf,
} from '../databaseModel'
import { FirstRunMore } from '../overview/FirstReadings'
import type { ListFilters } from '../useDatabaseUrl'
import { rowScore, scoreValue } from './ScorePill'

export type QueryListMode = 'live' | 'first_run' | 'scan'

export interface QuerySectionProps {
  rows: MonitorQuery[] | undefined
  loading: boolean
  error?: boolean
  onRetry?: () => void
  sort: RowSort
  openKey: string | null
  onOpen: (key: string) => void
  hoverKey: string | null
  onHoverKey: (key: string | null) => void
  /** The pane is open: trend and plan score leave until it closes. */
  paneOpen: boolean
  mode: QueryListMode
  /** The one-off scan the rows came from, without monitoring. */
  scanAt?: number | null
  /** Queries seen so far on a first run, from the page meta; the rest are on their way. */
  seenTotal?: number
  /** The Status, Tables and search filters, from the URL. */
  filters: ListFilters
  onFiltersChange: (patch: Partial<ListFilters>) => void
  onAnalyzeQuery: () => void
  onBenchmark: () => void
  /** A link shown at the right of the section title, such as the changes to do. */
  aside?: ReactNode
  /** The database's analysis jobs, polled faster than the rows: queued and running show
   * on the row as soon as the queue moves. */
  jobs?: AnalyzeJob[]
}

const COLUMNS = {
  closed: 'minmax(280px,2.6fr) 84px 112px 136px 80px 110px 24px',
  score: 'minmax(280px,2.6fr) 84px 112px 136px 80px 24px',
  open: 'minmax(240px,2fr) 80px 112px 130px 24px',
  scan: 'minmax(280px,2.6fr) 128px 112px 136px 24px',
}

// The list's own width in a 1180 px and a 1080 px window: under them the 24 h trend, then
// the plan score, leave the row.
const TREND_MIN_WIDTH = 930
const SCORE_MIN_WIDTH = 830

const HEAD: Record<keyof typeof COLUMNS, DividedListColumn[]> = {
  closed: [
    { label: 'Query' },
    { label: 'Calls/s', align: 'end' },
    { label: 'Avg latency', align: 'end' },
    { label: 'Share of time', align: 'end' },
    { label: 'Plan score', align: 'end' },
    { label: 'Latency, 24 h', align: 'end' },
    { label: '' },
  ],
  score: [
    { label: 'Query' },
    { label: 'Calls/s', align: 'end' },
    { label: 'Avg latency', align: 'end' },
    { label: 'Share of time', align: 'end' },
    { label: 'Plan score', align: 'end' },
    { label: '' },
  ],
  open: [
    { label: 'Query' },
    { label: 'Calls/s', align: 'end' },
    { label: 'Avg latency', align: 'end' },
    { label: 'Share of time', align: 'end' },
    { label: '' },
  ],
  scan: [
    { label: 'Query' },
    // A scan projects its recorded rate over a day.
    { label: 'Calls a day (est.)', align: 'end' },
    { label: 'Avg latency', align: 'end' },
    { label: 'Share of time', align: 'end' },
    { label: '' },
  ],
}

const TREND_COLOR = 'var(--color-content-layout-3)'

const menuButton = {
  variant: 'primary',
  modifier: 'ghost',
  size: 'small',
  icon: 'chevron-down',
  iconPosition: 'right',
} as const

const menuContent = 'min-w-44 rounded-panel bg-surface-primary-soft p-1.5'
const menuItem = 'h-8.5 rounded-md px-2.5 text-body-small'

export function StatusChip({ chip }: { chip: StatusChipSpec | null }) {
  const reduced = useReducedMotion()
  if (!chip) return null
  const tag = (
    <Tag
      size="small"
      variant={chip.variant}
      modifier={chip.modifier}
      className="rounded-pill whitespace-nowrap"
      label={chip.label}
    />
  )
  if (!chip.activity) return tag
  // An analysis on its way: a breathing dot while it runs, a hollow one until it starts.
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1.5"
      data-activity={chip.activity}
    >
      <span
        aria-hidden="true"
        className={cn(
          'h-1.5 w-1.5 shrink-0 rounded-full',
          chip.activity === 'running'
            ? cn('bg-content-rising-plain', !reduced && 'mk-breathe')
            : 'border border-content-layout-3'
        )}
      />
      {tag}
    </span>
  )
}

/** "Analyzing…" with a breathing dot while it runs, "Queued for analysis" with a hollow
 * one until it starts. */
function ActivityMark({ activity }: { activity: RowActivity }) {
  const reduced = useReducedMotion()
  return (
    <output
      data-activity={activity}
      data-testid="row-activity"
      className={cn(
        'inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap text-caption',
        activity === 'running'
          ? 'text-content-layout-1'
          : 'text-content-layout-2'
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'h-2 w-2 shrink-0 rounded-full',
          activity === 'running'
            ? 'bg-content-rising-plain'
            : 'border border-content-layout-2',
          !reduced && 'mk-breathe'
        )}
      />
      {ACTIVITY_LABEL[activity]}
    </output>
  )
}

/** A finished deep analysis, so a Healthy row reads as analyzed: its score when it has one. */
function AnalyzedMark({ query }: { query: MonitorQuery }) {
  const score = rowScore(query)
  if (!score && query.last_analysis?.summary == null) return null
  const value = score ? scoreValue(score) : null
  return (
    <span
      className="shrink-0 whitespace-nowrap text-caption text-content-layout-3"
      data-testid="row-analyzed"
    >
      {value != null ? `Analyzed, ${value}/100` : 'Analyzed'}
    </span>
  )
}

/** "Add index" in the second tone, the index itself in the first. */
function FixLine({ query }: { query: MonitorQuery }) {
  const fix = query.suggested_fix
  if (!fix)
    return (
      <span className="min-w-0 truncate text-mono-small text-content-layout-2">
        {query.text}
      </span>
    )
  const match = /^(Add index)\s+(.*)$/.exec(fix.label)
  return (
    <span className="min-w-0 truncate text-mono-small text-content-layout-2">
      {match ? (
        <>
          {match[1]} <span className="text-content-layout-1">{match[2]}</span>
        </>
      ) : (
        fix.label
      )}
    </span>
  )
}

function ShareBar({ share }: { share: number }) {
  return (
    <span className="flex items-center justify-end gap-2.5 tabular-nums">
      <span className="h-1 w-16 overflow-hidden rounded-sm bg-surface-primary-soft-hover">
        <span
          className="block h-full bg-content-layout-3"
          style={{ width: `${Math.max(1, Math.min(100, share * 100))}%` }}
        />
      </span>
      {formatSharePercent(share)}
    </span>
  )
}

function Trend({ query }: { query: MonitorQuery }) {
  const points = query.trend_24h ?? []
  if (points.length < 3)
    return (
      <span className="text-right text-caption text-content-layout-3">
        {DASH}
      </span>
    )
  return (
    <div className="h-7 w-25 justify-self-end">
      <MiniTrend
        ariaLabel={`Latency over 24 hours for ${query.display_name}`}
        points={points.map((point) => ({ x: point.ts, y: point.mean_ms }))}
        color={TREND_COLOR}
        height={28}
        resolution={3600}
        yMin="auto"
        interactive={false}
        formatValue={(value) => `${latencyText(value)} ms`}
      />
    </div>
  )
}

function QueryRow({
  query,
  job,
  layout,
  selected,
  hovered,
  onOpen,
  onHover,
}: {
  query: MonitorQuery
  job: AnalyzeJob | undefined
  layout: keyof typeof COLUMNS
  selected: boolean
  hovered: boolean
  onOpen: () => void
  onHover: (hovering: boolean) => void
}) {
  const delta = deltaLabel(query.latency_delta_24h)
  const scan = layout === 'scan'
  const activity = rowActivity(query, job, !scan)
  const chip = rowChip(query, !scan)
  return (
    <ListRow
      asChild
      interactive
      className={cn(
        'min-h-19 w-full py-4.5 text-left',
        (selected || hovered) && 'bg-row-hover'
      )}
    >
      <Pressable
        id={`query-${query.key}`}
        aria-current={selected || undefined}
        onClick={onOpen}
        onPointerEnter={() => onHover(true)}
        onPointerLeave={() => onHover(false)}
        data-testid="query-row"
      >
        <span className="min-w-0">
          <span className="flex items-center gap-2.5 text-body-medium font-medium text-content-layout-1">
            {!scan && (
              <span
                aria-hidden="true"
                className="h-2 w-2 shrink-0 rounded-sm"
                style={{ background: swatchColor(query.color_index) }}
              />
            )}
            <span className="truncate">{query.display_name}</span>
          </span>
          <span className="mt-1.5 flex min-w-0 items-center gap-2">
            {activity ? (
              <ActivityMark activity={activity} />
            ) : (
              // The polled jobs settle a row the list hasn't refreshed yet.
              <StatusChip chip={chip?.activity ? null : chip} />
            )}
            <AnalyzedMark query={query} />
            <FixLine query={query} />
          </span>
        </span>
        <span className="text-right text-body-small tabular-nums text-content-layout-1">
          {scan ? dailyCalls(query) : query.qps.toFixed(2)}
        </span>
        <span className="text-right tabular-nums">
          <span className="text-body-small text-content-layout-1">
            {latencyText(query.mean_ms)}
            <span className="ml-0.5 text-label-extra-small font-normal text-content-layout-3">
              ms
            </span>
          </span>
          {!scan && (
            <span
              className={cn(
                'block text-label-extra-small font-normal',
                delta.up ? 'text-content-warning-soft' : 'text-content-layout-3'
              )}
            >
              {delta.text}
            </span>
          )}
        </span>
        <span className="text-body-small text-content-layout-1">
          <ShareBar share={query.db_time_share} />
        </span>
        {(layout === 'closed' || layout === 'score') && (
          <>
            <span
              className={cn(
                'text-right text-caption tabular-nums',
                scorePoor(query.plan_score)
                  ? 'text-content-warning-soft'
                  : 'text-content-layout-3'
              )}
            >
              {query.plan_score ?? DASH}
            </span>
            {layout === 'closed' && <Trend query={query} />}
          </>
        )}
        <Icon
          name="chevron-right"
          label=""
          size="small"
          className="justify-self-end text-content-layout-3"
        />
      </Pressable>
    </ListRow>
  )
}

export function QuerySection({
  rows,
  loading,
  error = false,
  onRetry,
  sort,
  openKey,
  onOpen,
  hoverKey,
  onHoverKey,
  paneOpen,
  mode,
  scanAt = null,
  seenTotal,
  filters,
  onFiltersChange,
  onAnalyzeQuery,
  onBenchmark,
  aside,
  jobs,
}: QuerySectionProps) {
  const { status, table, q } = filters
  const [page, setPage] = useState(0)
  const searchRef = useRef<HTMLInputElement>(null)
  const [sectionRef, width] = useElementWidth<HTMLElement>(TREND_MIN_WIDTH)
  const scan = mode === 'scan'
  const layout: keyof typeof COLUMNS = scan
    ? 'scan'
    : paneOpen || (width > 0 && width < SCORE_MIN_WIDTH)
      ? 'open'
      : width > 0 && width < TREND_MIN_WIDTH
        ? 'score'
        : 'closed'

  // The box filters on each keystroke and writes the URL on a short delay; a URL
  // change from outside (back, a link) resets the box unless the user is typing.
  const [search, setSearch] = useState(q)
  const typing = useRef(false)
  useEffect(() => {
    if (!typing.current) setSearch(q)
  }, [q])
  useEffect(() => {
    if (search === q) {
      typing.current = false
      return
    }
    typing.current = true
    const timer = window.setTimeout(() => {
      typing.current = false
      onFiltersChange({ q: search })
    }, 250)
    return () => window.clearTimeout(timer)
  }, [search, q, onFiltersChange])

  const all = useMemo(() => sortRows(rows ?? [], sort), [rows, sort])
  const shown = useMemo(
    () => filterRows(all, { status, table, q: search }),
    [all, status, table, search]
  )
  const filtering = status !== 'all' || table !== null || search.trim() !== ''
  const pages = Math.max(1, Math.ceil(shown.length / PAGE_SIZE))
  const current = Math.min(page, pages - 1)
  const pageRows = shown.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE)
  const tables = useMemo(() => tablesOf(all), [all])
  const jobFor = useMemo(() => newestJobs(jobs), [jobs])

  // The open row stays on screen: its page is the current one.
  useEffect(() => {
    if (!openKey) return
    const at = shown.findIndex((row) => row.key === openKey)
    if (at >= 0) setPage(Math.floor(at / PAGE_SIZE))
  }, [openKey, shown])

  // "/" focuses the search box, from anywhere but another field.
  useEffect(() => {
    if (scan) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey)
        return
      const target = event.target
      if (
        target instanceof Element &&
        target.closest('input, textarea, [contenteditable="true"]')
      )
        return
      event.preventDefault()
      searchRef.current?.focus()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [scan])

  const statusLabel =
    STATUS_FILTERS.find((entry) => entry.value === status)?.label ?? 'All'

  return (
    <section
      ref={sectionRef}
      aria-labelledby="queries-title"
      data-testid="query-section"
      className={cn(scan ? 'mt-12' : 'mt-11')}
    >
      <div className="flex items-baseline gap-3">
        <h2
          id="queries-title"
          className="text-headline-5 text-content-layout-1"
        >
          Queries
        </h2>
        {rows && (
          <Text as="span" level="caption" className="text-content-layout-3">
            {sectionMeta(all, { sort, firstRun: mode === 'first_run', scanAt })}
          </Text>
        )}
        {aside && <span className="ml-auto">{aside}</span>}
      </div>

      {!scan && (
        <div className="mt-3.5 flex items-center gap-2">
          <Dropdown>
            <Dropdown.Trigger asChild>
              <Button
                {...menuButton}
                label={`Status: ${statusLabel}`}
                data-testid="status-menu"
              />
            </Dropdown.Trigger>
            <Dropdown.Content align="start" className={menuContent}>
              {STATUS_FILTERS.map((entry) => (
                <Dropdown.Item
                  key={entry.value}
                  label={entry.label}
                  active={entry.value === status}
                  className={menuItem}
                  onClick={() => {
                    onFiltersChange({ status: entry.value })
                    setPage(0)
                  }}
                />
              ))}
            </Dropdown.Content>
          </Dropdown>
          <Dropdown>
            <Dropdown.Trigger asChild>
              <Button
                {...menuButton}
                label={table ? `Tables: ${table}` : 'Tables'}
                data-testid="tables-menu"
              />
            </Dropdown.Trigger>
            <Dropdown.Content align="start" className={menuContent}>
              <Dropdown.Item
                label="All tables"
                active={table === null}
                className={menuItem}
                onClick={() => {
                  onFiltersChange({ table: null })
                  setPage(0)
                }}
              />
              {tables.map((name) => (
                <Dropdown.Item
                  key={name}
                  label={name}
                  active={name === table}
                  className={menuItem}
                  onClick={() => {
                    onFiltersChange({ table: name })
                    setPage(0)
                  }}
                />
              ))}
            </Dropdown.Content>
          </Dropdown>
          <div className="relative ml-auto w-60">
            <BaseInputText
              ref={searchRef}
              aria-label="Search queries"
              placeholder="Search"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value)
                setPage(0)
              }}
              className="h-7.5 pr-9 text-caption"
            />
            <kbd className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 rounded-sm border border-border-layout-2 px-1.5 text-mono-small text-content-layout-3">
              /
            </kbd>
          </div>
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Analyze a query"
            onClick={onAnalyzeQuery}
          />
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Benchmark"
            onClick={onBenchmark}
          />
        </div>
      )}

      {error && !rows ? (
        <div className="mt-4">
          <ErrorState
            tone="quiet"
            title="Queries didn't load"
            message="RDST couldn't read this database's queries."
            onRetry={onRetry}
          />
        </div>
      ) : loading && !rows ? (
        <div className="mt-4 space-y-3" aria-busy="true">
          <Skeleton className="h-16 w-full rounded-panel" />
          <Skeleton className="h-16 w-full rounded-panel" />
          <Skeleton className="h-16 w-full rounded-panel" />
        </div>
      ) : rows && rows.length === 0 ? (
        <Text as="p" level="caption" className="mt-3.5 text-content-layout-3">
          No queries recorded yet.
        </Text>
      ) : filtering && shown.length === 0 ? (
        <div className="mt-3.5 flex items-center gap-3">
          <Text as="p" level="caption" className="text-content-layout-3">
            No queries match these filters.
          </Text>
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Clear filters"
            onClick={() => {
              setSearch('')
              onFiltersChange({ status: 'all', table: null, q: '' })
              setPage(0)
            }}
          />
        </div>
      ) : (
        <>
          <DividedList
            columns={COLUMNS[layout]}
            head={HEAD[layout]}
            className={cn('mt-3.5', scan && 'mt-3')}
          >
            {pageRows.map((query) => (
              <QueryRow
                key={query.key}
                query={query}
                job={jobFor.get(query.key)}
                layout={layout}
                selected={query.key === openKey}
                hovered={query.key === hoverKey}
                onOpen={() => onOpen(query.key)}
                onHover={(hovering) => onHoverKey(hovering ? query.key : null)}
              />
            ))}
          </DividedList>
          {filtering ? (
            <Text as="p" level="caption" className="mt-3 text-content-layout-3">
              {shown.length} of {all.length} match
            </Text>
          ) : mode === 'first_run' ? (
            <FirstRunMore seenTotal={seenTotal} listed={shown.length} />
          ) : null}
          {shown.length > PAGE_SIZE && (
            <div className="mt-3.5 flex items-center justify-end gap-2.5">
              <Text as="span" level="caption" className="text-content-layout-3">
                {pagerLabel(current, shown.length)}
              </Text>
              {current > 0 && (
                <Button
                  variant="primary"
                  modifier="ghost"
                  size="small"
                  label="Previous"
                  onClick={() => setPage(current - 1)}
                />
              )}
              {current < pages - 1 && (
                <Button
                  variant="primary"
                  modifier="ghost"
                  size="small"
                  label="Next"
                  onClick={() => setPage(current + 1)}
                />
              )}
            </div>
          )}
        </>
      )}
    </section>
  )
}
