// Pure shaping for the Inbox page: the Act now and Watch sections, same-type groups,
// the counts every view shares, recently closed alerts and the number highlighting in
// one-line summaries. Timestamps are Unix
// seconds.
import type { InboxItem, MuteRule } from '@/lib/monitor/types'
import { itemBand, ruleMatches } from '../monitor-kit/severity'

/** Items a live mute rule covers, even before the backend re-files them as muted. */
export function isMuted(item: InboxItem, rules: MuteRule[] | undefined) {
  if (item.muted_by !== null) return true
  return Boolean(rules?.some((rule) => ruleMatches(rule, item)))
}

export interface InboxGroup {
  /** Stable id: the group key, or `item:<id>` for a single item. */
  id: string
  items: InboxItem[]
  lead: InboxItem
}

/** The Inbox tabs. Open lists Act now, Watch, then what recovered in the last 24 hours. */
export type InboxView = 'open' | 'act_now' | 'watch'

/** The two live sections. FYI items list at the bottom of Watch as low-priority rows. */
export type LiveBand = 'act_now' | 'watch'

export interface InboxSection {
  band: LiveBand
  groups: InboxGroup[]
  count: number
  unread: number
}

const GROUP_WINDOW = 6 * 3600

const byUrgency = (a: InboxItem, b: InboxItem) =>
  b.severity - a.severity || b.created_at - a.created_at

/** FYI rows sort after real Watch rows, whatever their severity. */
const lowPriority = (item: InboxItem) => Number(itemBand(item) === 'fyi')

/**
 * Items of one type on one target collapse into one row while each was filed within 6 h of
 * the previous one, so items filed minutes apart always share a row.
 */
export function groupItems(items: InboxItem[]): InboxGroup[] {
  const groups = new Map<string, InboxItem[]>()
  const lastAt = new Map<string, { key: string; at: number }>()
  for (const item of [...items].sort((a, b) => a.created_at - b.created_at)) {
    let key = item.group_key
    if (!key) {
      const kind = `${item.type}:${item.target}`
      const open = lastAt.get(kind)
      key =
        open && item.created_at - open.at <= GROUP_WINDOW
          ? open.key
          : `${kind}:${item.created_at}`
      lastAt.set(kind, { key, at: item.created_at })
    }
    const members = groups.get(key)
    if (members) members.push(item)
    else groups.set(key, [item])
  }
  return [...groups.entries()]
    .map(([key, members]) => {
      const sorted = [...members].sort((a, b) => b.created_at - a.created_at)
      return {
        id: sorted.length > 1 ? key : `item:${sorted[0].id}`,
        items: sorted,
        lead: [...sorted].sort(byUrgency)[0],
      }
    })
    .sort(
      (a, b) =>
        lowPriority(a.lead) - lowPriority(b.lead) || byUrgency(a.lead, b.lead)
    )
}

const isClosed = (item: InboxItem) =>
  item.state === 'resolved' || item.state === 'dismissed'

/** Where an open-list item belongs: Act now, Watch (FYI included) or closed. */
export function itemPlace(item: InboxItem): LiveBand | 'resolved' {
  const band = itemBand(item)
  if (
    band === 'recovered' ||
    item.status === 'back_to_normal' ||
    item.status === 'closed' ||
    isClosed(item)
  )
    return 'resolved'
  return band === 'act_now' ? 'act_now' : 'watch'
}

/** The numbers every count shows: tabs, section headers and the sidebar badge. */
export interface InboxCounts {
  /** Act now plus Watch: live, unmuted, not snoozed. */
  open: number
  actNow: number
  watch: number
  muted: number
  snoozed: number
}

