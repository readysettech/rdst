// Page-level state of /database in the URL (DESIGN_V4 "UI plan"): which view (Actions,
// Live or Report), the Actions section, the report shown, the live range, the hero metric,
// a health chart standing in for the hero, the side panels and the query list's filters.
// Every write replaces the history entry, so a filter change never adds a step to go back
// over.
import { useNavigate, useRouterState } from '@tanstack/react-router'
import { useCallback, useRef } from 'react'
import { parseFrom } from '@/features/inbox/incidentModel'
import {
  LIVE_RANGES,
  type LiveRange,
  type LoadMetric,
  parseLiveRange,
  RANGE_SECONDS,
} from '@/features/monitor-kit/charts'
import { formatClock, formatDayDate } from '@/features/monitor-kit/format'

/** `live` is the Queries tab; its value predates the tab's name and stays for old links. */
export type DatabaseView = 'live' | 'report'

const VIEWS: Record<string, DatabaseView> = {
  live: 'live',
  queries: 'live',
  report: 'report',
}
/** The Status menu: every query, or one status. */
export type ListStatus = 'all' | 'fix' | 'analyzing' | 'healthy' | 'stuck'
export const LIST_STATUSES: readonly ListStatus[] = [
  'all',
  'fix',
  'analyzing',
  'healthy',
  'stuck',
]

export interface ListFilters {
  status: ListStatus
  table: string | null
  q: string
}

export function parseListStatus(value: unknown): ListStatus {
  return LIST_STATUSES.includes(value as ListStatus)
    ? (value as ListStatus)
    : 'all'
}
export type HealthPanel = 'connections' | 'hit' | 'rollback'
export type SidePanel =
  | 'settings'
  | 'setup'
  | 'analyze'
  | 'enable'
  | 'benchmark'

export const DEFAULT_RANGE: LiveRange = '1h'

/** The smallest zoom window, in seconds. */
export const MIN_ZOOM = 300

/** A zoom keeps minute boundaries so retained incident detail can be read at any age. */
export function fitZoom(
  zoom: [number, number],
  now: number,
  first: number | null | undefined
): [number, number] {
  let [start, end] = zoom
  if (end - start < MIN_ZOOM) {
    const middle = (start + end) / 2
    start = middle - MIN_ZOOM / 2
    end = start + MIN_ZOOM
  }
  if (end > now) {
    start -= end - now
    end = now
  }
  if (first != null && start < first) {
    end = Math.min(now, end + (first - start))
    start = first
  }
  const step = 60
  const snappedEnd = Math.ceil(end / step) * step
  return [
    Math.floor(start / step) * step,
    snappedEnd > now ? Math.ceil(end) : snappedEnd,
  ]
}

/** The range, widened to the smallest one that reaches back to `start` when it falls short. */
export function rangeHolding(
  range: LiveRange,
  start: number,
  now = Date.now() / 1000
): LiveRange {
  const holds = (value: LiveRange) =>
    value === 'all' || now - RANGE_SECONDS[value] <= start
  // The ranges run shortest first, so the first that holds is the smallest.
  return holds(range) ? range : (LIVE_RANGES.find(holds) ?? 'all')
}

/** "2:10 to 2:25 PM" today, "Mon, Sep 28, 2:10 PM to 3:25 PM" on another day. */
export function windowLabel(
  [start, end]: [number, number],
  now = Date.now() / 1000
): string {
  const day = (ts: number) => new Date(ts * 1000).toDateString()
  const today = day(start) === day(now)
  const sameDay = day(start) === day(end)
  const from = today
    ? formatClock(start)
    : `${formatDayDate(start)}, ${formatClock(start)}`
  const to = sameDay
    ? formatClock(end)
    : `${formatDayDate(end)}, ${formatClock(end)}`
  return `${from} to ${to}`
}

/**
 * The time frame chip's label. A frame from the Inbox shows the hour before the change too;
 * the chip names the change itself, the shaded part (from its onset, as the banner does), so
 * the label and the chart's shaded start never read an hour apart.
 */
export function frameLabel(
  domain: [number, number],
  incident: { start: number; end: number | null } | null,
  now = Date.now() / 1000
): string {
  if (!incident || incident.start <= domain[0] || incident.start >= domain[1])
    return windowLabel(domain, now)
  const end = Math.min(domain[1], incident.end ?? domain[1])
  return `${windowLabel([incident.start, Math.max(end, incident.start)], now)}, shaded`
}

const METRICS: readonly LoadMetric[] = ['calls', 'latency', 'time']
const HEALTH: readonly HealthPanel[] = ['connections', 'hit', 'rollback']

interface Patch {
  view?: DatabaseView
  report?: number | 'latest' | null
  range?: LiveRange
  metric?: LoadMetric
  health?: HealthPanel | null
  panel?: SidePanel | null
  zoom?: [number, number] | null
  from?: null
  /** Clears the query list's Status filter (a new time frame shows every query). */
  filter?: null
  status?: ListStatus
  table?: string | null
  q?: string
}

function searchZoom(search: Record<string, unknown>): [number, number] | null {
  const match =
    typeof search.zoom === 'string' ? /^(\d+)-(\d+)$/.exec(search.zoom) : null
  return match && Number(match[2]) > Number(match[1])
    ? [Number(match[1]), Number(match[2])]
    : null
}

