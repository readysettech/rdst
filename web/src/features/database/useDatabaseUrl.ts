// Page-level state of /database in the URL (DESIGN_V4 "UI plan"): which view (Live or
// Report), the report shown, the live range, the hero metric, a health chart standing
// in for the hero, and the side panels. Every write replaces the history entry.
import { useNavigate, useRouterState } from '@tanstack/react-router'
import { useCallback, useRef } from 'react'
import { parseFrom } from '@/features/inbox/incidentModel'
import {
  type LiveRange,
  type LoadMetric,
  parseLiveRange,
} from '@/features/monitor-kit/charts'
import { formatClock, formatDayDate } from '@/features/monitor-kit/format'

export type DatabaseView = 'live' | 'report'
export type HealthPanel = 'connections' | 'hit' | 'rollback'
export type SidePanel = 'settings' | 'setup' | 'analyze' | 'enable'

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

  const view: DatabaseView = search.view === 'report' ? 'report' : 'live'
  const reportRaw = search.report
  const report: number | 'latest' =
    typeof reportRaw === 'string' && /^\d+$/.test(reportRaw)
      ? Number(reportRaw)
      : typeof reportRaw === 'number'
        ? reportRaw
        : 'latest'
  const range = parseLiveRange(search.range) ?? DEFAULT_RANGE
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
    search.panel === 'enable'
      ? search.panel
      : null
  const zoom = searchZoom(search)
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
      set('view', patch.view, 'live')
      set('report', patch.report, 'latest')
      set('range', patch.range, DEFAULT_RANGE)
      set('metric', patch.metric, 'calls')
      set('health', patch.health)
      set('panel', patch.panel)
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
    report,
    range,
    metric,
    health,
    panel,
    zoom,
    fromItem,
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