/** One source for every count, so the tabs, the sections and the badge agree. */
export function inboxCounts(
  items: InboxItem[],
  rules: MuteRule[] | undefined
): InboxCounts {
  const counts = { open: 0, actNow: 0, watch: 0, muted: 0, snoozed: 0 }
  for (const item of items) {
    const place = itemPlace(item)
    if (place === 'resolved') continue
    if (isMuted(item, rules)) counts.muted += 1
    else if (item.state === 'snoozed') counts.snoozed += 1
    else if (place === 'act_now') counts.actNow += 1
    else counts.watch += 1
  }
  counts.open = counts.actNow + counts.watch
  return counts
}

export interface OpenInbox {
  sections: Record<LiveBand, InboxSection>
  /** Measured back to normal, listed on Open for 24 hours. */
  recovered: InboxItem[]
  snoozed: InboxItem[]
  muted: InboxItem[]
  counts: InboxCounts
}

/** Splits the open list into Act now and Watch, recoveries, snoozed and muted items. */
export function shapeOpen(
  items: InboxItem[],
  rules: MuteRule[] | undefined
): OpenInbox {
  const muted: InboxItem[] = []
  const snoozed: InboxItem[] = []
  const recovered: InboxItem[] = []
  const live: Record<LiveBand, InboxItem[]> = { act_now: [], watch: [] }
  for (const item of items) {
    const place = itemPlace(item)
    if (place === 'resolved') recovered.push(item)
    else if (isMuted(item, rules)) muted.push(item)
    else if (item.state === 'snoozed') snoozed.push(item)
    else live[place].push(item)
  }
  const section = (band: LiveBand): InboxSection => ({
    band,
    groups: groupItems(live[band]),
    count: live[band].length,
    unread: live[band].filter((item) => item.state === 'new').length,
  })
  return {
    sections: { act_now: section('act_now'), watch: section('watch') },
    recovered,
    snoozed: snoozed.sort(byUrgency),
    muted: muted.sort((a, b) => b.created_at - a.created_at),
    counts: inboxCounts(items, rules),
  }
}

/** Recovered and closed alerts stay on Open this long; the daily report is the history. */
const RECENT_CLOSE = 86_400

function dayStart(ts: number) {
  const date = new Date(ts * 1000)
  date.setHours(0, 0, 0, 0)
  return date.getTime() / 1000
}

// An open item measured back to normal counts from when its readings recovered.
const closedAt = (item: InboxItem) =>
  item.resolved_at ?? item.status_at ?? item.updated_at

/**
 * Recoveries still on the open list plus alerts closed in the last 24 hours, each once,
 * most recently closed first.
 */
export function recentlyClosedItems(
  openItems: InboxItem[],
  doneItems: InboxItem[],
  now: number
): InboxItem[] {
  const byId = new Map<number, InboxItem>()
  for (const item of [...doneItems, ...openItems])
    if (
      itemPlace(item) === 'resolved' &&
      // Open ones back to normal stay until RDST or you close them.
      (!isClosed(item) || closedAt(item) >= now - RECENT_CLOSE)
    )
      byId.set(item.id, item)
  // Open ones still wait on you (resolve), so they lead.
  return [...byId.values()].sort(
    (a, b) =>
      Number(isClosed(a)) - Number(isClosed(b)) || closedAt(b) - closedAt(a)
  )
}

/**
 * How an alert ended, in the words of what was observed: 'Back to normal at 8:07 PM',
 * 'Running again at 8:07 PM', 'You resolved it at 8:07 PM'.
 */
export function closedLabel(item: InboxItem, now = Date.now() / 1000): string {
  const at = timePhrase('at', closedAt(item), now)
  if (item.state === 'dismissed') return `Dismissed ${at}`
  const byRdst =
    itemBand(item) === 'recovered' ||
    item.resolved_by === 'code' ||
    item.resolved_by === 'jev'
  if (!byRdst) return `You resolved it ${at}`
  if (item.type === 'daily_summary') return `Replaced by a newer report ${at}`
  return recoveryPhrase(item, closedAt(item), now)
}

