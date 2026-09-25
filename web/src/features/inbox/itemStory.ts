// What an inbox item is about, in plain words: the queries it affects (most impact
// first, each with its own before and after) and one calm explanation of the change.
import type { InboxItem } from '@/lib/monitor/types'
import { formatMs, formatShare } from '../monitor-kit/format'
import { itemBand } from '../monitor-kit/severity'
import { formatStampTime } from './inboxModel'
import { incidentWindow, type LineOnset, lineOnset } from './incidentModel'

/** A measured change: 'Latency 77 ms -> 253 ms', or 'Uses 48% of query time'. */
export interface Figures {
  label: string
  before: string | null
  after: string
}

export interface QueryLine {
  key: string
  name: string
  figures: Figures | null
  /** When this query's own change began, with its sparkline; lists show it. */
  onset?: LineOnset | null
}

export interface ItemStory {
  /** Affected queries: the measured ones first, by impact, then the rest by name. */
  queries: QueryLine[]
  /** For database-wide alerts: the change itself (cache hit ratio, rollbacks). */
  figures: Figures | null
  explanation: string
}

const LATENCY_TYPES = new Set([
  'latency_regression',
  'plan_change',
  'table_slowdown',
  'slow_creep',
  'query_improved',
])

const trim = (value: number, digits: number) =>
  value.toLocaleString('en-US', { maximumFractionDigits: digits })

/** '0.22 ms', '77 ms', '2.1 s'. */
export function latencyText(ms: number): string {
  if (ms >= 1000) return `${trim(ms / 1000, 1)} s`
  if (ms >= 10) return `${Math.round(ms)} ms`
  return `${formatMs(ms)} ms`
}

/** Calls per second in the unit that reads best: '1.8 a second', '5.1 a minute', '44 an hour'. */
export function rateText(qps: number): string {
  if (qps >= 1) return `${trim(qps, 1)} a second`
  if (qps * 60 >= 1) return `${trim(qps * 60, 1)} a minute`
  return `${trim(qps * 3600, 1)} an hour`
}

/** How much a measure moved, for a sentence that already says which way. */
function scale(change: number): { times: string | null; pct: string } {
  const factor = 1 + change
  const pct = `${Math.round(Math.abs(change) * 100)}%`
  if (factor >= 1.95) return { times: `about ${trim(factor, 1)} times`, pct }
  if (factor > 0 && factor <= 0.5)
    return { times: `about ${trim(1 / factor, 1)} times`, pct }
  return { times: null, pct }
}

/** 'takes about 3.3 times as long as before', 'takes 35% less time than before'. */
function latencyWords(change: number): string {
  const { times, pct } = scale(change)
  if (change >= 0)
    return times
      ? `takes ${times} as long as before`
      : `takes ${pct} longer than before`
  return times
    ? `is ${times} as fast as before`
    : `takes ${pct} less time than before`
}

/** 'runs about 3.1 times as often as before', 'runs 23% less often than before'. */
function rateWords(change: number): string {
  const { times, pct } = scale(change)
  if (change >= 0)
    return times
      ? `runs ${times} as often as before`
      : `runs ${pct} more often than before`
  return `runs ${pct} less often than before`
}

/**
 * 'It ran about 5.1 a minute until 3:11 PM and hasn't run since.' The last run is the
 * minute before the first silent one the readings show.
 */
export function stoppedSentence(
  item: InboxItem,
  rate: number,
  now = Date.now() / 1000
): string {
  const window = incidentWindow(item)
  const ran = `It ran about ${rateText(rate)}`
  const lead = window?.measured
    ? `${ran} until ${formatStampTime(window.start - 60, now)} and hasn't run since.`
    : `${ran}. RDST saw it had stopped by ${formatStampTime(item.created_at, now)}.`
  return `${lead} Usually a release changed or removed the code that calls it, or the job that runs it was turned off.`
}

/** "RDST first saw it at 9:42 PM." */
function firstSeen(item: InboxItem, now: number): string {
  const window = incidentWindow(item)
  return window?.measured
    ? `RDST first saw this query at ${formatStampTime(window.start, now)}.`
    : "RDST hasn't seen this query before."
}

function shareSentence(summary: string): string {
  const share = /([\d.]+%) of database time/.exec(summary)?.[1]
  return share
    ? ` This query now accounts for ${share} of all query time on this database.`
    : ''
}

function nameOf(item: InboxItem, key: string, names: Map<string, string>) {
  return (
    item.query_labels?.[key] || names.get(key) || `Query ${key.slice(0, 8)}`
  )
}

/** Replaces query references in backend prose ({{q:key}} or a bare key) with names. */
export function readableSummary(summary: string, names: Map<string, string>) {
  return summary
    .replaceAll(
      /\{\{q:([0-9a-f]+)\}\}/g,
      (_, key: string) => names.get(key) ?? 'one query'
    )
    .replaceAll(/\b[0-9a-f]{12}\b/g, (key) => names.get(key) ?? key)
}

function latencyFigures(before: number | null, after: number | null) {
  return after == null
    ? null
    : {
        label: 'Latency',
        before: before == null ? null : latencyText(before),
        after: latencyText(after),
      }
}

function rateFigures(before: number | null, after: number | null) {
  if (after == null) return null
  return {
    label: 'Calls',
    before: before == null ? null : rateText(before),
    after: after === 0 ? 'none' : rateText(after),
  }
}