export function useDatabaseUrl() {
  const navigate = useNavigate()
  const search = useRouterState({
    select: (state) => state.location.search as Record<string, unknown>,
  })
  const searchRef = useRef(search)
  searchRef.current = search

  // A link to a query, a time frame, an Inbox item or a list filter is about the Queries tab.
  const pointsAtQueries = [
    'query',
    'queries',
    'focus',
    'zoom',
    'from',
    'status',
    'table',
    'q',
  ].some((name) => typeof search[name] === 'string' && search[name] !== '')
  const requestedView: DatabaseView | null =
    (typeof search.view === 'string' ? VIEWS[search.view] : undefined) ??
    (pointsAtQueries ? 'live' : null)
  const view: DatabaseView = requestedView ?? 'live'
  const reportRaw = search.report
  const report: number | 'latest' =
    typeof reportRaw === 'string' && /^\d+$/.test(reportRaw)
      ? Number(reportRaw)
      : typeof reportRaw === 'number'
        ? reportRaw
        : 'latest'
  const zoom = searchZoom(search)
  // A time frame (an Inbox link, a shared zoom) opens on a range that holds it.
  const urlRange = parseLiveRange(search.range) ?? DEFAULT_RANGE
  const range = zoom ? rangeHolding(urlRange, zoom[0]) : urlRange
  // 'load' is the old name of the time-spent view.
  const metric: LoadMetric =
    search.metric === 'load'
      ? 'time'
      : METRICS.includes(search.metric as LoadMetric)
        ? (search.metric as LoadMetric)
        : 'calls'
  const health = HEALTH.includes(search.health as HealthPanel)
    ? (search.health as HealthPanel)
    : null
  const panel: SidePanel | null =
    search.panel === 'settings' ||
    search.panel === 'setup' ||
    search.panel === 'analyze' ||
    search.panel === 'enable' ||
    search.panel === 'benchmark'
      ? search.panel
      : null
  const status = parseListStatus(search.status)
  const table =
    typeof search.table === 'string' && search.table ? search.table : null
  const q = typeof search.q === 'string' ? search.q : ''
  // The Inbox item a link came from (DESIGN_V5 section 3).
  const fromItem = parseFrom(search.from)

  const write = useCallback(
    (patch: Patch) => {
      const next: Record<string, unknown> = { ...searchRef.current }
      const set = (name: string, value: unknown, fallback?: unknown) => {
        if (value === undefined) return
        if (value === null || value === fallback) delete next[name]
        else next[name] = String(value)
      }
      set('view', patch.view)
      set('report', patch.report, 'latest')
      set('range', patch.range, DEFAULT_RANGE)
      set('metric', patch.metric, 'calls')
      set('health', patch.health)
      set('panel', patch.panel)
      set('status', patch.status, 'all')
      set('table', patch.table, '')
      set('q', patch.q, '')
      if (patch.zoom !== undefined)
        set('zoom', patch.zoom ? `${patch.zoom[0]}-${patch.zoom[1]}` : null)
      set('from', patch.from)
      if (patch.filter === null) {
        delete next.filter
        delete next.jev
      }
      // A new range starts unzoomed.
      if (patch.range !== undefined && patch.zoom === undefined)
        delete next.zoom
      if (patch.view === 'live') delete next.report
      // Writes in the same tick build on each other.
      searchRef.current = next
      void navigate({
        to: '.',
        search: next as never,
        replace: true,
        resetScroll: false,
      })
    },
    [navigate]
  )

  return {
    view,
    /** The tab the URL asks for; null opens Live. */
    requestedView,
    report,
    range,
    metric,
    health,
    panel,
    zoom,
    status,
    table,
    q,
    fromItem,
    /** The list's filters; a field left out keeps its value. */
    setFilters: useCallback(
      (patch: Partial<ListFilters>) => write(patch),
      [write]
    ),
    clearFrom: useCallback(() => write({ from: null }), [write]),
    /** Back from an incident window to the last day. */
    showDay: useCallback(() => write({ range: '24h', zoom: null }), [write]),
    setZoom: useCallback(
      (value: [number, number] | null) => write({ zoom: value }),
      [write]
    ),
    setView: useCallback(
      (value: DatabaseView) => write({ view: value }),
      [write]
    ),
    openReport: useCallback(
      (id: number | 'latest') => write({ view: 'report', report: id }),
      [write]
    ),
    setRange: useCallback(
      (value: LiveRange) => write({ range: value }),
      [write]
    ),
    /** One write for arriving on a time frame: a range that holds it, every status. */
    arrive: useCallback(
      (patch: { range?: LiveRange; resetFilter?: boolean }) =>
        write({
          ...(patch.range
            ? { range: patch.range, zoom: searchZoom(searchRef.current) }
            : {}),
          ...(patch.resetFilter ? { filter: null } : {}),
        }),
      [write]
    ),
    setMetric: useCallback(
      (value: LoadMetric) => write({ metric: value }),
      [write]
    ),
    setHealth: useCallback(
      (value: HealthPanel | null) => write({ health: value }),
      [write]
    ),
    setPanel: useCallback(
      (value: SidePanel | null) => write({ panel: value }),
      [write]
    ),
  }
}
