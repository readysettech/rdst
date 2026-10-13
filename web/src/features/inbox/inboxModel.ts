// Pure shaping for the Inbox: which band an item sits in, the queue groups on Open and
// Closed, the times each row and header show, and the counts every view shares.
// Timestamps are Unix seconds.
import { fixtureOverride } from '@/lib/monitor/contract'
import { asCaScenario, caFixtures } from '@/lib/monitor/fixturesCa'
import type {
  InboxBand,
  InboxItem,
  InboxKind,
  MuteRule,
} from '@/lib/monitor/types'
import { plural } from '../monitor-kit/format'
import { itemBand, ruleMatches } from '../monitor-kit/severity'

const DAY = 86_400

/** Closed items stay this long. */
export const CLOSED_KEEP_S = 7 * DAY

/** Items a live mute rule covers, even before the backend re-files them as muted. */
export function isMuted(item: InboxItem, rules: MuteRule[] | undefined) {
  if (item.muted_by !== null) return true
  return Boolean(rules?.some((rule) => ruleMatches(rule, item)))
}

export const isClosed = (item: InboxItem) =>
  item.state === 'resolved' || item.state === 'dismissed'

/**
 * The redesign band; older backends without the view fields derive it. The state wins, so an
 * item closed or reopened in place reads its new band before the next fetch.
 */
export function bandOf(item: InboxItem): InboxBand {
  if (isClosed(item)) return 'closed'
  if (item.band && item.band !== 'closed') return item.band
  if (item.status === 'closed' && !item.band) return 'closed'
  return itemBand(item) === 'act_now' ? 'act_now' : 'watch'
}

export function kindOf(item: InboxItem): InboxKind {
  if (item.kind) return item.kind
  switch (item.type) {
    case 'stuck_query':
    case 'fixes_ready':
    case 'report_ready':
    case 'fix_confirmed':
    case 'traffic_spike':
    case 'monitoring_issue':
    case 'changes_ready':
    case 'no_change_yet':
      return item.type
    default:
      return 'other'
  }
}

/** When the item closed; open items read their latest update. */
export const closedAt = (item: InboxItem) =>
  item.resolved_at ?? item.status_at ?? item.updated_at

/** The wall clock, or the fixture's own clock when a redesign scenario is on. */
export function inboxClock(): number {
  const scenario = asCaScenario(fixtureOverride())
  return scenario ? caFixtures(scenario).now : Date.now() / 1000
}

/** The numbers every count shows: the header, the tabs and the sidebar badge. */
export interface InboxCounts {
  /** Act now plus Watch: live, unmuted, not snoozed. */
  open: number
  actNow: number
  watch: number
  muted: number
  snoozed: number
}

export function inboxCounts(
  items: InboxItem[],
  rules: MuteRule[] | undefined
): InboxCounts {
  const counts = { open: 0, actNow: 0, watch: 0, muted: 0, snoozed: 0 }
  for (const item of items) {
    const band = bandOf(item)
    if (band === 'closed') continue
    if (isMuted(item, rules)) counts.muted += 1
    else if (item.state === 'snoozed') counts.snoozed += 1
    else if (band === 'act_now') counts.actNow += 1
    else counts.watch += 1
  }
  counts.open = counts.actNow + counts.watch
  return counts
}

export interface QueueGroup {
  id: string
  label: string
  items: InboxItem[]
}

function dayStart(ts: number) {
  const date = new Date(ts * 1000)
  date.setHours(0, 0, 0, 0)
  return date.getTime() / 1000
}

const newestFirst = (a: InboxItem, b: InboxItem) => b.created_at - a.created_at
const latestClosedFirst = (a: InboxItem, b: InboxItem) =>
  closedAt(b) - closedAt(a)

/** Closed items still within the week they are kept, once each, newest first. */
export function keptClosed(items: InboxItem[], now: number): InboxItem[] {
  const byId = new Map<number, InboxItem>()
  for (const item of items)
    if (bandOf(item) === 'closed' && closedAt(item) >= now - CLOSED_KEEP_S)
      byId.set(item.id, item)
  return [...byId.values()].sort(latestClosedFirst)
}