const RECOVERY_LATENCY_TYPES = new Set([
  'latency_regression',
  'plan_change',
  'table_slowdown',
  'slow_creep',
  'query_improved',
])

const RECOVERY_CALLS_TYPES = new Set(['traffic_spike'])

/** The reading an item watches: 'Latency', 'Calls', 'Cache hit ratio', or null. */
export function itemMetric(item: InboxItem): string | null {
  if (RECOVERY_LATENCY_TYPES.has(item.type)) return 'Latency'
  if (RECOVERY_CALLS_TYPES.has(item.type)) return 'Calls'
  if (item.type === 'cache_hit_ratio_drop') return 'Cache hit ratio'
  if (item.type === 'rollback_or_deadlock_spike') return 'Rollbacks'
  if (item.type === 'connections_near_max') return 'Connections'
  if (item.type === 'lock_contention') return 'Lock waits'
  if (item.type === 'many_queries_changed') {
    const metrics = new Set(
      (item.incident?.queries ?? []).map((query) => query.metric)
    )
    const [only] = metrics
    if (metrics.size === 1 && only)
      return only === 'latency' ? 'Latency' : 'Calls'
    const metric = item.measurement?.metric
    if (
      !item.incident?.queries.length &&
      (metric === 'latency' || metric === 'calls')
    )
      return metric === 'latency' ? 'Latency' : 'Calls'
  }
  return null
}

/**
 * How it ended, naming what went back to normal: 'Latency back to normal at 11:25 PM',
 * 'Running again at 8:07 PM'.
 */
export function recoveryPhrase(
  item: InboxItem,
  ts: number,
  now = Date.now() / 1000
): string {
  const at = timePhrase('at', ts, now)
  switch (item.type) {
    case 'query_stopped_running':
      return `Running again ${at}`
    case 'new_heavy_query':
      return `Stopped running ${at}`
    case 'stuck_query':
      return `Finished ${at}`
    case 'monitoring_issue':
      return `Working again ${at}`
    case 'many_queries_changed': {
      const metric = itemMetric(item)
      return metric
        ? `${metric} back to normal on every query ${at}`
        : `Every query back to normal ${at}`
    }
    default: {
      const metric = itemMetric(item)
      return metric ? `${metric} back to normal ${at}` : `Back to normal ${at}`
    }
  }
}

/** 'at 9:42 PM', 'yesterday at 9:42 PM', 'on Sep 26 at 4:10 PM'. */
export function timePhrase(
  word: 'at' | 'by',
  ts: number,
  now = Date.now() / 1000
): string {
  const stamp = formatStampTime(ts, now)
  const yesterday = /^yesterday (.+)$/.exec(stamp)
  if (yesterday) return `yesterday ${word} ${yesterday[1]}`
  const dated = /^(.+), (\d{1,2}:\d{2}\s[AP]M)$/.exec(stamp)
  if (dated) return `on ${dated[1]} ${word} ${dated[2]}`
  return `${word} ${stamp}`
}

export type SummaryToken =
  | { kind: 'text'; text: string }
  | { kind: 'number'; text: string }
  | { kind: 'arrow' }