function groupStory(item: InboxItem, names: Map<string, string>): ItemStory {
  const metric = item.measurement?.metric
  const latency = metric === 'latency'
  const measured: QueryLine[] = (item.members ?? []).map((member) => ({
    key: member.query_key,
    name: member.display_name || nameOf(item, member.query_key, names),
    figures: latency
      ? latencyFigures(member.before, member.after)
      : rateFigures(member.before, member.after),
    onset: lineOnset(item, member.query_key),
  }))
  const listed = new Set(measured.map((line) => line.key))
  // Past the top three, the queries whose own jump the readings show come next.
  const rest = item.query_keys
    .filter((key) => !listed.has(key))
    .map((key) => ({
      key,
      name: nameOf(item, key, names),
      figures: null,
      onset: lineOnset(item, key),
    }))
    .sort(
      (a, b) =>
        Number(!a.onset) - Number(!b.onset) ||
        (a.onset?.start ?? 0) - (b.onset?.start ?? 0) ||
        a.name.localeCompare(b.name)
    )
  const median = item.median_change ?? item.measurement?.change ?? null
  let explanation = 'These queries changed at the same time.'
  if (median != null && metric)
    explanation = latency
      ? `These queries all got ${median >= 0 ? 'slower' : 'faster'} at the same time. The typical one now ${latencyWords(median)}.`
      : `These queries all started running ${median >= 0 ? 'more' : 'less'} often at the same time. The typical one now ${rateWords(median)}.`
  const queries = [...measured, ...rest]
  // Two queries can share a display name; the fingerprint tells them apart.
  const seen = new Map<string, number>()
  for (const line of queries)
    seen.set(line.name, (seen.get(line.name) ?? 0) + 1)
  for (const line of queries)
    if ((seen.get(line.name) ?? 0) > 1)
      line.name = `${line.name} (${line.key.slice(0, 8)})`
  return { queries, figures: null, explanation }
}

/** When the change began, if the readings show it: ' since 9:42 PM'. */
function measuredSince(item: InboxItem, now: number): string {
  const window = incidentWindow(item)
  return window?.measured ? ` since ${formatStampTime(window.start, now)}` : ''
}

/** The affected queries and a plain explanation for one inbox item. */
export function itemStory(
  item: InboxItem,
  names: Map<string, string>,
  now = Date.now() / 1000
): ItemStory {
  if (item.type === 'many_queries_changed' || item.query_keys.length > 1)
    return groupStory(item, names)
  const summary = readableSummary(item.summary, names)
  const key = item.query_keys[0] as string | undefined
  const line = (figures: Figures | null): QueryLine[] =>
    key
      ? [
          {
            key,
            name: item.subject_label || nameOf(item, key, names),
            figures,
            onset: lineOnset(item, key),
          },
        ]
      : []
  const m = item.measurement
  // A recovery's measurement describes the original alert; its summary says how it ended.
  if (!m || itemBand(item) === 'recovered')
    return { queries: line(null), figures: null, explanation: summary }
  const change = m.change
  if (LATENCY_TYPES.has(item.type)) {
    const figures = latencyFigures(m.before, m.after)
    const since = measuredSince(item, now)
    const up = (change ?? 0) >= 0
    const sentence =
      change == null
        ? summary
        : m.before != null && m.after != null
          ? `Each call took ${latencyText(m.before)} and has taken ${latencyText(m.after)}${since}.${up && item.type !== 'slow_creep' ? ' A changed plan, more rows to read, lock waits or a busier host are the usual causes.' : ''}`
          : `Each call now ${latencyWords(change)}.`
    return {
      queries: line(figures),
      figures: key ? null : figures,
      explanation: `${sentence}${shareSentence(item.summary)}`,
    }
  }
  if (item.type === 'traffic_spike')
    return {
      queries: line(rateFigures(m.before, m.after)),
      figures: null,
      explanation:
        change == null
          ? summary
          : m.before != null && m.after != null && change >= 0
            ? `It ran about ${rateText(m.before)} and has run ${rateText(m.after)}${measuredSince(item, now)}.${shareSentence(item.summary)} More users, a retry loop or a new caller are the usual causes.`
            : `It now ${rateWords(change)}.${shareSentence(item.summary)}`,
    }
  if (item.type === 'query_stopped_running')
    return {
      queries: line(rateFigures(m.before, 0)),
      figures: null,
      explanation:
        m.before != null ? stoppedSentence(item, m.before, now) : summary,
    }
  if (item.type === 'new_heavy_query' && m.after != null) {
    const perCall = /([\d.,]+ (?:ms|s)) a call/.exec(item.summary)?.[1]
    const share = formatShare(m.after)
    return {
      queries: line({
        label: 'Uses',
        before: null,
        after: `${share} of query time`,
      }),
      figures: null,
      explanation: `${firstSeen(item, now)}${perCall ? ` Each call takes ${perCall}.` : ''}`,
    }
  }
  if (item.type === 'cache_hit_ratio_drop' && m.after != null)
    return {
      queries: line(null),
      figures: {
        label: 'Cache hit ratio',
        before: m.before == null ? null : formatShare(m.before),
        after: formatShare(m.after),
      },
      explanation:
        'Fewer reads are coming from memory, so more of them go to disk, which is slower.',
    }
  if (item.type === 'rollback_or_deadlock_spike' && m.after != null)
    return {
      queries: line(null),
      figures: {
        label: 'Rollbacks',
        before: m.before == null ? null : `${trim(m.before, 1)} a minute`,
        after: `${trim(m.after, 1)} a minute`,
      },
      explanation:
        'More transactions are being rolled back than usual. Deadlocks and failing writes are the usual causes.',
    }
  if (item.type === 'connections_near_max')
    return {
      queries: line(null),
      figures: { label: '', before: null, after: m.text },
      explanation: `${summary} When every connection is taken, new ones are refused.`,
    }
  return { queries: line(null), figures: null, explanation: summary }
}