/** Open: Act now, then Watch; closed items live behind Closed. A group with nothing is left out. */
export function openGroups(open: InboxItem[]): QueueGroup[] {
  const groups: QueueGroup[] = [
    {
      id: 'act_now',
      label: 'Act now',
      items: open
        .filter((item) => bandOf(item) === 'act_now')
        .sort(newestFirst),
    },
    {
      id: 'watch',
      label: 'Watch',
      items: open.filter((item) => bandOf(item) === 'watch').sort(newestFirst),
    },
  ]
  return groups.filter((group) => group.items.length > 0)
}

/** Closed: Today, Yesterday, Earlier this week. */
export function closedGroups(closed: InboxItem[], now: number): QueueGroup[] {
  const today = dayStart(now)
  const yesterday = today - DAY
  const kept = keptClosed(closed, now)
  const groups: QueueGroup[] = [
    {
      id: 'today',
      label: 'Today',
      items: kept.filter((item) => closedAt(item) >= today),
    },
    {
      id: 'yesterday',
      label: 'Yesterday',
      items: kept.filter(
        (item) => closedAt(item) >= yesterday && closedAt(item) < today
      ),
    },
    {
      id: 'earlier',
      label: 'Earlier this week',
      items: kept.filter((item) => closedAt(item) < yesterday),
    },
  ]
  return groups.filter((group) => group.items.length > 0)
}

/** The time an item shows in the queue: when it was filed, or when it closed. */
export function queueStamp(item: InboxItem): number {
  return bandOf(item) === 'closed' ? closedAt(item) : item.created_at
}

/** "9:55 AM". */
export function clock12(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  })
}

/** "9:55", the day's own clock. */
export function shortClock(ts: number): string {
  const date = new Date(ts * 1000)
  return `${date.getHours() % 12 || 12}:${String(date.getMinutes()).padStart(2, '0')}`
}

/** A queue row's time: "9:55" today, "4:31 PM" yesterday, "Tue" earlier this week. */
export function queueTime(ts: number, now: number): string {
  const today = dayStart(now)
  const start = dayStart(ts)
  if (start >= today) return shortClock(ts)
  if (start >= today - DAY) return clock12(ts)
  if (start >= today - 6 * DAY)
    return new Date(ts * 1000).toLocaleDateString('en-US', { weekday: 'short' })
  return new Date(ts * 1000).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  })
}

/** "running 4 min" for a stuck statement still going. */
export function runningText(item: InboxItem, now: number): string | null {
  if (kindOf(item) !== 'stuck_query' || bandOf(item) === 'closed') return null
  const seconds = item.running_s ?? now - item.created_at
  const minutes = Math.max(1, Math.round(seconds / 60))
  return `running ${plural(minutes, 'min', 'min')}`
}

const escapeRegExp = (text: string) =>
  text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** A caption without its database, which the row shows under the time: "demo, 3 of 8" -> "3 of 8". */
export function withoutDatabase(text: string, target: string): string {
  if (!target) return text
  const name = escapeRegExp(target)
  return text
    .replace(new RegExp(`^${name}(, |: )`), '')
    .replace(new RegExp(` on ${name}(?=[.,]|$)`), '')
}

/** The item's backend body, parsed when it arrives as text. */
export function bodyOf(item: InboxItem): Record<string, unknown> {
  if (typeof item.body_json !== 'string') return item.body_json ?? {}
  try {
    return JSON.parse(item.body_json) as Record<string, unknown>
  } catch {
    return {}
  }
}

const num = (value: unknown) => (typeof value === 'number' ? value : null)

/** "14 ms", "9.6 ms". */
function msText(ms: number): string {
  return `${ms < 10 ? Number(ms.toFixed(1)) : Math.round(ms)} ms`
}

/**
 * A No change yet item in a line: "index applied October 6, still 14 ms after 2 days". Null
 * when the backend did not send the change's time and latency.
 */