// Numbers with their unit ("0.58 ms", "25 %", "3x", "14:02", "Sep 27, 8:49 AM"), not
// digits inside identifiers such as pg_sleep(180) or idx_2.
const NUMBER =
  /(?<![\w(.])(?:(?:[A-Z][a-z]{2} \d{1,2}, )?\d{1,2}:\d{2}(?:\s?[AP]M)?|[+-]?\d[\d,]*(?:\.\d+)?(?:\s?%|\s?(?:ms|min|s|h|x)\b)?)/g

/** Splits a one-line summary so the numbers that matter can be emphasized. */
export function summaryTokens(summary: string): SummaryToken[] {
  const tokens: SummaryToken[] = []
  const parts = summary.split(/\s*->\s*/)
  parts.forEach((part, index) => {
    if (index > 0) tokens.push({ kind: 'arrow' })
    let last = 0
    for (const match of part.matchAll(NUMBER)) {
      const start = match.index ?? 0
      if (start > last)
        tokens.push({ kind: 'text', text: part.slice(last, start) })
      tokens.push({ kind: 'number', text: match[0] })
      last = start + match[0].length
    }
    if (last < part.length)
      tokens.push({ kind: 'text', text: part.slice(last) })
  })
  return tokens
}

/** One line for a group: "3 alerts like this on localdev, filed within 4 min of each other". */
export function groupSummary(group: InboxGroup): string {
  const { items } = group
  const oldest = items[items.length - 1].created_at
  const span = items[0].created_at - oldest
  const window =
    span < 60
      ? 'at the same time'
      : span < 3600
        ? `within ${Math.max(1, Math.round(span / 60))} min of each other`
        : `within ${Math.round(span / 3600)} h of each other`
  return `${items.length} alerts like this on ${group.lead.target}, filed ${window}`
}

export interface SentStamp {
  /** 'Sent' for pushed alerts, 'Filed' for everything else. */
  verb: 'Sent' | 'Filed'
  /** '9:42 PM', 'yesterday 11:05 PM', 'Sep 27, 4:10 PM'. */
  when: string
  /** Why nothing was pushed: 'in the daily digest', 'no alert sent', 'muted'. */
  note: string | null
  /** The whole caption: 'Sent 9:42 PM', 'Filed 9:42 PM, in the daily digest'. */
  label: string
  /** Without the note, for tight rows: 'Filed 9:42 PM'. */
  short: string
  /** Full timestamps for the tooltip. */
  title: string
  /** ISO 8601, for the time element. */
  iso: string
  ts: number
}

const clock = (date: Date) =>
  date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })

/** A moment in words, as precise as the reader needs next to today's date. */
export function formatStampTime(ts: number, now = Date.now() / 1000): string {
  const date = new Date(ts * 1000)
  const today = dayStart(now)
  const start = dayStart(ts)
  if (start === today) return clock(date)
  if (start === dayStart(today - 1)) return `yesterday ${clock(date)}`
  const sameYear = date.getFullYear() === new Date(now * 1000).getFullYear()
  const day = date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    ...(sameYear ? {} : { year: 'numeric' }),
  })
  return `${day}, ${clock(date)}`
}

const fullTime = (ts: number) =>
  new Date(ts * 1000).toLocaleString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    timeZoneName: 'short',
  })

/**
 * When the alert went out. A pushed alert is 'Sent' at its latest push
 * (`notified_at`, or the filing time from backends that don't send it); anything
 * that was never pushed is 'Filed' and says where it went instead.
 */
export function sentStamp(item: InboxItem, now = Date.now() / 1000): SentStamp {
  const pushed = item.delivery === 'notify' && item.muted_by === null
  const notifiedAt = item.notified_at
  const sent = pushed && notifiedAt !== null
  const ts = sent ? (notifiedAt ?? item.created_at) : item.created_at
  const verb = sent ? 'Sent' : 'Filed'
  const note =
    item.muted_by !== null
      ? 'muted'
      : item.delivery === 'digest'
        ? 'in the daily digest'
        : sent
          ? null
          : 'no alert sent'
  const when = formatStampTime(ts, now)
  const lines = [`${verb} ${fullTime(ts)}${note ? `, ${note}` : ''}`]
  if (verb === 'Sent' && ts !== item.created_at)
    lines.push(`Filed ${fullTime(item.created_at)}`)
  if (item.updated_at - Math.max(ts, item.created_at) >= 60)
    lines.push(`Last updated ${fullTime(item.updated_at)}`)
  return {
    verb,
    when,
    note,
    label: note ? `${verb} ${when}, ${note}` : `${verb} ${when}`,
    short: `${verb} ${when}`,
    title: lines.join('\n'),
    iso: new Date(ts * 1000).toISOString(),
    ts,
  }
}
