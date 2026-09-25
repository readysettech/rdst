// An inbox item's incident window (DESIGN_V5 section 3): when the change began, when it
// ended, and the Database page zoomed to it. The backend measures the window from the
// per-query minute series (`item.incident`); this picks the right one for a query, zooms
// to it and says it in words.
import type { InboxItem } from '@/lib/monitor/types'
import { formatStampTime } from './inboxModel'

/** The zoom reaches this far either side of the window. */
export const INCIDENT_PAD = 3600

export interface IncidentWindow {
  start: number
  /** Back to normal from here; null while it lasts or when unknown. */
  end: number | null
  ongoing: boolean
  /** Measured from the readings, else the item's own times. */
  measured: boolean
  change: number | null
}

/**
 * The window for one query of the item: a group member's own onset when it has one,
 * else the item's.
 */
export function incidentWindow(
  item: InboxItem,
  queryKey?: string | null
): IncidentWindow | null {
  const closed =
    item.state === 'resolved' ||
    item.state === 'dismissed' ||
    item.severity_band === 'recovered' ||
    item.status === 'back_to_normal' ||
    item.status === 'closed'
  const incident = item.incident
  if (!incident) return null
  const member = queryKey
    ? incident.queries.find((query) => query.query_key === queryKey)
    : undefined
  if (member?.start != null)
    return {
      start: member.start,
      end: member.end,
      ongoing: member.end == null && !closed,
      measured: true,
      change: member.change,
    }
  return {
    start: incident.start,
    end: incident.end,
    ongoing: incident.ongoing && !closed,
    measured: incident.measured,
    change: incident.change,
  }
}

const DAY = 86_400
const RANGES: [string, number][] = [
  ['24h', DAY],
  ['7d', 7 * DAY],
  ['30d', 30 * DAY],
  ['90d', 90 * DAY],
]

/**
 * The chart window: an hour either side of the incident, never past now, and the
 * smallest page range that holds its onset (the page clips the hour before to what
 * the range shows).
 */
export function incidentZoom(
  window: Pick<IncidentWindow, 'start' | 'end'>,
  now = Date.now() / 1000
): { range: string; zoom: [number, number] } {
  const current = Math.floor(now)
  const start = Math.floor(window.start - INCIDENT_PAD)
  const end = Math.max(
    start + 120,
    Math.min(current, Math.ceil((window.end ?? current) + INCIDENT_PAD))
  )
  const range =
    RANGES.find(([, seconds]) => current - seconds <= window.start)?.[0] ??
    'all'
  return { range, zoom: [start, end] }
}

const FROM = /^inbox:(\d+)$/

/** The `from` search param a link from an inbox item carries. */
export function fromParam(item: Pick<InboxItem, 'id'>): string {
  return `inbox:${item.id}`
}

export function parseFrom(value: unknown): number | null {
  const match = typeof value === 'string' ? FROM.exec(value) : null
  return match ? Number(match[1]) : null
}

/** Search params that open the Database page on the item's window, carrying the item. */
export function incidentSearch(
  item: InboxItem,
  queryKey?: string | null,
  now = Date.now() / 1000
): Record<string, string> {
  const window = incidentWindow(item, queryKey)
  if (!window) return {}
  const { range, zoom } = incidentZoom(window, now)
  return { range, zoom: `${zoom[0]}-${zoom[1]}`, from: fromParam(item) }
}

const trim = (value: number) =>
  value.toLocaleString('en-US', { maximumFractionDigits: 1 })

/** '3.1x', '40%': how much a level moved, for a sentence that says which way. */
export function changeSize(change: number): string {
  const factor = 1 + change
  if (factor >= 1.95) return `${trim(factor)}x`
  if (factor > 0 && factor <= 0.5) return `${trim(1 / factor)}x`
  return `${Math.round(Math.abs(change) * 100)}%`
}

const LATENCY_TYPES = new Set([
  'latency_regression',
  'plan_change',
  'table_slowdown',
  'slow_creep',
  'query_improved',
])

/** One query's own change on a query line: when, how much, and its sparkline. */
export interface LineOnset {
  /** 'jumped 9:42 PM, 3.1x'. */
  text: string
  /** 'jumped 9:42 PM', for a line that already shows its numbers. */
  when: string
  start: number
  end: number | null
  metric: 'latency' | 'calls'
  points: { x: number; y: number }[]
  /** The sparkline's time span. */
  domain: [number, number] | null
}

function verbFor(
  type: string,
  metric: 'latency' | 'calls',
  change: number | null
): string {
  if (type === 'query_stopped_running') return 'stopped'
  if (type === 'new_heavy_query') return 'first seen'
  const up = (change ?? 0) >= 0
  if (metric === 'latency') return up ? 'slowed' : 'sped up'
  return up ? 'jumped' : 'dropped'
}

function extent(points: { x: number }[]): [number, number] | null {
  if (points.length < 2) return null
  return [points[0].x, points[points.length - 1].x]
}

/**
 * When one query of the item changed, from the measured window: a group member's own
 * onset and sparkline, or a single item's window and trend. Null when unmeasured.
 */
export function lineOnset(
  item: InboxItem,
  queryKey: string,
  now = Date.now() / 1000
): LineOnset | null {
  const incident = item.incident
  if (!incident) return null
  const group = incident.queries.length > 0
  const metric: 'latency' | 'calls' =
    incident.metric ?? (LATENCY_TYPES.has(item.type) ? 'latency' : 'calls')
  let start: number | null
  let end: number | null
  let change: number | null
  let points: { x: number; y: number }[]
  let domain: [number, number] | null
  if (group) {
    const member = incident.queries.find((q) => q.query_key === queryKey)
    if (!member) return null
    ;({ start, end, change } = member)
    points = member.spark.map((point) => ({ x: point.ts, y: point.value }))
    domain = extent(points)
  } else {
    if (!incident.measured || item.query_keys[0] !== queryKey) return null
    ;({ start, end, change } = incident)
    points = item.trend.map((point) => ({ x: point.ts, y: point.value }))
    domain = extent(points)
  }
  if (start == null) return null
  const sized =
    change != null &&
    item.type !== 'query_stopped_running' &&
    item.type !== 'new_heavy_query'
  const size = sized ? `, ${changeSize(change as number)}` : ''
  const when = `${verbFor(item.type, metric, change)} ${formatStampTime(start, now)}`
  return {
    text: `${when}${size}`,
    when,
    start,
    end,
    metric,
    points,
    domain,
  }
}