export function noChangeText(item: InboxItem): string | null {
  const body = bodyOf(item)
  const applied = num(body.detected_at)
  const after = num(body.after_ms) ?? item.after_ms ?? null
  if (applied == null || after == null) return null
  const days = Math.max(1, Math.round((closedAt(item) - applied) / DAY))
  const type = typeof body.action_type === 'string' ? body.action_type : ''
  const what =
    body.index_label || body.index_name || type.includes('index')
      ? 'index'
      : 'change'
  const date = new Date(applied * 1000).toLocaleDateString('en-US', {
    month: 'long',
    day: 'numeric',
  })
  return `${what} applied ${date}, still ${msText(after)} after ${plural(days, 'day')}`
}

/**
 * A queue row's meta line: the query for a stuck statement, what a change did not move,
 * the summary otherwise; never the database, which has its own column.
 */
export function rowMeta(item: InboxItem): string {
  const kind = kindOf(item)
  if (kind === 'stuck_query' && bandOf(item) !== 'closed')
    return item.subject_label
  if (kind === 'no_change_yet') {
    const text = noChangeText(item)
    if (text) return text
  }
  const meta = withoutDatabase(item.summary, item.target)
  return meta.replace(/\.$/, '') === item.title ? '' : meta
}

/** The summary's complete sentences: a row's snippet can stop mid-sentence. */
export function wholeSentences(text: string | null | undefined): string | null {
  if (!text) return null
  const cut = text.match(/(\.\.\.|\u2026)$/)
  if (!cut) return text
  const kept = text.slice(0, -cut[0].length)
  const end = kept.lastIndexOf('. ')
  return end === -1 ? null : kept.slice(0, end + 1)
}

/** The detail header's facts: "demo, filed 9:55 AM", "demo, closed 8:12 AM". */
export function detailMeta(item: InboxItem): string {
  if (bandOf(item) === 'closed')
    return `${item.target}, closed ${clock12(closedAt(item))}`
  return `${item.target}, filed ${clock12(item.created_at)}`
}

/** "1 item needs you, 1 to watch." for the detail panel with nothing selected. */
export function emptySentence(actNow: number, watch: number): string {
  const needs =
    actNow === 0
      ? 'Nothing needs you'
      : `${plural(actNow, 'item')} ${actNow === 1 ? 'needs' : 'need'} you`
  return `${needs}, ${watch} to watch.`
}

/** "2 items closed in the last 7 days." for the Closed view's empty panel. */
export function closedSentence(closed: number): string {
  return closed === 0
    ? 'Nothing closed in the last 7 days.'
    : `${plural(closed, 'item')} closed in the last 7 days.`
}

/** True when two lines say the same words, ignoring case and a final period. */
export function sameWords(a: string | null | undefined, b: string): boolean {
  const norm = (text: string) => text.trim().replace(/\.$/, '').toLowerCase()
  return a != null && norm(a) === norm(b)
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

/** A moment in words, as precise as the reader needs next to today's date. */
export function formatStampTime(ts: number, now = Date.now() / 1000): string {
  const date = new Date(ts * 1000)
  const today = dayStart(now)
  const start = dayStart(ts)
  if (start === today) return clock12(ts)
  if (start === dayStart(today - 1)) return `yesterday ${clock12(ts)}`
  const sameYear = date.getFullYear() === new Date(now * 1000).getFullYear()
  const day = date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    ...(sameYear ? {} : { year: 'numeric' }),
  })
  return `${day}, ${clock12(ts)}`
}

/** Open items the queue keeps aside: muted by a rule, or snoozed. */
export function asideItems(
  items: InboxItem[],
  rules: MuteRule[] | undefined
): { muted: InboxItem[]; snoozed: InboxItem[] } {
  const muted: InboxItem[] = []
  const snoozed: InboxItem[] = []
  for (const item of items) {
    if (bandOf(item) === 'closed') continue
    if (isMuted(item, rules)) muted.push(item)
    else if (item.state === 'snoozed') snoozed.push(item)
  }
  return { muted: muted.sort(newestFirst), snoozed: snoozed.sort(newestFirst) }
}
